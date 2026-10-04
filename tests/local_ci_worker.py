"""Fixed local-CI operations, including supervised workers inside Lima."""
from __future__ import annotations

import argparse
import base64
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from tests.local_ci_state import (LocalCIError, environment, group_members, owner_directory,
                                  process_identity, read_json, snapshot, write_json)
from tests.local_ci_state import boot_identity, live_owned, normalize_boot_identity, observed_descendants, prepare_service, service_sources, signal_owned, verified_boot_change


def guest_root() -> Path:
    return owner_directory(Path.home() / ".cache" / "story-local-ci-guest", create=True)


def guest_job(identifier: str) -> Path:
    if len(identifier) != 32 or any(character not in "0123456789abcdef" for character in identifier):
        raise LocalCIError("invalid guest job identity")
    return guest_root() / identifier


def install_control(checkout: Path) -> dict:
    checkout = checkout.resolve(strict=True)
    interpreter = Path(sys.executable).resolve(strict=True)
    if interpreter.is_relative_to(checkout):
        raise LocalCIError("guest control requires a persistent interpreter outside the delivery checkout")
    identity, sources = service_sources()
    lock = os.open(guest_root() / "control.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return publish_control(interpreter, identity, sources)
    finally:
        os.close(lock)


def publish_control(interpreter: Path, identity: str, sources: dict[str, bytes]) -> dict:
    directory = prepare_service(guest_root(), identity, sources)
    entry = directory / "entry.py"
    raw = b"from tests.local_ci_worker import main\nraise SystemExit(main())\n"
    if not entry.exists():
        descriptor = os.open(entry, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            target.write(raw)
    elif entry.is_symlink() or entry.read_bytes() != raw:
        raise LocalCIError("guest control entry differs from its immutable recipe")
    executable = directory / "python"
    if not executable.exists():
        executable.symlink_to(interpreter)
    elif executable.resolve() != interpreter:
        raise LocalCIError("guest control interpreter entry changed")
    return {"directory": str(directory), "entry": str(entry), "python": str(executable),
            "interpreter": str(interpreter), "code_identity": identity}


def update_guest(path: Path, updates: dict, *, owned: dict | None = None) -> dict:
    descriptor = os.open(path.parent / "state.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        value = read_json(path)
        uncertain = value.get("descendant_death_uncertain", False)
        value.update(updates)
        if uncertain:
            value["descendant_death_uncertain"] = True
        if value.get("descendant_death_uncertain") and value.get("state") in {"finished", "terminated"}:
            value["state"] = "quarantined"
        if owned:
            value.setdefault("owned_processes", {}).update(owned)
        write_json(path, value)
        return value
    finally:
        os.close(descriptor)


def test_environment(spec: dict) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("GITHUB_ACTIONS", None)
    if spec.get("ci_identity"):
        raw_count = env.get("GIT_CONFIG_COUNT", "0")
        if not re.fullmatch(r"0|[1-9][0-9]*", raw_count):
            raise LocalCIError("inherited GIT_CONFIG_COUNT must be a canonical nonnegative integer")
        try:
            count = int(raw_count)
        except ValueError:
            raise LocalCIError("inherited GIT_CONFIG_COUNT is invalid") from None
        identity_config = (("user.name", "Story Tests"),
                           ("user.email", "story-tests@example.invalid"))
        present = count >= 2 and all(
            env.get(f"GIT_CONFIG_KEY_{count - 2 + index}") == key
            and env.get(f"GIT_CONFIG_VALUE_{count - 2 + index}") == value
            for index, (key, value) in enumerate(identity_config))
        if not present:
            if any(f"GIT_CONFIG_{field}_{count + index}" in env
                   for index in range(2) for field in ("KEY", "VALUE")):
                raise LocalCIError("inherited Git configuration occupies local CI append slots")
            for index, (key, value) in enumerate(identity_config):
                env[f"GIT_CONFIG_KEY_{count + index}"] = key
                env[f"GIT_CONFIG_VALUE_{count + index}"] = value
            env["GIT_CONFIG_COUNT"] = str(count + 2)
        env.update({"GIT_AUTHOR_NAME": "Story Tests",
                    "GIT_AUTHOR_EMAIL": "story-tests@example.invalid",
                    "GIT_COMMITTER_NAME": "Story Tests",
                    "GIT_COMMITTER_EMAIL": "story-tests@example.invalid"})
    return env


def static_checks(checkout: Path, report: Path, base: str, head: str, *, ci_identity: bool = False) -> int:
    workflow = checkout / ".github/workflows/pr-validation.yml"
    recipe = ('document = YAML.safe_load(File.read(ARGV.fetch(0)), aliases: false); '
              'print JSON.generate(document.fetch("jobs").fetch("static").fetch("steps")'
              '.select { |step| step.key?("run") })')
    steps = json.loads(subprocess.check_output(
        ["ruby", "-ryaml", "-rjson", "-e", recipe, str(workflow)], text=True))
    if not steps:
        raise LocalCIError("pinned workflow has no static run steps")
    before = snapshot(checkout)
    env = test_environment({"ci_identity": ci_identity})
    env.update(BASE_SHA=base, HEAD_SHA=head, PYTHONPATH="src")
    env["PATH"] = str(checkout / ".venv/bin") + os.pathsep + env.get("PATH", "")
    checks = []
    for step in steps:
        if step.get("shell", "bash") != "bash" or step.get("working-directory", ".") != ".":
            raise LocalCIError("unsupported static workflow shell or working-directory")
        started = time.time()
        command = ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]]
        replacements = {"${{ needs.classify.outputs.base_sha }}": base, "${{ needs.classify.outputs.head_sha }}": head}
        step_env = env.copy()
        for name, value in step.get("env", {}).items():
            rendered = replacements.get(str(value), str(value))
            if "${{" in rendered:
                raise LocalCIError(f"unsupported static environment expression: {name}")
            step_env[name] = rendered
        completed = subprocess.run(command, cwd=checkout, env=step_env, capture_output=True, text=True)
        checks.append({"name": step.get("name"), "command": command,
                       "exit_code": completed.returncode, "stdout": completed.stdout,
                       "stderr": completed.stderr, "execution_seconds": time.time() - started})
    after = snapshot(checkout)
    code = int(any(check["exit_code"] for check in checks) or before != after)
    write_json(report, {"schema_version": 1, "base_sha": base, "head_sha": head,
                        "before": before, "after": after, "environment": environment(),
                        "checks": checks, "source_valid": before == after, "exit_code": code}, exclusive=True)
    return code


def execute_guest(spec: dict) -> int:
    directory = owner_directory(guest_job(spec["id"]), create=True)
    path = directory / "state.json"
    if path.exists():
        raise LocalCIError("guest job already exists")
    checkout = Path(spec["guest_checkout"]).resolve(strict=True)
    before = snapshot(checkout, exclude=tuple(spec.get("excluded_outputs", ())))
    state = {"id": spec["id"], "token": spec["token"], "state": "starting",
             "before": before, "environment": environment(), "started_at": time.time(), "boot_identity": boot_identity()}
    state["supervisor"] = {"pid": os.getpid(), "process_identity": process_identity(os.getpid()),
                           "boot_identity": state["boot_identity"]}
    write_json(path, state, exclusive=True)
    if before != spec["source"]:
        state.update(state="finished", exit_code=2, source_valid=False, error="guest source differs from host")
        write_json(path, state)
        return 2
    args = list(spec["args"])
    if "--json" in args:
        index = max(index + 1 for index, value in enumerate(args[:-1]) if value == "--json")
        if args[index] != "-":
            args[index] = str(directory / "report.json")
    spec.update(args=args, directory=str(directory), checkout=str(checkout),
                python=str(checkout / ".venv/bin/python"))
    write_json(directory / "spec.json", spec, exclusive=True)
    control = spec["guest_control"]
    command = [control["python"], control["entry"], "guest-run", str(directory / "spec.json")]
    with open(directory / "stdout.log", "xb") as output, open(directory / "stderr.log", "xb") as errors:
        os.chmod(output.name, 0o600)
        os.chmod(errors.name, 0o600)
        worker = subprocess.Popen(command, cwd=checkout, env=test_environment(spec), stdout=output,
                                  stderr=errors, start_new_session=True)
        checked_at = 0.0
        while worker.poll() is None:
            if time.monotonic() - checked_at >= 1:
                current = read_json(path)
                if current.get("pid") == worker.pid:
                    update_guest(path, {}, owned=observed_descendants(worker.pid))
                    checked_at = time.monotonic()
            time.sleep(0.1)
        code = worker.returncode
    state = read_json(path)
    # A lost leader never proves that its subprocesses have also terminated.
    remaining = sorted(set(group_members(worker.pid)) | set(live_owned(state.get("owned_processes", {}))))
    after = snapshot(checkout, exclude=tuple(spec.get("excluded_outputs", ())))
    uncertain = state.get("descendant_death_uncertain", False) or code < 0 or code == 130
    update_guest(path, {"state": "quarantined" if remaining or uncertain else "finished", "exit_code": code,
                       "after": after, "source_valid": before == after, "finished_at": time.time(),
                       "remaining": remaining, "descendant_death_uncertain": uncertain})
    return code


def execute_native(spec: dict, *, guest: bool = False) -> int:
    directory = owner_directory(Path(spec["directory"]))
    path = directory / ("state.json" if guest else "worker.json")
    checkout = Path(spec["checkout"])
    before = snapshot(checkout, exclude=tuple(spec.get("excluded_outputs", ())))
    state = {"id": spec["id"], "token": spec["token"], "state": "running",
             "pid": os.getpid(), "process_identity": process_identity(os.getpid()),
             "before": before, "environment": environment(), "boot_identity": boot_identity()}
    if guest:
        state = update_guest(path, state)
    else:
        write_json(path, state, exclusive=True)
    if before != spec["source"]:
        state.update(state="stale", error="source changed before worker execution")
        if guest:
            update_guest(path, {"state": "stale", "error": "source changed before worker execution"})
        else:
            write_json(path, state)
        return 2
    env = test_environment(spec)
    env.update(PYTHONPATH="src", STORY_LOCAL_CI_JOB=spec["id"],
               STORY_LOCAL_CI_TOKEN=spec["token"])
    env["STORY_LOCAL_CI_GUEST_STATE" if guest else "STORY_LOCAL_CI_WORKER_STATE"] = str(path)
    os.chdir(checkout)
    os.execve(spec["python"], [spec["python"], "-m", "tests.runner", *spec["args"]], env)
    return 2


def campaign_guest(operation: str, spec: dict) -> dict:
    if sys.version_info[:2] != (3, 11):
        raise LocalCIError("guest campaigns require locked Python 3.11")
    directory = owner_directory(guest_job(spec["id"]), create=True)
    checkout = Path(spec["guest_checkout"]).resolve(strict=True)
    before = snapshot(checkout)
    if before != spec["source"]:
        raise LocalCIError("guest campaign source differs from pinned host inputs")
    if operation == "static-guest":
        path = directory / "static.json"
        code = static_checks(checkout, path, spec["base_sha"], spec["head_sha"], ci_identity=bool(spec.get("ci_identity")))
        return {"exit_code": code, "report": base64.b64encode(path.read_bytes()).decode("ascii")}
    for name in ("full-1.json", "full-2.json"):
        with open(directory / name, "xb") as target:
            os.chmod(target.name, 0o600)
            target.write(base64.b64decode(spec["reports"][name], validate=True))
    steps = []
    for phase, arguments in (
        ("reconcile", ["-m", "tests.runner", "reconcile", str(directory / "full-1.json"),
                       str(directory / "full-2.json"), "--json", str(directory / "full-reconciled.json")]),
        ("budget", ["-m", "tests.suite_budget", "--base-ref", spec["base_sha"],
                    "--report", str(directory / "full-1.json"), "--report", str(directory / "full-2.json")]),
    ):
        command = [str(checkout / ".venv/bin/python"), *arguments]
        completed = subprocess.run(command, cwd=checkout, env=test_environment(spec), capture_output=True, text=True)
        steps.append({"phase": phase, "command": command, "exit_code": completed.returncode,
                      "stdout": completed.stdout, "stderr": completed.stderr})
    path = directory / "full-reconciled.json"
    return {"steps": steps, "source_valid": before == snapshot(checkout),
            "reconciliation": base64.b64encode(path.read_bytes()).decode("ascii") if path.exists() else None}


def _supervisor_live(record: object, current_boot: str) -> bool:
    if not isinstance(record, dict):
        return False
    pid, identity = record.get("pid"), record.get("process_identity")
    previous_boot = normalize_boot_identity(record.get("boot_identity"))
    return (isinstance(pid, int) and not isinstance(pid, bool) and pid > 0
            and isinstance(identity, str) and bool(identity)
            and previous_boot is not None and previous_boot == normalize_boot_identity(current_boot)
            and process_identity(pid) == identity)


def probe_guest(identifier: str) -> dict:
    path = guest_job(identifier) / "state.json"
    if not path.exists():
        return {"state": "unknown", "reason": "guest has no durable execution record"}
    descriptor = os.open(path.parent / "state.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        # The finalizer uses this same lock, so no stale observation can replace its outcome.
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        state = read_json(path)
        current_boot = boot_identity()
        if verified_boot_change(state.get("boot_identity"), current_boot):
            state.update(state="terminated", remaining=[], source_valid=False,
                         boot_recovery=True, descendant_death_uncertain=False)
            write_json(path, state)
            return state
        supervisor = state.get("supervisor")
        supervisor_before = _supervisor_live(supervisor, current_boot)
        pid = state.get("pid")
        remaining = sorted(set(group_members(pid) if isinstance(pid, int) else [])
                           | set(live_owned(state.get("owned_processes", {}))))
        supervisor_after = _supervisor_live(supervisor, boot_identity())
        state["remaining"] = remaining
        if remaining or state.get("descendant_death_uncertain"):
            state["state"] = "quarantined"
        elif state["state"] in {"running", "quarantined"}:
            if not (state["state"] == "running" and supervisor_before and supervisor_after):
                state.update(state="quarantined", descendant_death_uncertain=True,
                             recovery="guest supervisor exit unknown; escaped descendants cannot be excluded")
                write_json(path, state)
        return state
    finally:
        os.close(descriptor)


def cancel_guest(identifier: str, token: str) -> dict:
    state = probe_guest(identifier)
    if state.get("token") != token:
        raise LocalCIError("guest cancellation ownership mismatch")
    pid = state.get("pid")
    if isinstance(pid, int):
        path = guest_job(identifier) / "state.json"

        def refuse(message: str) -> None:
            update_guest(path, {"descendant_death_uncertain": True,
                               "state": "quarantined", "recovery": message})
            raise LocalCIError(message)

        identity = process_identity(pid)
        if identity is not None and identity != state.get("process_identity"):
            refuse("guest process identity changed; cancellation quarantined")
        observed = None
        if identity is not None:
            observed = observed_descendants(pid)
            if process_identity(pid) != identity:
                refuse("guest process identity changed during descendant inspection")
        state = update_guest(path,
                             {"descendant_death_uncertain": True, "state": "quarantined"},
                             owned=observed)
        for action in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
            identity = process_identity(pid)
            if identity is not None:
                if identity != state.get("process_identity"):
                    refuse("guest process identity changed during cancellation")
                members = group_members(pid)
                identity = process_identity(pid)
                if identity is not None and identity != state.get("process_identity"):
                    refuse("guest process identity changed before cancellation signal")
                if identity is not None and members:
                    os.killpg(pid, action)
            if identity is None and not live_owned(state.get("owned_processes", {})):
                break
            signal_owned(state.get("owned_processes", {}), action,
                         outside_group=pid if identity is not None else None)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and (group_members(pid) or live_owned(state.get("owned_processes", {}))):
                time.sleep(0.1)
    return probe_guest(identifier)


def collect_guest(identifier: str) -> dict:
    directory = guest_job(identifier)
    result = {"state": probe_guest(identifier), "files": {}}
    for name in ("stdout.log", "stderr.log", "report.json"):
        path = directory / name
        if path.exists():
            raw = path.read_bytes()
            if len(raw) > 64 * 1024 * 1024:
                raise LocalCIError("guest evidence exceeds collection limit; preserve guest artifact")
            result["files"][name] = base64.b64encode(raw).decode("ascii")
    return result


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    static = commands.add_parser("static")
    for name in ("checkout", "report", "base", "head"):
        static.add_argument("--" + name, required=True)
    commands.add_parser("snapshot").add_argument("checkout")
    commands.add_parser("install-control").add_argument("checkout")
    commands.add_parser("execute").add_argument("spec")
    commands.add_parser("native").add_argument("spec")
    for operation in ("guest-run", "static-guest", "finalize-guest"):
        commands.add_parser(operation).add_argument("spec")
    for operation in ("probe", "collect", "cancel"):
        command = commands.add_parser(operation)
        command.add_argument("id")
        if operation == "cancel":
            command.add_argument("token")
    args = parser.parse_args(argv)
    try:
        spec = (json.loads(sys.stdin.read()) if args.spec == "-" else read_json(Path(args.spec))) if hasattr(args, "spec") else None
        if args.command == "static":
            return static_checks(Path(args.checkout), Path(args.report), args.base, args.head)
        if args.command == "install-control":
            print(json.dumps(install_control(Path(args.checkout))))
            return 0
        if args.command == "execute":
            return execute_guest(spec)
        if args.command == "native":
            return execute_native(spec)
        if args.command == "guest-run":
            return execute_native(spec, guest=True)
        if args.command in {"static-guest", "finalize-guest"}:
            print(json.dumps(campaign_guest(args.command, spec)))
            return 0
        if args.command == "snapshot":
            value = snapshot(Path(args.checkout))
        elif args.command == "probe":
            value = probe_guest(args.id)
        elif args.command == "cancel":
            value = cancel_guest(args.id, args.token)
        else:
            value = collect_guest(args.id)
        print(json.dumps(value))
        return 0
    except (LocalCIError, OSError, subprocess.SubprocessError, ValueError) as error:
        print(f"local CI worker error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
