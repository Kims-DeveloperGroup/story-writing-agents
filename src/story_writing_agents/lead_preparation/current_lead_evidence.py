"""Validate current Lead receipt and selected-source evidence."""
from __future__ import annotations

from collections.abc import Callable
from functools import partial

from . import reply_contract_values
from ..agent_context.invocation.context_execution import AgentContextService
from ..agent_context.manifest import ContextSubjectKind
from ..agent_context.sources.capture_values import (
    CONTEXT_SOURCE_POLICY,
    ContextSourceSnapshot,
)
from ..agent_context.sources.focused_conversation import FocusedConversationSourceAdapter
from ..agent_context.sources.own_agent_state import OwnAgentStateSourceAdapter
from ..agent_context.sources.writing_artifact import create_guarded_writing_artifact_source_adapter
from ..agents import LEAD_ORCHESTRATOR
from ..artifact_proposals import ArtifactIntentEvidence
from ..conversation_storage.contracts import ConversationStorageServices
from ..conversations import (
    CollaborationEvidenceAcquisition,
    CollaborationInputKind,
    CollaborationInputRevision,
    CollaborationInputState,
    CollaborationLeadEvidence,
    CollaborationProposal,
    CollaborationStepKind,
    LeadEvidenceStatus,
    LeadOutcomeScope,
    LeadRecommendationRequestPayload,
)
from ..conversations.events import CollaborationEvent
from ..lead_preparation import (
    lead_evidence_receipts,
    lead_material_inputs,
)
from ..lead_provider_contracts import empty_core_status_evidence
from ..runtime_resources import session_worktree_access
from ..structured_output_validation import validation_failure
from ..workspace.initialization import SessionArtifactBinding
from ..workspace.session_source_workspace import SessionSourceWorkspace
from ..writing_artifacts import parse_artifact_references

__all__ = ("artifact_intent_evidence", "validate_current_lead_evidence")


