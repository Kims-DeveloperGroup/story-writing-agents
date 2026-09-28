"""Package-internal bounded process owner for trusted fixed Git adapters."""

from __future__ import annotations

import os
import re
import stat
import subprocess
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .contracts import (
    GitEffectiveIdentity,
    GitFailureCode,
    GitIdentity,
)


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
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_OPERATION_ID = re.compile(r"[0-9a-f]{32}")


class GitProcessProfile(StrEnum):
    """Closed environment profiles for trusted package-internal Git operations."""

    READ_ONLY = "read_only"
    MUTATION = "mutation"
    IDENTITY_MUTATION = "identity_mutation"
    INTERNAL_CHECKPOINT = "internal_checkpoint"


@dataclass(frozen=True, slots=True)
class GitAlternateIndex:
    """One exact operation-owned alternate index admitted only for checkpoints."""

    common_git: Path = field(repr=False)
    session_id: str
    operation_id: str
    path: Path = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.common_git, Path)
            or not self.common_git.is_absolute()
            or Path(os.path.normpath(os.fspath(self.common_git))) != self.common_git
            or self.common_git.name != ".git"
            or type(self.session_id) is not str
            or _SAFE_IDENTIFIER.fullmatch(self.session_id) is None
            or type(self.operation_id) is not str
            or _OPERATION_ID.fullmatch(self.operation_id) is None
        ):
            raise _process_failure(GitFailureCode.INVALID_REQUEST)
        value = (
            self.common_git
            / "story-writing-agents"
            / "runtime"
            / "session-history"
            / self.session_id
            / "checkpoint-inputs"
            / self.operation_id
            / "index"
        )
        object.__setattr__(self, "path", value)


