"""Focused workspace composition coverage for session-history snapshots."""

from __future__ import annotations

import inspect
import json
import os
import sys
import unittest
from unittest import mock

import story_writing_agents
import story_writing_agents.git as git_facade
import story_writing_agents.workspace as workspace_facade
from story_writing_agents import AgentBackend, SessionAccessState, SessionDurableState, Workspace
from story_writing_agents.git.session_history_contracts import (
    GIT_SESSION_HISTORY_DIRECTORY,
    GitSessionHistorySnapshot,
)
from story_writing_agents.workspace import initialization as initialization_module
from story_writing_agents.workspace.initialization import (
    compose_session_checkpoint_service,
    compose_story_commit_service,
    inspect_session_history,
    provision_session_resources,
    require_ready_session_resources,
)
from story_writing_agents.session_recovery.transition_assessment import (
    SessionTransitionAssessmentState,
    SessionTransitionNextAction,
    SessionTransitionOperation,
    SessionTransitionScope,
)

from tests.test_git_session_resources import GitSessionResourceGitTestCase
from tests.test_session_recovery_contracts import _has_cycle, _module_graph


class WorkspaceSessionHistoryCompositionTests(GitSessionResourceGitTestCase):
    def test_valid_dirty_settings_flow_through_real_idle_session_services(self) -> None:
        root = self.initialize_workspace("workspace-dirty-settings-lifecycle")
        defaults_path = root / "config/system_defaults.json"
        defaults = json.loads(defaults_path.read_text(encoding="utf-8"))
        defaults["default_backend"] = "codex"
        defaults_path.write_text(
            json.dumps(defaults, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        workspace = Workspace(root)
        backend = AgentBackend(workspace, executable=sys.executable)
        self.addCleanup(backend.close)
        ready = backend.provision_session_resources("novel")
        lifecycle = backend.create_session_with_access("novel")

        self.assertIs(lifecycle.durable_state, SessionDurableState.READY)
        self.assertIs(lifecycle.access_state, SessionAccessState.HELD_HERE)
        self.assertEqual(require_ready_session_resources(root, "novel"), ready)
        history = inspect_session_history(root, "novel")
        self.assertEqual(
            (history.candidates, history.commit_operations, history.checkpoint_operations),
            ((), (), ()),
        )
        assessment = backend.assess_session_transition(
            lifecycle_state=lifecycle,
            requested_operation=SessionTransitionOperation.ENTER_DESTINATION,
        )
        self.assertEqual(
            (assessment.scope, assessment.state, assessment.next_action),
            (
                SessionTransitionScope.IDLE,
                SessionTransitionAssessmentState.IDLE_READY,
                SessionTransitionNextAction.BEGIN_WITH_LEAD,
            ),
        )
        self.assertIsNotNone(compose_story_commit_service(root, "novel"))
        self.assertIsNotNone(compose_session_checkpoint_service(root, "novel"))
        self.assertEqual(inspect_session_history(root, "novel"), history)

    def test_internal_wrapper_uses_main_root_manifest_and_noncreating_snapshot(self) -> None:
        self.assertEqual(
            str(inspect.signature(inspect_session_history)),
            "(root: 'Path', session_id: 'str') -> 'GitSessionHistorySnapshot'",
        )
        self.assertEqual(initialization_module.__all__, [])
        for facade in (workspace_facade, git_facade, story_writing_agents):
            self.assertFalse(hasattr(facade, "inspect_session_history"))
            self.assertFalse(hasattr(facade, "GitSessionHistorySnapshot"))

        root = self.initialize_workspace("workspace-history-snapshot")
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            ready = provision_session_resources(root, "novel")
        history_root = root / ".git" / GIT_SESSION_HISTORY_DIRECTORY / "novel"
        repository_before = self.repository_snapshot(root)
        main_snapshot = inspect_session_history(root, "novel")
        session_snapshot = inspect_session_history(ready.identity.worktree_root, "novel")
        self.assertEqual(main_snapshot, session_snapshot)
        self.assertEqual(
            main_snapshot,
            GitSessionHistorySnapshot(main_snapshot.inspection, (), (), ()),
        )
        self.assertFalse(history_root.exists())
        self.assertEqual(self.repository_snapshot(root), repository_before)
        modules, edges = _module_graph()
        self.assertEqual((len(modules), len(edges)), (142, 567))
        self.assertFalse(_has_cycle(modules, edges))
        self.assertIn(("workspace.initialization", "git.session_history"), edges)


if __name__ == "__main__":
    unittest.main()
