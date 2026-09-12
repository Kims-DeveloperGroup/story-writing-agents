"""Durable common-Git storage for one ready story session's history."""

from __future__ import annotations

import os
import re
import stat
import uuid
from contextlib import contextmanager
from typing import Callable, Iterator, TypeVar

from .contracts import GitBaselineManifest
from .process import GitAlternateIndex
from .repository_lock import (
    GitRepositoryLease,
    GitRepositoryLockFailure,
    GitRepositoryLockFailureCode,
    lock_git_repository,
)
from .session_contracts import (
    GitSessionResourceError,
    GitSessionResourceInspection,
    GitSessionResourceState,
)
from .session_history_contracts import (
    GIT_SESSION_HISTORY_DIRECTORY,
    GIT_SESSION_HISTORY_MAX_RECORD_BYTES,
    GitSessionCandidateRecord,
    GitSessionCheckpointOperationRecord,
    GitSessionCommitOperationRecord,
    GitSessionHistoryError,
    GitSessionHistoryFailureCode,
    GitSessionHistoryInspection,
    GitSessionHistoryRecoveryAction,
    GitSessionHistorySnapshot,
    GitSessionHistoryState,
    advance_checkpoint_operation,
    advance_commit_operation,
    decode_candidate_record,
    decode_checkpoint_operation,
    decode_commit_operation,
    encode_candidate_record,
    encode_checkpoint_operation,
    encode_commit_operation,
)
from .session_resources import (
    require_ready_git_session_resources,
    require_ready_locked_git_session_resources,
)


_CANDIDATE_DIRECTORY = "candidates"
_COMMIT_DIRECTORY = "commit-operations"
_CHECKPOINT_DIRECTORY = "checkpoint-operations"
_CHECKPOINT_INPUT_DIRECTORY = "checkpoint-inputs"
_INDEX_NAME = "index"
_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600
_MAX_RECORDS = 65_536
_Record = TypeVar(
    "_Record",
    GitSessionCandidateRecord,
    GitSessionCommitOperationRecord,
    GitSessionCheckpointOperationRecord,
)


@contextmanager
def open_git_session_history(
    root: str | os.PathLike[str],
    manifest: GitBaselineManifest,
    session_id: str,
    *,
    create: bool = False,
    allowed_worktree_changes: tuple[str, ...] = (),
) -> Iterator[GitSessionHistory]:
    """Open one history while holding the shared repository serialization lease."""

    history: GitSessionHistory | None = None
    try:
        ready = require_ready_git_session_resources(
            root,
            manifest,
            session_id,
            allowed_worktree_changes=allowed_worktree_changes,
        )
        if ready.state is not GitSessionResourceState.READY:
            raise _history_error(GitSessionHistoryFailureCode.RECOVERY_REQUIRED)
        with lock_git_repository(ready.identity.main_root) as lease:
            locked = require_ready_locked_git_session_resources(
                lease,
                manifest,
                session_id,
                allowed_worktree_changes=allowed_worktree_changes,
            )
            if locked != ready:
                ready = locked
            descriptor = _open_history_root(
                lease,
                ready.identity.session_id,
                create=create,
            )
            history = GitSessionHistory(
                lease=lease,
                session_id=ready.identity.session_id,
                descriptor=descriptor,
            )
            history._bind_ready_context(
                manifest,
                ready,
                allowed_worktree_changes,
            )
            try:
                yield history
                history.require_active()
            finally:
                history.close()
    except GitSessionHistoryError:
        raise
    except GitRepositoryLockFailure as exc:
        code = (
            GitSessionHistoryFailureCode.UNSAFE_ROOT
            if exc.code is GitRepositoryLockFailureCode.UNSAFE_ROOT
            else GitSessionHistoryFailureCode.LOCK_UNAVAILABLE
        )
        raise _history_error(code) from None
    except GitSessionResourceError:
        raise _history_error(GitSessionHistoryFailureCode.RECOVERY_REQUIRED) from None
    except OSError:
        raise _history_error(GitSessionHistoryFailureCode.PERSISTENCE_FAILED) from None


