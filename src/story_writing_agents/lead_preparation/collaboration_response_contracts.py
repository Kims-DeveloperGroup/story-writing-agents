"""Pure specialist/synthesis contracts and structured-response correction values."""

from ..agent_context.contracts.call_offers import ContextJsonObjectResultContract
from ..agent_context.invocation.provider import ContextProviderTerminalCode
from ..agents import SPECIALIST_IDS
from ..conversations.event_vocabulary import ArtifactReviewDecision
from ..conversations.events import (
    CollaborationAssignment,
    CollaborationLeadEvidence,
    specialist_assessments_for_mode,
)
from ..errors import BackendProtocolError
from ..lead_provider_contracts import (
    LeadProviderConstraints,
    LeadResultRuleSeed,
    PremiseDecisionConstraints,
    nonempty_text_schema,
)
from ..specialist_routing import (
    AuthoringActivity,
    AuthoringIntent,
    AuthoringScope,
    SpecialistAssignmentMode,
)
from . import reply_contract_values
from ..conversations.events import CollaborationInputRevision
from ..lead_provider_contracts import LeadDiscussionConstraints
from ..lead_provider_contracts import LeadExplorationConstraints
from ..lead_exploration import LeadInteractionMode
from ..conversations.event_vocabulary import LeadProposalRelation
from ..lead_task_transitions import LeadTaskTransitionReceipt
from typing import Mapping
from . import response_rules
from .synchronization_review_response import synchronization_findings_schema
from ..conversations import CollaborationEvent
from ..lead_task_transitions import LeadTaskAction


LEAD_CORRECTABLE_RESPONSE_REJECTION_CODES = frozenset(
    {
        ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,
        ContextProviderTerminalCode.RESPONSE_CORRELATION_MISMATCH,
        ContextProviderTerminalCode.RESPONSE_SEMANTIC_REJECTED,
        ContextProviderTerminalCode.ANSWER_VALIDATION_FAILED,
    }
)


LEAD_RESPONSE_CORRECTION_INSTRUCTION = (
    "Correct the rejected response using the current native output schema and "
    "the exact frozen guidance in response_correction.bound_contract. "
    "Use only the actions currently offered for this call, preserve its actual "
    "task constraints, and do not invent completion or authority."
)


def specialist_result_contract(
    assignment: CollaborationAssignment,
    *, premise_dependencies: tuple[tuple[str, str], ...] = (),
) -> ContextJsonObjectResultContract:
    if assignment.mode is SpecialistAssignmentMode.DRAFT and premise_dependencies:
        conflicts = [reply_contract_values.closed_result_object({
            "slot_id": {"type": "string", "enum": [slot]},
            "capture_digest": {"type": "string", "enum": [digest]},
            "reason": nonempty_text_schema(),
        }, description="Hold this dependent draft for a conflict with the exact USER-selected whole premise; do not alter USER direction.") for slot, digest in premise_dependencies]
        return reply_contract_values.provider_result_contract("specialist-assignment-response-v5", reply_contract_values.closed_result_object({
            "assessment": {"type": "string", "enum": ["drafted", "conflict", "insufficient_evidence"]},
            "message": nonempty_text_schema(),
            "premise_conflicts": {"type": "array", "minItems": 0, "maxItems": len(premise_dependencies),
                "items": conflicts[0] if len(conflicts) == 1 else {"anyOf": conflicts}},
        }, description="Draft under the unchanged exact USER premise. Report a typed conflict instead of draft proof if that dependency cannot be honored; only a fresh USER choice/revision may change it."))
    if assignment.mode is SpecialistAssignmentMode.PREMISE_ANALYSIS:
        return reply_contract_values.provider_result_contract("premise-analysis-response-v1",
            PremiseDecisionConstraints("analyst", assignment.input_ids).analysis_schema())
    if assignment.mode is None:
        raise BackendProtocolError("live specialist contract requires an explicit mode")
    return reply_contract_values.provider_result_contract(
        "specialist-assignment-response-v4" if assignment.mode is SpecialistAssignmentMode.CONSTRAINT_ASSESSMENT else "specialist-assignment-response-v3",
        reply_contract_values.closed_result_object(
            {
                "assessment": {
                    "enum": [item.value for item in specialist_assessments_for_mode(assignment.mode)],
                    "type": "string",
                },
                "message": nonempty_text_schema(),
            },
            description=(
            "Return the assigned draft in response_text with draft_completed, or report "
            "insufficient_evidence when the task cannot be grounded; do not write documents."
        ) if assignment.mode is SpecialistAssignmentMode.DRAFT else (
            "Assess only declared constraints: use constraints_assessed for a grounded assessment, "
            "concerns_found/conflicts_found for reservations or contradictions, and insufficient_evidence "
            "for unknowns. Separate facts, interpretations, provisional ideas and uncertainty; this "
            "does not draft, approve a final candidate, or complete the authoring task."
        ) if assignment.mode is SpecialistAssignmentMode.CONSTRAINT_ASSESSMENT else (
            "Review the assigned sources: use supports_proposal for support, concerns_found "
            "for reservations, conflicts_found for contradictions, or insufficient_evidence "
            "when a grounded judgment is impossible. Explain the assessment without writing "
            "documents."
        ),
        ),
    )


