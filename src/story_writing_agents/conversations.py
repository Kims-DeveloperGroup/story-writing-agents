"""Provider-neutral subject-conversation persistence contracts."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from .agents import ROSTER
from .errors import SessionIdError


CONVERSATION_EVENT_SCHEMA_VERSION = 1
CONVERSATION_METADATA_SCHEMA_VERSION = 1
CONVERSATION_SUBJECT_NORMALIZATION_VERSION = 1
MAX_CONVERSATION_SUBJECT_LENGTH = 256

_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def _validate_positive_integer(value: int, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value < 1:
        raise ValueError(f"{label} must be positive")
    return value


def _validate_non_negative_integer(value: int, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value < 0:
        raise ValueError(f"{label} must not be negative")
    return value


class ConversationStatus(StrEnum):
    """Discovery lifecycle for an immutable subject conversation."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class ConversationEventKind(StrEnum):
    """Visible event kinds stored in an authoritative conversation transcript."""

    USER_MESSAGE = "user_message"
    AGENT_REPLY = "agent_reply"
    RUNTIME_ERROR = "runtime_error"


@dataclass(frozen=True, slots=True)
class ConversationContextPolicy:
    """Fixed caps applied when selecting complete turns for provider context."""

    max_complete_turns: int
    max_utf8_bytes: int

    def __post_init__(self) -> None:
        _validate_positive_integer(
            self.max_complete_turns,
            label="maximum complete turns",
        )
        _validate_positive_integer(
            self.max_utf8_bytes,
            label="maximum UTF-8 bytes",
        )


