"""Bounded asynchronous subprocess transport shared by provider adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


class ProcessExecutableNotFound(Exception):
    """The requested executable could not be resolved by the operating system."""


class ProcessStartFailed(Exception):
    """The operating system rejected process creation."""

    def __init__(self, error_type: str) -> None:
        super().__init__(error_type)
        self.error_type = error_type


class ProcessTimedOut(Exception):
    """The bounded provider process exceeded its timeout."""


@dataclass(frozen=True)
class ProcessRequest:
    """Immutable launch and communication inputs for one subprocess."""

    command: Sequence[str]
    stdin: bytes
    timeout_seconds: float
    cwd: Path | None = None
    env: Mapping[str, str] | None = None


@dataclass(frozen=True)
class ProcessResult:
    """Fully drained subprocess output and exit status."""

    stdout: bytes
    stderr: bytes
    returncode: int


CreateProcess = Callable[..., Awaitable[asyncio.subprocess.Process]]
WaitFor = Callable[..., Awaitable[object]]


async def run_process(
    request: ProcessRequest,
    *,
    create_process: CreateProcess = asyncio.create_subprocess_exec,
    wait_for: WaitFor = asyncio.wait_for,
) -> ProcessResult:
    """Launch, drain, bound, and clean up one provider subprocess."""

    launch_options: dict[str, object] = {
        "stdin": asyncio.subprocess.PIPE,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }
    if request.cwd is not None:
        launch_options["cwd"] = str(request.cwd)
    if request.env is not None:
        launch_options["env"] = dict(request.env)
    try:
        process = await create_process(*request.command, **launch_options)
    except FileNotFoundError as exc:
        raise ProcessExecutableNotFound from exc
    except OSError as exc:
        raise ProcessStartFailed(type(exc).__name__) from exc

    try:
        communicated = await wait_for(
            process.communicate(request.stdin),
            timeout=request.timeout_seconds,
        )
    except TimeoutError as exc:
        await stop_process(process, wait_for=wait_for)
        raise ProcessTimedOut from exc
    except asyncio.CancelledError:
        await stop_process(process, wait_for=wait_for)
        raise
    except BaseException:
        await stop_process(process, wait_for=wait_for)
        raise

    stdout, stderr = communicated
    assert isinstance(stdout, bytes)
    assert isinstance(stderr, bytes)
    assert process.returncode is not None
    return ProcessResult(
        stdout=stdout,
        stderr=stderr,
        returncode=process.returncode,
    )


async def stop_process(
    process: asyncio.subprocess.Process,
    *,
    wait_for: WaitFor = asyncio.wait_for,
) -> None:
    """Terminate a child and escalate to kill after a bounded wait."""

    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await wait_for(process.wait(), timeout=2)
    except TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            return
        try:
            await wait_for(process.wait(), timeout=2)
        except TimeoutError:
            return
