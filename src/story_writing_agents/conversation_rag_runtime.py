"""Authorized runtime use of provider-neutral conversation RAG services."""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Callable
from dataclasses import dataclass
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
    ConversationCitation,
    ConversationSearchHit,
    ConversationSearchMatchKind,
    ConversationSearchQuery,
    ConversationSearchResult,
    ConversationSearchScope,
)
from .conversation_storage.contracts import ConversationStorageServices
from .conversations import (
    ConversationEvent,
    ConversationEventKind,
    ConversationMetadata,
    ConversationStatus,
)

CONVERSATION_RAG_RUNTIME_SCHEMA_ID = "story-conversation-rag-visible-turn-v1"

_DOCUMENT_ID_DOMAIN = "story-conversation-rag-visible-turn-document-id-v1"
_REVISION_ID_DOMAIN = "story-conversation-rag-visible-turn-revision-v1"
_LITERAL_FRAGMENT_DOMAIN = "story-conversation-rag-visible-turn-literal-v1"
_MAX_QUERY_CODEPOINTS = 200
_MAX_CONTEXT_UTF8_BYTES = 32_768
_MAX_PROJECTED_SOURCE_UTF8_BYTES = 32 * 1_024 * 1_024
_SNAPSHOT_MUTATION_TIMEOUT_SECONDS = 30.0

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
        if self.reason in (
            ConversationRagReason.CORRUPT,
            ConversationRagReason.INCOMPATIBLE,
        ):
            if availability is not ConversationRagProjectionStateAvailability.CORRUPT:
                raise ValueError("invalid runtime state must be corrupt")
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


@dataclass(frozen=True, slots=True)
class _VisibleTurnDocument:
    metadata: ConversationMetadata
    user: ConversationEvent
    reply: ConversationEvent
    source: ConversationRagSourceDocument
    subject_range: tuple[int, int]
    user_range: tuple[int, int]
    reply_range: tuple[int, int]


def _validate_identifier(value: object, *, label: str) -> str:
    """Return an exact package identifier using an existing value contract."""

    del label
    scope = ConversationSearchScope(session_id=value)  # type: ignore[arg-type]
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


def _event_digest_fields(event: ConversationEvent) -> tuple[str, ...]:
    """Return exact ``ConversationEvent.to_dict`` values in schema order."""

    row = event.to_dict()
    return tuple(
        str(row[name])
        for name in (
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
        )
    )


def _source_text(
    metadata: ConversationMetadata,
    user: ConversationEvent,
    reply: ConversationEvent,
) -> tuple[str, tuple[int, int], tuple[int, int], tuple[int, int]]:
    subject_prefix = "Subject: "
    user_prefix = "\nUser: "
    reply_prefix = f"\nAgent[{reply.sender}]: "
    text = (
        subject_prefix
        + metadata.subject
        + user_prefix
        + user.content
        + reply_prefix
        + reply.content
    )
    subject_start = len(subject_prefix)
    subject_end = subject_start + len(metadata.subject)
    user_start = subject_end + len(user_prefix)
    user_end = user_start + len(user.content)
    reply_start = user_end + len(reply_prefix)
    reply_end = reply_start + len(reply.content)
    return (
        text,
        (subject_start, subject_end),
        (user_start, user_end),
        (reply_start, reply_end),
    )


