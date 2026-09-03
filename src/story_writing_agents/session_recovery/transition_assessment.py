"""Content-safe transition assessment for active and idle story sessions."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, fields, is_dataclass
from enum import StrEnum
from pathlib import Path

from ..conversations import CollaborationEventStore
from ..git import (
    GitExternalOperationInspection,
    GitExternalOperationKind,
    GitOperationError,
    GitRepositoryAdapter,
    GitStatus,
)
from ..git.session_contracts import (
    GitSessionFailureCode,
    GitSessionResourceError,
    GitSessionResourceInspection,
    GitSessionResourceState,
)
from ..git.session_history_contracts import (
    GitSessionHistoryError,
    GitSessionHistoryFailureCode,
    GitSessionHistorySnapshot,
    GitSessionHistoryState,
)
from ..session_lifecycle import (
    SessionAccessState,
    SessionDurableState,
    SessionLifecycleState,
)
from ..workspace.initialization import inspect_session_history, inspect_session_resources
from .contracts import (
    SessionArtifactApplicationEvidence,
    SessionRecoveryError,
    SessionRecoveryFailureCode,
    SessionRecoveryOwner,
    SessionRecoveryResult,
    SessionRecoveryState,
    SessionReviewedProposalEvidence,
)
from .reconciliation import SessionRecoveryReconciler


__all__ = [
    "SessionTransitionScope",
    "SessionTransitionAssessmentState",
    "SessionTransitionNextAction",
    "SessionTransitionOperation",
    "SessionTransitionAssessment",
    "SessionTransitionAssessor",
]


_SESSION_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


class SessionTransitionScope(StrEnum):
    """Whether transition truth wraps active work or proves an idle session."""

    IDLE = "idle"
    ACTIVE = "active"


class SessionTransitionAssessmentState(StrEnum):
    """Closed transition-safety classification."""

    IDLE_READY = "idle_ready"
    ACTIVE = "active"
    OUTCOME_UNKNOWN = "outcome_unknown"
    RECOVERY_REQUIRED = "recovery_required"


class SessionTransitionNextAction(StrEnum):
    """One content-safe instruction for the transition caller."""

    BEGIN_WITH_LEAD = "begin_with_lead"
    FOLLOW_ACTIVE_RECOVERY = "follow_active_recovery"
    INSPECT_OWNER_OUTCOME = "inspect_owner_outcome"
    OPERATOR_RECOVERY = "operator_recovery"


class SessionTransitionOperation(StrEnum):
    """Closed operations whose permission is represented by an assessment."""

    ENTER_DESTINATION = "enter_destination"
    LEAVE_SOURCE = "leave_source"
    PAUSE_SESSION = "pause_session"


_ALL_OPERATIONS = tuple(SessionTransitionOperation)
_ENTER_ONLY = (SessionTransitionOperation.ENTER_DESTINATION,)


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionTransitionAssessment:
    """Immutable content-free transition assessment for one story session."""

    session_id: str
    conversation_id: str | None
    scope: SessionTransitionScope
    state: SessionTransitionAssessmentState
    next_action: SessionTransitionNextAction
    owner: SessionRecoveryOwner | None
    failure_code: SessionRecoveryFailureCode
    allowed_operations: tuple[SessionTransitionOperation, ...]
    recovery_result: SessionRecoveryResult | None

    def __post_init__(self) -> None:
        _identifier(self.session_id)
        if self.conversation_id is not None:
            _identifier(self.conversation_id)
        if (
            type(self.scope) is not SessionTransitionScope
            or type(self.state) is not SessionTransitionAssessmentState
            or type(self.next_action) is not SessionTransitionNextAction
            or (
                self.owner is not None
                and type(self.owner) is not SessionRecoveryOwner
            )
            or type(self.failure_code) is not SessionRecoveryFailureCode
            or type(self.allowed_operations) is not tuple
            or any(
                type(operation) is not SessionTransitionOperation
                for operation in self.allowed_operations
            )
        ):
            _raise(SessionRecoveryFailureCode.UNSUPPORTED_SHAPE)
        canonical_operations = tuple(
            operation
            for operation in SessionTransitionOperation
            if operation in self.allowed_operations
        )
        if canonical_operations != self.allowed_operations:
            _raise(SessionRecoveryFailureCode.INVALID_REQUEST)
        if self.recovery_result is not None:
            if type(self.recovery_result) is not SessionRecoveryResult:
                _raise(SessionRecoveryFailureCode.UNSUPPORTED_SHAPE)
            if not _exact_owner_value(self.recovery_result, SessionRecoveryResult):
                _raise(SessionRecoveryFailureCode.INVALID_REQUEST)
            if (
                self.recovery_result.session_id != self.session_id
                or self.recovery_result.conversation_id != self.conversation_id
            ):
                _raise(SessionRecoveryFailureCode.INVALID_REQUEST)

        if self.state is SessionTransitionAssessmentState.IDLE_READY:
            valid = (
                self.scope is SessionTransitionScope.IDLE
                and self.conversation_id is None
                and self.next_action is SessionTransitionNextAction.BEGIN_WITH_LEAD
                and self.owner is None
                and self.failure_code is SessionRecoveryFailureCode.NONE
                and self.allowed_operations == _ALL_OPERATIONS
                and self.recovery_result is None
            )
        elif self.state is SessionTransitionAssessmentState.ACTIVE:
            valid = (
                self.scope is SessionTransitionScope.ACTIVE
                and self.conversation_id is not None
                and self.next_action
                is SessionTransitionNextAction.FOLLOW_ACTIVE_RECOVERY
                and self.owner is None
                and self.failure_code is SessionRecoveryFailureCode.NONE
                and self.recovery_result is not None
                and self.recovery_result.state
                not in {
                    SessionRecoveryState.OUTCOME_UNKNOWN,
                    SessionRecoveryState.RECOVERY_REQUIRED,
                }
                and self.allowed_operations
                == _active_operations(self.recovery_result.state)
            )
        elif self.state is SessionTransitionAssessmentState.OUTCOME_UNKNOWN:
            valid = (
                self.next_action
                is SessionTransitionNextAction.INSPECT_OWNER_OUTCOME
                and self.owner is not None
                and self.failure_code
                is SessionRecoveryFailureCode.OWNER_OUTCOME_UNKNOWN
                and not self.allowed_operations
                and (
                    self.scope is SessionTransitionScope.IDLE
                    and self.conversation_id is None
                    and self.recovery_result is None
                    or self.scope is SessionTransitionScope.ACTIVE
                    and self.conversation_id is not None
                    and self.recovery_result is not None
                    and self.recovery_result.state
                    is SessionRecoveryState.OUTCOME_UNKNOWN
                    and self.recovery_result.owner is self.owner
                    and self.recovery_result.failure_code is self.failure_code
                )
            )
        else:
            valid = (
                self.next_action is SessionTransitionNextAction.OPERATOR_RECOVERY
                and self.owner is not None
                and self.failure_code
                not in {
                    SessionRecoveryFailureCode.NONE,
                    SessionRecoveryFailureCode.OWNER_OUTCOME_UNKNOWN,
                }
                and not self.allowed_operations
                and (
                    self.scope is SessionTransitionScope.IDLE
                    and self.conversation_id is None
                    and self.recovery_result is None
                    or self.scope is SessionTransitionScope.ACTIVE
                    and self.conversation_id is not None
                    and self.recovery_result is not None
                    and self.recovery_result.state
                    is SessionRecoveryState.RECOVERY_REQUIRED
                    and self.recovery_result.owner is self.owner
                    and self.recovery_result.failure_code is self.failure_code
                )
            )
        if not valid:
            _raise(SessionRecoveryFailureCode.INVALID_REQUEST)


class SessionTransitionAssessor:
    """Assess transitions without performing or authorizing an operation."""

    __slots__ = ("_workspace_root",)

    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = _workspace_root(workspace_root)

    def inspect(
        self,
        *,
        lifecycle_state: SessionLifecycleState,
        conversation_id: str | None = None,
        collaboration_store: CollaborationEventStore | None = None,
        reviewed_proposal: SessionReviewedProposalEvidence | None = None,
        artifact_application: SessionArtifactApplicationEvidence | None = None,
    ) -> SessionTransitionAssessment:
        """Return exact active recovery or stable idle transition truth."""

        active = conversation_id is not None or collaboration_store is not None
        if (conversation_id is None) is not (collaboration_store is None):
            _raise(SessionRecoveryFailureCode.INVALID_REQUEST)
        if active:
            assert conversation_id is not None and collaboration_store is not None
            recovery = SessionRecoveryReconciler(
                self._workspace_root,
                collaboration_store,
            ).inspect(
                lifecycle_state=lifecycle_state,
                conversation_id=conversation_id,
                reviewed_proposal=reviewed_proposal,
                artifact_application=artifact_application,
            )
            return _active_assessment(recovery)
        if reviewed_proposal is not None or artifact_application is not None:
            _raise(SessionRecoveryFailureCode.INVALID_REQUEST)

        lifecycle = _owner_input(lifecycle_state, SessionLifecycleState)
        session_id = lifecycle.session_id
        if lifecycle.durable_state is SessionDurableState.READY and (
            lifecycle.access_state
            in {
                SessionAccessState.BLOCKED_BY_LIVE_LOCAL_OWNER,
                SessionAccessState.OWNERSHIP_UNKNOWN,
            }
        ):
            return _idle_blocked(
                session_id,
                SessionTransitionAssessmentState.OUTCOME_UNKNOWN,
                SessionRecoveryOwner.SESSION_LIFECYCLE,
                SessionRecoveryFailureCode.OWNER_OUTCOME_UNKNOWN,
            )
        if (
            lifecycle.durable_state is not SessionDurableState.READY
            or lifecycle.access_state is not SessionAccessState.HELD_HERE
        ):
            return _idle_blocked(
                session_id,
                SessionTransitionAssessmentState.RECOVERY_REQUIRED,
                SessionRecoveryOwner.SESSION_LIFECYCLE,
                SessionRecoveryFailureCode.INVALID_REQUEST,
            )

        resources_a, resource_failure = _resources_capture(
            self._workspace_root,
            session_id,
            second=False,
        )
        history_a, history_failure = _history_capture(
            self._workspace_root,
            session_id,
            second=False,
        )
        first_failure = _strongest(resource_failure, history_failure)
        if first_failure is not None:
            return _idle_blocked(session_id, *first_failure)
        assert resources_a is not None and history_a is not None

        history_fact = _history_fact(resources_a, history_a, session_id)
        resource_fact = _resource_fact(resources_a, session_id)
        first_fact = _strongest(resource_fact, history_fact)
        if first_fact is not None:
            return _idle_blocked(session_id, *first_fact)

        worktree = resources_a.identity.worktree_root
        try:
            adapter = GitRepositoryAdapter(worktree)
            external_a = adapter.inspect_external_operation()
        except GitOperationError:
            return _idle_recovery(session_id, SessionRecoveryOwner.EXTERNAL_GIT)
        except Exception:
            return _idle_recovery(session_id, SessionRecoveryOwner.EXTERNAL_GIT)
        if not _exact_owner_value(external_a, GitExternalOperationInspection):
            return _idle_recovery(session_id, SessionRecoveryOwner.EXTERNAL_GIT)
        try:
            status_a = adapter.status()
        except GitOperationError:
            return _idle_recovery(session_id, SessionRecoveryOwner.REPOSITORY_STATUS)
        except Exception:
            return _idle_recovery(session_id, SessionRecoveryOwner.REPOSITORY_STATUS)
        if not _exact_owner_value(status_a, GitStatus):
            return _idle_recovery(session_id, SessionRecoveryOwner.REPOSITORY_STATUS)

        resources_b, resource_failure = _resources_capture(
            self._workspace_root,
            session_id,
            second=True,
        )
        if resource_failure is not None:
            return _idle_blocked(session_id, *resource_failure)
        history_b, history_failure = _history_capture(
            self._workspace_root,
            session_id,
            second=True,
        )
        if history_failure is not None:
            return _idle_blocked(session_id, *history_failure)
        assert resources_b is not None and history_b is not None
        try:
            external_b = adapter.inspect_external_operation()
        except GitOperationError:
            return _idle_unknown(session_id, SessionRecoveryOwner.EXTERNAL_GIT)
        except Exception:
            return _idle_unknown(session_id, SessionRecoveryOwner.EXTERNAL_GIT)
        if not _exact_owner_value(external_b, GitExternalOperationInspection):
            return _idle_unknown(session_id, SessionRecoveryOwner.EXTERNAL_GIT)
        try:
            status_b = adapter.status()
        except GitOperationError:
            return _idle_unknown(session_id, SessionRecoveryOwner.REPOSITORY_STATUS)
        except Exception:
            return _idle_unknown(session_id, SessionRecoveryOwner.REPOSITORY_STATUS)
        if not _exact_owner_value(status_b, GitStatus):
            return _idle_unknown(session_id, SessionRecoveryOwner.REPOSITORY_STATUS)

        if resources_a != resources_b:
            return _idle_unknown(session_id, SessionRecoveryOwner.SESSION_RESOURCES)
        if (
            history_a.inspection.session_id != history_b.inspection.session_id
            or history_a.candidates != history_b.candidates
            or history_a.commit_operations != history_b.commit_operations
        ):
            return _idle_unknown(session_id, SessionRecoveryOwner.STORY_COMMIT)
        if (
            history_a.checkpoint_operations != history_b.checkpoint_operations
            or history_a.inspection.checkpoint_operations
            != history_b.inspection.checkpoint_operations
            or history_a.inspection.preserved_checkpoint_indices
            != history_b.inspection.preserved_checkpoint_indices
        ):
            return _idle_unknown(session_id, SessionRecoveryOwner.CHECKPOINT)
        if external_a != external_b:
            return _idle_unknown(session_id, SessionRecoveryOwner.EXTERNAL_GIT)
        if status_a != status_b:
            return _idle_unknown(session_id, SessionRecoveryOwner.REPOSITORY_STATUS)

        if external_b.kind is not GitExternalOperationKind.NONE:
            return _idle_blocked(
                session_id,
                SessionTransitionAssessmentState.RECOVERY_REQUIRED,
                SessionRecoveryOwner.EXTERNAL_GIT,
                SessionRecoveryFailureCode.EXTERNAL_GIT_OPERATION,
            )
        if (
            not status_b.clean
            or status_b.branch != resources_b.identity.branch
            or status_b.head_id != resources_b.current_branch_tip
        ):
            return _idle_blocked(
                session_id,
                SessionTransitionAssessmentState.RECOVERY_REQUIRED,
                SessionRecoveryOwner.REPOSITORY_STATUS,
                SessionRecoveryFailureCode.REPOSITORY_STATE_MISMATCH,
            )
        return SessionTransitionAssessment(
            session_id=session_id,
            conversation_id=None,
            scope=SessionTransitionScope.IDLE,
            state=SessionTransitionAssessmentState.IDLE_READY,
            next_action=SessionTransitionNextAction.BEGIN_WITH_LEAD,
            owner=None,
            failure_code=SessionRecoveryFailureCode.NONE,
            allowed_operations=_ALL_OPERATIONS,
            recovery_result=None,
        )


def _active_assessment(recovery: SessionRecoveryResult) -> SessionTransitionAssessment:
    if recovery.state is SessionRecoveryState.OUTCOME_UNKNOWN:
        state = SessionTransitionAssessmentState.OUTCOME_UNKNOWN
        action = SessionTransitionNextAction.INSPECT_OWNER_OUTCOME
        owner = recovery.owner
        failure = recovery.failure_code
        operations: tuple[SessionTransitionOperation, ...] = ()
    elif recovery.state is SessionRecoveryState.RECOVERY_REQUIRED:
        state = SessionTransitionAssessmentState.RECOVERY_REQUIRED
        action = SessionTransitionNextAction.OPERATOR_RECOVERY
        owner = recovery.owner
        failure = recovery.failure_code
        operations = ()
    else:
        state = SessionTransitionAssessmentState.ACTIVE
        action = SessionTransitionNextAction.FOLLOW_ACTIVE_RECOVERY
        owner = None
        failure = SessionRecoveryFailureCode.NONE
        operations = _active_operations(recovery.state)
    return SessionTransitionAssessment(
        session_id=recovery.session_id,
        conversation_id=recovery.conversation_id,
        scope=SessionTransitionScope.ACTIVE,
        state=state,
        next_action=action,
        owner=owner,
        failure_code=failure,
        allowed_operations=operations,
        recovery_result=recovery,
    )


def _active_operations(
    state: SessionRecoveryState,
) -> tuple[SessionTransitionOperation, ...]:
    if state in {
        SessionRecoveryState.PROPOSED,
        SessionRecoveryState.REVIEWED,
        SessionRecoveryState.AWAITING_APPLICATION,
        SessionRecoveryState.COMMITTED,
        SessionRecoveryState.REJECTED,
    }:
        return _ALL_OPERATIONS
    if state in {
        SessionRecoveryState.APPLIED_NOT_COMMITTED,
        SessionRecoveryState.STAGED,
    }:
        return _ENTER_ONLY
    _raise(SessionRecoveryFailureCode.INVALID_REQUEST)


def _resources_capture(
    root: Path,
    session_id: str,
    *,
    second: bool,
) -> tuple[
    GitSessionResourceInspection | None,
    tuple[
        SessionTransitionAssessmentState,
        SessionRecoveryOwner,
        SessionRecoveryFailureCode,
    ]
    | None,
]:
    try:
        value = inspect_session_resources(root, session_id)
    except GitSessionResourceError as failure:
        unknown = (
            second
            or failure.code is GitSessionFailureCode.OUTCOME_UNKNOWN
            or failure.state
            in {
                GitSessionResourceState.PROVISIONING,
                GitSessionResourceState.OUTCOME_UNKNOWN,
            }
        )
        return None, _fact(
            unknown,
            SessionRecoveryOwner.SESSION_RESOURCES,
        )
    except Exception:
        return None, _fact(second, SessionRecoveryOwner.SESSION_RESOURCES)
    if not _exact_owner_value(value, GitSessionResourceInspection):
        return None, _fact(second, SessionRecoveryOwner.SESSION_RESOURCES)
    return value, None


def _history_capture(
    root: Path,
    session_id: str,
    *,
    second: bool,
) -> tuple[
    GitSessionHistorySnapshot | None,
    tuple[
        SessionTransitionAssessmentState,
        SessionRecoveryOwner,
        SessionRecoveryFailureCode,
    ]
    | None,
]:
    try:
        value = inspect_session_history(root, session_id)
    except GitSessionHistoryError as failure:
        unknown = (
            second
            or failure.code is GitSessionHistoryFailureCode.OUTCOME_UNKNOWN
        )
        return None, _fact(unknown, SessionRecoveryOwner.STORY_COMMIT)
    except Exception:
        return None, _fact(second, SessionRecoveryOwner.STORY_COMMIT)
    if not _exact_owner_value(value, GitSessionHistorySnapshot):
        return None, _fact(second, SessionRecoveryOwner.STORY_COMMIT)
    return value, None


def _resource_fact(
    resources: GitSessionResourceInspection,
    session_id: str,
):
    if resources.identity.session_id != session_id:
        return (
            SessionTransitionAssessmentState.RECOVERY_REQUIRED,
            SessionRecoveryOwner.SESSION_RESOURCES,
            SessionRecoveryFailureCode.CORRELATION_MISMATCH,
        )
    if resources.state is GitSessionResourceState.RECOVERY_REQUIRED:
        return _recovery_fact(SessionRecoveryOwner.SESSION_RESOURCES)
    if resources.state is GitSessionResourceState.ABSENT:
        return (
            SessionTransitionAssessmentState.RECOVERY_REQUIRED,
            SessionRecoveryOwner.SESSION_RESOURCES,
            SessionRecoveryFailureCode.MISSING_PREDECESSOR,
        )
    if resources.state in {
        GitSessionResourceState.PROVISIONING,
        GitSessionResourceState.OUTCOME_UNKNOWN,
    }:
        return _unknown_fact(SessionRecoveryOwner.SESSION_RESOURCES)
    return None


def _history_fact(
    resources: GitSessionResourceInspection,
    snapshot: GitSessionHistorySnapshot,
    session_id: str,
):
    if snapshot.inspection.session_id != session_id:
        return (
            SessionTransitionAssessmentState.RECOVERY_REQUIRED,
            SessionRecoveryOwner.STORY_COMMIT,
            SessionRecoveryFailureCode.CORRELATION_MISMATCH,
        )
    for records, owner in (
        (snapshot.commit_operations, SessionRecoveryOwner.STORY_COMMIT),
        (snapshot.checkpoint_operations, SessionRecoveryOwner.CHECKPOINT),
    ):
        if any(record.state is GitSessionHistoryState.RECOVERY_REQUIRED for record in records):
            return _recovery_fact(owner)
    if snapshot.inspection.preserved_checkpoint_indices:
        return _recovery_fact(SessionRecoveryOwner.CHECKPOINT)
    if any(
        record.state is GitSessionHistoryState.STAGED
        for record in snapshot.commit_operations
    ):
        return _recovery_fact(SessionRecoveryOwner.STORY_COMMIT)
    for records, owner in (
        (snapshot.commit_operations, SessionRecoveryOwner.STORY_COMMIT),
        (snapshot.checkpoint_operations, SessionRecoveryOwner.CHECKPOINT),
    ):
        if any(
            record.state
            in {GitSessionHistoryState.RUNNING, GitSessionHistoryState.OUTCOME_UNKNOWN}
            for record in records
        ):
            return _unknown_fact(owner)

    operations_by_candidate = {
        candidate.candidate_id: tuple(
            operation
            for operation in snapshot.commit_operations
            if operation.candidate_id == candidate.candidate_id
        )
        for candidate in snapshot.candidates
    }
    if any(
        len(operations) != 1
        or operations[0].state
        not in {GitSessionHistoryState.COMMITTED, GitSessionHistoryState.REJECTED}
        for operations in operations_by_candidate.values()
    ):
        return (
            SessionTransitionAssessmentState.RECOVERY_REQUIRED,
            SessionRecoveryOwner.STORY_COMMIT,
            SessionRecoveryFailureCode.CORRELATION_MISMATCH,
        )

    if resources.state is GitSessionResourceState.READY:
        assert resources.initial_branch_tip is not None
        story_tip = _story_history_tip(snapshot, resources.initial_branch_tip)
        if story_tip is None or story_tip != resources.current_branch_tip:
            return (
                SessionTransitionAssessmentState.RECOVERY_REQUIRED,
                SessionRecoveryOwner.STORY_COMMIT,
                SessionRecoveryFailureCode.CORRELATION_MISMATCH,
            )
        assert resources.initial_internal_tip is not None
        checkpoint_tip = _checkpoint_history_tip(
            snapshot,
            resources.initial_internal_tip,
        )
        if checkpoint_tip is None or checkpoint_tip != resources.current_internal_tip:
            return (
                SessionTransitionAssessmentState.RECOVERY_REQUIRED,
                SessionRecoveryOwner.CHECKPOINT,
                SessionRecoveryFailureCode.CORRELATION_MISMATCH,
            )
    return None


def _story_history_tip(
    snapshot: GitSessionHistorySnapshot,
    initial_tip: str,
) -> str | None:
    candidates = {
        candidate.candidate_id: candidate for candidate in snapshot.candidates
    }
    remaining = [
        operation
        for operation in snapshot.commit_operations
        if operation.state is GitSessionHistoryState.COMMITTED
    ]
    tip = initial_tip
    while remaining:
        successors = [
            operation
            for operation in remaining
            if candidates[operation.candidate_id].branch_tip == tip
        ]
        if len(successors) != 1:
            return None
        successor = successors[0]
        assert successor.commit_id is not None
        tip = successor.commit_id
        remaining.remove(successor)
    return tip


def _checkpoint_history_tip(
    snapshot: GitSessionHistorySnapshot,
    initial_tip: str,
) -> str | None:
    remaining = [
        operation
        for operation in snapshot.checkpoint_operations
        if operation.state is GitSessionHistoryState.COMMITTED
    ]
    tip = initial_tip
    while remaining:
        successors = [
            operation
            for operation in remaining
            if operation.expected_internal_tip == tip
        ]
        if len(successors) != 1:
            return None
        successor = successors[0]
        assert successor.checkpoint_id is not None
        tip = successor.checkpoint_id
        remaining.remove(successor)
    return tip


def _strongest(*facts):
    values = tuple(fact for fact in facts if fact is not None)
    return next(
        (
            fact
            for fact in values
            if fact[0] is SessionTransitionAssessmentState.RECOVERY_REQUIRED
        ),
        values[0] if values else None,
    )


def _fact(unknown: bool, owner: SessionRecoveryOwner):
    return _unknown_fact(owner) if unknown else _recovery_fact(owner)


def _unknown_fact(owner: SessionRecoveryOwner):
    return (
        SessionTransitionAssessmentState.OUTCOME_UNKNOWN,
        owner,
        SessionRecoveryFailureCode.OWNER_OUTCOME_UNKNOWN,
    )


def _recovery_fact(owner: SessionRecoveryOwner):
    return (
        SessionTransitionAssessmentState.RECOVERY_REQUIRED,
        owner,
        SessionRecoveryFailureCode.OWNER_RECOVERY_REQUIRED,
    )


def _idle_unknown(
    session_id: str,
    owner: SessionRecoveryOwner,
) -> SessionTransitionAssessment:
    return _idle_blocked(session_id, *_unknown_fact(owner))


def _idle_recovery(
    session_id: str,
    owner: SessionRecoveryOwner,
) -> SessionTransitionAssessment:
    return _idle_blocked(session_id, *_recovery_fact(owner))


def _idle_blocked(
    session_id: str,
    state: SessionTransitionAssessmentState,
    owner: SessionRecoveryOwner,
    failure: SessionRecoveryFailureCode,
) -> SessionTransitionAssessment:
    action = (
        SessionTransitionNextAction.INSPECT_OWNER_OUTCOME
        if state is SessionTransitionAssessmentState.OUTCOME_UNKNOWN
        else SessionTransitionNextAction.OPERATOR_RECOVERY
    )
    return SessionTransitionAssessment(
        session_id=session_id,
        conversation_id=None,
        scope=SessionTransitionScope.IDLE,
        state=state,
        next_action=action,
        owner=owner,
        failure_code=failure,
        allowed_operations=(),
        recovery_result=None,
    )


def _workspace_root(value: object) -> Path:
    invalid = False
    try:
        raw = os.fspath(value)
        invalid = (
            type(value) is not type(Path())
            or not value.is_absolute()
            or value != Path(os.path.normpath(raw))
            or value.name in {"", ".", ".."}
            or any(character in raw for character in ("\x00", "\r", "\n"))
        )
        raw.encode("utf-8", errors="strict")
    except (AttributeError, TypeError, UnicodeError, ValueError):
        invalid = True
    if invalid:
        _raise(SessionRecoveryFailureCode.UNSUPPORTED_SHAPE)
    return value


def _owner_input(value: object, expected_type: type):
    if type(value) is not expected_type:
        _raise(SessionRecoveryFailureCode.UNSUPPORTED_SHAPE)
    if not _exact_owner_value(value, expected_type):
        _raise(SessionRecoveryFailureCode.INVALID_REQUEST)
    return _deep_reconstruct(value)


def _exact_owner_value(value: object, expected_type: type) -> bool:
    if type(value) is not expected_type:
        return False
    try:
        return _deep_reconstruct(value) == value
    except Exception:
        return False


def _deep_reconstruct(value: object) -> object:
    if type(value) is tuple:
        return tuple(_deep_reconstruct(item) for item in value)
    if is_dataclass(value) and not isinstance(value, type):
        return type(value)(
            **{
                item.name: _deep_reconstruct(getattr(value, item.name))
                for item in fields(value)
                if item.init
            }
        )
    return value


def _identifier(value: object) -> str:
    if type(value) is not str or _SESSION_IDENTIFIER.fullmatch(value) is None:
        _raise(SessionRecoveryFailureCode.INVALID_REQUEST)
    return value


def _raise(code: SessionRecoveryFailureCode) -> None:
    error = SessionRecoveryError(code)
    error.__cause__ = None
    error.__context__ = None
    raise error from None
