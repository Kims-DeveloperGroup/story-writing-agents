"""Provider-free smoke contracts after direct story execution retirement."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from story_writing_agents import AgentBackend, Workspace
from story_writing_agents import cli as cli_module
from story_writing_agents import smoke as smoke_module
from story_writing_agents.conversations import ConversationStatus
from story_writing_agents.smoke import run_smoke
from story_writing_agents.workspace import FilesystemConversationCatalog

from tests.helpers import initialized_workspace


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
        self.assertIn("provider-free", help_text)
        self.assertIn("--workspace", help_text)
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


class SmokePersistentTests(unittest.TestCase):
    def test_explicit_workspace_keeps_only_directive_and_catalog_management_state(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        core_before = {
            path.name: path.read_bytes()
            for path in workspace.path("core_documents").iterdir()
            if path.is_file()
        }
        report, exit_code = run_smoke(workspace)
        self.assertEqual(exit_code, 0)
        self.assertEqual(set(report), EXPECTED_KEYS)
        self.assertEqual(report["checks"], EXPECTED_CHECKS)
        self.assertEqual(
            report["workspace"],
            {
                "mode": "persistent",
                "path": str(workspace.root),
                "retained": True,
            },
        )
        session_id = report["session_id"]
        conversation_id = report["conversation_id"]
        self.assertEqual(
            workspace.load_session_overrides(session_id),
            {"agents": {}, "language": "EN", "persona": None},
        )
        metadata = FilesystemConversationCatalog(workspace).get(
            session_id,
            conversation_id,
        )
        self.assertEqual(metadata.status, ConversationStatus.ACTIVE)
        self.assertEqual(metadata.subject, "Smoke collaboration contract")
        self.assertEqual(
            {
                path.name: path.read_bytes()
                for path in workspace.path("core_documents").iterdir()
                if path.is_file()
            },
            core_before,
        )
        self.assertEqual(execution_artifacts(workspace), ())
        self.assertTrue(FORBIDDEN_REPORT_FIELDS.isdisjoint(report))

    def test_named_invariant_failure_is_schema_stable_and_content_free(self) -> None:
        workspace = initialized_workspace(self)
        with mock.patch.object(
            smoke_module,
            "_verify_catalog",
            side_effect=smoke_module._SmokeInvariantError(
                "conversation_catalog_persisted"
            ),
        ):
            report, exit_code = run_smoke(workspace)
        self.assertEqual(exit_code, 1)
        self.assertEqual(set(report), EXPECTED_KEYS | {"failed_check"})
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["failed_check"], "conversation_catalog_persisted")
        self.assertEqual(
            report["checks"],
            EXPECTED_CHECKS[:2],
        )
        self.assertIsNone(report["conversation_id"])
        self.assertIs(report["provider_tested"], False)
        self.assertEqual(execution_artifacts(workspace), ())

    def test_explicit_cli_uses_requested_workspace_and_same_schema(self) -> None:
        workspace = initialized_workspace(self)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = cli_module.main(["smoke", "--workspace", str(workspace.root)])
        self.assertEqual(exit_code, 0)
        report = json.loads(stdout.getvalue())
        self.assertEqual(set(report), EXPECTED_KEYS)
        self.assertEqual(report["workspace"]["path"], str(workspace.root))
        self.assertIs(report["workspace"]["retained"], True)
        self.assertEqual(execution_artifacts(workspace), ())

    def test_diagnostic_creation_trace_and_error_failures_are_inert(self) -> None:
        workspace = initialized_workspace(self)
        for collaborator in ("constructor", "trace"):
            with self.subTest(collaborator=collaborator):
                if collaborator == "constructor":
                    patch = mock.patch.object(
                        smoke_module,
                        "WorkspaceDebugLog",
                        side_effect=RuntimeError("diagnostic secret"),
                    )
                else:
                    patch = mock.patch.object(
                        smoke_module.WorkspaceDebugLog,
                        "trace",
                        side_effect=RuntimeError("diagnostic secret"),
                    )
                with patch:
                    report, exit_code = run_smoke(workspace)
                self.assertEqual(exit_code, 0)
                self.assertEqual(report["status"], "passed")
                self.assertEqual(report["checks"], EXPECTED_CHECKS)

        with (
            mock.patch.object(
                smoke_module,
                "_verify_catalog",
                side_effect=smoke_module._SmokeInvariantError(
                    "conversation_catalog_persisted"
                ),
            ),
            mock.patch.object(
                smoke_module.WorkspaceDebugLog,
                "error",
                side_effect=KeyboardInterrupt("diagnostic control"),
            ),
        ):
            report, exit_code = run_smoke(workspace)
        self.assertEqual(exit_code, 1)
        self.assertEqual(report["failed_check"], "conversation_catalog_persisted")

    def test_legacy_workspace_without_debug_logs_remains_ready(self) -> None:
        workspace = initialized_workspace(self)
        debug_logs = workspace.path("debug_logs")
        shutil.rmtree(debug_logs)
        report, exit_code = run_smoke(workspace)
        self.assertEqual(exit_code, 0)
        self.assertEqual(report["checks"], EXPECTED_CHECKS)
        self.assertEqual(execution_artifacts(workspace), ())

    def test_two_persistent_runs_use_unique_catalog_identities_without_execution(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        first, first_code = run_smoke(workspace)
        second, second_code = run_smoke(workspace)
        self.assertEqual((first_code, second_code), (0, 0))
        self.assertNotEqual(first["session_id"], second["session_id"])
        self.assertNotEqual(first["conversation_id"], second["conversation_id"])
        catalog = FilesystemConversationCatalog(workspace)
        for report in (first, second):
            metadata = catalog.get(
                report["session_id"],
                report["conversation_id"],
            )
            self.assertEqual(metadata.subject, "Smoke collaboration contract")
        self.assertEqual(execution_artifacts(workspace), ())

    def test_every_check_name_can_fail_as_the_exact_first_transferable_fact(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        direct_verifiers = {
            "workspace_initialized": "_verify_workspace_initialized",
            "directive_persisted": "_verify_directive",
            "conversation_catalog_persisted": "_verify_catalog",
        }
        for index, check in enumerate(EXPECTED_CHECKS):
            with self.subTest(check=check):
                if check in direct_verifiers:
                    patch = mock.patch.object(
                        smoke_module,
                        direct_verifiers[check],
                        side_effect=smoke_module._SmokeInvariantError(check),
                    )
                else:
                    real_require = smoke_module._require

                    def fail_named(
                        condition: bool,
                        failed_check: str,
                        *,
                        target: str = check,
                    ) -> None:
                        if failed_check == target:
                            raise smoke_module._SmokeInvariantError(target)
                        real_require(condition, failed_check)

                    patch = mock.patch.object(
                        smoke_module,
                        "_require",
                        side_effect=fail_named,
                    )
                with patch:
                    report, exit_code = run_smoke(workspace)
                self.assertEqual(exit_code, 1)
                self.assertEqual(report["failed_check"], check)
                self.assertEqual(report["checks"], EXPECTED_CHECKS[:index])
                self.assertIs(report["provider_tested"], False)
                self.assertEqual(execution_artifacts(workspace), ())

    def test_supplied_workspace_is_borrowed_and_ordinary_failure_is_identical(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        failure = OSError("private smoke storage detail")
        with mock.patch.object(
            smoke_module,
            "_verify_catalog",
            side_effect=failure,
        ):
            with self.assertRaises(OSError) as raised:
                run_smoke(workspace)
        self.assertIs(raised.exception, failure)
        self.assertTrue(workspace.root.exists())
        self.assertEqual(execution_artifacts(workspace), ())

    def test_process_controls_preserve_identity_and_temporary_cleanup(self) -> None:
        for failure in (KeyboardInterrupt("stop"), SystemExit(19)):
            with self.subTest(failure=type(failure).__name__):
                with mock.patch.object(
                    smoke_module,
                    "_verify_workspace_initialized",
                    side_effect=failure,
                ):
                    with self.assertRaises(type(failure)) as raised:
                        run_smoke()
                self.assertIs(raised.exception, failure)

        with mock.patch.object(
            smoke_module,
            "_verify_workspace_initialized",
            side_effect=smoke_module._SmokeInvariantError("workspace_initialized"),
        ):
            report, exit_code = run_smoke()
        self.assertEqual(exit_code, 1)
        self.assertFalse(Path(report["workspace"]["path"]).exists())


if __name__ == "__main__":
    unittest.main()
