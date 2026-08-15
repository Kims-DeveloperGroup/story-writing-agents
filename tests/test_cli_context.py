"""Context-aware CLI workspace, session, and run-input shorthand tests."""

from __future__ import annotations

import contextlib
import io
import inspect
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
    load_conversation_selection,
    resolve_workspace,
    save_conversation_selection,
    select_session,
)
from story_writing_agents.errors import (
    ConfigurationError,
    SessionIdError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)

from tests.helpers import ScriptedBackend, phase_requests


CLI_CONTEXT_PATH = "agent_states/orchestrator_state/cli_context.json"
CONVERSATION_SELECTION_DIRECTORY = (
    "agent_states/orchestrator_state/conversation_selections"
)


def conversation_selection_path(workspace: Workspace, session_id: str) -> Path:
    """Return one test-owned path for a safe conversation selection file."""

    return workspace.root / CONVERSATION_SELECTION_DIRECTORY / f"{session_id}.json"


def tree_snapshot(root: Path) -> dict[str, tuple[bytes, int, int]]:
    """Capture file bytes, modes, and timestamps for no-mutation assertions."""

    return {
        str(path.relative_to(root)): (
            path.read_bytes(),
            path.stat().st_mode,
            path.stat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


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


class ConversationSelectionTests(unittest.TestCase):
    """Verify exact lazy per-session conversation selection persistence."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = initialize_workspace(
            Path(self.temporary.name) / "workspace"
        )

    def write_raw(self, session_id: str, content: bytes) -> Path:
        """Write one deliberately untrusted persisted selection payload."""

        target = conversation_selection_path(self.workspace, session_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def test_public_signatures_docstrings_and_missing_file_are_exact(self) -> None:
        self.assertEqual(
            tuple(inspect.signature(load_conversation_selection).parameters),
            ("workspace", "session_id"),
        )
        self.assertEqual(
            tuple(inspect.signature(save_conversation_selection).parameters),
            ("workspace", "session_id", "selected_conversation_id"),
        )
        self.assertTrue(inspect.getdoc(load_conversation_selection))
        self.assertTrue(inspect.getdoc(save_conversation_selection))

        before = tree_snapshot(self.workspace.root)
        self.assertIsNone(
            load_conversation_selection(self.workspace, "chapter_one")
        )
        self.assertEqual(tree_snapshot(self.workspace.root), before)
        self.assertFalse(
            conversation_selection_path(
                self.workspace,
                "chapter_one",
            ).exists()
        )

    def test_read_time_disappearance_is_absent_but_other_io_propagates(
        self,
    ) -> None:
        """Treat only a direct read-time FileNotFoundError as absence."""

        session_id = "chapter_one"
        conversation_id = "c_0123456789abcdef0123456789abcdef"
        save_conversation_selection(
            self.workspace,
            session_id,
            conversation_id,
        )
        target = conversation_selection_path(self.workspace, session_id)
        before = tree_snapshot(self.workspace.root)
        expected_after = dict(before)
        expected_after.pop(str(target.relative_to(self.workspace.root)))
        original_read_text = self.workspace.read_text

        def disappear_then_read(relative: str | os.PathLike[str]) -> str:
            target.unlink()
            return original_read_text(relative)

        with mock.patch.object(
            self.workspace,
            "read_text",
            side_effect=disappear_then_read,
        ) as read_text:
            self.assertIsNone(
                load_conversation_selection(self.workspace, session_id)
            )
        read_text.assert_called_once_with(
            f"{CONVERSATION_SELECTION_DIRECTORY}/{session_id}.json"
        )
        self.assertFalse(target.exists())
        self.assertEqual(tree_snapshot(self.workspace.root), expected_after)
        self.assertEqual(list(target.parent.iterdir()), [])

        save_conversation_selection(
            self.workspace,
            session_id,
            conversation_id,
        )
        persisted_before = (target.read_bytes(), target.stat().st_mtime_ns)

        def deny_read(_relative: str | os.PathLike[str]) -> str:
            try:
                raise PermissionError("read-secret")
            except PermissionError as exc:
                raise WorkspaceFormatError("cannot read selection") from exc

        with mock.patch.object(
            self.workspace,
            "read_text",
            side_effect=deny_read,
        ):
            with self.assertRaises(WorkspaceFormatError) as raised:
                load_conversation_selection(self.workspace, session_id)
        self.assertIsInstance(raised.exception.__cause__, PermissionError)
        self.assertEqual(
            (target.read_bytes(), target.stat().st_mtime_ns),
            persisted_before,
        )

    def test_save_load_and_explicit_null_use_exact_independent_v1_files(
        self,
    ) -> None:
        select_session(self.workspace, "story_session")
        cli_context = self.workspace.path(CLI_CONTEXT_PATH)
        cli_before = (cli_context.read_bytes(), cli_context.stat().st_mtime_ns)

        selections = {
            "chapter_one": "c_0123456789abcdef0123456789abcdef",
            "chapter_two": "c_fedcba9876543210fedcba9876543210",
        }
        for session_id, conversation_id in selections.items():
            save_conversation_selection(
                self.workspace,
                session_id,
                conversation_id,
            )
            target = conversation_selection_path(self.workspace, session_id)
            expected = {
                "schema_version": 1,
                "session_id": session_id,
                "selected_conversation_id": conversation_id,
            }
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                json.dumps(
                    expected,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
            )
            self.assertEqual(
                load_conversation_selection(self.workspace, session_id),
                conversation_id,
            )

        save_conversation_selection(self.workspace, "chapter_one", None)
        self.assertIsNone(
            load_conversation_selection(self.workspace, "chapter_one")
        )
        self.assertEqual(
            load_conversation_selection(self.workspace, "chapter_two"),
            selections["chapter_two"],
        )
        self.assertIsNone(
            self.workspace.read_json(
                f"{CONVERSATION_SELECTION_DIRECTORY}/chapter_one.json"
            )["selected_conversation_id"]
        )
        self.assertEqual(
            (cli_context.read_bytes(), cli_context.stat().st_mtime_ns),
            cli_before,
        )

    def test_load_strictly_rejects_schema_version_identity_and_id_values(
        self,
    ) -> None:
        valid = {
            "schema_version": 1,
            "session_id": "chapter_one",
            "selected_conversation_id": (
                "c_0123456789abcdef0123456789abcdef"
            ),
        }
        cases = (
            ("not_object", []),
            (
                "missing_field",
                {
                    "schema_version": 1,
                    "session_id": "chapter_one",
                },
            ),
            ("extra_field", {**valid, "extra": True}),
            ("boolean_version", {**valid, "schema_version": True}),
            ("future_version", {**valid, "schema_version": 2}),
            ("string_version", {**valid, "schema_version": "1"}),
            ("integer_session", {**valid, "session_id": 7}),
            ("unsafe_session", {**valid, "session_id": "../escape"}),
            ("wrong_session", {**valid, "session_id": "chapter_two"}),
            (
                "boolean_conversation",
                {**valid, "selected_conversation_id": True},
            ),
            (
                "integer_conversation",
                {**valid, "selected_conversation_id": 7},
            ),
            (
                "unsafe_conversation",
                {**valid, "selected_conversation_id": "../escape"},
            ),
        )
        for label, value in cases:
            with self.subTest(label=label):
                self.write_raw(
                    "chapter_one",
                    (json.dumps(value, ensure_ascii=False) + "\n").encode(
                        "utf-8"
                    ),
                )
                with self.assertRaises(WorkspaceFormatError):
                    load_conversation_selection(
                        self.workspace,
                        "chapter_one",
                    )

        self.write_raw(
            "chapter_one",
            (
                b'{"schema_version":1,"schema_version":1,'
                b'"session_id":"chapter_one",'
                b'"selected_conversation_id":null}\n'
            ),
        )
        with self.assertRaises(WorkspaceFormatError):
            load_conversation_selection(self.workspace, "chapter_one")

    def test_load_rejects_malformed_json_and_non_utf8_without_rewriting(self) -> None:
        for label, content in (
            ("malformed", b"{not-json}\n"),
            ("non_utf8", b"\xff\n"),
        ):
            with self.subTest(label=label):
                target = self.write_raw("chapter_one", content)
                before = (target.read_bytes(), target.stat().st_mtime_ns)
                with self.assertRaises(WorkspaceFormatError):
                    load_conversation_selection(
                        self.workspace,
                        "chapter_one",
                    )
                self.assertEqual(
                    (target.read_bytes(), target.stat().st_mtime_ns),
                    before,
                )

    def test_call_identifiers_and_symlinked_storage_are_rejected_safely(
        self,
    ) -> None:
        for session_id, conversation_id in (
            ("../session", "conversation"),
            ("session", "../conversation"),
            ("session", 7),
        ):
            with self.subTest(
                session_id=session_id,
                conversation_id=conversation_id,
            ):
                with self.assertRaises(SessionIdError):
                    save_conversation_selection(
                        self.workspace,
                        session_id,
                        conversation_id,  # type: ignore[arg-type]
                    )
        with self.assertRaises(SessionIdError):
            load_conversation_selection(self.workspace, "../session")

        with tempfile.TemporaryDirectory() as external:
            selection_directory = (
                self.workspace.root / CONVERSATION_SELECTION_DIRECTORY
            )
            selection_directory.symlink_to(
                Path(external),
                target_is_directory=True,
            )
            for operation in (
                lambda: load_conversation_selection(
                    self.workspace,
                    "chapter_one",
                ),
                lambda: save_conversation_selection(
                    self.workspace,
                    "chapter_one",
                    "conversation_one",
                ),
            ):
                with self.assertRaises(WorkspaceSecurityError):
                    operation()

        self.workspace = initialize_workspace(
            Path(self.temporary.name) / "second_workspace"
        )
        target = conversation_selection_path(self.workspace, "chapter_one")
        target.parent.mkdir(parents=True, exist_ok=True)
        external_file = Path(self.temporary.name) / "outside.json"
        external_file.write_text("{}\n", encoding="utf-8")
        target.symlink_to(external_file)
        for operation in (
            lambda: load_conversation_selection(
                self.workspace,
                "chapter_one",
            ),
            lambda: save_conversation_selection(
                self.workspace,
                "chapter_one",
                "conversation_one",
            ),
        ):
            with self.assertRaises(WorkspaceSecurityError):
                operation()
        self.assertEqual(external_file.read_text(encoding="utf-8"), "{}\n")

    def test_failed_atomic_replace_preserves_previous_bytes_and_cleans_temp(
        self,
    ) -> None:
        save_conversation_selection(
            self.workspace,
            "chapter_one",
            "conversation_one",
        )
        target = conversation_selection_path(self.workspace, "chapter_one")
        before = (target.read_bytes(), target.stat().st_mtime_ns)
        with mock.patch(
            "story_writing_agents.workspace.os.replace",
            side_effect=OSError("selection-secret"),
        ):
            with self.assertRaises(OSError):
                save_conversation_selection(
                    self.workspace,
                    "chapter_one",
                    "conversation_two",
                )
        self.assertEqual(
            (target.read_bytes(), target.stat().st_mtime_ns),
            before,
        )
        self.assertEqual(list(target.parent.glob("*.tmp")), [])


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
