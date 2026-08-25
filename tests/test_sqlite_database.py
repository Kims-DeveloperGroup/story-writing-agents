"""Private SQLite identity, transaction, and provisioning verification."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from typing import Callable, cast
from unittest import mock

from story_writing_agents import (
    ConversationEventAppend,
    ConversationEventKind,
    Workspace,
    WorkspaceFormatError,
    WorkspaceSecurityError,
    WorkspaceVersionUnsupportedError,
)
from story_writing_agents.agents import LEAD_ORCHESTRATOR
from story_writing_agents.conversation_storage import (
    ConversationStorageCapability,
    ConversationStorageCoordinationError,
    ConversationStorageUnavailableError,
    WorkspaceConversationStorageMutationCoordinator,
)
from story_writing_agents.conversation_storage import (
    sqlite_database as database_module,
)
from story_writing_agents.conversation_storage import (
    composition as composition_module,
)
from story_writing_agents.conversation_storage import sqlite as sqlite_module
from story_writing_agents.conversation_storage.sqlite import (
    compose_sqlite_conversation_storage,
    preflight_sqlite_conversation_storage,
    provision_sqlite_conversation_storage,
)

from tests.sqlite_conversation_storage_fixture import (
    database_path,
    initialized_workspace,
    make_append,
    provision,
    provisioned_services,
    raw_connection,
    storage_snapshot,
)


class _TrackingConnection:
    """Delegate a real connection while recording and injecting lifecycle work."""

    def __init__(
        self,
        delegate: sqlite3.Connection,
        trace: list[str],
        *,
        fail_sql: str | None = None,
        rollback_failure: BaseException | None = None,
        close_failure: BaseException | None = None,
    ) -> None:
        object.__setattr__(self, "delegate", delegate)
        object.__setattr__(self, "trace", trace)
        object.__setattr__(self, "fail_sql", fail_sql)
        object.__setattr__(self, "rollback_failure", rollback_failure)
        object.__setattr__(self, "close_failure", close_failure)

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self.delegate, name)

    def __setattr__(self, name: str, value: object) -> None:
        if name in {
            "delegate",
            "trace",
            "fail_sql",
            "rollback_failure",
            "close_failure",
        }:
            object.__setattr__(self, name, value)
        else:
            setattr(self.delegate, name, value)

    def execute(
        self,
        sql: str,
        parameters: tuple[object, ...] = (),
    ) -> sqlite3.Cursor:
        normalized = " ".join(sql.split())
        self.trace.append(normalized)
        if self.fail_sql is not None and normalized.startswith(self.fail_sql):
            raise sqlite3.OperationalError(f"injected failure at {self.fail_sql}")
        return self.delegate.execute(sql, parameters)

    def rollback(self) -> None:
        self.trace.append("ROLLBACK_METHOD")
        if self.rollback_failure is not None:
            raise self.rollback_failure
        self.delegate.rollback()

    def close(self) -> None:
        self.trace.append("CLOSE_METHOD")
        try:
            self.delegate.close()
        finally:
            if self.close_failure is not None:
                raise self.close_failure


class _ExpirableProvisioningLease:
    """Deterministic lease evidence for one provisioning-boundary failure."""

    def __init__(self, workspace_key: str) -> None:
        self.workspace_key = workspace_key
        self.expired = False

    def validate(self, *, workspace_key: str) -> None:
        if workspace_key != self.workspace_key or self.expired:
            raise ConversationStorageCoordinationError()


def _tracking_connect(
    trace: list[str],
    **options: object,
) -> tuple[Callable[..., _TrackingConnection], list[_TrackingConnection]]:
    """Return one real connection wrapper factory and its opened connections."""

    real_connect = database_module._connect
    connections: list[_TrackingConnection] = []

    def connect(
        module: object,
        path: Path,
        *,
        read_only: bool,
    ) -> _TrackingConnection:
        delegate = real_connect(module, path, read_only=read_only)
        wrapper = _TrackingConnection(delegate, trace, **options)
        connections.append(wrapper)
        return wrapper

    return connect, connections


def _assert_exhaustive_statements(
    test_case: unittest.TestCase,
    trace: list[str],
    *,
    expected_count: int,
) -> None:
    """Assert one complete exhaustive verification statement set per scan."""

    test_case.assertEqual(trace.count("PRAGMA integrity_check"), expected_count)
    test_case.assertEqual(trace.count("PRAGMA foreign_key_check"), expected_count)
    test_case.assertEqual(
        sum(
            "LEFT JOIN conversations AS conversation" in statement
            and "WHERE conversation.session_id IS NULL" in statement
            for statement in trace
        ),
        expected_count,
    )


def _stat_identity_witness(
    value: os.stat_result,
    *,
    device: object,
    inode: object,
    link_count: int | None = None,
) -> os.stat_result:
    """Return a controlled no-follow stat value with an exact identity."""

    return cast(
        os.stat_result,
        mock.Mock(
            st_mode=value.st_mode,
            st_nlink=value.st_nlink if link_count is None else link_count,
            st_dev=device,
            st_ino=inode,
        ),
    )


class SQLiteDatabaseVerificationTests(unittest.TestCase):
    def test_runtime_minimum_is_3716_and_older_or_missing_sqlite_is_unavailable(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        self.assertEqual(database_module.SQLITE_MINIMUM_VERSION, (3, 7, 16))

        class OldSQLite:
            sqlite_version_info = (3, 7, 15)

            @staticmethod
            def connect(*args: object, **kwargs: object) -> None:
                del args, kwargs

        for side_effect in (OldSQLite(), ImportError("sqlite unavailable")):
            with self.subTest(value=type(side_effect).__name__):
                patcher = (
                    mock.patch.object(
                        database_module.importlib,
                        "import_module",
                        return_value=side_effect,
                    )
                    if isinstance(side_effect, OldSQLite)
                    else mock.patch.object(
                        database_module.importlib,
                        "import_module",
                        side_effect=side_effect,
                    )
                )
                with patcher:
                    result = preflight_sqlite_conversation_storage(workspace)
                self.assertIs(
                    result.capability,
                    ConversationStorageCapability.UNAVAILABLE,
                )
                self.assertFalse(database_path(workspace).exists())

    def test_connection_policy_is_verified_inside_each_read_transaction(self) -> None:
        workspace = initialized_workspace(self)
        provision(workspace)
        database = database_module.SQLiteDatabase(workspace)

        def inspect_policy(connection: object) -> tuple[object, ...]:
            return (
                connection.execute("PRAGMA busy_timeout").fetchone()[0],  # type: ignore[attr-defined]
                connection.execute("PRAGMA foreign_keys").fetchone()[0],  # type: ignore[attr-defined]
                connection.execute("PRAGMA synchronous").fetchone()[0],  # type: ignore[attr-defined]
                connection.execute("PRAGMA journal_mode").fetchone()[0],  # type: ignore[attr-defined]
                connection.execute("PRAGMA encoding").fetchone()[0],  # type: ignore[attr-defined]
            )

        self.assertEqual(
            database.read(
                inspect_policy,
                validate_snapshot=sqlite_module._validate_database_snapshot,
            ),
            (5000, 1, 2, "delete", "UTF-8"),
        )

    def test_corrupt_header_and_unsupported_version_are_byte_preserving(self) -> None:
        cases = ("corrupt", "application", "version", "encoding")
        for case in cases:
            with self.subTest(case=case):
                workspace = initialized_workspace(self)
                target = database_path(workspace)
                if case == "corrupt":
                    target.write_bytes(b"not a SQLite database")
                    expected_error = WorkspaceFormatError
                else:
                    provision(workspace)
                    raw = bytearray(target.read_bytes())
                    if case == "application":
                        raw[68:72] = (0).to_bytes(4, "big")
                        expected_error = WorkspaceFormatError
                    elif case == "version":
                        raw[60:64] = (2).to_bytes(4, "big")
                        expected_error = WorkspaceVersionUnsupportedError
                    else:
                        raw[56:60] = (2).to_bytes(4, "big")
                        expected_error = WorkspaceFormatError
                    target.write_bytes(raw)
                before = storage_snapshot(workspace)
                with self.assertRaises(expected_error):
                    preflight_sqlite_conversation_storage(workspace)
                self.assertEqual(storage_snapshot(workspace), before)

    def test_nonregular_and_multiply_linked_database_paths_fail_closed(self) -> None:
        directory_workspace = initialized_workspace(self)
        directory_target = database_path(directory_workspace)
        directory_target.mkdir()
        before = storage_snapshot(directory_workspace)
        with self.assertRaises(WorkspaceFormatError):
            preflight_sqlite_conversation_storage(directory_workspace)
        self.assertEqual(storage_snapshot(directory_workspace), before)

        linked_workspace = initialized_workspace(self)
        provision(linked_workspace)
        target = database_path(linked_workspace)
        duplicate = target.with_name("conversation_storage-copy.sqlite3")
        try:
            os.link(target, duplicate)
        except OSError as exc:
            self.skipTest(f"hard links are unsupported: {exc}")
        before = storage_snapshot(linked_workspace)
        with self.assertRaises(WorkspaceSecurityError):
            preflight_sqlite_conversation_storage(linked_workspace)
        self.assertEqual(storage_snapshot(linked_workspace), before)

    def test_database_name_symlink_is_security_and_preserves_both_workspaces(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        outside = initialized_workspace(self)
        provision(outside)
        target = database_path(workspace)
        outside_target = database_path(outside)
        try:
            os.symlink(outside_target, target)
        except OSError as exc:
            self.skipTest(f"symbolic links are unsupported: {exc}")

        before = storage_snapshot(workspace)
        outside_before = storage_snapshot(outside)
        with self.assertRaises(WorkspaceSecurityError) as caught:
            preflight_sqlite_conversation_storage(workspace)
        self.assertEqual(
            str(caught.exception),
            "unsafe SQLite conversation storage path",
        )
        self.assertIsNone(caught.exception.__cause__)
        self.assertEqual(storage_snapshot(workspace), before)
        self.assertEqual(storage_snapshot(outside), outside_before)

    def test_nonmeaningful_file_identities_fail_closed_at_every_boundary(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        provision(workspace)
        target = database_path(workspace)
        actual = os.stat(target, follow_symlinks=False)
        identity = (actual.st_dev, actual.st_ino)

        invalid_identities = (
            ("zero device", 0, actual.st_ino),
            ("zero inode", actual.st_dev, 0),
            ("boolean device", False, actual.st_ino),
            ("boolean inode", actual.st_dev, True),
            ("negative device", -1, actual.st_ino),
            ("negative inode", actual.st_dev, -1),
            ("noninteger device", "device", actual.st_ino),
            ("noninteger inode", actual.st_dev, None),
        )
        for label, device, inode in invalid_identities:
            with self.subTest(identity=label):
                witness = _stat_identity_witness(
                    actual,
                    device=device,
                    inode=inode,
                )
                with self.assertRaises(
                    database_module.SQLiteDatabaseSecurityError
                ) as caught:
                    database_module._meaningful_regular_file_identity(
                        witness,
                        expected_link_count=1,
                        nonregular_is_security=False,
                    )
                self.assertEqual(str(caught.exception), "")
                self.assertIsNone(caught.exception.__cause__)

        before = storage_snapshot(workspace)
        header_witness = _stat_identity_witness(
            actual,
            device=0,
            inode=actual.st_ino,
        )
        real_stat = database_module.os.stat

        def stat_with_invalid_header(
            path: Path,
            *,
            follow_symlinks: bool = True,
        ) -> os.stat_result:
            if path == target:
                return header_witness
            return real_stat(path, follow_symlinks=follow_symlinks)

        with mock.patch.object(
            database_module.os,
            "stat",
            stat_with_invalid_header,
        ):
            with self.assertRaises(WorkspaceSecurityError) as caught:
                preflight_sqlite_conversation_storage(workspace)
        self.assertEqual(
            str(caught.exception),
            "unsafe SQLite conversation storage path",
        )
        self.assertIsNone(caught.exception.__cause__)

        opened_witness = _stat_identity_witness(
            actual,
            device=actual.st_dev,
            inode=True,
        )
        with mock.patch.object(
            database_module.os,
            "fstat",
            return_value=opened_witness,
        ):
            with self.assertRaises(
                database_module.SQLiteDatabaseSecurityError
            ) as caught:
                database_module._verify_database_header(target)
        self.assertEqual(str(caught.exception), "")
        self.assertIsNone(caught.exception.__cause__)

        named_witness = _stat_identity_witness(
            actual,
            device=False,
            inode=actual.st_ino,
        )
        with mock.patch.object(
            database_module.os,
            "stat",
            return_value=named_witness,
        ):
            with self.assertRaises(
                database_module.SQLiteDatabaseSecurityError
            ) as caught:
                database_module._verify_named_file(
                    target,
                    expected_identity=identity,
                )
        self.assertEqual(str(caught.exception), "")
        self.assertIsNone(caught.exception.__cause__)

        recovery_witness = _stat_identity_witness(
            actual,
            device=actual.st_dev,
            inode=-1,
            link_count=2,
        )
        with mock.patch.object(
            database_module.os,
            "stat",
            return_value=recovery_witness,
        ):
            with self.assertRaises(
                database_module.SQLiteDatabaseSecurityError
            ) as caught:
                database_module._recovery_file_identity(target)
        self.assertEqual(str(caught.exception), "")
        self.assertIsNone(caught.exception.__cause__)

        database = database_module.SQLiteDatabase(workspace)
        real_header_verifier = database_module._verify_database_header
        final_witness = _stat_identity_witness(
            actual,
            device=actual.st_dev,
            inode="inode",
        )
        header_calls = 0

        def verify_header(
            path: Path,
            *,
            expected_link_count: int = 1,
        ) -> tuple[int, int]:
            nonlocal header_calls
            header_calls += 1
            if header_calls == 2:
                with mock.patch.object(
                    database_module.os,
                    "fstat",
                    return_value=final_witness,
                ):
                    return real_header_verifier(
                        path,
                        expected_link_count=expected_link_count,
                    )
            return real_header_verifier(
                path,
                expected_link_count=expected_link_count,
            )

        with mock.patch.object(
            database_module,
            "_verify_database_header",
            verify_header,
        ):
            with self.assertRaises(
                database_module.SQLiteDatabaseSecurityError
            ) as caught:
                database.read(
                    lambda _connection: None,
                    validate_snapshot=sqlite_module._validate_database_snapshot,
                )
        self.assertEqual(header_calls, 2)
        self.assertEqual(str(caught.exception), "")
        self.assertIsNone(caught.exception.__cause__)
        self.assertEqual(storage_snapshot(workspace), before)

    def test_every_sidecar_is_rejected_and_preserved_exactly(self) -> None:
        for suffix in ("-journal", "-wal", "-shm"):
            with self.subTest(suffix=suffix):
                workspace = initialized_workspace(self)
                provision(workspace)
                sidecar = Path(f"{database_path(workspace)}{suffix}")
                sidecar.write_bytes(f"exact-{suffix}-bytes".encode())
                before = storage_snapshot(workspace)
                with self.assertRaises(WorkspaceFormatError):
                    preflight_sqlite_conversation_storage(workspace)
                self.assertEqual(storage_snapshot(workspace), before)

    def test_schema_manifest_fingerprint_and_cookie_drift_fail_closed(self) -> None:
        for case in ("manifest", "fingerprint", "cookie"):
            with self.subTest(case=case):
                workspace = initialized_workspace(self)
                provision(workspace)
                with raw_connection(workspace) as connection:
                    if case == "manifest":
                        connection.execute(
                            "CREATE TABLE unexpected_object (value TEXT)"
                        )
                        connection.execute("PRAGMA schema_version = 1")
                    elif case == "fingerprint":
                        connection.execute(
                            "UPDATE conversation_storage_identity "
                            "SET schema_fingerprint = ? WHERE singleton = 1",
                            ("0" * 64,),
                        )
                    else:
                        connection.execute("PRAGMA schema_version = 2")
                before = storage_snapshot(workspace)
                with self.assertRaises(WorkspaceFormatError):
                    preflight_sqlite_conversation_storage(workspace)
                self.assertEqual(storage_snapshot(workspace), before)

    def test_schema_objects_fingerprint_and_query_plans_are_exact(self) -> None:
        workspace = initialized_workspace(self)
        provision(workspace)
        expected_objects = (
            ("index", "conversation_events_recent_outcomes"),
            ("index", "conversation_events_turn_slots"),
            ("index", "conversations_creation_order"),
            ("index", "conversations_status_creation_order"),
            ("table", "conversation_events"),
            ("table", "conversation_storage_identity"),
            ("table", "conversations"),
            ("trigger", "conversation_events_addressed_reply_insert"),
            ("trigger", "conversation_events_contiguous_insert"),
            ("trigger", "conversation_events_user_before_outcome_insert"),
        )
        expected_fingerprint = (
            "07249791c4c515db4c2b9ee7b5dc208e462ab48d3f9fde82cfbcf3bc768e26ce"
        )

        with raw_connection(workspace) as connection:
            objects = tuple(
                connection.execute(
                    "SELECT type, name FROM sqlite_master "
                    "WHERE substr(name, 1, 7) != 'sqlite_' "
                    "ORDER BY type, name"
                ).fetchall()
            )
            fingerprint = connection.execute(
                "SELECT schema_fingerprint FROM conversation_storage_identity"
            ).fetchone()
            event_order_plan = tuple(
                row[3]
                for row in connection.execute(
                    "EXPLAIN QUERY PLAN "
                    + sqlite_module._EVENT_SELECT
                    + " WHERE session_id = ? AND conversation_id = ? "
                    "ORDER BY sequence",
                    ("session_a", "conversation_a"),
                ).fetchall()
            )
            turn_plan = tuple(
                row[3]
                for row in connection.execute(
                    "EXPLAIN QUERY PLAN "
                    + sqlite_module._EVENT_SELECT
                    + " WHERE session_id = ? AND conversation_id = ? "
                    "AND turn_id = ? ORDER BY turn_slot",
                    ("session_a", "conversation_a", "turn_a"),
                ).fetchall()
            )
            recent_outcome_plan = tuple(
                row[3]
                for row in connection.execute(
                    "EXPLAIN QUERY PLAN SELECT sequence FROM conversation_events "
                    "WHERE session_id = ? AND conversation_id = ? "
                    "AND turn_slot = 1 ORDER BY sequence DESC LIMIT ?",
                    ("session_a", "conversation_a", 25),
                ).fetchall()
            )

        self.assertEqual(objects, expected_objects)
        self.assertEqual(len(objects), 10)
        self.assertNotIn(
            ("index", "conversation_events_sequence_order"),
            objects,
        )
        self.assertEqual(fingerprint, (expected_fingerprint,))
        self.assertEqual(database_module._SCHEMA_FINGERPRINT, expected_fingerprint)
        self.assertTrue(
            any(
                "sqlite_autoindex_conversation_events_1" in row
                for row in event_order_plan
            )
        )
        self.assertTrue(
            any("conversation_events_turn_slots" in row for row in turn_plan)
        )
        self.assertTrue(
            any(
                "conversation_events_recent_outcomes" in row
                for row in recent_outcome_plan
            )
        )
        self.assertFalse(
            any(
                "TEMP B-TREE" in row.upper()
                for row in (*event_order_plan, *turn_plan, *recent_outcome_plan)
            )
        )
        self.assertFalse(
            any(
                "conversation_events_sequence_order" in row
                for row in (*event_order_plan, *turn_plan, *recent_outcome_plan)
            )
        )

    def test_manifest_filter_is_literal_and_only_ignores_sqlite_owned_names(
        self,
    ) -> None:
        baseline = initialized_workspace(self)
        provision(baseline)
        with raw_connection(baseline) as connection:
            owned_names = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE substr(name, 1, 7) = 'sqlite_' ORDER BY name"
                ).fetchall()
            )
        self.assertTrue(owned_names)
        self.assertTrue(all(name.startswith("sqlite_") for name in owned_names))
        self.assertIs(
            preflight_sqlite_conversation_storage(baseline).capability,
            ConversationStorageCapability.READY,
        )

        for name in ("sqliteX", "sqlite%", "sqlite7"):
            with self.subTest(name=name):
                workspace = initialized_workspace(self)
                provision(workspace)
                quoted_name = '"' + name.replace('"', '""') + '"'
                with raw_connection(workspace) as connection:
                    connection.execute(f"CREATE TABLE {quoted_name} (value TEXT)")
                    connection.execute("PRAGMA schema_version = 1")
                    included_names = tuple(
                        row[0]
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master "
                            "WHERE substr(name, 1, 7) != 'sqlite_' ORDER BY name"
                        ).fetchall()
                    )
                self.assertIn(name, included_names)
                before = storage_snapshot(workspace)
                with self.assertRaises(WorkspaceFormatError):
                    preflight_sqlite_conversation_storage(workspace)
                self.assertEqual(storage_snapshot(workspace), before)

    def test_invalid_utf8_text_is_content_free_for_preflight_and_adapters(
        self,
    ) -> None:
        for column in ("subject", "content"):
            with self.subTest(column=column):
                workspace, services = provisioned_services(self)
                metadata = services.catalog.create("session_a", "Subject")
                if column == "content":
                    services.event_store.append(
                        make_append(
                            session_id="session_a",
                            conversation_id=metadata.conversation_id,
                            turn_id="turn_a",
                        )
                    )
                with raw_connection(workspace) as connection:
                    if column == "subject":
                        connection.execute(
                            "UPDATE conversations "
                            "SET subject = CAST(X'73656E74696E656C2D80' AS TEXT) "
                            "WHERE session_id = ? AND conversation_id = ?",
                            ("session_a", metadata.conversation_id),
                        )
                    else:
                        connection.execute(
                            "UPDATE conversation_events "
                            "SET content = CAST(X'73656E74696E656C2D80' AS TEXT) "
                            "WHERE session_id = ? AND conversation_id = ?",
                            ("session_a", metadata.conversation_id),
                        )
                before = storage_snapshot(workspace)

                def adapter_operation() -> object:
                    if column == "subject":
                        return services.catalog.get(
                            "session_a", metadata.conversation_id
                        )
                    return services.event_store.read(
                        "session_a", metadata.conversation_id
                    )

                operations = (
                    lambda: preflight_sqlite_conversation_storage(workspace),
                    adapter_operation,
                )
                for operation in operations:
                    with self.assertRaises(WorkspaceFormatError) as caught:
                        operation()
                    self.assertEqual(
                        str(caught.exception),
                        "invalid SQLite conversation storage",
                    )
                    self.assertNotIn("sentinel", str(caught.exception).casefold())
                    self.assertIsNone(caught.exception.__cause__)
                self.assertEqual(storage_snapshot(workspace), before)

    def test_database_pins_one_root_read_for_every_later_operation(self) -> None:
        class RootProbeWorkspace(Workspace):
            def __init__(self, root: str | os.PathLike[str]) -> None:
                self._root_reads = 0
                self._root_value = Path(root)
                super().__init__(root)

            @property
            def root(self) -> Path:
                self._root_reads += 1
                return self._root_value

            @root.setter
            def root(self, value: Path) -> None:
                self._root_value = value

        temporary = tempfile.TemporaryDirectory(prefix="sqlite-root-probe-")
        self.addCleanup(temporary.cleanup)
        workspace = RootProbeWorkspace(temporary.name).initialize()
        pinned_root = workspace.root
        alternate = initialized_workspace(self)
        alternate_before = storage_snapshot(alternate)
        workspace._root_reads = 0

        database = database_module.SQLiteDatabase(workspace)
        self.assertEqual(workspace._root_reads, 1)
        self.assertEqual(database.workspace_key, os.fspath(pinned_root))
        self.assertEqual(
            database._path,
            pinned_root.joinpath(database_module.SQLITE_DATABASE_PATH),
        )

        workspace.root = alternate.root
        database.provision(
            sqlite_module._validate_database_snapshot,
            validate_lease=lambda: None,
        )
        self.assertTrue(database._path.is_file())
        self.assertFalse(
            alternate.root.joinpath(database_module.SQLITE_DATABASE_PATH).exists()
        )
        self.assertIs(
            sqlite_module._preflight_database(database).capability,
            ConversationStorageCapability.READY,
        )

        def insert_one(connection: object) -> None:
            connection.execute(  # type: ignore[attr-defined]
                "INSERT INTO conversations "
                "(session_id, conversation_id, schema_version, subject, "
                "normalized_subject, subject_revision, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "session_a",
                    "conversation_a",
                    1,
                    "Pinned subject",
                    "pinned subject",
                    1,
                    "active",
                    "2026-08-25T00:00:00+00:00",
                    "2026-08-25T00:00:00+00:00",
                ),
            )

        database.write(
            insert_one,
            validate_snapshot=sqlite_module._validate_database_snapshot,
            validate_lease=lambda: None,
        )
        self.assertEqual(
            database.read(
                lambda connection: connection.execute(  # type: ignore[attr-defined]
                    "SELECT COUNT(*) FROM conversations"
                ).fetchone()[0],
                validate_snapshot=sqlite_module._validate_database_snapshot,
            ),
            1,
        )
        database.verify_existing(sqlite_module._validate_database_snapshot)
        self.assertEqual(workspace._root_reads, 1)
        self.assertEqual(storage_snapshot(alternate), alternate_before)

    def test_integrity_check_failure_is_mapped_without_repair(self) -> None:
        workspace = initialized_workspace(self)
        provision(workspace)
        before = storage_snapshot(workspace)
        real_fetchall = database_module._fetchall

        def injected_fetchall(
            module: object,
            connection: object,
            sql: str,
            parameters: tuple[object, ...] = (),
            *,
            verification: bool = False,
        ) -> list[tuple[object, ...]]:
            if "PRAGMA integrity_check" in sql:
                return [("injected integrity failure",)]
            return real_fetchall(
                module,
                connection,
                sql,
                parameters,
                verification=verification,
            )

        with mock.patch.object(database_module, "_fetchall", injected_fetchall):
            with self.assertRaises(WorkspaceFormatError):
                preflight_sqlite_conversation_storage(workspace)
        self.assertEqual(storage_snapshot(workspace), before)

    def test_verify_existing_and_preflight_remain_exhaustive(self) -> None:
        workspace = initialized_workspace(self)
        provision(workspace)
        database = database_module.SQLiteDatabase(workspace)
        real_snapshot_validator = sqlite_module._validate_database_snapshot

        for pathway in ("verify_existing", "preflight"):
            with self.subTest(pathway=pathway):
                trace: list[str] = []
                snapshot_calls: list[object] = []
                connect, connections = _tracking_connect(trace)

                def validate_snapshot(connection: object) -> None:
                    snapshot_calls.append(connection)
                    real_snapshot_validator(connection)

                with mock.patch.object(database_module, "_connect", connect):
                    if pathway == "verify_existing":
                        database.verify_existing(validate_snapshot)
                    else:
                        with mock.patch.object(
                            sqlite_module,
                            "_validate_database_snapshot",
                            validate_snapshot,
                        ):
                            result = preflight_sqlite_conversation_storage(workspace)
                        self.assertIs(
                            result.capability,
                            ConversationStorageCapability.READY,
                        )

                self.assertEqual(len(connections), 1)
                self.assertEqual(snapshot_calls, [connections[0]])
                _assert_exhaustive_statements(
                    self,
                    trace,
                    expected_count=1,
                )


class SQLiteBoundedAppendTests(unittest.TestCase):
    def test_append_statement_and_turn_row_shape_do_not_grow_with_history(
        self,
    ) -> None:
        traces: list[tuple[str, ...]] = []
        for complete_turns in (0, 24):
            with self.subTest(complete_turns=complete_turns):
                workspace, services = provisioned_services(self)
                metadata = services.catalog.create("session_a", "Bounded append")
                for index in range(complete_turns):
                    turn_id = f"history_{index:02d}"
                    services.event_store.append(
                        make_append(
                            session_id="session_a",
                            conversation_id=metadata.conversation_id,
                            turn_id=turn_id,
                        )
                    )
                    services.event_store.append(
                        make_append(
                            session_id="session_a",
                            conversation_id=metadata.conversation_id,
                            turn_id=turn_id,
                            kind=ConversationEventKind.AGENT_REPLY,
                        )
                    )

                trace: list[str] = []
                selected_sizes: list[int] = []
                connect, _ = _tracking_connect(trace)
                real_select_turn = sqlite_module._select_turn_events

                def tracked_select_turn(
                    connection: object,
                    session_id: str,
                    conversation_id: str,
                    turn_id: str,
                ) -> tuple[object, ...]:
                    events = real_select_turn(
                        connection,
                        session_id,
                        conversation_id,
                        turn_id,
                    )
                    selected_sizes.append(len(events))
                    return events

                with (
                    mock.patch.object(database_module, "_connect", connect),
                    mock.patch.object(
                        sqlite_module,
                        "_select_turn_events",
                        tracked_select_turn,
                    ),
                    mock.patch.object(
                        sqlite_module,
                        "_select_events",
                        side_effect=AssertionError(
                            "append scanned a complete transcript"
                        ),
                    ),
                ):
                    user = services.event_store.append(
                        make_append(
                            session_id="session_a",
                            conversation_id=metadata.conversation_id,
                            turn_id="new_turn",
                        )
                    )
                    reply = services.event_store.append(
                        make_append(
                            session_id="session_a",
                            conversation_id=metadata.conversation_id,
                            turn_id="new_turn",
                            kind=ConversationEventKind.AGENT_REPLY,
                        )
                    )

                self.assertEqual(
                    (user.sequence, reply.sequence),
                    (complete_turns * 2 + 1, complete_turns * 2 + 2),
                )
                self.assertEqual(selected_sizes, [0, 1])
                self.assertEqual(
                    sum(
                        statement.startswith(
                            "SELECT COALESCE(MAX(sequence), 0) FROM conversation_events"
                        )
                        for statement in trace
                    ),
                    2,
                )
                turn_selects = tuple(
                    statement
                    for statement in trace
                    if statement.startswith(
                        "SELECT schema_version, session_id, conversation_id, "
                        "turn_id, sequence, timestamp, sender, recipient, kind, "
                        "content, searchable_content, turn_slot "
                        "FROM conversation_events"
                    )
                    and "AND turn_id = ? ORDER BY turn_slot" in statement
                )
                self.assertEqual(len(turn_selects), 2)
                self.assertTrue(
                    all(
                        "AND turn_id = ? ORDER BY turn_slot" in statement
                        for statement in turn_selects
                    )
                )
                self.assertFalse(
                    any("ORDER BY sequence" in statement for statement in trace)
                )
                _assert_exhaustive_statements(self, trace, expected_count=0)
                traces.append(tuple(trace))

        self.assertEqual(traces[0], traces[1])

    def test_bounded_append_preserves_pairing_and_rejects_malformed_turns(
        self,
    ) -> None:
        workspace, services = provisioned_services(self)
        metadata = services.catalog.create("session_a", "Turn rules")
        identity = {
            "session_id": "session_a",
            "conversation_id": metadata.conversation_id,
            "turn_id": "turn_a",
        }

        with self.assertRaises(ValueError):
            services.event_store.append(
                make_append(**identity, kind=ConversationEventKind.AGENT_REPLY)
            )
        user = services.event_store.append(make_append(**identity))
        with self.assertRaises(ValueError):
            services.event_store.append(make_append(**identity))
        mismatched_reply = ConversationEventAppend(
            **identity,
            sender=LEAD_ORCHESTRATOR,
            recipient="user",
            kind=ConversationEventKind.AGENT_REPLY,
            content="mismatched",
        )
        with self.assertRaises(ValueError):
            services.event_store.append(mismatched_reply)
        reply = services.event_store.append(
            make_append(**identity, kind=ConversationEventKind.AGENT_REPLY)
        )
        with self.assertRaises(ValueError):
            services.event_store.append(
                make_append(
                    **identity,
                    kind=ConversationEventKind.RUNTIME_ERROR,
                )
            )
        self.assertEqual((user.sequence, reply.sequence), (1, 2))

        with raw_connection(workspace) as connection:
            connection.execute(
                "UPDATE conversation_events SET sender = ? "
                "WHERE session_id = ? AND conversation_id = ? AND sequence = 2",
                (
                    LEAD_ORCHESTRATOR,
                    "session_a",
                    metadata.conversation_id,
                ),
            )
        before = storage_snapshot(workspace)
        with self.assertRaises(WorkspaceFormatError):
            services.event_store.append(
                make_append(
                    **identity,
                    kind=ConversationEventKind.RUNTIME_ERROR,
                )
            )
        self.assertEqual(storage_snapshot(workspace), before)

        turn_sql = (
            "SELECT sequence, turn_id, turn_slot, kind, sender, recipient, content "
            "FROM conversation_events WHERE session_id = ? AND conversation_id = ? "
            "AND turn_id = ? ORDER BY turn_slot"
        )
        turn_parameters = (
            identity["session_id"],
            identity["conversation_id"],
            identity["turn_id"],
        )
        with raw_connection(workspace) as connection:
            connection.execute(
                "DELETE FROM conversation_events WHERE session_id = ? "
                "AND conversation_id = ? AND turn_id = ? AND turn_slot = 0",
                turn_parameters,
            )
            outcome_only = connection.execute(
                turn_sql,
                turn_parameters,
            ).fetchall()
        self.assertEqual(len(outcome_only), 1)
        self.assertEqual(outcome_only[0][2:4], (1, "agent_reply"))
        before = storage_snapshot(workspace)

        with self.assertRaises(WorkspaceFormatError) as caught:
            services.event_store.append(make_append(**identity))

        self.assertEqual(
            str(caught.exception),
            "invalid SQLite conversation storage",
        )
        self.assertIsNone(caught.exception.__cause__)
        self.assertEqual(storage_snapshot(workspace), before)
        with raw_connection(workspace) as connection:
            after = connection.execute(turn_sql, turn_parameters).fetchall()
        self.assertEqual(after, outcome_only)


class SQLiteDatabaseTransactionTests(unittest.TestCase):
    def _tracking_connect(
        self,
        trace: list[str],
        **options: object,
    ) -> tuple[Callable[..., _TrackingConnection], list[_TrackingConnection]]:
        return _tracking_connect(trace, **options)

    def test_routine_read_and_write_omit_exhaustive_verification(self) -> None:
        workspace = initialized_workspace(self)
        provision(workspace)
        database = database_module.SQLiteDatabase(workspace)
        snapshot_calls: list[object] = []

        def reject_snapshot(connection: object) -> None:
            snapshot_calls.append(connection)
            raise AssertionError("routine operation reconstructed the global snapshot")

        read_trace: list[str] = []
        read_connect, _ = _tracking_connect(read_trace)
        with mock.patch.object(database_module, "_connect", read_connect):
            result = database.read(
                lambda connection: connection.execute("SELECT 1").fetchone()[0],
                validate_snapshot=reject_snapshot,
            )
        self.assertEqual(result, 1)
        self.assertEqual(snapshot_calls, [])
        _assert_exhaustive_statements(self, read_trace, expected_count=0)
        self.assertEqual(read_trace.count("PRAGMA user_version"), 1)
        self.assertEqual(
            sum(
                statement.startswith(
                    "SELECT singleton, schema_version, schema_fingerprint"
                )
                for statement in read_trace
            ),
            1,
        )
        self.assertEqual(
            sum(
                statement.startswith("SELECT sql FROM sqlite_master")
                for statement in read_trace
            ),
            1,
        )

        write_trace: list[str] = []
        write_connect, _ = _tracking_connect(write_trace)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            with mock.patch.object(database_module, "_connect", write_connect):
                database.write(
                    lambda connection: connection.execute("SELECT 1").fetchone()[0],
                    validate_snapshot=reject_snapshot,
                    validate_lease=lambda: lease.validate(
                        workspace_key=coordinator.workspace_key
                    ),
                )
        self.assertEqual(snapshot_calls, [])
        _assert_exhaustive_statements(self, write_trace, expected_count=0)
        self.assertEqual(write_trace.count("PRAGMA user_version"), 2)
        self.assertEqual(
            sum(
                statement.startswith(
                    "SELECT singleton, schema_version, schema_fingerprint"
                )
                for statement in write_trace
            ),
            2,
        )
        self.assertEqual(
            sum(
                statement.startswith("SELECT sql FROM sqlite_master")
                for statement in write_trace
            ),
            2,
        )
        self.assertIn("BEGIN IMMEDIATE", write_trace)
        self.assertIn("COMMIT", write_trace)

    def test_caller_unicode_decode_error_is_not_misclassified(self) -> None:
        workspace = initialized_workspace(self)
        provision(workspace)
        database = database_module.SQLiteDatabase(workspace)
        failure = UnicodeDecodeError(
            "utf-8",
            b"caller-sentinel-\x80",
            16,
            17,
            "caller-owned failure",
        )

        with self.assertRaises(UnicodeDecodeError) as caught:
            database.read(
                lambda _connection: _raise(failure),
                validate_snapshot=sqlite_module._validate_database_snapshot,
            )
        self.assertIs(caught.exception, failure)
        self.assertIn("caller-owned failure", str(caught.exception))

    def test_read_uses_begin_snapshot_operation_commit_and_close_order(self) -> None:
        workspace = initialized_workspace(self)
        provision(workspace)
        trace: list[str] = []
        connect, connections = self._tracking_connect(trace)
        database = database_module.SQLiteDatabase(workspace)

        def operation(connection: object) -> int:
            trace.append("READ_OPERATION")
            return connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[
                0
            ]  # type: ignore[attr-defined,no-any-return]

        with mock.patch.object(database_module, "_connect", connect):
            result = database.read(
                operation,
                validate_snapshot=sqlite_module._validate_database_snapshot,
            )
        self.assertEqual(result, 0)
        self.assertEqual(len(connections), 1)
        begin = trace.index("BEGIN")
        verification = next(
            index for index, value in enumerate(trace) if value == "PRAGMA user_version"
        )
        operation_index = trace.index("READ_OPERATION")
        commit = trace.index("COMMIT")
        close = trace.index("CLOSE_METHOD")
        self.assertLess(begin, verification)
        self.assertLess(verification, operation_index)
        self.assertLess(operation_index, commit)
        self.assertLess(commit, close)
        self.assertNotIn("ROLLBACK_METHOD", trace)

    def test_read_snapshot_is_stable_while_a_concurrent_writer_waits_to_commit(
        self,
    ) -> None:
        workspace, services = provisioned_services(self)
        services.catalog.create("session_a", "Initial")
        database = database_module.SQLiteDatabase(workspace)
        writer_updated = threading.Event()
        reader_observed = threading.Event()
        writer_done = threading.Event()
        failures: list[BaseException] = []
        observations: list[tuple[str, str]] = []

        def raw_writer() -> None:
            try:
                connection = sqlite3.connect(
                    database_path(workspace),
                    timeout=5.0,
                    isolation_level=None,
                )
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(
                        "UPDATE conversations SET subject = ?, normalized_subject = ?",
                        ("Concurrent", "concurrent"),
                    )
                    writer_updated.set()
                    if not reader_observed.wait(timeout=5.0):
                        raise AssertionError("reader did not observe its snapshot")
                    connection.execute("COMMIT")
                    writer_done.set()
                finally:
                    connection.close()
            except BaseException as exc:
                failures.append(exc)
                writer_updated.set()
                writer_done.set()

        def read_operation(connection: object) -> tuple[str, str]:
            first = connection.execute("SELECT subject FROM conversations").fetchone()[
                0
            ]  # type: ignore[attr-defined]
            thread = threading.Thread(target=raw_writer)
            thread.start()
            self.addCleanup(lambda: thread.join(timeout=5.0))
            if not writer_updated.wait(timeout=5.0):
                raise AssertionError("writer did not update")
            second = connection.execute("SELECT subject FROM conversations").fetchone()[
                0
            ]  # type: ignore[attr-defined]
            observations.append((first, second))
            reader_observed.set()
            self.assertFalse(writer_done.is_set())
            return first, second

        result = database.read(
            read_operation,
            validate_snapshot=sqlite_module._validate_database_snapshot,
        )
        self.assertTrue(writer_done.wait(timeout=5.0))
        self.assertEqual(result, ("Initial", "Initial"))
        self.assertEqual(observations, [("Initial", "Initial")])
        self.assertEqual(failures, [])

    def test_write_uses_begin_immediate_reverify_commit_and_close_order(self) -> None:
        workspace, services = provisioned_services(self)
        metadata = services.catalog.create("session_a", "Before")
        trace: list[str] = []
        connect, _ = self._tracking_connect(trace)
        database = database_module.SQLiteDatabase(workspace)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)

        def operation(connection: object) -> None:
            trace.append("WRITE_OPERATION")
            connection.execute(  # type: ignore[attr-defined]
                "UPDATE conversations SET subject = ?, normalized_subject = ? "
                "WHERE session_id = ? AND conversation_id = ?",
                ("After", "after", "session_a", metadata.conversation_id),
            )

        with coordinator.borrow(timeout_seconds=2.0) as lease:
            with mock.patch.object(database_module, "_connect", connect):
                database.write(
                    operation,
                    validate_snapshot=sqlite_module._validate_database_snapshot,
                    validate_lease=lambda: lease.validate(
                        workspace_key=coordinator.workspace_key
                    ),
                )
        begin = trace.index("BEGIN IMMEDIATE")
        operation_index = trace.index("WRITE_OPERATION")
        commit = trace.index("COMMIT")
        self.assertLess(begin, operation_index)
        self.assertLess(operation_index, commit)
        self.assertGreater(
            sum(value == "PRAGMA user_version" for value in trace),
            1,
        )
        self.assertEqual(trace[-1], "CLOSE_METHOD")
        self.assertNotIn("ROLLBACK_METHOD", trace)

    def test_exception_and_control_flow_failures_roll_back_and_close(self) -> None:
        for failure in (
            RuntimeError("operation failed"),
            KeyboardInterrupt("interrupted"),
            SystemExit("stopped"),
        ):
            with self.subTest(failure=type(failure).__name__):
                workspace = initialized_workspace(self)
                provision(workspace)
                trace: list[str] = []
                connect, _ = self._tracking_connect(trace)
                database = database_module.SQLiteDatabase(workspace)
                coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)

                def operation(connection: object) -> None:
                    connection.execute("SELECT 1")  # type: ignore[attr-defined]
                    raise failure

                with coordinator.borrow(timeout_seconds=2.0) as lease:
                    with mock.patch.object(database_module, "_connect", connect):
                        with self.assertRaises(type(failure)) as raised:
                            database.write(
                                operation,
                                validate_snapshot=sqlite_module._validate_database_snapshot,
                                validate_lease=lambda: lease.validate(
                                    workspace_key=coordinator.workspace_key
                                ),
                            )
                self.assertIs(raised.exception, failure)
                self.assertIn("ROLLBACK_METHOD", trace)
                self.assertEqual(trace[-1], "CLOSE_METHOD")

    def test_commit_failure_rolls_back_closes_and_maps_to_unavailable(self) -> None:
        workspace = initialized_workspace(self)
        provision(workspace)
        trace: list[str] = []
        connect, _ = self._tracking_connect(trace, fail_sql="COMMIT")
        database = database_module.SQLiteDatabase(workspace)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            with mock.patch.object(database_module, "_connect", connect):
                with self.assertRaises(database_module.SQLiteDatabaseUnavailableError):
                    database.write(
                        lambda connection: connection.execute("SELECT 1"),
                        validate_snapshot=sqlite_module._validate_database_snapshot,
                        validate_lease=lambda: lease.validate(
                            workspace_key=coordinator.workspace_key
                        ),
                    )
        self.assertIn("ROLLBACK_METHOD", trace)
        self.assertEqual(trace[-1], "CLOSE_METHOD")

    def test_rollback_failure_never_suppresses_the_primary_failure(self) -> None:
        workspace = initialized_workspace(self)
        provision(workspace)
        trace: list[str] = []
        cleanup = RuntimeError("rollback failed")
        connect, _ = self._tracking_connect(trace, rollback_failure=cleanup)
        database = database_module.SQLiteDatabase(workspace)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        primary = KeyboardInterrupt("primary")
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            with mock.patch.object(database_module, "_connect", connect):
                with self.assertRaises(KeyboardInterrupt) as raised:
                    database.write(
                        lambda _connection: _raise(primary),
                        validate_snapshot=sqlite_module._validate_database_snapshot,
                        validate_lease=lambda: lease.validate(
                            workspace_key=coordinator.workspace_key
                        ),
                    )
        self.assertIs(raised.exception, primary)
        self.assertIn("SQLite transaction cleanup failed", raised.exception.__notes__)
        self.assertEqual(trace[-1], "CLOSE_METHOD")

    def test_postcommit_close_failure_leaves_a_complete_recoverable_write(self) -> None:
        workspace, services = provisioned_services(self)
        trace: list[str] = []
        close_failure = sqlite3.OperationalError("close failed")
        connect, _ = self._tracking_connect(trace, close_failure=close_failure)
        database = database_module.SQLiteDatabase(workspace)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            with mock.patch.object(database_module, "_connect", connect):
                with self.assertRaises(database_module.SQLiteDatabaseUnavailableError):
                    database.write(
                        lambda connection: connection.execute(
                            "INSERT INTO conversations "
                            "(session_id, conversation_id, schema_version, subject, "
                            "normalized_subject, subject_revision, status, created_at, "
                            "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                "session_a",
                                "close_failure_record",
                                1,
                                "Complete",
                                "complete",
                                1,
                                "active",
                                "2026-08-25T00:00:00+00:00",
                                "2026-08-25T00:00:00+00:00",
                            ),
                        ),
                        validate_snapshot=sqlite_module._validate_database_snapshot,
                        validate_lease=lambda: lease.validate(
                            workspace_key=coordinator.workspace_key
                        ),
                    )
        self.assertNotIn("ROLLBACK_METHOD", trace)
        fresh = compose_sqlite_conversation_storage(workspace)
        self.assertEqual(
            fresh.catalog.get("session_a", "close_failure_record").subject,
            "Complete",
        )

    def test_busy_lock_maps_to_public_unavailable_and_recovers_without_retry(
        self,
    ) -> None:
        workspace, services = provisioned_services(self)
        blocker = sqlite3.connect(database_path(workspace), isolation_level=None)
        try:
            blocker.execute("BEGIN IMMEDIATE")
            with mock.patch.object(
                database_module,
                "SQLITE_BUSY_TIMEOUT_SECONDS",
                0.01,
            ):
                with self.assertRaises(ConversationStorageUnavailableError):
                    services.catalog.create("session_a", "Busy")
        finally:
            blocker.rollback()
            blocker.close()
        created = services.catalog.create("session_a", "Recovered")
        self.assertEqual(created.subject, "Recovered")


class SQLiteDatabaseProvisioningTests(unittest.TestCase):
    def test_provision_rejects_orphaned_temporary_sidecars_without_change(
        self,
    ) -> None:
        for suffix in ("-journal", "-wal", "-shm"):
            with self.subTest(suffix=suffix):
                workspace = initialized_workspace(self)
                target = database_path(workspace)
                sidecar = target.with_name(
                    f".conversation_storage.sqlite3.orphan.tmp{suffix}"
                )
                sidecar_bytes = f"orphan temporary {suffix} bytes".encode()
                sidecar.write_bytes(sidecar_bytes)
                before = storage_snapshot(workspace)
                coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)

                for attempt in range(2):
                    with self.subTest(suffix=suffix, attempt=attempt):
                        with coordinator.borrow(timeout_seconds=2.0) as lease:
                            with self.assertRaises(WorkspaceSecurityError) as caught:
                                provision_sqlite_conversation_storage(
                                    workspace,
                                    lease=lease,
                                )
                        self.assertEqual(
                            str(caught.exception),
                            "unsafe SQLite conversation storage path",
                        )
                        self.assertIsNone(caught.exception.__cause__)
                        self.assertFalse(target.exists())
                        self.assertEqual(storage_snapshot(workspace), before)
                        self.assertEqual(sidecar.read_bytes(), sidecar_bytes)
                        self.assertEqual(
                            list(target.parent.glob(".conversation_storage.sqlite3.*")),
                            [sidecar],
                        )

    def test_provision_rejects_preexisting_temporary_residue_without_change(
        self,
    ) -> None:
        template = initialized_workspace(self)
        provision(template)
        candidate_bytes = database_path(template).read_bytes()

        for target_state in ("missing", "existing"):
            with self.subTest(target=target_state):
                workspace = initialized_workspace(self)
                if target_state == "existing":
                    provision(workspace)
                target = database_path(workspace)
                candidate = target.with_name(
                    ".conversation_storage.sqlite3.stale-before-link.tmp"
                )
                candidate.write_bytes(candidate_bytes)
                before = storage_snapshot(workspace)
                coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)

                for attempt in range(2):
                    with self.subTest(target=target_state, attempt=attempt):
                        with coordinator.borrow(timeout_seconds=2.0) as lease:
                            with self.assertRaises(WorkspaceSecurityError) as caught:
                                provision_sqlite_conversation_storage(
                                    workspace,
                                    lease=lease,
                                )
                        self.assertEqual(
                            str(caught.exception),
                            "unsafe SQLite conversation storage path",
                        )
                        self.assertIsNone(caught.exception.__cause__)
                        self.assertEqual(storage_snapshot(workspace), before)

    def test_provision_rejects_temporary_residue_racing_after_initial_scan(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        target = database_path(workspace)
        candidate = target.with_name(
            ".conversation_storage.sqlite3.raced-before-link.tmp"
        )
        candidate_bytes = b"preserve raced provisioning residue"
        real_mkstemp = tempfile.mkstemp

        def mkstemp_with_residue(
            suffix: str | None = None,
            prefix: str | None = None,
            dir: str | os.PathLike[str] | None = None,
            text: bool = False,
        ) -> tuple[int, str]:
            descriptor, temporary = real_mkstemp(
                suffix=suffix,
                prefix=prefix,
                dir=dir,
                text=text,
            )
            candidate.write_bytes(candidate_bytes)
            return descriptor, temporary

        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with mock.patch.object(
            tempfile,
            "mkstemp",
            side_effect=mkstemp_with_residue,
        ):
            with coordinator.borrow(timeout_seconds=2.0) as lease:
                with self.assertRaises(WorkspaceSecurityError) as caught:
                    provision_sqlite_conversation_storage(workspace, lease=lease)

        self.assertEqual(
            str(caught.exception),
            "unsafe SQLite conversation storage path",
        )
        self.assertIsNone(caught.exception.__cause__)
        self.assertFalse(target.exists())
        self.assertEqual(candidate.read_bytes(), candidate_bytes)
        self.assertEqual(
            list(target.parent.glob(".conversation_storage.sqlite3.*.tmp")),
            [candidate],
        )

    def test_provision_preserves_temporary_sidecar_racing_after_mkstemp(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        target = database_path(workspace)
        sidecar_bytes = b"preserve raced temporary sidecar"
        created: list[tuple[Path, Path]] = []
        real_mkstemp = tempfile.mkstemp

        def mkstemp_with_sidecar(
            suffix: str | None = None,
            prefix: str | None = None,
            dir: str | os.PathLike[str] | None = None,
            text: bool = False,
        ) -> tuple[int, str]:
            descriptor, temporary = real_mkstemp(
                suffix=suffix,
                prefix=prefix,
                dir=dir,
                text=text,
            )
            temporary_path = Path(temporary)
            sidecar = Path(f"{temporary}-wal")
            sidecar.write_bytes(sidecar_bytes)
            created.append((temporary_path, sidecar))
            return descriptor, temporary

        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with mock.patch.object(
            tempfile,
            "mkstemp",
            side_effect=mkstemp_with_sidecar,
        ):
            with coordinator.borrow(timeout_seconds=2.0) as lease:
                with self.assertRaises(WorkspaceSecurityError) as caught:
                    provision_sqlite_conversation_storage(workspace, lease=lease)

        self.assertEqual(
            str(caught.exception),
            "unsafe SQLite conversation storage path",
        )
        self.assertIsNone(caught.exception.__cause__)
        self.assertEqual(len(created), 1)
        temporary, sidecar = created[0]
        self.assertFalse(target.exists())
        self.assertFalse(temporary.exists())
        self.assertEqual(sidecar.read_bytes(), sidecar_bytes)
        self.assertEqual(
            list(target.parent.glob(".conversation_storage.sqlite3.*")),
            [sidecar],
        )

    def test_every_provisioning_path_retains_exhaustive_verification(self) -> None:
        template = initialized_workspace(self)
        provision(template)
        race_winner_bytes = database_path(template).read_bytes()

        for pathway, expected_count in (
            ("new", 1),
            ("existing", 1),
            ("race_winner", 2),
        ):
            with self.subTest(pathway=pathway):
                workspace = initialized_workspace(self)
                if pathway == "existing":
                    provision(workspace)
                database = database_module.SQLiteDatabase(workspace)
                coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
                trace: list[str] = []
                snapshot_calls: list[object] = []
                connect, connections = _tracking_connect(trace)

                def validate_snapshot(connection: object) -> None:
                    snapshot_calls.append(connection)
                    sqlite_module._validate_database_snapshot(connection)

                def install_race_winner(
                    source: Path,
                    target: Path,
                    *,
                    follow_symlinks: bool = True,
                ) -> None:
                    del source, follow_symlinks
                    target.write_bytes(race_winner_bytes)
                    raise FileExistsError("deterministic competing provisioner")

                with ExitStack() as stack:
                    stack.enter_context(
                        mock.patch.object(database_module, "_connect", connect)
                    )
                    if pathway == "race_winner":
                        stack.enter_context(
                            mock.patch.object(
                                os,
                                "link",
                                side_effect=install_race_winner,
                            )
                        )
                    with coordinator.borrow(timeout_seconds=2.0) as lease:
                        database.provision(
                            validate_snapshot,
                            validate_lease=lambda: lease.validate(
                                workspace_key=coordinator.workspace_key
                            ),
                        )

                self.assertEqual(len(snapshot_calls), expected_count)
                self.assertTrue(
                    all(connection in connections for connection in snapshot_calls)
                )
                _assert_exhaustive_statements(
                    self,
                    trace,
                    expected_count=expected_count,
                )
                self.assertTrue(database_path(workspace).is_file())

    @unittest.skipUnless(os.name == "posix", "hard-link recovery is POSIX-only")
    def test_interrupted_hard_link_is_observationally_rejected_then_recovered(
        self,
    ) -> None:
        workspace, services = provisioned_services(self)
        metadata = services.catalog.create("session_a", "Recovery witness")
        target = database_path(workspace)
        alias = target.with_name(".conversation_storage.sqlite3.interrupted.tmp")
        unrelated = target.with_name("unrelated.bin")
        unrelated.write_bytes(b"unrelated exact bytes")
        try:
            os.link(target, alias, follow_symlinks=False)
        except OSError as exc:
            self.skipTest(f"hard links are unsupported: {exc}")

        before = storage_snapshot(workspace)
        with self.assertRaises(WorkspaceSecurityError):
            preflight_sqlite_conversation_storage(workspace)
        with self.assertRaises(WorkspaceSecurityError):
            services.catalog.get("session_a", metadata.conversation_id)
        self.assertEqual(storage_snapshot(workspace), before)

        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            recovered = provision_sqlite_conversation_storage(
                workspace,
                lease=lease,
            )
        self.assertIs(recovered.capability, ConversationStorageCapability.READY)
        self.assertTrue(target.is_file())
        self.assertEqual(target.stat().st_nlink, 1)
        self.assertFalse(alias.exists())
        self.assertEqual(unrelated.read_bytes(), b"unrelated exact bytes")
        self.assertEqual(
            services.catalog.get("session_a", metadata.conversation_id),
            metadata,
        )

        recovered_snapshot = storage_snapshot(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            repeated = provision_sqlite_conversation_storage(
                workspace,
                lease=lease,
            )
        self.assertIs(repeated.capability, ConversationStorageCapability.READY)
        self.assertEqual(storage_snapshot(workspace), recovered_snapshot)

    @unittest.skipUnless(os.name == "posix", "hard-link recovery is POSIX-only")
    def test_interrupted_hard_link_validates_target_before_alias_removal(
        self,
    ) -> None:
        def corrupt_header(_workspace: Workspace, target: Path) -> None:
            target.write_bytes(b"corrupt target bytes")

        def unsupported_version(_workspace: Workspace, target: Path) -> None:
            raw = bytearray(target.read_bytes())
            raw[60:64] = (2).to_bytes(4, "big")
            target.write_bytes(raw)

        def invalid_manifest(workspace: Workspace, _target: Path) -> None:
            with raw_connection(workspace) as connection:
                connection.execute(
                    "CREATE TABLE unexpected_recovery_object (value TEXT)"
                )
                connection.execute("PRAGMA schema_version = 1")

        def global_orphan(workspace: Workspace, _target: Path) -> None:
            with raw_connection(workspace) as connection:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute(
                    "INSERT INTO conversation_events "
                    "(session_id, conversation_id, sequence, schema_version, "
                    "turn_id, turn_slot, timestamp, sender, recipient, kind, "
                    "content, searchable_content) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "orphan_session",
                        "orphan_conversation",
                        1,
                        1,
                        "orphan_turn",
                        0,
                        "2026-08-25T00:00:00+00:00",
                        "user",
                        "plot_architect",
                        "user_message",
                        "orphan content",
                        "orphan content",
                    ),
                )

        cases: tuple[
            tuple[str, type[Exception], Callable[[Workspace, Path], None]],
            ...,
        ] = (
            ("corrupt header", WorkspaceFormatError, corrupt_header),
            (
                "unsupported version",
                WorkspaceVersionUnsupportedError,
                unsupported_version,
            ),
            ("invalid manifest", WorkspaceFormatError, invalid_manifest),
            ("global orphan", WorkspaceFormatError, global_orphan),
        )
        messages = {
            WorkspaceFormatError: "invalid SQLite conversation storage",
            WorkspaceVersionUnsupportedError: (
                "unsupported SQLite conversation storage schema version"
            ),
        }

        for label, expected_error, configure in cases:
            with self.subTest(case=label):
                workspace = initialized_workspace(self)
                provision(workspace)
                target = database_path(workspace)
                configure(workspace, target)
                target_bytes = target.read_bytes()
                alias = target.with_name(
                    ".conversation_storage.sqlite3.interrupted-validation.tmp"
                )
                try:
                    os.link(target, alias, follow_symlinks=False)
                except OSError as exc:
                    self.skipTest(f"hard links are unsupported: {exc}")
                before = storage_snapshot(workspace)
                identity = os.stat(target, follow_symlinks=False)
                self.assertTrue(os.path.samefile(target, alias))
                self.assertEqual(identity.st_nlink, 2)

                coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
                with coordinator.borrow(timeout_seconds=2.0) as lease:
                    with self.assertRaises(expected_error) as caught:
                        provision_sqlite_conversation_storage(
                            workspace,
                            lease=lease,
                        )

                self.assertEqual(str(caught.exception), messages[expected_error])
                self.assertIsNone(caught.exception.__cause__)
                self.assertEqual(storage_snapshot(workspace), before)
                self.assertTrue(target.exists())
                self.assertTrue(alias.exists())
                self.assertTrue(os.path.samefile(target, alias))
                self.assertEqual(
                    os.stat(target, follow_symlinks=False).st_nlink,
                    2,
                )
                self.assertEqual(target.read_bytes(), target_bytes)
                self.assertEqual(alias.read_bytes(), target_bytes)

    @unittest.skipUnless(os.name == "posix", "hard-link recovery is POSIX-only")
    def test_interrupted_hard_link_recovery_rejects_every_ambiguous_shape(
        self,
    ) -> None:
        def new_case() -> tuple[Workspace, Path, Path]:
            workspace = initialized_workspace(self)
            provision(workspace)
            target = database_path(workspace)
            unrelated = target.with_name("unrelated.bin")
            unrelated.write_bytes(b"preserve unrelated")
            return workspace, target, unrelated

        def exact_alias(target: Path, label: str) -> Path:
            return target.with_name(f".conversation_storage.sqlite3.{label}.tmp")

        cases: list[tuple[str, type[Exception], Callable[[Path], None]]] = []

        def no_candidate(target: Path) -> None:
            os.link(target, target.with_name("wrong-pattern-link"))

        cases.append(("no matching owned name", WorkspaceSecurityError, no_candidate))

        def symlink_candidate(target: Path) -> None:
            os.link(target, target.with_name("wrong-pattern-link"))
            os.symlink(target.name, exact_alias(target, "symlink"))

        cases.append(("symlink candidate", WorkspaceSecurityError, symlink_candidate))

        def nonregular_candidate(target: Path) -> None:
            os.link(target, target.with_name("wrong-pattern-link"))
            exact_alias(target, "directory").mkdir()

        cases.append(
            ("nonregular candidate", WorkspaceSecurityError, nonregular_candidate)
        )

        def different_inode(target: Path) -> None:
            os.link(target, target.with_name("wrong-pattern-link"))
            candidate = exact_alias(target, "different")
            candidate.write_bytes(target.read_bytes())
            os.link(candidate, target.with_name("different-inode-companion"))

        cases.append(("different inode", WorkspaceSecurityError, different_inode))

        def multiple_candidates(target: Path) -> None:
            os.link(target, target.with_name("wrong-pattern-link"))
            exact_alias(target, "first").write_bytes(b"first")
            exact_alias(target, "second").write_bytes(b"second")

        cases.append(
            ("multiple candidates", WorkspaceSecurityError, multiple_candidates)
        )

        def third_link(target: Path) -> None:
            os.link(target, exact_alias(target, "candidate"))
            os.link(target, target.with_name("unexpected-third-link"))

        cases.append(("third link", WorkspaceSecurityError, third_link))

        for owner in ("target", "alias"):
            for suffix in ("-journal", "-wal", "-shm"):

                def sidecar(
                    target: Path,
                    *,
                    owner: str = owner,
                    suffix: str = suffix,
                ) -> None:
                    alias = exact_alias(target, "candidate")
                    os.link(target, alias)
                    sidecar_owner = target if owner == "target" else alias
                    Path(f"{sidecar_owner}{suffix}").write_bytes(b"sidecar")

                cases.append(
                    (
                        f"{owner} {suffix} sidecar",
                        WorkspaceFormatError,
                        sidecar,
                    )
                )

        unsupported: OSError | None = None
        for label, expected_error, configure in cases:
            with self.subTest(case=label):
                workspace, target, unrelated = new_case()
                try:
                    configure(target)
                except OSError as exc:
                    unsupported = exc
                    break
                before = storage_snapshot(workspace)
                coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
                with coordinator.borrow(timeout_seconds=2.0) as lease:
                    with self.assertRaises(expected_error):
                        provision_sqlite_conversation_storage(
                            workspace,
                            lease=lease,
                        )
                self.assertEqual(storage_snapshot(workspace), before)
                self.assertEqual(unrelated.read_bytes(), b"preserve unrelated")
        if unsupported is not None:
            self.skipTest(
                f"required filesystem primitives are unsupported: {unsupported}"
            )

    def test_missing_fcnt_keeps_observation_available_and_mutation_closed(
        self,
    ) -> None:
        workspace, services = provisioned_services(self)
        metadata = services.catalog.create("session_a", "Read-only witness")
        target = database_path(workspace)
        before = storage_snapshot(workspace)
        before_database = target.read_bytes()
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        provision_reached = False

        with mock.patch.object(composition_module, "_fcntl", None):
            self.assertIs(
                preflight_sqlite_conversation_storage(workspace).capability,
                ConversationStorageCapability.READY,
            )
            self.assertEqual(
                services.catalog.get("session_a", metadata.conversation_id),
                metadata,
            )
            with self.assertRaises(ConversationStorageCoordinationError) as caught:
                services.catalog.create("session_a", "Blocked mutation")
            mutation_failure = caught.exception
            with self.assertRaises(
                ConversationStorageCoordinationError
            ) as provision_caught:
                with coordinator.borrow(timeout_seconds=2.0) as lease:
                    provision_reached = True
                    provision_sqlite_conversation_storage(workspace, lease=lease)
        self.assertFalse(provision_reached)
        self.assertEqual(
            str(mutation_failure),
            "conversation storage failure: coordination_failed",
        )
        self.assertEqual(str(provision_caught.exception), str(mutation_failure))
        self.assertEqual(target.read_bytes(), before_database)
        self.assertEqual(storage_snapshot(workspace), before)

        missing = initialized_workspace(self)
        missing_target = database_path(missing)
        coordinator = WorkspaceConversationStorageMutationCoordinator(missing)
        with mock.patch.object(composition_module, "_fcntl", None):
            with self.assertRaises(ConversationStorageCoordinationError) as caught:
                with coordinator.borrow(timeout_seconds=2.0) as lease:
                    provision_sqlite_conversation_storage(missing, lease=lease)
        self.assertEqual(
            str(caught.exception),
            "conversation storage failure: coordination_failed",
        )
        self.assertFalse(missing_target.exists())

    def test_link_failure_cleans_only_owned_temporary_artifacts(self) -> None:
        workspace = initialized_workspace(self)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            with mock.patch.object(
                database_module.os,
                "link",
                side_effect=OSError("link failed"),
            ):
                with self.assertRaises(ConversationStorageUnavailableError):
                    provision_sqlite_conversation_storage(workspace, lease=lease)
        target = database_path(workspace)
        self.assertFalse(target.exists())
        self.assertEqual(
            list(target.parent.glob(".conversation_storage.sqlite3.*")), []
        )

    def test_lease_failure_after_temporary_creation_cleans_the_temporary(self) -> None:
        workspace = initialized_workspace(self)

        class StagedLease:
            def __init__(self) -> None:
                self.calls = 0

            def validate(self, *, workspace_key: str) -> None:
                self.calls += 1
                if workspace_key != os.fspath(workspace.root) or self.calls == 3:
                    raise ConversationStorageCoordinationError()

        lease = StagedLease()
        with self.assertRaises(ConversationStorageCoordinationError):
            provision_sqlite_conversation_storage(workspace, lease=lease)  # type: ignore[arg-type]
        target = database_path(workspace)
        self.assertFalse(target.exists())
        self.assertEqual(
            list(target.parent.glob(".conversation_storage.sqlite3.*")), []
        )

    def test_lease_expiry_after_final_prelink_scan_cleans_without_publication(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        target = database_path(workspace)
        lease = _ExpirableProvisioningLease(os.fspath(workspace.root))
        real_require = database_module._require_provisioning_candidates
        temporary_scans = 0

        def require_candidates(
            parent: Path,
            *,
            expected: tuple[Path, ...],
        ) -> None:
            nonlocal temporary_scans
            real_require(parent, expected=expected)
            if expected:
                temporary_scans += 1
                if temporary_scans == 2:
                    lease.expired = True

        with mock.patch.object(
            database_module,
            "_require_provisioning_candidates",
            side_effect=require_candidates,
        ):
            with self.assertRaises(ConversationStorageCoordinationError) as caught:
                provision_sqlite_conversation_storage(
                    workspace,
                    lease=lease,  # type: ignore[arg-type]
                )

        self.assertEqual(temporary_scans, 2)
        self.assertEqual(
            str(caught.exception),
            "conversation storage failure: coordination_failed",
        )
        self.assertIsNone(caught.exception.__cause__)
        self.assertFalse(target.exists())
        self.assertEqual(
            list(target.parent.glob(".conversation_storage.sqlite3.*")),
            [],
        )

    @unittest.skipUnless(os.name == "posix", "hard-link publication is POSIX-only")
    def test_lease_expiry_after_link_preserves_recoverable_two_link_state(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        target = database_path(workspace)
        probe_source = target.with_name("hard-link-capability-probe")
        probe_alias = target.with_name("hard-link-capability-alias")
        probe_source.write_bytes(b"probe")
        try:
            os.link(probe_source, probe_alias, follow_symlinks=False)
        except OSError as exc:
            self.skipTest(f"hard links are unsupported: {exc}")
        finally:
            probe_alias.unlink(missing_ok=True)
            probe_source.unlink(missing_ok=True)
        lease = _ExpirableProvisioningLease(os.fspath(workspace.root))
        real_require = database_module._require_provisioning_candidates
        temporary_scans = 0

        def require_candidates(
            parent: Path,
            *,
            expected: tuple[Path, ...],
        ) -> None:
            nonlocal temporary_scans
            real_require(parent, expected=expected)
            if expected:
                temporary_scans += 1
                if temporary_scans == 3:
                    lease.expired = True

        with mock.patch.object(
            database_module,
            "_require_provisioning_candidates",
            side_effect=require_candidates,
        ):
            with self.assertRaises(ConversationStorageCoordinationError) as caught:
                provision_sqlite_conversation_storage(
                    workspace,
                    lease=lease,  # type: ignore[arg-type]
                )

        self.assertEqual(temporary_scans, 3)
        self.assertEqual(
            str(caught.exception),
            "conversation storage failure: coordination_failed",
        )
        self.assertIsNone(caught.exception.__cause__)
        aliases = list(target.parent.glob(".conversation_storage.sqlite3.*.tmp"))
        self.assertEqual(len(aliases), 1)
        alias = aliases[0]
        self.assertTrue(target.is_file())
        self.assertTrue(alias.is_file())
        self.assertTrue(os.path.samefile(target, alias))
        self.assertEqual(os.stat(target, follow_symlinks=False).st_nlink, 2)
        target_bytes = target.read_bytes()
        self.assertEqual(alias.read_bytes(), target_bytes)
        self.assertNotIn(alias.name, str(caught.exception))

        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as fresh_lease:
            recovered = provision_sqlite_conversation_storage(
                workspace,
                lease=fresh_lease,
            )
        self.assertIs(recovered.capability, ConversationStorageCapability.READY)
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_bytes(), target_bytes)
        self.assertEqual(os.stat(target, follow_symlinks=False).st_nlink, 1)
        self.assertFalse(alias.exists())
        self.assertEqual(
            list(target.parent.glob(".conversation_storage.sqlite3.*")),
            [],
        )
        self.assertIs(
            preflight_sqlite_conversation_storage(workspace).capability,
            ConversationStorageCapability.READY,
        )

    def test_postinstall_sync_failure_leaves_one_complete_database(self) -> None:
        workspace = initialized_workspace(self)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            with mock.patch.object(
                database_module,
                "_synchronize_file",
                side_effect=database_module.SQLiteDatabaseUnavailableError(),
            ):
                with self.assertRaises(ConversationStorageUnavailableError):
                    provision_sqlite_conversation_storage(workspace, lease=lease)
        self.assertTrue(database_path(workspace).is_file())
        self.assertIs(
            preflight_sqlite_conversation_storage(workspace).capability,
            ConversationStorageCapability.READY,
        )
        self.assertEqual(
            list(
                database_path(workspace).parent.glob(".conversation_storage.sqlite3.*")
            ),
            [],
        )


def _raise(failure: BaseException) -> None:
    raise failure


if __name__ == "__main__":
    unittest.main()
