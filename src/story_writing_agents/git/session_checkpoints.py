"""Exact closed-path checkpoints on one story session's internal ref."""

from __future__ import annotations

import hashlib
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Callable, ParamSpec, TypeVar

from .contracts import (
    GitBaselineManifest,
    GitEffectiveIdentity,
    GitIdentity,
    GitManifestEntry,
)
from .process import (
    GitAlternateIndex,
    GitCommitTimestamps,
    GitProcessFailure,
    GitProcessProfile,
    GitProcessResult,
    GitProcessRunner,
)
from .session_checkpoint_contracts import (
    SESSION_CHECKPOINT_MAX_PATHS,
    SESSION_CHECKPOINT_MAX_TOTAL_BYTES,
    SESSION_CHECKPOINT_MESSAGE_PREFIX,
    SessionCheckpointError,
    SessionCheckpointFailureCode,
    SessionCheckpointPathOperation,
    SessionCheckpointRecoveryAction,
    SessionCheckpointRequest,
    SessionCheckpointResult,
    SessionCheckpointSnapshot,
    SessionCheckpointState,
    classify_session_checkpoint_path,
)
from .session_contracts import GitSessionIdentity
from .session_history import GitSessionHistory, open_git_session_history
from .session_history_contracts import (
    GitSessionCheckpointOperationRecord,
    GitSessionHistoryError,
    GitSessionHistoryFailureCode,
    GitSessionHistoryState,
    advance_checkpoint_operation,
)


_OBJECT_ID = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_INDEX_ROW = re.compile(rb"(100644) ([0-9a-f]{40}|[0-9a-f]{64}) 0\t")
_P = ParamSpec("_P")
_T = TypeVar("_T")


def _content_free_boundary(action: Callable[_P, _T]) -> Callable[_P, _T]:
    """Clear nested exception graphs at the checkpoint service boundary."""

    @wraps(action)
    def invoke(*args: _P.args, **kwargs: _P.kwargs) -> _T:
        failure: SessionCheckpointError | None = None
        try:
            return action(*args, **kwargs)
        except SessionCheckpointError as caught:
            failure = SessionCheckpointError(caught.code, caught.recovery_action)
            failure.__cause__ = None
            failure.__context__ = None
            failure.__suppress_context__ = True
        raise failure

    return invoke


@dataclass(frozen=True, slots=True)
class _RepositoryInvariants:
    main_symbolic_head: str
    main_head: str
    main_index_sha256: str
    main_status_sha256: str
    session_symbolic_head: str
    session_head: str
    session_index_sha256: str


@dataclass(frozen=True, slots=True)
class _CheckpointIntent:
    operation: GitSessionCheckpointOperationRecord
    alternate_index: GitAlternateIndex
    expected_commit_id: str
    message: bytes
    identity: GitEffectiveIdentity
    timestamps: GitCommitTimestamps
    invariants: _RepositoryInvariants
    expected_entries: tuple[tuple[str, str, str], ...]
    snapshots: tuple[SessionCheckpointSnapshot, ...] = field(repr=False)