def _visible_document(
    metadata: ConversationMetadata,
    user: ConversationEvent,
    reply: ConversationEvent,
) -> _VisibleTurnDocument:
    text, subject_range, user_range, reply_range = _source_text(
        metadata,
        user,
        reply,
    )
    document_id = _length_framed_sha256(
        _DOCUMENT_ID_DOMAIN,
        CONVERSATION_RAG_RUNTIME_SCHEMA_ID,
        metadata.session_id,
        metadata.conversation_id,
        user.turn_id,
    )
    revision_fields = (
        CONVERSATION_RAG_RUNTIME_SCHEMA_ID,
        str(metadata.schema_version),
        metadata.session_id,
        metadata.conversation_id,
        metadata.subject,
        str(metadata.subject_revision),
        *_event_digest_fields(user),
        *_event_digest_fields(reply),
        text,
    )
    revision_id = "sha256:" + _length_framed_sha256(
        _REVISION_ID_DOMAIN,
        *revision_fields,
    )
    identity = ConversationRagDocumentIdentity(
        key=ConversationRagDocumentKey(
            session_id=metadata.session_id,
            document_id=document_id,
        ),
        conversation_id=metadata.conversation_id,
        start_turn_id=user.turn_id,
        end_turn_id=user.turn_id,
        start_sequence=user.sequence,
        end_sequence=reply.sequence,
        revision_id=revision_id,
    )
    return _VisibleTurnDocument(
        metadata=metadata,
        user=user,
        reply=reply,
        source=ConversationRagSourceDocument(identity=identity, text=text),
        subject_range=subject_range,
        user_range=user_range,
        reply_range=reply_range,
    )


def _complete_visible_documents(
    metadata: ConversationMetadata,
    raw_events: object,
) -> tuple[_VisibleTurnDocument, ...]:
    if type(raw_events) is not tuple:
        raise TypeError("event store read must return a tuple")
    users: dict[str, ConversationEvent] = {}
    completed: set[str] = set()
    documents: list[_VisibleTurnDocument] = []
    for expected_sequence, event in enumerate(raw_events, start=1):
        if type(event) is not ConversationEvent:
            raise TypeError("event store returned an invalid event")
        if (
            event.session_id != metadata.session_id
            or event.conversation_id != metadata.conversation_id
            or event.sequence != expected_sequence
        ):
            raise ValueError("event store returned an invalid transcript")
        if event.kind is ConversationEventKind.USER_MESSAGE:
            if event.turn_id in users:
                raise ValueError("event store returned a duplicate user event")
            users[event.turn_id] = event
            continue
        user = users.get(event.turn_id)
        if user is None or event.turn_id in completed:
            raise ValueError("event store returned an invalid turn outcome")
        completed.add(event.turn_id)
        if event.kind is ConversationEventKind.AGENT_REPLY:
            if event.sender != user.recipient:
                raise ValueError("event store returned a reply from the wrong agent")
            documents.append(_visible_document(metadata, user, event))
    return tuple(documents)


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


def _authorized_target_under_lease(
    services: ConversationStorageServices,
    session_id: str,
    embedding: ConversationRagEmbeddingIdentity,
    *,
    lease: object,
    workspace_key: str,
) -> tuple[ConversationRagProjectionRequest, dict[str, _VisibleTurnDocument]]:
    _validate_snapshot_lease(lease, workspace_key=workspace_key)
    raw_metadata = _synchronous_result(
        services.catalog.list(session_id, include_archived=False),
        operation="conversation catalog list",
    )
    _validate_snapshot_lease(lease, workspace_key=workspace_key)
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
    documents: list[_VisibleTurnDocument] = []
    event_count = 0
    source_utf8_bytes = 0
    for item in sorted(metadata, key=lambda value: value.conversation_id):
        raw_events = _synchronous_result(
            services.event_store.read(session_id, item.conversation_id),
            operation="conversation event read",
        )
        _validate_snapshot_lease(lease, workspace_key=workspace_key)
        if type(raw_events) is not tuple:
            raise TypeError("event store read must return a tuple")
        event_count += len(raw_events)
        if event_count > CONVERSATION_RAG_POLICY.max_events:
            raise ValueError("authorized projection exceeds the event budget")
        visible = _complete_visible_documents(item, raw_events)
        for document in visible:
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
        documents.extend(visible)
    if len(documents) > CONVERSATION_RAG_POLICY.max_events:
        raise ValueError("authorized projection exceeds the document budget")
    _validate_snapshot_lease(lease, workspace_key=workspace_key)
    ordered = tuple(
        sorted(documents, key=lambda value: value.source.identity.key.document_id)
    )
    projection = ConversationRagProjectionRequest(
        session_id=session_id,
        schema_id=CONVERSATION_RAG_RUNTIME_SCHEMA_ID,
        embedding=embedding,
        documents=tuple(document.source for document in ordered),
    )
    return projection, {
        document.source.identity.key.document_id: document for document in ordered
    }


