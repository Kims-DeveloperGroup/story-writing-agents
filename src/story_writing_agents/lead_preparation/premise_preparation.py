"""Premise qualification and fresh-work admission through caller-owned reads."""

from ..specialist_routing import AuthoringScope
from ..errors import BackendProtocolError
from typing import Callable
from ..conversations import CollaborationConfirmationDecision
from ..conversations import CollaborationConfirmationPayload
from ..conversations import CollaborationEvent
from ..conversations import CollaborationEventDraft
from ..conversations import CollaborationEventKind
from ..conversations import CollaborationExecutionOutcomeKind
from ..conversations import CollaborationExecutionResult
from ..conversations import CollaborationHistoryProjection
from ..conversations import CollaborationInputKind
from ..conversations import CollaborationInputRevision
from ..conversations import CollaborationInputState
from ..conversations import CollaborationMessagePayload
from ..conversations.events import CollaborationPremiseFreshWorkAdmissionPayload
from ..conversations.events import CollaborationPremiseSource
from ..conversations import CollaborationProviderCallPayload
from ..conversations import CollaborationProviderCallState
from ..conversations.events import CollaborationSharedPremise
from ..conversations import CollaborationSpecialistAssessment
from ..conversations import CollaborationSpecialistReplyPayload
from ..conversations import CollaborationStepState
from ..conversations import CollaborationStepStatePayload
from ..conversations import LeadProposalIntent
from ..conversations import LeadRecommendationRequestPayload
from typing import Mapping
from ..lead_premise_decisions import PremiseAdmissionRef
from ..lead_premise_decisions import PremiseAuthoringSourceRef
from ..lead_premise_decisions import PremiseAuthoringTaskRef
from ..lead_premise_decisions import PremiseChoicePolicy
from ..lead_premise_decisions import PremiseDecisionAction
from ..lead_premise_decisions import PremiseDecisionCost
from ..lead_premise_decisions import PremiseDecisionSelection
from ..lead_premise_decisions import PremiseFreshWorkAdmission
from ..lead_premise_decisions import PremiseReportRef
from ..lead_premise_decisions import PremiseSelectionCapture
from ..specialist_routing import artifact_domain
from . import authoring_authority
from typing import cast
from ..conversations.events import encode_collaboration_event
from ..runtime_resources import collaboration_event_journal as event_journal
import hashlib
from ..lead_premise_decisions import premise_digest
from ..lead_premise_decisions import resolve_premise_choice
from ..conversations.events import CollaborationLeadSynthesisPayload
from ..conversations.events import CollaborationProposal
from ..collaboration_execution_results import LeadSynthesisResult
from ..lead_premise_decisions import PremiseDecisionReport
from ..lead_premise_decisions import PremiseDecisionSummary
from . import collaboration_response_decoding

__all__ = (
    'capture_shared_premises',
    'prepare_premise_admission',
    'validate_premise_control',
    'premise_synthesis_provenance',
    'premise_synthesis_result',
)

