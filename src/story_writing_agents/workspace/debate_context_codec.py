"""Strict version-1 debate context sidecar payload and snapshot validation."""

from __future__ import annotations

from typing import Any, Protocol

from ..conversations import (
    ConversationContextWindow,
    ConversationEvent,
    ConversationEventKind,
    validate_conversation_context_window,
)
from ..errors import SessionIdError, WorkspaceFormatError
from ..models import DebateContextMode


class _IdentifierValidator(Protocol):
    def __call__(self, value: str, *, label: str = "session ID") -> str: ...


_DEBATE_CONTEXT_EVENT_FIELDS = frozenset(
    {
        "schema_version",
        "session_id",
        "conversation_id",
        "turn_id",
        "sequence",
        "timestamp",
        "sender",
        "recipient",
        "kind",
        "content",
    }
)



_DEBATE_CONTEXT_FIELDS = frozenset(
    {
        "schema_version",
        "session_id",
        "debate_id",
        "conversation_id",
        "mode",
        "snapshot",
    }
)



_DEBATE_CONTEXT_SNAPSHOT_FIELDS = frozenset(
    {
        "events",
        "total_complete_turns",
        "retained_turns",
        "omitted_turns",
        "retained_utf8_bytes",
        "truncated",
    }
)



def serialize_debate_context_snapshot(
    snapshot: ConversationContextWindow,
) -> dict[str, Any]:
    """Return one canonical snapshot's exact version-1 JSON fields."""

    return {
        "events": [event.to_dict() for event in snapshot.events],
        "total_complete_turns": snapshot.total_complete_turns,
        "retained_turns": snapshot.retained_turns,
        "omitted_turns": snapshot.omitted_turns,
        "retained_utf8_bytes": snapshot.retained_utf8_bytes,
        "truncated": snapshot.truncated,
    }



def validate_debate_context_sidecar(
    value: Any,
    *,
    session_id: str,
    debate_id: str,
    validate_identifier: _IdentifierValidator,
) -> dict[str, Any]:
    """Return a strict identity-bound sidecar or raise without mutation."""

    if not isinstance(value, dict) or set(value) != _DEBATE_CONTEXT_FIELDS:
        raise WorkspaceFormatError("debate context sidecar has an invalid schema")
    if (
        isinstance(value["schema_version"], bool)
        or not isinstance(value["schema_version"], int)
        or value["schema_version"] != 1
    ):
        raise WorkspaceFormatError(
            "unsupported debate context sidecar schema version"
        )
    try:
        stored_session = validate_identifier(value["session_id"])
        stored_debate = validate_identifier(
            value["debate_id"],
            label="debate ID",
        )
        conversation = validate_identifier(
            value["conversation_id"],
            label="conversation ID",
        )
    except (SessionIdError, TypeError, ValueError) as exc:
        raise WorkspaceFormatError(
            "debate context sidecar has an invalid identity"
        ) from exc
    if stored_session != session_id or stored_debate != debate_id:
        raise WorkspaceFormatError(
            "debate context sidecar identity does not match its path"
        )
    raw_mode = value["mode"]
    if not isinstance(raw_mode, str):
        raise WorkspaceFormatError("debate context sidecar mode is invalid")
    try:
        mode = DebateContextMode(raw_mode)
    except ValueError as exc:
        raise WorkspaceFormatError(
            "debate context sidecar mode is invalid"
        ) from exc

    raw_snapshot = value["snapshot"]
    if mode is DebateContextMode.FRESH:
        if raw_snapshot is not None:
            raise WorkspaceFormatError(
                "fresh debate context sidecar snapshot must be null"
            )
        serialized_snapshot = None
    else:
        canonical_snapshot = _parse_debate_context_snapshot(
            raw_snapshot,
            session_id=session_id,
            conversation_id=conversation,
        )
        serialized_snapshot = serialize_debate_context_snapshot(
            canonical_snapshot
        )
    return {
        "schema_version": 1,
        "session_id": session_id,
        "debate_id": debate_id,
        "conversation_id": conversation,
        "mode": mode.value,
        "snapshot": serialized_snapshot,
    }



def _parse_debate_context_snapshot(
    value: Any,
    *,
    session_id: str,
    conversation_id: str,
) -> ConversationContextWindow:
    """Parse and canonicalize strict snapshot fields without filesystem I/O."""

    if not isinstance(value, dict) or set(value) != _DEBATE_CONTEXT_SNAPSHOT_FIELDS:
        raise WorkspaceFormatError(
            "included debate context snapshot has an invalid schema"
        )
    raw_events = value["events"]
    if not isinstance(raw_events, list):
        raise WorkspaceFormatError(
            "included debate context snapshot events must be a list"
        )
    events: list[ConversationEvent] = []
    for raw_event in raw_events:
        if (
            not isinstance(raw_event, dict)
            or set(raw_event) != _DEBATE_CONTEXT_EVENT_FIELDS
        ):
            raise WorkspaceFormatError(
                "included debate context event has an invalid schema"
            )
        try:
            raw_kind = raw_event["kind"]
            if not isinstance(raw_kind, str):
                raise TypeError("event kind must be a string")
            events.append(
                ConversationEvent(
                    schema_version=raw_event["schema_version"],
                    session_id=raw_event["session_id"],
                    conversation_id=raw_event["conversation_id"],
                    turn_id=raw_event["turn_id"],
                    sequence=raw_event["sequence"],
                    timestamp=raw_event["timestamp"],
                    sender=raw_event["sender"],
                    recipient=raw_event["recipient"],
                    kind=ConversationEventKind(raw_kind),
                    content=raw_event["content"],
                )
            )
        except (SessionIdError, TypeError, ValueError) as exc:
            raise WorkspaceFormatError(
                "included debate context event has an invalid value"
            ) from exc
    try:
        snapshot = ConversationContextWindow(
            events=tuple(events),
            total_complete_turns=value["total_complete_turns"],
            retained_turns=value["retained_turns"],
            omitted_turns=value["omitted_turns"],
            retained_utf8_bytes=value["retained_utf8_bytes"],
            truncated=value["truncated"],
        )
        return validate_conversation_context_window(
            snapshot,
            session_id=session_id,
            conversation_id=conversation_id,
        )
    except (SessionIdError, TypeError, ValueError) as exc:
        raise WorkspaceFormatError(
            "included debate context snapshot has an invalid value"
        ) from exc



def reject_duplicate_json_fields(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    """Build one JSON object while rejecting every duplicate field name."""

    result: dict[str, Any] = {}
    for key, field_value in pairs:
        if key in result:
            raise ValueError(f"duplicate field: {key}")
        result[key] = field_value
    return result
