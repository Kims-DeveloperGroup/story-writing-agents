"""Deferred explicit composition for the optional generic-rag dependency."""

from __future__ import annotations

from contextlib import suppress
from enum import StrEnum
from importlib import metadata
from typing import NoReturn, cast

from .conversation_rag import (
    ConversationRagCollaborators,
    ConversationRagError,
    ConversationRagLexicalRetriever,
    ConversationRagOutcome,
    ConversationRagProjectionRequest,
    ConversationRagProjectionResult,
    ConversationRagProjectionState,
    ConversationRagReason,
    ConversationRagRetrievalRequest,
    ConversationRagRetrievalResult,
    ConversationRagService,
    ConversationRagVectorIndexReader,
    ConversationRagVectorIndexResetter,
    ConversationRagVectorIndexWriter,
)
from .conversation_rag_local import (
    LOCAL_CONVERSATION_RAG_PROVIDER_ID,
    LocalConversationRagEmbedder,
    LocalConversationRagVectorRanker,
)
from .conversation_rag_store import (
    ConversationRagGenerationStore,
    ConversationRagStoreError,
    ConversationRagStoreFailure,
    ConversationRagStoreSnapshot,
    ConversationRagStoreStatus,
)

CONVERSATION_RAG_PROVIDER_ALLOWLIST = ("local",)

_GENERIC_RAG_DISTRIBUTION = "generic-rag"
_GENERIC_RAG_VERSION = "0.1.0"
_GENERIC_RAG_REQUIREMENTS = (
    (
        "generic_rag.contracts",
        (
            "ChunkingPolicy",
            "Document",
            "DocumentIdentity",
            "DocumentKey",
            "EmbeddingIdentity",
            "EmbeddingVector",
            "Fragment",
            "FragmentIdentity",
            "ProjectionCheckpoint",
            "ProjectionIdentity",
            "ProjectionLimits",
            "ProjectionManifest",
            "ProjectionManifestEntry",
            "ProjectionOutcome",
            "ProjectionReceipt",
            "ProjectionRequest",
            "ProjectionResult",
            "ProjectionStateAvailability",
            "ProjectionStateSnapshot",
            "ProjectionStateStatus",
            "RetrievalHit",
            "RetrievalLimits",
            "RetrievalOutcome",
            "RetrievalQuery",
            "RetrievalResult",
            "VectorRecord",
        ),
    ),
    (
        "generic_rag.errors",
        ("ContractValidationError",),
    ),
    (
        "generic_rag.ports",
        ("Borrowed",),
    ),
    (
        "generic_rag.projection",
        (
            "ProjectionFailureStage",
            "ProjectionOperationError",
            "ProjectionStateError",
            "project_documents",
            "rebuild_projection",
        ),
    ),
    (
        "generic_rag.retrieval",
        ("retrieve_hybrid", "retrieve_semantic"),
    ),
)


class ConversationRagCompositionFailure(StrEnum):
    """Fixed content-free optional-composition failure categories."""

    PROVIDER_UNSUPPORTED = "provider_unsupported"
    DEPENDENCY_MISSING = "dependency_missing"
    VERSION_INCOMPATIBLE = "version_incompatible"
    CAPABILITY_INCOMPATIBLE = "capability_incompatible"


_FAILURE_MESSAGES = {
    ConversationRagCompositionFailure.PROVIDER_UNSUPPORTED: (
        "conversation RAG provider is not supported"
    ),
    ConversationRagCompositionFailure.DEPENDENCY_MISSING: (
        "conversation RAG is unavailable; install the pinned 'rag' extra"
    ),
    ConversationRagCompositionFailure.VERSION_INCOMPATIBLE: (
        "conversation RAG dependency version is incompatible; "
        "reinstall the pinned 'rag' extra"
    ),
    ConversationRagCompositionFailure.CAPABILITY_INCOMPATIBLE: (
        "conversation RAG dependency capabilities are incompatible; "
        "reinstall the pinned 'rag' extra"
    ),
}


class ConversationRagCompositionError(ConversationRagError):
    """Sanitized actionable failure from explicit optional composition."""

    failure: ConversationRagCompositionFailure

    def __init__(self, failure: ConversationRagCompositionFailure) -> None:
        if type(failure) is not ConversationRagCompositionFailure:
            raise TypeError("composition failure must use the exact enum")
        self.failure = failure
        Exception.__init__(self, _FAILURE_MESSAGES[failure])


