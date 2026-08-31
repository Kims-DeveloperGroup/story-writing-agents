"""Provider-neutral visible conversation-event contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from ..agents import ROSTER
from ..errors import SessionIdError


CONVERSATION_EVENT_SCHEMA_VERSION = 1

_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def _validate_positive_integer(value: int, *, label: str) -> int:
    """Return a positive non-boolean integer without mutation.

    Other types raise ``TypeError`` and non-positive values raise ``ValueError``.
    """

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value < 1:
        raise ValueError(f"{label} must be positive")
    return value


class ConversationEventKind(StrEnum):
    """Visible event kinds stored in an authoritative conversation transcript."""

    USER_MESSAGE = "user_message"
    AGENT_REPLY = "agent_reply"
    RUNTIME_ERROR = "runtime_error"


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


def _validate_identifier(value: str, *, label: str) -> str:
    """Return a safe identifier or raise ``SessionIdError`` without mutation."""

    if not isinstance(value, str) or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise SessionIdError(
            f"{label} must match [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}"
        )
    return value


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
        raise ValueError(
            f"invalid actors for conversation event kind {kind.value!r}"
        )
    if not isinstance(content, str):
        raise TypeError("conversation event content must be a string")
    try:
        content.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("conversation event content must be valid UTF-8") from exc
    if not content:
        raise ValueError("conversation event content must not be empty")


def _validate_utc_timestamp(value: str, *, label: str) -> datetime:
    """Parse an exact canonical ISO-8601 UTC timestamp without mutation.

    Wrong types raise ``TypeError``; invalid, offset-wrong, or noncanonical
    values raise ``ValueError``.
    """

    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError(f"{label} must use UTC")
    if parsed.isoformat() != value:
        raise ValueError(f"{label} must use canonical ISO-8601 UTC format")
    return parsed


__all__ = [
    "CONVERSATION_EVENT_SCHEMA_VERSION",
    "ConversationEvent",
    "ConversationEventAppend",
    "ConversationEventKind",
]
