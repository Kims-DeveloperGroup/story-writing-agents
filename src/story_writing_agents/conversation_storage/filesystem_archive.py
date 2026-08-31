"""Privileged canonical conversation archive transfer for filesystem storage."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..conversations import ConversationEvent, ConversationMetadata
from ..errors import ManuscriptExistsError
from ..workspace.core import Workspace
from .filesystem import (
    FilesystemConversationCatalog,
    FilesystemConversationEventStore,
)
from .archive import (
    CONVERSATION_ARCHIVE_MAX_AGGREGATE_UTF8_BYTES,
    CONVERSATION_ARCHIVE_MAX_CONVERSATIONS,
    CONVERSATION_ARCHIVE_MAX_EVENTS,
    CONVERSATION_ARCHIVE_MAX_SESSIONS,
    CONVERSATION_ARCHIVE_SCHEMA_VERSION,
    ConversationArchive,
    ConversationArchiveBoundsError,
    ConversationArchiveConflictError,
    ConversationArchiveConversation,
    ConversationArchiveDestinationChangedError,
    ConversationArchiveDestinationError,
    ConversationArchiveError,
    ConversationArchiveEvent,
    ConversationArchiveFormatError,
    ConversationArchiveSession,
    ConversationArchiveSourceChangedError,
    ConversationArchiveSourceError,
    deserialize_conversation_archive,
    serialize_conversation_archive,
)
from .contracts import (
    ConversationArchiveImportReceipt,
    ConversationArchiveImportStatus,
    ConversationArchiveSink,
    ConversationArchiveSource,
    ConversationStorageCoordinationError,
    ConversationStorageMutationLease,
)


_DISCUSSION_LOGS = Path("discussion_logs")
_INTERACTIVE = "interactive"
_CONVERSATIONS = "conversations"
_METADATA = "metadata.json"
_TRANSCRIPT = "transcript.jsonl"
_MAX_INVENTORY_ENTRIES = 1_000_000
_MAX_INCLUDED_FILE_BYTES = 67_108_864
_MAX_INCLUDED_TOTAL_BYTES = 134_217_728


@dataclass(frozen=True, slots=True)
class _ArchiveWorkspaceTopology:
    """One construction-pinned root used by every archive operation."""

    root: Path

    @classmethod
    def capture(
        cls,
        workspace: Workspace | str | os.PathLike[str],
    ) -> _ArchiveWorkspaceTopology:
        captured = (
            workspace if isinstance(workspace, Workspace) else Workspace(workspace)
        )
        root = Path(os.fspath(captured.root))
        if not root.is_absolute():
            root = Path(os.path.abspath(root))
        return cls(root=root)

    @property
    def workspace_key(self) -> str:
        return os.fspath(self.root)

    def operation(self) -> _ArchiveOperation:
        workspace = Workspace(self.root)
        catalog = FilesystemConversationCatalog(workspace)
        return _ArchiveOperation(
            workspace=workspace,
            catalog=catalog,
            event_store=FilesystemConversationEventStore(
                workspace,
                catalog=catalog,
            ),
        )


@dataclass(frozen=True, slots=True)
class _ArchiveOperation:
    """Unreachable operation-local public filesystem collaborators."""

    workspace: Workspace
    catalog: FilesystemConversationCatalog
    event_store: FilesystemConversationEventStore


@dataclass(frozen=True, slots=True)
class _StoredConversation:
    value: ConversationArchiveConversation
    transcript_present: bool


@dataclass(frozen=True, slots=True)
class _PlannedFile:
    relative: Path
    text: str
    encoded: bytes


@dataclass(frozen=True, slots=True)
class _InstallPlan:
    value: ConversationArchiveConversation
    metadata: _PlannedFile | None
    transcript: _PlannedFile | None


@dataclass(frozen=True, slots=True)
class _InventorySnapshot:
    records: tuple[
        tuple[tuple[str, str], _StoredConversation],
        ...,
    ]
    paths: frozenset[Path]
    included_sizes: tuple[tuple[Path, int], ...]
    entry_count: int
    included_bytes: int

    def as_dict(self) -> dict[tuple[str, str], _StoredConversation]:
        return dict(self.records)


class _InventoryBudget:
    """Own bounded per-scan filesystem and logical accounting."""

    __slots__ = (
        "_contributing_sessions",
        "_conversations",
        "_entries",
        "_events",
        "_included_bytes",
        "_included_sizes",
        "_logical_remaining",
        "_paths",
    )

    def __init__(self) -> None:
        self._entries = 0
        self._contributing_sessions = 0
        self._conversations = 0
        self._events = 0
        self._included_bytes = 0
        self._included_sizes: dict[Path, int] = {}
        self._logical_remaining = CONVERSATION_ARCHIVE_MAX_AGGREGATE_UTF8_BYTES
        self._paths: set[Path] = set()

    def charge_entry(self, relative: Path) -> None:
        entries = self._entries + 1
        if entries > _MAX_INVENTORY_ENTRIES:
            raise ConversationArchiveBoundsError()
        self._entries = entries
        self._paths.add(relative)

    def charge_conversation(self) -> None:
        conversations = self._conversations + 1
        if conversations > CONVERSATION_ARCHIVE_MAX_CONVERSATIONS:
            raise ConversationArchiveBoundsError()
        self._conversations = conversations

    def charge_contributing_session(self) -> None:
        sessions = self._contributing_sessions + 1
        if sessions > CONVERSATION_ARCHIVE_MAX_SESSIONS:
            raise ConversationArchiveBoundsError()
        self._contributing_sessions = sessions

    def charge_events(self, count: int) -> None:
        events = self._events + count
        if events > CONVERSATION_ARCHIVE_MAX_EVENTS:
            raise ConversationArchiveBoundsError()
        self._events = events

    def charge_included_file(self, relative: Path, size: int) -> None:
        previous = self._included_sizes.get(relative, 0)
        retained_size = max(previous, size)
        included_bytes = self._included_bytes - previous + retained_size
        if included_bytes > _MAX_INCLUDED_TOTAL_BYTES:
            raise ConversationArchiveBoundsError()
        self._included_sizes[relative] = retained_size
        self._included_bytes = included_bytes

    def consume_text(self, value: object) -> None:
        if type(value) is not str:
            raise ConversationArchiveFormatError()
        try:
            size = len(value.encode("utf-8", errors="strict"))
        except UnicodeEncodeError:
            raise ConversationArchiveFormatError() from None
        if size > self._logical_remaining:
            raise ConversationArchiveBoundsError()
        self._logical_remaining -= size

    def snapshot(
        self,
        records: Mapping[tuple[str, str], _StoredConversation],
    ) -> _InventorySnapshot:
        return _InventorySnapshot(
            records=tuple(sorted(records.items())),
            paths=frozenset(self._paths),
            included_sizes=tuple(sorted(self._included_sizes.items())),
            entry_count=self._entries,
            included_bytes=self._included_bytes,
        )


class FilesystemConversationArchiveAdapter(
    ConversationArchiveSource[ConversationArchive],
    ConversationArchiveSink[ConversationArchive],
):
    """Export or import exact filesystem conversations under a caller lease.

    Export requires the caller to keep the filesystem conversation source
    stable and quiesced for the operation. Two complete identical scans detect
    ordinary source changes but do not make concurrent ordinary writers
    linearizable. Import validates the complete destination before creating any
    file, never overwrites or deletes, and may leave only an exact resumable
    metadata-first prefix after an operational failure.
    """

    __slots__ = ("_topology",)
    _topology: _ArchiveWorkspaceTopology

    def __init__(
        self,
        workspace: Workspace | str | os.PathLike[str],
    ) -> None:
        object.__setattr__(
            self,
            "_topology",
            _ArchiveWorkspaceTopology.capture(workspace),
        )

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("archive adapter topology is immutable")

    def __delattr__(self, _name: str) -> None:
        raise AttributeError("archive adapter topology is immutable")

    def export_archive(
        self,
        lease: ConversationStorageMutationLease,
    ) -> ConversationArchive:
        """Return one stable double-scanned authoritative filesystem archive."""

        def validate_lease() -> None:
            self._validate_lease(lease)

        validate_lease()
        try:
            operation = self._topology.operation()
        except Exception:
            raise ConversationArchiveSourceError() from None
        first = self._scan_source(operation, validate_lease)
        first_wire = serialize_conversation_archive(first)
        validate_lease()
        try:
            second = self._scan_source(operation, validate_lease)
            second_wire = serialize_conversation_archive(second)
        except ConversationStorageCoordinationError:
            raise
        except ConversationArchiveError:
            raise ConversationArchiveSourceChangedError() from None
        validate_lease()
        if first_wire != second_wire:
            raise ConversationArchiveSourceChangedError()
        validate_lease()
        return first

    def import_archive(
        self,
        lease: ConversationStorageMutationLease,
        payload: ConversationArchive,
        *,
        on_conversation_completed: Callable[[str, str], None] | None = None,
    ) -> ConversationArchiveImportReceipt:
        """Create missing exact files or accept an identical destination."""

        if on_conversation_completed is not None and not callable(
            on_conversation_completed
        ):
            raise TypeError("conversation completion callback must be callable")
        canonical = deserialize_conversation_archive(
            serialize_conversation_archive(payload)
        )

        def validate_lease() -> None:
            self._validate_lease(lease)

        resumable_identities = frozenset(
            (session.session_id, conversation.conversation_id)
            for session in canonical.sessions
            for conversation in session.conversations
        )
        validate_lease()
        try:
            operation = self._topology.operation()
        except Exception:
            raise ConversationArchiveDestinationError() from None
        before = self._scan_destination(
            operation,
            validate_lease,
            resumable_identities=resumable_identities,
        )
        plans = self._classify_import(canonical, before.as_dict())
        expected_after = self._preflight_import(
            operation,
            validate_lease=validate_lease,
            canonical=canonical,
            before=before,
            plans=plans,
        )

        created = False
        for plan in plans:
            if plan.metadata is not None:
                created = (
                    self._install_metadata(
                        operation,
                        plan,
                        validate_lease=validate_lease,
                    )
                    or created
                )
            if plan.transcript is not None:
                created = (
                    self._install_transcript(
                        operation,
                        plan,
                        validate_lease=validate_lease,
                    )
                    or created
                )
            if on_conversation_completed is not None:
                self._validate_completed_plan(
                    operation,
                    plan,
                    validate_lease=validate_lease,
                )
                validate_lease()
                on_conversation_completed(
                    plan.value.session_id,
                    plan.value.conversation_id,
                )
                validate_lease()

        validate_lease()
        try:
            after = self._scan_destination(
                operation,
                validate_lease,
                resumable_identities=resumable_identities,
            )
        except ConversationStorageCoordinationError:
            raise
        except ConversationArchiveError:
            raise ConversationArchiveDestinationChangedError() from None
        if after.records != tuple(sorted(expected_after.items())):
            raise ConversationArchiveDestinationChangedError()
        validate_lease()
        return ConversationArchiveImportReceipt(
            status=(
                ConversationArchiveImportStatus.IMPORTED
                if created
                else ConversationArchiveImportStatus.UNCHANGED
            )
        )

    def _validate_completed_plan(
        self,
        operation: _ArchiveOperation,
        plan: _InstallPlan,
        *,
        validate_lease: Callable[[], None],
    ) -> None:
        """Require one durable exact conversation before progress publication."""

        validate_lease()
        if not self._metadata_collision_is_exact(
            operation,
            plan.value,
            validate_lease=validate_lease,
        ):
            raise ConversationArchiveDestinationChangedError()
        if plan.value.events:
            if not self._transcript_collision_is_exact(
                operation,
                plan.value,
                validate_lease=validate_lease,
            ):
                raise ConversationArchiveDestinationChangedError()
        else:
            try:
                transcript = operation.workspace.path(
                    _transcript_relative(
                        plan.value.session_id,
                        plan.value.conversation_id,
                    )
                )
                if os.path.lexists(transcript):
                    raise ConversationArchiveDestinationChangedError()
            except ConversationArchiveError:
                raise
            except Exception:
                raise ConversationArchiveDestinationChangedError() from None
        validate_lease()

    def _validate_lease(self, lease: ConversationStorageMutationLease) -> None:
        validator = getattr(lease, "validate", None)
        if not callable(validator):
            raise ConversationStorageCoordinationError()
        try:
            validator(workspace_key=self._topology.workspace_key)
        except ConversationStorageCoordinationError:
            raise
        except Exception:
            raise ConversationStorageCoordinationError() from None

    def _scan_source(
        self,
        operation: _ArchiveOperation,
        validate_lease: Callable[[], None],
    ) -> ConversationArchive:
        try:
            inventory = self._scan_inventory(
                operation,
                validate_lease=validate_lease,
                allow_missing_root=False,
            )
            return self._archive_from_inventory(inventory.as_dict())
        except ConversationStorageCoordinationError:
            raise
        except ConversationArchiveBoundsError:
            raise
        except Exception:
            raise ConversationArchiveSourceError() from None

    def _scan_destination(
        self,
        operation: _ArchiveOperation,
        validate_lease: Callable[[], None],
        *,
        resumable_identities: frozenset[tuple[str, str]],
    ) -> _InventorySnapshot:
        try:
            return self._scan_inventory(
                operation,
                validate_lease=validate_lease,
                allow_missing_root=True,
                resumable_identities=resumable_identities,
            )
        except ConversationStorageCoordinationError:
            raise
        except ConversationArchiveBoundsError:
            raise
        except Exception:
            raise ConversationArchiveDestinationError() from None

    def _scan_inventory(
        self,
        operation: _ArchiveOperation,
        *,
        validate_lease: Callable[[], None],
        allow_missing_root: bool,
        resumable_identities: frozenset[tuple[str, str]] = frozenset(),
    ) -> _InventorySnapshot:
        budget = _InventoryBudget()
        validate_lease()
        root_entries = self._directory_entries(
            operation,
            budget,
            validate_lease,
            _DISCUSSION_LOGS,
            allow_missing=allow_missing_root,
        )
        if root_entries is None:
            return budget.snapshot({})
        if ".gitkeep" in root_entries:
            validate_lease()
            self._require_empty_sentinel(
                operation,
                _DISCUSSION_LOGS / ".gitkeep",
            )
            validate_lease()

        session_ids: list[str] = []
        for name, metadata in sorted(root_entries.items()):
            if name == ".gitkeep":
                continue
            if not stat.S_ISDIR(metadata.st_mode):
                self._require_excluded_regular(metadata)
                continue
            session = operation.workspace.validate_identifier(name)
            self._require_directory(operation, _DISCUSSION_LOGS / session)
            session_ids.append(session)

        inventory: dict[tuple[str, str], _StoredConversation] = {}
        resumable_sessions = frozenset(
            session_id for session_id, _conversation_id in resumable_identities
        )
        for session_id in sorted(session_ids):
            validate_lease()
            session_is_resumable = session_id in resumable_sessions
            session_relative = _DISCUSSION_LOGS / session_id
            session_entries = self._directory_entries(
                operation,
                budget,
                validate_lease,
                session_relative,
            )
            if session_entries is None:
                raise ValueError("missing conversation session")
            for name, metadata in sorted(session_entries.items()):
                if name == _INTERACTIVE:
                    if not stat.S_ISDIR(metadata.st_mode):
                        raise ValueError("invalid interactive conversation layout")
                    continue
                self._require_excluded_regular(metadata)
            if _INTERACTIVE not in session_entries:
                continue
            interactive_relative = session_relative / _INTERACTIVE
            self._require_directory(operation, interactive_relative)
            interactive_entries = self._directory_entries(
                operation,
                budget,
                validate_lease,
                interactive_relative,
            )
            if session_is_resumable and not interactive_entries:
                continue
            if interactive_entries is None or set(interactive_entries) != {
                _CONVERSATIONS
            }:
                raise ValueError("invalid interactive conversation layout")
            catalog_relative = interactive_relative / _CONVERSATIONS
            self._require_directory(operation, catalog_relative)
            catalog_entries = self._directory_entries(
                operation,
                budget,
                validate_lease,
                catalog_relative,
            )
            if session_is_resumable and not catalog_entries:
                continue
            if not catalog_entries:
                raise ValueError("empty conversation session layout")

            conversation_ids: list[str] = []
            transcript_presence: dict[str, bool] = {}
            for name in sorted(catalog_entries):
                budget.charge_conversation()
                conversation_id = operation.workspace.validate_identifier(
                    name,
                    label="conversation ID",
                )
                conversation_relative = catalog_relative / conversation_id
                self._require_directory(operation, conversation_relative)
                entries = self._directory_entries(
                    operation,
                    budget,
                    validate_lease,
                    conversation_relative,
                )
                if (
                    not entries
                    and (session_id, conversation_id) in resumable_identities
                ):
                    continue
                if (
                    entries is None
                    or _METADATA not in entries
                    or not set(entries).issubset({_METADATA, _TRANSCRIPT})
                ):
                    raise ValueError("invalid conversation artifact layout")
                transcript_present = _TRANSCRIPT in entries
                conversation_ids.append(conversation_id)
                transcript_presence[conversation_id] = transcript_present

            if conversation_ids:
                budget.charge_contributing_session()
            for conversation_id in sorted(conversation_ids):
                validate_lease()
                metadata_relative = _metadata_relative(session_id, conversation_id)
                transcript_relative = _transcript_relative(
                    session_id,
                    conversation_id,
                )
                self._require_included_file(
                    operation,
                    budget,
                    validate_lease,
                    metadata_relative,
                )
                stored_metadata = operation.catalog.get(
                    session_id,
                    conversation_id,
                )
                if (
                    stored_metadata.session_id != session_id
                    or stored_metadata.conversation_id != conversation_id
                ):
                    raise ValueError("conversation catalog identity mismatch")

                present = transcript_presence[conversation_id]
                self._require_included_file(
                    operation,
                    budget,
                    validate_lease,
                    metadata_relative,
                )
                if present:
                    self._require_included_file(
                        operation,
                        budget,
                        validate_lease,
                        transcript_relative,
                    )
                events = operation.event_store.read(session_id, conversation_id)
                if type(events) is not tuple:
                    raise ConversationArchiveFormatError()
                budget.charge_events(len(events))
                if any(type(event) is not ConversationEvent for event in events):
                    raise ConversationArchiveFormatError()
                if present and not events:
                    raise ValueError("empty transcript artifact")
                budget.consume_text(stored_metadata.subject)
                for event in events:
                    budget.consume_text(event.content)
                value = self._archive_conversation(
                    stored_metadata,
                    events,
                )
                inventory[(session_id, conversation_id)] = _StoredConversation(
                    value=value,
                    transcript_present=present,
                )
        validate_lease()
        return budget.snapshot(inventory)

    def _directory_entries(
        self,
        operation: _ArchiveOperation,
        budget: _InventoryBudget,
        validate_lease: Callable[[], None],
        relative: Path,
        *,
        allow_missing: bool = False,
    ) -> dict[str, os.stat_result] | None:
        validate_lease()
        target = operation.workspace.path(relative)
        try:
            metadata = os.stat(target, follow_symlinks=False)
        except FileNotFoundError:
            if allow_missing:
                return None
            raise
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("expected directory")
        entries: dict[str, os.stat_result] = {}
        with os.scandir(target) as iterator:
            for entry in iterator:
                validate_lease()
                child_relative = relative / entry.name
                budget.charge_entry(child_relative)
                child = operation.workspace.path(child_relative)
                entries[entry.name] = os.stat(child, follow_symlinks=False)
        validate_lease()
        return entries

    @staticmethod
    def _require_directory(operation: _ArchiveOperation, relative: Path) -> None:
        target = operation.workspace.path(relative)
        metadata = os.stat(target, follow_symlinks=False)
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("expected directory")

    @staticmethod
    def _bounded_regular_size(
        operation: _ArchiveOperation,
        relative: Path,
    ) -> int:
        target = operation.workspace.path(relative)
        metadata = os.stat(target, follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("expected singly linked regular file")
        if metadata.st_size < 0:
            raise ValueError("invalid regular file size")
        if metadata.st_size > _MAX_INCLUDED_FILE_BYTES:
            raise ConversationArchiveBoundsError()
        return metadata.st_size

    def _require_included_file(
        self,
        operation: _ArchiveOperation,
        budget: _InventoryBudget,
        validate_lease: Callable[[], None],
        relative: Path,
    ) -> None:
        validate_lease()
        size = self._bounded_regular_size(operation, relative)
        budget.charge_included_file(relative, size)
        validate_lease()

    @staticmethod
    def _require_empty_sentinel(
        operation: _ArchiveOperation,
        relative: Path,
    ) -> None:
        target = operation.workspace.path(relative)
        metadata = os.stat(target, follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size != 0
        ):
            raise ValueError("invalid discussion log sentinel")

    @staticmethod
    def _require_excluded_regular(metadata: os.stat_result) -> None:
        """Fail closed unless one excluded entry is a safe regular file."""

        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("unsafe excluded conversation artifact")

    @staticmethod
    def _archive_conversation(
        metadata: ConversationMetadata,
        events: tuple[ConversationEvent, ...],
    ) -> ConversationArchiveConversation:
        if type(metadata) is not ConversationMetadata or type(events) is not tuple:
            raise ConversationArchiveFormatError()
        return ConversationArchiveConversation(
            schema_version=metadata.schema_version,
            session_id=metadata.session_id,
            conversation_id=metadata.conversation_id,
            subject=metadata.subject,
            subject_revision=metadata.subject_revision,
            status=metadata.status,
            created_at=metadata.created_at,
            updated_at=metadata.updated_at,
            events=tuple(
                ConversationArchiveEvent(
                    schema_version=event.schema_version,
                    session_id=event.session_id,
                    conversation_id=event.conversation_id,
                    turn_id=event.turn_id,
                    sequence=event.sequence,
                    timestamp=event.timestamp,
                    sender=event.sender,
                    recipient=event.recipient,
                    kind=event.kind,
                    content=event.content,
                )
                for event in events
            ),
        )

    @staticmethod
    def _archive_from_inventory(
        inventory: Mapping[tuple[str, str], _StoredConversation],
    ) -> ConversationArchive:
        grouped: dict[str, list[ConversationArchiveConversation]] = {}
        for (session_id, _conversation_id), stored in inventory.items():
            grouped.setdefault(session_id, []).append(stored.value)
        return ConversationArchive(
            schema_version=CONVERSATION_ARCHIVE_SCHEMA_VERSION,
            sessions=tuple(
                ConversationArchiveSession(
                    session_id=session_id,
                    conversations=tuple(grouped[session_id]),
                )
                for session_id in sorted(grouped)
            ),
        )

    @staticmethod
    def _classify_import(
        archive: ConversationArchive,
        inventory: Mapping[tuple[str, str], _StoredConversation],
    ) -> tuple[_InstallPlan, ...]:
        plans: list[_InstallPlan] = []
        for session in archive.sessions:
            for conversation in session.conversations:
                stored = inventory.get(
                    (session.session_id, conversation.conversation_id)
                )
                if stored is None:
                    plans.append(
                        _InstallPlan(
                            value=conversation,
                            metadata=_planned_file(
                                _metadata_relative(
                                    conversation.session_id,
                                    conversation.conversation_id,
                                ),
                                _metadata_text(conversation),
                            ),
                            transcript=(
                                _planned_file(
                                    _transcript_relative(
                                        conversation.session_id,
                                        conversation.conversation_id,
                                    ),
                                    _transcript_text(conversation),
                                )
                                if conversation.events
                                else None
                            ),
                        )
                    )
                    continue
                if not _metadata_equal(stored.value, conversation):
                    raise ConversationArchiveConflictError()
                if stored.transcript_present:
                    if stored.value.events != conversation.events:
                        raise ConversationArchiveConflictError()
                    plans.append(_InstallPlan(conversation, None, None))
                    continue
                if stored.value.events:
                    raise ConversationArchiveDestinationError()
                plans.append(
                    _InstallPlan(
                        value=conversation,
                        metadata=None,
                        transcript=(
                            _planned_file(
                                _transcript_relative(
                                    conversation.session_id,
                                    conversation.conversation_id,
                                ),
                                _transcript_text(conversation),
                            )
                            if conversation.events
                            else None
                        ),
                    )
                )
        return tuple(plans)

    def _preflight_import(
        self,
        operation: _ArchiveOperation,
        *,
        validate_lease: Callable[[], None],
        canonical: ConversationArchive,
        before: _InventorySnapshot,
        plans: tuple[_InstallPlan, ...],
    ) -> dict[tuple[str, str], _StoredConversation]:
        expected_after = before.as_dict()
        for session in canonical.sessions:
            for conversation in session.conversations:
                expected_after[(session.session_id, conversation.conversation_id)] = (
                    _StoredConversation(
                        value=conversation,
                        transcript_present=bool(conversation.events),
                    )
                )

        planned_files = tuple(
            planned
            for plan in plans
            for planned in (plan.metadata, plan.transcript)
            if planned is not None
        )
        missing_paths: set[Path] = set()
        projected_included_bytes = before.included_bytes
        for planned in planned_files:
            projected_included_bytes += len(planned.encoded)
            if projected_included_bytes > _MAX_INCLUDED_TOTAL_BYTES:
                raise ConversationArchiveBoundsError()
            for relative in _archive_descendants(planned.relative):
                if relative not in before.paths:
                    missing_paths.add(relative)
        if before.entry_count + len(missing_paths) > _MAX_INVENTORY_ENTRIES:
            raise ConversationArchiveBoundsError()

        expected_archive = self._archive_from_inventory(expected_after)
        serialize_conversation_archive(expected_archive)

        for planned in planned_files:
            validate_lease()
            try:
                self._validate_install_path(
                    operation,
                    planned.relative,
                    validate_lease=validate_lease,
                )
            except ConversationStorageCoordinationError:
                raise
            except ConversationArchiveError:
                raise
            except Exception:
                raise ConversationArchiveDestinationChangedError() from None
        validate_lease()
        return expected_after

    def _install_metadata(
        self,
        operation: _ArchiveOperation,
        plan: _InstallPlan,
        *,
        validate_lease: Callable[[], None],
    ) -> bool:
        planned = plan.metadata
        if planned is None:
            raise ConversationArchiveDestinationError()
        validate_lease()
        try:
            self._validate_install_path(
                operation,
                planned.relative,
                validate_lease=validate_lease,
            )
        except ConversationStorageCoordinationError:
            raise
        except ConversationArchiveError:
            raise
        except Exception:
            raise ConversationArchiveDestinationChangedError() from None
        try:
            operation.workspace.write_text(
                planned.relative,
                planned.text,
                overwrite=False,
            )
        except ManuscriptExistsError:
            if self._metadata_collision_is_exact(
                operation,
                plan.value,
                validate_lease=validate_lease,
            ):
                validate_lease()
                return False
            raise ConversationArchiveDestinationChangedError() from None
        except Exception:
            if self._target_exists_or_is_unsafe(operation, planned.relative):
                raise ConversationArchiveDestinationChangedError() from None
            raise ConversationArchiveDestinationError() from None
        validate_lease()
        return True

    def _install_transcript(
        self,
        operation: _ArchiveOperation,
        plan: _InstallPlan,
        *,
        validate_lease: Callable[[], None],
    ) -> bool:
        planned = plan.transcript
        if planned is None or not plan.value.events:
            raise ConversationArchiveDestinationError()
        validate_lease()
        try:
            self._validate_install_path(
                operation,
                planned.relative,
                validate_lease=validate_lease,
            )
        except ConversationStorageCoordinationError:
            raise
        except ConversationArchiveError:
            raise
        except Exception:
            raise ConversationArchiveDestinationChangedError() from None
        try:
            operation.workspace.write_text(
                planned.relative,
                planned.text,
                overwrite=False,
            )
        except ManuscriptExistsError:
            if self._transcript_collision_is_exact(
                operation,
                plan.value,
                validate_lease=validate_lease,
            ):
                validate_lease()
                return False
            raise ConversationArchiveDestinationChangedError() from None
        except Exception:
            if self._target_exists_or_is_unsafe(operation, planned.relative):
                raise ConversationArchiveDestinationChangedError() from None
            raise ConversationArchiveDestinationError() from None
        validate_lease()
        return True

    @staticmethod
    def _validate_install_path(
        operation: _ArchiveOperation,
        relative: Path,
        *,
        validate_lease: Callable[[], None],
    ) -> None:
        validate_lease()
        target = operation.workspace.path(relative)
        parents = tuple(reversed(relative.parents))
        for parent_relative in parents:
            if parent_relative == Path("."):
                continue
            validate_lease()
            parent = operation.workspace.path(parent_relative)
            try:
                metadata = os.stat(parent, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(metadata.st_mode):
                raise ConversationArchiveDestinationChangedError()
        validate_lease()
        try:
            metadata = os.stat(target, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ConversationArchiveDestinationChangedError()

    @staticmethod
    def _target_exists_or_is_unsafe(
        operation: _ArchiveOperation,
        relative: Path,
    ) -> bool:
        try:
            target = operation.workspace.path(relative)
            return os.path.lexists(target)
        except Exception:
            return True

    def _metadata_collision_is_exact(
        self,
        operation: _ArchiveOperation,
        conversation: ConversationArchiveConversation,
        *,
        validate_lease: Callable[[], None],
    ) -> bool:
        try:
            validate_lease()
            self._bounded_regular_size(
                operation,
                _metadata_relative(
                    conversation.session_id,
                    conversation.conversation_id,
                ),
            )
            metadata = operation.catalog.get(
                conversation.session_id,
                conversation.conversation_id,
            )
            validate_lease()
            return _metadata_matches(metadata, conversation)
        except ConversationStorageCoordinationError:
            raise
        except Exception:
            return False

    def _transcript_collision_is_exact(
        self,
        operation: _ArchiveOperation,
        conversation: ConversationArchiveConversation,
        *,
        validate_lease: Callable[[], None],
    ) -> bool:
        try:
            validate_lease()
            self._bounded_regular_size(
                operation,
                _metadata_relative(
                    conversation.session_id,
                    conversation.conversation_id,
                ),
            )
            self._bounded_regular_size(
                operation,
                _transcript_relative(
                    conversation.session_id,
                    conversation.conversation_id,
                ),
            )
            events = operation.event_store.read(
                conversation.session_id,
                conversation.conversation_id,
            )
            if (
                type(events) is not tuple
                or len(events) > CONVERSATION_ARCHIVE_MAX_EVENTS
            ):
                return False
            if len(events) != len(conversation.events):
                return False
            exact = all(
                type(event) is ConversationEvent
                and self._archive_event(event) == expected
                for event, expected in zip(events, conversation.events, strict=True)
            )
            validate_lease()
            return exact
        except ConversationStorageCoordinationError:
            raise
        except Exception:
            return False

    @staticmethod
    def _archive_event(event: ConversationEvent) -> ConversationArchiveEvent:
        return ConversationArchiveEvent(
            schema_version=event.schema_version,
            session_id=event.session_id,
            conversation_id=event.conversation_id,
            turn_id=event.turn_id,
            sequence=event.sequence,
            timestamp=event.timestamp,
            sender=event.sender,
            recipient=event.recipient,
            kind=event.kind,
            content=event.content,
        )


def _metadata_relative(session_id: str, conversation_id: str) -> Path:
    return (
        _DISCUSSION_LOGS
        / session_id
        / _INTERACTIVE
        / _CONVERSATIONS
        / conversation_id
        / _METADATA
    )


def _transcript_relative(session_id: str, conversation_id: str) -> Path:
    return _metadata_relative(session_id, conversation_id).with_name(_TRANSCRIPT)


def _metadata_equal(
    left: ConversationArchiveConversation,
    right: ConversationArchiveConversation,
) -> bool:
    return (
        left.schema_version,
        left.session_id,
        left.conversation_id,
        left.subject,
        left.subject_revision,
        left.status,
        left.created_at,
        left.updated_at,
    ) == (
        right.schema_version,
        right.session_id,
        right.conversation_id,
        right.subject,
        right.subject_revision,
        right.status,
        right.created_at,
        right.updated_at,
    )


def _metadata_matches(
    metadata: ConversationMetadata,
    conversation: ConversationArchiveConversation,
) -> bool:
    return metadata == _domain_metadata(conversation)


def _domain_metadata(
    conversation: ConversationArchiveConversation,
) -> ConversationMetadata:
    return ConversationMetadata(
        schema_version=conversation.schema_version,
        session_id=conversation.session_id,
        conversation_id=conversation.conversation_id,
        subject=conversation.subject,
        subject_revision=conversation.subject_revision,
        status=conversation.status,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _metadata_text(conversation: ConversationArchiveConversation) -> str:
    try:
        return (
            json.dumps(
                _domain_metadata(conversation).to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    except (TypeError, ValueError):
        raise ConversationArchiveFormatError() from None


def _transcript_text(conversation: ConversationArchiveConversation) -> str:
    return "".join(
        json.dumps(
            ConversationEvent(
                schema_version=event.schema_version,
                session_id=event.session_id,
                conversation_id=event.conversation_id,
                turn_id=event.turn_id,
                sequence=event.sequence,
                timestamp=event.timestamp,
                sender=event.sender,
                recipient=event.recipient,
                kind=event.kind,
                content=event.content,
            ).to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
        for event in conversation.events
    )


def _planned_file(relative: Path, text: str) -> _PlannedFile:
    try:
        encoded = text.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise ConversationArchiveFormatError() from None
    if len(encoded) > _MAX_INCLUDED_FILE_BYTES:
        raise ConversationArchiveBoundsError()
    return _PlannedFile(relative=relative, text=text, encoded=encoded)


def _archive_descendants(relative: Path) -> tuple[Path, ...]:
    if not relative.parts or relative.parts[0] != _DISCUSSION_LOGS.name:
        raise ConversationArchiveDestinationError()
    current = _DISCUSSION_LOGS
    descendants: list[Path] = []
    for part in relative.parts[1:]:
        current /= part
        descendants.append(current)
    return tuple(descendants)


def compose_filesystem_conversation_archive(
    workspace: Workspace | str | os.PathLike[str],
    /,
) -> FilesystemConversationArchiveAdapter:
    """Compose the explicit filesystem archive adapter without scanning it."""

    return FilesystemConversationArchiveAdapter(workspace)


__all__ = [
    "FilesystemConversationArchiveAdapter",
    "compose_filesystem_conversation_archive",
]
