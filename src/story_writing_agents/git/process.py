"""Package-internal bounded process owner for trusted fixed Git adapters."""

from __future__ import annotations

import os
import stat
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from .contracts import GitFailureCode


_GIT_EXECUTABLE = "git"
_PROCESS_TIMEOUT_SECONDS = 30.0
_PROCESS_STOP_TIMEOUT_SECONDS = 2.0
_MAX_STDOUT_BYTES = 8 * 1024 * 1024
_MAX_STDERR_BYTES = 64 * 1024
_MAX_STDIN_BYTES = 64 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024
_SAFE_CONFIG = (
    "-c",
    f"core.hooksPath={os.devnull}",
    "-c",
    "commit.gpgSign=false",
    "-c",
    "tag.gpgSign=false",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "diff.external=",
    "-c",
    "pager.status=false",
    "-c",
    "pager.diff=false",
    "-c",
    "pager.log=false",
)
_IDENTITY_ENVIRONMENT = frozenset(
    {
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    }
)


@dataclass(frozen=True, slots=True)
class GitProcessResult:
    """Bounded process result retained by a trusted Git adapter."""

    stdout: bytes
    returncode: int


class GitProcessFailure(Exception):
    """Content-free internal process failure translated by the calling adapter."""

    __slots__ = ("code", "exit_code")

    def __init__(self, code: GitFailureCode, *, exit_code: int | None = None) -> None:
        self.code = code
        self.exit_code = exit_code
        super().__init__(code.value)


class _BoundedCapture:
    __slots__ = ("_stream", "_maximum", "data", "exceeded", "failed")

    def __init__(self, stream: object, maximum: int) -> None:
        self._stream = stream
        self._maximum = maximum
        self.data = bytearray()
        self.exceeded = False
        self.failed = False

    def read(self) -> None:
        try:
            while True:
                chunk = self._stream.read(_READ_CHUNK_BYTES)  # type: ignore[attr-defined]
                if not chunk:
                    return
                if not isinstance(chunk, bytes):
                    self.failed = True
                    return
                remaining = self._maximum - len(self.data)
                if remaining > 0:
                    self.data.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self.exceeded = True
        except BaseException:
            self.failed = True


