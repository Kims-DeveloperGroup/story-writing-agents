"""Owner-controlled state and source identities for the local test coordinator."""
from __future__ import annotations

import hashlib
import fcntl
import json
import os
import platform
import re
import stat
import subprocess
import tempfile
from pathlib import Path

CAPACITY = 4
SCHEMA_VERSION = 1
ACTIVE = {"starting", "running", "cancelling", "quarantined"}
TERMINAL = {"finished", "cancelled", "stale", "failed"}
SERVICE_MODULES = ("local_ci.py", "local_ci_coordinator.py", "local_ci_state.py", "local_ci_worker.py")
SERVICE_ENTRY = b"from tests.local_ci_worker import main\nraise SystemExit(main())\n"


class LocalCIError(ValueError):
    """A local execution request cannot produce valid evidence."""


def owner_directory(path: Path, *, create: bool = False) -> Path:
    path = path.absolute()
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise LocalCIError(f"not an owner-controlled directory: {path}")
    if info.st_mode & 0o077:
        raise LocalCIError(f"directory must have mode 0700: {path}")
    return path


def state_root() -> Path:
    # Deliberately has no per-checkout override: all clients on this host share it.
    reject_guest_coordinator()
    return owner_directory(Path.home() / ".cache" / "story-local-ci", create=True)


def reject_guest_coordinator() -> None:
    # Lima's installed boot scripts write the instance IID into this marker.
    # Native Linux has no such marker and remains a supported physical host.
    markers = ("/run/lima-ssh-ready", "/var/run/lima-ssh-ready", "/run/lima-guestagent.pid")
    if any(Path(path).exists() for path in markers):
        raise LocalCIError("direct Lima test admission is unavailable; submit through the host with --executor lima:INSTANCE")


def service_sources() -> tuple[str, dict[str, bytes]]:
    sources = {name: Path(__file__).with_name(name).read_bytes() for name in SERVICE_MODULES}
    digest = hashlib.sha256()
    for name, raw in sources.items():
        digest.update(name.encode() + b"\0" + raw + b"\0")
    return digest.hexdigest(), sources


def prepare_service(root: Path, identity: str, sources: dict[str, bytes]) -> Path:
    services = owner_directory(root / "services", create=True)
    lock = os.open(services / "bundle.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _prepare_service(root, identity, sources)
    finally:
        os.close(lock)


def _prepare_service(root: Path, identity: str, sources: dict[str, bytes]) -> Path:
    directory = owner_directory(root / "services" / identity / "tests", create=True)
    for name, raw in {"__init__.py": b"", **sources}.items():
        path = directory / name
        if path.exists():
            if path.is_symlink() or path.read_bytes() != raw:
                raise LocalCIError("coordinator service copy differs from its content identity")
            continue
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            target.write(raw)
    entry = directory.parent / "entry.py"
    if entry.exists():
        if entry.is_symlink() or entry.read_bytes() != SERVICE_ENTRY:
            raise LocalCIError("coordinator worker entry differs from its immutable recipe")
    else:
        descriptor = os.open(entry, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            target.write(SERVICE_ENTRY)
    return directory.parent


def read_json(path: Path) -> dict:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "r", encoding="utf-8") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise LocalCIError(f"not an owner-only regular file: {path}")
        value = json.load(source)
    if not isinstance(value, dict):
        raise LocalCIError(f"invalid object in {path}")
    return value


def write_json(path: Path, value: dict, *, exclusive: bool = False) -> None:
    rendered = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    if exclusive:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            target.write(rendered)
            target.flush()
            os.fsync(target.fileno())
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(rendered)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def git(checkout: Path, *args: str) -> str:
    completed = subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, text=True)
    if completed.returncode:
        raise LocalCIError(completed.stderr.strip() or f"Git failed: {args}")
    return completed.stdout.rstrip("\n")


def snapshot(checkout: Path, *, exclude: tuple[str, ...] = ()) -> dict:
    checkout = checkout.resolve(strict=True)
    root = git(checkout, "rev-parse", "--show-toplevel")
    if Path(root).resolve() != checkout:
        raise LocalCIError("checkout must be the repository root")
    records: dict[str, str] = {}
    excluded = {Path(value).resolve() for value in exclude}

    def inputs(repository: Path, prefix: str = "") -> None:
        raw = subprocess.check_output([
            "git", "-C", str(repository), "ls-files", "--cached", "--others",
            "--exclude-standard", "-z",
        ])
        for item in sorted(set(raw.split(b"\0")) - {b""}):
            relative = os.fsdecode(item)
            path = repository / relative
            if path.resolve() in excluded:
                continue
            label = prefix + relative
            try:
                info = path.lstat()
            except FileNotFoundError:
                records[label] = "absent"
                continue
            if stat.S_ISLNK(info.st_mode):
                records[label] = "link:" + hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest()
            elif stat.S_ISREG(info.st_mode):
                before = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                raw_bytes = path.read_bytes()
                after = path.lstat()
                if before != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                    raise LocalCIError(f"source changed while hashing: {path}")
                records[label] = f"file:{info.st_mode & 0o111}:{hashlib.sha256(raw_bytes).hexdigest()}"
            elif stat.S_ISDIR(info.st_mode) and (path / ".git").exists():
                records[label] = "submodule:" + git(path, "rev-parse", "HEAD")
                inputs(path, label + "/")
            else:
                records[label] = "nonregular"

    inputs(checkout)
    exclusions = [f":(literal,exclude){path.relative_to(checkout).as_posix()}"
                  for path in excluded if path.is_relative_to(checkout)]
    identity = {
        "checkout_sha": git(checkout, "rev-parse", "HEAD"),
        "checkout_tree": git(checkout, "rev-parse", "HEAD^{tree}"),
        "porcelain": git(checkout, "status", "--porcelain", "--untracked-files=all", "--", ".", *exclusions),
        "inputs": records,
    }
    identity["digest"] = "sha256:" + hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return identity


