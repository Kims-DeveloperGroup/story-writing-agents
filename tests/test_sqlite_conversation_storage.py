"""Public behavior and conformance tests for optional SQLite storage."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import story_writing_agents
import story_writing_agents.conversation_storage as storage_package
from story_writing_agents import (
    ConversationCatalog,
    ConversationEventKind,
    ConversationEventStore,
    ConversationExistsError,
    ReferenceConversationSearch,
    StoryRuntime,
    Workspace,
    WorkspaceFormatError,
    normalize_conversation_subject,
)
from story_writing_agents.conversation_storage import (
    ConversationArchiveImportStatus,
    ConversationStorageBackend,
    ConversationStorageCapability,
    ConversationStorageCoordinationError,
    ConversationStorageProvisioningNeededError,
    ConversationStorageServices,
    WorkspaceConversationStorageMutationCoordinator,
    compose_filesystem_conversation_storage,
)
from story_writing_agents.conversation_storage import sqlite as sqlite_module
from story_writing_agents.conversation_storage.sqlite import (
    SQLITE_CONVERSATION_STORAGE_APPLICATION_ID,
    SQLITE_CONVERSATION_STORAGE_BUSY_TIMEOUT_SECONDS,
    SQLITE_CONVERSATION_STORAGE_DATABASE_PATH,
    SQLITE_CONVERSATION_STORAGE_MINIMUM_VERSION,
    SQLITE_CONVERSATION_STORAGE_SCHEMA_VERSION,
    SQLiteConversationArchiveAdapter,
    SQLiteConversationCatalog,
    SQLiteConversationEventStore,
    compose_sqlite_conversation_archive,
    compose_sqlite_conversation_storage,
    preflight_sqlite_conversation_storage,
    provision_sqlite_conversation_storage,
)

from tests.conversation_event_store_contract import (
    ConversationEventStoreContractMixin,
)
from tests.conversation_search_contract import ConversationSearchContractMixin
from tests.sqlite_conversation_storage_fixture import (
    database_path,
    initialized_workspace,
    make_append,
    provision,
    provisioned_services,
    raw_connection,
    storage_snapshot,
)
from tests.test_conversation_catalog import _ConversationCatalogContractMixin


class SQLiteConversationCatalogContractTests(
    _ConversationCatalogContractMixin,
    unittest.TestCase,
):
    def make_catalog(self) -> ConversationCatalog:
        self.workspace = initialized_workspace(self)
        provision(self.workspace)
        return compose_sqlite_conversation_storage(self.workspace).catalog


class SQLiteConversationEventStoreContractTests(
    ConversationEventStoreContractMixin,
    unittest.TestCase,
):
    def make_store(self) -> ConversationEventStore:
        self.workspace = initialized_workspace(self)
        provision(self.workspace)
        services = compose_sqlite_conversation_storage(self.workspace)
        self.catalog = services.catalog
        return services.event_store

    def create_conversation(self, session_id: str, subject: str) -> str:
        return self.catalog.create(session_id, subject).conversation_id


class SQLiteConversationSearchContractTests(
    ConversationSearchContractMixin,
    unittest.TestCase,
):
    def make_adapters(
        self,
    ) -> tuple[SQLiteConversationCatalog, SQLiteConversationEventStore]:
        self.workspace = initialized_workspace(self)
        provision(self.workspace)
        services = compose_sqlite_conversation_storage(self.workspace)
        if not isinstance(services.catalog, SQLiteConversationCatalog):
            raise AssertionError("SQLite composition returned the wrong catalog")
        if not isinstance(services.event_store, SQLiteConversationEventStore):
            raise AssertionError("SQLite composition returned the wrong event store")
        return services.catalog, services.event_store


class SQLiteBoundaryAndCompositionTests(unittest.TestCase):
    def test_constants_exports_and_owning_modules_are_exact(self) -> None:
        expected = (
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
        )
        self.assertEqual(tuple(sqlite_module.__all__), expected)
        self.assertEqual(SQLITE_CONVERSATION_STORAGE_APPLICATION_ID, 0x53574143)
        self.assertEqual(SQLITE_CONVERSATION_STORAGE_BUSY_TIMEOUT_SECONDS, 5.0)
        self.assertEqual(
            SQLITE_CONVERSATION_STORAGE_DATABASE_PATH,
            "discussion_logs/conversation_storage.sqlite3",
        )
        self.assertEqual(SQLITE_CONVERSATION_STORAGE_MINIMUM_VERSION, (3, 7, 16))
        self.assertEqual(SQLITE_CONVERSATION_STORAGE_SCHEMA_VERSION, 1)
        for name in expected:
            with self.subTest(name=name):
                self.assertNotIn(name, storage_package.__all__)
                self.assertNotIn(name, story_writing_agents.__all__)
                self.assertFalse(hasattr(storage_package, name))
                self.assertFalse(hasattr(story_writing_agents, name))

    def test_optional_modules_import_lazily_without_io_or_sqlite_loading(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        source = repository / "src"
        script = f"""
