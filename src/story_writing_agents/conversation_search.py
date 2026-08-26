"""Provider-neutral deterministic conversation search and cited transcript reads."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Iterator, Protocol

from .conversation_literal_search import (
    build_conversation_literal_snippet,
    classify_conversation_literal_subject,
    conversation_literal_sort_key,
    normalize_conversation_literal_query,
    normalize_conversation_literal_text,
)
from .conversations import (
    ConversationCatalog,
    ConversationEvent,
    ConversationEventKind,
    ConversationEventStore,
    ConversationMetadata,
    ConversationStatus,
    normalize_conversation_subject,
)
from .errors import SessionIdError


_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def _positive_integer(value: int, *, label: str, maximum: int | None = None) -> int:
    """Return an exact positive integer within an optional inclusive maximum.

    Booleans and non-integers raise ``TypeError``; range violations raise
    ``ValueError`` without mutation.
    """

    if type(value) is not int:
        raise TypeError(f"{label} must be an integer")
    if value < 1:
        raise ValueError(f"{label} must be positive")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} must not exceed {maximum}")
    return value


def _non_negative_integer(value: int, *, label: str) -> int:
    """Return an exact non-negative integer without mutation.

    Booleans and non-integers raise ``TypeError``; negative values raise
    ``ValueError``.
    """

    if type(value) is not int:
        raise TypeError(f"{label} must be an integer")
    if value < 0:
        raise ValueError(f"{label} must not be negative")
    return value


def _boolean(value: bool, *, label: str) -> bool:
    """Return an exact boolean or raise ``TypeError`` without mutation."""

    if type(value) is not bool:
        raise TypeError(f"{label} must be a boolean")
    return value


def _identifier(value: str, *, label: str) -> str:
    """Return an exact-string safe identifier without mutation.

    Non-strings raise ``TypeError`` and syntax violations raise
    ``SessionIdError``.
    """

    if type(value) is not str:
        raise TypeError(f"{label} must be a string")
    if _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise SessionIdError(f"{label} must match [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}")
    return value


def _strict_utf8(value: str, *, label: str) -> str:
    """Return an exact string that encodes as strict UTF-8.

    Non-strings raise ``TypeError`` and unencodable strings raise ``ValueError``;
    no normalization or mutation is performed.
    """

    if type(value) is not str:
        raise TypeError(f"{label} must be a string")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8") from exc
    return value


def _canonical_utc_timestamp(value: str, *, label: str) -> datetime:
    """Parse an exact canonical ISO-8601 UTC timestamp without mutation.

    Invalid UTF-8, syntax, offset, or noncanonical spellings raise
    ``TypeError`` or ``ValueError``.
    """

    _strict_utf8(value, label=label)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError(f"{label} must use UTC")
    if parsed.isoformat() != value:
        raise ValueError(f"{label} must use canonical ISO-8601 UTC format")
    return parsed


def _has_control_or_format(value: str) -> bool:
    """Report whether any character belongs to a Unicode ``C`` category."""

    return any(unicodedata.category(character).startswith("C") for character in value)


def _normalized_query(value: str) -> str:
    """Return the bounded normalized literal used by conversation search.

    The source must be strict UTF-8, nonblank, within policy, and free of
    Unicode ``C`` characters; the normalized result is checked again for
    content and length. Violations raise ``TypeError`` or ``ValueError``
    without external reads or mutation.
    """

    return normalize_conversation_literal_query(
        value,
        max_characters=CONVERSATION_MEMORY_POLICY.max_query_characters,
    )


@dataclass(frozen=True, slots=True)
class ConversationMemoryPolicy:
    """Fixed validation, search-work, snippet, and transcript-page limits."""

    max_query_characters: int = 200
    default_search_hits: int = 10
    max_search_hits: int = 50
    max_scan_conversations: int = 256
    max_scan_events: int = 10_000
    max_snippet_characters: int = 240
    max_snippet_utf8_bytes: int = 1_024
    default_page_events: int = 24
    max_page_events: int = 100
    max_page_utf8_bytes: int = 32_768

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            _positive_integer(
                getattr(self, field_name),
                label=field_name.replace("_", " "),
            )
        if self.default_search_hits > self.max_search_hits:
            raise ValueError("default search hits must not exceed maximum search hits")
        if self.default_page_events > self.max_page_events:
            raise ValueError("default page events must not exceed maximum page events")
        if self.max_query_characters > self.max_snippet_characters:
            raise ValueError(
                "maximum query characters must not exceed snippet characters"
            )
        if self.max_query_characters * 4 > self.max_snippet_utf8_bytes:
            raise ValueError(
                "maximum query UTF-8 size must fit within the snippet byte limit"
            )


CONVERSATION_MEMORY_POLICY = ConversationMemoryPolicy()


class ConversationSearchMatchKind(StrEnum):
    """The strongest deterministic match found for one conversation."""

    EXACT_SUBJECT = "exact_subject"
    SUBJECT_PREFIX = "subject_prefix"
    SUBJECT_SUBSTRING = "subject_substring"
    TRANSCRIPT = "transcript"


@dataclass(frozen=True, slots=True)
class ConversationSearchScope:
    """One current story session explicitly authorized for memory access."""

    session_id: str

    def __post_init__(self) -> None:
        _identifier(self.session_id, label="session ID")


@dataclass(frozen=True, slots=True)
class ConversationSearchQuery:
    """A normalized-literal search request within one authorized scope."""

    text: str
    limit: int = CONVERSATION_MEMORY_POLICY.default_search_hits
    include_archived: bool = False

    def __post_init__(self) -> None:
        _normalized_query(self.text)
        _positive_integer(
            self.limit,
            label="conversation search hit limit",
            maximum=CONVERSATION_MEMORY_POLICY.max_search_hits,
        )
        _boolean(self.include_archived, label="include archived")


@dataclass(frozen=True, slots=True)
class ConversationCitation:
    """Stable conversation identity with an optional exact event reference."""

    session_id: str
    conversation_id: str
    turn_id: str | None
    sequence: int | None
    event_kind: ConversationEventKind | None

    def __post_init__(self) -> None:
        _identifier(self.session_id, label="session ID")
        _identifier(self.conversation_id, label="conversation ID")
        event_values = (self.turn_id, self.sequence, self.event_kind)
        if all(value is None for value in event_values):
            return
        if any(value is None for value in event_values):
            raise ValueError(
                "citation turn ID, sequence, and event kind must be present together"
            )
        _identifier(self.turn_id, label="turn ID")  # type: ignore[arg-type]
        _positive_integer(
            self.sequence,  # type: ignore[arg-type]
            label="event sequence",
        )
        if type(self.event_kind) is not ConversationEventKind:
            raise TypeError("citation event kind must be a ConversationEventKind")


@dataclass(frozen=True, slots=True)
class ConversationSearchHit:
    """One strongest bounded match for one immutable conversation identity."""

    citation: ConversationCitation
    subject: str
    subject_revision: int
    status: ConversationStatus
    updated_at: str
    match_kind: ConversationSearchMatchKind
    snippet: str

    def __post_init__(self) -> None:
        if type(self.citation) is not ConversationCitation:
            raise TypeError("search hit citation must be a ConversationCitation")
        _strict_utf8(self.subject, label="conversation subject")
        normalize_conversation_subject(self.subject)
        _positive_integer(self.subject_revision, label="subject revision")
        if type(self.status) is not ConversationStatus:
            raise TypeError("search hit status must be a ConversationStatus")
        _canonical_utc_timestamp(self.updated_at, label="updated_at")
        if type(self.match_kind) is not ConversationSearchMatchKind:
            raise TypeError(
                "search hit match kind must be a ConversationSearchMatchKind"
            )
        snippet = _strict_utf8(self.snippet, label="conversation search snippet")
        if not snippet:
            raise ValueError("conversation search snippet must not be empty")
        if _has_control_or_format(snippet):
            raise ValueError(
                "conversation search snippet must not contain control or "
                "format characters"
            )
        if len(snippet) > CONVERSATION_MEMORY_POLICY.max_snippet_characters:
            raise ValueError("conversation search snippet exceeds the character limit")
        if (
            len(snippet.encode("utf-8"))
            > CONVERSATION_MEMORY_POLICY.max_snippet_utf8_bytes
        ):
            raise ValueError("conversation search snippet exceeds the UTF-8 byte limit")
        if self.match_kind is ConversationSearchMatchKind.TRANSCRIPT:
            if self.citation.sequence is None:
                raise ValueError("transcript search hit requires an event citation")
            if self.citation.event_kind not in {
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
            }:
                raise ValueError("transcript search hit must cite visible prose")
        elif self.citation.sequence is not None:
            raise ValueError("subject search hit must use a conversation-only citation")


def _hit_sort_key(hit: ConversationSearchHit) -> tuple[int, str, str]:
    """Return the stable match-rank, normalized-subject, and identity key."""

    return conversation_literal_sort_key(
        hit.match_kind.value,
        normalize_conversation_subject(hit.subject),
        hit.citation.conversation_id,
    )


@dataclass(frozen=True, slots=True)
class ConversationSearchResult:
    """Bounded deterministic matches and explicit scan truncation metadata."""

    query: ConversationSearchQuery
    normalized_query: str
    hits: tuple[ConversationSearchHit, ...]
    matched_conversations: int
    scanned_conversations: int
    scanned_events: int
    scan_truncated: bool
    truncated: bool

    def __post_init__(self) -> None:
        if type(self.query) is not ConversationSearchQuery:
            raise TypeError("search result query must be a ConversationSearchQuery")
        normalized = _strict_utf8(
            self.normalized_query,
            label="normalized conversation search query",
        )
        if normalized != _normalized_query(self.query.text):
            raise ValueError("normalized conversation search query is inconsistent")
        if type(self.hits) is not tuple or any(
            type(hit) is not ConversationSearchHit for hit in self.hits
        ):
            raise TypeError(
                "search result hits must be a tuple of ConversationSearchHit"
            )
        _non_negative_integer(
            self.matched_conversations,
            label="matched conversations",
        )
        _non_negative_integer(
            self.scanned_conversations,
            label="scanned conversations",
        )
        _non_negative_integer(self.scanned_events, label="scanned events")
        if (
            self.scanned_conversations
            > CONVERSATION_MEMORY_POLICY.max_scan_conversations
        ):
            raise ValueError("scanned conversations exceed the fixed policy")
        if self.scanned_events > CONVERSATION_MEMORY_POLICY.max_scan_events:
            raise ValueError("scanned events exceed the fixed policy")
        _boolean(self.scan_truncated, label="scan truncated")
        _boolean(self.truncated, label="truncated")
        if len(self.hits) > self.query.limit:
            raise ValueError("search result contains more hits than requested")
        if self.matched_conversations < len(self.hits):
            raise ValueError("matched conversation count is inconsistent")
        if self.matched_conversations > self.scanned_conversations:
            raise ValueError("matched conversations exceed scanned conversations")
        if self.truncated is not (
            self.scan_truncated or self.matched_conversations > len(self.hits)
        ):
            raise ValueError("search result truncated flag is inconsistent")
        if tuple(sorted(self.hits, key=_hit_sort_key)) != self.hits:
            raise ValueError("search result hits are not in deterministic order")
        identities = {
            (hit.citation.session_id, hit.citation.conversation_id) for hit in self.hits
        }
        if len(identities) != len(self.hits):
            raise ValueError("search result contains duplicate conversations")


@dataclass(frozen=True, slots=True)
class ConversationTranscriptCursor:
    """Stable forward continuation within one snapshotted transcript range."""

    session_id: str
    conversation_id: str
    next_sequence: int
    end_sequence: int
    snapshot_last_sequence: int
    turn_id: str | None
    max_events: int
    citation: ConversationCitation | None

    def __post_init__(self) -> None:
        _identifier(self.session_id, label="session ID")
        _identifier(self.conversation_id, label="conversation ID")
        _positive_integer(self.next_sequence, label="next sequence")
        _positive_integer(self.end_sequence, label="end sequence")
        _positive_integer(
            self.snapshot_last_sequence,
            label="snapshot last sequence",
        )
        if self.next_sequence > self.end_sequence:
            raise ValueError("cursor next sequence exceeds its range")
        if self.end_sequence > self.snapshot_last_sequence:
            raise ValueError("cursor range exceeds its transcript snapshot")
        if self.turn_id is not None:
            _identifier(self.turn_id, label="turn ID")
        _positive_integer(
            self.max_events,
            label="transcript page event limit",
            maximum=CONVERSATION_MEMORY_POLICY.max_page_events,
        )
        if self.citation is not None:
            if type(self.citation) is not ConversationCitation:
                raise TypeError("cursor citation must be a ConversationCitation")
            if (
                self.citation.session_id != self.session_id
                or self.citation.conversation_id != self.conversation_id
            ):
                raise ValueError("cursor citation does not match its transcript")
            if (
                self.citation.sequence is not None
                and self.citation.sequence > self.snapshot_last_sequence
            ):
                raise ValueError("cursor citation exceeds its transcript snapshot")


@dataclass(frozen=True, slots=True)
class ConversationTranscriptPage:
    """One bounded event-atomic chronological transcript page."""

    session_id: str
    conversation_id: str
    events: tuple[ConversationEvent, ...]
    requested_start_sequence: int
    requested_end_sequence: int
    snapshot_last_sequence: int
    returned_utf8_bytes: int
    next_cursor: ConversationTranscriptCursor | None
    truncated: bool

    def __post_init__(self) -> None:
        _identifier(self.session_id, label="session ID")
        _identifier(self.conversation_id, label="conversation ID")
        if type(self.events) is not tuple or any(
            type(event) is not ConversationEvent for event in self.events
        ):
            raise TypeError(
                "transcript page events must be a tuple of ConversationEvent"
            )
        if len(self.events) > CONVERSATION_MEMORY_POLICY.max_page_events:
            raise ValueError("transcript page exceeds the fixed event limit")
        _positive_integer(
            self.requested_start_sequence,
            label="requested start sequence",
        )
        _non_negative_integer(
            self.requested_end_sequence,
            label="requested end sequence",
        )
        _non_negative_integer(
            self.snapshot_last_sequence,
            label="snapshot last sequence",
        )
        if self.requested_end_sequence > self.snapshot_last_sequence:
            raise ValueError("requested transcript range exceeds its snapshot")
        if self.requested_end_sequence < self.requested_start_sequence and not (
            self.requested_start_sequence == self.snapshot_last_sequence + 1
            and self.requested_end_sequence == self.snapshot_last_sequence
        ):
            raise ValueError("requested transcript range is invalid")
        previous_sequence = 0
        for event in self.events:
            if (
                event.session_id != self.session_id
                or event.conversation_id != self.conversation_id
            ):
                raise ValueError("transcript page event identity is inconsistent")
            if event.sequence <= previous_sequence:
                raise ValueError("transcript page events are not strictly increasing")
            if not (
                self.requested_start_sequence
                <= event.sequence
                <= self.requested_end_sequence
            ):
                raise ValueError(
                    "transcript page event lies outside its requested range"
                )
            previous_sequence = event.sequence
        _non_negative_integer(
            self.returned_utf8_bytes,
            label="returned UTF-8 bytes",
        )
        if self.returned_utf8_bytes != sum(
            len(event.content.encode("utf-8")) for event in self.events
        ):
            raise ValueError("returned UTF-8 byte count is inconsistent")
        if self.returned_utf8_bytes > CONVERSATION_MEMORY_POLICY.max_page_utf8_bytes:
            raise ValueError("transcript page exceeds the fixed UTF-8 byte limit")
        if self.next_cursor is not None:
            if type(self.next_cursor) is not ConversationTranscriptCursor:
                raise TypeError("next cursor must be a ConversationTranscriptCursor")
            if not self.events:
                raise ValueError("an empty transcript page cannot have a cursor")
            if (
                self.next_cursor.session_id != self.session_id
                or self.next_cursor.conversation_id != self.conversation_id
                or self.next_cursor.end_sequence != self.requested_end_sequence
                or self.next_cursor.snapshot_last_sequence
                != self.snapshot_last_sequence
            ):
                raise ValueError("next cursor does not match its transcript page")
            if len(self.events) > self.next_cursor.max_events:
                raise ValueError("transcript page exceeds its requested event limit")
            if self.next_cursor.next_sequence < self.requested_start_sequence:
                raise ValueError("next cursor precedes the requested transcript range")
            if (
                self.events
                and self.next_cursor.next_sequence <= self.events[-1].sequence
            ):
                raise ValueError("next cursor does not follow returned events")
        _boolean(self.truncated, label="transcript page truncated")
        if self.truncated is not (self.next_cursor is not None):
            raise ValueError("transcript page truncated flag is inconsistent")


class ConversationSearch(Protocol):
    """Storage-neutral deterministic conversation search port."""

    def search(
        self,
        scope: ConversationSearchScope,
        query: ConversationSearchQuery,
    ) -> ConversationSearchResult:
        """Search one explicitly scoped current story session."""

        ...


def _subject_match_kind(
    normalized_subject: str,
    normalized_query: str,
) -> ConversationSearchMatchKind | None:
    """Return the strongest literal subject match, or ``None`` when absent.

    Exact, prefix, and substring matches are tested in that precedence order.
    """

    match_kind = classify_conversation_literal_subject(
        normalized_subject,
        normalized_query,
    )
    return None if match_kind is None else ConversationSearchMatchKind(match_kind)


def _validated_metadata(
    metadata: object,
    *,
    session_id: str,
) -> ConversationMetadata:
    """Return exact metadata belonging to the requested session.

    Wrong types raise ``TypeError`` and cross-session entries raise
    ``ValueError`` without mutation.
    """

    if type(metadata) is not ConversationMetadata:
        raise TypeError("catalog entries must be ConversationMetadata")
    if metadata.session_id != session_id:
        raise ValueError("catalog returned a conversation from another session")
    return metadata


def _iter_validated_events(
    value: object,
    *,
    session_id: str,
    conversation_id: str,
    maximum: int | None = None,
) -> Iterator[ConversationEvent]:
    """Yield validated transcript events in contiguous storage order.

    Exact tuple/type/identity and user-before-outcome pairing are enforced.
    ``maximum`` stops before inspecting later events; malformed inspected data
    raises ``TypeError`` or ``ValueError`` without mutation.
    """

    if type(value) is not tuple:
        raise TypeError("event store read must return a tuple of ConversationEvent")
    user_events: dict[str, ConversationEvent] = {}
    completed_turns: set[str] = set()
    for expected_sequence, event in enumerate(value, start=1):
        if maximum is not None and expected_sequence > maximum:
            return
        if type(event) is not ConversationEvent:
            raise TypeError("event store read must return a tuple of ConversationEvent")
        if event.session_id != session_id or event.conversation_id != conversation_id:
            raise ValueError("event store returned an event from another transcript")
        if event.sequence != expected_sequence:
            raise ValueError("event store returned non-contiguous event sequences")
        if event.kind is ConversationEventKind.USER_MESSAGE:
            if event.turn_id in user_events:
                raise ValueError("event store returned duplicate user events")
            user_events[event.turn_id] = event
            yield event
            continue
        user_event = user_events.get(event.turn_id)
        if user_event is None:
            raise ValueError("event store returned an outcome before its user event")
        if event.turn_id in completed_turns:
            raise ValueError("event store returned duplicate turn outcomes")
        if (
            event.kind is ConversationEventKind.AGENT_REPLY
            and event.sender != user_event.recipient
        ):
            raise ValueError(
                "event store returned a reply from the wrong addressed agent"
            )
        completed_turns.add(event.turn_id)
        yield event


def _validated_events(
    value: object,
    *,
    session_id: str,
    conversation_id: str,
) -> tuple[ConversationEvent, ...]:
    """Fully validate and return an identity-bound ordered event tuple."""

    return tuple(
        _iter_validated_events(
            value,
            session_id=session_id,
            conversation_id=conversation_id,
        )
    )


class ReferenceConversationSearch:
    """Dependency-free bounded scan over catalog and event-store ports."""

    def __init__(
        self,
        catalog: ConversationCatalog,
        event_store: ConversationEventStore,
    ) -> None:
        if not callable(getattr(catalog, "list", None)):
            raise TypeError("conversation catalog must provide list()")
        if not callable(getattr(event_store, "read", None)):
            raise TypeError("conversation event store must provide read()")
        self._catalog = catalog
        self._event_store = event_store

    def search(
        self,
        scope: ConversationSearchScope,
        query: ConversationSearchQuery,
    ) -> ConversationSearchResult:
        """Search one session with the supported deterministic reference behavior.

        Normalized literal subject matches take precedence over a bounded scan
        for the first visible transcript match per conversation. The result is
        stably ordered, capped by ``query.limit``, and reports scan and result
        truncation. This ``ConversationSearch``-compatible implementation only
        reads its catalog and event-store ports; invalid adapter contracts raise
        ``TypeError`` or ``ValueError``.
        """

        if type(scope) is not ConversationSearchScope:
            raise TypeError(
                "conversation search scope must be a ConversationSearchScope"
            )
        if type(query) is not ConversationSearchQuery:
            raise TypeError(
                "conversation search query must be a ConversationSearchQuery"
            )

        normalized_query = _normalized_query(query.text)
        raw_metadata = self._catalog.list(
            scope.session_id,
            include_archived=query.include_archived,
        )
        if type(raw_metadata) is not tuple:
            raise TypeError("catalog list must return a tuple of ConversationMetadata")
        metadata = tuple(
            sorted(
                (
                    _validated_metadata(item, session_id=scope.session_id)
                    for item in raw_metadata
                ),
                key=lambda item: (item.created_at, item.conversation_id),
            )
        )
        identities = {item.conversation_id for item in metadata}
        if len(identities) != len(metadata):
            raise ValueError("catalog returned duplicate conversation identities")
        if not query.include_archived and any(
            item.status is ConversationStatus.ARCHIVED for item in metadata
        ):
            raise ValueError("catalog returned archived metadata without authorization")

        scan_truncated = (
            len(metadata) > CONVERSATION_MEMORY_POLICY.max_scan_conversations
        )
        selected_metadata = metadata[
            : CONVERSATION_MEMORY_POLICY.max_scan_conversations
        ]
        hits: list[ConversationSearchHit] = []
        scanned_events = 0
        event_budget_exhausted = False

        for item in selected_metadata:
            normalized_subject = normalize_conversation_subject(item.subject)
            match_kind = _subject_match_kind(normalized_subject, normalized_query)
            if match_kind is not None:
                hits.append(
                    ConversationSearchHit(
                        citation=ConversationCitation(
                            item.session_id,
                            item.conversation_id,
                            None,
                            None,
                            None,
                        ),
                        subject=item.subject,
                        subject_revision=item.subject_revision,
                        status=item.status,
                        updated_at=item.updated_at,
                        match_kind=match_kind,
                        snippet=build_conversation_literal_snippet(
                            normalize_conversation_literal_text(item.subject),
                            normalized_query,
                            max_characters=(
                                CONVERSATION_MEMORY_POLICY.max_snippet_characters
                            ),
                            max_utf8_bytes=(
                                CONVERSATION_MEMORY_POLICY.max_snippet_utf8_bytes
                            ),
                        ),
                    )
                )
                continue

            if event_budget_exhausted:
                scan_truncated = True
                continue
            raw_events = self._event_store.read(
                item.session_id,
                item.conversation_id,
            )
            if type(raw_events) is not tuple:
                raise TypeError(
                    "event store read must return a tuple of ConversationEvent"
                )
            remaining_event_budget = (
                CONVERSATION_MEMORY_POLICY.max_scan_events - scanned_events
            )
            matched_event: ConversationEvent | None = None
            matched_text = ""
            examined_in_conversation = 0
            for event in _iter_validated_events(
                raw_events,
                session_id=item.session_id,
                conversation_id=item.conversation_id,
                maximum=remaining_event_budget,
            ):
                examined_in_conversation += 1
                scanned_events += 1
                if event.kind is ConversationEventKind.RUNTIME_ERROR:
                    continue
                searchable = normalize_conversation_literal_text(event.content)
                if normalized_query in searchable:
                    matched_event = event
                    matched_text = searchable
                    break
            if matched_event is None and examined_in_conversation < len(raw_events):
                scan_truncated = True
            if scanned_events == CONVERSATION_MEMORY_POLICY.max_scan_events:
                event_budget_exhausted = True
            if matched_event is not None:
                hits.append(
                    ConversationSearchHit(
                        citation=ConversationCitation(
                            matched_event.session_id,
                            matched_event.conversation_id,
                            matched_event.turn_id,
                            matched_event.sequence,
                            matched_event.kind,
                        ),
                        subject=item.subject,
                        subject_revision=item.subject_revision,
                        status=item.status,
                        updated_at=item.updated_at,
                        match_kind=ConversationSearchMatchKind.TRANSCRIPT,
                        snippet=build_conversation_literal_snippet(
                            matched_text,
                            normalized_query,
                            max_characters=(
                                CONVERSATION_MEMORY_POLICY.max_snippet_characters
                            ),
                            max_utf8_bytes=(
                                CONVERSATION_MEMORY_POLICY.max_snippet_utf8_bytes
                            ),
                        ),
                    )
                )

        ordered_hits = tuple(sorted(hits, key=_hit_sort_key))
        returned_hits = ordered_hits[: query.limit]
        return ConversationSearchResult(
            query=query,
            normalized_query=normalized_query,
            hits=returned_hits,
            matched_conversations=len(ordered_hits),
            scanned_conversations=len(selected_metadata),
            scanned_events=scanned_events,
            scan_truncated=scan_truncated,
            truncated=scan_truncated or len(ordered_hits) > len(returned_hits),
        )


class _ConversationStorageServicesLike(Protocol):
    """Private structural view that avoids a storage-to-search import cycle."""

    @property
    def catalog(self) -> ConversationCatalog: ...

    @property
    def event_store(self) -> ConversationEventStore: ...

    @property
    def search(self) -> ConversationSearch: ...

    @property
    def mutation_coordinator(self) -> object: ...


def _storage_service_components(
    value: _ConversationStorageServicesLike,
) -> tuple[ConversationCatalog, ConversationEventStore, ConversationSearch]:
    """Validate the narrow immutable bundle surface without importing storage."""

    try:
        catalog = value.catalog
        event_store = value.event_store
        search = value.search
        coordinator = value.mutation_coordinator
    except AttributeError as exc:
        raise TypeError("conversation storage services are invalid") from exc
    if any(
        not callable(getattr(catalog, name, None))
        for name in ("create", "get", "list", "rename", "archive", "unarchive")
    ):
        raise TypeError("conversation storage catalog is invalid")
    if any(
        not callable(getattr(event_store, name, None))
        for name in ("append", "read", "read_context")
    ):
        raise TypeError("conversation storage event store is invalid")
    if not callable(getattr(search, "search", None)):
        raise TypeError("conversation storage search is invalid")
    if not callable(getattr(coordinator, "borrow", None)):
        raise TypeError("conversation storage coordinator is invalid")
    return catalog, event_store, search


class ConversationMemoryService:
    """Authorize one session and coordinate search with bounded cited reads."""

    def __init__(
        self,
        scope: ConversationSearchScope,
        catalog: ConversationCatalog | None = None,
        event_store: ConversationEventStore | None = None,
        *,
        search: ConversationSearch | None = None,
        conversation_storage_services: _ConversationStorageServicesLike | None = None,
    ) -> None:
        if type(scope) is not ConversationSearchScope:
            raise TypeError(
                "conversation search scope must be a ConversationSearchScope"
            )
        if conversation_storage_services is not None:
            if catalog is not None or event_store is not None or search is not None:
                raise TypeError(
                    "conversation_storage_services conflicts with raw collaborators"
                )
            catalog, event_store, search = _storage_service_components(
                conversation_storage_services
            )
        elif catalog is None or event_store is None:
            raise TypeError(
                "catalog and event_store are required without storage services"
            )
        if not callable(getattr(catalog, "get", None)):
            raise TypeError("conversation catalog must provide get()")
        if not callable(getattr(event_store, "read", None)):
            raise TypeError("conversation event store must provide read()")
        if search is not None and not callable(getattr(search, "search", None)):
            raise TypeError("conversation search adapter must provide search()")
        self._scope = scope
        self._conversation_storage_services = conversation_storage_services
        self._catalog = catalog
        self._event_store = event_store
        self._search = (
            ReferenceConversationSearch(catalog, event_store)
            if search is None
            else search
        )

    @property
    def scope(self) -> ConversationSearchScope:
        """Return the immutable authorized scope without I/O or mutation."""

        return self._scope

    def search(self, query: ConversationSearchQuery) -> ConversationSearchResult:
        """Return bounded cited hits from the authorized current session."""

        if type(query) is not ConversationSearchQuery:
            raise TypeError(
                "conversation search query must be a ConversationSearchQuery"
            )
        result = self._search.search(self._scope, query)
        if type(result) is not ConversationSearchResult:
            raise TypeError("conversation search adapter returned an invalid result")
        if result.query != query:
            raise ValueError("conversation search adapter changed the query")
        if any(
            hit.citation.session_id != self._scope.session_id for hit in result.hits
        ):
            raise ValueError(
                "conversation search adapter crossed the authorized session"
            )
        if not query.include_archived and any(
            hit.status is ConversationStatus.ARCHIVED for hit in result.hits
        ):
            raise ValueError(
                "conversation search adapter returned archived results "
                "without authorization"
            )
        return result

    def read_transcript(
        self,
        conversation_id: str,
        *,
        start_sequence: int = 1,
        end_sequence: int | None = None,
        turn_id: str | None = None,
        max_events: int = CONVERSATION_MEMORY_POLICY.default_page_events,
        citation: ConversationCitation | None = None,
    ) -> ConversationTranscriptPage:
        """Read one inclusive bounded chronological transcript range."""

        _identifier(conversation_id, label="conversation ID")
        _positive_integer(start_sequence, label="start sequence")
        if end_sequence is not None:
            _positive_integer(end_sequence, label="end sequence")
            if end_sequence < start_sequence:
                raise ValueError("end sequence must not precede start sequence")
        if turn_id is not None:
            _identifier(turn_id, label="turn ID")
        _positive_integer(
            max_events,
            label="transcript page event limit",
            maximum=CONVERSATION_MEMORY_POLICY.max_page_events,
        )
        if citation is not None and type(citation) is not ConversationCitation:
            raise TypeError("citation must be a ConversationCitation")
        if citation is not None:
            self._validate_citation_identity(
                citation,
                conversation_id=conversation_id,
            )
        return self._read_page(
            conversation_id=conversation_id,
            start_sequence=start_sequence,
            end_sequence=end_sequence,
            turn_id=turn_id,
            max_events=max_events,
            citation=citation,
            expected_snapshot=None,
        )

    def continue_transcript(
        self,
        cursor: ConversationTranscriptCursor,
    ) -> ConversationTranscriptPage:
        """Continue a page only while its transcript snapshot is unchanged."""

        if type(cursor) is not ConversationTranscriptCursor:
            raise TypeError("transcript cursor must be a ConversationTranscriptCursor")
        if cursor.session_id != self._scope.session_id:
            raise ValueError("transcript cursor is outside the authorized session")
        if cursor.citation is not None:
            self._validate_citation_identity(
                cursor.citation,
                conversation_id=cursor.conversation_id,
            )
        return self._read_page(
            conversation_id=cursor.conversation_id,
            start_sequence=cursor.next_sequence,
            end_sequence=cursor.end_sequence,
            turn_id=cursor.turn_id,
            max_events=cursor.max_events,
            citation=cursor.citation,
            expected_snapshot=cursor.snapshot_last_sequence,
        )

    def _read_page(
        self,
        *,
        conversation_id: str,
        start_sequence: int,
        end_sequence: int | None,
        turn_id: str | None,
        max_events: int,
        citation: ConversationCitation | None,
        expected_snapshot: int | None,
    ) -> ConversationTranscriptPage:
        """Read, validate, and page one immutable transcript snapshot.

        Catalog identity, complete event order, optional citation/turn, range,
        cursor snapshot, event count, and UTF-8 byte limits are enforced. The
        ports are read-only; stale or oversized references raise ``ValueError``
        and invalid adapter data raises ``TypeError`` or ``ValueError``.
        """

        metadata = _validated_metadata(
            self._catalog.get(self._scope.session_id, conversation_id),
            session_id=self._scope.session_id,
        )
        if metadata.conversation_id != conversation_id:
            raise ValueError("catalog returned the wrong conversation identity")
        events = _validated_events(
            self._event_store.read(self._scope.session_id, conversation_id),
            session_id=self._scope.session_id,
            conversation_id=conversation_id,
        )
        snapshot_last_sequence = events[-1].sequence if events else 0
        if (
            expected_snapshot is not None
            and snapshot_last_sequence != expected_snapshot
        ):
            raise ValueError("stale transcript cursor")

        requested_end = snapshot_last_sequence if end_sequence is None else end_sequence
        if requested_end > snapshot_last_sequence:
            raise ValueError("transcript range exceeds the current snapshot")
        if start_sequence > snapshot_last_sequence + 1:
            raise ValueError("transcript start sequence is stale")
        if start_sequence <= snapshot_last_sequence and requested_end < start_sequence:
            raise ValueError("transcript range is invalid")

        self._validate_citation(
            citation,
            conversation_id=conversation_id,
            events=events,
        )

        if turn_id is not None and not any(
            event.turn_id == turn_id for event in events
        ):
            raise ValueError("stale transcript turn reference")

        range_events = tuple(
            event
            for event in events
            if start_sequence <= event.sequence <= requested_end
            and (turn_id is None or event.turn_id == turn_id)
        )

        returned: list[ConversationEvent] = []
        returned_utf8_bytes = 0
        for event in range_events:
            event_utf8_bytes = len(event.content.encode("utf-8"))
            if (
                not returned
                and event_utf8_bytes > CONVERSATION_MEMORY_POLICY.max_page_utf8_bytes
            ):
                raise ValueError("transcript event exceeds the page UTF-8 byte limit")
            if len(returned) == max_events:
                break
            if (
                returned_utf8_bytes + event_utf8_bytes
                > CONVERSATION_MEMORY_POLICY.max_page_utf8_bytes
            ):
                break
            returned.append(event)
            returned_utf8_bytes += event_utf8_bytes

        next_cursor: ConversationTranscriptCursor | None = None
        if len(returned) < len(range_events):
            next_event = range_events[len(returned)]
            next_cursor = ConversationTranscriptCursor(
                session_id=self._scope.session_id,
                conversation_id=conversation_id,
                next_sequence=next_event.sequence,
                end_sequence=requested_end,
                snapshot_last_sequence=snapshot_last_sequence,
                turn_id=turn_id,
                max_events=max_events,
                citation=citation,
            )

        return ConversationTranscriptPage(
            session_id=self._scope.session_id,
            conversation_id=conversation_id,
            events=tuple(returned),
            requested_start_sequence=start_sequence,
            requested_end_sequence=requested_end,
            snapshot_last_sequence=snapshot_last_sequence,
            returned_utf8_bytes=returned_utf8_bytes,
            next_cursor=next_cursor,
            truncated=next_cursor is not None,
        )

    def _validate_citation(
        self,
        citation: ConversationCitation | None,
        *,
        conversation_id: str,
        events: tuple[ConversationEvent, ...],
    ) -> None:
        """Validate an optional citation against identity and snapshot events.

        Conversation-only citations are accepted after identity validation;
        stale event coordinates raise ``ValueError`` without mutation.
        """

        if citation is None:
            return
        self._validate_citation_identity(
            citation,
            conversation_id=conversation_id,
        )
        if citation.sequence is None:
            return
        if citation.sequence > len(events):
            raise ValueError("stale conversation citation")
        event = events[citation.sequence - 1]
        if (
            event.sequence != citation.sequence
            or event.turn_id != citation.turn_id
            or event.kind is not citation.event_kind
        ):
            raise ValueError("stale conversation citation")

    def _validate_citation_identity(
        self,
        citation: ConversationCitation,
        *,
        conversation_id: str,
    ) -> None:
        """Reject citations outside the authorized session or conversation."""

        if citation.session_id != self._scope.session_id:
            raise ValueError("conversation citation is outside the authorized session")
        if citation.conversation_id != conversation_id:
            raise ValueError("conversation citation identifies another conversation")


__all__ = [
    "CONVERSATION_MEMORY_POLICY",
    "ConversationCitation",
    "ConversationMemoryPolicy",
    "ConversationMemoryService",
    "ConversationSearch",
    "ConversationSearchHit",
    "ConversationSearchMatchKind",
    "ConversationSearchQuery",
    "ConversationSearchResult",
    "ConversationSearchScope",
    "ConversationTranscriptCursor",
    "ConversationTranscriptPage",
    "ReferenceConversationSearch",
]
