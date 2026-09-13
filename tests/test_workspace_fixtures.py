"""Isolation and trust-tier contracts for accelerated workspace fixtures."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from story_writing_agents import Workspace, WorkspaceFormatError, WorkspaceSecurityError
from story_writing_agents.workspace import open_workspace_root_directory
from story_writing_agents.workspace.initialization import (
    WorkspaceRootInspection,
    WorkspaceRootRole,
    WorkspaceRootState,
    list_session_resources,
    provision_session_resources,
)
from tests import helpers


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=False,
        capture_output=True,
        text=True,
    )


class WorkspaceFixtureContractTests(unittest.TestCase):
    def test_guarded_and_validated_tiers_bind_exact_fresh_ready_shape(self):
        validated = helpers.validated_initialized_workspace(self)
        guarded = helpers.pristine_initialized_workspace(self)
        self.assertNotEqual(validated.root, guarded.root)
        for workspace in (validated, guarded):
            observed = workspace.root.lstat()
            identity = (observed.st_dev, observed.st_ino)
            self.assertEqual(workspace._root_identity, identity)
            self.assertEqual(
                workspace._root_inspection,
                WorkspaceRootInspection(
                    WorkspaceRootState.READY,
                    identity,
                    WorkspaceRootRole.MAIN,
                    None,
                    workspace.root,
                ),
            )
            self.assertIs(workspace._root_role, WorkspaceRootRole.MAIN)
            self.assertIsNone(workspace._session_id)
            self.assertEqual(workspace._main_root, workspace.root)
            self.assertTrue(workspace._ready)
            self.assertFalse(workspace._initialization_failed)
            descriptor = open_workspace_root_directory(workspace)
            self.addCleanup(lambda value=descriptor: __import__("os").close(value))

    def test_real_initializer_tier_remains_real_and_independent(self):
        with tempfile.TemporaryDirectory(prefix="real-workspace-tier-") as temporary:
            root = Path(temporary) / "workspace"
            workspace = helpers.initialize_test_workspace(root)
            canonical_root = Path(os.path.realpath(root.parent)) / root.name
            self.assertEqual(workspace.root, canonical_root)
            self.assertTrue((root / ".git/index").is_file())
            self.assertIs(Workspace(root).require_ready()._root_role, WorkspaceRootRole.MAIN)

    def test_copies_have_independent_git_config_refs_and_application_state(self):
        first = helpers.pristine_initialized_workspace(self)
        second = helpers.pristine_initialized_workspace(self)
        self.assertNotEqual(first.root.lstat().st_ino, second.root.lstat().st_ino)
        self.assertNotEqual(
            (first.root / ".git/index").lstat().st_ino,
            (second.root / ".git/index").lstat().st_ino,
        )

        first.write_text("discussion_logs/only-first.txt", "artifact")
        first.save_session_overrides(
            "same_session", {"language": "EN", "persona": None, "agents": {}}
        )
        system_path = first.path("config/system_defaults.json")
        system = json.loads(system_path.read_text(encoding="utf-8"))
        system["show_message_details"] = True
        system_path.write_text(json.dumps(system, sort_keys=True) + "\n", encoding="utf-8")
        first.path("config/.conversation_storage.lock").write_text("locked", encoding="utf-8")
        first.path(
            "agent_states/orchestrator_state/provider_sessions/.access-claims.lock"
        ).write_text("access", encoding="utf-8")
        database = first.path("discussion_logs/fixture.sqlite3")
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE fixture (value TEXT)")
            connection.execute("INSERT INTO fixture VALUES ('first')")

        self.assertEqual(_git(first.root, "add", "config/system_defaults.json").returncode, 0)
        head = _git(first.root, "rev-parse", "HEAD")
        self.assertEqual(head.returncode, 0, head.stderr)
        self.assertEqual(
            _git(first.root, "update-ref", "refs/test/fixture", head.stdout.strip()).returncode,
            0,
        )
        self.assertEqual(
            _git(first.root, "diff", "--cached", "--name-only").stdout.strip(),
            "config/system_defaults.json",
        )
        self.assertEqual(_git(second.root, "diff", "--cached", "--name-only").stdout, "")
        self.assertNotEqual(
            (first.root / ".git/index").read_bytes(),
            (second.root / ".git/index").read_bytes(),
        )
        self.assertNotEqual(
            _git(second.root, "show-ref", "--verify", "refs/test/fixture").returncode,
            0,
        )
        self.assertFalse(second.path("discussion_logs/only-first.txt").exists())
        self.assertFalse(second.path("config/.conversation_storage.lock").exists())
        self.assertFalse(
            second.path(
                "agent_states/orchestrator_state/provider_sessions/.access-claims.lock"
            ).exists()
        )
        self.assertFalse(second.path("discussion_logs/fixture.sqlite3").exists())
        self.assertFalse(
            second.path(
                "agent_states/orchestrator_state/sessions/same_session.json"
            ).exists()
        )
        self.assertFalse(
            json.loads(second.path("config/system_defaults.json").read_text(encoding="utf-8"))[
                "show_message_details"
            ]
        )

    def test_same_session_ids_create_separate_worktrees_and_refs(self):
        first = helpers.pristine_initialized_workspace(self)
        second = helpers.pristine_initialized_workspace(self)
        self.assertEqual(list_session_resources(first.root).sessions, ())
        self.assertEqual(list_session_resources(second.root).sessions, ())
        first_session = provision_session_resources(first.root, "shared_session")
        self.assertEqual(list_session_resources(second.root).sessions, ())
        second_session = provision_session_resources(second.root, "shared_session")
        self.assertNotEqual(
            first_session.identity.worktree_root,
            second_session.identity.worktree_root,
        )
        self.assertNotEqual(
            first_session.identity.main_root,
            second_session.identity.main_root,
        )
        self.assertTrue(first_session.identity.worktree_root.is_dir())
        self.assertTrue(second_session.identity.worktree_root.is_dir())

    def test_corrupt_copy_and_replaced_root_fail_closed(self):
        workspace = helpers.validated_initialized_workspace(self)
        original_digest = helpers._tree_digest(workspace.root)
        workspace.path("config/workspace.json").write_text("{}\n", encoding="utf-8")
        with self.assertRaises(AssertionError):
            helpers._bind_guarded_ready_workspace(workspace.root, original_digest)
        with self.assertRaises(WorkspaceFormatError):
            Workspace(workspace.root)

        guarded = helpers.pristine_initialized_workspace(self)
        displaced = guarded.root.with_name("displaced-workspace")
        guarded.root.rename(displaced)
        guarded.root.mkdir()
        with self.assertRaises(WorkspaceSecurityError):
            open_workspace_root_directory(guarded)

    def test_template_integrity_cleanup_and_phase_timings_are_observable(self):
        sealed_before = helpers._workspace_template_digest()
        holder = unittest.TestCase()
        before = len(helpers._workspace_fixture_timing_snapshot())
        workspace = helpers.initialized_workspace(holder)
        copied_root = workspace.root
        self.assertTrue(copied_root.is_dir())
        holder.doCleanups()
        self.assertFalse(copied_root.exists())
        sealed_after = helpers._workspace_template_digest()
        self.assertEqual(sealed_before, sealed_after)

        timings = helpers._workspace_fixture_timing_snapshot()[before:]
        phases = {timing.phase for timing in timings}
        self.assertTrue(
            {"integrity_before", "copy", "bind", "integrity_after", "materialize",
             "fixture_mutation", "cleanup"} <= phases
        )
        self.assertTrue(all(timing.seconds >= 0 for timing in timings))
        self.assertTrue(all(timing.trust_tier == "guarded" for timing in timings))
        preparation = next(
            timing.seconds for timing in helpers._workspace_fixture_timing_snapshot()
            if timing.phase == "template_prepare"
        )
        materialization = next(
            timing.seconds for timing in timings if timing.phase == "materialize"
        )
        self.assertLess(materialization, preparation)


if __name__ == "__main__":
    unittest.main()