def _authorized_target(
    services: ConversationStorageServices,
    session_id: str,
    embedding: ConversationRagEmbeddingIdentity,
) -> tuple[ConversationRagProjectionRequest, dict[str, _VisibleTurnDocument]]:
    """Read one coherent authoritative target under a bounded mutation lease."""

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
        borrow(timeout_seconds=_SNAPSHOT_MUTATION_TIMEOUT_SECONDS),
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
        result = _authorized_target_under_lease(
            services,
            session_id,
            embedding,
            lease=lease,
            workspace_key=workspace_key,
        )
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
    return result


def _citation_for_range(
    document: _VisibleTurnDocument,
    start: int,
    end: int,
) -> ConversationCitation:
    for interval, event in (
        (document.subject_range, None),
        (document.user_range, document.user),
        (document.reply_range, document.reply),
    ):
        if start < interval[1] and end > interval[0]:
            if event is None:
                break
            return ConversationCitation(
                session_id=event.session_id,
                conversation_id=event.conversation_id,
                turn_id=event.turn_id,
                sequence=event.sequence,
                event_kind=event.kind,
            )
    identity = document.source.identity
    return ConversationCitation(
        session_id=identity.key.session_id,
        conversation_id=identity.conversation_id,
        turn_id=None,
        sequence=None,
        event_kind=None,
    )


def _excerpt(
    document: _VisibleTurnDocument,
    reference: ConversationRagSourceReference,
) -> tuple[ConversationRetrievalSource, str]:
    text = document.source.text[reference.start_codepoint : reference.end_codepoint]
    source = ConversationRetrievalSource(
        subject=document.metadata.subject,
        citation=_citation_for_range(
            document,
            reference.start_codepoint,
            reference.end_codepoint,
        ),
    )
    return source, text


def _literal_reference(
    document: _VisibleTurnDocument,
    interval: tuple[int, int],
) -> ConversationRagSourceReference:
    start, content_end = interval
    end = min(
        content_end,
        start + CONVERSATION_RAG_RUNTIME_POLICY.max_excerpt_codepoints,
    )
    fragment_id = "literal-" + _length_framed_sha256(
        _LITERAL_FRAGMENT_DOMAIN,
        document.source.identity.key.document_id,
        document.source.identity.revision_id,
        str(start),
        str(end),
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
    query: ConversationSearchQuery,
) -> ConversationSearchResult:
    if type(value) is not ConversationSearchResult:
        raise TypeError("conversation search returned an invalid result")
    if value.query != query:
        raise ValueError("conversation search result crossed its request")
    return ConversationSearchResult(
        query=value.query,
        normalized_query=value.normalized_query,
        hits=value.hits,
        matched_conversations=value.matched_conversations,
        scanned_conversations=value.scanned_conversations,
        scanned_events=value.scanned_events,
        scan_truncated=value.scan_truncated,
        truncated=value.truncated,
    )


