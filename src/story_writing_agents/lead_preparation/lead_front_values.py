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
)
