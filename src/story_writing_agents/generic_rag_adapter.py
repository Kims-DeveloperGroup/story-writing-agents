"""Exact translation between story conversation RAG and generic-rag 0.1.0."""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType

import generic_rag.contracts as _contracts
import generic_rag.errors as _generic_errors
import generic_rag.ports as _ports
import generic_rag.projection as _projection
import generic_rag.retrieval as _retrieval

from .conversation_rag import (
    CONVERSATION_RAG_POLICY,
    ConversationRagCandidate,
    ConversationRagCollaborators,
    ConversationRagContractError,
    ConversationRagDocumentIdentity,
    ConversationRagDocumentKey,
    ConversationRagEmbeddingIdentity,
    ConversationRagEmbeddingVector,
    ConversationRagOperationError,
    ConversationRagOutcome,
    ConversationRagProjectionCheckpoint,
    ConversationRagProjectionCheckpointEntry,
    ConversationRagProjectionFailureStage,
    ConversationRagProjectionOutcome,
    ConversationRagProjectionRequest,
    ConversationRagProjectionResult,
    ConversationRagProjectionState,
    ConversationRagProjectionStateAvailability,
    ConversationRagProjectionStatus,
    ConversationRagProviderCandidate,
    ConversationRagReason,
    ConversationRagRetrievalRequest,
    ConversationRagRetrievalResult,
    ConversationRagSourceReference,
    ConversationRagStateError,
    ConversationRagVectorRecord,
)

_CapabilityRow = tuple[str, tuple[str, ...]]
_DocumentIdentityKey = tuple[str, str, str]


def _require_exact_type(name: str, value: object, expected: type[object]) -> None:
    if type(value) is not expected:
        raise TypeError(
            f"{name} must be exactly {expected.__name__}, not {type(value).__name__}"
        )


@dataclass(frozen=True, slots=True)
class GenericRagCapabilitySnapshot:
    """Content-free module and symbol inventory from the imported package."""

    modules: tuple[_CapabilityRow, ...]

    def __post_init__(self) -> None:
        _require_exact_type("generic RAG capability modules", self.modules, tuple)
        previous = ""
        for row in self.modules:
            _require_exact_type("generic RAG capability row", row, tuple)
            if len(row) != 2:
                raise ValueError("generic RAG capability row must have two values")
            module_name, symbols = row
            _require_exact_type("generic RAG module name", module_name, str)
            _require_exact_type("generic RAG module symbols", symbols, tuple)
            if not module_name or module_name <= previous:
                raise ValueError("generic RAG capability modules must be ordered")
            if any(type(symbol) is not str or not symbol for symbol in symbols):
                raise TypeError("generic RAG capability symbols must be strings")
            if symbols != tuple(sorted(set(symbols))):
                raise ValueError("generic RAG capability symbols must be ordered")
            previous = module_name

    def supports(self, requirements: tuple[_CapabilityRow, ...], /) -> bool:
        """Return whether every exact host-required module symbol is present."""

        _require_exact_type("generic RAG requirements", requirements, tuple)
        available = {module: set(symbols) for module, symbols in self.modules}
        for row in requirements:
            if type(row) is not tuple or len(row) != 2:
                return False
            module_name, symbols = row
            if type(module_name) is not str or type(symbols) is not tuple:
                return False
            if any(type(symbol) is not str for symbol in symbols):
                return False
            if not set(symbols) <= available.get(module_name, set()):
                return False
        return True


def _capability_row(module: ModuleType) -> _CapabilityRow:
    names = getattr(module, "__all__", ())
    if type(names) not in (tuple, list):
        names = ()
    present = tuple(
        sorted(
            name
            for name in names
            if type(name) is str and name and hasattr(module, name)
        )
    )
    return (module.__name__, present)


def inspect_generic_rag_capabilities() -> GenericRagCapabilitySnapshot:
    """Return a sanitized inventory of the statically imported generic modules."""

    modules = (
        _contracts,
        _generic_errors,
        _ports,
        _projection,
        _retrieval,
    )
    return GenericRagCapabilitySnapshot(
        tuple(sorted(_capability_row(module) for module in modules))
    )


