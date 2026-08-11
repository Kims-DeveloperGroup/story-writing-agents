"""Context-aware CLI workspace, session, and run-input shorthand tests."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

from story_writing_agents import (
    AgentBackend,
    AgentReply,
    DebateStatus,
    Phase,
    StoryRuntime,
    Workspace,
    initialize_workspace,
)
from story_writing_agents import cli as cli_module
from story_writing_agents.cli_context import (
    load_cli_context,
    resolve_workspace,
    select_session,
)
from story_writing_agents.errors import (
    ConfigurationError,
    SessionIdError,
)

from tests.helpers import ScriptedBackend, phase_requests


CLI_CONTEXT_PATH = "agent_states/orchestrator_state/cli_context.json"


def invoke_cli(arguments: list[str]) -> tuple[int, str, str]:
    """Invoke the CLI in-process and capture its public streams."""

    stdout = io.StringIO()
    stderr = io.StringIO()
    with (
        contextlib.redirect_stdout(stdout),
        contextlib.redirect_stderr(stderr),
    ):
        exit_code = cli_module.main(arguments)
    return exit_code, stdout.getvalue(), stderr.getvalue()


@contextlib.contextmanager
def working_directory(path: Path) -> Iterator[None]:
    """Temporarily switch cwd and always restore it."""

    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def successful_backend() -> ScriptedBackend:
    """Return a provider-free backend that reaches immediate consensus."""

    def respond(request):
        if request.phase is Phase.DRAFT:
            return AgentReply.candidate(
                request.revision_id,
                "generated neutral candidate",
            )
        if request.phase is Phase.AUDIT:
            return AgentReply.approval(request.revision_id)
        if request.phase is Phase.FINALIZE:
            return AgentReply.finalization(request.revision_id)
        raise AssertionError(f"unexpected phase: {request.phase}")

    return ScriptedBackend(respond)


class WorkspaceDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)

    def test_root_and_nested_paths_discover_nearest_initialized_workspace(
        self,
    ) -> None:
        workspace = initialize_workspace(self.base / "workspace")
        nested = workspace.root / "one" / "two"
        nested.mkdir(parents=True)

        self.assertEqual(
            resolve_workspace(None, start=workspace.root).root,
            workspace.root,
        )
        self.assertEqual(
            resolve_workspace(None, start=nested).root,
            workspace.root,
        )

    def test_nested_workspace_wins_over_initialized_outer_workspace(self) -> None:
        outer = initialize_workspace(self.base / "outer")
        inner = initialize_workspace(outer.root / "projects" / "inner")
        inner_child = inner.root / "nested" / "child"
        inner_child.mkdir(parents=True)
        outer_child = outer.root / "outside_inner"
        outer_child.mkdir()

        self.assertEqual(
            resolve_workspace(None, start=inner_child).root,
            inner.root,
        )
        self.assertEqual(
            resolve_workspace(None, start=outer_child).root,
            outer.root,
        )

    def test_explicit_workspace_precedes_discovery_and_errors_are_actionable(
        self,
    ) -> None:
        outer = initialize_workspace(self.base / "outer")
        inner = initialize_workspace(outer.root / "inner")
        inner_child = inner.root / "child"
        inner_child.mkdir()

        self.assertEqual(
            resolve_workspace(str(outer.root), start=inner_child).root,
            outer.root,
        )

        uninitialized = self.base / "not_initialized"
        uninitialized.mkdir()
        with self.assertRaisesRegex(
            ConfigurationError,
            "workspace is not initialized",
        ):
            resolve_workspace(str(uninitialized), start=inner_child)

        isolated = tempfile.TemporaryDirectory()
        self.addCleanup(isolated.cleanup)
        with self.assertRaisesRegex(
            ConfigurationError,
            "no initialized story workspace found",
        ):
            resolve_workspace(None, start=Path(isolated.name))


class CliContextValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = initialize_workspace(
            Path(self.temporary.name) / "workspace"
        )

    def test_context_schema_version_and_selected_session_are_strict(self) -> None:
        cases = (
            ("not_object", [], ConfigurationError, "invalid schema"),
            (
                "missing_field",
                {"schema_version": 1},
                ConfigurationError,
                "invalid schema",
            ),
            (
                "extra_field",
                {
                    "schema_version": 1,
                    "selected_session": "session_a",
                    "extra": True,
                },
                ConfigurationError,
                "invalid schema",
            ),
            (
                "boolean_version",
                {"schema_version": True, "selected_session": "session_a"},
                ConfigurationError,
                "schema version",
            ),
            (
                "future_version",
                {"schema_version": 2, "selected_session": "session_a"},
                ConfigurationError,
                "schema version",
            ),
            (
                "null_session",
                {"schema_version": 1, "selected_session": None},
                ConfigurationError,
                "must be a session ID",
            ),
            (
                "integer_session",
                {"schema_version": 1, "selected_session": 7},
                ConfigurationError,
                "must be a session ID",
            ),
            (
                "unsafe_session",
                {"schema_version": 1, "selected_session": "../escape"},
                SessionIdError,
                "must match",
            ),
        )
        for label, value, error_type, message in cases:
            with self.subTest(label=label):
                self.workspace.write_json(CLI_CONTEXT_PATH, value)
                with self.assertRaisesRegex(error_type, message):
                    load_cli_context(self.workspace)


class SessionSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = initialize_workspace(
            Path(self.temporary.name) / "workspace"
        )

    def test_successful_start_selects_and_bare_end_start_preserve_selection(
        self,
    ) -> None:
        exit_code, stdout, stderr = invoke_cli(
            [
                "session",
                "start",
                "selected_session",
                "--workspace",
                str(self.workspace.root),
            ]
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            {"session_id": "selected_session", "active": True},
        )
        self.assertEqual(
            self.workspace.read_json(CLI_CONTEXT_PATH),
            {
                "schema_version": 1,
                "selected_session": "selected_session",
            },
        )

        exit_code, stdout, stderr = invoke_cli(
            [
                "session",
                "end",
                "--workspace",
                str(self.workspace.root),
            ]
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            {"session_id": "selected_session", "active": False},
        )
        self.assertEqual(
            load_cli_context(self.workspace).selected_session,
            "selected_session",
        )

        exit_code, stdout, stderr = invoke_cli(
            [
                "session",
                "start",
                "--workspace",
                str(self.workspace.root),
            ]
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            {"session_id": "selected_session", "active": True},
        )
        self.assertEqual(
            load_cli_context(self.workspace).selected_session,
            "selected_session",
        )

    def test_explicit_other_session_on_non_start_commands_never_reselects(
        self,
    ) -> None:
        exit_code, _, _ = invoke_cli(
            [
                "session",
                "start",
                "selected_session",
                "--workspace",
                str(self.workspace.root),
            ]
        )
        self.assertEqual(exit_code, 0)
        provider = AgentBackend(self.workspace)
        provider.start_session("other_session")
        provider.close()

        commands = (
            [
                "directive",
                "--workspace",
                str(self.workspace.root),
                "--session",
                "other_session",
                "[SYS: LANG=EN]",
            ],
            [
                "inspect",
                "--workspace",
                str(self.workspace.root),
                "--session",
                "other_session",
            ],
            [
                "session",
                "end",
                "other_session",
                "--workspace",
                str(self.workspace.root),
            ],
        )
        for arguments in commands:
            with self.subTest(command=arguments[0:2]):
                exit_code, _, stderr = invoke_cli(arguments)
                self.assertEqual(exit_code, 0, stderr)
                self.assertEqual(
                    load_cli_context(self.workspace).selected_session,
                    "selected_session",
                )

        backend = successful_backend()
        with mock.patch.object(
            cli_module,
            "AgentBackend",
            return_value=backend,
        ):
            exit_code, stdout, stderr = invoke_cli(
                [
                    "run",
                    "--workspace",
                    str(self.workspace.root),
                    "--session",
                    "other_session",
                    "--prompt",
                    "explicit other prompt",
                    "--debate-id",
                    "other_debate",
                    "--output",
                    "other.md",
                ]
            )
        self.assertEqual(exit_code, 0, stderr)
        self.assertEqual(json.loads(stdout)["session_id"], "other_session")
        self.assertEqual(
            load_cli_context(self.workspace).selected_session,
            "selected_session",
        )

    def test_positional_and_option_session_conflict_returns_code_two(self) -> None:
        exit_code, stdout, stderr = invoke_cli(
            [
                "session",
                "start",
                "positional_session",
                "--session",
                "option_session",
                "--workspace",
                str(self.workspace.root),
            ]
        )
        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("ConfigurationError", stderr)
        self.assertIn("provide a session once", stderr)
        self.assertFalse(self.workspace.path(CLI_CONTEXT_PATH).exists())

    def test_invalid_explicit_session_does_not_fall_back_to_selection(self) -> None:
        select_session(self.workspace, "selected_session")

        exit_code, stdout, stderr = invoke_cli(
            [
                "inspect",
                "--workspace",
                str(self.workspace.root),
                "--session",
                "",
            ]
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("SessionIdError", stderr)
        self.assertIn("must match", stderr)


class RunInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = initialize_workspace(
            Path(self.temporary.name) / "workspace"
        )

    def _run(
        self,
        arguments: list[str],
        backend: ScriptedBackend,
    ) -> tuple[int, str, str]:
        with mock.patch.object(
            cli_module,
            "AgentBackend",
            return_value=backend,
        ):
            return invoke_cli(arguments)

    def test_positional_prompt_and_existing_prompt_draft_options_remain_compatible(
        self,
    ) -> None:
        cases = (
            ("positional", ["positional prompt"], "prompt", "positional prompt"),
            ("prompt_option", ["--prompt", "option prompt"], "prompt", "option prompt"),
            ("draft_option", ["--draft", "option draft"], "draft", "option draft"),
        )
        for label, input_arguments, expected_kind, expected_payload in cases:
            with self.subTest(label=label):
                backend = successful_backend()
                exit_code, stdout, stderr = self._run(
                    [
                        "run",
                        "--workspace",
                        str(self.workspace.root),
                        "--session",
                        f"{label}_session",
                        *input_arguments,
                        "--debate-id",
                        f"{label}_debate",
                        "--output",
                        f"{label}.md",
                    ],
                    backend,
                )
                self.assertEqual(exit_code, 0, stderr)
                report = json.loads(stdout)
                self.assertEqual(report["status"], DebateStatus.CONSENSUS.value)
                self.assertEqual(report["input_kind"], expected_kind)
                if expected_kind == "prompt":
                    drafts = phase_requests(backend, Phase.DRAFT)
                    self.assertEqual(len(drafts), 1)
                    self.assertEqual(drafts[0].user_payload, expected_payload)
                else:
                    self.assertEqual(phase_requests(backend, Phase.DRAFT), [])
                    audits = phase_requests(backend, Phase.AUDIT)
                    self.assertTrue(
                        all(
                            request.user_payload == expected_payload
                            and request.candidate_payload == expected_payload
                            for request in audits
                        )
                    )

    def test_draft_file_is_cwd_relative_and_strict_utf8(self) -> None:
        cwd = Path(self.temporary.name) / "caller"
        cwd.mkdir()
        (cwd / "neutral.md").write_text(
            "cwd-relative draft",
            encoding="utf-8",
        )
        (cwd / "invalid.md").write_bytes(b"\xff")

        backend = successful_backend()
        with working_directory(cwd):
            exit_code, stdout, stderr = self._run(
                [
                    "run",
                    "--workspace",
                    str(self.workspace.root),
                    "--session",
                    "file_session",
                    "--draft-file",
                    "neutral.md",
                    "--debate-id",
                    "file_debate",
                    "--output",
                    "file.md",
                ],
                backend,
            )
        self.assertEqual(exit_code, 0, stderr)
        self.assertEqual(json.loads(stdout)["input_kind"], "draft")
        self.assertTrue(
            all(
                request.user_payload == "cwd-relative draft"
                for request in phase_requests(backend, Phase.AUDIT)
            )
        )

        with mock.patch.object(cli_module, "AgentBackend") as backend_class:
            with working_directory(cwd):
                exit_code, stdout, stderr = invoke_cli(
                    [
                        "run",
                        "--workspace",
                        str(self.workspace.root),
                        "--session",
                        "invalid_file_session",
                        "--draft-file",
                        "invalid.md",
                    ]
                )
        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("draft file is not valid UTF-8", stderr)
        backend_class.assert_not_called()

    def test_run_requires_exactly_one_input(self) -> None:
        invalid_cases = (
            ["run"],
            ["run", "positional", "--prompt", "option prompt"],
            ["run", "positional", "--draft", "option draft"],
        )
        for index, suffix in enumerate(invalid_cases):
            with self.subTest(suffix=suffix):
                with mock.patch.object(cli_module, "AgentBackend") as backend_class:
                    exit_code, stdout, stderr = invoke_cli(
                        [
                            *suffix,
                            "--workspace",
                            str(self.workspace.root),
                            "--session",
                            f"invalid_{index}",
                        ]
                    )
                self.assertEqual(exit_code, 2)
                self.assertEqual(stdout, "")
                self.assertIn("exactly one run input is required", stderr)
                backend_class.assert_not_called()

        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            cli_module.main(
                [
                    "run",
                    "--workspace",
                    str(self.workspace.root),
                    "--session",
                    "argparse_invalid",
                    "--prompt",
                    "prompt",
                    "--draft",
                    "draft",
                ]
            )
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("not allowed with argument", stderr.getvalue())


class InferredCommandTests(unittest.TestCase):
    def test_nested_cwd_infers_workspace_and_selected_session_for_commands(
        self,
    ) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = initialize_workspace(
            Path(temporary.name) / "workspace"
        )
        nested = workspace.root / "caller" / "nested"
        nested.mkdir(parents=True)

        with working_directory(nested):
            exit_code, stdout, stderr = invoke_cli(
                ["session", "start", "inferred_session"]
            )
            self.assertEqual(exit_code, 0, stderr)
            self.assertEqual(
                json.loads(stdout)["session_id"],
                "inferred_session",
            )

            exit_code, stdout, stderr = invoke_cli(
                ["directive", "[SYS: LANG=EN]"]
            )
            self.assertEqual(exit_code, 0, stderr)
            self.assertEqual(json.loads(stdout)["language"], "EN")

            exit_code, stdout, stderr = invoke_cli(["inspect"])
            self.assertEqual(exit_code, 0, stderr)
            self.assertEqual(
                json.loads(stdout)["session_id"],
                "inferred_session",
            )

            backend = successful_backend()
            with mock.patch.object(
                cli_module,
                "AgentBackend",
                return_value=backend,
            ):
                exit_code, stdout, stderr = invoke_cli(
                    [
                        "run",
                        "inferred positional prompt",
                        "--debate-id",
                        "inferred_debate",
                        "--output",
                        "inferred.md",
                    ]
                )
            self.assertEqual(exit_code, 0, stderr)
            report = json.loads(stdout)
            self.assertEqual(report["session_id"], "inferred_session")
            self.assertEqual(report["input_kind"], "prompt")
            self.assertEqual(
                phase_requests(backend, Phase.DRAFT)[0].user_payload,
                "inferred positional prompt",
            )

        inspected = StoryRuntime(workspace).inspect_debate(
            "inferred_session",
            "inferred_debate",
        )
        self.assertEqual(inspected["status"], "consensus")


if __name__ == "__main__":
    unittest.main()
