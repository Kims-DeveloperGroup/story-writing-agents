"""Provider-neutral deterministic conversation search and cited transcript reads."""

from __future__ import annotations

import hashlib
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
    COLLABORATION_EVENT_SCHEMA_VERSION,
    CollaborationEvent,
    CollaborationReadableField,
    CollaborationReadableFieldKey,
    CollaborationReadableRecord,
    CollaborationEventStore,
    ConversationCatalog,
    ConversationEvent,
    ConversationEventKind,
    ConversationMetadata,
    ConversationStatus,
    encode_collaboration_event,
    normalize_conversation_subject,
    project_collaboration_readable_history,
)
from .errors import SessionIdError


_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_SHA256_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


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

COLLABORATION_READABLE_SOURCE_SCHEMA_ID = "story-collaboration-readable-event-field-v2"


@dataclass(frozen=True, slots=True)
class CollaborationHistoryCitation:
    """Stable coordinates for an exact range in one readable event field.

    Ranges are zero-based, start-inclusive, and end-exclusive Unicode code-point
    offsets in the original readable field. Nested ordinals are also zero-based.
    """

    session_id: str
    conversation_id: str
    event_id: str
    sequence: int
    field_key: CollaborationReadableFieldKey
    start_codepoint: int
    end_codepoint: int
    assignment_ordinal: int | None = None
    input_ordinal: int | None = None

    def __post_init__(self) -> None:
        _identifier(self.session_id, label="session ID")
        _identifier(self.conversation_id, label="conversation ID")
        _identifier(self.event_id, label="event ID")
        _positive_integer(self.sequence, label="event sequence")
        if type(self.field_key) is not CollaborationReadableFieldKey:
            raise TypeError(
                "citation field key must be a CollaborationReadableFieldKey"
            )
        _non_negative_integer(self.start_codepoint, label="citation start code point")
        _positive_integer(self.end_codepoint, label="citation end code point")
        if self.end_codepoint <= self.start_codepoint:
            raise ValueError("citation code-point range must be nonempty")
        for value, label in (
            (self.assignment_ordinal, "citation assignment ordinal"),
            (self.input_ordinal, "citation input ordinal"),
        ):
            if value is not None:
                _non_negative_integer(value, label=label)
        CollaborationReadableField(
            key=self.field_key,
            text="citation-coordinate-validation",
            assignment_ordinal=self.assignment_ordinal,
            input_ordinal=self.input_ordinal,
        )


@dataclass(frozen=True, slots=True)
class CollaborationHistorySearchQuery:
    """One bounded deterministic literal query over readable v2 fields."""

    text: str
    limit: int = CONVERSATION_MEMORY_POLICY.default_search_hits

    def __post_init__(self) -> None:
        _normalized_query(self.text)
        _positive_integer(
            self.limit,
            label="collaboration search hit limit",
            maximum=CONVERSATION_MEMORY_POLICY.max_search_hits,
        )


@dataclass(frozen=True, slots=True)
class CollaborationHistorySearchHit:
    """First exact normalized-literal match in one readable field."""

    citation: CollaborationHistoryCitation
    snippet: str

    def __post_init__(self) -> None:
        if type(self.citation) is not CollaborationHistoryCitation:
            raise TypeError(
                "collaboration search citation must be a CollaborationHistoryCitation"
            )
        snippet = _strict_utf8(self.snippet, label="collaboration search snippet")
        if not snippet:
            raise ValueError("collaboration search snippet must not be empty")
        if len(snippet) > CONVERSATION_MEMORY_POLICY.max_snippet_characters:
            raise ValueError("collaboration search snippet exceeds the character limit")
        if len(snippet.encode("utf-8")) > (
            CONVERSATION_MEMORY_POLICY.max_snippet_utf8_bytes
        ):
            raise ValueError("collaboration search snippet exceeds the UTF-8 limit")


@dataclass(frozen=True, slots=True)
class CollaborationHistorySearchResult:
    """Deterministic sequence/field-ordered readable-history matches."""

    query: CollaborationHistorySearchQuery
    normalized_query: str
    hits: tuple[CollaborationHistorySearchHit, ...]
    matched_fields: int
    scanned_records: int
    scanned_fields: int
    scan_truncated: bool
    truncated: bool

    def __post_init__(self) -> None:
        if type(self.query) is not CollaborationHistorySearchQuery:
            raise TypeError("collaboration search query is invalid")
        if self.normalized_query != _normalized_query(self.query.text):
            raise ValueError("normalized collaboration search query is inconsistent")
        if type(self.hits) is not tuple or any(
            type(hit) is not CollaborationHistorySearchHit for hit in self.hits
        ):
            raise TypeError(
                "collaboration search hits must be a tuple of CollaborationHistorySearchHit"
            )
        for value, label in (
            (self.matched_fields, "matched fields"),
            (self.scanned_records, "scanned records"),
            (self.scanned_fields, "scanned fields"),
        ):
            _non_negative_integer(value, label=label)
        if len(self.hits) > self.query.limit:
            raise ValueError("collaboration search returned too many hits")
        if self.matched_fields < len(self.hits):
            raise ValueError("collaboration matched-field count is inconsistent")
        if self.matched_fields > self.scanned_fields:
            raise ValueError("collaboration matches exceed scanned fields")
        if self.scanned_records > CONVERSATION_MEMORY_POLICY.max_scan_events:
            raise ValueError("collaboration scanned records exceed the fixed policy")
        if type(self.scan_truncated) is not bool:
            raise TypeError("collaboration scan-truncated flag must be a boolean")
        if type(self.truncated) is not bool:
            raise TypeError("collaboration search truncated flag must be a boolean")
        if self.truncated is not (
            self.scan_truncated or self.matched_fields > len(self.hits)
        ):
            raise ValueError("collaboration search truncated flag is inconsistent")
        if self.hits != tuple(sorted(self.hits, key=_collaboration_search_hit_key)):
            raise ValueError("collaboration search hits are not deterministic")
        coordinates = tuple(hit.citation for hit in self.hits)
        if len(set(coordinates)) != len(coordinates):
            raise ValueError("collaboration search contains duplicate fields")


