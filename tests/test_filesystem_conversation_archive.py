"""Filesystem canonical conversation archive transfer tests."""

from __future__ import annotations

import inspect
import json
import os
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from story_writing_agents import (
    ConversationEventKind,
    ConversationEvent,
    ConversationMetadata,
    ConversationStatus,
    FilesystemConversationCatalog,
    FilesystemConversationEventStore,
    ManuscriptExistsError,
    Workspace,
)
from story_writing_agents.conversation_storage import (
    ConversationArchive,
    ConversationArchiveBoundsError,
    ConversationArchiveConflictError,
    ConversationArchiveDestinationChangedError,
    ConversationArchiveDestinationError,
    ConversationArchiveFormatError,
    ConversationArchiveImportStatus,
    ConversationArchiveImportReceipt,
    ConversationArchiveSourceChangedError,
    ConversationArchiveSourceError,
    ConversationStorageCoordinationError,
    ConversationStorageFailure,
    FilesystemConversationArchiveAdapter,
    WorkspaceConversationStorageMutationCoordinator,
    compose_filesystem_conversation_archive,
    serialize_conversation_archive,
)
from story_writing_agents.conversation_storage import archive as archive_module
from story_writing_agents.conversation_storage import (
    filesystem_archive as filesystem_archive_module,
)

from tests.conversation_archive_fixture import (
    archive_conversation,
    archive_events,
    archive_from_conversations,
    initialized_workspace,
    metadata_relative,
    nonempty_archive,
    one_conversation_archive,
    transcript_relative,
    workspace_snapshot,
)
from tests.conversation_event_store_contract import make_append


class ControlledLease:
    """Small public-protocol lease for deterministic boundary fault injection."""

    def __init__(
        self,
        workspace_key: str,
    ) -> None:
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


def _create_ordinary_conversation(
    workspace: Workspace,
    *,
    session_id: str,
    subject: str,
    outcome_kind: ConversationEventKind | None = None,
    archived: bool = False,
) -> tuple[ConversationMetadata, tuple[ConversationEvent, ...]]:
    catalog = FilesystemConversationCatalog(workspace)
    event_store = FilesystemConversationEventStore(workspace, catalog=catalog)
    metadata = catalog.create(session_id, subject)
    events: tuple[ConversationEvent, ...] = ()
    if outcome_kind is not None:
        first = event_store.append(
            make_append(
                session_id=session_id,
                conversation_id=metadata.conversation_id,
                turn_id="turn_a",
                content="question",
            )
        )
        second = event_store.append(
            make_append(
                session_id=session_id,
                conversation_id=metadata.conversation_id,
                turn_id="turn_a",
                kind=outcome_kind,
                content="answer"
                if outcome_kind is ConversationEventKind.AGENT_REPLY
                else "failed",
            )
        )
        events = (first, second)
    if archived:
        metadata = catalog.archive(session_id, metadata.conversation_id)
    return metadata, events


