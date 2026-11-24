"""Canonical JSON Lines for schema-v2 filesystem collaboration history."""

from __future__ import annotations

from ..conversations import (
    CollaborationEvent as CollaborationEventV2,
    decode_collaboration_event,
    encode_collaboration_event,
)
from ..errors import SessionIdError, WorkspaceFormatError


def decode_collaboration_history(
    payload: bytes,
    *,
    session_id: str,
    conversation_id: str,
    logical_artifact: str,
) -> tuple[CollaborationEventV2, ...]:
    """Decode exact canonical event-plus-LF records without accepting v1."""

    if not payload:
        raise WorkspaceFormatError(
            f"collaboration history is empty: {logical_artifact}"
        )
    if not payload.endswith(b"\n"):
        raise WorkspaceFormatError(
            f"collaboration history lacks a final newline: {logical_artifact}"
        )
    records = payload[:-1].split(b"\n")
    events: list[CollaborationEventV2] = []
    for line_number, record in enumerate(records, 1):
        if not record:
            raise WorkspaceFormatError(
                "collaboration history contains a blank record: "
                f"{logical_artifact} at line {line_number}"
            )
        try:
            event = decode_collaboration_event(record)
        except (
            AssertionError,
            KeyError,
            SessionIdError,
            TypeError,
            ValueError,
        ) as exc:
            raise WorkspaceFormatError(
                "collaboration history contains an invalid canonical event: "
                f"{logical_artifact} at line {line_number}"
            ) from exc
        if encode_collaboration_event(event) != record:
            raise WorkspaceFormatError(
                "collaboration history contains a noncanonical event: "
                f"{logical_artifact} at line {line_number}"
            )
        if (
            event.session_id != session_id
            or event.conversation_id != conversation_id
        ):
            raise WorkspaceFormatError(
                "collaboration event does not match its owning path: "
                f"{logical_artifact} at line {line_number}"
            )
        events.append(event)
    return tuple(events)



def encode_collaboration_history(
    events: tuple[CollaborationEventV2, ...],
) -> bytes:
    """Return canonical event bytes followed by one LF per event."""
    return b"".join(
        encode_collaboration_event(event) + b"\n" for event in events
    )
