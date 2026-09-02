"""Filesystem canonical schema-v2 conversation archive tests."""

from __future__ import annotations

import json
import os
import threading
import time
import unittest
from copy import copy
from pathlib import Path
from unittest import mock

from story_writing_agents import (
    ConversationMetadata,
    FilesystemConversationCatalog,
    ManuscriptExistsError,
    Workspace,
    WorkspaceFormatError,
)
from story_writing_agents.conversation_storage import (
    ConversationArchive,
    ConversationArchiveBoundsError,
    ConversationArchiveConflictError,
    ConversationArchiveDestinationChangedError,
    ConversationArchiveDestinationError,
    ConversationArchiveImportStatus,
    ConversationArchiveSourceChangedError,
    ConversationArchiveSourceError,
    ConversationStorageCoordinationError,
    FilesystemConversationArchiveAdapter,
    WorkspaceConversationStorageMutationCoordinator,
    compose_filesystem_conversation_archive,
    serialize_conversation_archive,
)
from story_writing_agents.conversation_storage import (
    filesystem_archive as filesystem_archive_module,
)
from story_writing_agents.conversation_storage import filesystem as filesystem_module
from story_writing_agents.conversation_storage.filesystem import (
    FilesystemCollaborationEventStore,
)

from tests.conversation_archive_fixture import (
    archive_from_conversations,
    event_images,
    history_relative,
    initialized_workspace,
    metadata_relative,
    nonempty_archive,
    one_conversation_archive,
    representative_archive,
    transcript_relative,
    workspace_snapshot,
)


class ControlledLease:
    """Small public-protocol lease for deterministic boundary faults."""

    def __init__(self, workspace_key: str) -> None:
        self._workspace_key = workspace_key
        self._owner_thread_id = threading.get_ident()
        self._expires_at_monotonic = time.monotonic() + 60.0
        self._active = True

    @property
    def workspace_key(self) -> str:
        return self._workspace_key

    @property
    def owner_thread_id(self) -> int:
        return self._owner_thread_id

    @property
    def expires_at_monotonic(self) -> float:
        return self._expires_at_monotonic

    @property
    def active(self) -> bool:
        return self._active

    def validate(self, *, workspace_key: str) -> None:
        if (
            workspace_key != self._workspace_key
            or threading.get_ident() != self._owner_thread_id
            or not self._active
        ):
            raise ConversationStorageCoordinationError()

    def deactivate(self) -> None:
        self._active = False


def _import(workspace: Workspace, payload: ConversationArchive):
    adapter = compose_filesystem_conversation_archive(workspace)
    coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
    with coordinator.borrow(timeout_seconds=2.0) as lease:
        return adapter.import_archive(lease, payload)


def _export(workspace: Workspace) -> ConversationArchive:
    adapter = compose_filesystem_conversation_archive(workspace)
    coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
    with coordinator.borrow(timeout_seconds=2.0) as lease:
        return adapter.export_archive(lease)


def _authoritative_snapshot(root: Path) -> dict[str, bytes | None]:
    """Ignore the expected empty process-coordination sentinel."""

    return {
        relative: value
        for relative, value in workspace_snapshot(root).items()
        if relative != "config/.conversation_storage.lock"
    }


def _create_legacy_conversation(
    workspace: Workspace,
    *,
    session_id: str = "legacy_session",
) -> ConversationMetadata:
    catalog = FilesystemConversationCatalog(workspace)
    metadata = catalog.create(session_id, "Legacy transcript")
    workspace.write_text(
        transcript_relative(session_id, metadata.conversation_id),
        '{"schema_version":1,"legacy":"transcript sentinel"}\n',
    )
    return metadata


