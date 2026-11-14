"""Exact Lead decision flow and scoped execution admission."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from collections.abc import (
    Callable,
    Coroutine,
)

from ..agent_context.contracts.events import validate_context_event_sink
from ..agent_context.invocation.context_execution import AgentContextService
from ..agent_context.progress import ContextActionProgressSink
from ..blocking_work import run_blocking
from ..collaboration_execution_results import (
    CollaborationInvocationResult,
    blocking_lead_result,
)
from ..conversation_storage.contracts import ConversationStorageServices
from ..conversations import (
    ArtifactProposalResult,
    ArtifactProposalState,
    CollaborationActorKind,
    CollaborationAppendReceipt,
    CollaborationConfirmationDecision,
    CollaborationConfirmationPayload,
    CollaborationEvent,
    CollaborationEventBatch,
    CollaborationEventDraft,
    CollaborationEventKind,
    CollaborationHistoryProjection,
    CollaborationInputKind,
    CollaborationInvocationKind,
    CollaborationLeadTaskTransitionPayload,
    CollaborationMessagePayload,
    CollaborationNextAction,
    CollaborationProviderCallPayload,
    CollaborationStepState,
    CollaborationStepStatePayload,
    LeadDecisionResult,
    LeadOutcomeKind,
    LeadProposalRelation,
    LeadStepDecision,
    LeadTurnResult,
    project_collaboration_history,
)
from ..conversations.events import CollaborationExplorationAdoptionPayload
from ..conversations.session_context import SessionHistorySnapshot
from ..errors import (
    BackendProtocolError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from ..lead_control_events import effective_lead_control_events
from ..lead_execution.confirmed_step_execution import ConfirmedStepExecution
from ..lead_preparation import authoring_authority
from ..lead_task_transitions import (
    LeadTaskAction,
    LeadTaskTransitionReceipt,
)
from ..runtime_resources import collaboration_event_journal as event_journal
from ..runtime_resources.session_execution_scopes import SessionExecutionScopes
from ..workspace.initialization import SessionArtifactBinding, SessionArtifactBindingError

__all__ = ["LeadStepDecisions"]


class LeadStepDecisions:
    """Resolve exact lead decisions using the existing scoped execution boundary."""

    def __init__(
        self,
        *,
        storage: ConversationStorageServices,
        scopes: SessionExecutionScopes,
        context_service: AgentContextService,
        execution: ConfirmedStepExecution,
    ) -> None:
        self._storage = storage
        self._scopes = scopes
        self._context_service = context_service
        self._execution = execution

    async def decide_lead_step(
        self,
        decision: LeadStepDecision,
        *,
        action_progress_sink: ContextActionProgressSink | None = None,
        validate_identity: Callable[[str, str], None],
        capture_snapshot: Callable[[str], SessionHistorySnapshot],
        validate_proposal_authority: Callable[..., None],
        append_batch: Callable[[CollaborationEventBatch], CollaborationAppendReceipt],
        run_provider: Callable[..., Coroutine[Any, Any, LeadTurnResult]],
        close_failed_attempt: Callable[..., LeadTurnResult | None],
        run_specialist: Callable[..., Coroutine[Any, Any, CollaborationInvocationResult]],
        run_synthesis: Callable[..., Coroutine[Any, Any, CollaborationInvocationResult]],
        persist_results: Callable[..., None],
        read_events: Callable[[str, str], tuple[CollaborationEvent, ...]],
        prepare_recommendation_request: Callable[..., CollaborationEventDraft],
        finalize_known_result: Callable[..., LeadTurnResult],
        context_service: AgentContextService,
        render_premise_report: Callable[..., LeadTurnResult],
        validate_exact_candidate_reviews: Callable[..., None],
        qualify_artifact_final_review: Callable[..., Any],
        attach_document_plan: Callable[..., LeadTurnResult],
        bind_session_artifacts: Callable[[str], SessionArtifactBinding],
        build_contextual_definition: Callable[..., Any],
        build_agent_profile: Callable[..., Any],
        read_authoring_events: Callable[[str], tuple[CollaborationEvent, ...]],
        validate_review_material: Callable[..., None],
        qualify_readiness_targets: Callable[..., Any],
        qualify_authoring_inputs: Callable[..., Any],
    ) -> LeadDecisionResult:
        """Apply one exact structured decision to the authoritative request tail."""

        if type(decision) is not LeadStepDecision:
            raise TypeError("decision must be a LeadStepDecision")
        validate_context_event_sink(action_progress_sink)
        await run_blocking(validate_identity, decision.session_id, decision.conversation_id)
        lock = self._scopes.conversation_lock(
            decision.session_id,
            decision.conversation_id,
        )
        async with self._scopes.lead_session_lifecycle(decision.session_id,
            continuation_factory=self._context_service.session_continuation,
        ), lock:
            events = self._storage.event_store.read(
                decision.session_id,
                decision.conversation_id,
            )
            if any(type(event.payload) is CollaborationExplorationAdoptionPayload for event in events):
                (await run_blocking(capture_snapshot, decision.session_id)).validate_exploration_adoptions(decision.conversation_id)
            projection = project_collaboration_history(events)
            if (projection.next_safe_action is CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
                    and any(type(event.payload) is CollaborationProviderCallPayload
                            and event.payload.invocation_kind is CollaborationInvocationKind.LEAD_DISCUSSION
                            for event in events)):
                raise ValueError("unresolved discussion allows inspection only")
            proposal = projection.current_proposal
            if (
                projection.current_artifact_application_request is not None
                and projection.next_safe_action in {
                    CollaborationNextAction.SUBMIT_APPLICATION_DECISION,
                    CollaborationNextAction.SUBMIT_COMMIT_DECISION,
                    CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME,
                    CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY,
                }
            ):
                raise ValueError(
                    "lead decision is blocked by an unresolved artifact or Commit boundary"
                )
            if (
                proposal is None
                or projection.current_state
                not in {
                    CollaborationStepState.AWAITING_CONFIRMATION,
                    CollaborationStepState.RECONFIRMATION_REQUIRED,
                }
                or not events
            ):
                raise ValueError("lead decision has no authoritative pending request")
            control_events = effective_lead_control_events(events)
            request_event = control_events[-1]
            request_payload = request_event.payload
            if type(request_payload) is not CollaborationStepStatePayload or (
                request_payload.state
                not in {
                    CollaborationStepState.AWAITING_CONFIRMATION,
                    CollaborationStepState.RECONFIRMATION_REQUIRED,
                }
            ):
                raise ValueError("lead decision request is not the exact history tail")
            exact = (
                decision.confirmation_request_id
                == request_payload.confirmation_request_id
                and decision.session_id == request_event.session_id
                and decision.conversation_id == request_event.conversation_id
                and decision.step_id == request_event.step_id == proposal.step_id
                and decision.proposal_id == proposal.proposal_id
                and decision.proposal_revision == proposal.proposal_revision
                and decision.proposal_digest == proposal.proposal_digest
            )
            if not exact:
                raise ValueError("lead decision does not match the authoritative tail")
            if decision.kind is CollaborationConfirmationDecision.REVISE and proposal.premise_decision is not None:
                turn = LeadTurnResult(decision.session_id, decision.conversation_id,
                    request_event.turn_id, proposal.step_id,
                    LeadOutcomeKind.PROPOSAL if projection.current_state is CollaborationStepState.AWAITING_CONFIRMATION else LeadOutcomeKind.RECOMMENDATION,
                    "This analysis question remains pending and unchanged. Reject it, then send a fresh message to change the question or sources. Already resolved USER inputs may use ordinary direct authoring; no provider was called.",
                    proposal, projection.current_state, request_payload.confirmation_request_id,
                    CollaborationNextAction.SUBMIT_CONFIRMATION, proposal.basis.evidence)
                return LeadDecisionResult(decision.session_id, decision.conversation_id,
                    proposal.step_id, decision.kind, proposal, turn)
            artifact_candidate = projection.current_artifact_candidate
            if decision.kind is CollaborationConfirmationDecision.CONFIRM:
                try:
                    validate_proposal_authority(
                        session_id=decision.session_id,
                        events=events,
                        projection=projection,
                        proposal=proposal,
                    )
                except (
                    SessionArtifactBindingError,
                    WorkspaceFormatError,
                    WorkspaceSecurityError,
                ):
                    return LeadDecisionResult(
                        decision.session_id,
                        decision.conversation_id,
                        decision.step_id,
                        decision.kind,
                        proposal,
                        blocking_lead_result(
                            decision.session_id,
                            decision.conversation_id,
                            CollaborationStepState.RECOVERY_REQUIRED,
                            "The proposal's exact session artifact resources are "
                            "unavailable. Repair them before confirming; no "
                            "provider work was started.",
                            step_id=decision.step_id,
                        ),
                    )
                except BackendProtocolError:
                    if artifact_candidate is None:
                        raise
                    turn_id = event_journal.new_id("t")
                    confirmation = event_journal.draft(
                        session_id=decision.session_id,
                        conversation_id=decision.conversation_id,
                        turn_id=turn_id,
                        step_id=decision.step_id,
                        kind=CollaborationEventKind.CONFIRMATION,
                        sender=event_journal.USER_ACTOR,
                        recipient=event_journal.RUNTIME_ACTOR,
                        payload=CollaborationConfirmationPayload(
                            confirmation_request_id=(decision.confirmation_request_id),
                            confirmation_request_event_id=request_event.event_id,
                            proposal_id=proposal.proposal_id,
                            proposal_revision=proposal.proposal_revision,
                            proposal_digest=proposal.proposal_digest,
                            decision=decision.kind,
                        ),
                    )
                    event_journal.append_drafts(
                        projection.last_sequence,
                        (
                            confirmation,
                            event_journal.draft(
                                session_id=decision.session_id,
                                conversation_id=decision.conversation_id,
                                turn_id=turn_id,
                                step_id=decision.step_id,
                                kind=(CollaborationEventKind.ARTIFACT_PROPOSAL_STATE),
                                sender=event_journal.RUNTIME_ACTOR,
                                recipient=event_journal.USER_ACTOR,
                                payload=authoring_authority.artifact_state_payload(
                                    artifact_candidate,
                                    ArtifactProposalState.STALE,
                                ),
                            ),
                            event_journal.draft(
                                session_id=decision.session_id,
                                conversation_id=decision.conversation_id,
                                turn_id=turn_id,
                                step_id=decision.step_id,
                                kind=CollaborationEventKind.STEP_STATE,
                                sender=event_journal.RUNTIME_ACTOR,
                                recipient=event_journal.USER_ACTOR,
                                payload=CollaborationStepStatePayload(
                                    state=CollaborationStepState.SUPERSEDED,
                                    proposal_id=proposal.proposal_id,
                                    proposal_revision=proposal.proposal_revision,
                                    proposal_digest=proposal.proposal_digest,
                                ),
                            ),
                        ),
                        append_batch=append_batch,
                    )
                    stale_turn = LeadTurnResult(
                        decision.session_id,
                        decision.conversation_id,
                        turn_id,
                        decision.step_id,
                        LeadOutcomeKind.RECOMMENDATION,
                        "The exact artifact candidate became stale before review. "
                        "No provider was called and no artifact was changed.",
                        None,
                        CollaborationStepState.SUPERSEDED,
                        None,
                        CollaborationNextAction.CONTINUE_WITH_LEAD,
                        proposal.basis.evidence,
                        ArtifactProposalResult(
                            ArtifactProposalState.STALE,
                            artifact_candidate,
                        ),
                    )
                    return LeadDecisionResult(
                        decision.session_id,
                        decision.conversation_id,
                        decision.step_id,
                        decision.kind,
                        proposal,
                        stale_turn,
                    )

            turn_id = event_journal.new_id("t")
            confirmation = event_journal.draft(
                session_id=decision.session_id,
                conversation_id=decision.conversation_id,
                turn_id=turn_id,
                step_id=decision.step_id,
                kind=CollaborationEventKind.CONFIRMATION,
                sender=event_journal.USER_ACTOR,
                recipient=event_journal.RUNTIME_ACTOR,
                payload=CollaborationConfirmationPayload(
                    confirmation_request_id=decision.confirmation_request_id,
                    confirmation_request_event_id=request_event.event_id,
                    proposal_id=proposal.proposal_id,
                    proposal_revision=proposal.proposal_revision,
                    proposal_digest=proposal.proposal_digest,
                    decision=decision.kind,
                ),
            )
            resulting_state = {
                CollaborationConfirmationDecision.CONFIRM: (
                    CollaborationStepState.CONFIRMED
                ),
                CollaborationConfirmationDecision.REJECT: (
                    CollaborationStepState.REJECTED
                ),
                CollaborationConfirmationDecision.REVISE: (
                    CollaborationStepState.SUPERSEDED
                ),
            }[decision.kind]
            state_event = event_journal.draft(
                session_id=decision.session_id,
                conversation_id=decision.conversation_id,
                turn_id=turn_id,
                step_id=decision.step_id,
                kind=CollaborationEventKind.STEP_STATE,
                sender=event_journal.RUNTIME_ACTOR,
                recipient=event_journal.USER_ACTOR,
                payload=CollaborationStepStatePayload(
                    state=resulting_state,
                    proposal_id=proposal.proposal_id,
                    proposal_revision=proposal.proposal_revision,
                    proposal_digest=proposal.proposal_digest,
                ),
            )
            drafts: tuple[CollaborationEventDraft, ...]
            artifact_decision: CollaborationEventDraft | None = None
            if artifact_candidate is not None and decision.kind is not (
                CollaborationConfirmationDecision.CONFIRM
            ):
                artifact_decision = event_journal.draft(
                    session_id=decision.session_id,
                    conversation_id=decision.conversation_id,
                    turn_id=turn_id,
                    step_id=decision.step_id,
                    kind=CollaborationEventKind.ARTIFACT_PROPOSAL_STATE,
                    sender=event_journal.RUNTIME_ACTOR,
                    recipient=event_journal.USER_ACTOR,
                    payload=authoring_authority.artifact_state_payload(
                        artifact_candidate,
                        (
                            ArtifactProposalState.REJECTED
                            if decision.kind is CollaborationConfirmationDecision.REJECT
                            else ArtifactProposalState.SUPERSEDED
                        ),
                    ),
                )
            if decision.kind is CollaborationConfirmationDecision.REVISE:
                assert decision.revision_request is not None
                drafts = (
                    confirmation,
                    event_journal.draft(
                        session_id=decision.session_id,
                        conversation_id=decision.conversation_id,
                        turn_id=turn_id,
                        step_id=decision.step_id,
                        kind=CollaborationEventKind.MESSAGE,
                        sender=event_journal.USER_ACTOR,
                        recipient=event_journal.LEAD_ACTOR,
                        payload=CollaborationMessagePayload(decision.revision_request),
                    ),
                    *((artifact_decision,) if artifact_decision is not None else ()),
                    state_event,
                )
            else:
                drafts = (
                    confirmation,
                    *((artifact_decision,) if artifact_decision is not None else ()),
                    state_event,
                )
            task_transition_event_id = None
            if decision.kind is CollaborationConfirmationDecision.REVISE and proposal.authoring_scope is not None:
                source = next((item for item in projection.available_tasks
                               if (item.ref.task_id, item.ref.task_revision) == (
                                   proposal.authoring_scope.task_id, proposal.authoring_scope.task_revision,
                               )), None)
                if source is None:
                    raise BackendProtocolError("revision requires the exact retained task source")
                revision_message = next(item for item in drafts
                                        if type(item.payload) is CollaborationMessagePayload
                                        and item.sender.kind is CollaborationActorKind.USER)
                transition = event_journal.draft(
                    session_id=decision.session_id, conversation_id=decision.conversation_id,
                    turn_id=turn_id, step_id=decision.step_id,
                    kind=CollaborationEventKind.LEAD_TASK_TRANSITION,
                    sender=event_journal.RUNTIME_ACTOR, recipient=event_journal.USER_ACTOR,
                    payload=CollaborationLeadTaskTransitionPayload(LeadTaskTransitionReceipt(
                        origin_event_id=confirmation.event_id, origin_kind="revise_decision",
                        origin_message_event_id=revision_message.event_id,
                        action=LeadTaskAction.RESUME, source_task_ref=source.ref,
                        target_task_id=source.ref.task_id, target_task_revision=source.ref.task_revision + 1,
                        planning_step_id=decision.step_id,
                    )),
                )
                task_transition_event_id = transition.event_id
                drafts += (transition,)
            event_journal.append_drafts(projection.last_sequence, drafts,
                append_batch=append_batch,
            )

            if decision.kind is CollaborationConfirmationDecision.CONFIRM:
                persisted = self._storage.event_store.read(
                    decision.session_id,
                    decision.conversation_id,
                )
                tail = persisted[-1]
                completed_turn = await self._execution.confirm_and_execute_locked(
                    decision=decision,
                    proposal=proposal,
                    tail=tail,
                    action_progress_sink=action_progress_sink,
                    validate_proposal_authority=validate_proposal_authority,
                    append_batch=append_batch,
                    run_specialist=run_specialist,
                    run_synthesis=run_synthesis,
                    persist_results=persist_results,
                    read_events=read_events,
                    prepare_recommendation_request=prepare_recommendation_request,
                    finalize_known_result=finalize_known_result,
                    context_service=context_service,
                    render_premise_report=render_premise_report,
                    validate_exact_candidate_reviews=validate_exact_candidate_reviews,
                    qualify_artifact_final_review=qualify_artifact_final_review,
                    attach_document_plan=attach_document_plan,
                    bind_session_artifacts=bind_session_artifacts,
                    build_contextual_definition=build_contextual_definition,
                    build_agent_profile=build_agent_profile,
                    read_authoring_events=read_authoring_events,
                    validate_review_material=validate_review_material,
                    qualify_readiness_targets=qualify_readiness_targets,
                    qualify_authoring_inputs=qualify_authoring_inputs,
                )
                return LeadDecisionResult(
                    decision.session_id,
                    decision.conversation_id,
                    decision.step_id,
                    decision.kind,
                    proposal,
                    completed_turn,
                )
            if decision.kind is CollaborationConfirmationDecision.REJECT:
                self._execution.discard_unexecuted_confirmation(
                decision.session_id,
                decision.conversation_id,
                decision.step_id,
            )
                rejected_turn = LeadTurnResult(
                    decision.session_id,
                    decision.conversation_id,
                    turn_id,
                    decision.step_id,
                    LeadOutcomeKind.RECOMMENDATION,
                    "The proposed collaboration was rejected. I will continue with you directly.",
                    None,
                    CollaborationStepState.REJECTED,
                    None,
                    CollaborationNextAction.CONTINUE_WITH_LEAD,
                    None,
                    (
                        None
                        if artifact_candidate is None
                        else ArtifactProposalResult(
                            ArtifactProposalState.REJECTED,
                            artifact_candidate,
                        )
                    ),
                )
                return LeadDecisionResult(
                    decision.session_id,
                    decision.conversation_id,
                    decision.step_id,
                    decision.kind,
                    proposal,
                    rejected_turn,
                )

            assert decision.revision_request is not None
            retained_references = " ".join(
                item.locator
                for item in proposal.basis.inputs
                if item.kind
                in {
                    CollaborationInputKind.CORE_DOCUMENT,
                    CollaborationInputKind.MANUSCRIPT,
                    CollaborationInputKind.WRITING_ARTIFACT,
                }
            )
            provider_text = decision.revision_request
            if retained_references:
                provider_text = f"{provider_text}\n{retained_references}"
            try:
                revised_turn = await run_provider(
                    session_id=decision.session_id,
                    conversation_id=decision.conversation_id,
                    turn_id=turn_id,
                    step_id=decision.step_id,
                    text=provider_text,
                    expected_relation=LeadProposalRelation.REVISE_PENDING,
                    previous_proposal=proposal,
                    context_event_sink=None,
                    action_progress_sink=action_progress_sink,
                    user_source_text=decision.revision_request,

                    task_transition_event_id=task_transition_event_id,
                )
            except BaseException as failure:
                close_failed_attempt(
                    decision.session_id, decision.conversation_id, failure=failure,
                    turn_id=turn_id, step_id=decision.step_id,
                )
                raise
            if (
                (artifact_candidate is not None
                and revised_turn.artifact_proposal is None
                and revised_turn.recommendation_request is None and revised_turn.kind is not LeadOutcomeKind.DISCUSSION)
            ):
                revised_turn = replace(
                    revised_turn,
                    artifact_proposal=ArtifactProposalResult(
                        ArtifactProposalState.SUPERSEDED,
                        artifact_candidate,
                    ),
                )
            return LeadDecisionResult(
                decision.session_id,
                decision.conversation_id,
                decision.step_id,
                decision.kind,
                proposal,
                revised_turn,
            )
