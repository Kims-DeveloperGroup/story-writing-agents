"""Qualify Lead impact judgments against exact review and source evidence."""

from collections.abc import Mapping
from dataclasses import replace
from hashlib import sha256
from types import MappingProxyType

from ..agent_context.actions import ContextEvidenceReceipt, ContextSourceBoundaryKind
from ..agent_context.manifest import ContextSourceKind
from ..conversations.event_contracts.synchronization import SynchronizationReadEvidence, SynchronizationReadReceipt
from ..conversations.event_contracts.proposal_inputs import authoring_scope_to_dict
from ..conversations.event_value_validation import canonical_json
from ..conversations.events import (
    ArtifactReviewCandidatePayload,
    CollaborationEvent,
    CollaborationEventDraft,
    CollaborationLeadJudgmentPayload,
    CollaborationLeadTaskTransitionPayload,
    CollaborationProposal,
    CollaborationSpecialistReplyPayload,
    synchronization_event_ref,
)
from ..conversations.event_vocabulary import (
    CollaborationActorKind,
    CollaborationEvidenceAcquisition,
    CollaborationInputKind,
    CollaborationSpecialistAssessment,
)
from ..lead_preparation.lead_material_inputs import select_current_reference_bytes
from ..lead_preparation.synchronization_review_response import SynchronizationReviewFinding
from ..specialist_routing import AuthoringIntent, SpecialistAssignmentMode, specialists_for_domains
from ..synchronization_assessment import (
    SynchronizationAbsence,
    SynchronizationAbsenceOrigin,
    SynchronizationAbsenceProof,
    SynchronizationAssessment,
    SynchronizationBasis,
    SynchronizationCandidate,
    SynchronizationDocument,
    SynchronizationEventRef,
    SynchronizationFinding,
    SynchronizationJudgment,
    SynchronizationRead,
)
from ..writing_artifacts.catalog import canonical_artifact_kind
from .synchronization_material import SynchronizationMaterial, earlier_read_id


def retain_current_review_reads(
    material: SynchronizationMaterial,
    base_state: Mapping[str, bytes],
    events: tuple[CollaborationEvent, ...],
    candidate: ArtifactReviewCandidatePayload,
    readiness_origin: CollaborationEvent | None = None,
) -> SynchronizationMaterial:
    """Reuse exact current evidence from candidate preparation and its pinned readiness."""
    origin = next((event for event in events if event.event_id == candidate.lead_judgment_event_id
                   and type(event.payload) is CollaborationLeadJudgmentPayload), None)
    documents = {document.path: document for document in material.census.documents
                 if document.digest == material.current_digests.get(document.path)}
    documents.update((document.path, document) for document in material.census.prior_documents
                     if document.path not in documents)
    reads: dict[str, SynchronizationRead] = {}
    text: dict[str, str] = {}
    for event in (origin, readiness_origin):
        if event is None or type(event.payload) is not CollaborationLeadJudgmentPayload:
            continue
        ref = synchronization_event_ref(event)
        for receipt in event.payload.evidence.receipts:
            if receipt.acquisition not in {
                CollaborationEvidenceAcquisition.READ, CollaborationEvidenceAcquisition.ATTACHED_REFERENCE,
            } or receipt.source_kind not in {
                CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
                CollaborationInputKind.WRITING_ARTIFACT,
            }:
                continue
            path = receipt.locator.removeprefix("${").removesuffix("}")
            document = documents.get(path)
            if document is None or path not in base_state or path in reads:
                continue
            selected = select_current_reference_bytes(base_state[path], receipt.start_line, receipt.end_line)
            if selected is None or f"sha256:{sha256(selected).hexdigest()}" != receipt.content_digest:
                continue
            if receipt.acquisition is CollaborationEvidenceAcquisition.ATTACHED_REFERENCE:
                if receipt.revision_digest != f"sha256:{document.digest}":
                    continue
            elif selected != base_state[path] and receipt.revision_digest != document.revision:
                continue
            read = SynchronizationRead(
                document, ref, receipt.evidence_id, receipt.start_line, receipt.end_line,
                selection_digest=receipt.content_digest, source_revision=receipt.revision_digest,
            )
            reads[path] = read
            text[earlier_read_id(read)] = selected.decode("utf-8")
    return replace(material, prior_reads=tuple(reads.values()), prior_read_text=MappingProxyType(text))


