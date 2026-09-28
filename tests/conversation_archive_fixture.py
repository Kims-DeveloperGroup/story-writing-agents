"""Deterministic fixtures for canonical schema-v2 conversation archives."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from story_writing_agents import ConversationStatus, Workspace
from story_writing_agents.conversations import (
    CONVERSATION_METADATA_SCHEMA_VERSION,
    CollaborationEvent,
    encode_collaboration_event,
)
from story_writing_agents.conversation_storage import (
    CONVERSATION_ARCHIVE_SCHEMA_VERSION,
    ConversationArchive,
    ConversationArchiveConversation,
    ConversationArchiveSession,
)

from tests.collaboration_contract_fixture import contract_batches, store_batch
from tests.helpers import pristine_initialized_workspace


CREATED_AT = "2026-01-01T00:00:00+00:00"
SECOND_AT = "2026-01-01T00:00:01+00:00"
UPDATED_AT = "2026-01-01T00:00:02+00:00"

# Frozen legacy bytes exist only to prove that schema-v1 archives are rejected.
LEGACY_SCHEMA_ONE_EMPTY_ARCHIVE = (
    b'{"schema_version":1,"sessions":[],"digest":{"algorithm":"sha256",'
    b'"value":"7712f58e5e3ce7e4aaf8d09f06c4a60771fee9d848148a84b25ed1589e3791c3"}}\n'
)


def collaboration_history(
    conversation_id: str = "conversation_a",
    *,
    terminal: str = "completed",
) -> tuple[CollaborationEvent, ...]:
    """Return one deterministic history built from the shared atomic families."""

    if terminal not in {"completed", "unknown"}:
        raise ValueError("terminal must be completed or unknown")
    batches = contract_batches(conversation_id)
    names = (
        (
            "proposal",
            "confirmation",
            "running",
            "calls",
            "result",
            "completion",
        )
        if terminal == "completed"
        else ("proposal", "confirmation", "running", "calls", "unknown")
    )
    history: tuple[CollaborationEvent, ...] = ()
    for name in names:
        history = store_batch(history, batches[name])
    return history


def event_images(archive: ConversationArchive) -> tuple[bytes, ...]:
    """Return every canonical event image in archive order."""

    return tuple(
        encode_collaboration_event(event)
        for session in archive.sessions
        for conversation in session.conversations
        for event in conversation.events
    )


def archive_conversation(
    *,
    session_id: str = "session_a",
    conversation_id: str = "conversation_a",
    subject: str = "Subject",
    subject_revision: int = 1,
    status: ConversationStatus = ConversationStatus.ACTIVE,
    created_at: str = CREATED_AT,
    updated_at: str = UPDATED_AT,
    events: tuple[CollaborationEvent, ...] | None = None,
) -> ConversationArchiveConversation:
    """Return one metadata-schema-v1 record with optional v2 history."""

    return ConversationArchiveConversation(
        schema_version=CONVERSATION_METADATA_SCHEMA_VERSION,
        session_id=session_id,
        conversation_id=conversation_id,
        subject=subject,
        subject_revision=subject_revision,
        status=status,
        created_at=created_at,
        updated_at=updated_at,
        events=() if events is None else events,
    )


def one_conversation_archive(
    *,
    session_id: str = "session_a",
    conversation_id: str = "conversation_a",
    subject: str = "Subject",
    subject_revision: int = 1,
    status: ConversationStatus = ConversationStatus.ACTIVE,
    events: tuple[CollaborationEvent, ...] | None = None,
) -> ConversationArchive:
    """Return one exact schema-v2 archive with one catalog record."""

    conversation = archive_conversation(
        session_id=session_id,
        conversation_id=conversation_id,
        subject=subject,
        subject_revision=subject_revision,
        status=status,
        events=events,
    )
    return ConversationArchive(
        schema_version=CONVERSATION_ARCHIVE_SCHEMA_VERSION,
        sessions=(
            ConversationArchiveSession(
                session_id=session_id,
                conversations=(conversation,),
            ),
        ),
    )


def archive_from_conversations(
    *conversations: ConversationArchiveConversation,
) -> ConversationArchive:
    """Group supplied catalog records into one canonical schema-v2 archive."""

    grouped: dict[str, list[ConversationArchiveConversation]] = {}
    for conversation in conversations:
        grouped.setdefault(conversation.session_id, []).append(conversation)
    return ConversationArchive(
        schema_version=CONVERSATION_ARCHIVE_SCHEMA_VERSION,
        sessions=tuple(
            ConversationArchiveSession(
                session_id=session_id,
                conversations=tuple(grouped[session_id]),
            )
            for session_id in grouped
        ),
    )


def nonempty_archive() -> ConversationArchive:
    """Return the stable Unicode archive with a complete collaboration history."""

    return one_conversation_archive(
        subject="안녕 🌙",
        status=ConversationStatus.ARCHIVED,
        events=collaboration_history(),
    )


def representative_archive() -> ConversationArchive:
    """Cover empty, completed, and outcome-unknown catalog histories."""

    return archive_from_conversations(
        archive_conversation(
            session_id="session_b",
            conversation_id="conversation_empty",
            subject="Empty active history",
            subject_revision=3,
        ),
        archive_conversation(
            session_id="session_a",
            conversation_id="conversation_completed",
            subject="Unicode 대화 🌙",
            subject_revision=4,
            events=collaboration_history("conversation_completed"),
        ),
        archive_conversation(
            session_id="session_a",
            conversation_id="conversation_unknown",
            subject="Archived outcome unknown",
            subject_revision=2,
            status=ConversationStatus.ARCHIVED,
            events=collaboration_history("conversation_unknown", terminal="unknown"),
        ),
    )


def initialized_workspace(test_case: unittest.TestCase) -> Workspace:
    """Return an independent already-ready workspace owned by one test case."""

    return pristine_initialized_workspace(test_case)


def workspace_snapshot(root: Path) -> dict[str, bytes | None]:
    """Return one deterministic regular directory/file namespace snapshot."""

    return {
        path.relative_to(root).as_posix(): (
            None if path.is_dir() else path.read_bytes()
        )
        for path in sorted(root.rglob("*"))
    }


def metadata_relative(session_id: str, conversation_id: str) -> Path:
    """Return the established filesystem metadata location."""

    return (
        Path("discussion_logs")
        / session_id
        / "interactive"
        / "conversations"
        / conversation_id
        / "metadata.json"
    )


def history_relative(session_id: str, conversation_id: str) -> Path:
    """Return the canonical schema-v2 collaboration-history location."""

    return metadata_relative(session_id, conversation_id).with_name(
        "collaboration-history.jsonl"
    )


def transcript_relative(session_id: str, conversation_id: str) -> Path:
    """Return the legacy schema-v1 transcript location used by rejection tests."""

    return metadata_relative(session_id, conversation_id).with_name("transcript.jsonl")


def compact_json(value: object, *, ensure_ascii: bool = False) -> bytes:
    """Encode a JSON value compactly with the archive's final LF."""

    return (
        json.dumps(
            value,
            ensure_ascii=ensure_ascii,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
