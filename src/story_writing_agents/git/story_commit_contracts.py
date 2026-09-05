"""Pure contracts for exact user-confirmed story commits."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import StrEnum

from ..writing_artifacts import (
    ArtifactChangeSet,
    canonical_artifact_kind,
    deserialize_artifact_change_set,
    serialize_artifact_change_set,
)


STORY_COMMIT_MAX_IMPACT_ITEMS = 64
STORY_COMMIT_MAX_MESSAGE_BYTES = 4096

_DIGEST = re.compile(r"[0-9a-f]{64}")
_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_OPERATION_ID = re.compile(r"[0-9a-f]{32}")
_OPAQUE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


class StoryCommitDecision(StrEnum):
    """Closed user decisions for one prepared candidate."""

    COMMIT = "commit"
    REJECT = "reject"
    REVISE = "revise"


class StoryCommitState(StrEnum):
    """Externally meaningful states of one story-commit attempt."""

    PREPARED = "prepared"
    RUNNING = "running"
    STAGED = "staged"
    COMMITTED = "committed"
    REJECTED = "rejected"
    OUTCOME_UNKNOWN = "outcome_unknown"
    RECOVERY_REQUIRED = "recovery_required"


class StoryCommitPathOperation(StrEnum):
    """Normalized path effects retained by a candidate."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class StoryCommitIdentitySource(StrEnum):
    """Content-free origin of one effective Git identity."""

    ENVIRONMENT = "environment"
    REPOSITORY = "repository"
    GLOBAL = "global"
    SYSTEM = "system"


class StoryCommitFailureCode(StrEnum):
    """Stable content-free story-commit failures."""

    INVALID_REQUEST = "invalid_request"
    APPLICATION_MISMATCH = "application_mismatch"
    CANDIDATE_NOT_FOUND = "candidate_not_found"
    CANDIDATE_DRIFT = "candidate_drift"
    PRESTAGED_CHANGES = "prestaged_changes"
    AUTHORITY_INVALID = "authority_invalid"
    AUTHORITY_CONSUMED = "authority_consumed"
    GIT_FAILED = "git_failed"
    HISTORY_FAILED = "history_failed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    RECOVERY_REQUIRED = "recovery_required"


class StoryCommitRecoveryAction(StrEnum):
    """One safe next action for a result or failure."""

    NONE = "none"
    REPREPARE = "reprepare"
    INSPECT = "inspect"
    OPERATOR_RECOVERY = "operator_recovery"


_DIAGNOSTICS = {
    StoryCommitFailureCode.INVALID_REQUEST: "Story commit request is invalid.",
    StoryCommitFailureCode.APPLICATION_MISMATCH: "Applied artifacts do not match the application evidence.",
    StoryCommitFailureCode.CANDIDATE_NOT_FOUND: "Story commit candidate does not exist.",
    StoryCommitFailureCode.CANDIDATE_DRIFT: "Story commit candidate no longer matches the session.",
    StoryCommitFailureCode.PRESTAGED_CHANGES: "The session Git index contains staged changes.",
    StoryCommitFailureCode.AUTHORITY_INVALID: "Story commit authority is invalid.",
    StoryCommitFailureCode.AUTHORITY_CONSUMED: "Story commit authority was already consumed.",
    StoryCommitFailureCode.GIT_FAILED: "Exact story commit Git operation failed.",
    StoryCommitFailureCode.HISTORY_FAILED: "Story commit history operation failed.",
    StoryCommitFailureCode.OUTCOME_UNKNOWN: "Story commit outcome is unknown.",
    StoryCommitFailureCode.RECOVERY_REQUIRED: "Story commit requires operator recovery.",
}