CONVERSATION_CONTEXT_POLICY = ConversationContextPolicy(
    max_complete_turns=24,
    max_utf8_bytes=32_768,
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


@dataclass(frozen=True, slots=True)
class ConversationContextWindow:
    """A bounded chronological suffix of complete conversation turns."""

    events: tuple[ConversationEvent, ...]
    total_complete_turns: int
    retained_turns: int
    omitted_turns: int
    retained_utf8_bytes: int
    truncated: bool

    def __post_init__(self) -> None:
        if not isinstance(self.events, tuple) or any(
            not isinstance(event, ConversationEvent) for event in self.events
        ):
            raise TypeError("context events must be a tuple of ConversationEvent")
        for value, label in (
            (self.total_complete_turns, "total complete turns"),
            (self.retained_turns, "retained turns"),
            (self.omitted_turns, "omitted turns"),
            (self.retained_utf8_bytes, "retained UTF-8 bytes"),
        ):
            _validate_non_negative_integer(value, label=label)
        if self.total_complete_turns != self.retained_turns + self.omitted_turns:
            raise ValueError("context turn counts are inconsistent")
        if not isinstance(self.truncated, bool):
            raise TypeError("context truncated flag must be a boolean")
        if self.truncated is not (self.omitted_turns > 0):
            raise ValueError("context truncated flag is inconsistent")


def validate_conversation_context_window(
    context: ConversationContextWindow,
    *,
    session_id: str,
    conversation_id: str | None = None,
    excluded_turn_id: str | None = None,
) -> ConversationContextWindow:
    """Return a canonical, identity-bound conversation context window.

    The supplied immutable window is copied into built-in event/window types and
    validated against the owning session, optional conversation, optional
    excluded turn, pairing rules, ordering, metadata, and fixed policy caps. The
    operation performs no I/O or mutation; invalid identities or content raise
    ``SessionIdError``, ``TypeError``, or ``ValueError``.
    """

    _validate_identifier(session_id, label="session ID")
    if conversation_id is not None:
        _validate_identifier(conversation_id, label="conversation ID")
    if excluded_turn_id is not None:
        _validate_identifier(excluded_turn_id, label="excluded turn ID")
    if type(context) is not ConversationContextWindow:
        raise TypeError("conversation context must be a ConversationContextWindow")

    try:
        source_events = context.events
        total_complete_turns = context.total_complete_turns
        retained_turns_metadata = context.retained_turns
        omitted_turns = context.omitted_turns
        retained_utf8_bytes_metadata = context.retained_utf8_bytes
        truncated = context.truncated
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("conversation context metadata is invalid") from exc
    if type(source_events) is not tuple:
        raise ValueError("conversation context metadata is invalid")

    canonical_events: list[ConversationEvent] = []
    for event in source_events:
        if not isinstance(event, ConversationEvent):
            raise ValueError("conversation context contains an invalid event")
        try:
            canonical_events.append(
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
            )
        except (AttributeError, SessionIdError, TypeError, ValueError) as exc:
            raise ValueError(
                "conversation context contains an invalid event"
            ) from exc

    try:
        canonical_context = ConversationContextWindow(
            events=tuple(canonical_events),
            total_complete_turns=total_complete_turns,
            retained_turns=retained_turns_metadata,
            omitted_turns=omitted_turns,
            retained_utf8_bytes=retained_utf8_bytes_metadata,
            truncated=truncated,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("conversation context metadata is invalid") from exc

    turns: dict[str, list[ConversationEvent]] = {}
    previous_sequence = 0
    retained_utf8_bytes = 0
    observed_conversation_id = conversation_id
    for event in canonical_context.events:
        if event.session_id != session_id:
            raise ValueError("conversation context contains a different session")
        if observed_conversation_id is None:
            observed_conversation_id = event.conversation_id
        elif event.conversation_id != observed_conversation_id:
            raise ValueError("conversation context contains a different conversation")
        if excluded_turn_id is not None and event.turn_id == excluded_turn_id:
            raise ValueError("conversation context contains the excluded turn")
        if event.sequence <= previous_sequence:
            raise ValueError(
                "conversation context event sequences must be strictly increasing"
            )
        previous_sequence = event.sequence
        retained_utf8_bytes += len(event.content.encode("utf-8"))
        turns.setdefault(event.turn_id, []).append(event)

    for turn_id, events in turns.items():
        if len(events) != 2:
            raise ValueError(f"conversation context turn is incomplete: {turn_id}")
        users = [
            event
            for event in events
            if event.kind is ConversationEventKind.USER_MESSAGE
        ]
        outcomes = [
            event
            for event in events
            if event.kind is not ConversationEventKind.USER_MESSAGE
        ]
        if len(users) != 1 or len(outcomes) != 1:
            raise ValueError(
                f"conversation context turn is not correctly paired: {turn_id}"
            )
        user, outcome = users[0], outcomes[0]
        if user.sequence >= outcome.sequence:
            raise ValueError(
                f"conversation context outcome precedes its user event: {turn_id}"
            )
        if (
            outcome.kind is ConversationEventKind.AGENT_REPLY
            and outcome.sender != user.recipient
        ):
            raise ValueError(
                "conversation context reply sender does not match addressed agent"
            )

    retained_turns = len(turns)
    if canonical_context.retained_turns != retained_turns:
        raise ValueError("conversation context retained-turn metadata is inconsistent")
    if canonical_context.retained_utf8_bytes != retained_utf8_bytes:
        raise ValueError("conversation context byte metadata is inconsistent")
    if retained_turns > CONVERSATION_CONTEXT_POLICY.max_complete_turns:
        raise ValueError("conversation context exceeds the retained-turn cap")
    if retained_utf8_bytes > CONVERSATION_CONTEXT_POLICY.max_utf8_bytes:
        raise ValueError("conversation context exceeds the UTF-8 byte cap")
    if all(type(event) is ConversationEvent for event in source_events):
        return context
    return canonical_context


def select_bounded_conversation_context(
    events: tuple[ConversationEvent, ...],
) -> ConversationContextWindow:
    """Select the fixed-policy newest suffix of complete transcript turns.

    ``events`` must be an exact tuple of canonical events from one transcript
    in contiguous storage order starting at one. Unmatched user events remain
    valid transcript history but are excluded from provider context. The pure
    operation performs no I/O or mutation and raises ``TypeError`` or
    ``ValueError`` when its snapshot violates the transcript contract.
    """

    if type(events) is not tuple:
        raise TypeError("conversation events must be an exact tuple")

    transcript_identity: tuple[str, str] | None = None
    user_events: dict[str, ConversationEvent] = {}
    outcomes: dict[str, ConversationEvent] = {}
    for ordinal, event in enumerate(events, start=1):
        if type(event) is not ConversationEvent:
            raise TypeError("conversation events must be canonical events")
        try:
            canonical = ConversationEvent(
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
        except (AttributeError, SessionIdError, TypeError, ValueError) as exc:
            raise ValueError(
                "conversation transcript contains an invalid event"
            ) from exc
        if canonical != event:
            raise ValueError("conversation transcript contains a noncanonical event")

        identity = (event.session_id, event.conversation_id)
        if transcript_identity is None:
            transcript_identity = identity
        elif identity != transcript_identity:
            raise ValueError("conversation events span multiple transcripts")
        if event.sequence != ordinal:
            raise ValueError(
                "conversation event sequences must be contiguous from one"
            )

        if event.kind is ConversationEventKind.USER_MESSAGE:
            if event.turn_id in user_events:
                raise ValueError("conversation turn has duplicate user events")
            user_events[event.turn_id] = event
            continue
        user_event = user_events.get(event.turn_id)
        if user_event is None:
            raise ValueError("conversation outcome precedes its user event")
        if event.turn_id in outcomes:
            raise ValueError("conversation turn has duplicate outcomes")
        if (
            event.kind is ConversationEventKind.AGENT_REPLY
            and event.sender != user_event.recipient
        ):
            raise ValueError(
                "conversation reply sender does not match the addressed agent"
            )
        outcomes[event.turn_id] = event

    complete_turns = [
        (event, outcomes[event.turn_id])
        for event in events
        if event.kind is ConversationEventKind.USER_MESSAGE
        and event.turn_id in outcomes
    ]
    retained_reversed: list[tuple[ConversationEvent, ConversationEvent]] = []
    retained_utf8_bytes = 0
    for turn in reversed(complete_turns):
        turn_utf8_bytes = sum(
            len(event.content.encode("utf-8", errors="strict"))
            for event in turn
        )
        if (
            len(retained_reversed)
            >= CONVERSATION_CONTEXT_POLICY.max_complete_turns
            or retained_utf8_bytes + turn_utf8_bytes
            > CONVERSATION_CONTEXT_POLICY.max_utf8_bytes
        ):
            break
        retained_reversed.append(turn)
        retained_utf8_bytes += turn_utf8_bytes

    retained = tuple(reversed(retained_reversed))
    retained_events = tuple(
        sorted(
            (event for turn in retained for event in turn),
            key=lambda event: event.sequence,
        )
    )
    total_complete_turns = len(complete_turns)
    retained_turns = len(retained)
    omitted_turns = total_complete_turns - retained_turns
    return ConversationContextWindow(
        events=retained_events,
        total_complete_turns=total_complete_turns,
        retained_turns=retained_turns,
        omitted_turns=omitted_turns,
        retained_utf8_bytes=retained_utf8_bytes,
        truncated=omitted_turns > 0,
    )


class ConversationEventStore(Protocol):
    """Storage-neutral visible transcript operations."""

    def append(self, event: ConversationEventAppend) -> ConversationEvent:
        """Durably append one event with storage-assigned order metadata."""

        ...

    def read(
        self,
        session_id: str,
        conversation_id: str,
    ) -> tuple[ConversationEvent, ...]:
        """Read the complete authoritative transcript in storage order."""

        ...

    def read_context(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationContextWindow:
        """Select the fixed-policy suffix of complete turns."""

        ...


def normalize_conversation_subject(subject: str) -> str:
    """Return the version-1 deterministic search key for a display subject.

    The original display value remains authoritative and is never rewritten.
    """

    display = _validate_subject(subject)
    normalized = unicodedata.normalize("NFKC", display).casefold()
    return " ".join(normalized.split())


@dataclass(frozen=True, slots=True)
class ConversationMetadata:
    """Immutable authoritative metadata for one subject conversation."""

    schema_version: int
    session_id: str
    conversation_id: str
    subject: str
    subject_revision: int
    status: ConversationStatus
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != CONVERSATION_METADATA_SCHEMA_VERSION
        ):
            raise ValueError(
                "unsupported conversation metadata schema version"
            )
        _validate_identifier(self.session_id, label="session ID")
        _validate_identifier(self.conversation_id, label="conversation ID")
        _validate_subject(self.subject)
        if (
            isinstance(self.subject_revision, bool)
            or not isinstance(self.subject_revision, int)
            or self.subject_revision < 1
        ):
            raise ValueError("subject revision must be a positive integer")
        if not isinstance(self.status, ConversationStatus):
            raise TypeError("conversation status must be a ConversationStatus")
        created = _validate_utc_timestamp(self.created_at, label="created_at")
        updated = _validate_utc_timestamp(self.updated_at, label="updated_at")
        if updated < created:
            raise ValueError("updated_at must not precede created_at")

    def to_dict(self) -> dict[str, Any]:
        """Return the exact JSON-compatible authoritative schema."""

        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "subject": self.subject,
            "subject_revision": self.subject_revision,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ConversationCatalog(Protocol):
    """Storage-neutral lifecycle operations for subject conversations."""

    def create(self, session_id: str, subject: str) -> ConversationMetadata:
        """Create a conversation with a new immutable identifier."""

        ...

    def get(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationMetadata:
        """Read one conversation or raise ``ConversationNotFoundError``."""

        ...

    def list(
        self,
        session_id: str,
        *,
        include_archived: bool = False,
    ) -> tuple[ConversationMetadata, ...]:
        """List conversations in deterministic creation order."""

        ...

    def rename(
        self,
        session_id: str,
        conversation_id: str,
        subject: str,
    ) -> ConversationMetadata:
        """Replace the display subject and increment its revision."""

        ...

    def archive(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationMetadata:
        """Move a conversation out of the default discovery set."""

        ...

    def unarchive(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationMetadata:
        """Return an archived conversation to the active discovery set."""

        ...


def _validate_identifier(value: str, *, label: str) -> str:
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


def _validate_subject(subject: str) -> str:
    if not isinstance(subject, str):
        raise TypeError("conversation subject must be a string")
    try:
        subject.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("conversation subject must be valid UTF-8") from exc
    if not subject.strip():
        raise ValueError("conversation subject must not be blank")
    if len(subject) > MAX_CONVERSATION_SUBJECT_LENGTH:
        raise ValueError(
            "conversation subject exceeds "
            f"{MAX_CONVERSATION_SUBJECT_LENGTH} characters"
        )
    if any(unicodedata.category(character) == "Cc" for character in subject):
        raise ValueError("conversation subject must not contain control characters")
    return subject


def _validate_utc_timestamp(value: str, *, label: str) -> datetime:
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
    "CONVERSATION_CONTEXT_POLICY",
    "CONVERSATION_EVENT_SCHEMA_VERSION",
    "CONVERSATION_METADATA_SCHEMA_VERSION",
    "CONVERSATION_SUBJECT_NORMALIZATION_VERSION",
    "MAX_CONVERSATION_SUBJECT_LENGTH",
    "ConversationCatalog",
    "ConversationContextPolicy",
    "ConversationContextWindow",
    "ConversationEvent",
    "ConversationEventAppend",
    "ConversationEventKind",
    "ConversationEventStore",
    "ConversationMetadata",
    "ConversationStatus",
    "normalize_conversation_subject",
    "select_bounded_conversation_context",
    "validate_conversation_context_window",
]
