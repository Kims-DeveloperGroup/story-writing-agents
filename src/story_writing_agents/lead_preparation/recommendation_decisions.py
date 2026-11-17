"""Recommendation decisions and terminal failure accounting policy."""

from __future__ import annotations

from dataclasses import dataclass
from . import recommendation_preparation
from ..agent_context.contracts.call_offers import ContextInvocationKind
from ..agents import LEAD_ORCHESTRATOR
from ..conversations import CollaborationConfirmationDecision
from ..conversations import CollaborationEvent
from ..conversations import CollaborationEventDraft
from ..conversations import CollaborationEventKind
from ..conversations import CollaborationHistoryProjection
from ..conversations import CollaborationInputKind
from ..conversations import CollaborationLeadOutcomeFailurePayload
from ..conversations import CollaborationLeadTaskTransitionPayload
from ..conversations import CollaborationMessagePayload
from ..conversations import LeadRecommendationDecision
from ..conversations import LeadRecommendationDecisionPayload
from ..conversations import LeadRecommendationRequestPayload
from ..conversations import LeadRecommendationResultPayload
from ..conversations import LeadRecommendationStartedPayload
from ..conversations import LeadRecommendationState
from ..errors import BackendProtocolError
from ..lead_task_transitions import LeadTaskAction
from ..lead_task_transitions import LeadTaskRef
from ..lead_task_transitions import LeadTaskTransitionReceipt
from ..runtime_resources import collaboration_event_journal as event_journal
from ..writing_artifacts.authorization import ReferenceAuthorizationErrorCode
from ..writing_artifacts.authorization import ReferenceInvocationState
from ..writing_artifacts.authorization import ReferenceOperationHistory
from ..writing_artifacts.authorization import ReferenceOperationState
from typing import cast

__all__ = (
    'PreparedRecommendationDecision',
    'PreparedRecommendationRevision',
    'RecommendationDecisionClosure',
    'RecommendationFailureMatch',
    'prepare_recommendation_decision',
    'build_recommendation_decision_outcome',
    'match_recommendation_failure',
    'terminal_recommendation_failure_is_accounted',
    'build_recommendation_failure_closure',
)


@dataclass(slots=True)
class PreparedRecommendationDecision:
    request_event: CollaborationEvent
    request: LeadRecommendationRequestPayload
    turn_id: str
    decision_draft: CollaborationEventDraft


@dataclass(slots=True)
class PreparedRecommendationRevision:
    request_event: CollaborationEvent
    request: LeadRecommendationRequestPayload
    turn_id: str
    decision_draft: CollaborationEventDraft
    source: LeadTaskRef | None
    planning_step: str
    user_message: CollaborationEventDraft
    transition: CollaborationEventDraft
    closure: CollaborationEventDraft


@dataclass(slots=True)
class RecommendationDecisionClosure:
    message: str
    draft: CollaborationEventDraft


@dataclass(slots=True)
class RecommendationFailureMatch:
    request_event: CollaborationEvent
    decision_event: CollaborationEvent
    start_event: CollaborationEvent
    lead_result: CollaborationEvent
    uses_references: bool


