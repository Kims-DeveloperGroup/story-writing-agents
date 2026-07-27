"""Public import, durable inspection, and command-line behavior tests."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

import story_writing_agents
from story_writing_agents import (
    AGENT_SPECS,
    PLOT_ARCHITECT,
    Runtime,
    RuntimeSession,
    Session,
    StoryRuntime,
    Workspace,
)
from story_writing_agents.cli import main
from story_writing_agents.errors import WorkspaceFormatError
from story_writing_agents.workspace import DEFAULT_AGENT_PROMPTS

from tests.helpers import initialized_workspace


class PackagingTests(unittest.TestCase):
    def test_console_script_aliases_map_to_the_same_exact_main_function(
        self,
    ) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with (repository_root / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)["project"]

        scripts = project["scripts"]
        expected_target = "story_writing_agents.cli:main"
        self.assertIn("story-writing-agents", scripts)
        self.assertIn("story", scripts)
        self.assertEqual(scripts["story-writing-agents"], expected_target)
        self.assertEqual(scripts["story"], expected_target)
        self.assertEqual(
            scripts["story"],
            scripts["story-writing-agents"],
        )


class PublicApiTests(unittest.TestCase):
    def test_all_declared_public_imports_exist_and_runtime_aliases_are_stable(
        self,
    ) -> None:
        for name in story_writing_agents.__all__:
            with self.subTest(name=name):
                self.assertTrue(hasattr(story_writing_agents, name))
        self.assertIs(Runtime, StoryRuntime)
        self.assertIs(Session, RuntimeSession)

    def test_top_level_exports_include_models_directives_and_public_errors(
        self,
    ) -> None:
        required = {
            "AgentBackend",
            "AgentDefinition",
            "AgentReply",
            "AgentRequest",
            "AgentStateEntry",
            "BoundAgent",
            "DirectiveKind",
            "PromptFileReference",
            "StoryWritingAgentsError",
            "WorkspaceSecurityError",
            "WorkspaceFormatError",
            "ConfigurationError",
            "DirectiveError",
            "SessionIdError",
            "ManuscriptExistsError",
            "BackendProtocolError",
            "DebateExistsError",
        }
        self.assertTrue(required.issubset(set(story_writing_agents.__all__)))
        for name in required:
            with self.subTest(name=name):
                self.assertIsNotNone(getattr(story_writing_agents, name))
        base_error = story_writing_agents.StoryWritingAgentsError
        for name in required.difference(
            {
                "AgentBackend",
                "AgentDefinition",
                "AgentReply",
                "AgentRequest",
                "AgentStateEntry",
                "BoundAgent",
                "DirectiveKind",
                "PromptFileReference",
                "StoryWritingAgentsError",
            }
        ):
            with self.subTest(error=name):
                self.assertTrue(
                    issubclass(getattr(story_writing_agents, name), base_error)
                )


class PersistenceValidationTests(unittest.TestCase):
    def test_persisted_debate_log_rejects_wrong_field_types(self) -> None:
        workspace = initialized_workspace(self)
        workspace.write_text(
            "discussion_logs/session_a/debate_a.jsonl",
            json.dumps(
                {
                    "sender": "plot_architect",
                    "phase": "audit",
                    "round": "not-an-integer",
                    "revision": "r0",
                    "kind": "approval",
                    "content": "neutral",
                    "timestamp": "2026-01-01T00:00:00+00:00",
                }
            )
            + "\n",
        )
        with self.assertRaisesRegex(WorkspaceFormatError, "line 1"):
            workspace.read_debate_messages("session_a", "debate_a")


class CliTests(unittest.TestCase):
    def test_module_help_lists_public_commands(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        existing_pythonpath = environment.get("PYTHONPATH")
        source_path = str(repository_root / "src")
        environment["PYTHONPATH"] = (
            source_path
            if not existing_pythonpath
            else os.pathsep.join((source_path, existing_pythonpath))
        )
        completed = subprocess.run(
            [sys.executable, "-m", "story_writing_agents", "--help"],
            cwd=repository_root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("story-writing-agents", completed.stdout)
        for command in ("init", "directive", "run", "inspect", "session", "smoke"):
            with self.subTest(command=command):
                self.assertIn(command, completed.stdout)

    def test_init_inspect_and_expected_error_exit_codes(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace_path = Path(temporary.name) / "workspace"

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["init", str(workspace_path)])
        self.assertEqual(exit_code, 0)
        initialized = json.loads(stdout.getvalue())
        self.assertEqual(Path(initialized["workspace"]), workspace_path.resolve())
        self.assertTrue((workspace_path / "config" / "system_defaults.json").is_file())

        workspace = Workspace(workspace_path)
        prompt_path = AGENT_SPECS[PLOT_ARCHITECT].system_prompt_file
        workspace.write_text(prompt_path, "custom prompt\n")
        workspace.write_text(
            "core_documents/global_outline.md",
            "preserved outline\n",
        )
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(
                [
                    "init",
                    str(workspace_path),
                    "--overwrite-prompts",
                ]
            )
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            workspace.read_text(prompt_path),
            DEFAULT_AGENT_PROMPTS[PLOT_ARCHITECT],
        )
        self.assertEqual(
            workspace.read_text("core_documents/global_outline.md"),
            "preserved outline\n",
        )

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(
                [
                    "inspect",
                    "--workspace",
                    str(workspace_path),
                    "--session",
                    "cli_session",
                ]
            )
        self.assertEqual(exit_code, 0)
        inspected = json.loads(stdout.getvalue())
        self.assertEqual(inspected["session_id"], "cli_session")
        self.assertEqual(inspected["debates"], [])

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = main(
                [
                    "directive",
                    "--workspace",
                    str(workspace_path),
                    "--session",
                    "cli_session",
                    "embedded [SYS: LANG=EN]",
                ]
            )
        self.assertEqual(exit_code, 2)
        self.assertIn("DirectiveError", stderr.getvalue())

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = main(
                [
                    "run",
                    "--workspace",
                    str(workspace_path),
                    "--session",
                    "cli_session",
                    "--backend",
                    "unsafe backend spec",
                    "--draft",
                    "neutral draft",
                ]
            )
        self.assertEqual(exit_code, 2)
        self.assertIn("ConfigurationError", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
