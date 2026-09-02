"""Public behavior and conformance tests for optional SQLite storage."""

from __future__ import annotations

import ast
import multiprocessing
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import story_writing_agents
import story_writing_agents.conversation_storage as storage_package
from story_writing_agents import (
    ConversationCatalog,
    Workspace,
    WorkspaceFormatError,
    normalize_conversation_subject,
)
from story_writing_agents.agents import LEAD_ORCHESTRATOR
from story_writing_agents.conversation_search import (
    CollaborationHistorySearchQuery,
    CollaborationHistorySearchScope,
    ReferenceCollaborationHistorySearch,
)
from story_writing_agents.conversation_storage import (
    ConversationArchiveConflictError,
    ConversationArchiveImportStatus,
    ConversationStorageBackend,
    ConversationStorageCapability,
    ConversationStorageCoordinationError,
    ConversationStorageProvisioningNeededError,
    WorkspaceConversationStorageMutationCoordinator,
    compose_filesystem_conversation_storage,
)
from story_writing_agents.conversation_storage import sqlite as sqlite_module
from story_writing_agents.conversation_storage import (
    sqlite_search_index as search_index_module,
)
from story_writing_agents.conversation_storage.sqlite import (
    SQLITE_CONVERSATION_STORAGE_APPLICATION_ID,
    SQLITE_CONVERSATION_STORAGE_BUSY_TIMEOUT_SECONDS,
    SQLITE_CONVERSATION_STORAGE_DATABASE_PATH,
    SQLITE_CONVERSATION_STORAGE_MINIMUM_VERSION,
    SQLITE_CONVERSATION_STORAGE_SCHEMA_VERSION,
    SQLiteConversationCatalog,
    SQLiteCollaborationEventStore,
    SQLiteCollaborationHistorySearch,
    compose_sqlite_conversation_archive,
    compose_sqlite_conversation_storage,
    preflight_sqlite_conversation_storage,
    provision_sqlite_conversation_storage,
)
from story_writing_agents.conversation_storage.sqlite_search_index import (
    SQLITE_CONVERSATION_SEARCH_INDEX_PATH,
    SQLiteConversationSearchIndex,
    SQLiteConversationSearchIndexError,
)

from tests.conversation_catalog_contract import ConversationCatalogContractMixin
from tests.conversation_search_contract import CollaborationSearchContractMixin
from tests.sqlite_conversation_storage_fixture import (
    database_path,
    initialized_workspace,
    provision,
    provisioned_services,
    raw_connection,
    storage_snapshot,
)
from tests.conversation_archive_fixture import (
    archive_conversation,
    archive_from_conversations,
    event_images,
    nonempty_archive,
    representative_archive,
)


def _archive_conversations(archive):  # type: ignore[no-untyped-def]
    """Flatten one canonical archive in its established order."""

    return tuple(
        conversation
        for session in archive.sessions
        for conversation in session.conversations
    )


def _insert_frozen_legacy_event(
    connection: sqlite3.Connection,
    *,
    session_id: str,
    conversation_id: str,
) -> None:
    """Insert one schema-v1 sentinel row without restoring a public writer."""

    connection.execute(
        "INSERT INTO conversation_events "
        "(session_id, conversation_id, sequence, schema_version, turn_id, "
        "turn_slot, timestamp, sender, recipient, kind, content, "
        "searchable_content) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            session_id,
            conversation_id,
            1,
            1,
            "legacy_turn",
            0,
            "2026-08-25T00:00:00+00:00",
            "user",
            LEAD_ORCHESTRATOR,
            "user_message",
            "legacy transcript row",
            "legacy transcript row",
        ),
    )


