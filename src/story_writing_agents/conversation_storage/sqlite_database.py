"""SQLite database lifecycle owner for optional conversation storage.

The module deliberately loads :mod:`sqlite3` only when an operation is
requested.  It owns the contained database path, the exact version-1 schema,
per-operation connections and transactions, read-only verification, and
create-only provisioning.  Domain construction remains in ``sqlite`` and is
supplied here as a validation callback.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import stat
import struct
import tempfile
from pathlib import Path
from types import TracebackType
from typing import Any, Callable, TypeVar

from ..workspace import Workspace


SQLITE_APPLICATION_ID = 0x53574143
SQLITE_BUSY_TIMEOUT_SECONDS = 5.0
SQLITE_DATABASE_PATH = "discussion_logs/conversation_storage.sqlite3"
SQLITE_MINIMUM_VERSION = (3, 7, 16)
SQLITE_SCHEMA_VERSION = 1

_DATABASE_HEADER_SIZE = 100
_DATABASE_MAGIC = b"SQLite format 3\x00"
_HEADER_WRITE_VERSION_OFFSET = 18
_HEADER_READ_VERSION_OFFSET = 19
_HEADER_ENCODING_OFFSET = 56
_HEADER_USER_VERSION_OFFSET = 60
_HEADER_APPLICATION_ID_OFFSET = 68
_SQLITE_UTF8_ENCODING = 1
_SQLITE_ROLLBACK_JOURNAL_VERSION = 1
_SQLITE_SCHEMA_COOKIE = 1
_SIDECAR_SUFFIXES = ("-journal", "-wal", "-shm")
_TEMPORARY_PREFIX = ".conversation_storage.sqlite3."
_TEMPORARY_SUFFIX = ".tmp"


class _SQLiteDatabaseError(Exception):
    """Base class for content-free failures translated by the public adapter."""


class SQLiteDatabaseMissingError(_SQLiteDatabaseError):
    """The fixed authoritative database has not been provisioned."""


class SQLiteDatabaseUnavailableError(_SQLiteDatabaseError):
    """The host runtime or an operational database resource is unavailable."""


class SQLiteDatabaseFormatError(_SQLiteDatabaseError):
    """The database identity, objects, or authoritative values are invalid."""


class SQLiteDatabaseVersionError(_SQLiteDatabaseError):
    """Recognized conversation storage uses an unsupported schema version."""


class SQLiteDatabaseSecurityError(_SQLiteDatabaseError):
    """The database path or named file identity is unsafe."""


_CREATE_IDENTITY_SQL = """
CREATE TABLE conversation_storage_identity (
    singleton INTEGER NOT NULL PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    schema_fingerprint TEXT NOT NULL,
    CHECK (typeof(singleton) = 'integer' AND singleton = 1),
    CHECK (typeof(schema_version) = 'integer' AND schema_version = 1),
    CHECK (typeof(schema_fingerprint) = 'text' AND length(schema_fingerprint) = 64)
)
""".strip()

_CREATE_CONVERSATIONS_SQL = """
CREATE TABLE conversations (
    session_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    subject TEXT NOT NULL,
    normalized_subject TEXT NOT NULL,
    subject_revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (session_id, conversation_id),
    CHECK (typeof(session_id) = 'text'),
    CHECK (typeof(conversation_id) = 'text'),
    CHECK (typeof(schema_version) = 'integer' AND schema_version = 1),
    CHECK (typeof(subject) = 'text'),
    CHECK (typeof(normalized_subject) = 'text'),
    CHECK (typeof(subject_revision) = 'integer' AND subject_revision >= 1),
    CHECK (typeof(status) = 'text' AND status IN ('active', 'archived')),
    CHECK (typeof(created_at) = 'text'),
    CHECK (typeof(updated_at) = 'text' AND updated_at >= created_at)
)
""".strip()

_CREATE_EVENTS_SQL = """
CREATE TABLE conversation_events (
    session_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    schema_version INTEGER NOT NULL,
    turn_id TEXT NOT NULL,
    turn_slot INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    PRIMARY KEY (session_id, conversation_id, sequence),
    FOREIGN KEY (session_id, conversation_id)
        REFERENCES conversations (session_id, conversation_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CHECK (typeof(session_id) = 'text'),
    CHECK (typeof(conversation_id) = 'text'),
    CHECK (typeof(sequence) = 'integer' AND sequence >= 1),
    CHECK (typeof(schema_version) = 'integer' AND schema_version = 1),
    CHECK (typeof(turn_id) = 'text'),
    CHECK (typeof(turn_slot) = 'integer' AND turn_slot IN (0, 1)),
    CHECK (typeof(timestamp) = 'text'),
    CHECK (typeof(sender) = 'text'),
    CHECK (typeof(recipient) = 'text'),
    CHECK (
        typeof(kind) = 'text'
        AND kind IN ('user_message', 'agent_reply', 'runtime_error')
    ),
    CHECK (typeof(content) = 'text' AND length(CAST(content AS BLOB)) > 0),
    CHECK (
        turn_slot = CASE WHEN kind = 'user_message' THEN 0 ELSE 1 END
    )
)
""".strip()

_CREATE_CONVERSATION_ORDER_INDEX_SQL = """
CREATE INDEX conversations_creation_order
ON conversations (session_id, created_at, conversation_id)
""".strip()

_CREATE_CONVERSATION_STATUS_INDEX_SQL = """
CREATE INDEX conversations_status_creation_order
ON conversations (session_id, status, created_at, conversation_id)
""".strip()

_CREATE_TURN_SLOT_INDEX_SQL = """
CREATE UNIQUE INDEX conversation_events_turn_slots
ON conversation_events (session_id, conversation_id, turn_id, turn_slot)
""".strip()

_CREATE_CONTIGUOUS_TRIGGER_SQL = """
CREATE TRIGGER conversation_events_contiguous_insert
BEFORE INSERT ON conversation_events
FOR EACH ROW
WHEN NEW.sequence != COALESCE(
    (
        SELECT MAX(sequence) + 1
        FROM conversation_events
        WHERE session_id = NEW.session_id
          AND conversation_id = NEW.conversation_id
    ),
    1
)
BEGIN
    SELECT RAISE(ABORT, 'conversation event sequence is not contiguous');
END
""".strip()

_CREATE_USER_BEFORE_OUTCOME_TRIGGER_SQL = """
CREATE TRIGGER conversation_events_user_before_outcome_insert
BEFORE INSERT ON conversation_events
FOR EACH ROW
WHEN NEW.turn_slot = 1
 AND NOT EXISTS (
    SELECT 1
    FROM conversation_events AS user_event
    WHERE user_event.session_id = NEW.session_id
      AND user_event.conversation_id = NEW.conversation_id
      AND user_event.turn_id = NEW.turn_id
      AND user_event.turn_slot = 0
      AND user_event.sequence < NEW.sequence
 )
BEGIN
    SELECT RAISE(ABORT, 'conversation outcome has no preceding user event');
END
""".strip()

_CREATE_ADDRESSED_REPLY_TRIGGER_SQL = """
CREATE TRIGGER conversation_events_addressed_reply_insert
BEFORE INSERT ON conversation_events
FOR EACH ROW
WHEN NEW.kind = 'agent_reply'
 AND NOT EXISTS (
    SELECT 1
    FROM conversation_events AS user_event
    WHERE user_event.session_id = NEW.session_id
      AND user_event.conversation_id = NEW.conversation_id
      AND user_event.turn_id = NEW.turn_id
      AND user_event.turn_slot = 0
      AND user_event.recipient = NEW.sender
 )
BEGIN
    SELECT RAISE(ABORT, 'conversation reply sender is not addressed');
END
""".strip()

_SCHEMA_STATEMENTS = (
    _CREATE_IDENTITY_SQL,
    _CREATE_CONVERSATIONS_SQL,
    _CREATE_EVENTS_SQL,
    _CREATE_CONVERSATION_ORDER_INDEX_SQL,
    _CREATE_CONVERSATION_STATUS_INDEX_SQL,
    _CREATE_TURN_SLOT_INDEX_SQL,
    _CREATE_CONTIGUOUS_TRIGGER_SQL,
    _CREATE_USER_BEFORE_OUTCOME_TRIGGER_SQL,
    _CREATE_ADDRESSED_REPLY_TRIGGER_SQL,
)


def _normalize_schema_sql(value: str) -> str:
    """Return a whitespace-insensitive representation of one schema object."""

    return " ".join(value.split())


_EXPECTED_SCHEMA_MANIFEST = frozenset(
    _normalize_schema_sql(statement) for statement in _SCHEMA_STATEMENTS
)
_SCHEMA_FINGERPRINT = hashlib.sha256(
    ("\n".join(sorted(_EXPECTED_SCHEMA_MANIFEST)) + "\n").encode("utf-8")
).hexdigest()

_ResultT = TypeVar("_ResultT")
_ConnectionOperation = Callable[[Any], _ResultT]
_SnapshotValidator = Callable[[Any], None]
_LeaseValidator = Callable[[], None]
_TargetValidator = Callable[[], None]


class SQLiteDatabase:
    """Own the fixed contained SQLite database and bounded connection lifecycle."""

    __slots__ = ("_database_path", "_workspace_key")

    def __init__(self, workspace: Workspace) -> None:
        if not isinstance(workspace, Workspace):
            raise TypeError("workspace must be a Workspace")
        root = workspace.root
        self._workspace_key = os.fspath(root)
        self._database_path = root.joinpath(SQLITE_DATABASE_PATH)

    @property
    def workspace_key(self) -> str:
        """Return the already-canonical workspace key without I/O."""

        return self._workspace_key

    @property
    def _path(self) -> Path:
        """Return the construction-pinned contained path without I/O."""

        return self._database_path

    def verify_existing(self, validate_snapshot: _SnapshotValidator) -> None:
        """Strictly verify existing storage through a read-only connection."""

        module = _load_and_probe_sqlite()
        self._run_connection(
            module,
            self._path,
            read_only=True,
            exhaustive_verification=True,
            validate_snapshot=validate_snapshot,
            operation=lambda _connection: None,
        )

    def read(
        self,
        operation: _ConnectionOperation[_ResultT],
        *,
        validate_snapshot: _SnapshotValidator,
    ) -> _ResultT:
        """Run one operation after bounded verification on a read-only connection."""

        module = _load_and_probe_sqlite()
        return self._run_connection(
            module,
            self._path,
            read_only=True,
            allow_live_rollback_journal=True,
            validate_snapshot=validate_snapshot,
            operation=operation,
        )

    def write(
        self,
        operation: _ConnectionOperation[_ResultT],
        *,
        validate_snapshot: _SnapshotValidator,
        validate_lease: _LeaseValidator,
    ) -> _ResultT:
        """Run one ``BEGIN IMMEDIATE`` operation with rollback and exact cleanup."""

        validate_lease()
        module = _load_and_probe_sqlite()
        return self._run_connection(
            module,
            self._path,
            read_only=False,
            validate_snapshot=validate_snapshot,
            operation=operation,
            validate_lease=validate_lease,
        )

    def provision(
        self,
        validate_snapshot: _SnapshotValidator,
        *,
        validate_lease: _LeaseValidator,
    ) -> None:
        """Create and atomically install v1 storage, or verify an existing file."""

        validate_lease()
        module = _load_and_probe_sqlite()
        try:
            self._run_connection(
                module,
                self._path,
                read_only=True,
                exhaustive_verification=True,
                validate_snapshot=validate_snapshot,
                operation=lambda _connection: None,
            )
            validate_lease()
            _require_provisioning_candidates(self._path.parent, expected=())
            return
        except SQLiteDatabaseMissingError:
            pass
        except SQLiteDatabaseSecurityError:

            def validate_interrupted_target() -> None:
                self._run_connection(
                    module,
                    self._path,
                    read_only=True,
                    expected_link_count=2,
                    exhaustive_verification=True,
                    validate_snapshot=validate_snapshot,
                    operation=lambda _connection: None,
                )

            _recover_interrupted_provisioning_alias(
                self._path,
                validate_lease=validate_lease,
                validate_target=validate_interrupted_target,
            )
            self._run_connection(
                module,
                self._path,
                read_only=True,
                exhaustive_verification=True,
                validate_snapshot=validate_snapshot,
                operation=lambda _connection: None,
            )
            validate_lease()
            _require_provisioning_candidates(self._path.parent, expected=())
            return

        target = self._path
        parent = target.parent
        if not parent.exists() or not parent.is_dir():
            raise SQLiteDatabaseUnavailableError()
        _reject_sidecars(target)
        _require_provisioning_candidates(parent, expected=())

        temporary_path: Path | None = None
        body_failure: BaseException | None = None
        cleanup_failure: tuple[BaseException, TracebackType | None] | None = None
        try:
            try:
                descriptor, raw_temporary = tempfile.mkstemp(
                    prefix=_TEMPORARY_PREFIX,
                    suffix=_TEMPORARY_SUFFIX,
                    dir=parent,
                )
                temporary_path = Path(raw_temporary)
                os.close(descriptor)
                _require_provisioning_candidates(
                    parent,
                    expected=(temporary_path,),
                )
            except OSError as exc:
                raise SQLiteDatabaseUnavailableError() from exc

            self._create_temporary_database(
                module,
                temporary_path,
                validate_snapshot=validate_snapshot,
            )
            validate_lease()
            _require_provisioning_candidates(
                parent,
                expected=(temporary_path,),
            )
            try:
                os.link(temporary_path, target, follow_symlinks=False)
                _require_provisioning_candidates(
                    parent,
                    expected=(temporary_path,),
                )
                temporary_path.unlink()
                _require_provisioning_candidates(parent, expected=())
            except FileExistsError:
                _require_provisioning_candidates(
                    parent,
                    expected=(temporary_path,),
                )
                self._run_connection(
                    module,
                    target,
                    read_only=True,
                    exhaustive_verification=True,
                    validate_snapshot=validate_snapshot,
                    operation=lambda _connection: None,
                )
                validate_lease()
                _require_provisioning_candidates(
                    parent,
                    expected=(temporary_path,),
                )
                return
            except OSError as exc:
                raise SQLiteDatabaseUnavailableError() from exc

            _verify_named_file(target, expected_identity=None)
            validate_lease()
            _synchronize_file(target)
            _synchronize_directory(parent)
            _require_provisioning_candidates(parent, expected=())
        except BaseException as exc:
            body_failure = exc
            raise
        finally:
            if temporary_path is not None:
                for candidate in (
                    temporary_path,
                    *(Path(f"{temporary_path}{suffix}") for suffix in _SIDECAR_SUFFIXES),
                ):
                    try:
                        candidate.unlink()
                    except FileNotFoundError:
                        pass
                    except BaseException as exc:
                        if cleanup_failure is None:
                            cleanup_failure = (exc, exc.__traceback__)
            if cleanup_failure is not None:
                cleanup, traceback = cleanup_failure
                if body_failure is not None:
                    body_failure.add_note("SQLite provisioning cleanup failed")
                elif isinstance(cleanup, Exception):
                    raise SQLiteDatabaseUnavailableError() from None
                elif not isinstance(cleanup, Exception):
                    raise cleanup.with_traceback(traceback)

    def _create_temporary_database(
        self,
        module: Any,
        path: Path,
        *,
        validate_snapshot: _SnapshotValidator,
    ) -> None:
        """Initialize and fully validate one private, not-yet-installed file."""

        connection: Any | None = None
        body_failure: BaseException | None = None
        cleanup_failure: tuple[BaseException, TracebackType | None] | None = None
        try:
            connection = _connect(module, path, read_only=False)
            _configure_connection(module, connection, verification=False)
            journal = _single_pragma_value(
                module,
                connection,
                "PRAGMA journal_mode = DELETE",
                verification=False,
            )
            if not isinstance(journal, str) or journal.casefold() != "delete":
                raise SQLiteDatabaseUnavailableError()
            _execute(module, connection, "PRAGMA encoding = 'UTF-8'")
            _execute(module, connection, "BEGIN IMMEDIATE")
            for statement in _SCHEMA_STATEMENTS:
                _execute(module, connection, statement)
            _execute(
                module,
                connection,
                "INSERT INTO conversation_storage_identity "
                "(singleton, schema_version, schema_fingerprint) VALUES (?, ?, ?)",
                (1, SQLITE_SCHEMA_VERSION, _SCHEMA_FINGERPRINT),
            )
            _execute(module, connection, f"PRAGMA user_version = {SQLITE_SCHEMA_VERSION}")
            _execute(module, connection, f"PRAGMA schema_version = {_SQLITE_SCHEMA_COOKIE}")
            _execute(module, connection, "COMMIT")
        except BaseException as exc:
            body_failure = exc
            if isinstance(exc, Exception):
                _raise_mapped_sqlite_exception(module, exc)
            raise
        finally:
            if connection is not None:
                if body_failure is not None:
                    try:
                        connection.rollback()
                    except BaseException as exc:
                        cleanup_failure = (exc, exc.__traceback__)
                try:
                    connection.close()
                except BaseException as exc:
                    if cleanup_failure is None:
                        cleanup_failure = (exc, exc.__traceback__)
            if cleanup_failure is not None:
                cleanup, traceback = cleanup_failure
                if body_failure is not None:
                    body_failure.add_note("SQLite temporary database cleanup failed")
                elif isinstance(cleanup, Exception):
                    _raise_mapped_sqlite_exception(module, cleanup)
                else:
                    raise cleanup.with_traceback(traceback)

        _write_application_id(path)
        self._run_connection(
            module,
            path,
            read_only=True,
            exhaustive_verification=True,
            validate_snapshot=validate_snapshot,
            operation=lambda _connection: None,
        )

    def _run_connection(
        self,
        module: Any,
        path: Path,
        *,
        read_only: bool,
        allow_live_rollback_journal: bool = False,
        expected_link_count: int = 1,
        exhaustive_verification: bool = False,
        validate_snapshot: _SnapshotValidator,
        operation: _ConnectionOperation[_ResultT],
        validate_lease: _LeaseValidator | None = None,
    ) -> _ResultT:
        """Own one connection, optional transaction, verification, and cleanup."""

        _reject_symlinked_database_parent(path)
        _validate_sidecars(
            path,
            allow_live_rollback_journal=allow_live_rollback_journal,
        )
        identity = _verify_database_header(
            path,
            expected_link_count=expected_link_count,
        )
        connection: Any | None = None
        transaction_started = False
        committed = False
        result: _ResultT
        body_failure: BaseException | None = None
        cleanup_failure: tuple[BaseException, TracebackType | None] | None = None
        try:
            connection = _connect(module, path, read_only=read_only)
            _verify_named_file(
                path,
                expected_identity=identity,
                expected_link_count=expected_link_count,
            )
            _configure_connection(module, connection, verification=True)
            if read_only:
                _execute(module, connection, "BEGIN")
                transaction_started = True
                if exhaustive_verification:
                    self._verify_database_exhaustive(
                        module,
                        connection,
                        validate_snapshot=validate_snapshot,
                    )
                else:
                    self._verify_database_identity(module, connection)
                result = operation(connection)
                _execute(module, connection, "COMMIT")
                committed = True
            else:
                if exhaustive_verification:
                    self._verify_database_exhaustive(
                        module,
                        connection,
                        validate_snapshot=validate_snapshot,
                    )
                else:
                    self._verify_database_identity(module, connection)
                if validate_lease is None:
                    raise SQLiteDatabaseUnavailableError()
                validate_lease()
                _execute(module, connection, "BEGIN IMMEDIATE")
                transaction_started = True
                result = operation(connection)
                self._verify_database_identity(module, connection)
                validate_lease()
                _execute(module, connection, "COMMIT")
                committed = True
            _verify_named_file(
                path,
                expected_identity=identity,
                expected_link_count=expected_link_count,
            )
        except BaseException as exc:
            body_failure = exc
            if isinstance(exc, Exception):
                _raise_mapped_sqlite_exception(module, exc)
            raise
        finally:
            if connection is not None:
                if transaction_started and not committed:
                    try:
                        connection.rollback()
                    except BaseException as exc:
                        cleanup_failure = (exc, exc.__traceback__)
                try:
                    connection.close()
                except BaseException as exc:
                    if cleanup_failure is None:
                        cleanup_failure = (exc, exc.__traceback__)
            if cleanup_failure is not None:
                cleanup, traceback = cleanup_failure
                if body_failure is not None:
                    body_failure.add_note("SQLite transaction cleanup failed")
                elif isinstance(cleanup, Exception):
                    _raise_mapped_sqlite_exception(module, cleanup)
                else:
                    raise cleanup.with_traceback(traceback)

        _validate_sidecars(
            path,
            allow_live_rollback_journal=allow_live_rollback_journal,
        )
        final_identity = _verify_database_header(
            path,
            expected_link_count=expected_link_count,
        )
        if final_identity != identity:
            raise SQLiteDatabaseSecurityError()
        return result

    @staticmethod
    def _verify_database_identity(
        module: Any,
        connection: Any,
    ) -> None:
        """Verify bounded database and schema identity invariants."""

        try:
            user_version = _single_pragma_value(
                module,
                connection,
                "PRAGMA user_version",
                verification=True,
            )
            if type(user_version) is not int:
                raise SQLiteDatabaseFormatError()
            if user_version != SQLITE_SCHEMA_VERSION:
                raise SQLiteDatabaseVersionError()

            schema_cookie = _single_pragma_value(
                module,
                connection,
                "PRAGMA schema_version",
                verification=True,
            )
            if type(schema_cookie) is not int or schema_cookie != _SQLITE_SCHEMA_COOKIE:
                raise SQLiteDatabaseFormatError()

            identity_rows = _fetchall(
                module,
                connection,
                "SELECT singleton, schema_version, schema_fingerprint "
                "FROM conversation_storage_identity",
                verification=True,
            )
            if len(identity_rows) != 1 or len(identity_rows[0]) != 3:
                raise SQLiteDatabaseFormatError()
            singleton, schema_version, fingerprint = identity_rows[0]
            if type(schema_version) is not int:
                raise SQLiteDatabaseFormatError()
            if schema_version != SQLITE_SCHEMA_VERSION:
                raise SQLiteDatabaseVersionError()
            if (
                type(singleton) is not int
                or singleton != 1
                or type(fingerprint) is not str
                or fingerprint != _SCHEMA_FINGERPRINT
            ):
                raise SQLiteDatabaseFormatError()

            manifest_rows = _fetchall(
                module,
                connection,
                "SELECT sql FROM sqlite_master "
                "WHERE substr(name, 1, 7) != 'sqlite_' "
                "ORDER BY type, name",
                verification=True,
            )
            if any(len(row) != 1 or type(row[0]) is not str for row in manifest_rows):
                raise SQLiteDatabaseFormatError()
            manifest = frozenset(
                _normalize_schema_sql(row[0]) for row in manifest_rows
            )
            if len(manifest_rows) != len(_EXPECTED_SCHEMA_MANIFEST) or (
                manifest != _EXPECTED_SCHEMA_MANIFEST
            ):
                raise SQLiteDatabaseFormatError()
        except (_SQLiteDatabaseError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            if _is_sqlite_exception(module, exc):
                _raise_mapped_sqlite_exception(module, exc, verification=True)
            raise

    @staticmethod
    def _verify_database_exhaustive(
        module: Any,
        connection: Any,
        *,
        validate_snapshot: _SnapshotValidator,
    ) -> None:
        """Verify integrity, FK ownership, and every domain row."""

        SQLiteDatabase._verify_database_identity(module, connection)
        try:
            integrity_rows = _fetchall(
                module,
                connection,
                "PRAGMA integrity_check",
                verification=True,
            )
            if integrity_rows != [("ok",)]:
                raise SQLiteDatabaseFormatError()

            foreign_key_check_rows = _fetchall(
                module,
                connection,
                "PRAGMA foreign_key_check",
                verification=True,
            )
            if foreign_key_check_rows != []:
                raise SQLiteDatabaseFormatError()

            orphan_rows = _fetchall(
                module,
                connection,
                "SELECT event.session_id, event.conversation_id "
                "FROM conversation_events AS event "
                "LEFT JOIN conversations AS conversation "
                "ON conversation.session_id = event.session_id "
                "AND conversation.conversation_id = event.conversation_id "
                "WHERE conversation.session_id IS NULL LIMIT 1",
                verification=True,
            )
            if orphan_rows:
                raise SQLiteDatabaseFormatError()

            validate_snapshot(connection)
        except (_SQLiteDatabaseError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            if _is_sqlite_exception(module, exc):
                _raise_mapped_sqlite_exception(module, exc, verification=True)
            raise


def _load_and_probe_sqlite() -> Any:
    """Load the optional standard-library module and verify host capability."""

    try:
        module = importlib.import_module("sqlite3")
    except Exception:
        raise SQLiteDatabaseUnavailableError() from None
    version = getattr(module, "sqlite_version_info", None)
    if (
        not isinstance(version, tuple)
        or len(version) < 3
        or any(type(item) is not int for item in version[:3])
        or tuple(version[:3]) < SQLITE_MINIMUM_VERSION
    ):
        raise SQLiteDatabaseUnavailableError()
    if not callable(getattr(module, "connect", None)):
        raise SQLiteDatabaseUnavailableError()

    connection: Any | None = None
    body_failure: BaseException | None = None
    try:
        connection = module.connect(":memory:", isolation_level=None)
        connection.execute("PRAGMA foreign_keys = ON")
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()
        if foreign_keys != (1,):
            raise SQLiteDatabaseUnavailableError()
        row = connection.execute("SELECT sqlite_version()").fetchone()
        if (
            not isinstance(row, tuple)
            or len(row) != 1
            or type(row[0]) is not str
        ):
            raise SQLiteDatabaseUnavailableError()
    except BaseException as exc:
        body_failure = exc
        if isinstance(exc, Exception):
            _raise_mapped_sqlite_exception(module, exc)
        raise
    finally:
        if connection is not None:
            try:
                connection.close()
            except BaseException as exc:
                if body_failure is not None:
                    body_failure.add_note("SQLite runtime probe cleanup failed")
                elif isinstance(exc, Exception):
                    _raise_mapped_sqlite_exception(module, exc)
                else:
                    raise
    return module


def _connect(module: Any, path: Path, *, read_only: bool) -> Any:
    """Open one bounded non-creating SQLite connection."""

    mode = "ro" if read_only else "rw"
    uri = f"{path.as_uri()}?mode={mode}"
    try:
        connection = module.connect(
            uri,
            uri=True,
            timeout=SQLITE_BUSY_TIMEOUT_SECONDS,
            isolation_level=None,
        )
        connection.text_factory = _strict_text_factory
        return connection
    except Exception as exc:
        _raise_mapped_sqlite_exception(module, exc)
        raise AssertionError("unreachable")


def _strict_text_factory(value: bytes) -> str:
    """Decode one SQLite TEXT value as strict UTF-8 without leaking content."""

    if type(value) is not bytes:
        raise SQLiteDatabaseFormatError()
    try:
        return value.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise SQLiteDatabaseFormatError() from None


def _configure_connection(
    module: Any,
    connection: Any,
    *,
    verification: bool,
) -> None:
    """Apply and verify per-connection safety and durability settings."""

    _execute(
        module,
        connection,
        f"PRAGMA busy_timeout = {int(SQLITE_BUSY_TIMEOUT_SECONDS * 1000)}",
        verification=verification,
    )
    _execute(
        module,
        connection,
        "PRAGMA foreign_keys = ON",
        verification=verification,
    )
    foreign_keys = _single_pragma_value(
        module,
        connection,
        "PRAGMA foreign_keys",
        verification=verification,
    )
    if type(foreign_keys) is not int or foreign_keys != 1:
        raise SQLiteDatabaseUnavailableError()
    _execute(
        module,
        connection,
        "PRAGMA synchronous = FULL",
        verification=verification,
    )
    synchronous = _single_pragma_value(
        module,
        connection,
        "PRAGMA synchronous",
        verification=verification,
    )
    if type(synchronous) is not int or synchronous != 2:
        raise SQLiteDatabaseUnavailableError()
    journal = _single_pragma_value(
        module,
        connection,
        "PRAGMA journal_mode",
        verification=verification,
    )
    if type(journal) is not str or journal.casefold() != "delete":
        raise SQLiteDatabaseFormatError()
    encoding = _single_pragma_value(
        module,
        connection,
        "PRAGMA encoding",
        verification=verification,
    )
    if encoding != "UTF-8":
        raise SQLiteDatabaseFormatError()


def _execute(
    module: Any,
    connection: Any,
    sql: str,
    parameters: tuple[object, ...] = (),
    *,
    verification: bool = False,
) -> Any:
    """Execute SQL while preventing a raw DB-API exception from escaping."""

    try:
        return connection.execute(sql, parameters)
    except Exception as exc:
        _raise_mapped_sqlite_exception(
            module,
            exc,
            verification=verification,
        )
        raise AssertionError("unreachable")


def _fetchall(
    module: Any,
    connection: Any,
    sql: str,
    parameters: tuple[object, ...] = (),
    *,
    verification: bool = False,
) -> list[tuple[Any, ...]]:
    """Execute and materialize rows without exposing a DB-API failure."""

    try:
        rows = connection.execute(sql, parameters).fetchall()
    except Exception as exc:
        _raise_mapped_sqlite_exception(
            module,
            exc,
            verification=verification,
        )
        raise AssertionError("unreachable")
    if not isinstance(rows, list) or any(not isinstance(row, tuple) for row in rows):
        raise SQLiteDatabaseFormatError()
    return rows


def _single_pragma_value(
    module: Any,
    connection: Any,
    sql: str,
    *,
    verification: bool,
) -> object:
    """Read one exact scalar PRAGMA result."""

    rows = _fetchall(
        module,
        connection,
        sql,
        verification=verification,
    )
    if len(rows) != 1 or len(rows[0]) != 1:
        if verification:
            raise SQLiteDatabaseFormatError()
        raise SQLiteDatabaseUnavailableError()
    return rows[0][0]


def _is_sqlite_exception(module: Any, exc: BaseException) -> bool:
    """Report whether ``exc`` belongs to the dynamically loaded DB-API."""

    error_type = getattr(module, "Error", None)
    return isinstance(error_type, type) and isinstance(exc, error_type)


def _raise_mapped_sqlite_exception(
    module: Any,
    exc: Exception,
    *,
    verification: bool = False,
) -> None:
    """Raise one private content-free category for any DB-API exception."""

    if isinstance(exc, _SQLiteDatabaseError):
        raise exc
    if not _is_sqlite_exception(module, exc):
        raise exc

    code = getattr(exc, "sqlite_errorcode", None)
    primary_code = code & 0xFF if type(code) is int else None
    operational_codes = {
        getattr(module, name, object())
        for name in (
            "SQLITE_BUSY",
            "SQLITE_LOCKED",
            "SQLITE_CANTOPEN",
            "SQLITE_IOERR",
            "SQLITE_FULL",
            "SQLITE_READONLY",
            "SQLITE_PROTOCOL",
            "SQLITE_INTERRUPT",
        )
    }
    if primary_code in operational_codes:
        raise SQLiteDatabaseUnavailableError() from None
    if verification:
        raise SQLiteDatabaseFormatError() from None
    operational_type = getattr(module, "OperationalError", None)
    if isinstance(operational_type, type) and isinstance(exc, operational_type):
        raise SQLiteDatabaseUnavailableError() from None
    database_type = getattr(module, "DatabaseError", None)
    if isinstance(database_type, type) and isinstance(exc, database_type):
        raise SQLiteDatabaseFormatError() from None
    raise SQLiteDatabaseUnavailableError() from None


def _verify_database_header(
    path: Path,
    *,
    expected_link_count: int = 1,
) -> tuple[int, int]:
    """Verify the exact named file and immutable header identity read-only."""

    try:
        named = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        raise SQLiteDatabaseMissingError() from None
    except OSError:
        raise SQLiteDatabaseUnavailableError() from None
    if stat.S_ISLNK(named.st_mode):
        raise SQLiteDatabaseSecurityError()
    named_identity = _meaningful_regular_file_identity(
        named,
        expected_link_count=expected_link_count,
        nonregular_is_security=False,
    )

    flags = os.O_RDONLY
    for name in ("O_CLOEXEC", "O_NOINHERIT", "O_BINARY", "O_NOFOLLOW", "O_NONBLOCK"):
        flags |= getattr(os, name, 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        try:
            os.set_inheritable(descriptor, False)
        except (AttributeError, OSError):
            pass
        opened = os.fstat(descriptor)
        opened_identity = _meaningful_regular_file_identity(
            opened,
            expected_link_count=expected_link_count,
            nonregular_is_security=True,
        )
        if opened_identity != named_identity:
            raise SQLiteDatabaseSecurityError()
        header = os.read(descriptor, _DATABASE_HEADER_SIZE)
    except _SQLiteDatabaseError:
        raise
    except OSError:
        raise SQLiteDatabaseUnavailableError() from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass

    if len(header) != _DATABASE_HEADER_SIZE or header[:16] != _DATABASE_MAGIC:
        raise SQLiteDatabaseFormatError()
    if (
        header[_HEADER_WRITE_VERSION_OFFSET] != _SQLITE_ROLLBACK_JOURNAL_VERSION
        or header[_HEADER_READ_VERSION_OFFSET] != _SQLITE_ROLLBACK_JOURNAL_VERSION
    ):
        raise SQLiteDatabaseFormatError()
    encoding = struct.unpack(">I", header[_HEADER_ENCODING_OFFSET:_HEADER_ENCODING_OFFSET + 4])[0]
    user_version = struct.unpack(">I", header[_HEADER_USER_VERSION_OFFSET:_HEADER_USER_VERSION_OFFSET + 4])[0]
    application_id = struct.unpack(">I", header[_HEADER_APPLICATION_ID_OFFSET:_HEADER_APPLICATION_ID_OFFSET + 4])[0]
    if application_id != SQLITE_APPLICATION_ID:
        raise SQLiteDatabaseFormatError()
    if user_version != SQLITE_SCHEMA_VERSION:
        raise SQLiteDatabaseVersionError()
    if encoding != _SQLITE_UTF8_ENCODING:
        raise SQLiteDatabaseFormatError()
    return named_identity


def _verify_named_file(
    path: Path,
    *,
    expected_identity: tuple[int, int] | None,
    expected_link_count: int = 1,
) -> tuple[int, int]:
    """Require a safe regular named database with the expected link count."""

    try:
        value = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        raise SQLiteDatabaseMissingError() from None
    except OSError:
        raise SQLiteDatabaseUnavailableError() from None
    if stat.S_ISLNK(value.st_mode):
        raise SQLiteDatabaseSecurityError()
    identity = _meaningful_regular_file_identity(
        value,
        expected_link_count=expected_link_count,
        nonregular_is_security=False,
    )
    if expected_identity is not None and identity != expected_identity:
        raise SQLiteDatabaseSecurityError()
    return identity


def _reject_sidecars(path: Path) -> None:
    """Reject every rollback/WAL sidecar without opening or removing it."""

    _validate_sidecars(path, allow_live_rollback_journal=False)


def _recover_interrupted_provisioning_alias(
    target: Path,
    *,
    validate_lease: _LeaseValidator,
    validate_target: _TargetValidator,
) -> None:
    """Remove only one exact owned hard-link alias left by provisioning."""

    validate_lease()
    _reject_symlinked_database_parent(target)
    target_identity = _recovery_file_identity(target)
    parent = target.parent
    candidates = _provisioning_alias_candidates(parent)
    if len(candidates) != 1:
        raise SQLiteDatabaseSecurityError()
    alias = candidates[0]
    if _recovery_file_identity(alias) != target_identity:
        raise SQLiteDatabaseSecurityError()
    _reject_sidecars(target)
    _reject_sidecars(alias)

    validate_target()
    validate_lease()
    _reject_symlinked_database_parent(target)
    if _provisioning_alias_candidates(parent) != (alias,):
        raise SQLiteDatabaseSecurityError()
    _reject_sidecars(target)
    _reject_sidecars(alias)
    if (
        _recovery_file_identity(target) != target_identity
        or _recovery_file_identity(alias) != target_identity
    ):
        raise SQLiteDatabaseSecurityError()
    validate_lease()
    try:
        alias.unlink()
    except FileNotFoundError:
        raise SQLiteDatabaseSecurityError() from None
    except OSError:
        raise SQLiteDatabaseUnavailableError() from None

    _verify_named_file(target, expected_identity=target_identity)
    try:
        os.stat(alias, follow_symlinks=False)
    except FileNotFoundError:
        pass
    except OSError:
        raise SQLiteDatabaseUnavailableError() from None
    else:
        raise SQLiteDatabaseSecurityError()
    validate_lease()
    _synchronize_file(target)
    _synchronize_directory(parent)


def _provisioning_alias_candidates(parent: Path) -> tuple[Path, ...]:
    """Return every contained sibling with the owned temporary-name shape."""

    try:
        with os.scandir(parent) as entries:
            return tuple(
                sorted(
                    (
                        Path(entry.path)
                        for entry in entries
                        if _is_provisioning_temporary_name(entry.name)
                    ),
                    key=os.fspath,
                )
            )
    except OSError:
        raise SQLiteDatabaseUnavailableError() from None


def _require_provisioning_candidates(
    parent: Path,
    *,
    expected: tuple[Path, ...],
) -> None:
    """Require the exact provisioning candidate set at one mutation boundary."""

    if _provisioning_alias_candidates(parent) != expected:
        raise SQLiteDatabaseSecurityError()


def _is_provisioning_temporary_name(name: str) -> bool:
    """Report whether one sibling name has the exact owned mkstemp shape."""

    return (
        name.startswith(_TEMPORARY_PREFIX)
        and name.endswith(_TEMPORARY_SUFFIX)
        and len(name) > len(_TEMPORARY_PREFIX) + len(_TEMPORARY_SUFFIX)
    )


def _recovery_file_identity(path: Path) -> tuple[int, int]:
    """Return one exact regular two-link identity or reject recovery."""

    try:
        value = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        raise SQLiteDatabaseSecurityError() from None
    except OSError:
        raise SQLiteDatabaseUnavailableError() from None
    return _meaningful_regular_file_identity(
        value,
        expected_link_count=2,
        nonregular_is_security=True,
    )


def _meaningful_regular_file_identity(
    value: os.stat_result,
    *,
    expected_link_count: int,
    nonregular_is_security: bool,
) -> tuple[int, int]:
    """Return one meaningful regular-file identity with an exact link count."""

    if not stat.S_ISREG(value.st_mode):
        if nonregular_is_security:
            raise SQLiteDatabaseSecurityError()
        raise SQLiteDatabaseFormatError()
    if value.st_nlink != expected_link_count:
        raise SQLiteDatabaseSecurityError()
    device = value.st_dev
    inode = value.st_ino
    if type(device) is not int or device <= 0 or type(inode) is not int or inode <= 0:
        raise SQLiteDatabaseSecurityError()
    return device, inode


def _reject_symlinked_database_parent(path: Path) -> None:
    """Reject a symlink at the fixed workspace-relative database directory."""

    try:
        value = os.stat(path.parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError:
        raise SQLiteDatabaseUnavailableError() from None
    if stat.S_ISLNK(value.st_mode):
        raise SQLiteDatabaseSecurityError()


def _validate_sidecars(
    path: Path,
    *,
    allow_live_rollback_journal: bool,
) -> None:
    """Validate fixed sidecars, optionally tolerating one safe live journal."""

    for suffix in _SIDECAR_SUFFIXES:
        candidate = Path(f"{path}{suffix}")
        try:
            value = os.stat(candidate, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError:
            raise SQLiteDatabaseUnavailableError() from None
        if suffix == "-journal" and allow_live_rollback_journal:
            if not stat.S_ISREG(value.st_mode):
                raise SQLiteDatabaseFormatError()
            if value.st_nlink != 1:
                raise SQLiteDatabaseSecurityError()
            continue
        raise SQLiteDatabaseFormatError()


def _write_application_id(path: Path) -> None:
    """Set the owned temporary file's header ID, including on SQLite 3.7.15."""

    flags = os.O_RDWR
    for name in ("O_CLOEXEC", "O_NOINHERIT", "O_BINARY", "O_NOFOLLOW"):
        flags |= getattr(os, name, 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise SQLiteDatabaseSecurityError()
        os.lseek(descriptor, _HEADER_APPLICATION_ID_OFFSET, os.SEEK_SET)
        written = os.write(descriptor, struct.pack(">I", SQLITE_APPLICATION_ID))
        if written != 4:
            raise OSError("short SQLite application ID write")
        os.fsync(descriptor)
    except _SQLiteDatabaseError:
        raise
    except OSError:
        raise SQLiteDatabaseUnavailableError() from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _synchronize_file(path: Path) -> None:
    """Synchronize the completed installed file without following symlinks."""

    flags = os.O_RDONLY
    for name in ("O_CLOEXEC", "O_NOINHERIT", "O_BINARY", "O_NOFOLLOW"):
        flags |= getattr(os, name, 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        os.fsync(descriptor)
    except OSError:
        raise SQLiteDatabaseUnavailableError() from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _synchronize_directory(path: Path) -> None:
    """Synchronize the containing directory after create-only installation."""

    flags = os.O_RDONLY
    for name in ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW"):
        flags |= getattr(os, name, 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        os.fsync(descriptor)
    except OSError:
        raise SQLiteDatabaseUnavailableError() from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


__all__ = [
    "SQLITE_APPLICATION_ID",
    "SQLITE_BUSY_TIMEOUT_SECONDS",
    "SQLITE_DATABASE_PATH",
    "SQLITE_MINIMUM_VERSION",
    "SQLITE_SCHEMA_VERSION",
    "SQLiteDatabase",
    "SQLiteDatabaseFormatError",
    "SQLiteDatabaseMissingError",
    "SQLiteDatabaseSecurityError",
    "SQLiteDatabaseUnavailableError",
    "SQLiteDatabaseVersionError",
]