def lead_synthesis_result_contract(
    *,
    evidence: CollaborationLeadEvidence,
    input_ids: tuple[str, ...],
    reply_event_ids: tuple[str, ...],
    allow_next_intent: bool,
    artifact_review: bool,
    authoring_scope: AuthoringScope,
    constraints: LeadProviderConstraints | None = None,
    synchronization_required: bool = False,
) -> LeadResultRuleSeed:
    del evidence
    allow_next_intent = allow_next_intent and not (authoring_scope.schema_version == 3 and authoring_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE})
    facts = constraints or LeadProviderConstraints(
        input_ids=(*input_ids, *reply_event_ids), scope=authoring_scope, continuation=True,
    )
    message = {"message": {
        **nonempty_text_schema(),
        "description": (
            "Nonempty UTF-8 editorial account against the shared brief, with treatments, "
            "reasons, and residual costs for each material recommendation."
        ),
    }}
    synthesis_guidance = (
        "Account for each material recommendation as adopted, modified, deferred, or "
        "rejected; explain its treatment against the shared brief and remaining tradeoffs. "
        "Distinguish factual conflict, predicted effect, preference, and missing information. "
        "Rejecting a remedy never erases its concern. Reviewed or completed does not mean "
        "unanimous support, publication, or satisfaction of a pending document-change request."
    )
    synchronization = (
        {"synchronization_findings": synchronization_findings_schema(reply_event_ids)}
        if synchronization_required else {}
    )
    if synchronization_required and not artifact_review:
        if (authoring_scope.intent is not AuthoringIntent.REVIEW_ONLY
                or authoring_scope.activity is not AuthoringActivity.REVIEW):
            raise ValueError("current synchronization findings require review-only authority")
        return reply_contract_values.lead_result_rule_seed(
            "consultation-synchronization-summary-v1",
            reply_contract_values.closed_result_object(
                {**message, **synchronization},
                description=(
                    "Review the exact current documents against the disclosed synchronization conditions. "
                    "Report which conditions the delivered READs and specialist findings support. Preserve "
                    "unknowns and unresolved concerns. This consultation evaluates actual files and grants "
                    "no drafting, Apply, or Commit authority. " + synthesis_guidance
                ),
            ),
        )
    contract_id = "consultation-summary-v4"
    if not artifact_review:
        variants = [reply_contract_values.closed_result_object(message,
            description=(
                "Conclude this consultation when the replies support a complete synthesis and no "
                "additional specialist round or proposal-drafting recommendation is needed."
            ),
        )]
        if allow_next_intent:
            variants.append(reply_contract_values.closed_result_object({
                **message,
                "next_intent": reply_contract_values.closed_result_object({
                    "purpose": nonempty_text_schema(),
                    "assignments": {
                        "items": facts.assignment_schema(),
                        "maxItems": len(SPECIALIST_IDS),
                        "minItems": 1,
                        "type": "array",
                    },
                },
                    description=(
                        "Describe the unresolved purpose and assign every affected specialist the"
                        " required prior inputs and replies."
                    ),
                ),
            },
                description=(
                    "Request another specialist round when unresolved issues require more work "
                    "and the offered round budget permits it; preserve concerns and source "
                    "lineage."
                ),
            ))
        variants.append(reply_contract_values.closed_result_object({
            **message,
            "recommended_action": facts.recommendation_schema(),
        },
            description=(
                "Recommend a separately confirmed proposal-drafting step when the consultation "
                "has established what should be prepared next."
            ),
        ))
        return reply_contract_values.lead_result_rule_seed(contract_id, {
            "anyOf": variants,
            "description": (
                "Synthesize the specialist replies independently, retaining every material "
                "concern and grounding the result in their shared task and evidence. "
                + synthesis_guidance
            ),
        })
    contract_id = "artifact-review-summary-v5" if synchronization_required else "artifact-review-summary-v4"
    return reply_contract_values.lead_result_rule_seed(
        contract_id,
        reply_contract_values.closed_result_object({
            "decision": {
                "enum": [item.value for item in ArtifactReviewDecision],
                "type": "string",
            },
            **message,
            **synchronization,
        },
            description=(
                "Integrate all reviews of the exact immutable candidate: "
                "ready_for_apply_confirmation only when it is ready, revision_required when "
                "changes are needed, or rejected when unsuitable. Review never grants Apply or "
                "Commit authority. "
                + synthesis_guidance
            ),
        ),
    )