@dataclass(frozen=True, slots=True)
class CollaborationHistoryPageCursor:
    """Forward continuation bound to one exact collaboration-history tail."""

    session_id: str
    conversation_id: str
    next_sequence: int
    end_sequence: int
    snapshot_last_sequence: int
    snapshot_tail_batch_digest: str
    max_records: int
    max_utf8_bytes: int

    def __post_init__(self) -> None:
        _identifier(self.session_id, label="session ID")
        _identifier(self.conversation_id, label="conversation ID")
        _positive_integer(self.next_sequence, label="next sequence")
        _positive_integer(self.end_sequence, label="end sequence")
        _positive_integer(self.snapshot_last_sequence, label="snapshot last sequence")
        if self.next_sequence > self.end_sequence:
            raise ValueError("collaboration cursor next sequence exceeds its range")
        if self.end_sequence > self.snapshot_last_sequence:
            raise ValueError("collaboration cursor exceeds its snapshot")
        _digest(self.snapshot_tail_batch_digest, label="snapshot tail batch digest")
        _positive_integer(
            self.max_records,
            label="collaboration page record limit",
            maximum=CONVERSATION_MEMORY_POLICY.max_page_events,
        )
        _positive_integer(
            self.max_utf8_bytes,
            label="collaboration page UTF-8 limit",
            maximum=CONVERSATION_MEMORY_POLICY.max_page_utf8_bytes,
        )


@dataclass(frozen=True, slots=True)
class CollaborationHistoryPage:
    """One bounded readable-record page from one exact history snapshot."""

    session_id: str | None
    conversation_id: str | None
    records: tuple[CollaborationReadableRecord, ...]
    requested_start_sequence: int
    requested_end_sequence: int
    snapshot_last_sequence: int
    snapshot_tail_batch_digest: str | None
    returned_utf8_bytes: int
    next_cursor: CollaborationHistoryPageCursor | None
    truncated: bool

    def __post_init__(self) -> None:
        if (self.session_id is None) is not (self.conversation_id is None):
            raise ValueError("collaboration page identity is incomplete")
        if self.session_id is not None:
            _identifier(self.session_id, label="session ID")
            assert self.conversation_id is not None
            _identifier(self.conversation_id, label="conversation ID")
        if type(self.records) is not tuple or any(
            type(record) is not CollaborationReadableRecord for record in self.records
        ):
            raise TypeError(
                "collaboration page records must be a tuple of CollaborationReadableRecord"
            )
        if len(self.records) > CONVERSATION_MEMORY_POLICY.max_page_events:
            raise ValueError("collaboration page exceeds the fixed record limit")
        _positive_integer(
            self.requested_start_sequence, label="requested start sequence"
        )
        _non_negative_integer(
            self.requested_end_sequence, label="requested end sequence"
        )
        _non_negative_integer(
            self.snapshot_last_sequence, label="snapshot last sequence"
        )
        empty_snapshot = self.snapshot_last_sequence == 0
        if empty_snapshot is not (self.snapshot_tail_batch_digest is None):
            raise ValueError("collaboration page tail digest is inconsistent")
        if self.snapshot_tail_batch_digest is not None:
            _digest(self.snapshot_tail_batch_digest, label="snapshot tail batch digest")
        if self.requested_end_sequence > self.snapshot_last_sequence:
            raise ValueError("collaboration page range exceeds its snapshot")
        if self.requested_end_sequence < self.requested_start_sequence and not (
            self.requested_start_sequence == self.snapshot_last_sequence + 1
            and self.requested_end_sequence == self.snapshot_last_sequence
        ):
            raise ValueError("collaboration page range is invalid")
        previous_sequence = 0
        for record in self.records:
            if (
                record.session_id != self.session_id
                or record.conversation_id != self.conversation_id
            ):
                raise ValueError("collaboration page record identity is inconsistent")
            if record.sequence <= previous_sequence:
                raise ValueError("collaboration page records are not ordered")
            if not (
                self.requested_start_sequence
                <= record.sequence
                <= self.requested_end_sequence
            ):
                raise ValueError("collaboration page record is outside its range")
            previous_sequence = record.sequence
        _non_negative_integer(self.returned_utf8_bytes, label="returned UTF-8 bytes")
        if self.returned_utf8_bytes != sum(
            record.readable_utf8_bytes for record in self.records
        ):
            raise ValueError("collaboration page UTF-8 count is inconsistent")
        if self.returned_utf8_bytes > CONVERSATION_MEMORY_POLICY.max_page_utf8_bytes:
            raise ValueError("collaboration page exceeds the fixed UTF-8 limit")
        if self.next_cursor is not None:
            if type(self.next_cursor) is not CollaborationHistoryPageCursor:
                raise TypeError("collaboration page cursor is invalid")
            if not self.records:
                raise ValueError("empty collaboration page cannot have a cursor")
            if (
                self.next_cursor.session_id != self.session_id
                or self.next_cursor.conversation_id != self.conversation_id
                or self.next_cursor.end_sequence != self.requested_end_sequence
                or self.next_cursor.snapshot_last_sequence
                != self.snapshot_last_sequence
                or self.next_cursor.snapshot_tail_batch_digest
                != self.snapshot_tail_batch_digest
                or self.next_cursor.next_sequence <= self.records[-1].sequence
            ):
                raise ValueError("collaboration page cursor is inconsistent")
        if type(self.truncated) is not bool:
            raise TypeError("collaboration page truncated flag must be a boolean")
        if self.truncated is not (self.next_cursor is not None):
            raise ValueError("collaboration page truncated flag is inconsistent")