class SQLiteConversationCatalogContractTests(
    ConversationCatalogContractMixin,
    unittest.TestCase,
):
    def make_catalog(self) -> ConversationCatalog:
        self.workspace = initialized_workspace(self)
        provision(self.workspace)
        return compose_sqlite_conversation_storage(self.workspace).catalog

    def test_rename_reaches_sqlite_maximum_then_fails_closed_at_limit(
        self,
    ) -> None:
        maximum = (1 << 63) - 1
        original = self.catalog.create("session_a", "Original subject")
        identity = (original.session_id, original.conversation_id)
        with raw_connection(self.workspace) as connection:
            connection.execute(
                "UPDATE conversations SET subject_revision = ? "
                "WHERE session_id = ? AND conversation_id = ?",
                (maximum - 1, *identity),
            )

        successful_subject = "  Maximum Café revision  "
        maximum_revision = self.catalog.rename(
            original.session_id,
            original.conversation_id,
            successful_subject,
        )
        self.assertEqual(maximum_revision.subject_revision, maximum)
        self.assertEqual(maximum_revision.subject, successful_subject)
        self.assertEqual(
            self.catalog.get(*identity),
            maximum_revision,
        )
        self.assertIs(
            preflight_sqlite_conversation_storage(self.workspace).capability,
            ConversationStorageCapability.READY,
        )

        metadata_sql = (
            "SELECT schema_version, session_id, conversation_id, subject, "
            "normalized_subject, subject_revision, status, created_at, updated_at "
            "FROM conversations WHERE session_id = ? AND conversation_id = ?"
        )
        with raw_connection(self.workspace) as connection:
            logical_before = connection.execute(metadata_sql, identity).fetchone()
        self.assertIsNotNone(logical_before)
        assert logical_before is not None
        self.assertEqual(
            logical_before[3:6],
            (
                successful_subject,
                normalize_conversation_subject(successful_subject),
                maximum,
            ),
        )
        before = storage_snapshot(self.workspace)

        attempted_subject = "overflow sentinel must not persist"
        with self.assertRaises(WorkspaceFormatError) as caught:
            self.catalog.rename(
                original.session_id,
                original.conversation_id,
                attempted_subject,
            )

        self.assertEqual(
            str(caught.exception),
            "invalid SQLite conversation storage",
        )
        self.assertIsNone(caught.exception.__cause__)
        self.assertNotIn("sentinel", str(caught.exception).casefold())
        self.assertNotIn("too large", str(caught.exception).casefold())
        self.assertEqual(storage_snapshot(self.workspace), before)
        with raw_connection(self.workspace) as connection:
            logical_after = connection.execute(metadata_sql, identity).fetchone()
        self.assertEqual(logical_after, logical_before)
        self.assertEqual(self.catalog.get(*identity), maximum_revision)


