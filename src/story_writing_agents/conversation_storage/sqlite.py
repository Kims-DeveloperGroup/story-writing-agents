"""Optional SQLite conversation catalog, event store, and archive adapter."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Iterator, Self, TypeVar
from uuid import uuid4

from ..conversation_literal_search import (
    CONVERSATION_LITERAL_NORMALIZATION_VERSION,
    build_conversation_literal_snippet,
    classify_conversation_literal_subject,
    conversation_literal_sort_key,
    normalize_conversation_literal_query,
    normalize_conversation_literal_text,
)
from ..conversation_search import (
    CONVERSATION_MEMORY_POLICY,
    ConversationCitation,
    ConversationSearchHit,
    ConversationSearchMatchKind,
    ConversationSearchQuery,
    ConversationSearchResult,
    ConversationSearchScope,
)
from ..conversations import (
    CONVERSATION_CONTEXT_POLICY,
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
from .sqlite_search_index import (
    SQLiteConversationSearchIndex,
    SQLiteConversationSearchIndexDocument,
    SQLiteConversationSearchIndexError,
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
    "timestamp, sender, recipient, kind, content, searchable_content, turn_slot "
    "FROM conversation_events"
)
_EVENT_SELECT_BY_TURN = _EVENT_SELECT.replace(
    "FROM conversation_events",
    "FROM conversation_events INDEXED BY conversation_events_turn_slots",
)
_EVENT_SELECT_RECENT = _EVENT_SELECT.replace(
    "FROM conversation_events",
    "FROM conversation_events INDEXED BY conversation_events_recent_outcomes",
)

_RECENT_CONTEXT_CANDIDATE_TURNS = CONVERSATION_CONTEXT_POLICY.max_complete_turns + 1

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

    __slots__ = (
        "_database",
        "_mutation_coordinator",
        "_mutation_timeout",
        "_search_index",
    )

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
        self._search_index = SQLiteConversationSearchIndex(
            self._database.workspace_key,
            normalization_version=CONVERSATION_LITERAL_NORMALIZATION_VERSION,
        )

    @classmethod
    def _from_components(
        cls,
        database: SQLiteDatabase,
        coordinator: ConversationStorageMutationCoordinator,
        timeout: float,
        search_index: SQLiteConversationSearchIndex,
    ) -> Self:
        instance = cls.__new__(cls)
        instance._database = database
        instance._mutation_coordinator = coordinator
        instance._mutation_timeout = timeout
        instance._search_index = search_index
        return instance

    def _read(self, operation: Callable[[object], ResultT]) -> ResultT:
        return _database_call(
            lambda: self._database.read(
                operation,
                validate_snapshot=_validate_database_snapshot,
            )
        )

    def _write(
        self,
        operation: Callable[[object], ResultT],
        *,
        changed: Callable[[ResultT], bool] | None = None,
    ) -> ResultT:
        with self._mutation_coordinator.borrow(
            timeout_seconds=self._mutation_timeout
        ) as lease:

            def validator() -> None:
                _validate_lease(
                    lease,
                    workspace_key=self._database.workspace_key,
                )

            return self._write_under_lease(
                operation,
                validate_lease=validator,
                changed=changed,
            )

    def _write_under_lease(
        self,
        operation: Callable[[object], ResultT],
        *,
        validate_lease: Callable[[], None],
        changed: Callable[[ResultT], bool] | None = None,
    ) -> ResultT:
        """Commit required lexical state, then best-effort refresh derived FTS."""

        def mutate(connection: object) -> tuple[ResultT, int | None]:
            result = operation(connection)
            effective = True if changed is None else changed(result)
            if type(effective) is not bool:
                raise TypeError("SQLite mutation changed predicate must be boolean")
            generation = _advance_lexical_generation(connection) if effective else None
            return result, generation

        result, generation = _database_call(
            lambda: self._database.write(
                mutate,
                validate_snapshot=_validate_database_snapshot,
                validate_lease=validate_lease,
            )
        )
        if generation is not None:
            _refresh_search_index_after_commit(
                self._database,
                self._search_index,
                source_generation=generation,
                validate_lease=validate_lease,
            )
        return result


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
        effective = False

        def set_in_transaction(connection: object) -> ConversationMetadata:
            nonlocal effective
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
            effective = True
            return updated

        return self._write(
            set_in_transaction,
            changed=lambda _result: effective,
        )


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
            return _select_recent_context(
                connection,
                session,
                conversation,
            )

        return self._read(read_context_from_connection)


class SQLiteConversationSearch(_SQLiteAdapter):
    """One-snapshot exact SQLite implementation of conversation search."""

    def search(
        self,
        scope: ConversationSearchScope,
        query: ConversationSearchQuery,
    ) -> ConversationSearchResult:
        """Search one authorized session with optional non-authoritative FTS."""

        if type(scope) is not ConversationSearchScope:
            raise TypeError(
                "conversation search scope must be a ConversationSearchScope"
            )
        if type(query) is not ConversationSearchQuery:
            raise TypeError(
                "conversation search query must be a ConversationSearchQuery"
            )
        normalized_query = normalize_conversation_literal_query(
            query.text,
            max_characters=CONVERSATION_MEMORY_POLICY.max_query_characters,
        )

        def search_snapshot(connection: object) -> ConversationSearchResult:
            return _search_sqlite_snapshot(
                connection,
                search_index=self._search_index,
                scope=scope,
                query=query,
                normalized_query=normalized_query,
            )

        return self._read(search_snapshot)


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
        effective = False

        def import_in_transaction(
            connection: object,
        ) -> ConversationArchiveImportReceipt:
            nonlocal effective
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
                effective = True
            receipt = ConversationArchiveImportReceipt(
                ConversationArchiveImportStatus.IMPORTED
                if imported
                else ConversationArchiveImportStatus.UNCHANGED
            )
            _validate_database_snapshot(connection)
            return receipt

        return self._write_under_lease(
            import_in_transaction,
            validate_lease=validate,
            changed=lambda _result: effective,
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
    generation = _database_call(
        lambda: database.read(
            _lexical_generation,
            validate_snapshot=_validate_database_snapshot,
        )
    )
    _refresh_search_index_after_commit(
        database,
        SQLiteConversationSearchIndex(
            database.workspace_key,
            normalization_version=CONVERSATION_LITERAL_NORMALIZATION_VERSION,
        ),
        source_generation=generation,
        validate_lease=validator,
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
    timeout = validate_conversation_storage_mutation_timeout(mutation_timeout_seconds)
    database = SQLiteDatabase(resolved)
    _preflight_database(database).require_ready()
    coordinator = WorkspaceConversationStorageMutationCoordinator(resolved)
    search_index = SQLiteConversationSearchIndex(
        database.workspace_key,
        normalization_version=CONVERSATION_LITERAL_NORMALIZATION_VERSION,
    )
    catalog = SQLiteConversationCatalog._from_components(
        database,
        coordinator,
        timeout,
        search_index,
    )
    event_store = SQLiteConversationEventStore._from_components(
        database,
        coordinator,
        timeout,
        search_index,
    )
    search = SQLiteConversationSearch._from_components(
        database,
        coordinator,
        timeout,
        search_index,
    )
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
    timeout = validate_conversation_storage_mutation_timeout(mutation_timeout_seconds)
    database = SQLiteDatabase(resolved)
    _preflight_database(database).require_ready()
    coordinator = WorkspaceConversationStorageMutationCoordinator(resolved)
    search_index = SQLiteConversationSearchIndex(
        database.workspace_key,
        normalization_version=CONVERSATION_LITERAL_NORMALIZATION_VERSION,
    )
    return SQLiteConversationArchiveAdapter._from_components(
        database,
        coordinator,
        timeout,
        search_index,
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
        raise WorkspaceSecurityError(
            "unsafe SQLite conversation storage path"
        ) from None
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
    if not callable(getattr(resolved, "borrow", None)) or getattr(
        resolved, "workspace_key", None
    ) != os.fspath(workspace.root):
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
        raise WorkspaceSecurityError(
            "unsafe SQLite conversation storage path"
        ) from None


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
        expected_normalized_subject = normalize_conversation_subject(metadata.subject)
    except (SessionIdError, TypeError, ValueError):
        raise SQLiteDatabaseFormatError() from None
    if normalized_subject != expected_normalized_subject:
        raise SQLiteDatabaseFormatError()
    return metadata


def _event_and_searchable_from_row(
    row: object,
) -> tuple[ConversationEvent, str]:
    """Construct one event and verify its required derived lexical values."""

    if type(row) is not tuple or len(row) != 12:
        raise SQLiteDatabaseFormatError()
    raw_kind = row[8]
    raw_searchable = row[10]
    raw_slot = row[11]
    if (
        type(raw_kind) is not str
        or type(raw_searchable) is not str
        or type(raw_slot) is not int
    ):
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
    expected_searchable = normalize_conversation_literal_text(event.content)
    if raw_slot != expected_slot or raw_searchable != expected_searchable:
        raise SQLiteDatabaseFormatError()
    return event, raw_searchable


def _event_from_row(row: object) -> ConversationEvent:
    """Construct one exact domain event from a persisted row."""

    event, _searchable = _event_and_searchable_from_row(row)
    return event


def _validate_database_snapshot(connection: object) -> None:
    """Reconstruct and validate every authoritative metadata/event row."""

    _read_validated_database_snapshot(connection)


def _lexical_generation(connection: object) -> int:
    """Return the exact required lexical-state generation."""

    row = connection.execute(  # type: ignore[attr-defined]
        "SELECT lexical_generation FROM conversation_storage_identity "
        "WHERE singleton = 1"
    ).fetchone()
    if (
        type(row) is not tuple
        or len(row) != 1
        or type(row[0]) is not int
        or row[0] < 0
        or row[0] > _SQLITE_SIGNED_INTEGER_MAX
    ):
        raise SQLiteDatabaseFormatError()
    return row[0]


def _advance_lexical_generation(connection: object) -> int:
    """Advance required lexical state once inside its authoritative transaction."""

    current = _lexical_generation(connection)
    if current == _SQLITE_SIGNED_INTEGER_MAX:
        raise SQLiteDatabaseFormatError()
    generation = current + 1
    cursor = connection.execute(  # type: ignore[attr-defined]
        "UPDATE conversation_storage_identity SET lexical_generation = ? "
        "WHERE singleton = 1 AND lexical_generation = ?",
        (generation, current),
    )
    if getattr(cursor, "rowcount", None) != 1:
        raise SQLiteDatabaseFormatError()
    return generation


def _refresh_search_index_after_commit(
    database: SQLiteDatabase,
    search_index: SQLiteConversationSearchIndex,
    *,
    source_generation: int,
    validate_lease: Callable[[], None],
) -> None:
    """Best-effort rebuild disposable FTS from one committed main snapshot."""

    def rebuild(connection: object) -> None:
        if _lexical_generation(connection) != source_generation:
            return
        rows = connection.execute(  # type: ignore[attr-defined]
            _EVENT_SELECT
            + " WHERE kind != ? ORDER BY session_id, conversation_id, sequence",
            (ConversationEventKind.RUNTIME_ERROR.value,),
        )

        def documents() -> Iterator[SQLiteConversationSearchIndexDocument]:
            for row in rows:
                event, searchable = _event_and_searchable_from_row(row)
                yield SQLiteConversationSearchIndexDocument(
                    session_id=event.session_id,
                    conversation_id=event.conversation_id,
                    sequence=event.sequence,
                    searchable_content=searchable,
                )

        search_index.rebuild(
            source_generation=source_generation,
            documents=documents(),
            validate_publication=validate_lease,
        )

    try:
        validate_lease()
        database.read(
            rebuild,
            validate_snapshot=_validate_database_snapshot,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except (
        ConversationStorageCoordinationError,
        SQLiteConversationSearchIndexError,
        SQLiteDatabaseFormatError,
        SQLiteDatabaseMissingError,
        SQLiteDatabaseSecurityError,
        SQLiteDatabaseUnavailableError,
        SQLiteDatabaseVersionError,
        SessionIdError,
        TypeError,
        ValueError,
    ):
        return


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


def _select_recent_context(
    connection: object,
    session_id: str,
    conversation_id: str,
) -> ConversationContextWindow:
    """Select context from at most 25 indexed complete-turn pairs."""

    total_row = connection.execute(  # type: ignore[attr-defined]
        "SELECT COUNT(*) FROM conversation_events "
        "WHERE session_id = ? AND conversation_id = ? AND turn_slot = 1",
        (session_id, conversation_id),
    ).fetchone()
    if (
        type(total_row) is not tuple
        or len(total_row) != 1
        or type(total_row[0]) is not int
        or total_row[0] < 0
    ):
        raise SQLiteDatabaseFormatError()
    total_complete_turns = total_row[0]

    user_rows = connection.execute(  # type: ignore[attr-defined]
        _EVENT_SELECT_RECENT
        + " WHERE session_id = ? AND conversation_id = ? AND turn_slot = 0 "
        "AND EXISTS ("
        "SELECT 1 FROM conversation_events AS outcome_event "
        "INDEXED BY conversation_events_turn_slots "
        "WHERE outcome_event.session_id = conversation_events.session_id "
        "AND outcome_event.conversation_id = conversation_events.conversation_id "
        "AND outcome_event.turn_id = conversation_events.turn_id "
        "AND outcome_event.turn_slot = 1"
        ") ORDER BY sequence DESC LIMIT ?",
        (
            session_id,
            conversation_id,
            _RECENT_CONTEXT_CANDIDATE_TURNS,
        ),
    ).fetchall()
    users = tuple(_event_from_row(row) for row in user_rows)
    if len(users) > total_complete_turns:
        raise SQLiteDatabaseFormatError()
    if not users:
        if total_complete_turns != 0:
            raise SQLiteDatabaseFormatError()
        return ConversationContextWindow(
            events=(),
            total_complete_turns=0,
            retained_turns=0,
            omitted_turns=0,
            retained_utf8_bytes=0,
            truncated=False,
        )

    turn_ids = tuple(user.turn_id for user in users)
    if len(set(turn_ids)) != len(turn_ids):
        raise SQLiteDatabaseFormatError()
    placeholders = ", ".join("?" for _ in turn_ids)
    outcome_rows = connection.execute(  # type: ignore[attr-defined]
        _EVENT_SELECT_BY_TURN
        + " WHERE session_id = ? AND conversation_id = ? AND turn_slot = 1 "
        f"AND turn_id IN ({placeholders}) ORDER BY sequence",
        (session_id, conversation_id, *turn_ids),
    ).fetchall()
    outcomes = tuple(_event_from_row(row) for row in outcome_rows)
    outcome_by_turn = {event.turn_id: event for event in outcomes}
    if len(users) != len(outcomes) or len(outcome_by_turn) != len(outcomes):
        raise SQLiteDatabaseFormatError()

    recent_reversed: list[tuple[ConversationEvent, ConversationEvent]] = []
    for user in users:
        outcome = outcome_by_turn.get(user.turn_id)
        if (
            outcome is None
            or user.kind is not ConversationEventKind.USER_MESSAGE
            or outcome.kind is ConversationEventKind.USER_MESSAGE
            or user.session_id != session_id
            or user.conversation_id != conversation_id
            or outcome.session_id != session_id
            or outcome.conversation_id != conversation_id
            or user.sequence >= outcome.sequence
            or (
                outcome.kind is ConversationEventKind.AGENT_REPLY
                and outcome.sender != user.recipient
            )
        ):
            raise SQLiteDatabaseFormatError()
        recent_reversed.append((user, outcome))

    retained_reversed: list[tuple[ConversationEvent, ConversationEvent]] = []
    retained_utf8_bytes = 0
    for turn in recent_reversed:
        if len(retained_reversed) >= CONVERSATION_CONTEXT_POLICY.max_complete_turns:
            break
        turn_utf8_bytes = sum(
            len(event.content.encode("utf-8", errors="strict")) for event in turn
        )
        if (
            retained_utf8_bytes + turn_utf8_bytes
            > CONVERSATION_CONTEXT_POLICY.max_utf8_bytes
        ):
            break
        retained_reversed.append(turn)
        retained_utf8_bytes += turn_utf8_bytes

    retained = tuple(reversed(retained_reversed))
    events = tuple(
        sorted(
            (event for turn in retained for event in turn),
            key=lambda event: event.sequence,
        )
    )
    retained_turns = len(retained)
    omitted_turns = total_complete_turns - retained_turns
    if omitted_turns < 0:
        raise SQLiteDatabaseFormatError()
    return ConversationContextWindow(
        events=events,
        total_complete_turns=total_complete_turns,
        retained_turns=retained_turns,
        omitted_turns=omitted_turns,
        retained_utf8_bytes=retained_utf8_bytes,
        truncated=omitted_turns > 0,
    )


def _search_sqlite_snapshot(
    connection: object,
    *,
    search_index: SQLiteConversationSearchIndex,
    scope: ConversationSearchScope,
    query: ConversationSearchQuery,
    normalized_query: str,
) -> ConversationSearchResult:
    """Construct one exact bounded result from a single main-database snapshot."""

    metadata_sql = _METADATA_SELECT + " WHERE session_id = ?"
    parameters: tuple[object, ...] = (scope.session_id,)
    if not query.include_archived:
        metadata_sql += " AND status = ?"
        parameters += (ConversationStatus.ACTIVE.value,)
    metadata_sql += " ORDER BY created_at, conversation_id LIMIT ?"
    parameters += (CONVERSATION_MEMORY_POLICY.max_scan_conversations + 1,)
    metadata_rows = connection.execute(  # type: ignore[attr-defined]
        metadata_sql,
        parameters,
    ).fetchall()
    metadata = tuple(_metadata_from_row(row) for row in metadata_rows)
    identities = {item.conversation_id for item in metadata}
    if len(identities) != len(metadata) or any(
        item.session_id != scope.session_id for item in metadata
    ):
        raise SQLiteDatabaseFormatError()
    if not query.include_archived and any(
        item.status is ConversationStatus.ARCHIVED for item in metadata
    ):
        raise SQLiteDatabaseFormatError()

    scan_truncated = len(metadata) > CONVERSATION_MEMORY_POLICY.max_scan_conversations
    selected_metadata = metadata[: CONVERSATION_MEMORY_POLICY.max_scan_conversations]
    generation = _lexical_generation(connection)
    try:
        candidate_hints = search_index.candidates(
            source_generation=generation,
            session_id=scope.session_id,
            conversation_ids=tuple(item.conversation_id for item in selected_metadata),
            normalized_query=normalized_query,
            maximum=CONVERSATION_MEMORY_POLICY.max_scan_events,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except SQLiteConversationSearchIndexError:
        candidate_hints = frozenset()

    hits: list[ConversationSearchHit] = []
    scanned_events = 0
    event_budget_exhausted = False
    remaining_candidate_hints = set(candidate_hints or ())
    for item in selected_metadata:
        normalized_subject = normalize_conversation_subject(item.subject)
        raw_match_kind = classify_conversation_literal_subject(
            normalized_subject,
            normalized_query,
        )
        if raw_match_kind is not None:
            hits.append(
                ConversationSearchHit(
                    citation=ConversationCitation(
                        item.session_id,
                        item.conversation_id,
                        None,
                        None,
                        None,
                    ),
                    subject=item.subject,
                    subject_revision=item.subject_revision,
                    status=item.status,
                    updated_at=item.updated_at,
                    match_kind=ConversationSearchMatchKind(raw_match_kind),
                    snippet=build_conversation_literal_snippet(
                        normalize_conversation_literal_text(item.subject),
                        normalized_query,
                        max_characters=(
                            CONVERSATION_MEMORY_POLICY.max_snippet_characters
                        ),
                        max_utf8_bytes=(
                            CONVERSATION_MEMORY_POLICY.max_snippet_utf8_bytes
                        ),
                    ),
                )
            )
            continue

        if event_budget_exhausted:
            scan_truncated = True
            continue
        remaining = CONVERSATION_MEMORY_POLICY.max_scan_events - scanned_events
        event_rows = connection.execute(  # type: ignore[attr-defined]
            _EVENT_SELECT + " WHERE session_id = ? AND conversation_id = ? "
            "ORDER BY sequence LIMIT ?",
            (item.session_id, item.conversation_id, remaining),
        ).fetchall()
        user_events: dict[str, ConversationEvent] = {}
        completed_turns: set[str] = set()
        matched_event: ConversationEvent | None = None
        matched_text = ""
        for expected_sequence, row in enumerate(event_rows, start=1):
            event, searchable = _event_and_searchable_from_row(row)
            if (
                event.session_id != item.session_id
                or event.conversation_id != item.conversation_id
                or event.sequence != expected_sequence
            ):
                raise SQLiteDatabaseFormatError()
            if event.kind is ConversationEventKind.USER_MESSAGE:
                if event.turn_id in user_events:
                    raise SQLiteDatabaseFormatError()
                user_events[event.turn_id] = event
            else:
                user = user_events.get(event.turn_id)
                if (
                    user is None
                    or event.turn_id in completed_turns
                    or (
                        event.kind is ConversationEventKind.AGENT_REPLY
                        and event.sender != user.recipient
                    )
                ):
                    raise SQLiteDatabaseFormatError()
                completed_turns.add(event.turn_id)
            scanned_events += 1
            coordinate = (event.conversation_id, event.sequence)
            remaining_candidate_hints.discard(coordinate)
            if event.kind is ConversationEventKind.RUNTIME_ERROR:
                continue
            if normalized_query in searchable:
                matched_event = event
                matched_text = searchable
                break

        if matched_event is None and len(event_rows) == remaining:
            later = connection.execute(  # type: ignore[attr-defined]
                "SELECT 1 FROM conversation_events WHERE session_id = ? "
                "AND conversation_id = ? AND sequence > ? LIMIT 1",
                (item.session_id, item.conversation_id, len(event_rows)),
            ).fetchone()
            if later is not None:
                if type(later) is not tuple or later != (1,):
                    raise SQLiteDatabaseFormatError()
                scan_truncated = True
        if scanned_events == CONVERSATION_MEMORY_POLICY.max_scan_events:
            event_budget_exhausted = True
        if matched_event is not None:
            hits.append(
                ConversationSearchHit(
                    citation=ConversationCitation(
                        matched_event.session_id,
                        matched_event.conversation_id,
                        matched_event.turn_id,
                        matched_event.sequence,
                        matched_event.kind,
                    ),
                    subject=item.subject,
                    subject_revision=item.subject_revision,
                    status=item.status,
                    updated_at=item.updated_at,
                    match_kind=ConversationSearchMatchKind.TRANSCRIPT,
                    snippet=build_conversation_literal_snippet(
                        matched_text,
                        normalized_query,
                        max_characters=(
                            CONVERSATION_MEMORY_POLICY.max_snippet_characters
                        ),
                        max_utf8_bytes=(
                            CONVERSATION_MEMORY_POLICY.max_snippet_utf8_bytes
                        ),
                    ),
                )
            )

    ordered_hits = tuple(
        sorted(
            hits,
            key=lambda hit: conversation_literal_sort_key(
                hit.match_kind.value,
                normalize_conversation_subject(hit.subject),
                hit.citation.conversation_id,
            ),
        )
    )
    returned_hits = ordered_hits[: query.limit]
    return ConversationSearchResult(
        query=query,
        normalized_query=normalized_query,
        hits=returned_hits,
        matched_conversations=len(ordered_hits),
        scanned_conversations=len(selected_metadata),
        scanned_events=scanned_events,
        scan_truncated=scan_truncated,
        truncated=scan_truncated or len(ordered_hits) > len(returned_hits),
    )


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

    if (
        type(metadata.schema_version) is not int
        or metadata.schema_version != CONVERSATION_METADATA_SCHEMA_VERSION
        or type(metadata.subject_revision) is not int
        or metadata.subject_revision < 1
        or metadata.subject_revision > _SQLITE_SIGNED_INTEGER_MAX
    ):
        raise SQLiteDatabaseFormatError()
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

    if (
        type(event.schema_version) is not int
        or event.schema_version != CONVERSATION_EVENT_SCHEMA_VERSION
        or type(event.sequence) is not int
        or event.sequence < 1
        or event.sequence > _SQLITE_SIGNED_INTEGER_MAX
    ):
        raise SQLiteDatabaseFormatError()
    turn_slot = 0 if event.kind is ConversationEventKind.USER_MESSAGE else 1
    searchable_content = normalize_conversation_literal_text(event.content)
    connection.execute(  # type: ignore[attr-defined]
        "INSERT INTO conversation_events "
        "(session_id, conversation_id, sequence, schema_version, turn_id, "
        "turn_slot, timestamp, sender, recipient, kind, content, "
        "searchable_content) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            searchable_content,
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
    "SQLiteConversationSearch",
    "compose_sqlite_conversation_archive",
    "compose_sqlite_conversation_storage",
    "preflight_sqlite_conversation_storage",
    "provision_sqlite_conversation_storage",
]
