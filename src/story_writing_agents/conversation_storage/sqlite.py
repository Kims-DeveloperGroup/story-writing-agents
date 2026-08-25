"""Optional SQLite conversation catalog, event store, and archive adapter."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Self, TypeVar
from uuid import uuid4

from ..conversation_search import ReferenceConversationSearch
from ..conversations import (
    CONVERSATION_EVENT_SCHEMA_VERSION,
    CONVERSATION_METADATA_SCHEMA_VERSION,
    ConversationContextWindow,
    ConversationEvent,
    ConversationEventAppend,
    ConversationEventKind,
    ConversationMetadata,
    ConversationStatus,
    normalize_conversation_subject,
    select_bounded_conversation_context,
)
from ..errors import (
    ConversationExistsError,
    ConversationNotFoundError,
    SessionIdError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
    WorkspaceVersionUnsupportedError,
)
from ..workspace import Workspace
from .sqlite_database import (
    SQLITE_APPLICATION_ID,
    SQLITE_BUSY_TIMEOUT_SECONDS,
    SQLITE_DATABASE_PATH,
    SQLITE_MINIMUM_VERSION,
    SQLITE_SCHEMA_VERSION,
    SQLiteDatabase,
    SQLiteDatabaseFormatError,
    SQLiteDatabaseMissingError,
    SQLiteDatabaseSecurityError,
    SQLiteDatabaseUnavailableError,
    SQLiteDatabaseVersionError,
)
from .composition import WorkspaceConversationStorageMutationCoordinator
from .contracts import (
    ConversationArchiveImportReceipt,
    ConversationArchiveImportStatus,
    ConversationStorageBackend,
    ConversationStorageCapability,
    ConversationStorageComponentBinding,
    ConversationStorageCoordinationError,
    ConversationStorageMutationCoordinator,
    ConversationStorageMutationLease,
    ConversationStoragePreflight,
    ConversationStorageProvenance,
    ConversationStorageServices,
    ConversationStorageUnavailableError,
    validate_conversation_storage_mutation_timeout,
)


SQLITE_CONVERSATION_STORAGE_APPLICATION_ID = SQLITE_APPLICATION_ID
SQLITE_CONVERSATION_STORAGE_BUSY_TIMEOUT_SECONDS = SQLITE_BUSY_TIMEOUT_SECONDS
SQLITE_CONVERSATION_STORAGE_DATABASE_PATH = SQLITE_DATABASE_PATH
SQLITE_CONVERSATION_STORAGE_MINIMUM_VERSION = SQLITE_MINIMUM_VERSION
SQLITE_CONVERSATION_STORAGE_SCHEMA_VERSION = SQLITE_SCHEMA_VERSION

_CONVERSATION_ID_ALLOCATION_ATTEMPTS = 16
_SQLITE_SIGNED_INTEGER_MAX = 9_223_372_036_854_775_807

_METADATA_SELECT = (
    "SELECT schema_version, session_id, conversation_id, subject, "
    "normalized_subject, subject_revision, status, created_at, updated_at "
    "FROM conversations"
)
_EVENT_SELECT = (
    "SELECT schema_version, session_id, conversation_id, turn_id, sequence, "
    "timestamp, sender, recipient, kind, content, turn_slot "
    "FROM conversation_events"
)

ResultT = TypeVar("ResultT")


@dataclass(frozen=True, slots=True)
class _SQLiteConversationArchiveRecord:
    """One exact authoritative metadata record and its ordered transcript."""

    metadata: ConversationMetadata
    events: tuple[ConversationEvent, ...]

    def __post_init__(self) -> None:
        if type(self.metadata) is not ConversationMetadata:
            raise TypeError("archive metadata must be ConversationMetadata")
        if type(self.events) is not tuple or any(
            type(event) is not ConversationEvent for event in self.events
        ):
            raise TypeError("archive events must be an exact event tuple")
        _validate_event_collection(
            self.events,
            session_id=self.metadata.session_id,
            conversation_id=self.metadata.conversation_id,
        )


@dataclass(frozen=True, slots=True)
class _SQLiteConversationArchivePayload:
    """Private deterministic SQLite archive shape pending the canonical format."""

    records: tuple[_SQLiteConversationArchiveRecord, ...]

    def __post_init__(self) -> None:
        if type(self.records) is not tuple or any(
            type(record) is not _SQLiteConversationArchiveRecord
            for record in self.records
        ):
            raise TypeError("archive records must be an exact record tuple")
        expected = tuple(
            sorted(
                self.records,
                key=lambda record: (
                    record.metadata.session_id,
                    record.metadata.created_at,
                    record.metadata.conversation_id,
                ),
            )
        )
        if self.records != expected:
            raise ValueError("archive records must use canonical order")
        identities = {
            (record.metadata.session_id, record.metadata.conversation_id)
            for record in self.records
        }
        if len(identities) != len(self.records):
            raise ValueError("archive records contain duplicate conversations")


class _SQLiteAdapter:
    """Shared construction and coordinator ownership for public SQLite ports."""

    __slots__ = ("_database", "_mutation_coordinator", "_mutation_timeout")

    def __init__(
        self,
        workspace: Workspace | str | os.PathLike[str],
        *,
        mutation_coordinator: ConversationStorageMutationCoordinator | None = None,
        mutation_timeout_seconds: float = (
            SQLITE_CONVERSATION_STORAGE_BUSY_TIMEOUT_SECONDS
        ),
    ) -> None:
        resolved = _resolve_workspace(workspace)
        timeout = validate_conversation_storage_mutation_timeout(
            mutation_timeout_seconds
        )
        coordinator = _resolve_coordinator(resolved, mutation_coordinator)
        self._database = SQLiteDatabase(resolved)
        self._mutation_coordinator = coordinator
        self._mutation_timeout = timeout

    @classmethod
    def _from_components(
        cls,
        database: SQLiteDatabase,
        coordinator: ConversationStorageMutationCoordinator,
        timeout: float,
    ) -> Self:
        instance = cls.__new__(cls)
        instance._database = database
        instance._mutation_coordinator = coordinator
        instance._mutation_timeout = timeout
        return instance

    def _read(self, operation: Callable[[object], ResultT]) -> ResultT:
        return _database_call(
            lambda: self._database.read(
                operation,
                validate_snapshot=_validate_database_snapshot,
            )
        )

    def _write(self, operation: Callable[[object], ResultT]) -> ResultT:
        with self._mutation_coordinator.borrow(
            timeout_seconds=self._mutation_timeout
        ) as lease:
            def validator() -> None:
                _validate_lease(
                    lease,
                    workspace_key=self._database.workspace_key,
                )

            return _database_call(
                lambda: self._database.write(
                    operation,
                    validate_snapshot=_validate_database_snapshot,
                    validate_lease=validator,
                )
            )


class SQLiteConversationCatalog(_SQLiteAdapter):
    """Transactional SQLite implementation of the conversation catalog port."""

    def create(self, session_id: str, subject: str) -> ConversationMetadata:
        """Create one active revision-1 conversation under a fresh UUID."""

        session = Workspace.validate_identifier(session_id)
        normalized_subject = normalize_conversation_subject(subject)

        def create_in_transaction(connection: object) -> ConversationMetadata:
            for _ in range(_CONVERSATION_ID_ALLOCATION_ATTEMPTS):
                conversation_id = f"c_{uuid4().hex}"
                existing = _select_metadata(
                    connection,
                    session,
                    conversation_id,
                )
                if existing is not None:
                    continue
                if _event_identity_exists(
                    connection,
                    session,
                    conversation_id,
                ):
                    raise SQLiteDatabaseFormatError()
                timestamp = datetime.now(UTC).isoformat()
                metadata = ConversationMetadata(
                    schema_version=CONVERSATION_METADATA_SCHEMA_VERSION,
                    session_id=session,
                    conversation_id=conversation_id,
                    subject=subject,
                    subject_revision=1,
                    status=ConversationStatus.ACTIVE,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                _insert_metadata(
                    connection,
                    metadata,
                    normalized_subject=normalized_subject,
                )
                return metadata
            raise ConversationExistsError(
                f"could not allocate a unique conversation ID in session {session!r}"
            )

        return self._write(create_in_transaction)

    def get(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationMetadata:
        """Read one exact session-owned metadata record."""

        session, conversation = _validate_identity(session_id, conversation_id)

        def get_from_connection(connection: object) -> ConversationMetadata:
            metadata = _select_metadata(connection, session, conversation)
            if metadata is None:
                raise ConversationNotFoundError(
                    f"conversation does not exist: {session}/{conversation}"
                )
            return metadata

        return self._read(get_from_connection)

    def list(
        self,
        session_id: str,
        *,
        include_archived: bool = False,
    ) -> tuple[ConversationMetadata, ...]:
        """List one session in deterministic creation/identity order."""

        session = Workspace.validate_identifier(session_id)
        if not isinstance(include_archived, bool):
            raise TypeError("include_archived must be a boolean")

        def list_from_connection(
            connection: object,
        ) -> tuple[ConversationMetadata, ...]:
            sql = _METADATA_SELECT + " WHERE session_id = ?"
            parameters: tuple[object, ...] = (session,)
            if not include_archived:
                sql += " AND status = ?"
                parameters += (ConversationStatus.ACTIVE.value,)
            sql += " ORDER BY created_at, conversation_id"
            return tuple(
                _metadata_from_row(row)
                for row in connection.execute(sql, parameters).fetchall()  # type: ignore[attr-defined]
            )

        return self._read(list_from_connection)

    def rename(
        self,
        session_id: str,
        conversation_id: str,
        subject: str,
    ) -> ConversationMetadata:
        """Replace the display subject and increment every successful revision."""

        session, conversation = _validate_identity(session_id, conversation_id)
        normalized_subject = normalize_conversation_subject(subject)

        def rename_in_transaction(connection: object) -> ConversationMetadata:
            current = _require_metadata(connection, session, conversation)
            if current.subject_revision == _SQLITE_SIGNED_INTEGER_MAX:
                raise SQLiteDatabaseFormatError()
            updated = ConversationMetadata(
                schema_version=current.schema_version,
                session_id=current.session_id,
                conversation_id=current.conversation_id,
                subject=subject,
                subject_revision=current.subject_revision + 1,
                status=current.status,
                created_at=current.created_at,
                updated_at=datetime.now(UTC).isoformat(),
            )
            connection.execute(  # type: ignore[attr-defined]
                "UPDATE conversations SET subject = ?, normalized_subject = ?, "
                "subject_revision = ?, updated_at = ? "
                "WHERE session_id = ? AND conversation_id = ?",
                (
                    updated.subject,
                    normalized_subject,
                    updated.subject_revision,
                    updated.updated_at,
                    updated.session_id,
                    updated.conversation_id,
                ),
            )
            return updated

        return self._write(rename_in_transaction)

    def archive(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationMetadata:
        """Archive a conversation, returning an already archived row unchanged."""

        return self._set_status(
            session_id,
            conversation_id,
            ConversationStatus.ARCHIVED,
        )

    def unarchive(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationMetadata:
        """Reactivate a conversation, returning an active row unchanged."""

        return self._set_status(
            session_id,
            conversation_id,
            ConversationStatus.ACTIVE,
        )

    def _set_status(
        self,
        session_id: str,
        conversation_id: str,
        status: ConversationStatus,
    ) -> ConversationMetadata:
        session, conversation = _validate_identity(session_id, conversation_id)

        def set_in_transaction(connection: object) -> ConversationMetadata:
            current = _require_metadata(connection, session, conversation)
            if current.status is status:
                return current
            updated = ConversationMetadata(
                schema_version=current.schema_version,
                session_id=current.session_id,
                conversation_id=current.conversation_id,
                subject=current.subject,
                subject_revision=current.subject_revision,
                status=status,
                created_at=current.created_at,
                updated_at=datetime.now(UTC).isoformat(),
            )
            connection.execute(  # type: ignore[attr-defined]
                "UPDATE conversations SET status = ?, updated_at = ? "
                "WHERE session_id = ? AND conversation_id = ?",
                (
                    updated.status.value,
                    updated.updated_at,
                    updated.session_id,
                    updated.conversation_id,
                ),
            )
            return updated

        return self._write(set_in_transaction)


class SQLiteConversationEventStore(_SQLiteAdapter):
    """Transactional SQLite implementation of the visible event-store port."""

    def append(self, event: ConversationEventAppend) -> ConversationEvent:
        """Canonicalize and append one event with a contiguous stored sequence."""

        if not isinstance(event, ConversationEventAppend):
            raise TypeError("event must be a ConversationEventAppend")
        canonical = ConversationEventAppend(
            session_id=event.session_id,
            conversation_id=event.conversation_id,
            turn_id=event.turn_id,
            sender=event.sender,
            recipient=event.recipient,
            kind=event.kind,
            content=event.content,
        )

        def append_in_transaction(connection: object) -> ConversationEvent:
            _require_metadata(
                connection,
                canonical.session_id,
                canonical.conversation_id,
            )
            next_sequence = _next_event_sequence(
                connection,
                canonical.session_id,
                canonical.conversation_id,
            )
            turn_events = _select_turn_events(
                connection,
                canonical.session_id,
                canonical.conversation_id,
                canonical.turn_id,
            )
            _validate_append(turn_events, canonical)
            stored = ConversationEvent(
                schema_version=CONVERSATION_EVENT_SCHEMA_VERSION,
                session_id=canonical.session_id,
                conversation_id=canonical.conversation_id,
                turn_id=canonical.turn_id,
                sequence=next_sequence,
                timestamp=datetime.now(UTC).isoformat(),
                sender=canonical.sender,
                recipient=canonical.recipient,
                kind=canonical.kind,
                content=canonical.content,
            )
            _insert_event(connection, stored)
            return stored

        return self._write(append_in_transaction)

    def read(
        self,
        session_id: str,
        conversation_id: str,
    ) -> tuple[ConversationEvent, ...]:
        """Read the complete authoritative transcript in storage order."""

        session, conversation = _validate_identity(session_id, conversation_id)

        def read_from_connection(
            connection: object,
        ) -> tuple[ConversationEvent, ...]:
            _require_metadata(connection, session, conversation)
            return _select_events(connection, session, conversation)

        return self._read(read_from_connection)

    def read_context(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationContextWindow:
        """Select the fixed-policy newest suffix of complete turns."""

        session, conversation = _validate_identity(session_id, conversation_id)

        def read_context_from_connection(
            connection: object,
        ) -> ConversationContextWindow:
            _require_metadata(connection, session, conversation)
            _, context = _select_events_and_context(
                connection,
                session,
                conversation,
            )
            return context

        return self._read(read_context_from_connection)


class SQLiteConversationArchiveAdapter(_SQLiteAdapter):
    """Privileged exact SQLite archive source and sink under caller leases."""

    def export_archive(
        self,
        lease: ConversationStorageMutationLease,
    ) -> _SQLiteConversationArchivePayload:
        """Export all metadata and ordered events under an explicit valid lease."""

        def validate() -> None:
            _validate_lease(lease, workspace_key=self._database.workspace_key)

        validate()

        def export_from_connection(
            connection: object,
        ) -> _SQLiteConversationArchivePayload:
            validate()
            payload = _read_archive_payload(connection)
            validate()
            return payload

        payload = self._read(export_from_connection)
        validate()
        return payload

    def import_archive(
        self,
        lease: ConversationStorageMutationLease,
        payload: _SQLiteConversationArchivePayload,
    ) -> ConversationArchiveImportReceipt:
        """Import absent complete records, accept identity, and reject divergence."""

        canonical = _canonical_archive_payload(payload)
        normalized_subjects = tuple(
            normalize_conversation_subject(record.metadata.subject)
            for record in canonical.records
        )

        def validate() -> None:
            _validate_lease(lease, workspace_key=self._database.workspace_key)

        validate()

        def import_in_transaction(
            connection: object,
        ) -> ConversationArchiveImportReceipt:
            _validate_database_snapshot(connection)
            imported = False
            for record, normalized_subject in zip(
                canonical.records,
                normalized_subjects,
                strict=True,
            ):
                metadata = record.metadata
                current = _select_metadata(
                    connection,
                    metadata.session_id,
                    metadata.conversation_id,
                )
                if current is not None:
                    current_events = _select_events(
                        connection,
                        metadata.session_id,
                        metadata.conversation_id,
                    )
                    if current != metadata or current_events != record.events:
                        raise ConversationExistsError(
                            "conversation archive conflicts with existing storage"
                        )
                    continue
                _insert_metadata(
                    connection,
                    metadata,
                    normalized_subject=normalized_subject,
                )
                for event in record.events:
                    _insert_event(connection, event)
                imported = True
            receipt = ConversationArchiveImportReceipt(
                ConversationArchiveImportStatus.IMPORTED
                if imported
                else ConversationArchiveImportStatus.UNCHANGED
            )
            _validate_database_snapshot(connection)
            return receipt

        return _database_call(
            lambda: self._database.write(
                import_in_transaction,
                validate_snapshot=_validate_database_snapshot,
                validate_lease=validate,
            )
        )


def preflight_sqlite_conversation_storage(
    workspace: Workspace | str | os.PathLike[str],
    /,
) -> ConversationStoragePreflight:
    """Return SQLite host/storage capability without creating or repairing it."""

    database = SQLiteDatabase(_resolve_workspace(workspace))
    return _preflight_database(database)


def provision_sqlite_conversation_storage(
    workspace: Workspace | str | os.PathLike[str],
    /,
    *,
    lease: ConversationStorageMutationLease,
) -> ConversationStoragePreflight:
    """Create v1 storage under an active correct-workspace external lease."""

    database = SQLiteDatabase(_resolve_workspace(workspace))

    def validator() -> None:
        _validate_lease(
            lease,
            workspace_key=database.workspace_key,
        )

    validator()
    _database_call(
        lambda: database.provision(
            _validate_database_snapshot,
            validate_lease=validator,
        )
    )
    validator()
    return ConversationStoragePreflight(
        backend=ConversationStorageBackend.SQLITE,
        capability=ConversationStorageCapability.READY,
    )


def compose_sqlite_conversation_storage(
    workspace: Workspace | str | os.PathLike[str],
    /,
    *,
    mutation_timeout_seconds: float = (
        SQLITE_CONVERSATION_STORAGE_BUSY_TIMEOUT_SECONDS
    ),
) -> ConversationStorageServices:
    """Preflight and bind one same-provenance SQLite service bundle."""

    resolved = _resolve_workspace(workspace)
    timeout = validate_conversation_storage_mutation_timeout(
        mutation_timeout_seconds
    )
    database = SQLiteDatabase(resolved)
    _preflight_database(database).require_ready()
    coordinator = WorkspaceConversationStorageMutationCoordinator(resolved)
    catalog = SQLiteConversationCatalog._from_components(
        database,
        coordinator,
        timeout,
    )
    event_store = SQLiteConversationEventStore._from_components(
        database,
        coordinator,
        timeout,
    )
    search = ReferenceConversationSearch(catalog, event_store)
    provenance = ConversationStorageProvenance(
        backend=ConversationStorageBackend.SQLITE,
        workspace_key=database.workspace_key,
    )
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


def compose_sqlite_conversation_archive(
    workspace: Workspace | str | os.PathLike[str],
    /,
    *,
    mutation_timeout_seconds: float = (
        SQLITE_CONVERSATION_STORAGE_BUSY_TIMEOUT_SECONDS
    ),
) -> SQLiteConversationArchiveAdapter:
    """Preflight and compose the privileged SQLite archive adapter."""

    resolved = _resolve_workspace(workspace)
    timeout = validate_conversation_storage_mutation_timeout(
        mutation_timeout_seconds
    )
    database = SQLiteDatabase(resolved)
    _preflight_database(database).require_ready()
    coordinator = WorkspaceConversationStorageMutationCoordinator(resolved)
    return SQLiteConversationArchiveAdapter._from_components(
        database,
        coordinator,
        timeout,
    )


def _preflight_database(database: SQLiteDatabase) -> ConversationStoragePreflight:
    """Return one content-free capability while preserving malformed failures."""

    try:
        database.verify_existing(_validate_database_snapshot)
    except SQLiteDatabaseMissingError:
        capability = ConversationStorageCapability.PROVISIONING_NEEDED
    except SQLiteDatabaseUnavailableError:
        capability = ConversationStorageCapability.UNAVAILABLE
    except SQLiteDatabaseVersionError:
        raise WorkspaceVersionUnsupportedError(
            "unsupported SQLite conversation storage schema version"
        ) from None
    except SQLiteDatabaseFormatError:
        raise WorkspaceFormatError("invalid SQLite conversation storage") from None
    except SQLiteDatabaseSecurityError:
        raise WorkspaceSecurityError("unsafe SQLite conversation storage path") from None
    else:
        capability = ConversationStorageCapability.READY
    return ConversationStoragePreflight(
        backend=ConversationStorageBackend.SQLITE,
        capability=capability,
    )


def _resolve_workspace(
    workspace: Workspace | str | os.PathLike[str],
) -> Workspace:
    """Resolve one workspace-or-path argument exactly once."""

    return workspace if isinstance(workspace, Workspace) else Workspace(workspace)


def _resolve_coordinator(
    workspace: Workspace,
    coordinator: ConversationStorageMutationCoordinator | None,
) -> ConversationStorageMutationCoordinator:
    """Return a structurally valid coordinator for the exact workspace."""

    resolved = (
        WorkspaceConversationStorageMutationCoordinator(workspace)
        if coordinator is None
        else coordinator
    )
    if (
        not callable(getattr(resolved, "borrow", None))
        or getattr(resolved, "workspace_key", None) != os.fspath(workspace.root)
    ):
        raise ConversationStorageCoordinationError()
    return resolved


def _validate_lease(
    lease: ConversationStorageMutationLease,
    *,
    workspace_key: str,
) -> None:
    """Validate structural lease evidence and map collaborator failures."""

    try:
        lease.validate(workspace_key=workspace_key)
    except ConversationStorageCoordinationError:
        raise
    except Exception:
        raise ConversationStorageCoordinationError() from None


def _database_call(operation: Callable[[], ResultT]) -> ResultT:
    """Translate private database categories to established public failures."""

    try:
        return operation()
    except SQLiteDatabaseMissingError:
        from .contracts import ConversationStorageProvisioningNeededError

        raise ConversationStorageProvisioningNeededError() from None
    except SQLiteDatabaseUnavailableError:
        raise ConversationStorageUnavailableError() from None
    except SQLiteDatabaseVersionError:
        raise WorkspaceVersionUnsupportedError(
            "unsupported SQLite conversation storage schema version"
        ) from None
    except SQLiteDatabaseFormatError:
        raise WorkspaceFormatError("invalid SQLite conversation storage") from None
    except SQLiteDatabaseSecurityError:
        raise WorkspaceSecurityError("unsafe SQLite conversation storage path") from None


def _validate_identity(session_id: str, conversation_id: str) -> tuple[str, str]:
    """Return two exact safe conversation identities."""

    return (
        Workspace.validate_identifier(session_id),
        Workspace.validate_identifier(conversation_id, label="conversation ID"),
    )


def _metadata_from_row(row: object) -> ConversationMetadata:
    """Construct domain metadata and verify its private normalized subject."""

    if type(row) is not tuple or len(row) != 9:
        raise SQLiteDatabaseFormatError()
    normalized_subject = row[4]
    status = row[6]
    if type(normalized_subject) is not str or type(status) is not str:
        raise SQLiteDatabaseFormatError()
    try:
        metadata = ConversationMetadata(
            schema_version=row[0],
            session_id=row[1],
            conversation_id=row[2],
            subject=row[3],
            subject_revision=row[5],
            status=ConversationStatus(status),
            created_at=row[7],
            updated_at=row[8],
        )
        expected_normalized_subject = normalize_conversation_subject(
            metadata.subject
        )
    except (SessionIdError, TypeError, ValueError):
        raise SQLiteDatabaseFormatError() from None
    if normalized_subject != expected_normalized_subject:
        raise SQLiteDatabaseFormatError()
    return metadata


def _event_from_row(row: object) -> ConversationEvent:
    """Construct one exact domain event and verify its persisted turn slot."""

    if type(row) is not tuple or len(row) != 11:
        raise SQLiteDatabaseFormatError()
    raw_kind = row[8]
    raw_slot = row[10]
    if type(raw_kind) is not str or type(raw_slot) is not int:
        raise SQLiteDatabaseFormatError()
    try:
        kind = ConversationEventKind(raw_kind)
        event = ConversationEvent(
            schema_version=row[0],
            session_id=row[1],
            conversation_id=row[2],
            turn_id=row[3],
            sequence=row[4],
            timestamp=row[5],
            sender=row[6],
            recipient=row[7],
            kind=kind,
            content=row[9],
        )
    except (SessionIdError, TypeError, ValueError):
        raise SQLiteDatabaseFormatError() from None
    expected_slot = 0 if kind is ConversationEventKind.USER_MESSAGE else 1
    if raw_slot != expected_slot:
        raise SQLiteDatabaseFormatError()
    return event


def _validate_database_snapshot(connection: object) -> None:
    """Reconstruct and validate every authoritative metadata/event row."""

    _read_validated_database_snapshot(connection)


def _read_validated_database_snapshot(
    connection: object,
) -> tuple[
    tuple[ConversationMetadata, ...],
    dict[tuple[str, str], tuple[ConversationEvent, ...]],
]:
    """Return every validated row grouped under an exact catalog identity."""

    try:
        metadata_rows = connection.execute(  # type: ignore[attr-defined]
            _METADATA_SELECT + " ORDER BY session_id, created_at, conversation_id"
        ).fetchall()
        event_rows = connection.execute(  # type: ignore[attr-defined]
            _EVENT_SELECT + " ORDER BY session_id, conversation_id, sequence"
        ).fetchall()
        metadata = tuple(_metadata_from_row(row) for row in metadata_rows)
        events = tuple(_event_from_row(row) for row in event_rows)
        identities = {(item.session_id, item.conversation_id) for item in metadata}
        if len(identities) != len(metadata):
            raise SQLiteDatabaseFormatError()
        grouped: dict[tuple[str, str], list[ConversationEvent]] = {}
        for event in events:
            identity = (event.session_id, event.conversation_id)
            if identity not in identities:
                raise SQLiteDatabaseFormatError()
            grouped.setdefault(identity, []).append(event)
        validated_groups: dict[tuple[str, str], tuple[ConversationEvent, ...]] = {}
        for identity in identities:
            identity_events = tuple(grouped.get(identity, ()))
            _validate_event_collection(
                identity_events,
                session_id=identity[0],
                conversation_id=identity[1],
            )
            validated_groups[identity] = identity_events
        return metadata, validated_groups
    except _SQLiteDatabaseErrorTypes:
        raise
    except (AttributeError, SessionIdError, TypeError, ValueError):
        raise SQLiteDatabaseFormatError() from None


_SQLiteDatabaseErrorTypes = (
    SQLiteDatabaseFormatError,
    SQLiteDatabaseMissingError,
    SQLiteDatabaseSecurityError,
    SQLiteDatabaseUnavailableError,
    SQLiteDatabaseVersionError,
)


def _validate_event_collection(
    events: tuple[ConversationEvent, ...],
    *,
    session_id: str,
    conversation_id: str,
) -> ConversationContextWindow:
    """Verify one exact identity-bound contiguous transcript and turn slots."""

    if type(events) is not tuple:
        raise TypeError("conversation events must be an exact tuple")
    for expected_sequence, event in enumerate(events, start=1):
        if type(event) is not ConversationEvent:
            raise TypeError("conversation events must be canonical events")
        if (
            event.session_id != session_id
            or event.conversation_id != conversation_id
            or event.sequence != expected_sequence
        ):
            raise ValueError("conversation transcript identity or order is invalid")
    return select_bounded_conversation_context(events)


def _select_metadata(
    connection: object,
    session_id: str,
    conversation_id: str,
) -> ConversationMetadata | None:
    """Read one metadata row from an already verified connection."""

    rows = connection.execute(  # type: ignore[attr-defined]
        _METADATA_SELECT + " WHERE session_id = ? AND conversation_id = ?",
        (session_id, conversation_id),
    ).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise SQLiteDatabaseFormatError()
    return _metadata_from_row(rows[0])


def _require_metadata(
    connection: object,
    session_id: str,
    conversation_id: str,
) -> ConversationMetadata:
    """Read one row or raise the established missing-identity failure."""

    metadata = _select_metadata(connection, session_id, conversation_id)
    if metadata is None:
        raise ConversationNotFoundError(
            f"conversation does not exist: {session_id}/{conversation_id}"
        )
    return metadata


def _select_events(
    connection: object,
    session_id: str,
    conversation_id: str,
) -> tuple[ConversationEvent, ...]:
    """Read and validate one authoritative event sequence."""

    events, _ = _select_events_and_context(
        connection,
        session_id,
        conversation_id,
    )
    return events


def _select_events_and_context(
    connection: object,
    session_id: str,
    conversation_id: str,
) -> tuple[tuple[ConversationEvent, ...], ConversationContextWindow]:
    """Read one transcript and return its single validated context selection."""

    rows = connection.execute(  # type: ignore[attr-defined]
        _EVENT_SELECT
        + " WHERE session_id = ? AND conversation_id = ? ORDER BY sequence",
        (session_id, conversation_id),
    ).fetchall()
    events = tuple(_event_from_row(row) for row in rows)
    try:
        context = _validate_event_collection(
            events,
            session_id=session_id,
            conversation_id=conversation_id,
        )
    except (TypeError, ValueError):
        raise SQLiteDatabaseFormatError() from None
    return events, context


def _event_identity_exists(
    connection: object,
    session_id: str,
    conversation_id: str,
) -> bool:
    """Report bounded event presence for one metadata allocation identity."""

    row = connection.execute(  # type: ignore[attr-defined]
        "SELECT 1 FROM conversation_events "
        "WHERE session_id = ? AND conversation_id = ? LIMIT 1",
        (session_id, conversation_id),
    ).fetchone()
    if row is None:
        return False
    if type(row) is not tuple or row != (1,):
        raise SQLiteDatabaseFormatError()
    return True


def _next_event_sequence(
    connection: object,
    session_id: str,
    conversation_id: str,
) -> int:
    """Return one plus the indexed maximum sequence for an addressed transcript."""

    row = connection.execute(  # type: ignore[attr-defined]
        "SELECT COALESCE(MAX(sequence), 0) FROM conversation_events "
        "WHERE session_id = ? AND conversation_id = ?",
        (session_id, conversation_id),
    ).fetchone()
    if (
        type(row) is not tuple
        or len(row) != 1
        or type(row[0]) is not int
        or row[0] < 0
        or row[0] >= _SQLITE_SIGNED_INTEGER_MAX
    ):
        raise SQLiteDatabaseFormatError()
    return row[0] + 1


def _select_turn_events(
    connection: object,
    session_id: str,
    conversation_id: str,
    turn_id: str,
) -> tuple[ConversationEvent, ...]:
    """Read at most one indexed user/outcome pair for an addressed turn."""

    rows = connection.execute(  # type: ignore[attr-defined]
        _EVENT_SELECT + " WHERE session_id = ? AND conversation_id = ? AND turn_id = ? "
        "ORDER BY turn_slot",
        (session_id, conversation_id, turn_id),
    ).fetchall()
    if len(rows) > 2:
        raise SQLiteDatabaseFormatError()
    return tuple(_event_from_row(row) for row in rows)


def _insert_metadata(
    connection: object,
    metadata: ConversationMetadata,
    *,
    normalized_subject: str,
) -> None:
    """Insert all exact authoritative metadata fields."""

    connection.execute(  # type: ignore[attr-defined]
        "INSERT INTO conversations "
        "(session_id, conversation_id, schema_version, subject, "
        "normalized_subject, subject_revision, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            metadata.session_id,
            metadata.conversation_id,
            metadata.schema_version,
            metadata.subject,
            normalized_subject,
            metadata.subject_revision,
            metadata.status.value,
            metadata.created_at,
            metadata.updated_at,
        ),
    )


def _insert_event(connection: object, event: ConversationEvent) -> None:
    """Insert all exact authoritative event fields and its derived slot."""

    turn_slot = 0 if event.kind is ConversationEventKind.USER_MESSAGE else 1
    connection.execute(  # type: ignore[attr-defined]
        "INSERT INTO conversation_events "
        "(session_id, conversation_id, sequence, schema_version, turn_id, "
        "turn_slot, timestamp, sender, recipient, kind, content) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event.session_id,
            event.conversation_id,
            event.sequence,
            event.schema_version,
            event.turn_id,
            turn_slot,
            event.timestamp,
            event.sender,
            event.recipient,
            event.kind.value,
            event.content,
        ),
    )


def _validate_append(
    events: tuple[ConversationEvent, ...],
    event: ConversationEventAppend,
) -> None:
    """Validate one indexed stored turn and the proposed append."""

    if type(events) is not tuple or len(events) > 2:
        raise SQLiteDatabaseFormatError()
    user: ConversationEvent | None = None
    outcome: ConversationEvent | None = None
    for stored in events:
        if (
            type(stored) is not ConversationEvent
            or stored.session_id != event.session_id
            or stored.conversation_id != event.conversation_id
            or stored.turn_id != event.turn_id
        ):
            raise SQLiteDatabaseFormatError()
        if stored.kind is ConversationEventKind.USER_MESSAGE:
            if user is not None or outcome is not None:
                raise SQLiteDatabaseFormatError()
            user = stored
        else:
            if outcome is not None:
                raise SQLiteDatabaseFormatError()
            outcome = stored

    if outcome is not None:
        if user is None or outcome.sequence <= user.sequence:
            raise SQLiteDatabaseFormatError()
        if (
            outcome.kind is ConversationEventKind.AGENT_REPLY
            and outcome.sender != user.recipient
        ):
            raise SQLiteDatabaseFormatError()

    if event.kind is ConversationEventKind.USER_MESSAGE:
        if user is not None:
            raise ValueError(
                f"conversation turn already has a user event: {event.turn_id}"
            )
        return
    if user is None:
        raise ValueError(f"conversation outcome has no user event: {event.turn_id}")
    if outcome is not None:
        raise ValueError(f"conversation turn already has an outcome: {event.turn_id}")
    if (
        event.kind is ConversationEventKind.AGENT_REPLY
        and event.sender != user.recipient
    ):
        raise ValueError(
            "conversation reply sender does not match the addressed agent: "
            f"{event.turn_id}"
        )


def _read_archive_payload(connection: object) -> _SQLiteConversationArchivePayload:
    """Return the deterministic private archive from one verified snapshot."""

    metadata, grouped = _read_validated_database_snapshot(connection)
    records = tuple(
        _SQLiteConversationArchiveRecord(
            metadata=item,
            events=grouped[(item.session_id, item.conversation_id)],
        )
        for item in metadata
    )
    return _SQLiteConversationArchivePayload(records=records)


def _canonical_archive_payload(
    payload: _SQLiteConversationArchivePayload,
) -> _SQLiteConversationArchivePayload:
    """Copy and fully validate an untrusted adapter-owned archive payload."""

    if type(payload) is not _SQLiteConversationArchivePayload:
        raise TypeError("payload must be a SQLite conversation archive")
    if type(payload.records) is not tuple:
        raise TypeError("archive contains invalid records")
    records: list[_SQLiteConversationArchiveRecord] = []
    for source_record in payload.records:
        if type(source_record) is not _SQLiteConversationArchiveRecord:
            raise TypeError("archive contains an invalid record")
        source_metadata = source_record.metadata
        if type(source_metadata) is not ConversationMetadata:
            raise TypeError("archive contains invalid metadata")
        metadata = ConversationMetadata(
            schema_version=source_metadata.schema_version,
            session_id=source_metadata.session_id,
            conversation_id=source_metadata.conversation_id,
            subject=source_metadata.subject,
            subject_revision=source_metadata.subject_revision,
            status=source_metadata.status,
            created_at=source_metadata.created_at,
            updated_at=source_metadata.updated_at,
        )
        if type(source_record.events) is not tuple:
            raise TypeError("archive contains invalid events")
        events = tuple(
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
            )
            for event in source_record.events
            if type(event) is ConversationEvent
        )
        if len(events) != len(source_record.events):
            raise TypeError("archive contains invalid events")
        records.append(
            _SQLiteConversationArchiveRecord(
                metadata=metadata,
                events=events,
            )
        )
    return _SQLiteConversationArchivePayload(records=tuple(records))


__all__ = [
    "SQLITE_CONVERSATION_STORAGE_APPLICATION_ID",
    "SQLITE_CONVERSATION_STORAGE_BUSY_TIMEOUT_SECONDS",
    "SQLITE_CONVERSATION_STORAGE_DATABASE_PATH",
    "SQLITE_CONVERSATION_STORAGE_MINIMUM_VERSION",
    "SQLITE_CONVERSATION_STORAGE_SCHEMA_VERSION",
    "SQLiteConversationArchiveAdapter",
    "SQLiteConversationCatalog",
    "SQLiteConversationEventStore",
    "compose_sqlite_conversation_archive",
    "compose_sqlite_conversation_storage",
    "preflight_sqlite_conversation_storage",
    "provision_sqlite_conversation_storage",
]
