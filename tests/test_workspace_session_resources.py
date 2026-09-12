"""Workspace composition coverage for exact Git session resources."""

from __future__ import annotations

import inspect
import os
import stat
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
    SessionArtifactBinding,
    SessionArtifactBindingError,
    WorkspaceRootRole,
    compose_session_artifact_binding,
    discover_workspace_location,
    inspect_session_resources,
    list_session_resources,
    provision_session_resources,
    require_ready_session_resources,
)
from story_writing_agents.writing_artifacts.repository import WritingArtifactRepository

from tests.test_git_session_resources import GitSessionResourceGitTestCase


class WorkspaceSessionResourceCompositionTests(GitSessionResourceGitTestCase):
    """Prove role-aware discovery and bounded package-internal composition."""

    def test_internal_wrapper_surface_is_typed_and_public_facades_do_not_expand(
        self,
    ) -> None:
        self.assertEqual(initialization_module.__all__, [])
        expected = {
            compose_session_artifact_binding: "(root: 'Path', session_id: 'str') -> 'SessionArtifactBinding'",
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
        self.assertEqual(len(git_facade.__all__), 24)
        self.assertEqual(len(workspace_facade.__all__), 3)
        for name in (
            "SessionArtifactBinding",
            "SessionArtifactBindingError",
            "compose_session_artifact_binding",
        ):
            self.assertFalse(hasattr(workspace_facade, name))
            self.assertFalse(hasattr(story_writing_agents, name))

    def test_artifact_binding_is_lazy_and_exactly_uses_ready_session_root(self) -> None:
        root = self.initialize_workspace("artifact-binding")
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            ready = provision_session_resources(root, "novel")
        source = ready.identity.worktree_root / "core_documents/global_outline.md"
        source.write_text("Session-only outline.\n", encoding="utf-8")
        before = self.repository_snapshot(root)

        with mock.patch.object(
            WritingArtifactRepository,
            "list_paths",
            side_effect=AssertionError("binding construction performed artifact I/O"),
        ):
            binding = compose_session_artifact_binding(root, "novel")

        self.assertIs(type(binding), SessionArtifactBinding)
        self.assertEqual(binding.resources, ready)
        self.assertFalse(hasattr(binding, "repository"))
        with binding.open_ready_resources() as access:
            self.assertEqual(
                access.repository.read_text("core_documents/global_outline.md"),
                "Session-only outline.\n",
            )
        with self.assertRaises(SessionArtifactBindingError):
            _ = access.repository
        self.assertEqual(self.repository_snapshot(root), before)
        self.assertEqual(
            (root / "core_documents/global_outline.md").read_bytes(),
            b"",
        )

    def test_absent_mismatched_and_nonready_bindings_fail_without_fallback(self) -> None:
        root = self.initialize_workspace("artifact-binding-failures")
        before = self.repository_snapshot(root)
        with mock.patch.object(
            initialization_module,
            "WritingArtifactRepository",
            side_effect=AssertionError("repository was constructed for absent resources"),
        ) as repository:
            for session_id in ("absent", "foreign"):
                with self.subTest(session_id=session_id):
                    with self.assertRaisesRegex(
                        SessionArtifactBindingError,
                        "session artifact resources are unavailable",
                    ):
                        compose_session_artifact_binding(root, session_id)
        repository.assert_not_called()
        self.assertEqual(self.repository_snapshot(root), before)

        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            ready = provision_session_resources(root, "novel")
        self.run_git(root, "update-ref", "-d", ready.identity.internal_ref)
        after_loss = self.repository_snapshot(root)
        with self.assertRaises(SessionArtifactBindingError):
            compose_session_artifact_binding(root, "novel")
        self.assertEqual(self.repository_snapshot(root), after_loss)

    def test_moved_session_binding_fails_closed_and_preserves_private_bytes(self) -> None:
        root = self.initialize_workspace("artifact-binding-moved")
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            ready = provision_session_resources(root, "novel")
        moved = self.test_root / "moved-artifact-binding"
        ready.identity.worktree_root.rename(moved)
        private = moved / "private.txt"
        private.write_bytes(b"preserve moved private bytes\n")
        before = self.repository_snapshot(root)

        with self.assertRaises(SessionArtifactBindingError):
            compose_session_artifact_binding(root, "novel")

        self.assertEqual(self.repository_snapshot(root), before)
        self.assertEqual(private.read_bytes(), b"preserve moved private bytes\n")

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

    def test_session_workspace_treats_unknown_provider_tree_as_opaque(
        self,
    ) -> None:
        root = self.initialize_workspace("opaque-session")
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            ready = provision_session_resources(root, "novel")
        session_root = ready.identity.worktree_root
        provider = (
            session_root
            / "agent_states/orchestrator_state/provider_sessions/.antigravity"
        )
        opaque = provider / "sessions/novel/provider/opaque-unreadable"
        opaque.mkdir(parents=True)
        opaque.joinpath("private.bin").write_bytes(b"private provider bytes\n")
        outside = self.test_root / "outside-session-provider"
        outside.write_bytes(b"outside session provider bytes\n")
        nested_link = provider / "sessions/novel/provider/native-link"
        nested_link.symlink_to(outside)
        native_pipe = provider / "provider-native.pipe"
        os.mkfifo(native_pipe)
        untracked = session_root / "private-untracked.txt"
        untracked.write_bytes(b"private untracked session bytes\n")
        story = session_root / "core_documents/global_outline.md"
        original_story = story.read_bytes()
        unstaged_story = original_story + b"\nUnstaged session story change.\n"
        story.write_bytes(unstaged_story)
        opaque.chmod(0)
        self.addCleanup(opaque.chmod, 0o700)

        selected = (
            provider,
            opaque,
            nested_link,
            native_pipe,
            untracked,
            story,
            outside,
        )

        def identities() -> tuple[tuple[int, int, int], ...]:
            return tuple(
                (
                    stat.S_IFMT(value.st_mode) | stat.S_IMODE(value.st_mode),
                    value.st_size,
                    value.st_mtime_ns,
                )
                for value in (path.lstat() for path in selected)
            )

        before = identities()
        status = self.run_git(
            session_root,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ).stdout
        self.assertIn(b"private-untracked.txt", status)
        self.assertIn(b"core_documents/global_outline.md", status)
        self.assertNotIn(b".antigravity", status)
        with (
            mock.patch.object(
                initialization_module,
                "_root_names",
                side_effect=AssertionError("ready session root was enumerated"),
            ),
            mock.patch.object(
                initialization_module,
                "_snapshot_relative_files",
                side_effect=AssertionError("ready session tree was enumerated"),
            ),
        ):
            reopened = Workspace(session_root).require_ready()

        self.assertEqual(reopened.root, session_root)
        self.assertEqual(identities(), before)
        self.assertEqual(story.read_bytes(), unstaged_story)
        self.assertEqual(os.readlink(nested_link), os.fspath(outside))
        self.assertEqual(outside.read_bytes(), b"outside session provider bytes\n")

    def test_session_workspace_staged_unknown_is_recovery_without_mutation(
        self,
    ) -> None:
        root = self.initialize_workspace("session-staged-unknown")
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            ready = provision_session_resources(root, "novel")
        session_root = ready.identity.worktree_root
        unknown = session_root / "staged-private.txt"
        unknown.write_bytes(b"staged private session bytes\n")
        self.run_git(session_root, "add", "--", unknown.name)

        def session_snapshot() -> tuple[bytes, bytes, bytes]:
            return (
                self.run_git(
                    session_root,
                    "status",
                    "--porcelain=v2",
                    "-z",
                    "--untracked-files=all",
                ).stdout,
                self.run_git(session_root, "ls-files", "--stage", "-z").stdout,
                unknown.read_bytes(),
            )

        before_main = self.repository_snapshot(root)
        before_session = session_snapshot()
        with self.assertRaises(WorkspaceFormatError) as caught:
            Workspace(session_root)

        self.assertIn("recovery", str(caught.exception))
        self.assertNotIn("staged private session bytes", str(caught.exception))
        self.assertEqual(self.repository_snapshot(root), before_main)
        self.assertEqual(session_snapshot(), before_session)

    def test_session_workspace_missing_required_configuration_is_recovery(
        self,
    ) -> None:
        root = self.initialize_workspace("session-required-path")
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            ready = provision_session_resources(root, "novel")
        session_root = ready.identity.worktree_root
        required = session_root / "config/agent_configs.json"
        required.unlink()
        before = self.repository_snapshot(root)

        with self.assertRaises(WorkspaceFormatError):
            Workspace(session_root)

        self.assertEqual(self.repository_snapshot(root), before)
        self.assertFalse(required.exists())

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
