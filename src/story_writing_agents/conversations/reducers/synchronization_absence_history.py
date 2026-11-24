"""Qualify current absence of a creation that never received a successful Apply."""

from collections.abc import Callable, Mapping
from dataclasses import replace

from ...synchronization_assessment import (
    SynchronizationAbsence,
    SynchronizationAbsenceOrigin,
    SynchronizationAbsenceProof,
    SynchronizationAssessment,
    SynchronizationCandidate,
    SynchronizationDocument,
    SynchronizationEventRef,
    SynchronizationFinding,
    SynchronizationJudgment,
)
from ...synchronization_obligations import (
    SynchronizationCondition,
    SynchronizationObligations,
    SynchronizationStatus,
)
from ...specialist_routing import ArtifactReviewDomain
from ...writing_artifacts.change_sets import ArtifactCreateOperation, ArtifactRenameOperation
from ..event_contracts.artifact_apply import ArtifactApplicationRequestPayload, ArtifactApplicationResultPayload
from ..event_contracts.artifact_reviews import (
    ArtifactReviewCandidatePayload,
    ArtifactReviewedProposal,
    ArtifactReviewSynthesisPayload,
)
from ..event_contracts.synchronization import SynchronizationPayload
from ..event_vocabulary import ArtifactApplicationOutcomeKind, ArtifactReviewDecision
from ..events import CollaborationEvent, synchronization_event_ref


_SUCCESS = (ArtifactApplicationOutcomeKind.APPLIED, ArtifactApplicationOutcomeKind.UNCHANGED)
_KNOWN_UNAPPLIED = (
    ArtifactApplicationOutcomeKind.REJECTED,
    ArtifactApplicationOutcomeKind.SUPERSEDED,
    ArtifactApplicationOutcomeKind.STALE,
)


def _candidate_key(candidate) -> SynchronizationCandidate:
    return SynchronizationCandidate(
        candidate.candidate_id, candidate.candidate_revision,
        candidate.candidate_digest, candidate.change_set_digest,
    )


def successful_candidates(events: tuple[CollaborationEvent, ...]) -> frozenset[SynchronizationCandidate]:
    return frozenset(
        _candidate_key(payload.result)
        for event in events
        if type(payload := event.payload) is ArtifactApplicationResultPayload
        and payload.outcome in _SUCCESS and payload.result is not None
    )


def _owns_path(candidate: ArtifactReviewCandidatePayload, path: str) -> bool:
    return any(
        (type(operation) is ArtifactCreateOperation and operation.path == path)
        or (type(operation) is ArtifactRenameOperation and operation.destination_path == path)
        for operation in candidate.candidate.change_set.operations
    )


def _disposition_is_known(
    events: tuple[CollaborationEvent, ...], origin: SynchronizationAbsenceOrigin,
    synthesis: CollaborationEvent | None,
) -> bool:
    requests: set[str] = set()
    outcomes: dict[str, ArtifactApplicationOutcomeKind] = {}
    published = False
    for event in events:
        payload = event.payload
        if type(payload) is ArtifactApplicationRequestPayload:
            if _candidate_key(payload.application_request) == origin.candidate:
                requests.add(payload.application_request.application_id)
        elif type(payload) is ArtifactApplicationResultPayload:
            outcomes[payload.application_id] = payload.outcome
        elif type(payload) is ArtifactReviewedProposal:
            if (
                synthesis is not None and payload.synthesis_event_id == synthesis.event_id
                and payload.candidate.candidate_id == origin.candidate.candidate_id
                and payload.candidate.candidate_revision == origin.candidate.candidate_revision
                and payload.candidate.candidate_digest == origin.candidate.candidate_digest
            ):
                published = True
    if requests:
        return all(outcomes.get(request_id) in _KNOWN_UNAPPLIED for request_id in requests)
    return published or (
        synthesis is not None and type(synthesis.payload) is ArtifactReviewSynthesisPayload
        and synthesis.payload.decision in (ArtifactReviewDecision.REVISE, ArtifactReviewDecision.REJECT)
    )


def prospective_absence_is_valid(
    origin: SynchronizationAbsenceOrigin,
    pair: tuple[str, str],
    domain: ArtifactReviewDomain | None,
    detection: CollaborationEvent,
    candidate_tail: CollaborationEvent,
    candidate: CollaborationEvent,
    owner_history: tuple[CollaborationEvent, ...],
) -> bool:
    payload = detection.payload
    if (
        type(payload) is not SynchronizationPayload or payload.assessment is None
        or type(candidate.payload) is not ArtifactReviewCandidatePayload
        or synchronization_event_ref(detection) != origin.detected_by
        or synchronization_event_ref(candidate_tail) != origin.candidate_tail
        or not (candidate.sequence < detection.sequence <= candidate_tail.sequence)
        or not (candidate.conversation_id == detection.conversation_id == candidate_tail.conversation_id)
    ):
        return False
    assessment = payload.assessment
    if (
        not assessment.basis.projected or assessment.basis.candidate != origin.candidate
        or origin.path not in pair
        or candidate.payload.candidate.change_set.change_set_digest != origin.candidate.change_set_digest
        or not _owns_path(candidate.payload, origin.path)
        or not any(
            prior.judgment is SynchronizationJudgment.NEEDS_CHANGE
            and set(prior.pair) == set(pair) and prior.domain is domain
            for prior in assessment.findings
        )
    ):
        return False
    prefix = tuple(event for event in owner_history if event.sequence <= candidate_tail.sequence)
    synthesis_ref = assessment.basis.review_synthesis
    synthesis = next((
        event for event in prefix if synthesis_ref is not None and event.event_id == synthesis_ref.event_id
        and synchronization_event_ref(event) == synthesis_ref
    ), None)
    return origin.candidate not in successful_candidates(prefix) and _disposition_is_known(prefix, origin, synthesis)


