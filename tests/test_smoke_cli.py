"""Built-in provider-free smoke command and CLI contract tests."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

from story_writing_agents import (
    AgentReply,
    DebateStatus,
    Phase,
    PLOT_ARCHITECT,
    StoryRuntime,
    Workspace,
    initialize_workspace,
)
from story_writing_agents import cli as cli_module
from story_writing_agents import smoke as smoke_module
from story_writing_agents.agents import CORE_DOCUMENTS, ROSTER, SPECIALIST_IDS

from tests.helpers import ScriptedBackend, all_manuscripts


EXPECTED_CHECKS = [
    "workspace_initialized",
    "directive_persisted",
    "revision_workflow",
    "consensus_persisted",
    "manuscript_committed",
    "core_documents_unchanged",
]


def invoke_cli(arguments: list[str]) -> tuple[int, str, str]:
    """Invoke the CLI in-process and return code, stdout, and stderr."""

    stdout = io.StringIO()
    stderr = io.StringIO()
    with (
        contextlib.redirect_stdout(stdout),
        contextlib.redirect_stderr(stderr),
    ):
        exit_code = cli_module.main(arguments)
    return exit_code, stdout.getvalue(), stderr.getvalue()


class SmokeHelpTests(unittest.TestCase):
    def test_smoke_help_explains_provider_free_and_temporary_default(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            cli_module.main(["smoke", "--help"])

        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(stderr.getvalue(), "")
        help_text = stdout.getvalue().lower()
        self.assertIn("provider-free", help_text)
        self.assertIn("temporary", help_text)
        self.assertIn("--workspace", help_text)

    def test_smoke_rejects_backend_argument_with_argparse_code_two(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            cli_module.main(
                ["smoke", "--backend", "provider.module:Backend"]
            )

        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("unrecognized arguments: --backend", stderr.getvalue())


class SmokeTemporaryTests(unittest.TestCase):
    def test_zero_argument_smoke_is_compact_provider_free_and_temporary(
        self,
    ) -> None:
        with mock.patch.object(
            cli_module,
            "AgentBackend",
            side_effect=AssertionError("smoke must not construct a provider"),
        ) as agent_backend:
            exit_code, stdout, stderr = invoke_cli(["smoke"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        agent_backend.assert_not_called()
        self.assertEqual(len(stdout.splitlines()), 1)
        report = json.loads(stdout)
        self.assertEqual(
            stdout,
            json.dumps(
                report,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n",
        )
        self.assertEqual(
            set(report),
            {
                "checks",
                "command",
                "debate_id",
                "debate_status",
                "note",
                "provider_tested",
                "revision_id",
                "rounds_completed",
                "session_id",
                "status",
                "workspace",
            },
        )
        self.assertEqual(report["command"], "smoke")
        self.assertEqual(report["status"], "passed")
        self.assertIs(report["provider_tested"], False)
        self.assertEqual(report["checks"], EXPECTED_CHECKS)
        self.assertEqual(report["debate_status"], "consensus")
        self.assertEqual(report["revision_id"], "r1")
        self.assertEqual(report["rounds_completed"], 1)
        self.assertIn("No LLM or external provider", report["note"])
        self.assertEqual(report["workspace"]["mode"], "temporary")
        self.assertIs(report["workspace"]["retained"], False)
        self.assertFalse(Path(report["workspace"]["path"]).exists())


class SmokePersistentTests(unittest.TestCase):
    def test_persistent_smoke_retains_consensus_artifacts_and_preserves_core(
        self,
    ) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = initialize_workspace(Path(temporary.name) / "workspace")
        for index, filename in enumerate(CORE_DOCUMENTS):
            workspace.write_text(
                f"core_documents/{filename}",
                "" if index == 0 else f"neutral-core-{index}\n",
            )
        core_before = {
            filename: workspace.path(
                f"core_documents/{filename}"
            ).read_bytes()
            for filename in CORE_DOCUMENTS
        }
        self.assertEqual(core_before[CORE_DOCUMENTS[0]], b"")

        exit_code, stdout, stderr = invoke_cli(
            ["smoke", "--workspace", str(workspace.root)]
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        report = json.loads(stdout)
        self.assertEqual(report["workspace"]["mode"], "persistent")
        self.assertIs(report["workspace"]["retained"], True)
        self.assertEqual(
            Path(report["workspace"]["path"]),
            workspace.root,
        )
        self.assertTrue(workspace.root.exists())

        inspected = StoryRuntime(Workspace(workspace.root)).inspect_debate(
            report["session_id"],
            report["debate_id"],
        )
        self.assertEqual(inspected["status"], "consensus")
        self.assertTrue(inspected["terminal"])
        self.assertEqual(inspected["result"]["status"], "consensus")
        self.assertEqual(inspected["result"]["revision_id"], "r1")
        self.assertEqual(inspected["result"]["rounds_completed"], 1)
        self.assertTrue(inspected["messages"])
        manuscript_path = inspected["result"]["manuscript_path"]
        self.assertIsNotNone(manuscript_path)
        assert manuscript_path is not None
        self.assertTrue(workspace.path(manuscript_path).is_file())
        self.assertEqual(
            workspace.read_text(manuscript_path),
            smoke_module._REVISION_ONE,
        )
        core_after = {
            filename: workspace.path(
                f"core_documents/{filename}"
            ).read_bytes()
            for filename in CORE_DOCUMENTS
        }
        self.assertEqual(core_after, core_before)

    def test_two_persistent_runs_create_unique_session_debate_and_outputs(
        self,
    ) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = initialize_workspace(Path(temporary.name) / "workspace")
        reports = []

        for _ in range(2):
            exit_code, stdout, stderr = invoke_cli(
                ["smoke", "--workspace", str(workspace.root)]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(stderr, "")
            reports.append(json.loads(stdout))

        self.assertEqual(len({item["session_id"] for item in reports}), 2)
        self.assertEqual(len({item["debate_id"] for item in reports}), 2)
        manuscript_paths = []
        runtime = StoryRuntime(Workspace(workspace.root))
        for report in reports:
            inspected = runtime.inspect_debate(
                report["session_id"],
                report["debate_id"],
            )
            self.assertEqual(inspected["status"], "consensus")
            manuscript_paths.append(
                inspected["result"]["manuscript_path"]
            )
        self.assertEqual(len(set(manuscript_paths)), 2)
        self.assertEqual(
            {path.name for path in all_manuscripts(workspace)},
            {Path(path).name for path in manuscript_paths},
        )

    def test_successful_revision_check_enforces_exact_internal_phase_counts(
        self,
    ) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace_path = Path(temporary.name) / "workspace"

        with mock.patch.object(
            smoke_module,
            "_verify_revision_workflow",
            wraps=smoke_module._verify_revision_workflow,
        ) as verification:
            report, exit_code = smoke_module.run_smoke(workspace_path)

        self.assertEqual(exit_code, 0)
        self.assertIn("revision_workflow", report["checks"])
        verification.assert_called_once()
        backend = verification.call_args.args[1]
        counts = Counter(request.phase for request in backend.requests)
        self.assertEqual(
            counts,
            {
                Phase.DRAFT: 1,
                Phase.AUDIT: 6,
                Phase.PROPOSE_REVISION: 1,
                Phase.SYNTHESIZE: 1,
                Phase.FINALIZE: 1,
            },
        )
        self.assertEqual(
            backend.maximum_audit_active,
            len(SPECIALIST_IDS),
        )
        self.assertEqual(
            Counter(
                definition.agent_id
                for definition in backend.definitions
            ),
            Counter({agent_id: 1 for agent_id in ROSTER}),
        )
        self.assertTrue(
            all(
                not hasattr(request, "system_prompt")
                for request in backend.requests
            )
        )


class SmokeFailureTests(unittest.TestCase):
    def test_malformed_existing_config_uses_existing_cli_error_contract(
        self,
    ) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = initialize_workspace(Path(temporary.name) / "workspace")
        workspace.path("config/system_defaults.json").write_bytes(b"{invalid")

        exit_code, stdout, stderr = invoke_cli(
            ["smoke", "--workspace", str(workspace.root)]
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertTrue(stderr.startswith("WorkspaceFormatError: "))
        self.assertIn("config/system_defaults.json", stderr)
        self.assertTrue(stderr.endswith("\n"))

    def test_invariant_failure_is_compact_failed_json_with_code_one(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace_path = Path(temporary.name) / "workspace"

        with mock.patch.object(
            smoke_module,
            "_verify_revision_workflow",
            side_effect=smoke_module._SmokeInvariantError(
                "revision_workflow"
            ),
        ):
            exit_code, stdout, stderr = invoke_cli(
                ["smoke", "--workspace", str(workspace_path)]
            )

        self.assertEqual(exit_code, 1)
        self.assertEqual(stderr, "")
        self.assertEqual(len(stdout.splitlines()), 1)
        report = json.loads(stdout)
        self.assertEqual(
            stdout,
            json.dumps(
                report,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n",
        )
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["failed_check"], "revision_workflow")
        self.assertEqual(
            report["checks"],
            ["workspace_initialized", "directive_persisted"],
        )
        self.assertIs(report["provider_tested"], False)
        self.assertEqual(report["debate_status"], "consensus")


class ExistingRunExitCodeTests(unittest.TestCase):
    def test_run_deadlock_and_failed_codes_remain_three_and_four(self) -> None:
        cases = (
            ("deadlock", 3),
            ("failed", 4),
        )
        for outcome, expected_code in cases:
            with self.subTest(outcome=outcome):
                temporary = tempfile.TemporaryDirectory()
                self.addCleanup(temporary.cleanup)
                workspace = initialize_workspace(
                    Path(temporary.name) / "workspace"
                )

                def respond(request):
                    if request.phase is Phase.AUDIT:
                        if outcome == "failed" and request.agent_id == PLOT_ARCHITECT:
                            return AgentReply.approval("wrong-revision")
                        if request.agent_id == PLOT_ARCHITECT:
                            return AgentReply.conflict(
                                request.revision_id,
                                "neutral unresolved conflict",
                            )
                        return AgentReply.approval(request.revision_id)
                    raise AssertionError(
                        f"unexpected phase: {request.phase}"
                    )

                backend = ScriptedBackend(respond)
                with mock.patch.object(
                    cli_module,
                    "AgentBackend",
                    return_value=backend,
                ):
                    exit_code, stdout, stderr = invoke_cli(
                        [
                            "run",
                            "--workspace",
                            str(workspace.root),
                            "--session",
                            f"{outcome}_session",
                            "--backend",
                            "plot_architect=codex",
                            "--draft",
                            "neutral candidate",
                            "--debate-id",
                            f"{outcome}_debate",
                            "--max-debate-rounds",
                            "0",
                        ]
                    )

                self.assertEqual(exit_code, expected_code)
                self.assertEqual(stderr, "")
                report = json.loads(stdout)
                self.assertEqual(report["status"], outcome)
                self.assertEqual(all_manuscripts(workspace), [])


if __name__ == "__main__":
    unittest.main()
