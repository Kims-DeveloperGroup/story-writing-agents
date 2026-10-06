"""Public planning, transfer, checkpoint, and coordination tests."""

from __future__ import annotations

from tests.conversation_archive_fixture import one_conversation_archive
from tests.test_collaboration_events import editorial_history
import ast
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from copy import copy
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest import mock

import story_writing_agents
import story_writing_agents.conversation_storage as storage_package
from story_writing_agents import (
    FilesystemConversationCatalog,
    Workspace,
)
from story_writing_agents.conversation_storage import (
    CONVERSATION_STORAGE_ACTIVATION_STATE_PATH,
    CONVERSATION_STORAGE_MUTATION_GUARD_PATH,
    ConversationStorageActivationState,
    ConversationStorageBackend,
    ConversationStorageParity,
    FilesystemConversationArchiveAdapter,
    WorkspaceConversationStorageMutationCoordinator,
    compose_filesystem_conversation_archive,
    compose_filesystem_conversation_storage,
)
from story_writing_agents.conversation_storage import (
    migration as migration_module,
)
from story_writing_agents.conversation_storage.migration import (
    CONVERSATION_STORAGE_TRANSFER_CHECKPOINT_DIRECTORY,
    CONVERSATION_STORAGE_TRANSFER_SCHEMA_VERSION,
    ConversationStorageTransferOperation,
    ConversationStorageTransferOutcome,
    ConversationStorageTransferReport,
    transfer_conversation_storage,
    verify_conversation_storage,
)
from story_writing_agents.conversation_storage.sqlite import (
    SQLITE_CONVERSATION_STORAGE_DATABASE_PATH,
    SQLiteConversationArchiveAdapter,
    compose_sqlite_conversation_storage,
)
from story_writing_agents.conversation_storage import sqlite as sqlite_module

from tests.conversation_archive_fixture import (
    archive_conversation,
    archive_from_conversations,
)
from tests.conversation_storage_migration_fixture import (
    NamespaceEntry,
    checkpoint_path,
    checkpoint_relative,
    export_backend,
    import_backend,
    transfer_workspace,
    namespace_snapshot,
    representative_archive,
)
from tests.sqlite_conversation_storage_fixture import raw_connection


def _other_backend(backend: ConversationStorageBackend) -> ConversationStorageBackend:
    return (
        ConversationStorageBackend.SQLITE
        if backend is ConversationStorageBackend.FILESYSTEM
        else ConversationStorageBackend.FILESYSTEM
    )


def _transfer(
    workspace: Workspace,
    source: ConversationStorageBackend,
    *,
    dry_run: bool = False,
) -> ConversationStorageTransferReport:
    return transfer_conversation_storage(
        workspace,
        source_backend=source,
        destination_backend=_other_backend(source),
        dry_run=dry_run,
    )


def _write_frozen_legacy_transcript(
    workspace: Workspace,
    *,
    session_id: str,
    conversation_id: str,
) -> None:
    workspace.write_text(
        Path("discussion_logs")
        / session_id
        / "interactive"
        / "conversations"
        / conversation_id
        / "transcript.jsonl",
        '{"schema_version":1,"legacy":"transcript sentinel"}\n',
    )


def _insert_frozen_legacy_event(
    connection: object,
    *,
    session_id: str,
    conversation_id: str,
) -> None:
    connection.execute(  # type: ignore[attr-defined]
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
            "lead_orchestrator",
            "user_message",
            "legacy transcript row",
            "legacy transcript row",
        ),
    )


