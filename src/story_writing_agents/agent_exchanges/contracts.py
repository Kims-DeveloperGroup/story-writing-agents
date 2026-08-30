"""Immutable contracts for bounded agent request and response diagnostics."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from ..agents import ROSTER
from ..errors import StoryWritingAgentsError


AGENT_EXCHANGE_SCHEMA_VERSION = 1
AGENT_EXCHANGE_CONTENT_MAX_BYTES = 262_144
AGENT_EXCHANGE_TOTAL_MAX_BYTES = 16_777_216

_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_SAFE_EXCHANGE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_RFC3339_MILLISECONDS = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"\.[0-9]{3}\+00:00"
)


class AgentExchangeProvider(StrEnum):
    """Provider identities admitted by the durable exchange schema."""

    CODEX = "codex"
    ANTIGRAVITY = "antigravity"


class AgentExchangeRequestType(StrEnum):
    """Physical provider-call positions admitted by the durable schema."""

    REQUEST = "request"
    RESPONSE = "response"
    NO_RESPONSE = "no_response"
    OUTCOME = "outcome"


class AgentExchangeOutcome(StrEnum):
    """Closed terminal outcomes for one physical provider call."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PROVIDER_FAILURE = "provider_failure"
    CANCELLED = "cancelled"
    POST_RESPONSE_RUNTIME_FAILURE = "post_response_runtime_failure"


class AgentExchangeScopeKind(StrEnum):
    """Conversation-like scopes to which an exchange may be correlated."""

    CONVERSATION = "conversation"
    DEBATE = "debate"


class AgentExchangeFailure(StrEnum):
    """Stable content-free storage failure classifications."""

    UNSUPPORTED_PLATFORM = "unsupported_platform"
    CONTENTION = "contention"
    UNSAFE_STORAGE = "unsafe_storage"
    CORRUPT_STORAGE = "corrupt_storage"
    CAPACITY_EXCEEDED = "capacity_exceeded"
    IO_FAILED = "io_failed"
    NOT_FOUND = "not_found"


class AgentExchangeStorageError(StoryWritingAgentsError):
    """Carry one fixed safe failure without filesystem or content details."""

    __slots__ = ("failure",)

    def __init__(self, failure: AgentExchangeFailure) -> None:
        if type(failure) is not AgentExchangeFailure:
            raise TypeError("failure must be an AgentExchangeFailure")
        self.failure = failure
        super().__init__(f"agent exchange storage failure: {failure.value}")


def _require_identifier(value: object, *, label: str) -> str:
    """Return one exact safe identity used by a record or filesystem name."""

    if type(value) is not str:
        raise TypeError(f"{label} must be text")
    if _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{label} must match [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}")
    return value


def _require_exchange_identifier(value: object) -> str:
    """Return one bounded provider-call correlation identity."""

    if type(value) is not str:
        raise TypeError("exchange_id must be text")
    if _SAFE_EXCHANGE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError("exchange_id must match [A-Za-z0-9][A-Za-z0-9_-]{0,127}")
    return value


def _require_positive_integer(value: object, *, label: str) -> int:
    """Return one positive non-boolean sequence or count."""

    if type(value) is not int:
        raise TypeError(f"{label} must be an integer")
    if value <= 0:
        raise ValueError(f"{label} must be positive")
    return value


