"""Provider-neutral subject-conversation catalog contracts."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from .errors import SessionIdError


CONVERSATION_METADATA_SCHEMA_VERSION = 1
CONVERSATION_SUBJECT_NORMALIZATION_VERSION = 1
MAX_CONVERSATION_SUBJECT_LENGTH = 256

_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


class ConversationStatus(StrEnum):
    """Discovery lifecycle for an immutable subject conversation."""

    ACTIVE = "active"
    ARCHIVED = "archived"


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
    "CONVERSATION_METADATA_SCHEMA_VERSION",
    "CONVERSATION_SUBJECT_NORMALIZATION_VERSION",
    "MAX_CONVERSATION_SUBJECT_LENGTH",
    "ConversationCatalog",
    "ConversationMetadata",
    "ConversationStatus",
    "normalize_conversation_subject",
]
