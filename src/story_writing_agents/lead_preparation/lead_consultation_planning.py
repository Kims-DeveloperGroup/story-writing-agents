"""Consultation document, input, roster and proposal creation policies."""

from ..specialist_routing import AuthoringActivity, AuthoringScope
from ..errors import BackendProtocolError
from ..conversations import COLLABORATION_PHYSICAL_CALL_LIMIT, COLLABORATION_ROUND_LIMIT
from typing import Callable
from ..conversations import (
    CollaborationAssignment, CollaborationBudget, CollaborationEvent, CollaborationEventDraft,
    CollaborationHistoryProjection, CollaborationInputKind, CollaborationInputRevision,
    CollaborationLeadEvidence, CollaborationLeadJudgmentPayload, CollaborationMessagePayload,
)
from ..conversations.events import CollaborationPremiseFreshWorkAdmissionPayload
from ..conversations import (
    CollaborationProposal, CollaborationProposalBasis, CollaborationSideEffect,
    CollaborationStepKind,
)
from ..document_plan import DocumentNodeBinding, DocumentNodeSelection, DocumentPlan
from ..conversations import LeadAssignmentIntent
from ..runtime_resources.provider_call_accounting import LeadCallBudgetExceeded
from ..conversations import LeadProposalIntent, LeadRecommendationRequestPayload
from ..lead_task_transitions import LeadTaskRef
from ..lead_premise_decisions import PremiseAdmissionRef, PremiseDecisionSpec
from .recommendation_preparation import RecommendationCapability
from ..agents import SPECIALIST_IDS
from dataclasses import asdict
from . import authoring_authority, document_plan_selection
from ..runtime_resources import collaboration_event_journal as event_journal
import hashlib
import json
from ..lead_premise_decisions import premise_digest
from . import proposal_basis_values
import re
from dataclasses import replace
from ..specialist_routing import stage_is_focused_review, validate_stage_routing

__all__ = (
    'consultation_document_selection',
    'consultation_document_binding',
    'consultation_candidate_inputs',
    'inherit_final_review_inputs',
    'validate_consultation_plan',
    'prepare_consultation_assignments',
    'build_consultation_proposal',
)


def consultation_document_selection(
    intent: LeadProposalIntent,
    projection: CollaborationHistoryProjection,
    source_events: tuple[CollaborationEvent, ...],
) -> DocumentNodeSelection | None:
    document_selection = None
    eligible = tuple(view for view in projection.document_plans if view.plan.unresolved_target
        and any(type(event.payload) is CollaborationLeadJudgmentPayload
            and event.payload.document_plan is not None
            and event.payload.document_plan.ref.plan_id == view.plan.ref.plan_id
            and event.payload.authoring_scope is not None
            and event.payload.authoring_scope.task_id == intent.authoring_scope.task_id for event in source_events))
    if len(eligible) == 1:
        document_selection = DocumentNodeSelection(eligible[0].plan.ref,
            target_path=intent.authoring_scope.target_paths[0])
    return document_selection


def consultation_document_binding(
    session_id: str,
    step_id: str,
    scope: AuthoringScope,
    scope_event: CollaborationEventDraft,
    inputs: tuple[CollaborationInputRevision, ...],
    intent: LeadProposalIntent,
    *,
    preparation: RecommendationCapability | None,
    read_collaboration_events: Callable[[str, str], tuple[CollaborationEvent, ...]],
) -> tuple[DocumentNodeBinding, DocumentPlan]:
    facts = document_plan_selection.document_goal_facts(session_id, scope_event, scope.target_paths, preparation=preparation, read_collaboration_events=read_collaboration_events)
    proposed_plan = proposal_basis_values.build_document_plan(facts, scope, inputs, intent.core_document_readiness)
    selected = next(node for node in proposed_plan.nodes if node.target_path == scope.target_paths[0])
    task = LeadTaskRef(session_id=session_id, conversation_id=scope_event.conversation_id, task_id=scope.task_id, task_revision=scope.task_revision,
        scope_event_id=scope_event.event_id, scope_digest="sha256:" + hashlib.sha256(json.dumps(asdict(scope), sort_keys=True,
            separators=(",", ":"), ensure_ascii=False).encode()).hexdigest())
    document_node = DocumentNodeBinding(proposed_plan.ref, selected.node_id, task, task, step_id)
    return document_node, proposed_plan


