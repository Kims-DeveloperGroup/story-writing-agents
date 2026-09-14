"""Provider-free smoke contracts after direct story execution retirement."""

from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from story_writing_agents import AgentBackend, Workspace
from story_writing_agents import cli as cli_module
from story_writing_agents import smoke as smoke_module
from story_writing_agents.smoke import run_smoke

from tests.helpers import pristine_initialized_workspace, workspace_git_identity


EXPECTED_CHECKS = [
    "workspace_initialized",
    "directive_persisted",
    "conversation_catalog_persisted",
    "collaboration_history_empty",
    "collaboration_readable_context_empty",
    "collaboration_search_empty",
    "collaboration_page_empty",
    "execution_artifacts_unchanged",
]

EXPLICIT_CHECKS = [
    "workspace_initialized",
    "execution_artifacts_unchanged",
]

EXPECTED_KEYS = {
    "command",
    "status",
    "provider_tested",
    "workspace",
    "session_id",
    "conversation_id",
    "checks",
    "note",
}

FORBIDDEN_REPORT_FIELDS = {
    "backend",
    "debate_id",
    "revision",
    "output",
    "manuscript",
    "provider",
    "fake_provider",
    "agents",
}


def tree_snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    """Capture every regular file byte sequence and timestamp."""

    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def execution_artifacts(workspace: Workspace) -> tuple[str, ...]:
    """Return forbidden provider/debate/transcript/manuscript artifacts."""

    selected: list[str] = []
    for path in workspace.root.rglob("*"):
        if not path.is_file() or path.name == ".gitkeep":
            continue
        relative = path.relative_to(workspace.root)
        parts = relative.parts
        if (
            path.name in {"transcript.jsonl", "collaboration-history.jsonl"}
            or "debates" in parts
            or "provider_sessions" in parts
            or parts[:1] == ("manuscript",)
            or parts[:2]
            in {
                ("agent_states", "plot_architect_state"),
                ("agent_states", "character_custodian_state"),
                ("agent_states", "execution_specialist_state"),
            }
            or parts[:2] == ("debug_logs", "agent_exchanges")
        ):
            selected.append(relative.as_posix())
    return tuple(sorted(selected))


class SmokeHelpAndTemporaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(workspace_git_identity())

    def test_help_is_provider_free_and_has_no_execution_options(self) -> None:
        parser = cli_module.build_parser()
        stderr = io.StringIO()
        stdout = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            parser.parse_args(["smoke", "--help"])
        self.assertEqual(raised.exception.code, 0)
        help_text = stdout.getvalue()
        normalized_help = " ".join(help_text.split())
        self.assertIn("provider-free", help_text)
        self.assertIn("--workspace", help_text)
        self.assertIn(
            "verify an existing workspace without modifying it",
            normalized_help,
        )
        self.assertNotIn("diagnostic artifacts", normalized_help)
        for removed in ("--backend", "--prompt", "--draft", "--output"):
            self.assertNotIn(removed, help_text)

    def test_temporary_smoke_report_is_exact_and_workspace_is_removed(self) -> None:
        with mock.patch.object(
            AgentBackend,
            "bind",
            side_effect=AssertionError("smoke bound a provider"),
        ) as bind:
            report, exit_code = run_smoke()
        self.assertEqual(exit_code, 0)
        self.assertEqual(set(report), EXPECTED_KEYS)
        self.assertEqual(report["command"], "smoke")
        self.assertEqual(report["status"], "passed")
        self.assertIs(report["provider_tested"], False)
        self.assertEqual(report["checks"], EXPECTED_CHECKS)
        self.assertEqual(
            report["note"],
            "No LLM or external provider was called.",
        )
        self.assertEqual(
            set(report["workspace"]),
            {"mode", "path", "retained"},
        )
        self.assertEqual(report["workspace"]["mode"], "temporary")
        self.assertIs(report["workspace"]["retained"], False)
        self.assertFalse(Path(report["workspace"]["path"]).exists())
        self.assertTrue(FORBIDDEN_REPORT_FIELDS.isdisjoint(report))
        bind.assert_not_called()

    def test_real_temporary_cli_is_compact_json_and_provider_free(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "story_writing_agents", "smoke"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(len(completed.stdout.splitlines()), 1)
        report = json.loads(completed.stdout)
        self.assertEqual(report["checks"], EXPECTED_CHECKS)
        self.assertIs(report["provider_tested"], False)


class SmokeExplicitWorkspaceTests(unittest.TestCase):
    """Verify explicit smoke is a strict read-only readiness check."""

    def setUp(self) -> None:
        self.enterContext(workspace_git_identity())
        self.workspace = pristine_initialized_workspace(self)

    def test_explicit_smoke_is_exact_read_only_and_collaborator_free(self) -> None:
        before = tree_snapshot(self.workspace.root)
        with (
            mock.patch.object(smoke_module, "initialize_workspace") as initialize,
            mock.patch.object(smoke_module, "StoryRuntime") as runtime,
            mock.patch.object(smoke_module, "WorkspaceDebugLog") as debug_log,
            mock.patch.object(AgentBackend, "bind") as bind,
        ):
            report, exit_code = run_smoke(self.workspace)
        self.assertEqual(exit_code, 0)
        self.assertEqual(set(report), EXPECTED_KEYS)
        self.assertEqual(report["checks"], EXPLICIT_CHECKS)
        self.assertEqual(
            report["workspace"],
            {"mode": "persistent", "path": str(self.workspace.root), "retained": True},
        )
        self.assertIsNone(report["session_id"])
        self.assertIsNone(report["conversation_id"])
        self.assertEqual(tree_snapshot(self.workspace.root), before)
        initialize.assert_not_called()
        runtime.assert_not_called()
        debug_log.assert_not_called()
        bind.assert_not_called()

    def test_explicit_cli_uses_the_same_read_only_contract(self) -> None:
        before = tree_snapshot(self.workspace.root)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = cli_module.main(
                ["smoke", "--workspace", str(self.workspace.root)]
            )
        self.assertEqual(exit_code, 0)
        report = json.loads(stdout.getvalue())
        self.assertEqual(report["checks"], EXPLICIT_CHECKS)
        self.assertEqual(report["workspace"]["path"], str(self.workspace.root))
        self.assertEqual(tree_snapshot(self.workspace.root), before)

    def test_opaque_antigravity_state_survives_explicit_smoke_unchanged(
        self,
    ) -> None:
        root = self.workspace.root
        provider = (
            root / "agent_states/orchestrator_state/provider_sessions/.antigravity"
        )
        opaque = provider / "sessions/novel/provider/opaque-unreadable"
        opaque.mkdir(parents=True)
        private = opaque / "private.bin"
        private.write_bytes(b"private smoke bytes\n")
        outside = root.parent / "outside-smoke-target"
        outside.write_bytes(b"outside smoke bytes\n")
        nested_link = provider / "sessions/novel/provider/native-link"
        nested_link.symlink_to(outside)
        native_pipe = provider / "provider-native.pipe"
        os.mkfifo(native_pipe)
        opaque.chmod(0)
        self.addCleanup(opaque.chmod, 0o700)

        selected = (provider, opaque, nested_link, native_pipe, outside)

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
        index_before = root.joinpath(".git/index").read_bytes()

        report, exit_code = run_smoke(self.workspace)

        self.assertEqual(exit_code, 0)
        self.assertEqual(report["checks"], EXPLICIT_CHECKS)
        self.assertIs(report["provider_tested"], False)
        self.assertEqual(identities(), before)
        self.assertEqual(root.joinpath(".git/index").read_bytes(), index_before)
        self.assertEqual(os.readlink(nested_link), os.fspath(outside))
        self.assertEqual(outside.read_bytes(), b"outside smoke bytes\n")
        opaque.chmod(0o700)
        self.assertEqual(private.read_bytes(), b"private smoke bytes\n")
        opaque.chmod(0)

    def test_valid_dirty_configuration_survives_smoke_and_fresh_cli_restart(
        self,
    ) -> None:
        defaults = self.workspace.read_json("config/system_defaults.json")
        defaults["default_backend"] = "codex"
        self.workspace.write_json("config/system_defaults.json", defaults)
        root = self.workspace.root
        before = tree_snapshot(root)

        report, exit_code = run_smoke(Workspace(root))

        self.assertEqual(exit_code, 0)
        self.assertEqual(report["checks"], EXPLICIT_CHECKS)
        self.assertEqual(tree_snapshot(root), before)

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            cli_exit = cli_module.main(["smoke", "--workspace", str(root)])
        cli_report = json.loads(stdout.getvalue())

        self.assertEqual(cli_exit, 0)
        self.assertEqual(cli_report["checks"], EXPLICIT_CHECKS)
        self.assertEqual(cli_report["workspace"]["path"], str(root))
        self.assertEqual(tree_snapshot(root), before)


if __name__ == "__main__":
    unittest.main()