def capture_shared_premises(
    session_id: str,
    intent: LeadProposalIntent,
    inputs: tuple[CollaborationInputRevision, ...],
    *,
    read_session_authoring_events: Callable[[str], tuple[CollaborationEvent, ...]],
) -> tuple[CollaborationSharedPremise, ...]:
    if intent.authoring_scope is None or intent.authoring_scope.schema_version < 3:
        return ()
    available = {item.input_id: item for item in inputs}
    events = read_session_authoring_events(session_id)
    captured = []
    for premise in intent.shared_premises:
        if premise.selected_premise is None or not premise.selected_premise.strip():
            raise BackendProtocolError("unresolved premise requires clarification or a fresh proposal")
        if not premise.basis_input_ids:
            raise BackendProtocolError("selected premise requires an immutable PRESENT source")
        sources = []
        for alias in premise.basis_input_ids:
            item = available.get(alias)
            if item is None or item.state is not CollaborationInputState.PRESENT:
                raise BackendProtocolError("premise source is missing or unavailable")
            owner = None
            if item.kind in {CollaborationInputKind.USER_INPUT, CollaborationInputKind.CONVERSATION_EVENT,
                             CollaborationInputKind.ARTIFACT_CANDIDATE}:
                if item.kind is CollaborationInputKind.USER_INPUT:
                    turn_id = item.locator.removeprefix("conversation/").removesuffix("/user_input")
                    matches = [event for event in events if event.turn_id == turn_id
                               and event.sender.kind.value == "user"]
                elif item.kind is CollaborationInputKind.CONVERSATION_EVENT:
                    event_id = item.locator.removeprefix("conversation/")
                    matches = [event for event in events if event.event_id == event_id]
                else:
                    candidate_id = item.locator.removeprefix("artifact-candidate/")
                    matches = [event for event in events
                               if getattr(event.payload, "candidate_id", None) == candidate_id]
                owners = {event.conversation_id for event in matches}
                if len(owners) != 1:
                    raise BackendProtocolError("premise source has no unique durable owner")
                if any(type(event.payload) is CollaborationSpecialistReplyPayload
                       and event.payload.assessment is not CollaborationSpecialistAssessment.DRAFTED
                       for event in matches):
                    raise BackendProtocolError("unresolved predecessor cannot supply a shared premise")
                for event in matches:
                    if type(event.payload) is not CollaborationSpecialistReplyPayload:
                        continue
                    reply = event.payload
                    completed = any(item.conversation_id == event.conversation_id
                                    and type(item.payload) is CollaborationExecutionResult
                                    and item.payload.execution_attempt_id == reply.execution_attempt_id
                                    and item.payload.proposal_digest == reply.proposal_digest
                                    and item.payload.overall_outcome.value == "succeeded"
                                    for item in events)
                    confirmed = any(item.conversation_id == event.conversation_id
                                    and type(item.payload) is CollaborationConfirmationPayload
                                    and item.payload.decision is CollaborationConfirmationDecision.CONFIRM
                                    and (item.payload.proposal_id, item.payload.proposal_revision, item.payload.proposal_digest)
                                    == (reply.proposal_id, reply.proposal_revision, reply.proposal_digest)
                                    for item in events)
                    if not completed or not confirmed:
                        raise BackendProtocolError("unfinished predecessor requires clarification or a fresh proposal")
                owner = owners.pop()
            sources.append(CollaborationPremiseSource(item, owner))
        captured.append(CollaborationSharedPremise(premise.slot_id, premise.selected_premise, tuple(sources)))
    return tuple(captured)