def _raise_composition_failure(
    failure: ConversationRagCompositionFailure,
) -> NoReturn:
    """Raise one fixed error outside any caught dependency exception scope."""

    raise ConversationRagCompositionError(failure)


def compose_conversation_rag(
    provider: str,
    collaborators: ConversationRagCollaborators,
    /,
) -> ConversationRagService:
    """Compose the explicit allowlisted optional service after compatibility checks.

    Importing this module does not inspect metadata or import generic-rag. This
    function performs one local distribution-version read and then a deferred,
    statically named adapter import. It never discovers providers dynamically.
    """

    if type(provider) is not str or provider not in CONVERSATION_RAG_PROVIDER_ALLOWLIST:
        _raise_composition_failure(
            ConversationRagCompositionFailure.PROVIDER_UNSUPPORTED
        )
    if type(collaborators) is not ConversationRagCollaborators:
        raise TypeError("collaborators must be exactly ConversationRagCollaborators")

    metadata_failure: ConversationRagCompositionFailure | None = None
    installed_version: str | None = None
    try:
        installed_version = metadata.version(_GENERIC_RAG_DISTRIBUTION)
    except metadata.PackageNotFoundError:
        metadata_failure = ConversationRagCompositionFailure.DEPENDENCY_MISSING
    except Exception:  # noqa: BLE001 - sanitize optional metadata failures
        metadata_failure = ConversationRagCompositionFailure.CAPABILITY_INCOMPATIBLE
    if metadata_failure is not None:
        _raise_composition_failure(metadata_failure)
    if installed_version != _GENERIC_RAG_VERSION:
        _raise_composition_failure(
            ConversationRagCompositionFailure.VERSION_INCOMPATIBLE
        )

    import_failure = False
    adapter_symbols: tuple[object, object] | None = None
    try:
        from .generic_rag_adapter import (
            GenericRagConversationService,
            inspect_generic_rag_capabilities,
        )

        adapter_symbols = (
            GenericRagConversationService,
            inspect_generic_rag_capabilities,
        )
    except Exception:  # noqa: BLE001 - sanitize optional import failures
        import_failure = True
    if import_failure or adapter_symbols is None:
        _raise_composition_failure(
            ConversationRagCompositionFailure.CAPABILITY_INCOMPATIBLE
        )

    service_type, capability_inspector = adapter_symbols
    capability_failure = False
    try:
        snapshot = capability_inspector()  # type: ignore[operator]
        capability_failure = not snapshot.supports(_GENERIC_RAG_REQUIREMENTS)
    except Exception:  # noqa: BLE001 - sanitize capability probe failures
        capability_failure = True
    if capability_failure:
        _raise_composition_failure(
            ConversationRagCompositionFailure.CAPABILITY_INCOMPATIBLE
        )

    service: object | None = None
    service_failure = False
    try:
        service = service_type(collaborators)  # type: ignore[operator]
    except Exception:  # noqa: BLE001 - sanitize optional construction failures
        service_failure = True
    if service_failure or service is None:
        _raise_composition_failure(
            ConversationRagCompositionFailure.CAPABILITY_INCOMPATIBLE
        )
    return service  # type: ignore[return-value]


class _UnusedCollaborator:
    """Non-null sentinel for generic collaborators unused by one operation."""

    __slots__ = ()


_UNUSED_COLLABORATOR = _UnusedCollaborator()
_UNUSED_WRITER = cast(ConversationRagVectorIndexWriter, _UNUSED_COLLABORATOR)
_UNUSED_RESETTER = cast(ConversationRagVectorIndexResetter, _UNUSED_COLLABORATOR)
_UNUSED_READER = cast(ConversationRagVectorIndexReader, _UNUSED_COLLABORATOR)


def _literal_fallback(
    request: ConversationRagRetrievalRequest,
    reason: ConversationRagReason,
) -> ConversationRagRetrievalResult:
    return ConversationRagRetrievalResult(
        request,
        ConversationRagOutcome.LITERAL_FALLBACK,
        reason,
        (),
        False,
    )