def consultation_candidate_inputs(
    intent: LeadProposalIntent,
    inputs: tuple[CollaborationInputRevision, ...],
    document_node: DocumentNodeBinding,
) -> LeadProposalIntent:
    aliases = tuple(f"plan_candidate_{index}" for index, _ in enumerate(document_node.candidate_uses, 1))
    if not set(aliases).issubset(item.input_id for item in inputs):
        raise ValueError("document predecessor material must be captured before disclosure")
    intent = replace(intent, assignments=tuple(replace(item,
        input_ids=tuple(dict.fromkeys((*item.input_ids, *aliases)))) for item in intent.assignments))
    return intent


def inherit_final_review_inputs(
    intent: LeadProposalIntent,
    inputs: tuple[CollaborationInputRevision, ...],
    source_events: tuple[CollaborationEvent, ...],
    step_id: str,
) -> tuple[tuple[CollaborationInputRevision, ...], LeadProposalIntent]:
    requests = tuple(event.payload for event in source_events
                     if type(event.payload) is LeadRecommendationRequestPayload
                     and event.step_id == step_id)
    request = requests[-1] if requests else None
    source = None if request is None else next((event.payload for event in source_events
        if type(event.payload) is CollaborationProposal and (
            event.payload.proposal_id, event.payload.proposal_revision, event.payload.proposal_digest,
        ) == (request.source_proposal_id, request.source_proposal_revision, request.source_proposal_digest)), None)
    inherited = () if source is None else tuple(item for item in source.basis.inputs
        if re.fullmatch(r"selected_material_[1-9][0-9]*", item.input_id))
    if inherited:
        existing = {item.input_id: item for item in inputs}
        if any(item.input_id in existing and existing[item.input_id] != item for item in inherited):
            raise BackendProtocolError("final review changed the selected material capture")
        inputs += tuple(item for item in inherited if item.input_id not in existing)
        intent = replace(intent, assignments=tuple(replace(assignment,
            input_ids=tuple(dict.fromkeys((*assignment.input_ids, *(item.input_id for item in inherited)))),
        ) for assignment in intent.assignments))
    return inputs, intent


def validate_consultation_plan(
    intent: LeadProposalIntent,
    inputs: tuple[CollaborationInputRevision, ...],
    projection: CollaborationHistoryProjection,
    evidence: CollaborationLeadEvidence,
) -> tuple[tuple[LeadAssignmentIntent, ...], tuple[str, ...]]:
    if intent.authoring_scope is None and intent.premise_decision is None:
        raise BackendProtocolError("live proposal requires authoring scope or exact premise question")
    if intent.authoring_scope is not None:
        validate_stage_routing(intent.authoring_scope, tuple(item.agent_id for item in intent.assignments), intent.stage_plan,
            source_input_ids=tuple(item.input_id for item in inputs),
            required_condition_ids=(() if intent.stage_plan is None else authoring_authority.stage_required_condition_ids(tuple(inputs), evidence)),
            affected_paths=authoring_authority.authoring_affected_paths(inputs, evidence))
    assignments = intent.assignments
    specialist_ids = tuple(item.agent_id for item in assignments)
    canonical = tuple(
        agent_id for agent_id in SPECIALIST_IDS if agent_id in specialist_ids
    )
    if specialist_ids != canonical:
        raise BackendProtocolError(
            "lead assignments must use canonical specialist order"
        )
    known_input_ids = {item.input_id for item in inputs}
    assigned_input_ids = {
        input_id for item in assignments for input_id in item.input_ids
    }
    if assigned_input_ids != known_input_ids:
        raise BackendProtocolError(
            "lead proposal inputs must close exactly over runtime aliases"
        )
    required_reviewers = {
        reviewer
        for item in inputs
        if item.kind in {CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
                         CollaborationInputKind.WRITING_ARTIFACT}
        for reviewer in (authoring_authority.core_reviewer(item.locator),)
        if reviewer is not None
    }
    if stage_is_focused_review(intent.stage_plan):
        required_reviewers.intersection_update(specialist_ids)
    if not required_reviewers.issubset(specialist_ids):
        raise BackendProtocolError(
            "lead proposal omits a mandatory closed-fact reviewer"
        )
    assignment_inputs = {
        item.agent_id: frozenset(item.input_ids) for item in assignments
    }
    if any(
        (not stage_is_focused_review(intent.stage_plan) or reviewer in specialist_ids) and (
        reviewer not in assignment_inputs
        or item.input_id not in assignment_inputs[reviewer])
        for item in inputs
        if item.kind in {CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
                         CollaborationInputKind.WRITING_ARTIFACT}
        for reviewer in (authoring_authority.core_reviewer(item.locator),)
        if reviewer is not None
    ):
        raise BackendProtocolError(
            "mandatory reviewer assignment omits its closed-fact input"
        )
    if projection.rounds_spent >= COLLABORATION_ROUND_LIMIT:
        raise BackendProtocolError("specialist round three is unreachable")
    if intent.authoring_scope is not None and intent.authoring_scope.schema_version == 3 and intent.authoring_scope.activity in {
        AuthoringActivity.DRAFT, AuthoringActivity.REVISE,
    } and projection.rounds_spent:
        raise LeadCallBudgetExceeded("v3 authoring reserves round two for final review; clarify or freshly propose")
    return assignments, specialist_ids


