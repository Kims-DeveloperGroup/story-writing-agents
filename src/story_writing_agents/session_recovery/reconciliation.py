"""Bounded owner observation and correlated story-session recovery."""

from __future__ import annotations

import inspect
import os
import re
from dataclasses import fields, is_dataclass
from pathlib import Path

from ..conversations import (
    CollaborationEventStore,
    CollaborationHistoryProjection,
    CollaborationNextAction,
)
from ..git import (
    GitCommitMetadata,
    GitExternalOperationInspection,
    GitLogMetadata,
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
    GitSessionCandidateRecord,
    GitSessionCheckpointOperationRecord,
    GitSessionCommitOperationRecord,
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
from ..workspace.initialization import (
    SessionArtifactBinding,
    SessionArtifactBindingError,
    compose_session_artifact_binding,
    inspect_session_history,
    inspect_session_resources,
)
from ..writing_artifacts import (
    ArtifactApplicationError,
    ArtifactApplicationErrorCode,
    ArtifactApplicationResult,
)
from .contracts import (
    SessionArtifactApplicationEvidence,
    SessionRecoveryError,
    SessionRecoveryEvidence,
    SessionRecoveryFailureCode,
    SessionRecoveryNextAction,
    SessionRecoveryOwner,
    SessionRecoveryResult,
    SessionRecoveryState,
    SessionReviewedProposalEvidence,
    is_unmatched_submitted_lead_projection,
    reconcile_session_recovery,
)


__all__ = ["SessionRecoveryReconciler"]


_SESSION_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


class SessionRecoveryReconciler:
    """Observe authoritative owners twice and reconcile one stable generation."""

    __slots__ = ("_collaboration_store", "_workspace_root")

    def __init__(
        self,
        workspace_root: Path,
        collaboration_store: CollaborationEventStore,
    ) -> None:
        self._workspace_root = _require_workspace_root(workspace_root)
        self._collaboration_store = _require_collaboration_store(collaboration_store)

    def inspect(
        self,
        *,
        lifecycle_state: SessionLifecycleState,
        conversation_id: str,
        reviewed_proposal: SessionReviewedProposalEvidence | None = None,
        artifact_application: SessionArtifactApplicationEvidence | None = None,
    ) -> SessionRecoveryResult:
        """Return recovery truth from one stable, correlated owner observation."""

        lifecycle = _require_owner_input(lifecycle_state, SessionLifecycleState)
        conversation = _require_session_identifier(conversation_id)
        reviewed = _require_optional_owner_input(
            reviewed_proposal,
            SessionReviewedProposalEvidence,
        )
        application = _require_optional_owner_input(
            artifact_application,
            SessionArtifactApplicationEvidence,
        )
        session_id = lifecycle.session_id

        if lifecycle.durable_state is SessionDurableState.READY and (
            lifecycle.access_state
            in {
                SessionAccessState.BLOCKED_BY_LIVE_LOCAL_OWNER,
                SessionAccessState.OWNERSHIP_UNKNOWN,
            }
        ):
            return _blocked(
                session_id,
                conversation,
                0,
                SessionRecoveryState.OUTCOME_UNKNOWN,
                SessionRecoveryOwner.SESSION_LIFECYCLE,
                SessionRecoveryFailureCode.OWNER_OUTCOME_UNKNOWN,
            )
        if (
            lifecycle.durable_state is not SessionDurableState.READY
            or lifecycle.access_state is not SessionAccessState.HELD_HERE
        ):
            return _blocked(
                session_id,
                conversation,
                0,
                SessionRecoveryState.RECOVERY_REQUIRED,
                SessionRecoveryOwner.SESSION_LIFECYCLE,
                SessionRecoveryFailureCode.INVALID_REQUEST,
            )

        history_a = self._read_collaboration(
            session_id,
            conversation,
            tail=0,
            second_capture=False,
        )
        if isinstance(history_a, SessionRecoveryResult):
            return history_a
        tail = history_a.last_sequence
        empty_history = _is_canonical_empty_history(history_a)
        if empty_history and (reviewed is not None or application is not None):
            return _blocked(
                session_id,
                conversation,
                tail,
                SessionRecoveryState.RECOVERY_REQUIRED,
                SessionRecoveryOwner.COLLABORATION_HISTORY,
                SessionRecoveryFailureCode.MISSING_PREDECESSOR,
            )
        unmatched_submitted_lead = is_unmatched_submitted_lead_projection(history_a)
        if not empty_history and not unmatched_submitted_lead and (
            history_a.session_id is None
            or history_a.conversation_id is None
            or (
                (history_a.current_proposal is None)
                != (history_a.current_state is None)
            )
            or tail < 1
        ):
            return _blocked(
                session_id,
                conversation,
                tail,
                SessionRecoveryState.RECOVERY_REQUIRED,
                SessionRecoveryOwner.COLLABORATION_HISTORY,
                SessionRecoveryFailureCode.MISSING_PREDECESSOR,
            )
        if unmatched_submitted_lead and (
            reviewed is not None or application is not None
        ):
            return _blocked(
                session_id,
                conversation,
                tail,
                SessionRecoveryState.RECOVERY_REQUIRED,
                SessionRecoveryOwner.COLLABORATION_HISTORY,
                SessionRecoveryFailureCode.MISSING_PREDECESSOR,
            )
        if not empty_history and (
            history_a.session_id != session_id
            or history_a.conversation_id != conversation
        ):
            return _blocked(
                session_id,
                conversation,
                0,
                SessionRecoveryState.RECOVERY_REQUIRED,
                SessionRecoveryOwner.COLLABORATION_HISTORY,
                SessionRecoveryFailureCode.CORRELATION_MISMATCH,
            )
        if reviewed is not None and (
            reviewed.session_id != session_id
            or reviewed.conversation_id != conversation
            or reviewed.collaboration_tail_sequence != tail
        ):
            return _blocked(
                session_id,
                conversation,
                tail,
                SessionRecoveryState.RECOVERY_REQUIRED,
                SessionRecoveryOwner.COLLABORATION_HISTORY,
                SessionRecoveryFailureCode.CORRELATION_MISMATCH,
            )
        if application is not None and reviewed is None:
            return _blocked(
                session_id,
                conversation,
                tail,
                SessionRecoveryState.RECOVERY_REQUIRED,
                SessionRecoveryOwner.ARTIFACT_APPLICATION,
                SessionRecoveryFailureCode.MISSING_PREDECESSOR,
            )
        if application is not None and (
            application.session_id != session_id
            or application.conversation_id != conversation
            or application.collaboration_tail_sequence != tail
            or application.proposal_id != reviewed.proposal_id
            or application.correlation_id != reviewed.correlation_id
        ):
            return _blocked(
                session_id,
                conversation,
                tail,
                SessionRecoveryState.RECOVERY_REQUIRED,
                SessionRecoveryOwner.ARTIFACT_APPLICATION,
                SessionRecoveryFailureCode.CORRELATION_MISMATCH,
            )

        resources_a = self._read_resources(session_id, conversation, tail, False)
        if isinstance(resources_a, SessionRecoveryResult):
            return resources_a
        if resources_a.identity.session_id != session_id:
            return _blocked(
                session_id,
                conversation,
                tail,
                SessionRecoveryState.RECOVERY_REQUIRED,
                SessionRecoveryOwner.SESSION_RESOURCES,
                SessionRecoveryFailureCode.CORRELATION_MISMATCH,
            )
        resource_blocker = _resource_blocker(
            resources_a,
            session_id,
            conversation,
            tail,
        )
        if resource_blocker is not None:
            return resource_blocker
        try:
            artifact_binding = compose_session_artifact_binding(
                self._workspace_root,
                session_id,
            )
        except SessionArtifactBindingError:
            return _owner_recovery(
                session_id,
                conversation,
                tail,
                SessionRecoveryOwner.SESSION_RESOURCES,
            )
        if artifact_binding.resources != resources_a:
            return _owner_recovery(
                session_id,
                conversation,
                tail,
                SessionRecoveryOwner.SESSION_RESOURCES,
            )

        snapshot_a = self._read_history(session_id, conversation, tail, False)
        if isinstance(snapshot_a, SessionRecoveryResult):
            return snapshot_a
        history_blocker = _history_blocker(
            snapshot_a,
            session_id,
            conversation,
            tail,
        )
        if history_blocker is not None:
            return history_blocker

        if (
            not empty_history
            and history_a.current_proposal is None
            and history_a.current_state is None
        ):
            for candidate in snapshot_a.candidates:
                operations = tuple(
                    operation
                    for operation in snapshot_a.commit_operations
                    if operation.candidate_id == candidate.candidate_id
                )
                if len(operations) != 1:
                    return _blocked(
                        session_id,
                        conversation,
                        tail,
                        SessionRecoveryState.RECOVERY_REQUIRED,
                        SessionRecoveryOwner.STORY_COMMIT,
                        SessionRecoveryFailureCode.MISSING_PREDECESSOR,
                    )

        if application is not None:
            application_blocker = _inspect_application(
                artifact_binding,
                application,
                session_id,
                conversation,
                tail,
            )
            if application_blocker is not None:
                return application_blocker

        try:
            adapter = GitRepositoryAdapter(resources_a.identity.worktree_root)
            with artifact_binding.open_ready_resources():
                external_a = adapter.inspect_external_operation()
        except SessionArtifactBindingError:
            return _owner_recovery(
                session_id,
                conversation,
                tail,
                SessionRecoveryOwner.SESSION_RESOURCES,
            )
        except GitOperationError:
            return _blocked(
                session_id,
                conversation,
                tail,
                SessionRecoveryState.RECOVERY_REQUIRED,
                SessionRecoveryOwner.EXTERNAL_GIT,
                SessionRecoveryFailureCode.OWNER_RECOVERY_REQUIRED,
            )
        except Exception:
            return _blocked(
                session_id,
                conversation,
                tail,
                SessionRecoveryState.RECOVERY_REQUIRED,
                SessionRecoveryOwner.EXTERNAL_GIT,
                SessionRecoveryFailureCode.OWNER_RECOVERY_REQUIRED,
            )
        if not _exact_owner_value(external_a, GitExternalOperationInspection):
            return _owner_recovery(
                session_id,
                conversation,
                tail,
                SessionRecoveryOwner.EXTERNAL_GIT,
            )
        try:
            with artifact_binding.open_ready_resources():
                status_a = adapter.status()
        except SessionArtifactBindingError:
            return _owner_recovery(
                session_id,
                conversation,
                tail,
                SessionRecoveryOwner.SESSION_RESOURCES,
            )
        except GitOperationError:
            return _owner_recovery(
                session_id,
                conversation,
                tail,
                SessionRecoveryOwner.REPOSITORY_STATUS,
            )
        except Exception:
            return _owner_recovery(
                session_id,
                conversation,
                tail,
                SessionRecoveryOwner.REPOSITORY_STATUS,
            )
        if not _exact_owner_value(status_a, GitStatus):
            return _owner_recovery(
                session_id,
                conversation,
                tail,
                SessionRecoveryOwner.REPOSITORY_STATUS,
            )

        selected_a = _select_history(
            snapshot_a,
            resources_a,
            external_a,
            reviewed,
            application,
        )
        if isinstance(selected_a, tuple) and len(selected_a) == 3:
            candidate_a, commit_a, _checkpoint_a = selected_a
        else:
            owner, failure = selected_a
            return _blocked(
                session_id,
                conversation,
                tail,
                SessionRecoveryState.RECOVERY_REQUIRED,
                owner,
                failure,
            )

        log_metadata: GitLogMetadata | None = None
        if commit_a is not None and commit_a.state is GitSessionHistoryState.COMMITTED:
            try:
                with artifact_binding.open_ready_resources():
                    log_metadata = adapter.log_metadata()
            except SessionArtifactBindingError:
                return _owner_recovery(
                    session_id,
                    conversation,
                    tail,
                    SessionRecoveryOwner.SESSION_RESOURCES,
                )
            except GitOperationError:
                return _owner_recovery(
                    session_id,
                    conversation,
                    tail,
                    SessionRecoveryOwner.REPOSITORY_STATUS,
                )
            except Exception:
                return _owner_recovery(
                    session_id,
                    conversation,
                    tail,
                    SessionRecoveryOwner.REPOSITORY_STATUS,
                )
            if not _exact_owner_value(log_metadata, GitLogMetadata):
                return _owner_recovery(
                    session_id,
                    conversation,
                    tail,
                    SessionRecoveryOwner.REPOSITORY_STATUS,
                )

        history_b = self._read_collaboration(
            session_id,
            conversation,
            tail=tail,
            second_capture=True,
        )
        if isinstance(history_b, SessionRecoveryResult):
            return history_b
        resources_b = self._read_resources(session_id, conversation, tail, True)
        if isinstance(resources_b, SessionRecoveryResult):
            return _owner_recovery(
                session_id,
                conversation,
                tail,
                SessionRecoveryOwner.SESSION_RESOURCES,
            )
        snapshot_b = self._read_history(session_id, conversation, tail, True)
        if isinstance(snapshot_b, SessionRecoveryResult):
            return snapshot_b
        try:
            with artifact_binding.open_ready_resources():
                external_b = adapter.inspect_external_operation()
        except SessionArtifactBindingError:
            return _owner_recovery(
                session_id,
                conversation,
                tail,
                SessionRecoveryOwner.SESSION_RESOURCES,
            )
        except GitOperationError:
            return _owner_outcome(session_id, conversation, tail, SessionRecoveryOwner.EXTERNAL_GIT)
        except Exception:
            return _owner_outcome(session_id, conversation, tail, SessionRecoveryOwner.EXTERNAL_GIT)
        if not _exact_owner_value(external_b, GitExternalOperationInspection):
            return _owner_outcome(session_id, conversation, tail, SessionRecoveryOwner.EXTERNAL_GIT)
        try:
            with artifact_binding.open_ready_resources():
                status_b = adapter.status()
        except SessionArtifactBindingError:
            return _owner_recovery(
                session_id,
                conversation,
                tail,
                SessionRecoveryOwner.SESSION_RESOURCES,
            )
        except GitOperationError:
            return _owner_outcome(session_id, conversation, tail, SessionRecoveryOwner.REPOSITORY_STATUS)
        except Exception:
            return _owner_outcome(session_id, conversation, tail, SessionRecoveryOwner.REPOSITORY_STATUS)
        if not _exact_owner_value(status_b, GitStatus):
            return _owner_outcome(session_id, conversation, tail, SessionRecoveryOwner.REPOSITORY_STATUS)

        if history_a != history_b:
            return _owner_outcome(session_id, conversation, tail, SessionRecoveryOwner.COLLABORATION_HISTORY)
        if resources_a != resources_b:
            return _owner_recovery(
                session_id,
                conversation,
                tail,
                SessionRecoveryOwner.SESSION_RESOURCES,
            )
        try:
            with artifact_binding.open_ready_resources() as access:
                if access.resources != resources_b:
                    raise SessionArtifactBindingError(
                        "recovery session resources changed"
                    )
        except SessionArtifactBindingError:
            return _owner_recovery(
                session_id,
                conversation,
                tail,
                SessionRecoveryOwner.SESSION_RESOURCES,
            )
        if (
            snapshot_a.inspection.session_id != snapshot_b.inspection.session_id
            or snapshot_a.candidates != snapshot_b.candidates
            or snapshot_a.commit_operations != snapshot_b.commit_operations
        ):
            return _owner_outcome(session_id, conversation, tail, SessionRecoveryOwner.STORY_COMMIT)
        if (
            snapshot_a.checkpoint_operations != snapshot_b.checkpoint_operations
            or snapshot_a.inspection.checkpoint_operations
            != snapshot_b.inspection.checkpoint_operations
            or snapshot_a.inspection.preserved_checkpoint_indices
            != snapshot_b.inspection.preserved_checkpoint_indices
        ):
            return _owner_outcome(session_id, conversation, tail, SessionRecoveryOwner.CHECKPOINT)
        if external_a != external_b:
            return _owner_outcome(session_id, conversation, tail, SessionRecoveryOwner.EXTERNAL_GIT)
        if status_a != status_b:
            return _owner_outcome(session_id, conversation, tail, SessionRecoveryOwner.REPOSITORY_STATUS)

        if empty_history:
            return SessionRecoveryResult(
                session_id=session_id,
                conversation_id=conversation,
                collaboration_tail_sequence=0,
                state=SessionRecoveryState.READY,
                next_action=SessionRecoveryNextAction.CONTINUE_WITH_LEAD,
                owner=SessionRecoveryOwner.LEAD,
                failure_code=SessionRecoveryFailureCode.NONE,
            )

        selected_b = _select_history(
            snapshot_b,
            resources_b,
            external_b,
            reviewed,
            application,
        )
        if isinstance(selected_b, tuple) and len(selected_b) == 3:
            candidate, commit, checkpoint = selected_b
        else:
            owner, failure = selected_b
            return _blocked(
                session_id,
                conversation,
                tail,
                SessionRecoveryState.RECOVERY_REQUIRED,
                owner,
                failure,
            )

        branch_commit: GitCommitMetadata | None = None
        if commit is not None and commit.state is GitSessionHistoryState.COMMITTED:
            if log_metadata is None or not log_metadata.commits:
                return _blocked(
                    session_id,
                    conversation,
                    tail,
                    SessionRecoveryState.RECOVERY_REQUIRED,
                    SessionRecoveryOwner.REPOSITORY_STATUS,
                    SessionRecoveryFailureCode.MISSING_PREDECESSOR,
                )
            branch_commit = log_metadata.commits[0]
            if status_b.head_id != commit.commit_id:
                return _blocked(
                    session_id,
                    conversation,
                    tail,
                    SessionRecoveryState.RECOVERY_REQUIRED,
                    SessionRecoveryOwner.REPOSITORY_STATUS,
                    SessionRecoveryFailureCode.REPOSITORY_STATE_MISMATCH,
                )
            if branch_commit.commit_id != commit.commit_id:
                return _blocked(
                    session_id,
                    conversation,
                    tail,
                    SessionRecoveryState.RECOVERY_REQUIRED,
                    SessionRecoveryOwner.STORY_COMMIT,
                    SessionRecoveryFailureCode.CORRELATION_MISMATCH,
                )

        try:
            evidence = SessionRecoveryEvidence(
                lifecycle_state=lifecycle,
                collaboration_history=history_b,
                session_resources=resources_b,
                external_operation=external_b,
                reviewed_proposal=reviewed,
                artifact_application=application,
                session_history=snapshot_b.inspection,
                story_commit_candidate=candidate,
                story_commit_operation=commit,
                checkpoint_operation=checkpoint,
                repository_status=status_b,
                branch_commit_metadata=branch_commit,
                worktree_identity_digest=external_b.worktree_identity_digest,
            )
            return reconcile_session_recovery(evidence)
        except SessionRecoveryError:
            return _owner_recovery(
                session_id,
                conversation,
                tail,
                SessionRecoveryOwner.STORY_COMMIT,
            )

    def _read_collaboration(
        self,
        session_id: str,
        conversation_id: str,
        *,
        tail: int,
        second_capture: bool,
    ) -> CollaborationHistoryProjection | SessionRecoveryResult:
        try:
            value = self._collaboration_store.read_context(
                session_id,
                conversation_id,
            )
        except Exception:
            if second_capture:
                return _owner_outcome(
                    session_id,
                    conversation_id,
                    tail,
                    SessionRecoveryOwner.COLLABORATION_HISTORY,
                )
            return _owner_recovery(
                session_id,
                conversation_id,
                0,
                SessionRecoveryOwner.COLLABORATION_HISTORY,
            )
        if not _exact_owner_value(value, CollaborationHistoryProjection):
            return (
                _owner_outcome(
                    session_id,
                    conversation_id,
                    tail,
                    SessionRecoveryOwner.COLLABORATION_HISTORY,
                )
                if second_capture
                else _owner_recovery(
                    session_id,
                    conversation_id,
                    0,
                    SessionRecoveryOwner.COLLABORATION_HISTORY,
                )
            )
        return value

    def _read_resources(
        self,
        session_id: str,
        conversation_id: str,
        tail: int,
        second_capture: bool,
    ) -> GitSessionResourceInspection | SessionRecoveryResult:
        try:
            value = inspect_session_resources(self._workspace_root, session_id)
        except GitSessionResourceError as failure:
            if second_capture or failure.code is GitSessionFailureCode.OUTCOME_UNKNOWN:
                return _owner_outcome(
                    session_id,
                    conversation_id,
                    tail,
                    SessionRecoveryOwner.SESSION_RESOURCES,
                )
            return _owner_recovery(
                session_id,
                conversation_id,
                tail,
                SessionRecoveryOwner.SESSION_RESOURCES,
            )
        except Exception:
            return (
                _owner_outcome(
                    session_id,
                    conversation_id,
                    tail,
                    SessionRecoveryOwner.SESSION_RESOURCES,
                )
                if second_capture
                else _owner_recovery(
                    session_id,
                    conversation_id,
                    tail,
                    SessionRecoveryOwner.SESSION_RESOURCES,
                )
            )
        if not _exact_owner_value(value, GitSessionResourceInspection):
            return (
                _owner_outcome(
                    session_id,
                    conversation_id,
                    tail,
                    SessionRecoveryOwner.SESSION_RESOURCES,
                )
                if second_capture
                else _owner_recovery(
                    session_id,
                    conversation_id,
                    tail,
                    SessionRecoveryOwner.SESSION_RESOURCES,
                )
            )
        return value

    def _read_history(
        self,
        session_id: str,
        conversation_id: str,
        tail: int,
        second_capture: bool,
    ) -> GitSessionHistorySnapshot | SessionRecoveryResult:
        try:
            value = inspect_session_history(self._workspace_root, session_id)
        except GitSessionHistoryError as failure:
            if (
                second_capture
                or failure.code is GitSessionHistoryFailureCode.OUTCOME_UNKNOWN
            ):
                return _owner_outcome(
                    session_id,
                    conversation_id,
                    tail,
                    SessionRecoveryOwner.STORY_COMMIT,
                )
            return _owner_recovery(
                session_id,
                conversation_id,
                tail,
                SessionRecoveryOwner.STORY_COMMIT,
            )
        except Exception:
            return (
                _owner_outcome(
                    session_id,
                    conversation_id,
                    tail,
                    SessionRecoveryOwner.STORY_COMMIT,
                )
                if second_capture
                else _owner_recovery(
                    session_id,
                    conversation_id,
                    tail,
                    SessionRecoveryOwner.STORY_COMMIT,
                )
            )
        if not _exact_owner_value(value, GitSessionHistorySnapshot):
            return (
                _owner_outcome(
                    session_id,
                    conversation_id,
                    tail,
                    SessionRecoveryOwner.STORY_COMMIT,
                )
                if second_capture
                else _owner_recovery(
                    session_id,
                    conversation_id,
                    tail,
                    SessionRecoveryOwner.STORY_COMMIT,
                )
            )
        return value


def _is_canonical_empty_history(history: CollaborationHistoryProjection) -> bool:
    """Return whether every collaboration-owned field is at its empty value."""

    return history == CollaborationHistoryProjection(
        session_id=None,
        conversation_id=None,
        last_sequence=0,
        current_turn_id=None,
        current_step_id=None,
        current_proposal=None,
        current_state=None,
        confirmation_request_id=None,
        confirmation_decision=None,
        execution_attempt_id=None,
        execution_result_event_id=None,
        execution_result=None,
        next_safe_action=CollaborationNextAction.NONE,
    )


def _resource_blocker(
    resources: GitSessionResourceInspection,
    session_id: str,
    conversation_id: str,
    tail: int,
) -> SessionRecoveryResult | None:
    if resources.state in {
        GitSessionResourceState.PROVISIONING,
        GitSessionResourceState.OUTCOME_UNKNOWN,
    }:
        return _owner_outcome(session_id, conversation_id, tail, SessionRecoveryOwner.SESSION_RESOURCES)
    if resources.state is GitSessionResourceState.ABSENT:
        return _blocked(
            session_id,
            conversation_id,
            tail,
            SessionRecoveryState.RECOVERY_REQUIRED,
            SessionRecoveryOwner.SESSION_RESOURCES,
            SessionRecoveryFailureCode.MISSING_PREDECESSOR,
        )
    if resources.state is GitSessionResourceState.RECOVERY_REQUIRED:
        return _owner_recovery(session_id, conversation_id, tail, SessionRecoveryOwner.SESSION_RESOURCES)
    return None


def _history_blocker(
    snapshot: GitSessionHistorySnapshot,
    session_id: str,
    conversation_id: str,
    tail: int,
) -> SessionRecoveryResult | None:
    if snapshot.inspection.session_id != session_id:
        return _blocked(
            session_id,
            conversation_id,
            tail,
            SessionRecoveryState.RECOVERY_REQUIRED,
            SessionRecoveryOwner.STORY_COMMIT,
            SessionRecoveryFailureCode.CORRELATION_MISMATCH,
        )
    for records, owner in (
        (snapshot.commit_operations, SessionRecoveryOwner.STORY_COMMIT),
        (snapshot.checkpoint_operations, SessionRecoveryOwner.CHECKPOINT),
    ):
        if any(item.state is GitSessionHistoryState.RECOVERY_REQUIRED for item in records):
            return _owner_recovery(session_id, conversation_id, tail, owner)
    for records, owner in (
        (snapshot.commit_operations, SessionRecoveryOwner.STORY_COMMIT),
        (snapshot.checkpoint_operations, SessionRecoveryOwner.CHECKPOINT),
    ):
        if any(
            item.state
            in {GitSessionHistoryState.RUNNING, GitSessionHistoryState.OUTCOME_UNKNOWN}
            for item in records
        ):
            return _owner_outcome(session_id, conversation_id, tail, owner)
    return None


def _inspect_application(
    artifact_binding: SessionArtifactBinding,
    application: SessionArtifactApplicationEvidence,
    session_id: str,
    conversation_id: str,
    tail: int,
) -> SessionRecoveryResult | None:
    try:
        with artifact_binding.open_ready_resources() as access:
            stored = access.repository.inspect_artifact_application(
                application.application_result.application_id
            )
    except SessionArtifactBindingError:
        return _owner_recovery(
            session_id,
            conversation_id,
            tail,
            SessionRecoveryOwner.ARTIFACT_APPLICATION,
        )
    except ArtifactApplicationError as failure:
        failure_code = (
            SessionRecoveryFailureCode.OWNER_RECOVERY_REQUIRED
            if failure.code is ArtifactApplicationErrorCode.RECOVERY_REQUIRED
            else SessionRecoveryFailureCode.CORRELATION_MISMATCH
        )
        return _blocked(
            session_id,
            conversation_id,
            tail,
            SessionRecoveryState.RECOVERY_REQUIRED,
            SessionRecoveryOwner.ARTIFACT_APPLICATION,
            failure_code,
        )
    except Exception:
        return _owner_recovery(
            session_id,
            conversation_id,
            tail,
            SessionRecoveryOwner.ARTIFACT_APPLICATION,
        )
    if stored is None:
        return _blocked(
            session_id,
            conversation_id,
            tail,
            SessionRecoveryState.RECOVERY_REQUIRED,
            SessionRecoveryOwner.ARTIFACT_APPLICATION,
            SessionRecoveryFailureCode.MISSING_PREDECESSOR,
        )
    if not _exact_owner_value(stored, ArtifactApplicationResult) or stored != application.application_result:
        return _blocked(
            session_id,
            conversation_id,
            tail,
            SessionRecoveryState.RECOVERY_REQUIRED,
            SessionRecoveryOwner.ARTIFACT_APPLICATION,
            SessionRecoveryFailureCode.CORRELATION_MISMATCH,
        )
    return None


def _select_history(
    snapshot: GitSessionHistorySnapshot,
    resources: GitSessionResourceInspection,
    external: GitExternalOperationInspection,
    reviewed: SessionReviewedProposalEvidence | None,
    application: SessionArtifactApplicationEvidence | None,
) -> (
    tuple[
        GitSessionCandidateRecord | None,
        GitSessionCommitOperationRecord | None,
        GitSessionCheckpointOperationRecord | None,
    ]
    | tuple[SessionRecoveryOwner, SessionRecoveryFailureCode]
):
    correlation_id = (
        application.correlation_id
        if application is not None
        else reviewed.correlation_id if reviewed is not None else None
    )
    if correlation_id is None:
        return None, None, None

    related_candidates = tuple(
        candidate
        for candidate in snapshot.candidates
        if candidate.correlation_id == correlation_id
        or (
            application is not None
            and candidate.application_id == application.application_result.application_id
        )
    )
    if application is None and related_candidates:
        return SessionRecoveryOwner.STORY_COMMIT, SessionRecoveryFailureCode.MISSING_PREDECESSOR
    exact_candidates = ()
    if application is not None:
        exact_candidates = tuple(
            candidate
            for candidate in related_candidates
            if candidate.session_id == resources.identity.session_id
            and candidate.branch == resources.identity.branch
            and candidate.application_id == application.application_result.application_id
            and candidate.proposal_id == application.proposal_id
            and candidate.correlation_id == application.correlation_id
            and candidate.worktree_identity_sha256 == external.worktree_identity_digest
            and {item.path for item in candidate.paths}
            == set(application.application_result.affected_paths)
        )
    if len(exact_candidates) > 1 or (
        related_candidates and len(exact_candidates) != len(related_candidates)
    ):
        return SessionRecoveryOwner.STORY_COMMIT, SessionRecoveryFailureCode.CORRELATION_MISMATCH
    candidate = exact_candidates[0] if exact_candidates else None

    related_commits = tuple(
        operation
        for operation in snapshot.commit_operations
        if operation.correlation_id == correlation_id
        or (candidate is not None and operation.candidate_id == candidate.candidate_id)
    )
    if candidate is None and related_commits:
        return SessionRecoveryOwner.STORY_COMMIT, SessionRecoveryFailureCode.MISSING_PREDECESSOR
    exact_commits = tuple(
        operation
        for operation in related_commits
        if candidate is not None
        and operation.candidate_id == candidate.candidate_id
        and operation.correlation_id == correlation_id
    )
    if len(exact_commits) > 1 or len(exact_commits) != len(related_commits):
        return SessionRecoveryOwner.STORY_COMMIT, SessionRecoveryFailureCode.CORRELATION_MISMATCH
    commit = exact_commits[0] if exact_commits else None

    checkpoints = tuple(
        operation
        for operation in snapshot.checkpoint_operations
        if operation.session_id == resources.identity.session_id
        and operation.correlation_id == correlation_id
    )
    if len(checkpoints) > 1:
        return SessionRecoveryOwner.CHECKPOINT, SessionRecoveryFailureCode.CORRELATION_MISMATCH
    checkpoint = checkpoints[0] if checkpoints else None
    return candidate, commit, checkpoint


def _require_workspace_root(value: object) -> Path:
    invalid = False
    try:
        invalid = (
            type(value) is not type(Path())
            or not value.is_absolute()
            or value != Path(os.path.normpath(os.fspath(value)))
            or value.name in {"", ".", ".."}
            or any(character in os.fspath(value) for character in ("\x00", "\r", "\n"))
        )
        os.fspath(value).encode("utf-8", errors="strict")
    except (AttributeError, TypeError, UnicodeError, ValueError):
        invalid = True
    if invalid:
        _raise_input(SessionRecoveryFailureCode.UNSUPPORTED_SHAPE)
    return value


def _require_collaboration_store(value: object) -> CollaborationEventStore:
    invalid = isinstance(value, type)
    if not invalid:
        try:
            invalid = any(
                not callable(inspect.getattr_static(value, name))
                for name in ("append_batch", "read", "read_context")
            )
        except (AttributeError, TypeError):
            invalid = True
    if invalid:
        _raise_input(SessionRecoveryFailureCode.UNSUPPORTED_SHAPE)
    return value  # type: ignore[return-value]


def _require_session_identifier(value: object) -> str:
    if type(value) is not str or _SESSION_IDENTIFIER.fullmatch(value) is None:
        _raise_input(SessionRecoveryFailureCode.INVALID_REQUEST)
    return value


def _require_owner_input(value: object, expected_type: type):
    if type(value) is not expected_type:
        _raise_input(SessionRecoveryFailureCode.UNSUPPORTED_SHAPE)
    if not _exact_owner_value(value, expected_type):
        _raise_input(SessionRecoveryFailureCode.INVALID_REQUEST)
    return _deep_reconstruct(value)


def _require_optional_owner_input(value: object | None, expected_type: type):
    if value is None:
        return None
    return _require_owner_input(value, expected_type)


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


def _owner_recovery(
    session_id: str,
    conversation_id: str,
    tail: int,
    owner: SessionRecoveryOwner,
) -> SessionRecoveryResult:
    return _blocked(
        session_id,
        conversation_id,
        tail,
        SessionRecoveryState.RECOVERY_REQUIRED,
        owner,
        SessionRecoveryFailureCode.OWNER_RECOVERY_REQUIRED,
    )


def _owner_outcome(
    session_id: str,
    conversation_id: str,
    tail: int,
    owner: SessionRecoveryOwner,
) -> SessionRecoveryResult:
    return _blocked(
        session_id,
        conversation_id,
        tail,
        SessionRecoveryState.OUTCOME_UNKNOWN,
        owner,
        SessionRecoveryFailureCode.OWNER_OUTCOME_UNKNOWN,
    )


def _blocked(
    session_id: str,
    conversation_id: str,
    tail: int,
    state: SessionRecoveryState,
    owner: SessionRecoveryOwner,
    failure: SessionRecoveryFailureCode,
) -> SessionRecoveryResult:
    action = (
        SessionRecoveryNextAction.INSPECT_OWNER_OUTCOME
        if state is SessionRecoveryState.OUTCOME_UNKNOWN
        else SessionRecoveryNextAction.OPERATOR_RECOVERY
    )
    return SessionRecoveryResult(
        session_id=session_id,
        conversation_id=conversation_id,
        collaboration_tail_sequence=tail,
        state=state,
        next_action=action,
        owner=owner,
        failure_code=failure,
    )


def _raise_input(code: SessionRecoveryFailureCode) -> None:
    error = SessionRecoveryError(code)
    error.__cause__ = None
    error.__context__ = None
    raise error from None