def prepare_lead_rule_seed(
    *,
    discussion_only: bool,
    discussion_constraints: LeadDiscussionConstraints | None,
    expected_relation: LeadProposalRelation,
    inputs: tuple[CollaborationInputRevision, ...],
    offered_tasks: Mapping[tuple[str, int], AuthoringScope | None],
    constraints: LeadProviderConstraints | LeadExplorationConstraints,
    successor_only: bool,
    task_receipt: LeadTaskTransitionReceipt | None,
    revision_source_scope: AuthoringScope | None,
    source_artifact_revision: bool,
    selected_document_review_path: str | None,
    interaction_mode: LeadInteractionMode,
) -> LeadResultRuleSeed:
    return reply_contract_values.lead_discussion_result_contract(discussion_constraints) if discussion_only else response_rules.lead_revision_result_contract(
        expected_relation,
        tuple((item.input_id for item in inputs)),
        tuple(offered_tasks),
        constraints,
        discussion_constraints,
        fresh_revision_only=successor_only or (task_receipt is not None and task_receipt.target_task_id is not None),
        source_scope=revision_source_scope,
        artifact_revision_only=source_artifact_revision,
        selected_document_review_path=selected_document_review_path,
    ) if discussion_constraints is not None else reply_contract_values.lead_result_rule_seed("lead-exploration-response-v1", constraints.schema()) if interaction_mode is LeadInteractionMode.EXPLORATION else response_rules.lead_front_result_contract(
            expected_relation,
            tuple(item.input_id for item in inputs),
            tuple(offered_tasks),
            constraints=constraints,
            fresh_revision_only=successor_only or (task_receipt is not None and task_receipt.target_task_id is not None),
        )


__all__ = (
    'specialist_result_contract',
    'lead_synthesis_result_contract',
    'LEAD_CORRECTABLE_RESPONSE_REJECTION_CODES',
    'LEAD_RESPONSE_CORRECTION_INSTRUCTION',
    'prepare_lead_rule_seed',
    'task_discussion_constraints',
)


def task_discussion_constraints(transition: CollaborationEvent, receipt: LeadTaskTransitionReceipt, discussion_only: bool) -> LeadDiscussionConstraints | None:
    ref = receipt.source_task_ref
    discussion_constraints = LeadDiscussionConstraints(
        transition.event_id, None if ref is None else ref.task_id,
        None if ref is None else ref.task_revision,
    ) if (
    discussion_only
    or (
        receipt.target_task_id is not None
        and receipt.target_task_revision is not None
        and (
            receipt.action in {LeadTaskAction.RESUME, LeadTaskAction.NEW_TASK}
            or receipt.origin_kind == "revise_decision"
        )
    )
    ) else None
    return discussion_constraints
