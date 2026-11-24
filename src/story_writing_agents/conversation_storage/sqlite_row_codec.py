"""Typed SQLite catalog metadata and schema-v2 collaboration rows."""

from __future__ import annotations

from ..conversations import (
    CollaborationEvent as CollaborationEventV2,
    ConversationMetadata,
    ConversationStatus,
    decode_collaboration_event,
    encode_collaboration_event,
    normalize_conversation_subject,
)
from ..errors import SessionIdError
from .sqlite_database import SQLiteDatabaseFormatError


def decode_conversation_metadata_row(row: object) -> ConversationMetadata:
    """Construct domain metadata and verify its private normalized subject."""

    if type(row) is not tuple or len(row) != 9:
        raise SQLiteDatabaseFormatError()
    normalized_subject = row[4]
    status = row[6]
    if type(normalized_subject) is not str or type(status) is not str:
        raise SQLiteDatabaseFormatError()
    try:
        metadata = ConversationMetadata(
            schema_version=row[0],
            session_id=row[1],
            conversation_id=row[2],
            subject=row[3],
            subject_revision=row[5],
            status=ConversationStatus(status),
            created_at=row[7],
            updated_at=row[8],
        )
        expected_normalized_subject = normalize_conversation_subject(metadata.subject)
    except (SessionIdError, TypeError, ValueError):
        raise SQLiteDatabaseFormatError() from None
    if normalized_subject != expected_normalized_subject:
        raise SQLiteDatabaseFormatError()
    return metadata


def decode_collaboration_event_row(row: object) -> CollaborationEventV2:
    """Decode one canonical v2 BLOB and verify every redundant SQL column."""

    if type(row) is not tuple or len(row) != 13 or type(row[12]) is not bytes:
        raise SQLiteDatabaseFormatError()
    try:
        event = decode_collaboration_event(row[12])
        canonical = encode_collaboration_event(event)
    except (AssertionError, KeyError, SessionIdError, TypeError, ValueError):
        raise SQLiteDatabaseFormatError() from None
    expected = (
        event.schema_version,
        event.event_id,
        event.session_id,
        event.conversation_id,
        event.turn_id,
        event.step_id,
        event.sequence,
        event.timestamp,
        event.batch_id,
        event.batch_ordinal,
        event.batch_size,
        event.batch_digest,
        canonical,
    )
    if row != expected:
        raise SQLiteDatabaseFormatError()
    return event