class StoryCommitError(RuntimeError):
    """One bounded failure containing no story, path, or Git output."""

    __slots__ = ("code", "recovery_action")

    def __init__(
        self,
        code: StoryCommitFailureCode,
        recovery_action: StoryCommitRecoveryAction | None = None,
    ) -> None:
        if type(code) is not StoryCommitFailureCode:
            raise TypeError("story commit failure code is invalid")
        if recovery_action is None:
            recovery_action = {
                StoryCommitFailureCode.CANDIDATE_NOT_FOUND: StoryCommitRecoveryAction.REPREPARE,
                StoryCommitFailureCode.CANDIDATE_DRIFT: StoryCommitRecoveryAction.REPREPARE,
                StoryCommitFailureCode.APPLICATION_MISMATCH: StoryCommitRecoveryAction.REPREPARE,
                StoryCommitFailureCode.OUTCOME_UNKNOWN: StoryCommitRecoveryAction.INSPECT,
                StoryCommitFailureCode.RECOVERY_REQUIRED: StoryCommitRecoveryAction.OPERATOR_RECOVERY,
            }.get(code, StoryCommitRecoveryAction.NONE)
        if type(recovery_action) is not StoryCommitRecoveryAction:
            raise TypeError("story commit recovery action is invalid")
        self.code = code
        self.recovery_action = recovery_action
        super().__init__(_DIAGNOSTICS[code])


@dataclass(frozen=True, slots=True)
class StoryCommitApplicationEvidence:
    """Typed proof that one exact artifact change set was applied successfully."""

    session_id: str
    application_id: str
    proposal_id: str
    correlation_id: str
    change_set: ArtifactChangeSet = field(repr=False)
    applied_state_digest: str

    def __post_init__(self) -> None:
        _require_session(self.session_id)
        for value in (self.application_id, self.proposal_id, self.correlation_id):
            _require_opaque(value)
        change_set = _reconstruct_change_set(self.change_set)
        if (
            _require_digest(self.applied_state_digest)
            != change_set.projected_state_digest
        ):
            raise _error(StoryCommitFailureCode.APPLICATION_MISMATCH)


@dataclass(frozen=True, slots=True)
class StoryCommitPath:
    """One content-free canonical before/after path identity."""

    path: str
    operation: StoryCommitPathOperation
    before_sha256: str | None
    after_sha256: str | None

    def __post_init__(self) -> None:
        _require_path(self.path)
        if type(self.operation) is not StoryCommitPathOperation:
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)
        before = _optional_digest(self.before_sha256)
        after = _optional_digest(self.after_sha256)
        valid = (
            (
                self.operation is StoryCommitPathOperation.CREATE
                and before is None
                and after is not None
            )
            or (
                self.operation is StoryCommitPathOperation.UPDATE
                and before is not None
                and after is not None
                and before != after
            )
            or (
                self.operation is StoryCommitPathOperation.DELETE
                and before is not None
                and after is None
            )
        )
        if not valid:
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)


@dataclass(frozen=True, slots=True)
class StoryCommitCandidate:
    """Immutable content-free identity prepared before any Git mutation."""

    candidate_id: str
    session_id: str
    branch: str
    branch_tip: str
    expected_tree: str
    worktree_identity_sha256: str
    application_id: str
    proposal_id: str
    correlation_id: str
    paths: tuple[StoryCommitPath, ...]
    proposed_message: str = field(repr=False)
    impact_boundary: tuple[str, ...] = field(repr=False)
    author_identity_source: StoryCommitIdentitySource
    committer_identity_source: StoryCommitIdentitySource

    def __post_init__(self) -> None:
        _require_digest(self.candidate_id)
        session = _require_session(self.session_id)
        if self.branch != f"story/session/{session}":
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)
        tip = _require_object_id(self.branch_tip)
        tree = _require_object_id(self.expected_tree)
        if len(tip) != len(tree):
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)
        _require_digest(self.worktree_identity_sha256)
        for value in (self.application_id, self.proposal_id, self.correlation_id):
            _require_opaque(value)
        if (
            type(self.paths) is not tuple
            or not self.paths
            or any(type(item) is not StoryCommitPath for item in self.paths)
            or self.paths
            != tuple(sorted(self.paths, key=lambda item: item.path.encode("utf-8")))
            or len({item.path for item in self.paths}) != len(self.paths)
        ):
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)
        _require_message(self.proposed_message)
        _require_impact(self.impact_boundary)
        if (
            type(self.author_identity_source) is not StoryCommitIdentitySource
            or type(self.committer_identity_source) is not StoryCommitIdentitySource
        ):
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)