class SessionCheckpointService:
    """Checkpoint exact owner snapshots without borrowing user Git state."""

    __slots__ = ("_manifest", "_root", "_session_id")

    def __init__(
        self,
        root: str | os.PathLike[str],
        manifest: GitBaselineManifest,
        session_id: str,
    ) -> None:
        try:
            path = Path(root)
            identity = GitSessionIdentity(path, session_id)
        except (TypeError, RuntimeError):
            raise _error(SessionCheckpointFailureCode.INVALID_REQUEST) from None
        if Path(os.path.normpath(os.fspath(path))) != path:
            raise _error(SessionCheckpointFailureCode.INVALID_REQUEST)
        self._root = identity.main_root
        self._session_id = identity.session_id
        self._manifest = _reconstruct_manifest(manifest)

    @_content_free_boundary
    def checkpoint(self, request: SessionCheckpointRequest) -> SessionCheckpointResult:
        """Create one exact internal checkpoint or return a proved no-op."""

        value = _reconstruct_request(request)
        if value.session_id != self._session_id:
            raise _error(SessionCheckpointFailureCode.INVALID_REQUEST)
        operation: GitSessionCheckpointOperationRecord | None = None
        ref_updated = False
        try:
            with open_git_session_history(
                self._root,
                self._manifest,
                self._session_id,
                create=True,
            ) as history:
                intent = self._prepare(value)
                if type(intent) is SessionCheckpointResult:
                    return intent
                operation = intent.operation
                history.write_checkpoint_operation(operation)
                allocated = history.allocate_checkpoint_index(operation.operation_id)
                if allocated != intent.alternate_index:
                    raise _error(SessionCheckpointFailureCode.RECOVERY_REQUIRED)
                self._stage(intent)
                checkpoint_id = self._commit(intent)
                self._cas(operation, checkpoint_id)
                ref_updated = True
                self._verify_invariants(intent, checkpoint_id)
                operation = advance_checkpoint_operation(
                    operation,
                    GitSessionHistoryState.COMMITTED,
                    checkpoint_id=checkpoint_id,
                )
                history.write_checkpoint_operation(operation)
                history.remove_completed_checkpoint_index(intent.alternate_index)
                return _operation_result(operation)
        except SessionCheckpointError:
            self._preserve_uncertain(operation, ref_updated)
            if operation is not None:
                raise _error(SessionCheckpointFailureCode.OUTCOME_UNKNOWN) from None
            raise
        except (GitSessionHistoryError, GitProcessFailure):
            self._preserve_uncertain(operation, ref_updated)
            code = (
                SessionCheckpointFailureCode.OUTCOME_UNKNOWN
                if operation is not None
                else SessionCheckpointFailureCode.HISTORY_FAILED
            )
            raise _error(code) from None
        except Exception:
            self._preserve_uncertain(operation, ref_updated)
            code = (
                SessionCheckpointFailureCode.OUTCOME_UNKNOWN
                if operation is not None
                else SessionCheckpointFailureCode.GIT_FAILED
            )
            raise _error(code) from None
        except BaseException:
            self._preserve_uncertain(operation, ref_updated)
            raise

    @_content_free_boundary
    def inspect(self, operation_id: str) -> SessionCheckpointResult:
        """Read one durable content-free checkpoint classification."""

        try:
            with open_git_session_history(
                self._root,
                self._manifest,
                self._session_id,
            ) as history:
                return _operation_result(
                    history.read_checkpoint_operation(operation_id)
                )
        except GitSessionHistoryError as exc:
            code = (
                SessionCheckpointFailureCode.INVALID_REQUEST
                if exc.code is GitSessionHistoryFailureCode.RECORD_NOT_FOUND
                else SessionCheckpointFailureCode.HISTORY_FAILED
            )
            raise _error(code) from None

    @_content_free_boundary
    def recover(self, operation_id: str) -> SessionCheckpointResult:
        """Classify an interrupted checkpoint without replay or destructive work."""

        try:
            with open_git_session_history(
                self._root,
                self._manifest,
                self._session_id,
            ) as history:
                operation = history.read_checkpoint_operation(operation_id)
                if operation.state is GitSessionHistoryState.RECOVERY_REQUIRED:
                    return _operation_result(operation)
                current = self._internal_tip()
                if operation.state is GitSessionHistoryState.COMMITTED:
                    if (
                        current != operation.checkpoint_id
                        or not self._commit_matches_intent(current, operation)
                    ):
                        raise _error(SessionCheckpointFailureCode.RECOVERY_REQUIRED)
                    self._cleanup_committed_index(history, operation)
                    return _operation_result(operation)
                if (
                    current != operation.expected_internal_tip
                    and self._commit_matches_intent(current, operation)
                ):
                    committed = advance_checkpoint_operation(
                        operation,
                        GitSessionHistoryState.COMMITTED,
                        checkpoint_id=current,
                    )
                    history.write_checkpoint_operation(committed)
                    self._cleanup_committed_index(history, committed)
                    return _operation_result(committed)
                preserved = operation.operation_id in set(
                    history.inspect().preserved_checkpoint_indices
                )
                if current == operation.expected_internal_tip:
                    recovered = advance_checkpoint_operation(
                        operation,
                        GitSessionHistoryState.RECOVERY_REQUIRED,
                        alternate_index_preserved=preserved,
                    )
                elif operation.state is GitSessionHistoryState.RUNNING:
                    recovered = advance_checkpoint_operation(
                        operation,
                        GitSessionHistoryState.OUTCOME_UNKNOWN,
                        alternate_index_preserved=preserved,
                    )
                else:
                    return _operation_result(operation)
                history.write_checkpoint_operation(recovered)
                return _operation_result(recovered)
        except SessionCheckpointError:
            raise
        except GitSessionHistoryError:
            raise _error(SessionCheckpointFailureCode.HISTORY_FAILED) from None
        except GitProcessFailure:
            raise _error(SessionCheckpointFailureCode.RECOVERY_REQUIRED) from None
        except Exception:
            raise _error(SessionCheckpointFailureCode.RECOVERY_REQUIRED) from None

    def _prepare(
        self,
        request: SessionCheckpointRequest,
    ) -> _CheckpointIntent | SessionCheckpointResult:
        identity = GitSessionIdentity(self._root, self._session_id)
        main_identity = _directory_identity(identity.main_root)
        session_identity = _directory_identity(identity.worktree_root)
        main_runner = GitProcessRunner(identity.main_root)
        session_runner = GitProcessRunner(identity.worktree_root)
        invariants = _capture_invariants(
            main_runner,
            main_identity,
            session_runner,
            session_identity,
            identity,
        )
        tip = _object_id(
            _required(
                main_runner,
                main_identity,
                ("rev-parse", "--verify", identity.internal_ref),
                GitProcessProfile.READ_ONLY,
            ).stdout
        )
        entries = _tree_entries(
            main_runner,
            main_identity,
            tip,
            self._session_id,
        )
        algorithm = "sha1" if len(tip) == 40 else "sha256"
        current_tree = _tree_id(main_runner, main_identity, tip)
        if _derive_tree(entries, algorithm) != current_tree:
            raise _error(SessionCheckpointFailureCode.RECOVERY_REQUIRED)
        expected = _apply_snapshots(
            main_runner,
            main_identity,
            entries,
            request.snapshots,
            algorithm,
        )
        expected_tree = _derive_tree(expected, algorithm)
        if expected_tree == current_tree:
            return SessionCheckpointResult(
                session_id=self._session_id,
                correlation_id=request.correlation_id,
                state=SessionCheckpointState.UNCHANGED,
                recovery_action=SessionCheckpointRecoveryAction.NONE,
                internal_tip=tip,
            )
        effective_identity = _effective_identity(main_runner, main_identity)
        timestamp = int(time.time())
        timestamps = GitCommitTimestamps(timestamp, timestamp)
        operation_id = uuid.uuid4().hex
        message = _message(self._session_id, request.correlation_id, operation_id)
        raw_commit = _commit_payload(
            expected_tree,
            tip,
            effective_identity,
            timestamps,
            message,
        )
        operation = GitSessionCheckpointOperationRecord(
            session_id=self._session_id,
            operation_id=operation_id,
            correlation_id=request.correlation_id,
            state=GitSessionHistoryState.RUNNING,
            sequence=0,
            expected_internal_tip=tip,
            expected_tree=expected_tree,
            commit_intent_sha256=_intent_digest(operation_id, raw_commit),
        )
        return _CheckpointIntent(
            operation=operation,
            alternate_index=GitAlternateIndex(
                common_git=self._root / ".git",
                session_id=self._session_id,
                operation_id=operation_id,
            ),
            expected_commit_id=_git_object_id("commit", raw_commit, algorithm),
            message=message,
            identity=effective_identity,
            timestamps=timestamps,
            invariants=invariants,
            expected_entries=tuple(
                (path, mode, oid)
                for path, (mode, oid) in sorted(
                    expected.items(), key=lambda item: item[0].encode("utf-8")
                )
            ),
            snapshots=request.snapshots,
        )

    def _stage(self, intent: _CheckpointIntent) -> None:
        identity = _directory_identity(self._root)
        runner = GitProcessRunner(self._root)
        alternate = intent.alternate_index
        _required(
            runner,
            identity,
            ("read-tree", intent.operation.expected_internal_tip),
            GitProcessProfile.INTERNAL_CHECKPOINT,
            alternate_index=alternate,
        )
        expected = {path: (mode, oid) for path, mode, oid in intent.expected_entries}
        for snapshot in intent.snapshots:
            if snapshot.operation is SessionCheckpointPathOperation.DELETE:
                _required(
                    runner,
                    identity,
                    ("update-index", "--force-remove", "--", snapshot.path),
                    GitProcessProfile.INTERNAL_CHECKPOINT,
                    alternate_index=alternate,
                )
                continue
            assert snapshot.content is not None
            blob = _object_id(
                _required(
                    runner,
                    identity,
                    ("hash-object", "-w", "--no-filters", "--stdin"),
                    GitProcessProfile.INTERNAL_CHECKPOINT,
                    stdin=snapshot.content,
                    alternate_index=alternate,
                ).stdout
            )
            mode, expected_blob = expected[snapshot.path]
            if blob != expected_blob:
                raise _error(SessionCheckpointFailureCode.OUTCOME_UNKNOWN)
            _required(
                runner,
                identity,
                (
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"{mode},{blob},{snapshot.path}",
                ),
                GitProcessProfile.INTERNAL_CHECKPOINT,
                alternate_index=alternate,
            )
        if _index_entries(runner, identity, alternate) != expected:
            raise _error(SessionCheckpointFailureCode.RECOVERY_REQUIRED)
        tree = _object_id(
            _required(
                runner,
                identity,
                ("write-tree",),
                GitProcessProfile.INTERNAL_CHECKPOINT,
                alternate_index=alternate,
            ).stdout
        )
        if tree != intent.operation.expected_tree:
            raise _error(SessionCheckpointFailureCode.RECOVERY_REQUIRED)
        for path in expected:
            classify_session_checkpoint_path(path, self._session_id)

    def _commit(self, intent: _CheckpointIntent) -> str:
        identity = _directory_identity(self._root)
        commit = _object_id(
            _required(
                GitProcessRunner(self._root),
                identity,
                (
                    "commit-tree",
                    intent.operation.expected_tree,
                    "-p",
                    intent.operation.expected_internal_tip,
                ),
                GitProcessProfile.INTERNAL_CHECKPOINT,
                stdin=intent.message,
                effective_identity=intent.identity,
                alternate_index=intent.alternate_index,
                commit_timestamps=intent.timestamps,
            ).stdout
        )
        if commit != intent.expected_commit_id:
            raise _error(SessionCheckpointFailureCode.OUTCOME_UNKNOWN)
        return commit

    def _cas(
        self,
        operation: GitSessionCheckpointOperationRecord,
        checkpoint_id: str,
    ) -> None:
        identity = GitSessionIdentity(self._root, self._session_id)
        _required(
            GitProcessRunner(self._root),
            _directory_identity(self._root),
            (
                "update-ref",
                identity.internal_ref,
                checkpoint_id,
                operation.expected_internal_tip,
            ),
            GitProcessProfile.MUTATION,
        )

    def _verify_invariants(
        self,
        intent: _CheckpointIntent,
        checkpoint_id: str,
    ) -> None:
        identity = GitSessionIdentity(self._root, self._session_id)
        main_identity = _directory_identity(identity.main_root)
        session_identity = _directory_identity(identity.worktree_root)
        main_runner = GitProcessRunner(identity.main_root)
        session_runner = GitProcessRunner(identity.worktree_root)
        if (
            self._internal_tip() != checkpoint_id
            or not self._commit_matches_intent(checkpoint_id, intent.operation)
            or _capture_invariants(
                main_runner,
                main_identity,
                session_runner,
                session_identity,
                identity,
            )
            != intent.invariants
        ):
            raise _error(SessionCheckpointFailureCode.OUTCOME_UNKNOWN)

    def _internal_tip(self) -> str:
        identity = GitSessionIdentity(self._root, self._session_id)
        return _object_id(
            _required(
                GitProcessRunner(self._root),
                _directory_identity(self._root),
                ("rev-parse", "--verify", identity.internal_ref),
                GitProcessProfile.READ_ONLY,
            ).stdout
        )

    def _commit_matches_intent(
        self,
        commit_id: str,
        operation: GitSessionCheckpointOperationRecord,
    ) -> bool:
        try:
            payload = _required(
                GitProcessRunner(self._root),
                _directory_identity(self._root),
                ("cat-file", "commit", commit_id),
                GitProcessProfile.READ_ONLY,
            ).stdout
            return _intent_digest(operation.operation_id, payload) == (
                operation.commit_intent_sha256
            )
        except (SessionCheckpointError, GitProcessFailure):
            return False

    def _cleanup_committed_index(
        self,
        history: GitSessionHistory,
        operation: GitSessionCheckpointOperationRecord,
    ) -> None:
        if operation.operation_id not in set(
            history.inspect().preserved_checkpoint_indices
        ):
            return
        history.remove_completed_checkpoint_index(
            GitAlternateIndex(
                common_git=self._root / ".git",
                session_id=self._session_id,
                operation_id=operation.operation_id,
            )
        )

    def _preserve_uncertain(
        self,
        operation: GitSessionCheckpointOperationRecord | None,
        ref_updated: bool,
    ) -> None:
        if operation is None:
            return
        try:
            with open_git_session_history(
                self._root,
                self._manifest,
                self._session_id,
                create=True,
            ) as history:
                current = history.read_checkpoint_operation(operation.operation_id)
                if current.state is not GitSessionHistoryState.RUNNING:
                    return
                preserved = current.operation_id in set(
                    history.inspect().preserved_checkpoint_indices
                )
                state = (
                    GitSessionHistoryState.OUTCOME_UNKNOWN
                    if ref_updated or preserved
                    else GitSessionHistoryState.RECOVERY_REQUIRED
                )
                history.write_checkpoint_operation(
                    advance_checkpoint_operation(
                        current,
                        state,
                        alternate_index_preserved=preserved,
                    )
                )
        except BaseException:
            return