class FilesystemConversationArchiveCompositionTests(unittest.TestCase):
    def test_composition_is_io_free_direct_and_v2_owned(self) -> None:
        workspace = initialized_workspace(self)
        before = workspace_snapshot(workspace.root)
        adapter = compose_filesystem_conversation_archive(workspace)
        self.assertEqual(workspace_snapshot(workspace.root), before)
        self.assertIsInstance(adapter, FilesystemConversationArchiveAdapter)

        operation = filesystem_archive_module._ArchiveWorkspaceTopology.capture(
            workspace
        ).operation()
        self.assertIs(type(operation.catalog), FilesystemConversationCatalog)
        self.assertIs(type(operation.event_store), FilesystemCollaborationEventStore)
        self.assertIs(operation.event_store.catalog, operation.catalog)
        self.assertIs(
            filesystem_archive_module.FilesystemCollaborationEventStore,
            filesystem_module.FilesystemCollaborationEventStore,
        )
        for name in ("workspace", "catalog", "event_store"):
            self.assertFalse(hasattr(adapter, name))
            with self.assertRaises(AttributeError):
                setattr(adapter, name, object())
        self.assertIsInstance(
            compose_filesystem_conversation_archive(workspace.root),
            FilesystemConversationArchiveAdapter,
        )
        self.assertEqual(workspace_snapshot(workspace.root), before)

    def test_empty_export_and_missing_root_are_distinct(self) -> None:
        workspace = initialized_workspace(self)
        self.assertEqual(_export(workspace), ConversationArchive(2, ()))
        workspace.path("discussion_logs/.gitkeep").unlink()
        workspace.path("discussion_logs").rmdir()
        with self.assertRaises(ConversationArchiveSourceError):
            _export(workspace)
        missing = workspace.root / "missing"
        with self.assertRaisesRegex(WorkspaceFormatError, "workspace is not ready"):
            compose_filesystem_conversation_archive(missing)

    def test_topology_and_lease_pin_all_io_to_one_root(self) -> None:
        workspace = initialized_workspace(self)
        other = initialized_workspace(self)
        pinned_workspace = copy(workspace)
        adapter = compose_filesystem_conversation_archive(workspace)
        payload = nonempty_archive()
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            workspace.root = other.root
            adapter.import_archive(lease, payload)
            self.assertEqual(adapter.export_archive(lease), payload)
        self.assertEqual(_export(pinned_workspace), payload)
        self.assertEqual(_export(other), ConversationArchive(2, ()))

        wrong = ControlledLease(os.fspath(other.root))
        with self.assertRaises(ConversationStorageCoordinationError):
            adapter.export_archive(wrong)
        released = ControlledLease(coordinator.workspace_key)
        released.deactivate()
        with self.assertRaises(ConversationStorageCoordinationError):
            adapter.import_archive(released, payload)


