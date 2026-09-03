"""Pure durable contracts for one story session's local Git history."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any


GIT_SESSION_HISTORY_DIRECTORY = "story-writing-agents/runtime/session-history"
GIT_SESSION_HISTORY_SCHEMA_VERSION = 1
GIT_SESSION_HISTORY_MAX_RECORD_BYTES = 1024 * 1024
GIT_SESSION_HISTORY_MAX_PATHS = 4096
GIT_SESSION_HISTORY_MAX_TEXT_BYTES = 4096

_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_OPERATION_ID = re.compile(r"[0-9a-f]{32}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_OPAQUE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


class GitSessionHistoryAction(StrEnum):
    STORY_COMMIT = "story_commit"
    CHECKPOINT = "checkpoint"


class GitSessionHistoryState(StrEnum):
    PREPARED = "prepared"
    RUNNING = "running"
    STAGED = "staged"
    COMMITTED = "committed"
    REJECTED = "rejected"
    OUTCOME_UNKNOWN = "outcome_unknown"
    RECOVERY_REQUIRED = "recovery_required"


class GitSessionHistoryFailureCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    UNSAFE_ROOT = "unsafe_root"
    LOCK_UNAVAILABLE = "lock_unavailable"
    RECORD_NOT_FOUND = "record_not_found"
    RECORD_EXISTS = "record_exists"
    RECORD_INVALID = "record_invalid"
    RECORD_LIMIT_EXCEEDED = "record_limit_exceeded"
    TRANSITION_INVALID = "transition_invalid"
    PERSISTENCE_FAILED = "persistence_failed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    RECOVERY_REQUIRED = "recovery_required"


class GitSessionHistoryRecoveryAction(StrEnum):
    NONE = "none"
    RETRY = "retry"
    REPREPARE = "reprepare"
    INSPECT = "inspect"
    OPERATOR_RECOVERY = "operator_recovery"


class GitSessionIdentitySource(StrEnum):
    ENVIRONMENT = "environment"
    REPOSITORY = "repository"
    GLOBAL = "global"
    SYSTEM = "system"


class GitSessionCandidatePathOperation(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


_DIAGNOSTICS = {
    GitSessionHistoryFailureCode.INVALID_REQUEST: "Git session history request is invalid.",
    GitSessionHistoryFailureCode.UNSAFE_ROOT: "Git session history root is unsafe.",
    GitSessionHistoryFailureCode.LOCK_UNAVAILABLE: "Git session history lock is unavailable.",
    GitSessionHistoryFailureCode.RECORD_NOT_FOUND: "Git session history record does not exist.",
    GitSessionHistoryFailureCode.RECORD_EXISTS: "Git session history record already exists.",
    GitSessionHistoryFailureCode.RECORD_INVALID: "Git session history record is invalid.",
    GitSessionHistoryFailureCode.RECORD_LIMIT_EXCEEDED: "Git session history record exceeds its limit.",
    GitSessionHistoryFailureCode.TRANSITION_INVALID: "Git session history transition is invalid.",
    GitSessionHistoryFailureCode.PERSISTENCE_FAILED: "Git session history persistence failed.",
    GitSessionHistoryFailureCode.OUTCOME_UNKNOWN: "Git session history operation outcome is unknown.",
    GitSessionHistoryFailureCode.RECOVERY_REQUIRED: "Git session history requires recovery.",
}


class GitSessionHistoryError(RuntimeError):
    """One bounded content-free session-history failure."""

    __slots__ = ("code", "recovery_action")

    def __init__(
        self,
        code: GitSessionHistoryFailureCode,
        recovery_action: GitSessionHistoryRecoveryAction,
    ) -> None:
        if (
            type(code) is not GitSessionHistoryFailureCode
            or type(recovery_action) is not GitSessionHistoryRecoveryAction
        ):
            raise TypeError("session-history error facts are invalid")
        self.code = code
        self.recovery_action = recovery_action
        super().__init__(_DIAGNOSTICS[code])


@dataclass(frozen=True, slots=True)
class GitSessionCandidatePath:
    """One content-free before/after identity in a prepared candidate."""

    path: str
    operation: GitSessionCandidatePathOperation
    before_sha256: str | None
    after_sha256: str | None

    def __post_init__(self) -> None:
        _require_relative_path(self.path)
        if type(self.operation) is not GitSessionCandidatePathOperation:
            raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)
        before = _optional_digest(self.before_sha256)
        after = _optional_digest(self.after_sha256)
        if (
            (
                self.operation is GitSessionCandidatePathOperation.CREATE
                and (before is not None or after is None)
            )
            or (
                self.operation is GitSessionCandidatePathOperation.UPDATE
                and (before is None or after is None or before == after)
            )
            or (
                self.operation is GitSessionCandidatePathOperation.DELETE
                and (before is None or after is not None)
            )
        ):
            raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)


@dataclass(frozen=True, slots=True)
class GitSessionCandidateRecord:
    """Immutable content-addressed evidence prepared before Git mutation."""

    session_id: str
    branch: str
    branch_tip: str
    expected_tree: str
    worktree_identity_sha256: str
    application_id: str
    proposal_id: str
    correlation_id: str
    paths: tuple[GitSessionCandidatePath, ...]
    proposed_message: str = field(repr=False)
    impact_boundary: tuple[str, ...] = field(repr=False)
    author_identity_source: GitSessionIdentitySource
    committer_identity_source: GitSessionIdentitySource
    candidate_id: str = ""
    schema_version: int = GIT_SESSION_HISTORY_SCHEMA_VERSION
    state: GitSessionHistoryState = GitSessionHistoryState.PREPARED

    def __post_init__(self) -> None:
        session = _require_session_id(self.session_id)
        if self.branch != f"story/session/{session}":
            raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)
        object_ids = (
            _require_object_id(self.branch_tip),
            _require_object_id(self.expected_tree),
        )
        if len(object_ids[0]) != len(object_ids[1]):
            raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)
        _require_digest(self.worktree_identity_sha256)
        for value in (self.application_id, self.proposal_id, self.correlation_id):
            _require_opaque_id(value)
        if (
            type(self.paths) is not tuple
            or not self.paths
            or len(self.paths) > GIT_SESSION_HISTORY_MAX_PATHS
            or any(type(item) is not GitSessionCandidatePath for item in self.paths)
            or len({item.path for item in self.paths}) != len(self.paths)
        ):
            raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)
        paths = tuple(_reconstruct_candidate_path(item) for item in self.paths)
        if paths != self.paths or paths != tuple(
            sorted(paths, key=lambda item: item.path.encode("utf-8"))
        ):
            raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)
        _require_text(self.proposed_message, allow_newline=False)
        if (
            type(self.impact_boundary) is not tuple
            or not self.impact_boundary
            or len(self.impact_boundary) > 64
        ):
            raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)
        for value in self.impact_boundary:
            _require_text(value, allow_newline=False)
        if len(set(self.impact_boundary)) != len(
            self.impact_boundary
        ) or self.impact_boundary != tuple(
            sorted(self.impact_boundary, key=lambda value: value.encode("utf-8"))
        ):
            raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)
        if (
            type(self.author_identity_source) is not GitSessionIdentitySource
            or type(self.committer_identity_source) is not GitSessionIdentitySource
            or type(self.schema_version) is not int
            or self.schema_version != GIT_SESSION_HISTORY_SCHEMA_VERSION
            or self.state is not GitSessionHistoryState.PREPARED
        ):
            raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)
        if self.candidate_id == "":
            object.__setattr__(self, "candidate_id", _candidate_id(self))
        else:
            _require_digest(self.candidate_id)
        if _candidate_id(self) != self.candidate_id:
            raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)


@dataclass(frozen=True, slots=True)
class GitSessionCommitOperationRecord:
    """Monotonic durable state for one exact candidate decision and commit."""

    session_id: str
    operation_id: str
    candidate_id: str
    correlation_id: str
    state: GitSessionHistoryState
    sequence: int
    expected_branch_tip: str
    commit_id: str | None = None
    schema_version: int = GIT_SESSION_HISTORY_SCHEMA_VERSION
    action: GitSessionHistoryAction = GitSessionHistoryAction.STORY_COMMIT

    def __post_init__(self) -> None:
        _require_session_id(self.session_id)
        _require_operation_id(self.operation_id)
        _require_digest(self.candidate_id)
        _require_opaque_id(self.correlation_id)
        tip = _require_object_id(self.expected_branch_tip)
        commit = _optional_object_id(self.commit_id)
        if (
            type(self.state) is not GitSessionHistoryState
            or self.state is GitSessionHistoryState.PREPARED
            or type(self.sequence) is not int
            or self.sequence < 0
            or self.sequence > 2**63 - 1
            or type(self.schema_version) is not int
            or self.schema_version != GIT_SESSION_HISTORY_SCHEMA_VERSION
            or self.action is not GitSessionHistoryAction.STORY_COMMIT
            or (commit is not None and len(commit) != len(tip))
            or (self.state is GitSessionHistoryState.COMMITTED) != (commit is not None)
            or self.state not in _COMMIT_STATES
        ):
            raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)


@dataclass(frozen=True, slots=True)
class GitSessionCheckpointOperationRecord:
    """Monotonic durable state for one internal-ref checkpoint operation."""

    session_id: str
    operation_id: str
    correlation_id: str
    state: GitSessionHistoryState
    sequence: int
    expected_internal_tip: str
    expected_tree: str
    commit_intent_sha256: str
    checkpoint_id: str | None = None
    alternate_index_preserved: bool = False
    schema_version: int = GIT_SESSION_HISTORY_SCHEMA_VERSION
    action: GitSessionHistoryAction = GitSessionHistoryAction.CHECKPOINT

    def __post_init__(self) -> None:
        _require_session_id(self.session_id)
        _require_operation_id(self.operation_id)
        _require_opaque_id(self.correlation_id)
        tip = _require_object_id(self.expected_internal_tip)
        tree = _require_object_id(self.expected_tree)
        _require_digest(self.commit_intent_sha256)
        checkpoint = _optional_object_id(self.checkpoint_id)
        if (
            type(self.state) is not GitSessionHistoryState
            or type(self.sequence) is not int
            or self.sequence < 0
            or self.sequence > 2**63 - 1
            or type(self.alternate_index_preserved) is not bool
            or type(self.schema_version) is not int
            or self.schema_version != GIT_SESSION_HISTORY_SCHEMA_VERSION
            or self.action is not GitSessionHistoryAction.CHECKPOINT
            or len(tree) != len(tip)
            or (checkpoint is not None and len(checkpoint) != len(tip))
            or (self.state is GitSessionHistoryState.COMMITTED)
            != (checkpoint is not None)
            or self.state not in _CHECKPOINT_STATES
            or (
                self.alternate_index_preserved
                and self.state
                not in {
                    GitSessionHistoryState.OUTCOME_UNKNOWN,
                    GitSessionHistoryState.RECOVERY_REQUIRED,
                }
            )
        ):
            raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)


@dataclass(frozen=True, slots=True)
class GitSessionHistoryInspection:
    """Content-free bounded inventory of persisted identities and states."""

    session_id: str
    candidate_ids: tuple[str, ...]
    commit_operations: tuple[tuple[str, GitSessionHistoryState], ...]
    checkpoint_operations: tuple[tuple[str, GitSessionHistoryState], ...]
    preserved_checkpoint_indices: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_session_id(self.session_id)
        if (
            type(self.candidate_ids) is not tuple
            or self.candidate_ids != tuple(sorted(self.candidate_ids))
            or len(set(self.candidate_ids)) != len(self.candidate_ids)
        ):
            raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)
        for value in self.candidate_ids:
            _require_digest(value)
        for values, allowed in (
            (self.commit_operations, _COMMIT_STATES),
            (self.checkpoint_operations, _CHECKPOINT_STATES),
        ):
            if type(values) is not tuple or values != tuple(sorted(values)):
                raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)
            for operation_id, state in values:
                _require_operation_id(operation_id)
                if type(state) is not GitSessionHistoryState or state not in allowed:
                    raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)
        if type(
            self.preserved_checkpoint_indices
        ) is not tuple or self.preserved_checkpoint_indices != tuple(
            sorted(self.preserved_checkpoint_indices)
        ):
            raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)
        for operation_id in self.preserved_checkpoint_indices:
            _require_operation_id(operation_id)


_COMMIT_STATES = frozenset(
    {
        GitSessionHistoryState.RUNNING,
        GitSessionHistoryState.STAGED,
        GitSessionHistoryState.COMMITTED,
        GitSessionHistoryState.REJECTED,
        GitSessionHistoryState.OUTCOME_UNKNOWN,
        GitSessionHistoryState.RECOVERY_REQUIRED,
    }
)
_CHECKPOINT_STATES = frozenset(
    {
        GitSessionHistoryState.RUNNING,
        GitSessionHistoryState.COMMITTED,
        GitSessionHistoryState.OUTCOME_UNKNOWN,
        GitSessionHistoryState.RECOVERY_REQUIRED,
    }
)
_COMMIT_TRANSITIONS = {
    GitSessionHistoryState.RUNNING: frozenset(
        {
            GitSessionHistoryState.STAGED,
            GitSessionHistoryState.OUTCOME_UNKNOWN,
            GitSessionHistoryState.RECOVERY_REQUIRED,
        }
    ),
    GitSessionHistoryState.STAGED: frozenset(
        {
            GitSessionHistoryState.COMMITTED,
            GitSessionHistoryState.OUTCOME_UNKNOWN,
            GitSessionHistoryState.RECOVERY_REQUIRED,
        }
    ),
    GitSessionHistoryState.OUTCOME_UNKNOWN: frozenset(
        {GitSessionHistoryState.COMMITTED, GitSessionHistoryState.RECOVERY_REQUIRED}
    ),
    GitSessionHistoryState.REJECTED: frozenset(),
    GitSessionHistoryState.COMMITTED: frozenset(),
    GitSessionHistoryState.RECOVERY_REQUIRED: frozenset(),
}
_CHECKPOINT_TRANSITIONS = {
    GitSessionHistoryState.RUNNING: frozenset(
        {
            GitSessionHistoryState.COMMITTED,
            GitSessionHistoryState.OUTCOME_UNKNOWN,
            GitSessionHistoryState.RECOVERY_REQUIRED,
        }
    ),
    GitSessionHistoryState.OUTCOME_UNKNOWN: frozenset(
        {GitSessionHistoryState.COMMITTED, GitSessionHistoryState.RECOVERY_REQUIRED}
    ),
    GitSessionHistoryState.COMMITTED: frozenset(),
    GitSessionHistoryState.RECOVERY_REQUIRED: frozenset(),
}


def build_candidate_record(**values: object) -> GitSessionCandidateRecord:
    """Build one candidate with its content identity calculated canonically."""

    return GitSessionCandidateRecord(**values)  # type: ignore[arg-type]


def advance_commit_operation(
    current: GitSessionCommitOperationRecord,
    state: GitSessionHistoryState,
    *,
    commit_id: str | None = None,
) -> GitSessionCommitOperationRecord:
    """Return the next legal immutable commit-operation record."""

    value = _reconstruct_commit_record(current)
    if (
        type(state) is not GitSessionHistoryState
        or state not in _COMMIT_TRANSITIONS[value.state]
    ):
        raise _error(GitSessionHistoryFailureCode.TRANSITION_INVALID)
    return replace(
        value,
        state=state,
        sequence=value.sequence + 1,
        commit_id=commit_id,
    )


def advance_checkpoint_operation(
    current: GitSessionCheckpointOperationRecord,
    state: GitSessionHistoryState,
    *,
    checkpoint_id: str | None = None,
    alternate_index_preserved: bool = False,
) -> GitSessionCheckpointOperationRecord:
    """Return the next legal immutable checkpoint-operation record."""

    value = _reconstruct_checkpoint_record(current)
    if (
        type(state) is not GitSessionHistoryState
        or state not in _CHECKPOINT_TRANSITIONS[value.state]
    ):
        raise _error(GitSessionHistoryFailureCode.TRANSITION_INVALID)
    return replace(
        value,
        state=state,
        sequence=value.sequence + 1,
        checkpoint_id=checkpoint_id,
        alternate_index_preserved=alternate_index_preserved,
    )


def encode_candidate_record(value: GitSessionCandidateRecord) -> bytes:
    record = _reconstruct_candidate_record(value)
    return _encode(_candidate_object(record, include_id=True))


def decode_candidate_record(payload: bytes) -> GitSessionCandidateRecord:
    value = _decode(payload)
    try:
        paths = tuple(
            GitSessionCandidatePath(
                path=item["path"],
                operation=GitSessionCandidatePathOperation(item["operation"]),
                before_sha256=item["before_sha256"],
                after_sha256=item["after_sha256"],
            )
            for item in value["paths"]
        )
        record = GitSessionCandidateRecord(
            session_id=value["session_id"],
            branch=value["branch"],
            branch_tip=value["branch_tip"],
            expected_tree=value["expected_tree"],
            worktree_identity_sha256=value["worktree_identity_sha256"],
            application_id=value["application_id"],
            proposal_id=value["proposal_id"],
            correlation_id=value["correlation_id"],
            paths=paths,
            proposed_message=value["proposed_message"],
            impact_boundary=tuple(value["impact_boundary"]),
            author_identity_source=GitSessionIdentitySource(
                value["author_identity_source"]
            ),
            committer_identity_source=GitSessionIdentitySource(
                value["committer_identity_source"]
            ),
            candidate_id=value["candidate_id"],
            schema_version=value["schema_version"],
            state=GitSessionHistoryState(value["state"]),
        )
    except (KeyError, TypeError, ValueError, GitSessionHistoryError):
        raise _error(GitSessionHistoryFailureCode.RECORD_INVALID) from None
    if encode_candidate_record(record) != payload:
        raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)
    return record


def encode_commit_operation(value: GitSessionCommitOperationRecord) -> bytes:
    record = _reconstruct_commit_record(value)
    return _encode(_operation_object(record))


def decode_commit_operation(payload: bytes) -> GitSessionCommitOperationRecord:
    value = _decode(payload)
    try:
        record = GitSessionCommitOperationRecord(
            session_id=value["session_id"],
            operation_id=value["operation_id"],
            candidate_id=value["candidate_id"],
            correlation_id=value["correlation_id"],
            state=GitSessionHistoryState(value["state"]),
            sequence=value["sequence"],
            expected_branch_tip=value["expected_branch_tip"],
            commit_id=value["commit_id"],
            schema_version=value["schema_version"],
            action=GitSessionHistoryAction(value["action"]),
        )
    except (KeyError, TypeError, ValueError, GitSessionHistoryError):
        raise _error(GitSessionHistoryFailureCode.RECORD_INVALID) from None
    if encode_commit_operation(record) != payload:
        raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)
    return record


def encode_checkpoint_operation(value: GitSessionCheckpointOperationRecord) -> bytes:
    record = _reconstruct_checkpoint_record(value)
    return _encode(_operation_object(record))


def decode_checkpoint_operation(payload: bytes) -> GitSessionCheckpointOperationRecord:
    value = _decode(payload)
    try:
        record = GitSessionCheckpointOperationRecord(
            session_id=value["session_id"],
            operation_id=value["operation_id"],
            correlation_id=value["correlation_id"],
            state=GitSessionHistoryState(value["state"]),
            sequence=value["sequence"],
            expected_internal_tip=value["expected_internal_tip"],
            expected_tree=value["expected_tree"],
            commit_intent_sha256=value["commit_intent_sha256"],
            checkpoint_id=value["checkpoint_id"],
            alternate_index_preserved=value["alternate_index_preserved"],
            schema_version=value["schema_version"],
            action=GitSessionHistoryAction(value["action"]),
        )
    except (KeyError, TypeError, ValueError, GitSessionHistoryError):
        raise _error(GitSessionHistoryFailureCode.RECORD_INVALID) from None
    if encode_checkpoint_operation(record) != payload:
        raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)
    return record


def _candidate_object(
    value: GitSessionCandidateRecord, *, include_id: bool
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "application_id": value.application_id,
        "author_identity_source": value.author_identity_source.value,
        "branch": value.branch,
        "branch_tip": value.branch_tip,
        "committer_identity_source": value.committer_identity_source.value,
        "correlation_id": value.correlation_id,
        "expected_tree": value.expected_tree,
        "impact_boundary": list(value.impact_boundary),
        "paths": [
            {
                "after_sha256": item.after_sha256,
                "before_sha256": item.before_sha256,
                "operation": item.operation.value,
                "path": item.path,
            }
            for item in value.paths
        ],
        "proposal_id": value.proposal_id,
        "proposed_message": value.proposed_message,
        "schema_version": value.schema_version,
        "session_id": value.session_id,
        "state": value.state.value,
        "worktree_identity_sha256": value.worktree_identity_sha256,
    }
    if include_id:
        result["candidate_id"] = value.candidate_id
    return result


def _operation_object(value: object) -> dict[str, Any]:
    if type(value) is GitSessionCommitOperationRecord:
        return {
            "action": value.action.value,
            "candidate_id": value.candidate_id,
            "commit_id": value.commit_id,
            "correlation_id": value.correlation_id,
            "expected_branch_tip": value.expected_branch_tip,
            "operation_id": value.operation_id,
            "schema_version": value.schema_version,
            "sequence": value.sequence,
            "session_id": value.session_id,
            "state": value.state.value,
        }
    if type(value) is GitSessionCheckpointOperationRecord:
        return {
            "action": value.action.value,
            "alternate_index_preserved": value.alternate_index_preserved,
            "checkpoint_id": value.checkpoint_id,
            "commit_intent_sha256": value.commit_intent_sha256,
            "correlation_id": value.correlation_id,
            "expected_internal_tip": value.expected_internal_tip,
            "expected_tree": value.expected_tree,
            "operation_id": value.operation_id,
            "schema_version": value.schema_version,
            "sequence": value.sequence,
            "session_id": value.session_id,
            "state": value.state.value,
        }
    raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)


def _candidate_id(value: GitSessionCandidateRecord) -> str:
    return hashlib.sha256(
        _encode(_candidate_object(value, include_id=False))
    ).hexdigest()


def _encode(value: object) -> bytes:
    try:
        payload = (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError, UnicodeError):
        raise _error(GitSessionHistoryFailureCode.RECORD_INVALID) from None
    if len(payload) > GIT_SESSION_HISTORY_MAX_RECORD_BYTES:
        raise _error(GitSessionHistoryFailureCode.RECORD_LIMIT_EXCEEDED)
    return payload


def _decode(payload: bytes) -> dict[str, Any]:
    if (
        type(payload) is not bytes
        or not payload
        or len(payload) > GIT_SESSION_HISTORY_MAX_RECORD_BYTES
    ):
        code = (
            GitSessionHistoryFailureCode.RECORD_LIMIT_EXCEEDED
            if isinstance(payload, bytes)
            and len(payload) > GIT_SESSION_HISTORY_MAX_RECORD_BYTES
            else GitSessionHistoryFailureCode.RECORD_INVALID
        )
        raise _error(code)
    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, RecursionError, json.JSONDecodeError):
        raise _error(GitSessionHistoryFailureCode.RECORD_INVALID) from None
    if type(value) is not dict or _encode(value) != payload:
        raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite number")


def _require_session_id(value: object) -> str:
    if type(value) is not str or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)
    return value


def _require_operation_id(value: object) -> str:
    if type(value) is not str or _OPERATION_ID.fullmatch(value) is None:
        raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)
    return value


def _require_digest(value: object) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)
    return value


def _optional_digest(value: object) -> str | None:
    return None if value is None else _require_digest(value)


def _require_object_id(value: object) -> str:
    if type(value) is not str or _OBJECT_ID.fullmatch(value) is None:
        raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)
    return value


def _optional_object_id(value: object) -> str | None:
    return None if value is None else _require_object_id(value)


def _require_opaque_id(value: object) -> str:
    if type(value) is not str or _OPAQUE_ID.fullmatch(value) is None:
        raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)
    return value


def _require_text(value: object, *, allow_newline: bool) -> str:
    if type(value) is not str or not value:
        raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise _error(GitSessionHistoryFailureCode.RECORD_INVALID) from None
    forbidden = "\x00\r" + ("" if allow_newline else "\n")
    if len(encoded) > GIT_SESSION_HISTORY_MAX_TEXT_BYTES or any(
        character in value for character in forbidden
    ):
        raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)
    return value


def _require_relative_path(value: object) -> str:
    if type(value) is not str or not value or "\\" in value or "\x00" in value:
        raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise _error(GitSessionHistoryFailureCode.RECORD_INVALID) from None
    parsed = PurePosixPath(value)
    if (
        len(encoded) > 4096
        or parsed.is_absolute()
        or parsed.as_posix() != value
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise _error(GitSessionHistoryFailureCode.RECORD_INVALID)
    return value


def _reconstruct_candidate_path(value: object) -> GitSessionCandidatePath:
    try:
        if type(value) is not GitSessionCandidatePath:
            raise TypeError
        return GitSessionCandidatePath(
            value.path, value.operation, value.before_sha256, value.after_sha256
        )
    except (AttributeError, TypeError, GitSessionHistoryError):
        raise _error(GitSessionHistoryFailureCode.RECORD_INVALID) from None


def _reconstruct_candidate_record(value: object) -> GitSessionCandidateRecord:
    try:
        if type(value) is not GitSessionCandidateRecord:
            raise TypeError
        return replace(
            value,
            paths=tuple(_reconstruct_candidate_path(item) for item in value.paths),
        )
    except (AttributeError, TypeError, GitSessionHistoryError):
        raise _error(GitSessionHistoryFailureCode.RECORD_INVALID) from None


def _reconstruct_commit_record(value: object) -> GitSessionCommitOperationRecord:
    try:
        if type(value) is not GitSessionCommitOperationRecord:
            raise TypeError
        return replace(value)
    except (AttributeError, TypeError, GitSessionHistoryError):
        raise _error(GitSessionHistoryFailureCode.RECORD_INVALID) from None


def _reconstruct_checkpoint_record(
    value: object,
) -> GitSessionCheckpointOperationRecord:
    try:
        if type(value) is not GitSessionCheckpointOperationRecord:
            raise TypeError
        return replace(value)
    except (AttributeError, TypeError, GitSessionHistoryError):
        raise _error(GitSessionHistoryFailureCode.RECORD_INVALID) from None


def _error(code: GitSessionHistoryFailureCode) -> GitSessionHistoryError:
    action = {
        GitSessionHistoryFailureCode.OUTCOME_UNKNOWN: GitSessionHistoryRecoveryAction.INSPECT,
        GitSessionHistoryFailureCode.RECOVERY_REQUIRED: GitSessionHistoryRecoveryAction.OPERATOR_RECOVERY,
        GitSessionHistoryFailureCode.RECORD_NOT_FOUND: GitSessionHistoryRecoveryAction.REPREPARE,
    }.get(code, GitSessionHistoryRecoveryAction.NONE)
    error = GitSessionHistoryError(code, action)
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True
    return error


__all__ = [
    "GIT_SESSION_HISTORY_DIRECTORY",
    "GIT_SESSION_HISTORY_MAX_PATHS",
    "GIT_SESSION_HISTORY_MAX_RECORD_BYTES",
    "GIT_SESSION_HISTORY_MAX_TEXT_BYTES",
    "GIT_SESSION_HISTORY_SCHEMA_VERSION",
    "GitSessionCandidatePath",
    "GitSessionCandidatePathOperation",
    "GitSessionCandidateRecord",
    "GitSessionCheckpointOperationRecord",
    "GitSessionCommitOperationRecord",
    "GitSessionHistoryAction",
    "GitSessionHistoryError",
    "GitSessionHistoryFailureCode",
    "GitSessionHistoryInspection",
    "GitSessionHistoryRecoveryAction",
    "GitSessionHistoryState",
    "GitSessionIdentitySource",
    "advance_checkpoint_operation",
    "advance_commit_operation",
    "build_candidate_record",
    "decode_candidate_record",
    "decode_checkpoint_operation",
    "decode_commit_operation",
    "encode_candidate_record",
    "encode_checkpoint_operation",
    "encode_commit_operation",
]
