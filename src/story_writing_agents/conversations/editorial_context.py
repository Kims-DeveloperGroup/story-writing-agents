"""Project authoritative editorial context from collaboration history."""

from __future__ import annotations

import hashlib

from .event_contracts.artifact_apply import (
    ArtifactApplicationDecisionPayload,
    ArtifactApplicationRequestPayload,
    ArtifactApplicationResultPayload,
)
from .event_contracts.artifact_reviews import ArtifactReviewedProposal
from .event_contracts.authoring_records import CollaborationEditorialRecordPayload
from .event_vocabulary import (
    ArtifactApplicationDecisionKind,
    ArtifactApplicationOutcomeKind,
)
from .events import (
    CollaborationEvent as CollaborationEventV2,
    CollaborationProposal,
    LeadRecommendationRequestPayload,
    encode_collaboration_event,
)
from .reducers.chronological_history import project_collaboration_history
from .reducers.editorial_event_evidence import editorial_provenance_available
from ..editorial_state import (
    EditorialApplicationAttempt,
    EditorialAppliedFact,
    EditorialApprovalState,
    EditorialProvenanceRef,
    EditorialStateIntegrityError,
    EditorialStateProjection,
)


def project_editorial_state(session_id: str, events: tuple[CollaborationEventV2, ...]) -> EditorialStateProjection:
    """Derive exact USER chains and historical Apply associations across a session."""
    if any(event.session_id != session_id for event in events):
        raise EditorialStateIntegrityError("editorial source history crosses sessions")
    if not any(type(event.payload) is CollaborationEditorialRecordPayload for event in events):
        if any(type(event.payload) in {CollaborationProposal, LeadRecommendationRequestPayload} and event.payload.basis.editorial_refs for event in events):
            raise EditorialStateIntegrityError("editorial workflow capture has no USER history")
        return EditorialStateProjection(session_id, ())
    groups = {}
    for event in events:
        groups.setdefault(event.conversation_id, []).append(event)
    for group in groups.values():
        project_collaboration_history(tuple(group))
    records = tuple(event.payload.record for event in events if type(event.payload) is CollaborationEditorialRecordPayload)
    record_events = {event.payload.record.ref: event for event in events
                     if type(event.payload) is CollaborationEditorialRecordPayload}
    for admission in record_events.values():
        record = admission.payload.record
        for ref in tuple(dict.fromkeys(record.context_refs + record.request.relation_refs())):
            source = record_events.get(ref)
            if source is None or source.event_id == admission.event_id:
                raise EditorialStateIntegrityError("editorial context names an unavailable admission")
            if source.conversation_id == admission.conversation_id:
                if source.sequence >= admission.sequence:
                    raise EditorialStateIntegrityError("editorial context names a future local source")
            else:
                prefix = tuple(row for row in groups[source.conversation_id]
                               if row.sequence <= source.sequence)
                project_collaboration_history(prefix)
    state = EditorialStateProjection(session_id, records)
    by_ref = {record.ref: record for record in records}
    requests = [record.request.request_id for record in records]
    if len(set(requests)) != len(requests):
        raise EditorialStateIntegrityError("editorial request repeats across conversations")
    gaps = tuple((record.ref, source) for record in records for source in record.draft.provenance_refs
                 if not editorial_provenance_available(source, events))
    for event in events:
        if type(event.payload) not in {CollaborationProposal, LeadRecommendationRequestPayload}:
            continue
        for ref in event.payload.basis.editorial_refs:
            if ref not in by_ref or by_ref[ref].approval_state is not EditorialApprovalState.SELECTED:
                raise EditorialStateIntegrityError("workflow cites no exact selected USER editorial revision")
    attempts = []
    for request_event in sorted(events, key=lambda row: (row.conversation_id, row.sequence)):
        request = request_event.payload
        if type(request) is not ArtifactApplicationRequestPayload:
            continue
        reviewed = request.reviewed_proposal
        group = groups[request_event.conversation_id]
        consultations = tuple(row.payload for row in group
                              if type(row.payload) is CollaborationProposal
                              and (row.payload.proposal_id, row.payload.proposal_revision,
                                   row.payload.proposal_digest) == (
                                  reviewed.consultation_proposal_id,
                                  reviewed.consultation_proposal_revision,
                                  reviewed.consultation_proposal_digest))
        if len(consultations) != 1:
            raise EditorialStateIntegrityError("editorial application has no exact consultation")
        consultation = consultations[0]
        if not consultation.basis.editorial_refs:
            continue
        scope = consultation.authoring_scope
        if scope is None:
            raise EditorialStateIntegrityError("editorial application has no exact task scope")
        results = tuple(row for row in group
                        if type(row.payload) is ArtifactApplicationResultPayload
                        and row.payload.confirmation_request_id == request.confirmation_request_id
                        and row.payload.application_request_digest == request.application_request_digest)
        if len(results) > 1:
            raise EditorialStateIntegrityError("editorial application repeats its exact result")
        result_event = results[0] if results else None
        result = None if result_event is None else result_event.payload
        decisions = tuple(row.payload for row in group
                          if type(row.payload) is ArtifactApplicationDecisionPayload
                          and row.payload.confirmation_request_event_id == request_event.event_id)
        if result is not None:
            phase = {ArtifactApplicationOutcomeKind.APPLIED: "applied",
                     ArtifactApplicationOutcomeKind.UNCHANGED: "unchanged",
                     ArtifactApplicationOutcomeKind.OUTCOME_UNKNOWN: "uncertain",
                     ArtifactApplicationOutcomeKind.RECOVERY_REQUIRED: "recovery_required"}.get(
                         result.outcome, "failed")
        else:
            phase = "uncertain" if any(item.decision is ArtifactApplicationDecisionKind.APPLY
                                       for item in decisions) else "pending"
        request_ref = EditorialProvenanceRef(
            session_id, request_event.conversation_id, request_event.event_id,
            hashlib.sha256(encode_collaboration_event(request_event)).hexdigest())
        result_ref = None if result_event is None else EditorialProvenanceRef(
            session_id, result_event.conversation_id, result_event.event_id,
            hashlib.sha256(encode_collaboration_event(result_event)).hexdigest())
        paths = tuple(dict.fromkeys(path for operation in request.application_request.change_set.operations
                                    for path in (getattr(operation, "path", None),
                                                 getattr(operation, "source_path", None),
                                                 getattr(operation, "destination_path", None))
                                    if path is not None))
        affected = () if result is None or result.result is None else tuple(result.result.affected_paths)
        for ref in consultation.basis.editorial_refs:
            attempts.append(EditorialApplicationAttempt(
                ref, request_ref, result_ref, scope.task_id, scope.task_revision,
                reviewed.proposal_id, reviewed.proposal_digest,
                reviewed.candidate.candidate_id, reviewed.candidate.candidate_digest,
                request.application_request.provenance.application_id,
                request.application_request_digest, paths, affected, phase))
    facts = []
    for event in sorted(events, key=lambda item: (item.conversation_id, item.sequence)):
        result = event.payload
        if type(result) is not ArtifactApplicationResultPayload or result.outcome not in {
            ArtifactApplicationOutcomeKind.APPLIED, ArtifactApplicationOutcomeKind.UNCHANGED}:
            continue
        local = groups[event.conversation_id]
        reviewed = tuple(item.payload for item in local if type(item.payload) is ArtifactReviewedProposal
                         and item.payload.proposal_id == result.reviewed_proposal_id
                         and item.payload.proposal_digest == result.reviewed_proposal_digest)
        if len(reviewed) != 1:
            raise EditorialStateIntegrityError("editorial association has no exact reviewed proposal")
        reviewed = reviewed[0]
        consultations = tuple(item.payload for item in local if type(item.payload) is CollaborationProposal
            and (item.payload.proposal_id, item.payload.proposal_revision, item.payload.proposal_digest) ==
            (reviewed.consultation_proposal_id, reviewed.consultation_proposal_revision, reviewed.consultation_proposal_digest))
        if len(consultations) != 1:
            raise EditorialStateIntegrityError("editorial association has no exact reviewed consultation")
        consultation = consultations[0]
        if not consultation.basis.editorial_refs:
            continue
        scope = consultation.authoring_scope
        if scope is None or result.result is None:
            raise EditorialStateIntegrityError("editorial application lacks exact task or application facts")
        application_ref = EditorialProvenanceRef(event.session_id, event.conversation_id, event.event_id,
                                                hashlib.sha256(encode_collaboration_event(event)).hexdigest())
        for ref in consultation.basis.editorial_refs:
            facts.append(EditorialAppliedFact(ref, scope.task_id, scope.task_revision, reviewed.proposal_id,
                reviewed.proposal_digest, reviewed.candidate.candidate_id, reviewed.candidate.candidate_digest,
                application_ref, result.application_id, result.application_request_digest, result.outcome.value,
                tuple(result.result.affected_paths)))
    return EditorialStateProjection(session_id, records, tuple(facts), gaps, tuple(attempts))
