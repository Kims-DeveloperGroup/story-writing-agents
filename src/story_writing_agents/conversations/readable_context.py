"""Read-only collaboration history views and bounded selection."""

from __future__ import annotations

from ..agent_context.group_observation import LeadGroupTrace
from .event_vocabulary import COLLABORATION_EVENT_SCHEMA_VERSION
from .events import (
    CollaborationEvent as CollaborationEventV2,
    CollaborationLeadGroupTracePayload,
)
from .reducers.chronological_history import project_collaboration_history
from .views.context_value_validation import validate_identifier as _validate_identifier
from .views.readable_records import (
    COLLABORATION_READABLE_CONTEXT_POLICY,
    CollaborationContextBootstrap,
    CollaborationReadableContext,
    CollaborationReadableContextPolicy,
    CollaborationReadableRecord,
    collaboration_readable_fields as _collaboration_readable_fields,
)


def project_collaboration_readable_history(
    events: tuple[CollaborationEventV2, ...],
) -> tuple[CollaborationReadableRecord, ...]:
    """Return one readable record per event after full history validation.

    The projection is pure and preserves strict durable sequence order. It
    accepts only canonical schema-v2 events; schema-v1 values, partial batches,
    sequence gaps, duplicate identities, digest drift, and illegal transitions
    fail before any record is returned.
    """

    project_collaboration_history(events)
    return tuple(
        CollaborationReadableRecord(
            schema_version=event.schema_version,
            session_id=event.session_id,
            conversation_id=event.conversation_id,
            turn_id=event.turn_id,
            step_id=event.step_id,
            event_id=event.event_id,
            batch_id=event.batch_id,
            batch_ordinal=event.batch_ordinal,
            batch_size=event.batch_size,
            batch_digest=event.batch_digest,
            sequence=event.sequence,
            timestamp=event.timestamp,
            sender=event.sender,
            recipient=event.recipient,
            direction=event.direction,
            kind=event.kind,
            payload=event.payload,
            fields=_collaboration_readable_fields(event.payload),
        )
        for event in events
    )


def select_bounded_collaboration_context(
    events: tuple[CollaborationEventV2, ...],
    *,
    event_ids: tuple[str, ...] = (),
    policy: CollaborationReadableContextPolicy = (
        COLLABORATION_READABLE_CONTEXT_POLICY
    ),
) -> CollaborationReadableContext:
    """Return a content-free bootstrap plus explicitly selected records.

    The default empty selection never copies transcript text into provider
    context. Callers add known event identities after an authorized search or
    read. Selected records remain in authoritative sequence order and are
    retained record-atomically up to both fixed caps. Current user input is not
    accepted here and therefore remains a separate invocation input.
    """

    if type(policy) is not CollaborationReadableContextPolicy:
        raise TypeError("readable context policy is invalid")
    records = project_collaboration_readable_history(events)
    if type(event_ids) is not tuple:
        raise TypeError("readable context event IDs must be an exact tuple")
    for event_id in event_ids:
        _validate_identifier(event_id, label="readable context event ID")
    if len(set(event_ids)) != len(event_ids):
        raise ValueError("readable context event IDs must be unique")

    bootstrap = CollaborationContextBootstrap(
        schema_version=COLLABORATION_EVENT_SCHEMA_VERSION,
        session_id=records[0].session_id if records else None,
        conversation_id=records[0].conversation_id if records else None,
        total_records=len(records),
        last_sequence=records[-1].sequence if records else 0,
        tail_batch_digest=records[-1].batch_digest if records else None,
    )
    by_id = {record.event_id: record for record in records}
    unknown = tuple(event_id for event_id in event_ids if event_id not in by_id)
    if unknown:
        raise ValueError("readable context references an unknown event ID")
    selected_event_ids = set(event_ids)
    requested = tuple(
        record for record in records if record.event_id in selected_event_ids
    )

    retained: list[CollaborationReadableRecord] = []
    retained_utf8_bytes = 0
    for record in requested:
        record_bytes = record.readable_utf8_bytes
        if len(retained) == policy.max_records:
            break
        if retained_utf8_bytes + record_bytes > policy.max_utf8_bytes:
            break
        retained.append(record)
        retained_utf8_bytes += record_bytes

    omitted = len(requested) - len(retained)
    return CollaborationReadableContext(
        bootstrap=bootstrap,
        records=tuple(retained),
        requested_records=len(requested),
        retained_records=len(retained),
        omitted_records=omitted,
        retained_utf8_bytes=retained_utf8_bytes,
        truncated=omitted > 0,
    )


def project_lead_group_traces(events: tuple[CollaborationEventV2, ...]) -> tuple[LeadGroupTrace, ...]:
    """Explicit private read projection; never part of task or reply authority."""
    project_collaboration_history(events)
    result = tuple(event.payload.trace for event in events
                   if type(event.payload) is CollaborationLeadGroupTracePayload)
    if len({item.invocation_id for item in result}) != len(result):
        raise ValueError("logical invocation has duplicate group traces")
    return result