def _to_generic_embedding_identity(
    value: ConversationRagEmbeddingIdentity,
) -> _contracts.EmbeddingIdentity:
    _require_exact_type(
        "story embedding identity",
        value,
        ConversationRagEmbeddingIdentity,
    )
    return _contracts.EmbeddingIdentity(value.model_id, value.dimensions)


def _to_story_embedding_identity(
    value: object,
) -> ConversationRagEmbeddingIdentity:
    _require_exact_type(
        "generic embedding identity", value, _contracts.EmbeddingIdentity
    )
    assert isinstance(value, _contracts.EmbeddingIdentity)
    return ConversationRagEmbeddingIdentity(value.model_id, value.dimensions)


def _to_generic_document_key(
    value: ConversationRagDocumentKey,
) -> _contracts.DocumentKey:
    _require_exact_type("story document key", value, ConversationRagDocumentKey)
    return _contracts.DocumentKey(value.session_id, value.document_id)


def _to_story_document_key(value: object) -> ConversationRagDocumentKey:
    _require_exact_type("generic document key", value, _contracts.DocumentKey)
    assert isinstance(value, _contracts.DocumentKey)
    return ConversationRagDocumentKey(value.corpus_id, value.document_id)


def _to_generic_document_identity(
    value: ConversationRagDocumentIdentity,
) -> _contracts.DocumentIdentity:
    _require_exact_type(
        "story document identity",
        value,
        ConversationRagDocumentIdentity,
    )
    return _contracts.DocumentIdentity(
        _to_generic_document_key(value.key),
        value.revision_id,
    )


def _generic_identity_key(value: object) -> _DocumentIdentityKey:
    _require_exact_type("generic document identity", value, _contracts.DocumentIdentity)
    assert isinstance(value, _contracts.DocumentIdentity)
    _require_exact_type("generic document key", value.key, _contracts.DocumentKey)
    return (value.key.corpus_id, value.key.document_id, value.revision_id)


def _story_identity_key(value: ConversationRagDocumentIdentity) -> _DocumentIdentityKey:
    return (value.key.session_id, value.key.document_id, value.revision_id)


def _document_map(
    documents: tuple[ConversationRagDocumentIdentity, ...],
) -> dict[_DocumentIdentityKey, ConversationRagDocumentIdentity]:
    return {_story_identity_key(document): document for document in documents}


def _checkpoint_documents(
    state: ConversationRagProjectionState,
) -> dict[_DocumentIdentityKey, ConversationRagDocumentIdentity]:
    if state.checkpoint is None:
        return {}
    return _document_map(tuple(entry.document for entry in state.checkpoint.entries))


def _projection_documents(
    request: ConversationRagProjectionRequest,
) -> dict[_DocumentIdentityKey, ConversationRagDocumentIdentity]:
    return _document_map(tuple(document.identity for document in request.documents))


def _to_generic_projection_identity(
    schema_id: str,
    embedding: ConversationRagEmbeddingIdentity,
) -> _contracts.ProjectionIdentity:
    return _contracts.ProjectionIdentity(
        schema_id,
        _to_generic_embedding_identity(embedding),
    )


def _to_generic_chunking() -> _contracts.ChunkingPolicy:
    return _contracts.ChunkingPolicy(
        CONVERSATION_RAG_POLICY.max_fragment_codepoints,
        CONVERSATION_RAG_POLICY.fragment_overlap_codepoints,
    )


def _to_generic_projection_request(
    request: ConversationRagProjectionRequest,
) -> _contracts.ProjectionRequest:
    _require_exact_type(
        "projection request",
        request,
        ConversationRagProjectionRequest,
    )
    documents = tuple(
        _contracts.Document(
            _to_generic_document_identity(document.identity),
            document.text,
            (),
        )
        for document in request.documents
    )
    maximum_document_width = max(
        1,
        max((len(document.text) for document in request.documents), default=0),
    )
    return _contracts.ProjectionRequest(
        request.session_id,
        _to_generic_projection_identity(request.schema_id, request.embedding),
        _to_generic_chunking(),
        _contracts.ProjectionLimits(
            CONVERSATION_RAG_POLICY.max_events,
            maximum_document_width,
            CONVERSATION_RAG_POLICY.max_embedding_batch_size,
        ),
        documents,
    )