def capture_synchronization_read_evidence(
    invocation_id: str,
    synthesis: SynchronizationEventRef,
    candidate: SynchronizationCandidate | None,
    used_reads: tuple[ContextEvidenceReceipt, ...],
    *,
    absent_origins: tuple[SynchronizationAbsenceOrigin, ...] = (),
    current_state_digest: str | None = None,
) -> SynchronizationReadEvidence:
    """Retain only the exact physical artifact READs authorized for this synthesis."""
    receipts = tuple(
        SynchronizationReadReceipt(
            item.evidence_id, item.provenance.locator, item.source_digest, item.revision,
            item.boundary_digest, item.boundary.start, item.boundary.end,
            item.originating_search_action_index, item.completing_read_action_index,
        )
        for item in used_reads
        if item.source_kind is ContextSourceKind.WRITING_ARTIFACT
        and canonical_artifact_kind(item.provenance.locator) is not None
        and item.boundary.kind in {ContextSourceBoundaryKind.ARTIFACT_LINES, ContextSourceBoundaryKind.WHOLE}
    )
    return SynchronizationReadEvidence(
        invocation_id, synthesis, candidate, receipts, absent_origins, current_state_digest,
    )


def _request_event(
    events: tuple[CollaborationEvent, ...],
    anchor: CollaborationEvent,
    proposal: CollaborationProposal,
) -> CollaborationEvent | None:
    scope = proposal.authoring_scope
    if scope is None:
        return None
    request_ids = {
        event.payload.receipt.origin_event_id
        for event in events
        if type(event.payload) is CollaborationLeadTaskTransitionPayload
        and (event.payload.receipt.target_task_id, event.payload.receipt.target_task_revision)
        == (scope.task_id, scope.task_revision)
        and event.sequence < anchor.sequence
    }
    exact = tuple(event for event in events if event.event_id in request_ids
                  and event.sender.kind is CollaborationActorKind.USER)
    if len(exact) == 1:
        return exact[0]
    same_turn = tuple(event for event in events if event.turn_id == anchor.turn_id
                      and event.sender.kind is CollaborationActorKind.USER
                      and event.sequence < anchor.sequence)
    return same_turn[0] if not exact and len(same_turn) == 1 else None


def _review_sources(
    judgment: SynchronizationReviewFinding,
    replies: dict[str, CollaborationEventDraft],
    lead_source: SynchronizationEventRef,
) -> SynchronizationEventRef | None:
    if not judgment.review_event_ids:
        return lead_source if judgment.domain is None or judgment.judgment.value == "needs_change" else None
    if any(event_id not in replies for event_id in judgment.review_event_ids):
        return None
    if judgment.domain is None:
        return lead_source
    required = specialists_for_domains((judgment.domain,))
    positive = judgment.judgment.value in ("no_change", "not_applicable")
    return next((
        synchronization_event_ref(event)
        for event in (replies[event_id] for event_id in judgment.review_event_ids)
        if event.sender.agent_id in required
        and event.payload.mode is SpecialistAssignmentMode.REVIEW
        and event.payload.assessment not in (
            CollaborationSpecialistAssessment.INSUFFICIENT_EVIDENCE,
            CollaborationSpecialistAssessment.DRAFTED,
        )
        and (not positive or event.payload.assessment is CollaborationSpecialistAssessment.SUPPORTS)
    ), None)


