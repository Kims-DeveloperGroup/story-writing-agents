"""USER task and ordinary-turn values without resource ownership."""

from ..conversations import CollaborationEvent
from ..conversations import CollaborationEventDraft
from ..conversations import CollaborationEventKind
from ..conversations.events import CollaborationExplorationAdoptionPayload
from ..conversations import CollaborationHistoryProjection
from ..conversations.events import CollaborationLeadExplorationRequestPayload
from ..conversations import CollaborationLeadMaterialCapturePayload
from ..synchronization_repair import SynchronizationRepairSelection
from ..conversations import CollaborationLeadTaskDecisionPayload
from ..conversations import CollaborationLeadTaskTransitionPayload
from ..conversations import CollaborationMessagePayload
from ..conversations.events import CollaborationPremiseSelectionPayload
from ..conversations import CollaborationProposal
from ..conversations import CollaborationStepState
from ..conversations import CollaborationStepStatePayload
from ..document_plan import DocumentNodeSelection
from ..lead_exploration import ExplorationAdoptionCapture
from ..lead_exploration import ExplorationAdoptionSelection
from ..lead_task_transitions import LeadMaterialCapture
from ..conversations import LeadRecommendationResultPayload
from ..conversations import LeadRecommendationState
from ..lead_task_transitions import LeadTaskAction
from ..lead_task_transitions import LeadTaskDecision
from ..lead_task_transitions import LeadTaskTransitionReceipt
from ..lead_premise_decisions import PremiseCostLink
from ..lead_premise_decisions import PremiseDecisionReport
from ..lead_premise_decisions import PremiseDecisionSelection
from ..lead_premise_decisions import PremiseOption
from ..lead_premise_decisions import PremiseSelectionCapture
from ..conversations.events import encode_collaboration_event
from ..runtime_resources import collaboration_event_journal as event_journal
import hashlib
from dataclasses import replace

__all__ = (
    "prepare_exploration_adoption",
    "prepare_exploration_request",
    "prepare_lead_turn_origin",
    "prepare_premise_selection",
    "prepare_task_transition",
    "superseding_turn_draft",
)

