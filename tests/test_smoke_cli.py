"""Provider-free smoke contracts after direct story execution retirement."""

from __future__ import annotations

import hashlib
import shutil
import tempfile

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


def editorial_run(*arguments):
    tool = Path(__file__).resolve().parents[1] / "tools" / "evaluate_lead_editorial.py"
    return subprocess.run(
        [sys.executable, str(tool), *map(str, arguments)],
        capture_output=True,
        text=True,
        check=False,
    )


def editorial_action(command, dataset, captures, *, packet=None, reviews=None, output):
    arguments = [command, "--dataset", dataset, "--captures", captures]
    if packet is not None:
        arguments.extend(["--packet", packet, "--reviews", reviews])
    return editorial_run(*arguments, "--out-dir", output)


def editorial_fixture(case, bundle="lead-editorial-offline-example"):
    dataset = Path(__file__).resolve().parents[1] / "docs" / "evaluations" / "lead-editorial-cases.json"
    scratch = Path(case.enterContext(tempfile.TemporaryDirectory()))
    captures = shutil.copytree(dataset.parent / bundle, scratch / "captures")
    packet = scratch / "prepared"
    completed = editorial_action('prepare', dataset, captures / 'captures.json', output=packet)
    case.assertEqual(completed.returncode, 0, completed.stderr)
    return dataset, captures, packet


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

        offline_help = editorial_run("--help")
        self.assertEqual(offline_help.returncode, 0, offline_help.stderr)
        self.assertIn("prepare", offline_help.stdout)
        self.assertIn("compare", offline_help.stdout)
        for flag in ("--provider", "--backend", "--agent", "--apply", "--commit"):
            self.assertNotIn(flag, offline_help.stdout)

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

        dataset, captures, packet_dir = editorial_fixture(self)
        packet = json.loads((packet_dir / "review-packet.json").read_bytes())
        cases = json.loads(dataset.read_bytes())["cases"]
        self.assertEqual([case["case_id"] for case in packet["cases"]], [case["case_id"] for case in cases])
        self.assertEqual(packet["dataset_sha256"], hashlib.sha256(dataset.read_bytes()).hexdigest())
        self.assertEqual(packet["captures_sha256"], hashlib.sha256((captures / "captures.json").read_bytes()).hexdigest())
        self.assertEqual(set(path.name for path in packet_dir.iterdir()), {"review-packet.json", "review-packet.md", "blind-map.json"})
        rubric = packet["rubric"]
        self.assertEqual(rubric["score_range"], [0, 4])
        self.assertEqual(set(rubric["dimensions"]), set(json.loads(dataset.read_bytes())["scoring_dimensions"]))
        markdown = (packet_dir / "review-packet.md").read_text()
        for anchors in rubric["dimensions"].values():
            self.assertEqual(set(anchors), {"0", "2", "4"})
        self.assertTrue(all(text and text in markdown for anchors in rubric["dimensions"].values() for text in anchors.values()))
        self.assertTrue(rubric["instructions"])
        self.assertTrue(all(instruction in markdown for instruction in rubric["instructions"]))
        self.assertIs(packet["constructed_fixtures"], json.loads(dataset.read_bytes())["constructed_fixtures"])
        self.assertIn("Dataset constructed fixtures: true", markdown)
        self.assertIn("Case constructed fixture: true", markdown)
        for actual, source in zip(packet["cases"], cases):
            self.assertIs(actual["constructed_fixture"], source["constructed_fixture"])
            self.assertEqual(actual["input"], {key: source[key] for key in ("sources", "user_prompts", "editorial_brief", "context_material", "workflow_steps")})
            self.assertEqual(actual["source_sha256"], {item["source_id"]: item["sha256"] for item in source["sources"]})
            self.assertNotIn("current_baseline", actual["input"])
            self.assertNotIn("target_behavior", actual["input"])
        self.assertNotIn("fixture-before", (packet_dir / "review-packet.json").read_text())
        again = packet_dir.parent / "again"
        repeated = editorial_action('prepare', dataset, captures / 'captures.json', output=again)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        for filename in ("review-packet.json", "review-packet.md", "blind-map.json"):
            self.assertEqual((packet_dir / filename).read_bytes(), (again / filename).read_bytes())

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

        dataset, captures, packet_dir = editorial_fixture(self)
        output = packet_dir.parent / "comparison"
        compared = editorial_action('compare', dataset, captures / 'captures.json', packet=packet_dir / 'review-packet.json', reviews=captures / 'reviews.json', output=output)
        self.assertEqual(compared.returncode, 0, compared.stderr)
        report = json.loads((output / "comparison-report.json").read_bytes())
        self.assertEqual(set(path.name for path in output.iterdir()), {"comparison-report.json", "comparison-report.md"})
        self.assertEqual((report["coverage"]["case_count"], report["coverage"]["captured_pairs"]), (9, 1))
        self.assertEqual((report["quality_conclusion"], report["improvement_assessment"]), ("INCONCLUSIVE", "NOT_ASSESSED"))
        self.assertEqual([case["case_id"] for case in report["cases"]], [case["case_id"] for case in json.loads(dataset.read_bytes())["cases"]])
        self.assertTrue(all(case["evidence_status"] == "INCONCLUSIVE" for case in report["cases"]))
        case = report["cases"][0]
        manifest = json.loads((captures / "captures.json").read_bytes())
        for arm, run in case["runs"].items():
            self.assertEqual(run["output_text"].encode("utf-8"), (captures / "output-a.txt").read_bytes())
            self.assertEqual(run["output_utf8_bytes"], len((captures / "output-a.txt").read_bytes()))
            self.assertEqual(run["output_sha256"], hashlib.sha256((captures / "output-a.txt").read_bytes()).hexdigest())
            self.assertEqual(run["costs"], manifest["cases"][0]["runs"][arm]["costs"])
            self.assertIn("accounting_backing", case["cost_missing"][arm])
            self.assertIn("dialogue-source-grounding", case["workflow"]["missing_or_nonpassing"][arm])
        self.assertEqual(case["matched_conditions"]["status"], "unknown")
        self.assertTrue(case["matched_conditions"]["unknown_fields"])
        expected_reviews = json.loads((captures / "reviews.json").read_bytes())["reviews"]
        axes = json.loads(dataset.read_bytes())["scoring_dimensions"]
        key = f'{manifest["comparison_id"]}\0{manifest["seed"]}\0{case["case_id"]}'
        sign = -1 if hashlib.sha256(key.encode()).digest()[0] & 1 else 1
        self.assertEqual(case["side_to_arm"], dict(zip("AB", [arm["arm_id"] for arm in manifest["arms"]][::sign])))
        self.assertEqual(len(case["reviews"]), len(expected_reviews))
        for actual, expected in zip(case["reviews"], expected_reviews):
            self.assertEqual(actual["reviewer_id"], expected["reviewer_id"])
            self.assertEqual(actual["ratings"], expected["ratings"])
            deltas = {
                axis: sign * (expected["ratings"]["B"][axis]["score"] - expected["ratings"]["A"][axis]["score"])
                for axis in axes
            }
            self.assertEqual(actual["paired_deltas"], deltas)
            self.assertTrue(actual["unbacked_rating_fields"])
        self.assertTrue(case["disagreements"]["preferences"])
        self.assertEqual(case["disagreements"]["axes"], {axis: len({r["paired_deltas"][axis] for r in case["reviews"]}) > 1 for axis in axes})

        bundle = Path(__file__).parent / "fixtures" / "lead_editorial_comparison"
        dataset, captures, packet_dir = editorial_fixture(self, bundle)
        compared = editorial_action(
            "compare", dataset, captures / "captures.json", packet=packet_dir / "review-packet.json",
            reviews=captures / "reviews.json", output=packet_dir.parent / "comparison")
        self.assertEqual(compared.returncode, 0, compared.stderr)
        constructed = json.loads((packet_dir.parent / "comparison" / "comparison-report.json").read_bytes())
        for case_id, expected in {
            "stronger-effect": ("matched", "PAIRED_EVIDENCE_AVAILABLE"),
            "incompatible-recommendations": ("mismatched", "INCONCLUSIVE"),
        }.items():
            actual = next(case for case in constructed["cases"] if case["case_id"] == case_id)
            self.assertEqual(actual["workflow"]["expected"], [])
            self.assertEqual(actual["matched_conditions"]["status"], expected[0])
            self.assertEqual(actual["evidence_status"], expected[1])
            self.assertEqual(actual["matched_conditions"]["differences"], [] if expected[0] == "matched" else ["settings"])
        self.assertEqual(constructed["quality_conclusion"], "INCONCLUSIVE")


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

        dataset, captures, packet_dir = editorial_fixture(self)
        inputs_before = tree_snapshot(captures)
        packet_before = tree_snapshot(packet_dir)
        refused = editorial_action('prepare', dataset, captures / 'captures.json', output=packet_dir)
        self.assertEqual(refused.returncode, 2, refused.stderr)
        self.assertEqual(tree_snapshot(captures), inputs_before)
        self.assertEqual(tree_snapshot(packet_dir), packet_before)
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