def _qualified_finding(
    *,
    changed: SynchronizationDocument,
    target: SynchronizationDocument,
    documents_changed: frozenset[str],
    supplied: SynchronizationReviewFinding | None,
    reads: dict[str, SynchronizationRead],
    lead_source: SynchronizationEventRef,
    candidate_source: SynchronizationEventRef | None,
    replies: dict[str, CollaborationEventDraft],
    absences: tuple[SynchronizationAbsenceProof, ...] = (),
) -> SynchronizationFinding:
    pair = (changed.path, target.path)
    absent_paths = {proof.origin.path for proof in absences}
    absence_ids = {proof.origin.receipt_id for proof in absences}
    if target.absence is SynchronizationAbsence.INITIAL:
        return SynchronizationFinding(*pair, SynchronizationJudgment.INITIAL,
                                      "This planned document has not been constructed yet.")
    if target.digest is None and target.path not in absent_paths:
        return SynchronizationFinding(*pair, SynchronizationJudgment.MISSING,
                                      "The required target is absent from the projected state.")
    if supplied is None or supplied.judgment.value == "unknown":
        reason = ("No semantic finding covers this target." if supplied is None else supplied.reason)
        return SynchronizationFinding(*pair, SynchronizationJudgment.UNDECIDED, reason)

    evidence = tuple(reads.get(evidence_id) for evidence_id in supplied.evidence_ids
                     if evidence_id != "candidate" and evidence_id not in absence_ids)
    review_source = _review_sources(supplied, replies, lead_source)
    if review_source is None or any(item is None for item in evidence):
        return SynchronizationFinding(*pair, SynchronizationJudgment.UNREAD,
                                      "The finding cites unavailable READ or specialist evidence.")
    material = []
    for document in (changed, target):
        if document.path in absent_paths:
            continue
        if candidate_source is not None and document.path in documents_changed and document.digest is not None:
            material.append(SynchronizationRead(document, candidate_source, "candidate"))
            continue
        matching = tuple(item for item in evidence if item is not None and item.document == document)
        if not matching:
            return SynchronizationFinding(*pair, SynchronizationJudgment.UNREAD,
                                          "The finding lacks READ evidence for the current document bytes.")
        material.extend(matching)
    return SynchronizationFinding(
        *pair, SynchronizationJudgment(supplied.judgment.value), supplied.reason,
        tuple(material), review_source, supplied.domain, supplied.action, supplied.resolution,
        supplied.abstraction, supplied.resolves, absences,
    )


def _material_reads(
    material: SynchronizationMaterial,
    read_evidence: SynchronizationReadEvidence,
    read_source: SynchronizationEventRef,
) -> dict[str, SynchronizationRead]:
    documents = {document.path: document for document in material.census.documents}
    prior = {document.path: document for document in material.census.prior_documents}
    reads = {earlier_read_id(item): item for item in material.prior_reads}
    for item in read_evidence.receipts:
        document = documents.get(item.path)
        if document is not None and document.absence is SynchronizationAbsence.ABSENT:
            document = prior.get(item.path)
        if document is not None and item.source_digest == f"sha256:{document.digest}":
            reads[item.receipt_id] = SynchronizationRead(
                document, read_source, item.receipt_id, item.start_line, item.end_line,
                selection_digest=item.selection_digest, source_revision=item.source_revision,
            )
    return reads


def _conditions_and_rules(
    proposal: CollaborationProposal,
    documents: dict[str, SynchronizationDocument],
) -> tuple[str, str]:
    conditions = authoring_scope_to_dict(proposal.authoring_scope)
    conditions.update(editorial_context=proposal.basis.editorial_context_digest)
    rules = documents["core_documents/narrative_rules.md"]
    return (
        "sha256:" + sha256(canonical_json(conditions)).hexdigest(),
        "sha256:" + sha256(canonical_json(rules.digest)).hexdigest(),
    )


def _specialist_replies(replies: tuple[CollaborationEventDraft, ...]) -> dict[str, CollaborationEventDraft]:
    return {
        event.event_id: event for event in replies
        if type(event.payload) is CollaborationSpecialistReplyPayload
    }


def qualify_candidate_synchronization(
    *,
    events: tuple[CollaborationEvent, ...],
    proposal: CollaborationProposal,
    candidate_payload: ArtifactReviewCandidatePayload,
    synthesis: SynchronizationEventRef,
    replies: tuple[CollaborationEventDraft, ...],
    material: SynchronizationMaterial,
    findings: tuple[SynchronizationReviewFinding, ...],
    read_evidence: SynchronizationReadEvidence,
    read_source: SynchronizationEventRef,
) -> SynchronizationAssessment | None:
    """Use runtime-supplied candidate, exact reviewers, and completed physical READs."""
    candidate = candidate_payload.candidate
    identity = SynchronizationCandidate(candidate.candidate_id, candidate.candidate_revision,
                                        candidate.candidate_digest, candidate.change_set.change_set_digest)
    if read_evidence.synthesis != synthesis or read_evidence.candidate != identity:
        raise ValueError("candidate synchronization was given uncorrelated READ evidence")
    candidate_event = next((event for event in events if event.payload == candidate_payload), None)
    scope = proposal.authoring_scope
    if candidate_event is None or scope is None:
        return None
    request = _request_event(events, candidate_event, proposal)
    if request is None:
        return None
    documents = {document.path: document for document in material.census.documents}
    prior = {document.path: document for document in material.census.prior_documents}
    reads = _material_reads(material, read_evidence, read_source)
    changed_documents = frozenset(path for path in material.census.changes if documents[path].digest is not None)
    conditions, rules = _conditions_and_rules(proposal, documents)
    basis = SynchronizationBasis(
        scope.task_id, scope.task_revision, synchronization_event_ref(request),
        conditions, rules, candidate.change_set.base_state_digest, candidate.change_set.projected_state_digest,
        identity, review_synthesis=synthesis,
    )
    supplied = {(item.changed_path, item.target_path): item for item in findings
                if item.changed_path in material.census.changes and item.target_path in documents}
    pairs = set(supplied)
    pairs.update((changed, target.path) for target in documents.values()
                 if target.absence is SynchronizationAbsence.INITIAL for changed in material.census.changes)
    candidate_source = synchronization_event_ref(candidate_event)
    reviews = _specialist_replies(replies)
    qualified = tuple(
        _qualified_finding(
            changed=documents[changed] if changed in changed_documents else prior[changed],
            target=prior.get(target, documents[target]) if documents[target].digest is None else documents[target],
            documents_changed=changed_documents, supplied=supplied.get((changed, target)),
            reads=reads, lead_source=synthesis, candidate_source=candidate_source, replies=reviews,
        )
        for changed, target in sorted(pairs) if changed != target
    )
    return SynchronizationAssessment(basis, material.census, qualified)


