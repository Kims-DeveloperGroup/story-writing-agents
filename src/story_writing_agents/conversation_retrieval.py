"""Immutable contracts for bounded agent-directed conversation retrieval."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Callable, Protocol, TypeAlias  # noqa: UP035 - public alias parity

from .agents import ROSTER
from .conversation_rag import (
    CONVERSATION_RAG_POLICY,
    ConversationRagOutcome,
    ConversationRagReason,
    ConversationRagSourceReference,
)
from .conversation_search import (
    CONVERSATION_MEMORY_POLICY,
    CollaborationHistoryCitation,
)
from .conversations import (
    CollaborationReadableContext,
    CollaborationReadableRecord,
)
from .reply_trace import ReplyTrace
from .structured_output_validation import validate_details
from .errors import SessionIdError
from .models import StorySessionContext

_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_SAFE_REFERENCE_OPERATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_RAG_CITATION_ID = re.compile(r"g(?:[1-9]|1[0-9]|20)")
_BEGIN_UNTRUSTED_STORY_DATA = "<<<BEGIN_UNTRUSTED_STORY_DATA>>>"
_END_UNTRUSTED_STORY_DATA = "<<<END_UNTRUSTED_STORY_DATA>>>"


def _positive_integer(value: int, *, label: str) -> int:
    """Return one positive built-in integer without I/O or mutation."""

    if type(value) is not int:
        raise TypeError(f"{label} must be an integer")
    if value < 1:
        raise ValueError(f"{label} must be positive")
    return value


def _non_negative_integer(value: int, *, label: str) -> int:
    """Return one nonnegative built-in integer without I/O or mutation."""

    if type(value) is not int:
        raise TypeError(f"{label} must be an integer")
    if value < 0:
        raise ValueError(f"{label} must not be negative")
    return value


def _identifier(value: str, *, label: str) -> str:
    """Return one exact safe identifier or raise before any side effect."""

    if type(value) is not str:
        raise TypeError(f"{label} must be a string")
    if _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise SessionIdError(f"{label} must match [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}")
    return value


def _strict_utf8(value: str, *, label: str, nonempty: bool = False) -> str:
    """Return exact strict-UTF-8 text, optionally requiring nonempty content."""

    if type(value) is not str:
        raise TypeError(f"{label} must be a string")
    if nonempty and not value:
        raise ValueError(f"{label} must not be empty")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8") from exc
    return value


def _boolean(value: bool, *, label: str) -> bool:
    """Return one exact boolean or raise without external effects."""

    if type(value) is not bool:
        raise TypeError(f"{label} must be a boolean")
    return value


def _citation_to_dict(citation: CollaborationHistoryCitation) -> dict[str, object]:
    """Project one validated citation into its exact JSON-compatible shape."""

    return {
        "session_id": citation.session_id,
        "conversation_id": citation.conversation_id,
        "event_id": citation.event_id,
        "sequence": citation.sequence,
        "field_key": citation.field_key.value,
        "start_codepoint": citation.start_codepoint,
        "end_codepoint": citation.end_codepoint,
        "assignment_ordinal": citation.assignment_ordinal,
        "input_ordinal": citation.input_ordinal,
    }


def _validated_citation(
    value: CollaborationHistoryCitation,
    *,
    label: str,
) -> CollaborationHistoryCitation:
    """Return a canonical exact citation without performing external reads."""

    if type(value) is not CollaborationHistoryCitation:
        raise TypeError(f"{label} must be a CollaborationHistoryCitation")
    try:
        return CollaborationHistoryCitation(
            session_id=value.session_id,
            conversation_id=value.conversation_id,
            event_id=value.event_id,
            sequence=value.sequence,
            field_key=value.field_key,
            start_codepoint=value.start_codepoint,
            end_codepoint=value.end_codepoint,
            assignment_ordinal=value.assignment_ordinal,
            input_ordinal=value.input_ordinal,
        )
    except (AttributeError, SessionIdError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} is invalid") from exc


class ConversationRetrievalActionKind(StrEnum):
    """Provider actions allowed during one bounded retrieval turn."""

    SEARCH_CONVERSATIONS = "search_conversations"
    READ_HISTORY = "read_history"
    ANSWER = "answer"


class ConversationRetrievalProgressPhase(StrEnum):
    """Trusted nonsensitive phases for retrieval memory operations."""

    SEARCH_STARTED = "search_started"
    SEARCH_COMPLETED = "search_completed"
    READ_STARTED = "read_started"
    READ_COMPLETED = "read_completed"


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationRagAttemptOutcome:
    """Content-free result of one optional semantic-retrieval attempt."""

    outcome: ConversationRagOutcome
    reason: ConversationRagReason

    def __post_init__(self) -> None:
        """Validate the exact outcome/reason combinations without I/O."""

        if type(self.outcome) is not ConversationRagOutcome:
            raise TypeError("conversation RAG outcome must use the exact enum")
        if type(self.reason) is not ConversationRagReason:
            raise TypeError("conversation RAG reason must use the exact enum")
        if self.outcome is ConversationRagOutcome.COMPLETE:
            if self.reason is not ConversationRagReason.NONE:
                raise ValueError("complete conversation RAG requires reason none")
            return
        if self.outcome is ConversationRagOutcome.PARTIAL:
            if self.reason is not ConversationRagReason.PARTIAL:
                raise ValueError("partial conversation RAG requires reason partial")
            return
        if self.reason not in (
            ConversationRagReason.DISABLED,
            ConversationRagReason.UNAVAILABLE,
            ConversationRagReason.STALE,
            ConversationRagReason.CORRUPT,
            ConversationRagReason.INCOMPATIBLE,
            ConversationRagReason.FAILED,
        ):
            raise ValueError("literal fallback requires a failure reason")

    def to_dict(self) -> dict[str, str]:
        """Return the exact provider-visible content-free outcome shape."""

        return {
            "outcome": self.outcome.value,
            "reason": self.reason.value,
        }


def _rag_reference_to_dict(
    reference: ConversationRagSourceReference,
) -> dict[str, object]:
    """Project one validated RAG source reference without provider text."""

    document = reference.document
    return {
        "document": {
            "key": {
                "session_id": document.key.session_id,
                "document_id": document.key.document_id,
            },
            "conversation_id": document.conversation_id,
            "start_turn_id": document.start_turn_id,
            "end_turn_id": document.end_turn_id,
            "start_sequence": document.start_sequence,
            "end_sequence": document.end_sequence,
            "revision_id": document.revision_id,
        },
        "fragment_id": reference.fragment_id,
        "start_codepoint": reference.start_codepoint,
        "end_codepoint": reference.end_codepoint,
    }


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationRagExcerpt:
    """One bounded authoritative excerpt supplied as untrusted story data."""

    citation_id: str
    source: ConversationRetrievalSource
    reference: ConversationRagSourceReference
    content: str = field(repr=False)

    def __post_init__(self) -> None:
        """Validate correlation, range width, and fixed excerpt bounds."""

        if (
            type(self.citation_id) is not str
            or _RAG_CITATION_ID.fullmatch(self.citation_id) is None
        ):
            raise ValueError("conversation RAG citation ID is invalid")
        if type(self.source) is not ConversationRetrievalSource:
            raise TypeError("conversation RAG source must use the exact type")
        if type(self.reference) is not ConversationRagSourceReference:
            raise TypeError("conversation RAG reference must use the exact type")
        content = _strict_utf8(
            self.content,
            label="conversation RAG excerpt content",
            nonempty=True,
        )
        document = self.reference.document
        if (
            self.source.citation.session_id != document.key.session_id
            or self.source.citation.conversation_id != document.conversation_id
        ):
            raise ValueError("conversation RAG excerpt source crossed its document")
        citation = self.source.citation
        if (
            citation.sequence < document.start_sequence
            or citation.sequence > document.end_sequence
        ):
            raise ValueError("conversation RAG excerpt citation crossed its range")
        width = self.reference.end_codepoint - self.reference.start_codepoint
        if len(content) != width:
            raise ValueError("conversation RAG excerpt width is inconsistent")
        if width > CONVERSATION_RAG_POLICY.max_fragment_codepoints:
            raise ValueError("conversation RAG excerpt exceeds the fixed limit")

    def to_dict(self) -> dict[str, object]:
        """Return the exact provider-visible authoritative excerpt shape."""

        return {
            "citation_id": self.citation_id,
            "source": self.source.to_dict(),
            "reference": _rag_reference_to_dict(self.reference),
            "content": self.content,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationRagContext:
    """Bounded preflight context included only when RAG was attempted."""

    outcome: ConversationRagAttemptOutcome
    excerpts: tuple[ConversationRagExcerpt, ...]
    retained_utf8_bytes: int
    truncated: bool

    @classmethod
    def disabled(cls) -> ConversationRagContext:
        """Return the canonical empty context for a forced disabled attempt."""

        return cls(
            outcome=ConversationRagAttemptOutcome(
                outcome=ConversationRagOutcome.LITERAL_FALLBACK,
                reason=ConversationRagReason.DISABLED,
            ),
            excerpts=(),
            retained_utf8_bytes=0,
            truncated=False,
        )

    def __post_init__(self) -> None:
        """Validate deterministic citations, exact byte count, and budgets."""

        if type(self.outcome) is not ConversationRagAttemptOutcome:
            raise TypeError("conversation RAG context outcome must use the exact type")
        if type(self.excerpts) is not tuple or any(
            type(excerpt) is not ConversationRagExcerpt for excerpt in self.excerpts
        ):
            raise TypeError("conversation RAG excerpts must be a tuple of exact values")
        if len(self.excerpts) > CONVERSATION_RAG_POLICY.max_candidates:
            raise ValueError("conversation RAG context exceeds the excerpt limit")
        for ordinal, excerpt in enumerate(self.excerpts, start=1):
            if excerpt.citation_id != f"g{ordinal}":
                raise ValueError("conversation RAG citation IDs are inconsistent")
        references = tuple(excerpt.reference for excerpt in self.excerpts)
        if len(set(references)) != len(references):
            raise ValueError("conversation RAG context contains duplicate references")
        retained = _non_negative_integer(
            self.retained_utf8_bytes,
            label="conversation RAG retained UTF-8 bytes",
        )
        actual = sum(len(excerpt.content.encode("utf-8")) for excerpt in self.excerpts)
        if retained != actual:
            raise ValueError("conversation RAG retained byte count is inconsistent")
        if retained > 32_768:
            raise ValueError("conversation RAG context exceeds the UTF-8 byte limit")
        _boolean(self.truncated, label="conversation RAG context truncated")
        if self.truncated and self.outcome.outcome is ConversationRagOutcome.COMPLETE:
            raise ValueError("truncated conversation RAG context must be partial")

    def to_dict(self) -> dict[str, object]:
        """Return the exact initial-provider payload member."""

        return {
            **self.outcome.to_dict(),
            "excerpts": [excerpt.to_dict() for excerpt in self.excerpts],
            "retained_utf8_bytes": self.retained_utf8_bytes,
            "truncated": self.truncated,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationRetrievalPolicy:
    """Fixed provider, search, readable-history, and framing limits."""

    max_nonterminal_actions: int = 4
    max_provider_calls: int = 5
    max_search_hits_per_action: int = 10
    max_search_hits_total: int = 20
    max_history_records: int = 24
    max_observation_utf8_bytes: int = 32_768

    def __post_init__(self) -> None:
        """Validate positive, internal, and outer memory-policy limits."""

        for field_name in self.__dataclass_fields__:
            _positive_integer(
                getattr(self, field_name),
                label=field_name.replace("_", " "),
            )
        if self.max_provider_calls != self.max_nonterminal_actions + 1:
            raise ValueError(
                "maximum provider calls must equal nonterminal actions plus one"
            )
        if self.max_search_hits_total < self.max_search_hits_per_action:
            raise ValueError(
                "total search hits must not be less than per-action search hits"
            )
        if self.max_search_hits_per_action > CONVERSATION_MEMORY_POLICY.max_search_hits:
            raise ValueError(
                "per-action search hits exceed the conversation memory policy"
            )
        if self.max_history_records > CONVERSATION_MEMORY_POLICY.max_page_events:
            raise ValueError(
                "maximum history records exceed the conversation memory policy"
            )
        if (
            self.max_observation_utf8_bytes
            > CONVERSATION_MEMORY_POLICY.max_page_utf8_bytes
        ):
            raise ValueError(
                "maximum observation bytes exceed the conversation memory policy"
            )

    def to_dict(self) -> dict[str, int]:
        """Return the exact provider policy object without mutation or failure."""

        return {
            "max_nonterminal_actions": self.max_nonterminal_actions,
            "max_provider_calls": self.max_provider_calls,
            "max_search_hits_per_action": self.max_search_hits_per_action,
            "max_search_hits_total": self.max_search_hits_total,
            "max_history_records": self.max_history_records,
            "max_observation_utf8_bytes": self.max_observation_utf8_bytes,
        }


CONVERSATION_RETRIEVAL_POLICY = ConversationRetrievalPolicy()


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationRetrievalRequest:
    """One correlated bounded-retrieval advisory request and current context."""

    session_id: str
    conversation_id: str
    turn_id: str
    agent_id: str
    content: str
    context: CollaborationReadableContext
    story_session_context: StorySessionContext | None = None
    rag_context: ConversationRagContext | None = None

    def __post_init__(self) -> None:
        """Validate identity, content, and context without I/O or durability."""

        _identifier(self.session_id, label="session ID")
        _identifier(self.conversation_id, label="conversation ID")
        _identifier(self.turn_id, label="turn ID")
        _identifier(self.agent_id, label="agent ID")
        if self.agent_id not in ROSTER:
            raise ValueError(
                f"conversation retrieval agent is not canonical: {self.agent_id}"
            )
        _strict_utf8(
            self.content,
            label="conversation retrieval request content",
            nonempty=True,
        )
        if type(self.context) is not CollaborationReadableContext:
            raise TypeError(
                "conversation retrieval context must be a CollaborationReadableContext"
            )
        bootstrap = self.context.bootstrap
        if bootstrap.session_id is not None and (
            bootstrap.session_id != self.session_id
            or bootstrap.conversation_id != self.conversation_id
        ):
            raise ValueError("conversation retrieval context crossed its history")
        if self.story_session_context is not None:
            if type(self.story_session_context) is not StorySessionContext:
                raise TypeError(
                    "story_session_context must be a StorySessionContext or None"
                )
            if (
                self.story_session_context.session_id != self.session_id
                or self.story_session_context.agent_id != self.agent_id
                or (
                    self.story_session_context.focused_context.conversation_id
                    is not None
                    and self.story_session_context.focused_context.conversation_id
                    != self.conversation_id
                )
            ):
                raise ValueError("story-session context request identity mismatch")
        if self.rag_context is not None:
            if type(self.rag_context) is not ConversationRagContext:
                raise TypeError(
                    "conversation retrieval RAG context must use the exact type"
                )
            if any(
                excerpt.reference.document.key.session_id != self.session_id
                for excerpt in self.rag_context.excerpts
            ):
                raise ValueError(
                    "conversation retrieval RAG context crossed its session"
                )


def _validate_action_common(
    *,
    kind: ConversationRetrievalActionKind,
    expected_kind: ConversationRetrievalActionKind,
    session_id: str,
    conversation_id: str,
    turn_id: str,
    action_index: int,
) -> None:
    """Validate one action discriminator, correlation, and positive index."""

    if type(kind) is not ConversationRetrievalActionKind:
        raise TypeError("conversation retrieval action kind is invalid")
    if kind is not expected_kind:
        raise ValueError(f"{expected_kind.value} action must use its matching kind")
    _identifier(session_id, label="session ID")
    _identifier(conversation_id, label="conversation ID")
    _identifier(turn_id, label="turn ID")
    _positive_integer(action_index, label="action index")


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationRetrievalSearchAction:
    """Request one active-only literal conversation search."""

    kind: ConversationRetrievalActionKind
    session_id: str
    conversation_id: str
    turn_id: str
    action_index: int
    query: str
    trace: ReplyTrace | None = None
    validation_details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate shape and text without authorizing or performing a search."""

        _validate_action_common(
            kind=self.kind,
            expected_kind=ConversationRetrievalActionKind.SEARCH_CONVERSATIONS,
            session_id=self.session_id,
            conversation_id=self.conversation_id,
            turn_id=self.turn_id,
            action_index=self.action_index,
        )
        _strict_utf8(
            self.query,
            label="conversation retrieval search query",
            nonempty=True,
        )
        if self.trace is not None and type(self.trace) is not ReplyTrace:
            raise TypeError("retrieval search trace must be ReplyTrace or None")
        validate_details(self.validation_details)


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationRetrievalReadAction:
    """Request a bounded readable-history range from a field citation."""

    kind: ConversationRetrievalActionKind
    session_id: str
    conversation_id: str
    turn_id: str
    action_index: int
    citation_id: str
    start_sequence: int
    end_sequence: int | None
    trace: ReplyTrace | None = None
    validation_details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate shape and range without authorizing or reading history."""

        _validate_action_common(
            kind=self.kind,
            expected_kind=ConversationRetrievalActionKind.READ_HISTORY,
            session_id=self.session_id,
            conversation_id=self.conversation_id,
            turn_id=self.turn_id,
            action_index=self.action_index,
        )
        _identifier(self.citation_id, label="citation ID")
        _positive_integer(self.start_sequence, label="start sequence")
        if self.end_sequence is not None:
            _positive_integer(self.end_sequence, label="end sequence")
        if self.end_sequence is not None and self.end_sequence < self.start_sequence:
            raise ValueError("end sequence must not precede start sequence")
        if self.trace is not None and type(self.trace) is not ReplyTrace:
            raise TypeError("retrieval read trace must be ReplyTrace or None")
        validate_details(self.validation_details)


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationRetrievalAnswerAction:
    """Return one terminal visible answer and ordered supplied citation IDs."""

    kind: ConversationRetrievalActionKind
    session_id: str
    conversation_id: str
    turn_id: str
    action_index: int
    content: str
    citation_ids: tuple[str, ...]
    trace: ReplyTrace | None = None
    validation_details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate answer shape without resolving citation authorization."""

        _validate_action_common(
            kind=self.kind,
            expected_kind=ConversationRetrievalActionKind.ANSWER,
            session_id=self.session_id,
            conversation_id=self.conversation_id,
            turn_id=self.turn_id,
            action_index=self.action_index,
        )
        _strict_utf8(
            self.content,
            label="conversation retrieval answer content",
            nonempty=True,
        )
        if type(self.citation_ids) is not tuple:
            raise TypeError("answer citation IDs must be a tuple")
        seen: set[str] = set()
        for citation_id in self.citation_ids:
            _identifier(citation_id, label="answer citation ID")
            if citation_id in seen:
                raise ValueError("answer citation IDs must be unique")
            seen.add(citation_id)
        if self.trace is not None and type(self.trace) is not ReplyTrace:
            raise TypeError("retrieval answer trace must be ReplyTrace or None")
        validate_details(self.validation_details)