class GitSessionHistory:
    """One live descriptor-relative session-history transaction."""

    __slots__ = (
        "_active",
        "_allowed_worktree_changes",
        "_descriptor",
        "_lease",
        "_manifest",
        "_resources",
        "_session_id",
    )

    def __init__(
        self,
        *,
        lease: GitRepositoryLease,
        session_id: str,
        descriptor: int | None,
    ) -> None:
        self._lease = lease
        self._session_id = session_id
        self._descriptor = descriptor
        self._manifest: GitBaselineManifest | None = None
        self._resources: GitSessionResourceInspection | None = None
        self._allowed_worktree_changes: tuple[str, ...] = ()
        self._active = True

    def _bind_ready_context(
        self,
        manifest: GitBaselineManifest,
        resources: GitSessionResourceInspection,
        allowed_worktree_changes: tuple[str, ...],
    ) -> None:
        if self._manifest is not None or self._resources is not None:
            raise _history_error(GitSessionHistoryFailureCode.INVALID_REQUEST)
        self._manifest = manifest
        self._resources = resources
        self._allowed_worktree_changes = allowed_worktree_changes

    @property
    def session_id(self) -> str:
        self.require_active()
        return self._session_id

    def require_active(self) -> None:
        if not self._active:
            raise _history_error(GitSessionHistoryFailureCode.LOCK_UNAVAILABLE)
        try:
            self._lease.require_active()
            if self._descriptor is not None:
                opened = os.fstat(self._descriptor)
                if not stat.S_ISDIR(opened.st_mode):
                    raise OSError
        except (GitRepositoryLockFailure, OSError):
            raise _history_error(GitSessionHistoryFailureCode.UNSAFE_ROOT) from None

    def require_semantic_ready(
        self,
        *,
        expected_branch_tip: str | None = None,
        expected_internal_tip: str | None = None,
    ) -> GitSessionResourceInspection:
        """Revalidate exact READY session semantics under this live Git lease."""

        self.require_active()
        manifest = self._manifest
        resources = self._resources
        if manifest is None or resources is None:
            raise _history_error(GitSessionHistoryFailureCode.INVALID_REQUEST)
        try:
            confirmed = require_ready_locked_git_session_resources(
                self._lease,
                manifest,
                self._session_id,
                allowed_worktree_changes=self._allowed_worktree_changes,
            )
        except GitSessionResourceError:
            raise _history_error(
                GitSessionHistoryFailureCode.RECOVERY_REQUIRED
            ) from None
        if (
            confirmed.state is not GitSessionResourceState.READY
            or confirmed.identity != resources.identity
            or (
                expected_branch_tip is not None
                and confirmed.current_branch_tip != expected_branch_tip
            )
            or (
                expected_internal_tip is not None
                and confirmed.current_internal_tip != expected_internal_tip
            )
        ):
            raise _history_error(GitSessionHistoryFailureCode.RECOVERY_REQUIRED)
        self.require_active()
        return confirmed

    def close(self) -> None:
        if not self._active:
            return
        self._active = False
        if self._descriptor is not None:
            os.close(self._descriptor)
            self._descriptor = None

    def write_candidate(
        self,
        record: GitSessionCandidateRecord,
    ) -> GitSessionCandidateRecord:
        value = decode_candidate_record(encode_candidate_record(record))
        self._require_session(value.session_id)
        directory = self._record_directory(_CANDIDATE_DIRECTORY, create=True)
        assert directory is not None
        try:
            _write_immutable(
                directory,
                f"{value.candidate_id}.json",
                encode_candidate_record(value),
            )
        finally:
            os.close(directory)
        return value

    def read_candidate(self, candidate_id: str) -> GitSessionCandidateRecord:
        name = _record_name(candidate_id, length=64)
        return self._read_record(
            _CANDIDATE_DIRECTORY,
            name,
            decode_candidate_record,
        )

    def write_commit_operation(
        self,
        record: GitSessionCommitOperationRecord,
    ) -> GitSessionCommitOperationRecord:
        value = decode_commit_operation(encode_commit_operation(record))
        self._require_session(value.session_id)
        self._write_operation(
            _COMMIT_DIRECTORY,
            _record_name(value.operation_id, length=32),
            value,
            encode_commit_operation,
            decode_commit_operation,
            _valid_commit_successor,
        )
        return value

    def read_commit_operation(
        self,
        operation_id: str,
    ) -> GitSessionCommitOperationRecord:
        return self._read_record(
            _COMMIT_DIRECTORY,
            _record_name(operation_id, length=32),
            decode_commit_operation,
        )

    def write_checkpoint_operation(
        self,
        record: GitSessionCheckpointOperationRecord,
    ) -> GitSessionCheckpointOperationRecord:
        value = decode_checkpoint_operation(encode_checkpoint_operation(record))
        self._require_session(value.session_id)
        self._write_operation(
            _CHECKPOINT_DIRECTORY,
            _record_name(value.operation_id, length=32),
            value,
            encode_checkpoint_operation,
            decode_checkpoint_operation,
            _valid_checkpoint_successor,
        )
        return value

    def read_checkpoint_operation(
        self,
        operation_id: str,
    ) -> GitSessionCheckpointOperationRecord:
        return self._read_record(
            _CHECKPOINT_DIRECTORY,
            _record_name(operation_id, length=32),
            decode_checkpoint_operation,
        )

    def allocate_checkpoint_index(self, operation_id: str) -> GitAlternateIndex:
        """Create one empty index only for a durable running checkpoint."""

        operation = self.read_checkpoint_operation(operation_id)
        if operation.state is not GitSessionHistoryState.RUNNING:
            raise _history_error(GitSessionHistoryFailureCode.TRANSITION_INVALID)
        parent = self._record_directory(_CHECKPOINT_INPUT_DIRECTORY, create=True)
        assert parent is not None
        directory = -1
        try:
            directory = _open_directory(
                parent,
                operation.operation_id,
                create=True,
            )
            names = set(_bounded_names(directory, suffix=""))
            if not names.issubset({_INDEX_NAME}):
                raise _history_error(GitSessionHistoryFailureCode.RECOVERY_REQUIRED)
            if _INDEX_NAME in names:
                _read_regular(directory, _INDEX_NAME, maximum=64 * 1024 * 1024)
        finally:
            if directory >= 0:
                os.close(directory)
            os.close(parent)
        return GitAlternateIndex(
            common_git=self._lease.common_git,
            session_id=self._session_id,
            operation_id=operation.operation_id,
        )

    def remove_completed_checkpoint_index(self, value: GitAlternateIndex) -> None:
        """Remove only the exact index of a durably committed checkpoint."""

        alternate = _reconstruct_alternate_index(value)
        if alternate.session_id != self._session_id:
            raise _history_error(GitSessionHistoryFailureCode.INVALID_REQUEST)
        operation = self.read_checkpoint_operation(alternate.operation_id)
        if operation.state is not GitSessionHistoryState.COMMITTED:
            raise _history_error(GitSessionHistoryFailureCode.TRANSITION_INVALID)
        parent = self._record_directory(_CHECKPOINT_INPUT_DIRECTORY, create=False)
        if parent is None:
            raise _history_error(GitSessionHistoryFailureCode.RECORD_NOT_FOUND)
        directory = -1
        try:
            directory = _open_directory(parent, alternate.operation_id, create=False)
            _unlink_regular(directory, _INDEX_NAME)
            if os.listdir(directory):
                raise _history_error(GitSessionHistoryFailureCode.RECOVERY_REQUIRED)
            os.close(directory)
            directory = -1
            os.rmdir(alternate.operation_id, dir_fd=parent)
            os.fsync(parent)
        except FileNotFoundError:
            raise _history_error(
                GitSessionHistoryFailureCode.RECORD_NOT_FOUND
            ) from None
        finally:
            if directory >= 0:
                os.close(directory)
            os.close(parent)

    def inspect(self) -> GitSessionHistoryInspection:
        """Return a complete bounded content-free inventory or fail closed."""

        inspection, _candidates, _commits, _checkpoints = self._capture()
        return inspection

    def snapshot(self) -> GitSessionHistorySnapshot:
        """Return one complete immutable record snapshot or fail closed."""

        inspection, candidates, commits, checkpoints = self._capture()
        result = GitSessionHistorySnapshot(
            inspection,
            candidates,
            commits,
            checkpoints,
        )
        if self.inspect() != inspection:
            raise _history_error(GitSessionHistoryFailureCode.RECOVERY_REQUIRED)
        return result

    def _capture(
        self,
    ) -> tuple[
        GitSessionHistoryInspection,
        tuple[GitSessionCandidateRecord, ...],
        tuple[GitSessionCommitOperationRecord, ...],
        tuple[GitSessionCheckpointOperationRecord, ...],
    ]:
        """Decode one complete sorted inventory under the active repository lease."""

        self.require_active()
        if self._descriptor is None:
            return (
                GitSessionHistoryInspection(self._session_id, (), (), (), ()),
                (),
                (),
                (),
            )
        candidates = self._inventory_records(
            _CANDIDATE_DIRECTORY,
            length=64,
            decoder=decode_candidate_record,
        )
        commits = self._inventory_operations(
            _COMMIT_DIRECTORY,
            decoder=decode_commit_operation,
        )
        checkpoints = self._inventory_operations(
            _CHECKPOINT_DIRECTORY,
            decoder=decode_checkpoint_operation,
        )
        preserved = self._inventory_indices()
        _require_allowed_names(
            self._descriptor,
            {
                _CANDIDATE_DIRECTORY,
                _COMMIT_DIRECTORY,
                _CHECKPOINT_DIRECTORY,
                _CHECKPOINT_INPUT_DIRECTORY,
            },
        )
        inspection = GitSessionHistoryInspection(
            self._session_id,
            tuple(item[0] for item in candidates),
            tuple((item[0], item[1].state) for item in commits),
            tuple((item[0], item[1].state) for item in checkpoints),
            tuple(item[0] for item in preserved),
        )
        return (
            inspection,
            tuple(item[1] for item in candidates),
            tuple(item[1] for item in commits),
            tuple(item[1] for item in checkpoints),
        )

    def _require_session(self, session_id: str) -> None:
        self.require_active()
        if session_id != self._session_id:
            raise _history_error(GitSessionHistoryFailureCode.INVALID_REQUEST)

    def _record_directory(self, name: str, *, create: bool) -> int | None:
        self.require_active()
        if self._descriptor is None:
            if not create:
                return None
            raise _history_error(GitSessionHistoryFailureCode.PERSISTENCE_FAILED)
        try:
            return _open_directory(self._descriptor, name, create=create)
        except FileNotFoundError:
            return None

    def _read_record(
        self,
        directory_name: str,
        file_name: str,
        decoder: Callable[[bytes], _Record],
    ) -> _Record:
        directory = self._record_directory(directory_name, create=False)
        if directory is None:
            raise _history_error(GitSessionHistoryFailureCode.RECORD_NOT_FOUND)
        try:
            return decoder(_read_regular(directory, file_name))
        except FileNotFoundError:
            raise _history_error(
                GitSessionHistoryFailureCode.RECORD_NOT_FOUND
            ) from None
        finally:
            os.close(directory)

    def _write_operation(
        self,
        directory_name: str,
        file_name: str,
        record: _Record,
        encoder: Callable[[_Record], bytes],
        decoder: Callable[[bytes], _Record],
        successor: Callable[[_Record, _Record], bool],
    ) -> None:
        directory = self._record_directory(directory_name, create=True)
        assert directory is not None
        payload = encoder(record)
        try:
            try:
                current = decoder(_read_regular(directory, file_name))
            except FileNotFoundError:
                if getattr(record, "sequence", -1) != 0 or getattr(
                    record,
                    "state",
                    None,
                ) not in {
                    GitSessionHistoryState.RUNNING,
                    GitSessionHistoryState.REJECTED,
                }:
                    raise _history_error(
                        GitSessionHistoryFailureCode.TRANSITION_INVALID
                    )
                _write_immutable(directory, file_name, payload)
                return
            if not successor(current, record):
                raise _history_error(GitSessionHistoryFailureCode.TRANSITION_INVALID)
            _replace_regular(directory, file_name, payload)
        finally:
            os.close(directory)

    def _inventory_records(
        self,
        directory_name: str,
        *,
        length: int,
        decoder: Callable[[bytes], _Record],
    ) -> tuple[tuple[str, _Record], ...]:
        directory = self._record_directory(directory_name, create=False)
        if directory is None:
            return ()
        try:
            names = _bounded_names(directory)
            result = []
            for name in names:
                identity = _identity_from_name(name, length=length)
                value = decoder(_read_regular(directory, name))
                if length == 64 and getattr(value, "candidate_id", None) != identity:
                    raise _history_error(GitSessionHistoryFailureCode.RECORD_INVALID)
                result.append((identity, value))
            return tuple(result)
        finally:
            os.close(directory)

    def _inventory_operations(
        self,
        directory_name: str,
        *,
        decoder: Callable[[bytes], _Record],
    ) -> tuple[tuple[str, _Record], ...]:
        result = self._inventory_records(
            directory_name,
            length=32,
            decoder=decoder,
        )
        for operation_id, value in result:
            if (
                getattr(value, "operation_id", None) != operation_id
                or getattr(value, "session_id", None) != self._session_id
            ):
                raise _history_error(GitSessionHistoryFailureCode.RECORD_INVALID)
        return result

    def _inventory_indices(self) -> tuple[tuple[str, None], ...]:
        parent = self._record_directory(_CHECKPOINT_INPUT_DIRECTORY, create=False)
        if parent is None:
            return ()
        result: list[tuple[str, None]] = []
        try:
            for operation_id in _bounded_names(parent, suffix=""):
                _record_name(operation_id, length=32, suffix="")
                directory = _open_directory(parent, operation_id, create=False)
                try:
                    _require_exact_names(directory, {_INDEX_NAME})
                    _read_regular(directory, _INDEX_NAME, maximum=64 * 1024 * 1024)
                finally:
                    os.close(directory)
                result.append((operation_id, None))
            return tuple(result)
        finally:
            os.close(parent)


