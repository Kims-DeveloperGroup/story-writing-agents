"""Provider-neutral contracts for optional conversation RAG integration."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


def _require_exact_type(name: str, value: object, expected: type[object]) -> None:
    if type(value) is not expected:
        raise TypeError(
            f"{name} must be exactly {expected.__name__}, not {type(value).__name__}"
        )


def _identifier(name: str, value: object) -> str:
    _require_exact_type(name, value, str)
    assert isinstance(value, str)
    if _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{name} must match [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}")
    return value


def _nonblank_string(name: str, value: object) -> str:
    _require_exact_type(name, value, str)
    assert isinstance(value, str)
    if not value or value.isspace():
        raise ValueError(f"{name} must not be empty or whitespace-only")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{name} must be valid UTF-8") from exc
    return value


def _string(name: str, value: object) -> str:
    _require_exact_type(name, value, str)
    assert isinstance(value, str)
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{name} must be valid UTF-8") from exc
    return value


def _positive_integer(name: str, value: object) -> int:
    _require_exact_type(name, value, int)
    assert isinstance(value, int)
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _nonnegative_integer(name: str, value: object) -> int:
    _require_exact_type(name, value, int)
    assert isinstance(value, int)
    if value < 0:
        raise ValueError(f"{name} must be nonnegative")
    return value


@dataclass(frozen=True, slots=True)
class ConversationRagPolicy:
    """The fixed work budgets shared by story-side RAG collaborators."""

    max_candidates: int = 20
    max_conversations: int = 256
    max_events: int = 10_000
    max_fragment_codepoints: int = 800
    fragment_overlap_codepoints: int = 80
    max_embedding_batch_size: int = 32

    def __post_init__(self) -> None:
        expected = (20, 256, 10_000, 800, 80, 32)
        actual = (
            self.max_candidates,
            self.max_conversations,
            self.max_events,
            self.max_fragment_codepoints,
            self.fragment_overlap_codepoints,
            self.max_embedding_batch_size,
        )
        if any(type(value) is not int for value in actual):
            raise TypeError("conversation RAG policy values must be integers")
        if actual != expected:
            raise ValueError("conversation RAG policy is fixed")


CONVERSATION_RAG_POLICY = ConversationRagPolicy()


class ConversationRagOutcome(StrEnum):
    """Stable host result for one attempted semantic retrieval."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    LITERAL_FALLBACK = "literal_fallback"


class ConversationRagReason(StrEnum):
    """Content-free reason accompanying an attempted RAG outcome."""

    NONE = "none"
    PARTIAL = "partial"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    CORRUPT = "corrupt"
    INCOMPATIBLE = "incompatible"
    FAILED = "failed"


class ConversationRagProjectionStateAvailability(StrEnum):
    """Availability of a caller-owned projection checkpoint."""

    MISSING = "missing"
    PRESENT = "present"
    CORRUPT = "corrupt"


class ConversationRagProjectionStatus(StrEnum):
    """Compatibility of caller state with one complete projection target."""

    MISSING = "missing"
    CURRENT = "current"
    STALE = "stale"
    CORRUPT = "corrupt"
    SCHEMA_MISMATCH = "schema_mismatch"
    EMBEDDING_MISMATCH = "embedding_mismatch"


class ConversationRagProjectionOutcome(StrEnum):
    """Successful story-side projection result."""

    COMPLETED = "completed"
    UNCHANGED = "unchanged"


class ConversationRagProjectionFailureStage(StrEnum):
    """Content-free stage at which projection collaboration failed."""

    EMBEDDER_IDENTITY = "embedder_identity"
    EMBEDDING = "embedding"
    REPLACEMENT = "replacement"
    DELETION = "deletion"
    RESET = "reset"
    ADAPTER = "adapter"


@dataclass(frozen=True, slots=True)
class ConversationRagDocumentKey:
    """Stable session-scoped identity for one derived RAG document."""

    session_id: str
    document_id: str

    def __post_init__(self) -> None:
        _identifier("session ID", self.session_id)
        _identifier("RAG document ID", self.document_id)