def process_identity(pid: int) -> str | None:
    process = Path("/proc") / str(pid) / "stat"
    if process.exists():
        try:
            fields = process.read_text().rsplit(")", 1)[1].split()
            if fields[0] == "Z":
                return None
            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            return f"{boot}:{fields[19]}"
        except (OSError, IndexError):
            return None
    result = subprocess.run(["ps", "-p", str(pid), "-o", "stat=", "-o", "lstart="],
                            capture_output=True, text=True, env=process_metadata_environment())
    fields = result.stdout.strip().split(None, 1)
    return fields[1] if len(fields) == 2 and not fields[0].startswith("Z") else None


def group_members(group: int) -> list[int]:
    result = subprocess.run(["ps", "-eo", "pid=,pgid=,stat="], capture_output=True, text=True,
                            env=process_metadata_environment())
    if result.returncode:
        raise LocalCIError("cannot prove worker process-group termination")
    return [int(fields[0]) for line in result.stdout.splitlines()
            if len(fields := line.split()) == 3 and int(fields[1]) == group
            and not fields[2].startswith("Z")]


def observed_descendants(pid: int) -> dict[str, dict]:
    """Record current descendants, including children that changed sessions."""
    result = subprocess.run(["ps", "-eo", "pid=,ppid=,pgid=,uid=,stat="], capture_output=True, text=True,
                            env=process_metadata_environment())
    if result.returncode:
        raise LocalCIError("cannot inspect owned descendant processes")
    table = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 5 and int(fields[3]) == os.getuid() and not fields[4].startswith("Z"):
            table[int(fields[0])] = (int(fields[1]), int(fields[2]))
    owned = {pid}
    while True:
        discovered = {child for child, (parent, _group) in table.items() if parent in owned}
        if discovered <= owned:
            break
        owned.update(discovered)
    records = {}
    for child in owned & table.keys():
        identity = process_identity(child)
        if identity is not None:
            records[str(child)] = {"pid": child, "identity": identity, "pgid": table[child][1]}
    return records


def live_owned(records: dict[str, dict]) -> list[int]:
    return [record["pid"] for record in records.values()
            if process_identity(record["pid"]) == record["identity"]]


def signal_owned(records: dict[str, dict], action: int, *, outside_group: int | None = None) -> None:
    for record in records.values():
        if outside_group is not None and record["pgid"] == outside_group:
            continue
        if process_identity(record["pid"]) == record["identity"]:
            try:
                os.kill(record["pid"], action)
            except ProcessLookupError:
                pass


def environment() -> dict:
    return {"python": platform.python_version(), "platform": platform.platform(),
            "machine": platform.machine(), "host": platform.node()}


def boot_identity() -> str:
    path = Path("/proc/sys/kernel/random/boot_id")
    if path.exists():
        identity = normalize_boot_identity("linux:" + path.read_text().strip())
        if identity is None:
            raise LocalCIError("cannot establish numeric executor boot identity")
        return identity
    result = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True,
                            env=process_metadata_environment())
    if result.returncode or not result.stdout.strip():
        raise LocalCIError("cannot establish executor boot identity")
    identity = normalize_boot_identity("sysctl:" + result.stdout.strip())
    if identity is None:
        raise LocalCIError("cannot establish numeric executor boot identity")
    return identity


def normalize_boot_identity(value: object) -> str | None:
    """Parse stable boot evidence, including legacy Darwin display strings."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    linux = re.fullmatch(r"linux:([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})", value)
    if linux:
        return "linux:" + linux[1].lower()
    numeric = re.fullmatch(r"sysctl:(\d+):(\d+)", value)
    legacy = re.fullmatch(
        r"(?:sysctl:)?\{\s*sec\s*=\s*(\d+)\s*,\s*usec\s*=\s*(\d+)\s*\}(?:\s+.*)?", value,
    )
    fields = numeric or legacy
    if fields is None:
        return None
    try:
        seconds, microseconds = int(fields[1]), int(fields[2])
    except ValueError:
        return None
    if not 0 <= microseconds < 1_000_000:
        return None
    return f"sysctl:{seconds}:{microseconds}"


def verified_boot_change(previous: object, current: object) -> bool:
    before = normalize_boot_identity(previous)
    after = normalize_boot_identity(current)
    return (before is not None and after is not None
            and before.split(":", 1)[0] == after.split(":", 1)[0] and before != after)


def process_metadata_environment() -> dict[str, str]:
    return {**os.environ, "TZ": "UTC", "LC_ALL": "C", "LANG": "C"}