def prepare_task_transition(
    session_id: str,
    conversation_id: str,
    decision: LeadTaskDecision,
    document_selection: DocumentNodeSelection | None,
    events: tuple[CollaborationEvent, ...],
    projection: CollaborationHistoryProjection,
    materials: tuple[str, ...],
    *,
    synchronization_repair: SynchronizationRepairSelection | None = None,
) -> tuple[str, str, CollaborationEventDraft, CollaborationEventDraft | None, tuple[CollaborationEventDraft, ...], CollaborationEventDraft]:
    turn_id = event_journal.new_id("t")
    step_id = event_journal.new_id("s")
    target_id = target_revision = None
    if decision.action is LeadTaskAction.RESUME:
        target_id = decision.task_ref.task_id
        target_revision = decision.task_ref.task_revision + 1
        previous_planning = next((event.payload.receipt for event in events
                                  if type(event.payload) is CollaborationLeadTaskTransitionPayload
                                  and (event.payload.receipt.target_task_id,
                                       event.payload.receipt.target_task_revision) == (target_id, target_revision)), None)
        if previous_planning is not None:
            step_id = previous_planning.planning_step_id
    elif decision.action is LeadTaskAction.NEW_TASK:
        target_id, target_revision = event_journal.new_id("s"), 1
    origin = replace(event_journal.draft(
        session_id=session_id, conversation_id=conversation_id,
        turn_id=turn_id, step_id=step_id, kind=CollaborationEventKind.LEAD_TASK_DECISION,
        sender=event_journal.USER_ACTOR, recipient=event_journal.RUNTIME_ACTOR,
        payload=CollaborationLeadTaskDecisionPayload(
            decision, document_selection=document_selection, synchronization_repair=synchronization_repair,
        ),
    ), event_id=decision.request_id)
    message = None if decision.text is None else event_journal.draft(
        session_id=session_id, conversation_id=conversation_id,
        turn_id=turn_id, step_id=step_id, kind=CollaborationEventKind.MESSAGE,
        sender=event_journal.USER_ACTOR, recipient=event_journal.LEAD_ACTOR,
        payload=CollaborationMessagePayload(decision.text),
    )
    captures = tuple(event_journal.draft(
        session_id=session_id, conversation_id=conversation_id,
        turn_id=turn_id, step_id=step_id, kind=CollaborationEventKind.LEAD_MATERIAL_CAPTURE,
        sender=event_journal.RUNTIME_ACTOR, recipient=event_journal.USER_ACTOR,
        payload=CollaborationLeadMaterialCapturePayload(LeadMaterialCapture(
            origin_event_id=origin.event_id, source=ref, selected_text=text,
            material_digest=ref.material_digest, target_task_id=target_id,
            target_task_revision=target_revision,
        )),
    ) for ref, text in zip(decision.material_refs, materials, strict=True))
    receipt = LeadTaskTransitionReceipt(
        origin_event_id=origin.event_id, origin_kind="task_decision", action=decision.action,
        origin_message_event_id=None if message is None else message.event_id,
        source_task_ref=decision.task_ref, target_task_id=target_id,
        target_task_revision=target_revision,
        planning_step_id=step_id if target_id is not None else None,
        material_capture_event_ids=tuple(event.event_id for event in captures),
    )
    transition = event_journal.draft(
        session_id=session_id, conversation_id=conversation_id,
        turn_id=turn_id, step_id=step_id, kind=CollaborationEventKind.LEAD_TASK_TRANSITION,
        sender=event_journal.RUNTIME_ACTOR, recipient=event_journal.USER_ACTOR,
        payload=CollaborationLeadTaskTransitionPayload(receipt),
    )
    return (
        turn_id,
        step_id,
        origin,
        message,
        captures,
        transition,
    )


def prepare_lead_turn_origin(
    session_id: str,
    conversation_id: str,
    text: str,
    projection: CollaborationHistoryProjection,
) -> tuple[str, str, CollaborationEventDraft, CollaborationProposal | None]:
    turn_id = event_journal.new_id("t")
    provider_step_id = event_journal.new_id("s")
    pending_proposal = (
        projection.current_proposal
        if projection.current_state
        in {
            CollaborationStepState.AWAITING_CONFIRMATION,
            CollaborationStepState.RECONFIRMATION_REQUIRED,
        }
        else None
    )
    message_step_id = (
        provider_step_id
        if pending_proposal is None
        else pending_proposal.step_id
    )
    user_event = event_journal.draft(
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        step_id=message_step_id,
        kind=CollaborationEventKind.MESSAGE,
        sender=event_journal.USER_ACTOR,
        recipient=event_journal.LEAD_ACTOR,
        payload=CollaborationMessagePayload(text),
    )
    return (
        turn_id,
        provider_step_id,
        user_event,
        pending_proposal,
    )


def prepare_premise_selection(
    session_id: str,
    conversation_id: str,
    turn_id: str,
    provider_step_id: str,
    control: CollaborationEventDraft,
    selection: PremiseDecisionSelection,
    report: PremiseDecisionReport,
    option: PremiseOption,
) -> tuple[PremiseSelectionCapture, CollaborationEventDraft]:
    task_id = selection.authoring_task_id or event_journal.new_id("s")
    task_revision = 1 if selection.expected_authoring_task_revision is None else selection.expected_authoring_task_revision + 1
    premise_capture = PremiseSelectionCapture(control.event_id, selection,
        option.premise_text, option.material_digest, option.option_id, task_id,
        task_revision, report.spec.slot_id, report.spec.dependent_domains,
        PremiseCostLink(report.cost, report.prior_authoring_cost, report.prior_admission_ref))
    selected = event_journal.draft(session_id=session_id, conversation_id=conversation_id,
        turn_id=turn_id, step_id=provider_step_id, kind=CollaborationEventKind.PREMISE_SELECTION,
        sender=event_journal.RUNTIME_ACTOR, recipient=event_journal.USER_ACTOR,
        payload=CollaborationPremiseSelectionPayload(premise_capture))
    return (
        premise_capture,
        selected,
    )