def _status_reason(status: ConversationRagStoreStatus) -> ConversationRagReason:
    return {
        ConversationRagStoreStatus.MISSING: ConversationRagReason.UNAVAILABLE,
        ConversationRagStoreStatus.STALE: ConversationRagReason.STALE,
        ConversationRagStoreStatus.CORRUPT: ConversationRagReason.CORRUPT,
        ConversationRagStoreStatus.INCOMPATIBLE: ConversationRagReason.INCOMPATIBLE,
        ConversationRagStoreStatus.PROVIDER_FAILED: ConversationRagReason.FAILED,
    }[status]


def _store_failure_reason(
    failure: ConversationRagStoreFailure,
) -> ConversationRagReason:
    return {
        ConversationRagStoreFailure.UNSUPPORTED: ConversationRagReason.FAILED,
        ConversationRagStoreFailure.BUSY: ConversationRagReason.FAILED,
        ConversationRagStoreFailure.UNSAFE: ConversationRagReason.CORRUPT,
        ConversationRagStoreFailure.CORRUPT: ConversationRagReason.CORRUPT,
        ConversationRagStoreFailure.INCOMPATIBLE: (ConversationRagReason.INCOMPATIBLE),
        ConversationRagStoreFailure.PROVIDER_FAILED: ConversationRagReason.FAILED,
        ConversationRagStoreFailure.CONFIRMATION_REQUIRED: (
            ConversationRagReason.FAILED
        ),
        ConversationRagStoreFailure.OPERATION_FAILED: ConversationRagReason.FAILED,
    }[failure]