def prepare_consultation_assignments(
    previous_proposal: CollaborationProposal | None,
    projection: CollaborationHistoryProjection,
    assignments: tuple[LeadAssignmentIntent, ...],
    allocated_limits: tuple[int, ...],
) -> tuple[int, str, tuple[CollaborationAssignment, ...]]:
    proposal_revision = (
        1 if previous_proposal is None else previous_proposal.proposal_revision + 1
    )
    proposal_id = event_journal.new_id("p")
    runtime_assignments = tuple(
        CollaborationAssignment(
            assignment_id=event_journal.new_id("a"),
            specialist_id=item.agent_id,
            round=projection.rounds_spent + 1,
            instruction=item.instruction,
            input_ids=item.input_ids,
            mode=item.mode,
            review_basis=item.review_basis,
            max_physical_calls=call_limit,
        )
        for item, call_limit in zip(
            assignments,
            allocated_limits,
            strict=True,
        )
    )
    return proposal_revision, proposal_id, runtime_assignments


def build_consultation_proposal(
    session_id: str,
    step_id: str,
    intent: LeadProposalIntent,
    projection: CollaborationHistoryProjection,
    inputs: tuple[CollaborationInputRevision, ...],
    required_calls: int,
    synthesis_calls: int,
    proposal_id: str,
    proposal_revision: int,
    runtime_assignments: tuple[CollaborationAssignment, ...],
    basis: CollaborationProposalBasis,
    document_node: DocumentNodeBinding | None,
    proposed_plan: DocumentPlan | None,
    *,
    read_collaboration_events: Callable[[str, str], tuple[CollaborationEvent, ...]],
) -> CollaborationProposal:
    budget = CollaborationBudget(
        round_limit=COLLABORATION_ROUND_LIMIT,
        physical_call_limit=COLLABORATION_PHYSICAL_CALL_LIMIT,
        rounds_spent=projection.rounds_spent,
        rounds_added=1,
        physical_calls_spent=projection.physical_calls_spent,
        max_additional_physical_calls=required_calls,
        synthesis_max_physical_calls=synthesis_calls,
    )
    spec = None
    if intent.premise_decision is not None:
        if projection.rounds_spent:
            raise LeadCallBudgetExceeded("premise analysis cannot restart its terminal round")
        question = intent.premise_decision
        current_events = read_collaboration_events(session_id, projection.conversation_id)
        origin = next((event for event in reversed(current_events)
            if event.turn_id == projection.current_turn_id and event.sender == event_journal.USER_ACTOR
            and type(event.payload) is CollaborationMessagePayload), None)
        if origin is None:
            raise BackendProtocolError("premise question has no exact current USER origin")
        admitted = next((event for event in current_events if event.step_id == step_id
            and type(event.payload) is CollaborationPremiseFreshWorkAdmissionPayload), None)
        spec = PremiseDecisionSpec(question.question_id, question.slot_id, question.question,
            question.dependent_domains, origin.event_id,
            None if admitted is None or admitted.payload.admission.prior_authoring_source_ref is None
            else admitted.payload.admission.prior_authoring_source_ref.task,
            None if admitted is None else PremiseAdmissionRef(admitted.event_id, premise_digest(admitted.payload.admission)))
    proposal = CollaborationProposal.create(
        step_id=step_id,
        proposal_id=proposal_id,
        proposal_revision=proposal_revision,
        step_kind=CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION,
        purpose=intent.purpose,
        assignments=runtime_assignments,
        user_impact=proposal_basis_values.premise_user_impact(spec, step_id, inputs,
            read_collaboration_events(session_id, projection.conversation_id)),
        side_effects=(CollaborationSideEffect.PROVIDER_INVOCATION,),
        budget=budget,
        basis=basis,
        authoring_scope=intent.authoring_scope,
        premise_decision=spec,
     stage_plan=intent.stage_plan, document_node=document_node, document_plan=proposed_plan)
    return proposal
