"""Map invocation-local receipts into durable Lead evidence."""

from __future__ import annotations

from ..conversations import (
    CollaborationEvidenceAcquisition,
    CollaborationEvidenceReceipt,
    CollaborationInputKind,
    CollaborationInputRevision,
    CollaborationInputState,
    CollaborationLeadEvidence,
    CollaborationLeadJudgmentPayload,
    LeadEvidenceStatus,
    LeadOutcomeScope,
    LeadProviderOutcome,
)
from ..agent_context.actions import (
    ContextAnswerAuthorization,
    ContextEvidenceReceipt,
    ContextSourceBoundaryKind,
    ContextSourceStatusKind,
    ContextSourceStatusReceipt,
    ContextZeroHitSearchReceipt,
)
from ..agent_context.manifest import ContextSourceKind
from ..specialist_routing import artifact_domain
from ..structured_output_validation import carry_validation, validation_failure
from . import reply_contract_values


def evidence_source_kind(
    source_kind: ContextSourceKind,
    locator: str,
) -> CollaborationInputKind:
    if source_kind is ContextSourceKind.WRITING_ARTIFACT:
        if locator.startswith("core_documents/"):
            return CollaborationInputKind.CORE_DOCUMENT
        if locator.startswith("manuscript/"):
            return CollaborationInputKind.MANUSCRIPT
        return CollaborationInputKind.WRITING_ARTIFACT
    if source_kind is ContextSourceKind.CONVERSATION:
        return CollaborationInputKind.CONVERSATION_EVENT
    return CollaborationInputKind.AGENT_STATE