def prepare_exploration_request(
    session_id: str,
    conversation_id: str,
    turn_id: str,
    provider_step_id: str,
    user_event: CollaborationEventDraft,
) -> CollaborationEventDraft:
    request = event_journal.draft(session_id=session_id, conversation_id=conversation_id,
        turn_id=turn_id, step_id=provider_step_id,
        kind=CollaborationEventKind.LEAD_EXPLORATION_REQUEST,
        sender=event_journal.USER_ACTOR, recipient=event_journal.LEAD_ACTOR,
        payload=CollaborationLeadExplorationRequestPayload(user_event.event_id))
    return request


def prepare_exploration_adoption(
    session_id: str,
    conversation_id: str,
    turn_id: str,
    provider_step_id: str,
    user_event: CollaborationEventDraft,
    adoption: ExplorationAdoptionSelection,
    selected_text: str | None,
    adoption_source: CollaborationEvent | None,
) -> tuple[ExplorationAdoptionCapture, CollaborationEventDraft]:
    assert selected_text is not None and adoption_source is not None
    adoption_capture = ExplorationAdoptionCapture(
        user_event.event_id, adoption, selected_text, event_journal.new_id("task"),
        source_event_digest=hashlib.sha256(encode_collaboration_event(adoption_source)).hexdigest(),
        selected_material_digest=hashlib.sha256(selected_text.encode("utf-8")).hexdigest())
    capture_event = event_journal.draft(session_id=session_id, conversation_id=conversation_id,
        turn_id=turn_id, step_id=provider_step_id,
        kind=CollaborationEventKind.EXPLORATION_ADOPTION,
        sender=event_journal.USER_ACTOR, recipient=event_journal.LEAD_ACTOR,
        payload=CollaborationExplorationAdoptionPayload(adoption_capture))
    return (
        adoption_capture,
        capture_event,
    )


def superseding_turn_draft(
    session_id: str,
    conversation_id: str,
    turn_id: str,
    provider_step_id: str,
    user_event: CollaborationEventDraft,
    pending_proposal: CollaborationProposal | None,
    projection: CollaborationHistoryProjection,
) -> CollaborationEventDraft | None:
    if pending_proposal is not None:
        superseded = event_journal.draft(
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            step_id=pending_proposal.step_id,
            kind=CollaborationEventKind.STEP_STATE,
            sender=event_journal.RUNTIME_ACTOR,
            recipient=event_journal.USER_ACTOR,
            payload=CollaborationStepStatePayload(
                state=CollaborationStepState.SUPERSEDED,
                proposal_id=pending_proposal.proposal_id,
                proposal_revision=pending_proposal.proposal_revision,
                proposal_digest=pending_proposal.proposal_digest,
            ),
        )
        return superseded
    elif projection.current_recommendation_state is LeadRecommendationState.PENDING:
        request = projection.current_recommendation_request
        assert request is not None
        superseded = event_journal.draft(
            session_id=session_id, conversation_id=conversation_id, turn_id=turn_id,
            step_id=provider_step_id, kind=CollaborationEventKind.LEAD_RECOMMENDATION_RESULT,
            sender=event_journal.RUNTIME_ACTOR, recipient=event_journal.USER_ACTOR,
            payload=LeadRecommendationResultPayload(
                request.request_event_id, request.request_id, request.action_id,
                request.action_revision, request.action_digest, LeadRecommendationState.SUPERSEDED,
                "New ordinary input superseded the pending recommendation.",
                superseding_user_event_id=user_event.event_id,
            ),
        )
        return superseded
    else:
        return None