def _to_generic_manifest(
    checkpoint: ConversationRagProjectionCheckpoint,
) -> _contracts.ProjectionManifest:
    _require_exact_type(
        "projection checkpoint",
        checkpoint,
        ConversationRagProjectionCheckpoint,
    )
    projection = _to_generic_projection_identity(
        checkpoint.schema_id,
        checkpoint.embedding,
    )
    entries = tuple(
        _contracts.ProjectionManifestEntry(
            _to_generic_document_identity(entry.document),
            entry.source_digest,
            entry.fragment_count,
        )
        for entry in checkpoint.entries
    )
    return _contracts.ProjectionManifest(
        checkpoint.session_id,
        projection,
        _contracts.ChunkingPolicy(
            checkpoint.max_fragment_codepoints,
            checkpoint.fragment_overlap_codepoints,
        ),
        entries,
        _contracts.ProjectionCheckpoint(
            checkpoint.session_id,
            projection,
            checkpoint.token,
        ),
    )


def _to_generic_state(
    state: ConversationRagProjectionState,
) -> _contracts.ProjectionStateSnapshot:
    _require_exact_type("projection state", state, ConversationRagProjectionState)
    availability = {
        ConversationRagProjectionStateAvailability.MISSING: (
            _contracts.ProjectionStateAvailability.MISSING
        ),
        ConversationRagProjectionStateAvailability.PRESENT: (
            _contracts.ProjectionStateAvailability.PRESENT
        ),
        ConversationRagProjectionStateAvailability.CORRUPT: (
            _contracts.ProjectionStateAvailability.CORRUPT
        ),
    }[state.availability]
    manifest = (
        None if state.checkpoint is None else _to_generic_manifest(state.checkpoint)
    )
    return _contracts.ProjectionStateSnapshot(availability, manifest)


def _projection_status(value: object) -> ConversationRagProjectionStatus:
    _require_exact_type(
        "generic projection status", value, _contracts.ProjectionStateStatus
    )
    assert isinstance(value, _contracts.ProjectionStateStatus)
    return ConversationRagProjectionStatus(value.value)


def _projection_outcome(value: object) -> ConversationRagProjectionOutcome:
    _require_exact_type(
        "generic projection outcome", value, _contracts.ProjectionOutcome
    )
    assert isinstance(value, _contracts.ProjectionOutcome)
    if value is _contracts.ProjectionOutcome.COMPLETED:
        return ConversationRagProjectionOutcome.COMPLETED
    if value is _contracts.ProjectionOutcome.UNCHANGED:
        return ConversationRagProjectionOutcome.UNCHANGED
    raise ValueError("generic projection success has an invalid outcome")


def _from_generic_manifest(
    value: object,
    documents: dict[_DocumentIdentityKey, ConversationRagDocumentIdentity],
) -> ConversationRagProjectionCheckpoint:
    _require_exact_type(
        "generic projection manifest", value, _contracts.ProjectionManifest
    )
    assert isinstance(value, _contracts.ProjectionManifest)
    projection = value.projection
    _require_exact_type(
        "generic projection identity", projection, _contracts.ProjectionIdentity
    )
    entries: list[ConversationRagProjectionCheckpointEntry] = []
    for generic_entry in value.entries:
        _require_exact_type(
            "generic projection entry",
            generic_entry,
            _contracts.ProjectionManifestEntry,
        )
        document = documents.get(_generic_identity_key(generic_entry.document))
        if document is None:
            raise ValueError("generic projection returned an unknown document")
        entries.append(
            ConversationRagProjectionCheckpointEntry(
                document,
                generic_entry.source_digest,
                generic_entry.fragment_count,
            )
        )
    checkpoint = value.checkpoint
    _require_exact_type(
        "generic projection checkpoint",
        checkpoint,
        _contracts.ProjectionCheckpoint,
    )
    chunking = value.chunking
    _require_exact_type("generic chunking policy", chunking, _contracts.ChunkingPolicy)
    return ConversationRagProjectionCheckpoint(
        session_id=value.corpus_id,
        schema_id=projection.schema_id,
        embedding=_to_story_embedding_identity(projection.embedding),
        max_fragment_codepoints=chunking.max_fragment_codepoints,
        fragment_overlap_codepoints=chunking.overlap_codepoints,
        entries=tuple(entries),
        token=checkpoint.token,
    )


