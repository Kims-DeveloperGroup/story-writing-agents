"""Immutable conversation records and their payload validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...agents import ROSTER
from ..event_value_validation import (
    raise_content_free_value_error as _raise_content_free_value_error,
    validate_identifier as _validate_identifier,
    validate_positive_integer as _validate_positive_integer,
    validate_utc_timestamp as _validate_utc_timestamp,
)
from ..event_vocabulary import (
    CONVERSATION_EVENT_SCHEMA_VERSION,
    ConversationEventKind,
)


@dataclass(frozen=True, slots=True)
class ConversationEventAppend:
    """Caller-supplied visible event before storage assigns ordering metadata."""

    session_id: str
    conversation_id: str
    turn_id: str
    sender: str
    recipient: str
    kind: ConversationEventKind
    content: str

    def __post_init__(self) -> None:
        _validate_identifier(self.session_id, label="session ID")
        _validate_identifier(self.conversation_id, label="conversation ID")
        _validate_identifier(self.turn_id, label="turn ID")
        _validate_event_payload(
            sender=self.sender,
            recipient=self.recipient,
            kind=self.kind,
            content=self.content,
        )


@dataclass(frozen=True, slots=True)
class ConversationEvent:
    """One storage-ordered visible event in a conversation transcript."""

    schema_version: int
    session_id: str
    conversation_id: str
    turn_id: str
    sequence: int
    timestamp: str
    sender: str
    recipient: str
    kind: ConversationEventKind
    content: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != CONVERSATION_EVENT_SCHEMA_VERSION
        ):
            raise ValueError("unsupported conversation event schema version")
        _validate_identifier(self.session_id, label="session ID")
        _validate_identifier(self.conversation_id, label="conversation ID")
        _validate_identifier(self.turn_id, label="turn ID")
        _validate_positive_integer(self.sequence, label="event sequence")
        _validate_utc_timestamp(self.timestamp, label="timestamp")
        _validate_event_payload(
            sender=self.sender,
            recipient=self.recipient,
            kind=self.kind,
            content=self.content,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the exact JSON-compatible authoritative event schema."""

        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "sender": self.sender,
            "recipient": self.recipient,
            "kind": self.kind.value,
            "content": self.content,
        }


def _validate_event_payload(
    *,
    sender: str,
    recipient: str,
    kind: ConversationEventKind,
    content: str,
) -> None:
    """Validate one visible event's kind, actor direction, and content.

    Content must be nonempty strict UTF-8 and actors must match the event kind.
    The helper performs no mutation and raises ``TypeError`` or ``ValueError``.
    """

    if not isinstance(kind, ConversationEventKind):
        raise TypeError("conversation event kind must be a ConversationEventKind")
    if not isinstance(sender, str):
        raise TypeError("conversation event sender must be a string")
    if not isinstance(recipient, str):
        raise TypeError("conversation event recipient must be a string")
    if kind is ConversationEventKind.USER_MESSAGE:
        actors_are_valid = sender == "user" and recipient in ROSTER
    elif kind is ConversationEventKind.AGENT_REPLY:
        actors_are_valid = sender in ROSTER and recipient == "user"
    else:
        actors_are_valid = sender == "runtime" and recipient == "user"
    if not actors_are_valid:
        raise ValueError(f"invalid actors for conversation event kind {kind.value!r}")
    if not isinstance(content, str):
        raise TypeError("conversation event content must be a string")
    try:
        content.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        _raise_content_free_value_error(
            "conversation event content must be valid UTF-8"
        )
    if not content:
        raise ValueError("conversation event content must not be empty")
