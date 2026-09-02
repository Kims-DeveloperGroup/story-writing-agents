"""Workspace-facing conversation-storage composition."""

from __future__ import annotations

import copy
import errno
import json
import os
import stat
import threading
import time
import weakref
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - non-POSIX hosts fail closed on borrow.
    _fcntl = None

from ..conversation_search import (
    CollaborationHistorySearchQuery,
    CollaborationHistorySearchResult,
    CollaborationHistorySearchScope,
    ReferenceCollaborationHistorySearch,
)
from ..workspace.core import Workspace
from .filesystem import (
    FilesystemCollaborationEventStore,
    FilesystemConversationCatalog,
)
from .contracts import (
    CONVERSATION_STORAGE_ACTIVATION_SCHEMA_VERSION,
    ConversationStorageActivationConflictError,
    ConversationStorageActivationError,
    ConversationStorageActivationOutcome,
    ConversationStorageActivationOutcomeKind,
    ConversationStorageActivationState,
    ConversationStorageActivationStateError,
    ConversationStorageActivationVersionError,
    ConversationStorageBackend,
    ConversationStorageComponentBinding,
    ConversationStorageCoordinationError,
    ConversationStorageTransferReadiness,
    ConversationStorageMutationLease,
    ConversationStorageProvenance,
    ConversationStorageServices,
    validate_conversation_storage_mutation_timeout,
)


CONVERSATION_STORAGE_ACTIVATION_STATE_PATH = "config/conversation_storage_state.json"
CONVERSATION_STORAGE_MUTATION_GUARD_PATH = "config/.conversation_storage.lock"

_ACTIVATION_STATE_FIELDS = frozenset(
    {
        "schema_version",
        "backend",
        "generation",
    }
)
_MUTATION_LOCK_RETRY_SECONDS = 0.05
_FILESYSTEM_MUTATION_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class _WorkspaceTopology:
    """One immutable canonical root and already-authorized workspace lease."""

    root: Path
    _workspace_lease: Workspace = field(repr=False, compare=False)

    @classmethod
    def capture(cls, workspace: Workspace) -> _WorkspaceTopology:
        """Capture one ready lease without later reopening its mutated root."""

        workspace.require_ready()
        root = Path(os.fspath(workspace.root))
        if not root.is_absolute():
            root = Path(os.path.abspath(root))
        lease = copy.copy(workspace)
        lease.root = root
        return cls(root=root, _workspace_lease=lease)

    @property
    def workspace_key(self) -> str:
        """Return the exact provenance and coordination key."""

        return os.fspath(self.root)

    def workspace(self) -> Workspace:
        """Return an isolated copy of the captured ready workspace lease."""

        workspace = copy.copy(self._workspace_lease)
        workspace.root = self.root
        return workspace


class _PinnedFilesystemConversationCatalog(FilesystemConversationCatalog):
    """Concrete catalog whose inherited operations use only a pinned topology."""

    __slots__ = ("_coordinator", "_topology")

    def __init__(
        self,
        topology: _WorkspaceTopology,
        construction_workspace: Workspace,
        coordinator: WorkspaceConversationStorageMutationCoordinator,
    ) -> None:
        self._topology = topology
        self._coordinator = coordinator
        super().__init__(construction_workspace)

    @property
    def workspace(self) -> Workspace:
        """Materialize a pinned workspace for each inherited operation access."""

        return self._topology.workspace()

    @workspace.setter
    def workspace(self, _workspace: object) -> None:
        """Ignore mutable collaborator rebinding in favor of the pinned topology."""

    def create(self, session_id: str, subject: str) -> Any:
        with self._coordinator.borrow(
            timeout_seconds=_FILESYSTEM_MUTATION_TIMEOUT_SECONDS
        ):
            return super().create(session_id, subject)

    def rename(
        self,
        session_id: str,
        conversation_id: str,
        subject: str,
    ) -> Any:
        with self._coordinator.borrow(
            timeout_seconds=_FILESYSTEM_MUTATION_TIMEOUT_SECONDS
        ):
            return super().rename(session_id, conversation_id, subject)

    def archive(
        self,
        session_id: str,
        conversation_id: str,
    ) -> Any:
        with self._coordinator.borrow(
            timeout_seconds=_FILESYSTEM_MUTATION_TIMEOUT_SECONDS
        ):
            return super().archive(session_id, conversation_id)

    def unarchive(
        self,
        session_id: str,
        conversation_id: str,
    ) -> Any:
        with self._coordinator.borrow(
            timeout_seconds=_FILESYSTEM_MUTATION_TIMEOUT_SECONDS
        ):
            return super().unarchive(session_id, conversation_id)