class ConversationStorageTransferSurfaceTests(unittest.TestCase):
    def test_direct_surface_report_and_facades_are_exact(self) -> None:
        expected = (
            "CONVERSATION_STORAGE_TRANSFER_SCHEMA_VERSION",
            "CONVERSATION_STORAGE_TRANSFER_CHECKPOINT_DIRECTORY",
            "ConversationStorageTransferOperation",
            "ConversationStorageTransferOutcome",
            "ConversationStorageTransferReport",
            "transfer_conversation_storage",
            "verify_conversation_storage",
        )
        self.assertEqual(tuple(migration_module.__all__), expected)
        self.assertEqual(CONVERSATION_STORAGE_TRANSFER_SCHEMA_VERSION, 1)
        self.assertEqual(
            CONVERSATION_STORAGE_TRANSFER_CHECKPOINT_DIRECTORY,
            "agent_states/orchestrator_state/conversation_storage_transfers",
        )
        self.assertEqual(
            tuple(ConversationStorageTransferOperation),
            (
                ConversationStorageTransferOperation.TRANSFER,
                ConversationStorageTransferOperation.VERIFY,
            ),
        )
        self.assertEqual(
            tuple(ConversationStorageTransferOutcome),
            (
                ConversationStorageTransferOutcome.COMPLETED,
                ConversationStorageTransferOutcome.WORK_REQUIRED,
                ConversationStorageTransferOutcome.FAILED,
            ),
        )
        for name in expected:
            with self.subTest(name=name):
                self.assertNotIn(name, storage_package.__all__)
                self.assertNotIn(name, story_writing_agents.__all__)
                self.assertFalse(hasattr(storage_package, name))
                self.assertFalse(hasattr(story_writing_agents, name))
        for old_name in (
            "CONVERSATION_STORAGE_MIGRATION_SCHEMA_VERSION",
            "CONVERSATION_STORAGE_MIGRATION_CHECKPOINT_DIRECTORY",
            "ConversationStorageMigrationOperation",
            "ConversationStorageMigrationOutcome",
            "ConversationStorageMigrationReport",
            "migrate_conversation_storage",
        ):
            self.assertNotIn(old_name, migration_module.__all__)
            self.assertFalse(hasattr(migration_module, old_name))
            self.assertFalse(hasattr(storage_package, old_name))
            self.assertFalse(hasattr(story_writing_agents, old_name))
        self.assertEqual(len(storage_package.__all__), 63)
        self.assertEqual(len(story_writing_agents.__all__), 106)

        report = ConversationStorageTransferReport(
            operation=ConversationStorageTransferOperation.TRANSFER,
            outcome=ConversationStorageTransferOutcome.WORK_REQUIRED,
            source_backend=ConversationStorageBackend.FILESYSTEM,
            destination_backend=ConversationStorageBackend.SQLITE,
            dry_run=True,
            parity=ConversationStorageParity.UNVERIFIED,
            create=2,
            identical=1,
            conflict=0,
            unsupported=0,
            destination_only=0,
            source_conversations=3,
            source_events=4,
            checkpoint="none",
            failure=None,
        )
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(
            report.to_dict(),
            {
                "schema_version": 1,
                "operation": "transfer",
                "outcome": "work_required",
                "from": "filesystem",
                "to": "sqlite",
                "dry_run": True,
                "parity": "unverified",
                "counts": {
                    "create": 2,
                    "identical": 1,
                    "conflict": 0,
                    "unsupported": 0,
                    "destination_only": 0,
                },
                "source": {"conversations": 3, "events": 4},
                "checkpoint": "none",
                "failure": None,
            },
        )
        self.assertEqual(
            report.to_json(),
            json.dumps(
                report.to_dict(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        with self.assertRaises(FrozenInstanceError):
            report.create = 9  # type: ignore[misc]

        scoped_sources = (
            Path(migration_module.__file__).read_text(encoding="utf-8"),
            Path(sqlite_module.__file__).read_text(encoding="utf-8"),
        )
        self.assertTrue(all("ALTER TABLE" not in source for source in scoped_sources))

    def test_imports_are_lazy_dependency_exact_and_io_free(self) -> None:
        dependencies = {
            node.module
            for node in ast.walk(
                ast.parse(Path(migration_module.__file__).read_text(encoding="utf-8"))
            )
            if isinstance(node, ast.ImportFrom) and node.level > 0
        }
        self.assertEqual(
            dependencies,
            {
                "errors",
                "workspace.core",
                "archive",
                "composition",
                "contracts",
                "filesystem_archive",
                "sqlite",
            },
        )
        repository = Path(__file__).resolve().parents[1]
        source = repository / "src"
        script = f"""
import pathlib
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, {str(source)!r})
before = tuple(pathlib.Path.cwd().iterdir())
import story_writing_agents
import story_writing_agents.cli
import story_writing_agents.conversation_storage
assert 'story_writing_agents.conversation_storage.migration' not in sys.modules
assert 'sqlite3' not in sys.modules
import story_writing_agents.conversation_storage.migration as transfer
assert tuple(transfer.__all__) == {tuple(migration_module.__all__)!r}
assert 'sqlite3' not in sys.modules
assert tuple(pathlib.Path.cwd().iterdir()) == before
"""
        with tempfile.TemporaryDirectory(prefix="transfer-import-") as temporary:
            completed = subprocess.run(
                [sys.executable, "-I", "-B", "-c", script],
                cwd=temporary,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)


class ConversationStorageTransferPlanningTests(unittest.TestCase):
    def test_any_legacy_source_or_destination_rejects_before_transfer_mutation(
        self,
    ) -> None:
        filesystem_source = transfer_workspace(self)
        catalog = FilesystemConversationCatalog(filesystem_source)
        metadata = catalog.create("legacy_session", "Legacy filesystem source")
        _write_frozen_legacy_transcript(
            filesystem_source,
            session_id=metadata.session_id,
            conversation_id=metadata.conversation_id,
        )
        with raw_connection(filesystem_source) as connection:
            sqlite_before = (
                tuple(connection.execute("SELECT * FROM conversations").fetchall()),
                tuple(
                    connection.execute("SELECT * FROM collaboration_events").fetchall()
                ),
            )
        source_report = _transfer(
            filesystem_source, ConversationStorageBackend.FILESYSTEM
        )
        self.assertEqual(source_report.failure, "source_invalid")
        with raw_connection(filesystem_source) as connection:
            self.assertEqual(
                (
                    tuple(connection.execute("SELECT * FROM conversations").fetchall()),
                    tuple(
                        connection.execute(
                            "SELECT * FROM collaboration_events"
                        ).fetchall()
                    ),
                ),
                sqlite_before,
            )
        self.assertFalse(
            checkpoint_path(
                filesystem_source, ConversationStorageBackend.FILESYSTEM
            ).exists()
        )

        sqlite_destination = transfer_workspace(self)
        import_backend(
            sqlite_destination,
            ConversationStorageBackend.FILESYSTEM,
            representative_archive(),
        )
        services = compose_sqlite_conversation_storage(sqlite_destination)
        legacy = services.catalog.create("legacy_target", "Legacy SQLite target")
        with raw_connection(sqlite_destination) as connection:
            _insert_frozen_legacy_event(
                connection,
                session_id=legacy.session_id,
                conversation_id=legacy.conversation_id,
            )
        with raw_connection(sqlite_destination) as connection:
            destination_before = (
                tuple(connection.execute("SELECT * FROM conversations").fetchall()),
                tuple(
                    connection.execute("SELECT * FROM conversation_events").fetchall()
                ),
                tuple(
                    connection.execute("SELECT * FROM collaboration_events").fetchall()
                ),
            )
        destination_report = _transfer(
            sqlite_destination, ConversationStorageBackend.FILESYSTEM
        )
        self.assertEqual(destination_report.failure, "destination_invalid")
        with raw_connection(sqlite_destination) as connection:
            self.assertEqual(
                (
                    tuple(connection.execute("SELECT * FROM conversations").fetchall()),
                    tuple(
                        connection.execute(
                            "SELECT * FROM conversation_events"
                        ).fetchall()
                    ),
                    tuple(
                        connection.execute(
                            "SELECT * FROM collaboration_events"
                        ).fetchall()
                    ),
                ),
                destination_before,
            )
        self.assertFalse(
            checkpoint_path(
                sqlite_destination, ConversationStorageBackend.FILESYSTEM
            ).exists()
        )

    def test_mixed_valid_and_sqlite_unsupported_records_block_every_create(
        self,
    ) -> None:
        workspace = transfer_workspace(self)
        payload = archive_from_conversations(
            archive_conversation(
                session_id="session_a",
                conversation_id="conversation_valid",
                subject="valid create sentinel",
                subject_revision=(1 << 63) - 1,
            ),
            archive_conversation(
                session_id="session_b",
                conversation_id="conversation_unsupported",
                subject="unsupported private sentinel",
                subject_revision=1 << 63,
            ),
        )
        import_backend(workspace, ConversationStorageBackend.FILESYSTEM, payload)
        before = namespace_snapshot(workspace.root)

        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                report = _transfer(
                    workspace,
                    ConversationStorageBackend.FILESYSTEM,
                    dry_run=dry_run,
                )
                self.assertEqual(
                    (
                        report.create,
                        report.unsupported,
                        report.outcome,
                        report.failure,
                        report.exit_code,
                    ),
                    (
                        1,
                        1,
                        ConversationStorageTransferOutcome.FAILED,
                        "unsupported",
                        2,
                    ),
                )
                self.assertNotIn("private sentinel", report.to_json())

        with raw_connection(workspace) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT session_id, conversation_id FROM conversations"
                ).fetchall(),
                [],
            )
        self.assertFalse(
            checkpoint_path(workspace, ConversationStorageBackend.FILESYSTEM).exists()
        )
        after = namespace_snapshot(workspace.root)
        self.assertEqual(
            {key: value for key, value in after.items() if key in before},
            before,
        )

    def test_dry_run_classifies_create_identical_conflict_and_destination_only(
        self,
    ) -> None:
        source_create = archive_conversation(
            session_id="session_a",
            conversation_id="create_record",
            subject="create",
        )
        identical = archive_conversation(
            session_id="session_a",
            conversation_id="identical_record",
            subject="identical",
        )
        conflict_source = archive_conversation(
            session_id="session_b",
            conversation_id="conflict_record",
            subject="source conflict",
        )
        conflict_destination = archive_conversation(
            session_id="session_b",
            conversation_id="conflict_record",
            subject="destination conflict",
        )
        destination_only = archive_conversation(
            session_id="session_c",
            conversation_id="destination_only",
            subject="destination only",
        )
        source_payload = archive_from_conversations(
            source_create, identical, conflict_source
        )
        destination_payload = archive_from_conversations(
            identical, conflict_destination, destination_only
        )
        for source in ConversationStorageBackend:
            with self.subTest(source=source):
                workspace = transfer_workspace(self, active_backend=source)
                import_backend(workspace, source, source_payload)
                import_backend(workspace, _other_backend(source), destination_payload)
                before = namespace_snapshot(workspace.root)

                first = _transfer(workspace, source, dry_run=True)
                second = _transfer(workspace, source, dry_run=True)

                self.assertEqual(first, second)
                self.assertEqual(
                    (
                        first.create,
                        first.identical,
                        first.conflict,
                        first.unsupported,
                        first.destination_only,
                        first.failure,
                    ),
                    (1, 1, 1, 0, 1, "conflict"),
                )
                self.assertEqual(first.exit_code, 2)
                self.assertEqual(namespace_snapshot(workspace.root), before)

    def test_destination_only_blocks_actual_transfer_without_any_mutation(
        self,
    ) -> None:
        source = archive_conversation(
            session_id="session_a",
            conversation_id="shared_record",
            subject="shared",
        )
        destination_only = archive_conversation(
            session_id="session_b",
            conversation_id="destination_only",
            subject="destination-only private sentinel",
        )
        workspace = transfer_workspace(self)
        import_backend(
            workspace,
            ConversationStorageBackend.FILESYSTEM,
            archive_from_conversations(source),
        )
        import_backend(
            workspace,
            ConversationStorageBackend.SQLITE,
            archive_from_conversations(source, destination_only),
        )
        before = namespace_snapshot(workspace.root)

        report = _transfer(workspace, ConversationStorageBackend.FILESYSTEM)

        self.assertEqual(
            (
                report.identical,
                report.destination_only,
                report.outcome,
                report.failure,
                report.exit_code,
            ),
            (
                1,
                1,
                ConversationStorageTransferOutcome.FAILED,
                "destination_only",
                2,
            ),
        )
        self.assertNotIn("private sentinel", report.to_json())
        self.assertEqual(namespace_snapshot(workspace.root), before)
        self.assertFalse(
            checkpoint_path(workspace, ConversationStorageBackend.FILESYSTEM).exists()
        )

    def test_empty_create_and_identical_plans_are_deterministic_both_directions(
        self,
    ) -> None:
        payload = representative_archive()
        for source in ConversationStorageBackend:
            with self.subTest(source=source):
                workspace = transfer_workspace(self, active_backend=source)
                empty = _transfer(workspace, source, dry_run=True)
                self.assertEqual(
                    empty.outcome, ConversationStorageTransferOutcome.COMPLETED
                )
                self.assertEqual(empty.parity, ConversationStorageParity.VERIFIED)
                self.assertEqual(empty.source_conversations, 0)
                self.assertFalse(checkpoint_path(workspace, source).exists())

                import_backend(workspace, source, payload)
                create = _transfer(workspace, source, dry_run=True)
                self.assertEqual(create.create, 3)
                self.assertEqual(create.identical, 0)
                self.assertEqual(
                    create.outcome, ConversationStorageTransferOutcome.WORK_REQUIRED
                )
                self.assertEqual(create.source_events, 43)
                self.assertFalse(checkpoint_path(workspace, source).exists())

                import_backend(workspace, _other_backend(source), payload)
                identical = _transfer(workspace, source, dry_run=True)
                self.assertEqual(identical.create, 0)
                self.assertEqual(identical.identical, 3)
                self.assertEqual(identical.parity, ConversationStorageParity.VERIFIED)
                self.assertFalse(checkpoint_path(workspace, source).exists())

    def test_preconditions_fail_without_provisioning_or_authoritative_mutation(
        self,
    ) -> None:
        workspace = transfer_workspace(self, sqlite_ready=False)
        before = namespace_snapshot(workspace.root)
        same = transfer_conversation_storage(
            workspace,
            source_backend="filesystem",
            destination_backend="filesystem",
        )
        self.assertEqual((same.failure, same.exit_code), ("invalid_request", 2))
        self.assertEqual(namespace_snapshot(workspace.root), before)

        unavailable = _transfer(workspace, ConversationStorageBackend.FILESYSTEM)
        self.assertEqual(unavailable.failure, "provisioning_needed")
        self.assertFalse(
            workspace.path(SQLITE_CONVERSATION_STORAGE_DATABASE_PATH).exists()
        )
        self.assertFalse(
            checkpoint_path(workspace, ConversationStorageBackend.FILESYSTEM).exists()
        )
        guard = workspace.path(CONVERSATION_STORAGE_MUTATION_GUARD_PATH)
        self.assertEqual(guard.read_bytes(), b"")

        ready = transfer_workspace(self)
        mismatch = _transfer(ready, ConversationStorageBackend.SQLITE)
        self.assertEqual(mismatch.failure, "activation_mismatch")
        self.assertFalse(
            checkpoint_path(ready, ConversationStorageBackend.SQLITE).exists()
        )

        marker = ready.path(CONVERSATION_STORAGE_ACTIVATION_STATE_PATH)
        marker.write_bytes(b"{malformed activation sentinel")
        malformed_before = namespace_snapshot(ready.root)
        malformed = _transfer(ready, ConversationStorageBackend.FILESYSTEM)
        self.assertEqual(malformed.failure, "activation_invalid")
        self.assertEqual(namespace_snapshot(ready.root), malformed_before)
        self.assertNotIn("sentinel", malformed.to_json())


class ConversationStorageTransferExecutionTests(unittest.TestCase):
    def test_both_directions_preserve_exact_canonical_fields_and_retained_checkpoint(
        self,
    ) -> None:
        payload = representative_archive()
        for source in ConversationStorageBackend:
            with self.subTest(source=source):
                workspace = transfer_workspace(self, active_backend=source)
                import_backend(workspace, source, payload)
                activation = workspace.path(CONVERSATION_STORAGE_ACTIVATION_STATE_PATH)
                activation_before = (
                    activation.read_bytes() if activation.exists() else None
                )
                config_before = workspace.path(
                    "config/system_defaults.json"
                ).read_bytes()
                opposite_checkpoint = checkpoint_path(workspace, _other_backend(source))
                opposite_checkpoint.parent.mkdir(parents=True, exist_ok=True)
                opposite_bytes = b"unrelated direction checkpoint sentinel\n"
                opposite_checkpoint.write_bytes(opposite_bytes)

                report = _transfer(workspace, source)

                self.assertEqual(
                    report.outcome, ConversationStorageTransferOutcome.COMPLETED
                )
                self.assertEqual(report.parity, ConversationStorageParity.VERIFIED)
                self.assertEqual(report.checkpoint, "completed")
                self.assertEqual(report.create, 3)
                self.assertEqual(report.source_events, 43)
                self.assertEqual(
                    export_backend(workspace, _other_backend(source)), payload
                )
                self.assertEqual(export_backend(workspace, source), payload)
                self.assertEqual(
                    activation.read_bytes() if activation.exists() else None,
                    activation_before,
                )
                self.assertEqual(
                    workspace.path("config/system_defaults.json").read_bytes(),
                    config_before,
                )
                self.assertEqual(opposite_checkpoint.read_bytes(), opposite_bytes)
                checkpoint = checkpoint_path(workspace, source)
                checkpoint_before = checkpoint.read_bytes()
                value = json.loads(checkpoint_before)
                self.assertEqual(value["schema_version"], 1)
                self.assertEqual(
                    value["direction"],
                    f"{source.value}-to-{_other_backend(source).value}",
                )
                self.assertEqual(value["source"], {"conversations": 3, "events": 43})
                self.assertEqual(
                    value["progress"],
                    {"completed_conversations": 3, "total_conversations": 3},
                )
                self.assertEqual(len(value["completed"]), 3)
                self.assertNotIn("Unicode", checkpoint_before.decode("utf-8"))
                self.assertNotIn("runtime failure", checkpoint_before.decode("utf-8"))

                retry = _transfer(workspace, source)
                self.assertEqual(
                    retry.outcome, ConversationStorageTransferOutcome.COMPLETED
                )
                self.assertEqual(retry.identical, 3)
                self.assertEqual(checkpoint.read_bytes(), checkpoint_before)
                verified = verify_conversation_storage(
                    workspace,
                    source_backend=source,
                    destination_backend=_other_backend(source),
                )
                self.assertEqual(verified.parity, ConversationStorageParity.VERIFIED)
                self.assertEqual(checkpoint.read_bytes(), checkpoint_before)

        memory = one_conversation_archive(events=editorial_history())
        for source in ConversationStorageBackend:
            current = transfer_workspace(self, active_backend=source)
            import_backend(current, source, memory)
            report = _transfer(current, source)
            self.assertEqual((report.outcome, report.source_events), (ConversationStorageTransferOutcome.COMPLETED, 1))
            self.assertEqual(export_backend(current, _other_backend(source)), memory)
            self.assertEqual(export_backend(current, source), memory)

    def test_filesystem_to_sqlite_operational_failure_rolls_back_one_transaction(
        self,
    ) -> None:
        workspace = transfer_workspace(self)
        import_backend(
            workspace,
            ConversationStorageBackend.FILESYSTEM,
            representative_archive(),
        )
        original = sqlite_module._insert_metadata
        calls = 0

        def fail_second(connection, metadata, *, normalized_subject):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            if calls == 2:
                raise sqlite_module.SQLiteDatabaseFormatError()
            return original(
                connection,
                metadata,
                normalized_subject=normalized_subject,
            )

        with mock.patch.object(sqlite_module, "_insert_metadata", new=fail_second):
            report = _transfer(workspace, ConversationStorageBackend.FILESYSTEM)

        self.assertEqual(calls, 2)
        self.assertEqual(report.failure, "destination_invalid")
        with raw_connection(workspace) as connection:
            self.assertEqual(
                connection.execute("SELECT * FROM conversations").fetchall(), []
            )
            self.assertEqual(
                connection.execute("SELECT * FROM conversation_events").fetchall(), []
            )
            self.assertEqual(
                connection.execute("SELECT * FROM collaboration_events").fetchall(),
                [],
            )
        self.assertFalse(
            checkpoint_path(workspace, ConversationStorageBackend.FILESYSTEM).exists()
        )

    def test_filesystem_to_sqlite_control_interrupt_rolls_back_and_retries_exactly(
        self,
    ) -> None:
        payload = representative_archive()
        for control_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(control=control_type.__name__):
                workspace = transfer_workspace(self)
                import_backend(
                    workspace,
                    ConversationStorageBackend.FILESYSTEM,
                    payload,
                )
                activation = workspace.path(CONVERSATION_STORAGE_ACTIVATION_STATE_PATH)
                activation_before = (
                    activation.read_bytes() if activation.exists() else None
                )
                configuration = (
                    workspace.path("config/system_defaults.json"),
                    workspace.path("config/agent_configs.json"),
                )
                configuration_before = tuple(
                    path.read_bytes() for path in configuration
                )
                before = namespace_snapshot(workspace.root)
                original_insert = sqlite_module._insert_collaboration_event
                calls = 0

                def interrupt_second_event(connection, event):  # type: ignore[no-untyped-def]
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        raise control_type()
                    return original_insert(connection, event)

                with (
                    mock.patch.object(
                        sqlite_module,
                        "_insert_collaboration_event",
                        new=interrupt_second_event,
                    ),
                    self.assertRaises(control_type),
                ):
                    _transfer(workspace, ConversationStorageBackend.FILESYSTEM)

                self.assertEqual(calls, 2)
                self.assertEqual(namespace_snapshot(workspace.root), before)
                self.assertEqual(
                    activation.read_bytes() if activation.exists() else None,
                    activation_before,
                )
                self.assertEqual(
                    tuple(path.read_bytes() for path in configuration),
                    configuration_before,
                )
                self.assertEqual(
                    export_backend(workspace, ConversationStorageBackend.FILESYSTEM),
                    payload,
                )
                with raw_connection(workspace) as connection:
                    self.assertEqual(
                        connection.execute("SELECT * FROM conversations").fetchall(),
                        [],
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT * FROM conversation_events"
                        ).fetchall(),
                        [],
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT * FROM collaboration_events"
                        ).fetchall(),
                        [],
                    )
                checkpoint = checkpoint_path(
                    workspace,
                    ConversationStorageBackend.FILESYSTEM,
                )
                self.assertFalse(checkpoint.exists())

                retried = _transfer(
                    workspace,
                    ConversationStorageBackend.FILESYSTEM,
                )
                self.assertIs(
                    retried.outcome,
                    ConversationStorageTransferOutcome.COMPLETED,
                )
                self.assertIs(retried.parity, ConversationStorageParity.VERIFIED)
                self.assertEqual(retried.checkpoint, "completed")
                self.assertEqual(
                    export_backend(workspace, ConversationStorageBackend.SQLITE),
                    payload,
                )
                self.assertEqual(
                    export_backend(workspace, ConversationStorageBackend.FILESYSTEM),
                    payload,
                )
                self.assertEqual(
                    activation.read_bytes() if activation.exists() else None,
                    activation_before,
                )
                self.assertEqual(
                    tuple(path.read_bytes() for path in configuration),
                    configuration_before,
                )

    def test_sqlite_to_filesystem_control_interrupt_keeps_prefix_and_resumes_exactly(
        self,
    ) -> None:
        payload = representative_archive()
        first = payload.sessions[0].conversations[0]
        expected_prefix = archive_from_conversations(first)

        def sqlite_namespace(workspace: Workspace) -> dict[str, NamespaceEntry]:
            return {
                relative: entry
                for relative, entry in namespace_snapshot(workspace.root).items()
                if relative.startswith("discussion_logs/conversation_storage.sqlite3")
                or relative.startswith(
                    "discussion_logs/.conversation_storage_fts5.sqlite3"
                )
            }

        for control_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(control=control_type.__name__):
                workspace = transfer_workspace(
                    self,
                    active_backend=ConversationStorageBackend.SQLITE,
                )
                import_backend(
                    workspace,
                    ConversationStorageBackend.SQLITE,
                    payload,
                )
                activation = workspace.path(CONVERSATION_STORAGE_ACTIVATION_STATE_PATH)
                activation_before = activation.read_bytes()
                configuration = (
                    workspace.path("config/system_defaults.json"),
                    workspace.path("config/agent_configs.json"),
                )
                configuration_before = tuple(
                    path.read_bytes() for path in configuration
                )
                source_before = sqlite_namespace(workspace)
                original_import = FilesystemConversationArchiveAdapter.import_archive
                callbacks: list[tuple[str, str]] = []

                def interrupt_after_first_completion(
                    adapter: FilesystemConversationArchiveAdapter,
                    lease: object,
                    archive: object,
                    *,
                    on_conversation_completed=None,  # type: ignore[no-untyped-def]
                ):
                    if on_conversation_completed is None:
                        raise AssertionError("transfer omitted its completion callback")

                    def record_then_interrupt(
                        session_id: str,
                        conversation_id: str,
                    ) -> None:
                        on_conversation_completed(session_id, conversation_id)
                        callbacks.append((session_id, conversation_id))
                        if len(callbacks) == 1:
                            raise control_type()

                    return original_import(
                        adapter,
                        lease,  # type: ignore[arg-type]
                        archive,  # type: ignore[arg-type]
                        on_conversation_completed=record_then_interrupt,
                    )

                with (
                    mock.patch.object(
                        FilesystemConversationArchiveAdapter,
                        "import_archive",
                        new=interrupt_after_first_completion,
                    ),
                    self.assertRaises(control_type),
                ):
                    _transfer(workspace, ConversationStorageBackend.SQLITE)

                self.assertEqual(
                    callbacks,
                    [(first.session_id, first.conversation_id)],
                )
                self.assertEqual(
                    export_backend(workspace, ConversationStorageBackend.FILESYSTEM),
                    expected_prefix,
                )
                self.assertEqual(
                    export_backend(workspace, ConversationStorageBackend.SQLITE),
                    payload,
                )
                source_after = sqlite_namespace(workspace)
                self.assertEqual(source_after, source_before)
                self.assertEqual(activation.read_bytes(), activation_before)
                self.assertEqual(
                    tuple(path.read_bytes() for path in configuration),
                    configuration_before,
                )

                checkpoint = checkpoint_path(
                    workspace,
                    ConversationStorageBackend.SQLITE,
                )
                checkpoint_value = json.loads(checkpoint.read_bytes())
                self.assertEqual(
                    checkpoint_value["progress"],
                    {"completed_conversations": 1, "total_conversations": 3},
                )
                self.assertEqual(
                    checkpoint_value["completed"],
                    [
                        {
                            "session_id": first.session_id,
                            "conversation_id": first.conversation_id,
                        }
                    ],
                )

                retried = _transfer(
                    workspace,
                    ConversationStorageBackend.SQLITE,
                )
                self.assertIs(
                    retried.outcome,
                    ConversationStorageTransferOutcome.COMPLETED,
                )
                self.assertIs(retried.parity, ConversationStorageParity.VERIFIED)
                self.assertEqual(retried.checkpoint, "completed")
                self.assertEqual(
                    export_backend(workspace, ConversationStorageBackend.FILESYSTEM),
                    payload,
                )
                self.assertEqual(
                    export_backend(workspace, ConversationStorageBackend.SQLITE),
                    payload,
                )
                self.assertEqual(activation.read_bytes(), activation_before)
                self.assertEqual(
                    tuple(path.read_bytes() for path in configuration),
                    configuration_before,
                )
                terminal_checkpoint = json.loads(checkpoint.read_bytes())
                self.assertEqual(
                    terminal_checkpoint["progress"],
                    {"completed_conversations": 3, "total_conversations": 3},
                )

    def test_fts_is_excluded_from_sqlite_source_transfer_and_reports(self) -> None:
        workspace = transfer_workspace(
            self, active_backend=ConversationStorageBackend.SQLITE
        )
        payload = representative_archive()
        import_backend(workspace, ConversationStorageBackend.SQLITE, payload)
        companion = (
            workspace.root / "discussion_logs/.conversation_storage_fts5.sqlite3"
        )
        if companion.exists():
            companion.unlink()
        sentinel = b"private FTS-only sentinel\x00"
        companion.write_bytes(sentinel)

        report = _transfer(workspace, ConversationStorageBackend.SQLITE)

        self.assertEqual(report.parity, ConversationStorageParity.VERIFIED)
        self.assertEqual(companion.read_bytes(), sentinel)
        self.assertEqual(
            export_backend(workspace, ConversationStorageBackend.FILESYSTEM), payload
        )
        self.assertNotIn("FTS-only", report.to_json())
        self.assertNotIn(
            "FTS-only",
            checkpoint_path(workspace, ConversationStorageBackend.SQLITE).read_text(
                encoding="utf-8"
            ),
        )

    def test_checkpoint_write_failures_leave_complete_retryable_filesystem_records(
        self,
    ) -> None:
        payload = representative_archive()
        for mode in ("prewrite", "postwrite"):
            with self.subTest(mode=mode):
                workspace = transfer_workspace(
                    self, active_backend=ConversationStorageBackend.SQLITE
                )
                import_backend(workspace, ConversationStorageBackend.SQLITE, payload)
                original_write = Workspace.write_json
                failed = False

                def fail_checkpoint(
                    operation_workspace: Workspace,
                    relative: str | os.PathLike[str],
                    value: object,
                    *,
                    overwrite: bool = True,
                ) -> Path:
                    nonlocal failed
                    if not failed and os.fspath(relative) == checkpoint_relative(
                        ConversationStorageBackend.SQLITE
                    ):
                        failed = True
                        if mode == "postwrite":
                            original_write(
                                operation_workspace,
                                relative,
                                value,
                                overwrite=overwrite,
                            )
                        raise OSError("private checkpoint failure sentinel")
                    return original_write(
                        operation_workspace,
                        relative,
                        value,
                        overwrite=overwrite,
                    )

                with mock.patch.object(Workspace, "write_json", new=fail_checkpoint):
                    failed_report = _transfer(
                        workspace, ConversationStorageBackend.SQLITE
                    )
                self.assertEqual(failed_report.failure, "checkpoint_invalid")
                self.assertNotIn("sentinel", failed_report.to_json())
                destination_after_failure = export_backend(
                    workspace, ConversationStorageBackend.FILESYSTEM
                )
                self.assertGreaterEqual(
                    sum(
                        len(session.conversations)
                        for session in destination_after_failure.sessions
                    ),
                    1,
                )

                resumed = _transfer(workspace, ConversationStorageBackend.SQLITE)
                self.assertEqual(resumed.parity, ConversationStorageParity.VERIFIED)
                self.assertEqual(
                    export_backend(workspace, ConversationStorageBackend.FILESYSTEM),
                    payload,
                )

    def test_verify_ignores_malformed_checkpoint_and_reports_mismatch_as_exit_one(
        self,
    ) -> None:
        workspace = transfer_workspace(self)
        payload = representative_archive()
        import_backend(workspace, ConversationStorageBackend.FILESYSTEM, payload)
        import_backend(workspace, ConversationStorageBackend.SQLITE, payload)
        checkpoint = checkpoint_path(workspace, ConversationStorageBackend.FILESYSTEM)
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_bytes(b"malformed private checkpoint sentinel")
        before = namespace_snapshot(workspace.root)

        verified = verify_conversation_storage(
            workspace,
            source_backend="filesystem",
            destination_backend="sqlite",
        )

        self.assertEqual(verified.exit_code, 0)
        self.assertEqual(verified.parity, ConversationStorageParity.VERIFIED)
        self.assertEqual(namespace_snapshot(workspace.root), before)

        extra = archive_from_conversations(
            archive_conversation(
                session_id="extra_session",
                conversation_id="extra_conversation",
                subject="destination-only private sentinel",
            )
        )
        import_backend(workspace, ConversationStorageBackend.SQLITE, extra)
        mismatch = verify_conversation_storage(
            workspace,
            source_backend="filesystem",
            destination_backend="sqlite",
        )
        self.assertEqual(mismatch.exit_code, 1)
        self.assertEqual(mismatch.failure, "parity_mismatch")
        self.assertEqual(mismatch.parity, ConversationStorageParity.MISMATCH)
        self.assertNotIn("private sentinel", mismatch.to_json())

    def test_source_change_after_destination_commit_is_reported_without_checkpoint(
        self,
    ) -> None:
        workspace = transfer_workspace(self)
        payload = representative_archive()
        import_backend(workspace, ConversationStorageBackend.FILESYSTEM, payload)
        filesystem_archive = compose_filesystem_conversation_archive(workspace)
        late_record = archive_from_conversations(
            archive_conversation(
                session_id="late_session",
                conversation_id="late_conversation",
                subject="late private source sentinel",
            )
        )
        original_import = SQLiteConversationArchiveAdapter.import_archive

        def import_then_change_source(adapter, lease, archive):  # type: ignore[no-untyped-def]
            receipt = original_import(adapter, lease, archive)
            filesystem_archive.import_archive(lease, late_record)
            return receipt

        with mock.patch.object(
            SQLiteConversationArchiveAdapter,
            "import_archive",
            new=import_then_change_source,
        ):
            report = _transfer(workspace, ConversationStorageBackend.FILESYSTEM)

        self.assertEqual(report.failure, "source_changed")
        self.assertEqual(report.exit_code, 2)
        self.assertNotIn("private source sentinel", report.to_json())
        self.assertFalse(
            checkpoint_path(workspace, ConversationStorageBackend.FILESYSTEM).exists()
        )
        self.assertEqual(
            export_backend(workspace, ConversationStorageBackend.SQLITE), payload
        )
        self.assertNotEqual(
            export_backend(workspace, ConversationStorageBackend.FILESYSTEM), payload
        )


