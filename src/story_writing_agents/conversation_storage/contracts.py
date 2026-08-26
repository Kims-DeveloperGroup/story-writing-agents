"""Storage-selection contracts shared by conversation-storage adapters."""

from __future__ import annotations

import math
from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, Protocol, TypeVar

from ..conversation_search import ConversationSearch
from ..conversations import ConversationCatalog, ConversationEventStore
from ..errors import StoryWritingAgentsError


CONVERSATION_STORAGE_ACTIVATION_SCHEMA_VERSION = 1
CONVERSATION_STORAGE_MUTATION_MAX_TIMEOUT_SECONDS = 3_600.0

ArchivePayloadT_co = TypeVar("ArchivePayloadT_co", covariant=True)
ArchivePayloadT_contra = TypeVar("ArchivePayloadT_contra", contravariant=True)
ComponentT_co = TypeVar("ComponentT_co", covariant=True)


class ConversationStorageBackend(StrEnum):
    """Supported authoritative conversation-storage backends."""

    FILESYSTEM = "filesystem"
    SQLITE = "sqlite"


class ConversationStorageCapability(StrEnum):
    """Content-free result of preflighting one backend."""

    READY = "ready"
    PROVISIONING_NEEDED = "provisioning_needed"
    UNAVAILABLE = "unavailable"


class ConversationStorageParity(StrEnum):
    """Exact archive parity state for one proposed backend switch."""

    NOT_REQUIRED = "not_required"
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    MISMATCH = "mismatch"


class ConversationStorageActivationOutcomeKind(StrEnum):
    """Persisted result of one activation compare-and-set."""

    UNCHANGED = "unchanged"
    ACTIVATED = "activated"


class ConversationArchiveImportStatus(StrEnum):
    """Successful result of importing one generic exact archive payload."""

    IMPORTED = "imported"
    UNCHANGED = "unchanged"


class ConversationStorageFailure(StrEnum):
    """Stable, content-free failure codes for storage workflows."""

    INVALID_CONFIGURATION = "invalid_configuration"
    INCONSISTENT_SERVICES = "inconsistent_services"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    PROVISIONING_NEEDED = "provisioning_needed"
    MIGRATION_REQUIRED = "migration_required"
    PARITY_MISMATCH = "parity_mismatch"
    ACTIVATION_STATE_INVALID = "activation_state_invalid"
    ACTIVATION_STATE_UNSUPPORTED = "activation_state_unsupported"
    ACTIVATION_CONFLICT = "activation_conflict"
    ACTIVATION_FAILED = "activation_failed"
    COORDINATION_FAILED = "coordination_failed"


class ConversationStorageError(StoryWritingAgentsError):
    """Base class carrying one fixed conversation-storage failure code."""

    def __init__(self, failure: ConversationStorageFailure) -> None:
        if type(failure) is not ConversationStorageFailure:
            raise TypeError("failure must be a ConversationStorageFailure")
        self.failure = failure
        super().__init__(f"conversation storage failure: {failure.value}")


class ConversationStorageConfigurationError(ConversationStorageError):
    """Fixed failure for an invalid backend configuration value."""

    def __init__(self) -> None:
        super().__init__(ConversationStorageFailure.INVALID_CONFIGURATION)


class ConversationStorageServicesError(ConversationStorageError):
    """Fixed failure for a malformed explicit component binding."""

    def __init__(self) -> None:
        super().__init__(ConversationStorageFailure.INCONSISTENT_SERVICES)


class ConversationStorageUnavailableError(ConversationStorageError):
    """Fixed failure for a backend unavailable to the current host."""

    def __init__(self) -> None:
        super().__init__(ConversationStorageFailure.BACKEND_UNAVAILABLE)


class ConversationStorageProvisioningNeededError(ConversationStorageError):
    """Fixed failure when explicit host provisioning must run first."""

    def __init__(self) -> None:
        super().__init__(ConversationStorageFailure.PROVISIONING_NEEDED)


class ConversationStorageMigrationRequiredError(ConversationStorageError):
    """Fixed failure when a nonempty source has no verified migration."""

    def __init__(self) -> None:
        super().__init__(ConversationStorageFailure.MIGRATION_REQUIRED)


class ConversationStorageParityError(ConversationStorageError):
    """Fixed failure when exact source and destination archives differ."""

    def __init__(self) -> None:
        super().__init__(ConversationStorageFailure.PARITY_MISMATCH)