def _open_history_root(
    lease: GitRepositoryLease,
    session_id: str,
    *,
    create: bool,
) -> int | None:
    lease.require_active()
    descriptor = os.dup(lease.common_descriptor)
    try:
        for name in (*GIT_SESSION_HISTORY_DIRECTORY.split("/"), session_id):
            try:
                child = _open_directory(descriptor, name, create=create)
            except FileNotFoundError:
                os.close(descriptor)
                return None
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_directory(parent: int, name: str, *, create: bool) -> int:
    _require_name(name)
    _require_exact_spelling(parent, name, missing_ok=create)
    if create:
        failure: GitSessionHistoryError | None = None
        try:
            os.mkdir(name, _DIRECTORY_MODE, dir_fd=parent)
            os.fsync(parent)
        except FileExistsError:
            pass
        except OSError:
            failure = _history_error(GitSessionHistoryFailureCode.PERSISTENCE_FAILED)
        if failure is not None:
            raise failure
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(name, flags, dir_fd=parent)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode):
            raise OSError
        _require_exact_spelling(parent, name, missing_ok=False)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _require_exact_spelling(parent: int, name: str, *, missing_ok: bool) -> None:
    names = os.listdir(parent)
    folded = name.casefold()
    if any(candidate != name and candidate.casefold() == folded for candidate in names):
        raise _history_error(GitSessionHistoryFailureCode.UNSAFE_ROOT)
    if not missing_ok and name not in names:
        raise FileNotFoundError(name)