ConversationRetrievalAction: TypeAlias = (
    ConversationRetrievalSearchAction
    | ConversationRetrievalReadAction
    | ConversationRetrievalAnswerAction
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationRetrievalSource:
    """One exact canonical event-field citation supplied to an agent."""

    citation: CollaborationHistoryCitation

    def __post_init__(self) -> None:
        """Validate the citation without reading authoritative history."""

        canonical = _validated_citation(
            self.citation,
            label="conversation retrieval source citation",
        )
        object.__setattr__(self, "citation", canonical)

    def to_dict(self) -> dict[str, object]:
        """Return the exact JSON-compatible source shape without mutation."""

        return {"citation": _citation_to_dict(self.citation)}


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationRetrievalSearchItem:
    """One provider-visible event-field search hit with a local citation ID."""

    citation_id: str
    source: ConversationRetrievalSource
    snippet: str

    def __post_init__(self) -> None:
        """Validate one hit shape without performing search or persistence."""

        _identifier(self.citation_id, label="search citation ID")
        if type(self.source) is not ConversationRetrievalSource:
            raise TypeError("search item source must be a ConversationRetrievalSource")
        _strict_utf8(
            self.snippet,
            label="conversation retrieval search snippet",
            nonempty=True,
        )

    def to_dict(self) -> dict[str, object]:
        """Return the exact JSON-compatible search-item shape without mutation."""

        return {
            "citation_id": self.citation_id,
            "source": self.source.to_dict(),
            "snippet": self.snippet,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationRetrievalHistoryRecord:
    """One structured readable record with one citation ID per closed field."""

    record: CollaborationReadableRecord
    field_citation_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        """Validate closed readable data without exposing the full payload."""

        if type(self.record) is not CollaborationReadableRecord:
            raise TypeError("history record must be a CollaborationReadableRecord")
        if type(self.field_citation_ids) is not tuple or len(
            self.field_citation_ids
        ) != len(self.record.fields):
            raise TypeError("history field citation IDs must match readable fields")
        for citation_id in self.field_citation_ids:
            _identifier(citation_id, label="history field citation ID")
        if len(set(self.field_citation_ids)) != len(self.field_citation_ids):
            raise ValueError("history field citation IDs must be unique")

    def to_dict(self) -> dict[str, object]:
        """Return identity facts plus ordered closed readable fields only."""

        record = self.record
        return {
            "schema_version": record.schema_version,
            "session_id": record.session_id,
            "conversation_id": record.conversation_id,
            "turn_id": record.turn_id,
            "step_id": record.step_id,
            "event_id": record.event_id,
            "batch_id": record.batch_id,
            "batch_ordinal": record.batch_ordinal,
            "batch_size": record.batch_size,
            "batch_digest": record.batch_digest,
            "sequence": record.sequence,
            "timestamp": record.timestamp,
            "sender": {
                "kind": record.sender.kind.value,
                "agent_id": record.sender.agent_id,
            },
            "recipient": {
                "kind": record.recipient.kind.value,
                "agent_id": record.recipient.agent_id,
            },
            "direction": record.direction.value,
            "kind": record.kind.value,
            "fields": [
                {
                    "citation_id": citation_id,
                    "key": field.key.value,
                    "text": field.text,
                    "assignment_ordinal": field.assignment_ordinal,
                    "input_ordinal": field.input_ordinal,
                }
                for field, citation_id in zip(
                    record.fields,
                    self.field_citation_ids,
                    strict=True,
                )
            ],
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationRetrievalSearchObservation:
    """One bounded provider-visible search observation."""

    session_id: str
    conversation_id: str
    turn_id: str
    action_index: int
    hits: tuple[ConversationRetrievalSearchItem, ...]
    truncated: bool

    def __post_init__(self) -> None:
        """Validate correlation, deterministic IDs, bounds, and safe hit sources."""

        _identifier(self.session_id, label="session ID")
        _identifier(self.conversation_id, label="conversation ID")
        _identifier(self.turn_id, label="turn ID")
        _positive_integer(self.action_index, label="action index")
        if self.action_index > CONVERSATION_RETRIEVAL_POLICY.max_nonterminal_actions:
            raise ValueError("search observation action index exceeds the policy")
        if type(self.hits) is not tuple or any(
            type(hit) is not ConversationRetrievalSearchItem for hit in self.hits
        ):
            raise TypeError(
                "search observation hits must be a tuple of "
                "ConversationRetrievalSearchItem"
            )
        if len(self.hits) > CONVERSATION_RETRIEVAL_POLICY.max_search_hits_per_action:
            raise ValueError("search observation exceeds the per-action hit limit")
        for ordinal, hit in enumerate(self.hits, start=1):
            if hit.citation_id != f"s{self.action_index}h{ordinal}":
                raise ValueError("search observation citation IDs are inconsistent")
            if hit.source.citation.session_id != self.session_id:
                raise ValueError("search observation source crossed its session")
        _boolean(self.truncated, label="search observation truncated")

    def to_dict(self) -> dict[str, object]:
        """Return the exact typed search-observation dictionary."""

        return {
            "kind": ConversationRetrievalActionKind.SEARCH_CONVERSATIONS.value,
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "action_index": self.action_index,
            "hits": [hit.to_dict() for hit in self.hits],
            "truncated": self.truncated,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationRetrievalReadObservation:
    """One bounded provider-visible structured history observation."""

    session_id: str
    conversation_id: str
    turn_id: str
    action_index: int
    source_citation_id: str
    source: ConversationRetrievalSource
    records: tuple[ConversationRetrievalHistoryRecord, ...]
    truncated: bool

    def __post_init__(self) -> None:
        """Validate correlation, source, deterministic IDs, and complete pairs."""

        _identifier(self.session_id, label="session ID")
        _identifier(self.conversation_id, label="conversation ID")
        _identifier(self.turn_id, label="turn ID")
        _positive_integer(self.action_index, label="action index")
        if self.action_index > CONVERSATION_RETRIEVAL_POLICY.max_nonterminal_actions:
            raise ValueError("read observation action index exceeds the policy")
        _identifier(self.source_citation_id, label="source citation ID")
        if type(self.source) is not ConversationRetrievalSource:
            raise TypeError(
                "read observation source must be a ConversationRetrievalSource"
            )
        if self.source.citation.session_id != self.session_id:
            raise ValueError("read observation source crossed its session")
        if type(self.records) is not tuple or any(
            type(record) is not ConversationRetrievalHistoryRecord
            for record in self.records
        ):
            raise TypeError(
                "read observation records must be a tuple of "
                "ConversationRetrievalHistoryRecord"
            )
        if len(self.records) > CONVERSATION_RETRIEVAL_POLICY.max_history_records:
            raise ValueError("read observation exceeds the history record limit")
        previous_sequence = 0
        for record_ordinal, item in enumerate(self.records, start=1):
            record = item.record
            if (
                record.session_id != self.session_id
                or record.conversation_id != self.source.citation.conversation_id
            ):
                raise ValueError("read observation record crossed its source")
            if record.sequence <= previous_sequence:
                raise ValueError(
                    "read observation record sequences must be strictly increasing"
                )
            previous_sequence = record.sequence
            expected_ids = tuple(
                f"r{self.action_index}e{record_ordinal}f{field_ordinal}"
                for field_ordinal in range(1, len(record.fields) + 1)
            )
            if item.field_citation_ids != expected_ids:
                raise ValueError("read observation citation IDs are inconsistent")
        _boolean(self.truncated, label="read observation truncated")

    def to_dict(self) -> dict[str, object]:
        """Return the exact typed read-observation dictionary."""

        return {
            "kind": ConversationRetrievalActionKind.READ_HISTORY.value,
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "action_index": self.action_index,
            "source_citation_id": self.source_citation_id,
            "source": self.source.to_dict(),
            "records": [record.to_dict() for record in self.records],
            "truncated": self.truncated,
        }


ConversationRetrievalObservation: TypeAlias = (
    ConversationRetrievalSearchObservation | ConversationRetrievalReadObservation
)


def _validate_sources(
    sources: tuple[ConversationRetrievalSource, ...],
    *,
    session_id: str,
) -> None:
    """Validate exact unique same-session sources without external access."""

    if type(sources) is not tuple or any(
        type(source) is not ConversationRetrievalSource for source in sources
    ):
        raise TypeError(
            "conversation retrieval sources must be a tuple of "
            "ConversationRetrievalSource"
        )
    seen: set[ConversationRetrievalSource] = set()
    for source in sources:
        if source.citation.session_id != session_id:
            raise ValueError("conversation retrieval source crossed its session")
        if source in seen:
            raise ValueError("conversation retrieval sources must be unique")
        seen.add(source)


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationRetrievalReply:
    """One correlated terminal answer with canonical supplied sources."""

    session_id: str
    conversation_id: str
    turn_id: str
    content: str
    sources: tuple[ConversationRetrievalSource, ...]
    trace: ReplyTrace | None = None
    validation_details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate answer correlation, content, and source set without I/O."""

        _identifier(self.session_id, label="session ID")
        _identifier(self.conversation_id, label="conversation ID")
        _identifier(self.turn_id, label="turn ID")
        _strict_utf8(
            self.content,
            label="conversation retrieval reply content",
            nonempty=True,
        )
        _validate_sources(self.sources, session_id=self.session_id)
        if self.trace is not None and type(self.trace) is not ReplyTrace:
            raise TypeError("retrieval reply trace must be ReplyTrace or None")
        validate_details(self.validation_details)


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationRetrievalResult:
    """One successful retrieval result with readable context and exact sources."""

    session_id: str
    conversation_id: str
    turn_id: str
    agent_id: str
    content: str
    context: CollaborationReadableContext
    sources: tuple[ConversationRetrievalSource, ...]
    reference_operation_id: str | None = None
    rag_outcome: ConversationRagAttemptOutcome | None = None
    rag_sources: tuple[ConversationRetrievalSource, ...] = ()
    trace: ReplyTrace | None = None
    validation_details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate result identity, context, content, and canonical sources."""

        _identifier(self.session_id, label="session ID")
        _identifier(self.conversation_id, label="conversation ID")
        _identifier(self.turn_id, label="turn ID")
        _identifier(self.agent_id, label="agent ID")
        if self.agent_id not in ROSTER:
            raise ValueError(
                f"conversation retrieval agent is not canonical: {self.agent_id}"
            )
        _strict_utf8(
            self.content,
            label="conversation retrieval result content",
            nonempty=True,
        )
        if type(self.context) is not CollaborationReadableContext:
            raise TypeError(
                "conversation retrieval context must be a CollaborationReadableContext"
            )
        bootstrap = self.context.bootstrap
        if bootstrap.session_id is not None and (
            bootstrap.session_id != self.session_id
            or bootstrap.conversation_id != self.conversation_id
        ):
            raise ValueError("conversation retrieval context crossed its history")
        _validate_sources(self.sources, session_id=self.session_id)
        if self.reference_operation_id is not None and (
            type(self.reference_operation_id) is not str
            or _SAFE_REFERENCE_OPERATION_ID.fullmatch(self.reference_operation_id)
            is None
        ):
            raise ValueError("reference operation ID is invalid")
        if self.rag_outcome is not None and (
            type(self.rag_outcome) is not ConversationRagAttemptOutcome
        ):
            raise TypeError(
                "conversation retrieval RAG outcome must use the exact type"
            )
        _validate_sources(self.rag_sources, session_id=self.session_id)
        if self.rag_sources and self.rag_outcome is None:
            raise ValueError(
                "conversation retrieval RAG sources require an attempted outcome"
            )
        rag_source_set = set(self.rag_sources)
        if (
            tuple(source for source in self.sources if source in rag_source_set)
            != self.rag_sources
        ):
            raise ValueError(
                "conversation retrieval RAG sources must be an ordered result subset"
            )
        if self.trace is not None and type(self.trace) is not ReplyTrace:
            raise TypeError("retrieval result trace must be ReplyTrace or None")
        validate_details(self.validation_details)


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationRetrievalProgressEvent:
    """One trusted nonsensitive search or history-read progress fact."""

    session_id: str
    conversation_id: str
    turn_id: str
    agent_id: str
    action_index: int
    phase: ConversationRetrievalProgressPhase
    search_hit_count: int = 0
    history_record_count: int = 0
    observation_utf8_bytes: int = 0
    truncated: bool = False

    def __post_init__(self) -> None:
        """Enforce exact phase-safe counts without content, I/O, or mutation."""

        _identifier(self.session_id, label="session ID")
        _identifier(self.conversation_id, label="conversation ID")
        _identifier(self.turn_id, label="turn ID")
        _identifier(self.agent_id, label="agent ID")
        if self.agent_id not in ROSTER:
            raise ValueError(
                f"conversation retrieval agent is not canonical: {self.agent_id}"
            )
        _positive_integer(self.action_index, label="action index")
        if self.action_index > CONVERSATION_RETRIEVAL_POLICY.max_nonterminal_actions:
            raise ValueError("progress action index exceeds the retrieval policy")
        if type(self.phase) is not ConversationRetrievalProgressPhase:
            raise TypeError("conversation retrieval progress phase is invalid")
        for value, label in (
            (self.search_hit_count, "search hit count"),
            (self.history_record_count, "history record count"),
            (self.observation_utf8_bytes, "observation UTF-8 bytes"),
        ):
            _non_negative_integer(value, label=label)
        _boolean(self.truncated, label="progress truncated")

        if self.phase in {
            ConversationRetrievalProgressPhase.SEARCH_STARTED,
            ConversationRetrievalProgressPhase.READ_STARTED,
        }:
            if (
                any(
                    (
                        self.search_hit_count,
                        self.history_record_count,
                        self.observation_utf8_bytes,
                    )
                )
                or self.truncated
            ):
                raise ValueError("started retrieval progress must have zero metadata")
            return
        if self.observation_utf8_bytes < 1:
            raise ValueError("completed retrieval progress requires observation bytes")
        if (
            self.observation_utf8_bytes
            > CONVERSATION_RETRIEVAL_POLICY.max_observation_utf8_bytes
        ):
            raise ValueError("progress observation bytes exceed the retrieval policy")
        if self.phase is ConversationRetrievalProgressPhase.SEARCH_COMPLETED:
            if self.history_record_count:
                raise ValueError("search progress cannot carry history counts")
            if (
                self.search_hit_count
                > CONVERSATION_RETRIEVAL_POLICY.max_search_hits_per_action
            ):
                raise ValueError("search progress exceeds the per-action hit limit")
            return
        if self.search_hit_count:
            raise ValueError("read progress cannot carry a search hit count")
        if (
            self.history_record_count
            > CONVERSATION_RETRIEVAL_POLICY.max_history_records
        ):
            raise ValueError("read progress exceeds the history policy")


ConversationRetrievalProgressSink: TypeAlias = Callable[
    [ConversationRetrievalProgressEvent], None
]


class ConversationRetrievalActionHandler(Protocol):
    """Synchronous runtime-owned authorization for decoded retrieval actions."""

    def handle_conversation_retrieval_action(
        self,
        action: ConversationRetrievalAction,
    ) -> ConversationRetrievalObservation | ConversationRetrievalReply:
        """Authorize one action and return its observation or terminal reply."""

        ...


def serialize_conversation_retrieval_observation(
    observation: ConversationRetrievalObservation,
) -> str:
    """Frame one exact observation as canonical untrusted story data.

    The built-in search or history observation is serialized with sorted compact
    JSON and exact delimiters. No I/O or mutation occurs; subclasses and other
    values raise ``TypeError`` and invalid text is rejected by the contracts.
    """

    if type(observation) not in {
        ConversationRetrievalSearchObservation,
        ConversationRetrievalReadObservation,
    }:
        raise TypeError(
            "observation must be an exact conversation retrieval observation"
        )
    payload = json.dumps(
        observation.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{_BEGIN_UNTRUSTED_STORY_DATA}\n{payload}\n{_END_UNTRUSTED_STORY_DATA}"


__all__ = [
    "CONVERSATION_RETRIEVAL_POLICY",
    "ConversationRagAttemptOutcome",
    "ConversationRagContext",
    "ConversationRagExcerpt",
    "ConversationRetrievalAction",
    "ConversationRetrievalActionHandler",
    "ConversationRetrievalActionKind",
    "ConversationRetrievalAnswerAction",
    "ConversationRetrievalHistoryRecord",
    "ConversationRetrievalObservation",
    "ConversationRetrievalPolicy",
    "ConversationRetrievalProgressEvent",
    "ConversationRetrievalProgressPhase",
    "ConversationRetrievalProgressSink",
    "ConversationRetrievalReadAction",
    "ConversationRetrievalReadObservation",
    "ConversationRetrievalReply",
    "ConversationRetrievalRequest",
    "ConversationRetrievalResult",
    "ConversationRetrievalSearchAction",
    "ConversationRetrievalSearchItem",
    "ConversationRetrievalSearchObservation",
    "ConversationRetrievalSource",
    "serialize_conversation_retrieval_observation",
]