def _capture_invariants(
    main_runner: GitProcessRunner,
    main_identity: tuple[int, int],
    session_runner: GitProcessRunner,
    session_identity: tuple[int, int],
    identity: GitSessionIdentity,
) -> _RepositoryInvariants:
    main_symbolic = _line(
        _required(
            main_runner,
            main_identity,
            ("symbolic-ref", "--quiet", "HEAD"),
            GitProcessProfile.READ_ONLY,
        ).stdout
    )
    session_symbolic = _line(
        _required(
            session_runner,
            session_identity,
            ("symbolic-ref", "--quiet", "HEAD"),
            GitProcessProfile.READ_ONLY,
        ).stdout
    )
    if main_symbolic != "refs/heads/main" or session_symbolic != (
        f"refs/heads/{identity.branch}"
    ):
        raise _error(SessionCheckpointFailureCode.SESSION_NOT_READY)
    main_status = _required(
        main_runner,
        main_identity,
        ("status", "--porcelain=v2", "-z", "--untracked-files=all"),
        GitProcessProfile.READ_ONLY,
    ).stdout
    if main_status:
        raise _error(SessionCheckpointFailureCode.SESSION_NOT_READY)
    main_index = _required(
        main_runner,
        main_identity,
        ("ls-files", "--stage", "-z"),
        GitProcessProfile.READ_ONLY,
    ).stdout
    session_index = _required(
        session_runner,
        session_identity,
        ("ls-files", "--stage", "-z"),
        GitProcessProfile.READ_ONLY,
    ).stdout
    return _RepositoryInvariants(
        main_symbolic_head=main_symbolic,
        main_head=_object_id(
            _required(
                main_runner,
                main_identity,
                ("rev-parse", "--verify", "HEAD"),
                GitProcessProfile.READ_ONLY,
            ).stdout
        ),
        main_index_sha256=hashlib.sha256(main_index).hexdigest(),
        main_status_sha256=hashlib.sha256(main_status).hexdigest(),
        session_symbolic_head=session_symbolic,
        session_head=_object_id(
            _required(
                session_runner,
                session_identity,
                ("rev-parse", "--verify", "HEAD"),
                GitProcessProfile.READ_ONLY,
            ).stdout
        ),
        session_index_sha256=hashlib.sha256(session_index).hexdigest(),
    )


