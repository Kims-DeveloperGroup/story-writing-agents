"""Bounded asynchronous subprocess transport shared by provider adapters."""

from __future__ import annotations

import asyncio
import inspect
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


StdoutLineCallback = Callable[[bytes], Awaitable[None] | None]


@dataclass(frozen=True)
class ProcessRequest:
    """Immutable launch and communication inputs for one subprocess."""

    command: Sequence[str]
    stdin: bytes
    timeout_seconds: float
    cwd: Path | None = None
    env: Mapping[str, str] | None = None
    on_stdout_line: StdoutLineCallback | None = None


@dataclass(frozen=True)
class ProcessResult:
    """Fully drained subprocess output and exit status."""

    stdout: bytes
    stderr: bytes
    returncode: int


CreateProcess = Callable[..., Awaitable[asyncio.subprocess.Process]]
WaitFor = Callable[..., Awaitable[object]]


async def _communicate_streaming(
    process: asyncio.subprocess.Process,
    stdin: bytes,
    on_stdout_line: StdoutLineCallback,
) -> tuple[bytes, bytes]:
    """Drain process streams while dispatching lines to a callback in real time."""

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []

    async def write_stdin() -> None:
        if process.stdin is None:
            return
        try:
            if stdin:
                process.stdin.write(stdin)
                await process.stdin.drain()
            process.stdin.close()
            await process.stdin.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass

    async def read_stdout() -> None:
        if process.stdout is None:
            return
        buffer = bytearray()
        search_start = 0
        while True:
            chunk = await process.stdout.read(65536)
            if not chunk:
                break
            stdout_chunks.append(chunk)
            buffer.extend(chunk)
            start = 0
            while True:
                newline_index = buffer.find(b"\n", max(start, search_start))
                if newline_index == -1:
                    search_start = len(buffer)
                    break
                line = bytes(buffer[start : newline_index + 1])
                start = newline_index + 1
                search_start = start
                try:
                    callback_result = on_stdout_line(line)
                    if inspect.isawaitable(callback_result):
                        await callback_result
                except Exception:
                    pass
            if start > 0:
                del buffer[:start]
                search_start -= start
        if buffer:
            trailing_line = bytes(buffer)
            try:
                callback_result = on_stdout_line(trailing_line)
                if inspect.isawaitable(callback_result):
                    await callback_result
            except Exception:
                pass

    async def read_stderr() -> None:
        if process.stderr is None:
            return
        while True:
            chunk = await process.stderr.read(65536)
            if not chunk:
                break
            stderr_chunks.append(chunk)

    tasks = [
        asyncio.create_task(write_stdin()),
        asyncio.create_task(read_stdout()),
        asyncio.create_task(read_stderr()),
        asyncio.create_task(process.wait()),
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return b"".join(stdout_chunks), b"".join(stderr_chunks)


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
        if (
            request.on_stdout_line is None
            or getattr(process, "stdout", None) is None
            or not hasattr(process.stdout, "readline")
        ):
            communicated = await wait_for(
                process.communicate(request.stdin),
                timeout=request.timeout_seconds,
            )
        else:
            communicated = await wait_for(
                _communicate_streaming(process, request.stdin, request.on_stdout_line),
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