def derive_absence_origins(
    events: tuple[CollaborationEvent, ...],
    selected_conditions: tuple[SynchronizationCondition, ...],
    current_documents: tuple[SynchronizationDocument, ...],
) -> tuple[SynchronizationAbsenceOrigin, ...]:
    """Pin the exact owner prefix for selected projected conditions with absent creations."""
    absent_paths = {item.path for item in current_documents if item.absence is SynchronizationAbsence.ABSENT}
    if not absent_paths:
        return ()
    by_ref: dict[tuple[str, str], CollaborationEvent] = {}
    histories: dict[str, list[CollaborationEvent]] = {}
    for event in events:
        by_ref[event.conversation_id, event.event_id] = event
        histories.setdefault(event.conversation_id, []).append(event)
    applied = successful_candidates(events)
    origins = []
    for condition in selected_conditions:
        paths = absent_paths.intersection((condition.changed_path, condition.target_path))
        if not paths:
            continue
        detection = by_ref.get((condition.detected_by.conversation_id, condition.detected_by.event_id))
        if detection is None or type(detection.payload) is not SynchronizationPayload:
            continue
        assessment = detection.payload.assessment
        if assessment is None or not assessment.basis.projected or assessment.basis.candidate in applied:
            continue
        identity = assessment.basis.candidate
        assert identity is not None
        history = tuple(histories[detection.conversation_id])
        candidate = next((
            event for event in history if type(event.payload) is ArtifactReviewCandidatePayload
            and event.payload.candidate.candidate_digest == identity.candidate_digest
            and event.payload.candidate.candidate_id == identity.candidate_id
            and event.payload.candidate.candidate_revision == identity.candidate_revision
        ), None)
        if candidate is None:
            continue
        tail = max(history, key=lambda event: event.sequence)
        for path in sorted(paths):
            origin = SynchronizationAbsenceOrigin(
                path, condition.condition_id, condition.detected_by, identity, synchronization_event_ref(tail),
            )
            if prospective_absence_is_valid(
                origin, (condition.changed_path, condition.target_path), condition.domain,
                detection, tail, candidate, history,
            ):
                origins.append(origin)
    return tuple(origins)


def validate_absence_proof(
    proof: SynchronizationAbsenceProof,
    finding: SynchronizationFinding,
    assessment: SynchronizationAssessment,
    lookup: Callable[[SynchronizationEventRef], CollaborationEvent | None],
    candidates: dict[tuple[str, SynchronizationCandidate], CollaborationEvent],
    events: Mapping[tuple[str, str], CollaborationEvent],
) -> None:
    origin = proof.origin
    receipt = lookup(proof.source)
    detection = lookup(origin.detected_by)
    tail = lookup(origin.candidate_tail)
    if receipt is None or detection is None or tail is None:
        return
    evidence = receipt.payload.read_evidence if type(receipt.payload) is SynchronizationPayload else None
    candidate = candidates.get((detection.conversation_id, origin.candidate))
    if (
        evidence is None or evidence.candidate is not None
        or evidence.synthesis != assessment.basis.review_synthesis
        or evidence.current_state_digest != assessment.basis.result_state_digest
        or origin not in evidence.absent_origins
        or candidate is None
        or not prospective_absence_is_valid(
            origin, finding.pair, finding.domain, detection, tail, candidate,
            tuple(event for event in events.values() if event.conversation_id == detection.conversation_id),
        )
    ):
        raise ValueError("synchronization absence lacks exact current inventory and known unapplied candidate")


def reopen_applied_absences(
    obligations: SynchronizationObligations,
    assessments: Mapping[SynchronizationEventRef, SynchronizationAssessment],
    successful: frozenset[SynchronizationCandidate],
) -> SynchronizationObligations:
    """A later actual application invalidates an earlier absence, without rewriting history."""
    if not successful:
        return obligations
    heads = []
    for head in obligations.heads:
        states = []
        for state in head.conditions:
            proof = assessments.get(state.evidence) if state.evidence is not None else None
            if proof is not None and any(
                absence.origin.condition_id == state.condition.condition_id and absence.origin.candidate in successful
                for finding in proof.findings for absence in finding.absences
            ):
                state = replace(
                    state, status=SynchronizationStatus.REVALIDATE, evidence=None,
                    reason="The previously unapplied candidate now has a successful Apply.",
                )
            states.append(state)
        heads.append(replace(head, current_conditions=tuple(states)))
    return SynchronizationObligations(tuple(heads))