def _tree_entries(
    runner: GitProcessRunner,
    identity: tuple[int, int],
    commit_id: str,
    session_id: str,
) -> dict[str, tuple[str, str]]:
    output = _required(
        runner,
        identity,
        ("ls-tree", "-r", "-z", "--full-tree", commit_id, "--"),
        GitProcessProfile.READ_ONLY,
    ).stdout
    result: dict[str, tuple[str, str]] = {}
    for row in output.split(b"\x00"):
        if not row:
            continue
        try:
            header, raw_path = row.split(b"\t", 1)
            mode, kind, raw_oid = header.split(b" ")
            path = raw_path.decode("utf-8", errors="strict")
            oid = raw_oid.decode("ascii")
        except (UnicodeError, ValueError):
            raise _error(SessionCheckpointFailureCode.RECOVERY_REQUIRED) from None
        if (
            mode != b"100644"
            or kind != b"blob"
            or _OBJECT_ID.fullmatch(raw_oid) is None
            or len(raw_oid) != len(commit_id)
            or path in result
        ):
            raise _error(SessionCheckpointFailureCode.RECOVERY_REQUIRED)
        try:
            classify_session_checkpoint_path(path, session_id)
        except SessionCheckpointError:
            raise _error(SessionCheckpointFailureCode.RECOVERY_REQUIRED) from None
        result[path] = ("100644", oid)
        if len(result) > SESSION_CHECKPOINT_MAX_PATHS:
            raise _error(SessionCheckpointFailureCode.RECOVERY_REQUIRED)
    _verify_tree_blob_sizes(runner, identity, result)
    return result


