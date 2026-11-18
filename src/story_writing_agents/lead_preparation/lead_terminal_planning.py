"""Failure and structured-fallback event policies without resource ownership."""

from ..conversations import (
    ArtifactReviewFailureCode, ArtifactReviewFailurePayload, CollaborationEvent,
    CollaborationEventDraft, CollaborationEventKind, CollaborationHistoryProjection,
    CollaborationLeadOutcomeFailureCode, CollaborationLeadOutcomeFailurePayload,
    CollaborationLeadTaskTransitionPayload, CollaborationMessagePayload, CollaborationStepState,
)
from ..agents import LEAD_ORCHESTRATOR
from ..conversations import LeadRecommendationState
from ..lead_task_transitions import LeadTaskAction
from ..reply_trace import ReplyTrace, ReplyTraceOrigin
from ..runtime_resources import collaboration_event_journal as event_journal
from . import reply_contract_values

SAFE_STRUCTURED_FALLBACK_MESSAGE = "LEAD_CONTRACT_REJECTED"

__all__ = (
    'SAFE_STRUCTURED_FALLBACK_MESSAGE',
    'lead_failure_drafts',
    'discussion_control',
    'structured_fallback_values',
    'structured_fallback_draft',
)


def lead_failure_drafts(
    *,
    session_id: str,
    conversation_id: str,
    turn_id: str,
    step_id: str,
    invocation_id: str | None,
    call_id: str | None,
    failure_code: CollaborationLeadOutcomeFailureCode,
    artifact_intent: bool,
    projection: CollaborationHistoryProjection,
) -> tuple[CollaborationEventDraft, ...]:
    failure_drafts: tuple[CollaborationEventDraft, ...] = (
        event_journal.draft(
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            step_id=step_id,
            kind=CollaborationEventKind.LEAD_OUTCOME_FAILURE,
            sender=event_journal.RUNTIME_ACTOR,
            recipient=event_journal.USER_ACTOR,
            payload=CollaborationLeadOutcomeFailurePayload(
                code=failure_code,
                invocation_id=invocation_id,
                call_id=call_id,
            ),
        ),
    )
    if artifact_intent and projection.current_recommendation_state is not LeadRecommendationState.RUNNING:
        failure_drafts += (
            event_journal.draft(
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=step_id,
                kind=CollaborationEventKind.ARTIFACT_REVIEW_FAILURE,
                sender=event_journal.RUNTIME_ACTOR,
                recipient=event_journal.USER_ACTOR,
                payload=ArtifactReviewFailurePayload(
                    code=ArtifactReviewFailureCode.INTENT_REJECTED,
                    invocation_id=invocation_id,
                    call_id=call_id,
                ),
            ),
        )
    return failure_drafts


def discussion_control(
    events: tuple[CollaborationEvent, ...],
    turn_id: str,
    step_id: str,
) -> CollaborationEvent | None:
    control = next((event for event in events
                    if type(event.payload) is CollaborationLeadTaskTransitionPayload
                    and (event.turn_id, event.step_id) == (turn_id, step_id)
                    and event.payload.receipt.action is LeadTaskAction.DISCUSS), None)
    return control


def structured_fallback_values(
    message: str,
    language: str | None,
    task_revisions: tuple[tuple[str, int], ...],
    state: CollaborationStepState | None,
    validation_details: tuple[str, ...],
) -> tuple[str, ReplyTrace]:
    if message == SAFE_STRUCTURED_FALLBACK_MESSAGE:
        message = reply_contract_values.lead_terminal_notice(language, task_revisions, state, validation_details)
    trace = ReplyTrace(
        role=LEAD_ORCHESTRATOR,
        action="none",
        context_sources=("current_input",),
        recovered_fields=("structured_reply",),
        origin=ReplyTraceOrigin.RECOVERED,
    )
    return message, trace


def structured_fallback_draft(
    session_id: str,
    conversation_id: str,
    turn_id: str,
    step_id: str,
    message: str,
    trace: ReplyTrace,
    provider_invocation_id: str | None,
) -> CollaborationEventDraft:
    return event_journal.draft(
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        step_id=step_id,
        kind=CollaborationEventKind.MESSAGE,
        sender=event_journal.LEAD_ACTOR,
        recipient=event_journal.USER_ACTOR,
        payload=CollaborationMessagePayload(
            message,
            trace,
            provider_invocation_id=provider_invocation_id,
        ),
    )