def prepare_recommendation_decision(
    session_id: str,
    conversation_id: str,
    decision: LeadRecommendationDecision,
    events: tuple[CollaborationEvent, ...],
    projection: CollaborationHistoryProjection,
) -> PreparedRecommendationDecision | PreparedRecommendationRevision:
    request = projection.current_recommendation_request
    if request is None or projection.current_recommendation_state is not LeadRecommendationState.PENDING:
        raise BackendProtocolError("recommendation decision is no longer pending")
    expected = LeadRecommendationDecision(
        session_id, conversation_id, request.request_event_id, request.tail_sequence,
        request.request_id, request.action_id, request.action_revision, request.action_digest,
        decision.kind, decision.revision_request,
    )
    if decision != expected or events[-1].event_id != request.request_event_id:
        raise BackendProtocolError("recommendation decision does not match its exact current tail")
    request_event = events[-1]
    turn_id = event_journal.new_id("t")
    decision_draft = event_journal.draft(
        session_id=session_id, conversation_id=conversation_id, turn_id=turn_id,
        step_id=request_event.step_id, kind=CollaborationEventKind.LEAD_RECOMMENDATION_DECISION,
        sender=event_journal.USER_ACTOR, recipient=event_journal.RUNTIME_ACTOR,
        payload=LeadRecommendationDecisionPayload(decision),
    )
    if decision.kind is CollaborationConfirmationDecision.REVISE:
        source = next((view.ref for view in projection.available_tasks
                       if request.authoring_scope is not None
                       and (view.ref.task_id, view.ref.task_revision)
                       == (request.authoring_scope.task_id, request.authoring_scope.task_revision)), None)
        if request.authoring_scope is not None and source is None:
            raise ValueError("recommendation revision requires exact current source task")
        planning_step = request_event.step_id if source is None else event_journal.new_id("s")
        user_message = event_journal.draft(
            session_id=session_id, conversation_id=conversation_id, turn_id=turn_id,
            step_id=planning_step, kind=CollaborationEventKind.MESSAGE,
            sender=event_journal.USER_ACTOR, recipient=event_journal.LEAD_ACTOR,
            payload=CollaborationMessagePayload(decision.revision_request),
        )
        receipt = LeadTaskTransitionReceipt(
            origin_event_id=decision_draft.event_id, origin_kind="revise_decision",
            action=LeadTaskAction.DISCUSS if source is None else LeadTaskAction.RESUME,
            origin_message_event_id=user_message.event_id, source_task_ref=source,
            target_task_id=None if source is None else source.task_id,
            target_task_revision=None if source is None else source.task_revision + 1,
            planning_step_id=None if source is None else planning_step,
        )
        transition = event_journal.draft(
            session_id=session_id, conversation_id=conversation_id, turn_id=turn_id,
            step_id=planning_step, kind=CollaborationEventKind.LEAD_TASK_TRANSITION,
            sender=event_journal.RUNTIME_ACTOR, recipient=event_journal.USER_ACTOR,
            payload=CollaborationLeadTaskTransitionPayload(receipt),
        )
        closure = event_journal.draft(
            session_id=session_id, conversation_id=conversation_id, turn_id=turn_id,
            step_id=request_event.step_id, kind=CollaborationEventKind.LEAD_RECOMMENDATION_RESULT,
            sender=event_journal.RUNTIME_ACTOR, recipient=event_journal.USER_ACTOR,
            payload=LeadRecommendationResultPayload(
                request.request_event_id, request.request_id, request.action_id,
                request.action_revision, request.action_digest, LeadRecommendationState.STALE,
                "Revise closed the exact pending recommendation for a fresh discussion or proposal.",
                decision_event_id=decision_draft.event_id,
            ),
        )
        return PreparedRecommendationRevision(
            request_event=request_event,
            request=request,
            turn_id=turn_id,
            decision_draft=decision_draft,
            source=source,
            planning_step=planning_step,
            user_message=user_message,
            transition=transition,
            closure=closure,
        )
    return PreparedRecommendationDecision(
        request_event=request_event,
        request=request,
        turn_id=turn_id,
        decision_draft=decision_draft,
    )


def build_recommendation_decision_outcome(
    session_id: str,
    conversation_id: str,
    turn_id: str,
    request_event: CollaborationEvent,
    request: LeadRecommendationRequestPayload,
    decision: LeadRecommendationDecision,
    decision_draft: CollaborationEventDraft,
    *,
    stale: bool,
) -> RecommendationDecisionClosure | CollaborationEventDraft:
    if stale or decision.kind is CollaborationConfirmationDecision.REJECT:
        state = LeadRecommendationState.STALE if stale else LeadRecommendationState.REJECTED
        message = ("The recommendation changed and needs fresh planning and confirmation."
                   if stale else "The recommendation was rejected; the authoring objective remains open.")
        closure = event_journal.draft(
            session_id=session_id, conversation_id=conversation_id, turn_id=turn_id,
            step_id=request_event.step_id, kind=CollaborationEventKind.LEAD_RECOMMENDATION_RESULT,
            sender=event_journal.RUNTIME_ACTOR, recipient=event_journal.USER_ACTOR,
            payload=LeadRecommendationResultPayload(
                request.request_event_id, request.request_id, request.action_id,
                request.action_revision, request.action_digest, state, message,
                decision_event_id=decision_draft.event_id,
            ),
        )
        return RecommendationDecisionClosure(message=message, draft=closure)
    start_id = event_journal.new_id("e")
    start = event_journal.draft_with_id(
        event_id=start_id, session_id=session_id, conversation_id=conversation_id,
        turn_id=turn_id, step_id=request_event.step_id,
        kind=CollaborationEventKind.LEAD_RECOMMENDATION_STARTED,
        sender=event_journal.RUNTIME_ACTOR, recipient=event_journal.LEAD_ACTOR,
        payload=LeadRecommendationStartedPayload(
            start_id, request.request_event_id, decision_draft.event_id, request.request_id,
            request.action_id, request.action_revision, request.action_digest, event_journal.new_id("raexec"),
        ),
    )
    return start


