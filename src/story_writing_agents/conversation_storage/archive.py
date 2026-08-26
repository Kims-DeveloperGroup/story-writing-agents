"""Canonical, lossless conversation archive values and wire format."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any

from ..conversations import (
    ConversationEvent,
    ConversationEventKind,
    ConversationMetadata,
    ConversationStatus,
    select_bounded_conversation_context,
)
from ..errors import SessionIdError, StoryWritingAgentsError


CONVERSATION_ARCHIVE_SCHEMA_VERSION = 1
CONVERSATION_ARCHIVE_MAX_RAW_BYTES = 67_108_864
CONVERSATION_ARCHIVE_MAX_SESSIONS = 1_024
CONVERSATION_ARCHIVE_MAX_CONVERSATIONS = 65_536
CONVERSATION_ARCHIVE_MAX_EVENTS = 1_000_000
CONVERSATION_ARCHIVE_MAX_AGGREGATE_UTF8_BYTES = 33_554_432

_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
_TOP_LEVEL_FIELDS = frozenset({"schema_version", "sessions", "digest"})
_SESSION_FIELDS = frozenset({"session_id", "conversations"})
_CONVERSATION_FIELDS = frozenset(
    {
        "schema_version",
        "session_id",
        "conversation_id",
        "subject",
        "subject_revision",
        "status",
        "created_at",
        "updated_at",
        "events",
    }
)
_EVENT_FIELDS = frozenset(
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
_DIGEST_FIELDS = frozenset({"algorithm", "value"})


class ConversationArchiveError(StoryWritingAgentsError):
    """Base class for content-free canonical archive failures."""

    def __init__(self) -> None:
        super().__init__("conversation archive error")


class ConversationArchiveFormatError(ConversationArchiveError):
    """The logical value or canonical wire image is malformed."""

    def __init__(self) -> None:
        StoryWritingAgentsError.__init__(
            self,
            "conversation archive format is invalid",
        )


class ConversationArchiveVersionError(ConversationArchiveError):
    """The archive schema version is unsupported."""

    def __init__(self) -> None:
        StoryWritingAgentsError.__init__(
            self,
            "conversation archive version is unsupported",
        )


class ConversationArchiveBoundsError(ConversationArchiveError):
    """The archive exceeds a fixed count or byte bound."""

    def __init__(self) -> None:
        StoryWritingAgentsError.__init__(
            self,
            "conversation archive bounds exceeded",
        )


class ConversationArchiveDigestError(ConversationArchiveError):
    """The archive digest is malformed or does not match its content."""

    def __init__(self) -> None:
        StoryWritingAgentsError.__init__(
            self,
            "conversation archive digest is invalid",
        )


class ConversationArchiveSourceError(ConversationArchiveError):
    """The authoritative archive source cannot be exported safely."""

    def __init__(self) -> None:
        StoryWritingAgentsError.__init__(
            self,
            "conversation archive source is invalid",
        )


class ConversationArchiveSourceChangedError(ConversationArchiveSourceError):
    """The authoritative source changed during a stable-source export."""

    def __init__(self) -> None:
        StoryWritingAgentsError.__init__(
            self,
            "conversation archive source changed",
        )


class ConversationArchiveDestinationError(ConversationArchiveError):
    """The filesystem archive destination cannot be imported safely."""

    def __init__(self) -> None:
        StoryWritingAgentsError.__init__(
            self,
            "conversation archive destination is invalid",
        )


class ConversationArchiveConflictError(ConversationArchiveDestinationError):
    """An existing destination identity differs from the archive."""

    def __init__(self) -> None:
        StoryWritingAgentsError.__init__(
            self,
            "conversation archive destination conflicts",
        )


class ConversationArchiveDestinationChangedError(ConversationArchiveDestinationError):
    """The destination changed after complete preflight classification."""

    def __init__(self) -> None:
        StoryWritingAgentsError.__init__(
            self,
            "conversation archive destination changed",
        )


def _validate_schema_version(value: object) -> None:
    if type(value) is not int:
        raise ConversationArchiveFormatError()
    if value != CONVERSATION_ARCHIVE_SCHEMA_VERSION:
        raise ConversationArchiveVersionError()


def _utf8_size(value: object) -> int:
    if type(value) is not str:
        raise ConversationArchiveFormatError()
    try:
        return len(value.encode("utf-8", errors="strict"))
    except UnicodeEncodeError:
        raise ConversationArchiveFormatError() from None


class _Utf8Budget:
    """Consume one fixed aggregate text budget without traversing past overflow."""

    __slots__ = ("_remaining",)

    def __init__(self) -> None:
        self._remaining = CONVERSATION_ARCHIVE_MAX_AGGREGATE_UTF8_BYTES

    def consume(self, value: object) -> None:
        size = _utf8_size(value)
        if size > self._remaining:
            raise ConversationArchiveBoundsError()
        self._remaining -= size


def _require_events_tuple(
    value: object,
) -> tuple[ConversationArchiveEvent, ...]:
    if type(value) is not tuple:
        raise ConversationArchiveFormatError()
    if len(value) > CONVERSATION_ARCHIVE_MAX_EVENTS:
        raise ConversationArchiveBoundsError()
    if any(type(event) is not ConversationArchiveEvent for event in value):
        raise ConversationArchiveFormatError()
    return value


def _require_conversations_tuple(
    value: object,
) -> tuple[ConversationArchiveConversation, ...]:
    if type(value) is not tuple:
        raise ConversationArchiveFormatError()
    if not value:
        raise ConversationArchiveFormatError()
    if len(value) > CONVERSATION_ARCHIVE_MAX_CONVERSATIONS:
        raise ConversationArchiveBoundsError()
    if any(
        type(conversation) is not ConversationArchiveConversation
        for conversation in value
    ):
        raise ConversationArchiveFormatError()
    return value


def _require_sessions_tuple(
    value: object,
) -> tuple[ConversationArchiveSession, ...]:
    if type(value) is not tuple:
        raise ConversationArchiveFormatError()
    if len(value) > CONVERSATION_ARCHIVE_MAX_SESSIONS:
        raise ConversationArchiveBoundsError()
    if any(type(session) is not ConversationArchiveSession for session in value):
        raise ConversationArchiveFormatError()
    return value


def _checked_count(current: int, added: int, maximum: int) -> int:
    total = current + added
    if total > maximum:
        raise ConversationArchiveBoundsError()
    return total


def _preflight_conversation_shape(
    conversation: ConversationArchiveConversation,
) -> tuple[ConversationArchiveEvent, ...]:
    try:
        return _require_events_tuple(conversation.events)
    except AttributeError:
        raise ConversationArchiveFormatError() from None


def _preflight_session_shape(
    session: ConversationArchiveSession,
) -> tuple[ConversationArchiveConversation, ...]:
    try:
        conversations = _require_conversations_tuple(session.conversations)
    except AttributeError:
        raise ConversationArchiveFormatError() from None
    total_events = 0
    for conversation in conversations:
        events = _preflight_conversation_shape(conversation)
        total_events = _checked_count(
            total_events,
            len(events),
            CONVERSATION_ARCHIVE_MAX_EVENTS,
        )
    return conversations


def _preflight_archive_shape(
    archive: ConversationArchive,
) -> tuple[ConversationArchiveSession, ...]:
    try:
        sessions = _require_sessions_tuple(archive.sessions)
    except AttributeError:
        raise ConversationArchiveFormatError() from None

    conversations_by_session: list[tuple[ConversationArchiveConversation, ...]] = []
    total_conversations = 0
    for session in sessions:
        try:
            conversations = _require_conversations_tuple(session.conversations)
        except AttributeError:
            raise ConversationArchiveFormatError() from None
        total_conversations = _checked_count(
            total_conversations,
            len(conversations),
            CONVERSATION_ARCHIVE_MAX_CONVERSATIONS,
        )
        conversations_by_session.append(conversations)

    total_events = 0
    for conversations in conversations_by_session:
        for conversation in conversations:
            events = _preflight_conversation_shape(conversation)
            total_events = _checked_count(
                total_events,
                len(events),
                CONVERSATION_ARCHIVE_MAX_EVENTS,
            )
    return sessions


def _consume_conversations(
    conversations: tuple[ConversationArchiveConversation, ...],
) -> None:
    budget = _Utf8Budget()
    for conversation in conversations:
        budget.consume(conversation.subject)
        for event in conversation.events:
            budget.consume(event.content)


def _consume_sessions(
    sessions: tuple[ConversationArchiveSession, ...],
) -> None:
    budget = _Utf8Budget()
    for session in sessions:
        for conversation in session.conversations:
            budget.consume(conversation.subject)
            for event in conversation.events:
                budget.consume(event.content)


@dataclass(frozen=True, slots=True)
class ConversationArchiveEvent:
    """One exact authoritative conversation event in an archive."""

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
        _validate_schema_version(self.schema_version)
        if type(self.kind) is not ConversationEventKind:
            raise ConversationArchiveFormatError()
        try:
            ConversationEvent(
                schema_version=self.schema_version,
                session_id=self.session_id,
                conversation_id=self.conversation_id,
                turn_id=self.turn_id,
                sequence=self.sequence,
                timestamp=self.timestamp,
                sender=self.sender,
                recipient=self.recipient,
                kind=self.kind,
                content=self.content,
            )
        except (SessionIdError, TypeError, ValueError):
            raise ConversationArchiveFormatError() from None
        if _utf8_size(self.content) > CONVERSATION_ARCHIVE_MAX_AGGREGATE_UTF8_BYTES:
            raise ConversationArchiveBoundsError()


@dataclass(frozen=True, slots=True)
class ConversationArchiveConversation:
    """One exact metadata record and its complete authoritative transcript."""

    schema_version: int
    session_id: str
    conversation_id: str
    subject: str
    subject_revision: int
    status: ConversationStatus
    created_at: str
    updated_at: str
    events: tuple[ConversationArchiveEvent, ...]

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        events = _require_events_tuple(self.events)
        if type(self.status) is not ConversationStatus:
            raise ConversationArchiveFormatError()
        try:
            ConversationMetadata(
                schema_version=self.schema_version,
                session_id=self.session_id,
                conversation_id=self.conversation_id,
                subject=self.subject,
                subject_revision=self.subject_revision,
                status=self.status,
                created_at=self.created_at,
                updated_at=self.updated_at,
            )
        except (SessionIdError, TypeError, ValueError):
            raise ConversationArchiveFormatError() from None

        aggregate = _Utf8Budget()
        aggregate.consume(self.subject)
        for event in events:
            aggregate.consume(event.content)

        canonical_events = tuple(sorted(events, key=lambda event: event.sequence))
        if any(
            event.session_id != self.session_id
            or event.conversation_id != self.conversation_id
            for event in canonical_events
        ):
            raise ConversationArchiveFormatError()
        domain_events = tuple(
            ConversationEvent(
                schema_version=event.schema_version,
                session_id=event.session_id,
                conversation_id=event.conversation_id,
                turn_id=event.turn_id,
                sequence=event.sequence,
                timestamp=event.timestamp,
                sender=event.sender,
                recipient=event.recipient,
                kind=event.kind,
                content=event.content,
            )
            for event in canonical_events
        )
        try:
            select_bounded_conversation_context(domain_events)
        except (SessionIdError, TypeError, ValueError):
            raise ConversationArchiveFormatError() from None
        object.__setattr__(self, "events", canonical_events)


@dataclass(frozen=True, slots=True)
class ConversationArchiveSession:
    """One nonempty session grouping of canonical conversations."""

    session_id: str
    conversations: tuple[ConversationArchiveConversation, ...]

    def __post_init__(self) -> None:
        conversations = _preflight_session_shape(self)
        if type(self.session_id) is not str:
            raise ConversationArchiveFormatError()
        _consume_conversations(conversations)
        canonical_conversations = tuple(
            sorted(
                conversations,
                key=lambda conversation: (
                    conversation.created_at,
                    conversation.conversation_id,
                ),
            )
        )
        if any(
            conversation.session_id != self.session_id
            for conversation in canonical_conversations
        ):
            raise ConversationArchiveFormatError()
        identities = {
            conversation.conversation_id for conversation in canonical_conversations
        }
        if len(identities) != len(canonical_conversations):
            raise ConversationArchiveFormatError()
        object.__setattr__(self, "conversations", canonical_conversations)


@dataclass(frozen=True, slots=True)
class ConversationArchive:
    """One canonical, immutable, digest-independent archive value."""

    schema_version: int
    sessions: tuple[ConversationArchiveSession, ...]

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        sessions = _preflight_archive_shape(self)
        _consume_sessions(sessions)
        canonical_sessions = tuple(
            sorted(sessions, key=lambda session: session.session_id)
        )
        if len({session.session_id for session in canonical_sessions}) != len(
            canonical_sessions
        ):
            raise ConversationArchiveFormatError()

        object.__setattr__(self, "sessions", canonical_sessions)


def _copy_event(event: object) -> ConversationArchiveEvent:
    if type(event) is not ConversationArchiveEvent:
        raise ConversationArchiveFormatError()
    try:
        return ConversationArchiveEvent(
            schema_version=event.schema_version,
            session_id=event.session_id,
            conversation_id=event.conversation_id,
            turn_id=event.turn_id,
            sequence=event.sequence,
            timestamp=event.timestamp,
            sender=event.sender,
            recipient=event.recipient,
            kind=event.kind,
            content=event.content,
        )
    except AttributeError:
        raise ConversationArchiveFormatError() from None


def _copy_conversation(
    conversation: object,
) -> ConversationArchiveConversation:
    if type(conversation) is not ConversationArchiveConversation:
        raise ConversationArchiveFormatError()
    events = _preflight_conversation_shape(conversation)
    try:
        return ConversationArchiveConversation(
            schema_version=conversation.schema_version,
            session_id=conversation.session_id,
            conversation_id=conversation.conversation_id,
            subject=conversation.subject,
            subject_revision=conversation.subject_revision,
            status=conversation.status,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            events=tuple(_copy_event(event) for event in events),
        )
    except (AttributeError, TypeError):
        raise ConversationArchiveFormatError() from None


def _copy_session(session: object) -> ConversationArchiveSession:
    if type(session) is not ConversationArchiveSession:
        raise ConversationArchiveFormatError()
    conversations = _preflight_session_shape(session)
    try:
        return ConversationArchiveSession(
            session_id=session.session_id,
            conversations=tuple(
                _copy_conversation(conversation) for conversation in conversations
            ),
        )
    except (AttributeError, TypeError):
        raise ConversationArchiveFormatError() from None


def _copy_archive(archive: object) -> ConversationArchive:
    if type(archive) is not ConversationArchive:
        raise ConversationArchiveFormatError()
    sessions = _preflight_archive_shape(archive)
    try:
        return ConversationArchive(
            schema_version=archive.schema_version,
            sessions=tuple(_copy_session(session) for session in sessions),
        )
    except (AttributeError, TypeError):
        raise ConversationArchiveFormatError() from None


def _event_value(event: ConversationArchiveEvent) -> dict[str, object]:
    return {
        "schema_version": event.schema_version,
        "session_id": event.session_id,
        "conversation_id": event.conversation_id,
        "turn_id": event.turn_id,
        "sequence": event.sequence,
        "timestamp": event.timestamp,
        "sender": event.sender,
        "recipient": event.recipient,
        "kind": event.kind.value,
        "content": event.content,
    }


def _conversation_value(
    conversation: ConversationArchiveConversation,
) -> dict[str, object]:
    return {
        "schema_version": conversation.schema_version,
        "session_id": conversation.session_id,
        "conversation_id": conversation.conversation_id,
        "subject": conversation.subject,
        "subject_revision": conversation.subject_revision,
        "status": conversation.status.value,
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
        "events": [_event_value(event) for event in conversation.events],
    }


def _sessions_value(archive: ConversationArchive) -> list[dict[str, object]]:
    return [
        {
            "session_id": session.session_id,
            "conversations": [
                _conversation_value(conversation)
                for conversation in session.conversations
            ],
        }
        for session in archive.sessions
    ]


def _encode_json(value: object) -> bytes:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        return (text + "\n").encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise ConversationArchiveFormatError() from None


def _digest_image(archive: ConversationArchive) -> bytes:
    return _encode_json(
        {
            "schema_version": archive.schema_version,
            "sessions": _sessions_value(archive),
        }
    )


def serialize_conversation_archive(archive: ConversationArchive, /) -> bytes:
    """Return the exact canonical UTF-8 JSON version-1 archive image."""

    canonical = _copy_archive(archive)
    digest = hashlib.sha256(_digest_image(canonical)).hexdigest()
    wire = _encode_json(
        {
            "schema_version": canonical.schema_version,
            "sessions": _sessions_value(canonical),
            "digest": {"algorithm": "sha256", "value": digest},
        }
    )
    if len(wire) > CONVERSATION_ARCHIVE_MAX_RAW_BYTES:
        raise ConversationArchiveBoundsError()
    return wire


def _reject_duplicate_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate archive field")
        value[key] = item
    return value


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON constant")


def _require_object(value: object, fields: frozenset[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise ConversationArchiveFormatError()
    return value


def _require_bounded_list(
    value: object,
    *,
    maximum: int,
    nonempty: bool = False,
) -> list[Any]:
    if type(value) is not list:
        raise ConversationArchiveFormatError()
    if len(value) > maximum:
        raise ConversationArchiveBoundsError()
    if nonempty and not value:
        raise ConversationArchiveFormatError()
    return value


def _preflight_raw_archive(raw_sessions_value: object) -> list[Any]:
    raw_sessions = _require_bounded_list(
        raw_sessions_value,
        maximum=CONVERSATION_ARCHIVE_MAX_SESSIONS,
    )
    if any(type(session) is not dict for session in raw_sessions):
        raise ConversationArchiveFormatError()

    raw_conversation_groups: list[list[Any]] = []
    total_conversations = 0
    for session_value in raw_sessions:
        session = _require_object(session_value, _SESSION_FIELDS)
        raw_conversations = _require_bounded_list(
            session["conversations"],
            maximum=CONVERSATION_ARCHIVE_MAX_CONVERSATIONS,
            nonempty=True,
        )
        total_conversations = _checked_count(
            total_conversations,
            len(raw_conversations),
            CONVERSATION_ARCHIVE_MAX_CONVERSATIONS,
        )
        raw_conversation_groups.append(raw_conversations)

    conversation_records: list[dict[str, Any]] = []
    for raw_conversations in raw_conversation_groups:
        if any(type(conversation) is not dict for conversation in raw_conversations):
            raise ConversationArchiveFormatError()
        conversation_records.extend(
            _require_object(conversation, _CONVERSATION_FIELDS)
            for conversation in raw_conversations
        )

    raw_event_groups: list[list[Any]] = []
    total_events = 0
    for conversation in conversation_records:
        raw_events = _require_bounded_list(
            conversation["events"],
            maximum=CONVERSATION_ARCHIVE_MAX_EVENTS,
        )
        total_events = _checked_count(
            total_events,
            len(raw_events),
            CONVERSATION_ARCHIVE_MAX_EVENTS,
        )
        raw_event_groups.append(raw_events)

    event_records_by_conversation: list[list[dict[str, Any]]] = []
    for raw_events in raw_event_groups:
        if any(type(event) is not dict for event in raw_events):
            raise ConversationArchiveFormatError()
        event_records_by_conversation.append(
            [_require_object(event, _EVENT_FIELDS) for event in raw_events]
        )

    aggregate = _Utf8Budget()
    for conversation, events in zip(
        conversation_records,
        event_records_by_conversation,
        strict=True,
    ):
        aggregate.consume(conversation["subject"])
        for event in events:
            aggregate.consume(event["content"])
    return raw_sessions


def _parse_event(value: object) -> ConversationArchiveEvent:
    record = _require_object(value, _EVENT_FIELDS)
    _validate_schema_version(record["schema_version"])
    if type(record["kind"]) is not str:
        raise ConversationArchiveFormatError()
    try:
        kind = ConversationEventKind(record["kind"])
    except ValueError:
        raise ConversationArchiveFormatError() from None
    return ConversationArchiveEvent(
        schema_version=record["schema_version"],
        session_id=record["session_id"],
        conversation_id=record["conversation_id"],
        turn_id=record["turn_id"],
        sequence=record["sequence"],
        timestamp=record["timestamp"],
        sender=record["sender"],
        recipient=record["recipient"],
        kind=kind,
        content=record["content"],
    )


def _parse_conversation(value: object) -> ConversationArchiveConversation:
    record = _require_object(value, _CONVERSATION_FIELDS)
    _validate_schema_version(record["schema_version"])
    if type(record["status"]) is not str:
        raise ConversationArchiveFormatError()
    try:
        status = ConversationStatus(record["status"])
    except ValueError:
        raise ConversationArchiveFormatError() from None
    raw_events = _require_bounded_list(
        record["events"],
        maximum=CONVERSATION_ARCHIVE_MAX_EVENTS,
    )
    if any(type(event) is not dict for event in raw_events):
        raise ConversationArchiveFormatError()
    return ConversationArchiveConversation(
        schema_version=record["schema_version"],
        session_id=record["session_id"],
        conversation_id=record["conversation_id"],
        subject=record["subject"],
        subject_revision=record["subject_revision"],
        status=status,
        created_at=record["created_at"],
        updated_at=record["updated_at"],
        events=tuple(_parse_event(event) for event in raw_events),
    )


def _parse_session(value: object) -> ConversationArchiveSession:
    record = _require_object(value, _SESSION_FIELDS)
    raw_conversations = _require_bounded_list(
        record["conversations"],
        maximum=CONVERSATION_ARCHIVE_MAX_CONVERSATIONS,
        nonempty=True,
    )
    if any(type(conversation) is not dict for conversation in raw_conversations):
        raise ConversationArchiveFormatError()
    return ConversationArchiveSession(
        session_id=record["session_id"],
        conversations=tuple(
            _parse_conversation(conversation) for conversation in raw_conversations
        ),
    )


def _deserialize_conversation_archive(data: bytes) -> ConversationArchive:
    try:
        value = json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_fields,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ConversationArchiveFormatError() from None

    top = _require_object(value, _TOP_LEVEL_FIELDS)
    _validate_schema_version(top["schema_version"])
    raw_sessions = _preflight_raw_archive(top["sessions"])
    archive = ConversationArchive(
        schema_version=top["schema_version"],
        sessions=tuple(_parse_session(session) for session in raw_sessions),
    )

    digest = _require_object(top["digest"], _DIGEST_FIELDS)
    algorithm = digest["algorithm"]
    digest_value = digest["value"]
    if (
        type(algorithm) is not str
        or algorithm != "sha256"
        or type(digest_value) is not str
        or _DIGEST_PATTERN.fullmatch(digest_value) is None
    ):
        raise ConversationArchiveDigestError()
    expected_digest = hashlib.sha256(_digest_image(archive)).hexdigest()
    if not hmac.compare_digest(digest_value, expected_digest):
        raise ConversationArchiveDigestError()
    if serialize_conversation_archive(archive) != data:
        raise ConversationArchiveFormatError()
    return archive


def deserialize_conversation_archive(data: bytes, /) -> ConversationArchive:
    """Parse only the exact canonical version-1 UTF-8 JSON archive image."""

    if type(data) is not bytes:
        raise ConversationArchiveFormatError()
    if len(data) > CONVERSATION_ARCHIVE_MAX_RAW_BYTES:
        raise ConversationArchiveBoundsError()
    try:
        return _deserialize_conversation_archive(data)
    except RecursionError:
        raise ConversationArchiveFormatError() from None


__all__ = [
    "CONVERSATION_ARCHIVE_SCHEMA_VERSION",
    "CONVERSATION_ARCHIVE_MAX_RAW_BYTES",
    "CONVERSATION_ARCHIVE_MAX_SESSIONS",
    "CONVERSATION_ARCHIVE_MAX_CONVERSATIONS",
    "CONVERSATION_ARCHIVE_MAX_EVENTS",
    "CONVERSATION_ARCHIVE_MAX_AGGREGATE_UTF8_BYTES",
    "ConversationArchive",
    "ConversationArchiveSession",
    "ConversationArchiveConversation",
    "ConversationArchiveEvent",
    "ConversationArchiveError",
    "ConversationArchiveFormatError",
    "ConversationArchiveVersionError",
    "ConversationArchiveBoundsError",
    "ConversationArchiveDigestError",
    "ConversationArchiveSourceError",
    "ConversationArchiveSourceChangedError",
    "ConversationArchiveDestinationError",
    "ConversationArchiveConflictError",
    "ConversationArchiveDestinationChangedError",
    "serialize_conversation_archive",
    "deserialize_conversation_archive",
]