@dataclass(frozen=True, slots=True)
class StoryCommitProjection:
    """Lead-facing candidate facts with no story bytes or authority."""

    candidate_id: str
    session_id: str
    branch: str
    branch_tip: str
    expected_tree: str
    application_id: str
    proposal_id: str
    correlation_id: str
    paths: tuple[StoryCommitPath, ...]
    proposed_message: str
    impact_boundary: tuple[str, ...]
    author_identity_source: StoryCommitIdentitySource
    committer_identity_source: StoryCommitIdentitySource

    def __post_init__(self) -> None:
        candidate = StoryCommitCandidate(
            candidate_id=self.candidate_id,
            session_id=self.session_id,
            branch=self.branch,
            branch_tip=self.branch_tip,
            expected_tree=self.expected_tree,
            worktree_identity_sha256="0" * 64,
            application_id=self.application_id,
            proposal_id=self.proposal_id,
            correlation_id=self.correlation_id,
            paths=self.paths,
            proposed_message=self.proposed_message,
            impact_boundary=self.impact_boundary,
            author_identity_source=self.author_identity_source,
            committer_identity_source=self.committer_identity_source,
        )
        del candidate


@dataclass(frozen=True, slots=True)
class StoryCommitConfirmation:
    """One exact structured user decision for a candidate."""

    confirmation_id: str
    candidate_id: str
    proposal_id: str
    correlation_id: str
    decision: StoryCommitDecision

    def __post_init__(self) -> None:
        _require_opaque(self.confirmation_id)
        _require_digest(self.candidate_id)
        _require_opaque(self.proposal_id)
        _require_opaque(self.correlation_id)
        if type(self.decision) is not StoryCommitDecision:
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)


@dataclass(frozen=True, slots=True)
class StoryCommitResult:
    """Content-free result or restart classification for one decision."""

    session_id: str
    candidate_id: str
    operation_id: str
    correlation_id: str
    state: StoryCommitState
    recovery_action: StoryCommitRecoveryAction
    branch: str
    branch_tip: str
    commit_id: str | None = None

    def __post_init__(self) -> None:
        session = _require_session(self.session_id)
        _require_digest(self.candidate_id)
        _require_operation_id(self.operation_id)
        _require_opaque(self.correlation_id)
        if (
            type(self.state) is not StoryCommitState
            or type(self.recovery_action) is not StoryCommitRecoveryAction
            or self.branch != f"story/session/{session}"
        ):
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)
        tip = _require_object_id(self.branch_tip)
        commit = _optional_object_id(self.commit_id)
        if commit is not None and len(commit) != len(tip):
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)
        if (self.state is StoryCommitState.COMMITTED) != (commit is not None):
            raise _error(StoryCommitFailureCode.INVALID_REQUEST)


def project_story_commit_candidate(
    candidate: StoryCommitCandidate,
) -> StoryCommitProjection:
    """Return a defensively reconstructed lead-facing projection."""

    value = _reconstruct_candidate(candidate)
    return StoryCommitProjection(
        candidate_id=value.candidate_id,
        session_id=value.session_id,
        branch=value.branch,
        branch_tip=value.branch_tip,
        expected_tree=value.expected_tree,
        application_id=value.application_id,
        proposal_id=value.proposal_id,
        correlation_id=value.correlation_id,
        paths=value.paths,
        proposed_message=value.proposed_message,
        impact_boundary=value.impact_boundary,
        author_identity_source=value.author_identity_source,
        committer_identity_source=value.committer_identity_source,
    )


def _reconstruct_candidate(value: object) -> StoryCommitCandidate:
    try:
        if type(value) is not StoryCommitCandidate:
            raise TypeError
        return replace(
            value,
            paths=tuple(replace(item) for item in value.paths),
            impact_boundary=tuple(value.impact_boundary),
        )
    except (AttributeError, TypeError, RuntimeError):
        raise _error(StoryCommitFailureCode.INVALID_REQUEST) from None