def validate_premise_control(
    session_id: str,
    conversation_id: str,
    selection: PremiseDecisionSelection,
    events: tuple[CollaborationEvent, ...],
    projection: CollaborationHistoryProjection,
    *,
    read_session_authoring_events: Callable[[str], tuple[CollaborationEvent, ...]],
) -> None:
    if selection.source_conversation_id != conversation_id:
        raise ValueError("premise choice must belong to the focused conversation")
    report = projection.premise_report
    if report is None or projection.premise_report_event_id != selection.report_event_id:
        raise ValueError("premise choice report is stale or unavailable")
    if (selection.report_digest, selection.question_id) != (report.report_digest, report.spec.question_id):
        raise ValueError("premise choice changed the exact report identity")
    if selection.action is PremiseDecisionAction.CHOOSE:
        actual = {(item.option_id, item.material_digest) for item in report.options}
        allowed = ({(selection.option_id, selection.material_digest)} if selection.choice_policy is PremiseChoicePolicy.USER_CHOICE
            else {(item.option_id, item.material_digest) for item in selection.allowed_choices})
        if not allowed <= actual:
            raise ValueError("premise USER choice references unavailable or changed whole material")
        if selection.choice_policy is PremiseChoicePolicy.USER_CHOICE:
            resolve_premise_choice(selection, report)
    if (
        projection.premise_selection is not None
        and projection.premise_selection.selection.report_event_id == selection.report_event_id
        and selection.action is not PremiseDecisionAction.CHOOSE
    ):
        raise ValueError("reject/defer cannot revoke an already captured independent authoring choice")
    if projection.premise_decision is not None and projection.premise_decision.action is PremiseDecisionAction.REJECT:
        raise ValueError("a rejected premise report cannot be chosen")
    if selection.authoring_task_id is not None:
        scope = authoring_authority.authoring_tasks(read_session_authoring_events(session_id)).get(selection.authoring_task_id)
        local = authoring_authority.authoring_tasks(events).get(selection.authoring_task_id)
        if scope is None or local is None or scope != local or scope.task_revision != selection.expected_authoring_task_revision:
            raise ValueError("premise successor requires the exact current task revision")
    for path in selection.target_paths:
        if artifact_domain(path) is None:
            raise ValueError("premise target is not an eligible authoring artifact")
    source = next((event for event in events if event.event_id == selection.report_event_id), None)
    result = next((event for event in events if type(event.payload) is CollaborationExecutionResult
                   and event.payload.synthesis_result_event_id == selection.report_event_id
                   and event.payload.overall_outcome is CollaborationExecutionOutcomeKind.SUCCEEDED), None)
    if source is None or result is None or not any(
        type(event.payload) is CollaborationStepStatePayload
        and event.payload.state is CollaborationStepState.COMPLETED
        and event.payload.execution_result_event_id == result.event_id for event in events
    ):
        raise ValueError("premise choice requires a successful terminal analysis")


def prepare_premise_admission(
    session_id: str,
    conversation_id: str,
    turn_id: str,
    step_id: str,
    invocation_id: str,
    question_id: str | None,
    offered_tasks: Mapping[tuple[str, int], AuthoringScope | None],
    events: tuple[CollaborationEvent, ...],
    projection: CollaborationHistoryProjection,
    *,
    premise_capture: PremiseSelectionCapture | None = None,
    read_session_authoring_events: Callable[[str], tuple[CollaborationEvent, ...]],
) -> tuple[PremiseFreshWorkAdmission, CollaborationEventDraft]:
    origins = tuple(event for event in events if event.turn_id == turn_id
                    and event.sender == event_journal.USER_ACTOR and type(event.payload) is CollaborationMessagePayload)
    if len(origins) != 1:
        raise BackendProtocolError("fresh work needs one exact durable USER input")
    origin = origins[0]
    old = next((event for event in reversed(events) if type(event.payload) is CollaborationPremiseFreshWorkAdmissionPayload), None)
    parent = None if old is None else PremiseAdmissionRef(old.event_id, premise_digest(old.payload.admission))
    report = projection.premise_report
    report_ref = None if report is None else PremiseReportRef(conversation_id,
        cast(str, projection.premise_report_event_id), report.report_digest, report.spec.question_id)
    source_ref = None
    offered_authoring_tasks = tuple(PremiseAuthoringTaskRef(*pair) for pair in offered_tasks)
    selected_authoring_task = (
        None if premise_capture is None else PremiseAuthoringTaskRef(
            premise_capture.authoring_task_id, premise_capture.authoring_task_revision
        )
    )
    if premise_capture is None:
        prior_task_pair = next(iter(offered_tasks), (None, None))
        current = authoring_authority.authoring_tasks(read_session_authoring_events(session_id))
        scope = current.get(prior_task_pair[0])
        if scope is not None:
            source, synthesis, _prefix = authoring_authority.completed_authoring_source(scope, events)
            steps = {source.step_id, *(event.step_id for event in events
                if type(event.payload) is LeadRecommendationRequestPayload and (
                    event.payload.source_proposal_id, event.payload.source_proposal_revision,
                    event.payload.source_proposal_digest
                ) == (source.proposal_id, source.proposal_revision, source.proposal_digest))}
            calls = tuple(event.payload.call_id for event in events if event.step_id in steps
                and type(event.payload) is CollaborationProviderCallPayload
                and event.payload.state is CollaborationProviderCallState.SUBMITTED)
            rounds = sum(event.step_id in steps and type(event.payload) is CollaborationStepStatePayload
                and event.payload.state is CollaborationStepState.RUNNING for event in events)
            source_ref = PremiseAuthoringSourceRef(PremiseAuthoringTaskRef(scope.task_id, scope.task_revision),
                PremiseDecisionCost(source.step_id, calls, rounds, source.proposal_id,
                    source.proposal_revision, source.proposal_digest, synthesis.payload.execution_attempt_id))
    admission = PremiseFreshWorkAdmission(origin.event_id,
        "sha256:" + hashlib.sha256(encode_collaboration_event(origin)).hexdigest(),
        step_id, invocation_id, 2, question_id, offered_authoring_tasks, selected_authoring_task,
        parent, report_ref, source_ref)
    draft = event_journal.draft(session_id=session_id, conversation_id=conversation_id,
        turn_id=turn_id, step_id=step_id, kind=CollaborationEventKind.PREMISE_FRESH_WORK_ADMISSION,
        sender=event_journal.RUNTIME_ACTOR, recipient=event_journal.LEAD_ACTOR,
        payload=CollaborationPremiseFreshWorkAdmissionPayload(admission))
    return (
        admission,
        draft,
    )


