"""Bounded asynchronous subprocess transport shared by provider adapters."""

from __future__ import annotations

import asyncio
import json
import os
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

    def __post_init__(self) -> None:
        paths = (
            self.executable,
            *self.read_subpaths,
            *self.read_paths,
            *self.write_subpaths,
            *self.write_paths,
            *self.deny_subpaths,
            *self.deny_write_subpaths,
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
            ))
            or any(not isinstance(path, Path) for path in paths)
            or any(not path.is_absolute() for path in paths)
            or any(Path(os.path.realpath(path)) != path for path in paths)
            or type(self.network) is not bool
        ):
            raise ValueError("Darwin sandbox requirements must use canonical paths")


@dataclass(frozen=True)
class ProcessRequest:
    """Immutable launch and communication inputs for one subprocess."""

    command: Sequence[str]
    stdin: bytes
    timeout_seconds: float
    cwd: Path | None = None
    env: Mapping[str, str] | None = None
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
        (
            "(allow file-read-data (literal \"/\"))",
            "(allow file-read* (subpath \"/System\"))",
            "(allow file-read* (subpath \"/usr/lib\"))",
            "(allow file-read* (literal \"/dev/null\"))",
            "(allow file-write* (literal \"/dev/null\"))",
            "(allow file-read* (literal \"/dev/urandom\"))",
        )
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
