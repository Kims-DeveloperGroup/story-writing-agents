"""Canonical test runner: supervision owner."""
from __future__ import annotations

import math
import os
import sys
import time
from pathlib import Path
from .contracts import RunnerError
from .evidence import load_json_object, write_json
from .profile import HOSTED_CLEANUP_SECONDS, METHOD_TOKEN_ENV


def method_group_absent(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return True
    return False


def method_supervisor(config_path: str) -> int:
    """Private Linux entry: owns one worker group, never discovers applications."""
    import ctypes
    import select
    import signal
    import subprocess
    config = load_json_object(config_path)
    required = {"version", "argv", "cwd", "read_fd", "work_deadline", "deadline", "token", "receipt", "log"}
    if (set(config) != required or type(config["version"]) is not int
            or config["version"] != 1 or sys.platform != "linux"):
        raise RunnerError("invalid method supervisor configuration")
    command, work_deadline, deadline = config["argv"], config["work_deadline"], config["deadline"]
    if (type(command) is not list or not command or command[0] != sys.executable
            or any(type(value) is not str or not value for value in command)
            or type(config["read_fd"]) is not int or config["read_fd"] < 0
            or any(type(value) not in (int, float) or not math.isfinite(value)
                   for value in (work_deadline, deadline))
            or deadline != work_deadline + HOSTED_CLEANUP_SECONDS
            or type(config["token"]) is not str or len(config["token"]) != 32
            or set(config["token"]) - set("0123456789abcdef")):
        raise RunnerError("invalid method supervisor grant")
    # Linux PR_SET_CHILD_SUBREAPER: adopted descendants can be reaped after the
    # direct Popen child has been waited. Never consume Popen's status via waitpid.
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    prctl.restype = ctypes.c_int
    if prctl(36, 1, 0, 0, 0) != 0:
        raise RunnerError("method supervisor requires Linux child-subreaper ownership")
    receipt = {"version": 1, "token": config["token"], "supervisor_pid": os.getpid(),
               "worker_pid": None, "worker_exit": None, "reason": "not_started", "reaped": False,
               "group_absent": False, "complete": False, "work_deadline": work_deadline, "deadline": deadline}
    process = None
    interrupted = False
    old_handlers = {}
    def request_stop(signum, frame):
        nonlocal interrupted
        interrupted = True
    for signum in (signal.SIGINT, signal.SIGTERM):
        old_handlers[signum] = signal.signal(signum, request_stop)
    cleanup_stop = deadline - 5  # Retain five seconds for receipt publication and parent reaping.
    read_fd = config["read_fd"]
    os.set_blocking(read_fd, False)
    try:
        if interrupted or time.monotonic() >= work_deadline or select.select([read_fd], [], [], 0)[0]:
            receipt["reason"] = "no_grant_or_parent"
            return 1
        environment = dict(os.environ, **{METHOD_TOKEN_ENV: config["token"]})
        with open(config["log"], "xb", buffering=0) as log:
            if interrupted or time.monotonic() >= work_deadline:
                receipt["reason"] = "no_grant_or_parent"
                return 1
            process = subprocess.Popen(command, cwd=config["cwd"], env=environment, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
            receipt["worker_pid"] = process.pid
            receipt["reason"] = "completed"
            while process.poll() is None:
                remaining = work_deadline - time.monotonic()
                if interrupted or remaining <= 0:
                    receipt["reason"] = "interrupted" if interrupted else "deadline"
                    break
                if select.select([read_fd], [], [], min(0.1, remaining))[0]:
                    receipt["reason"] = "parent_closed"
                    break
    finally:
        if process is not None:
            if not method_group_absent(process.pid):
                if process.poll() is not None and receipt["reason"] == "completed":
                    receipt["reason"] = "descendants_survived"
                try:
                    os.killpg(process.pid, signal.SIGINT)
                except ProcessLookupError:
                    pass
                interrupt_end = min(cleanup_stop, time.monotonic() + 5)
                while time.monotonic() < interrupt_end:
                    if process.poll() is not None:
                        # Only now may waitpid reap adopted descendants.
                        try:
                            while time.monotonic() < cleanup_stop and os.waitpid(-1, os.WNOHANG)[0]:
                                pass
                        except ChildProcessError:
                            pass
                    if method_group_absent(process.pid):
                        break
                    time.sleep(min(0.05, max(0, interrupt_end - time.monotonic())))
                if not method_group_absent(process.pid):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            while time.monotonic() < cleanup_stop:
                if process.poll() is not None:
                    receipt["worker_exit"] = process.returncode
                    receipt["reaped"] = True
                    try:
                        while time.monotonic() < cleanup_stop and os.waitpid(-1, os.WNOHANG)[0]:
                            pass
                    except ChildProcessError:
                        pass
                if receipt["reaped"] and method_group_absent(process.pid):
                    receipt["group_absent"] = True
                    break
                time.sleep(min(0.05, max(0, cleanup_stop - time.monotonic())))
        receipt["complete"] = bool(receipt["reaped"] and receipt["group_absent"])
        receipt["finished"] = time.monotonic()
        write_json(config["receipt"], receipt)
        os.close(read_fd)
        for signum, previous in old_handlers.items():
            signal.signal(signum, previous)
    return 0 if receipt["complete"] and receipt["reason"] == "completed" else 1


def launch_method(command, directory: Path, *, work_deadline: float):
    import secrets
    import subprocess
    if (type(work_deadline) not in (int, float) or not math.isfinite(work_deadline)
            or time.monotonic() >= work_deadline):
        raise RunnerError("method work deadline exhausted before launch")
    deadline = work_deadline + HOSTED_CLEANUP_SECONDS
    token = secrets.token_hex(16)
    read_fd, write_fd = os.pipe()
    config = {"version": 1, "argv": list(command), "cwd": str(Path(__file__).resolve().parents[2]),
              "read_fd": read_fd, "deadline": deadline, "work_deadline": work_deadline,
              "token": token, "receipt": str(directory / "supervisor.json"), "log": str(directory / "worker.log")}
    write_json(str(directory / "config.json"), config)
    try:
        if time.monotonic() >= work_deadline:
            raise RunnerError("method work deadline exhausted before supervisor launch")
        process = subprocess.Popen([sys.executable, "-m", "tests.runner", "--_method-supervisor",
                                    str(directory / "config.json")], cwd=config["cwd"], pass_fds=(read_fd,),
                                   stdin=subprocess.DEVNULL, start_new_session=True)
    except BaseException:
        os.close(write_fd)
        raise
    finally:
        os.close(read_fd)
    return process, write_fd, config


def method_receipt(config, process):
    receipt = load_json_object(config["receipt"])
    keys = {"version", "token", "supervisor_pid", "worker_pid", "worker_exit", "reason", "reaped",
            "group_absent", "complete", "work_deadline", "deadline", "finished"}
    if (set(receipt) != keys or type(receipt["version"]) is not int or receipt["version"] != 1
            or receipt["token"] != config["token"] or type(receipt["supervisor_pid"]) is not int
            or receipt["supervisor_pid"] != process.pid or type(receipt["worker_pid"]) is not int
            or receipt["reason"] not in ("completed", "descendants_survived", "interrupted", "deadline", "parent_closed")
            or receipt["worker_pid"] <= 1 or type(receipt["worker_exit"]) is not int
            or any(receipt[name] is not True for name in ("reaped", "group_absent", "complete"))
            or receipt["work_deadline"] != config["work_deadline"] or receipt["deadline"] != config["deadline"]
            or type(receipt["finished"]) not in (int, float) or not math.isfinite(receipt["finished"])
            or receipt["finished"] > config["deadline"] or not method_group_absent(receipt["worker_pid"])):
        raise RunnerError("method supervisor cleanup proof is incomplete")
    return receipt
