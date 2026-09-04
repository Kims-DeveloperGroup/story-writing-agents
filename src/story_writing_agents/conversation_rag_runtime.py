"""Authorized runtime use of provider-neutral conversation RAG services."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, TypeVar, cast, runtime_checkable

from .conversation_literal_search import (
    normalize_conversation_literal_query,
    normalize_conversation_literal_text,
)
from .conversation_rag import (
    CONVERSATION_RAG_POLICY,
    ConversationRagCandidate,
    ConversationRagDocumentIdentity,
    ConversationRagDocumentKey,
    ConversationRagEmbeddingIdentity,
    ConversationRagError,
    ConversationRagOutcome,
    ConversationRagProjectionRequest,
    ConversationRagProjectionResult,
    ConversationRagProjectionState,
    ConversationRagProjectionStateAvailability,
    ConversationRagReason,
    ConversationRagRetrievalRequest,
    ConversationRagRetrievalResult,
    ConversationRagService,
    ConversationRagSourceDocument,
    ConversationRagSourceReference,
)
from .conversation_retrieval import (
    ConversationRagAttemptOutcome,
    ConversationRagContext,
    ConversationRagExcerpt,
    ConversationRetrievalSource,
)
from .conversation_search import (
    CollaborationHistoryCitation,
    CollaborationHistorySearchHit,
    CollaborationHistorySearchQuery,
    CollaborationHistorySearchResult,
    CollaborationHistorySearchScope,
)
from .conversation_storage.contracts import ConversationStorageServices
from .conversations import (
    CollaborationEvent,
    CollaborationReadableField,
    CollaborationReadableRecord,
    ConversationMetadata,
    ConversationStatus,
    encode_collaboration_event,
    project_collaboration_readable_history,
)

CONVERSATION_RAG_RUNTIME_SCHEMA_ID = "story-collaboration-rag-readable-event-v2"

_DOCUMENT_ID_DOMAIN = "story-collaboration-rag-readable-event-document-id-v2"
_REVISION_ID_DOMAIN = "story-collaboration-rag-readable-event-revision-v2"
_LITERAL_FRAGMENT_DOMAIN = "story-collaboration-rag-readable-event-literal-v2"
_MAX_QUERY_CODEPOINTS = 200
_MAX_CONTEXT_UTF8_BYTES = 32_768
_MAX_PROJECTED_SOURCE_UTF8_BYTES = 32 * 1_024 * 1_024
_SNAPSHOT_MUTATION_TIMEOUT_SECONDS = 30.0
_PROJECTION_MUTATION_TIMEOUT_SECONDS = 3_600.0
_AUTHORIZED_TARGET_TOKEN_DOMAIN = "story-collaboration-rag-authorized-target-v2"

_SyncResultT = TypeVar("_SyncResultT")


@dataclass(frozen=True, slots=True)
class ConversationRagRuntimePolicy:
    """Fixed authorization, reread, merge, and provider-context budgets."""

    max_query_codepoints: int = _MAX_QUERY_CODEPOINTS
    max_literal_candidates: int = CONVERSATION_RAG_POLICY.max_candidates
    max_semantic_candidates: int = CONVERSATION_RAG_POLICY.max_candidates
    max_excerpts: int = CONVERSATION_RAG_POLICY.max_candidates
    max_excerpt_codepoints: int = CONVERSATION_RAG_POLICY.max_fragment_codepoints
    max_context_utf8_bytes: int = _MAX_CONTEXT_UTF8_BYTES
    max_projected_source_utf8_bytes: int = _MAX_PROJECTED_SOURCE_UTF8_BYTES

    def __post_init__(self) -> None:
        expected = (
            200,
            20,
            20,
            20,
            800,
            32_768,
            33_554_432,
        )
        actual = tuple(
            getattr(self, field_name) for field_name in self.__dataclass_fields__
        )
        if any(type(value) is not int for value in actual):
            raise TypeError("conversation RAG runtime policy values must be integers")
        if actual != expected:
            raise ValueError("conversation RAG runtime policy is fixed")


CONVERSATION_RAG_RUNTIME_POLICY = ConversationRagRuntimePolicy()


@dataclass(frozen=True, slots=True)
class ConversationRagRuntimeStateSnapshot:
    """Restart-safe projection state and its content-free runtime reason."""

    session_id: str
    state: ConversationRagProjectionState
    reason: ConversationRagReason

    def __post_init__(self) -> None:
        _validate_identifier(self.session_id, label="session ID")
        if type(self.state) is not ConversationRagProjectionState:
            raise TypeError("runtime projection state must use the exact RAG state")
        if type(self.reason) is not ConversationRagReason:
            raise TypeError("runtime projection reason must use the exact enum")
        availability = self.state.availability
        if self.reason is ConversationRagReason.NONE:
            if availability is not ConversationRagProjectionStateAvailability.PRESENT:
                raise ValueError("ready runtime state requires a present checkpoint")
            return
        if self.reason is ConversationRagReason.UNAVAILABLE:
            if availability is not ConversationRagProjectionStateAvailability.MISSING:
                raise ValueError("unavailable runtime state must be missing")
            return
        if self.reason is ConversationRagReason.CORRUPT:
            if availability is not ConversationRagProjectionStateAvailability.CORRUPT:
                raise ValueError("corrupt runtime state must be corrupt")
            return
        if self.reason is ConversationRagReason.INCOMPATIBLE:
            if availability not in (
                ConversationRagProjectionStateAvailability.PRESENT,
                ConversationRagProjectionStateAvailability.CORRUPT,
            ):
                raise ValueError(
                    "incompatible runtime state must retain observed state"
                )
            return
        if self.reason in (
            ConversationRagReason.STALE,
            ConversationRagReason.FAILED,
        ):
            if availability is ConversationRagProjectionStateAvailability.CORRUPT:
                raise ValueError("stale or failed runtime state cannot be corrupt")
            return
        raise ValueError("runtime projection state reason is invalid")


@runtime_checkable
class ConversationRagRuntimeState(Protocol):
    """Persistent state/status capability injected beside a low-level service."""

    @property
    def workspace_key(self) -> str: ...

    @property
    def embedding(self) -> ConversationRagEmbeddingIdentity: ...

    def inspect_projection_state(
        self,
        session_id: str,
        /,
    ) -> ConversationRagRuntimeStateSnapshot: ...

    def mark_projection_stale(self, session_id: str, /) -> None: ...


class ConversationRagRuntimeError(ConversationRagError):
    """Fixed content-free failure at the authorization/runtime boundary."""

    def __init__(self) -> None:
        Exception.__init__(self, "conversation RAG runtime operation failed")


class ConversationRagRuntimeInspectionStatus(StrEnum):
    """Content-free comparison of one indexed state with its live target."""

    MISSING = "missing"
    READY = "ready"
    STALE = "stale"
    FAILED = "failed"
    CORRUPT = "corrupt"
    INCOMPATIBLE = "incompatible"


@dataclass(frozen=True, slots=True)
class ConversationRagRuntimeInspection:
    """One read-only, content-free authoritative/indexed comparison."""

    session_id: str
    status: ConversationRagRuntimeInspectionStatus
    authoritative_checkpoint: str
    indexed_checkpoint: str | None
    authoritative_conversations: int
    authoritative_events: int
    authoritative_documents: int
    authoritative_source_utf8_bytes: int
    indexed_documents: int
    rebuild_required: bool

    def __post_init__(self) -> None:
        _validate_identifier(self.session_id, label="session ID")
        if type(self.status) is not ConversationRagRuntimeInspectionStatus:
            raise TypeError("runtime inspection status must use the exact enum")
        if (
            type(self.authoritative_checkpoint) is not str
            or not self.authoritative_checkpoint.startswith("sha256:")
            or len(self.authoritative_checkpoint) != 71
        ):
            raise ValueError("authoritative checkpoint is invalid")
        if self.indexed_checkpoint is not None and (
            type(self.indexed_checkpoint) is not str or not self.indexed_checkpoint
        ):
            raise ValueError("indexed checkpoint is invalid")
        for label, value, maximum in (
            (
                "authoritative conversation count",
                self.authoritative_conversations,
                CONVERSATION_RAG_POLICY.max_conversations,
            ),
            (
                "authoritative event count",
                self.authoritative_events,
                CONVERSATION_RAG_POLICY.max_events,
            ),
            (
                "authoritative document count",
                self.authoritative_documents,
                CONVERSATION_RAG_POLICY.max_events,
            ),
            (
                "authoritative source byte count",
                self.authoritative_source_utf8_bytes,
                CONVERSATION_RAG_RUNTIME_POLICY.max_projected_source_utf8_bytes,
            ),
            (
                "indexed document count",
                self.indexed_documents,
                CONVERSATION_RAG_POLICY.max_events,
            ),
        ):
            if type(value) is not int or value < 0 or value > maximum:
                raise ValueError(f"{label} is invalid")
        if type(self.rebuild_required) is not bool:
            raise TypeError("rebuild_required must be a boolean")
        if self.rebuild_required != (
            self.status is not ConversationRagRuntimeInspectionStatus.READY
        ):
            raise ValueError("rebuild requirement is inconsistent with status")


@dataclass(frozen=True, slots=True)
class _ReadableFieldRange:
    field: CollaborationReadableField
    start_codepoint: int
    end_codepoint: int
    encoded_boundaries: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _ReadableEventDocument:
    record: CollaborationReadableRecord
    source: ConversationRagSourceDocument
    field_ranges: tuple[_ReadableFieldRange, ...]


@dataclass(frozen=True, slots=True)
class _AuthorizedTarget:
    request: ConversationRagProjectionRequest
    documents: dict[str, _ReadableEventDocument]
    conversation_count: int
    event_count: int
    source_utf8_bytes: int


def _validate_identifier(value: object, *, label: str) -> str:
    """Return an exact package identifier using an existing value contract."""

    del label
    scope = CollaborationHistorySearchScope(session_id=value)  # type: ignore[arg-type]
    return scope.session_id


def _strict_utf8(value: object, *, label: str, nonempty: bool = False) -> str:
    if type(value) is not str:
        raise TypeError(f"{label} must be a string")
    assert isinstance(value, str)
    if nonempty and not value:
        raise ValueError(f"{label} must not be empty")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8") from exc
    return value


def _synchronous_result(
    value: _SyncResultT,
    *,
    operation: str,
) -> _SyncResultT:
    """Reject dynamic async shapes without scheduling or warning."""

    if inspect.isawaitable(value):
        if inspect.iscoroutine(value):
            value.close()
        raise TypeError(f"{operation} must be synchronous")
    if inspect.isasyncgen(value):
        raise TypeError(f"{operation} must be synchronous")
    return value


def _is_declared_async(value: object) -> bool:
    call = type(value).__call__ if callable(value) else None
    return (
        inspect.iscoroutinefunction(value)
        or inspect.isasyncgenfunction(value)
        or inspect.iscoroutinefunction(call)
        or inspect.isasyncgenfunction(call)
    )


def _length_framed_sha256(domain: str, *fields: str) -> str:
    digest = hashlib.sha256()
    for value in (domain, *fields):
        encoded = value.encode("utf-8", errors="strict")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _readable_document(
    event: CollaborationEvent,
    record: CollaborationReadableRecord,
) -> _ReadableEventDocument:
    fields = [
        {
            "assignment_ordinal": field.assignment_ordinal,
            "input_ordinal": field.input_ordinal,
            "key": field.key.value,
            "text": field.text,
        }
        for field in record.fields
    ]
    text = json.dumps(
        {"fields": fields},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    ranges: list[_ReadableFieldRange] = []
    cursor = 0
    marker = '"text":'
    for field in record.fields:
        encoded = json.dumps(field.text, ensure_ascii=False, separators=(",", ":"))
        marker_start = text.find(marker + encoded, cursor)
        if marker_start < 0:
            raise ValueError("readable event document framing is inconsistent")
        start = marker_start + len(marker) + 1
        end = start + len(encoded) - 2
        encoded_parts = tuple(
            json.dumps(character, ensure_ascii=False)[1:-1] for character in field.text
        )
        if "".join(encoded_parts) != text[start:end]:
            raise ValueError("readable event field encoding is inconsistent")
        boundaries = [start]
        for part in encoded_parts:
            boundaries.append(boundaries[-1] + len(part))
        ranges.append(
            _ReadableFieldRange(
                field=field,
                start_codepoint=start,
                end_codepoint=end,
                encoded_boundaries=tuple(boundaries),
            )
        )
        cursor = end + 1
    document_id = _length_framed_sha256(
        _DOCUMENT_ID_DOMAIN,
        CONVERSATION_RAG_RUNTIME_SCHEMA_ID,
        record.session_id,
        record.conversation_id,
        record.event_id,
    )
    revision_id = "sha256:" + _length_framed_sha256(
        _REVISION_ID_DOMAIN,
        CONVERSATION_RAG_RUNTIME_SCHEMA_ID,
        record.event_id,
        encode_collaboration_event(event).decode("utf-8"),
        record.batch_digest,
        text,
    )
    identity = ConversationRagDocumentIdentity(
        key=ConversationRagDocumentKey(
            session_id=record.session_id,
            document_id=document_id,
        ),
        conversation_id=record.conversation_id,
        start_turn_id=record.turn_id,
        end_turn_id=record.turn_id,
        start_sequence=record.sequence,
        end_sequence=record.sequence,
        revision_id=revision_id,
    )
    return _ReadableEventDocument(
        record=record,
        source=ConversationRagSourceDocument(identity=identity, text=text),
        field_ranges=tuple(ranges),
    )


def _readable_event_documents(
    metadata: ConversationMetadata,
    raw_events: object,
) -> tuple[_ReadableEventDocument, ...]:
    if type(raw_events) is not tuple:
        raise TypeError("event store read must return a tuple")
    if any(type(event) is not CollaborationEvent for event in raw_events):
        raise TypeError("event store returned an invalid collaboration event")
    records = project_collaboration_readable_history(raw_events)
    if any(
        record.session_id != metadata.session_id
        or record.conversation_id != metadata.conversation_id
        for record in records
    ):
        raise ValueError("event store returned history for another conversation")
    return tuple(
        _readable_document(event, record)
        for event, record in zip(raw_events, records, strict=True)
    )


def _synchronous_member(
    owner: object,
    name: str,
    *,
    operation: str,
) -> Callable[..., object]:
    member = getattr(owner, name, None)
    if member is None or not callable(member) or _is_declared_async(member):
        raise TypeError(f"{operation} must be synchronous")
    return cast(Callable[..., object], member)


def _validate_snapshot_lease(lease: object, *, workspace_key: str) -> None:
    lease_workspace = _synchronous_result(
        getattr(lease, "workspace_key", None),
        operation="conversation storage lease workspace",
    )
    if type(lease_workspace) is not str or lease_workspace != workspace_key:
        raise TypeError("conversation storage lease crossed its workspace")
    validate = _synchronous_member(
        lease,
        "validate",
        operation="conversation storage lease validation",
    )
    outcome = _synchronous_result(
        validate(workspace_key=workspace_key),
        operation="conversation storage lease validation",
    )
    if outcome is not None:
        raise TypeError("conversation storage lease validation must return None")


def _read_authorized_target(
    services: ConversationStorageServices,
    session_id: str,
    embedding: ConversationRagEmbeddingIdentity,
    *,
    validate_snapshot: Callable[[], None],
) -> _AuthorizedTarget:
    """Build one target while a caller-defined observation remains valid."""

    validate_snapshot()
    raw_metadata = _synchronous_result(
        services.catalog.list(session_id, include_archived=False),
        operation="conversation catalog list",
    )
    validate_snapshot()
    if type(raw_metadata) is not tuple:
        raise TypeError("catalog list must return a tuple")
    if len(raw_metadata) > CONVERSATION_RAG_POLICY.max_conversations:
        raise ValueError("authorized projection exceeds the conversation budget")
    metadata: list[ConversationMetadata] = []
    seen_conversations: set[str] = set()
    for item in raw_metadata:
        if type(item) is not ConversationMetadata:
            raise TypeError("catalog returned invalid metadata")
        if (
            item.session_id != session_id
            or item.status is not ConversationStatus.ACTIVE
            or item.conversation_id in seen_conversations
        ):
            raise ValueError("catalog returned an invalid active target")
        seen_conversations.add(item.conversation_id)
        metadata.append(item)
    documents: list[_ReadableEventDocument] = []
    event_count = 0
    source_utf8_bytes = 0
    for item in sorted(metadata, key=lambda value: value.conversation_id):
        raw_events = _synchronous_result(
            services.event_store.read(session_id, item.conversation_id),
            operation="conversation event read",
        )
        validate_snapshot()
        if type(raw_events) is not tuple:
            raise TypeError("event store read must return a tuple")
        event_count += len(raw_events)
        if event_count > CONVERSATION_RAG_POLICY.max_events:
            raise ValueError("authorized projection exceeds the event budget")
        readable = _readable_event_documents(item, raw_events)
        for document in readable:
            source_utf8_bytes += len(
                document.source.text.encode("utf-8", errors="strict")
            )
            if (
                source_utf8_bytes
                > CONVERSATION_RAG_RUNTIME_POLICY.max_projected_source_utf8_bytes
            ):
                raise ValueError(
                    "authorized projection exceeds the source UTF-8 budget"
                )
        documents.extend(readable)
    if len(documents) > CONVERSATION_RAG_POLICY.max_events:
        raise ValueError("authorized projection exceeds the document budget")
    validate_snapshot()
    ordered = tuple(
        sorted(documents, key=lambda value: value.source.identity.key.document_id)
    )
    projection = ConversationRagProjectionRequest(
        session_id=session_id,
        schema_id=CONVERSATION_RAG_RUNTIME_SCHEMA_ID,
        embedding=embedding,
        documents=tuple(document.source for document in ordered),
    )
    return _AuthorizedTarget(
        request=projection,
        documents={
            document.source.identity.key.document_id: document for document in ordered
        },
        conversation_count=len(metadata),
        event_count=event_count,
        source_utf8_bytes=source_utf8_bytes,
    )


def _authorized_target_under_lease(
    services: ConversationStorageServices,
    session_id: str,
    embedding: ConversationRagEmbeddingIdentity,
    *,
    lease: object,
    workspace_key: str,
) -> _AuthorizedTarget:
    return _read_authorized_target(
        services,
        session_id,
        embedding,
        validate_snapshot=lambda: _validate_snapshot_lease(
            lease,
            workspace_key=workspace_key,
        ),
    )


@contextmanager
def _authorized_target_lease(
    services: ConversationStorageServices,
    session_id: str,
    embedding: ConversationRagEmbeddingIdentity,
    *,
    timeout_seconds: float,
) -> Iterator[tuple[_AuthorizedTarget, object, str]]:
    """Yield one target while retaining its bounded mutation lease."""

    workspace_key = services.provenance.workspace_key
    coordinator = services.mutation_coordinator
    coordinator_workspace = _synchronous_result(
        getattr(coordinator, "workspace_key", None),
        operation="conversation storage coordinator workspace",
    )
    if type(coordinator_workspace) is not str or coordinator_workspace != workspace_key:
        raise TypeError("conversation storage coordinator crossed its workspace")
    borrow = _synchronous_member(
        coordinator,
        "borrow",
        operation="conversation storage coordinator borrow",
    )
    manager = _synchronous_result(
        borrow(timeout_seconds=timeout_seconds),
        operation="conversation storage coordinator borrow",
    )
    enter = _synchronous_member(
        manager,
        "__enter__",
        operation="conversation storage coordinator enter",
    )
    exit_manager = _synchronous_member(
        manager,
        "__exit__",
        operation="conversation storage coordinator exit",
    )
    lease = _synchronous_result(
        enter(),
        operation="conversation storage coordinator enter",
    )
    try:
        target = _authorized_target_under_lease(
            services,
            session_id,
            embedding,
            lease=lease,
            workspace_key=workspace_key,
        )
        yield target, lease, workspace_key
    except BaseException as body_error:
        try:
            exit_outcome = _synchronous_result(
                exit_manager(
                    type(body_error),
                    body_error,
                    body_error.__traceback__,
                ),
                operation="conversation storage coordinator exit",
            )
        except BaseException:
            if not isinstance(body_error, Exception):
                raise body_error.with_traceback(body_error.__traceback__) from None
            raise
        if exit_outcome is not None and type(exit_outcome) is not bool:
            if not isinstance(body_error, Exception):
                raise body_error.with_traceback(body_error.__traceback__) from None
            raise TypeError(
                "conversation storage coordinator exit returned a value"
            ) from body_error
        if exit_outcome is True:
            if not isinstance(body_error, Exception):
                raise body_error.with_traceback(body_error.__traceback__) from None
            raise TypeError(
                "conversation storage coordinator suppressed a failure"
            ) from body_error
        raise
    exit_outcome = _synchronous_result(
        exit_manager(None, None, None),
        operation="conversation storage coordinator exit",
    )
    if exit_outcome is not None and exit_outcome is not False:
        raise TypeError("conversation storage coordinator exit returned a value")


def _authorized_target(
    services: ConversationStorageServices,
    session_id: str,
    embedding: ConversationRagEmbeddingIdentity,
) -> _AuthorizedTarget:
    """Read one coherent authoritative target under a bounded mutation lease."""

    with _authorized_target_lease(
        services,
        session_id,
        embedding,
        timeout_seconds=_SNAPSHOT_MUTATION_TIMEOUT_SECONDS,
    ) as leased:
        target, _lease, _workspace_key = leased
        return target


def _field_coordinate(
    field: CollaborationReadableField,
) -> tuple[object, int | None, int | None]:
    return field.key, field.assignment_ordinal, field.input_ordinal


def _field_range_for_citation(
    document: _ReadableEventDocument,
    citation: CollaborationHistoryCitation,
) -> _ReadableFieldRange | None:
    if (
        citation.session_id != document.record.session_id
        or citation.conversation_id != document.record.conversation_id
        or citation.event_id != document.record.event_id
        or citation.sequence != document.record.sequence
    ):
        return None
    coordinate = (
        citation.field_key,
        citation.assignment_ordinal,
        citation.input_ordinal,
    )
    return next(
        (
            field_range
            for field_range in document.field_ranges
            if _field_coordinate(field_range.field) == coordinate
        ),
        None,
    )


def _citation_for_range(
    document: _ReadableEventDocument,
    start: int,
    end: int,
) -> CollaborationHistoryCitation | None:
    for field_range in document.field_ranges:
        if not (
            field_range.start_codepoint <= start < end <= field_range.end_codepoint
        ):
            continue
        try:
            source_start = field_range.encoded_boundaries.index(start)
            source_end = field_range.encoded_boundaries.index(end)
        except ValueError:
            return None
        field = field_range.field
        record = document.record
        return CollaborationHistoryCitation(
            session_id=record.session_id,
            conversation_id=record.conversation_id,
            event_id=record.event_id,
            sequence=record.sequence,
            field_key=field.key,
            start_codepoint=source_start,
            end_codepoint=source_end,
            assignment_ordinal=field.assignment_ordinal,
            input_ordinal=field.input_ordinal,
        )
    return None


def _excerpt(
    document: _ReadableEventDocument,
    reference: ConversationRagSourceReference,
    *,
    citation: CollaborationHistoryCitation | None = None,
) -> tuple[ConversationRetrievalSource, str] | None:
    exact = citation or _citation_for_range(
        document,
        reference.start_codepoint,
        reference.end_codepoint,
    )
    if exact is None:
        return None
    text = document.source.text[reference.start_codepoint : reference.end_codepoint]
    return ConversationRetrievalSource(citation=exact), text


def _literal_reference(
    document: _ReadableEventDocument,
    citation: CollaborationHistoryCitation,
) -> ConversationRagSourceReference | None:
    field_range = _field_range_for_citation(document, citation)
    if field_range is None or citation.end_codepoint > len(field_range.field.text):
        return None
    start = field_range.encoded_boundaries[citation.start_codepoint]
    end = field_range.encoded_boundaries[citation.end_codepoint]
    if (
        start >= end
        or end - start > CONVERSATION_RAG_RUNTIME_POLICY.max_excerpt_codepoints
    ):
        return None
    fragment_id = "literal-" + _length_framed_sha256(
        _LITERAL_FRAGMENT_DOMAIN,
        document.source.identity.key.document_id,
        document.source.identity.revision_id,
        citation.event_id,
        citation.field_key.value,
        str(citation.assignment_ordinal),
        str(citation.input_ordinal),
        str(citation.start_codepoint),
        str(citation.end_codepoint),
    )
    return ConversationRagSourceReference(
        document=document.source.identity,
        fragment_id=fragment_id,
        start_codepoint=start,
        end_codepoint=end,
    )


def _canonical_literal_result(
    value: object,
    *,
    query: CollaborationHistorySearchQuery,
) -> CollaborationHistorySearchResult:
    if type(value) is not CollaborationHistorySearchResult:
        raise TypeError("collaboration history search returned an invalid result")
    if value.query != query:
        raise ValueError("collaboration history search result crossed its request")
    return CollaborationHistorySearchResult(
        query=value.query,
        normalized_query=value.normalized_query,
        hits=value.hits,
        matched_fields=value.matched_fields,
        scanned_records=value.scanned_records,
        scanned_fields=value.scanned_fields,
        scan_truncated=value.scan_truncated,
        truncated=value.truncated,
    )


def _literal_references(
    result: CollaborationHistorySearchResult,
    documents: dict[str, _ReadableEventDocument],
) -> tuple[
    list[tuple[ConversationRagSourceReference, ConversationRetrievalSource, str]], bool
]:
    by_event = {document.record.event_id: document for document in documents.values()}
    selected: list[
        tuple[ConversationRagSourceReference, ConversationRetrievalSource, str]
    ] = []
    dropped = False
    for hit in result.hits:
        if type(hit) is not CollaborationHistorySearchHit:
            dropped = True
            continue
        document = by_event.get(hit.citation.event_id)
        reference = (
            None if document is None else _literal_reference(document, hit.citation)
        )
        if document is None or reference is None:
            dropped = True
            continue
        excerpt = _excerpt(document, reference, citation=hit.citation)
        if excerpt is None:
            dropped = True
            continue
        source, text = excerpt
        selected.append((reference, source, text))
    return selected, dropped


def _canonical_semantic_result(
    value: object,
    *,
    request: ConversationRagRetrievalRequest,
) -> ConversationRagRetrievalResult:
    if type(value) is not ConversationRagRetrievalResult:
        raise TypeError("conversation RAG service returned an invalid result")
    canonical = ConversationRagRetrievalResult(
        request=value.request,
        outcome=value.outcome,
        reason=value.reason,
        candidates=value.candidates,
        truncated=value.truncated,
    )
    if canonical.request != request:
        raise ValueError("conversation RAG result crossed its request")
    return canonical


def _semantic_references(
    candidates: tuple[ConversationRagCandidate, ...],
    documents: dict[str, _ReadableEventDocument],
) -> tuple[
    list[tuple[ConversationRagSourceReference, ConversationRetrievalSource, str]], bool
]:
    selected: list[
        tuple[ConversationRagSourceReference, ConversationRetrievalSource, str]
    ] = []
    seen_documents: set[ConversationRagDocumentKey] = set()
    dropped = False
    for candidate in candidates:
        if type(candidate) is not ConversationRagCandidate:
            dropped = True
            continue
        reference = candidate.source
        document = documents.get(reference.document.key.document_id)
        if (
            document is None
            or reference.document != document.source.identity
            or reference.start_codepoint < 0
            or reference.end_codepoint > len(document.source.text)
            or reference.end_codepoint - reference.start_codepoint
            > CONVERSATION_RAG_RUNTIME_POLICY.max_excerpt_codepoints
        ):
            dropped = True
            continue
        if reference.document.key in seen_documents:
            continue
        excerpt = _excerpt(document, reference)
        if excerpt is None:
            dropped = True
            continue
        source, text = excerpt
        seen_documents.add(reference.document.key)
        selected.append((reference, source, text))
    return selected, dropped


def _context(
    outcome: ConversationRagAttemptOutcome,
    selected: list[
        tuple[ConversationRagSourceReference, ConversationRetrievalSource, str]
    ],
    *,
    truncated: bool,
) -> ConversationRagContext:
    retained: list[ConversationRagExcerpt] = []
    retained_bytes = 0
    seen_documents: set[ConversationRagDocumentKey] = set()
    omitted = truncated
    for reference, source, text in selected:
        document_key = reference.document.key
        if document_key in seen_documents:
            continue
        encoded_bytes = len(text.encode("utf-8"))
        if (
            len(retained) >= CONVERSATION_RAG_RUNTIME_POLICY.max_excerpts
            or retained_bytes + encoded_bytes
            > CONVERSATION_RAG_RUNTIME_POLICY.max_context_utf8_bytes
        ):
            omitted = True
            break
        seen_documents.add(document_key)
        retained_bytes += encoded_bytes
        retained.append(
            ConversationRagExcerpt(
                citation_id=f"g{len(retained) + 1}",
                source=source,
                reference=reference,
                content=text,
            )
        )
    if omitted and outcome.outcome is ConversationRagOutcome.COMPLETE:
        outcome = ConversationRagAttemptOutcome(
            outcome=ConversationRagOutcome.PARTIAL,
            reason=ConversationRagReason.PARTIAL,
        )
    return ConversationRagContext(
        outcome=outcome,
        excerpts=tuple(retained),
        retained_utf8_bytes=retained_bytes,
        truncated=omitted,
    )


def _state_embedding_for(
    services: ConversationStorageServices,
    state_port: ConversationRagRuntimeState,
) -> ConversationRagEmbeddingIdentity:
    if type(services) is not ConversationStorageServices:
        raise TypeError("conversation RAG runtime requires ConversationStorageServices")
    workspace_key = _synchronous_result(
        state_port.workspace_key,
        operation="conversation RAG state workspace",
    )
    embedding = _synchronous_result(
        state_port.embedding,
        operation="conversation RAG state embedding",
    )
    if (
        type(workspace_key) is not str
        or workspace_key != services.provenance.workspace_key
        or type(embedding) is not ConversationRagEmbeddingIdentity
    ):
        raise TypeError("conversation RAG state crossed its workspace")
    return embedding


def _runtime_state_snapshot(
    state_port: ConversationRagRuntimeState,
    session_id: str,
) -> ConversationRagRuntimeStateSnapshot:
    inspector = _synchronous_member(
        state_port,
        "inspect_projection_state",
        operation="conversation RAG state inspection",
    )
    raw = _synchronous_result(
        inspector(session_id),
        operation="conversation RAG state inspection",
    )
    if type(raw) is not ConversationRagRuntimeStateSnapshot:
        raise TypeError("conversation RAG state port returned an invalid snapshot")
    canonical = ConversationRagRuntimeStateSnapshot(
        session_id=raw.session_id,
        state=raw.state,
        reason=raw.reason,
    )
    if canonical.session_id != session_id:
        raise ValueError("conversation RAG state crossed its session")
    return canonical


def _authorized_target_checkpoint(target: _AuthorizedTarget) -> str:
    fields: list[str] = [
        target.request.session_id,
        target.request.schema_id,
        target.request.embedding.model_id,
        str(target.request.embedding.dimensions),
        str(CONVERSATION_RAG_POLICY.max_fragment_codepoints),
        str(CONVERSATION_RAG_POLICY.fragment_overlap_codepoints),
    ]
    for source in target.request.documents:
        identity = source.identity
        fields.extend(
            (
                identity.key.document_id,
                identity.conversation_id,
                identity.start_turn_id,
                identity.end_turn_id,
                str(identity.start_sequence),
                str(identity.end_sequence),
                identity.revision_id,
            )
        )
    return "sha256:" + _length_framed_sha256(
        _AUTHORIZED_TARGET_TOKEN_DOMAIN,
        *fields,
    )


def _inspection_status(
    snapshot: ConversationRagRuntimeStateSnapshot,
    target: _AuthorizedTarget,
) -> ConversationRagRuntimeInspectionStatus:
    if snapshot.reason is ConversationRagReason.UNAVAILABLE:
        return ConversationRagRuntimeInspectionStatus.MISSING
    if snapshot.reason is ConversationRagReason.STALE:
        return ConversationRagRuntimeInspectionStatus.STALE
    if snapshot.reason is ConversationRagReason.FAILED:
        return ConversationRagRuntimeInspectionStatus.FAILED
    if snapshot.reason is ConversationRagReason.CORRUPT:
        return ConversationRagRuntimeInspectionStatus.CORRUPT
    if snapshot.reason is ConversationRagReason.INCOMPATIBLE:
        return ConversationRagRuntimeInspectionStatus.INCOMPATIBLE
    if snapshot.reason is not ConversationRagReason.NONE:
        raise ValueError("runtime state reason cannot be inspected")
    checkpoint = snapshot.state.checkpoint
    if checkpoint is None:
        raise ValueError("ready runtime state omitted its checkpoint")
    if (
        checkpoint.session_id != target.request.session_id
        or checkpoint.schema_id != target.request.schema_id
        or checkpoint.embedding != target.request.embedding
        or checkpoint.max_fragment_codepoints
        != CONVERSATION_RAG_POLICY.max_fragment_codepoints
        or checkpoint.fragment_overlap_codepoints
        != CONVERSATION_RAG_POLICY.fragment_overlap_codepoints
    ):
        return ConversationRagRuntimeInspectionStatus.INCOMPATIBLE
    if tuple(entry.document for entry in checkpoint.entries) != tuple(
        document.identity for document in target.request.documents
    ):
        return ConversationRagRuntimeInspectionStatus.STALE
    return ConversationRagRuntimeInspectionStatus.READY


def inspect_authorized_conversation_rag(
    conversation_storage_services: ConversationStorageServices,
    state_port: ConversationRagRuntimeState,
    session_id: str,
    /,
) -> ConversationRagRuntimeInspection:
    """Optimistically compare one stable live target without mutating storage."""

    session = _validate_identifier(session_id, label="session ID")
    try:
        embedding = _state_embedding_for(conversation_storage_services, state_port)
        indexed_before = _runtime_state_snapshot(state_port, session)
        target_before = _read_authorized_target(
            conversation_storage_services,
            session,
            embedding,
            validate_snapshot=lambda: None,
        )
        target_after = _read_authorized_target(
            conversation_storage_services,
            session,
            embedding,
            validate_snapshot=lambda: None,
        )
        indexed_after = _runtime_state_snapshot(state_port, session)
        if target_before != target_after or indexed_before != indexed_after:
            raise ValueError("conversation RAG inspection changed while observed")
        status = _inspection_status(indexed_after, target_after)
        checkpoint = indexed_after.state.checkpoint
        return ConversationRagRuntimeInspection(
            session_id=session,
            status=status,
            authoritative_checkpoint=_authorized_target_checkpoint(target_after),
            indexed_checkpoint=None if checkpoint is None else checkpoint.token,
            authoritative_conversations=target_after.conversation_count,
            authoritative_events=target_after.event_count,
            authoritative_documents=len(target_after.request.documents),
            authoritative_source_utf8_bytes=target_after.source_utf8_bytes,
            indexed_documents=0 if checkpoint is None else len(checkpoint.entries),
            rebuild_required=(
                status is not ConversationRagRuntimeInspectionStatus.READY
            ),
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise ConversationRagRuntimeError() from None


class ConversationRagRuntimeService:
    """Stateful authorization wrapper over one provider-neutral RAG service."""

    __slots__ = ("_automatic_enabled", "_low_level_service", "_state_port")

    def __init__(
        self,
        low_level_service: ConversationRagService,
        state_port: ConversationRagRuntimeState,
        /,
        *,
        automatic_enabled: bool = False,
    ) -> None:
        required_service_methods = (
            "project_documents",
            "rebuild_projection",
            "retrieve_semantic",
            "retrieve_hybrid",
        )
        for name in required_service_methods:
            method = getattr(low_level_service, name, None)
            if method is None or not callable(method) or _is_declared_async(method):
                raise TypeError("low-level conversation RAG service is invalid")
        required_state_members = (
            "inspect_projection_state",
            "mark_projection_stale",
        )
        for name in required_state_members:
            method = getattr(state_port, name, None)
            if method is None or not callable(method) or _is_declared_async(method):
                raise TypeError("conversation RAG runtime state port is invalid")
        if type(automatic_enabled) is not bool:
            raise TypeError("automatic conversation RAG must be a boolean")
        self._low_level_service = low_level_service
        self._state_port = state_port
        self._automatic_enabled = automatic_enabled

    @property
    def automatic_enabled(self) -> bool:
        """Report whether an absent forced query triggers one automatic attempt."""

        return self._automatic_enabled

    def _embedding_for(
        self,
        services: ConversationStorageServices,
    ) -> ConversationRagEmbeddingIdentity:
        try:
            return _state_embedding_for(services, self._state_port)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:  # noqa: BLE001 - translate arbitrary injected port failures
            raise ConversationRagRuntimeError() from None

    def _inspect_state(
        self,
        session_id: str,
    ) -> ConversationRagRuntimeStateSnapshot:
        return _runtime_state_snapshot(self._state_port, session_id)

    def _mark_stale(self, session_id: str) -> None:
        marker: Callable[[str], object] = self._state_port.mark_projection_stale
        outcome = _synchronous_result(
            marker(session_id),
            operation="conversation RAG stale marker",
        )
        if outcome is not None:
            raise TypeError("conversation RAG stale marker must return None")

    def _best_effort_mark_stale(self, session_id: str) -> None:
        try:
            self._mark_stale(session_id)
        except Exception:  # noqa: BLE001 - stale marking is explicitly best-effort
            return

    def _query(self, turn_text: str, forced_query: str | None) -> str | None:
        source = _strict_utf8(turn_text, label="conversation RAG turn text")
        if forced_query is not None:
            return normalize_conversation_literal_query(
                forced_query,
                max_characters=CONVERSATION_RAG_RUNTIME_POLICY.max_query_codepoints,
            )
        if not self._automatic_enabled:
            return None
        normalized = normalize_conversation_literal_text(source)
        capped = normalized[
            : CONVERSATION_RAG_RUNTIME_POLICY.max_query_codepoints
        ].rstrip()
        return capped or None

    def preflight(
        self,
        conversation_storage_services: ConversationStorageServices,
        session_id: str,
        turn_text: str,
        /,
        *,
        forced_query: str | None = None,
    ) -> ConversationRagContext | None:
        """Attempt one bounded retrieval and authorize every returned excerpt."""

        session = _validate_identifier(session_id, label="session ID")
        if forced_query is not None and type(forced_query) is not str:
            raise TypeError("forced conversation RAG query must be a string")
        query_text = self._query(turn_text, forced_query)
        if query_text is None:
            return None
        if type(conversation_storage_services) is not ConversationStorageServices:
            raise TypeError(
                "conversation RAG runtime requires ConversationStorageServices"
            )
        try:
            embedding = self._embedding_for(conversation_storage_services)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:  # noqa: BLE001 - valid injected state may fail ordinarily
            return _context(
                ConversationRagAttemptOutcome(
                    outcome=ConversationRagOutcome.LITERAL_FALLBACK,
                    reason=ConversationRagReason.FAILED,
                ),
                [],
                truncated=False,
            )
        literal_query = CollaborationHistorySearchQuery(
            text=query_text,
            limit=CONVERSATION_RAG_RUNTIME_POLICY.max_literal_candidates,
        )
        literal_result: CollaborationHistorySearchResult | None = None
        literal_failed = False
        try:
            raw_literal = _synchronous_result(
                conversation_storage_services.search.search(
                    CollaborationHistorySearchScope(session_id=session),
                    literal_query,
                ),
                operation="conversation literal search",
            )
            literal_result = _canonical_literal_result(
                raw_literal,
                query=literal_query,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:  # noqa: BLE001 - literal ports are injected boundaries
            literal_failed = True

        rag_request = ConversationRagRetrievalRequest(
            session_id=session,
            text=query_text,
            hit_limit=CONVERSATION_RAG_RUNTIME_POLICY.max_semantic_candidates,
        )
        semantic: ConversationRagRetrievalResult | None = None
        semantic_reason = ConversationRagReason.FAILED
        semantic_state_used = False
        try:
            state = self._inspect_state(session)
            semantic_reason = state.reason
            if state.reason is ConversationRagReason.NONE:
                semantic_state_used = True
                semantic = _canonical_semantic_result(
                    _synchronous_result(
                        self._low_level_service.retrieve_semantic(
                            rag_request,
                            state.state,
                        ),
                        operation="conversation RAG semantic retrieval",
                    ),
                    request=rag_request,
                )
                semantic_reason = semantic.reason
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:  # noqa: BLE001 - semantic providers are injected boundaries
            semantic = None
            semantic_reason = ConversationRagReason.FAILED

        try:
            target = _authorized_target(
                conversation_storage_services,
                session,
                embedding,
            )
            documents = target.documents
            literal_selected: list[
                tuple[
                    ConversationRagSourceReference,
                    ConversationRetrievalSource,
                    str,
                ]
            ] = []
            literal_dropped = False
            if literal_result is not None:
                literal_selected, literal_dropped = _literal_references(
                    literal_result,
                    documents,
                )
            semantic_selected: list[
                tuple[
                    ConversationRagSourceReference,
                    ConversationRetrievalSource,
                    str,
                ]
            ] = []
            semantic_dropped = False
            if semantic is not None:
                semantic_selected, semantic_dropped = _semantic_references(
                    semantic.candidates,
                    documents,
                )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:  # noqa: BLE001 - authoritative ports are injected boundaries
            if semantic_state_used:
                self._best_effort_mark_stale(session)
            return _context(
                ConversationRagAttemptOutcome(
                    outcome=ConversationRagOutcome.LITERAL_FALLBACK,
                    reason=ConversationRagReason.FAILED,
                ),
                [],
                truncated=False,
            )

        if semantic_dropped:
            self._best_effort_mark_stale(session)

        selected = [*literal_selected, *semantic_selected]
        truncated = (
            literal_dropped
            or semantic_dropped
            or (literal_result is not None and literal_result.truncated)
            or (semantic is not None and semantic.truncated)
        )
        if (
            semantic is None
            or semantic.outcome is ConversationRagOutcome.LITERAL_FALLBACK
        ):
            reason = semantic_reason
            if literal_failed:
                reason = ConversationRagReason.FAILED
            attempt = ConversationRagAttemptOutcome(
                outcome=ConversationRagOutcome.LITERAL_FALLBACK,
                reason=reason,
            )
        elif (
            semantic.outcome is ConversationRagOutcome.PARTIAL
            or literal_failed
            or literal_dropped
            or semantic_dropped
            or (literal_result is not None and literal_result.truncated)
            or semantic.truncated
        ):
            attempt = ConversationRagAttemptOutcome(
                outcome=ConversationRagOutcome.PARTIAL,
                reason=ConversationRagReason.PARTIAL,
            )
        else:
            attempt = ConversationRagAttemptOutcome(
                outcome=ConversationRagOutcome.COMPLETE,
                reason=ConversationRagReason.NONE,
            )
        return _context(attempt, selected, truncated=truncated)

    def search_authorized(
        self,
        conversation_storage_services: ConversationStorageServices,
        session_id: str,
        query: str,
        /,
    ) -> ConversationRagContext:
        """Run one explicit bounded search without consulting automatic policy."""

        if type(query) is not str:
            raise TypeError("conversation RAG search query must be a string")
        context = self.preflight(
            conversation_storage_services,
            session_id,
            query,
            forced_query=query,
        )
        if context is None:
            raise ConversationRagRuntimeError() from None
        return context

    def project_authorized(
        self,
        conversation_storage_services: ConversationStorageServices,
        session_id: str,
        /,
        *,
        rebuild: bool = False,
    ) -> ConversationRagProjectionResult:
        """Project the complete current ACTIVE target and verify publication."""

        session = _validate_identifier(session_id, label="session ID")
        if type(rebuild) is not bool:
            raise TypeError("conversation RAG rebuild must be a boolean")
        if type(conversation_storage_services) is not ConversationStorageServices:
            raise TypeError(
                "conversation RAG runtime requires ConversationStorageServices"
            )
        try:
            embedding = self._embedding_for(conversation_storage_services)
            with _authorized_target_lease(
                conversation_storage_services,
                session,
                embedding,
                timeout_seconds=_PROJECTION_MUTATION_TIMEOUT_SECONDS,
            ) as leased:
                target, lease, workspace_key = leased
                request = target.request
                before = self._inspect_state(session)
                force_rebuild = rebuild or before.reason in (
                    ConversationRagReason.CORRUPT,
                    ConversationRagReason.INCOMPATIBLE,
                )
                result = _synchronous_result(
                    (
                        self._low_level_service.rebuild_projection(
                            request,
                            before.state,
                        )
                        if force_rebuild
                        else self._low_level_service.project_documents(
                            request,
                            before.state,
                        )
                    ),
                    operation="conversation RAG projection",
                )
                if type(result) is not ConversationRagProjectionResult:
                    raise TypeError(
                        "conversation RAG projection returned an invalid result"
                    )
                canonical = ConversationRagProjectionResult(
                    status_before=result.status_before,
                    outcome=result.outcome,
                    attempted_documents=result.attempted_documents,
                    completed_documents=result.completed_documents,
                    checkpoint=result.checkpoint,
                )
                checkpoint = canonical.checkpoint
                if (
                    checkpoint.session_id != session
                    or checkpoint.schema_id != CONVERSATION_RAG_RUNTIME_SCHEMA_ID
                    or checkpoint.embedding != embedding
                    or tuple(entry.document for entry in checkpoint.entries)
                    != tuple(document.identity for document in request.documents)
                ):
                    raise ValueError("conversation RAG checkpoint crossed its target")
                after = self._inspect_state(session)
                if (
                    after.reason is not ConversationRagReason.NONE
                    or after.state.availability
                    is not ConversationRagProjectionStateAvailability.PRESENT
                    or after.state.checkpoint != checkpoint
                ):
                    raise ValueError("conversation RAG publication was not persisted")
                current = _authorized_target_under_lease(
                    conversation_storage_services,
                    session,
                    embedding,
                    lease=lease,
                    workspace_key=workspace_key,
                )
                if current.request != request:
                    raise ValueError(
                        "conversation RAG source changed during publication"
                    )
                return canonical
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:  # noqa: BLE001 - projection collaborators are injected
            self._best_effort_mark_stale(session)
            raise ConversationRagRuntimeError() from None


__all__ = [
    "CONVERSATION_RAG_RUNTIME_POLICY",
    "CONVERSATION_RAG_RUNTIME_SCHEMA_ID",
    "ConversationRagRuntimeInspection",
    "ConversationRagRuntimeInspectionStatus",
    "ConversationRagRuntimeError",
    "ConversationRagRuntimePolicy",
    "ConversationRagRuntimeService",
    "ConversationRagRuntimeState",
    "ConversationRagRuntimeStateSnapshot",
    "inspect_authorized_conversation_rag",
]