def artifact_intent_evidence(
    evidence: CollaborationLeadEvidence,
    *,
    session_id: str,
    conversation_id: str,
    artifact_binding: SessionArtifactBinding,
    context_service: AgentContextService,
    current_lead_read_snapshot: Callable[..., ContextSourceSnapshot],
) -> tuple[ArtifactIntentEvidence, ...]:
    """Validate current selected receipts before resolving artifact content."""

    scope = context_service.scope(
        session_id,
        ContextSubjectKind.CONVERSATION,
        conversation_id,
        LEAD_ORCHESTRATOR,
    )
    repository_access = partial(
        session_worktree_access.open_session_artifact_repository,
        artifact_binding,
    )
    writing_adapter = create_guarded_writing_artifact_source_adapter(
        repository_access,
        scope,
    )
    session_sources = SessionSourceWorkspace.from_artifact_binding(
        artifact_binding
    )
    current_statuses: dict[str, CollaborationEvidenceReceipt] | None = None
    result: list[ArtifactIntentEvidence] = []
    for receipt in evidence.receipts:
        if receipt.acquisition is CollaborationEvidenceAcquisition.CORE_STATUS:
            if (
                receipt.source_kind is not CollaborationInputKind.CORE_DOCUMENT
                or not empty_core_status_evidence(
                    locator=receipt.locator,
                    present=receipt.source_state is CollaborationInputState.PRESENT,
                    utf8_bytes=receipt.utf8_bytes + 1,
                    content_digest=receipt.content_digest,
                )
            ):
                continue
            if current_statuses is None:
                current_statuses = {
                    item.evidence_id: lead_evidence_receipts.durable_core_status_receipt(
                        item,
                        operation_id=receipt.operation_id,
                        result_correlation_id=receipt.result_correlation_id,
                    )
                    for item in writing_adapter.canonical_core_statuses(
                        receipt.manifest_digest,
                        CONTEXT_SOURCE_POLICY,
                    )
                }
            if current_statuses.get(receipt.evidence_id) != receipt:
                raise validation_failure(
                    reply_contract_values.LeadOutcomeValidationError(
                        'artifact empty-core status evidence is no longer current',
                    ),
                    '$.artifact_intent.operations',
                    'artifact empty-core status evidence is no longer current',
                )
            assert receipt.locator is not None
            assert receipt.content_digest is not None
            result.append(ArtifactIntentEvidence(
                evidence_id=receipt.evidence_id,
                path=receipt.locator,
                source_digest=receipt.content_digest,
                boundary_digest=receipt.content_digest,
            ))
            continue
        if receipt.source_kind not in {
            CollaborationInputKind.CORE_DOCUMENT,
            CollaborationInputKind.MANUSCRIPT,
            CollaborationInputKind.WRITING_ARTIFACT,
        } or receipt.acquisition not in {
            CollaborationEvidenceAcquisition.ATTACHED_REFERENCE,
            CollaborationEvidenceAcquisition.READ,
        }:
            continue
        if (
            receipt.locator is None
            or receipt.revision_digest is None
            or receipt.content_digest is None
        ):
            raise validation_failure(
                reply_contract_values.LeadOutcomeValidationError('artifact evidence is missing runtime identity'),
                '$.artifact_intent.operations',
                'artifact evidence is missing runtime identity',
            )
        locator = receipt.locator
        whole_read = False
        if (
            receipt.acquisition
            is CollaborationEvidenceAcquisition.ATTACHED_REFERENCE
        ):
            batch = parse_artifact_references(locator)
            if len(batch.references) != 1:
                raise validation_failure(
                    reply_contract_values.LeadOutcomeValidationError('artifact reference evidence is ambiguous'),
                    '$.artifact_intent.operations',
                    'artifact reference evidence is ambiguous',
                )
            path = batch.references[0].path
            source_digest = receipt.revision_digest
        else:
            path = locator
            snapshot = current_lead_read_snapshot(
                session_id=session_id,
                conversation_id=conversation_id,
                scope=scope,
                receipt=receipt,
                writing_adapter=writing_adapter,
                session_sources=session_sources,
            )
            source_digest = snapshot.descriptor.content_digest
            whole_read = (
                receipt.content_digest == snapshot.descriptor.content_digest
                and receipt.utf8_bytes == snapshot.descriptor.utf8_bytes
            )
        result.append(
            ArtifactIntentEvidence(
                evidence_id=receipt.evidence_id,
                path=path,
                source_digest=source_digest,
                boundary_digest=receipt.content_digest,
                start_line=None if whole_read else receipt.start_line,
                end_line=None if whole_read else receipt.end_line,
            )
        )
    return tuple(result)