def premise_synthesis_provenance(
    current: tuple[CollaborationEvent, ...],
    proposal: CollaborationProposal,
    attempt_id: str,
    conversation_id: str,
) -> tuple[PremiseDecisionCost, PremiseFreshWorkAdmission | None, PremiseReportRef | None, PremiseAdmissionRef | None]:
    calls = tuple(event.payload.call_id for event in current
        if event.step_id == proposal.step_id and type(event.payload) is CollaborationProviderCallPayload
        and event.payload.state is CollaborationProviderCallState.SUBMITTED)
    cost = PremiseDecisionCost(proposal.step_id, calls, 1, proposal.proposal_id,
        proposal.proposal_revision, proposal.proposal_digest, attempt_id)
    admission = next((event.payload.admission for event in current
        if event.step_id == proposal.step_id and type(event.payload) is CollaborationPremiseFreshWorkAdmissionPayload), None)
    earlier = next((event for event in reversed(current) if type(event.payload) is CollaborationLeadSynthesisPayload
        and event.payload.premise_report is not None and event.step_id != proposal.step_id), None)
    earlier_ref = None if earlier is None else PremiseReportRef(conversation_id, earlier.event_id,
        earlier.payload.premise_report.report_digest, earlier.payload.premise_report.spec.question_id)
    cost_head = next((event for event in reversed(current) if type(event.payload) is CollaborationPremiseFreshWorkAdmissionPayload), None)
    parent_ref = (admission.prior_admission_ref if admission is not None else
        None if cost_head is None else PremiseAdmissionRef(cost_head.event_id, premise_digest(cost_head.payload.admission)))
    return cost, admission, earlier_ref, parent_ref

def premise_synthesis_result(
    proposal: CollaborationProposal,
    attempt_id: str,
    ids: tuple[str, ...],
    replies: tuple[CollaborationSpecialistReplyPayload, ...],
    summary: PremiseDecisionSummary,
    report: PremiseDecisionReport,
) -> LeadSynthesisResult:
    payload = CollaborationLeadSynthesisPayload(proposal.proposal_id,
        proposal.proposal_revision, proposal.proposal_digest, attempt_id,
        summary.message, ids, collaboration_response_decoding.required_unresolved_input_ids(replies),
        proposal.basis.evidence, premise_report=report)
    return LeadSynthesisResult(payload, None)