class LocalConversationRagComposition:
    """Store-aware local service that creates one generic adapter per call."""

    __slots__ = ("_embedder", "_lexical_retriever", "_ranker", "_store")

    def __init__(
        self,
        store: ConversationRagGenerationStore,
        lexical_retriever: ConversationRagLexicalRetriever,
        /,
    ) -> None:
        if type(store) is not ConversationRagGenerationStore:
            raise TypeError("local composition store must use the exact type")
        if lexical_retriever is None or not callable(
            getattr(lexical_retriever, "search", None)
        ):
            raise TypeError("local composition lexical retriever must provide search")
        self._store = store
        self._lexical_retriever = lexical_retriever
        self._embedder = LocalConversationRagEmbedder()
        self._ranker = LocalConversationRagVectorRanker()
        # Explicit composition validates the optional pin/capabilities now;
        # importing this module remains provider-free and side-effect-free.
        compose_conversation_rag(
            LOCAL_CONVERSATION_RAG_PROVIDER_ID,
            ConversationRagCollaborators(
                self._embedder,
                _UNUSED_WRITER,
                _UNUSED_RESETTER,
                _UNUSED_READER,
                self._lexical_retriever,
            ),
        )

    @property
    def service(self) -> LocalConversationRagComposition:
        """Return the provider-neutral composed service."""

        return self

    @property
    def store(self) -> ConversationRagGenerationStore:
        """Return the retained caller-owned generation store."""

        return self._store

    def _service(
        self,
        collaborators: ConversationRagCollaborators,
    ) -> ConversationRagService:
        return compose_conversation_rag(
            LOCAL_CONVERSATION_RAG_PROVIDER_ID,
            collaborators,
        )

    def _record_provider_failure(self, session_id: str) -> None:
        # Advisory persistence must not replace the established ordinary
        # provider failure; Exception deliberately excludes control failures.
        with suppress(Exception):
            self._store.set_status(
                session_id,
                ConversationRagStoreStatus.PROVIDER_FAILED,
            )

    def project_documents(
        self,
        request: ConversationRagProjectionRequest,
        state: ConversationRagProjectionState,
        /,
    ) -> ConversationRagProjectionResult:
        """Incrementally project through one unpublished call-local builder."""

        return self._project(request, state, rebuild=False)

    def rebuild_projection(
        self,
        request: ConversationRagProjectionRequest,
        state: ConversationRagProjectionState,
        /,
    ) -> ConversationRagProjectionResult:
        """Restart and completely rebuild through one inactive generation."""

        return self._project(request, state, rebuild=True)

    def _project(
        self,
        request: ConversationRagProjectionRequest,
        state: ConversationRagProjectionState,
        *,
        rebuild: bool,
    ) -> ConversationRagProjectionResult:
        if type(request) is not ConversationRagProjectionRequest:
            raise TypeError("projection request must use the exact RAG request")
        if type(state) is not ConversationRagProjectionState:
            raise TypeError("projection state must use the exact RAG state")
        try:
            with self._store.begin_generation(
                LOCAL_CONVERSATION_RAG_PROVIDER_ID,
                request,
                state,
                rebuild=rebuild,
            ) as builder:
                service = self._service(
                    ConversationRagCollaborators(
                        self._embedder,
                        builder,
                        builder,
                        _UNUSED_READER,
                        self._lexical_retriever,
                    )
                )
                result = (
                    service.rebuild_projection(request, builder.state)
                    if rebuild
                    else service.project_documents(request, builder.state)
                )
                builder.publish(result)
                return result
        except ConversationRagStoreError:
            raise
        except Exception:
            self._record_provider_failure(request.session_id)
            raise

    def retrieve_semantic(
        self,
        request: ConversationRagRetrievalRequest,
        state: ConversationRagProjectionState,
        /,
    ) -> ConversationRagRetrievalResult:
        """Retrieve from one fully verified active local generation."""

        return self._retrieve(request, state, hybrid=False)

    def retrieve_hybrid(
        self,
        request: ConversationRagRetrievalRequest,
        state: ConversationRagProjectionState,
        /,
    ) -> ConversationRagRetrievalResult:
        """Fuse verified local and caller-owned lexical candidates."""

        return self._retrieve(request, state, hybrid=True)

    def _retrieve(
        self,
        request: ConversationRagRetrievalRequest,
        state: ConversationRagProjectionState,
        *,
        hybrid: bool,
    ) -> ConversationRagRetrievalResult:
        if type(request) is not ConversationRagRetrievalRequest:
            raise TypeError("retrieval request must use the exact RAG request")
        if type(state) is not ConversationRagProjectionState:
            raise TypeError("retrieval state must use the exact RAG state")
        try:
            if state.checkpoint is None:
                snapshot = self._store.inspect(
                    request.session_id,
                    LOCAL_CONVERSATION_RAG_PROVIDER_ID,
                    self._embedder.identity,
                )
            else:
                snapshot = self._store.inspect(
                    request.session_id,
                    LOCAL_CONVERSATION_RAG_PROVIDER_ID,
                    self._embedder.identity,
                    expected_projection_schema_id=state.checkpoint.schema_id,
                )
        except ConversationRagStoreError as exc:
            return _literal_fallback(request, _store_failure_reason(exc.failure))
        if snapshot.status is not ConversationRagStoreStatus.READY:
            return _literal_fallback(request, _status_reason(snapshot.status))
        if snapshot.state != state:
            return _literal_fallback(request, ConversationRagReason.STALE)
        return self._retrieve_ready(request, snapshot, hybrid=hybrid)

    def _retrieve_ready(
        self,
        request: ConversationRagRetrievalRequest,
        snapshot: ConversationRagStoreSnapshot,
        *,
        hybrid: bool,
    ) -> ConversationRagRetrievalResult:
        try:
            with self._store.open_active_reader(snapshot, self._ranker) as reader:
                service = self._service(
                    ConversationRagCollaborators(
                        self._embedder,
                        _UNUSED_WRITER,
                        _UNUSED_RESETTER,
                        reader,
                        self._lexical_retriever,
                    )
                )
                return (
                    service.retrieve_hybrid(request, snapshot.state)
                    if hybrid
                    else service.retrieve_semantic(request, snapshot.state)
                )
        except ConversationRagCompositionError as exc:
            reason = (
                ConversationRagReason.UNAVAILABLE
                if exc.failure is ConversationRagCompositionFailure.DEPENDENCY_MISSING
                else ConversationRagReason.INCOMPATIBLE
            )
            return _literal_fallback(request, reason)
        except ConversationRagStoreError as exc:
            return _literal_fallback(request, _store_failure_reason(exc.failure))
        except ConversationRagError:
            return _literal_fallback(request, ConversationRagReason.FAILED)


def compose_local_conversation_rag(
    store: ConversationRagGenerationStore,
    lexical_retriever: ConversationRagLexicalRetriever,
    /,
) -> LocalConversationRagComposition:
    """Compose the fixed network-free provider over an explicit store."""

    return LocalConversationRagComposition(store, lexical_retriever)


__all__ = [
    "CONVERSATION_RAG_PROVIDER_ALLOWLIST",
    "ConversationRagCompositionError",
    "ConversationRagCompositionFailure",
    "LocalConversationRagComposition",
    "compose_conversation_rag",
    "compose_local_conversation_rag",
]