@dataclass(frozen=True, slots=True)
class ConversationRagDocumentIdentity:
    """Authoritative story range represented by one document revision."""

    key: ConversationRagDocumentKey
    conversation_id: str
    start_turn_id: str
    end_turn_id: str
    start_sequence: int
    end_sequence: int
    revision_id: str

    def __post_init__(self) -> None:
        _require_exact_type("RAG document key", self.key, ConversationRagDocumentKey)
        _identifier("conversation ID", self.conversation_id)
        _identifier("start turn ID", self.start_turn_id)
        _identifier("end turn ID", self.end_turn_id)
        _positive_integer("start sequence", self.start_sequence)
        _positive_integer("end sequence", self.end_sequence)
        if self.start_sequence > self.end_sequence:
            raise ValueError("RAG document sequence range must be ordered")
        _nonblank_string("RAG document revision ID", self.revision_id)


@dataclass(frozen=True, slots=True)
class ConversationRagSourceDocument:
    """One authorized source document supplied for derived projection."""

    identity: ConversationRagDocumentIdentity
    text: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_exact_type(
            "RAG source identity",
            self.identity,
            ConversationRagDocumentIdentity,
        )
        _string("RAG source text", self.text)


@dataclass(frozen=True, slots=True)
class ConversationRagSourceReference:
    """Stable source identity and half-open fragment range, without text."""

    document: ConversationRagDocumentIdentity
    fragment_id: str
    start_codepoint: int
    end_codepoint: int

    def __post_init__(self) -> None:
        _require_exact_type(
            "RAG source document",
            self.document,
            ConversationRagDocumentIdentity,
        )
        _nonblank_string("RAG fragment ID", self.fragment_id)
        _nonnegative_integer("fragment start", self.start_codepoint)
        _nonnegative_integer("fragment end", self.end_codepoint)
        if self.start_codepoint >= self.end_codepoint:
            raise ValueError("RAG fragment range must satisfy start < end")


@dataclass(frozen=True, slots=True)
class ConversationRagEmbeddingIdentity:
    """Opaque embedding model identity and exact vector dimensions."""

    model_id: str
    dimensions: int

    def __post_init__(self) -> None:
        _nonblank_string("embedding model ID", self.model_id)
        _positive_integer("embedding dimensions", self.dimensions)


@dataclass(frozen=True, slots=True)
class ConversationRagEmbeddingVector:
    """Canonical finite vector returned by a story-owned embedder."""

    values: tuple[float, ...]

    def __post_init__(self) -> None:
        _require_exact_type("embedding values", self.values, tuple)
        if not self.values:
            raise ValueError("embedding values must not be empty")
        if any(
            type(value) is not float or not math.isfinite(value)
            for value in self.values
        ):
            raise TypeError("embedding values must be finite floats")


@dataclass(frozen=True, slots=True)
class ConversationRagProviderCandidate:
    """One non-authoritative provider fragment used only for validation."""

    source: ConversationRagSourceReference
    text: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_exact_type(
            "provider candidate source",
            self.source,
            ConversationRagSourceReference,
        )
        _nonblank_string("provider candidate text", self.text)
        if len(self.text) != self.source.end_codepoint - self.source.start_codepoint:
            raise ValueError("provider candidate text width is inconsistent")


@dataclass(frozen=True, slots=True)
class ConversationRagVectorRecord:
    """One story-owned derived fragment and embedding pair."""

    candidate: ConversationRagProviderCandidate
    embedding: ConversationRagEmbeddingVector

    def __post_init__(self) -> None:
        _require_exact_type(
            "vector record candidate",
            self.candidate,
            ConversationRagProviderCandidate,
        )
        _require_exact_type(
            "vector record embedding",
            self.embedding,
            ConversationRagEmbeddingVector,
        )