class _PinnedFilesystemCollaborationEventStore(FilesystemCollaborationEventStore):
    """Schema-v2 event store pinned to one topology and catalog collaborator."""

    __slots__ = ("_coordinator", "_pinned_catalog", "_topology")

    def __init__(
        self,
        topology: _WorkspaceTopology,
        construction_workspace: Workspace,
        *,
        catalog: FilesystemConversationCatalog,
        coordinator: WorkspaceConversationStorageMutationCoordinator,
    ) -> None:
        self._topology = topology
        self._pinned_catalog = catalog
        self._coordinator = coordinator
        super().__init__(construction_workspace, catalog=catalog)

    @property
    def workspace(self) -> Workspace:
        """Materialize a pinned workspace for each inherited operation access."""

        return self._topology.workspace()

    @workspace.setter
    def workspace(self, _workspace: object) -> None:
        """Ignore mutable workspace rebinding in favor of the pinned topology."""

    @property
    def catalog(self) -> FilesystemConversationCatalog:
        """Return the construction-pinned catalog collaborator."""

        return self._pinned_catalog

    @catalog.setter
    def catalog(self, _catalog: object) -> None:
        """Ignore mutable catalog rebinding in favor of the pinned collaborator."""

    def append_batch(self, batch: Any) -> Any:
        with self._coordinator.borrow(
            timeout_seconds=_FILESYSTEM_MUTATION_TIMEOUT_SECONDS
        ):
            return super().append_batch(batch)


class _PinnedReferenceCollaborationHistorySearch(ReferenceCollaborationHistorySearch):
    """Concrete search that ignores mutable inherited collaborator bindings."""

    __slots__ = ("_pinned_catalog", "_pinned_event_store")

    def __init__(
        self,
        catalog: FilesystemConversationCatalog,
        event_store: FilesystemCollaborationEventStore,
    ) -> None:
        self._pinned_catalog = catalog
        self._pinned_event_store = event_store
        super().__init__(catalog, event_store)

    def search(
        self,
        scope: CollaborationHistorySearchScope,
        query: CollaborationHistorySearchQuery,
    ) -> CollaborationHistorySearchResult:
        """Search through only the construction-pinned concrete collaborators."""

        return ReferenceCollaborationHistorySearch(
            self._pinned_catalog,
            self._pinned_event_store,
        ).search(scope, query)


class _WorkspaceMutationState:
    """Weakly retained synchronized process state for one workspace."""

    __slots__ = ("local", "lock", "__weakref__")

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.local = threading.local()


_MUTATION_STATES_GUARD = threading.Lock()
_MUTATION_STATES: weakref.WeakValueDictionary[
    str,
    _WorkspaceMutationState,
] = weakref.WeakValueDictionary()


def _mutation_state_for(workspace_key: str) -> _WorkspaceMutationState:
    """Return the shared weak process state for one canonical workspace."""

    with _MUTATION_STATES_GUARD:
        retained = _MUTATION_STATES.get(workspace_key)
        if retained is None:
            retained = _WorkspaceMutationState()
            _MUTATION_STATES[workspace_key] = retained
        return retained


def _reset_mutation_states() -> None:
    """Reset the weak registry when no coordinator borrow is active.

    This package-private test seam owns no descriptor: every descriptor is
    acquired and released by ``borrow`` before a state can become inactive.
    """

    with _MUTATION_STATES_GUARD:
        _MUTATION_STATES.clear()


class _WorkspaceConversationStorageMutationLease:
    """Coordinator-created, owner-thread-bound active lease evidence."""

    __slots__ = (
        "_active",
        "_expires_at_monotonic",
        "_owner_thread_id",
        "_workspace_key",
    )

    def __init__(self, *, workspace_key: str, expires_at_monotonic: float) -> None:
        self._workspace_key = workspace_key
        self._owner_thread_id = threading.get_ident()
        self._expires_at_monotonic = expires_at_monotonic
        self._active = True

    @property
    def workspace_key(self) -> str:
        return self._workspace_key

    @property
    def owner_thread_id(self) -> int:
        return self._owner_thread_id

    @property
    def expires_at_monotonic(self) -> float:
        return self._expires_at_monotonic

    @property
    def active(self) -> bool:
        return self._active and time.monotonic() < self._expires_at_monotonic

    def validate(self, *, workspace_key: str) -> None:
        """Reject wrong-workspace, wrong-thread, expired, or released use."""

        if (
            type(workspace_key) is not str
            or workspace_key != self._workspace_key
            or threading.get_ident() != self._owner_thread_id
            or not self.active
        ):
            raise ConversationStorageCoordinationError()

    def _deactivate(self) -> None:
        """End the coordinator-owned lifecycle after the outer borrow exits."""

        self._active = False