def qualify_current_synchronization(
    *,
    events: tuple[CollaborationEvent, ...],
    proposal: CollaborationProposal,
    synthesis: SynchronizationEventRef,
    replies: tuple[CollaborationEventDraft, ...],
    material: SynchronizationMaterial,
    findings: tuple[SynchronizationReviewFinding, ...],
    read_evidence: SynchronizationReadEvidence,
    read_source: SynchronizationEventRef,
) -> SynchronizationAssessment | None:
    """Qualify a review of current physical bytes against selected obligations."""
    if (
        read_evidence.synthesis != synthesis or read_evidence.candidate is not None
        or read_evidence.current_state_digest != material.current_state_digest
        or read_evidence.absent_origins != material.absence_origins
    ):
        raise ValueError("current synchronization was given uncorrelated physical evidence")
    scope = proposal.authoring_scope
    if scope is None or scope.intent is not AuthoringIntent.REVIEW_ONLY or material.current_state_digest is None:
        return None
    anchor = next((event for event in events if event.payload == proposal), None)
    request = None if anchor is None else _request_event(events, anchor, proposal)
    if request is None:
        return None
    documents = {document.path: document for document in material.census.documents}
    reads = _material_reads(material, read_evidence, read_source)
    conditions, rules = _conditions_and_rules(proposal, documents)
    basis = SynchronizationBasis(
        scope.task_id, scope.task_revision, synchronization_event_ref(request),
        conditions, rules, material.current_state_digest, material.current_state_digest,
        review_synthesis=synthesis,
    )
    selected = {item.condition_id: item for item in material.selected_conditions}
    reviews = _specialist_replies(replies)
    qualified = []
    for finding in findings:
        paths = {finding.changed_path, finding.target_path}
        if (
            not finding.resolves
            or not paths.issubset(scope.target_paths)
            or finding.changed_path not in material.census.changes
            or any(path not in documents for path in paths)
            or any(
                condition_id not in selected
                or finding.domain is not selected[condition_id].domain
                or paths != {selected[condition_id].changed_path, selected[condition_id].target_path}
                for condition_id in finding.resolves
            )
        ):
            continue
        absent_paths = {path for path in paths if documents[path].digest is None}
        absences = tuple(
            SynchronizationAbsenceProof(origin, read_source)
            for origin in read_evidence.absent_origins
            if origin.path in absent_paths and origin.condition_id in finding.resolves
            and origin.receipt_id in finding.evidence_ids
        )
        if absent_paths and (
            finding.judgment.value != "not_applicable"
            or {(proof.origin.path, proof.origin.condition_id) for proof in absences}
            != {(path, condition_id) for path in absent_paths for condition_id in finding.resolves}
        ):
            continue
        qualified.append(_qualified_finding(
            changed=documents[finding.changed_path], target=documents[finding.target_path],
            documents_changed=frozenset(), supplied=finding, reads=reads, lead_source=synthesis,
            candidate_source=None, replies=reviews, absences=absences,
        ))
    return SynchronizationAssessment(basis, material.census, tuple(qualified))