def durable_lead_evidence(
    outcome: LeadProviderOutcome,
    authorization: ContextAnswerAuthorization,
    inputs: tuple[CollaborationInputRevision, ...],
) -> CollaborationLeadEvidence:
    """Map only invocation-local runtime facts named by the provider."""
    premise_analysis = (
        outcome.proposal is not None
        and outcome.proposal.premise_decision is not None
    )
    if premise_analysis != (outcome.scope is LeadOutcomeScope.PREMISE_ANALYSIS):
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError("premise analysis evidence scope is unbound"),
            "$.authoring_scope",
            "premise analysis evidence scope requires its exact typed question intent",
        )

    operation_id = authorization.correlation.operation_id
    result_correlation_id = authorization.correlation.result_correlation_id
    manifest_digest = authorization.manifest_digest
    if outcome.preliminary_review is not None and not authorization.completed_evidence and not authorization.mandatory_references:
        evidence = CollaborationLeadEvidence(outcome.scope, outcome.evidence_status, ())
        CollaborationLeadJudgmentPayload(outcome.kind, outcome.message, evidence, outcome.authoring_scope,
            preliminary_review=outcome.preliminary_review)
        return evidence
    inputs_by_id = {item.input_id: item for item in inputs}
    available: list[CollaborationEvidenceReceipt] = []
    for reference in authorization.mandatory_references:
        input_revision = inputs_by_id.get(reference.reference_id)
        if input_revision is None:
            raise validation_failure(
                reply_contract_values.lead_source_owner_rejection(reply_contract_values.LeadOutcomeValidationError('lead reference evidence is not an authoritative input')),
                '$.authoring_scope',
                'lead reference evidence is not an authoritative input',
            )
        available.append(
            CollaborationEvidenceReceipt(
                evidence_id=reference.reference_id,
                acquisition=(CollaborationEvidenceAcquisition.ATTACHED_REFERENCE),
                operation_id=operation_id,
                result_correlation_id=result_correlation_id,
                manifest_digest=manifest_digest,
                source_id=reference.source_id,
                source_kind=input_revision.kind,
                locator=input_revision.locator,
                source_state=CollaborationInputState.PRESENT,
                revision_digest=reference.revision,
                content_digest=reference.content_digest,
                utf8_bytes=reference.utf8_bytes,
                boundary_kind=(
                    "whole"
                    if input_revision.start_line is None
                    else "artifact_lines"
                ),
                boundary_start=input_revision.start_line,
                boundary_end=input_revision.end_line,
                start_line=input_revision.start_line,
                end_line=input_revision.end_line,
                ordinal=reference.ordinal,
            )
        )
    authoring_scope = outcome.authoring_scope
    scoped_paths = (
        frozenset()
        if authoring_scope is None
        else frozenset(authoring_scope.target_paths)
    )
    scoped_domains = (
        frozenset()
        if authoring_scope is None
        else frozenset(authoring_scope.domains)
    )
    available.extend(
        durable_core_status_receipt(
            item,
            operation_id=operation_id,
            result_correlation_id=result_correlation_id,
        )
        for item in authorization.core_statuses
        if (
            outcome.scope is not LeadOutcomeScope.WORKFLOW_META
            and (
                outcome.core_document_assessment is not None
                or item.locator in scoped_paths
                or artifact_domain(item.locator) in scoped_domains
            )
        )
    )
    action_receipts: list[CollaborationEvidenceReceipt] = []
    action_receipts.extend(
        _durable_zero_hit_receipt(item)
        for item in authorization.zero_hit_searches
    )
    action_receipts.extend(
        _durable_read_receipt(
            item,
            operation_id=operation_id,
            result_correlation_id=result_correlation_id,
            manifest_digest=manifest_digest,
        )
        for item in (authorization.completed_evidence if outcome.preliminary_review is not None else authorization.used_evidence)
    )
    action_receipts.sort(
        key=lambda item: (
            item.action_index or 0,
            0
            if item.acquisition is CollaborationEvidenceAcquisition.ZERO_HIT_SEARCH
            else 1,
            item.evidence_id,
        )
    )
    available.extend(action_receipts)
    available_by_id = {item.evidence_id: item for item in available}
    if len(available_by_id) != len(available):
        raise validation_failure(
            reply_contract_values.lead_source_owner_rejection(reply_contract_values.LeadOutcomeValidationError('runtime evidence IDs are duplicate')),
            '$.authoring_scope',
            'runtime evidence IDs are duplicate',
        )
    if outcome.scope is LeadOutcomeScope.WORKFLOW_META:
        selected_ids: tuple[str, ...] = ()
        status = LeadEvidenceStatus.NOT_APPLICABLE
    else:
        selected_ids = tuple(item.evidence_id for item in available)
        present_content = any(
            item.source_state is CollaborationInputState.PRESENT
            and item.utf8_bytes > 0
            and item.acquisition in {
                CollaborationEvidenceAcquisition.ATTACHED_REFERENCE,
                CollaborationEvidenceAcquisition.READ,
            }
            for item in available
        )
        missing = any(
            (
                item.acquisition is CollaborationEvidenceAcquisition.ZERO_HIT_SEARCH
                and CollaborationInputKind.WRITING_ARTIFACT
                in item.authorized_source_kinds
            )
            or (
                item.acquisition is CollaborationEvidenceAcquisition.CORE_STATUS
                and item.source_state is CollaborationInputState.ABSENT
            )
            for item in available
        )
        empty = any(
            item.source_state is CollaborationInputState.PRESENT
            and item.utf8_bytes == 0
            and item.acquisition in {
                CollaborationEvidenceAcquisition.ATTACHED_REFERENCE,
                CollaborationEvidenceAcquisition.CORE_STATUS,
            }
            for item in available
        )
        if present_content and (missing or empty):
            status = LeadEvidenceStatus.INCOMPLETE
        elif missing:
            status = LeadEvidenceStatus.MISSING
        elif empty:
            status = LeadEvidenceStatus.EMPTY
        elif present_content:
            status = LeadEvidenceStatus.VERIFIED
        elif available:
            status = LeadEvidenceStatus.INCOMPLETE
        elif premise_analysis:
            status = LeadEvidenceStatus.NOT_APPLICABLE
        else:
            raise validation_failure(
                reply_contract_values.lead_source_owner_rejection(reply_contract_values.LeadOutcomeValidationError('story-specific Lead outcome has no runtime evidence')),
                '$.authoring_scope',
                'story-specific Lead outcome has no runtime evidence',
            )
    if outcome.preliminary_review is not None:
        status = outcome.evidence_status
    evidence = CollaborationLeadEvidence(
        scope=outcome.scope,
        status=status,
        receipts=tuple(available_by_id[item] for item in selected_ids),
    )
    try:
        CollaborationLeadJudgmentPayload(outcome.kind, outcome.message, evidence, outcome.authoring_scope, core_document_readiness=outcome.core_document_readiness if outcome.core_document_assessment is not None else None,
                                                                                                           authoring_admission=outcome.authoring_admission, preliminary_review=outcome.preliminary_review)
    except (TypeError, ValueError) as exc:
        raise carry_validation(
            validation_failure(
                reply_contract_values.LeadOutcomeValidationError('lead outcome violates the closed evidence matrix'),
                '$.authoring_scope',
                'lead outcome violates the closed evidence matrix',
            ),
            exc,
        ) from exc
    return evidence


