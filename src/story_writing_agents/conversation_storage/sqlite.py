"""Optional SQLite conversation catalog, event store, and archive adapter."""

from __future__ import annotations

import os
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
    CollaborationHistorySearchHit,
    CollaborationHistorySearchQuery,
    CollaborationHistorySearchResult,
    CollaborationHistorySearchScope,
    ConversationCitation,
    ConversationSearchHit,
    ConversationSearchMatchKind,
    ConversationSearchQuery,
    ConversationSearchResult,
    ConversationSearchScope,
    search_collaboration_history,
)
from ..conversations import (
    CONVERSATION_CONTEXT_POLICY,
    CONVERSATION_EVENT_SCHEMA_VERSION,
    CONVERSATION_METADATA_SCHEMA_VERSION,
    COLLABORATION_EVENT_SCHEMA_VERSION,
    CollaborationAppendError,
    CollaborationAppendFailure,
    CollaborationAppendReceipt,
    CollaborationAppendStatus,
    CollaborationEvent as CollaborationEventV2,
    CollaborationEventBatch,
    CollaborationHistoryProjection,
    CollaborationReadableFieldKey,
    ConversationContextWindow,
    ConversationEvent,
    ConversationEventKind,
    ConversationMetadata,
    ConversationStatus,
    collaboration_batch_digest,
    decode_collaboration_event,
    encode_collaboration_event,
    normalize_conversation_subject,
    project_collaboration_history,
    project_collaboration_readable_history,
    select_bounded_conversation_context,
    validate_collaboration_candidate_batch,
)
from ..conversations.events import ConversationEventAppend
from ..errors import (
    ConversationExistsError,
    ConversationNotFoundError,
    SessionIdError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
    WorkspaceVersionUnsupportedError,
)
from ..workspace.core import Workspace
from .archive import (
    CONVERSATION_ARCHIVE_SCHEMA_VERSION,
    ConversationArchive,
    ConversationArchiveConflictError,
    ConversationArchiveConversation,
    ConversationArchiveSession,
    deserialize_conversation_archive,
    serialize_conversation_archive,
)
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
    probe_sqlite_runtime,
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
_COLLABORATION_EVENT_SELECT = (
    "SELECT schema_version, event_id, session_id, conversation_id, turn_id, "
    "step_id, sequence, timestamp, batch_id, batch_ordinal, batch_size, "
    "batch_digest, event_bytes FROM collaboration_events"
)

_RECENT_CONTEXT_CANDIDATE_TURNS = CONVERSATION_CONTEXT_POLICY.max_complete_turns + 1