class SQLiteCollaborationSearchContractTests(
    CollaborationSearchContractMixin,
    unittest.TestCase,
):
    def make_adapters(
        self,
    ) -> tuple[SQLiteConversationCatalog, SQLiteCollaborationEventStore]:
        self.workspace = initialized_workspace(self)
        provision(self.workspace)
        self.services = compose_sqlite_conversation_storage(self.workspace)
        self.assertIsInstance(self.services.catalog, SQLiteConversationCatalog)
        self.assertIsInstance(
            self.services.event_store,
            SQLiteCollaborationEventStore,
        )
        self.assertIsInstance(
            self.services.search,
            SQLiteCollaborationHistorySearch,
        )
        return self.services.catalog, self.services.event_store

    def make_search(
        self,
        catalog: object,
        store: object,
    ) -> SQLiteCollaborationHistorySearch:
        del catalog, store
        return self.services.search

    def test_sqlite_results_equal_the_reference_v2_search(self) -> None:
        query = CollaborationHistorySearchQuery("Consult specialists")
        scope = CollaborationHistorySearchScope(self.session_id)
        reference = ReferenceCollaborationHistorySearch(
            self.catalog,
            self.store,
        )
        self.assertEqual(
            self.services.search.search(scope, query),
            reference.search(scope, query),
        )


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
            "SQLiteCollaborationEventStore",
            "SQLiteCollaborationHistorySearch",
            "compose_sqlite_conversation_archive",
            "compose_sqlite_conversation_storage",
            "preflight_sqlite_conversation_storage",
            "probe_sqlite_conversation_storage_runtime",
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
        self.assertEqual(SQLITE_CONVERSATION_STORAGE_SCHEMA_VERSION, 2)
        self.assertIs(
            SQLiteCollaborationEventStore,
            sqlite_module.SQLiteCollaborationEventStore,
        )
        self.assertEqual(
            SQLITE_CONVERSATION_SEARCH_INDEX_PATH,
            "discussion_logs/.conversation_storage_fts5.sqlite3",
        )
        self.assertEqual(
            search_index_module.SQLITE_CONVERSATION_SEARCH_INDEX_SCHEMA_VERSION,
            2,
        )
        self.assertEqual(
            tuple(search_index_module.__all__),
            (
                "SQLITE_CONVERSATION_SEARCH_INDEX_PATH",
                "SQLITE_CONVERSATION_SEARCH_INDEX_SCHEMA_VERSION",
                "SQLiteConversationSearchIndex",
                "SQLiteConversationSearchIndexDocument",
                "SQLiteConversationSearchIndexError",
            ),
        )
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
import story_writing_agents.conversation_storage.sqlite_search_index as index_owner
import story_writing_agents.conversation_literal_search as literal_owner
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
    'probe_sqlite_runtime',
)
assert tuple(index_owner.__all__) == (
    'SQLITE_CONVERSATION_SEARCH_INDEX_PATH',
    'SQLITE_CONVERSATION_SEARCH_INDEX_SCHEMA_VERSION',
    'SQLiteConversationSearchIndex',
    'SQLiteConversationSearchIndexDocument',
    'SQLiteConversationSearchIndexError',
)
assert len(literal_owner.__all__) == 6
assert 'sqlite3' not in sys.modules
assert tuple(pathlib.Path.cwd().iterdir()) == before
assert all(name not in story_writing_agents.__all__ for name in public.__all__)
assert all(name not in package.__all__ for name in public.__all__)
assert all(name not in story_writing_agents.__all__ for name in index_owner.__all__)
assert all(name not in package.__all__ for name in index_owner.__all__)
assert all(name not in story_writing_agents.__all__ for name in literal_owner.__all__)
assert all(name not in package.__all__ for name in literal_owner.__all__)
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
                "archive",
                "conversation_search",
                "conversation_literal_search",
                "conversations",
                "conversations.events",
                "errors",
                "workspace.core",
                "sqlite_database",
                "sqlite_search_index",
                "composition",
                "contracts",
            },
        )
        index_tree = ast.parse(
            Path(search_index_module.__file__).read_text(encoding="utf-8")
        )
        self.assertEqual(
            {
                node.module
                for node in ast.walk(index_tree)
                if isinstance(node, ast.ImportFrom) and node.level > 0
            },
            set(),
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
        self.assertIsInstance(services.event_store, SQLiteCollaborationEventStore)
        self.assertIsInstance(services.search, SQLiteCollaborationHistorySearch)
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

    def test_live_journal_blocks_fresh_composition_but_not_existing_reads(
        self,
    ) -> None:
        workspace, services = provisioned_services(self)
        metadata = services.catalog.create("session_a", "Committed subject")
        journal = Path(f"{database_path(workspace)}-journal")

        with raw_connection(workspace) as connection:
            self.assertEqual(
                connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0], "delete"
            )
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE conversations SET subject = ?, normalized_subject = ? "
                "WHERE session_id = ? AND conversation_id = ?",
                (
                    "Uncommitted subject",
                    "uncommitted subject",
                    metadata.session_id,
                    metadata.conversation_id,
                ),
            )
            self.assertTrue(journal.is_file())
            self.assertEqual(
                services.catalog.get(
                    metadata.session_id,
                    metadata.conversation_id,
                ).subject,
                "Committed subject",
            )
            before = storage_snapshot(workspace)

            with self.assertRaises(WorkspaceFormatError) as caught:
                compose_sqlite_conversation_storage(workspace)

            self.assertEqual(
                str(caught.exception),
                "invalid SQLite conversation storage",
            )
            self.assertIsNone(caught.exception.__cause__)
            self.assertEqual(storage_snapshot(workspace), before)
            connection.execute("COMMIT")

        self.assertFalse(journal.exists())
        self.assertEqual(
            compose_sqlite_conversation_storage(workspace)
            .catalog.get(metadata.session_id, metadata.conversation_id)
            .subject,
            "Uncommitted subject",
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
        self.assertEqual(int.from_bytes(first[60:64], "big"), 2)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
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
                "turn_slot, timestamp, sender, recipient, kind, content, "
                "searchable_content) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    "orphan",
                ),
            )
        before = storage_snapshot(workspace)
        with self.assertRaises(WorkspaceFormatError):
            preflight_sqlite_conversation_storage(workspace)
        self.assertEqual(storage_snapshot(workspace), before)

    def test_catalog_create_rejects_orphan_id_collision_before_metadata_insert(
        self,
    ) -> None:
        workspace, services = provisioned_services(self)
        allocated_hex = "0123456789abcdef0123456789abcdef"
        conversation_id = f"c_{allocated_hex}"
        with raw_connection(workspace) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(
                "INSERT INTO conversation_events "
                "(session_id, conversation_id, sequence, schema_version, turn_id, "
                "turn_slot, timestamp, sender, recipient, kind, content, "
                "searchable_content) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "session_a",
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
                    "orphan event",
                ),
            )
        before = storage_snapshot(workspace)
        allocated_uuid = mock.Mock(hex=allocated_hex)

        with (
            mock.patch.object(
                sqlite_module,
                "uuid4",
                return_value=allocated_uuid,
            ) as uuid4,
            mock.patch.object(
                sqlite_module,
                "_insert_metadata",
                wraps=sqlite_module._insert_metadata,
            ) as insert_metadata,
        ):
            with self.assertRaises(WorkspaceFormatError) as caught:
                services.catalog.create("session_a", "Collision witness")

        uuid4.assert_not_called()
        insert_metadata.assert_not_called()
        self.assertEqual(
            str(caught.exception),
            "invalid SQLite conversation storage",
        )
        self.assertIsNone(caught.exception.__cause__)
        self.assertEqual(storage_snapshot(workspace), before)
        with raw_connection(workspace) as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM conversations "
                    "WHERE session_id = ? AND conversation_id = ?",
                    ("session_a", conversation_id),
                ).fetchone()
            )
            self.assertEqual(
                connection.execute(
                    "SELECT content FROM conversation_events "
                    "WHERE session_id = ? AND conversation_id = ?",
                    ("session_a", conversation_id),
                ).fetchall(),
                [("orphan event",)],
            )


