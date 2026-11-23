"""Validate readiness source ownership and prefixes."""

from __future__ import annotations

import hashlib
import json

from ...authoring_admission import (
    AdmissionSource,
    AdmissionSourceKind,
)
from ..event_contracts.evidence import (
    CollaborationLeadEvidence,
    authoring_source_facts,
)
from ..event_contracts.lead_outcomes import CollaborationLeadJudgmentPayload
from ..event_vocabulary import (
    CollaborationInputKind,
    CollaborationInputState,
)
from .task_source_history import (
    validate_admission_source_origins as _validate_admission_source_origins,
)
from ...core_document_readiness import (
    CORE_DOCUMENTS,
    CoreDocumentReadinessBasis,
)


def validate_readiness_sources(
    basis: CoreDocumentReadinessBasis, evidence: CollaborationLeadEvidence,
    prior: dict[str, CollaborationEvent], *, turn_id: str,
) -> None:
    """Validate stored semantic findings against the original causal physical prefix."""
    if type(basis) is not CoreDocumentReadinessBasis:
        raise TypeError("readiness basis must be current and typed")
    actual_reads = {v.source_id: v for v in authoring_source_facts((), evidence)}
    material = []
    request = []
    for source in basis.sources:
        if source.kind is AdmissionSourceKind.READ:
            actual = actual_reads.get(source.source_id)
            if (actual is None or (actual.source_identity, actual.revision_digest, actual.utf8_bytes,
                actual.original_authority) != (source.source_identity, source.revision_digest,
                source.utf8_bytes, source.source_classification)):
                raise ValueError("readiness READ acquisition does not match captured receipts")
            receipts = tuple(r for r in evidence.receipts if r.evidence_id == source.source_id)
            if len(receipts) != 1:
                raise ValueError("readiness READ receipt is ambiguous")
            r = receipts[0]
            path = r.locator.removeprefix("${").removesuffix("}") if r.locator else None
            whole = (path in CORE_DOCUMENTS and r.source_kind is CollaborationInputKind.CORE_DOCUMENT
                and r.source_state is CollaborationInputState.PRESENT and r.boundary_kind == "whole"
                and r.start_line is None and r.end_line is None)
            if (source.whole_document != whole or source.document_path != (path if whole else None)
                or source.content_digest != r.content_digest or source.acquisition_identity != r.evidence_id):
                raise ValueError("readiness whole-document boundary changed")
        else:
            material.append(AdmissionSource(source.source_id, source.kind, source.source_identity,
                source.revision_digest, source.source_classification, source.utf8_bytes))
            if source.kind is AdmissionSourceKind.USER and source.source_id == "user_input":
                request.append(source)
    if (len(request) != 1 or request[0].source_identity != f"conversation/{turn_id}/user_input"
        or request[0].revision_digest != basis.request_digest):
        raise ValueError("readiness assessment changed its actual originating USER request")
    _validate_admission_source_origins(tuple(material), prior)


def validate_readiness_owner_prefix(
    subject: CollaborationEvent, prior: dict[str, CollaborationEvent],
) -> None:
    capture = type(subject.payload) is CollaborationLeadJudgmentPayload
    basis = subject.payload.core_document_readiness if capture else subject.payload.basis.core_document_readiness
    owner = subject.conversation_id
    coverage = tuple(v for v in basis.history_coverage if v[0] == owner)
    metadata = tuple(v for v in basis.history_metadata if v[0] == owner)
    if len(coverage) != 1 or len(metadata) != 1:
        raise ValueError("readiness capture lacks exact original owner coverage")
    _, count, digest = coverage[0]
    if count >= subject.sequence or count > len(prior):
        raise ValueError("readiness original prefix is missing or future")
    _, title, status = metadata[0]
    records = tuple(prior.values())[:count]
    value = {"subject": title, "status": status,
        "records": [[v.sequence, v.event_id, v.batch_digest, v.timestamp] for v in records]}
    actual = "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True,
        separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    if actual != digest:
        raise ValueError("readiness immutable owner prefix changed")
    if capture:
        validate_readiness_sources(basis, subject.payload.evidence, {v.event_id: v for v in records},
                                   turn_id=subject.turn_id)
    else:
        if len(basis.prior_assessment_refs) != 1:
            raise ValueError("readiness use requires one actual causal assessment")
        reference = basis.prior_assessment_refs[0]
        matches = tuple(v for v in records if f"{v.session_id}/{v.conversation_id}/{v.event_id}" == reference)
        if not matches and not reference.startswith(f"{subject.session_id}/{subject.conversation_id}/"):
            return  # Exact foreign origin is verified by the full session snapshot.
        if len(matches) != 1 or type(matches[0].payload) is not CollaborationLeadJudgmentPayload:
            raise ValueError("readiness source assessment missing or ambiguous")
        original = matches[0]
        source = original.payload.core_document_readiness
        if source is None or (basis.assessment, basis.sources, basis.request_digest) != (source.assessment, source.sources, source.request_digest):
            raise ValueError("readiness use changed exact original assessment/source facts")
        validate_readiness_sources(basis, original.payload.evidence, {v.event_id: v for v in records},
                                   turn_id=original.turn_id)
