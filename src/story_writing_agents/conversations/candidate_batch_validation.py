"""Validate collaboration append candidates in fixed conflict order."""

from __future__ import annotations

from .event_append_results import (
    CollaborationAppendError,
    CollaborationAppendReceipt,
)
from .event_vocabulary import (
    COLLABORATION_EVENT_SCHEMA_VERSION,
    CollaborationAppendFailure,
    CollaborationAppendStatus,
)
from .events import (
    CollaborationEvent as CollaborationEventV2,
    CollaborationEventBatch,
    collaboration_batch_digest,
)
from .reducers.chronological_history import project_collaboration_history
from .reducers.history_projection import CollaborationHistoryProjection
from .reducers.stored_batches import (
    find_collaboration_batch as _find_collaboration_batch,
)
from ..errors import SessionIdError


def validate_collaboration_candidate_batch(
    history: tuple[CollaborationEventV2, ...],
    batch: CollaborationEventBatch,
) -> CollaborationHistoryProjection | CollaborationAppendReceipt:
    """Classify one CAS candidate against history without I/O or mutation.

    A new valid candidate returns its projected history. An exact committed
    retry returns the original receipt with ``ALREADY_COMMITTED``. Every fixed
    semantic store failure is raised as ``CollaborationAppendError``.
    """

    if type(batch) is not CollaborationEventBatch:
        raise TypeError("candidate must be a CollaborationEventBatch")
    try:
        projection = project_collaboration_history(history)
    except (AssertionError, KeyError, SessionIdError, TypeError, ValueError) as exc:
        raise CollaborationAppendError(
            CollaborationAppendFailure.CORRUPT_HISTORY
        ) from exc

    matching_batch = _find_collaboration_batch(history, batch.batch_id)
    if matching_batch is not None:
        stored_batch, stored_events = matching_batch
        if stored_batch != batch:
            raise CollaborationAppendError(CollaborationAppendFailure.BATCH_ID_CONFLICT)
        return CollaborationAppendReceipt(
            status=CollaborationAppendStatus.ALREADY_COMMITTED,
            batch_id=stored_batch.batch_id,
            batch_digest=collaboration_batch_digest(stored_batch),
            first_sequence=stored_events[0].sequence,
            last_sequence=stored_events[-1].sequence,
            timestamps=tuple(event.timestamp for event in stored_events),
        )

    existing_event_ids = {event.event_id for event in history}
    if any(event.event_id in existing_event_ids for event in batch.events):
        raise CollaborationAppendError(CollaborationAppendFailure.EVENT_ID_CONFLICT)
    if batch.expected_last_sequence != projection.last_sequence:
        raise CollaborationAppendError(CollaborationAppendFailure.STALE_SEQUENCE)
    if history:
        first = batch.events[0]
        if (first.session_id, first.conversation_id) != (
            projection.session_id,
            projection.conversation_id,
        ):
            raise CollaborationAppendError(
                CollaborationAppendFailure.INVALID_TRANSITION
            )
    timestamp = "1970-01-01T00:00:00+00:00"
    digest = collaboration_batch_digest(batch)
    stored = tuple(
        CollaborationEventV2(
            schema_version=COLLABORATION_EVENT_SCHEMA_VERSION,
            event_id=event.event_id,
            session_id=event.session_id,
            conversation_id=event.conversation_id,
            turn_id=event.turn_id,
            step_id=event.step_id,
            sequence=batch.expected_last_sequence + ordinal + 1,
            timestamp=timestamp,
            batch_id=batch.batch_id,
            batch_ordinal=ordinal,
            batch_size=len(batch.events),
            batch_digest=digest,
            kind=event.kind,
            sender=event.sender,
            recipient=event.recipient,
            payload=event.payload,
        )
        for ordinal, event in enumerate(batch.events)
    )
    try:
        return project_collaboration_history(history + stored)
    except (AssertionError, KeyError, SessionIdError, TypeError, ValueError) as exc:
        raise CollaborationAppendError(
            CollaborationAppendFailure.INVALID_TRANSITION
        ) from exc