class FilesystemConversationArchiveRoundTripTests(unittest.TestCase):
    def test_real_layout_round_trip_is_byte_and_codec_exact(self) -> None:
        workspace = initialized_workspace(self)
        payload = representative_archive()
        receipt = _import(workspace, payload)
        first = _export(workspace)
        second = _export(workspace)

        self.assertIs(receipt.status, ConversationArchiveImportStatus.IMPORTED)
        self.assertEqual(first, payload)
        self.assertEqual(second, payload)
        self.assertEqual(
            serialize_conversation_archive(first),
            serialize_conversation_archive(payload),
        )
        self.assertEqual(event_images(first), event_images(payload))
        self.assertEqual(len(event_images(first)), 25)
        for session in payload.sessions:
            for conversation in session.conversations:
                metadata = workspace.path(
                    metadata_relative(session.session_id, conversation.conversation_id)
                )
                history = workspace.path(
                    history_relative(session.session_id, conversation.conversation_id)
                )
                self.assertTrue(metadata.is_file())
                self.assertEqual(json.loads(metadata.read_bytes())["schema_version"], 1)
                if conversation.events:
                    self.assertTrue(history.is_file())
                    self.assertEqual(
                        len(history.read_bytes().splitlines()), len(conversation.events)
                    )
                else:
                    self.assertFalse(history.exists())
                self.assertFalse(
                    workspace.path(
                        transcript_relative(
                            session.session_id, conversation.conversation_id
                        )
                    ).exists()
                )

        retry = _import(workspace, payload)
        self.assertIs(retry.status, ConversationArchiveImportStatus.UNCHANGED)
        self.assertEqual(_export(workspace), payload)

    def test_unrelated_complete_records_and_private_lock_state_are_preserved(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        existing = one_conversation_archive(
            session_id="existing_session",
            conversation_id="existing_conversation",
        )
        incoming = one_conversation_archive(
            events=nonempty_archive().sessions[0].conversations[0].events
        )
        _import(workspace, existing)
        lock = workspace.path(
            metadata_relative("existing_session", "existing_conversation").with_name(
                ".collaboration-history.lock"
            )
        )
        lock.write_bytes(b"")
        _import(workspace, incoming)
        combined = _export(workspace)
        self.assertEqual(
            {
                (s.session_id, c.conversation_id)
                for s in combined.sessions
                for c in s.conversations
            },
            {
                ("existing_session", "existing_conversation"),
                ("session_a", "conversation_a"),
            },
        )
        self.assertEqual(lock.read_bytes(), b"")

    def test_source_change_between_double_scans_is_detected(self) -> None:
        workspace = initialized_workspace(self)
        _import(workspace, nonempty_archive())
        adapter = compose_filesystem_conversation_archive(workspace)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        original = FilesystemConversationArchiveAdapter._scan_source
        calls = 0

        def scan_then_change(
            current,
            operation,
            validate_lease,  # type: ignore[no-untyped-def]
        ):
            nonlocal calls
            value = original(current, operation, validate_lease)
            calls += 1
            if calls == 1:
                target = workspace.path(
                    metadata_relative("session_a", "conversation_a")
                )
                record = json.loads(target.read_bytes())
                record["subject"] = "drifted subject"
                target.write_text(
                    json.dumps(record, sort_keys=True) + "\n", encoding="utf-8"
                )
            return value

        with coordinator.borrow(timeout_seconds=2.0) as lease:
            with mock.patch.object(
                FilesystemConversationArchiveAdapter,
                "_scan_source",
                new=scan_then_change,
            ):
                with self.assertRaises(ConversationArchiveSourceChangedError):
                    adapter.export_archive(lease)


class FilesystemConversationArchiveLegacyAndSecurityTests(unittest.TestCase):
    def test_any_legacy_transcript_rejects_source_and_destination_without_mutation(
        self,
    ) -> None:
        source = initialized_workspace(self)
        _create_legacy_conversation(source)
        source_before = _authoritative_snapshot(source.root)
        with self.assertRaises(ConversationArchiveSourceError):
            _export(source)
        self.assertEqual(_authoritative_snapshot(source.root), source_before)

        destination = initialized_workspace(self)
        _create_legacy_conversation(destination)
        before = _authoritative_snapshot(destination.root)
        with self.assertRaises(ConversationArchiveDestinationError):
            _import(destination, nonempty_archive())
        self.assertEqual(_authoritative_snapshot(destination.root), before)

    def test_orphan_unsafe_and_symlinked_history_fail_closed(self) -> None:
        for mode in ("orphan", "symlink"):
            with self.subTest(mode=mode):
                workspace = initialized_workspace(self)
                outside = workspace.path("outside-history.jsonl")
                outside.write_bytes(b"private sentinel\n")
                relative = history_relative("session_a", "conversation_a")
                target = workspace.path(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                if mode == "orphan":
                    target.write_bytes(b"{}\n")
                else:
                    try:
                        os.symlink(outside, target)
                    except OSError as exc:
                        self.skipTest(f"symlink unavailable: {exc}")
                before = _authoritative_snapshot(workspace.root)
                with self.assertRaises(ConversationArchiveSourceError):
                    _export(workspace)
                self.assertEqual(_authoritative_snapshot(workspace.root), before)
                self.assertEqual(outside.read_bytes(), b"private sentinel\n")

    def test_inventory_file_and_logical_bounds_fail_before_write(self) -> None:
        payload = nonempty_archive()
        for constant, limit in (
            ("_MAX_INVENTORY_ENTRIES", 0),
            ("_MAX_INCLUDED_TOTAL_BYTES", 1),
            ("_MAX_INCLUDED_FILE_BYTES", 1),
        ):
            workspace = initialized_workspace(self)
            before = _authoritative_snapshot(workspace.root)
            with self.subTest(constant=constant):
                with mock.patch.object(filesystem_archive_module, constant, limit):
                    with self.assertRaises(ConversationArchiveBoundsError):
                        _import(workspace, payload)
            self.assertEqual(_authoritative_snapshot(workspace.root), before)


class FilesystemConversationArchiveRecoveryTests(unittest.TestCase):
    def test_callback_failure_leaves_only_complete_record_and_retry_finishes(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        payload = representative_archive()
        adapter = compose_filesystem_conversation_archive(workspace)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        completed: list[tuple[str, str]] = []

        def stop_after_first(session_id: str, conversation_id: str) -> None:
            completed.append((session_id, conversation_id))
            raise RuntimeError("private callback sentinel")

        with coordinator.borrow(timeout_seconds=2.0) as lease:
            with self.assertRaisesRegex(RuntimeError, "private callback sentinel"):
                adapter.import_archive(
                    lease,
                    payload,
                    on_conversation_completed=stop_after_first,
                )
        self.assertEqual(completed, [("session_a", "conversation_completed")])
        prefix = _export(workspace)
        self.assertEqual(
            prefix,
            archive_from_conversations(payload.sessions[0].conversations[0]),
        )
        self.assertEqual(
            event_images(prefix),
            event_images(
                archive_from_conversations(payload.sessions[0].conversations[0])
            ),
        )
        self.assertIs(
            _import(workspace, payload).status, ConversationArchiveImportStatus.IMPORTED
        )
        self.assertEqual(_export(workspace), payload)

    def test_history_write_failure_leaves_metadata_only_residue_and_retries(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        payload = nonempty_archive()
        original = Workspace.write_text
        failed = False

        def fail_history(
            operation_workspace: Workspace,
            relative: str | os.PathLike[str],
            text: str,
            *,
            overwrite: bool = True,
        ) -> Path:
            nonlocal failed
            if not failed and Path(relative).name == "collaboration-history.jsonl":
                failed = True
                raise OSError("private history failure")
            return original(operation_workspace, relative, text, overwrite=overwrite)

        with mock.patch.object(Workspace, "write_text", new=fail_history):
            with self.assertRaises(ConversationArchiveDestinationError):
                _import(workspace, payload)
        metadata = workspace.path(metadata_relative("session_a", "conversation_a"))
        history = workspace.path(history_relative("session_a", "conversation_a"))
        self.assertTrue(metadata.is_file())
        self.assertFalse(history.exists())
        self.assertEqual(list(history.parent.glob("*.tmp")), [])
        residue = _export(workspace)
        self.assertEqual(residue.sessions[0].conversations[0].events, ())
        self.assertIs(
            _import(workspace, payload).status, ConversationArchiveImportStatus.IMPORTED
        )
        self.assertEqual(_export(workspace), payload)

    def test_preflight_conflict_and_install_collision_never_overwrite(self) -> None:
        workspace = initialized_workspace(self)
        existing = one_conversation_archive(subject="Existing")
        _import(workspace, existing)
        before = workspace_snapshot(workspace.root)
        with self.assertRaises(ConversationArchiveConflictError):
            _import(workspace, one_conversation_archive(subject="Incoming"))
        self.assertEqual(workspace_snapshot(workspace.root), before)

        fresh = initialized_workspace(self)
        original = Workspace.write_text

        def collide(
            operation_workspace: Workspace,
            relative: str | os.PathLike[str],
            text: str,
            *,
            overwrite: bool = True,
        ) -> Path:
            if Path(relative).name == "collaboration-history.jsonl":
                target = operation_workspace.path(relative)
                target.write_text("collision\n", encoding="utf-8")
                raise ManuscriptExistsError(str(relative))
            return original(operation_workspace, relative, text, overwrite=overwrite)

        with mock.patch.object(Workspace, "write_text", new=collide):
            with self.assertRaises(ConversationArchiveDestinationChangedError):
                _import(fresh, nonempty_archive())
        self.assertEqual(
            fresh.path(history_relative("session_a", "conversation_a")).read_bytes(),
            b"collision\n",
        )

    def test_frozen_v1_transcript_sentinel_is_rejected_without_a_public_writer(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        metadata = _create_legacy_conversation(workspace)
        self.assertTrue(
            workspace.path(
                transcript_relative(metadata.session_id, metadata.conversation_id)
            ).is_file()
        )
        with self.assertRaises(ConversationArchiveSourceError):
            _export(workspace)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