def _from_generic_projection_result(
    value: object,
    documents: dict[_DocumentIdentityKey, ConversationRagDocumentIdentity],
) -> ConversationRagProjectionResult:
    _require_exact_type("generic projection result", value, _contracts.ProjectionResult)
    assert isinstance(value, _contracts.ProjectionResult)
    receipt = value.receipt
    _require_exact_type(
        "generic projection receipt", receipt, _contracts.ProjectionReceipt
    )
    return ConversationRagProjectionResult(
        status_before=_projection_status(value.status_before),
        outcome=_projection_outcome(receipt.outcome),
        attempted_documents=receipt.attempted_documents,
        completed_documents=receipt.completed_documents,
        checkpoint=_from_generic_manifest(value.manifest, documents),
    )


def _to_story_embedding_vector(value: object) -> ConversationRagEmbeddingVector:
    _require_exact_type("generic embedding vector", value, _contracts.EmbeddingVector)
    assert isinstance(value, _contracts.EmbeddingVector)
    _require_exact_type("generic embedding values", value.values, tuple)
    if any(type(coordinate) is not float for coordinate in value.values):
        raise TypeError("generic embedding coordinates are not canonical")
    return ConversationRagEmbeddingVector(value.values)


def _to_generic_embedding_vector(
    value: ConversationRagEmbeddingVector,
) -> _contracts.EmbeddingVector:
    _require_exact_type("story embedding vector", value, ConversationRagEmbeddingVector)
    return _contracts.EmbeddingVector(value.values)


def _from_generic_source_reference(
    value: object,
    documents: dict[_DocumentIdentityKey, ConversationRagDocumentIdentity],
) -> ConversationRagSourceReference:
    _require_exact_type("generic fragment identity", value, _contracts.FragmentIdentity)
    assert isinstance(value, _contracts.FragmentIdentity)
    document = documents.get(_generic_identity_key(value.document))
    if document is None:
        raise ValueError("generic retrieval returned an unknown document")
    return ConversationRagSourceReference(
        document,
        value.fragment_id,
        value.start,
        value.end,
    )


def _to_generic_fragment(
    value: ConversationRagProviderCandidate,
    documents: dict[_DocumentIdentityKey, ConversationRagDocumentIdentity],
) -> _contracts.Fragment:
    _require_exact_type(
        "story provider candidate",
        value,
        ConversationRagProviderCandidate,
    )
    expected = documents.get(_story_identity_key(value.source.document))
    if expected != value.source.document:
        raise ValueError("provider candidate source is not in the published checkpoint")
    source = value.source
    identity = _contracts.FragmentIdentity(
        _to_generic_document_identity(source.document),
        source.fragment_id,
        source.start_codepoint,
        source.end_codepoint,
    )
    return _contracts.Fragment(identity, value.text, ())


class _EmbedderBridge:
    __slots__ = ("_embedder",)

    def __init__(self, embedder: object) -> None:
        self._embedder = embedder

    @property
    def identity(self) -> _contracts.EmbeddingIdentity:
        identity = self._embedder.identity  # type: ignore[attr-defined]
        return _to_generic_embedding_identity(identity)

    def embed(
        self,
        texts: tuple[str, ...],
        /,
    ) -> tuple[_contracts.EmbeddingVector, ...]:
        output = self._embedder.embed(texts)  # type: ignore[attr-defined]
        _require_exact_type("story embedder output", output, tuple)
        return tuple(_to_generic_embedding_vector(vector) for vector in output)