def _verify_tree_blob_sizes(
    runner: GitProcessRunner,
    identity: tuple[int, int],
    entries: dict[str, tuple[str, str]],
) -> None:
    if not entries:
        return
    object_ids = tuple(
        sorted({oid for _mode, oid in entries.values()}, key=lambda oid: oid.encode())
    )
    stdin = b"".join(oid.encode("ascii") + b"\n" for oid in object_ids)
    output = _required(
        runner,
        identity,
        ("cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"),
        GitProcessProfile.READ_ONLY,
        stdin=stdin,
    ).stdout
    if not output.endswith(b"\n") or b"\r" in output or b"\x00" in output:
        raise _error(SessionCheckpointFailureCode.RECOVERY_REQUIRED)
    rows = output[:-1].split(b"\n")
    if len(rows) != len(object_ids):
        raise _error(SessionCheckpointFailureCode.RECOVERY_REQUIRED)
    sizes: dict[str, int] = {}
    for expected_oid, row in zip(object_ids, rows, strict=True):
        try:
            raw_oid, kind, raw_size = row.split(b" ")
            oid = raw_oid.decode("ascii")
        except (UnicodeError, ValueError):
            raise _error(SessionCheckpointFailureCode.RECOVERY_REQUIRED) from None
        if (
            oid != expected_oid
            or kind != b"blob"
            or not raw_size
            or not raw_size.isdigit()
            or (len(raw_size) > 1 and raw_size.startswith(b"0"))
            or len(raw_size) > len(str(SESSION_CHECKPOINT_MAX_TOTAL_BYTES))
            or oid in sizes
        ):
            raise _error(SessionCheckpointFailureCode.RECOVERY_REQUIRED)
        size = int(raw_size)
        if size > SESSION_CHECKPOINT_MAX_TOTAL_BYTES:
            raise _error(SessionCheckpointFailureCode.RECOVERY_REQUIRED)
        sizes[oid] = size
    total = 0
    for _mode, oid in entries.values():
        total += sizes[oid]
        if total > SESSION_CHECKPOINT_MAX_TOTAL_BYTES:
            raise _error(SessionCheckpointFailureCode.RECOVERY_REQUIRED)


