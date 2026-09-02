"""Provider-free ``story storage use`` command contracts."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from story_writing_agents import cli as cli_module
from story_writing_agents.cli import build_parser, main
from story_writing_agents.conversation_storage import (
    CONVERSATION_STORAGE_ACTIVATION_STATE_PATH,
)
from story_writing_agents.conversation_storage.selection import (
    ConversationStorageSelectionFailure,
    ConversationStorageSelectionReport,
)

from tests.helpers import initialized_workspace
from tests.sqlite_conversation_storage_fixture import database_path


def _completed(backend: str) -> ConversationStorageSelectionReport:
    return ConversationStorageSelectionReport(
        operation="use",
        outcome="completed",
        desired_backend=backend,
        installation="not_required",
        provisioning="not_required",
        parity="not_required",
        configuration="unchanged",
        activation="next_shell",
    )


class ConversationStorageSelectionCliTests(unittest.TestCase):
    def test_parser_grammar_and_flag_ownership_are_exact(self) -> None:
        parser = build_parser()
        parsed = parser.parse_args(
            [
                "storage",
                "use",
                "sqlite",
                "--install-missing",
                "--yes",
                "--workspace",
                "/workspace",
            ]
        )
        self.assertEqual(
            vars(parsed),
            {
                "command": "storage",
                "storage_action": "use",
                "backend": "sqlite",
                "install_missing": True,
                "yes": True,
                "workspace": "/workspace",
            },
        )
        filesystem = parser.parse_args(["storage", "use", "filesystem"])
        self.assertFalse(filesystem.install_missing)
        self.assertFalse(filesystem.yes)
        self.assertIsNone(filesystem.workspace)

        invalid = (
            ["storage", "use"],
            ["storage", "use", "sqlite", "extra"],
            ["storage", "migrate", "filesystem", "sqlite", "--yes"],
            ["storage", "verify", "filesystem", "sqlite", "--install-missing"],
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                stderr = io.StringIO()
                with (
                    contextlib.redirect_stderr(stderr),
                    self.assertRaises(SystemExit) as caught,
                ):
                    parser.parse_args(arguments)
                self.assertEqual(caught.exception.code, 2)
                self.assertIn("error:", stderr.getvalue())

    def test_invalid_backend_and_flag_pairs_are_early_exact_json_exit_two(self) -> None:
        cases = (
            (["storage", "use", "mysql"], None),
            (
                ["storage", "use", "filesystem", "--install-missing", "--yes"],
                "filesystem",
            ),
            (["storage", "use", "sqlite", "--install-missing"], "sqlite"),
            (["storage", "use", "sqlite", "--yes"], "sqlite"),
        )
        for arguments, desired in cases:
            with self.subTest(arguments=arguments):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    mock.patch.object(cli_module, "resolve_workspace") as resolve,
                    mock.patch.object(
                        cli_module,
                        "select_conversation_storage",
                    ) as select,
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    self.assertEqual(main(arguments), 2)
                resolve.assert_not_called()
                select.assert_not_called()
                self.assertEqual(stderr.getvalue(), "")
                lines = stdout.getvalue().splitlines()
                self.assertEqual(len(lines), 1)
                value = json.loads(lines[0])
                self.assertEqual(value["schema_version"], 1)
                self.assertEqual(value["operation"], "use")
                self.assertEqual(value["outcome"], "failed")
                self.assertEqual(value["desired_backend"], desired)
                self.assertEqual(value["failure"], "invalid_request")
                self.assertEqual(
                    lines[0],
                    json.dumps(
                        value,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )

    def test_use_dispatch_is_provider_free_and_passes_exact_selection_arguments(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        for backend, flags in (
            ("filesystem", []),
            ("sqlite", ["--install-missing", "--yes"]),
        ):
            with self.subTest(backend=backend):
                report = _completed(backend)
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    mock.patch.object(
                        cli_module,
                        "resolve_workspace",
                        return_value=workspace,
                    ) as resolve,
                    mock.patch.object(
                        cli_module,
                        "select_conversation_storage",
                        return_value=report,
                    ) as select,
                    mock.patch.object(cli_module, "ProviderAuthService") as auth,
                    mock.patch.object(cli_module, "AgentBackend") as provider,
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    self.assertEqual(
                        main(
                            [
                                "storage",
                                "use",
                                backend,
                                *flags,
                                "--workspace",
                                str(workspace.root),
                            ]
                        ),
                        0,
                    )
                resolve.assert_called_once_with(str(workspace.root))
                select.assert_called_once_with(
                    workspace,
                    backend,
                    install_missing=bool(flags),
                    yes=bool(flags),
                    diagnostic_stream=stderr,
                )
                auth.assert_not_called()
                provider.assert_not_called()
                self.assertEqual(stdout.getvalue(), report.to_json() + "\n")
                self.assertEqual(stderr.getvalue(), "")

    def test_workspace_and_selection_failures_emit_one_sanitized_json_document(
        self,
    ) -> None:
        secret = "private-workspace-secret"
        failed = ConversationStorageSelectionReport.failed(
            ConversationStorageSelectionFailure.PROVISIONING_FAILED,
            desired_backend="sqlite",
            installation="not_required",
            provisioning="failed",
        )
        cases = (
            (mock.Mock(side_effect=OSError(secret)), None, "workspace_invalid"),
            (
                mock.Mock(return_value=object()),
                mock.Mock(return_value=failed),
                "provisioning_failed",
            ),
        )
        for resolver, selector, expected in cases:
            with self.subTest(expected=expected):
                stdout = io.StringIO()
                stderr = io.StringIO()
                patches = [
                    mock.patch.object(cli_module, "resolve_workspace", resolver),
                ]
                if selector is not None:
                    patches.append(
                        mock.patch.object(
                            cli_module,
                            "select_conversation_storage",
                            selector,
                        )
                    )
                with (
                    patches[0],
                    patches[1] if len(patches) == 2 else contextlib.nullcontext(),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    self.assertEqual(
                        main(["storage", "use", "sqlite"]),
                        2,
                    )
                self.assertEqual(stderr.getvalue(), "")
                self.assertNotIn(secret, stdout.getvalue())
                lines = stdout.getvalue().splitlines()
                self.assertEqual(len(lines), 1)
                self.assertEqual(json.loads(lines[0])["failure"], expected)

    def test_help_lists_use_and_process_invalid_workspace_is_ci_guarded(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(repository_root / "src")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["CI"] = "1"
        help_result = subprocess.run(
            [sys.executable, "-m", "story_writing_agents", "storage", "--help"],
            cwd=repository_root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("use", help_result.stdout)

        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing-workspace"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "story_writing_agents",
                    "storage",
                    "use",
                    "sqlite",
                    "--install-missing",
                    "--yes",
                    "--workspace",
                    str(missing),
                ],
                cwd=repository_root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(completed.stderr, "")
            self.assertEqual(
                json.loads(completed.stdout)["failure"],
                "workspace_invalid",
            )
            self.assertFalse(missing.exists())

    def test_process_rejects_dirty_workspace_before_inspecting_invalid_database(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        database = database_path(workspace)
        database_before = b"invalid-sqlite-private-secret"
        database.write_bytes(database_before)
        configuration_paths = (
            workspace.path("config/system_defaults.json"),
            workspace.path("config/agent_configs.json"),
        )
        configuration_before = tuple(path.read_bytes() for path in configuration_paths)
        activation = workspace.path(CONVERSATION_STORAGE_ACTIVATION_STATE_PATH)
        self.assertFalse(activation.exists())

        repository_root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(repository_root / "src")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["CI"] = "1"
        arguments = [
            sys.executable,
            "-m",
            "story_writing_agents",
            "storage",
            "use",
            "sqlite",
            "--workspace",
            str(workspace.root),
        ]
        self.assertNotIn("--install-missing", arguments)
        self.assertNotIn("--yes", arguments)
        completed = subprocess.run(
            arguments,
            cwd=repository_root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stderr, "")
        lines = completed.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        value = json.loads(lines[0])
        self.assertEqual(
            value,
            {
                "activation": "unchanged",
                "configuration": "preserved",
                "desired_backend": "sqlite",
                "failure": "workspace_invalid",
                "installation": "not_run",
                "operation": "use",
                "outcome": "failed",
                "parity": "unverified",
                "provisioning": "not_run",
                "schema_version": 1,
            },
        )
        self.assertEqual(
            lines[0],
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        self.assertNotIn("private-secret", completed.stdout)
        self.assertEqual(
            tuple(path.read_bytes() for path in configuration_paths),
            configuration_before,
        )
        self.assertEqual(database.read_bytes(), database_before)
        self.assertFalse(activation.exists())


if __name__ == "__main__":
    unittest.main()