def search_collaboration_history(
    events: tuple[CollaborationEvent, ...],
    query: CollaborationHistorySearchQuery,
) -> CollaborationHistorySearchResult:
    """Search one fully validated v2 history with exact field citations."""

    if type(query) is not CollaborationHistorySearchQuery:
        raise TypeError("collaboration search query is invalid")
    records = project_collaboration_readable_history(events)
    normalized_query = _normalized_query(query.text)
    matches: list[CollaborationHistorySearchHit] = []
    scanned_fields = 0
    scanned_records = records[: CONVERSATION_MEMORY_POLICY.max_scan_events]
    scan_truncated = len(records) > len(scanned_records)
    for record in scanned_records:
        for field in record.fields:
            scanned_fields += 1
            normalized_text, spans = _normalized_literal_with_spans(field.text)
            match_start = normalized_text.find(normalized_query)
            if match_start < 0:
                continue
            match_spans = spans[match_start : match_start + len(normalized_query)]
            start_codepoint = min(span[0] for span in match_spans)
            end_codepoint = max(span[1] for span in match_spans)
            matches.append(
                CollaborationHistorySearchHit(
                    citation=CollaborationHistoryCitation(
                        session_id=record.session_id,
                        conversation_id=record.conversation_id,
                        event_id=record.event_id,
                        sequence=record.sequence,
                        field_key=field.key,
                        assignment_ordinal=field.assignment_ordinal,
                        input_ordinal=field.input_ordinal,
                        start_codepoint=start_codepoint,
                        end_codepoint=end_codepoint,
                    ),
                    snippet=build_conversation_literal_snippet(
                        normalized_text,
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
    matches.sort(key=_collaboration_search_hit_key)
    retained = tuple(matches[: query.limit])
    return CollaborationHistorySearchResult(
        query=query,
        normalized_query=normalized_query,
        hits=retained,
        matched_fields=len(matches),
        scanned_records=len(scanned_records),
        scanned_fields=scanned_fields,
        scan_truncated=scan_truncated,
        truncated=scan_truncated or len(matches) > len(retained),
    )


def page_collaboration_history(
    events: tuple[CollaborationEvent, ...],
    *,
    start_sequence: int = 1,
    end_sequence: int | None = None,
    max_records: int = CONVERSATION_MEMORY_POLICY.default_page_events,
    max_utf8_bytes: int = CONVERSATION_MEMORY_POLICY.max_page_utf8_bytes,
) -> CollaborationHistoryPage:
    """Read one bounded page from a freshly validated v2 history snapshot."""

    _positive_integer(start_sequence, label="start sequence")
    if end_sequence is not None:
        _positive_integer(end_sequence, label="end sequence")
        if end_sequence < start_sequence:
            raise ValueError("end sequence must not precede start sequence")
    _positive_integer(
        max_records,
        label="collaboration page record limit",
        maximum=CONVERSATION_MEMORY_POLICY.max_page_events,
    )
    _positive_integer(
        max_utf8_bytes,
        label="collaboration page UTF-8 limit",
        maximum=CONVERSATION_MEMORY_POLICY.max_page_utf8_bytes,
    )
    return _page_collaboration_history(
        events,
        start_sequence=start_sequence,
        end_sequence=end_sequence,
        max_records=max_records,
        max_utf8_bytes=max_utf8_bytes,
        expected_last_sequence=None,
        expected_tail_batch_digest=None,
    )


def continue_collaboration_history(
    events: tuple[CollaborationEvent, ...],
    cursor: CollaborationHistoryPageCursor,
) -> CollaborationHistoryPage:
    """Continue only when both the sequence and tail-batch snapshot still match."""

    if type(cursor) is not CollaborationHistoryPageCursor:
        raise TypeError("collaboration page cursor is invalid")
    page = _page_collaboration_history(
        events,
        start_sequence=cursor.next_sequence,
        end_sequence=cursor.end_sequence,
        max_records=cursor.max_records,
        max_utf8_bytes=cursor.max_utf8_bytes,
        expected_last_sequence=cursor.snapshot_last_sequence,
        expected_tail_batch_digest=cursor.snapshot_tail_batch_digest,
    )
    if (
        page.session_id != cursor.session_id
        or page.conversation_id != cursor.conversation_id
    ):
        raise ValueError("collaboration page cursor identifies another history")
    return page


def collaboration_readable_document_id(
    *,
    workspace_id: str,
    metadata_revision: int,
    record: CollaborationReadableRecord,
    field: CollaborationReadableField,
) -> str:
    """Return the derived RAG/context-source identity for one readable field.

    The digest is evidence for derived lookup only. It is not an event identity,
    append receipt, confirmation capability, or collaboration recovery fact.
    """

    workspace = _strict_utf8(workspace_id, label="workspace identity")
    if not workspace or "\x00" in workspace:
        raise ValueError("workspace identity must be nonempty and contain no NUL")
    _positive_integer(metadata_revision, label="metadata revision")
    if type(record) is not CollaborationReadableRecord:
        raise TypeError("readable document record is invalid")
    if type(field) is not CollaborationReadableField or field not in record.fields:
        raise ValueError("readable document field does not belong to its record")
    values = (
        COLLABORATION_READABLE_SOURCE_SCHEMA_ID,
        str(COLLABORATION_EVENT_SCHEMA_VERSION),
        workspace,
        record.session_id,
        record.conversation_id,
        str(metadata_revision),
        record.event_id,
        str(record.sequence),
        record.batch_digest,
        field.key.value,
        "" if field.assignment_ordinal is None else str(field.assignment_ordinal),
        "" if field.input_ordinal is None else str(field.input_ordinal),
        field.text,
    )
    digest = hashlib.sha256()
    canonical_event = encode_collaboration_event(
        CollaborationEvent(
            schema_version=record.schema_version,
            event_id=record.event_id,
            session_id=record.session_id,
            conversation_id=record.conversation_id,
            turn_id=record.turn_id,
            step_id=record.step_id,
            sequence=record.sequence,
            timestamp=record.timestamp,
            batch_id=record.batch_id,
            batch_ordinal=record.batch_ordinal,
            batch_size=record.batch_size,
            batch_digest=record.batch_digest,
            kind=record.kind,
            sender=record.sender,
            recipient=record.recipient,
            payload=record.payload,
        )
    )
    digest.update(len(canonical_event).to_bytes(8, byteorder="big"))
    digest.update(canonical_event)
    for value in values:
        encoded = value.encode("utf-8", errors="strict")
        digest.update(len(encoded).to_bytes(8, byteorder="big"))
        digest.update(encoded)
    return f"sha256:{digest.hexdigest()}"


def resolve_collaboration_citation(
    events: tuple[CollaborationEvent, ...],
    citation: CollaborationHistoryCitation,
) -> str:
    """Return the exact original-field substring identified by a fresh citation."""

    if type(citation) is not CollaborationHistoryCitation:
        raise TypeError("collaboration citation is invalid")
    records = project_collaboration_readable_history(events)
    record = next(
        (
            item
            for item in records
            if item.event_id == citation.event_id and item.sequence == citation.sequence
        ),
        None,
    )
    if record is None:
        raise ValueError("stale collaboration citation event")
    if (
        record.session_id != citation.session_id
        or record.conversation_id != citation.conversation_id
    ):
        raise ValueError("collaboration citation identifies another history")
    field = next(
        (
            item
            for item in record.fields
            if item.key is citation.field_key
            and item.assignment_ordinal == citation.assignment_ordinal
            and item.input_ordinal == citation.input_ordinal
        ),
        None,
    )
    if field is None:
        raise ValueError("stale collaboration citation field")
    if citation.end_codepoint > len(field.text):
        raise ValueError("stale collaboration citation range")
    return field.text[citation.start_codepoint : citation.end_codepoint]


def _page_collaboration_history(
    events: tuple[CollaborationEvent, ...],
    *,
    start_sequence: int,
    end_sequence: int | None,
    max_records: int,
    max_utf8_bytes: int,
    expected_last_sequence: int | None,
    expected_tail_batch_digest: str | None,
) -> CollaborationHistoryPage:
    records = project_collaboration_readable_history(events)
    snapshot_last_sequence = records[-1].sequence if records else 0
    snapshot_tail_batch_digest = records[-1].batch_digest if records else None
    if expected_last_sequence is not None and (
        snapshot_last_sequence != expected_last_sequence
        or snapshot_tail_batch_digest != expected_tail_batch_digest
    ):
        raise ValueError("stale collaboration page cursor")
    requested_end = snapshot_last_sequence if end_sequence is None else end_sequence
    if requested_end > snapshot_last_sequence:
        raise ValueError("collaboration page range exceeds the current snapshot")
    if start_sequence > snapshot_last_sequence + 1:
        raise ValueError("collaboration page start sequence is stale")
    if start_sequence <= snapshot_last_sequence and requested_end < start_sequence:
        raise ValueError("collaboration page range is invalid")

    range_records = tuple(
        record
        for record in records
        if start_sequence <= record.sequence <= requested_end
    )
    retained: list[CollaborationReadableRecord] = []
    retained_utf8_bytes = 0
    for record in range_records:
        record_bytes = record.readable_utf8_bytes
        if not retained and record_bytes > max_utf8_bytes:
            raise ValueError("collaboration record exceeds the page UTF-8 limit")
        if len(retained) == max_records:
            break
        if retained_utf8_bytes + record_bytes > max_utf8_bytes:
            break
        retained.append(record)
        retained_utf8_bytes += record_bytes

    next_cursor: CollaborationHistoryPageCursor | None = None
    if len(retained) < len(range_records):
        next_record = range_records[len(retained)]
        assert records and snapshot_tail_batch_digest is not None
        next_cursor = CollaborationHistoryPageCursor(
            session_id=records[0].session_id,
            conversation_id=records[0].conversation_id,
            next_sequence=next_record.sequence,
            end_sequence=requested_end,
            snapshot_last_sequence=snapshot_last_sequence,
            snapshot_tail_batch_digest=snapshot_tail_batch_digest,
            max_records=max_records,
            max_utf8_bytes=max_utf8_bytes,
        )
    return CollaborationHistoryPage(
        session_id=records[0].session_id if records else None,
        conversation_id=records[0].conversation_id if records else None,
        records=tuple(retained),
        requested_start_sequence=start_sequence,
        requested_end_sequence=requested_end,
        snapshot_last_sequence=snapshot_last_sequence,
        snapshot_tail_batch_digest=snapshot_tail_batch_digest,
        returned_utf8_bytes=retained_utf8_bytes,
        next_cursor=next_cursor,
        truncated=next_cursor is not None,
    )


_COLLABORATION_FIELD_ORDER = tuple(CollaborationReadableFieldKey)


def _collaboration_hit_key(
    coordinate: tuple[int, str, int | None, int | None],
) -> tuple[int, int, int, int]:
    sequence, field_value, assignment_ordinal, input_ordinal = coordinate
    return (
        sequence,
        _COLLABORATION_FIELD_ORDER.index(CollaborationReadableFieldKey(field_value)),
        -1 if assignment_ordinal is None else assignment_ordinal,
        -1 if input_ordinal is None else input_ordinal,
    )


def _collaboration_search_hit_key(
    hit: CollaborationHistorySearchHit,
) -> tuple[str, str, int, int, int, int]:
    citation = hit.citation
    coordinate = _collaboration_hit_key(
        (
            citation.sequence,
            citation.field_key.value,
            citation.assignment_ordinal,
            citation.input_ordinal,
        )
    )
    return (citation.session_id, citation.conversation_id, *coordinate)


def _digest(value: str, *, label: str) -> str:
    text = _strict_utf8(value, label=label)
    if _SHA256_DIGEST.fullmatch(text) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return text


def _normalized_literal_with_spans(
    source: str,
) -> tuple[str, tuple[tuple[int, int], ...]]:
    """Normalize exactly while retaining original code-point source ranges.

    Common combining sequences are normalized as clusters. A prefix-diff
    fallback handles the uncommon case where NFKC composition crosses those
    cluster boundaries. Case-fold expansions share their originating range,
    and collapsed whitespace carries the complete original whitespace range.
    """

    text = _strict_utf8(source, label="collaboration readable field")
    normalized_characters: list[str] = []
    normalized_spans: list[tuple[int, int]] = []
    start = 0
    while start < len(text):
        end = start + 1
        while end < len(text) and unicodedata.combining(text[end]) != 0:
            end += 1
        normalized_cluster = unicodedata.normalize("NFKC", text[start:end]).casefold()
        normalized_characters.extend(normalized_cluster)
        normalized_spans.extend([(start, end)] * len(normalized_cluster))
        start = end
    pre_collapse = "".join(normalized_characters)
    exact_pre_collapse = unicodedata.normalize("NFKC", text).casefold()
    if pre_collapse != exact_pre_collapse:
        pre_collapse, normalized_spans = _prefix_normalized_spans(text)

    result_characters: list[str] = []
    result_spans: list[tuple[int, int]] = []
    pending_whitespace: list[tuple[int, int]] = []
    for character, span in zip(pre_collapse, normalized_spans, strict=True):
        searchable = (
            " " if unicodedata.category(character).startswith("C") else character
        )
        if searchable.isspace():
            pending_whitespace.append(span)
            continue
        if result_characters and pending_whitespace:
            result_characters.append(" ")
            result_spans.append(
                (
                    min(item[0] for item in pending_whitespace),
                    max(item[1] for item in pending_whitespace),
                )
            )
        pending_whitespace.clear()
        result_characters.append(searchable)
        result_spans.append(span)

    normalized = "".join(result_characters)
    if normalized != normalize_conversation_literal_text(text):
        raise ValueError("collaboration literal normalization mapping is inconsistent")
    return normalized, tuple(result_spans)


def _prefix_normalized_spans(
    source: str,
) -> tuple[str, list[tuple[int, int]]]:
    """Map rare cross-cluster normalization through deterministic prefix diffs."""

    current = ""
    spans: list[tuple[int, int]] = []
    for source_end in range(1, len(source) + 1):
        updated = unicodedata.normalize("NFKC", source[:source_end]).casefold()
        common_prefix = 0
        while (
            common_prefix < len(current)
            and common_prefix < len(updated)
            and current[common_prefix] == updated[common_prefix]
        ):
            common_prefix += 1
        common_suffix = 0
        while (
            common_suffix < len(current) - common_prefix
            and common_suffix < len(updated) - common_prefix
            and current[-common_suffix - 1] == updated[-common_suffix - 1]
        ):
            common_suffix += 1
        removed_end = len(spans) - common_suffix if common_suffix else len(spans)
        removed = spans[common_prefix:removed_end]
        replacement_span = (
            min([source_end - 1, *(item[0] for item in removed)]),
            max([source_end, *(item[1] for item in removed)]),
        )
        replacement_end = (
            len(updated) - common_suffix if common_suffix else len(updated)
        )
        replacement_size = replacement_end - common_prefix
        suffix = spans[len(spans) - common_suffix :] if common_suffix else []
        spans = spans[:common_prefix] + [replacement_span] * replacement_size + suffix
        current = updated
    return current, spans


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


class _SchemaV1ConversationEventReader(Protocol):
    """Private read-only shape retained until stage-2 consumers are removed."""

    def read(
        self,
        session_id: str,
        conversation_id: str,
    ) -> tuple[ConversationEvent, ...]: ...


class ReferenceConversationSearch:
    """Dependency-free bounded scan over catalog and event-store ports."""

    def __init__(
        self,
        catalog: ConversationCatalog,
        event_store: _SchemaV1ConversationEventReader,
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
    def event_store(self) -> _SchemaV1ConversationEventReader: ...

    @property
    def search(self) -> ConversationSearch: ...

    @property
    def mutation_coordinator(self) -> object: ...


def _storage_service_components(
    value: _ConversationStorageServicesLike,
) -> tuple[ConversationCatalog, _SchemaV1ConversationEventReader, ConversationSearch]:
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
        event_store: _SchemaV1ConversationEventReader | None = None,
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


@dataclass(frozen=True, slots=True)
class CollaborationHistorySearchScope:
    """One current story session explicitly authorized for history lookup."""

    session_id: str
    include_archived: bool = False

    def __post_init__(self) -> None:
        _identifier(self.session_id, label="session ID")
        _boolean(self.include_archived, label="include archived")


class CollaborationHistorySearch(Protocol):
    """Storage-neutral aggregate search over canonical readable event fields."""

    def search(
        self,
        scope: CollaborationHistorySearchScope,
        query: CollaborationHistorySearchQuery,
    ) -> CollaborationHistorySearchResult:
        """Return bounded event-and-field cited hits from one session."""

        ...


class ReferenceCollaborationHistorySearch:
    """Bounded deterministic aggregate search over a catalog and v2 store."""

    def __init__(
        self,
        catalog: ConversationCatalog,
        event_store: CollaborationEventStore,
    ) -> None:
        if not callable(getattr(catalog, "list", None)):
            raise TypeError("conversation catalog must provide list()")
        if not callable(getattr(event_store, "read", None)):
            raise TypeError("collaboration event store must provide read()")
        self._catalog = catalog
        self._event_store = event_store

    def search(
        self,
        scope: CollaborationHistorySearchScope,
        query: CollaborationHistorySearchQuery,
    ) -> CollaborationHistorySearchResult:
        """Search ordered public fields only; catalog subjects never create hits."""

        if type(scope) is not CollaborationHistorySearchScope:
            raise TypeError("collaboration search scope is invalid")
        if type(query) is not CollaborationHistorySearchQuery:
            raise TypeError("collaboration search query is invalid")
        raw_metadata = self._catalog.list(
            scope.session_id,
            include_archived=scope.include_archived,
        )
        if type(raw_metadata) is not tuple:
            raise TypeError("conversation catalog list must return an exact tuple")
        metadata = tuple(
            _validated_metadata(item, session_id=scope.session_id)
            for item in raw_metadata
        )
        if len({item.conversation_id for item in metadata}) != len(metadata):
            raise ValueError("catalog returned duplicate conversation identities")
        if not scope.include_archived and any(
            item.status is ConversationStatus.ARCHIVED for item in metadata
        ):
            raise ValueError("catalog returned archived metadata without authorization")

        selected = metadata[: CONVERSATION_MEMORY_POLICY.max_scan_conversations]
        scan_truncated = len(selected) != len(metadata)
        remaining_records = CONVERSATION_MEMORY_POLICY.max_scan_events
        matched: list[CollaborationHistorySearchHit] = []
        scanned_fields = 0
        scanned_records = 0
        normalized_query = _normalized_query(query.text)
        for metadata_index, item in enumerate(selected):
            events = self._event_store.read(item.session_id, item.conversation_id)
            records = project_collaboration_readable_history(events)
            retained = records[:remaining_records]
            if len(retained) != len(records):
                scan_truncated = True
            for record in retained:
                scanned_records += 1
                for field in record.fields:
                    scanned_fields += 1
                    normalized_text, spans = _normalized_literal_with_spans(field.text)
                    match_start = normalized_text.find(normalized_query)
                    if match_start < 0:
                        continue
                    match_spans = spans[
                        match_start : match_start + len(normalized_query)
                    ]
                    matched.append(
                        CollaborationHistorySearchHit(
                            citation=CollaborationHistoryCitation(
                                session_id=record.session_id,
                                conversation_id=record.conversation_id,
                                event_id=record.event_id,
                                sequence=record.sequence,
                                field_key=field.key,
                                start_codepoint=min(span[0] for span in match_spans),
                                end_codepoint=max(span[1] for span in match_spans),
                                assignment_ordinal=field.assignment_ordinal,
                                input_ordinal=field.input_ordinal,
                            ),
                            snippet=build_conversation_literal_snippet(
                                normalized_text,
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
            remaining_records -= len(retained)
            if remaining_records == 0:
                if metadata_index + 1 < len(selected):
                    scan_truncated = True
                break

        matched.sort(key=_collaboration_search_hit_key)
        retained_hits = tuple(matched[: query.limit])
        return CollaborationHistorySearchResult(
            query=query,
            normalized_query=normalized_query,
            hits=retained_hits,
            matched_fields=len(matched),
            scanned_records=scanned_records,
            scanned_fields=scanned_fields,
            scan_truncated=scan_truncated,
            truncated=scan_truncated or len(matched) > len(retained_hits),
        )


class _CollaborationStorageServicesLike(Protocol):
    """Narrow storage bundle view that avoids a storage-to-search cycle."""

    @property
    def catalog(self) -> ConversationCatalog: ...

    @property
    def event_store(self) -> CollaborationEventStore: ...

    @property
    def search(self) -> CollaborationHistorySearch: ...

    @property
    def mutation_coordinator(self) -> object: ...


def _collaboration_storage_service_components(
    value: _CollaborationStorageServicesLike,
) -> tuple[ConversationCatalog, CollaborationEventStore, CollaborationHistorySearch]:
    try:
        catalog = value.catalog
        event_store = value.event_store
        search = value.search
        coordinator = value.mutation_coordinator
    except AttributeError as exc:
        raise TypeError("conversation storage services are invalid") from exc
    if not callable(getattr(catalog, "get", None)):
        raise TypeError("conversation storage catalog is invalid")
    if any(
        not callable(getattr(event_store, name, None))
        for name in ("append_batch", "read", "read_context")
    ):
        raise TypeError("collaboration event store is invalid")
    if not callable(getattr(search, "search", None)):
        raise TypeError("collaboration history search is invalid")
    if not callable(getattr(coordinator, "borrow", None)):
        raise TypeError("conversation storage coordinator is invalid")
    return catalog, event_store, search


class CollaborationHistoryService:
    """Authorize one session for aggregate search and bounded readable pages."""

    def __init__(
        self,
        scope: CollaborationHistorySearchScope,
        catalog: ConversationCatalog | None = None,
        event_store: CollaborationEventStore | None = None,
        *,
        search: CollaborationHistorySearch | None = None,
        conversation_storage_services: _CollaborationStorageServicesLike | None = None,
    ) -> None:
        if type(scope) is not CollaborationHistorySearchScope:
            raise TypeError("collaboration search scope is invalid")
        if conversation_storage_services is not None:
            if catalog is not None or event_store is not None or search is not None:
                raise TypeError(
                    "conversation_storage_services conflicts with raw collaborators"
                )
            catalog, event_store, search = _collaboration_storage_service_components(
                conversation_storage_services
            )
        elif catalog is None or event_store is None:
            raise TypeError(
                "catalog and event_store are required without storage services"
            )
        if not callable(getattr(catalog, "get", None)):
            raise TypeError("conversation catalog must provide get()")
        if not callable(getattr(event_store, "read", None)):
            raise TypeError("collaboration event store must provide read()")
        if search is not None and not callable(getattr(search, "search", None)):
            raise TypeError("collaboration search adapter must provide search()")
        self._scope = scope
        self._catalog = catalog
        self._event_store = event_store
        self._search = (
            ReferenceCollaborationHistorySearch(catalog, event_store)
            if search is None
            else search
        )

    @property
    def scope(self) -> CollaborationHistorySearchScope:
        """Return the immutable authorization scope."""

        return self._scope

    def search(
        self,
        query: CollaborationHistorySearchQuery,
    ) -> CollaborationHistorySearchResult:
        """Return field-cited matches from the authorized session."""

        if type(query) is not CollaborationHistorySearchQuery:
            raise TypeError("collaboration search query is invalid")
        result = self._search.search(self._scope, query)
        if type(result) is not CollaborationHistorySearchResult:
            raise TypeError("collaboration search adapter returned an invalid result")
        if result.query != query:
            raise ValueError("collaboration search adapter changed the query")
        if any(
            hit.citation.session_id != self._scope.session_id for hit in result.hits
        ):
            raise ValueError("collaboration search crossed the authorized session")
        return result

    def read_history(
        self,
        conversation_id: str,
        *,
        start_sequence: int = 1,
        end_sequence: int | None = None,
        max_records: int = CONVERSATION_MEMORY_POLICY.default_page_events,
        max_utf8_bytes: int = CONVERSATION_MEMORY_POLICY.max_page_utf8_bytes,
        citation: CollaborationHistoryCitation | None = None,
    ) -> CollaborationHistoryPage:
        """Read one bounded canonical history page after catalog authorization."""

        conversation = _identifier(conversation_id, label="conversation ID")
        metadata = _validated_metadata(
            self._catalog.get(self._scope.session_id, conversation),
            session_id=self._scope.session_id,
        )
        if metadata.conversation_id != conversation:
            raise ValueError("catalog returned the wrong conversation identity")
        if (
            not self._scope.include_archived
            and metadata.status is ConversationStatus.ARCHIVED
        ):
            raise ValueError("archived conversation is outside the authorized scope")
        events = self._event_store.read(self._scope.session_id, conversation)
        if citation is not None:
            self._validate_citation(citation, conversation_id=conversation)
            resolve_collaboration_citation(events, citation)
        page = page_collaboration_history(
            events,
            start_sequence=start_sequence,
            end_sequence=end_sequence,
            max_records=max_records,
            max_utf8_bytes=max_utf8_bytes,
        )
        if page.session_id is None:
            return CollaborationHistoryPage(
                session_id=self._scope.session_id,
                conversation_id=conversation,
                records=(),
                requested_start_sequence=page.requested_start_sequence,
                requested_end_sequence=page.requested_end_sequence,
                snapshot_last_sequence=0,
                snapshot_tail_batch_digest=None,
                returned_utf8_bytes=0,
                next_cursor=None,
                truncated=False,
            )
        return page

    def continue_history(
        self,
        cursor: CollaborationHistoryPageCursor,
    ) -> CollaborationHistoryPage:
        """Continue a page only while its exact v2 snapshot remains current."""

        if type(cursor) is not CollaborationHistoryPageCursor:
            raise TypeError("collaboration page cursor is invalid")
        if cursor.session_id != self._scope.session_id:
            raise ValueError("collaboration cursor is outside the authorized session")
        metadata = _validated_metadata(
            self._catalog.get(cursor.session_id, cursor.conversation_id),
            session_id=self._scope.session_id,
        )
        if (
            not self._scope.include_archived
            and metadata.status is ConversationStatus.ARCHIVED
        ):
            raise ValueError("archived conversation is outside the authorized scope")
        events = self._event_store.read(cursor.session_id, cursor.conversation_id)
        return continue_collaboration_history(events, cursor)

    def _validate_citation(
        self,
        citation: CollaborationHistoryCitation,
        *,
        conversation_id: str,
    ) -> None:
        if type(citation) is not CollaborationHistoryCitation:
            raise TypeError("collaboration citation is invalid")
        if citation.session_id != self._scope.session_id:
            raise ValueError("collaboration citation is outside the authorized session")
        if citation.conversation_id != conversation_id:
            raise ValueError("collaboration citation identifies another conversation")


__all__ = [
    "COLLABORATION_READABLE_SOURCE_SCHEMA_ID",
    "CONVERSATION_MEMORY_POLICY",
    "CollaborationHistoryCitation",
    "CollaborationHistoryPage",
    "CollaborationHistoryPageCursor",
    "CollaborationHistorySearch",
    "CollaborationHistorySearchHit",
    "CollaborationHistorySearchQuery",
    "CollaborationHistorySearchResult",
    "CollaborationHistorySearchScope",
    "CollaborationHistoryService",
    "ConversationMemoryPolicy",
    "ReferenceCollaborationHistorySearch",
    "collaboration_readable_document_id",
    "continue_collaboration_history",
    "page_collaboration_history",
    "resolve_collaboration_citation",
    "search_collaboration_history",
]