class ConversationStorageActivationStateError(ConversationStorageError):
    """Fixed failure for malformed persisted activation state."""

    def __init__(self) -> None:
        super().__init__(ConversationStorageFailure.ACTIVATION_STATE_INVALID)


class ConversationStorageActivationVersionError(ConversationStorageError):
    """Fixed failure for an unsupported persisted activation schema."""

    def __init__(self) -> None:
        super().__init__(ConversationStorageFailure.ACTIVATION_STATE_UNSUPPORTED)


class ConversationStorageActivationConflictError(ConversationStorageError):
    """Fixed failure when activation state changed after a caller's read."""

    def __init__(self) -> None:
        super().__init__(ConversationStorageFailure.ACTIVATION_CONFLICT)


class ConversationStorageActivationError(ConversationStorageError):
    """Fixed failure when validated activation state cannot be persisted."""

    def __init__(self) -> None:
        super().__init__(ConversationStorageFailure.ACTIVATION_FAILED)


class ConversationStorageCoordinationError(ConversationStorageError):
    """Fixed failure to acquire, validate, or release a mutation lease."""

    def __init__(self) -> None:
        super().__init__(ConversationStorageFailure.COORDINATION_FAILED)


def parse_conversation_storage_backend(
    value: object | None,
) -> ConversationStorageBackend:
    """Parse storage, treating a missing or null value as filesystem."""

    if value is None:
        return ConversationStorageBackend.FILESYSTEM
    if type(value) is not str:
        raise ConversationStorageConfigurationError()
    try:
        return ConversationStorageBackend(value)
    except ValueError:
        raise ConversationStorageConfigurationError() from None


