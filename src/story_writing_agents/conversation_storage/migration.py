"""Provider-free canonical filesystem and SQLite conversation migration."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..errors import (
    SessionIdError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
    WorkspaceVersionUnsupportedError,
)
from ..workspace.core import Workspace
from .archive import (
    CONVERSATION_ARCHIVE_MAX_CONVERSATIONS,
    CONVERSATION_ARCHIVE_MAX_EVENTS,
    ConversationArchive,
    ConversationArchiveBoundsError,
    ConversationArchiveConflictError,
    ConversationArchiveConversation,
    ConversationArchiveDestinationError,
    ConversationArchiveError,
    ConversationArchiveFormatError,
    ConversationArchiveSourceChangedError,
    ConversationArchiveSourceError,
    ConversationArchiveVersionError,
    deserialize_conversation_archive,
    serialize_conversation_archive,
)
from .composition import (
    WorkspaceConversationStorageMutationCoordinator,
    load_conversation_storage_activation_state,
)
from .contracts import (
    ConversationStorageActivationStateError,
    ConversationStorageActivationVersionError,
    ConversationStorageBackend,
    ConversationStorageConfigurationError,
    ConversationStorageCoordinationError,
    ConversationStorageError,
    ConversationStorageMutationLease,
    ConversationStorageParity,
    ConversationStorageProvisioningNeededError,
    ConversationStorageUnavailableError,
    parse_conversation_storage_backend,
)
from .filesystem_archive import (
    FilesystemConversationArchiveAdapter,
    compose_filesystem_conversation_archive,
)
from .sqlite import (
    SQLiteConversationArchiveAdapter,
    compose_sqlite_conversation_archive,
)


CONVERSATION_STORAGE_MIGRATION_SCHEMA_VERSION = 1
CONVERSATION_STORAGE_MIGRATION_CHECKPOINT_DIRECTORY = (
    "agent_states/orchestrator_state/conversation_storage_migrations"
)

_MIGRATION_TIMEOUT_SECONDS = 3_600.0
_SQLITE_SIGNED_INTEGER_MAX = 9_223_372_036_854_775_807
_CHECKPOINT_IDENTITY_MAX_BYTES = 256
_CHECKPOINT_FIXED_MAX_BYTES = 4_096
_CHECKPOINT_MAX_RAW_BYTES = (
    _CHECKPOINT_FIXED_MAX_BYTES
    + CONVERSATION_ARCHIVE_MAX_CONVERSATIONS * _CHECKPOINT_IDENTITY_MAX_BYTES
)
_CHECKPOINT_FIELDS = frozenset(
    {
        "schema_version",
        "direction",
        "source_fingerprint",
        "source",
        "progress",
        "completed",
    }
)
_CHECKPOINT_SOURCE_FIELDS = frozenset({"conversations", "events"})
_CHECKPOINT_PROGRESS_FIELDS = frozenset(
    {"completed_conversations", "total_conversations"}
)
_CHECKPOINT_IDENTITY_FIELDS = frozenset({"session_id", "conversation_id"})
_FINGERPRINT_PATTERN = re.compile(r"[0-9a-f]{64}")
_CHECKPOINT_STATES = frozenset({"none", "resumed", "advanced", "completed"})
_FAILURE_CODES = frozenset(
    {
        "activation_invalid",
        "activation_mismatch",
        "backend_unavailable",
        "checkpoint_invalid",
        "checkpoint_unsupported",
        "conflict",
        "coordination_failed",
        "destination_invalid",
        "destination_only",
        "invalid_request",
        "operational_failure",
        "parity_mismatch",
        "provisioning_needed",
        "source_changed",
        "source_invalid",
        "unsafe_state",
        "unsupported",
        "workspace_invalid",
    }
)

_Identity = tuple[str, str]


class ConversationStorageMigrationOperation(StrEnum):
    """Supported explicit conversation-storage workflows."""

    MIGRATE = "migrate"
    VERIFY = "verify"


class ConversationStorageMigrationOutcome(StrEnum):
    """Stable top-level migration report outcomes."""

    COMPLETED = "completed"
    WORK_REQUIRED = "work_required"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ConversationStorageMigrationReport:
    """One strict counts-only result for migration users and the CLI."""

    operation: ConversationStorageMigrationOperation
    outcome: ConversationStorageMigrationOutcome
    source_backend: ConversationStorageBackend
    destination_backend: ConversationStorageBackend
    dry_run: bool
    parity: ConversationStorageParity
    create: int
    identical: int
    conflict: int
    unsupported: int
    destination_only: int
    source_conversations: int
    source_events: int
    checkpoint: str
    failure: str | None

    def __post_init__(self) -> None:
        if type(self.operation) is not ConversationStorageMigrationOperation:
            raise TypeError("migration report operation is invalid")
        if type(self.outcome) is not ConversationStorageMigrationOutcome:
            raise TypeError("migration report outcome is invalid")
        if type(self.source_backend) is not ConversationStorageBackend:
            raise TypeError("migration report source backend is invalid")
        if type(self.destination_backend) is not ConversationStorageBackend:
            raise TypeError("migration report destination backend is invalid")
        if type(self.dry_run) is not bool:
            raise TypeError("migration report dry_run is invalid")
        if type(self.parity) is not ConversationStorageParity or self.parity not in (
            ConversationStorageParity.VERIFIED,
            ConversationStorageParity.MISMATCH,
            ConversationStorageParity.UNVERIFIED,
        ):
            raise ValueError("migration report parity is invalid")
        for value in (
            self.create,
            self.identical,
            self.conflict,
            self.unsupported,
            self.destination_only,
            self.source_conversations,
            self.source_events,
        ):
            if type(value) is not int or value < 0:
                raise TypeError("migration report counts are invalid")
        if self.checkpoint not in _CHECKPOINT_STATES:
            raise ValueError("migration report checkpoint is invalid")
        if self.outcome is ConversationStorageMigrationOutcome.FAILED:
            if self.failure not in _FAILURE_CODES:
                raise ValueError("migration report failure is invalid")
        elif self.failure is not None:
            raise ValueError("successful migration report failure must be null")

    @classmethod
    def failed(
        cls,
        *,
        operation: ConversationStorageMigrationOperation | str,
        source_backend: ConversationStorageBackend | str,
        destination_backend: ConversationStorageBackend | str,
        dry_run: bool,
        failure: str,
    ) -> ConversationStorageMigrationReport:
        """Build a zero-count content-free report for an adapter entry failure."""

        resolved_operation = (
            operation
            if type(operation) is ConversationStorageMigrationOperation
            else ConversationStorageMigrationOperation(operation)
        )
        return cls(
            operation=resolved_operation,
            outcome=ConversationStorageMigrationOutcome.FAILED,
            source_backend=_coerce_backend(source_backend),
            destination_backend=_coerce_backend(destination_backend),
            dry_run=dry_run,
            parity=ConversationStorageParity.UNVERIFIED,
            create=0,
            identical=0,
            conflict=0,
            unsupported=0,
            destination_only=0,
            source_conversations=0,
            source_events=0,
            checkpoint="none",
            failure=failure,
        )

    @property
    def exit_code(self) -> int:
        """Return the fixed process exit for this complete result."""

        if self.outcome is not ConversationStorageMigrationOutcome.FAILED:
            return 0
        if (
            self.operation is ConversationStorageMigrationOperation.VERIFY
            and self.parity is ConversationStorageParity.MISMATCH
            and self.failure == "parity_mismatch"
            and self.conflict == 0
            and self.unsupported == 0
        ):
            return 1
        return 2

    def to_dict(self) -> dict[str, object]:
        """Return the fixed version-1 JSON-compatible report schema."""

        return {
            "schema_version": CONVERSATION_STORAGE_MIGRATION_SCHEMA_VERSION,
            "operation": self.operation.value,
            "outcome": self.outcome.value,
            "from": self.source_backend.value,
            "to": self.destination_backend.value,
            "dry_run": self.dry_run,
            "parity": self.parity.value,
            "counts": {
                "create": self.create,
                "identical": self.identical,
                "conflict": self.conflict,
                "unsupported": self.unsupported,
                "destination_only": self.destination_only,
            },
            "source": {
                "conversations": self.source_conversations,
                "events": self.source_events,
            },
            "checkpoint": self.checkpoint,
            "failure": self.failure,
        }

    def to_json(self) -> str:
        """Return compact deterministic JSON without a trailing newline."""

        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


@dataclass(slots=True)
class _WorkflowState:
    operation: ConversationStorageMigrationOperation
    source_backend: ConversationStorageBackend
    destination_backend: ConversationStorageBackend
    dry_run: bool
    create: int = 0
    identical: int = 0
    conflict: int = 0
    unsupported: int = 0
    destination_only: int = 0
    source_conversations: int = 0
    source_events: int = 0
    checkpoint: str = "none"
    phase: str = "validation"

    def report(
        self,
        outcome: ConversationStorageMigrationOutcome,
        *,
        parity: ConversationStorageParity,
        failure: str | None = None,
    ) -> ConversationStorageMigrationReport:
        return ConversationStorageMigrationReport(
            operation=self.operation,
            outcome=outcome,
            source_backend=self.source_backend,
            destination_backend=self.destination_backend,
            dry_run=self.dry_run,
            parity=parity,
            create=self.create,
            identical=self.identical,
            conflict=self.conflict,
            unsupported=self.unsupported,
            destination_only=self.destination_only,
            source_conversations=self.source_conversations,
            source_events=self.source_events,
            checkpoint=self.checkpoint,
            failure=failure,
        )


@dataclass(frozen=True, slots=True)
class _MigrationWorkspaceTopology:
    root: Path

    @classmethod
    def capture(cls, workspace: Workspace) -> _MigrationWorkspaceTopology:
        if not isinstance(workspace, Workspace):
            raise TypeError("workspace must be a Workspace")
        root = Path(os.fspath(workspace.root))
        if not root.is_absolute():
            root = Path(os.path.abspath(root))
        return cls(root=root)

    @property
    def workspace_key(self) -> str:
        return os.fspath(self.root)

    def workspace(self) -> Workspace:
        return Workspace(self.root)


@dataclass(frozen=True, slots=True)
class _ArchiveAdapters:
    filesystem: FilesystemConversationArchiveAdapter
    sqlite: SQLiteConversationArchiveAdapter

    def select(
        self,
        backend: ConversationStorageBackend,
    ) -> FilesystemConversationArchiveAdapter | SQLiteConversationArchiveAdapter:
        return (
            self.filesystem
            if backend is ConversationStorageBackend.FILESYSTEM
            else self.sqlite
        )


@dataclass(frozen=True, slots=True)
class _MigrationPlan:
    source_archive: ConversationArchive
    destination_archive: ConversationArchive
    source_records: dict[_Identity, ConversationArchiveConversation]
    destination_records: dict[_Identity, ConversationArchiveConversation]
    create: tuple[_Identity, ...]
    identical: tuple[_Identity, ...]
    conflict: tuple[_Identity, ...]
    unsupported: tuple[_Identity, ...]
    destination_only: tuple[_Identity, ...]


@dataclass(frozen=True, slots=True)
class _Checkpoint:
    direction: str
    source_fingerprint: str
    source_conversations: int
    source_events: int
    completed: tuple[_Identity, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": CONVERSATION_STORAGE_MIGRATION_SCHEMA_VERSION,
            "direction": self.direction,
            "source_fingerprint": self.source_fingerprint,
            "source": {
                "conversations": self.source_conversations,
                "events": self.source_events,
            },
            "progress": {
                "completed_conversations": len(self.completed),
                "total_conversations": self.source_conversations,
            },
            "completed": [
                {"session_id": session_id, "conversation_id": conversation_id}
                for session_id, conversation_id in self.completed
            ],
        }


class _CheckpointInvalidError(Exception):
    """A fixed migration checkpoint cannot be trusted."""


class _CheckpointVersionError(_CheckpointInvalidError):
    """A recognized checkpoint uses an unsupported schema version."""


def migrate_conversation_storage(
    workspace: Workspace,
    *,
    source_backend: ConversationStorageBackend | str,
    destination_backend: ConversationStorageBackend | str,
    dry_run: bool = False,
) -> ConversationStorageMigrationReport:
    """Plan or migrate one activated backend to the other canonical backend."""

    if type(dry_run) is not bool:
        raise TypeError("dry_run must be a boolean")
    return _run_storage_workflow(
        workspace,
        operation=ConversationStorageMigrationOperation.MIGRATE,
        source_backend=_coerce_backend(source_backend),
        destination_backend=_coerce_backend(destination_backend),
        dry_run=dry_run,
    )


def verify_conversation_storage(
    workspace: Workspace,
    *,
    source_backend: ConversationStorageBackend | str,
    destination_backend: ConversationStorageBackend | str,
) -> ConversationStorageMigrationReport:
    """Compare one activated canonical backend with the other without mutation."""

    return _run_storage_workflow(
        workspace,
        operation=ConversationStorageMigrationOperation.VERIFY,
        source_backend=_coerce_backend(source_backend),
        destination_backend=_coerce_backend(destination_backend),
        dry_run=False,
    )


def _run_storage_workflow(
    workspace: Workspace,
    *,
    operation: ConversationStorageMigrationOperation,
    source_backend: ConversationStorageBackend,
    destination_backend: ConversationStorageBackend,
    dry_run: bool,
) -> ConversationStorageMigrationReport:
    state = _WorkflowState(
        operation=operation,
        source_backend=source_backend,
        destination_backend=destination_backend,
        dry_run=dry_run,
    )
    if source_backend is destination_backend:
        return state.report(
            ConversationStorageMigrationOutcome.FAILED,
            parity=ConversationStorageParity.UNVERIFIED,
            failure="invalid_request",
        )

    try:
        topology = _MigrationWorkspaceTopology.capture(workspace)
        operation_workspace = topology.workspace()
        coordinator = WorkspaceConversationStorageMutationCoordinator(
            operation_workspace
        )
        if coordinator.workspace_key != topology.workspace_key:
            raise ConversationStorageCoordinationError()
        with coordinator.borrow(timeout_seconds=_MIGRATION_TIMEOUT_SECONDS) as lease:
            _validate_lease(lease, topology.workspace_key)
            activation = load_conversation_storage_activation_state(operation_workspace)
            if activation.backend is not source_backend:
                return state.report(
                    ConversationStorageMigrationOutcome.FAILED,
                    parity=ConversationStorageParity.UNVERIFIED,
                    failure="activation_mismatch",
                )

            state.phase = (
                "source"
                if source_backend is ConversationStorageBackend.SQLITE
                else "destination"
            )
            adapters = _compose_archive_adapters(operation_workspace)
            source_adapter = adapters.select(source_backend)
            destination_adapter = adapters.select(destination_backend)

            state.phase = "source"
            source_archive = _export_canonical(source_adapter, lease)
            source_wire = serialize_conversation_archive(source_archive)
            state.source_conversations, state.source_events = _archive_counts(
                source_archive
            )
            state.phase = "destination"
            destination_archive = _export_canonical(destination_adapter, lease)
            plan = _build_plan(
                source_archive,
                destination_archive,
                destination_backend=destination_backend,
            )
            state.create = len(plan.create)
            state.identical = len(plan.identical)
            state.conflict = len(plan.conflict)
            state.unsupported = len(plan.unsupported)
            state.destination_only = len(plan.destination_only)

            blocked = _blocked_plan_report(state)
            if blocked is not None:
                return blocked
            if operation is ConversationStorageMigrationOperation.VERIFY:
                return _verification_report(state, plan)

            source_fingerprint = hashlib.sha256(source_wire).hexdigest()
            state.phase = "checkpoint"
            completed, resumed, current_checkpoint = _load_resume_progress(
                operation_workspace,
                lease=lease,
                plan=plan,
                direction=_direction(source_backend, destination_backend),
                source_fingerprint=source_fingerprint,
                source_events=state.source_events,
            )
            if resumed:
                state.checkpoint = "resumed"
            if dry_run:
                return state.report(
                    (
                        ConversationStorageMigrationOutcome.WORK_REQUIRED
                        if plan.create
                        else ConversationStorageMigrationOutcome.COMPLETED
                    ),
                    parity=(
                        ConversationStorageParity.UNVERIFIED
                        if plan.create
                        else ConversationStorageParity.VERIFIED
                    ),
                )

            state.phase = "destination"
            if destination_backend is ConversationStorageBackend.FILESYSTEM:

                def conversation_completed(
                    session_id: str,
                    conversation_id: str,
                ) -> None:
                    nonlocal current_checkpoint
                    identity = (session_id, conversation_id)
                    if identity not in plan.source_records:
                        raise _CheckpointInvalidError()
                    if identity in completed:
                        return
                    next_completed = tuple(sorted((*completed, identity)))
                    checkpoint = _checkpoint_for(
                        direction=_direction(source_backend, destination_backend),
                        source_fingerprint=source_fingerprint,
                        source_conversations=state.source_conversations,
                        source_events=state.source_events,
                        completed=next_completed,
                    )
                    _write_checkpoint(
                        operation_workspace,
                        lease=lease,
                        checkpoint=checkpoint,
                        expected_previous=current_checkpoint,
                    )
                    completed.add(identity)
                    current_checkpoint = checkpoint
                    state.checkpoint = "advanced"

                adapters.filesystem.import_archive(
                    lease,
                    source_archive,
                    on_conversation_completed=conversation_completed,
                )
            else:
                adapters.sqlite.import_archive(lease, source_archive)

            state.phase = "source_final"
            final_source = _export_canonical(source_adapter, lease)
            if serialize_conversation_archive(final_source) != source_wire:
                raise ConversationArchiveSourceChangedError()
            state.phase = "destination_final"
            final_destination = _export_canonical(destination_adapter, lease)
            if serialize_conversation_archive(final_destination) != source_wire:
                return state.report(
                    ConversationStorageMigrationOutcome.FAILED,
                    parity=ConversationStorageParity.MISMATCH,
                    failure="parity_mismatch",
                )

            state.phase = "checkpoint"
            terminal = _checkpoint_for(
                direction=_direction(source_backend, destination_backend),
                source_fingerprint=source_fingerprint,
                source_conversations=state.source_conversations,
                source_events=state.source_events,
                completed=tuple(sorted(plan.source_records)),
            )
            _write_checkpoint(
                operation_workspace,
                lease=lease,
                checkpoint=terminal,
                expected_previous=current_checkpoint,
            )
            state.checkpoint = "completed"
            _validate_lease(lease, topology.workspace_key)
            return state.report(
                ConversationStorageMigrationOutcome.COMPLETED,
                parity=ConversationStorageParity.VERIFIED,
            )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        return _failure_report(state, exc)


def _compose_archive_adapters(workspace: Workspace) -> _ArchiveAdapters:
    return _ArchiveAdapters(
        filesystem=compose_filesystem_conversation_archive(workspace),
        sqlite=compose_sqlite_conversation_archive(workspace),
    )


def _export_canonical(
    adapter: FilesystemConversationArchiveAdapter | SQLiteConversationArchiveAdapter,
    lease: ConversationStorageMutationLease,
) -> ConversationArchive:
    archive = adapter.export_archive(lease)
    return deserialize_conversation_archive(serialize_conversation_archive(archive))


def _build_plan(
    source: ConversationArchive,
    destination: ConversationArchive,
    *,
    destination_backend: ConversationStorageBackend,
) -> _MigrationPlan:
    source_records = _archive_records(source)
    destination_records = _archive_records(destination)
    create: list[_Identity] = []
    identical: list[_Identity] = []
    conflicts: list[_Identity] = []
    unsupported: list[_Identity] = []
    for identity, source_record in source_records.items():
        if (
            destination_backend is ConversationStorageBackend.SQLITE
            and not _sqlite_record_is_representable(source_record)
        ):
            unsupported.append(identity)
            continue
        destination_record = destination_records.get(identity)
        if destination_record is None:
            create.append(identity)
        elif destination_record == source_record:
            identical.append(identity)
        else:
            conflicts.append(identity)
    return _MigrationPlan(
        source_archive=source,
        destination_archive=destination,
        source_records=source_records,
        destination_records=destination_records,
        create=tuple(sorted(create)),
        identical=tuple(sorted(identical)),
        conflict=tuple(sorted(conflicts)),
        unsupported=tuple(sorted(unsupported)),
        destination_only=tuple(sorted(set(destination_records) - set(source_records))),
    )


def _sqlite_record_is_representable(
    conversation: ConversationArchiveConversation,
) -> bool:
    """Report whether SQLite can encode every canonical integer in one record."""

    if (
        type(conversation.subject_revision) is not int
        or conversation.subject_revision < 1
        or conversation.subject_revision > _SQLITE_SIGNED_INTEGER_MAX
    ):
        return False
    return all(
        type(event.sequence) is int
        and 1 <= event.sequence <= _SQLITE_SIGNED_INTEGER_MAX
        for event in conversation.events
    )


def _archive_records(
    archive: ConversationArchive,
) -> dict[_Identity, ConversationArchiveConversation]:
    records: dict[_Identity, ConversationArchiveConversation] = {}
    for session in archive.sessions:
        for conversation in session.conversations:
            identity = (session.session_id, conversation.conversation_id)
            if identity in records or conversation.session_id != session.session_id:
                raise ConversationArchiveFormatError()
            records[identity] = conversation
    return records


def _archive_counts(archive: ConversationArchive) -> tuple[int, int]:
    conversations = 0
    events = 0
    for session in archive.sessions:
        conversations += len(session.conversations)
        events += sum(
            len(conversation.events) for conversation in session.conversations
        )
    if (
        conversations > CONVERSATION_ARCHIVE_MAX_CONVERSATIONS
        or events > CONVERSATION_ARCHIVE_MAX_EVENTS
    ):
        raise ConversationArchiveBoundsError()
    return conversations, events


def _blocked_plan_report(
    state: _WorkflowState,
) -> ConversationStorageMigrationReport | None:
    if state.conflict:
        return state.report(
            ConversationStorageMigrationOutcome.FAILED,
            parity=ConversationStorageParity.UNVERIFIED,
            failure="conflict",
        )
    if state.unsupported:
        return state.report(
            ConversationStorageMigrationOutcome.FAILED,
            parity=ConversationStorageParity.UNVERIFIED,
            failure="unsupported",
        )
    if (
        state.operation is ConversationStorageMigrationOperation.MIGRATE
        and state.destination_only
    ):
        return state.report(
            ConversationStorageMigrationOutcome.FAILED,
            parity=ConversationStorageParity.UNVERIFIED,
            failure="destination_only",
        )
    return None


def _verification_report(
    state: _WorkflowState,
    plan: _MigrationPlan,
) -> ConversationStorageMigrationReport:
    if plan.create or plan.destination_only:
        return state.report(
            ConversationStorageMigrationOutcome.FAILED,
            parity=ConversationStorageParity.MISMATCH,
            failure="parity_mismatch",
        )
    return state.report(
        ConversationStorageMigrationOutcome.COMPLETED,
        parity=ConversationStorageParity.VERIFIED,
    )


def _load_resume_progress(
    workspace: Workspace,
    *,
    lease: ConversationStorageMutationLease,
    plan: _MigrationPlan,
    direction: str,
    source_fingerprint: str,
    source_events: int,
) -> tuple[set[_Identity], bool, _Checkpoint | None]:
    _validate_lease(lease, os.fspath(workspace.root))
    checkpoint = _read_checkpoint(workspace, direction=direction)
    if checkpoint is None:
        return set(), False, None
    if checkpoint.source_fingerprint != source_fingerprint:
        return set(), False, checkpoint
    if (
        checkpoint.source_conversations != len(plan.source_records)
        or checkpoint.source_events != source_events
    ):
        raise _CheckpointInvalidError()
    for identity in checkpoint.completed:
        source_record = plan.source_records.get(identity)
        if (
            source_record is None
            or plan.destination_records.get(identity) != source_record
        ):
            raise _CheckpointInvalidError()
    return set(checkpoint.completed), True, checkpoint


def _checkpoint_for(
    *,
    direction: str,
    source_fingerprint: str,
    source_conversations: int,
    source_events: int,
    completed: tuple[_Identity, ...],
) -> _Checkpoint:
    checkpoint = _Checkpoint(
        direction=direction,
        source_fingerprint=source_fingerprint,
        source_conversations=source_conversations,
        source_events=source_events,
        completed=completed,
    )
    _validate_checkpoint(checkpoint, expected_direction=direction)
    return checkpoint


def _read_checkpoint(
    workspace: Workspace,
    *,
    direction: str,
) -> _Checkpoint | None:
    relative = _checkpoint_relative(direction)
    encoded = _read_checkpoint_bytes(workspace, relative)
    if encoded is None:
        return None
    try:
        text = encoded.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _CheckpointInvalidError() from None
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_fields,
            parse_constant=_reject_constant,
        )
    except (RecursionError, TypeError, ValueError, json.JSONDecodeError):
        raise _CheckpointInvalidError() from None
    return _parse_checkpoint(value, expected_direction=direction)


def _read_checkpoint_bytes(
    workspace: Workspace,
    relative: str,
) -> bytes | None:
    """Read one fixed checkpoint through a bounded no-follow descriptor."""

    descriptor: int | None = None
    try:
        target = workspace.path(relative)
        flags = os.O_RDONLY
        for name in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
            flags |= getattr(os, name, 0)
        try:
            descriptor = os.open(target, flags)
        except FileNotFoundError:
            return None
        os.set_inheritable(descriptor, False)
        opened = os.fstat(descriptor)
        named = os.stat(target, follow_symlinks=False)
        opened_identity = _checkpoint_stat_identity(opened)
        if opened_identity != _checkpoint_stat_identity(named):
            raise _CheckpointInvalidError()
        remaining = _CHECKPOINT_MAX_RAW_BYTES + 1
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        encoded = b"".join(chunks)
        after = os.fstat(descriptor)
        renamed = os.stat(target, follow_symlinks=False)
        if (
            len(encoded) > _CHECKPOINT_MAX_RAW_BYTES
            or len(encoded) != opened.st_size
            or _checkpoint_stat_identity(after) != opened_identity
            or _checkpoint_stat_identity(renamed) != opened_identity
        ):
            raise _CheckpointInvalidError()
        return encoded
    except _CheckpointInvalidError:
        raise
    except Exception:
        raise _CheckpointInvalidError() from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _write_checkpoint(
    workspace: Workspace,
    *,
    lease: ConversationStorageMutationLease,
    checkpoint: _Checkpoint,
    expected_previous: _Checkpoint | None,
) -> None:
    _validate_checkpoint(checkpoint, expected_direction=checkpoint.direction)
    relative = _checkpoint_relative(checkpoint.direction)
    if _read_checkpoint(workspace, direction=checkpoint.direction) != (
        expected_previous
    ):
        raise _CheckpointInvalidError()
    value = checkpoint.to_dict()
    try:
        encoded = (
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise _CheckpointInvalidError() from None
    if len(encoded) > _CHECKPOINT_MAX_RAW_BYTES:
        raise _CheckpointInvalidError()
    _validate_lease(lease, os.fspath(workspace.root))
    if _read_checkpoint(workspace, direction=checkpoint.direction) != (
        expected_previous
    ):
        raise _CheckpointInvalidError()
    try:
        workspace.write_json(relative, value)
    except Exception:
        raise _CheckpointInvalidError() from None
    _validate_lease(lease, os.fspath(workspace.root))
    if _read_checkpoint(workspace, direction=checkpoint.direction) != checkpoint:
        raise _CheckpointInvalidError()


def _checkpoint_metadata(
    workspace: Workspace,
    relative: str,
) -> tuple[int, int, int, int, int] | None:
    try:
        target = workspace.path(relative)
        metadata = os.stat(target, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except Exception:
        raise _CheckpointInvalidError() from None
    return _checkpoint_stat_identity(metadata)


def _checkpoint_stat_identity(
    metadata: os.stat_result,
) -> tuple[int, int, int, int, int]:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or type(metadata.st_size) is not int
        or metadata.st_size < 0
        or metadata.st_size > _CHECKPOINT_MAX_RAW_BYTES
        or type(metadata.st_dev) is not int
        or metadata.st_dev <= 0
        or type(metadata.st_ino) is not int
        or metadata.st_ino <= 0
    ):
        raise _CheckpointInvalidError()
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _parse_checkpoint(value: object, *, expected_direction: str) -> _Checkpoint:
    if type(value) is not dict:
        raise _CheckpointInvalidError()
    version = value.get("schema_version")
    if type(version) is int and (
        version != CONVERSATION_STORAGE_MIGRATION_SCHEMA_VERSION
    ):
        raise _CheckpointVersionError()
    if set(value) != _CHECKPOINT_FIELDS or type(version) is not int:
        raise _CheckpointInvalidError()
    direction = value["direction"]
    fingerprint = value["source_fingerprint"]
    if (
        type(direction) is not str
        or direction != expected_direction
        or type(fingerprint) is not str
        or _FINGERPRINT_PATTERN.fullmatch(fingerprint) is None
    ):
        raise _CheckpointInvalidError()
    source = _exact_object(value["source"], _CHECKPOINT_SOURCE_FIELDS)
    progress = _exact_object(value["progress"], _CHECKPOINT_PROGRESS_FIELDS)
    source_conversations = _bounded_integer(
        source["conversations"],
        maximum=CONVERSATION_ARCHIVE_MAX_CONVERSATIONS,
    )
    source_events = _bounded_integer(
        source["events"],
        maximum=CONVERSATION_ARCHIVE_MAX_EVENTS,
    )
    completed_count = _bounded_integer(
        progress["completed_conversations"],
        maximum=CONVERSATION_ARCHIVE_MAX_CONVERSATIONS,
    )
    total_count = _bounded_integer(
        progress["total_conversations"],
        maximum=CONVERSATION_ARCHIVE_MAX_CONVERSATIONS,
    )
    raw_completed = value["completed"]
    if type(raw_completed) is not list or len(raw_completed) > (
        CONVERSATION_ARCHIVE_MAX_CONVERSATIONS
    ):
        raise _CheckpointInvalidError()
    completed: list[_Identity] = []
    for item in raw_completed:
        identity = _exact_object(item, _CHECKPOINT_IDENTITY_FIELDS)
        session_id = identity["session_id"]
        conversation_id = identity["conversation_id"]
        if type(session_id) is not str or type(conversation_id) is not str:
            raise _CheckpointInvalidError()
        try:
            Workspace.validate_identifier(session_id)
            Workspace.validate_identifier(conversation_id, label="conversation ID")
        except SessionIdError:
            raise _CheckpointInvalidError() from None
        completed.append((session_id, conversation_id))
    canonical_completed = tuple(sorted(completed))
    if (
        tuple(completed) != canonical_completed
        or len(set(completed)) != len(completed)
        or completed_count != len(completed)
        or total_count != source_conversations
        or completed_count > total_count
    ):
        raise _CheckpointInvalidError()
    checkpoint = _Checkpoint(
        direction=direction,
        source_fingerprint=fingerprint,
        source_conversations=source_conversations,
        source_events=source_events,
        completed=canonical_completed,
    )
    _validate_checkpoint(checkpoint, expected_direction=expected_direction)
    return checkpoint


def _validate_checkpoint(
    checkpoint: _Checkpoint,
    *,
    expected_direction: str,
) -> None:
    if type(checkpoint) is not _Checkpoint:
        raise _CheckpointInvalidError()
    if (
        type(checkpoint.direction) is not str
        or checkpoint.direction != expected_direction
        or type(checkpoint.source_fingerprint) is not str
        or _FINGERPRINT_PATTERN.fullmatch(checkpoint.source_fingerprint) is None
        or type(checkpoint.source_conversations) is not int
        or checkpoint.source_conversations < 0
        or checkpoint.source_conversations > CONVERSATION_ARCHIVE_MAX_CONVERSATIONS
        or type(checkpoint.source_events) is not int
        or checkpoint.source_events < 0
        or checkpoint.source_events > CONVERSATION_ARCHIVE_MAX_EVENTS
        or type(checkpoint.completed) is not tuple
        or len(checkpoint.completed) > checkpoint.source_conversations
        or checkpoint.completed != tuple(sorted(checkpoint.completed))
        or len(set(checkpoint.completed)) != len(checkpoint.completed)
    ):
        raise _CheckpointInvalidError()
    for session_id, conversation_id in checkpoint.completed:
        try:
            Workspace.validate_identifier(session_id)
            Workspace.validate_identifier(conversation_id, label="conversation ID")
        except (SessionIdError, TypeError, ValueError):
            raise _CheckpointInvalidError() from None


def _checkpoint_relative(direction: str) -> str:
    if direction not in ("filesystem-to-sqlite", "sqlite-to-filesystem"):
        raise _CheckpointInvalidError()
    return f"{CONVERSATION_STORAGE_MIGRATION_CHECKPOINT_DIRECTORY}/{direction}.json"


def _exact_object(value: object, fields: frozenset[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise _CheckpointInvalidError()
    return value


def _bounded_integer(value: object, *, maximum: int) -> int:
    if type(value) is not int or value < 0 or value > maximum:
        raise _CheckpointInvalidError()
    return value


def _reject_duplicate_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate checkpoint field")
        value[key] = item
    return value


def _reject_constant(_value: str) -> None:
    raise ValueError("invalid checkpoint constant")


def _failure_report(
    state: _WorkflowState,
    failure: Exception,
) -> ConversationStorageMigrationReport:
    parity = ConversationStorageParity.UNVERIFIED
    if isinstance(failure, ConversationArchiveConflictError):
        state.conflict = max(state.conflict, 1)
        code = "conflict"
    elif isinstance(failure, ConversationArchiveVersionError):
        state.unsupported = max(state.unsupported, 1)
        code = "unsupported"
    elif isinstance(failure, ConversationArchiveSourceChangedError):
        code = "source_changed"
    elif isinstance(failure, ConversationArchiveSourceError):
        code = "source_invalid"
    elif isinstance(failure, ConversationArchiveDestinationError):
        code = "destination_invalid"
    elif isinstance(failure, ConversationArchiveError):
        code = (
            "source_invalid"
            if state.phase.startswith("source")
            else ("destination_invalid")
        )
    elif isinstance(failure, _CheckpointVersionError):
        code = "checkpoint_unsupported"
    elif isinstance(failure, _CheckpointInvalidError):
        code = "checkpoint_invalid"
    elif isinstance(failure, ConversationStorageCoordinationError):
        code = "coordination_failed"
    elif isinstance(failure, ConversationStorageProvisioningNeededError):
        code = "provisioning_needed"
    elif isinstance(failure, ConversationStorageUnavailableError):
        code = "backend_unavailable"
    elif isinstance(failure, ConversationStorageActivationVersionError):
        code = "unsupported"
    elif isinstance(failure, ConversationStorageActivationStateError):
        code = "activation_invalid"
    elif isinstance(failure, ConversationStorageConfigurationError):
        code = "invalid_request"
    elif isinstance(failure, WorkspaceSecurityError):
        code = "unsafe_state"
    elif isinstance(failure, WorkspaceVersionUnsupportedError):
        code = "unsupported"
    elif isinstance(failure, WorkspaceFormatError):
        code = (
            "source_invalid"
            if state.phase.startswith("source")
            else ("destination_invalid")
        )
    elif isinstance(failure, ConversationStorageError):
        code = "operational_failure"
    else:
        code = "operational_failure"
    if state.phase == "destination_final" and code == "destination_invalid":
        parity = ConversationStorageParity.MISMATCH
    return state.report(
        ConversationStorageMigrationOutcome.FAILED,
        parity=parity,
        failure=code,
    )


def _coerce_backend(
    value: ConversationStorageBackend | str,
) -> ConversationStorageBackend:
    if type(value) is ConversationStorageBackend:
        return value
    return parse_conversation_storage_backend(value)


def _direction(
    source: ConversationStorageBackend,
    destination: ConversationStorageBackend,
) -> str:
    direction = f"{source.value}-to-{destination.value}"
    if direction not in ("filesystem-to-sqlite", "sqlite-to-filesystem"):
        raise ConversationStorageConfigurationError()
    return direction


def _validate_lease(
    lease: ConversationStorageMutationLease,
    workspace_key: str,
) -> None:
    validator = getattr(lease, "validate", None)
    if not callable(validator):
        raise ConversationStorageCoordinationError()
    try:
        validator(workspace_key=workspace_key)
    except ConversationStorageCoordinationError:
        raise
    except Exception:
        raise ConversationStorageCoordinationError() from None


__all__ = [
    "CONVERSATION_STORAGE_MIGRATION_SCHEMA_VERSION",
    "CONVERSATION_STORAGE_MIGRATION_CHECKPOINT_DIRECTORY",
    "ConversationStorageMigrationOperation",
    "ConversationStorageMigrationOutcome",
    "ConversationStorageMigrationReport",
    "migrate_conversation_storage",
    "verify_conversation_storage",
]