def _require_exact_names(parent: int, expected: set[str]) -> None:
    names = set(_bounded_names(parent, suffix=""))
    if names != expected:
        raise _history_error(GitSessionHistoryFailureCode.RECORD_INVALID)


def _require_allowed_names(parent: int, allowed: set[str]) -> None:
    names = set(_bounded_names(parent, suffix=""))
    if not names.issubset(allowed):
        raise _history_error(GitSessionHistoryFailureCode.RECORD_INVALID)


def _bounded_names(parent: int, *, suffix: str = ".json") -> tuple[str, ...]:
    names = os.listdir(parent)
    if len(names) > _MAX_RECORDS or any(type(name) is not str for name in names):
        raise _history_error(GitSessionHistoryFailureCode.RECORD_LIMIT_EXCEEDED)
    if suffix and any(not name.endswith(suffix) for name in names):
        raise _history_error(GitSessionHistoryFailureCode.RECORD_INVALID)
    return tuple(sorted(names))


def _record_name(value: str, *, length: int, suffix: str = ".json") -> str:
    if (
        type(value) is not str
        or len(value) != length
        or re.fullmatch(r"[0-9a-f]+", value, flags=re.ASCII) is None
    ):
        raise _history_error(GitSessionHistoryFailureCode.INVALID_REQUEST)
    return f"{value}{suffix}"