import pathlib
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, {str(source)!r})
before = tuple(pathlib.Path.cwd().iterdir())
import story_writing_agents
import story_writing_agents.conversation_storage as package
assert 'story_writing_agents.conversation_storage.sqlite' not in sys.modules
assert 'sqlite3' not in sys.modules
import story_writing_agents.conversation_storage.sqlite as public
import story_writing_agents.conversation_storage.sqlite_database as database_owner
assert tuple(public.__all__) == {tuple(sqlite_module.__all__)!r}
assert tuple(database_owner.__all__) == (
    'SQLITE_APPLICATION_ID',
    'SQLITE_BUSY_TIMEOUT_SECONDS',
    'SQLITE_DATABASE_PATH',
    'SQLITE_MINIMUM_VERSION',
    'SQLITE_SCHEMA_VERSION',
    'SQLiteDatabase',
    'SQLiteDatabaseFormatError',
    'SQLiteDatabaseMissingError',
    'SQLiteDatabaseSecurityError',
    'SQLiteDatabaseUnavailableError',
    'SQLiteDatabaseVersionError',
)
assert 'sqlite3' not in sys.modules
assert tuple(pathlib.Path.cwd().iterdir()) == before
assert all(name not in story_writing_agents.__all__ for name in public.__all__)
assert all(name not in package.__all__ for name in public.__all__)
"""
        with tempfile.TemporaryDirectory(prefix="sqlite-import-test-") as temporary:
            completed = subprocess.run(
                [sys.executable, "-I", "-B", "-c", script],
                cwd=temporary,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_dependency_edges_are_exact_and_defaults_do_not_select_sqlite(
        self,
    ) -> None:
        public_tree = ast.parse(
            Path(sqlite_module.__file__).read_text(encoding="utf-8")
        )
        dependencies = {
            node.module
            for node in ast.walk(public_tree)
            if isinstance(node, ast.ImportFrom) and node.level > 0
        }
        self.assertEqual(
            dependencies,
            {
                "conversation_search",
                "conversations",
                "errors",
                "workspace",
                "sqlite_database",
                "composition",
                "contracts",
            },
        )

        workspace = initialized_workspace(self)
        before = storage_snapshot(workspace)
        filesystem = compose_filesystem_conversation_storage(workspace)
        self.assertIs(filesystem.backend, ConversationStorageBackend.FILESYSTEM)
        self.assertEqual(storage_snapshot(workspace), before)
        self.assertFalse(database_path(workspace).exists())

    def test_composition_returns_one_sqlite_provenance_and_shared_authority(
        self,
    ) -> None:
        workspace, services = provisioned_services(self)
        self.assertIs(services.backend, ConversationStorageBackend.SQLITE)
        self.assertIsInstance(services.catalog, SQLiteConversationCatalog)
        self.assertIsInstance(services.event_store, SQLiteConversationEventStore)
        self.assertIsInstance(services.search, ReferenceConversationSearch)
        self.assertIs(
            services.catalog_binding.provenance,
            services.event_store_binding.provenance,
        )
        self.assertIs(
            services.catalog_binding.provenance,
            services.search_binding.provenance,
        )
        self.assertEqual(
            services.mutation_coordinator.workspace_key,
            services.provenance.workspace_key,
        )
        self.assertEqual(services.provenance.workspace_key, os.fspath(workspace.root))

    def test_missing_composition_fails_without_provisioning_or_default_change(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        before = storage_snapshot(workspace)
        with self.assertRaises(ConversationStorageProvisioningNeededError):
            compose_sqlite_conversation_storage(workspace)
        with self.assertRaises(ConversationStorageProvisioningNeededError):
            compose_sqlite_conversation_archive(workspace)
        self.assertEqual(storage_snapshot(workspace), before)
        self.assertFalse(database_path(workspace).exists())

    def test_fresh_composition_and_injected_runtime_observe_persisted_context(
        self,
    ) -> None:
        workspace, services = provisioned_services(self)
        metadata = services.catalog.create("session_a", "Persistent subject")
        user = services.event_store.append(
            make_append(
                session_id="session_a",
                conversation_id=metadata.conversation_id,
                turn_id="turn_a",
                content="question with \x00 and 한🙂",
            )
        )
        reply = services.event_store.append(
            make_append(
                session_id="session_a",
                conversation_id=metadata.conversation_id,
                turn_id="turn_a",
                kind=ConversationEventKind.AGENT_REPLY,
                content="persisted reply",
            )
        )

        fresh = compose_sqlite_conversation_storage(workspace)
        runtime = StoryRuntime(
            workspace,
            None,
            conversation_event_store=fresh.event_store,
        )
        self.assertIs(runtime.conversation_event_store, fresh.event_store)
        self.assertEqual(
            fresh.event_store.read("session_a", metadata.conversation_id),
            (user, reply),
        )
        self.assertEqual(
            fresh.event_store.read_context(
                "session_a", metadata.conversation_id
            ).events,
            (user, reply),
        )


class SQLitePreflightAndProvisionTests(unittest.TestCase):
    def test_missing_preflight_is_read_only_and_reports_provisioning_needed(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        before = storage_snapshot(workspace)
        result = preflight_sqlite_conversation_storage(workspace)
        self.assertIs(result.backend, ConversationStorageBackend.SQLITE)
        self.assertIs(
            result.capability,
            ConversationStorageCapability.PROVISIONING_NEEDED,
        )
        self.assertEqual(storage_snapshot(workspace), before)

    def test_provision_requires_an_active_exact_workspace_lease(self) -> None:
        workspace = initialized_workspace(self)
        other = initialized_workspace(self)
        coordinator = WorkspaceConversationStorageMutationCoordinator(other)
        before = storage_snapshot(workspace)
        with coordinator.borrow(timeout_seconds=1.0) as wrong:
            with self.assertRaises(ConversationStorageCoordinationError):
                provision_sqlite_conversation_storage(workspace, lease=wrong)
        self.assertEqual(storage_snapshot(workspace), before)

        own = WorkspaceConversationStorageMutationCoordinator(workspace)
        with own.borrow(timeout_seconds=1.0) as released:
            pass
        with self.assertRaises(ConversationStorageCoordinationError):
            provision_sqlite_conversation_storage(workspace, lease=released)
        self.assertEqual(storage_snapshot(workspace), before)

    def test_provision_rejects_a_lease_used_from_another_thread(self) -> None:
        workspace = initialized_workspace(self)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        failures: list[BaseException] = []
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            thread = threading.Thread(
                target=lambda: _capture_failure(
                    failures,
                    lambda: provision_sqlite_conversation_storage(
                        workspace,
                        lease=lease,
                    ),
                )
            )
            thread.start()
            thread.join(timeout=2.0)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ConversationStorageCoordinationError)
        self.assertFalse(database_path(workspace).exists())

    def test_provision_creates_exact_database_and_is_byte_idempotent(self) -> None:
        workspace = initialized_workspace(self)
        provision(workspace)
        target = database_path(workspace)
        first = target.read_bytes()
        self.assertTrue(first.startswith(b"SQLite format 3\x00"))
        self.assertEqual(
            int.from_bytes(first[68:72], "big"),
            SQLITE_CONVERSATION_STORAGE_APPLICATION_ID,
        )
        self.assertEqual(int.from_bytes(first[60:64], "big"), 1)
        self.assertFalse(
            any(
                Path(f"{target}{suffix}").exists()
                for suffix in ("-journal", "-wal", "-shm")
            )
        )
        self.assertEqual(
            list(target.parent.glob(".conversation_storage.sqlite3.*.tmp")), []
        )

        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            result = provision_sqlite_conversation_storage(workspace, lease=lease)
        self.assertIs(result.capability, ConversationStorageCapability.READY)
        self.assertEqual(target.read_bytes(), first)

    def test_normalized_subject_tamper_is_rejected_without_preflight_repair(
        self,
    ) -> None:
        workspace, services = provisioned_services(self)
        metadata = services.catalog.create("session_a", "  Café\u00a0Story  ")
        with raw_connection(workspace) as connection:
            connection.execute(
                "UPDATE conversations SET normalized_subject = ? "
                "WHERE session_id = ? AND conversation_id = ?",
                ("wrong-normalized-value", "session_a", metadata.conversation_id),
            )
        before = storage_snapshot(workspace)
        with self.assertRaises(WorkspaceFormatError):
            preflight_sqlite_conversation_storage(workspace)
        self.assertEqual(storage_snapshot(workspace), before)

    def test_foreign_key_orphan_is_rejected_without_preflight_repair(self) -> None:
        workspace = initialized_workspace(self)
        provision(workspace)
        with raw_connection(workspace) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(
                "INSERT INTO conversation_events "
                "(session_id, conversation_id, sequence, schema_version, turn_id, "
                "turn_slot, timestamp, sender, recipient, kind, content) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "session_a",
                    "missing_conversation",
                    1,
                    1,
                    "turn_a",
                    0,
                    "2026-08-25T00:00:00+00:00",
                    "user",
                    "plot_architect",
                    "user_message",
                    "orphan",
                ),
            )
        before = storage_snapshot(workspace)
        with self.assertRaises(WorkspaceFormatError):
            preflight_sqlite_conversation_storage(workspace)
        self.assertEqual(storage_snapshot(workspace), before)


class SQLiteArchiveTests(unittest.TestCase):
    @staticmethod
    def _insert_orphan(
        workspace: Workspace,
        *,
        session_id: str,
        conversation_id: str,
    ) -> None:
        with raw_connection(workspace) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(
                "INSERT INTO conversation_events "
                "(session_id, conversation_id, sequence, schema_version, turn_id, "
                "turn_slot, timestamp, sender, recipient, kind, content) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    conversation_id,
                    1,
                    1,
                    "orphan_turn",
                    0,
                    "2026-08-25T00:00:00+00:00",
                    "user",
                    "plot_architect",
                    "user_message",
                    "orphan event",
                ),
            )

    def _populated_archive(
        self,
    ) -> tuple[
        Workspace,
        ConversationStorageServices,
        SQLiteConversationArchiveAdapter,
    ]:
        workspace, services = provisioned_services(self)
        first = services.catalog.create("session_b", "Second subject")
        second = services.catalog.create("session_a", "  First subject  ")
        services.event_store.append(
            make_append(
                session_id="session_a",
                conversation_id=second.conversation_id,
                turn_id="turn_a",
                content="exact \x00 한🙂 content",
            )
        )
        services.event_store.append(
            make_append(
                session_id="session_a",
                conversation_id=second.conversation_id,
                turn_id="turn_a",
                kind=ConversationEventKind.AGENT_REPLY,
                content="reply",
            )
        )
        services.catalog.archive("session_b", first.conversation_id)
        return workspace, services, compose_sqlite_conversation_archive(workspace)

    def test_export_is_exact_canonical_and_deterministic(self) -> None:
        workspace, services, archive = self._populated_archive()
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            first = archive.export_archive(lease)
            second = archive.export_archive(lease)
        self.assertEqual(first, second)
        identities = tuple(
            (record.metadata.session_id, record.metadata.conversation_id)
            for record in first.records
        )
        self.assertEqual(
            identities,
            tuple(
                (item.session_id, item.conversation_id)
                for item in sorted(
                    services.catalog.list("session_a", include_archived=True)
                    + services.catalog.list("session_b", include_archived=True),
                    key=lambda item: (
                        item.session_id,
                        item.created_at,
                        item.conversation_id,
                    ),
                )
            ),
        )
        event_contents = tuple(
            event.content for record in first.records for event in record.events
        )
        self.assertIn("exact \x00 한🙂 content", event_contents)

    def test_archive_round_trip_and_identical_reimport_are_exact(self) -> None:
        source, _, source_archive = self._populated_archive()
        source_coordinator = WorkspaceConversationStorageMutationCoordinator(source)
        with source_coordinator.borrow(timeout_seconds=2.0) as lease:
            payload = source_archive.export_archive(lease)

        target = initialized_workspace(self)
        provision(target)
        target_archive = compose_sqlite_conversation_archive(target)
        target_coordinator = WorkspaceConversationStorageMutationCoordinator(target)
        with target_coordinator.borrow(timeout_seconds=2.0) as lease:
            imported = target_archive.import_archive(lease, payload)
            exact = target_archive.export_archive(lease)
        self.assertIs(imported.status, ConversationArchiveImportStatus.IMPORTED)
        self.assertEqual(exact, payload)

        before = storage_snapshot(target)
        with target_coordinator.borrow(timeout_seconds=2.0) as lease:
            unchanged = target_archive.import_archive(lease, payload)
        self.assertIs(unchanged.status, ConversationArchiveImportStatus.UNCHANGED)
        self.assertEqual(storage_snapshot(target), before)

    def test_archive_divergence_rolls_back_earlier_imports(self) -> None:
        source, _, source_archive = self._populated_archive()
        coordinator = WorkspaceConversationStorageMutationCoordinator(source)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            payload = source_archive.export_archive(lease)
        self.assertGreaterEqual(len(payload.records), 2)

        target = initialized_workspace(self)
        provision(target)
        conflict = payload.records[-1].metadata
        with raw_connection(target) as connection:
            connection.execute(
                "INSERT INTO conversations "
                "(session_id, conversation_id, schema_version, subject, "
                "normalized_subject, subject_revision, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    conflict.session_id,
                    conflict.conversation_id,
                    conflict.schema_version,
                    "divergent subject",
                    normalize_conversation_subject("divergent subject"),
                    conflict.subject_revision,
                    conflict.status.value,
                    conflict.created_at,
                    conflict.updated_at,
                ),
            )
        before = storage_snapshot(target)
        target_archive = compose_sqlite_conversation_archive(target)
        target_coordinator = WorkspaceConversationStorageMutationCoordinator(target)
        with target_coordinator.borrow(timeout_seconds=2.0) as lease:
            with self.assertRaises(ConversationExistsError):
                target_archive.import_archive(lease, payload)
        self.assertEqual(storage_snapshot(target), before)
        target_services = compose_sqlite_conversation_storage(target)
        self.assertEqual(
            sum(
                len(target_services.catalog.list(session, include_archived=True))
                for session in ("session_a", "session_b")
            ),
            1,
        )

    def test_archive_export_rejects_any_global_orphan_without_change(self) -> None:
        workspace, _, archive = self._populated_archive()
        self._insert_orphan(
            workspace,
            session_id="unrelated_session",
            conversation_id="unrelated_conversation",
        )
        before = storage_snapshot(workspace)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            with self.assertRaises(WorkspaceFormatError) as caught:
                archive.export_archive(lease)
        self.assertEqual(
            str(caught.exception),
            "invalid SQLite conversation storage",
        )
        self.assertIsNone(caught.exception.__cause__)
        self.assertEqual(storage_snapshot(workspace), before)

    def test_archive_import_preflight_rejects_same_and_unrelated_orphans(
        self,
    ) -> None:
        source, _, source_archive = self._populated_archive()
        source_coordinator = WorkspaceConversationStorageMutationCoordinator(source)
        with source_coordinator.borrow(timeout_seconds=2.0) as lease:
            payload = source_archive.export_archive(lease)
        same_identity = next(
            (
                record.metadata.session_id,
                record.metadata.conversation_id,
            )
            for record in payload.records
            if record.events
        )

        for label, identity in (
            ("same identity", same_identity),
            ("unrelated identity", ("orphan_session", "orphan_conversation")),
        ):
            with self.subTest(case=label):
                target = initialized_workspace(self)
                provision(target)
                target_archive = compose_sqlite_conversation_archive(target)
                self._insert_orphan(
                    target,
                    session_id=identity[0],
                    conversation_id=identity[1],
                )
                before = storage_snapshot(target)
                with raw_connection(target) as connection:
                    logical_before = tuple(
                        connection.execute(
                            "SELECT * FROM conversation_events "
                            "ORDER BY session_id, conversation_id, sequence"
                        ).fetchall()
                    )
                coordinator = WorkspaceConversationStorageMutationCoordinator(target)
                with (
                    mock.patch.object(
                        sqlite_module,
                        "_insert_metadata",
                        wraps=sqlite_module._insert_metadata,
                    ) as insert_metadata,
                    coordinator.borrow(timeout_seconds=2.0) as lease,
                ):
                    with self.assertRaises(WorkspaceFormatError):
                        target_archive.import_archive(lease, payload)
                insert_metadata.assert_not_called()
                self.assertEqual(storage_snapshot(target), before)
                with raw_connection(target) as connection:
                    logical_after = tuple(
                        connection.execute(
                            "SELECT * FROM conversation_events "
                            "ORDER BY session_id, conversation_id, sequence"
                        ).fetchall()
                    )
                self.assertEqual(logical_after, logical_before)

    def test_archive_postvalidation_corruption_rolls_back_every_import(self) -> None:
        source, _, source_archive = self._populated_archive()
        source_coordinator = WorkspaceConversationStorageMutationCoordinator(source)
        with source_coordinator.borrow(timeout_seconds=2.0) as lease:
            payload = source_archive.export_archive(lease)

        target = initialized_workspace(self)
        provision(target)
        target_archive = compose_sqlite_conversation_archive(target)
        coordinator = WorkspaceConversationStorageMutationCoordinator(target)
        before = storage_snapshot(target)
        real_validator = sqlite_module._validate_database_snapshot
        validation_calls = 0

        def corrupt_before_second_validation(connection: object) -> None:
            nonlocal validation_calls
            validation_calls += 1
            if validation_calls == 2:
                connection.execute(  # type: ignore[attr-defined]
                    "UPDATE conversations "
                    "SET normalized_subject = 'post-validation-corruption' "
                    "WHERE rowid = (SELECT MIN(rowid) FROM conversations)"
                )
            real_validator(connection)

        with (
            mock.patch.object(
                sqlite_module,
                "_validate_database_snapshot",
                corrupt_before_second_validation,
            ),
            coordinator.borrow(timeout_seconds=2.0) as lease,
        ):
            with self.assertRaises(WorkspaceFormatError):
                target_archive.import_archive(lease, payload)
        self.assertEqual(validation_calls, 2)
        self.assertEqual(storage_snapshot(target), before)
        services = compose_sqlite_conversation_storage(target)
        self.assertEqual(services.catalog.list("session_a", include_archived=True), ())
        self.assertEqual(services.catalog.list("session_b", include_archived=True), ())

    def test_archive_import_preserves_unrelated_exact_destination_records(
        self,
    ) -> None:
        source, _, source_archive = self._populated_archive()
        source_coordinator = WorkspaceConversationStorageMutationCoordinator(source)
        with source_coordinator.borrow(timeout_seconds=2.0) as lease:
            payload = source_archive.export_archive(lease)

        target, services = provisioned_services(self)
        extra_metadata = services.catalog.create("extra_session", "Extra subject")
        extra_event = services.event_store.append(
            make_append(
                session_id="extra_session",
                conversation_id=extra_metadata.conversation_id,
                turn_id="extra_turn",
                content="preserve exact extra event",
            )
        )
        target_archive = compose_sqlite_conversation_archive(target)
        coordinator = WorkspaceConversationStorageMutationCoordinator(target)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            imported = target_archive.import_archive(lease, payload)
            combined = target_archive.export_archive(lease)
        self.assertIs(imported.status, ConversationArchiveImportStatus.IMPORTED)
        self.assertEqual(
            services.catalog.get("extra_session", extra_metadata.conversation_id),
            extra_metadata,
        )
        self.assertEqual(
            services.event_store.read("extra_session", extra_metadata.conversation_id),
            (extra_event,),
        )
        self.assertTrue(all(record in combined.records for record in payload.records))
        self.assertEqual(len(combined.records), len(payload.records) + 1)

        before = storage_snapshot(target)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            unchanged = target_archive.import_archive(lease, payload)
        self.assertIs(unchanged.status, ConversationArchiveImportStatus.UNCHANGED)
        self.assertEqual(storage_snapshot(target), before)

    def test_archive_rejects_wrong_released_and_malformed_evidence_without_change(
        self,
    ) -> None:
        workspace, _, archive = self._populated_archive()
        other = initialized_workspace(self)
        other_coordinator = WorkspaceConversationStorageMutationCoordinator(other)
        before = storage_snapshot(workspace)
        with other_coordinator.borrow(timeout_seconds=2.0) as wrong:
            with self.assertRaises(ConversationStorageCoordinationError):
                archive.export_archive(wrong)
        self.assertEqual(storage_snapshot(workspace), before)

        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as released:
            payload = archive.export_archive(released)
        with self.assertRaises(ConversationStorageCoordinationError):
            archive.export_archive(released)
        object.__setattr__(payload, "records", list(payload.records))
        before = storage_snapshot(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            with self.assertRaises(TypeError):
                archive.import_archive(lease, payload)
        self.assertEqual(storage_snapshot(workspace), before)


class SQLiteConcurrencyTests(unittest.TestCase):
    def test_threaded_sequence_and_turn_slot_races_are_atomic(self) -> None:
        workspace, services = provisioned_services(self)
        metadata = services.catalog.create("session_a", "Thread races")
        workers = 6
        barrier = threading.Barrier(workers)

        def append_user(index: int):  # type: ignore[no-untyped-def]
            barrier.wait(timeout=3.0)
            return services.event_store.append(
                make_append(
                    session_id="session_a",
                    conversation_id=metadata.conversation_id,
                    turn_id=f"turn_{index}",
                    content=f"question-{index}",
                )
            )

        with ThreadPoolExecutor(max_workers=workers) as executor:
            users = tuple(executor.map(append_user, range(workers)))
        for index in range(workers):
            services.event_store.append(
                make_append(
                    session_id="session_a",
                    conversation_id=metadata.conversation_id,
                    turn_id=f"turn_{index}",
                    kind=ConversationEventKind.AGENT_REPLY,
                    content=f"reply-{index}",
                )
            )
        events = services.event_store.read("session_a", metadata.conversation_id)
        self.assertEqual(tuple(event.sequence for event in events), tuple(range(1, 13)))
        self.assertEqual({event.sequence for event in users}, set(range(1, 7)))

        race_barrier = threading.Barrier(2)

        def same_turn_user():  # type: ignore[no-untyped-def]
            race_barrier.wait(timeout=3.0)
            return services.event_store.append(
                make_append(
                    session_id="session_a",
                    conversation_id=metadata.conversation_id,
                    turn_id="same_turn",
                )
            )

        successes, failures = _run_two_way_race(same_turn_user)
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValueError)

    @unittest.skipUnless(os.name == "posix", "requires POSIX advisory locking")
    def test_spawned_process_appends_have_one_contiguous_history(self) -> None:
        workspace, services = provisioned_services(self)
        metadata = services.catalog.create("session_a", "Process sequencing")
        processes = [
            _spawn_worker(
                "append",
                workspace,
                "session_a",
                metadata.conversation_id,
                f"process_turn_{index}",
            )
            for index in range(3)
        ]
        self.addCleanup(lambda: _cleanup_processes(processes))
        for process in processes:
            assert process.stdout is not None
            self.assertEqual(process.stdout.readline(), "ready\n")
        for process in processes:
            assert process.stdin is not None
            process.stdin.write("x")
            process.stdin.flush()
        child_sequences: list[int] = []
        for process in processes:
            assert process.stdout is not None
            output = process.stdout.readline()
            process.wait(timeout=10.0)
            stderr = process.stderr.read() if process.stderr is not None else ""
            self.assertEqual(process.returncode, 0, stderr)
            child_sequences.extend(json.loads(output))

        events = services.event_store.read("session_a", metadata.conversation_id)
        self.assertEqual(tuple(event.sequence for event in events), tuple(range(1, 7)))
        self.assertEqual(sorted(child_sequences), list(range(1, 7)))

    @unittest.skipUnless(os.name == "posix", "requires POSIX advisory locking")
    def test_spawned_process_provisioners_install_one_complete_database(self) -> None:
        workspace = initialized_workspace(self)
        processes = [_spawn_worker("provision", workspace) for _ in range(2)]
        self.addCleanup(lambda: _cleanup_processes(processes))
        for process in processes:
            assert process.stdout is not None
            self.assertEqual(process.stdout.readline(), "ready\n")
        for process in processes:
            assert process.stdin is not None
            process.stdin.write("x")
            process.stdin.flush()
        for process in processes:
            assert process.stdout is not None
            self.assertEqual(process.stdout.readline(), "provisioned\n")
            process.wait(timeout=10.0)
            stderr = process.stderr.read() if process.stderr is not None else ""
            self.assertEqual(process.returncode, 0, stderr)
        self.assertIs(
            preflight_sqlite_conversation_storage(workspace).capability,
            ConversationStorageCapability.READY,
        )
        target = database_path(workspace)
        self.assertEqual(
            list(target.parent.glob(".conversation_storage.sqlite3.*.tmp")), []
        )


def _capture_failure(failures: list[BaseException], operation) -> None:  # type: ignore[no-untyped-def]
    try:
        operation()
    except BaseException as exc:
        failures.append(exc)


def _run_two_way_race(operation):  # type: ignore[no-untyped-def]
    successes: list[object] = []
    failures: list[BaseException] = []

    def invoke() -> None:
        try:
            successes.append(operation())
        except BaseException as exc:
            failures.append(exc)

    threads = [threading.Thread(target=invoke) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)
        if thread.is_alive():
            raise AssertionError("race worker did not terminate")
    return successes, failures


def _spawn_worker(
    mode: str,
    workspace,
    session: str = "session_a",
    conversation: str = "conversation_a",
    turn: str = "turn_a",
):  # type: ignore[no-untyped-def]
    repository = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(repository / "src"), str(repository))
    )
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tests.sqlite_conversation_storage_fixture",
            mode,
            os.fspath(workspace.root),
            session,
            conversation,
            turn,
        ],
        cwd=repository,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _cleanup_processes(processes: list[subprocess.Popen[str]]) -> None:
    for process in processes:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5.0)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


if __name__ == "__main__":
    unittest.main()