def match_recommendation_failure(
    events: tuple[CollaborationEvent, ...],
    projection: CollaborationHistoryProjection,
) -> RecommendationFailureMatch | None:
    start = projection.current_recommendation_start
    if start is None or projection.current_recommendation_state is not LeadRecommendationState.RUNNING:
        return None
    by_id = {event.event_id: event for event in events}
    request_event = by_id[start.request_event_id]
    decision_event = by_id[start.decision_event_id]
    start_event = by_id[start.start_event_id]
    tail = events[-1]
    lead_result = next(
        (
            event for event in reversed(events)
            if type(event.payload) is CollaborationLeadOutcomeFailurePayload
            and event.turn_id == start_event.turn_id
            and event.step_id == start_event.step_id
            and event.sequence >= start_event.sequence
        ),
        tail,
    )
    if (type(lead_result.payload) is not CollaborationLeadOutcomeFailurePayload
        or lead_result.turn_id != start_event.turn_id
        or lead_result.step_id != start_event.step_id):
        return None
    request = cast(LeadRecommendationRequestPayload, request_event.payload)
    uses_references = any(item.kind in {
        CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
        CollaborationInputKind.WRITING_ARTIFACT,
    } for item in request.basis.inputs)
    return RecommendationFailureMatch(
        request_event=request_event,
        decision_event=decision_event,
        start_event=start_event,
        lead_result=lead_result,
        uses_references=uses_references,
    )


def terminal_recommendation_failure_is_accounted(
    reference_history: object,
    operation_id: str,
) -> bool:
    permitted = {None, ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
                 ReferenceAuthorizationErrorCode.PROVIDER_FAILURE}
    terminal_proof = bool(
        type(reference_history) is ReferenceOperationHistory
        and reference_history.manifest.operation_id == operation_id
        and reference_history.operation_events[-1].operation_state in {
            ReferenceOperationState.ACCEPTED, ReferenceOperationState.TERMINATED,
        }
        and reference_history.operation_events[-1].outcome in permitted
        and reference_history.invocation_events
        and all(invocation[-1].correlation is not None
            and invocation[-1].correlation.operation_id == operation_id
            and invocation[-1].correlation.agent_id == LEAD_ORCHESTRATOR
            and invocation[-1].correlation.phase == ContextInvocationKind.LEAD.value
            and invocation[-1].correlation.round_number is None
            and invocation[-1].invocation_state in {
                ReferenceInvocationState.ACCEPTED, ReferenceInvocationState.TERMINATED,
            } and invocation[-1].outcome in permitted
            for invocation in reference_history.invocation_events)
    )
    return terminal_proof


def build_recommendation_failure_closure(
    events: tuple[CollaborationEvent, ...],
    failure: RecommendationFailureMatch,
) -> tuple[CollaborationEventDraft, tuple[str, ...]]:
    request_event = failure.request_event
    decision_event = failure.decision_event
    start_event = failure.start_event
    lead_result = failure.lead_result
    closure = recommendation_preparation.recommendation_closure_draft(lead_result, request_event=request_event,
        decision_event=decision_event, start_event=start_event)
    validation_details = tuple(
        detail
        for event in events
        if event.turn_id == start_event.turn_id
        and event.step_id == start_event.step_id
        and type(event.payload) is CollaborationMessagePayload
        and event.sender == event_journal.RUNTIME_ACTOR
        and event.recipient == event_journal.USER_ACTOR
        for detail in event.payload.validation_details
    )
    return closure, validation_details