class _VectorWriterBridge:
    __slots__ = ("_documents", "_writer")

    def __init__(
        self,
        writer: object,
        documents: dict[_DocumentIdentityKey, ConversationRagDocumentIdentity],
    ) -> None:
        self._writer = writer
        self._documents = documents

    def replace_document(
        self,
        document: _contracts.DocumentIdentity,
        records: tuple[_contracts.VectorRecord, ...],
        /,
    ) -> None:
        story_document = self._documents.get(_generic_identity_key(document))
        if story_document is None:
            raise ValueError("generic writer supplied an unknown document")
        _require_exact_type("generic vector records", records, tuple)
        translated: list[ConversationRagVectorRecord] = []
        for record in records:
            _require_exact_type(
                "generic vector record", record, _contracts.VectorRecord
            )
            fragment = record.fragment
            _require_exact_type("generic fragment", fragment, _contracts.Fragment)
            translated.append(
                ConversationRagVectorRecord(
                    ConversationRagProviderCandidate(
                        _from_generic_source_reference(
                            fragment.identity,
                            self._documents,
                        ),
                        fragment.text,
                    ),
                    _to_story_embedding_vector(record.embedding),
                )
            )
        return self._writer.replace_document(  # type: ignore[no-any-return,attr-defined]
            story_document,
            tuple(translated),
        )

    def delete_document(self, document: _contracts.DocumentKey, /) -> None:
        return self._writer.delete_document(  # type: ignore[no-any-return,attr-defined]
            _to_story_document_key(document)
        )


class _VectorResetterBridge:
    __slots__ = ("_resetter",)

    def __init__(self, resetter: object) -> None:
        self._resetter = resetter

    def reset_corpus(self, corpus_id: str, /) -> None:
        return self._resetter.reset_session(corpus_id)  # type: ignore[no-any-return,attr-defined]


class _VectorReaderBridge:
    __slots__ = ("_documents", "_reader", "_request")

    def __init__(
        self,
        reader: object,
        request: ConversationRagRetrievalRequest,
        documents: dict[_DocumentIdentityKey, ConversationRagDocumentIdentity],
    ) -> None:
        self._reader = reader
        self._request = request
        self._documents = documents

    def search(
        self,
        query: _contracts.RetrievalQuery,
        embedding: _contracts.EmbeddingVector,
        /,
    ) -> tuple[_contracts.Fragment, ...]:
        _validate_generic_query(query, self._request)
        output = self._reader.search(  # type: ignore[attr-defined]
            self._request,
            _to_story_embedding_vector(embedding),
        )
        _require_exact_type("story vector reader output", output, tuple)
        return tuple(
            _to_generic_fragment(candidate, self._documents) for candidate in output
        )


class _LexicalRetrieverBridge:
    __slots__ = ("_documents", "_request", "_retriever")

    def __init__(
        self,
        retriever: object,
        request: ConversationRagRetrievalRequest,
        documents: dict[_DocumentIdentityKey, ConversationRagDocumentIdentity],
    ) -> None:
        self._retriever = retriever
        self._request = request
        self._documents = documents

    def search(
        self,
        query: _contracts.RetrievalQuery,
        /,
    ) -> tuple[_contracts.Fragment, ...]:
        _validate_generic_query(query, self._request)
        output = self._retriever.search(self._request)  # type: ignore[attr-defined]
        _require_exact_type("story lexical retriever output", output, tuple)
        return tuple(
            _to_generic_fragment(candidate, self._documents) for candidate in output
        )


def _to_generic_query(
    request: ConversationRagRetrievalRequest,
) -> _contracts.RetrievalQuery:
    return _contracts.RetrievalQuery(
        request.session_id,
        request.text,
        request.hit_limit,
        CONVERSATION_RAG_POLICY.max_candidates,
    )


def _validate_generic_query(
    query: object,
    request: ConversationRagRetrievalRequest,
) -> None:
    _require_exact_type("generic retrieval query", query, _contracts.RetrievalQuery)
    assert isinstance(query, _contracts.RetrievalQuery)
    if query != _to_generic_query(request):
        raise ValueError("generic retrieval query changed across the adapter")


def _projection_stage(value: object) -> ConversationRagProjectionFailureStage:
    if type(value) is not _projection.ProjectionFailureStage:
        return ConversationRagProjectionFailureStage.ADAPTER
    assert isinstance(value, _projection.ProjectionFailureStage)
    try:
        return ConversationRagProjectionFailureStage(value.value)
    except ValueError:
        return ConversationRagProjectionFailureStage.ADAPTER


