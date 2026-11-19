"""Pure Lead front request material values."""
from ..authoring_admission import AdmissionSource
from ..specialist_routing import AuthoringScope
from ..conversations.event_vocabulary import CollaborationConfirmationDecision
from ..conversations.events import CollaborationEvent
from ..conversations.context import CollaborationHistoryProjection
from ..conversations.events import CollaborationInputRevision
from ..document_plan import DocumentNodeBinding
from ..document_plan import DocumentNodeSelection
from ..lead_provider_contracts import LeadExplorationConstraints
from ..lead_provider_contracts import LeadProviderConstraints
from typing import Mapping
from ..conversations.context import artifact_objective_context
from dataclasses import asdict
from ..conversations.events import authoring_scope_to_dict
from ..document_plan import document_node_binding_to_dict
from ..document_plan import document_node_selection_to_dict
from . import lead_material_inputs
from ..agent_task_projection import project_agent_task_metadata
from . import reply_contract_values
from ..conversations import COLLABORATION_PHYSICAL_CALL_LIMIT
from ..conversations import COLLABORATION_ROUND_LIMIT
from ..conversations import CollaborationInputKind
from ..conversations import CollaborationInputState
from ..conversations import CollaborationInvocationKind
from ..conversations import CollaborationProposal
from ..conversations import CollaborationProviderCallPayload
from ..conversations import CollaborationProviderCallState
from ..document_plan import DocumentCandidateUseIntent
from ..document_plan import DocumentPlanningGoalRef
from ..lead_exploration import ExplorationAdoptionCapture
from ..conversations import LeadProposalRelation
from ..lead_task_transitions import LeadTaskTransitionReceipt
from ..lead_premise_decisions import PremiseSelectionCapture
from .recommendation_preparation import RecommendationCapability
from ..agents import SPECIALIST_IDS


def lead_front_material_prefix(
    document_selection: DocumentNodeSelection | None,
    document_node: DocumentNodeBinding | None,
    candidate_material: list[dict[str, object]],
    conversation_id: str,
    constraints: LeadProviderConstraints | LeadExplorationConstraints,
    historical_user_sources: tuple[tuple[AdmissionSource, str], ...],
    objective_projection: CollaborationHistoryProjection,
) -> dict[str, object]:
    return {
        **({"document_selection": document_node_selection_to_dict(document_selection)} if document_selection is not None else {}),
        **({"document_node": document_node_binding_to_dict(document_node)} if document_node is not None else {}),
        **({"plan_candidate_material": candidate_material} if candidate_material else {}),
        "conversation_id": conversation_id,
        "provider_constraints": constraints.prompt_facts(),
        "authoring_user_sources": lead_material_inputs.authoring_user_materials(historical_user_sources),
        "artifact_objectives": artifact_objective_context(objective_projection),
    }

def lead_front_material_suffix(
    objective_projection: CollaborationHistoryProjection,
    objective_events: tuple[CollaborationEvent, ...],
    binding_prior: AuthoringScope | None,
    ordinary_front_cap: int | None,
    successor_only: bool,
    inputs: tuple[CollaborationInputRevision, ...],
) -> dict[str, object]:
    return {
        "proposal_drafting_source_facts": [
            {
                "input_id": draft.event_id,
                "source_conversation_id": draft.conversation_id,
                "revision_digest": draft.content_digest,
                "authoring_scope": (
                    None if getattr(event.payload, "authoring_scope", None) is None
                    else authoring_scope_to_dict(event.payload.authoring_scope)
                ),
            }
            for draft in objective_projection.offered_draft_sources
            for event in objective_events if event.event_id == draft.event_id
        ],
        "current_writing_scope": (None if binding_prior is None else
            project_agent_task_metadata(authoring_scope_to_dict(binding_prior), "authoring_scope")),
                        "workflow": "lead_front_channel",
                        **({"existing_task_call_limit": ordinary_front_cap,
                            "existing_task_call_limit_reason": (
                                "The exhausted source revision cannot continue. Only fresh local successor revisions are offered; these Lead/READ/correction calls belong to the new finite revision budget, while old scope, calls and rounds remain unchanged."
                                if successor_only else "Preserve the offered confirmed task's preparation and full final-review budget. This bound also limits fresh alternatives in this invocation; clarify or freshly propose when infeasible."
                            )} if ordinary_front_cap is not None else {}),
                        "recommended_action_source_aliases": [] if successor_only else [
                            "current_response", *(item.event_id for item in objective_projection.offered_draft_sources),
                            *(item.input_id for item in inputs),
                        ],
    }

