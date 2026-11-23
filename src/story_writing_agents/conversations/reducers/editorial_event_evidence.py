"""Validate editorial event provenance against stored history."""

from __future__ import annotations

import hashlib

from ..event_contracts.authoring_records import (
    CollaborationEditorialRecordPayload,
    CollaborationLeadExplorationResultPayload,
)
from ..event_contracts.lead_outcomes import CollaborationLeadJudgmentPayload
from ..event_vocabulary import CollaborationEvidenceAcquisition
from ..events import (
    CollaborationEvent as CollaborationEventV2,
    encode_collaboration_event,
)
from ...editorial_state import (
    EditorialProvenanceRef,
    EditorialStateIntegrityError,
)


def validate_editorial_event_headers(events: tuple[CollaborationEventV2, ...]) -> None:
    requests = set()
    for event in events:
        if type(event.payload) is not CollaborationEditorialRecordPayload:
            continue
        record = event.payload.record
        if event.batch_size != 1:
            raise EditorialStateIntegrityError("USER editorial intake cannot co-batch workflow authority")
        if (record.session_id, record.conversation_id, record.event_id) != (event.session_id, event.conversation_id, event.event_id):
            raise EditorialStateIntegrityError("editorial record does not match its exact USER event")
        if record.request.request_id in requests:
            raise EditorialStateIntegrityError("editorial request identity repeats")
        requests.add(record.request.request_id)


def editorial_provenance_available(source: EditorialProvenanceRef, events: tuple[CollaborationEventV2, ...]) -> bool:
    matches = tuple(event for event in events if (event.session_id, event.conversation_id, event.event_id) ==
                    (source.session_id, source.conversation_id, source.event_id))
    if len(matches) != 1 or hashlib.sha256(encode_collaboration_event(matches[0])).hexdigest() != source.event_digest:
        return False
    if source.member_id is None:
        return True
    payload = matches[0].payload
    if type(payload) is CollaborationLeadExplorationResultPayload:
        return any(item.idea_id == source.member_id for item in payload.report.ideas)
    evidence = payload.evidence if type(payload) is CollaborationLeadJudgmentPayload else None
    return evidence is not None and any(receipt.evidence_id == source.member_id and
        receipt.acquisition is CollaborationEvidenceAcquisition.READ for receipt in evidence.receipts)