def _reconstruct_change_set(value: object) -> ArtifactChangeSet:
    try:
        if type(value) is not ArtifactChangeSet:
            raise TypeError
        return deserialize_artifact_change_set(serialize_artifact_change_set(value))
    except Exception:
        raise _error(StoryCommitFailureCode.INVALID_REQUEST) from None


def _require_session(value: object) -> str:
    if type(value) is not str or _SESSION_ID.fullmatch(value) is None:
        raise _error(StoryCommitFailureCode.INVALID_REQUEST)
    return value


def _require_opaque(value: object) -> str:
    if type(value) is not str or _OPAQUE_ID.fullmatch(value) is None:
        raise _error(StoryCommitFailureCode.INVALID_REQUEST)
    return value


def _require_digest(value: object) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise _error(StoryCommitFailureCode.INVALID_REQUEST)
    return value


def _optional_digest(value: object | None) -> str | None:
    if value is not None:
        return _require_digest(value)
    return None


def _require_object_id(value: object) -> str:
    if type(value) is not str or _OBJECT_ID.fullmatch(value) is None:
        raise _error(StoryCommitFailureCode.INVALID_REQUEST)
    return value


def _optional_object_id(value: object | None) -> str | None:
    if value is not None:
        return _require_object_id(value)
    return None


def _require_operation_id(value: object) -> str:
    if type(value) is not str or _OPERATION_ID.fullmatch(value) is None:
        raise _error(StoryCommitFailureCode.INVALID_REQUEST)
    return value


def _require_path(value: object) -> str:
    if type(value) is not str:
        raise _error(StoryCommitFailureCode.INVALID_REQUEST)
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        encoded = b""
    if (
        not encoded
        or len(encoded) > 1024
        or value.startswith("/")
        or value.endswith("/")
        or "\\" in value
        or "\x00" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise _error(StoryCommitFailureCode.INVALID_REQUEST)
    try:
        canonical = canonical_artifact_kind(value)
    except (TypeError, ValueError):
        canonical = None
    if canonical is None:
        raise _error(StoryCommitFailureCode.INVALID_REQUEST)
    return value


def _require_message(value: object) -> str:
    if type(value) is not str:
        raise _error(StoryCommitFailureCode.INVALID_REQUEST)
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        encoded = b""
    if (
        not encoded
        or len(encoded) > STORY_COMMIT_MAX_MESSAGE_BYTES
        or "\x00" in value
        or "\r" in value
        or "\n" in value
    ):
        raise _error(StoryCommitFailureCode.INVALID_REQUEST)
    return value


def _require_impact(value: object) -> tuple[str, ...]:
    if (
        type(value) is not tuple
        or not value
        or len(value) > STORY_COMMIT_MAX_IMPACT_ITEMS
        or any(type(item) is not str for item in value)
    ):
        raise _error(StoryCommitFailureCode.INVALID_REQUEST)
    try:
        valid = all(
            item
            and len(item.encode("utf-8", errors="strict")) <= 4096
            and "\x00" not in item
            and "\r" not in item
            and "\n" not in item
            for item in value
        )
    except UnicodeEncodeError:
        valid = False
    if (
        not valid
        or len(set(value)) != len(value)
        or value != tuple(sorted(value, key=lambda item: item.encode("utf-8")))
    ):
        raise _error(StoryCommitFailureCode.INVALID_REQUEST)
    return value


def _error(code: StoryCommitFailureCode) -> StoryCommitError:
    error = StoryCommitError(code)
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True
    return error


__all__ = [
    "STORY_COMMIT_MAX_IMPACT_ITEMS",
    "STORY_COMMIT_MAX_MESSAGE_BYTES",
    "StoryCommitApplicationEvidence",
    "StoryCommitCandidate",
    "StoryCommitConfirmation",
    "StoryCommitDecision",
    "StoryCommitError",
    "StoryCommitFailureCode",
    "StoryCommitIdentitySource",
    "StoryCommitPath",
    "StoryCommitPathOperation",
    "StoryCommitProjection",
    "StoryCommitRecoveryAction",
    "StoryCommitResult",
    "StoryCommitState",
    "project_story_commit_candidate",
]
