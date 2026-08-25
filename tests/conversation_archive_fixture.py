"""Deterministic fixtures for canonical conversation archive tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from story_writing_agents import (
    ConversationEventKind,
    ConversationStatus,
    Workspace,
)
from story_writing_agents.conversation_storage import (
    ConversationArchive,
    ConversationArchiveConversation,
    ConversationArchiveEvent,
    ConversationArchiveSession,
)


CREATED_AT = "2026-01-01T00:00:00+00:00"
SECOND_AT = "2026-01-01T00:00:01+00:00"
UPDATED_AT = "2026-01-01T00:00:02+00:00"


def archive_event(
    *,
    session_id: str = "session_a",
    conversation_id: str = "conversation_a",
    turn_id: str = "turn_a",
    sequence: int = 1,
    timestamp: str = CREATED_AT,
    sender: str = "user",
    recipient: str = "lead_orchestrator",
    kind: ConversationEventKind = ConversationEventKind.USER_MESSAGE,
    content: str = "question",
) -> ConversationArchiveEvent:
    """Return one valid exact archive event with overridable fields."""

    return ConversationArchiveEvent(
        schema_version=1,
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        sequence=sequence,
        timestamp=timestamp,
        sender=sender,
        recipient=recipient,
        kind=kind,
        content=content,
    )


def archive_events(
    *,
    session_id: str = "session_a",
    conversation_id: str = "conversation_a",
    outcome_kind: ConversationEventKind = ConversationEventKind.AGENT_REPLY,
    user_content: str = "question",
    outcome_content: str = "answer",
) -> tuple[ConversationArchiveEvent, ConversationArchiveEvent]:
    """Return one complete user/outcome turn."""

    user = archive_event(
        session_id=session_id,
        conversation_id=conversation_id,
        content=user_content,
    )
    if outcome_kind is ConversationEventKind.AGENT_REPLY:
        sender = "lead_orchestrator"
    else:
        sender = "runtime"
    outcome = archive_event(
        session_id=session_id,
        conversation_id=conversation_id,
        sequence=2,
        timestamp=SECOND_AT,
        sender=sender,
        recipient="user",
        kind=outcome_kind,
        content=outcome_content,
    )
    return user, outcome


def archive_conversation(
    *,
    session_id: str = "session_a",
    conversation_id: str = "conversation_a",
    subject: str = "Subject",
    subject_revision: int = 1,
    status: ConversationStatus = ConversationStatus.ACTIVE,
    created_at: str = CREATED_AT,
    updated_at: str = UPDATED_AT,
    events: tuple[ConversationArchiveEvent, ...] | None = None,
) -> ConversationArchiveConversation:
    """Return one valid exact conversation, empty unless events are supplied."""

    return ConversationArchiveConversation(
        schema_version=1,
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
    events: tuple[ConversationArchiveEvent, ...] | None = None,
) -> ConversationArchive:
    """Return one exact archive with one session and conversation."""

    conversation = archive_conversation(
        session_id=session_id,
        conversation_id=conversation_id,
        subject=subject,
        subject_revision=subject_revision,
        status=status,
        events=events,
    )
    return ConversationArchive(
        schema_version=1,
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
    """Group supplied conversations into one canonical archive."""

    grouped: dict[str, list[ConversationArchiveConversation]] = {}
    for conversation in conversations:
        grouped.setdefault(conversation.session_id, []).append(conversation)
    return ConversationArchive(
        schema_version=1,
        sessions=tuple(
            ConversationArchiveSession(
                session_id=session_id,
                conversations=tuple(grouped[session_id]),
            )
            for session_id in grouped
        ),
    )


def nonempty_archive() -> ConversationArchive:
    """Return the stable Unicode/runtime-error golden archive."""

    events = archive_events(
        outcome_kind=ConversationEventKind.RUNTIME_ERROR,
        user_content="질문\x00끝",
        outcome_content="실패",
    )
    return one_conversation_archive(
        subject="안녕 🌙",
        status=ConversationStatus.ARCHIVED,
        events=events,
    )


def initialized_workspace(
    test_case: unittest.TestCase,
) -> Workspace:
    """Return a temporary initialized workspace owned by one test case."""

    temporary = tempfile.TemporaryDirectory()
    test_case.addCleanup(temporary.cleanup)
    return Workspace(temporary.name).initialize()


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


def transcript_relative(session_id: str, conversation_id: str) -> Path:
    """Return the established filesystem transcript location."""

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
