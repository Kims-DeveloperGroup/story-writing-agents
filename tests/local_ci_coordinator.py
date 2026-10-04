"""Single durable host coordinator for four native or Lima test executions."""
from __future__ import annotations

import base64
import fcntl
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

from tests.local_ci_state import (ACTIVE, CAPACITY, TERMINAL, LocalCIError, environment,
                                  group_members, owner_directory, process_identity,
                                  read_json, service_sources, snapshot, state_root, write_json)
from tests.local_ci_state import boot_identity, live_owned, observed_descendants, prepare_service, signal_owned, verified_boot_change

MAX_REQUEST = 16 * 1024 * 1024

# Executed by the prepared guest interpreter; it never imports target-checkout
# tooling. Only this exact module set and entry recipe can be published.
GUEST_BOOTSTRAP = r'''
import base64, fcntl, hashlib, json, os, stat, sys
from pathlib import Path
spec = json.load(sys.stdin)
names = ("local_ci.py", "local_ci_coordinator.py", "local_ci_state.py", "local_ci_worker.py")
if set(spec["sources"]) != set(names):
    raise SystemExit("invalid fixed guest control module set")
sources = {name: base64.b64decode(spec["sources"][name], validate=True) for name in names}
digest = hashlib.sha256()
for name, raw in sources.items():
    digest.update(name.encode() + b"\0" + raw + b"\0")
if digest.hexdigest() != spec["identity"]:
    raise SystemExit("guest control content identity differs")
checkout = Path(spec["checkout"]).resolve(strict=True)
interpreter = Path(sys.executable).resolve(strict=True)
if interpreter.is_relative_to(checkout):
    raise SystemExit("guest control interpreter must persist outside delivery checkout")
def directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise SystemExit("guest control directory is not owner-only")
    return path
root = directory(Path.home() / ".cache" / "story-local-ci-guest")
lock = os.open(root / "control.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
fcntl.flock(lock, fcntl.LOCK_EX)
bundle = root / "services" / spec["identity"]
tests = directory(bundle / "tests")
def publish(path, raw):
    if path.exists():
        if path.is_symlink() or path.read_bytes() != raw:
            raise SystemExit("immutable guest control leaf differs")
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as target:
        target.write(raw)
publish(tests / "__init__.py", b"")
for name, raw in sources.items():
    publish(tests / name, raw)
entry = bundle / "entry.py"
publish(entry, b"from tests.local_ci_worker import main\nraise SystemExit(main())\n")
executable = bundle / "python"
if executable.exists():
    if executable.resolve() != interpreter:
        raise SystemExit("immutable guest interpreter entry differs")
else:
    executable.symlink_to(interpreter)
print(json.dumps({"directory": str(bundle), "entry": str(entry), "python": str(executable),
                  "interpreter": str(interpreter), "code_identity": spec["identity"]}))
'''


def lima_command(job: dict, operation: str, *arguments: str) -> list[str]:
    instance = job["executor"].split(":", 1)[1]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", instance):
        raise LocalCIError("invalid Lima instance name")
    if job.get("guest_control"):
        control = job["guest_control"]
        return ["limactl", "shell", "--workdir", control["directory"], instance,
                "env", "PYTHONPATH=" + control["directory"], control["python"],
                control["entry"], operation, *arguments]
    raise LocalCIError("durable Lima worker control metadata missing; admission must remain quarantined")


