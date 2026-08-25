"""Private SQLite identity, transaction, and provisioning verification."""

from __future__ import annotations

import os
import sqlite3
import threading
import unittest
from pathlib import Path
from typing import Callable
from unittest import mock

from story_writing_agents import (
    WorkspaceFormatError,
    WorkspaceSecurityError,
    WorkspaceVersionUnsupportedError,
)
from story_writing_agents.conversation_storage import (
    ConversationStorageCapability,
    ConversationStorageCoordinationError,
    ConversationStorageUnavailableError,
    WorkspaceConversationStorageMutationCoordinator,
)
from story_writing_agents.conversation_storage import (
    sqlite_database as database_module,
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


class SQLiteDatabaseTransactionTests(unittest.TestCase):
    def _tracking_connect(
        self,
        trace: list[str],
        **options: object,
    ) -> tuple[Callable[..., _TrackingConnection], list[_TrackingConnection]]:
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