class WorkspaceConversationStorageMutationCoordinator:
    """Own one bounded exclusive mutation lease for a workspace.

    Construction performs no I/O. ``borrow`` validates its explicit timeout,
    acquires the shared in-process registry lock, then a POSIX advisory lock on
    the fixed contained ``config/.conversation_storage.lock`` guard. The guard
    may remain, but the descriptor and active lease never outlive the context.

    Same-thread nesting returns the same lease without reacquiring the advisory
    lock or extending its expiry. Every supported storage write must validate
    this lease before mutation. Acquire workspace leases in ascending
    ``workspace_key`` order and before catalog, event-store, archive, database,
    parity, transfer, or activation locks.
    """

    __slots__ = ("_state", "_topology", "_workspace_key")

    def __init__(self, workspace: Workspace) -> None:
        if not isinstance(workspace, Workspace):
            raise TypeError("workspace must be a Workspace")
        self._initialize_topology(_WorkspaceTopology.capture(workspace))

    @classmethod
    def _from_topology(
        cls,
        topology: _WorkspaceTopology,
    ) -> WorkspaceConversationStorageMutationCoordinator:
        """Construct internally from the bundle's already-captured topology."""

        coordinator = cls.__new__(cls)
        coordinator._initialize_topology(topology)
        return coordinator

    def _initialize_topology(self, topology: _WorkspaceTopology) -> None:
        """Bind immutable topology, exact key, and shared process state once."""

        self._topology = topology
        self._workspace_key = topology.workspace_key
        self._state = _mutation_state_for(self._workspace_key)

    @property
    def workspace_key(self) -> str:
        """Return the canonical workspace identity without filesystem I/O."""

        return self._workspace_key

    @contextmanager
    def borrow(
        self,
        *,
        timeout_seconds: float,
    ) -> Iterator[ConversationStorageMutationLease]:
        """Borrow one active lease until exit or its monotonic expiry."""

        timeout = validate_conversation_storage_mutation_timeout(timeout_seconds)
        deadline = time.monotonic() + timeout
        if not self._state.lock.acquire(timeout=timeout):
            raise ConversationStorageCoordinationError()

        depth = getattr(self._state.local, "depth", 0)
        if depth > 0:
            lease = self._state.local.lease
            try:
                lease.validate(workspace_key=self._workspace_key)
                self._state.local.depth = depth + 1
                try:
                    yield lease
                finally:
                    self._state.local.depth -= 1
            finally:
                self._state.lock.release()
            return

        descriptor: int | None = None
        lease: _WorkspaceConversationStorageMutationLease | None = None
        body_failure: BaseException | None = None
        cleanup_failure: BaseException | None = None
        try:
            descriptor = self._open_guard()
            self._acquire_guard(descriptor, deadline=deadline)
            self._verify_guard(descriptor)
            lease = _WorkspaceConversationStorageMutationLease(
                workspace_key=self._workspace_key,
                expires_at_monotonic=deadline,
            )
            self._state.local.depth = 1
            self._state.local.lease = lease
            self._state.local.descriptor = descriptor
            try:
                yield lease
            except BaseException as exc:
                body_failure = exc
                raise
        finally:
            if lease is not None:
                lease._deactivate()
            if descriptor is not None:
                try:
                    self._release_guard(descriptor)
                except BaseException as exc:
                    cleanup_failure = exc
                try:
                    os.close(descriptor)
                except BaseException as exc:
                    if cleanup_failure is None:
                        cleanup_failure = exc
            for attribute in ("descriptor", "lease", "depth"):
                try:
                    delattr(self._state.local, attribute)
                except AttributeError:
                    pass
            try:
                self._state.lock.release()
            except BaseException as exc:
                if cleanup_failure is None:
                    cleanup_failure = exc
            if cleanup_failure is not None:
                if body_failure is not None:
                    body_failure.add_note(
                        "conversation storage coordination cleanup failed"
                    )
                elif isinstance(cleanup_failure, Exception):
                    raise ConversationStorageCoordinationError() from None
                else:
                    raise cleanup_failure

    def _open_guard(self) -> int:
        """Open and validate the fixed contained regular guard file."""

        descriptor: int | None = None
        try:
            workspace = self._topology.workspace()
            parent = workspace.path("config")
            target = workspace.path(CONVERSATION_STORAGE_MUTATION_GUARD_PATH)
            if not parent.is_dir():
                raise OSError("conversation storage guard parent is unavailable")
            flags = os.O_CREAT | os.O_RDWR
            for name in ("O_CLOEXEC", "O_NOFOLLOW"):
                flags |= getattr(os, name, 0)
            descriptor = os.open(target, flags, 0o600)
            os.set_inheritable(descriptor, False)
            self._verify_guard(descriptor)
            return descriptor
        except Exception:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            raise ConversationStorageCoordinationError() from None

    def _verify_guard(self, descriptor: int) -> None:
        """Require descriptor and fixed name to identify one safe regular file."""

        try:
            target = self._topology.workspace().path(
                CONVERSATION_STORAGE_MUTATION_GUARD_PATH
            )
            opened = os.fstat(descriptor)
            named = os.stat(target, follow_symlinks=False)
        except Exception:
            raise ConversationStorageCoordinationError() from None
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or opened.st_nlink != 1
            or named.st_nlink != 1
        ):
            raise ConversationStorageCoordinationError()
        opened_identity = (opened.st_dev, opened.st_ino)
        named_identity = (named.st_dev, named.st_ino)
        if any((*opened_identity, *named_identity)) and (
            opened_identity != named_identity
        ):
            raise ConversationStorageCoordinationError()

    @staticmethod
    def _acquire_guard(descriptor: int, *, deadline: float) -> None:
        """Acquire the POSIX advisory lock within the lease deadline."""

        if _fcntl is None:
            raise ConversationStorageCoordinationError()
        contention = {
            errno.EACCES,
            errno.EAGAIN,
            getattr(errno, "EDEADLK", errno.EACCES),
        }
        while True:
            try:
                _fcntl.flock(descriptor, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                return
            except OSError as exc:
                if exc.errno not in contention:
                    raise ConversationStorageCoordinationError() from None
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    raise ConversationStorageCoordinationError() from None
                time.sleep(min(_MUTATION_LOCK_RETRY_SECONDS, remaining))

    @staticmethod
    def _release_guard(descriptor: int) -> None:
        """Release the POSIX advisory lock held by an outer borrow."""

        if _fcntl is None:
            raise ConversationStorageCoordinationError()
        _fcntl.flock(descriptor, _fcntl.LOCK_UN)


def compose_filesystem_conversation_storage(
    workspace: Workspace | str | os.PathLike[str],
) -> ConversationStorageServices:
    """Bind the filesystem catalog and sole schema-v2 history/search ports."""

    resolved = workspace if isinstance(workspace, Workspace) else Workspace(workspace)
    topology = _WorkspaceTopology.capture(resolved)
    provenance = ConversationStorageProvenance(
        backend=ConversationStorageBackend.FILESYSTEM,
        workspace_key=topology.workspace_key,
    )
    coordinator = WorkspaceConversationStorageMutationCoordinator._from_topology(
        topology
    )
    catalog = _PinnedFilesystemConversationCatalog(
        topology,
        resolved,
        coordinator,
    )
    event_store = _PinnedFilesystemCollaborationEventStore(
        topology,
        resolved,
        catalog=catalog,
        coordinator=coordinator,
    )
    search = _PinnedReferenceCollaborationHistorySearch(catalog, event_store)
    return ConversationStorageServices(
        catalog_binding=ConversationStorageComponentBinding(
            provenance=provenance,
            component=catalog,
        ),
        event_store_binding=ConversationStorageComponentBinding(
            provenance=provenance,
            component=event_store,
        ),
        search_binding=ConversationStorageComponentBinding(
            provenance=provenance,
            component=search,
        ),
        mutation_coordinator=coordinator,
    )


def load_conversation_storage_activation_state(
    workspace: Workspace,
) -> ConversationStorageActivationState:
    """Read exact state; absence returns filesystem generation zero, unwritten."""

    if not isinstance(workspace, Workspace):
        raise TypeError("workspace must be a Workspace")
    try:
        topology = _WorkspaceTopology.capture(workspace)
    except Exception:
        raise ConversationStorageActivationStateError() from None
    return _load_conversation_storage_activation_state(topology)


def _load_conversation_storage_activation_state(
    topology: _WorkspaceTopology,
) -> ConversationStorageActivationState:
    """Read activation state through one operation-local pinned workspace."""

    try:
        workspace = topology.workspace()
        target = workspace.path(CONVERSATION_STORAGE_ACTIVATION_STATE_PATH)
        if not target.exists():
            return ConversationStorageActivationState(
                backend=ConversationStorageBackend.FILESYSTEM,
                generation=0,
            )
        if not target.is_file():
            raise ConversationStorageActivationStateError()
        value = json.loads(
            workspace.read_text(CONVERSATION_STORAGE_ACTIVATION_STATE_PATH),
            object_pairs_hook=_reject_duplicate_fields,
        )
        if type(value) is not dict:
            raise ConversationStorageActivationStateError()
        raw_version = value.get("schema_version")
        if type(raw_version) is int and (
            raw_version != CONVERSATION_STORAGE_ACTIVATION_SCHEMA_VERSION
        ):
            raise ConversationStorageActivationVersionError()
        if set(value) != _ACTIVATION_STATE_FIELDS or type(raw_version) is not int:
            raise ConversationStorageActivationStateError()
        raw_backend = value["backend"]
        if type(raw_backend) is not str:
            raise ConversationStorageActivationStateError()
        try:
            return ConversationStorageActivationState(
                schema_version=raw_version,
                backend=ConversationStorageBackend(raw_backend),
                generation=value["generation"],
            )
        except (TypeError, ValueError):
            raise ConversationStorageActivationStateError() from None
    except ConversationStorageActivationVersionError:
        raise
    except ConversationStorageActivationStateError:
        raise
    except Exception:
        raise ConversationStorageActivationStateError() from None


def compare_and_set_conversation_storage_activation(
    workspace: Workspace,
    *,
    lease: ConversationStorageMutationLease,
    expected_generation: int,
    readiness: ConversationStorageTransferReadiness,
) -> ConversationStorageActivationOutcome:
    """Activate under a supplied valid lease using expected-generation CAS.

    The caller holds one outer lease across source inspection, transfer,
    parity, and this operation. Same-backend activation is an unwritten no-op.
    A backend change persists generation ``expected_generation + 1`` through
    contained atomic ``Workspace.write_json`` before returning its outcome.
    """

    if not isinstance(workspace, Workspace):
        raise TypeError("workspace must be a Workspace")
    if type(expected_generation) is not int:
        raise TypeError("expected generation must be an integer")
    if expected_generation < 0:
        raise ValueError("expected generation must not be negative")
    if type(readiness) is not ConversationStorageTransferReadiness:
        raise TypeError("readiness must be a ConversationStorageTransferReadiness")
    topology = _WorkspaceTopology.capture(workspace)
    workspace_key = topology.workspace_key
    try:
        lease.validate(workspace_key=workspace_key)
    except ConversationStorageCoordinationError:
        raise
    except Exception:
        raise ConversationStorageCoordinationError() from None

    current = _load_conversation_storage_activation_state(topology)
    if (
        current.generation != expected_generation
        or readiness.source_generation != expected_generation
        or readiness.source_backend is not current.backend
    ):
        raise ConversationStorageActivationConflictError()
    readiness.require_activation_ready()
    if readiness.target_backend is current.backend:
        return ConversationStorageActivationOutcome(
            kind=ConversationStorageActivationOutcomeKind.UNCHANGED,
            previous=current,
            current=current,
        )
    try:
        lease.validate(workspace_key=workspace_key)
    except ConversationStorageCoordinationError:
        raise
    except Exception:
        raise ConversationStorageCoordinationError() from None
    next_state = ConversationStorageActivationState(
        backend=readiness.target_backend,
        generation=expected_generation + 1,
    )
    try:
        topology.workspace().write_json(
            CONVERSATION_STORAGE_ACTIVATION_STATE_PATH,
            next_state.to_dict(),
        )
    except Exception:
        raise ConversationStorageActivationError() from None
    return ConversationStorageActivationOutcome(
        kind=ConversationStorageActivationOutcomeKind.ACTIVATED,
        previous=current,
        current=next_state,
    )


def _reject_duplicate_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build an activation object while rejecting duplicate JSON fields."""

    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate activation-state field")
        value[key] = item
    return value


__all__ = [
    "CONVERSATION_STORAGE_ACTIVATION_STATE_PATH",
    "CONVERSATION_STORAGE_MUTATION_GUARD_PATH",
    "WorkspaceConversationStorageMutationCoordinator",
    "compare_and_set_conversation_storage_activation",
    "compose_filesystem_conversation_storage",
    "load_conversation_storage_activation_state",
]