class ConversationStorageTransferCheckpointTests(unittest.TestCase):
    def _workspace_with_source(self) -> Workspace:
        workspace = transfer_workspace(self)
        import_backend(
            workspace,
            ConversationStorageBackend.FILESYSTEM,
            representative_archive(),
        )
        return workspace

    def test_malformed_duplicate_version_progress_and_bounded_checkpoints_fail_closed(
        self,
    ) -> None:
        valid_shape = {
            "schema_version": 1,
            "direction": "filesystem-to-sqlite",
            "source_fingerprint": "0" * 64,
            "source": {"conversations": 3, "events": 4},
            "progress": {"completed_conversations": 0, "total_conversations": 3},
            "completed": [],
        }
        cases = (
            ("utf8", b"\xff", "checkpoint_invalid"),
            ("non-object", b"[]", "checkpoint_invalid"),
            (
                "duplicate",
                b'{"schema_version":1,"schema_version":1}',
                "checkpoint_invalid",
            ),
            (
                "constant",
                b'{"checkpoint":NaN}',
                "checkpoint_invalid",
            ),
            (
                "missing field",
                json.dumps(
                    {key: item for key, item in valid_shape.items() if key != "source"}
                ).encode(),
                "checkpoint_invalid",
            ),
            (
                "extra field",
                json.dumps({**valid_shape, "extra": None}).encode(),
                "checkpoint_invalid",
            ),
            (
                "version",
                json.dumps({**valid_shape, "schema_version": 2}).encode(),
                "checkpoint_unsupported",
            ),
            (
                "progress",
                json.dumps(
                    {
                        **valid_shape,
                        "progress": {
                            "completed_conversations": 2,
                            "total_conversations": 3,
                        },
                        "completed": [
                            {
                                "session_id": "session_a",
                                "conversation_id": "conversation_empty",
                            }
                        ],
                    }
                ).encode(),
                "checkpoint_invalid",
            ),
            (
                "boolean count",
                json.dumps(
                    {
                        **valid_shape,
                        "source": {"conversations": True, "events": 4},
                    }
                ).encode(),
                "checkpoint_invalid",
            ),
            (
                "count bound",
                json.dumps(
                    {
                        **valid_shape,
                        "source": {
                            "conversations": (
                                migration_module.CONVERSATION_ARCHIVE_MAX_CONVERSATIONS
                                + 1
                            ),
                            "events": (
                                migration_module.CONVERSATION_ARCHIVE_MAX_EVENTS + 1
                            ),
                        },
                        "progress": {
                            "completed_conversations": 0,
                            "total_conversations": (
                                migration_module.CONVERSATION_ARCHIVE_MAX_CONVERSATIONS
                                + 1
                            ),
                        },
                    }
                ).encode(),
                "checkpoint_invalid",
            ),
            (
                "duplicate completed identity",
                json.dumps(
                    {
                        **valid_shape,
                        "progress": {
                            "completed_conversations": 2,
                            "total_conversations": 3,
                        },
                        "completed": [
                            {
                                "session_id": "session_a",
                                "conversation_id": "conversation_empty",
                            },
                            {
                                "session_id": "session_a",
                                "conversation_id": "conversation_empty",
                            },
                        ],
                    }
                ).encode(),
                "checkpoint_invalid",
            ),
            (
                "noncanonical completed order",
                json.dumps(
                    {
                        **valid_shape,
                        "progress": {
                            "completed_conversations": 2,
                            "total_conversations": 3,
                        },
                        "completed": [
                            {
                                "session_id": "session_b",
                                "conversation_id": "conversation_runtime",
                            },
                            {
                                "session_id": "session_a",
                                "conversation_id": "conversation_empty",
                            },
                        ],
                    }
                ).encode(),
                "checkpoint_invalid",
            ),
            (
                "unsafe identity",
                json.dumps(
                    {
                        **valid_shape,
                        "progress": {
                            "completed_conversations": 1,
                            "total_conversations": 3,
                        },
                        "completed": [
                            {
                                "session_id": "../unsafe",
                                "conversation_id": "conversation_empty",
                            }
                        ],
                    }
                ).encode(),
                "checkpoint_invalid",
            ),
            (
                "wrong direction",
                json.dumps(
                    {**valid_shape, "direction": "sqlite-to-filesystem"}
                ).encode(),
                "checkpoint_invalid",
            ),
            (
                "fingerprint",
                json.dumps(
                    {**valid_shape, "source_fingerprint": "private fingerprint"}
                ).encode(),
                "checkpoint_invalid",
            ),
        )
        for label, encoded, failure in cases:
            with self.subTest(case=label):
                workspace = self._workspace_with_source()
                checkpoint = checkpoint_path(
                    workspace, ConversationStorageBackend.FILESYSTEM
                )
                checkpoint.parent.mkdir(parents=True, exist_ok=True)
                checkpoint.write_bytes(encoded)
                before = namespace_snapshot(workspace.root)
                report = _transfer(
                    workspace,
                    ConversationStorageBackend.FILESYSTEM,
                    dry_run=True,
                )
                self.assertEqual(report.failure, failure)
                self.assertEqual(namespace_snapshot(workspace.root), before)
                self.assertNotIn("session_a", report.to_json())

        workspace = self._workspace_with_source()
        checkpoint = checkpoint_path(workspace, ConversationStorageBackend.FILESYSTEM)
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(valid_shape).encode()
        checkpoint.write_bytes(encoded)
        with mock.patch.object(
            migration_module, "_CHECKPOINT_MAX_RAW_BYTES", len(encoded) - 1
        ):
            bounded = _transfer(
                workspace,
                ConversationStorageBackend.FILESYSTEM,
                dry_run=True,
            )
        self.assertEqual(bounded.failure, "checkpoint_invalid")

    def test_symlink_and_hardlink_checkpoints_are_rejected_and_preserved(self) -> None:
        for kind in ("symlink", "hardlink"):
            with self.subTest(kind=kind):
                workspace = self._workspace_with_source()
                target = workspace.root / checkpoint_relative(
                    ConversationStorageBackend.FILESYSTEM
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                outside = workspace.root / f"outside-{kind}.json"
                outside_bytes = b'{"private":"outside checkpoint sentinel"}\n'
                outside.write_bytes(outside_bytes)
                try:
                    if kind == "symlink":
                        os.symlink(outside, target)
                    else:
                        os.link(outside, target)
                except OSError as exc:
                    self.skipTest(f"{kind} creation unavailable: {exc}")
                report = _transfer(
                    workspace,
                    ConversationStorageBackend.FILESYSTEM,
                    dry_run=True,
                )
                self.assertIn(report.failure, ("checkpoint_invalid", "unsafe_state"))
                self.assertEqual(outside.read_bytes(), outside_bytes)
                if kind == "symlink":
                    self.assertTrue(target.is_symlink())
                else:
                    self.assertTrue(target.exists())

    def test_changed_source_replaces_safe_checkpoint_but_blocked_plan_preserves_it(
        self,
    ) -> None:
        workspace = self._workspace_with_source()
        completed = _transfer(workspace, ConversationStorageBackend.FILESYSTEM)
        self.assertEqual(completed.parity, ConversationStorageParity.VERIFIED)
        checkpoint = checkpoint_path(workspace, ConversationStorageBackend.FILESYSTEM)
        old_checkpoint = checkpoint.read_bytes()

        existing = representative_archive()
        new_record = archive_conversation(
            session_id="session_c",
            conversation_id="new_conversation",
            subject="new safe source",
        )
        import_backend(
            workspace,
            ConversationStorageBackend.FILESYSTEM,
            archive_from_conversations(
                *(
                    conversation
                    for session in existing.sessions
                    for conversation in session.conversations
                ),
                new_record,
            ),
        )
        rerun = _transfer(workspace, ConversationStorageBackend.FILESYSTEM)
        self.assertEqual(rerun.parity, ConversationStorageParity.VERIFIED)
        self.assertNotEqual(checkpoint.read_bytes(), old_checkpoint)

        retained = checkpoint.read_bytes()
        metadata = workspace.root / (
            "discussion_logs/session_b/interactive/conversations/"
            "conversation_empty/metadata.json"
        )
        value = json.loads(metadata.read_text(encoding="utf-8"))
        value["subject"] = "source conflict sentinel"
        metadata.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        blocked = _transfer(workspace, ConversationStorageBackend.FILESYSTEM)
        self.assertEqual(blocked.failure, "conflict")
        self.assertEqual(checkpoint.read_bytes(), retained)


class ConversationStorageTransferCoordinationTests(unittest.TestCase):
    def test_wrong_root_lease_fails_before_activation_composition_or_io(self) -> None:
        workspace = transfer_workspace(self)
        other = transfer_workspace(self)
        other_coordinator = WorkspaceConversationStorageMutationCoordinator(other)
        fake_coordinator = mock.Mock(
            workspace_key=os.fspath(workspace.root),
        )

        with other_coordinator.borrow(timeout_seconds=2.0) as wrong_lease:
            fake_coordinator.borrow.return_value = contextlib.nullcontext(wrong_lease)
            with (
                mock.patch.object(
                    migration_module,
                    "WorkspaceConversationStorageMutationCoordinator",
                    return_value=fake_coordinator,
                ),
                mock.patch.object(
                    migration_module,
                    "load_conversation_storage_activation_state",
                    side_effect=AssertionError("activation materialized"),
                ) as activation,
                mock.patch.object(
                    migration_module,
                    "_compose_archive_adapters",
                    side_effect=AssertionError("archive adapters composed"),
                ) as compose,
            ):
                report = _transfer(workspace, ConversationStorageBackend.FILESYSTEM)

        self.assertEqual(report.failure, "coordination_failed")
        self.assertEqual(report.exit_code, 2)
        self.assertNotIn(os.fspath(other.root), report.to_json())
        activation.assert_not_called()
        compose.assert_not_called()

    def test_outer_lease_excludes_supported_writer_through_final_parity(self) -> None:
        workspace = transfer_workspace(self)
        payload = representative_archive()
        import_backend(workspace, ConversationStorageBackend.FILESYSTEM, payload)
        services = compose_filesystem_conversation_storage(workspace)
        attempted = threading.Event()
        completed = threading.Event()
        failures: list[BaseException] = []

        def writer() -> None:
            attempted.set()
            try:
                with services.mutation_coordinator.borrow(timeout_seconds=2.0):
                    services.catalog.create("writer_session", "late supported writer")
            except BaseException as exc:
                failures.append(exc)
            finally:
                completed.set()

        original_import = SQLiteConversationArchiveAdapter.import_archive
        thread: threading.Thread | None = None

        def import_while_writer_waits(adapter, lease, archive):  # type: ignore[no-untyped-def]
            nonlocal thread
            thread = threading.Thread(target=writer)
            thread.start()
            self.assertTrue(attempted.wait(timeout=1.0))
            self.assertFalse(completed.wait(timeout=0.1))
            return original_import(adapter, lease, archive)

        with mock.patch.object(
            SQLiteConversationArchiveAdapter,
            "import_archive",
            new=import_while_writer_waits,
        ):
            report = _transfer(workspace, ConversationStorageBackend.FILESYSTEM)
        assert thread is not None
        thread.join(timeout=3.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertTrue(completed.is_set())
        self.assertEqual(report.parity, ConversationStorageParity.VERIFIED)

    def test_caller_root_mutation_cannot_redirect_any_transfer_io(self) -> None:
        workspace = transfer_workspace(self)
        pinned_workspace = copy(workspace)
        payload = representative_archive()
        import_backend(workspace, ConversationStorageBackend.FILESYSTEM, payload)
        workspace.write_json(
            CONVERSATION_STORAGE_ACTIVATION_STATE_PATH,
            ConversationStorageActivationState(
                ConversationStorageBackend.FILESYSTEM,
                0,
            ).to_dict(),
        )
        other = transfer_workspace(self)
        root_b = other.root
        other_before = namespace_snapshot(root_b)
        original_read = Workspace.read_text

        def mutate_caller(
            operation_workspace: Workspace,
            relative: str | os.PathLike[str],
        ) -> str:
            workspace.root = root_b
            return original_read(operation_workspace, relative)

        with mock.patch.object(Workspace, "read_text", new=mutate_caller):
            report = _transfer(workspace, ConversationStorageBackend.FILESYSTEM)

        self.assertEqual(report.parity, ConversationStorageParity.VERIFIED)
        self.assertEqual(
            export_backend(pinned_workspace, ConversationStorageBackend.SQLITE),
            payload,
        )
        self.assertEqual(namespace_snapshot(root_b), other_before)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