class GitProcessRunner:
    """Run trusted fixed Git argv under one freshly verified lexical root."""

    __slots__ = ("_root",)

    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path) or not root.is_absolute():
            raise _process_failure(GitFailureCode.INVALID_REQUEST)
        self._root = root

    def run(
        self,
        arguments: tuple[str, ...],
        *,
        expected_identity: tuple[int, int],
        stdin: bytes = b"",
        read_only: bool = False,
        identity_environment: dict[str, str] | None = None,
    ) -> GitProcessResult:
        """Run one caller-fixed argv and retain no child or pipe afterwards."""

        if (
            type(arguments) is not tuple
            or not arguments
            or any(type(argument) is not str for argument in arguments)
            or type(stdin) is not bytes
            or len(stdin) > _MAX_STDIN_BYTES
        ):
            raise _process_failure(GitFailureCode.INVALID_REQUEST)
        self._verify_root(expected_identity)
        environment = _safe_environment(
            read_only=read_only,
            identity_environment=identity_environment,
        )
        command = (_GIT_EXECUTABLE, "--no-pager", *_SAFE_CONFIG, *arguments)
        process: subprocess.Popen[bytes] | None = None
        start_failure: GitProcessFailure | None = None
        try:
            process = subprocess.Popen(
                command,
                cwd=os.fspath(self._root),
                env=environment,
                stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
        except FileNotFoundError:
            start_failure = _process_failure(GitFailureCode.EXECUTABLE_UNAVAILABLE)
        except OSError:
            start_failure = _process_failure(GitFailureCode.PROCESS_START_FAILED)
        if start_failure is not None:
            raise start_failure
        assert process is not None
        process_stdin = process.stdin if stdin else None
        process_stdout = process.stdout
        process_stderr = process.stderr
        if (
            process_stdout is None
            or process_stderr is None
            or (stdin and process_stdin is None)
        ):
            _stop_process(process)
            _close_streams(process_stdin, process_stdout, process_stderr)
            raise _process_failure(GitFailureCode.PROCESS_START_FAILED)
        stdout = _BoundedCapture(process_stdout, _MAX_STDOUT_BYTES)
        stderr = _BoundedCapture(process_stderr, _MAX_STDERR_BYTES)
        readers = (
            threading.Thread(target=stdout.read, daemon=True),
            threading.Thread(target=stderr.read, daemon=True),
        )
        started: list[threading.Thread] = []
        close_failed = False
        try:
            for reader in readers:
                reader.start()
                started.append(reader)
            if stdin:
                assert process_stdin is not None
                try:
                    process_stdin.write(stdin)
                finally:
                    close_failed |= not _close_stream(process_stdin)
                    process_stdin = None
            timed_out = False
            try:
                returncode = process.wait(timeout=_PROCESS_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                timed_out = True
            if timed_out:
                _stop_process(process)
                raise _process_failure(GitFailureCode.PROCESS_TIMED_OUT)
        except BaseException:
            _stop_process(process)
            raise
        finally:
            for reader in started:
                reader.join(_PROCESS_STOP_TIMEOUT_SECONDS)
            for stream in (process_stdin, process_stdout, process_stderr):
                close_failed |= not _close_stream(stream)
            for reader in started:
                if reader.is_alive():
                    reader.join(_PROCESS_STOP_TIMEOUT_SECONDS)
        self._verify_root(expected_identity)
        if (
            any(reader.is_alive() for reader in readers)
            or stdout.failed
            or stderr.failed
            or close_failed
        ):
            raise _process_failure(GitFailureCode.PROCESS_START_FAILED)
        if stdout.exceeded or stderr.exceeded:
            raise _process_failure(
                GitFailureCode.PROCESS_OUTPUT_EXCEEDED,
                exit_code=returncode,
            )
        return GitProcessResult(stdout=bytes(stdout.data), returncode=returncode)

    def _verify_root(self, expected_identity: tuple[int, int]) -> None:
        value: os.stat_result | None = None
        canonical: Path | None = None
        try:
            value = os.lstat(self._root)
            canonical = Path(os.path.realpath(self._root))
        except OSError:
            pass
        if (
            value is None
            or canonical is None
            or not stat.S_ISDIR(value.st_mode)
            or stat.S_ISLNK(value.st_mode)
            or canonical != self._root
            or (value.st_dev, value.st_ino) != expected_identity
        ):
            raise _process_failure(GitFailureCode.UNSAFE_ROOT)


def _safe_environment(
    *,
    read_only: bool,
    identity_environment: dict[str, str] | None,
) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_") or key in _IDENTITY_ENVIRONMENT
    }
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": os.devnull,
            "GIT_PAGER": "cat",
            "GIT_FLUSH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "LC_ALL": "C",
            "LANG": "C",
        }
    )
    if read_only:
        environment["GIT_OPTIONAL_LOCKS"] = "0"
    if identity_environment is not None:
        if set(identity_environment) != _IDENTITY_ENVIRONMENT:
            raise _process_failure(GitFailureCode.IDENTITY_INVALID)
        environment.update(identity_environment)
    return environment


def _close_stream(stream: object | None) -> bool:
    close = getattr(stream, "close", None)
    if close is None:
        return True
    try:
        close()
    except BaseException:
        return False
    return True


def _close_streams(*streams: object | None) -> None:
    for stream in streams:
        _close_stream(stream)


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except (OSError, ProcessLookupError):
        return
    try:
        process.wait(timeout=_PROCESS_STOP_TIMEOUT_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        process.kill()
    except (OSError, ProcessLookupError):
        return
    try:
        process.wait(timeout=_PROCESS_STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return


def _process_failure(
    code: GitFailureCode, *, exit_code: int | None = None
) -> GitProcessFailure:
    error = GitProcessFailure(code, exit_code=exit_code)
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True
    return error


__all__ = ["GitProcessFailure", "GitProcessResult", "GitProcessRunner"]