def _literal_references(
    result: ConversationSearchResult,
    documents: dict[str, _VisibleTurnDocument],
) -> tuple[
    list[tuple[ConversationRagSourceReference, ConversationRetrievalSource, str]], bool
]:
    by_conversation: dict[str, list[_VisibleTurnDocument]] = {}
    for indexed_document in documents.values():
        by_conversation.setdefault(
            indexed_document.source.identity.conversation_id,
            [],
        ).append(indexed_document)
    selected: list[
        tuple[ConversationRagSourceReference, ConversationRetrievalSource, str]
    ] = []
    dropped = False
    for hit in result.hits:
        if type(hit) is not ConversationSearchHit:
            dropped = True
            continue
        candidates = by_conversation.get(hit.citation.conversation_id, [])
        if not candidates:
            dropped = True
            continue
        metadata = candidates[0].metadata
        if (
            hit.citation.session_id != metadata.session_id
            or hit.subject != metadata.subject
            or hit.subject_revision != metadata.subject_revision
            or hit.status is not ConversationStatus.ACTIVE
        ):
            dropped = True
            continue
        document: _VisibleTurnDocument | None = None
        interval: tuple[int, int] | None = None
        if hit.match_kind is ConversationSearchMatchKind.TRANSCRIPT:
            for candidate in candidates:
                if hit.citation.turn_id != candidate.user.turn_id:
                    continue
                if (
                    hit.citation.sequence == candidate.user.sequence
                    and hit.citation.event_kind is ConversationEventKind.USER_MESSAGE
                ):
                    document, interval = candidate, candidate.user_range
                    break
                if (
                    hit.citation.sequence == candidate.reply.sequence
                    and hit.citation.event_kind is ConversationEventKind.AGENT_REPLY
                ):
                    document, interval = candidate, candidate.reply_range
                    break
        elif (
            hit.match_kind
            in (
                ConversationSearchMatchKind.EXACT_SUBJECT,
                ConversationSearchMatchKind.SUBJECT_PREFIX,
                ConversationSearchMatchKind.SUBJECT_SUBSTRING,
            )
            and hit.citation.sequence is None
        ):
            document = max(
                candidates,
                key=lambda value: (
                    value.reply.sequence,
                    value.source.identity.key.document_id,
                ),
            )
            interval = document.subject_range
        if document is None or interval is None or interval[0] >= interval[1]:
            dropped = True
            continue
        reference = _literal_reference(document, interval)
        source, text = _excerpt(document, reference)
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
    documents: dict[str, _VisibleTurnDocument],
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
        source, text = _excerpt(document, reference)
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
        if type(services) is not ConversationStorageServices:
            raise TypeError(
                "conversation RAG runtime requires ConversationStorageServices"
            )
        try:
            workspace_key = _synchronous_result(
                self._state_port.workspace_key,
                operation="conversation RAG state workspace",
            )
            embedding = _synchronous_result(
                self._state_port.embedding,
                operation="conversation RAG state embedding",
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:  # noqa: BLE001 - translate arbitrary injected port failures
            raise ConversationRagRuntimeError() from None
        if (
            type(workspace_key) is not str
            or workspace_key != services.provenance.workspace_key
            or type(embedding) is not ConversationRagEmbeddingIdentity
        ):
            raise ConversationRagRuntimeError() from None
        return embedding

    def _inspect_state(
        self,
        session_id: str,
    ) -> ConversationRagRuntimeStateSnapshot:
        raw = _synchronous_result(
            self._state_port.inspect_projection_state(session_id),
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
        literal_query = ConversationSearchQuery(
            text=query_text,
            limit=CONVERSATION_RAG_RUNTIME_POLICY.max_literal_candidates,
            include_archived=False,
        )
        literal_result: ConversationSearchResult | None = None
        literal_failed = False
        try:
            raw_literal = _synchronous_result(
                conversation_storage_services.search.search(
                    ConversationSearchScope(session_id=session),
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
            _projection, documents = _authorized_target(
                conversation_storage_services,
                session,
                embedding,
            )
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
            request, _documents = _authorized_target(
                conversation_storage_services,
                session,
                embedding,
            )
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
            current, _current_documents = _authorized_target(
                conversation_storage_services,
                session,
                embedding,
            )
            if current != request:
                raise ValueError("conversation RAG source changed during publication")
            return canonical
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:  # noqa: BLE001 - projection collaborators are injected
            self._best_effort_mark_stale(session)
            raise ConversationRagRuntimeError() from None


__all__ = [
    "CONVERSATION_RAG_RUNTIME_POLICY",
    "CONVERSATION_RAG_RUNTIME_SCHEMA_ID",
    "ConversationRagRuntimeError",
    "ConversationRagRuntimePolicy",
    "ConversationRagRuntimeService",
    "ConversationRagRuntimeState",
    "ConversationRagRuntimeStateSnapshot",
]