def _apply_snapshots(
    runner: GitProcessRunner,
    identity: tuple[int, int],
    current: dict[str, tuple[str, str]],
    snapshots: tuple[SessionCheckpointSnapshot, ...],
    algorithm: str,
) -> dict[str, tuple[str, str]]:
    result = dict(current)
    for snapshot in snapshots:
        prior = current.get(snapshot.path)
        if snapshot.operation is SessionCheckpointPathOperation.CREATE:
            if prior is not None:
                raise _error(SessionCheckpointFailureCode.SNAPSHOT_DRIFT)
        else:
            if prior is None:
                raise _error(SessionCheckpointFailureCode.SNAPSHOT_DRIFT)
            content = _required(
                runner,
                identity,
                ("cat-file", "blob", prior[1]),
                GitProcessProfile.READ_ONLY,
            ).stdout
            if hashlib.sha256(content).hexdigest() != snapshot.before_sha256:
                raise _error(SessionCheckpointFailureCode.SNAPSHOT_DRIFT)
        if snapshot.operation is SessionCheckpointPathOperation.DELETE:
            result.pop(snapshot.path, None)
        else:
            assert snapshot.content is not None
            result[snapshot.path] = (
                "100644",
                _git_object_id("blob", snapshot.content, algorithm),
            )
    return result


def _index_entries(
    runner: GitProcessRunner,
    identity: tuple[int, int],
    alternate: GitAlternateIndex,
) -> dict[str, tuple[str, str]]:
    output = _required(
        runner,
        identity,
        ("ls-files", "--stage", "-z"),
        GitProcessProfile.INTERNAL_CHECKPOINT,
        alternate_index=alternate,
    ).stdout
    result: dict[str, tuple[str, str]] = {}
    for row in output.split(b"\x00"):
        if not row:
            continue
        matched = _INDEX_ROW.match(row)
        if matched is None:
            raise _error(SessionCheckpointFailureCode.RECOVERY_REQUIRED)
        try:
            path = row[matched.end() :].decode("utf-8", errors="strict")
            oid = matched.group(2).decode("ascii")
        except UnicodeError:
            raise _error(SessionCheckpointFailureCode.RECOVERY_REQUIRED) from None
        if path in result:
            raise _error(SessionCheckpointFailureCode.RECOVERY_REQUIRED)
        result[path] = ("100644", oid)
    return result


