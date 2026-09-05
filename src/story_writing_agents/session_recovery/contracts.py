"""Pure correlated evidence and recovery classification for one story session."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields, is_dataclass
from enum import StrEnum
from typing import NoReturn

from ..conversations import (
    CollaborationConfirmationDecision,
    CollaborationHistoryProjection,
    CollaborationStepState,
)
from ..git import (
    GitChangeKind,
    GitCommitMetadata,
    GitExternalOperationInspection,
    GitExternalOperationKind,
    GitStatus,
)
from ..git.session_contracts import (
    GitSessionResourceInspection,
    GitSessionResourceState,
)
from ..git.session_history_contracts import (
    GitSessionCandidateRecord,
    GitSessionCheckpointOperationRecord,
    GitSessionCommitOperationRecord,
    GitSessionHistoryInspection,
    GitSessionHistoryState,
)
from ..session_lifecycle import (
    SessionAccessState,
    SessionDurableState,
    SessionLifecycleState,
)
from ..writing_artifacts import (
    ArtifactApplicationResult,
    ArtifactApplicationState,
    ArtifactChangeSet,
    ArtifactRelationshipValidation,
)


_SESSION_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_OPAQUE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_TAGGED_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class SessionRecoveryState(StrEnum):
    """Closed user-visible state of one active correlated story workflow."""

    READY = "ready"
    PROPOSED = "proposed"
    REVIEWED = "reviewed"
    AWAITING_APPLICATION = "awaiting_application"
    APPLIED_NOT_COMMITTED = "applied_not_committed"
    STAGED = "staged"
    COMMITTED = "committed"
    REJECTED = "rejected"
    OUTCOME_UNKNOWN = "outcome_unknown"
    RECOVERY_REQUIRED = "recovery_required"


class SessionRecoveryNextAction(StrEnum):
    """Content-safe instruction derived from durable facts, never authority."""

    CONTINUE_WITH_LEAD = "continue_with_lead"
    REQUEST_APPLICATION_CONFIRMATION = "request_application_confirmation"
    SUBMIT_APPLICATION_DECISION = "submit_application_decision"
    REQUEST_COMMIT_CONFIRMATION = "request_commit_confirmation"
    INSPECT_STORY_COMMIT = "inspect_story_commit"
    INSPECT_OWNER_OUTCOME = "inspect_owner_outcome"
    OPERATOR_RECOVERY = "operator_recovery"


class SessionRecoveryOwner(StrEnum):
    """Authoritative owner responsible for the classified state or failure."""

    LEAD = "lead"
    SESSION_LIFECYCLE = "session_lifecycle"
    COLLABORATION_HISTORY = "collaboration_history"
    SESSION_RESOURCES = "session_resources"
    ARTIFACT_APPLICATION = "artifact_application"
    STORY_COMMIT = "story_commit"
    CHECKPOINT = "checkpoint"
    EXTERNAL_GIT = "external_git"
    REPOSITORY_STATUS = "repository_status"


class SessionRecoveryFailureCode(StrEnum):
    """Closed content-free construction and recovery classifications."""

    NONE = "none"
    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED_SHAPE = "unsupported_shape"
    CORRELATION_MISMATCH = "correlation_mismatch"
    MISSING_PREDECESSOR = "missing_predecessor"
    OWNER_OUTCOME_UNKNOWN = "owner_outcome_unknown"
    OWNER_RECOVERY_REQUIRED = "owner_recovery_required"
    EXTERNAL_GIT_OPERATION = "external_git_operation"
    REPOSITORY_STATE_MISMATCH = "repository_state_mismatch"


_ERROR_MESSAGES = {
    SessionRecoveryFailureCode.INVALID_REQUEST: (
        "Session recovery request is invalid."
    ),
    SessionRecoveryFailureCode.UNSUPPORTED_SHAPE: (
        "Session recovery evidence shape is unsupported."
    ),
}


class SessionRecoveryError(RuntimeError):
    """A fixed, content-safe malformed-input or unsupported-shape failure."""

    __slots__ = ("code",)

    def __init__(self, code: SessionRecoveryFailureCode) -> None:
        if type(code) is not SessionRecoveryFailureCode or code not in _ERROR_MESSAGES:
            raise TypeError("session recovery error code is invalid")
        self.code = code
        super().__init__(_ERROR_MESSAGES[code])


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionReviewedProposalEvidence:
    """Session-bound reviewed artifact proposal and optional decision request."""

    session_id: str
    conversation_id: str
    collaboration_tail_sequence: int
    proposal_id: str
    proposal_revision: int
    proposal_digest: str
    candidate_id: str
    candidate_revision: int
    candidate_digest: str
    change_set: ArtifactChangeSet = field(repr=False)
    relationship_validation: ArtifactRelationshipValidation = field(repr=False)
    relationship_validation_digest: str
    correlation_id: str
    application_confirmation_request_id: str | None
    confirmation_choices: tuple[CollaborationConfirmationDecision, ...]

    def __post_init__(self) -> None:
        _session_identifier(self.session_id)
        _session_identifier(self.conversation_id)
        _sequence(self.collaboration_tail_sequence)
        _session_identifier(self.proposal_id)
        _positive_integer(self.proposal_revision)
        _tagged_digest(self.proposal_digest)
        _opaque_identifier(self.candidate_id)
        _positive_integer(self.candidate_revision)
        _tagged_digest(self.candidate_digest)
        _owner_value(self.change_set, ArtifactChangeSet)
        _owner_value(self.relationship_validation, ArtifactRelationshipValidation)
        _tagged_digest(self.relationship_validation_digest)
        _opaque_identifier(self.correlation_id)
        _optional_opaque_identifier(self.application_confirmation_request_id)
        _confirmation_choices(self.confirmation_choices)
        if (self.application_confirmation_request_id is None) is not (
            not self.confirmation_choices
        ):
            _raise_error(SessionRecoveryFailureCode.INVALID_REQUEST)


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionArtifactApplicationEvidence:
    """Session binding for one immutable workspace artifact-application result."""

    session_id: str
    conversation_id: str
    collaboration_tail_sequence: int
    proposal_id: str
    correlation_id: str
    application_result: ArtifactApplicationResult = field(repr=False)

    def __post_init__(self) -> None:
        _session_identifier(self.session_id)
        _session_identifier(self.conversation_id)
        _sequence(self.collaboration_tail_sequence)
        _session_identifier(self.proposal_id)
        _opaque_identifier(self.correlation_id)
        _owner_value(self.application_result, ArtifactApplicationResult)


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionRecoveryEvidence:
    """Complete owner-supplied facts used by the pure recovery classifier."""

    lifecycle_state: SessionLifecycleState = field(repr=False)
    collaboration_history: CollaborationHistoryProjection = field(repr=False)
    session_resources: GitSessionResourceInspection = field(repr=False)
    external_operation: GitExternalOperationInspection = field(repr=False)
    reviewed_proposal: SessionReviewedProposalEvidence | None = field(
        default=None,
        repr=False,
    )
    artifact_application: SessionArtifactApplicationEvidence | None = field(
        default=None,
        repr=False,
    )
    session_history: GitSessionHistoryInspection = field(repr=False)
    story_commit_candidate: GitSessionCandidateRecord | None = field(
        default=None,
        repr=False,
    )
    story_commit_operation: GitSessionCommitOperationRecord | None = field(
        default=None,
        repr=False,
    )
    checkpoint_operation: GitSessionCheckpointOperationRecord | None = field(
        default=None,
        repr=False,
    )
    repository_status: GitStatus = field(repr=False)
    branch_commit_metadata: GitCommitMetadata | None = field(default=None, repr=False)
    worktree_identity_digest: str

    def __post_init__(self) -> None:
        _owner_value(self.lifecycle_state, SessionLifecycleState)
        _owner_value(self.collaboration_history, CollaborationHistoryProjection)
        _owner_value(self.session_resources, GitSessionResourceInspection)
        _owner_value(self.external_operation, GitExternalOperationInspection)
        _optional_owner_value(
            self.reviewed_proposal,
            SessionReviewedProposalEvidence,
        )
        _optional_owner_value(
            self.artifact_application,
            SessionArtifactApplicationEvidence,
        )
        _owner_value(self.session_history, GitSessionHistoryInspection)
        _optional_owner_value(
            self.story_commit_candidate,
            GitSessionCandidateRecord,
        )
        _optional_owner_value(
            self.story_commit_operation,
            GitSessionCommitOperationRecord,
        )
        _optional_owner_value(
            self.checkpoint_operation,
            GitSessionCheckpointOperationRecord,
        )
        _owner_value(self.repository_status, GitStatus)
        _optional_owner_value(self.branch_commit_metadata, GitCommitMetadata)
        _digest(self.worktree_identity_digest)


_NEXT_ACTION = {
    SessionRecoveryState.READY: SessionRecoveryNextAction.CONTINUE_WITH_LEAD,
    SessionRecoveryState.PROPOSED: SessionRecoveryNextAction.CONTINUE_WITH_LEAD,
    SessionRecoveryState.REVIEWED: (
        SessionRecoveryNextAction.REQUEST_APPLICATION_CONFIRMATION
    ),
    SessionRecoveryState.AWAITING_APPLICATION: (
        SessionRecoveryNextAction.SUBMIT_APPLICATION_DECISION
    ),
    SessionRecoveryState.APPLIED_NOT_COMMITTED: (
        SessionRecoveryNextAction.REQUEST_COMMIT_CONFIRMATION
    ),
    SessionRecoveryState.STAGED: SessionRecoveryNextAction.INSPECT_STORY_COMMIT,
    SessionRecoveryState.COMMITTED: SessionRecoveryNextAction.CONTINUE_WITH_LEAD,
    SessionRecoveryState.REJECTED: SessionRecoveryNextAction.CONTINUE_WITH_LEAD,
    SessionRecoveryState.OUTCOME_UNKNOWN: (
        SessionRecoveryNextAction.INSPECT_OWNER_OUTCOME
    ),
    SessionRecoveryState.RECOVERY_REQUIRED: (
        SessionRecoveryNextAction.OPERATOR_RECOVERY
    ),
}


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionRecoveryResult:
    """Content-free visible recovery state and its one safe next action."""

    session_id: str
    conversation_id: str
    collaboration_tail_sequence: int
    state: SessionRecoveryState
    next_action: SessionRecoveryNextAction
    owner: SessionRecoveryOwner
    failure_code: SessionRecoveryFailureCode
    proposal_id: str | None = None
    confirmation_request_id: str | None = None
    application_id: str | None = None
    reviewed_candidate_id: str | None = None
    story_commit_candidate_id: str | None = None
    story_commit_operation_id: str | None = None
    commit_id: str | None = None
    checkpoint_operation_id: str | None = None
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        _session_identifier(self.session_id)
        _session_identifier(self.conversation_id)
        _sequence(self.collaboration_tail_sequence)
        if (
            type(self.state) is not SessionRecoveryState
            or type(self.next_action) is not SessionRecoveryNextAction
            or type(self.owner) is not SessionRecoveryOwner
            or type(self.failure_code) is not SessionRecoveryFailureCode
        ):
            _raise_error(SessionRecoveryFailureCode.UNSUPPORTED_SHAPE)
        if self.next_action is not _NEXT_ACTION[self.state]:
            _raise_error(SessionRecoveryFailureCode.INVALID_REQUEST)
        if self.state is SessionRecoveryState.RECOVERY_REQUIRED:
            if self.failure_code in {
                SessionRecoveryFailureCode.NONE,
                SessionRecoveryFailureCode.OWNER_OUTCOME_UNKNOWN,
            }:
                _raise_error(SessionRecoveryFailureCode.INVALID_REQUEST)
        elif self.state is SessionRecoveryState.OUTCOME_UNKNOWN:
            if (
                self.failure_code
                is not SessionRecoveryFailureCode.OWNER_OUTCOME_UNKNOWN
            ):
                _raise_error(SessionRecoveryFailureCode.INVALID_REQUEST)
        elif self.failure_code is not SessionRecoveryFailureCode.NONE:
            _raise_error(SessionRecoveryFailureCode.INVALID_REQUEST)
        for value in (
            self.proposal_id,
            self.confirmation_request_id,
            self.application_id,
            self.reviewed_candidate_id,
            self.story_commit_candidate_id,
            self.story_commit_operation_id,
            self.commit_id,
            self.checkpoint_operation_id,
            self.correlation_id,
        ):
            _optional_opaque_identifier(value)
        if self.state in {
            SessionRecoveryState.PROPOSED,
            SessionRecoveryState.REVIEWED,
            SessionRecoveryState.AWAITING_APPLICATION,
            SessionRecoveryState.APPLIED_NOT_COMMITTED,
            SessionRecoveryState.STAGED,
            SessionRecoveryState.COMMITTED,
            SessionRecoveryState.REJECTED,
        } and self.proposal_id is None:
            _raise_error(SessionRecoveryFailureCode.INVALID_REQUEST)
        if (
            self.state is SessionRecoveryState.AWAITING_APPLICATION
            and self.confirmation_request_id is None
        ):
            _raise_error(SessionRecoveryFailureCode.INVALID_REQUEST)
        if self.state in {
            SessionRecoveryState.APPLIED_NOT_COMMITTED,
            SessionRecoveryState.STAGED,
            SessionRecoveryState.COMMITTED,
        } and self.application_id is None:
            _raise_error(SessionRecoveryFailureCode.INVALID_REQUEST)
        if self.state in {
            SessionRecoveryState.STAGED,
            SessionRecoveryState.COMMITTED,
        } and (
            self.story_commit_candidate_id is None
            or self.story_commit_operation_id is None
        ):
            _raise_error(SessionRecoveryFailureCode.INVALID_REQUEST)
        if self.state is SessionRecoveryState.COMMITTED and self.commit_id is None:
            _raise_error(SessionRecoveryFailureCode.INVALID_REQUEST)


def reconcile_session_recovery(
    evidence: SessionRecoveryEvidence,
) -> SessionRecoveryResult:
    """Classify exact owner evidence without reading, replaying, or mutating."""

    _owner_value(evidence, SessionRecoveryEvidence)

    history = evidence.collaboration_history
    reviewed = evidence.reviewed_proposal
    application = evidence.artifact_application
    commit = evidence.story_commit_operation

    if _is_fresh_idle(evidence):
        _raise_error(SessionRecoveryFailureCode.UNSUPPORTED_SHAPE)

    recovery = _recovery_failure(evidence)
    if recovery is not None:
        failure, owner = recovery
        return _result(evidence, SessionRecoveryState.RECOVERY_REQUIRED, owner, failure)

    unresolved_owner = _unresolved_owner(evidence)
    if unresolved_owner is not None:
        return _result(
            evidence,
            SessionRecoveryState.OUTCOME_UNKNOWN,
            unresolved_owner,
            SessionRecoveryFailureCode.OWNER_OUTCOME_UNKNOWN,
        )

    if commit is not None and commit.state is GitSessionHistoryState.COMMITTED:
        return _result(
            evidence,
            SessionRecoveryState.COMMITTED,
            SessionRecoveryOwner.STORY_COMMIT,
            SessionRecoveryFailureCode.NONE,
        )
    if commit is not None and commit.state is GitSessionHistoryState.STAGED:
        return _result(
            evidence,
            SessionRecoveryState.STAGED,
            SessionRecoveryOwner.STORY_COMMIT,
            SessionRecoveryFailureCode.NONE,
        )
    if commit is not None and commit.state is GitSessionHistoryState.REJECTED:
        return _result(
            evidence,
            SessionRecoveryState.REJECTED,
            SessionRecoveryOwner.STORY_COMMIT,
            SessionRecoveryFailureCode.NONE,
        )
    if application is not None:
        return _result(
            evidence,
            SessionRecoveryState.APPLIED_NOT_COMMITTED,
            SessionRecoveryOwner.ARTIFACT_APPLICATION,
            SessionRecoveryFailureCode.NONE,
        )
    if reviewed is not None:
        state = (
            SessionRecoveryState.AWAITING_APPLICATION
            if reviewed.application_confirmation_request_id is not None
            else SessionRecoveryState.REVIEWED
        )
        return _result(
            evidence,
            state,
            SessionRecoveryOwner.LEAD,
            SessionRecoveryFailureCode.NONE,
        )
    if history.current_state is CollaborationStepState.REJECTED:
        return _result(
            evidence,
            SessionRecoveryState.REJECTED,
            SessionRecoveryOwner.COLLABORATION_HISTORY,
            SessionRecoveryFailureCode.NONE,
        )
    return _result(
        evidence,
        SessionRecoveryState.PROPOSED,
        SessionRecoveryOwner.LEAD,
        SessionRecoveryFailureCode.NONE,
    )


def _recovery_failure(
    evidence: SessionRecoveryEvidence,
) -> tuple[SessionRecoveryFailureCode, SessionRecoveryOwner] | None:
    history = evidence.collaboration_history
    resources = evidence.session_resources
    reviewed = evidence.reviewed_proposal
    application = evidence.artifact_application
    candidate = evidence.story_commit_candidate
    commit = evidence.story_commit_operation
    checkpoint = evidence.checkpoint_operation
    metadata = evidence.branch_commit_metadata

    if resources.state is GitSessionResourceState.RECOVERY_REQUIRED:
        return (
            SessionRecoveryFailureCode.OWNER_RECOVERY_REQUIRED,
            SessionRecoveryOwner.SESSION_RESOURCES,
        )
    if history.current_state is CollaborationStepState.RECOVERY_REQUIRED:
        return (
            SessionRecoveryFailureCode.OWNER_RECOVERY_REQUIRED,
            SessionRecoveryOwner.COLLABORATION_HISTORY,
        )
    for operation, owner in (
        (commit, SessionRecoveryOwner.STORY_COMMIT),
        (checkpoint, SessionRecoveryOwner.CHECKPOINT),
    ):
        if (
            operation is not None
            and operation.state is GitSessionHistoryState.RECOVERY_REQUIRED
        ):
            return SessionRecoveryFailureCode.OWNER_RECOVERY_REQUIRED, owner
    for operations, owner in (
        (
            evidence.session_history.commit_operations,
            SessionRecoveryOwner.STORY_COMMIT,
        ),
        (
            evidence.session_history.checkpoint_operations,
            SessionRecoveryOwner.CHECKPOINT,
        ),
    ):
        if any(
            state is GitSessionHistoryState.RECOVERY_REQUIRED
            for _, state in operations
        ):
            return SessionRecoveryFailureCode.OWNER_RECOVERY_REQUIRED, owner
    if evidence.external_operation.kind is not GitExternalOperationKind.NONE:
        return (
            SessionRecoveryFailureCode.EXTERNAL_GIT_OPERATION,
            SessionRecoveryOwner.EXTERNAL_GIT,
        )

    missing_owner = _missing_predecessor_owner(evidence)
    if missing_owner is not None:
        return SessionRecoveryFailureCode.MISSING_PREDECESSOR, missing_owner

    session_id = evidence.lifecycle_state.session_id
    conversation_id = history.conversation_id
    if (
        history.session_id != session_id
        or conversation_id is None
        or resources.identity.session_id != session_id
        or evidence.session_history.session_id != session_id
        or (reviewed is not None and reviewed.session_id != session_id)
        or (application is not None and application.session_id != session_id)
        or (candidate is not None and candidate.session_id != session_id)
        or (commit is not None and commit.session_id != session_id)
        or (checkpoint is not None and checkpoint.session_id != session_id)
        or (
            reviewed is not None
            and reviewed.conversation_id != conversation_id
        )
        or (
            application is not None
            and application.conversation_id != conversation_id
        )
        or evidence.external_operation.worktree_identity_digest
        != evidence.worktree_identity_digest
    ):
        return (
            SessionRecoveryFailureCode.CORRELATION_MISMATCH,
            SessionRecoveryOwner.SESSION_LIFECYCLE,
        )

    proposal = history.current_proposal
    if proposal is None or history.current_state is None or history.last_sequence < 1:
        return (
            SessionRecoveryFailureCode.MISSING_PREDECESSOR,
            SessionRecoveryOwner.COLLABORATION_HISTORY,
        )
    if (
        history.current_step_id != proposal.step_id
        or history.current_turn_id is None
        or (
            history.confirmation_request_id is None
            and history.confirmation_decision is not None
        )
    ):
        return (
            SessionRecoveryFailureCode.CORRELATION_MISMATCH,
            SessionRecoveryOwner.COLLABORATION_HISTORY,
        )
    if reviewed is not None and (
        reviewed.collaboration_tail_sequence != history.last_sequence
        or reviewed.proposal_id != proposal.proposal_id
        or reviewed.proposal_revision != proposal.proposal_revision
        or reviewed.proposal_digest != proposal.proposal_digest
        or (
            reviewed.application_confirmation_request_id is not None
            and reviewed.application_confirmation_request_id
            != history.confirmation_request_id
        )
    ):
        return (
            SessionRecoveryFailureCode.CORRELATION_MISMATCH,
            SessionRecoveryOwner.COLLABORATION_HISTORY,
        )

    if reviewed is not None and (
        reviewed.change_set.base_state_digest
        != reviewed.relationship_validation.base_state_digest
        or reviewed.change_set.projected_state_digest
        != reviewed.relationship_validation.projected_state_digest
        or reviewed.change_set.change_set_digest
        != reviewed.relationship_validation.change_set_digest
    ):
        return (
            SessionRecoveryFailureCode.CORRELATION_MISMATCH,
            SessionRecoveryOwner.ARTIFACT_APPLICATION,
        )

    if application is not None:
        assert reviewed is not None
        result = application.application_result
        if (
            application.collaboration_tail_sequence != history.last_sequence
            or application.proposal_id != reviewed.proposal_id
            or application.correlation_id != reviewed.correlation_id
            or result.candidate_id != reviewed.candidate_id
            or result.candidate_revision != reviewed.candidate_revision
            or result.candidate_digest != reviewed.candidate_digest
            or result.change_set_digest != reviewed.change_set.change_set_digest
            or result.relationship_validation_digest
            != reviewed.relationship_validation_digest
        ):
            return (
                SessionRecoveryFailureCode.CORRELATION_MISMATCH,
                SessionRecoveryOwner.ARTIFACT_APPLICATION,
            )

    correlation_ids = tuple(
        value
        for value in (
            reviewed.correlation_id if reviewed is not None else None,
            application.correlation_id if application is not None else None,
            candidate.correlation_id if candidate is not None else None,
            commit.correlation_id if commit is not None else None,
            checkpoint.correlation_id if checkpoint is not None else None,
        )
        if value is not None
    )
    if len(set(correlation_ids)) > 1:
        return (
            SessionRecoveryFailureCode.CORRELATION_MISMATCH,
            SessionRecoveryOwner.STORY_COMMIT,
        )

    if candidate is not None:
        assert application is not None
        if (
            candidate.application_id != application.application_result.application_id
            or candidate.proposal_id != application.proposal_id
            or candidate.branch != resources.identity.branch
            or (
                (commit is None or commit.state is not GitSessionHistoryState.COMMITTED)
                and candidate.branch_tip != resources.current_branch_tip
            )
            or candidate.worktree_identity_sha256 != evidence.worktree_identity_digest
            or candidate.candidate_id not in evidence.session_history.candidate_ids
            or _candidate_paths(candidate)
            != set(application.application_result.affected_paths)
        ):
            return (
                SessionRecoveryFailureCode.CORRELATION_MISMATCH,
                SessionRecoveryOwner.STORY_COMMIT,
            )

    if commit is not None:
        assert candidate is not None
        matching_commit_operations = tuple(
            item
            for item in evidence.session_history.commit_operations
            if item[0] == commit.operation_id
        )
        if (
            commit.candidate_id != candidate.candidate_id
            or commit.expected_branch_tip != candidate.branch_tip
            or matching_commit_operations != ((commit.operation_id, commit.state),)
        ):
            return (
                SessionRecoveryFailureCode.CORRELATION_MISMATCH,
                SessionRecoveryOwner.STORY_COMMIT,
            )

    if checkpoint is not None:
        matching_checkpoint_operations = tuple(
            item
            for item in evidence.session_history.checkpoint_operations
            if item[0] == checkpoint.operation_id
        )
        if (
            matching_checkpoint_operations
            != ((checkpoint.operation_id, checkpoint.state),)
            or (
                checkpoint.state is GitSessionHistoryState.COMMITTED
                and checkpoint.checkpoint_id != resources.current_internal_tip
            )
            or (
                checkpoint.alternate_index_preserved
                != (
                    checkpoint.operation_id
                    in evidence.session_history.preserved_checkpoint_indices
                )
            )
        ):
            return (
                SessionRecoveryFailureCode.CORRELATION_MISMATCH,
                SessionRecoveryOwner.CHECKPOINT,
            )

    if evidence.lifecycle_state.durable_state is not SessionDurableState.READY:
        return (
            SessionRecoveryFailureCode.CORRELATION_MISMATCH,
            SessionRecoveryOwner.SESSION_LIFECYCLE,
        )
    if resources.state is GitSessionResourceState.ABSENT:
        return (
            SessionRecoveryFailureCode.MISSING_PREDECESSOR,
            SessionRecoveryOwner.SESSION_RESOURCES,
        )
    if resources.state is GitSessionResourceState.READY and (
        evidence.repository_status.branch != resources.identity.branch
        or evidence.repository_status.head_id != resources.current_branch_tip
    ):
        return (
            SessionRecoveryFailureCode.REPOSITORY_STATE_MISMATCH,
            SessionRecoveryOwner.REPOSITORY_STATUS,
        )

    repository_failure = _repository_failure(evidence)
    if repository_failure:
        return (
            SessionRecoveryFailureCode.REPOSITORY_STATE_MISMATCH,
            SessionRecoveryOwner.REPOSITORY_STATUS,
        )

    if metadata is not None:
        assert commit is not None and candidate is not None
        if (
            commit.state is not GitSessionHistoryState.COMMITTED
            or commit.commit_id != metadata.commit_id
            or metadata.tree_id != candidate.expected_tree
            or metadata.parent_ids != (candidate.branch_tip,)
            or resources.current_branch_tip != metadata.commit_id
        ):
            return (
                SessionRecoveryFailureCode.CORRELATION_MISMATCH,
                SessionRecoveryOwner.STORY_COMMIT,
            )
    elif commit is not None and commit.state is GitSessionHistoryState.COMMITTED:
        return (
            SessionRecoveryFailureCode.MISSING_PREDECESSOR,
            SessionRecoveryOwner.STORY_COMMIT,
        )
    return None


def _missing_predecessor_owner(
    evidence: SessionRecoveryEvidence,
) -> SessionRecoveryOwner | None:
    reviewed = evidence.reviewed_proposal
    application = evidence.artifact_application
    candidate = evidence.story_commit_candidate
    commit = evidence.story_commit_operation
    checkpoint = evidence.checkpoint_operation
    metadata = evidence.branch_commit_metadata
    if reviewed is None and any(
        value is not None for value in (application, candidate, commit, metadata)
    ):
        return SessionRecoveryOwner.ARTIFACT_APPLICATION
    if application is None and any(
        value is not None for value in (candidate, commit, metadata)
    ):
        return SessionRecoveryOwner.STORY_COMMIT
    if candidate is None and any(value is not None for value in (commit, metadata)):
        return SessionRecoveryOwner.STORY_COMMIT
    if commit is None and metadata is not None:
        return SessionRecoveryOwner.STORY_COMMIT
    represented_checkpoint_indices = (
        ()
        if checkpoint is None or not checkpoint.alternate_index_preserved
        else (checkpoint.operation_id,)
    )
    if (
        evidence.session_history.preserved_checkpoint_indices
        != represented_checkpoint_indices
    ):
        return SessionRecoveryOwner.CHECKPOINT
    return None


def _unresolved_owner(evidence: SessionRecoveryEvidence) -> SessionRecoveryOwner | None:
    if evidence.session_resources.state in {
        GitSessionResourceState.PROVISIONING,
        GitSessionResourceState.OUTCOME_UNKNOWN,
    }:
        return SessionRecoveryOwner.SESSION_RESOURCES
    if evidence.lifecycle_state.access_state in {
        SessionAccessState.BLOCKED_BY_LIVE_LOCAL_OWNER,
        SessionAccessState.OWNERSHIP_UNKNOWN,
    }:
        return SessionRecoveryOwner.SESSION_LIFECYCLE
    if evidence.collaboration_history.current_state in {
        CollaborationStepState.RUNNING,
        CollaborationStepState.OUTCOME_UNKNOWN,
    }:
        return SessionRecoveryOwner.COLLABORATION_HISTORY
    if any(
        state in {
            GitSessionHistoryState.RUNNING,
            GitSessionHistoryState.OUTCOME_UNKNOWN,
        }
        for _, state in evidence.session_history.commit_operations
    ):
        return SessionRecoveryOwner.STORY_COMMIT
    if any(
        state in {
            GitSessionHistoryState.RUNNING,
            GitSessionHistoryState.OUTCOME_UNKNOWN,
        }
        for _, state in evidence.session_history.checkpoint_operations
    ):
        return SessionRecoveryOwner.CHECKPOINT
    if evidence.story_commit_operation is not None and (
        evidence.story_commit_operation.state
        in {GitSessionHistoryState.RUNNING, GitSessionHistoryState.OUTCOME_UNKNOWN}
    ):
        return SessionRecoveryOwner.STORY_COMMIT
    if evidence.checkpoint_operation is not None and (
        evidence.checkpoint_operation.state
        in {GitSessionHistoryState.RUNNING, GitSessionHistoryState.OUTCOME_UNKNOWN}
    ):
        return SessionRecoveryOwner.CHECKPOINT
    return None


def _repository_failure(evidence: SessionRecoveryEvidence) -> bool:
    status = evidence.repository_status
    candidate = evidence.story_commit_candidate
    commit = evidence.story_commit_operation
    application = evidence.artifact_application
    if any(
        entry.index_change is GitChangeKind.UNMERGED
        or entry.worktree_change is GitChangeKind.UNMERGED
        for entry in status.entries
    ):
        return True
    if commit is not None and commit.state is GitSessionHistoryState.COMMITTED:
        return not status.clean
    if commit is not None and commit.state is GitSessionHistoryState.STAGED:
        assert candidate is not None
        return (
            any(
                entry.index_change is None or entry.worktree_change is not None
                for entry in status.entries
            )
            or _status_paths(status) != _candidate_paths(candidate)
            or status.head_id != candidate.branch_tip
        )
    if application is not None:
        if application.application_result.state is ArtifactApplicationState.UNCHANGED:
            return not status.clean
        return (
            any(
                entry.index_change is not None or entry.worktree_change is None
                for entry in status.entries
            )
            or _status_paths(status)
            != set(application.application_result.affected_paths)
        )
    return not status.clean


def _is_fresh_idle(evidence: SessionRecoveryEvidence) -> bool:
    history = evidence.collaboration_history
    return (
        history.session_id is None
        and history.conversation_id is None
        and history.last_sequence == 0
        and history.current_proposal is None
        and evidence.reviewed_proposal is None
        and evidence.artifact_application is None
        and evidence.story_commit_candidate is None
        and evidence.story_commit_operation is None
        and evidence.checkpoint_operation is None
        and evidence.branch_commit_metadata is None
    )


def _result(
    evidence: SessionRecoveryEvidence,
    state: SessionRecoveryState,
    owner: SessionRecoveryOwner,
    failure: SessionRecoveryFailureCode,
) -> SessionRecoveryResult:
    history = evidence.collaboration_history
    reviewed = evidence.reviewed_proposal
    application = evidence.artifact_application
    candidate = evidence.story_commit_candidate
    commit = evidence.story_commit_operation
    checkpoint = evidence.checkpoint_operation
    proposal_id = (
        reviewed.proposal_id
        if reviewed is not None
        else (
            history.current_proposal.proposal_id
            if history.current_proposal is not None
            else None
        )
    )
    correlation_id = next(
        (
            value
            for value in (
                reviewed.correlation_id if reviewed is not None else None,
                application.correlation_id if application is not None else None,
                candidate.correlation_id if candidate is not None else None,
                commit.correlation_id if commit is not None else None,
                checkpoint.correlation_id if checkpoint is not None else None,
            )
            if value is not None
        ),
        None,
    )
    conversation_id = history.conversation_id
    if conversation_id is None:
        _raise_error(SessionRecoveryFailureCode.UNSUPPORTED_SHAPE)
    return SessionRecoveryResult(
        session_id=evidence.lifecycle_state.session_id,
        conversation_id=conversation_id,
        collaboration_tail_sequence=history.last_sequence,
        state=state,
        next_action=_NEXT_ACTION[state],
        owner=owner,
        failure_code=failure,
        proposal_id=proposal_id,
        confirmation_request_id=(
            reviewed.application_confirmation_request_id
            if reviewed is not None
            else history.confirmation_request_id
        ),
        application_id=(
            application.application_result.application_id
            if application is not None
            else None
        ),
        reviewed_candidate_id=(
            reviewed.candidate_id if reviewed is not None else None
        ),
        story_commit_candidate_id=(
            candidate.candidate_id if candidate is not None else None
        ),
        story_commit_operation_id=(
            commit.operation_id if commit is not None else None
        ),
        commit_id=commit.commit_id if commit is not None else None,
        checkpoint_operation_id=(
            checkpoint.operation_id if checkpoint is not None else None
        ),
        correlation_id=correlation_id,
    )


def _candidate_paths(candidate: GitSessionCandidateRecord) -> set[str]:
    return {path.path for path in candidate.paths}


def _status_paths(status: GitStatus) -> set[str]:
    paths = {entry.path for entry in status.entries}
    paths.update(
        entry.previous_path
        for entry in status.entries
        if entry.previous_path is not None
    )
    return paths


def _owner_value(value: object, expected_type: type) -> None:
    if type(value) is not expected_type:
        _raise_error(SessionRecoveryFailureCode.UNSUPPORTED_SHAPE)
    invalid = False
    try:
        rebuilt = _deep_reconstruct(value)
        invalid = rebuilt != value
    except Exception:
        invalid = True
    if invalid:
        _raise_error(SessionRecoveryFailureCode.INVALID_REQUEST)


def _optional_owner_value(value: object | None, expected_type: type) -> None:
    if value is not None:
        _owner_value(value, expected_type)


def _deep_reconstruct(value: object) -> object:
    if type(value) is tuple:
        return tuple(_deep_reconstruct(item) for item in value)
    if is_dataclass(value) and not isinstance(value, type):
        value_type = type(value)
        keyword_values = {
            item.name: _deep_reconstruct(getattr(value, item.name))
            for item in fields(value)
            if item.init
        }
        return value_type(**keyword_values)
    return value


def _session_identifier(value: object) -> str:
    if type(value) is not str or _SESSION_IDENTIFIER.fullmatch(value) is None:
        _raise_error(SessionRecoveryFailureCode.INVALID_REQUEST)
    return value


def _opaque_identifier(value: object) -> str:
    if type(value) is not str or _OPAQUE_IDENTIFIER.fullmatch(value) is None:
        _raise_error(SessionRecoveryFailureCode.INVALID_REQUEST)
    return value


def _optional_opaque_identifier(value: object | None) -> None:
    if value is not None:
        _opaque_identifier(value)


def _sequence(value: object) -> int:
    if type(value) is not int or value < 0 or value > 2**63 - 1:
        _raise_error(SessionRecoveryFailureCode.INVALID_REQUEST)
    return value


def _positive_integer(value: object) -> int:
    if type(value) is not int or value < 1 or value > 2**63 - 1:
        _raise_error(SessionRecoveryFailureCode.INVALID_REQUEST)
    return value


def _digest(value: object) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        _raise_error(SessionRecoveryFailureCode.INVALID_REQUEST)
    return value


def _tagged_digest(value: object) -> str:
    if type(value) is not str or _TAGGED_DIGEST.fullmatch(value) is None:
        _raise_error(SessionRecoveryFailureCode.INVALID_REQUEST)
    return value


def _confirmation_choices(value: object) -> None:
    if type(value) is not tuple or any(
        type(item) is not CollaborationConfirmationDecision for item in value
    ):
        _raise_error(SessionRecoveryFailureCode.UNSUPPORTED_SHAPE)
    if len(set(value)) != len(value):
        _raise_error(SessionRecoveryFailureCode.INVALID_REQUEST)


def _raise_error(code: SessionRecoveryFailureCode) -> NoReturn:
    error = SessionRecoveryError(code)
    error.__cause__ = None
    error.__context__ = None
    raise error from None
