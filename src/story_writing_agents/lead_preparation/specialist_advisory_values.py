"""Bounded specialist advisory policy and chronological publication values."""

from ..specialist_routing import AuthoringActivity
from ..specialist_routing import AuthoringScope
from ..conversations import COLLABORATION_PHYSICAL_CALL_LIMIT
from ..conversations import COLLABORATION_ROUND_LIMIT
from ..conversations import CollaborationAssignment
from ..conversations import CollaborationBudget
from ..conversations import CollaborationEventDraft
from ..conversations import CollaborationEventKind
from ..conversations import CollaborationHistoryProjection
from ..conversations import CollaborationInputRevision
from ..conversations import CollaborationMessagePayload
from ..conversations import CollaborationProposal
from ..conversations import CollaborationStepState
from ..conversations import CollaborationStepStatePayload
from ..conversations import LeadAssignmentIntent
from ..specialist_routing import ReviewBasis
from ..specialist_routing import SpecialistAssignmentMode
from . import authoring_authority
from ..runtime_resources import collaboration_event_journal as event_journal
from ..writing_artifacts import parse_artifact_references
from ..specialist_routing import validate_specialist_routing

__all__ = (
    "ADVISORY_USER_IMPACT",
    "advisory_confirmation_batch",
    "prepare_advisory_origin",
    "prepare_specialist_advisory_intent",
    "specialist_advisory_assignment",
    "specialist_advisory_budget",
)

ADVISORY_USER_IMPACT = (
    "The selected specialist provides visible advice only. No lead synthesis, "
    "chaining, artifact mutation, or Git operation occurs."
)

def prepare_specialist_advisory_intent(
    authoring_scope: AuthoringScope,
    specialist_id: str,
    text: str,
    inputs: tuple[CollaborationInputRevision, ...],
    review_basis: ReviewBasis | None,
) -> tuple[SpecialistAssignmentMode, LeadAssignmentIntent]:
    validate_specialist_routing(authoring_scope, (specialist_id,))
    referenced_reviewers = {
        reviewer
        for reference in parse_artifact_references(text).references
        for reviewer in (authoring_authority.core_reviewer(reference.token),)
        if reviewer is not None
    }
    if any(reviewer != specialist_id for reviewer in referenced_reviewers):
        raise ValueError(
            "referenced artifact is owned by another reviewer"
        )
    mode = (SpecialistAssignmentMode.REVIEW if authoring_scope.activity is AuthoringActivity.REVIEW
            else SpecialistAssignmentMode.DRAFT)
    advisory_intent = LeadAssignmentIntent(
        specialist_id, text, tuple(item.input_id for item in inputs), mode, review_basis,
    )
    return (
        mode,
        advisory_intent,
    )


def specialist_advisory_assignment(
    specialist_id: str,
    text: str,
    projection: CollaborationHistoryProjection,
    inputs: tuple[CollaborationInputRevision, ...],
    call_limits: tuple[int, ...],
    mode: SpecialistAssignmentMode,
    review_basis: ReviewBasis | None,
) -> CollaborationAssignment:
    return CollaborationAssignment(
        assignment_id=event_journal.new_id("a"),
        specialist_id=specialist_id,
        round=projection.rounds_spent + 1,
        instruction=text,
        input_ids=tuple(item.input_id for item in inputs),
        max_physical_calls=call_limits[0],
        mode=mode,
        review_basis=review_basis,
    )


def specialist_advisory_budget(
    projection: CollaborationHistoryProjection,
    required_calls: int,
) -> CollaborationBudget:
    return CollaborationBudget(
        round_limit=COLLABORATION_ROUND_LIMIT,
        physical_call_limit=COLLABORATION_PHYSICAL_CALL_LIMIT,
        rounds_spent=projection.rounds_spent,
        rounds_added=1,
        physical_calls_spent=projection.physical_calls_spent,
        max_additional_physical_calls=required_calls,
        synthesis_max_physical_calls=0,
    )


def prepare_advisory_origin(
    session_id: str,
    conversation_id: str,
    turn_id: str,
    step_id: str,
    specialist_id: str,
    text: str,
    proposal: CollaborationProposal,
) -> tuple[str, CollaborationEventDraft, str, CollaborationEventDraft]:
    request_id = event_journal.new_id("c")
    user_event = event_journal.draft(
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        step_id=step_id,
        kind=CollaborationEventKind.MESSAGE,
        sender=event_journal.USER_ACTOR,
        recipient=event_journal.LEAD_ACTOR,
        payload=CollaborationMessagePayload(text),
    )
    message = (
        f"I propose a direct advisory from {specialist_id}. Confirm, reject, "
        "or revise this exact bounded request."
    )
    proposal_event = event_journal.draft(
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        step_id=step_id,
        kind=CollaborationEventKind.MESSAGE,
        sender=event_journal.LEAD_ACTOR,
        recipient=event_journal.USER_ACTOR,
        payload=proposal,
    )
    return (
        request_id,
        user_event,
        message,
        proposal_event,
    )


def advisory_confirmation_batch(
    session_id: str,
    conversation_id: str,
    turn_id: str,
    step_id: str,
    user_event: CollaborationEventDraft,
    message: str,
    proposal_event: CollaborationEventDraft,
    proposal: CollaborationProposal,
    request_id: str,
) -> tuple[CollaborationEventDraft, ...]:
    return (
        user_event,
        event_journal.draft(
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            step_id=step_id,
            kind=CollaborationEventKind.MESSAGE,
            sender=event_journal.LEAD_ACTOR,
            recipient=event_journal.USER_ACTOR,
            payload=CollaborationMessagePayload(message),
        ),
        proposal_event,
        event_journal.draft(
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            step_id=step_id,
            kind=CollaborationEventKind.STEP_STATE,
            sender=event_journal.RUNTIME_ACTOR,
            recipient=event_journal.USER_ACTOR,
            payload=CollaborationStepStatePayload(
                state=CollaborationStepState.AWAITING_CONFIRMATION,
                proposal_id=proposal.proposal_id,
                proposal_revision=proposal.proposal_revision,
                proposal_digest=proposal.proposal_digest,
                confirmation_request_id=request_id,
            ),
        ),
    )
