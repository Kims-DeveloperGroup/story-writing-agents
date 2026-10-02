"""Bounded asynchronous subprocess transport shared by provider adapters."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .provider_execution_workspace import ProviderExecutionWorkspace


_DESCRIPTOR_EXEC_HELPER = (
    "import os,sys;fd=int(sys.argv[1]);os.fchdir(fd);os.close(fd);"
    "os.execvpe(sys.argv[2],sys.argv[2:],os.environ)"
)
_DARWIN_SANDBOX_EXECUTABLE = Path("/usr/bin/sandbox-exec")


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
class DarwinSandboxRequirements:
    """Exact provider resources admitted by the shared Darwin boundary."""

    executable: Path
    read_subpaths: tuple[Path, ...] = ()
    read_paths: tuple[Path, ...] = ()
    write_subpaths: tuple[Path, ...] = ()
    write_paths: tuple[Path, ...] = ()
    deny_subpaths: tuple[Path, ...] = ()
    deny_write_subpaths: tuple[Path, ...] = ()
    network: bool = False
    metadata_paths: tuple[Path, ...] = ()
    posix_shm_read_prefixes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        paths = (
            self.executable,
            *self.read_subpaths,
            *self.read_paths,
            *self.write_subpaths,
            *self.write_paths,
            *self.deny_subpaths,
            *self.deny_write_subpaths,
            *self.metadata_paths,
        )
        if (
            not isinstance(self.executable, Path)
            or any(type(group) is not tuple for group in (
                self.read_subpaths,
                self.read_paths,
                self.write_subpaths,
                self.write_paths,
                self.deny_subpaths,
                self.deny_write_subpaths,
                self.metadata_paths,
            ))
            or any(not isinstance(path, Path) for path in paths)
            or any(not path.is_absolute() for path in paths)
            or any(Path(os.path.realpath(path)) != path for path in paths)
            or type(self.network) is not bool
        ):
            raise ValueError("Darwin sandbox requirements must use canonical paths")
        if type(self.posix_shm_read_prefixes) is not tuple or any(
            type(prefix) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", prefix) is None
            for prefix in self.posix_shm_read_prefixes
        ):
            raise ValueError("Darwin shared-memory read prefixes must use safe names")


@dataclass(frozen=True)
class ProcessRequest:
    """Immutable launch and communication inputs for one subprocess."""

    command: Sequence[str]
    stdin: bytes
    timeout_seconds: float
    cwd: Path | None = None
    env: Mapping[str, str] | None = None
    on_stdout_line: StdoutLineCallback | None = None
    execution_workspace: ProviderExecutionWorkspace | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    darwin_sandbox: DarwinSandboxRequirements | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if self.execution_workspace is not None and (
            type(self.execution_workspace) is not ProviderExecutionWorkspace
            or self.cwd is not None
        ):
            raise ValueError(
                "process cwd and provider execution workspace are mutually exclusive"
            )
        if self.darwin_sandbox is not None and (
            type(self.darwin_sandbox) is not DarwinSandboxRequirements
            or self.execution_workspace is None
        ):
            raise ValueError(
                "Darwin provider confinement requires an execution workspace"
            )


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
                drain = process.stdin.drain()
                if inspect.isawaitable(drain):
                    await drain
            close = getattr(process.stdin, "close", None)
            if callable(close):
                close()
            wait_closed = getattr(process.stdin, "wait_closed", None)
            if callable(wait_closed):
                res = wait_closed()
                if inspect.isawaitable(res):
                    await res
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
        asyncio.ensure_future(write_stdin()),
        asyncio.ensure_future(read_stdout()),
        asyncio.ensure_future(read_stderr()),
        asyncio.ensure_future(process.wait()),
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
    execution_workspace = request.execution_workspace
    launch_descriptor = -1
    command: Sequence[str] = request.command
    if execution_workspace is not None:
        if os.name != "posix" or not hasattr(os, "fchdir"):
            raise ProcessStartFailed("UnsupportedPlatform")
        execution_workspace.require_current()
        if request.darwin_sandbox is not None:
            command = _darwin_confined_command(
                command,
                request.darwin_sandbox,
            )
        launch_descriptor = execution_workspace.duplicate_launch_descriptor()
        command = (
            os.path.realpath(sys.executable),
            "-I",
            "-c",
            _DESCRIPTOR_EXEC_HELPER,
            str(launch_descriptor),
            *command,
        )
        launch_options["pass_fds"] = (launch_descriptor,)
    elif request.cwd is not None:
        launch_options["cwd"] = str(request.cwd)
    if request.env is not None:
        launch_options["env"] = dict(request.env)
    try:
        process = await create_process(*command, **launch_options)
    except FileNotFoundError as exc:
        _close_launch_descriptor(launch_descriptor, failure=exc)
        raise ProcessExecutableNotFound from exc
    except OSError as exc:
        _close_launch_descriptor(launch_descriptor, failure=exc)
        raise ProcessStartFailed(type(exc).__name__) from exc
    except BaseException as exc:
        _close_launch_descriptor(launch_descriptor, failure=exc)
        raise
    else:
        try:
            _close_launch_descriptor(launch_descriptor)
        except BaseException:
            await stop_process(process, wait_for=wait_for)
            raise

    if execution_workspace is not None:
        try:
            execution_workspace.require_current()
        except BaseException:
            await stop_process(process, wait_for=wait_for)
            raise

    try:
        if (
            request.on_stdout_line is None
            or getattr(process, "stdout", None) is None
            or not hasattr(process.stdout, "read")
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

    if execution_workspace is not None:
        execution_workspace.require_current()

    stdout, stderr = communicated
    assert isinstance(stdout, bytes)
    assert isinstance(stderr, bytes)
    assert process.returncode is not None
    return ProcessResult(
        stdout=stdout,
        stderr=stderr,
        returncode=process.returncode,
    )


def _darwin_confined_command(
    command: Sequence[str],
    requirements: DarwinSandboxRequirements,
) -> tuple[str, ...]:
    """Apply the shared, fail-closed Darwin SBPL process boundary."""

    if sys.platform != "darwin" or not _DARWIN_SANDBOX_EXECUTABLE.is_file():
        raise ProcessStartFailed("UnsupportedDarwinSandbox")
    if (
        not command
        or type(command[0]) is not str
        or Path(os.path.realpath(command[0])) != requirements.executable
    ):
        raise ProcessStartFailed("InvalidDarwinSandboxExecutable")
    return (
        os.fspath(Path(os.path.realpath(_DARWIN_SANDBOX_EXECUTABLE))),
        "-p",
        _darwin_sandbox_profile(requirements),
        *command,
    )


def _darwin_sandbox_profile(
    requirements: DarwinSandboxRequirements,
) -> str:
    """Compile exact provider requirements into the generic Darwin policy."""

    def literal(path: Path | str) -> str:
        return json.dumps(os.fspath(path), ensure_ascii=True)

    rules = [
        "(version 1)",
        "(deny default)",
        "(allow process-exec (literal "
        + literal(requirements.executable)
        + "))",
        "(allow process-info*)",
        "(allow signal (target self))",
        "(allow sysctl-read)",
        "(allow mach-lookup)",
    ]
    if requirements.network:
        rules.extend(("(allow network-outbound)", "(allow system-socket)"))
    rules.extend(
        "(allow ipc-posix-shm-read* (ipc-posix-name-prefix " + literal(prefix) + "))"
        for prefix in requirements.posix_shm_read_prefixes
    )
    rules.extend(
        (
            "(allow file-read-data (literal \"/\"))",
            # Resolving Darwin's system configuration alias needs lstat access
            # to the alias itself, not only its canonical /private/etc target.
            "(allow file-read-metadata (literal \"/etc\"))",
            "(allow file-read* (subpath \"/System\"))",
            "(allow file-read* (subpath \"/usr/lib\"))",
            "(allow file-read* (literal \"/dev/null\"))",
            "(allow file-write* (literal \"/dev/null\"))",
            "(allow file-read* (literal \"/dev/urandom\"))",
        )
    )
    resources = (
        requirements.executable,
        *requirements.read_subpaths,
        *requirements.read_paths,
        *requirements.write_subpaths,
        *requirements.write_paths,
        *requirements.metadata_paths,
    )
    metadata_paths = {
        ancestor
        for resource in resources
        for ancestor in (resource, *resource.parents)
    }
    rules.extend(
        "(allow file-read-metadata (literal " + literal(path) + "))"
        for path in sorted(metadata_paths)
    )
    rules.extend(
        "(allow file-read* (subpath " + literal(path) + "))"
        for path in requirements.read_subpaths
    )
    rules.extend(
        "(allow file-read* (literal " + literal(path) + "))"
        for path in requirements.read_paths
    )
    rules.extend(
        "(allow file-read* (subpath " + literal(path) + "))\n"
        "(allow file-write* (subpath " + literal(path) + "))"
        for path in requirements.write_subpaths
    )
    rules.extend(
        "(allow file-read* (literal " + literal(path) + "))\n"
        "(allow file-write* (literal " + literal(path) + "))"
        for path in requirements.write_paths
    )
    rules.extend(
        "(deny file-read* (literal " + literal(path) + "))\n"
        "(deny file-read* (subpath " + literal(path) + "))\n"
        "(deny file-write* (literal " + literal(path) + "))\n"
        "(deny file-write* (subpath " + literal(path) + "))"
        for path in requirements.deny_subpaths
    )
    rules.extend(
        "(deny file-write* (literal " + literal(path) + "))\n"
        "(deny file-write* (subpath " + literal(path) + "))"
        for path in requirements.deny_write_subpaths
    )
    return "\n".join(rules)


def _close_launch_descriptor(
    descriptor: int,
    *,
    failure: BaseException | None = None,
) -> None:
    """Close parent transfer authority without replacing an active failure."""

    if descriptor < 0:
        return
    try:
        os.close(descriptor)
    except BaseException as cleanup_failure:
        if failure is None:
            raise
        try:
            failure.add_note(
                "provider launch descriptor cleanup failed: "
                f"{type(cleanup_failure).__name__}"
            )
        except BaseException:
            pass


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