@dataclass(frozen=True, slots=True)
class AgentExchangeRecordInput:
    """One caller-supplied request, response, or safe no-response value."""

    session_id: str
    scope_kind: AgentExchangeScopeKind
    scope_id: str
    agent_id: str
    provider: AgentExchangeProvider
    exchange_id: str
    request_type: AgentExchangeRequestType
    content: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_identifier(self.session_id, label="session_id")
        if type(self.scope_kind) is not AgentExchangeScopeKind:
            raise TypeError("scope_kind must be an AgentExchangeScopeKind")
        _require_identifier(self.scope_id, label="scope_id")
        agent_id = _require_identifier(self.agent_id, label="agent_id")
        if agent_id not in ROSTER:
            raise ValueError("agent_id must be canonical")
        if type(self.provider) is not AgentExchangeProvider:
            raise TypeError("provider must be an AgentExchangeProvider")
        _require_exchange_identifier(self.exchange_id)
        if type(self.request_type) is not AgentExchangeRequestType:
            raise TypeError("request_type must be an AgentExchangeRequestType")
        if type(self.content) is not str:
            raise TypeError("content must be text")
        try:
            self.content.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise ValueError("content must be strict UTF-8 text") from None


@dataclass(frozen=True, slots=True)
class AgentExchangeAppendReceipt:
    """Content-free persisted identity and retention facts for one append."""

    session_id: str
    agent_id: str
    exchange_id: str
    workspace_sequence: int
    sequence: int
    timestamp: str
    content_full_utf8_bytes: int
    content_retained_utf8_bytes: int
    content_full_characters: int
    content_retained_characters: int
    content_truncated: bool
    content_sha256: str

    def __post_init__(self) -> None:
        _require_identifier(self.session_id, label="session_id")
        agent_id = _require_identifier(self.agent_id, label="agent_id")
        if agent_id not in ROSTER:
            raise ValueError("agent_id must be canonical")
        _require_exchange_identifier(self.exchange_id)
        _require_positive_integer(
            self.workspace_sequence,
            label="workspace_sequence",
        )
        _require_positive_integer(self.sequence, label="sequence")
        if type(self.timestamp) is not str or (
            _RFC3339_MILLISECONDS.fullmatch(self.timestamp) is None
        ):
            raise ValueError("timestamp must be UTC RFC3339 with milliseconds")
        try:
            parsed_timestamp = datetime.fromisoformat(self.timestamp)
        except ValueError:
            raise ValueError("timestamp must be a valid UTC instant") from None
        if parsed_timestamp.utcoffset() != UTC.utcoffset(None):
            raise ValueError("timestamp must be UTC")
        if type(self.content_full_utf8_bytes) is not int:
            raise TypeError("content_full_utf8_bytes must be an integer")
        if type(self.content_retained_utf8_bytes) is not int:
            raise TypeError("content_retained_utf8_bytes must be an integer")
        if (
            self.content_full_utf8_bytes < 0
            or self.content_retained_utf8_bytes < 0
            or self.content_retained_utf8_bytes > AGENT_EXCHANGE_CONTENT_MAX_BYTES
            or self.content_retained_utf8_bytes > self.content_full_utf8_bytes
        ):
            raise ValueError("content byte counts are invalid")
        if type(self.content_full_characters) is not int:
            raise TypeError("content_full_characters must be an integer")
        if type(self.content_retained_characters) is not int:
            raise TypeError("content_retained_characters must be an integer")
        if (
            self.content_full_characters < 0
            or self.content_retained_characters < 0
            or self.content_retained_characters > self.content_full_characters
        ):
            raise ValueError("content character counts are invalid")
        if type(self.content_truncated) is not bool:
            raise TypeError("content_truncated must be a boolean")
        if self.content_truncated != (
            self.content_retained_utf8_bytes != self.content_full_utf8_bytes
        ) or self.content_truncated != (
            self.content_retained_characters != self.content_full_characters
        ):
            raise ValueError("content truncation facts are inconsistent")
        if type(self.content_sha256) is not str or (
            _SHA256.fullmatch(self.content_sha256) is None
        ):
            raise ValueError("content_sha256 is invalid")