@dataclass(frozen=True, slots=True)
class GitCommitTimestamps:
    """Exact bounded author and committer seconds for one trusted commit."""

    author_timestamp_seconds: int
    committer_timestamp_seconds: int

    def __post_init__(self) -> None:
        for value in (
            self.author_timestamp_seconds,
            self.committer_timestamp_seconds,
        ):
            if type(value) is not int or not 0 <= value <= 2**63 - 1:
                raise _process_failure(GitFailureCode.INVALID_REQUEST)


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
        profile: GitProcessProfile | None = None,
        effective_identity: GitEffectiveIdentity | None = None,
        alternate_index: GitAlternateIndex | None = None,
        commit_timestamps: GitCommitTimestamps | None = None,
        read_only: bool | None = None,
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
        selected_profile, selected_identity, selected_timestamps = _process_request(
            profile=profile,
            effective_identity=effective_identity,
            alternate_index=alternate_index,
            commit_timestamps=commit_timestamps,
            read_only=read_only,
            identity_environment=identity_environment,
        )
        self._verify_root(expected_identity)
        if alternate_index is not None:
            self._verify_alternate_index(alternate_index)
        environment = _safe_environment(
            profile=selected_profile,
            effective_identity=selected_identity,
            alternate_index=alternate_index,
            commit_timestamps=selected_timestamps,
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
        returncode: int | None = None
        wait_failure: BaseException | None = None

        def wait_for_process() -> None:
            nonlocal returncode, wait_failure
            try:
                returncode = process.wait(timeout=None)
            except BaseException as failure:
                wait_failure = failure

        waiter = threading.Thread(target=wait_for_process, daemon=True)
        started: list[threading.Thread] = []
        close_failed = False
        body_failure: BaseException | None = None

        def start_worker(worker: threading.Thread) -> None:
            # Track before start so a partial start is also owned.
            started.append(worker)
            start_failed = False
            try:
                worker.start()
            except (OSError, RuntimeError):
                start_failed = True
            if start_failed:
                raise _process_failure(GitFailureCode.PROCESS_START_FAILED)

        try:
            for reader in readers:
                start_worker(reader)
            if stdin:
                assert process_stdin is not None
                try:
                    process_stdin.write(stdin)
                finally:
                    close_failed |= not _close_stream(process_stdin)
                    process_stdin = None
            timed_out = False
            try:
                start_worker(waiter)
                waiter.join(_PROCESS_TIMEOUT_SECONDS)
                timed_out = waiter.is_alive()
                if not timed_out and wait_failure is not None:
                    raise wait_failure
            except subprocess.TimeoutExpired:
                timed_out = True
            if timed_out:
                _stop_process(process)
                raise _process_failure(GitFailureCode.PROCESS_TIMED_OUT)
            if returncode is None:
                raise _process_failure(GitFailureCode.PROCESS_START_FAILED)
        except BaseException as failure:
            body_failure = failure
            _stop_process(process)
            raise
        finally:
            for worker in started:
                if worker.ident is not None:
                    worker.join(_PROCESS_STOP_TIMEOUT_SECONDS)
            for stream in (process_stdin, process_stdout, process_stderr):
                close_failed |= not _close_stream(stream)
            for worker in started:
                if worker.is_alive():
                    worker.join(_PROCESS_STOP_TIMEOUT_SECONDS)
            if body_failure is not None and any(
                worker.is_alive() for worker in started
            ):
                body_failure.add_note("Git process worker cleanup did not finish")
        self._verify_root(expected_identity)
        if alternate_index is not None:
            self._verify_alternate_index(alternate_index)
        if (
            any(worker.is_alive() for worker in (*readers, waiter))
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

    def _verify_alternate_index(self, value: GitAlternateIndex) -> None:
        reconstructed = _reconstruct_alternate_index(value)
        if reconstructed.common_git != self._root / ".git":
            raise _process_failure(GitFailureCode.INVALID_REQUEST)
        current = reconstructed.common_git
        expected_parts = reconstructed.path.relative_to(current).parts[:-1]
        try:
            common = os.lstat(current)
            if (
                not stat.S_ISDIR(common.st_mode)
                or stat.S_ISLNK(common.st_mode)
                or Path(os.path.realpath(current)) != current
            ):
                raise OSError
            for part in expected_parts:
                current /= part
                observed = os.lstat(current)
                if not stat.S_ISDIR(observed.st_mode) or stat.S_ISLNK(observed.st_mode):
                    raise OSError
            try:
                opened = os.lstat(reconstructed.path)
            except FileNotFoundError:
                opened = None
            if opened is not None and (
                not stat.S_ISREG(opened.st_mode)
                or stat.S_ISLNK(opened.st_mode)
                or opened.st_nlink != 1
            ):
                raise OSError
        except OSError:
            raise _process_failure(GitFailureCode.INVALID_REQUEST) from None


def _process_request(
    *,
    profile: GitProcessProfile | None,
    effective_identity: GitEffectiveIdentity | None,
    alternate_index: GitAlternateIndex | None,
    commit_timestamps: GitCommitTimestamps | None,
    read_only: bool | None,
    identity_environment: dict[str, str] | None,
) -> tuple[
    GitProcessProfile,
    GitEffectiveIdentity | None,
    GitCommitTimestamps | None,
]:
    if profile is not None:
        if (
            type(profile) is not GitProcessProfile
            or read_only is not None
            or identity_environment is not None
        ):
            raise _process_failure(GitFailureCode.INVALID_REQUEST)
        selected = profile
        identity = (
            None
            if effective_identity is None
            else _reconstruct_effective_identity(effective_identity)
        )
    else:
        if read_only is not None and type(read_only) is not bool:
            raise _process_failure(GitFailureCode.INVALID_REQUEST)
        if effective_identity is not None or alternate_index is not None:
            raise _process_failure(GitFailureCode.INVALID_REQUEST)
        identity = _legacy_identity(identity_environment)
        selected = (
            GitProcessProfile.READ_ONLY
            if read_only is True
            else (
                GitProcessProfile.IDENTITY_MUTATION
                if identity is not None
                else GitProcessProfile.MUTATION
            )
        )
    if selected is GitProcessProfile.INTERNAL_CHECKPOINT:
        if alternate_index is None:
            raise _process_failure(GitFailureCode.INVALID_REQUEST)
        _reconstruct_alternate_index(alternate_index)
    elif alternate_index is not None:
        raise _process_failure(GitFailureCode.INVALID_REQUEST)
    if selected is GitProcessProfile.IDENTITY_MUTATION and identity is None:
        raise _process_failure(GitFailureCode.IDENTITY_INVALID)
    if selected is GitProcessProfile.MUTATION and identity is not None:
        raise _process_failure(GitFailureCode.IDENTITY_INVALID)
    timestamps = (
        None
        if commit_timestamps is None
        else _reconstruct_commit_timestamps(commit_timestamps)
    )
    if timestamps is not None and (
        selected is not GitProcessProfile.INTERNAL_CHECKPOINT or identity is None
    ):
        raise _process_failure(GitFailureCode.INVALID_REQUEST)
    if profile is None and commit_timestamps is not None:
        raise _process_failure(GitFailureCode.INVALID_REQUEST)
    return selected, identity, timestamps


def _legacy_identity(value: dict[str, str] | None) -> GitEffectiveIdentity | None:
    if value is None:
        return None
    if type(value) is not dict or set(value) != _IDENTITY_ENVIRONMENT:
        raise _process_failure(GitFailureCode.IDENTITY_INVALID)
    try:
        result = GitEffectiveIdentity(
            author=GitIdentity(
                name=value["GIT_AUTHOR_NAME"],
                email=value["GIT_AUTHOR_EMAIL"],
            ),
            committer=GitIdentity(
                name=value["GIT_COMMITTER_NAME"],
                email=value["GIT_COMMITTER_EMAIL"],
            ),
        )
    except (KeyError, RuntimeError, TypeError):
        raise _process_failure(GitFailureCode.IDENTITY_INVALID) from None
    return result


def _reconstruct_effective_identity(value: object) -> GitEffectiveIdentity:
    try:
        if type(value) is not GitEffectiveIdentity:
            raise TypeError
        result = GitEffectiveIdentity(
            author=GitIdentity(value.author.name, value.author.email),
            committer=GitIdentity(value.committer.name, value.committer.email),
        )
    except (AttributeError, RuntimeError, TypeError):
        raise _process_failure(GitFailureCode.IDENTITY_INVALID) from None
    return result


def _identity_environment(value: GitEffectiveIdentity) -> dict[str, str]:
    identity = _reconstruct_effective_identity(value)
    return {
        "GIT_AUTHOR_NAME": identity.author.name,
        "GIT_AUTHOR_EMAIL": identity.author.email,
        "GIT_COMMITTER_NAME": identity.committer.name,
        "GIT_COMMITTER_EMAIL": identity.committer.email,
    }


def _reconstruct_commit_timestamps(value: object) -> GitCommitTimestamps:
    try:
        if type(value) is not GitCommitTimestamps:
            raise TypeError
        return GitCommitTimestamps(
            author_timestamp_seconds=value.author_timestamp_seconds,
            committer_timestamp_seconds=value.committer_timestamp_seconds,
        )
    except (AttributeError, RuntimeError, TypeError):
        raise _process_failure(GitFailureCode.INVALID_REQUEST) from None


def _reconstruct_alternate_index(value: object) -> GitAlternateIndex:
    try:
        if type(value) is not GitAlternateIndex:
            raise TypeError
        result = GitAlternateIndex(
            common_git=value.common_git,
            session_id=value.session_id,
            operation_id=value.operation_id,
        )
        if value.path != result.path:
            raise TypeError
    except (AttributeError, RuntimeError, TypeError):
        raise _process_failure(GitFailureCode.INVALID_REQUEST) from None
    return result


def _safe_environment(
    *,
    profile: GitProcessProfile,
    effective_identity: GitEffectiveIdentity | None,
    alternate_index: GitAlternateIndex | None,
    commit_timestamps: GitCommitTimestamps | None,
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
            "GIT_EDITOR": "true",
            "GIT_SEQUENCE_EDITOR": "true",
            "GIT_MERGE_AUTOEDIT": "no",
            "GIT_FLUSH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "LC_ALL": "C",
            "LANG": "C",
        }
    )
    if profile is GitProcessProfile.READ_ONLY:
        environment["GIT_OPTIONAL_LOCKS"] = "0"
    if effective_identity is not None:
        environment.update(_identity_environment(effective_identity))
    if commit_timestamps is not None:
        timestamps = _reconstruct_commit_timestamps(commit_timestamps)
        environment.update(
            {
                "GIT_AUTHOR_DATE": f"{timestamps.author_timestamp_seconds} +0000",
                "GIT_COMMITTER_DATE": (
                    f"{timestamps.committer_timestamp_seconds} +0000"
                ),
            }
        )
    if profile is GitProcessProfile.INTERNAL_CHECKPOINT:
        assert alternate_index is not None
        environment["GIT_INDEX_FILE"] = os.fspath(alternate_index.path)
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


__all__ = [
    "GitAlternateIndex",
    "GitCommitTimestamps",
    "GitProcessFailure",
    "GitProcessProfile",
    "GitProcessResult",
    "GitProcessRunner",
]