@dataclass(frozen=True, slots=True)
class ConversationRagProjectionCheckpointEntry:
    """One source revision and digest recorded in a projection checkpoint."""

    document: ConversationRagDocumentIdentity
    source_digest: str
    fragment_count: int

    def __post_init__(self) -> None:
        _require_exact_type(
            "checkpoint document",
            self.document,
            ConversationRagDocumentIdentity,
        )
        _require_exact_type("source digest", self.source_digest, str)
        if _SHA256.fullmatch(self.source_digest) is None:
            raise ValueError("source digest must be lowercase sha256")
        _nonnegative_integer("fragment count", self.fragment_count)


@dataclass(frozen=True, slots=True)
class ConversationRagProjectionCheckpoint:
    """Complete caller-owned story representation of a generic manifest."""

    session_id: str
    schema_id: str
    embedding: ConversationRagEmbeddingIdentity
    max_fragment_codepoints: int
    fragment_overlap_codepoints: int
    entries: tuple[ConversationRagProjectionCheckpointEntry, ...]
    token: str

    def __post_init__(self) -> None:
        _identifier("session ID", self.session_id)
        _nonblank_string("projection schema ID", self.schema_id)
        _require_exact_type(
            "checkpoint embedding",
            self.embedding,
            ConversationRagEmbeddingIdentity,
        )
        _positive_integer("maximum fragment codepoints", self.max_fragment_codepoints)
        _nonnegative_integer(
            "fragment overlap codepoints",
            self.fragment_overlap_codepoints,
        )
        if (
            self.max_fragment_codepoints
            != CONVERSATION_RAG_POLICY.max_fragment_codepoints
            or self.fragment_overlap_codepoints
            != CONVERSATION_RAG_POLICY.fragment_overlap_codepoints
        ):
            raise ValueError("conversation RAG chunking policy is fixed")
        _require_exact_type("checkpoint entries", self.entries, tuple)
        if any(
            type(entry) is not ConversationRagProjectionCheckpointEntry
            for entry in self.entries
        ):
            raise TypeError("checkpoint entries must use exact entry values")
        if any(
            entry.document.key.session_id != self.session_id for entry in self.entries
        ):
            raise ValueError("checkpoint entries must match the session")
        keys = tuple(entry.document.key for entry in self.entries)
        if len(set(keys)) != len(keys):
            raise ValueError("checkpoint document keys must be unique")
        if self.entries != tuple(
            sorted(self.entries, key=lambda entry: entry.document.key.document_id)
        ):
            raise ValueError("checkpoint entries must be in canonical order")
        _nonblank_string("projection checkpoint token", self.token)


@dataclass(frozen=True, slots=True)
class ConversationRagProjectionState:
    """One caller-loaded projection checkpoint availability snapshot."""

    availability: ConversationRagProjectionStateAvailability
    checkpoint: ConversationRagProjectionCheckpoint | None

    def __post_init__(self) -> None:
        _require_exact_type(
            "projection state availability",
            self.availability,
            ConversationRagProjectionStateAvailability,
        )
        if self.availability is ConversationRagProjectionStateAvailability.PRESENT:
            _require_exact_type(
                "projection checkpoint",
                self.checkpoint,
                ConversationRagProjectionCheckpoint,
            )
        elif self.checkpoint is not None:
            raise ValueError("missing or corrupt projection state has no checkpoint")