def _tree_id(
    runner: GitProcessRunner,
    identity: tuple[int, int],
    commit_id: str,
) -> str:
    return _object_id(
        _required(
            runner,
            identity,
            ("rev-parse", "--verify", f"{commit_id}^{{tree}}"),
            GitProcessProfile.READ_ONLY,
        ).stdout
    )


def _effective_identity(
    runner: GitProcessRunner,
    identity: tuple[int, int],
) -> GitEffectiveIdentity:
    return GitEffectiveIdentity(
        author=_identity_line(
            _required(
                runner,
                identity,
                ("var", "GIT_AUTHOR_IDENT"),
                GitProcessProfile.READ_ONLY,
            ).stdout
        ),
        committer=_identity_line(
            _required(
                runner,
                identity,
                ("var", "GIT_COMMITTER_IDENT"),
                GitProcessProfile.READ_ONLY,
            ).stdout
        ),
    )


def _identity_line(value: bytes) -> GitIdentity:
    try:
        prefix, _, suffix = value.rstrip(b"\n").rpartition(b" <")
        email, separator, _tail = suffix.partition(b">")
        if not prefix or not separator:
            raise ValueError
        return GitIdentity(
            prefix.decode("utf-8", errors="strict"),
            email.decode("utf-8", errors="strict"),
        )
    except (UnicodeError, ValueError, RuntimeError):
        raise _error(SessionCheckpointFailureCode.GIT_FAILED) from None


def _derive_tree(entries: dict[str, tuple[str, str]], algorithm: str) -> str:
    root: dict[str, object] = {}
    for path, (mode, oid) in entries.items():
        parts = path.split("/")
        node = root
        for part in parts[:-1]:
            child = node.setdefault(part, {})
            if type(child) is not dict:
                raise _error(SessionCheckpointFailureCode.RECOVERY_REQUIRED)
            node = child
        if parts[-1] in node:
            raise _error(SessionCheckpointFailureCode.RECOVERY_REQUIRED)
        node[parts[-1]] = (mode, oid)

    def encode(node: dict[str, object]) -> str:
        items: list[tuple[bytes, bool, str, str]] = []
        for name, value in node.items():
            raw_name = name.encode("utf-8")
            if type(value) is dict:
                items.append((raw_name, True, "40000", encode(value)))
            else:
                mode, oid = value  # type: ignore[misc]
                items.append((raw_name, False, mode, oid))
        items.sort(key=lambda item: item[0] + (b"/" if item[1] else b"\x00"))
        payload = b"".join(
            mode.encode("ascii") + b" " + name + b"\x00" + bytes.fromhex(oid)
            for name, _directory, mode, oid in items
        )
        return _git_object_id("tree", payload, algorithm)

    return encode(root)


def _message(session_id: str, correlation_id: str, operation_id: str) -> bytes:
    return (
        f"{SESSION_CHECKPOINT_MESSAGE_PREFIX}{session_id}\n\n"
        f"Correlation: {correlation_id}\nOperation: {operation_id}\n"
    ).encode("utf-8", errors="strict")


def _commit_payload(
    tree: str,
    parent: str,
    identity: GitEffectiveIdentity,
    timestamps: GitCommitTimestamps,
    message: bytes,
) -> bytes:
    author = identity.author
    committer = identity.committer
    return (
        f"tree {tree}\n"
        f"parent {parent}\n"
        f"author {author.name} <{author.email}> "
        f"{timestamps.author_timestamp_seconds} +0000\n"
        f"committer {committer.name} <{committer.email}> "
        f"{timestamps.committer_timestamp_seconds} +0000\n\n"
    ).encode("utf-8", errors="strict") + message


def _intent_digest(operation_id: str, payload: bytes) -> str:
    return hashlib.sha256(
        b"story-writing-agents/session-checkpoint-intent/v1\x00"
        + operation_id.encode("ascii")
        + b"\x00"
        + payload
    ).hexdigest()


def _git_object_id(kind: str, payload: bytes, algorithm: str) -> str:
    value = f"{kind} {len(payload)}\0".encode("ascii") + payload
    return (
        hashlib.sha1(value) if algorithm == "sha1" else hashlib.sha256(value)
    ).hexdigest()


