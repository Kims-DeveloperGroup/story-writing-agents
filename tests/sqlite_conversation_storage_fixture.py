"""Shared deterministic fixtures for the optional SQLite storage tests."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from unittest import TestCase

from story_writing_agents import (
    ConversationEventAppend,
    ConversationEventKind,
    Workspace,
)
from story_writing_agents.agents import PLOT_ARCHITECT
from story_writing_agents.conversation_storage import (
    ConversationStorageCapability,
    ConversationStorageServices,
    WorkspaceConversationStorageMutationCoordinator,
)
from story_writing_agents.conversation_storage.sqlite import (
    SQLITE_CONVERSATION_STORAGE_DATABASE_PATH,
    compose_sqlite_conversation_storage,
    provision_sqlite_conversation_storage,
)


@dataclass(frozen=True, slots=True)
class NamespaceEntry:
    """One no-follow namespace entry used for preservation assertions."""

    kind: str
    mode: int
    payload: bytes | str | None


def initialized_workspace(test_case: TestCase) -> Workspace:
    """Return one initialized temporary workspace owned by ``test_case``."""

    temporary = tempfile.TemporaryDirectory(prefix="sqlite-storage-test-")
    test_case.addCleanup(temporary.cleanup)
    return Workspace(temporary.name).initialize()


def database_path(workspace: Workspace) -> Path:
    """Return the fixed SQLite database path without creating it."""

    return workspace.path(SQLITE_CONVERSATION_STORAGE_DATABASE_PATH)


def storage_snapshot(workspace: Workspace) -> dict[str, NamespaceEntry]:
    """Snapshot the SQLite namespace without following symbolic links."""

    root = workspace.path("discussion_logs")
    snapshot: dict[str, NamespaceEntry] = {}

    def visit(directory: Path) -> None:
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                metadata = entry.stat(follow_symlinks=False)
                mode = stat.S_IMODE(metadata.st_mode)
                if entry.is_symlink():
                    snapshot[relative] = NamespaceEntry(
                        "symlink",
                        mode,
                        os.readlink(path),
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


def provisioned_services(
    test_case: TestCase,
) -> tuple[Workspace, ConversationStorageServices]:
    """Provision one workspace and return its workspace and service bundle."""

    workspace = initialized_workspace(test_case)
    provision(workspace)
    return workspace, compose_sqlite_conversation_storage(workspace)


def provision(workspace: Workspace) -> None:
    """Provision the fixed database through one real caller-owned lease."""

    coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
    with coordinator.borrow(timeout_seconds=2.0) as lease:
        result = provision_sqlite_conversation_storage(workspace, lease=lease)
    if result.capability is not ConversationStorageCapability.READY:
        raise AssertionError("SQLite test provisioning did not become ready")


@contextmanager
def raw_connection(workspace: Workspace) -> Iterator[sqlite3.Connection]:
    """Open and always close one explicit test-owned raw SQLite connection."""

    connection = sqlite3.connect(database_path(workspace), isolation_level=None)
    try:
        yield connection
    finally:
        connection.close()


def make_append(
    *,
    session_id: str,
    conversation_id: str,
    turn_id: str,
    kind: ConversationEventKind = ConversationEventKind.USER_MESSAGE,
    content: str = "visible content",
) -> ConversationEventAppend:
    """Build one exact valid append request for SQLite tests and workers."""

    if kind is ConversationEventKind.USER_MESSAGE:
        sender, recipient = "user", PLOT_ARCHITECT
    elif kind is ConversationEventKind.AGENT_REPLY:
        sender, recipient = PLOT_ARCHITECT, "user"
    else:
        sender, recipient = "runtime", "user"
    return ConversationEventAppend(
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        sender=sender,
        recipient=recipient,
        kind=kind,
        content=content,
    )


def _worker_append(arguments: argparse.Namespace) -> None:
    print("ready", flush=True)
    if sys.stdin.read(1) != "x":
        raise RuntimeError("append worker did not receive its start token")
    services = compose_sqlite_conversation_storage(arguments.workspace)
    user = services.event_store.append(
        make_append(
            session_id=arguments.session,
            conversation_id=arguments.conversation,
            turn_id=arguments.turn,
            content=f"question-{arguments.turn}",
        )
    )
    reply = services.event_store.append(
        make_append(
            session_id=arguments.session,
            conversation_id=arguments.conversation,
            turn_id=arguments.turn,
            kind=ConversationEventKind.AGENT_REPLY,
            content=f"reply-{arguments.turn}",
        )
    )
    print(json.dumps([user.sequence, reply.sequence]), flush=True)


def _worker_provision(arguments: argparse.Namespace) -> None:
    print("ready", flush=True)
    if sys.stdin.read(1) != "x":
        raise RuntimeError("provision worker did not receive its start token")
    workspace = Workspace(arguments.workspace)
    provision(workspace)
    print("provisioned", flush=True)


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("append", "provision"))
    parser.add_argument("workspace")
    parser.add_argument("session", nargs="?", default="session_a")
    parser.add_argument("conversation", nargs="?", default="conversation_a")
    parser.add_argument("turn", nargs="?", default="turn_a")
    return parser.parse_args()


def main() -> int:
    arguments = _parse_arguments()
    if arguments.mode == "append":
        _worker_append(arguments)
    else:
        _worker_provision(arguments)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