@dataclass(frozen=True, slots=True)
class ConversationRagProjectionRequest:
    """Complete bounded target for one session's derived projection."""

    session_id: str
    schema_id: str
    embedding: ConversationRagEmbeddingIdentity
    documents: tuple[ConversationRagSourceDocument, ...]

    def __post_init__(self) -> None:
        _identifier("session ID", self.session_id)
        _nonblank_string("projection schema ID", self.schema_id)
        _require_exact_type(
            "projection embedding",
            self.embedding,
            ConversationRagEmbeddingIdentity,
        )
        _require_exact_type("projection documents", self.documents, tuple)
        if any(
            type(document) is not ConversationRagSourceDocument
            for document in self.documents
        ):
            raise TypeError("projection documents must use exact source values")
        if len(self.documents) > CONVERSATION_RAG_POLICY.max_events:
            raise ValueError("projection exceeds the document budget")
        if any(
            document.identity.key.session_id != self.session_id
            for document in self.documents
        ):
            raise ValueError("projection documents must match the session")
        keys = tuple(document.identity.key for document in self.documents)
        if len(set(keys)) != len(keys):
            raise ValueError("projection document keys must be unique")
        if self.documents != tuple(
            sorted(
                self.documents, key=lambda document: document.identity.key.document_id
            )
        ):
            raise ValueError("projection documents must be in canonical order")
        conversations = {
            document.identity.conversation_id for document in self.documents
        }
        if len(conversations) > CONVERSATION_RAG_POLICY.max_conversations:
            raise ValueError("projection exceeds the conversation budget")
        event_count = sum(
            document.identity.end_sequence - document.identity.start_sequence + 1
            for document in self.documents
        )
        if event_count > CONVERSATION_RAG_POLICY.max_events:
            raise ValueError("projection exceeds the event budget")


@dataclass(frozen=True, slots=True)
class ConversationRagProjectionResult:
    """Complete successful projection result and publishable checkpoint."""

    status_before: ConversationRagProjectionStatus
    outcome: ConversationRagProjectionOutcome
    attempted_documents: int
    completed_documents: int
    checkpoint: ConversationRagProjectionCheckpoint

    def __post_init__(self) -> None:
        _require_exact_type(
            "projection status",
            self.status_before,
            ConversationRagProjectionStatus,
        )
        _require_exact_type(
            "projection outcome",
            self.outcome,
            ConversationRagProjectionOutcome,
        )
        _nonnegative_integer("attempted documents", self.attempted_documents)
        _nonnegative_integer("completed documents", self.completed_documents)
        if self.completed_documents != self.attempted_documents:
            raise ValueError("successful projection counts must match")
        if (
            self.outcome is ConversationRagProjectionOutcome.UNCHANGED
            and self.attempted_documents != 0
        ):
            raise ValueError("unchanged projection must have zero attempts")
        _require_exact_type(
            "projection checkpoint",
            self.checkpoint,
            ConversationRagProjectionCheckpoint,
        )


@dataclass(frozen=True, slots=True)
class ConversationRagRetrievalRequest:
    """One bounded semantic query within a single authorized session."""

    session_id: str
    text: str = field(repr=False)
    hit_limit: int = CONVERSATION_RAG_POLICY.max_candidates

    def __post_init__(self) -> None:
        _identifier("session ID", self.session_id)
        _nonblank_string("RAG query", self.text)
        _positive_integer("RAG hit limit", self.hit_limit)
        if self.hit_limit > CONVERSATION_RAG_POLICY.max_candidates:
            raise ValueError("RAG hit limit exceeds the candidate budget")


@dataclass(frozen=True, slots=True)
class ConversationRagCandidate:
    """One validated stable source identity returned without provider text."""

    source: ConversationRagSourceReference
    rank: int

    def __post_init__(self) -> None:
        _require_exact_type(
            "RAG candidate source",
            self.source,
            ConversationRagSourceReference,
        )
        _positive_integer("RAG candidate rank", self.rank)