def recommendation_preparation_material(
    request_event: CollaborationEvent,
    decision_event: CollaborationEvent,
    start_event: CollaborationEvent,
    inputs: tuple[CollaborationInputRevision, ...],
    candidate_material_by_input: Mapping[CollaborationInputRevision, dict[str, object]],
    objective_events: tuple[CollaborationEvent, ...],
) -> dict[str, object]:
    material = {
        "request": asdict(request_event.payload),
        "decision": asdict(decision_event.payload),
        "start": asdict(start_event.payload),
        "selected_inputs": [candidate_material_by_input[item] if item in candidate_material_by_input
                            else reply_contract_values.collaboration_input_value(item, objective_events,
                                evidence=request_event.payload.basis.evidence)
                            for item in inputs],
        "instruction": (
            "The actual REVISE decision authorizes only a fresh RECOMMENDATION with recommended_action. "
            "Do not prepare an artifact, propose a consultation, or transition an objective."
            if decision_event.payload.decision.kind is CollaborationConfirmationDecision.REVISE
            else "Confirm already authorized this exact preparation. Return its exact artifact/review proposal "
            "or exact consultation proposal; ask a focused QUESTION only for a material blocker. "
            "Never recommend or ask to confirm this preparation again. Match the frozen action, "
            "targets, sources and objective. For a new artifact objective use START with the exact "
            "request instruction and targets; otherwise CONTINUE its offered objective."
        ),
    }
    return material


__all__ = (
    'lead_front_material_prefix',
    'lead_front_material_suffix',
    'recommendation_preparation_material',
    'document_lineage_front_cap',
    'front_budget_limits',
    'adopted_idea_input',
    'selected_premise_input',
    'document_candidate_material_inputs',
    'front_submitted_call_count',
)


def document_lineage_front_cap(ordinary_front_cap: int | None, inherited_calls: int, inherited_rounds: int) -> int:
    ordinary_front_cap = min(reply_contract_values.CONTEXT_INVOCATION_CALL_LIMIT if ordinary_front_cap is None else ordinary_front_cap,
        max(0, COLLABORATION_PHYSICAL_CALL_LIMIT - inherited_calls))
    if inherited_rounds >= COLLABORATION_ROUND_LIMIT:
        ordinary_front_cap = 0
    return ordinary_front_cap

def front_budget_limits(ordinary_front_cap: int | None, task_receipt: LeadTaskTransitionReceipt | None, document_goal: DocumentPlanningGoalRef | None, projection: CollaborationHistoryProjection | None, document_node: DocumentNodeBinding | None, preparation: RecommendationCapability | None, previous_proposal: CollaborationProposal | None, expected_relation: LeadProposalRelation) -> tuple[int | None, bool]:
    if (document_node is None and document_goal is not None and task_receipt is None
            and projection is not None):
        current_view = next((view for view in projection.document_plans if view.plan.ref.plan_id == document_goal.plan_id), None)
        if current_view is not None:
            ordinary_front_cap = min(reply_contract_values.CONTEXT_INVOCATION_CALL_LIMIT if ordinary_front_cap is None else ordinary_front_cap,
                                     current_view.remaining_physical_calls)
    successor_only = (document_node is None and document_goal is None and
        ordinary_front_cap is not None and ordinary_front_cap < 1
        and preparation is None and previous_proposal is None
        and expected_relation is LeadProposalRelation.NEW_STEP
    )
    if successor_only:
        # A fresh revision owns new finite accounting, never the exhausted source's calls.
        # Reserve full three-role draft/synthesis plus two preparation and four final calls.
        ordinary_front_cap = min(reply_contract_values.CONTEXT_INVOCATION_CALL_LIMIT,
                                 COLLABORATION_PHYSICAL_CALL_LIMIT - (2 * len(SPECIALIST_IDS) + 4))
    return ordinary_front_cap, successor_only

def adopted_idea_input(capture: ExplorationAdoptionCapture, event_id: str) -> CollaborationInputRevision:
    return CollaborationInputRevision(
        input_id="adopted_idea", kind=CollaborationInputKind.CONVERSATION_EVENT,
        state=CollaborationInputState.PRESENT,
        locator=f"conversation/{event_id}",
        revision_digest=f"sha256:{capture.selected_material_digest}",
    )

def selected_premise_input(capture: PremiseSelectionCapture, event_id: str) -> CollaborationInputRevision:
    return CollaborationInputRevision(
        input_id="selected_premise", kind=CollaborationInputKind.CONVERSATION_EVENT,
        state=CollaborationInputState.PRESENT, locator=f"conversation/{event_id}",
        revision_digest="sha256:" + capture.material_digest,
    )

def document_candidate_material_inputs(intents: tuple[DocumentCandidateUseIntent, ...], inputs: tuple[CollaborationInputRevision, ...]) -> tuple[CollaborationInputRevision, ...]:
    candidate_aliases = {f"plan_candidate_{index}" for index, _ in enumerate(intents, 1)}
    candidate_inputs = tuple(item for item in inputs if item.input_id in candidate_aliases)
    return candidate_inputs

def front_submitted_call_count(events: tuple[CollaborationEvent, ...], step_id: str) -> int:
    return sum(type(event.payload) is CollaborationProviderCallPayload
                and event.step_id == step_id
                and event.payload.invocation_kind is not CollaborationInvocationKind.LEAD_DISCUSSION
                and event.payload.state is CollaborationProviderCallState.SUBMITTED
                for event in events)