def validate_current_lead_evidence(
    *,
    session_id: str,
    events: tuple[CollaborationEvent, ...],
    proposal: CollaborationProposal | LeadRecommendationRequestPayload,
    artifact_binding: SessionArtifactBinding | None = None,
    context_service: AgentContextService,
    conversation_storage_services: ConversationStorageServices,
    bind_session_artifacts: Callable[[str], SessionArtifactBinding],
    current_lead_read_input: Callable[..., CollaborationInputRevision],
) -> dict[str, CollaborationInputRevision]:
    """Re-derive selected evidence without replaying provider SEARCH text."""

    if artifact_binding is None:
        artifact_binding = bind_session_artifacts(session_id)
    if any(event.session_id != session_id for event in events):
        raise ValueError("lead evidence history has cross-session events")
    evidence = proposal.basis.evidence
    if evidence is None:
        return {}
    conversation_ids = {event.conversation_id for event in events}
    if len(conversation_ids) != 1:
        raise ValueError("lead evidence conversation identity is ambiguous")
    conversation_id = next(iter(conversation_ids))
    if not evidence.receipts:
        if (
            type(proposal) is CollaborationProposal
            and proposal.premise_decision is not None
            and proposal.step_kind is CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
            and proposal.authoring_scope is None
            and evidence.scope is LeadOutcomeScope.PREMISE_ANALYSIS
            and evidence.status is LeadEvidenceStatus.NOT_APPLICABLE
        ):
            return {}
        raise ValueError("story-specific proposal evidence cannot be empty")
    operation_id = evidence.receipts[0].operation_id
    result_correlation_id = evidence.receipts[0].result_correlation_id
    manifest_digest = evidence.receipts[0].manifest_digest
    status_receipts = tuple(
        item
        for item in evidence.receipts
        if item.acquisition is CollaborationEvidenceAcquisition.CORE_STATUS
    )
    scope = context_service.scope(
        session_id,
        ContextSubjectKind.CONVERSATION,
        conversation_id,
        LEAD_ORCHESTRATOR,
    )
    repository_access = partial(
        session_worktree_access.open_session_artifact_repository,
        artifact_binding,
    )
    writing_adapter = create_guarded_writing_artifact_source_adapter(
        repository_access,
        scope,
    )
    session_sources = SessionSourceWorkspace.from_artifact_binding(
        artifact_binding
    )
    if status_receipts:
        current_statuses = {
            item.evidence_id: lead_evidence_receipts.durable_core_status_receipt(
                item,
                operation_id=operation_id,
                result_correlation_id=result_correlation_id,
            )
            for item in writing_adapter.canonical_core_statuses(
                manifest_digest,
                CONTEXT_SOURCE_POLICY,
            )
        }
        if any(
            current_statuses.get(item.evidence_id) != item
            for item in status_receipts
        ):
            raise ValueError("lead core-status evidence is no longer current")

    zero_hits = tuple(
        item
        for item in evidence.receipts
        if item.acquisition is CollaborationEvidenceAcquisition.ZERO_HIT_SEARCH
    )
    if zero_hits:
        descriptors = writing_adapter.inventory(CONTEXT_SOURCE_POLICY)
        for item in zero_hits:
            current_descriptors: list[Any] = []
            if (
                CollaborationInputKind.WRITING_ARTIFACT
                in item.authorized_source_kinds
            ):
                current_descriptors.extend(
                    sorted(
                        (
                            descriptor
                            for descriptor in descriptors
                            if item.artifact_path_prefix is None
                            or descriptor.provenance.locator.startswith(
                                item.artifact_path_prefix
                            )
                        ),
                        key=lambda descriptor: descriptor.source_id,
                    )
                )
            if (
                CollaborationInputKind.CONVERSATION_EVENT
                in item.authorized_source_kinds
            ):
                services = conversation_storage_services
                current_descriptors.extend(
                    sorted(
                        FocusedConversationSourceAdapter(
                            services.catalog,
                            services.event_store,
                            scope,
                            conversation_id,
                            include_same_session_active=True,
                            include_searchable_history=True,
                        ).inventory(CONTEXT_SOURCE_POLICY),
                        key=lambda descriptor: descriptor.source_id,
                    )
                )
            if CollaborationInputKind.AGENT_STATE in item.authorized_source_kinds:
                current_descriptors.extend(
                    sorted(
                        OwnAgentStateSourceAdapter(
                            session_sources,
                            scope,
                        ).inventory(CONTEXT_SOURCE_POLICY),
                        key=lambda descriptor: descriptor.source_id,
                    )
                )
            if (
                tuple(descriptor.source_id for descriptor in current_descriptors)
                != item.authorized_source_ids
                or lead_material_inputs.lead_authorized_source_fingerprint(
                    tuple(current_descriptors)
                )
                != item.authorized_source_fingerprint
            ):
                raise ValueError(
                    "lead zero-hit evidence source manifest has drifted"
                )

    read_inputs: dict[str, CollaborationInputRevision] = {}
    for receipt in evidence.receipts:
        if receipt.acquisition is not CollaborationEvidenceAcquisition.READ:
            continue
        current_input = current_lead_read_input(
            session_id=session_id,
            conversation_id=conversation_id,
            scope=scope,
            receipt=receipt,
            writing_adapter=writing_adapter,
            session_sources=session_sources,
        )
        if receipt.evidence_id in read_inputs:
            raise ValueError("lead READ evidence identity is duplicated")
        read_inputs[receipt.evidence_id] = current_input
    return read_inputs