@dataclass(frozen=True, slots=True)
class ConversationRagRetrievalResult:
    """Bounded semantic result or explicit literal-fallback instruction."""

    request: ConversationRagRetrievalRequest
    outcome: ConversationRagOutcome
    reason: ConversationRagReason
    candidates: tuple[ConversationRagCandidate, ...]
    truncated: bool

    def __post_init__(self) -> None:
        _require_exact_type(
            "RAG retrieval request",
            self.request,
            ConversationRagRetrievalRequest,
        )
        _require_exact_type("RAG outcome", self.outcome, ConversationRagOutcome)
        _require_exact_type("RAG reason", self.reason, ConversationRagReason)
        _require_exact_type("RAG candidates", self.candidates, tuple)
        _require_exact_type("RAG truncated", self.truncated, bool)
        if any(
            type(candidate) is not ConversationRagCandidate
            for candidate in self.candidates
        ):
            raise TypeError("RAG candidates must use exact candidate values")
        if len(self.candidates) > self.request.hit_limit:
            raise ValueError("RAG result exceeds its hit limit")
        if tuple(candidate.rank for candidate in self.candidates) != tuple(
            range(1, len(self.candidates) + 1)
        ):
            raise ValueError("RAG candidate ranks must be contiguous")
        sources = tuple(candidate.source for candidate in self.candidates)
        if len(set(sources)) != len(sources):
            raise ValueError("RAG candidate sources must be unique")
        if self.outcome is ConversationRagOutcome.COMPLETE:
            if self.reason is not ConversationRagReason.NONE:
                raise ValueError("complete RAG results require reason none")
        elif self.outcome is ConversationRagOutcome.PARTIAL:
            if self.reason is not ConversationRagReason.PARTIAL or not self.candidates:
                raise ValueError(
                    "partial RAG results require candidates and reason partial"
                )
        else:
            if self.reason not in (
                ConversationRagReason.DISABLED,
                ConversationRagReason.UNAVAILABLE,
                ConversationRagReason.STALE,
                ConversationRagReason.CORRUPT,
                ConversationRagReason.INCOMPATIBLE,
                ConversationRagReason.FAILED,
            ):
                raise ValueError("literal fallback requires a failure reason")
            if self.candidates or self.truncated:
                raise ValueError("literal fallback cannot contain RAG candidates")


@runtime_checkable
class ConversationRagEmbedder(Protocol):
    """Caller-owned embedding collaborator."""

    @property
    def identity(self) -> ConversationRagEmbeddingIdentity: ...

    def embed(
        self,
        texts: tuple[str, ...],
        /,
    ) -> tuple[ConversationRagEmbeddingVector, ...]: ...


@runtime_checkable
class ConversationRagVectorIndexWriter(Protocol):
    """Caller-owned complete-document vector mutation collaborator."""

    def replace_document(
        self,
        document: ConversationRagDocumentIdentity,
        records: tuple[ConversationRagVectorRecord, ...],
        /,
    ) -> None: ...

    def delete_document(self, document: ConversationRagDocumentKey, /) -> None: ...


@runtime_checkable
class ConversationRagVectorIndexResetter(Protocol):
    """Caller-owned destructive session-projection reset collaborator."""

    def reset_session(self, session_id: str, /) -> None: ...


@runtime_checkable
class ConversationRagVectorIndexReader(Protocol):
    """Caller-owned semantic candidate collaborator."""

    def search(
        self,
        request: ConversationRagRetrievalRequest,
        embedding: ConversationRagEmbeddingVector,
        /,
    ) -> tuple[ConversationRagProviderCandidate, ...]: ...


@runtime_checkable
class ConversationRagLexicalRetriever(Protocol):
    """Caller-owned lexical candidate collaborator for deterministic fusion."""

    def search(
        self,
        request: ConversationRagRetrievalRequest,
        /,
    ) -> tuple[ConversationRagProviderCandidate, ...]: ...


@dataclass(frozen=True, slots=True)
class ConversationRagCollaborators:
    """Caller-owned collaborators retained without lifecycle acquisition."""

    embedder: ConversationRagEmbedder
    vector_writer: ConversationRagVectorIndexWriter
    vector_resetter: ConversationRagVectorIndexResetter
    vector_reader: ConversationRagVectorIndexReader
    lexical_retriever: ConversationRagLexicalRetriever

    def __post_init__(self) -> None:
        if any(
            value is None
            for value in (
                self.embedder,
                self.vector_writer,
                self.vector_resetter,
                self.vector_reader,
                self.lexical_retriever,
            )
        ):
            raise TypeError("conversation RAG collaborators must not be None")


