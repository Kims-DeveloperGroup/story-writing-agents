"""Materialize and validate exact evidence-backed proposal inputs."""

from __future__ import annotations

from ..specialist_routing import (
    AuthoringActivity,
    SpecialistAssignmentMode,
    specialists_for_domains,
    stage_is_focused_review,
    validate_stage_routing,
)
from ..conversations import (
    CollaborationEvidenceAcquisition,
    CollaborationEvidenceReceipt,
    CollaborationInputKind,
    CollaborationInputRevision,
    CollaborationInputState,
    CollaborationLeadEvidence,
    LeadProposalIntent,
)
from ..agents import SPECIALIST_IDS
from . import authoring_authority, reply_contract_values
import re
from ..structured_output_validation import validation_call, validation_failure


def materialize_lead_evidence_inputs(
    inputs: tuple[CollaborationInputRevision, ...],
    evidence: CollaborationLeadEvidence,
    *,
    validation_path: str = '$.proposal.assignments',
) -> tuple[CollaborationInputRevision, ...]:
    """Materialize only cited READ content as exact proposal inputs."""

    materialized = list(inputs)
    known_inputs = {item.input_id: item for item in materialized}
    for receipt in evidence.receipts:
        if receipt.acquisition is not CollaborationEvidenceAcquisition.READ:
            continue
        missing = tuple(field for field in ('source_kind', 'locator', 'content_digest')
                        if getattr(receipt, field) is None)
        if missing:
            reason = (f"lead READ evidence {receipt.evidence_id!r} is incomplete: "
                      + ', '.join(missing))
            raise validation_failure(
                reply_contract_values.LeadOutcomeValidationError(reason), validation_path, reason,
            )
        candidate = CollaborationInputRevision(
            input_id=receipt.evidence_id,
            kind=receipt.source_kind,
            locator=(
                artifact_evidence_locator(receipt)
                if receipt.source_kind
                in {
                    CollaborationInputKind.CORE_DOCUMENT,
                    CollaborationInputKind.MANUSCRIPT,
                    CollaborationInputKind.WRITING_ARTIFACT,
                }
                else receipt.locator
            ),
            state=CollaborationInputState.PRESENT,
            revision_digest=receipt.content_digest,
            start_line=receipt.start_line,
            end_line=receipt.end_line,
        )
        existing = known_inputs.get(receipt.evidence_id)
        if existing is not None:
            differences = tuple(field for field in (
                'locator', 'state', 'revision_digest', 'start_line', 'end_line',
            ) if getattr(existing, field) != getattr(candidate, field))
            if differences:
                reason = (f"lead READ evidence {receipt.evidence_id!r} conflicts with existing input: "
                          + ', '.join(differences))
                raise validation_failure(
                    reply_contract_values.LeadOutcomeValidationError(reason), validation_path, reason,
                )
            continue
        materialized.append(candidate)
        known_inputs[receipt.evidence_id] = candidate
    return tuple(materialized)


def artifact_evidence_locator(receipt: CollaborationEvidenceReceipt) -> str:
    """Project one authorized artifact READ as a canonical input indicator."""

    assert receipt.locator is not None
    if receipt.start_line is None:
        return f"${{{receipt.locator}}}"
    assert receipt.end_line is not None
    return f"${{{receipt.locator}}}#L{receipt.start_line}-L{receipt.end_line}"


def validate_lead_intent_evidence_inputs(
    intent: LeadProposalIntent,
    inputs: tuple[CollaborationInputRevision, ...],
) -> None:
    if intent.authoring_scope is None and intent.premise_decision is None:
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError('live intent requires authoring scope'),
            '$.authoring_scope',
            'live intent requires authoring scope',
        )
    if intent.authoring_scope is not None:
        validation_call(
            '$.proposal.assignments',
            validate_stage_routing,
            intent.authoring_scope,
            tuple(item.agent_id for item in intent.assignments),
            intent.stage_plan,
            affected_paths=authoring_authority.authoring_affected_paths(inputs, None),
        )
    specialist_ids = tuple(item.agent_id for item in intent.assignments)
    canonical = tuple(
        agent_id for agent_id in SPECIALIST_IDS if agent_id in specialist_ids
    )
    assigned = {
        input_id
        for assignment in intent.assignments
        for input_id in assignment.input_ids
    }
    if specialist_ids != canonical or assigned != {
        item.input_id for item in inputs
    }:
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError(
                (
                    'lead proposal routing is not canonical or input-complete'
                ),
            ),
            '$.proposal.assignments',
            (
                'assignments must include all required specialists in canonical order and refer '
                'only to disclosed input_ids'
            ),
        )
    selected_aliases = {item.input_id for item in inputs
                        if item.kind is CollaborationInputKind.CONVERSATION_EVENT
                        and re.fullmatch(r"selected_material_[1-9][0-9]*", item.input_id)}
    if (selected_aliases and intent.authoring_scope is not None
            and intent.authoring_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
            and any(not selected_aliases.issubset(assignment.input_ids)
                    for assignment in intent.assignments)):
        raise reply_contract_values.LeadOutcomeValidationError("every writer must retain every exact USER-selected whole input")
    assignment_inputs = {
        item.agent_id: frozenset(item.input_ids) for item in intent.assignments
    }
    for item in inputs:
        if item.kind not in {CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
                             CollaborationInputKind.WRITING_ARTIFACT}:
            continue
        reviewer = authoring_authority.core_reviewer(item.locator)
        if reviewer is not None and (
            not stage_is_focused_review(intent.stage_plan) or reviewer in specialist_ids
        ) and (
            reviewer not in assignment_inputs
            or item.input_id not in assignment_inputs[reviewer]
        ):
            raise validation_failure(
                reply_contract_values.LeadOutcomeValidationError(
                    'lead proposal omits a mandatory core-document reviewer',
                ),
                '$.proposal.assignments',
                'lead proposal omits a mandatory core-document reviewer',
            )

    if intent.premise_decision is not None:
        if (
            intent.authoring_scope is not None
            or specialist_ids != specialists_for_domains(
                intent.premise_decision.dependent_domains
            )
            or any(
                assignment.mode is not SpecialistAssignmentMode.PREMISE_ANALYSIS
                for assignment in intent.assignments
            )
        ):
            raise validation_failure(
                reply_contract_values.LeadOutcomeValidationError("premise analysis analyst routing is unbound"),
                "$.proposal.assignments",
                "candidate-free analysis requires the exact question-domain analyst roster "
                "and premise_analysis modes",
            )