def durable_core_status_receipt(
    receipt: ContextSourceStatusReceipt,
    *,
    operation_id: str,
    result_correlation_id: str,
) -> CollaborationEvidenceReceipt:
    return CollaborationEvidenceReceipt(
        evidence_id=receipt.evidence_id,
        acquisition=CollaborationEvidenceAcquisition.CORE_STATUS,
        operation_id=operation_id,
        result_correlation_id=result_correlation_id,
        manifest_digest=receipt.manifest_digest,
        source_id=receipt.source_id,
        source_kind=CollaborationInputKind.CORE_DOCUMENT,
        locator=receipt.locator,
        source_state=(
            CollaborationInputState.ABSENT
            if receipt.status is ContextSourceStatusKind.ABSENT
            else CollaborationInputState.PRESENT
        ),
        revision_digest=receipt.revision,
        content_digest=receipt.source_digest,
        utf8_bytes=receipt.source_utf8_bytes,
        ordinal=receipt.ordinal,
    )


def _durable_zero_hit_receipt(
    receipt: ContextZeroHitSearchReceipt,
) -> CollaborationEvidenceReceipt:
    mapped_kinds = tuple(
        dict.fromkeys(
            evidence_source_kind(kind, "")
            for kind in receipt.authorized_source_kinds
        )
    )
    return CollaborationEvidenceReceipt(
        evidence_id=receipt.evidence_id,
        acquisition=CollaborationEvidenceAcquisition.ZERO_HIT_SEARCH,
        operation_id=receipt.correlation.operation_id,
        result_correlation_id=receipt.correlation.result_correlation_id,
        manifest_digest=receipt.manifest_digest,
        source_state=CollaborationInputState.ABSENT,
        action_index=receipt.correlation.action_index,
        query_digest=receipt.query_digest,
        query_count=receipt.query_count,
        authorized_source_ids=receipt.authorized_source_ids,
        authorized_source_kinds=mapped_kinds,
        authorized_source_fingerprint=(receipt.authorized_source_fingerprint),
        artifact_path_prefix=receipt.artifact_path_prefix,
    )


def _durable_read_receipt(
    receipt: ContextEvidenceReceipt,
    *,
    operation_id: str,
    result_correlation_id: str,
    manifest_digest: str,
) -> CollaborationEvidenceReceipt:
    locator = receipt.provenance.locator
    boundary = receipt.boundary
    return CollaborationEvidenceReceipt(
        evidence_id=receipt.evidence_id,
        acquisition=CollaborationEvidenceAcquisition.READ,
        operation_id=operation_id,
        result_correlation_id=result_correlation_id,
        manifest_digest=manifest_digest,
        source_id=receipt.source_id,
        source_kind=evidence_source_kind(receipt.source_kind, locator),
        locator=locator,
        source_state=CollaborationInputState.PRESENT,
        revision_digest=receipt.revision,
        content_digest=receipt.boundary_digest,
        utf8_bytes=receipt.boundary_utf8_bytes,
        boundary_kind=boundary.kind.value,
        boundary_start=boundary.start,
        boundary_end=boundary.end,
        start_line=(
            boundary.start
            if boundary.kind is ContextSourceBoundaryKind.ARTIFACT_LINES
            else None
        ),
        end_line=(
            boundary.end
            if boundary.kind is ContextSourceBoundaryKind.ARTIFACT_LINES
            else None
        ),
        action_index=receipt.completing_read_action_index,
        origin_action_index=receipt.originating_search_action_index,
    )
