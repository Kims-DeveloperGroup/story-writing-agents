"""Reviewed artifact authority, Apply publication, and generation-pin handoff."""

from __future__ import annotations

from ..review_contracts import FinalReviewQualification
import hashlib
import uuid
from dataclasses import replace
from typing import (
    Callable,
    cast,
)
from ..conversations.session_context import SessionHistorySnapshot
from ..artifact_proposals import ArtifactReviewCandidate
from ..conversation_storage.contracts import ConversationStorageServices
from ..conversations.events import CollaborationExplorationAdoptionPayload
from ..git.story_commit_contracts import StoryCommitApplicationEvidence
from ..errors import (
    BackendProtocolError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from ..workspace import Workspace
from ..workspace.initialization import (
    SessionArtifactBinding,
    SessionArtifactBindingError,
    SessionArtifactGenerationPin,
    compose_session_artifact_binding,
    compose_story_commit_service,
)
from ..writing_artifacts import (
    ArtifactApplicationError,
    ArtifactApplicationErrorCode,
    ArtifactChangeSetError,
    ArtifactReviewReason,
    build_artifact_application_request,
    project_artifact_change_set,
    serialize_artifact_application_request,
    validate_artifact_relationships,
)
from ..runtime_resources import collaboration_event_journal as event_journal
from ..runtime_resources.session_execution_scopes import SessionExecutionScopes
from ..conversations import (
    ArtifactApplicationDecision,
    ArtifactApplicationDecisionKind,
    ArtifactApplicationDecisionPayload,
    ArtifactApplicationDecisionResult,
    ArtifactApplicationNextAction,
    ArtifactApplicationOutcomeKind,
    ArtifactApplicationRequestPayload,
    ArtifactApplicationResultPayload,
    ArtifactProposalResult,
    ArtifactProposalState,
    ArtifactReviewCandidatePayload,
    ArtifactReviewDecision,
    ArtifactReviewFailureCode,
    ArtifactReviewFailurePayload,
    ArtifactReviewReply,
    ArtifactReviewSynthesisPayload,
    ArtifactReviewedProposal,
    CollaborationActor,
    CollaborationActorKind,
    CollaborationAppendStatus,
    CollaborationEvent,
    CollaborationEventDraft,
    CollaborationEventKind,
    CollaborationExecutionOutcomeKind,
    CollaborationExecutionResult,
    CollaborationHistoryProjection,
    CollaborationInvocationKind,
    CollaborationMessagePayload,
    CollaborationNextAction,
    CollaborationProposal,
    CollaborationProviderCallPayload,
    CollaborationSideEffect,
    CollaborationSpecialistReplyPayload,
    CollaborationStepState,
    LeadOutcomeKind,
    LeadTurnResult,
    StoryCommitRequestPayload,
    build_final_review_evidence,
    build_story_commit_request_payload,
    project_collaboration_history,
)
from ..lead_preparation import authoring_authority
from .pending_story_commits import PendingStoryCommits
from .prepared_story_commit import PreparedStoryCommit
from ..lead_control_events import (
    effective_lead_control_events,
    terminal_state_draft,
)
from ..runtime_resources.generation_pin_cleanup import close_untransferred_generation_pin


class _ArtifactApplicationCapability:
    """Process-local, nonserializable, single-use artifact write authority."""

    __slots__ = ("_consumed", "_secret", "request_digest", "tail_event_id")

    def __init__(self, request_digest: str, tail_event_id: str) -> None:
        self.request_digest = request_digest
        self.tail_event_id = tail_event_id
        self._secret = bytearray(uuid.uuid4().bytes + uuid.uuid4().bytes)
        self._consumed = False

    def __repr__(self) -> str:
        return "_ArtifactApplicationCapability(<content hidden>)"

    def __reduce__(self) -> object:
        raise TypeError("artifact-application capability cannot be serialized")

    def consume(self) -> None:
        if self._consumed:
            raise BackendProtocolError("artifact-application capability is consumed")
        self._consumed = True
        for index in range(len(self._secret)):
            self._secret[index] = 0


class ArtifactApplicationAuthority:
    """Own exact reviewed-request authority and its Apply publication lifecycle."""

    def __init__(
        self,
        workspace: Workspace,
        conversation_storage_services: ConversationStorageServices,
        session_execution_scopes: SessionExecutionScopes,
    ) -> None:
        self.workspace = workspace
        self.conversation_storage_services = conversation_storage_services
        self._session_execution_scopes = session_execution_scopes
        self._artifact_application_capabilities: dict[
            tuple[str, str, str], _ArtifactApplicationCapability
        ] = {}

    async def decide_artifact_application(
        self,
        decision: ArtifactApplicationDecision,
        *,
        pending_commits: PendingStoryCommits,
        validate_lead_identity: Callable[[str, str], None],
        capture_editorial_snapshot: Callable[[str], SessionHistorySnapshot],
        validate_exact_candidate_reviews: Callable[[str, CollaborationProposal, ArtifactReviewCandidate, tuple[CollaborationEvent, ...]], None],
        qualify_artifact_final_review: Callable[..., FinalReviewQualification],
    ) -> ArtifactApplicationDecisionResult:
        """Consume one exact structured decision for the current reviewed request."""

        if type(decision) is not ArtifactApplicationDecision:
            raise TypeError("decision must be an ArtifactApplicationDecision")
        validate_lead_identity(decision.session_id, decision.conversation_id)
        lock = self._session_execution_scopes.conversation_lock(decision.session_id, decision.conversation_id)
        async with lock:
            events = self.conversation_storage_services.event_store.read(
                decision.session_id, decision.conversation_id
            )
            if any(type(event.payload) is CollaborationExplorationAdoptionPayload for event in events):
                capture_editorial_snapshot(decision.session_id).validate_exploration_adoptions(decision.conversation_id)
            projection = project_collaboration_history(events)
            control_events = effective_lead_control_events(events)
            if (projection.next_safe_action is CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
                    and any(type(event.payload) is CollaborationProviderCallPayload
                            and event.payload.invocation_kind is CollaborationInvocationKind.LEAD_DISCUSSION
                            for event in events)):
                raise ValueError("unresolved discussion allows inspection only")
            request = projection.current_artifact_application_request
            if (
                request is None
                or projection.current_artifact_application_result is not None
                or not events
                or type(control_events[-1].payload) is not ArtifactApplicationRequestPayload
                or control_events[-1].payload != request
            ):
                raise ValueError(
                    "artifact application has no authoritative request tail"
                )
            reviewed = request.reviewed_proposal
            application = request.application_request
            exact = (
                decision.session_id == control_events[-1].session_id
                and decision.conversation_id == control_events[-1].conversation_id
                and decision.step_id == control_events[-1].step_id == reviewed.step_id
                and decision.confirmation_request_id == request.confirmation_request_id
                and decision.reviewed_proposal_id == reviewed.proposal_id
                and decision.reviewed_proposal_revision == reviewed.proposal_revision
                and decision.reviewed_proposal_digest == reviewed.proposal_digest
                and decision.candidate_id == application.candidate_id
                and decision.candidate_revision == application.candidate_revision
                and decision.candidate_digest == application.candidate_digest
                and decision.application_id == application.application_id
                and decision.application_request_digest
                == request.application_request_digest
            )
            if not exact:
                raise ValueError("artifact application decision does not match tail")

            decision_event_id = event_journal.new_id("e")
            decision_payload = ArtifactApplicationDecisionPayload(
                confirmation_request_id=request.confirmation_request_id,
                confirmation_request_event_id=control_events[-1].event_id,
                reviewed_proposal_id=reviewed.proposal_id,
                reviewed_proposal_revision=reviewed.proposal_revision,
                reviewed_proposal_digest=reviewed.proposal_digest,
                candidate_id=application.candidate_id,
                candidate_revision=application.candidate_revision,
                candidate_digest=application.candidate_digest,
                application_id=application.application_id,
                application_request_digest=request.application_request_digest,
                decision=decision.kind,
                revision_request=decision.revision_request,
            )
            event_journal.append_drafts(
                projection.last_sequence,
                (event_journal.draft_with_id(
                    event_id=decision_event_id,
                    session_id=decision.session_id,
                    conversation_id=decision.conversation_id,
                    turn_id=event_journal.new_id("t"),
                    step_id=decision.step_id,
                    kind=CollaborationEventKind.ARTIFACT_APPLICATION_DECISION,
                    sender=event_journal.USER_ACTOR,
                    recipient=event_journal.RUNTIME_ACTOR,
                    payload=decision_payload,
                ),),
                append_batch=self.append_collaboration_event_batch,
            )

            outcome: ArtifactApplicationOutcomeKind
            next_action: ArtifactApplicationNextAction
            application_result = None
            artifact_binding: SessionArtifactBinding | None = None
            generation_pin: SessionArtifactGenerationPin | None = None
            key = (decision.session_id, decision.conversation_id, decision.step_id)
            if decision.kind is ArtifactApplicationDecisionKind.REJECT:
                self._artifact_application_capabilities.pop(key, None)
                outcome = ArtifactApplicationOutcomeKind.REJECTED
                next_action = ArtifactApplicationNextAction.CONTINUE_WITH_LEAD
                message = (
                    "The reviewed artifact application was rejected. "
                    "No artifact or Git state changed."
                )
            elif decision.kind is ArtifactApplicationDecisionKind.REVISE:
                self._artifact_application_capabilities.pop(key, None)
                outcome = ArtifactApplicationOutcomeKind.SUPERSEDED
                next_action = ArtifactApplicationNextAction.CONTINUE_WITH_LEAD
                message = (
                    "The reviewed artifact application was superseded for revision. "
                    "No artifact or Git state changed."
                )
            else:
                capability = self._artifact_application_capabilities.pop(key, None)
                if capability is None:
                    outcome = ArtifactApplicationOutcomeKind.STALE
                    next_action = (
                        ArtifactApplicationNextAction.REGENERATE_AND_REVIEW
                    )
                    message = (
                        "This runtime did not produce the reviewed application "
                        "request. No write occurred; regenerate and review it."
                    )
                else:
                    persisted = self.conversation_storage_services.event_store.read(
                        decision.session_id, decision.conversation_id
                    )
                    current = project_collaboration_history(persisted)
                    tail = persisted[-1]
                    authority_matches = not (
                        current.current_artifact_application_request != request
                        or current.current_artifact_application_result is not None
                        or type(tail.payload) is not ArtifactApplicationDecisionPayload
                        or tail.payload != decision_payload
                        or capability.tail_event_id
                        != decision_payload.confirmation_request_event_id
                        or capability.request_digest
                        != request.application_request_digest
                    )
                    capability.consume()
                    if not authority_matches:
                        outcome = ArtifactApplicationOutcomeKind.STALE
                        next_action = (
                            ArtifactApplicationNextAction.REGENERATE_AND_REVIEW
                        )
                        message = (
                            "The reviewed artifact application authority drifted. "
                            "No write occurred; regenerate and review it."
                        )
                    else:
                        repository_execution_started = False
                        try:
                            consultation = next((event.payload for event in persisted
                                if type(event.payload) is CollaborationProposal
                                and event.payload.proposal_id == reviewed.consultation_proposal_id
                                and event.payload.proposal_revision == reviewed.consultation_proposal_revision
                                and event.payload.proposal_digest == reviewed.consultation_proposal_digest), None)
                            if consultation is None:
                                raise ArtifactApplicationError(ArtifactApplicationErrorCode.CORRELATION_MISMATCH)
                            try:
                                validate_exact_candidate_reviews(
                                    decision.session_id,
                                    consultation,
                                    reviewed.candidate,
                                    persisted,
                                )
                            except (ValueError, BackendProtocolError) as exc:
                                raise ArtifactApplicationError(ArtifactApplicationErrorCode.CORRELATION_MISMATCH) from exc
                            qualification = qualify_artifact_final_review(
                                session_id=decision.session_id, reviewed_proposal=reviewed,
                                consultation=consultation, events=persisted)
                            if not qualification.eligible:
                                raise ArtifactApplicationError(ArtifactApplicationErrorCode.CORRELATION_MISMATCH)
                            reviewed.__post_init__()
                            reviewed.candidate.__post_init__()
                            artifact_binding = compose_session_artifact_binding(
                                self.workspace.root,
                                decision.session_id
                            )
                            generation_pin = artifact_binding.acquire_generation_pin()
                            with artifact_binding.open_ready_resources() as access:
                                artifacts = access.repository
                                snapshots = artifacts.capture_complete_state()
                                base_state = {
                                    item.path: item.content for item in snapshots
                                }
                                project_artifact_change_set(
                                    base_state, application.change_set
                                )
                                relationship = validate_artifact_relationships(
                                    base_state,
                                    application.change_set,
                                    attached_core_paths=(
                                        application.attached_core_paths
                                    ),
                                )
                                if relationship != (
                                    reviewed.candidate.relationship_validation
                                ):
                                    raise ArtifactApplicationError(
                                        ArtifactApplicationErrorCode.DRIFT
                                    )
                                access.require_ready()
                                repository_execution_started = True
                                application_result = (
                                    artifacts.apply_artifact_changes(application)
                                )
                        except (
                            SessionArtifactBindingError,
                            WorkspaceFormatError,
                            WorkspaceSecurityError,
                        ):
                            if repository_execution_started:
                                application_result = None
                                outcome = ArtifactApplicationOutcomeKind.OUTCOME_UNKNOWN
                                next_action = (
                                    ArtifactApplicationNextAction.INSPECT_AND_RESOLVE_OUTCOME
                                )
                                message = (
                                    "The artifact application outcome is uncertain. "
                                    "Inspect the exact application before any new write."
                                )
                            else:
                                outcome = ArtifactApplicationOutcomeKind.RECOVERY_REQUIRED
                                next_action = (
                                    ArtifactApplicationNextAction.OPERATOR_RECOVERY
                                )
                                message = (
                                    "The exact session artifact resources are not ready. "
                                    "Repository execution did not start; operator recovery "
                                    "is required."
                                )
                        except ArtifactChangeSetError:
                            outcome = ArtifactApplicationOutcomeKind.STALE
                            next_action = (
                                ArtifactApplicationNextAction.REGENERATE_AND_REVIEW
                            )
                            message = (
                                "The reviewed artifact application is stale. No "
                                "application was started; regenerate and review it."
                            )
                        except ArtifactApplicationError as exc:
                            if exc.code is ArtifactApplicationErrorCode.RECOVERY_REQUIRED:
                                outcome = (
                                    ArtifactApplicationOutcomeKind.RECOVERY_REQUIRED
                                )
                                next_action = (
                                    ArtifactApplicationNextAction.OPERATOR_RECOVERY
                                )
                                message = (
                                    "Artifact application recovery is required. "
                                    "Do not retry the write."
                                )
                            elif repository_execution_started:
                                outcome = (
                                    ArtifactApplicationOutcomeKind.OUTCOME_UNKNOWN
                                )
                                next_action = (
                                    ArtifactApplicationNextAction.INSPECT_AND_RESOLVE_OUTCOME
                                )
                                message = (
                                    "The artifact application outcome is uncertain. "
                                    "Inspect the exact application before any new write."
                                )
                            elif exc.code in {
                                ArtifactApplicationErrorCode.DRIFT,
                                ArtifactApplicationErrorCode.CORRELATION_MISMATCH,
                            }:
                                outcome = ArtifactApplicationOutcomeKind.STALE
                                next_action = (
                                    ArtifactApplicationNextAction.REGENERATE_AND_REVIEW
                                )
                                message = (
                                    "The reviewed artifact application drifted. No "
                                    "new write was authorized; regenerate and review it."
                                )
                            else:
                                outcome = (
                                    ArtifactApplicationOutcomeKind.OUTCOME_UNKNOWN
                                )
                                next_action = (
                                    ArtifactApplicationNextAction.INSPECT_AND_RESOLVE_OUTCOME
                                )
                                message = (
                                    "The artifact application outcome is uncertain. "
                                    "Inspect the exact application before any new write."
                                )
                        except BaseException as failure:
                            if generation_pin is not None:
                                close_untransferred_generation_pin(
                                    generation_pin,
                                    failure,
                                )
                                generation_pin = None
                            raise
                        else:
                            outcome = ArtifactApplicationOutcomeKind(
                                application_result.state.value
                            )
                            next_action = (
                                ArtifactApplicationNextAction.REQUEST_COMMIT_CONFIRMATION
                            )
                            message = (
                                "The exact reviewed artifact changes were applied "
                                "atomically. No Git staging or commit occurred."
                            )

            commit_request: StoryCommitRequestPayload | None = None
            pending_commit: PreparedStoryCommit | None = None
            result_persisted = False
            try:
                post_decision = self.conversation_storage_services.event_store.read(
                    decision.session_id, decision.conversation_id
                )
                if (
                    not post_decision
                    or post_decision[-1].event_id != decision_event_id
                    or post_decision[-1].payload != decision_payload
                ):
                    raise ValueError("artifact application decision tail changed")
                result_event_id = event_journal.new_id("e")
                if application_result is not None:
                    assert artifact_binding is not None
                    assert generation_pin is not None
                    try:
                        service = compose_story_commit_service(
                            self.workspace.root,
                            decision.session_id,
                            artifact_binding,
                        )
                        candidate = service.prepare(
                            StoryCommitApplicationEvidence(
                                session_id=decision.session_id,
                                application_id=application.application_id,
                                proposal_id=reviewed.proposal_id,
                                correlation_id=request.application_request_digest,
                                change_set=application.change_set,
                                applied_state_digest=(
                                    application.change_set.projected_state_digest
                                ),
                            ),
                            proposed_message="Apply reviewed artifact changes",
                            impact_boundary=(
                                f"commit {len(application_result.affected_paths)} "
                                "reviewed artifact paths",
                            ),
                        )
                        with artifact_binding.open_ready_resources():
                            commit_request = build_story_commit_request_payload(
                                confirmation_request_id=event_journal.new_id("g"),
                                application_result_event_id=result_event_id,
                                candidate=candidate,
                            )
                        pending_commit = PreparedStoryCommit(
                            commit_request, service, generation_pin
                        )
                        generation_pin = None
                    except Exception:
                        next_action = (
                            ArtifactApplicationNextAction.REPREPARE_COMMIT_REQUEST
                        )
                        message = (
                            "The artifact result is known, but an exact commit "
                            "request could not be prepared. Reprepare it before "
                            "a separate Commit decision."
                        )
                result_payload = ArtifactApplicationResultPayload(
                    confirmation_request_id=request.confirmation_request_id,
                    decision_event_id=decision_event_id,
                    reviewed_proposal_id=reviewed.proposal_id,
                    reviewed_proposal_digest=reviewed.proposal_digest,
                    application_id=application.application_id,
                    application_request_digest=request.application_request_digest,
                    outcome=outcome,
                    next_action=next_action,
                    result=application_result,
                )
                result_draft = event_journal.draft_with_id(
                    event_id=result_event_id,
                    session_id=decision.session_id,
                    conversation_id=decision.conversation_id,
                    turn_id=post_decision[-1].turn_id,
                    step_id=decision.step_id,
                    kind=CollaborationEventKind.ARTIFACT_APPLICATION_RESULT,
                    sender=CollaborationActor(CollaborationActorKind.ARTIFACT),
                    recipient=event_journal.USER_ACTOR,
                    payload=result_payload,
                )

                def append_verified(drafts: tuple[CollaborationEventDraft, ...]) -> bool | None:
                    """Resolve one CAS append by exact batch readback on error."""
                    batch_id = event_journal.new_id("b")
                    try:
                        event_journal.append_drafts(
                            post_decision[-1].sequence, drafts, batch_id=batch_id,
                                                                append_batch=self.append_collaboration_event_batch,
                        )
                        return True
                    except Exception:
                        try:
                            observed = self.conversation_storage_services.event_store.read(
                                decision.session_id, decision.conversation_id
                            )
                        except Exception:
                            return None
                        expected_sequence = post_decision[-1].sequence
                        if (
                            len(observed) == expected_sequence + len(drafts)
                            and observed[:expected_sequence] == post_decision
                            and all(
                                stored.batch_id == batch_id
                                and stored.batch_ordinal == ordinal
                                and stored.batch_size == len(drafts)
                                and stored.sequence == expected_sequence + ordinal + 1
                                and stored.to_draft() == draft
                                for ordinal, (stored, draft) in enumerate(
                                    zip(observed[expected_sequence:], drafts, strict=True)
                                )
                            )
                        ):
                            return True
                        if (
                            observed == post_decision
                        ):
                            return False
                        return None

                drafts = (result_draft,)
                if commit_request is not None:
                    drafts += (
                        event_journal.draft(
                            session_id=decision.session_id,
                            conversation_id=decision.conversation_id,
                            turn_id=post_decision[-1].turn_id,
                            step_id=decision.step_id,
                            kind=CollaborationEventKind.STORY_COMMIT_REQUEST,
                            sender=event_journal.LEAD_ACTOR,
                            recipient=event_journal.USER_ACTOR,
                            payload=commit_request,
                        ),
                    )
                published = append_verified(drafts)
                if published is False and commit_request is not None:
                    assert pending_commit is not None
                    pending_commit.close()
                    pending_commit = None
                    commit_request = None
                    result_payload = replace(
                        result_payload,
                        next_action=(
                            ArtifactApplicationNextAction.REPREPARE_COMMIT_REQUEST
                        ),
                    )
                    message = (
                        "The artifact result is known, but the exact commit "
                        "request was not published. Reprepare it before Commit."
                    )
                    published = append_verified(
                        (replace(result_draft, payload=result_payload),)
                    )
                if published is not True:
                    raise RuntimeError("artifact handoff publication is unproved")
                result_persisted = True
                pending_commit = pending_commits.replace_after_application(
                    key, pending_commit
                )
            except BaseException as failure:
                if pending_commit is not None:
                    pending_commit.close()
                if generation_pin is not None:
                    close_untransferred_generation_pin(
                        generation_pin, failure
                    )
                    generation_pin = None
                if not isinstance(failure, Exception):
                    raise
                if result_persisted:
                    message = (
                        "The exact application result is durable, but its "
                        "process-local commit preparation is unavailable. "
                        "Inspect the exact request before Commit."
                    )
                else:
                    commit_request = None
                    result_payload = ArtifactApplicationResultPayload(
                        confirmation_request_id=request.confirmation_request_id,
                        decision_event_id=decision_event_id,
                        reviewed_proposal_id=reviewed.proposal_id,
                        reviewed_proposal_digest=reviewed.proposal_digest,
                        application_id=application.application_id,
                        application_request_digest=request.application_request_digest,
                        outcome=ArtifactApplicationOutcomeKind.OUTCOME_UNKNOWN,
                        next_action=(
                            ArtifactApplicationNextAction.INSPECT_AND_RESOLVE_OUTCOME
                        ),
                    )
                    message = (
                        "The decision is durable but its visible result could not be "
                        "verified. Inspect the exact application; do not retry it."
                    )
            else:
                if generation_pin is not None:
                    generation_pin.close()
                    generation_pin = None
            return ArtifactApplicationDecisionResult(
                request=request,
                decision=decision.kind,
                outcome=result_payload,
                message=message,
                commit_request=commit_request,
            )

    def finalize_artifact_result_locked(
        self,
        *,
        session_id: str,
        events: tuple[CollaborationEvent, ...],
        projection: CollaborationHistoryProjection,
        proposal: CollaborationProposal,
        result: CollaborationExecutionResult,
        result_event_id: str,
        candidate: ArtifactReviewCandidatePayload,
        turn_id: str,
        validation_details: tuple[str, ...],
        validate_exact_candidate_reviews: Callable[[str, CollaborationProposal, ArtifactReviewCandidate, tuple[CollaborationEvent, ...]], None],
        qualify_artifact_final_review: Callable[..., FinalReviewQualification],
        attach_document_plan: Callable[..., LeadTurnResult],
    ) -> LeadTurnResult:
        """Finalize known review facts without granting artifact-write authority."""

        if not events or any(event.session_id != session_id for event in events):
            raise BackendProtocolError(
                "artifact finalization history has cross-session events"
            )
        if projection.session_id not in {None, session_id}:
            raise BackendProtocolError(
                "artifact finalization projection has cross-session authority"
            )

        artifact_synthesis_event = next(
            (
                event
                for event in events
                if type(event.payload) is ArtifactReviewSynthesisPayload
                and event.payload.execution_attempt_id == result.execution_attempt_id
                and event.payload.candidate_digest
                == candidate.candidate.candidate_digest
            ),
            None,
        )
        artifact_synthesis = (
            None
            if artifact_synthesis_event is None
            else cast(ArtifactReviewSynthesisPayload, artifact_synthesis_event.payload)
        )
        state = ArtifactProposalState.FAILED
        artifact_recovery_required = False
        reviewed_proposal: ArtifactReviewedProposal | None = None
        application_payload: ArtifactApplicationRequestPayload | None = None
        message = (
            "Artifact review did not complete. Every knowable reviewer result was "
            "preserved and no artifact was changed."
        )
        if (
            result.overall_outcome is CollaborationExecutionOutcomeKind.SUCCEEDED
            and artifact_synthesis is not None
        ):
            message = artifact_synthesis.message
            state = {
                ArtifactReviewDecision.PROPOSE: ArtifactProposalState.REVIEWED,
                ArtifactReviewDecision.REVISE: ArtifactProposalState.REVISION_REQUIRED,
                ArtifactReviewDecision.REJECT: ArtifactProposalState.REJECTED,
            }[artifact_synthesis.decision]
            if state is ArtifactProposalState.REVIEWED:
                try:
                    validate_exact_candidate_reviews(
                        session_id,
                        proposal,
                        candidate.candidate,
                        events,
                    )
                    artifact_binding = compose_session_artifact_binding(self.workspace.root, session_id)
                    with artifact_binding.open_ready_resources() as access:
                        snapshots = access.repository.capture_complete_state()
                        project_artifact_change_set(
                            {item.path: item.content for item in snapshots},
                            candidate.candidate.change_set,
                        )
                except (
                    SessionArtifactBindingError,
                    WorkspaceFormatError,
                    WorkspaceSecurityError,
                ):
                    artifact_recovery_required = True
                    state = ArtifactProposalState.RECOVERY_REQUIRED
                    message = (
                        "Artifact review results are durable, but the exact session "
                        "artifact resources were lost before finalization. Repair "
                        "them before continuing; provider work will not be replayed."
                    )
                except (ArtifactChangeSetError, ValueError, BackendProtocolError):
                    state = ArtifactProposalState.STALE
                    message = (
                        "The reviewed candidate became stale before finalization. "
                        "No artifact was changed; request a new candidate."
                    )
                else:
                    reply_events = tuple(
                        next(
                            event
                            for event in events
                            if event.event_id == outcome.reply_event_id
                            and type(event.payload)
                            is CollaborationSpecialistReplyPayload
                        )
                        for outcome in result.assignment_outcomes
                    )
                    reviews = tuple(
                        ArtifactReviewReply(
                            specialist_id=cast(str, event.sender.agent_id),
                            assignment_id=cast(
                                CollaborationSpecialistReplyPayload,
                                event.payload,
                            ).assignment_id,
                            reply_event_id=event.event_id,
                            assessment=cast(
                                CollaborationSpecialistReplyPayload,
                                event.payload,
                            ).assessment,
                        )
                        for event in reply_events
                    )
                    candidate_event = next(
                        event
                        for event in events
                        if type(event.payload) is ArtifactReviewCandidatePayload
                        and event.payload == candidate
                    )
                    assert artifact_synthesis_event is not None
                    reviewed_proposal = ArtifactReviewedProposal.create(
                        proposal_id=event_journal.deterministic_id(
                            "ap", candidate.candidate.candidate_id, "reviewed"
                        ),
                        proposal_revision=1,
                        candidate=candidate.candidate,
                        session_id=events[-1].session_id,
                        conversation_id=events[-1].conversation_id,
                        turn_id=turn_id,
                        step_id=proposal.step_id,
                        base_tail_sequence=candidate_event.sequence,
                        reviewed_tail_sequence=artifact_synthesis_event.sequence,
                        lead_judgment_event_id=candidate.lead_judgment_event_id,
                        synthesis_event_id=artifact_synthesis_event.event_id,
                        consultation_proposal_id=proposal.proposal_id,
                        consultation_proposal_revision=proposal.proposal_revision,
                        consultation_proposal_digest=proposal.proposal_digest,
                        mandatory_specialist_ids=candidate.mandatory_specialist_ids,
                        disclosed_specialist_ids=candidate.disclosed_specialist_ids,
                        reviews=reviews,
                        unresolved_input_ids=artifact_synthesis.unresolved_input_ids,
                        rationale=artifact_synthesis.rationale,
                        user_impact=artifact_synthesis.user_impact,
                        limitations=artifact_synthesis.limitations,
                        grounding_evidence_ids=(
                            candidate.candidate.grounding_evidence_ids
                        ),
                     final_review_evidence=build_final_review_evidence(candidate.candidate, proposal, reviews))
                    qualification = qualify_artifact_final_review(
                        session_id=events[-1].session_id, reviewed_proposal=reviewed_proposal,
                        consultation=proposal, events=events)
                    if not qualification.eligible:
                        reviewed_proposal = None
                        state = ArtifactProposalState.STALE
                        message = "The final review basis is stale; no application authority was prepared."
                    else:
                        attached_core_paths = tuple(
                            sorted(
                                {
                                    fact.artifact_path
                                    for fact in (
                                        candidate.candidate.relationship_validation.review_facts
                                    )
                                    if fact.reason is ArtifactReviewReason.ATTACHED_CORE_EVIDENCE
                                },
                                key=lambda path: path.encode("utf-8"),
                            )
                        )
                        application_request = build_artifact_application_request(
                            application_id=event_journal.deterministic_id(
                                "aa", reviewed_proposal.proposal_id,
                                reviewed_proposal.proposal_digest,
                            ),
                            candidate_id=candidate.candidate.candidate_id,
                            candidate_revision=candidate.candidate.candidate_revision,
                            candidate_digest=candidate.candidate.candidate_digest,
                            change_set=candidate.candidate.change_set,
                            relationship_validation=(
                                candidate.candidate.relationship_validation
                            ),
                            attached_core_paths=attached_core_paths,
                        )
                        application_payload = ArtifactApplicationRequestPayload(
                            confirmation_request_id=event_journal.deterministic_id(
                                "ar", reviewed_proposal.proposal_id,
                                reviewed_proposal.proposal_digest,
                            ),
                            reviewed_proposal=reviewed_proposal,
                            application_request=application_request,
                            application_request_digest=(
                                "sha256:"
                                + hashlib.sha256(
                                    serialize_artifact_application_request(
                                        application_request
                                    )
                                ).hexdigest()
                            ),
                            base_tail_sequence=projection.last_sequence,
                            choices=(
                                ArtifactApplicationDecisionKind.APPLY,
                                ArtifactApplicationDecisionKind.REJECT,
                                ArtifactApplicationDecisionKind.REVISE,
                            ),
                            side_effects=(CollaborationSideEffect.ARTIFACT_MUTATION,),
                            prohibited_side_effects=(
                                CollaborationSideEffect.GIT_INDEX_MUTATION,
                                CollaborationSideEffect.GIT_COMMIT,
                                CollaborationSideEffect.GIT_REMOTE_MUTATION,
                                CollaborationSideEffect.GIT_MERGE,
                            ),
                        )

        final_drafts: list[CollaborationEventDraft] = [
            event_journal.draft_with_id(
                event_id=event_journal.deterministic_id("e", result_event_id, "notice"),
                session_id=events[-1].session_id,
                conversation_id=events[-1].conversation_id,
                turn_id=turn_id,
                step_id=proposal.step_id,
                kind=CollaborationEventKind.MESSAGE,
                sender=event_journal.LEAD_ACTOR,
                recipient=event_journal.USER_ACTOR,
                payload=CollaborationMessagePayload(message, provider_invocation_id=None if artifact_synthesis is None else artifact_synthesis.provider_invocation_id),
            )
        ]
        if reviewed_proposal is not None:
            final_drafts.append(
                event_journal.draft_with_id(
                    event_id=event_journal.deterministic_id(
                        "e", result_event_id, "reviewed_artifact"
                    ),
                    session_id=events[-1].session_id,
                    conversation_id=events[-1].conversation_id,
                    turn_id=turn_id,
                    step_id=proposal.step_id,
                    kind=CollaborationEventKind.ARTIFACT_REVIEWED_PROPOSAL,
                    sender=event_journal.RUNTIME_ACTOR,
                    recipient=event_journal.USER_ACTOR,
                    payload=reviewed_proposal,
                )
            )
        elif state is ArtifactProposalState.FAILED:
            final_drafts.append(
                event_journal.draft_with_id(
                    event_id=event_journal.deterministic_id(
                        "e", result_event_id, "artifact_failure"
                    ),
                    session_id=events[-1].session_id,
                    conversation_id=events[-1].conversation_id,
                    turn_id=turn_id,
                    step_id=proposal.step_id,
                    kind=CollaborationEventKind.ARTIFACT_REVIEW_FAILURE,
                    sender=event_journal.RUNTIME_ACTOR,
                    recipient=event_journal.USER_ACTOR,
                    payload=ArtifactReviewFailurePayload(
                        code=ArtifactReviewFailureCode.REVIEW_INCOMPLETE,
                        candidate_id=candidate.candidate.candidate_id,
                        candidate_digest=candidate.candidate.candidate_digest,
                    ),
                )
            )
        final_drafts.append(
            event_journal.draft_with_id(
                event_id=event_journal.deterministic_id(
                    "e", result_event_id, "artifact_state", state.value
                ),
                session_id=events[-1].session_id,
                conversation_id=events[-1].conversation_id,
                turn_id=turn_id,
                step_id=proposal.step_id,
                kind=CollaborationEventKind.ARTIFACT_PROPOSAL_STATE,
                sender=event_journal.RUNTIME_ACTOR,
                recipient=event_journal.USER_ACTOR,
                payload=authoring_authority.artifact_state_payload(
                    candidate,
                    state,
                    execution_attempt_id=result.execution_attempt_id,
                    reviewed_proposal=reviewed_proposal,
                ),
            )
        )
        core_state = (
            CollaborationStepState.RECOVERY_REQUIRED
            if artifact_recovery_required
            else {
                CollaborationExecutionOutcomeKind.SUCCEEDED: CollaborationStepState.COMPLETED,
                CollaborationExecutionOutcomeKind.FAILED: CollaborationStepState.FAILED,
                CollaborationExecutionOutcomeKind.CANCELLED: CollaborationStepState.INTERRUPTED,
            }[result.overall_outcome]
        )
        final_drafts.append(
            terminal_state_draft(
                events=events,
                turn_id=turn_id,
                proposal=proposal,
                result_event_id=result_event_id,
                state=core_state,
            )
        )
        if application_payload is not None:
            final_drafts.append(
                event_journal.draft_with_id(
                    event_id=event_journal.deterministic_id(
                        "e", result_event_id, "artifact_application_request"
                    ),
                    session_id=events[-1].session_id,
                    conversation_id=events[-1].conversation_id,
                    turn_id=turn_id,
                    step_id=proposal.step_id,
                    kind=CollaborationEventKind.ARTIFACT_APPLICATION_REQUEST,
                    sender=event_journal.LEAD_ACTOR,
                    recipient=event_journal.USER_ACTOR,
                    payload=application_payload,
                )
            )
        append_receipt = event_journal.append_drafts(
            projection.last_sequence,
            tuple(final_drafts),
            batch_id=event_journal.deterministic_id("b", result_event_id, "finalize"),
            append_batch=self.append_collaboration_event_batch,
        )
        if (
            application_payload is not None
            and append_receipt.status is CollaborationAppendStatus.APPENDED
        ):
            request_event = final_drafts[-1]
            self._artifact_application_capabilities[
                (
                    request_event.session_id,
                    request_event.conversation_id,
                    request_event.step_id,
                )
            ] = _ArtifactApplicationCapability(
                application_payload.application_request_digest,
                request_event.event_id,
            )
        elif artifact_recovery_required:
            self._artifact_application_capabilities.pop(
                (
                    events[-1].session_id,
                    events[-1].conversation_id,
                    proposal.step_id,
                ),
                None,
            )
        turn = LeadTurnResult(
            events[-1].session_id,
            events[-1].conversation_id,
            turn_id,
            proposal.step_id,
            LeadOutcomeKind.RECOMMENDATION,
            message,
            None,
            core_state,
            None,
            (
                CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY
                if artifact_recovery_required
                else CollaborationNextAction.SUBMIT_APPLICATION_DECISION
                if application_payload is not None
                else CollaborationNextAction.CONTINUE_WITH_LEAD
            ),
            None,
            ArtifactProposalResult(state, candidate, reviewed_proposal),
            application_payload,
            validation_details=validation_details,
            provider_invocation_id=None if artifact_synthesis is None else artifact_synthesis.provider_invocation_id,
        )
        return (attach_document_plan(turn, events, projection)
                if reviewed_proposal is not None and proposal.document_node is not None else turn)