def _identity_from_name(value: str, *, length: int) -> str:
    if not value.endswith(".json"):
        raise _history_error(GitSessionHistoryFailureCode.RECORD_INVALID)
    identity = value[:-5]
    try:
        expected = _record_name(identity, length=length)
    except GitSessionHistoryError:
        raise _history_error(GitSessionHistoryFailureCode.RECORD_INVALID) from None
    if expected != value:
        raise _history_error(GitSessionHistoryFailureCode.RECORD_INVALID)
    return identity


def _require_name(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise _history_error(GitSessionHistoryFailureCode.INVALID_REQUEST)
    return value


def _read_regular(
    parent: int,
    name: str,
    *,
    maximum: int = GIT_SESSION_HISTORY_MAX_RECORD_BYTES,
) -> bytes:
    _require_name(name)
    _require_exact_spelling(parent, name, missing_ok=False)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    failure: GitSessionHistoryError | None = None
    payload: bytes | None = None
    try:
        descriptor = os.open(name, flags, dir_fd=parent)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > maximum
        ):
            raise OSError
        chunks = bytearray()
        while True:
            chunk = os.read(descriptor, min(64 * 1024, maximum + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > maximum:
                raise _history_error(GitSessionHistoryFailureCode.RECORD_LIMIT_EXCEEDED)
        after = os.fstat(descriptor)

        def facts(value: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
            return (
                value.st_dev,
                value.st_ino,
                value.st_mode,
                value.st_nlink,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
            )

        if facts(before) != facts(after) or len(chunks) != before.st_size:
            raise OSError
        payload = bytes(chunks)
    except GitSessionHistoryError:
        raise
    except OSError:
        failure = _history_error(GitSessionHistoryFailureCode.RECORD_INVALID)
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if failure is not None:
        raise failure
    assert payload is not None
    return payload


def _regular_identity(
    parent: int,
    name: str,
) -> tuple[int, int, int, int, int, int, int]:
    """Return the identity of one exact, bounded, single-link regular record."""

    _require_name(name)
    _require_exact_spelling(parent, name, missing_ok=False)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    failure: GitSessionHistoryError | None = None
    identity: tuple[int, int, int, int, int, int, int] | None = None
    try:
        descriptor = os.open(name, flags, dir_fd=parent)
        value = os.fstat(descriptor)
        if (
            not stat.S_ISREG(value.st_mode)
            or value.st_nlink != 1
            or value.st_size > GIT_SESSION_HISTORY_MAX_RECORD_BYTES
        ):
            raise OSError
        identity = (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_nlink,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )
    except OSError:
        failure = _history_error(GitSessionHistoryFailureCode.RECOVERY_REQUIRED)
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if failure is not None:
        raise failure
    assert identity is not None
    return identity


def _write_immutable(parent: int, name: str, payload: bytes) -> None:
    _require_name(name)
    if (
        type(payload) is not bytes
        or len(payload) > GIT_SESSION_HISTORY_MAX_RECORD_BYTES
    ):
        raise _history_error(GitSessionHistoryFailureCode.RECORD_LIMIT_EXCEEDED)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        _require_exact_spelling(parent, name, missing_ok=True)
        descriptor = os.open(name, flags, _FILE_MODE, dir_fd=parent)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.fsync(parent)
    except FileExistsError:
        if _read_regular(parent, name, maximum=max(len(payload), 1)) == payload:
            return
        raise _history_error(GitSessionHistoryFailureCode.RECORD_EXISTS) from None
    except GitSessionHistoryError:
        raise
    except OSError:
        raise _history_error(GitSessionHistoryFailureCode.PERSISTENCE_FAILED) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _replace_regular(parent: int, name: str, payload: bytes) -> None:
    _read_regular(parent, name)
    expected_identity = _regular_identity(parent, name)
    temporary = f".{name}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    installed = False
    try:
        descriptor = os.open(temporary, flags, _FILE_MODE, dir_fd=parent)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        if _regular_identity(parent, name) != expected_identity:
            raise _history_error(GitSessionHistoryFailureCode.RECOVERY_REQUIRED)
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        installed = True
        os.fsync(parent)
    except OSError:
        raise _history_error(GitSessionHistoryFailureCode.PERSISTENCE_FAILED) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not installed:
            try:
                os.unlink(temporary, dir_fd=parent)
            except OSError:
                pass


def _unlink_regular(parent: int, name: str) -> None:
    _read_regular(parent, name, maximum=64 * 1024 * 1024)
    os.unlink(name, dir_fd=parent)
    os.fsync(parent)


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError
        offset += written


def _valid_commit_successor(
    current: GitSessionCommitOperationRecord,
    proposed: GitSessionCommitOperationRecord,
) -> bool:
    try:
        return (
            advance_commit_operation(
                current,
                proposed.state,
                commit_id=proposed.commit_id,
            )
            == proposed
        )
    except GitSessionHistoryError:
        return False


def _valid_checkpoint_successor(
    current: GitSessionCheckpointOperationRecord,
    proposed: GitSessionCheckpointOperationRecord,
) -> bool:
    try:
        return (
            advance_checkpoint_operation(
                current,
                proposed.state,
                checkpoint_id=proposed.checkpoint_id,
                alternate_index_preserved=proposed.alternate_index_preserved,
            )
            == proposed
        )
    except GitSessionHistoryError:
        return False


def _reconstruct_alternate_index(value: object) -> GitAlternateIndex:
    try:
        if type(value) is not GitAlternateIndex:
            raise TypeError
        result = GitAlternateIndex(
            value.common_git, value.session_id, value.operation_id
        )
        if result.path != value.path:
            raise TypeError
        return result
    except (AttributeError, TypeError, RuntimeError):
        raise _history_error(GitSessionHistoryFailureCode.INVALID_REQUEST) from None


def _history_error(code: GitSessionHistoryFailureCode) -> GitSessionHistoryError:
    action = {
        GitSessionHistoryFailureCode.OUTCOME_UNKNOWN: GitSessionHistoryRecoveryAction.INSPECT,
        GitSessionHistoryFailureCode.RECOVERY_REQUIRED: GitSessionHistoryRecoveryAction.OPERATOR_RECOVERY,
        GitSessionHistoryFailureCode.RECORD_NOT_FOUND: GitSessionHistoryRecoveryAction.REPREPARE,
        GitSessionHistoryFailureCode.PERSISTENCE_FAILED: GitSessionHistoryRecoveryAction.INSPECT,
    }.get(code, GitSessionHistoryRecoveryAction.NONE)
    error = GitSessionHistoryError(code, action)
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True
    return error


__all__ = [
    "GitSessionHistory",
    "GitSessionHistorySnapshot",
    "open_git_session_history",
]