class FilesystemConversationArchiveCompositionTests(unittest.TestCase):
    def test_composition_is_io_free_and_exposes_only_the_archive_ports(self) -> None:
        workspace = initialized_workspace(self)
        before = workspace_snapshot(workspace.root)

        adapter = compose_filesystem_conversation_archive(workspace)

        self.assertEqual(workspace_snapshot(workspace.root), before)
        self.assertIsInstance(adapter, FilesystemConversationArchiveAdapter)
        for name in ("workspace", "catalog", "event_store"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(adapter, name))
                with self.assertRaises(AttributeError):
                    setattr(adapter, name, object())
        path_adapter = compose_filesystem_conversation_archive(workspace.root)
        self.assertIsInstance(path_adapter, FilesystemConversationArchiveAdapter)
        self.assertEqual(workspace_snapshot(workspace.root), before)

    def test_initialized_empty_export_and_missing_source_root_are_distinct(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        adapter = compose_filesystem_conversation_archive(workspace)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            exported = adapter.export_archive(lease)
        self.assertEqual(exported, ConversationArchive(1, ()))

        workspace.path("discussion_logs/.gitkeep").unlink()
        workspace.path("discussion_logs").rmdir()
        controlled = ControlledLease(os.fspath(workspace.root))
        with self.assertRaises(ConversationArchiveSourceError):
            adapter.export_archive(controlled)

    def test_caller_root_mutation_cannot_redirect_export_or_import(self) -> None:
        source = initialized_workspace(self)
        source_root = source.root
        source_metadata, _ = _create_ordinary_conversation(
            source,
            session_id="session_a",
            subject="Pinned source subject",
        )
        other = initialized_workspace(self)
        other_root = other.root
        _create_ordinary_conversation(
            other,
            session_id="session_a",
            subject="Other workspace decoy",
        )
        other_before = workspace_snapshot(other_root)
        source_adapter = compose_filesystem_conversation_archive(source)
        source.root = other_root

        exported = source_adapter.export_archive(
            ControlledLease(os.fspath(source_root))
        )

        self.assertEqual(
            exported.sessions[0].conversations[0].conversation_id,
            source_metadata.conversation_id,
        )
        self.assertEqual(
            exported.sessions[0].conversations[0].subject,
            "Pinned source subject",
        )
        self.assertEqual(workspace_snapshot(other_root), other_before)

        destination = initialized_workspace(self)
        destination_root = destination.root
        destination_adapter = compose_filesystem_conversation_archive(destination)
        destination.root = other_root
        receipt = destination_adapter.import_archive(
            ControlledLease(os.fspath(destination_root)),
            exported,
        )

        self.assertIs(receipt.status, ConversationArchiveImportStatus.IMPORTED)
        self.assertTrue(
            Workspace(destination_root)
            .path(
                metadata_relative(
                    "session_a",
                    source_metadata.conversation_id,
                )
            )
            .is_file()
        )
        self.assertEqual(workspace_snapshot(other_root), other_before)

    def test_wrong_root_lease_fails_before_operation_materialization_or_io(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        other = initialized_workspace(self)
        adapter = compose_filesystem_conversation_archive(workspace)
        wrong = ControlledLease(os.fspath(other.root))
        workspace_before = workspace_snapshot(workspace.root)
        other_before = workspace_snapshot(other.root)

        for operation in (
            lambda: adapter.export_archive(wrong),
            lambda: adapter.import_archive(wrong, nonempty_archive()),
        ):
            with self.subTest(operation=operation):
                with (
                    mock.patch.object(
                        Workspace,
                        "__init__",
                        side_effect=AssertionError("workspace materialized"),
                    ) as workspace_init,
                    mock.patch.object(
                        FilesystemConversationCatalog,
                        "__init__",
                        side_effect=AssertionError("catalog materialized"),
                    ) as catalog_init,
                    mock.patch.object(
                        FilesystemConversationEventStore,
                        "__init__",
                        side_effect=AssertionError("event store materialized"),
                    ) as event_store_init,
                    mock.patch.object(
                        Workspace,
                        "path",
                        side_effect=AssertionError("filesystem path accessed"),
                    ) as path,
                    mock.patch.object(
                        Workspace,
                        "read_text",
                        side_effect=AssertionError("filesystem read attempted"),
                    ) as read_text,
                    mock.patch.object(
                        Workspace,
                        "write_text",
                        side_effect=AssertionError("filesystem write attempted"),
                    ) as write_text,
                ):
                    with self.assertRaises(
                        ConversationStorageCoordinationError
                    ) as caught:
                        operation()
                for collaborator in (
                    workspace_init,
                    catalog_init,
                    event_store_init,
                    path,
                    read_text,
                    write_text,
                ):
                    collaborator.assert_not_called()
                self.assertEqual(
                    str(caught.exception),
                    "conversation storage failure: coordination_failed",
                )
                self.assertIsNone(caught.exception.__cause__)
                self.assertEqual(workspace_snapshot(workspace.root), workspace_before)
                self.assertEqual(workspace_snapshot(other.root), other_before)

    def test_released_wrong_workspace_and_mid_operation_leases_fail_coordination(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        other = initialized_workspace(self)
        adapter = compose_filesystem_conversation_archive(workspace)
        other_coordinator = WorkspaceConversationStorageMutationCoordinator(other)
        with other_coordinator.borrow(timeout_seconds=2.0) as wrong:
            with self.assertRaises(ConversationStorageCoordinationError) as raised:
                adapter.export_archive(wrong)
            self.assertIs(
                raised.exception.failure,
                ConversationStorageFailure.COORDINATION_FAILED,
            )

        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as released:
            released.validate(workspace_key=coordinator.workspace_key)
        with self.assertRaises(ConversationStorageCoordinationError):
            adapter.export_archive(released)

        _create_ordinary_conversation(
            workspace,
            session_id="session_a",
            subject="Lease boundary",
        )
        interrupted = ControlledLease(os.fspath(workspace.root))
        original_get = FilesystemConversationCatalog.get

        def get_then_invalidate(
            catalog: FilesystemConversationCatalog,
            session_id: str,
            conversation_id: str,
        ) -> ConversationMetadata:
            metadata = original_get(catalog, session_id, conversation_id)
            interrupted.deactivate()
            return metadata

        with mock.patch.object(
            FilesystemConversationCatalog,
            "get",
            new=get_then_invalidate,
        ):
            with self.assertRaises(ConversationStorageCoordinationError):
                adapter.export_archive(interrupted)


class FilesystemConversationArchiveExportTests(unittest.TestCase):
    def test_real_layout_export_is_exact_deterministic_and_excludes_private_state(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        first_metadata, _ = _create_ordinary_conversation(
            workspace,
            session_id="session_b",
            subject="Empty history",
        )
        second_metadata, second_events = _create_ordinary_conversation(
            workspace,
            session_id="session_a",
            subject="Archived runtime failure",
            outcome_kind=ConversationEventKind.RUNTIME_ERROR,
            archived=True,
        )
        third_metadata, third_events = _create_ordinary_conversation(
            workspace,
            session_id="session_a",
            subject="Active reply",
            outcome_kind=ConversationEventKind.AGENT_REPLY,
        )
        sentinel = "excluded-private-sentinel"
        excluded_paths = (
            "discussion_logs/runtime.log",
            "discussion_logs/debate_only/debate.jsonl",
            "discussion_logs/session_a/debate.jsonl",
            "discussion_logs/session_a/debate.state.json",
            "discussion_logs/session_a/debate.result.json",
            "discussion_logs/session_a/debate.context.json",
            "debug_logs/runtime.jsonl",
            "config/provider_credentials.json",
            "agent_states/orchestrator_state/provider_threads.json",
            "config/rag/projection.json",
        )
        for path in excluded_paths:
            workspace.write_text(path, sentinel + "\n", overwrite=False)
        workspace.path("manuscript/excluded.md").write_text(
            sentinel + "\n", encoding="utf-8"
        )

        adapter = compose_filesystem_conversation_archive(workspace)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            first = adapter.export_archive(lease)
            second = adapter.export_archive(lease)

        self.assertEqual(first, second)
        self.assertEqual(
            serialize_conversation_archive(first),
            serialize_conversation_archive(second),
        )
        self.assertEqual(
            tuple(session.session_id for session in first.sessions),
            ("session_a", "session_b"),
        )
        by_id = {
            conversation.conversation_id: conversation
            for session in first.sessions
            for conversation in session.conversations
        }
        self.assertEqual(
            set(by_id),
            {
                first_metadata.conversation_id,
                second_metadata.conversation_id,
                third_metadata.conversation_id,
            },
        )
        self.assertEqual(by_id[first_metadata.conversation_id].events, ())
        self.assertIs(
            by_id[second_metadata.conversation_id].status,
            ConversationStatus.ARCHIVED,
        )
        self.assertEqual(
            tuple(
                event.content for event in by_id[second_metadata.conversation_id].events
            ),
            tuple(event.content for event in second_events),
        )
        self.assertEqual(
            tuple(event.kind for event in by_id[third_metadata.conversation_id].events),
            tuple(event.kind for event in third_events),
        )
        wire = serialize_conversation_archive(first)
        self.assertNotIn(sentinel.encode(), wire)
        for path in excluded_paths:
            self.assertNotIn(path.encode(), wire)
        self.assertNotIn(b"excluded.md", wire)
        for excluded_name in (
            b"agent_configs",
            b"system_defaults",
            b"persona_templates",
            b"conversation_storage_activation",
            b"projection",
        ):
            self.assertNotIn(excluded_name, wire)

    def test_inventory_reads_only_addressed_catalog_records(self) -> None:
        source = initialized_workspace(self)
        metadata, _ = _create_ordinary_conversation(
            source,
            session_id="session_a",
            subject="Addressed",
        )
        source_adapter = compose_filesystem_conversation_archive(source)
        source_lease = ControlledLease(os.fspath(source.root))
        original_get = FilesystemConversationCatalog.get
        addressed: list[tuple[str, str]] = []

        def record_get(
            catalog: FilesystemConversationCatalog,
            session_id: str,
            conversation_id: str,
        ) -> ConversationMetadata:
            addressed.append((session_id, conversation_id))
            return original_get(catalog, session_id, conversation_id)

        destination = initialized_workspace(self)
        destination_adapter = compose_filesystem_conversation_archive(destination)
        destination_lease = ControlledLease(os.fspath(destination.root))
        with (
            mock.patch.object(
                FilesystemConversationCatalog,
                "get",
                new=record_get,
            ),
            mock.patch.object(
                FilesystemConversationCatalog,
                "list",
                side_effect=AssertionError("catalog.list must not be used"),
            ) as catalog_list,
        ):
            exported = source_adapter.export_archive(source_lease)
            receipt = destination_adapter.import_archive(
                destination_lease,
                exported,
            )

        self.assertIs(receipt.status, ConversationArchiveImportStatus.IMPORTED)
        self.assertTrue(addressed)
        self.assertEqual(
            set(addressed),
            {("session_a", metadata.conversation_id)},
        )
        catalog_list.assert_not_called()

    def test_conversation_change_between_scans_is_detected(self) -> None:
        workspace = initialized_workspace(self)
        metadata, _ = _create_ordinary_conversation(
            workspace,
            session_id="session_a",
            subject="Before",
        )
        catalog = FilesystemConversationCatalog(workspace)
        adapter = compose_filesystem_conversation_archive(workspace)
        lease = ControlledLease(os.fspath(workspace.root))
        original_get = FilesystemConversationCatalog.get
        changed = False

        def get_then_rename(
            operation_catalog: FilesystemConversationCatalog,
            session_id: str,
            conversation_id: str,
        ) -> ConversationMetadata:
            nonlocal changed
            stored = original_get(operation_catalog, session_id, conversation_id)
            if not changed:
                changed = True
                catalog.rename("session_a", metadata.conversation_id, "After")
            return stored

        with mock.patch.object(
            FilesystemConversationCatalog,
            "get",
            new=get_then_rename,
        ):
            with self.assertRaises(ConversationArchiveSourceChangedError):
                adapter.export_archive(lease)

    def test_excluded_only_change_between_scans_does_not_change_archive(self) -> None:
        workspace = initialized_workspace(self)
        _create_ordinary_conversation(
            workspace,
            session_id="session_a",
            subject="Stable",
        )
        adapter = compose_filesystem_conversation_archive(workspace)
        lease = ControlledLease(os.fspath(workspace.root))
        original_get = FilesystemConversationCatalog.get
        changed = False

        def get_then_change_excluded(
            operation_catalog: FilesystemConversationCatalog,
            session_id: str,
            conversation_id: str,
        ) -> ConversationMetadata:
            nonlocal changed
            stored = original_get(operation_catalog, session_id, conversation_id)
            if not changed:
                changed = True
                workspace.write_text(
                    "discussion_logs/runtime.log",
                    "excluded-private-sentinel\n",
                    overwrite=False,
                )
            return stored

        with mock.patch.object(
            FilesystemConversationCatalog,
            "get",
            new=get_then_change_excluded,
        ):
            exported = adapter.export_archive(lease)

        self.assertEqual(len(exported.sessions), 1)
        self.assertNotIn(
            b"excluded-private-sentinel",
            serialize_conversation_archive(exported),
        )

    def test_malformed_or_unsafe_conversation_subtrees_fail_content_free(self) -> None:
        def unknown_artifact(
            workspace: Workspace,
            metadata: ConversationMetadata,
        ) -> None:
            relative = metadata_relative(metadata.session_id, metadata.conversation_id)
            workspace.write_text(relative.with_name("unknown.txt"), "secret-sentinel")

        def malformed_metadata(
            workspace: Workspace,
            metadata: ConversationMetadata,
        ) -> None:
            workspace.write_text(
                metadata_relative(metadata.session_id, metadata.conversation_id),
                "secret-sentinel",
            )

        def hardlinked_metadata(
            workspace: Workspace,
            metadata: ConversationMetadata,
        ) -> None:
            target = workspace.path(
                metadata_relative(metadata.session_id, metadata.conversation_id)
            )
            os.link(target, workspace.root / "metadata-hardlink")

        def symlinked_metadata(
            workspace: Workspace,
            metadata: ConversationMetadata,
        ) -> None:
            target = workspace.path(
                metadata_relative(metadata.session_id, metadata.conversation_id)
            )
            target.unlink()
            os.symlink(workspace.path("config/system_defaults.json"), target)

        def empty_transcript(
            workspace: Workspace,
            metadata: ConversationMetadata,
        ) -> None:
            workspace.write_text(
                transcript_relative(metadata.session_id, metadata.conversation_id),
                "",
                overwrite=False,
            )

        mutators = (
            unknown_artifact,
            malformed_metadata,
            hardlinked_metadata,
            symlinked_metadata,
            empty_transcript,
        )
        for mutator in mutators:
            with self.subTest(mutator=mutator.__name__):
                workspace = initialized_workspace(self)
                metadata, _ = _create_ordinary_conversation(
                    workspace,
                    session_id="session_a",
                    subject="Subject",
                )
                mutator(workspace, metadata)
                adapter = compose_filesystem_conversation_archive(workspace)
                lease = ControlledLease(os.fspath(workspace.root))
                with self.assertRaises(ConversationArchiveSourceError) as raised:
                    adapter.export_archive(lease)
                self.assertNotIn("secret-sentinel", str(raised.exception))

    def test_partial_or_orphan_source_layouts_fail_closed(self) -> None:
        def empty_interactive(workspace: Workspace) -> None:
            workspace.path("discussion_logs/session_a/interactive").mkdir(parents=True)

        def empty_catalog(workspace: Workspace) -> None:
            workspace.path("discussion_logs/session_a/interactive/conversations").mkdir(
                parents=True
            )

        def orphan_transcript(workspace: Workspace) -> None:
            relative = transcript_relative("session_a", "conversation_a")
            workspace.write_text(relative, "{}\n", overwrite=False)

        def unsafe_identity(workspace: Workspace) -> None:
            workspace.path(
                "discussion_logs/session_a/interactive/conversations/bad.id"
            ).mkdir(parents=True)

        for mutator in (
            empty_interactive,
            empty_catalog,
            orphan_transcript,
            unsafe_identity,
        ):
            with self.subTest(mutator=mutator.__name__):
                workspace = initialized_workspace(self)
                mutator(workspace)
                adapter = compose_filesystem_conversation_archive(workspace)
                with self.assertRaises(ConversationArchiveSourceError):
                    adapter.export_archive(ControlledLease(os.fspath(workspace.root)))

    def test_unsafe_excluded_entries_fail_closed(self) -> None:
        for kind in ("symlink", "hardlink", "directory"):
            with self.subTest(kind=kind):
                workspace = initialized_workspace(self)
                source = workspace.path("config/system_defaults.json")
                target = workspace.root / "discussion_logs" / "excluded"
                if kind == "symlink":
                    os.symlink(source, target)
                elif kind == "hardlink":
                    os.link(source, target)
                else:
                    target.mkdir()
                    os.symlink(source, target / "unsafe")
                adapter = compose_filesystem_conversation_archive(workspace)
                with self.assertRaises(ConversationArchiveSourceError):
                    adapter.export_archive(ControlledLease(os.fspath(workspace.root)))

    def test_excluded_entries_are_charged_but_their_bytes_are_never_read(self) -> None:
        workspace = initialized_workspace(self)
        excluded = workspace.path("discussion_logs/runtime.log")
        excluded_bytes = b"excluded-private-sentinel\n"
        excluded.write_bytes(excluded_bytes)
        sentinel = workspace.path("discussion_logs/.gitkeep")
        adapter = compose_filesystem_conversation_archive(workspace)
        lease = ControlledLease(os.fspath(workspace.root))
        original_read_bytes = Path.read_bytes
        forbidden_reads: list[Path] = []

        def reject_excluded_read(path: Path) -> bytes:
            if path in (excluded, sentinel):
                forbidden_reads.append(path)
                raise AssertionError("excluded archive bytes were read")
            return original_read_bytes(path)

        with (
            mock.patch.object(Path, "read_bytes", new=reject_excluded_read),
            mock.patch.object(
                filesystem_archive_module,
                "_MAX_INVENTORY_ENTRIES",
                2,
            ),
        ):
            exported = adapter.export_archive(lease)
        self.assertEqual(exported, ConversationArchive(1, ()))
        self.assertEqual(forbidden_reads, [])
        self.assertEqual(excluded.read_bytes(), excluded_bytes)

        with (
            mock.patch.object(Path, "read_bytes", new=reject_excluded_read),
            mock.patch.object(
                filesystem_archive_module,
                "_MAX_INVENTORY_ENTRIES",
                1,
            ),
        ):
            with self.assertRaises(ConversationArchiveBoundsError) as caught:
                adapter.export_archive(lease)
        self.assertEqual(str(caught.exception), "conversation archive bounds exceeded")
        self.assertIsNone(caught.exception.__cause__)
        self.assertEqual(forbidden_reads, [])

    def test_inventory_count_limits_and_exact_boundaries(self) -> None:
        def export(workspace: Workspace) -> ConversationArchive:
            return compose_filesystem_conversation_archive(workspace).export_archive(
                ControlledLease(os.fspath(workspace.root))
            )

        cases: list[tuple[str, Workspace, int, int]] = []
        sessions = initialized_workspace(self)
        _create_ordinary_conversation(
            sessions,
            session_id="session_a",
            subject="A",
        )
        _create_ordinary_conversation(
            sessions,
            session_id="session_b",
            subject="B",
        )
        cases.append(("CONVERSATION_ARCHIVE_MAX_SESSIONS", sessions, 2, 1))

        conversations = initialized_workspace(self)
        _create_ordinary_conversation(
            conversations,
            session_id="session_a",
            subject="A",
        )
        _create_ordinary_conversation(
            conversations,
            session_id="session_a",
            subject="B",
        )
        cases.append(("CONVERSATION_ARCHIVE_MAX_CONVERSATIONS", conversations, 2, 1))

        events = initialized_workspace(self)
        _create_ordinary_conversation(
            events,
            session_id="session_a",
            subject="Events",
            outcome_kind=ConversationEventKind.AGENT_REPLY,
        )
        cases.append(("CONVERSATION_ARCHIVE_MAX_EVENTS", events, 2, 1))

        for constant, workspace, inclusive, overflow in cases:
            with self.subTest(constant=constant, boundary="inclusive"):
                with mock.patch.object(
                    filesystem_archive_module,
                    constant,
                    inclusive,
                ):
                    export(workspace)
            with self.subTest(constant=constant, boundary="overflow"):
                before = workspace_snapshot(workspace.root)
                with mock.patch.object(
                    filesystem_archive_module,
                    constant,
                    overflow,
                ):
                    with self.assertRaises(ConversationArchiveBoundsError) as caught:
                        export(workspace)
                self.assertEqual(
                    str(caught.exception),
                    "conversation archive bounds exceeded",
                )
                self.assertIsNone(caught.exception.__cause__)
                self.assertEqual(workspace_snapshot(workspace.root), before)

    def test_file_byte_logical_and_wire_limits_are_inclusive(self) -> None:
        workspace = initialized_workspace(self)
        metadata, events = _create_ordinary_conversation(
            workspace,
            session_id="session_a",
            subject="Bounded subject",
            outcome_kind=ConversationEventKind.AGENT_REPLY,
        )
        adapter = compose_filesystem_conversation_archive(workspace)
        lease = ControlledLease(os.fspath(workspace.root))
        exported = adapter.export_archive(lease)
        metadata_size = (
            workspace.path(
                metadata_relative(metadata.session_id, metadata.conversation_id)
            )
            .stat()
            .st_size
        )
        transcript_size = (
            workspace.path(
                transcript_relative(metadata.session_id, metadata.conversation_id)
            )
            .stat()
            .st_size
        )
        included_total = metadata_size + transcript_size
        logical_total = len(metadata.subject.encode()) + sum(
            len(event.content.encode()) for event in events
        )
        wire_size = len(serialize_conversation_archive(exported))
        cases = (
            (
                filesystem_archive_module,
                "_MAX_INCLUDED_FILE_BYTES",
                max(metadata_size, transcript_size),
            ),
            (
                filesystem_archive_module,
                "_MAX_INCLUDED_TOTAL_BYTES",
                included_total,
            ),
            (
                filesystem_archive_module,
                "CONVERSATION_ARCHIVE_MAX_AGGREGATE_UTF8_BYTES",
                logical_total,
            ),
            (archive_module, "CONVERSATION_ARCHIVE_MAX_RAW_BYTES", wire_size),
        )
        for owner, constant, inclusive in cases:
            with self.subTest(constant=constant, boundary="inclusive"):
                with mock.patch.object(owner, constant, inclusive):
                    self.assertEqual(adapter.export_archive(lease), exported)
            with self.subTest(constant=constant, boundary="overflow"):
                before = workspace_snapshot(workspace.root)
                with mock.patch.object(owner, constant, inclusive - 1):
                    with self.assertRaises(ConversationArchiveBoundsError) as caught:
                        adapter.export_archive(lease)
                self.assertEqual(
                    str(caught.exception),
                    "conversation archive bounds exceeded",
                )
                self.assertIsNone(caught.exception.__cause__)
                self.assertEqual(workspace_snapshot(workspace.root), before)


class FilesystemConversationArchiveImportTests(unittest.TestCase):
    def _import(
        self,
        workspace: Workspace,
        payload: ConversationArchive,
    ) -> tuple[
        FilesystemConversationArchiveAdapter,
        ConversationArchiveImportReceipt,
    ]:
        adapter = compose_filesystem_conversation_archive(workspace)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            receipt = adapter.import_archive(lease, payload)
        return adapter, receipt

    def test_completion_callback_is_keyword_only_ordered_and_exact(self) -> None:
        parameter = inspect.signature(
            FilesystemConversationArchiveAdapter.import_archive
        ).parameters["on_conversation_completed"]
        self.assertIs(parameter.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIsNone(parameter.default)

        workspace = initialized_workspace(self)
        payload = archive_from_conversations(
            archive_conversation(
                session_id="session_a",
                conversation_id="conversation_a",
                subject="empty callback record",
            ),
            archive_conversation(
                session_id="session_b",
                conversation_id="conversation_b",
                subject="event callback record",
                events=archive_events(
                    session_id="session_b",
                    conversation_id="conversation_b",
                ),
            ),
        )
        adapter = compose_filesystem_conversation_archive(workspace)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        completed: list[tuple[str, str]] = []

        def observe(session_id: str, conversation_id: str) -> None:
            metadata = workspace.path(metadata_relative(session_id, conversation_id))
            transcript = workspace.path(
                transcript_relative(session_id, conversation_id)
            )
            self.assertTrue(metadata.is_file())
            if conversation_id == "conversation_a":
                self.assertFalse(transcript.exists())
            else:
                self.assertTrue(transcript.is_file())
                self.assertEqual(
                    len(transcript.read_text(encoding="utf-8").splitlines()), 2
                )
            completed.append((session_id, conversation_id))

        with coordinator.borrow(timeout_seconds=2.0) as lease:
            receipt = adapter.import_archive(
                lease,
                payload,
                on_conversation_completed=observe,
            )
        self.assertIs(receipt.status, ConversationArchiveImportStatus.IMPORTED)
        self.assertEqual(
            completed,
            [("session_a", "conversation_a"), ("session_b", "conversation_b")],
        )

        completed.clear()
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            unchanged = adapter.import_archive(
                lease,
                payload,
                on_conversation_completed=observe,
            )
        self.assertIs(unchanged.status, ConversationArchiveImportStatus.UNCHANGED)
        self.assertEqual(
            completed,
            [("session_a", "conversation_a"), ("session_b", "conversation_b")],
        )

    def test_callback_failure_leaves_complete_retryable_record_and_preflight_never_calls(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        payload = one_conversation_archive(events=archive_events())
        adapter = compose_filesystem_conversation_archive(workspace)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        calls: list[tuple[str, str]] = []

        def fail_after_completion(session_id: str, conversation_id: str) -> None:
            calls.append((session_id, conversation_id))
            raise RuntimeError("private callback sentinel")

        with coordinator.borrow(timeout_seconds=2.0) as lease:
            with self.assertRaisesRegex(RuntimeError, "private callback sentinel"):
                adapter.import_archive(
                    lease,
                    payload,
                    on_conversation_completed=fail_after_completion,
                )
        self.assertEqual(calls, [("session_a", "conversation_a")])
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            self.assertEqual(adapter.export_archive(lease), payload)
            retry = adapter.import_archive(
                lease,
                payload,
                on_conversation_completed=lambda session_id,
                conversation_id: calls.append((session_id, conversation_id)),
            )
        self.assertIs(retry.status, ConversationArchiveImportStatus.UNCHANGED)
        self.assertEqual(len(calls), 2)

        conflict = one_conversation_archive(subject="different")
        calls.clear()
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            with self.assertRaises(ConversationArchiveConflictError):
                adapter.import_archive(
                    lease,
                    conflict,
                    on_conversation_completed=lambda session_id,
                    conversation_id: calls.append((session_id, conversation_id)),
                )
        self.assertEqual(calls, [])

    def test_absent_import_is_exact_and_identical_retry_is_unchanged(self) -> None:
        workspace = initialized_workspace(self)
        payload = nonempty_archive()
        adapter, receipt = self._import(workspace, payload)
        self.assertIs(receipt.status, ConversationArchiveImportStatus.IMPORTED)
        catalog = FilesystemConversationCatalog(workspace)
        metadata = catalog.get("session_a", "conversation_a")
        conversation = payload.sessions[0].conversations[0]
        self.assertEqual(
            (
                metadata.subject,
                metadata.subject_revision,
                metadata.status,
                metadata.created_at,
                metadata.updated_at,
            ),
            (
                conversation.subject,
                conversation.subject_revision,
                conversation.status,
                conversation.created_at,
                conversation.updated_at,
            ),
        )
        events = FilesystemConversationEventStore(workspace, catalog=catalog).read(
            "session_a", "conversation_a"
        )
        self.assertEqual(
            tuple(
                (
                    event.turn_id,
                    event.sequence,
                    event.timestamp,
                    event.sender,
                    event.recipient,
                    event.kind,
                    event.content,
                )
                for event in events
            ),
            tuple(
                (
                    event.turn_id,
                    event.sequence,
                    event.timestamp,
                    event.sender,
                    event.recipient,
                    event.kind,
                    event.content,
                )
                for event in conversation.events
            ),
        )
        before_retry = workspace_snapshot(workspace.root)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            retry = adapter.import_archive(lease, payload)
            exported = adapter.export_archive(lease)
        self.assertIs(retry.status, ConversationArchiveImportStatus.UNCHANGED)
        self.assertEqual(workspace_snapshot(workspace.root), before_retry)
        self.assertEqual(exported, payload)

    def test_empty_archive_and_empty_history_are_exact(self) -> None:
        workspace = initialized_workspace(self)
        adapter, empty_receipt = self._import(
            workspace,
            ConversationArchive(1, ()),
        )
        self.assertIs(
            empty_receipt.status,
            ConversationArchiveImportStatus.UNCHANGED,
        )
        payload = one_conversation_archive()
        _, receipt = self._import(workspace, payload)
        self.assertIs(receipt.status, ConversationArchiveImportStatus.IMPORTED)
        self.assertTrue(
            workspace.path(metadata_relative("session_a", "conversation_a")).is_file()
        )
        self.assertFalse(
            workspace.path(transcript_relative("session_a", "conversation_a")).exists()
        )
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            self.assertEqual(adapter.export_archive(lease), payload)

    def test_missing_destination_root_accepts_empty_and_nonempty_archives(self) -> None:
        for payload in (ConversationArchive(1, ()), one_conversation_archive()):
            with self.subTest(empty=not payload.sessions):
                temporary = tempfile.TemporaryDirectory()
                self.addCleanup(temporary.cleanup)
                workspace = Workspace(temporary.name)
                adapter = compose_filesystem_conversation_archive(workspace)
                receipt = adapter.import_archive(
                    ControlledLease(os.fspath(workspace.root)),
                    payload,
                )
                self.assertIs(
                    receipt.status,
                    ConversationArchiveImportStatus.UNCHANGED
                    if not payload.sessions
                    else ConversationArchiveImportStatus.IMPORTED,
                )
                self.assertEqual(
                    workspace.path("discussion_logs").exists(),
                    bool(payload.sessions),
                )

    def test_mixed_additive_and_unrelated_complete_records_are_preserved(self) -> None:
        workspace = initialized_workspace(self)
        conversation_a = archive_conversation(conversation_id="conversation_a")
        conversation_b = archive_conversation(conversation_id="conversation_b")
        unrelated = archive_conversation(
            session_id="session_z",
            conversation_id="conversation_z",
            subject="Unrelated",
        )
        self._import(workspace, archive_from_conversations(conversation_a, unrelated))
        unrelated_relative = metadata_relative("session_z", "conversation_z")
        unrelated_bytes = workspace.path(unrelated_relative).read_bytes()

        adapter, receipt = self._import(
            workspace,
            archive_from_conversations(conversation_a, conversation_b),
        )

        self.assertIs(receipt.status, ConversationArchiveImportStatus.IMPORTED)
        self.assertEqual(
            workspace.path(unrelated_relative).read_bytes(),
            unrelated_bytes,
        )
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            exported = adapter.export_archive(lease)
        identities = {
            (session.session_id, conversation.conversation_id)
            for session in exported.sessions
            for conversation in session.conversations
        }
        self.assertEqual(
            identities,
            {
                ("session_a", "conversation_a"),
                ("session_a", "conversation_b"),
                ("session_z", "conversation_z"),
            },
        )

    def test_destination_excluded_state_is_preserved_byte_for_byte(self) -> None:
        workspace = initialized_workspace(self)
        excluded = {
            "discussion_logs/runtime.log": b"root excluded\n",
            "discussion_logs/session_a/debate.jsonl": b"debate excluded\n",
            "debug_logs/runtime.jsonl": b"debug excluded\n",
            "config/provider_credentials.json": b"credential excluded\n",
        }
        for relative, content in excluded.items():
            workspace.write_text(relative, content.decode(), overwrite=False)

        self._import(workspace, nonempty_archive())

        for relative, content in excluded.items():
            with self.subTest(relative=relative):
                self.assertEqual(workspace.path(relative).read_bytes(), content)

    def test_destination_union_limits_fail_before_any_archive_write(self) -> None:
        def prepare(
            case: str,
        ) -> tuple[
            Workspace,
            ConversationArchive,
            tuple[tuple[object, str, int], ...],
        ]:
            workspace = initialized_workspace(self)
            with_events = case in {"events", "entries", "included_bytes"}
            existing_events = (
                archive_events(
                    session_id="session_a",
                    conversation_id="conversation_a",
                )
                if case == "events"
                else ()
            )
            incoming_session = "session_a" if case == "conversations" else "session_b"
            incoming_events = (
                archive_events(
                    session_id=incoming_session,
                    conversation_id="conversation_b",
                )
                if with_events
                else ()
            )
            incoming_subject = "X" * 200 if case == "file_bytes" else "B"
            existing = archive_conversation(
                session_id="session_a",
                conversation_id="conversation_a",
                subject="A",
                events=existing_events,
            )
            incoming = archive_conversation(
                session_id=incoming_session,
                conversation_id="conversation_b",
                subject=incoming_subject,
                events=incoming_events,
            )
            self._import(workspace, archive_from_conversations(existing))
            payload = archive_from_conversations(incoming)
            union = archive_from_conversations(existing, incoming)

            metadata = filesystem_archive_module._metadata_text(incoming).encode()
            transcript = filesystem_archive_module._transcript_text(incoming).encode()
            planned_files = (
                metadata,
                *((transcript,) if transcript else ()),
            )
            current_paths = {
                path.relative_to(workspace.root)
                for path in workspace.path("discussion_logs").rglob("*")
            }
            planned_relatives = [
                metadata_relative(incoming.session_id, incoming.conversation_id)
            ]
            if transcript:
                planned_relatives.append(
                    transcript_relative(
                        incoming.session_id,
                        incoming.conversation_id,
                    )
                )
            projected_paths = set(current_paths)
            for relative in planned_relatives:
                projected_paths.add(relative)
                projected_paths.update(
                    parent
                    for parent in relative.parents
                    if parent != Path(".")
                    and parent != Path("discussion_logs")
                    and parent.parts[0] == "discussion_logs"
                )
            included_before = sum(
                path.stat().st_size
                for path in workspace.path("discussion_logs").rglob("*")
                if path.is_file() and path.name in {"metadata.json", "transcript.jsonl"}
            )
            aggregate = sum(
                len(conversation.subject.encode())
                + sum(len(event.content.encode()) for event in conversation.events)
                for conversation in (existing, incoming)
            )
            exact_by_case = {
                "sessions": 2,
                "conversations": 2,
                "events": len(existing.events) + len(incoming.events),
                "entries": len(projected_paths),
                "file_bytes": max(map(len, planned_files)),
                "included_bytes": included_before + sum(map(len, planned_files)),
                "aggregate": aggregate,
                "wire": len(serialize_conversation_archive(union)),
            }
            constant_by_case = {
                "sessions": "CONVERSATION_ARCHIVE_MAX_SESSIONS",
                "conversations": "CONVERSATION_ARCHIVE_MAX_CONVERSATIONS",
                "events": "CONVERSATION_ARCHIVE_MAX_EVENTS",
                "entries": "_MAX_INVENTORY_ENTRIES",
                "file_bytes": "_MAX_INCLUDED_FILE_BYTES",
                "included_bytes": "_MAX_INCLUDED_TOTAL_BYTES",
                "aggregate": "CONVERSATION_ARCHIVE_MAX_AGGREGATE_UTF8_BYTES",
                "wire": "CONVERSATION_ARCHIVE_MAX_RAW_BYTES",
            }
            constant = constant_by_case[case]
            exact = exact_by_case[case]
            if case == "file_bytes":
                existing_maximum = max(
                    path.stat().st_size
                    for path in workspace.path("discussion_logs").rglob("*")
                    if path.is_file() and path.name == "metadata.json"
                )
                self.assertLess(existing_maximum, exact)
            if case in {"sessions", "conversations", "events", "aggregate"}:
                patches = (
                    (archive_module, constant, exact),
                    (filesystem_archive_module, constant, exact),
                )
            elif case == "wire":
                patches = ((archive_module, constant, exact),)
            else:
                patches = ((filesystem_archive_module, constant, exact),)
            return workspace, payload, patches

        cases = (
            "sessions",
            "conversations",
            "events",
            "entries",
            "file_bytes",
            "included_bytes",
            "aggregate",
            "wire",
        )
        for case in cases:
            with self.subTest(case=case, boundary="inclusive"):
                workspace, payload, patches = prepare(case)
                with ExitStack() as stack:
                    for owner, constant, exact in patches:
                        stack.enter_context(mock.patch.object(owner, constant, exact))
                    _, receipt = self._import(workspace, payload)
                self.assertIs(
                    receipt.status,
                    ConversationArchiveImportStatus.IMPORTED,
                )

            with self.subTest(case=case, boundary="overflow"):
                workspace, payload, patches = prepare(case)
                destination_before = workspace_snapshot(workspace.root)
                source_before = serialize_conversation_archive(payload)
                with ExitStack() as stack:
                    for owner, constant, exact in patches:
                        stack.enter_context(
                            mock.patch.object(owner, constant, exact - 1)
                        )
                    write_text = stack.enter_context(
                        mock.patch.object(
                            Workspace,
                            "write_text",
                            side_effect=AssertionError(
                                "archive write happened before preflight completed"
                            ),
                        )
                    )
                    with self.assertRaises(ConversationArchiveBoundsError) as caught:
                        self._import(workspace, payload)
                self.assertEqual(
                    str(caught.exception),
                    "conversation archive bounds exceeded",
                )
                self.assertIsNone(caught.exception.__cause__)
                write_text.assert_not_called()
                self.assertEqual(
                    workspace_snapshot(workspace.root),
                    destination_before,
                )
                self.assertEqual(serialize_conversation_archive(payload), source_before)

    def test_complete_preflight_rejects_late_conflict_before_earlier_write(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        expected_b = archive_conversation(conversation_id="conversation_b")
        divergent_b = archive_conversation(
            conversation_id="conversation_b", subject="Divergent"
        )
        self._import(workspace, archive_from_conversations(divergent_b))
        before = workspace_snapshot(workspace.root)
        conversation_a = archive_conversation(conversation_id="conversation_a")
        adapter = compose_filesystem_conversation_archive(workspace)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            with self.assertRaises(ConversationArchiveConflictError):
                adapter.import_archive(
                    lease,
                    archive_from_conversations(conversation_a, expected_b),
                )
        self.assertEqual(workspace_snapshot(workspace.root), before)
        self.assertFalse(
            workspace.path(metadata_relative("session_a", "conversation_a")).exists()
        )

    def test_metadata_and_transcript_divergence_never_overwrite(self) -> None:
        for target in ("metadata", "transcript"):
            with self.subTest(target=target):
                workspace = initialized_workspace(self)
                payload = nonempty_archive()
                self._import(workspace, payload)
                if target == "metadata":
                    relative = metadata_relative("session_a", "conversation_a")
                    value = json.loads(workspace.path(relative).read_text())
                    value["subject"] = "Divergent"
                    workspace.write_json(relative, value)
                else:
                    relative = transcript_relative("session_a", "conversation_a")
                    lines = workspace.path(relative).read_text().splitlines()
                    value = json.loads(lines[0])
                    value["content"] = "Divergent"
                    lines[0] = json.dumps(
                        value,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    workspace.write_text(relative, "\n".join(lines) + "\n")
                before = workspace_snapshot(workspace.root)
                adapter = compose_filesystem_conversation_archive(workspace)
                coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
                with coordinator.borrow(timeout_seconds=2.0) as lease:
                    with self.assertRaises(ConversationArchiveConflictError):
                        adapter.import_archive(lease, payload)
                self.assertEqual(workspace_snapshot(workspace.root), before)

    def test_invalid_payload_and_destination_fail_before_archive_writes(self) -> None:
        workspace = initialized_workspace(self)
        adapter = compose_filesystem_conversation_archive(workspace)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            before = workspace_snapshot(workspace.root)
            invalid = nonempty_archive()
            object.__setattr__(
                invalid.sessions[0].conversations[0],
                "subject",
                "secret-sentinel\x00",
            )
            with self.assertRaises(ConversationArchiveFormatError) as raised:
                adapter.import_archive(lease, invalid)
            self.assertNotIn("secret-sentinel", str(raised.exception))
            self.assertEqual(workspace_snapshot(workspace.root), before)

        malformed = metadata_relative("other", "other_conversation")
        workspace.write_text(malformed, "secret-sentinel", overwrite=False)
        before = workspace_snapshot(workspace.root)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            with self.assertRaises(
                ConversationArchiveDestinationError
            ) as destination_raised:
                adapter.import_archive(lease, one_conversation_archive())
            self.assertNotIn(
                "secret-sentinel",
                str(destination_raised.exception),
            )
        self.assertEqual(workspace_snapshot(workspace.root), before)

    def test_empty_prefix_and_exact_metadata_only_residues_resume(self) -> None:
        for residue in ("session", "interactive", "catalog", "conversation"):
            with self.subTest(residue=residue):
                workspace = initialized_workspace(self)
                prefixes = {
                    "session": "discussion_logs/session_a",
                    "interactive": "discussion_logs/session_a/interactive",
                    "catalog": "discussion_logs/session_a/interactive/conversations",
                    "conversation": (
                        "discussion_logs/session_a/interactive/conversations/"
                        "conversation_a"
                    ),
                }
                workspace.path(prefixes[residue]).mkdir(parents=True)
                _, receipt = self._import(workspace, nonempty_archive())
                self.assertIs(
                    receipt.status,
                    ConversationArchiveImportStatus.IMPORTED,
                )

        workspace = initialized_workspace(self)
        empty_history = one_conversation_archive(
            subject="안녕 🌙",
            status=ConversationStatus.ARCHIVED,
        )
        self._import(workspace, empty_history)
        _, receipt = self._import(workspace, nonempty_archive())
        self.assertIs(receipt.status, ConversationArchiveImportStatus.IMPORTED)
        self.assertTrue(
            workspace.path(transcript_relative("session_a", "conversation_a")).is_file()
        )

    def test_metadata_first_operational_failure_is_resumable(self) -> None:
        workspace = initialized_workspace(self)
        payload = nonempty_archive()
        adapter = compose_filesystem_conversation_archive(workspace)
        original_write_text = Workspace.write_text
        completed: list[tuple[str, str]] = []

        def fail_transcript(
            operation_workspace: Workspace,
            relative: str | os.PathLike[str],
            content: str,
            *,
            overwrite: bool = True,
        ) -> Path:
            if Path(relative).name == "transcript.jsonl":
                raise OSError("operational failure")
            return original_write_text(
                operation_workspace,
                relative,
                content,
                overwrite=overwrite,
            )

        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with mock.patch.object(
            Workspace,
            "write_text",
            new=fail_transcript,
        ):
            with coordinator.borrow(timeout_seconds=2.0) as lease:
                with self.assertRaises(ConversationArchiveDestinationError) as raised:
                    adapter.import_archive(
                        lease,
                        payload,
                        on_conversation_completed=lambda session_id,
                        conversation_id: completed.append(
                            (session_id, conversation_id)
                        ),
                    )
                self.assertIs(
                    type(raised.exception), ConversationArchiveDestinationError
                )
        self.assertEqual(completed, [])
        self.assertTrue(
            workspace.path(metadata_relative("session_a", "conversation_a")).is_file()
        )
        self.assertFalse(
            workspace.path(transcript_relative("session_a", "conversation_a")).exists()
        )
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            receipt = adapter.import_archive(
                lease,
                payload,
                on_conversation_completed=lambda session_id,
                conversation_id: completed.append((session_id, conversation_id)),
            )
        self.assertIs(receipt.status, ConversationArchiveImportStatus.IMPORTED)
        self.assertEqual(completed, [("session_a", "conversation_a")])

    def test_empty_directory_residue_after_metadata_failure_is_resumable(self) -> None:
        workspace = initialized_workspace(self)
        payload = nonempty_archive()
        adapter = compose_filesystem_conversation_archive(workspace)

        def fail_after_prefix(
            operation_workspace: Workspace,
            relative: str | os.PathLike[str],
            _content: str,
            *,
            overwrite: bool = True,
        ) -> Path:
            del overwrite
            target = operation_workspace.path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            raise OSError("operational failure")

        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with mock.patch.object(
            Workspace,
            "write_text",
            new=fail_after_prefix,
        ):
            with coordinator.borrow(timeout_seconds=2.0) as lease:
                with self.assertRaises(ConversationArchiveDestinationError):
                    adapter.import_archive(lease, payload)
        conversation_directory = workspace.path(
            metadata_relative("session_a", "conversation_a").parent
        )
        self.assertTrue(conversation_directory.is_dir())
        self.assertEqual(list(conversation_directory.iterdir()), [])
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            receipt = adapter.import_archive(lease, payload)
        self.assertIs(receipt.status, ConversationArchiveImportStatus.IMPORTED)

    def test_install_collision_and_postwrite_mutation_are_detected(self) -> None:
        workspace = initialized_workspace(self)
        payload = nonempty_archive()
        adapter = compose_filesystem_conversation_archive(workspace)
        original_write_text = Workspace.write_text

        def collide_transcript(
            operation_workspace: Workspace,
            relative: str | os.PathLike[str],
            content: str,
            *,
            overwrite: bool = True,
        ) -> Path:
            if Path(relative).name == "transcript.jsonl":
                original_write_text(
                    operation_workspace,
                    relative,
                    content.replace("질문", "divergent"),
                    overwrite=False,
                )
                raise ManuscriptExistsError("collision")
            return original_write_text(
                operation_workspace,
                relative,
                content,
                overwrite=overwrite,
            )

        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with mock.patch.object(
            Workspace,
            "write_text",
            new=collide_transcript,
        ):
            with coordinator.borrow(timeout_seconds=2.0) as lease:
                with self.assertRaises(ConversationArchiveDestinationChangedError):
                    adapter.import_archive(lease, payload)
        self.assertIn(
            "divergent",
            workspace.path(
                transcript_relative("session_a", "conversation_a")
            ).read_text(),
        )

        workspace = initialized_workspace(self)
        unrelated = one_conversation_archive(
            session_id="session_z",
            conversation_id="conversation_z",
            subject="Before",
        )
        self._import(workspace, unrelated)
        adapter = compose_filesystem_conversation_archive(workspace)
        original_write_text = Workspace.write_text
        unrelated_relative = metadata_relative("session_z", "conversation_z")
        unrelated_value = json.loads(workspace.path(unrelated_relative).read_text())
        unrelated_value["subject"] = "After"
        mutated_text = (
            json.dumps(
                unrelated_value,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        mutated = False

        def mutate_after_preflight(
            operation_workspace: Workspace,
            relative: str | os.PathLike[str],
            content: str,
            *,
            overwrite: bool = True,
        ) -> Path:
            nonlocal mutated
            if not mutated and Path(relative).name == "metadata.json":
                mutated = True
                original_write_text(
                    operation_workspace,
                    unrelated_relative,
                    mutated_text,
                )
            return original_write_text(
                operation_workspace,
                relative,
                content,
                overwrite=overwrite,
            )

        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with mock.patch.object(
            Workspace,
            "write_text",
            new=mutate_after_preflight,
        ):
            with coordinator.borrow(timeout_seconds=2.0) as lease:
                with self.assertRaises(ConversationArchiveDestinationChangedError):
                    adapter.import_archive(lease, nonempty_archive())
        self.assertTrue(
            workspace.path(metadata_relative("session_a", "conversation_a")).is_file()
        )
        self.assertEqual(
            json.loads(workspace.path(unrelated_relative).read_text())["subject"],
            "After",
        )

    def test_wrong_or_invalidated_lease_retains_coordination_error(self) -> None:
        workspace = initialized_workspace(self)
        other = initialized_workspace(self)
        adapter = compose_filesystem_conversation_archive(workspace)
        other_coordinator = WorkspaceConversationStorageMutationCoordinator(other)
        with other_coordinator.borrow(timeout_seconds=2.0) as lease:
            with self.assertRaises(ConversationStorageCoordinationError):
                adapter.import_archive(lease, nonempty_archive())

        controlled = ControlledLease(os.fspath(workspace.root))
        original_write_text = Workspace.write_text

        def write_then_invalidate(
            operation_workspace: Workspace,
            relative: str | os.PathLike[str],
            content: str,
            *,
            overwrite: bool = True,
        ) -> Path:
            target = original_write_text(
                operation_workspace,
                relative,
                content,
                overwrite=overwrite,
            )
            if Path(relative).name == "metadata.json":
                controlled.deactivate()
            return target

        with mock.patch.object(
            Workspace,
            "write_text",
            new=write_then_invalidate,
        ):
            with self.assertRaises(ConversationStorageCoordinationError):
                adapter.import_archive(controlled, nonempty_archive())
        self.assertTrue(
            workspace.path(metadata_relative("session_a", "conversation_a")).is_file()
        )
        self.assertFalse(
            workspace.path(transcript_relative("session_a", "conversation_a")).exists()
        )

    def test_unsafe_or_orphan_destination_layouts_reject_before_target_write(
        self,
    ) -> None:
        def unknown_interactive(workspace: Workspace) -> None:
            workspace.path("discussion_logs/other/interactive").mkdir(parents=True)
            workspace.write_text(
                "discussion_logs/other/interactive/unknown.txt",
                "secret-sentinel",
            )

        def orphan_conversation(workspace: Workspace) -> None:
            workspace.path(
                "discussion_logs/other/interactive/conversations/orphan"
            ).mkdir(parents=True)

        def unsafe_identity(workspace: Workspace) -> None:
            workspace.path(
                "discussion_logs/other/interactive/conversations/bad.id"
            ).mkdir(parents=True)

        def symlinked_root_entry(workspace: Workspace) -> None:
            os.symlink(
                workspace.path("config/system_defaults.json"),
                workspace.root / "discussion_logs" / "unsafe",
            )

        for mutator in (
            unknown_interactive,
            orphan_conversation,
            unsafe_identity,
            symlinked_root_entry,
        ):
            with self.subTest(mutator=mutator.__name__):
                workspace = initialized_workspace(self)
                mutator(workspace)
                adapter = compose_filesystem_conversation_archive(workspace)
                with self.assertRaises(ConversationArchiveDestinationError):
                    adapter.import_archive(
                        ControlledLease(os.fspath(workspace.root)),
                        nonempty_archive(),
                    )
                self.assertFalse(
                    workspace.path(
                        metadata_relative("session_a", "conversation_a")
                    ).exists()
                )

    def test_ordinary_catalog_and_event_allocation_remains_unchanged(self) -> None:
        workspace = initialized_workspace(self)
        self._import(workspace, nonempty_archive())
        catalog = FilesystemConversationCatalog(workspace)
        allocated = catalog.create("session_a", "Ordinary allocation")
        self.assertTrue(allocated.conversation_id.startswith("c_"))
        self.assertNotEqual(allocated.conversation_id, "conversation_a")
        event_store = FilesystemConversationEventStore(workspace, catalog=catalog)
        stored = event_store.append(
            make_append(
                session_id="session_a",
                conversation_id=allocated.conversation_id,
                turn_id="ordinary_turn",
            )
        )
        self.assertEqual(stored.sequence, 1)
        self.assertEqual(stored.conversation_id, allocated.conversation_id)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