def _translated_projection_failure(exc: Exception) -> Exception:
    if type(exc) is _projection.ProjectionStateError:
        status_value = exc.status
        if type(status_value) is _contracts.ProjectionStateStatus:
            try:
                return ConversationRagStateError(
                    ConversationRagProjectionStatus(status_value.value)
                )
            except ValueError:
                pass
        return ConversationRagOperationError(
            ConversationRagProjectionFailureStage.ADAPTER,
            None,
            0,
            0,
        )
    if type(exc) is _projection.ProjectionOperationError:
        affected = exc.affected_document
        affected_document = (
            _to_story_document_key(affected) if affected is not None else None
        )
        receipt = exc.receipt
        attempted = receipt.attempted_documents if receipt is not None else 0
        completed = receipt.completed_documents if receipt is not None else 0
        return ConversationRagOperationError(
            _projection_stage(exc.stage),
            affected_document,
            attempted,
            completed,
        )
    if type(exc) is _generic_errors.ContractValidationError:
        return ConversationRagContractError()
    return ConversationRagOperationError(
        ConversationRagProjectionFailureStage.ADAPTER,
        None,
        0,
        0,
    )


def _retrieval_outcome(
    value: object,
) -> tuple[ConversationRagOutcome, ConversationRagReason]:
    _require_exact_type("generic retrieval outcome", value, _contracts.RetrievalOutcome)
    assert isinstance(value, _contracts.RetrievalOutcome)
    return {
        _contracts.RetrievalOutcome.COMPLETE: (
            ConversationRagOutcome.COMPLETE,
            ConversationRagReason.NONE,
        ),
        _contracts.RetrievalOutcome.PARTIAL: (
            ConversationRagOutcome.PARTIAL,
            ConversationRagReason.PARTIAL,
        ),
        _contracts.RetrievalOutcome.UNAVAILABLE: (
            ConversationRagOutcome.LITERAL_FALLBACK,
            ConversationRagReason.UNAVAILABLE,
        ),
        _contracts.RetrievalOutcome.STALE: (
            ConversationRagOutcome.LITERAL_FALLBACK,
            ConversationRagReason.STALE,
        ),
        _contracts.RetrievalOutcome.FAILED: (
            ConversationRagOutcome.LITERAL_FALLBACK,
            ConversationRagReason.FAILED,
        ),
    }[value]


def _from_generic_retrieval_result(
    value: object,
    request: ConversationRagRetrievalRequest,
    documents: dict[_DocumentIdentityKey, ConversationRagDocumentIdentity],
) -> ConversationRagRetrievalResult:
    _require_exact_type("generic retrieval result", value, _contracts.RetrievalResult)
    assert isinstance(value, _contracts.RetrievalResult)
    _validate_generic_query(value.query, request)
    outcome, reason = _retrieval_outcome(value.outcome)
    candidates: list[ConversationRagCandidate] = []
    for hit in value.hits:
        _require_exact_type("generic retrieval hit", hit, _contracts.RetrievalHit)
        fragment = hit.fragment
        _require_exact_type("generic retrieval fragment", fragment, _contracts.Fragment)
        candidates.append(
            ConversationRagCandidate(
                _from_generic_source_reference(fragment.identity, documents),
                hit.rank,
            )
        )
    return ConversationRagRetrievalResult(
        request,
        outcome,
        reason,
        tuple(candidates),
        value.truncated,
    )