def prepare_lima_control(job: dict) -> dict:
    identity, sources = service_sources()
    instance = job["executor"].split(":", 1)[1]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", instance):
        raise LocalCIError("invalid Lima instance name")
    checkout = job["guest_checkout"]
    command = ["limactl", "shell", "--workdir", checkout, instance,
               checkout + "/.venv/bin/python", "-c", GUEST_BOOTSTRAP]
    payload = {"identity": identity, "checkout": checkout,
               "sources": {name: base64.b64encode(raw).decode("ascii") for name, raw in sources.items()}}
    result = subprocess.run(command, input=json.dumps(payload), capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise LocalCIError("guest control bootstrap failed: " + result.stderr.strip())
    control = json.loads(result.stdout)
    if control.get("code_identity") != identity:
        raise LocalCIError("guest worker control revision differs from the host coordinator")
    return control


def validate_arguments(arguments: list[str]) -> None:
    if not arguments or arguments[0] != "run":
        raise LocalCIError("coordinator accepts only canonical runner run requests")
    values = {"--id", "--module", "--tier", "--shard", "--json", "--slowest"}
    index = 1
    while index < len(arguments):
        option = arguments[index]
        if option == "--failfast":
            index += 1
        elif option in values and index + 1 < len(arguments):
            index += 2
        else:
            raise LocalCIError(f"unsupported canonical runner argument: {option}")


class Coordinator:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "state.json"
        self.state = read_json(self.path) if self.path.exists() else {
            "schema_version": 1, "capacity": CAPACITY, "host": environment(), "jobs": []}
        if self.state.get("schema_version") != 1 or self.state.get("capacity") != CAPACITY:
            raise LocalCIError("incompatible coordinator state; preserve it for recovery")
        self.processes: dict[str, subprocess.Popen] = {}
        self.streams: dict[str, tuple] = {}
        self.code_identity = service_sources()[0]
        self.boot = boot_identity()
        self.stop_requested = False
        for job in self.state["jobs"]:
            if job["state"] in ACTIVE:
                if verified_boot_change(job.get("host_boot_identity"), self.boot):
                    job.update(state="failed", exit_code=2, source_valid=False, finished_at=time.time(),
                               error="host boot changed; old descendants proven dead, prior execution evidence invalid")
                else:
                    job.update(state="quarantined", descendant_death_uncertain=True,
                               recovery="coordinator restarted; complete descendant death proof unavailable")
        self.save()

    def save(self) -> None:
        write_json(self.path, self.state)

    def find(self, identifier: str) -> dict:
        for job in self.state["jobs"]:
            if job["id"] == identifier:
                return job
        raise LocalCIError("unknown local CI job")

    def public(self, job: dict) -> dict:
        hidden = {"source", "source_start", "source_finish", "token"}
        return {name: value for name, value in job.items() if name not in hidden}

    def request(self, request: dict) -> dict:
        action = request.get("action")
        if request.get("code_identity", self.code_identity) != self.code_identity and action in {"submit", "reserve"}:
            if any(job["state"] not in TERMINAL for job in self.state["jobs"]):
                raise LocalCIError("another coordinator revision still owns work; wait for its jobs to finish")
            self.stop_requested = True
            return {"idle_restart": True}
        if action == "status":
            return {"capacity": CAPACITY, "executing_or_quarantined": len(self.active()),
                    "code_identity": self.code_identity,
                    "jobs": [self.public(job) for job in self.state["jobs"]]}
        if action == "get":
            return self.public(self.find(request["id"]))
        if action in {"submit", "reserve"}:
            owner = request["owner"]
            if not isinstance(owner, dict) or process_identity(owner["pid"]) != owner["identity"]:
                raise LocalCIError("submitting client process identity is stale")
            job = {"id": uuid.uuid4().hex, "token": secrets.token_hex(32), "state": "queued",
                   "kind": "reservation" if action == "reserve" else "runner",
                   "owner": owner, "task": request.get("task", "local runner"),
                   "coordinator_code_identity": self.code_identity,
                   "host_boot_identity": self.boot,
                   "queued_at": time.time(), "sequence": len(self.state["jobs"]) + 1}
            directory = owner_directory(self.root / "jobs" / job["id"], create=True)
            job["directory"] = str(directory)
            write_json(directory / "ownership.json", {"id": job["id"], "token": job["token"]}, exclusive=True)
            if action == "submit":
                validate_arguments(request["args"])
                executor = request.get("executor", "native")
                if executor != "native" and not re.fullmatch(r"lima:[A-Za-z0-9][A-Za-z0-9_.-]*", executor):
                    raise LocalCIError("executor must be native or lima:INSTANCE")
                checkout = str(Path(request["checkout"]).resolve(strict=True))
                source = snapshot(Path(checkout), exclude=tuple(request.get("excluded_outputs", ())))
                if source != request["source"]:
                    raise LocalCIError("source changed before enqueue")
                reservation = request.get("reservation")
                if reservation:
                    claim = self.find(reservation)
                    if (claim["kind"] != "reservation" or claim["owner"] != owner
                            or claim["state"] != "reserved" or request.get("reservation_token") != claim["token"]):
                        raise LocalCIError("isolated reservation ownership or state mismatch")
                job.update(checkout=checkout, source=source, args=request["args"],
                           python=request["python"], executor=executor,
                           guest_checkout=request.get("guest_checkout", checkout), reservation=reservation,
                           ci_identity=bool(request.get("ci_identity", False)))
                job["excluded_outputs"] = request.get("excluded_outputs", [])
            self.state["jobs"].append(job)
            self.save()
            return {"id": job["id"], "token": job["token"]}
        if action == "release":
            job = self.find(request["id"])
            if request.get("token") != job["token"]:
                raise LocalCIError("reservation release ownership mismatch")
            if job["kind"] != "reservation":
                raise LocalCIError("not an isolated reservation")
            job["release_requested"] = True
            self.save()
            return self.public(job)
        if action == "cancel":
            job = self.find(request["id"])
            if request.get("token") != job["token"]:
                raise LocalCIError("cancellation ownership mismatch")
            self.cancel(job)
            self.save()
            return self.public(job)
        raise LocalCIError("unknown coordinator operation")

    def active(self) -> list[dict]:
        return [job for job in self.state["jobs"] if job["state"] in ACTIVE]

    def event(self, job: dict, name: str) -> None:
        job.setdefault("events", []).append({"event": name, "at": time.time(),
                                             "active_ids": [item["id"] for item in self.active()]})

    def launch(self, job: dict) -> None:
        try:
            current = snapshot(Path(job["checkout"]), exclude=tuple(job.get("excluded_outputs", ())))
            if current != job["source"]:
                job.update(state="stale", exit_code=2, error="source changed while waiting", source_finish=current)
                return
            job.update(source_start=current, state="starting", started_at=time.time(),
                       queue_wait_seconds=time.time() - job["queued_at"],
                       overlap_ids=[item["id"] for item in self.active() if item is not job])
            self.event(job, "admitted")
            self.save()  # Persist uncertain admission before creating any process.
            if job["executor"] != "native":
                job["guest_control"] = prepare_lima_control(job)
                self.save()  # Controls are durable before any guest test may start.
            env = os.environ.copy()
            env["PYTHONPATH"] = "src"
            spec = {key: job[key] for key in ("id", "token", "checkout", "source", "args",
                                             "python", "directory", "guest_checkout", "ci_identity", "excluded_outputs")}
            if job.get("guest_control"):
                spec["guest_control"] = job["guest_control"]
            directory = Path(job["directory"])
            write_json(directory / "spec.json", spec, exclusive=True)
            if job["executor"] == "native":
                identity, sources = service_sources()
                bundle = prepare_service(self.root, identity, sources)
                command = [job["python"], str(bundle / "entry.py"), "native", str(directory / "spec.json")]
            else:
                command = lima_command(job, "execute", "-")
            output = open(directory / "stdout.log", "xb")
            errors = open(directory / "stderr.log", "xb")
            os.chmod(output.name, 0o600)
            os.chmod(errors.name, 0o600)
            self.streams[job["id"]] = (output, errors)
            with open(directory / "spec.json", "rb") as specification:
                worker = subprocess.Popen(command, cwd=job["checkout"], env=env,
                                          stdin=specification if job["executor"] != "native" else subprocess.DEVNULL,
                                          stdout=output, stderr=errors, start_new_session=True)
            self.processes[job["id"]] = worker
            job.update(state="running", pid=worker.pid)
            job.update(process_identity=process_identity(worker.pid),
                       command=command)
        except (LocalCIError, OSError, subprocess.SubprocessError) as error:
            if job["id"] in self.processes:
                job.update(state="quarantined", recovery=str(error))
            else:
                job.update(state="failed", exit_code=2, error=str(error))
                for stream in self.streams.pop(job["id"], ()):
                    stream.close()
        self.save()

    def guest_rpc(self, job: dict, operation: str, *args: str) -> dict:
        result = subprocess.run(lima_command(job, operation, *args), capture_output=True,
                                text=True, timeout=15)
        if result.returncode:
            raise LocalCIError(result.stderr.strip() or f"Lima {operation} failed")
        return json.loads(result.stdout)

    def finish(self, job: dict, code: int | None) -> None:
        terminated = False
        job["worker_exit_code"] = code
        if code is None or code < 0 or code == 130:
            job["descendant_death_uncertain"] = True
        try:
            if job["executor"] != "native":
                collected = self.guest_rpc(job, "collect", job["id"])
                guest = collected["state"]
                job["guest"] = {key: value for key, value in guest.items() if key != "token"}
                if guest.get("descendant_death_uncertain"):
                    job["descendant_death_uncertain"] = True
                if guest.get("boot_recovery"):
                    job["descendant_death_uncertain"] = False
                    job["guest_boot_recovery"] = True
                job["guest_source_valid"] = guest.get("source_valid", False) and not guest.get("boot_recovery")
                code = guest.get("exit_code", code)
                job["worker_exit_code"] = code
                for name, encoded in collected["files"].items():
                    if name not in {"stdout.log", "stderr.log", "report.json"}:
                        raise LocalCIError("unexpected guest evidence file")
                    destination = Path(job["directory"]) / ("guest-" + name)
                    if not destination.exists():
                        with open(destination, "xb") as target:
                            os.chmod(target.name, 0o600)
                            target.write(base64.b64decode(encoded, validate=True))
                if "report.json" in collected["files"] and "--json" in job["args"]:
                    position = max(index + 1 for index, value in enumerate(job["args"][:-1]) if value == "--json")
                    destination = job["args"][position]
                    if destination != "-" and not job.get("report_exported"):
                        with open(destination, "xb") as target:
                            os.chmod(target.name, 0o600)
                            target.write(base64.b64decode(collected["files"]["report.json"], validate=True))
                        job["report_exported"] = True
                if guest["state"] not in {"finished", "terminated"} or guest.get("remaining"):
                    raise LocalCIError("guest execution or owned descendant termination has not been proven")
                terminated = True
            elif group_members(job["pid"]):
                raise LocalCIError("native worker group still has executing members")
            else:
                if live_owned(job.get("owned_processes", {})):
                    raise LocalCIError("owned native descendants remain alive outside the original process group")
                if job.get("descendant_death_uncertain"):
                    raise LocalCIError("complete owned descendant death proof unavailable; admission retained")
                terminated = True
                if (Path(job["directory"]) / "worker.json").exists():
                    worker = read_json(Path(job["directory"]) / "worker.json")
                    job["worker_environment"] = worker["environment"]
                    job["worker_source_start"] = worker["before"]["digest"]
            current = snapshot(Path(job["checkout"]), exclude=tuple(job.get("excluded_outputs", ())))
            if job.get("descendant_death_uncertain"):
                terminated = False
                raise LocalCIError("complete owned descendant death proof unavailable; admission retained")
            valid = current == job["source"] and job.get("guest_source_valid", True)
            job.update(source_finish=current, source_valid=valid, worker_exit_code=code,
                       exit_code=130 if job.get("cancel_requested") else code if valid and code is not None else 2,
                       state="cancelled" if job.get("cancel_requested") else "finished",
                       finished_at=time.time(), execution_seconds=time.time() - job["started_at"])
            if code is None:
                job["error"] = "worker exit code was lost during coordinator restart"
            if not valid:
                job["error"] = "source changed during execution; evidence invalid"
            self.event(job, "terminated")
        except (LocalCIError, OSError, subprocess.SubprocessError, ValueError) as error:
            if terminated:
                job.update(state="cancelled" if job.get("cancel_requested") else "failed",
                           worker_exit_code=code, exit_code=130 if job.get("cancel_requested") else 2,
                           source_valid=False, finished_at=time.time(), error=str(error))
            else:
                job.update(state="quarantined", recovery=str(error))
        self.save()

    def _signal_native(self, job: dict, action: int) -> bool:
        pid = job["pid"]
        identity = process_identity(pid)
        if identity is not None:
            if identity != job.get("process_identity"):
                raise LocalCIError("worker PID reused; cancellation refused")
            members = group_members(pid)
            identity = process_identity(pid)
            if identity is not None and identity != job.get("process_identity"):
                raise LocalCIError("worker identity changed before cancellation signal")
            if identity is not None and members:
                os.killpg(pid, action)
        signal_owned(job.get("owned_processes", {}), action,
                     outside_group=pid if identity is not None else None)
        return identity is not None

    def cancel(self, job: dict) -> None:
        if job["kind"] == "reservation":
            job["release_requested"] = True
            for child in self.state["jobs"]:
                if child.get("reservation") == job["id"]:
                    self.cancel(child)
            if job["state"] == "queued":
                job.update(state="cancelled", finished_at=time.time())
            return
        if job["state"] in TERMINAL:
            return
        job["cancel_requested"] = True
        if job["state"] == "queued":
            job.update(state="cancelled", exit_code=130, finished_at=time.time())
            return
        job["descendant_death_uncertain"] = True
        if job.get("executor") == "native" and job.get("pid"):
            try:
                identity = process_identity(job["pid"])
                if identity is not None:
                    if identity != job.get("process_identity"):
                        raise LocalCIError("worker PID reused; cancellation refused")
                    observed = observed_descendants(job["pid"])
                    if process_identity(job["pid"]) != identity:
                        raise LocalCIError("worker identity changed during descendant inspection")
                    job.setdefault("owned_processes", {}).update(observed)
            except (OSError, LocalCIError) as error:
                job.update(state="quarantined", recovery=str(error))
                self.save()
                return
        self.save()  # Journal uncertainty and observed ownership before signalling.
        if job["executor"] != "native":
            try:
                self.guest_rpc(job, "cancel", job["id"], job["token"])
            except (LocalCIError, OSError, subprocess.SubprocessError, ValueError) as error:
                job.update(state="quarantined", recovery=str(error))
                return
        pid = job.get("pid")
        if not pid:
            job.update(state="quarantined", recovery="admitted worker identity unavailable")
            return
        try:
            members = group_members(pid)
            if members or live_owned(job.get("owned_processes", {})):
                leader_live = self._signal_native(job, signal.SIGINT)
                job.update(state="cancelling" if leader_live else "quarantined",
                           cancel_at=time.time(), cancel_stage="interrupt")
            else:
                self.finish(job, None)
        except (OSError, LocalCIError) as error:
            job.update(state="quarantined", recovery=str(error))

    def tick(self) -> None:
        before = json.dumps(self.state, sort_keys=True)
        for job in self.active():
            if (job.get("executor") == "native" and job.get("pid")
                    and time.time() - job.get("descendants_checked_at", 0) > 1
                    and (identity := process_identity(job["pid"])) is not None
                    and identity == job.get("process_identity")):
                observed = observed_descendants(job["pid"])
                inspected_identity = process_identity(job["pid"])
                if inspected_identity == identity:
                    job.setdefault("owned_processes", {}).update(observed)
                elif inspected_identity is not None or job["id"] not in self.processes:
                    job.update(state="quarantined", descendant_death_uncertain=True,
                               recovery="worker identity changed during descendant inspection")
                job["descendants_checked_at"] = time.time()
            process = self.processes.get(job["id"])
            if process is not None and process.poll() is not None:
                self.processes.pop(job["id"])
                for stream in self.streams.pop(job["id"], ()):
                    stream.close()
                self.finish(job, process.returncode)
            elif (job["state"] in {"cancelling", "quarantined"} and job.get("cancel_requested")
                  and job.get("cancel_stage") and time.time() - job["cancel_at"] > 3):
                pid = job["pid"]
                try:
                    stage = job.get("cancel_stage", "terminate")
                    action = signal.SIGTERM if stage == "interrupt" else signal.SIGKILL
                    self._signal_native(job, action)
                    job.update(cancel_stage="terminate" if stage == "interrupt" else "kill", cancel_at=time.time())
                    if not group_members(pid) and not live_owned(job.get("owned_processes", {})):
                        self.finish(job, None)
                except (OSError, LocalCIError) as error:
                    job.update(state="quarantined", recovery=str(error))
            elif process is None and job["state"] == "quarantined":
                if job.get("descendant_death_uncertain") and job.get("executor") == "native":
                    continue
                if job.get("executor") == "native":
                    worker_file = Path(job["directory"]) / "worker.json"
                    if worker_file.exists() and not job.get("pid"):
                        worker = read_json(worker_file)
                        job.update(pid=worker["pid"], process_identity=worker["process_identity"])
                    if job.get("pid") and not group_members(job["pid"]):
                        self.finish(job, None)
                elif time.time() - job.get("last_probe", 0) > 10:
                    job["last_probe"] = time.time()
                    self.finish(job, None)
        for job in self.state["jobs"]:
            if (job["state"] not in TERMINAL and not job.get("orphaned")
                    and time.time() - job.get("owner_checked_at", 0) > 2):
                job["owner_checked_at"] = time.time()
                owner = job["owner"]
                if process_identity(owner["pid"]) != owner["identity"]:
                    job["orphaned"] = True
                    self.cancel(job)
        reserved = next((job for job in self.state["jobs"] if job["kind"] == "reservation"
                         and job["state"] == "reserved"), None)
        if reserved and reserved.get("release_requested"):
            if not any(job.get("reservation") == reserved["id"] and job["state"] not in TERMINAL
                       for job in self.state["jobs"]):
                reserved.update(state="finished", finished_at=time.time())
                reserved = None
        waiting = [job for job in self.state["jobs"] if job["state"] == "queued"]
        for job in waiting:
            if reserved and job.get("reservation") != reserved["id"]:
                continue
            if job["kind"] == "reservation":
                if self.active() or reserved:
                    break
                job.update(state="reserved", started_at=time.time(),
                           queue_wait_seconds=time.time() - job["queued_at"])
                reserved = job
            elif len(self.active()) < CAPACITY:
                self.launch(job)
            else:
                break
        if json.dumps(self.state, sort_keys=True) != before:
            self.save()


def serve() -> None:
    root = state_root()
    lock = os.open(root / "server.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    coordinator = Coordinator(root)
    path = root / "coordinator.sock"
    if path.exists():
        path.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    os.chmod(path, 0o600)
    server.listen(32)
    server.settimeout(0.2)
    write_json(root / "service.json", {"pid": os.getpid(), "identity": process_identity(os.getpid()),
                                       "code_identity": coordinator.code_identity})
    try:
        while True:
            coordinator.tick()
            try:
                client, _address = server.accept()
            except TimeoutError:
                continue
            with client:
                client.settimeout(2)
                try:
                    data = b""
                    while not data.endswith(b"\n"):
                        block = client.recv(65536)
                        if not block:
                            raise LocalCIError("incomplete coordinator request")
                        data += block
                        if len(data) > MAX_REQUEST:
                            raise LocalCIError("coordinator request exceeds limit")
                    result = coordinator.request(json.loads(data))
                    response = {"ok": True, "result": result}
                except (LocalCIError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
                    response = {"ok": False, "error": str(error)}
                try:
                    client.sendall(json.dumps(response).encode() + b"\n")
                except OSError:
                    pass
            if coordinator.stop_requested:
                break
    finally:
        server.close()
        path.unlink(missing_ok=True)
        os.close(lock)


if __name__ == "__main__":
    serve()
