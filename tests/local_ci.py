"""Shared local validation: run a pinned campaign, inspect jobs, or cancel owned work."""
from __future__ import annotations

import argparse
import base64
import fcntl
import json
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

from tests.local_ci_state import (TERMINAL, LocalCIError, git, owner_directory,
                                  process_identity, read_json, snapshot, state_root, write_json)
from tests.local_ci_state import prepare_service, reject_guest_coordinator, service_sources

_OWNERSHIP: dict[str, str] = {}


def claim(request: dict) -> str:
    result = rpc(request)
    _OWNERSHIP[result["id"]] = result["token"]
    return result["id"]


def control(action: str, identifier: str) -> dict:
    return rpc({"action": action, "id": identifier, "token": _OWNERSHIP[identifier]})


def _exchange(request: dict) -> dict:
    root = state_root()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(45)
        connection.connect(str(root / "coordinator.sock"))
        connection.sendall(json.dumps({"code_identity": service_sources()[0], **request}).encode() + b"\n")
        chunks = []
        while not chunks or not chunks[-1].endswith(b"\n"):
            block = connection.recv(65536)
            if not block:
                raise LocalCIError("coordinator disconnected before returning evidence")
            chunks.append(block)
        response = json.loads(b"".join(chunks))
        if not response["ok"]:
            raise LocalCIError(response["error"])
        if response["result"].get("idle_restart"):
            return {"idle_restart": True}
        return response["result"]


