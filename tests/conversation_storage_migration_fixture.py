"""Deterministic helpers for filesystem and SQLite migration tests."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from unittest import TestCase

from story_writing_agents import ConversationEventKind, ConversationStatus, Workspace
from story_writing_agents.conversation_storage import (
    CONVERSATION_STORAGE_ACTIVATION_STATE_PATH,
    ConversationArchive,
    ConversationStorageActivationState,
    ConversationStorageBackend,
    WorkspaceConversationStorageMutationCoordinator,
    compose_filesystem_conversation_archive,
)
from story_writing_agents.conversation_storage.migration import (
    CONVERSATION_STORAGE_MIGRATION_CHECKPOINT_DIRECTORY,
)
from story_writing_agents.conversation_storage.sqlite import (
    compose_sqlite_conversation_archive,
)

from tests.conversation_archive_fixture import (
    archive_conversation,
    archive_events,
    archive_from_conversations,
    initialized_workspace,
)
from tests.sqlite_conversation_storage_fixture import provision


@dataclass(frozen=True, slots=True)
class NamespaceEntry:
    """One no-follow namespace entry used for exact preservation assertions."""

    kind: str
    mode: int
    payload: bytes | str | None


def namespace_snapshot(root: Path) -> dict[str, NamespaceEntry]:
    """Return a recursive deterministic snapshot without following symlinks."""

    snapshot: dict[str, NamespaceEntry] = {}

    def visit(directory: Path) -> None:
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda candidate: candidate.name):
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                metadata = entry.stat(follow_symlinks=False)
                mode = stat.S_IMODE(metadata.st_mode)
                if entry.is_symlink():
                    snapshot[relative] = NamespaceEntry(
                        "symlink", mode, os.readlink(path)
                    )
                elif entry.is_dir(follow_symlinks=False):
                    snapshot[relative] = NamespaceEntry("directory", mode, None)
                    visit(path)
                elif entry.is_file(follow_symlinks=False):
                    snapshot[relative] = NamespaceEntry("file", mode, path.read_bytes())
                else:
                    snapshot[relative] = NamespaceEntry("other", mode, None)

    visit(root)
    return snapshot


def representative_archive() -> ConversationArchive:
    """Return active, archived, empty, reply, runtime-error, Unicode/NUL records."""

    reply_events = archive_events(
        session_id="session_a",
        conversation_id="conversation_reply",
        user_content="Unicode 질문\x00 reply request",
        outcome_content="reply answer 🙂",
    )
    runtime_events = archive_events(
        session_id="session_b",
        conversation_id="conversation_runtime",
        outcome_kind=ConversationEventKind.RUNTIME_ERROR,
        user_content="runtime request",
        outcome_content="private runtime failure sentinel",
    )
    return archive_from_conversations(
        archive_conversation(
            session_id="session_a",
            conversation_id="conversation_empty",
            subject="Empty active history",
            subject_revision=3,
        ),
        archive_conversation(
            session_id="session_a",
            conversation_id="conversation_reply",
            subject="Unicode 대화 🌙",
            subject_revision=4,
            events=reply_events,
        ),
        archive_conversation(
            session_id="session_b",
            conversation_id="conversation_runtime",
            subject="Archived runtime error",
            subject_revision=2,
            status=ConversationStatus.ARCHIVED,
            events=runtime_events,
        ),
    )


def migration_workspace(
    test_case: TestCase,
    *,
    active_backend: ConversationStorageBackend = ConversationStorageBackend.FILESYSTEM,
    sqlite_ready: bool = True,
) -> Workspace:
    """Return an initialized workspace with optional ready SQLite storage."""

    workspace = initialized_workspace(test_case)
    if sqlite_ready:
        provision(workspace)
    if active_backend is ConversationStorageBackend.SQLITE:
        workspace.write_json(
            CONVERSATION_STORAGE_ACTIVATION_STATE_PATH,
            ConversationStorageActivationState(
                ConversationStorageBackend.SQLITE,
                1,
            ).to_dict(),
        )
    return workspace


def archive_adapter(workspace: Workspace, backend: ConversationStorageBackend):
    """Compose one privileged canonical adapter for a ready test workspace."""

    if backend is ConversationStorageBackend.FILESYSTEM:
        return compose_filesystem_conversation_archive(workspace)
    return compose_sqlite_conversation_archive(workspace)


def import_backend(
    workspace: Workspace,
    backend: ConversationStorageBackend,
    payload: ConversationArchive,
) -> None:
    """Import one canonical fixture through a real caller-owned lease."""

    adapter = archive_adapter(workspace, backend)
    coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
    with coordinator.borrow(timeout_seconds=2.0) as lease:
        adapter.import_archive(lease, payload)


def export_backend(
    workspace: Workspace,
    backend: ConversationStorageBackend,
) -> ConversationArchive:
    """Export one backend through a real caller-owned lease."""

    adapter = archive_adapter(workspace, backend)
    coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
    with coordinator.borrow(timeout_seconds=2.0) as lease:
        return adapter.export_archive(lease)


def checkpoint_relative(source: ConversationStorageBackend) -> str:
    """Return the exact checkpoint path for a source backend direction."""

    destination = (
        ConversationStorageBackend.SQLITE
        if source is ConversationStorageBackend.FILESYSTEM
        else ConversationStorageBackend.FILESYSTEM
    )
    return (
        f"{CONVERSATION_STORAGE_MIGRATION_CHECKPOINT_DIRECTORY}/"
        f"{source.value}-to-{destination.value}.json"
    )


def checkpoint_path(workspace: Workspace, source: ConversationStorageBackend) -> Path:
    """Return one fixed checkpoint path without creating it."""

    return workspace.path(checkpoint_relative(source))