class GenericRagConversationService:
    """Story-facing service backed by the exact generic-rag 0.1.0 API."""

    __slots__ = ("_collaborators",)

    def __init__(self, collaborators: ConversationRagCollaborators) -> None:
        _require_exact_type(
            "conversation RAG collaborators",
            collaborators,
            ConversationRagCollaborators,
        )
        self._collaborators = collaborators

    def project_documents(
        self,
        request: ConversationRagProjectionRequest,
        state: ConversationRagProjectionState,
        /,
    ) -> ConversationRagProjectionResult:
        """Apply one complete compatible target using borrowed collaborators."""

        return self._run_projection(_projection.project_documents, request, state)

    def rebuild_projection(
        self,
        request: ConversationRagProjectionRequest,
        state: ConversationRagProjectionState,
        /,
    ) -> ConversationRagProjectionResult:
        """Reset and rebuild one complete session projection."""

        return self._run_projection(_projection.rebuild_projection, request, state)

    def _run_projection(
        self,
        operation: object,
        request: ConversationRagProjectionRequest,
        state: ConversationRagProjectionState,
    ) -> ConversationRagProjectionResult:
        _require_exact_type(
            "projection request",
            request,
            ConversationRagProjectionRequest,
        )
        _require_exact_type("projection state", state, ConversationRagProjectionState)
        generic_request = _to_generic_projection_request(request)
        generic_state = _to_generic_state(state)
        documents = _projection_documents(request)
        writer = _VectorWriterBridge(self._collaborators.vector_writer, documents)
        embedder = _EmbedderBridge(self._collaborators.embedder)
        failure: Exception | None = None
        result: ConversationRagProjectionResult | None = None
        try:
            if operation is _projection.project_documents:
                generic_result = _projection.project_documents(
                    generic_request,
                    generic_state,
                    _ports.Borrowed(embedder),
                    _ports.Borrowed(writer),
                )
            elif operation is _projection.rebuild_projection:
                generic_result = _projection.rebuild_projection(
                    generic_request,
                    generic_state,
                    _ports.Borrowed(embedder),
                    _ports.Borrowed(writer),
                    _ports.Borrowed(
                        _VectorResetterBridge(self._collaborators.vector_resetter)
                    ),
                )
            else:
                raise ValueError("unsupported projection operation")
            result = _from_generic_projection_result(generic_result, documents)
        except Exception as exc:  # noqa: BLE001 - translate collaborator failures
            failure = _translated_projection_failure(exc)
        if failure is not None:
            raise failure
        assert result is not None
        return result

    def retrieve_semantic(
        self,
        request: ConversationRagRetrievalRequest,
        state: ConversationRagProjectionState,
        /,
    ) -> ConversationRagRetrievalResult:
        """Retrieve semantic candidates without exposing provider text."""

        return self._run_retrieval(False, request, state)

    def retrieve_hybrid(
        self,
        request: ConversationRagRetrievalRequest,
        state: ConversationRagProjectionState,
        /,
    ) -> ConversationRagRetrievalResult:
        """Retrieve fused semantic and lexical candidates without provider text."""

        return self._run_retrieval(True, request, state)

    def _run_retrieval(
        self,
        hybrid: bool,
        request: ConversationRagRetrievalRequest,
        state: ConversationRagProjectionState,
    ) -> ConversationRagRetrievalResult:
        _require_exact_type(
            "retrieval request",
            request,
            ConversationRagRetrievalRequest,
        )
        _require_exact_type("projection state", state, ConversationRagProjectionState)
        documents = _checkpoint_documents(state)
        generic_query = _to_generic_query(request)
        generic_state = _to_generic_state(state)
        embedder = _EmbedderBridge(self._collaborators.embedder)
        reader = _VectorReaderBridge(
            self._collaborators.vector_reader,
            request,
            documents,
        )
        failure: Exception | None = None
        result: ConversationRagRetrievalResult | None = None
        try:
            if hybrid:
                generic_result = _retrieval.retrieve_hybrid(
                    generic_query,
                    generic_state,
                    _contracts.RetrievalLimits(max(1, len(request.text))),
                    _ports.Borrowed(embedder),
                    _ports.Borrowed(reader),
                    _ports.Borrowed(
                        _LexicalRetrieverBridge(
                            self._collaborators.lexical_retriever,
                            request,
                            documents,
                        )
                    ),
                )
            else:
                generic_result = _retrieval.retrieve_semantic(
                    generic_query,
                    generic_state,
                    _contracts.RetrievalLimits(max(1, len(request.text))),
                    _ports.Borrowed(embedder),
                    _ports.Borrowed(reader),
                )
            result = _from_generic_retrieval_result(
                generic_result,
                request,
                documents,
            )
        except Exception as exc:  # noqa: BLE001 - translate collaborator failures
            if type(exc) is _generic_errors.ContractValidationError:
                failure = ConversationRagContractError()
            else:
                failure = ConversationRagOperationError(
                    ConversationRagProjectionFailureStage.ADAPTER,
                    None,
                    0,
                    0,
                )
        if failure is not None:
            raise failure
        assert result is not None
        return result


__all__ = [
    "GenericRagCapabilitySnapshot",
    "GenericRagConversationService",
    "inspect_generic_rag_capabilities",
]
