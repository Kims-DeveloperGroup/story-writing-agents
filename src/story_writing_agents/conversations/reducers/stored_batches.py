"""Pure stored batch identity and ordering checks."""

from __future__ import annotations

from ..events import (
    CollaborationEvent as CollaborationEventV2,
    CollaborationEventBatch,
    collaboration_batch_digest,
)


def find_collaboration_batch(
    events: tuple[CollaborationEventV2, ...],
    batch_id: str,
) -> tuple[CollaborationEventBatch, tuple[CollaborationEventV2, ...]] | None:
    offset = 0
    while offset < len(events):
        size = events[offset].batch_size
        stored_events = events[offset : offset + size]
        if stored_events[0].batch_id == batch_id:
            return (
                CollaborationEventBatch(
                    batch_id=batch_id,
                    expected_last_sequence=offset,
                    events=tuple(event.to_draft() for event in stored_events),
                ),
                stored_events,
            )
        offset += size
    return None


def validate_stored_collaboration_batches(
    events: tuple[CollaborationEventV2, ...],
) -> None:
    identity = (events[0].session_id, events[0].conversation_id)
    event_ids: set[str] = set()
    batch_ids: set[str] = set()
    offset = 0
    while offset < len(events):
        first = events[offset]
        if (first.session_id, first.conversation_id) != identity:
            raise ValueError("collaboration history spans multiple transcripts")
        if first.sequence != offset + 1:
            raise ValueError("collaboration sequences must be contiguous from one")
        if first.batch_ordinal != 0:
            raise ValueError("collaboration batch must begin at ordinal zero")
        if first.batch_id in batch_ids:
            raise ValueError("collaboration batch ID is reused noncontiguously")
        batch_ids.add(first.batch_id)
        end = offset + first.batch_size
        if end > len(events):
            raise ValueError("collaboration history contains a partial batch")
        group = events[offset:end]
        for ordinal, event in enumerate(group):
            if (event.session_id, event.conversation_id) != identity:
                raise ValueError("collaboration batch crosses transcript identity")
            if event.turn_id != first.turn_id:
                raise ValueError("collaboration batch crosses turn identity")
            if event.sequence != offset + ordinal + 1:
                raise ValueError("collaboration sequence metadata is not contiguous")
            if (
                event.batch_id != first.batch_id
                or event.batch_size != first.batch_size
                or event.batch_ordinal != ordinal
                or event.batch_digest != first.batch_digest
            ):
                raise ValueError("collaboration batch metadata is inconsistent")
            if event.event_id in event_ids:
                raise ValueError("collaboration event ID is reused")
            event_ids.add(event.event_id)
        reconstructed = CollaborationEventBatch(
            batch_id=first.batch_id,
            expected_last_sequence=offset,
            events=tuple(event.to_draft() for event in group),
        )
        if collaboration_batch_digest(reconstructed) != first.batch_digest:
            raise ValueError("collaboration batch digest does not match its events")
        offset = end


def retained_collaboration_batch(
    events: tuple[CollaborationEventV2, ...], offset: int,
) -> tuple[CollaborationEventV2, ...]:
    """Group retained rows after full physical envelope/batch validation."""
    end = offset + 1
    while end < len(events) and events[end].batch_id == events[offset].batch_id:
        end += 1
    return events[offset:end]