@runtime_checkable
class ConversationRagService(Protocol):
    """Injected story-facing projection and retrieval service."""

    def project_documents(
        self,
        request: ConversationRagProjectionRequest,
        state: ConversationRagProjectionState,
        /,
    ) -> ConversationRagProjectionResult: ...

    def rebuild_projection(
        self,
        request: ConversationRagProjectionRequest,
        state: ConversationRagProjectionState,
        /,
    ) -> ConversationRagProjectionResult: ...

    def retrieve_semantic(
        self,
        request: ConversationRagRetrievalRequest,
        state: ConversationRagProjectionState,
        /,
    ) -> ConversationRagRetrievalResult: ...

    def retrieve_hybrid(
        self,
        request: ConversationRagRetrievalRequest,
        state: ConversationRagProjectionState,
        /,
    ) -> ConversationRagRetrievalResult: ...


class ConversationRagError(Exception):
    """Base class for content-free optional RAG boundary failures."""

    def __init__(self) -> None:
        super().__init__("conversation RAG operation failed")


class ConversationRagContractError(ConversationRagError):
    """Fixed failure for an invalid generic or collaborator contract."""

    def __init__(self) -> None:
        Exception.__init__(self, "conversation RAG contract validation failed")


class ConversationRagStateError(ConversationRagError):
    """Content-free incompatible incremental-state failure."""

    status: ConversationRagProjectionStatus

    def __init__(self, status: ConversationRagProjectionStatus) -> None:
        _require_exact_type(
            "projection status",
            status,
            ConversationRagProjectionStatus,
        )
        self.status = status
        Exception.__init__(self, "conversation RAG projection state is incompatible")


class ConversationRagOperationError(ConversationRagError):
    """Content-free translated projection collaborator failure."""

    stage: ConversationRagProjectionFailureStage
    affected_document: ConversationRagDocumentKey | None
    attempted_documents: int
    completed_documents: int

    def __init__(
        self,
        stage: ConversationRagProjectionFailureStage,
        affected_document: ConversationRagDocumentKey | None,
        attempted_documents: int,
        completed_documents: int,
    ) -> None:
        _require_exact_type(
            "projection failure stage",
            stage,
            ConversationRagProjectionFailureStage,
        )
        if affected_document is not None:
            _require_exact_type(
                "affected document",
                affected_document,
                ConversationRagDocumentKey,
            )
        _nonnegative_integer("attempted documents", attempted_documents)
        _nonnegative_integer("completed documents", completed_documents)
        if completed_documents > attempted_documents:
            raise ValueError("completed documents cannot exceed attempts")
        self.stage = stage
        self.affected_document = affected_document
        self.attempted_documents = attempted_documents
        self.completed_documents = completed_documents
        Exception.__init__(self, "conversation RAG collaborator failed")


__all__ = [
    "CONVERSATION_RAG_POLICY",
    "ConversationRagCandidate",
    "ConversationRagCollaborators",
    "ConversationRagContractError",
    "ConversationRagDocumentIdentity",
    "ConversationRagDocumentKey",
    "ConversationRagEmbedder",
    "ConversationRagEmbeddingIdentity",
    "ConversationRagEmbeddingVector",
    "ConversationRagError",
    "ConversationRagLexicalRetriever",
    "ConversationRagOperationError",
    "ConversationRagOutcome",
    "ConversationRagPolicy",
    "ConversationRagProjectionCheckpoint",
    "ConversationRagProjectionCheckpointEntry",
    "ConversationRagProjectionFailureStage",
    "ConversationRagProjectionOutcome",
    "ConversationRagProjectionRequest",
    "ConversationRagProjectionResult",
    "ConversationRagProjectionState",
    "ConversationRagProjectionStateAvailability",
    "ConversationRagProjectionStatus",
    "ConversationRagProviderCandidate",
    "ConversationRagReason",
    "ConversationRagRetrievalRequest",
    "ConversationRagRetrievalResult",
    "ConversationRagService",
    "ConversationRagSourceDocument",
    "ConversationRagSourceReference",
    "ConversationRagStateError",
    "ConversationRagVectorIndexReader",
    "ConversationRagVectorIndexResetter",
    "ConversationRagVectorIndexWriter",
    "ConversationRagVectorRecord",
]