@dataclass(frozen=True, slots=True)
class AgentExchangeSelection:
    """Exact content-free session, scope, and agent snapshot selection."""

    session_id: str
    scope_kind: AgentExchangeScopeKind
    scope_id: str
    agent_id: str

    def __post_init__(self) -> None:
        _require_identifier(self.session_id, label="session_id")
        if type(self.scope_kind) is not AgentExchangeScopeKind:
            raise TypeError("scope_kind must be an AgentExchangeScopeKind")
        _require_identifier(self.scope_id, label="scope_id")
        agent_id = _require_identifier(self.agent_id, label="agent_id")
        if agent_id not in ROSTER:
            raise ValueError("agent_id must be canonical")


@dataclass(frozen=True, slots=True)
class AgentExchangeInventoryItem:
    """Content-free summary for one exact selectable exchange stream."""

    selection: AgentExchangeSelection
    record_count: int
    first_workspace_sequence: int
    last_workspace_sequence: int
    first_sequence: int
    last_sequence: int

    def __post_init__(self) -> None:
        if type(self.selection) is not AgentExchangeSelection:
            raise TypeError("selection must be an AgentExchangeSelection")
        _require_positive_integer(self.record_count, label="record_count")
        first_workspace = _require_positive_integer(
            self.first_workspace_sequence,
            label="first_workspace_sequence",
        )
        last_workspace = _require_positive_integer(
            self.last_workspace_sequence,
            label="last_workspace_sequence",
        )
        first = _require_positive_integer(self.first_sequence, label="first_sequence")
        last = _require_positive_integer(self.last_sequence, label="last_sequence")
        if first_workspace > last_workspace or first > last:
            raise ValueError("inventory sequence range is invalid")


@dataclass(frozen=True, slots=True)
class AgentExchangeSnapshot:
    """Immutable strict-UTF-8 JSONL bytes for one exact selection."""

    selection: AgentExchangeSelection
    jsonl_bytes: bytes = field(repr=False)
    record_count: int
    first_workspace_sequence: int
    last_workspace_sequence: int
    first_sequence: int
    last_sequence: int

    def __post_init__(self) -> None:
        if type(self.selection) is not AgentExchangeSelection:
            raise TypeError("selection must be an AgentExchangeSelection")
        if type(self.jsonl_bytes) is not bytes or not self.jsonl_bytes:
            raise ValueError("jsonl_bytes must be nonempty immutable bytes")
        if len(self.jsonl_bytes) > AGENT_EXCHANGE_TOTAL_MAX_BYTES:
            raise ValueError("jsonl_bytes exceeds the exchange storage bound")
        if not self.jsonl_bytes.endswith(b"\n"):
            raise ValueError("jsonl_bytes must end at a complete record")
        try:
            self.jsonl_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise ValueError("jsonl_bytes must be strict UTF-8") from None
        _require_positive_integer(self.record_count, label="record_count")
        if self.jsonl_bytes.count(b"\n") != self.record_count:
            raise ValueError("record_count does not match complete JSONL lines")
        first_workspace = _require_positive_integer(
            self.first_workspace_sequence,
            label="first_workspace_sequence",
        )
        last_workspace = _require_positive_integer(
            self.last_workspace_sequence,
            label="last_workspace_sequence",
        )
        first = _require_positive_integer(self.first_sequence, label="first_sequence")
        last = _require_positive_integer(self.last_sequence, label="last_sequence")
        if first_workspace > last_workspace or first > last:
            raise ValueError("snapshot sequence range is invalid")


__all__ = [
    "AGENT_EXCHANGE_CONTENT_MAX_BYTES",
    "AGENT_EXCHANGE_SCHEMA_VERSION",
    "AGENT_EXCHANGE_TOTAL_MAX_BYTES",
    "AgentExchangeAppendReceipt",
    "AgentExchangeFailure",
    "AgentExchangeInventoryItem",
    "AgentExchangeOutcome",
    "AgentExchangeProvider",
    "AgentExchangeRecordInput",
    "AgentExchangeRequestType",
    "AgentExchangeScopeKind",
    "AgentExchangeSelection",
    "AgentExchangeSnapshot",
    "AgentExchangeStorageError",
]