def _required(
    runner: GitProcessRunner,
    identity: tuple[int, int],
    arguments: tuple[str, ...],
    profile: GitProcessProfile,
    *,
    stdin: bytes = b"",
    effective_identity: GitEffectiveIdentity | None = None,
    alternate_index: GitAlternateIndex | None = None,
    commit_timestamps: GitCommitTimestamps | None = None,
) -> GitProcessResult:
    try:
        result = runner.run(
            arguments,
            expected_identity=identity,
            stdin=stdin,
            profile=profile,
            effective_identity=effective_identity,
            alternate_index=alternate_index,
            commit_timestamps=commit_timestamps,
        )
    except GitProcessFailure:
        raise _error(SessionCheckpointFailureCode.GIT_FAILED) from None
    if result.returncode != 0:
        raise _error(SessionCheckpointFailureCode.GIT_FAILED)
    return result


def _object_id(value: bytes) -> str:
    raw = value.strip()
    if _OBJECT_ID.fullmatch(raw) is None:
        raise _error(SessionCheckpointFailureCode.GIT_FAILED)
    return raw.decode("ascii")


def _line(value: bytes) -> str:
    try:
        text = value.decode("utf-8", errors="strict")
    except UnicodeError:
        raise _error(SessionCheckpointFailureCode.GIT_FAILED) from None
    if "\x00" in text or "\r" in text or text.count("\n") > 1:
        raise _error(SessionCheckpointFailureCode.GIT_FAILED)
    return text.rstrip("\n")


def _directory_identity(root: Path) -> tuple[int, int]:
    try:
        value = os.lstat(root)
        canonical = Path(os.path.realpath(root))
    except OSError:
        raise _error(SessionCheckpointFailureCode.SESSION_NOT_READY) from None
    if canonical != root or not root.is_dir() or root.is_symlink():
        raise _error(SessionCheckpointFailureCode.SESSION_NOT_READY)
    return value.st_dev, value.st_ino


def _reconstruct_request(value: object) -> SessionCheckpointRequest:
    try:
        if type(value) is not SessionCheckpointRequest:
            raise TypeError
        return SessionCheckpointRequest(
            session_id=value.session_id,
            correlation_id=value.correlation_id,
            snapshots=tuple(
                SessionCheckpointSnapshot(
                    owner=item.owner,
                    path=item.path,
                    operation=item.operation,
                    before_sha256=item.before_sha256,
                    after_sha256=item.after_sha256,
                    content=item.content,
                )
                for item in value.snapshots
            ),
        )
    except (AttributeError, TypeError, SessionCheckpointError):
        raise _error(SessionCheckpointFailureCode.INVALID_REQUEST) from None


def _reconstruct_manifest(value: object) -> GitBaselineManifest:
    try:
        if type(value) is not GitBaselineManifest:
            raise TypeError
        return GitBaselineManifest(
            tuple(
                GitManifestEntry(item.path, item.content_digest)
                for item in value.entries
            )
        )
    except (AttributeError, TypeError, RuntimeError):
        raise _error(SessionCheckpointFailureCode.INVALID_REQUEST) from None


def _operation_result(
    operation: GitSessionCheckpointOperationRecord,
) -> SessionCheckpointResult:
    state = SessionCheckpointState(operation.state.value)
    recovery = {
        SessionCheckpointState.RUNNING: SessionCheckpointRecoveryAction.INSPECT,
        SessionCheckpointState.OUTCOME_UNKNOWN: SessionCheckpointRecoveryAction.INSPECT,
        SessionCheckpointState.RECOVERY_REQUIRED: SessionCheckpointRecoveryAction.OPERATOR_RECOVERY,
    }.get(state, SessionCheckpointRecoveryAction.NONE)
    return SessionCheckpointResult(
        session_id=operation.session_id,
        correlation_id=operation.correlation_id,
        state=state,
        recovery_action=recovery,
        internal_tip=operation.checkpoint_id or operation.expected_internal_tip,
        operation_id=operation.operation_id,
        checkpoint_id=operation.checkpoint_id,
    )


def _error(code: SessionCheckpointFailureCode) -> SessionCheckpointError:
    action = {
        SessionCheckpointFailureCode.SNAPSHOT_DRIFT: SessionCheckpointRecoveryAction.RETRY,
        SessionCheckpointFailureCode.SESSION_NOT_READY: SessionCheckpointRecoveryAction.RETRY,
        SessionCheckpointFailureCode.OUTCOME_UNKNOWN: SessionCheckpointRecoveryAction.INSPECT,
        SessionCheckpointFailureCode.RECOVERY_REQUIRED: SessionCheckpointRecoveryAction.OPERATOR_RECOVERY,
    }.get(code, SessionCheckpointRecoveryAction.NONE)
    error = SessionCheckpointError(code, action)
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True
    return error


__all__ = ["SessionCheckpointService"]