def validate_conversation_storage_mutation_timeout(value: object) -> float:
    """Return one finite positive lease timeout within the fixed package cap."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("conversation storage mutation timeout must be a number")
    timeout = float(value)
    if (
        not math.isfinite(timeout)
        or timeout <= 0.0
        or timeout > CONVERSATION_STORAGE_MUTATION_MAX_TIMEOUT_SECONDS
    ):
        raise ValueError(
            "conversation storage mutation timeout must be positive and not "
            f"exceed {CONVERSATION_STORAGE_MUTATION_MAX_TIMEOUT_SECONDS:g} seconds"
        )
    return timeout


@dataclass(frozen=True, slots=True)
class ConversationStoragePreflight:
    """Typed capability result produced before a settings transaction."""

    backend: ConversationStorageBackend
    capability: ConversationStorageCapability

    def __post_init__(self) -> None:
        if type(self.backend) is not ConversationStorageBackend:
            raise TypeError("backend must be a ConversationStorageBackend")
        if type(self.capability) is not ConversationStorageCapability:
            raise TypeError("capability must be a ConversationStorageCapability")

    def require_ready(self) -> None:
        """Return normally only when adapter composition may proceed."""

        if self.capability is ConversationStorageCapability.READY:
            return
        if self.capability is ConversationStorageCapability.PROVISIONING_NEEDED:
            raise ConversationStorageProvisioningNeededError()
        raise ConversationStorageUnavailableError()


@dataclass(frozen=True, slots=True)
class ConversationStorageMigrationReadiness:
    """Parity proof governing whether one requested backend may activate."""

    source_backend: ConversationStorageBackend
    target_backend: ConversationStorageBackend
    source_generation: int
    source_is_empty: bool
    parity: ConversationStorageParity

    def __post_init__(self) -> None:
        if type(self.source_backend) is not ConversationStorageBackend:
            raise TypeError("source backend must be a ConversationStorageBackend")
        if type(self.target_backend) is not ConversationStorageBackend:
            raise TypeError("target backend must be a ConversationStorageBackend")
        if type(self.source_generation) is not int:
            raise TypeError("source generation must be an integer")
        if self.source_generation < 0:
            raise ValueError("source generation must not be negative")
        if type(self.source_is_empty) is not bool:
            raise TypeError("source is empty must be a boolean")
        if type(self.parity) is not ConversationStorageParity:
            raise TypeError("parity must be a ConversationStorageParity")
        if self.source_backend is self.target_backend or self.source_is_empty:
            if self.parity is not ConversationStorageParity.NOT_REQUIRED:
                raise ValueError("parity must be not_required for this transition")
        elif self.parity is ConversationStorageParity.NOT_REQUIRED:
            raise ValueError("a nonempty backend switch requires a parity result")

    @property
    def can_activate(self) -> bool:
        """Report whether this proof permits the requested activation."""

        return (
            self.source_backend is self.target_backend
            or self.source_is_empty
            or self.parity is ConversationStorageParity.VERIFIED
        )

    def require_activation_ready(self) -> None:
        """Raise a fixed failure unless activation is allowed."""

        if self.can_activate:
            return
        if self.parity is ConversationStorageParity.MISMATCH:
            raise ConversationStorageParityError()
        raise ConversationStorageMigrationRequiredError()


@dataclass(frozen=True, slots=True)
class ConversationStorageActivationState:
    """Versioned persisted backend selection and CAS generation."""

    backend: ConversationStorageBackend
    generation: int
    schema_version: int = CONVERSATION_STORAGE_ACTIVATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int:
            raise TypeError("activation schema version must be an integer")
        if self.schema_version != CONVERSATION_STORAGE_ACTIVATION_SCHEMA_VERSION:
            raise ValueError("unsupported conversation storage activation schema")
        if type(self.backend) is not ConversationStorageBackend:
            raise TypeError("backend must be a ConversationStorageBackend")
        if type(self.generation) is not int:
            raise TypeError("activation generation must be an integer")
        if self.generation < 0:
            raise ValueError("activation generation must not be negative")

    def to_dict(self) -> dict[str, object]:
        """Return the exact JSON-compatible activation-state schema."""

        return {
            "schema_version": self.schema_version,
            "backend": self.backend.value,
            "generation": self.generation,
        }


@dataclass(frozen=True, slots=True)
class ConversationStorageActivationOutcome:
    """Validated previous/current state pair from one activation attempt."""

    kind: ConversationStorageActivationOutcomeKind
    previous: ConversationStorageActivationState
    current: ConversationStorageActivationState

    def __post_init__(self) -> None:
        if type(self.kind) is not ConversationStorageActivationOutcomeKind:
            raise TypeError("activation outcome kind is invalid")
        if type(self.previous) is not ConversationStorageActivationState:
            raise TypeError("previous activation state is invalid")
        if type(self.current) is not ConversationStorageActivationState:
            raise TypeError("current activation state is invalid")
        if self.kind is ConversationStorageActivationOutcomeKind.UNCHANGED:
            if self.current != self.previous:
                raise ValueError("unchanged activation must preserve state")
            return
        if self.current.backend is self.previous.backend:
            raise ValueError("activated state must change backend")
        if self.current.generation != self.previous.generation + 1:
            raise ValueError("activated state must increment generation once")


@dataclass(frozen=True, slots=True, eq=False)
class ConversationStorageProvenance:
    """Typed backend/workspace identity asserted by one adapter composition."""

    backend: ConversationStorageBackend
    workspace_key: str

    def __post_init__(self) -> None:
        if type(self.backend) is not ConversationStorageBackend:
            raise TypeError("backend must be a ConversationStorageBackend")
        if type(self.workspace_key) is not str or not self.workspace_key:
            raise TypeError("workspace key must be a nonempty string")


class ConversationStorageMutationLease(Protocol):
    """Active, owner-thread-bound evidence for one workspace mutation."""

    @property
    def workspace_key(self) -> str: ...

    @property
    def owner_thread_id(self) -> int: ...

    @property
    def expires_at_monotonic(self) -> float: ...

    @property
    def active(self) -> bool: ...

    def validate(self, *, workspace_key: str) -> None:
        """Reject wrong-workspace, wrong-thread, expired, or released use."""

        ...


class ConversationStorageMutationCoordinator(Protocol):
    """Own one exclusive workspace mutation lease and its cleanup lifecycle."""

    @property
    def workspace_key(self) -> str: ...

    def borrow(
        self,
        *,
        timeout_seconds: float,
    ) -> AbstractContextManager[ConversationStorageMutationLease]:
        """Acquire one exclusive, bounded, same-thread-reentrant lease."""

        ...


@dataclass(frozen=True, slots=True)
class ConversationStorageComponentBinding(Generic[ComponentT_co]):
    """Typed adapter-owned binding of one raw port to one provenance token."""

    provenance: ConversationStorageProvenance
    component: ComponentT_co

    def __post_init__(self) -> None:
        if type(self.provenance) is not ConversationStorageProvenance:
            raise ConversationStorageServicesError()


@dataclass(frozen=True, slots=True)
class ConversationStorageServices:
    """Three same-provenance component bindings plus their coordinator."""

    catalog_binding: ConversationStorageComponentBinding[ConversationCatalog]
    event_store_binding: ConversationStorageComponentBinding[ConversationEventStore]
    search_binding: ConversationStorageComponentBinding[ConversationSearch]
    mutation_coordinator: ConversationStorageMutationCoordinator

    def __post_init__(self) -> None:
        bindings = (
            self.catalog_binding,
            self.event_store_binding,
            self.search_binding,
        )
        if any(
            type(binding) is not ConversationStorageComponentBinding
            for binding in bindings
        ):
            raise ConversationStorageServicesError()
        provenance = self.catalog_binding.provenance
        if any(binding.provenance is not provenance for binding in bindings[1:]):
            raise ConversationStorageServicesError()
        required_methods = (
            (
                self.catalog_binding.component,
                ("create", "get", "list", "rename", "archive", "unarchive"),
            ),
            (
                self.event_store_binding.component,
                ("append", "read", "read_context"),
            ),
            (self.search_binding.component, ("search",)),
        )
        for component, method_names in required_methods:
            if any(
                not callable(getattr(component, name, None))
                for name in method_names
            ):
                raise ConversationStorageServicesError()
        if not callable(getattr(self.mutation_coordinator, "borrow", None)):
            raise ConversationStorageServicesError()
        if (
            getattr(self.mutation_coordinator, "workspace_key", None)
            != provenance.workspace_key
        ):
            raise ConversationStorageServicesError()

    @property
    def provenance(self) -> ConversationStorageProvenance:
        return self.catalog_binding.provenance

    @property
    def backend(self) -> ConversationStorageBackend:
        return self.provenance.backend

    @property
    def catalog(self) -> ConversationCatalog:
        """Return the raw catalog port without wrapping or introspection."""

        return self.catalog_binding.component

    @property
    def event_store(self) -> ConversationEventStore:
        """Return the raw event-store port without wrapping or introspection."""

        return self.event_store_binding.component

    @property
    def search(self) -> ConversationSearch:
        """Return the raw search port without wrapping or introspection."""

        return self.search_binding.component


@dataclass(frozen=True, slots=True)
class ConversationArchiveImportReceipt:
    """Typed, payload-independent acknowledgement from an exact archive sink."""

    status: ConversationArchiveImportStatus

    def __post_init__(self) -> None:
        if type(self.status) is not ConversationArchiveImportStatus:
            raise TypeError("archive import status is invalid")


class ConversationArchiveSource(Protocol[ArchivePayloadT_co]):
    """Privileged generic exact-export port used under one active lease."""

    def export_archive(
        self,
        lease: ConversationStorageMutationLease,
    ) -> ArchivePayloadT_co:
        """Export an adapter payload preserving all authoritative values."""

        ...


class ConversationArchiveSink(Protocol[ArchivePayloadT_contra]):
    """Privileged generic exact-import port used under one active lease."""

    def import_archive(
        self,
        lease: ConversationStorageMutationLease,
        payload: ArchivePayloadT_contra,
    ) -> ConversationArchiveImportReceipt:
        """Import exact values or raise without ordinary ID/time allocation."""

        ...


__all__ = [
    "CONVERSATION_STORAGE_ACTIVATION_SCHEMA_VERSION",
    "CONVERSATION_STORAGE_MUTATION_MAX_TIMEOUT_SECONDS",
    "ConversationArchiveImportReceipt",
    "ConversationArchiveImportStatus",
    "ConversationArchiveSink",
    "ConversationArchiveSource",
    "ConversationStorageActivationConflictError",
    "ConversationStorageActivationError",
    "ConversationStorageActivationOutcome",
    "ConversationStorageActivationOutcomeKind",
    "ConversationStorageActivationState",
    "ConversationStorageActivationStateError",
    "ConversationStorageActivationVersionError",
    "ConversationStorageBackend",
    "ConversationStorageCapability",
    "ConversationStorageComponentBinding",
    "ConversationStorageConfigurationError",
    "ConversationStorageCoordinationError",
    "ConversationStorageError",
    "ConversationStorageFailure",
    "ConversationStorageMigrationReadiness",
    "ConversationStorageMigrationRequiredError",
    "ConversationStorageMutationCoordinator",
    "ConversationStorageMutationLease",
    "ConversationStorageParity",
    "ConversationStorageParityError",
    "ConversationStoragePreflight",
    "ConversationStorageProvenance",
    "ConversationStorageProvisioningNeededError",
    "ConversationStorageServices",
    "ConversationStorageServicesError",
    "ConversationStorageUnavailableError",
    "parse_conversation_storage_backend",
    "validate_conversation_storage_mutation_timeout",
]