def rpc(request: dict) -> dict:
    try:
        result = _exchange(request)
        if not result.get("idle_restart"):
            return result
    except (ConnectionRefusedError, FileNotFoundError):
        pass
    root = state_root()
    descriptor = os.open(root / "startup.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            result = _exchange(request)
            if not result.get("idle_restart"):
                return result
            time.sleep(0.2)
        except (ConnectionRefusedError, FileNotFoundError):
            pass
        identity, sources = service_sources()
        service = prepare_service(root, identity, sources)
        log_descriptor = os.open(root / "coordinator.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(log_descriptor, "ab") as log:
            subprocess.Popen([sys.executable, "-m", "tests.local_ci_coordinator"],
                             cwd=service, stdout=log, stderr=log,
                             env={**os.environ, "PYTHONPATH": str(service), "PYTHONDONTWRITEBYTECODE": "1"}, start_new_session=True)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                result = _exchange(request)
                if not result.get("idle_restart"):
                    return result
            except (ConnectionRefusedError, FileNotFoundError):
                time.sleep(0.1)
        raise LocalCIError(f"coordinator did not start; retain {root / 'coordinator.log'}")
    finally:
        os.close(descriptor)


def owner() -> dict:
    return {"pid": os.getpid(), "identity": process_identity(os.getpid())}


def admitted_worker() -> bool:
    token = os.environ.get("STORY_LOCAL_CI_TOKEN")
    identifier = os.environ.get("STORY_LOCAL_CI_JOB")
    path = os.environ.get("STORY_LOCAL_CI_WORKER_STATE") or os.environ.get("STORY_LOCAL_CI_GUEST_STATE")
    if not token or not identifier or not path:
        return False
    value = read_json(Path(path))
    if value.get("token") != token or value.get("id") != identifier or value.get("state") != "running":
        raise LocalCIError("invalid inherited local test admission")
    pid = value.get("pid")
    if not isinstance(pid, int) or process_identity(pid) != value.get("process_identity"):
        raise LocalCIError("admitted worker identity is no longer live")
    # Descendants belong to the same top-level execution. An unrelated process
    # must not inherit a copied capability and bypass admission.
    current = os.getpid()
    for _ in range(128):
        if current == pid:
            return True
        result = subprocess.run(["ps", "-p", str(current), "-o", "ppid="], capture_output=True, text=True)
        if not result.stdout.strip():
            break
        parent = int(result.stdout.strip())
        if parent <= 1 or parent == current:
            break
        current = parent
    raise LocalCIError("local test admission does not belong to this process tree")


def submit(checkout: Path, arguments: list[str], *, python: str, task: str,
           executor: str = "native", guest_checkout: str | None = None,
           reservation: str | None = None, ci_identity: bool = False) -> str:
    positions = [index + 1 for index, value in enumerate(arguments[:-1]) if value == "--json"]
    excluded = [str((checkout / arguments[positions[-1]]).resolve())] if positions and arguments[positions[-1]] != "-" else []
    request = {"action": "submit", "owner": owner(), "task": task,
               "checkout": str(checkout), "source": snapshot(checkout, exclude=tuple(excluded)), "args": arguments,
               "python": python, "executor": executor, "guest_checkout": guest_checkout or str(checkout),
               "reservation": reservation, "reservation_token": _OWNERSHIP.get(reservation),
               "ci_identity": ci_identity, "excluded_outputs": excluded}
    return claim(request)


def wait_job(identifier: str, *, stream: bool = True) -> dict:
    offsets: dict[str, int] = {}
    try:
        while True:
            result = rpc({"action": "get", "id": identifier})
            if stream and result.get("directory"):
                directory = Path(result["directory"])
                prefix = "guest-" if result.get("executor", "native") != "native" else ""
                for name, target in (("stdout.log", sys.stdout), ("stderr.log", sys.stderr)):
                    path = directory / (prefix + name)
                    if path.exists():
                        with open(path, "rb") as source:
                            source.seek(offsets.get(name, 0))
                            raw = source.read()
                            offsets[name] = source.tell()
                        if raw:
                            if hasattr(target, "buffer"):
                                target.buffer.write(raw)
                            else:
                                target.write(raw.decode("utf-8", errors="replace"))
                            target.flush()
            if result["state"] in TERMINAL or result["state"] == "reserved":
                return result
            if result["state"] == "quarantined":
                raise LocalCIError(f"job {identifier} is quarantined: {result.get('recovery')}; slot retained")
            time.sleep(0.2)
    except KeyboardInterrupt:
        control("cancel", identifier)
        raise


def runner_admission(arguments: list[str]) -> int | None:
    if admitted_worker():
        return None
    reject_guest_coordinator()
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return None
    checkout = Path(__file__).resolve().parents[1]
    identifier = submit(checkout, arguments, python=sys.executable, task=f"runner:{checkout.name}")
    result = wait_job(identifier)
    return int(result.get("exit_code", 2))


def classify(checkout: Path, base: str, head: str, labels: list[str]) -> dict:
    recipe = ('document = YAML.safe_load(File.read(ARGV.fetch(0)), aliases: false); '
              'print document.fetch("jobs").fetch("classify").fetch("steps")'
              '.find { |step| step["id"] == "paths" }.fetch("with").fetch("script")')
    script = subprocess.check_output(["ruby", "-ryaml", "-e", recipe,
                                      str(checkout / ".github/workflows/pr-validation.yml")], text=True)
    raw = subprocess.check_output(["git", "-C", str(checkout), "diff", "--name-status", "-z", base + "..." + head])
    parts = raw.split(b"\0")
    files = []
    index = 0
    while index < len(parts) and parts[index]:
        status = os.fsdecode(parts[index])
        index += 1
        first = os.fsdecode(parts[index])
        index += 1
        if status.startswith(("R", "C")):
            second = os.fsdecode(parts[index])
            index += 1
            files.append({"filename": second, "previous_filename": first})
        else:
            files.append({"filename": first})
    harness = '''
import fs from "node:fs";
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const output = {};
const summary = {addHeading(){return this}, addTable(){return this}, async write(){}};
const context = {eventName: "pull_request", sha: input.checkout,
  repo: {owner: "local", repo: "validation"}, payload: {pull_request: {
    number: 0, base: {sha: input.base}, head: {sha: input.head},
    labels: input.labels.map(name => ({name}))}}};
const github = {paginate: async () => input.files, rest: {pulls: {listFiles(){}}}};
const core = {summary, setOutput(name, value){output[name] = value}};
await new (Object.getPrototypeOf(async function(){}).constructor)("context", "github", "core", input.script)(context, github, core);
process.stdout.write(JSON.stringify(output));
'''
    specification = {"script": script, "files": files, "base": base, "head": head,
                     "checkout": git(checkout, "rev-parse", "HEAD"), "labels": labels}
    completed = subprocess.run(["node", "--input-type=module", "-e", harness],
                               input=json.dumps(specification), capture_output=True, text=True)
    if completed.returncode:
        raise LocalCIError("pinned workflow classification failed: " + completed.stderr)
    result = json.loads(completed.stdout)
    if any(result.get(key) not in {"true", "false"} for key in ("runtime", "full")):
        raise LocalCIError("pinned workflow classification is incomplete")
    result["changed_paths"] = sorted({path for item in files for path in item.values()})
    result["labels"] = labels
    return result


def campaign(args: argparse.Namespace) -> int:
    campaign_started = time.monotonic()
    checkout = Path(args.checkout).resolve(strict=True)
    if args.executor != "native" and not re.fullmatch(r"lima:[A-Za-z0-9][A-Za-z0-9_.-]*", args.executor):
        raise LocalCIError("executor must be native or lima:INSTANCE")
    if args.guest_checkout and not Path(args.guest_checkout).is_absolute():
        raise LocalCIError("guest checkout must be an absolute path")
    report = owner_directory(Path(args.report_dir), create=True)
    if any(report.iterdir()) or report.is_relative_to(checkout):
        raise LocalCIError("use a fresh owner-only report directory outside the checkout")
    for name in ("base_sha", "head_sha"):
        value = getattr(args, name)
        resolved = git(checkout, "rev-parse", "--verify", value + "^{commit}")
        if value != resolved:
            raise LocalCIError(f"{name} must be a full pinned commit SHA")
        subprocess.run(["git", "-C", str(checkout), "merge-base", "--is-ancestor", value, "HEAD"], check=True)
    if git(checkout, "rev-parse", "HEAD") != args.head_sha:
        raise LocalCIError("campaign checkout HEAD must equal the pinned head SHA exactly")
    original = snapshot(checkout)
    if original["porcelain"]:
        raise LocalCIError("campaign evidence requires a clean pinned validation checkout")
    python = str(checkout / ".venv/bin/python")
    version = subprocess.check_output([python, "--version"], text=True).strip()
    if not version.startswith("Python 3.11."):
        raise LocalCIError("campaigns require the locked Python 3.11 environment")
    selection = classify(checkout, args.base_sha, args.head_sha, args.label)
    if any(path.startswith("src/") and path.endswith(".py") for path in selection["changed_paths"]) and not (args.id or args.module):
        raise LocalCIError("runtime-code changes require explicit affected --id or --module selectors")
    full_concurrency = getattr(args, "full_concurrency", None)
    if full_concurrency is None:
        full_concurrency = 1 if args.timing_mode == "isolated" else 4
    if type(full_concurrency) is not int or full_concurrency not in (1, 2, 3, 4):
        raise LocalCIError("full concurrency must be 1, 2, 3, or 4")
    metadata = {"schema_version": 1, "task": args.task, "base_sha": args.base_sha,
                "head_sha": args.head_sha, "source": original, "classification": selection,
                "executor": args.executor, "timing_mode": args.timing_mode, "full_concurrency": full_concurrency, "requested_full_concurrency": getattr(args, "full_concurrency", None), "python": version,
                "started_at": time.time(), "steps": [], "jobs": []}
    guest_spec = {"id": uuid.uuid4().hex, "guest_checkout": args.guest_checkout or str(checkout),
                  "executor": args.executor, "source": original,
                  "base_sha": args.base_sha, "head_sha": args.head_sha, "ci_identity": True}
    submitted: list[str] = []
    reservation = None
    code = 0

    def cleanup(action: str, identifier: str) -> None:
        nonlocal code
        try:
            control(action, identifier)
        except (Exception, KeyboardInterrupt) as error:
            if isinstance(error, KeyboardInterrupt) and code == 0:
                code = 130
                metadata.setdefault("error", "interrupted")
            metadata.setdefault("cleanup_errors", []).append(
                {"action": action, "id": identifier, "error": str(error) or "interrupted"})

    try:
        if args.timing_mode == "isolated" and selection["runtime"] == "true":
            reservation = claim({"action": "reserve", "owner": owner(), "task": args.task})
            metadata["reservation"] = wait_job(reservation, stream=False)
        started = time.time()
        if args.executor == "native":
            identity, sources = service_sources()
            bundle = prepare_service(state_root(), identity, sources)
            command = [python, str(bundle / "entry.py"), "static", "--checkout", str(checkout),
                       "--report", str(report / "static.json"), "--base", args.base_sha, "--head", args.head_sha]
            from tests.local_ci_worker import test_environment
            completed = subprocess.run(command, cwd=checkout, env=test_environment({"ci_identity": True}), capture_output=True, text=True)
        else:
            from tests.local_ci_coordinator import lima_command, prepare_lima_control
            guest_spec["guest_control"] = prepare_lima_control(guest_spec)
            metadata["guest_control"] = guest_spec["guest_control"]
            command = lima_command(guest_spec, "static-guest", "-")
            completed = subprocess.run(command, input=json.dumps(guest_spec), capture_output=True, text=True)
            if completed.returncode == 0:
                value = json.loads(completed.stdout)
                with open(report / "static.json", "xb") as target:
                    os.chmod(target.name, 0o600)
                    target.write(base64.b64decode(value["report"], validate=True))
                completed = subprocess.CompletedProcess(command, value["exit_code"], "", completed.stderr)
        metadata["steps"].append({"phase": "static", "command": command, "exit_code": completed.returncode,
                                  "stdout": completed.stdout, "stderr": completed.stderr,
                                  "execution_seconds": time.time() - started})
        code |= int(completed.returncode != 0)
        if selection["runtime"] == "true":
            selections = [("fast", ["--tier", "fast"])]
            affected = [value for option, values in (("--id", args.id), ("--module", args.module))
                        for item in values for value in (option, item)]
            if affected:
                selections.append(("affected", affected))
            for name, selectors in selections:
                identifier = submit(checkout, ["run", *selectors, "--json", str(report / (name + ".json"))],
                                    python=python, task=args.task + ":" + name, executor=args.executor,
                                    guest_checkout=args.guest_checkout, reservation=reservation, ci_identity=True)
                submitted.append(identifier)
                result = wait_job(identifier)
                metadata["jobs"].append(result)
                code |= int(result.get("exit_code", 2) != 0)
        if selection["full"] == "true":
            full_started = time.monotonic()
            identifiers: list[str] = []
            active: dict[str, int] = {}
            full_results: dict[int, dict] = {}
            next_index = 1
            outstanding_peak = 0
            while active or next_index <= 5:
                while next_index <= 5 and len(active) < full_concurrency:
                    identifier = submit(checkout, ["run", "--shard", f"{next_index}/5", "--json", str(report / f"full-{next_index}.json")],
                                        python=python, task=args.task + f":full-{next_index}", executor=args.executor,
                                        guest_checkout=args.guest_checkout, reservation=reservation, ci_identity=True)
                    identifiers.append(identifier)
                    submitted.append(identifier)
                    active[identifier] = next_index
                    next_index += 1
                    outstanding_peak = max(outstanding_peak, len(active))
                ready = []
                for identifier in active:
                    state = rpc({"action": "get", "id": identifier})["state"]
                    if state in TERMINAL or state == "quarantined":
                        ready.append(identifier)
                if not ready:
                    time.sleep(0.2)
                    continue
                for identifier in ready:
                    result = wait_job(identifier)
                    full_results[active.pop(identifier)] = result
                    metadata["jobs"].append(result)
                    code |= int(result.get("exit_code", 2) != 0)
            intervals = [(result.get("started_at"), result.get("finished_at")) for result in full_results.values()]
            peak = None
            if all(type(start) in (int, float) and type(finish) in (int, float) and finish >= start
                   for start, finish in intervals):
                running = peak = 0
                for _, delta in sorted([(start, 1) for start, _ in intervals] + [(finish, -1) for _, finish in intervals]):
                    running += delta
                    peak = max(peak, running)
            metadata["full_phase"] = {
                "elapsed_seconds": time.monotonic() - full_started,
                "shard_count": 5, "resolved_concurrency": full_concurrency,
                "outstanding_jobs_peak": outstanding_peak,
                "worker_peak_from_terminal_intervals": peak,
                "queue_wait_seconds": {f"full-{index}": full_results[index].get("queue_wait_seconds") for index in range(1, 6)},
                "terminal_states": {f"full-{index}": full_results[index].get("state") for index in range(1, 6)},
            }
            write_json(report / "full-exit-status.json", {
                f"full-{index}": rpc({"action": "get", "id": identifier}).get("worker_exit_code")
                for index, identifier in enumerate(identifiers, 1)}, exclusive=True)
            if args.executor != "native":
                guest_spec["reports"] = {name: base64.b64encode((report / name).read_bytes()).decode("ascii")
                                         for name in ("full-1.json", "full-2.json", "full-3.json", "full-4.json", "full-5.json")}
                command = lima_command(guest_spec, "finalize-guest", "-")
                completed = subprocess.run(command, input=json.dumps(guest_spec), capture_output=True, text=True)
                if completed.returncode:
                    raise LocalCIError("guest finalization failed: " + completed.stderr)
                value = json.loads(completed.stdout)
                metadata["steps"].extend(value["steps"])
                code |= int(not value["source_valid"] or any(step["exit_code"] for step in value["steps"]))
                if value.get("reconciliation"):
                    with open(report / "full-reconciled.json", "xb") as target:
                        os.chmod(target.name, 0o600)
                        target.write(base64.b64decode(value["reconciliation"], validate=True))
            for phase, arguments in (() if args.executor != "native" else (
                ("reconcile", ["-m", "tests.runner", "reconcile", *[str(report / f"full-{index}.json") for index in range(1, 6)],
                               "--json", str(report / "full-reconciled.json")]),
                ("budget", ["-m", "tests.suite_budget", "--base-ref", args.base_sha,
                            *[value for index in range(1, 6) for value in ("--report", str(report / f"full-{index}.json"))]]),
            )):
                command = [python, *arguments]
                from tests.local_ci_worker import test_environment
                completed = subprocess.run(command, cwd=checkout, env=test_environment({"ci_identity": True}), capture_output=True, text=True, umask=0o077)
                metadata["steps"].append({"phase": phase, "command": command, "exit_code": completed.returncode,
                                          "stdout": completed.stdout, "stderr": completed.stderr})
                code |= int(completed.returncode != 0)
        current = snapshot(checkout)
        metadata.update(source_finish=current, source_valid=current == original)
        code |= int(current != original)
    except (KeyboardInterrupt, LocalCIError, OSError, ValueError, subprocess.SubprocessError) as error:
        code = 130 if isinstance(error, KeyboardInterrupt) else 2
        metadata["error"] = str(error) or "interrupted"
        for identifier in submitted:
            cleanup("cancel", identifier)
        if reservation:
            cleanup("cancel", reservation)
        raise
    finally:
        if reservation:
            cleanup("release", reservation)
        if metadata.get("cleanup_errors"):
            code = code or 2
            metadata.setdefault("error", "campaign cleanup failed; inspect cleanup_errors")
        metadata.update(finished_at=time.time(), execution_seconds=time.monotonic() - campaign_started, exit_code=code)
        write_json(report / "campaign.json", metadata, exclusive=True)
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="execute the lanes selected by the pinned workflow")
    for name in ("checkout", "base-sha", "head-sha", "report-dir"):
        run.add_argument("--" + name, required=True)
    for selector in ("id", "module", "label"):
        run.add_argument("--" + selector, action="append", default=[])
    run.add_argument("--executor", default="native")
    run.add_argument("--guest-checkout")
    run.add_argument("--timing-mode", choices=("shared", "isolated"), default="shared")
    run.add_argument("--full-concurrency", choices=(1, 2, 3, 4), type=int,
                     help="full shard processes per campaign (default: shared 4, isolated 1)")
    run.add_argument("--task", default="local CI")
    commands.add_parser("status")
    cancel = commands.add_parser("cancel")
    cancel.add_argument("job_id")
    credential = cancel.add_mutually_exclusive_group(required=True)
    credential.add_argument("--token")
    credential.add_argument("--owner-file")
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return campaign(args)
        request = {"action": args.command}
        if args.command == "cancel":
            request["id"] = args.job_id
            value = read_json(Path(args.owner_file)) if args.owner_file else {"id": args.job_id, "token": args.token}
            if value.get("id") != args.job_id:
                raise LocalCIError("cancellation owner file is for another job")
            request["token"] = value["token"]
        print(json.dumps(rpc(request), indent=2, sort_keys=True))
        return 0
    except (LocalCIError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"local CI error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