class SQLiteArchiveTests(unittest.TestCase):
    def import_payload(
        self,
        workspace: Workspace,
        payload: object,
    ):
        archive = compose_sqlite_conversation_archive(workspace)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            return archive.import_archive(lease, payload)  # type: ignore[arg-type]

    def export_payload(self, workspace: Workspace):  # type: ignore[no-untyped-def]
        archive = compose_sqlite_conversation_archive(workspace)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            return archive.export_archive(lease)

    @staticmethod
    def authoritative_rows(workspace: Workspace) -> tuple[object, ...]:
        with raw_connection(workspace) as connection:
            return (
                tuple(
                    connection.execute(
                        "SELECT * FROM conversations ORDER BY session_id, conversation_id"
                    ).fetchall()
                ),
                tuple(
                    connection.execute(
                        "SELECT * FROM collaboration_events "
                        "ORDER BY session_id, conversation_id, sequence"
                    ).fetchall()
                ),
                tuple(
                    connection.execute(
                        "SELECT * FROM conversation_events "
                        "ORDER BY session_id, conversation_id, sequence"
                    ).fetchall()
                ),
            )

    def test_v2_archive_round_trip_is_deterministic_byte_and_codec_exact(self) -> None:
        payload = representative_archive()
        source = initialized_workspace(self)
        provision(source)
        receipt = self.import_payload(source, payload)
        first = self.export_payload(source)
        second = self.export_payload(source)
        self.assertIs(receipt.status, ConversationArchiveImportStatus.IMPORTED)
        self.assertEqual(first, payload)
        self.assertEqual(second, payload)
        self.assertEqual(event_images(first), event_images(payload))
        self.assertEqual(len(event_images(first)), 25)

        target = initialized_workspace(self)
        provision(target)
        imported = self.import_payload(target, first)
        self.assertIs(imported.status, ConversationArchiveImportStatus.IMPORTED)
        self.assertEqual(self.export_payload(target), payload)
        before = storage_snapshot(target)
        unchanged = self.import_payload(target, payload)
        self.assertIs(unchanged.status, ConversationArchiveImportStatus.UNCHANGED)
        self.assertEqual(storage_snapshot(target), before)

    def test_import_is_one_transaction_for_metadata_events_and_all_batches(
        self,
    ) -> None:
        target = initialized_workspace(self)
        provision(target)
        before = self.authoritative_rows(target)
        original = sqlite_module._insert_collaboration_event
        calls = 0

        def fail_late(connection, event):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            if calls == 8:
                raise sqlite_module.SQLiteDatabaseFormatError()
            return original(connection, event)

        with mock.patch.object(
            sqlite_module,
            "_insert_collaboration_event",
            new=fail_late,
        ):
            with self.assertRaises(WorkspaceFormatError):
                self.import_payload(target, representative_archive())
        self.assertEqual(calls, 8)
        self.assertEqual(self.authoritative_rows(target), before)
        self.assertEqual(self.export_payload(target).sessions, ())

        imported = self.import_payload(target, representative_archive())
        self.assertIs(imported.status, ConversationArchiveImportStatus.IMPORTED)
        self.assertEqual(self.export_payload(target), representative_archive())

    def test_subject_and_collaboration_integer_bounds_preflight_before_io(self) -> None:
        maximum = (1 << 63) - 1
        payload = nonempty_archive()
        conversation = payload.sessions[0].conversations[0]
        object.__setattr__(conversation, "subject_revision", maximum)
        target = initialized_workspace(self)
        provision(target)
        self.assertIs(
            self.import_payload(target, payload).status,
            ConversationArchiveImportStatus.IMPORTED,
        )
        self.assertEqual(self.export_payload(target), payload)

        overflow = nonempty_archive()
        object.__setattr__(
            overflow.sessions[0].conversations[0],
            "subject_revision",
            maximum + 1,
        )
        rejected = initialized_workspace(self)
        provision(rejected)
        before = self.authoritative_rows(rejected)
        with mock.patch.object(
            sqlite_module,
            "_insert_metadata",
            wraps=sqlite_module._insert_metadata,
        ) as insert_metadata:
            with self.assertRaises(WorkspaceFormatError):
                self.import_payload(rejected, overflow)
        insert_metadata.assert_not_called()
        self.assertEqual(self.authoritative_rows(rejected), before)

    def test_any_schema_one_database_row_rejects_export_and_import_before_mutation(
        self,
    ) -> None:
        source, services = provisioned_services(self)
        metadata = services.catalog.create("legacy_session", "Legacy SQLite")
        with raw_connection(source) as connection:
            _insert_frozen_legacy_event(
                connection,
                session_id=metadata.session_id,
                conversation_id=metadata.conversation_id,
            )
        before = self.authoritative_rows(source)
        with self.assertRaises(WorkspaceFormatError):
            self.export_payload(source)
        self.assertEqual(self.authoritative_rows(source), before)

        destination, destination_services = provisioned_services(self)
        legacy = destination_services.catalog.create(
            "unrelated_session", "Unrelated legacy"
        )
        with raw_connection(destination) as connection:
            _insert_frozen_legacy_event(
                connection,
                session_id=legacy.session_id,
                conversation_id=legacy.conversation_id,
            )
        before = self.authoritative_rows(destination)
        with mock.patch.object(
            sqlite_module,
            "_insert_collaboration_event",
            wraps=sqlite_module._insert_collaboration_event,
        ) as insert_event:
            with self.assertRaises(WorkspaceFormatError):
                self.import_payload(destination, representative_archive())
        insert_event.assert_not_called()
        self.assertEqual(self.authoritative_rows(destination), before)

    def test_conflict_and_postvalidation_corruption_roll_back_every_record(
        self,
    ) -> None:
        payload = representative_archive()
        target = initialized_workspace(self)
        provision(target)
        conflict = payload.sessions[0].conversations[-1]
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
                    "Divergent subject",
                    normalize_conversation_subject("Divergent subject"),
                    conflict.subject_revision,
                    conflict.status.value,
                    conflict.created_at,
                    conflict.updated_at,
                ),
            )
        before = self.authoritative_rows(target)
        with self.assertRaises(ConversationArchiveConflictError):
            self.import_payload(target, payload)
        self.assertEqual(self.authoritative_rows(target), before)

        clean = initialized_workspace(self)
        provision(clean)
        before = self.authoritative_rows(clean)
        validator = sqlite_module._validate_database_snapshot
        calls = 0

        def corrupt_postwrite(connection):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            if calls == 4:
                connection.execute(
                    "UPDATE collaboration_events SET batch_digest = ? "
                    "WHERE rowid = (SELECT MIN(rowid) FROM collaboration_events)",
                    ("sha256:" + "0" * 64,),
                )
            validator(connection)

        with mock.patch.object(
            sqlite_module,
            "_validate_database_snapshot",
            new=corrupt_postwrite,
        ):
            with self.assertRaises(WorkspaceFormatError):
                self.import_payload(clean, payload)
        self.assertEqual(calls, 4)
        self.assertEqual(self.authoritative_rows(clean), before)

    def test_unrelated_v2_records_are_preserved_and_wrong_lease_fails_closed(
        self,
    ) -> None:
        target = initialized_workspace(self)
        provision(target)
        extra = archive_from_conversations(
            archive_conversation(
                session_id="extra_session",
                conversation_id="extra_conversation",
            )
        )
        self.import_payload(target, extra)
        self.import_payload(target, representative_archive())
        combined = self.export_payload(target)
        identities = {
            (session.session_id, conversation.conversation_id)
            for session in combined.sessions
            for conversation in session.conversations
        }
        self.assertIn(("extra_session", "extra_conversation"), identities)
        self.assertEqual(len(identities), 4)

        archive = compose_sqlite_conversation_archive(target)
        other = initialized_workspace(self)
        coordinator = WorkspaceConversationStorageMutationCoordinator(other)
        before = self.authoritative_rows(target)
        with coordinator.borrow(timeout_seconds=2.0) as wrong:
            with self.assertRaises(ConversationStorageCoordinationError):
                archive.export_archive(wrong)
        self.assertEqual(self.authoritative_rows(target), before)

    def test_fts_companion_and_best_effort_failure_never_change_archive_authority(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        provision(workspace)
        companion = workspace.path(SQLITE_CONVERSATION_SEARCH_INDEX_PATH)
        if companion.exists():
            companion.unlink()
        companion.write_bytes(b"private FTS-only sentinel\x00")
        payload = representative_archive()
        with mock.patch.object(
            SQLiteConversationSearchIndex,
            "rebuild",
            side_effect=SQLiteConversationSearchIndexError(),
        ):
            receipt = self.import_payload(workspace, payload)
        self.assertIs(receipt.status, ConversationArchiveImportStatus.IMPORTED)
        self.assertEqual(self.export_payload(workspace), payload)
        self.assertEqual(
            event_images(self.export_payload(workspace)), event_images(payload)
        )
        self.assertEqual(companion.read_bytes(), b"private FTS-only sentinel\x00")


class SQLiteProvisioningConcurrencyTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "requires POSIX advisory locking")
    def test_spawned_process_provisioners_install_one_complete_database(self) -> None:
        workspace = initialized_workspace(self)
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        results = context.Queue()
        processes = [
            context.Process(
                target=_provision_process,
                args=(workspace, start, results),
            )
            for _ in range(2)
        ]
        try:
            for process in processes:
                process.start()
            start.set()
            for process in processes:
                process.join(timeout=15)
            alive = tuple(process.is_alive() for process in processes)
            exitcodes = tuple(process.exitcode for process in processes)
            outcomes = [results.get(timeout=2) for _ in processes]
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
                process.close()
            results.close()
            results.join_thread()
        self.assertEqual(alive, (False,) * len(processes))
        self.assertEqual(exitcodes, (0,) * len(processes))
        self.assertEqual(outcomes, ["provisioned", "provisioned"])
        self.assertIs(
            preflight_sqlite_conversation_storage(workspace).capability,
            ConversationStorageCapability.READY,
        )
        target = database_path(workspace)
        self.assertEqual(
            list(target.parent.glob(".conversation_storage.sqlite3.*.tmp")),
            [],
        )


def _provision_process(workspace: Workspace, start: object, results: object) -> None:
    """Provision through one transferred authorized workspace lease."""

    start.wait()  # type: ignore[attr-defined]
    try:
        provision(workspace)
    except BaseException as exc:
        results.put(("unexpected", type(exc).__name__, str(exc)))  # type: ignore[attr-defined]
        raise
    results.put("provisioned")  # type: ignore[attr-defined]


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


if __name__ == "__main__":
    unittest.main()