ResultT = TypeVar("ResultT")


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
        def read_validated(connection: object) -> ResultT:
            _validate_database_snapshot(connection)
            return operation(connection)

        return _database_call(
            lambda: self._database.read(
                read_validated,
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
        validate_snapshot: Callable[[object], None] | None = None,
    ) -> ResultT:
        """Commit required lexical state, then best-effort refresh derived FTS."""

        snapshot_validator = (
            _validate_database_snapshot
            if validate_snapshot is None
            else validate_snapshot
        )

        def mutate(connection: object) -> tuple[ResultT, int | None]:
            snapshot_validator(connection)
            result = operation(connection)
            effective = True if changed is None else changed(result)
            if type(effective) is not bool:
                raise TypeError("SQLite mutation changed predicate must be boolean")
            generation = _advance_lexical_generation(connection) if effective else None
            return result, generation

        result, generation = _database_call(
            lambda: self._database.write(
                mutate,
                validate_snapshot=snapshot_validator,
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


class _SchemaV1SQLiteConversationEventStore(_SQLiteAdapter):
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


class SQLiteCollaborationEventStore(_SQLiteAdapter):
    """Persist atomic canonical schema-v2 collaboration-history batches."""

    def append_batch(
        self,
        batch: CollaborationEventBatch,
    ) -> CollaborationAppendReceipt:
        """Atomically append one CAS batch or return its stable retry receipt."""

        if type(batch) is not CollaborationEventBatch:
            raise TypeError("batch must be a CollaborationEventBatch")
        first = batch.events[0]
        session, conversation = _validate_identity(
            first.session_id,
            first.conversation_id,
        )

        with self._mutation_coordinator.borrow(
            timeout_seconds=self._mutation_timeout
        ) as lease:

            def validate_lease() -> None:
                _validate_lease(
                    lease,
                    workspace_key=self._database.workspace_key,
                )

            def append_in_transaction(
                connection: object,
            ) -> CollaborationAppendReceipt:
                _require_metadata(connection, session, conversation)
                try:
                    history = _select_collaboration_events(
                        connection,
                        session,
                        conversation,
                    )
                except SQLiteDatabaseFormatError as exc:
                    raise CollaborationAppendError(
                        CollaborationAppendFailure.CORRUPT_HISTORY
                    ) from exc

                validation = validate_collaboration_candidate_batch(history, batch)
                if isinstance(validation, CollaborationAppendReceipt):
                    return validation

                digest = collaboration_batch_digest(batch)
                stored = tuple(
                    CollaborationEventV2(
                        schema_version=COLLABORATION_EVENT_SCHEMA_VERSION,
                        event_id=event.event_id,
                        session_id=event.session_id,
                        conversation_id=event.conversation_id,
                        turn_id=event.turn_id,
                        step_id=event.step_id,
                        sequence=len(history) + ordinal + 1,
                        timestamp=datetime.now(UTC).isoformat(),
                        batch_id=batch.batch_id,
                        batch_ordinal=ordinal,
                        batch_size=len(batch.events),
                        batch_digest=digest,
                        kind=event.kind,
                        sender=event.sender,
                        recipient=event.recipient,
                        payload=event.payload,
                    )
                    for ordinal, event in enumerate(batch.events)
                )
                completed = history + stored
                try:
                    project_collaboration_history(completed)
                except (
                    AssertionError,
                    KeyError,
                    SessionIdError,
                    TypeError,
                    ValueError,
                ) as exc:
                    raise CollaborationAppendError(
                        CollaborationAppendFailure.INVALID_TRANSITION
                    ) from exc
                for event in stored:
                    _insert_collaboration_event(connection, event)
                try:
                    committed = _select_collaboration_events(
                        connection,
                        session,
                        conversation,
                    )
                except SQLiteDatabaseFormatError as exc:
                    raise CollaborationAppendError(
                        CollaborationAppendFailure.CORRUPT_HISTORY
                    ) from exc
                if committed != completed:
                    raise CollaborationAppendError(
                        CollaborationAppendFailure.CORRUPT_HISTORY
                    )
                return CollaborationAppendReceipt(
                    status=CollaborationAppendStatus.APPENDED,
                    batch_id=batch.batch_id,
                    batch_digest=digest,
                    first_sequence=stored[0].sequence,
                    last_sequence=stored[-1].sequence,
                    timestamps=tuple(event.timestamp for event in stored),
                )

            def validate_append_snapshot(connection: object) -> None:
                _validate_database_snapshot(
                    connection,
                    excluded_history_identity=(session, conversation),
                )

            return self._write_under_lease(
                append_in_transaction,
                validate_lease=validate_lease,
                changed=lambda receipt: (
                    receipt.status is CollaborationAppendStatus.APPENDED
                ),
                validate_snapshot=validate_append_snapshot,
            )

    def read(
        self,
        session_id: str,
        conversation_id: str,
    ) -> tuple[CollaborationEventV2, ...]:
        """Read and strictly validate the complete canonical v2 history."""

        session, conversation = _validate_identity(session_id, conversation_id)

        def read_from_connection(
            connection: object,
        ) -> tuple[CollaborationEventV2, ...]:
            _require_metadata(connection, session, conversation)
            return _select_collaboration_events(
                connection,
                session,
                conversation,
            )

        return self._read(read_from_connection)

    def read_context(
        self,
        session_id: str,
        conversation_id: str,
    ) -> CollaborationHistoryProjection:
        """Read and return the exact projection of the complete v2 history."""

        session, conversation = _validate_identity(session_id, conversation_id)

        def read_context_from_connection(
            connection: object,
        ) -> CollaborationHistoryProjection:
            _require_metadata(connection, session, conversation)
            return project_collaboration_history(
                _select_collaboration_events(
                    connection,
                    session,
                    conversation,
                )
            )

        return self._read(read_context_from_connection)


class _SchemaV1SQLiteConversationSearch(_SQLiteAdapter):
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


class SQLiteCollaborationHistorySearch(_SQLiteAdapter):
    """One-snapshot aggregate search over canonical v2 readable fields."""

    def search(
        self,
        scope: CollaborationHistorySearchScope,
        query: CollaborationHistorySearchQuery,
    ) -> CollaborationHistorySearchResult:
        """Search authoritative event fields; catalog subjects never create hits."""

        if type(scope) is not CollaborationHistorySearchScope:
            raise TypeError("collaboration search scope is invalid")
        if type(query) is not CollaborationHistorySearchQuery:
            raise TypeError("collaboration search query is invalid")
        normalized_query = normalize_conversation_literal_query(
            query.text,
            max_characters=CONVERSATION_MEMORY_POLICY.max_query_characters,
        )

        def search_snapshot(connection: object) -> CollaborationHistorySearchResult:
            return _search_collaboration_sqlite_snapshot(
                connection,
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
    ) -> ConversationArchive:
        """Export all metadata and ordered events under an explicit valid lease."""

        def validate() -> None:
            _validate_lease(lease, workspace_key=self._database.workspace_key)

        validate()

        def export_from_connection(
            connection: object,
        ) -> ConversationArchive:
            validate()
            payload = _read_conversation_archive(connection)
            validate()
            return payload

        payload = self._read(export_from_connection)
        validate()
        return payload

    def import_archive(
        self,
        lease: ConversationStorageMutationLease,
        payload: ConversationArchive,
    ) -> ConversationArchiveImportReceipt:
        """Import absent complete records, accept identity, and reject divergence."""

        try:
            records = _canonical_archive_records(payload)
        except SQLiteDatabaseFormatError:
            raise WorkspaceFormatError("invalid SQLite conversation storage") from None

        def validate() -> None:
            _validate_lease(lease, workspace_key=self._database.workspace_key)

        validate()
        effective = False

        def import_in_transaction(
            connection: object,
        ) -> ConversationArchiveImportReceipt:
            nonlocal effective
            _validate_database_snapshot(connection)
            _require_no_legacy_conversation_events(connection)
            imported = False
            for metadata, events, normalized_subject in records:
                current = _select_metadata(
                    connection,
                    metadata.session_id,
                    metadata.conversation_id,
                )
                if current is not None:
                    current_events = _select_collaboration_events(
                        connection,
                        metadata.session_id,
                        metadata.conversation_id,
                    )
                    if current != metadata or current_events != events:
                        raise ConversationArchiveConflictError()
                    continue
                _insert_metadata(
                    connection,
                    metadata,
                    normalized_subject=normalized_subject,
                )
                for event in events:
                    _insert_collaboration_event(connection, event)
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


def probe_sqlite_conversation_storage_runtime() -> ConversationStoragePreflight:
    """Map the active interpreter probe to content-free storage capability."""

    try:
        probe_sqlite_runtime()
    except SQLiteDatabaseUnavailableError:
        capability = ConversationStorageCapability.UNAVAILABLE
    else:
        capability = ConversationStorageCapability.READY
    return ConversationStoragePreflight(
        backend=ConversationStorageBackend.SQLITE,
        capability=capability,
    )


def provision_sqlite_conversation_storage(
    workspace: Workspace | str | os.PathLike[str],
    /,
    *,
    lease: ConversationStorageMutationLease,
) -> ConversationStoragePreflight:
    """Create v2 storage under an active correct-workspace external lease."""

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
    event_store = SQLiteCollaborationEventStore._from_components(
        database,
        coordinator,
        timeout,
        search_index,
    )
    search = SQLiteCollaborationHistorySearch._from_components(
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


def _collaboration_event_from_row(row: object) -> CollaborationEventV2:
    """Decode one canonical v2 BLOB and verify every redundant SQL column."""

    if type(row) is not tuple or len(row) != 13 or type(row[12]) is not bytes:
        raise SQLiteDatabaseFormatError()
    try:
        event = decode_collaboration_event(row[12])
        canonical = encode_collaboration_event(event)
    except (AssertionError, KeyError, SessionIdError, TypeError, ValueError):
        raise SQLiteDatabaseFormatError() from None
    expected = (
        event.schema_version,
        event.event_id,
        event.session_id,
        event.conversation_id,
        event.turn_id,
        event.step_id,
        event.sequence,
        event.timestamp,
        event.batch_id,
        event.batch_ordinal,
        event.batch_size,
        event.batch_digest,
        canonical,
    )
    if row != expected:
        raise SQLiteDatabaseFormatError()
    return event


def _validate_database_snapshot(
    connection: object,
    *,
    excluded_history_identity: tuple[str, str] | None = None,
) -> None:
    """Validate catalog and v2 histories, rejecting every schema-v1 row."""

    _require_no_legacy_conversation_events(connection)
    metadata_rows = connection.execute(  # type: ignore[attr-defined]
        _METADATA_SELECT + " ORDER BY session_id, created_at, conversation_id"
    ).fetchall()
    metadata = tuple(_metadata_from_row(row) for row in metadata_rows)
    identities = {(item.session_id, item.conversation_id) for item in metadata}
    if len(identities) != len(metadata):
        raise SQLiteDatabaseFormatError()
    _validate_all_collaboration_histories(
        connection,
        identities=identities,
        excluded_identity=(
            excluded_history_identity
            if excluded_history_identity in identities
            else None
        ),
    )


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
            "SELECT session_id, conversation_id FROM conversations "
            "ORDER BY session_id, created_at, conversation_id"
        ).fetchall()

        def documents() -> Iterator[SQLiteConversationSearchIndexDocument]:
            for row in rows:
                if (
                    type(row) is not tuple
                    or len(row) != 2
                    or type(row[0]) is not str
                    or type(row[1]) is not str
                ):
                    raise SQLiteDatabaseFormatError()
                records = project_collaboration_readable_history(
                    _select_collaboration_events(connection, row[0], row[1])
                )
                for record in records:
                    for field in record.fields:
                        yield SQLiteConversationSearchIndexDocument(
                            session_id=record.session_id,
                            conversation_id=record.conversation_id,
                            event_id=record.event_id,
                            sequence=record.sequence,
                            field_key=field.key.value,
                            assignment_ordinal=field.assignment_ordinal,
                            input_ordinal=field.input_ordinal,
                            searchable_content=(
                                normalize_conversation_literal_text(field.text)
                            ),
                        )

        document_stream = documents()
        try:
            search_index.rebuild(
                source_generation=source_generation,
                documents=document_stream,
                validate_publication=validate_lease,
            )
        finally:
            try:
                document_stream.close()
            except RuntimeError:
                pass

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
        _validate_all_collaboration_histories(
            connection,
            identities=identities,
        )
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


def _validate_collaboration_event_collection(
    events: tuple[CollaborationEventV2, ...],
    *,
    session_id: str,
    conversation_id: str,
) -> CollaborationHistoryProjection:
    """Verify one exact addressed canonical collaboration history."""

    try:
        projection = project_collaboration_history(events)
    except (AssertionError, KeyError, SessionIdError, TypeError, ValueError):
        raise SQLiteDatabaseFormatError() from None
    if events and (
        projection.session_id != session_id
        or projection.conversation_id != conversation_id
    ):
        raise SQLiteDatabaseFormatError()
    return projection


def _validate_all_collaboration_histories(
    connection: object,
    *,
    identities: set[tuple[str, str]],
    excluded_identity: tuple[str, str] | None = None,
) -> None:
    """Validate every stored collaboration row under an existing catalog row."""

    rows = connection.execute(  # type: ignore[attr-defined]
        _COLLABORATION_EVENT_SELECT + " ORDER BY session_id, conversation_id, sequence"
    ).fetchall()
    events: list[CollaborationEventV2] = []
    for row in rows:
        if excluded_identity is not None and type(row) is tuple and len(row) == 13:
            if (row[2], row[3]) == excluded_identity:
                continue
            try:
                decoded = decode_collaboration_event(row[12])
            except (AssertionError, KeyError, SessionIdError, TypeError, ValueError):
                pass
            else:
                if (decoded.session_id, decoded.conversation_id) == excluded_identity:
                    raise CollaborationAppendError(
                        CollaborationAppendFailure.CORRUPT_HISTORY
                    )
        events.append(_collaboration_event_from_row(row))
    grouped: dict[tuple[str, str], list[CollaborationEventV2]] = {}
    for event in events:
        identity = (event.session_id, event.conversation_id)
        if identity not in identities:
            raise SQLiteDatabaseFormatError()
        grouped.setdefault(identity, []).append(event)
    for identity, history in grouped.items():
        _validate_collaboration_event_collection(
            tuple(history),
            session_id=identity[0],
            conversation_id=identity[1],
        )


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


def _select_collaboration_events(
    connection: object,
    session_id: str,
    conversation_id: str,
) -> tuple[CollaborationEventV2, ...]:
    """Read and fully validate one authoritative schema-v2 history."""

    rows = connection.execute(  # type: ignore[attr-defined]
        _COLLABORATION_EVENT_SELECT
        + " WHERE session_id = ? AND conversation_id = ? ORDER BY sequence",
        (session_id, conversation_id),
    ).fetchall()
    events = tuple(_collaboration_event_from_row(row) for row in rows)
    _validate_collaboration_event_collection(
        events,
        session_id=session_id,
        conversation_id=conversation_id,
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


def _search_collaboration_sqlite_snapshot(
    connection: object,
    *,
    scope: CollaborationHistorySearchScope,
    query: CollaborationHistorySearchQuery,
    normalized_query: str,
) -> CollaborationHistorySearchResult:
    """Search complete v2 histories inside one validated database snapshot."""

    metadata_sql = _METADATA_SELECT + " WHERE session_id = ?"
    parameters: tuple[object, ...] = (scope.session_id,)
    if not scope.include_archived:
        metadata_sql += " AND status = ?"
        parameters += (ConversationStatus.ACTIVE.value,)
    metadata_sql += " ORDER BY created_at, conversation_id LIMIT ?"
    parameters += (CONVERSATION_MEMORY_POLICY.max_scan_conversations + 1,)
    rows = connection.execute(  # type: ignore[attr-defined]
        metadata_sql,
        parameters,
    ).fetchall()
    metadata = tuple(_metadata_from_row(row) for row in rows)
    if len({item.conversation_id for item in metadata}) != len(metadata) or any(
        item.session_id != scope.session_id for item in metadata
    ):
        raise SQLiteDatabaseFormatError()
    if not scope.include_archived and any(
        item.status is ConversationStatus.ARCHIVED for item in metadata
    ):
        raise SQLiteDatabaseFormatError()

    selected = metadata[: CONVERSATION_MEMORY_POLICY.max_scan_conversations]
    scan_truncated = len(selected) != len(metadata)
    remaining_records = CONVERSATION_MEMORY_POLICY.max_scan_events
    hits: list[CollaborationHistorySearchHit] = []
    matched_fields = 0
    scanned_records = 0
    scanned_fields = 0
    exhaustive_query = CollaborationHistorySearchQuery(
        query.text,
        limit=CONVERSATION_MEMORY_POLICY.max_search_hits,
    )
    for metadata_index, item in enumerate(selected):
        events = _select_collaboration_events(
            connection,
            item.session_id,
            item.conversation_id,
        )
        if len(events) > remaining_records:
            scan_truncated = True
            break
        result = search_collaboration_history(events, exhaustive_query)
        hits.extend(result.hits)
        matched_fields += result.matched_fields
        scanned_records += result.scanned_records
        scanned_fields += result.scanned_fields
        remaining_records -= len(events)
        if remaining_records == 0:
            if metadata_index + 1 < len(selected):
                scan_truncated = True
            break

    hits.sort(
        key=lambda hit: (
            hit.citation.session_id,
            hit.citation.conversation_id,
            hit.citation.sequence,
            tuple(CollaborationReadableFieldKey).index(hit.citation.field_key),
            -1
            if hit.citation.assignment_ordinal is None
            else hit.citation.assignment_ordinal,
            -1 if hit.citation.input_ordinal is None else hit.citation.input_ordinal,
        )
    )
    retained = tuple(hits[: query.limit])
    return CollaborationHistorySearchResult(
        query=query,
        normalized_query=normalized_query,
        hits=retained,
        matched_fields=matched_fields,
        scanned_records=scanned_records,
        scanned_fields=scanned_fields,
        scan_truncated=scan_truncated,
        truncated=scan_truncated or matched_fields > len(retained),
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


def _insert_collaboration_event(
    connection: object,
    event: CollaborationEventV2,
) -> None:
    """Insert one canonical v2 event and every indexed redundant fact."""

    if (
        type(event) is not CollaborationEventV2
        or event.schema_version != COLLABORATION_EVENT_SCHEMA_VERSION
        or event.sequence > _SQLITE_SIGNED_INTEGER_MAX
        or event.batch_ordinal > _SQLITE_SIGNED_INTEGER_MAX
        or event.batch_size > _SQLITE_SIGNED_INTEGER_MAX
    ):
        raise SQLiteDatabaseFormatError()
    encoded = encode_collaboration_event(event)
    connection.execute(  # type: ignore[attr-defined]
        "INSERT INTO collaboration_events "
        "(session_id, conversation_id, sequence, schema_version, event_id, "
        "turn_id, step_id, timestamp, batch_id, batch_ordinal, batch_size, "
        "batch_digest, event_bytes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event.session_id,
            event.conversation_id,
            event.sequence,
            event.schema_version,
            event.event_id,
            event.turn_id,
            event.step_id,
            event.timestamp,
            event.batch_id,
            event.batch_ordinal,
            event.batch_size,
            event.batch_digest,
            encoded,
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


def _read_conversation_archive(connection: object) -> ConversationArchive:
    """Return one fully canonical archive from a verified database snapshot."""

    _require_no_legacy_conversation_events(connection)
    metadata_rows = connection.execute(  # type: ignore[attr-defined]
        _METADATA_SELECT + " ORDER BY session_id, created_at, conversation_id"
    ).fetchall()
    metadata = tuple(_metadata_from_row(row) for row in metadata_rows)
    sessions: dict[str, list[ConversationArchiveConversation]] = {}
    for item in metadata:
        events = _select_collaboration_events(
            connection,
            item.session_id,
            item.conversation_id,
        )
        sessions.setdefault(item.session_id, []).append(
            ConversationArchiveConversation(
                schema_version=item.schema_version,
                session_id=item.session_id,
                conversation_id=item.conversation_id,
                subject=item.subject,
                subject_revision=item.subject_revision,
                status=item.status,
                created_at=item.created_at,
                updated_at=item.updated_at,
                events=events,
            )
        )
    archive = ConversationArchive(
        schema_version=CONVERSATION_ARCHIVE_SCHEMA_VERSION,
        sessions=tuple(
            ConversationArchiveSession(
                session_id=session_id,
                conversations=tuple(sessions[session_id]),
            )
            for session_id in sorted(sessions)
        ),
    )
    return deserialize_conversation_archive(serialize_conversation_archive(archive))


def _canonical_archive_records(
    payload: ConversationArchive,
) -> tuple[tuple[ConversationMetadata, tuple[CollaborationEventV2, ...], str], ...]:
    """Preflight every canonical and SQLite-specific sink bound before I/O."""

    canonical = deserialize_conversation_archive(
        serialize_conversation_archive(payload)
    )
    records: list[
        tuple[ConversationMetadata, tuple[CollaborationEventV2, ...], str]
    ] = []
    for session in canonical.sessions:
        for conversation in session.conversations:
            if (
                type(conversation.subject_revision) is not int
                or conversation.subject_revision > _SQLITE_SIGNED_INTEGER_MAX
            ):
                raise SQLiteDatabaseFormatError()
            metadata = ConversationMetadata(
                schema_version=conversation.schema_version,
                session_id=conversation.session_id,
                conversation_id=conversation.conversation_id,
                subject=conversation.subject,
                subject_revision=conversation.subject_revision,
                status=conversation.status,
                created_at=conversation.created_at,
                updated_at=conversation.updated_at,
            )
            events: list[CollaborationEventV2] = []
            for archived_event in conversation.events:
                if (
                    type(archived_event.sequence) is not int
                    or archived_event.sequence > _SQLITE_SIGNED_INTEGER_MAX
                    or archived_event.batch_ordinal > _SQLITE_SIGNED_INTEGER_MAX
                    or archived_event.batch_size > _SQLITE_SIGNED_INTEGER_MAX
                ):
                    raise SQLiteDatabaseFormatError()
                events.append(archived_event)
            canonical_events = tuple(events)
            _validate_collaboration_event_collection(
                canonical_events,
                session_id=metadata.session_id,
                conversation_id=metadata.conversation_id,
            )
            records.append(
                (
                    metadata,
                    canonical_events,
                    normalize_conversation_subject(metadata.subject),
                )
            )
    return tuple(records)


def _require_no_legacy_conversation_events(connection: object) -> None:
    """Reject schema-v1 transcript rows before archive export or import mutation."""

    row = connection.execute(  # type: ignore[attr-defined]
        "SELECT 1 FROM conversation_events LIMIT 1"
    ).fetchone()
    if row is not None:
        if type(row) is not tuple or row != (1,):
            raise SQLiteDatabaseFormatError()
        raise SQLiteDatabaseFormatError()


__all__ = [
    "SQLITE_CONVERSATION_STORAGE_APPLICATION_ID",
    "SQLITE_CONVERSATION_STORAGE_BUSY_TIMEOUT_SECONDS",
    "SQLITE_CONVERSATION_STORAGE_DATABASE_PATH",
    "SQLITE_CONVERSATION_STORAGE_MINIMUM_VERSION",
    "SQLITE_CONVERSATION_STORAGE_SCHEMA_VERSION",
    "SQLiteConversationArchiveAdapter",
    "SQLiteConversationCatalog",
    "SQLiteCollaborationEventStore",
    "SQLiteCollaborationHistorySearch",
    "compose_sqlite_conversation_archive",
    "compose_sqlite_conversation_storage",
    "preflight_sqlite_conversation_storage",
    "probe_sqlite_conversation_storage_runtime",
    "provision_sqlite_conversation_storage",
]
