"""Deferred explicit composition for the optional generic-rag dependency."""

from __future__ import annotations

from enum import StrEnum
from importlib import metadata
from typing import NoReturn

from .conversation_rag import (
    ConversationRagCollaborators,
    ConversationRagError,
    ConversationRagService,
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


__all__ = [
    "CONVERSATION_RAG_PROVIDER_ALLOWLIST",
    "ConversationRagCompositionError",
    "ConversationRagCompositionFailure",
    "compose_conversation_rag",
]
