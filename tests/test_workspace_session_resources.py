"""Workspace composition coverage for exact Git session resources."""

from __future__ import annotations

import inspect
import os
import unittest
from unittest import mock

import story_writing_agents
import story_writing_agents.git as git_facade
import story_writing_agents.workspace as workspace_facade
from story_writing_agents import Workspace
from story_writing_agents.errors import WorkspaceFormatError
from story_writing_agents.git.session_contracts import GitSessionResourceState
from story_writing_agents.workspace import initialization as initialization_module
from story_writing_agents.workspace.core import discover_workspace
from story_writing_agents.workspace.initialization import (
    WorkspaceRootRole,
    discover_workspace_location,
    inspect_session_resources,
    list_session_resources,
    provision_session_resources,
    require_ready_session_resources,
)

from tests.test_git_session_resources import GitSessionResourceGitTestCase


class WorkspaceSessionResourceCompositionTests(GitSessionResourceGitTestCase):
    """Prove role-aware discovery and bounded package-internal composition."""

    def test_internal_wrapper_surface_is_typed_and_public_facades_do_not_expand(
        self,
    ) -> None:
        self.assertEqual(initialization_module.__all__, [])
        expected = {
            inspect_session_resources: "(root: 'Path', session_id: 'str') -> 'GitSessionResourceInspection'",
            list_session_resources: "(root: 'Path') -> 'GitSessionResourceInventory'",
            provision_session_resources: "(root: 'Path', session_id: 'str') -> 'GitSessionResourceInspection'",
            require_ready_session_resources: "(root: 'Path', session_id: 'str') -> 'GitSessionResourceInspection'",
        }
        for operation, signature in expected.items():
            with self.subTest(operation=operation.__name__):
                self.assertEqual(str(inspect.signature(operation)), signature)
                self.assertFalse(hasattr(workspace_facade, operation.__name__))
                self.assertFalse(hasattr(story_writing_agents, operation.__name__))
        self.assertEqual(len(story_writing_agents.__all__), 104)
        self.assertEqual(len(git_facade.__all__), 22)
        self.assertEqual(len(workspace_facade.__all__), 3)

    def test_main_and_session_discovery_return_nearest_typed_identity(self) -> None:
        root = self.initialize_workspace("discovery")
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            ready = provision_session_resources(root, "novel")
        session_root = ready.identity.worktree_root
        main_nested = root / "core_documents"
        session_nested = session_root / "core_documents"

        main = discover_workspace_location(main_nested)
        session = discover_workspace_location(session_nested)
        assert main is not None and session is not None
        self.assertEqual(main.root, root)
        self.assertIs(main.role, WorkspaceRootRole.MAIN)
        self.assertIsNone(main.session_id)
        self.assertEqual(main.main_root, root)
        self.assertEqual(session.root, session_root)
        self.assertIs(session.role, WorkspaceRootRole.SESSION)
        self.assertEqual(session.session_id, "novel")
        self.assertEqual(session.main_root, root)

        main_workspace = discover_workspace(main_nested)
        session_workspace = discover_workspace(session_nested)
        assert main_workspace is not None and session_workspace is not None
        self.assertEqual(main_workspace.root, root)
        self.assertIs(main_workspace._root_role, WorkspaceRootRole.MAIN)  # noqa: SLF001
        self.assertEqual(session_workspace.root, session_root)
        self.assertIs(session_workspace._root_role, WorkspaceRootRole.SESSION)  # noqa: SLF001
        self.assertEqual(session_workspace._session_id, "novel")  # noqa: SLF001
        self.assertEqual(session_workspace._main_root, root)  # noqa: SLF001

    def test_exact_session_workspace_activates_without_relaxing_linked_worktrees(
        self,
    ) -> None:
        root = self.initialize_workspace("activation")
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            ready = provision_session_resources(root, "novel")
        session = Workspace(ready.identity.worktree_root).require_ready()
        self.assertEqual(
            session.read_text("config/workspace.json"),
            Workspace(root).read_text("config/workspace.json"),
        )
        self.assertIs(
            require_ready_session_resources(session.root, "novel").state,
            GitSessionResourceState.READY,
        )

        unrelated = self.test_root / "unrelated"
        self.run_git(
            root,
            "worktree",
            "add",
            "--detach",
            os.fspath(unrelated),
            "HEAD",
        )
        before = self.repository_snapshot(root)
        with self.assertRaises(WorkspaceFormatError):
            Workspace(unrelated)
        self.assertEqual(self.repository_snapshot(root), before)

    def test_moved_session_worktree_is_recovery_required_and_never_cleaned(
        self,
    ) -> None:
        root = self.initialize_workspace("moved")
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            ready = provision_session_resources(root, "novel")
        original = ready.identity.worktree_root
        moved = self.test_root / "preserved-moved-session"
        original.rename(moved)
        private = moved / "private.txt"
        private.write_bytes(b"preserve moved bytes\n")
        before = self.repository_snapshot(root)

        inspected = inspect_session_resources(root, "novel")
        self.assertIs(inspected.state, GitSessionResourceState.RECOVERY_REQUIRED)
        with self.assertRaises(WorkspaceFormatError):
            Workspace(root)
        self.assertEqual(self.repository_snapshot(root), before)
        self.assertEqual(private.read_bytes(), b"preserve moved bytes\n")
        self.assertFalse(original.exists())

    def test_multiple_exact_sessions_are_sorted_and_main_remains_ready(self) -> None:
        root = self.initialize_workspace("multiple")
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            provision_session_resources(root, "zeta")
            provision_session_resources(root, "alpha")
        inventory = list_session_resources(root)
        self.assertEqual(
            tuple(value.identity.session_id for value in inventory.sessions),
            ("alpha", "zeta"),
        )
        self.assertTrue(
            all(
                value.state is GitSessionResourceState.READY
                for value in inventory.sessions
            )
        )
        self.assertEqual(Workspace(root).require_ready().root, root)


if __name__ == "__main__":
    unittest.main()
