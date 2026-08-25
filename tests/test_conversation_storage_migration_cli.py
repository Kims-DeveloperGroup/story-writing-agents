"""Provider-free command-line tests for explicit conversation migration."""

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
from story_writing_agents.conversation_storage import ConversationStorageBackend

from tests.conversation_storage_migration_fixture import (
    import_backend,
    migration_workspace,
    representative_archive,
)


class ConversationStorageMigrationCliTests(unittest.TestCase):
    def test_storage_grammar_is_exact_and_invalid_syntax_precedes_io(self) -> None:
        parser = build_parser()
        self.assertEqual(
            vars(
                parser.parse_args(
                    [
                        "storage",
                        "migrate",
                        "--from",
                        "filesystem",
                        "--to",
                        "sqlite",
                        "--workspace",
                        "/story",
                        "--dry-run",
                    ]
                )
            ),
            {
                "command": "storage",
                "storage_action": "migrate",
                "source_backend": "filesystem",
                "destination_backend": "sqlite",
                "workspace": "/story",
                "dry_run": True,
            },
        )
        self.assertEqual(
            vars(
                parser.parse_args(
                    [
                        "storage",
                        "verify",
                        "--from",
                        "sqlite",
                        "--to",
                        "filesystem",
                    ]
                )
            ),
            {
                "command": "storage",
                "storage_action": "verify",
                "source_backend": "sqlite",
                "destination_backend": "filesystem",
                "workspace": None,
            },
        )
        invalid = (
            ["storage"],
            ["storage", "migrate", "--from", "filesystem"],
            [
                "storage",
                "migrate",
                "--from",
                "mysql",
                "--to",
                "sqlite",
            ],
            [
                "storage",
                "verify",
                "--from",
                "filesystem",
                "--to",
                "sqlite",
                "--dry-run",
            ],
        )
        for arguments in invalid:
            with (
                self.subTest(arguments=arguments),
                mock.patch.object(
                    cli_module,
                    "resolve_workspace",
                    side_effect=AssertionError("workspace access"),
                ) as resolve,
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as caught,
            ):
                main(arguments)
            self.assertEqual(caught.exception.code, 2)
            resolve.assert_not_called()

    def test_early_dispatch_is_compact_provider_free_and_debug_free(self) -> None:
        workspace = migration_workspace(self)
        import_backend(
            workspace,
            ConversationStorageBackend.FILESYSTEM,
            representative_archive(),
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(
                cli_module, "resolve_workspace", return_value=workspace
            ) as resolve,
            mock.patch.object(
                cli_module,
                "_create_cli_debug_log",
                side_effect=AssertionError("debug log created"),
            ) as debug_log,
            mock.patch.object(
                cli_module,
                "load_cli_context",
                side_effect=AssertionError("CLI context loaded"),
            ) as cli_context,
            mock.patch.object(
                cli_module,
                "AgentBackend",
                side_effect=AssertionError("provider backend created"),
            ) as backend,
            mock.patch.object(
                cli_module,
                "StoryRuntime",
                side_effect=AssertionError("runtime created"),
            ) as runtime,
            mock.patch.object(
                cli_module,
                "ConfigManager",
                side_effect=AssertionError("settings configuration created"),
            ) as configuration,
            mock.patch.object(
                cli_module,
                "InteractiveController",
                side_effect=AssertionError("session controller created"),
            ) as controller,
            mock.patch.object(
                cli_module,
                "read_draft_file",
                side_effect=AssertionError("artifact input read"),
            ) as artifact_input,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = main(
                [
                    "storage",
                    "migrate",
                    "--from",
                    "filesystem",
                    "--to",
                    "sqlite",
                    "--dry-run",
                ]
            )

        self.assertEqual(exit_code, 0)
        resolve.assert_called_once_with(None)
        debug_log.assert_not_called()
        cli_context.assert_not_called()
        backend.assert_not_called()
        runtime.assert_not_called()
        configuration.assert_not_called()
        controller.assert_not_called()
        artifact_input.assert_not_called()
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(stdout.getvalue().count("\n"), 1)
        raw = stdout.getvalue().removesuffix("\n")
        value = json.loads(raw)
        self.assertEqual(value["outcome"], "work_required")
        self.assertEqual(value["counts"]["create"], 3)
        self.assertEqual(
            raw,
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        self.assertFalse(workspace.path("debug_logs/runtime.jsonl").exists())

    def test_real_subprocess_exits_zero_one_two_and_resolves_current_workspace(
        self,
    ) -> None:
        workspace = migration_workspace(self)
        import_backend(
            workspace,
            ConversationStorageBackend.FILESYSTEM,
            representative_archive(),
        )
        repository = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.fspath(repository / "src")

        def invoke(*arguments: str) -> tuple[subprocess.CompletedProcess[str], dict]:
            completed = subprocess.run(
                [sys.executable, "-m", "story_writing_agents", *arguments],
                cwd=workspace.root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            self.assertEqual(completed.stderr, "")
            self.assertEqual(completed.stdout.count("\n"), 1)
            value = json.loads(completed.stdout)
            self.assertEqual(
                completed.stdout,
                json.dumps(
                    value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n",
            )
            return completed, value

        mismatch, mismatch_value = invoke(
            "storage",
            "verify",
            "--from",
            "filesystem",
            "--to",
            "sqlite",
        )
        self.assertEqual(mismatch.returncode, 1)
        self.assertEqual(mismatch_value["failure"], "parity_mismatch")

        dry_run, dry_value = invoke(
            "storage",
            "migrate",
            "--from",
            "filesystem",
            "--to",
            "sqlite",
            "--dry-run",
        )
        self.assertEqual(dry_run.returncode, 0)
        self.assertEqual(dry_value["outcome"], "work_required")

        migrated, migrated_value = invoke(
            "storage",
            "migrate",
            "--from",
            "filesystem",
            "--to",
            "sqlite",
        )
        self.assertEqual(migrated.returncode, 0)
        self.assertEqual(migrated_value["parity"], "verified")

        verified, verified_value = invoke(
            "storage",
            "verify",
            "--from",
            "filesystem",
            "--to",
            "sqlite",
        )
        self.assertEqual(verified.returncode, 0)
        self.assertEqual(verified_value["parity"], "verified")

        invalid, invalid_value = invoke(
            "storage",
            "migrate",
            "--from",
            "filesystem",
            "--to",
            "filesystem",
        )
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(invalid_value["failure"], "invalid_request")
        self.assertFalse(workspace.path("debug_logs/runtime.jsonl").exists())

    def test_missing_workspace_is_one_content_free_schema_v1_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="missing-migration-workspace-") as root:
            missing = Path(root) / "missing"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                exit_code = main(
                    [
                        "storage",
                        "verify",
                        "--from",
                        "filesystem",
                        "--to",
                        "sqlite",
                        "--workspace",
                        os.fspath(missing),
                    ]
                )
        self.assertEqual(exit_code, 2)
        self.assertEqual(stderr.getvalue(), "")
        value = json.loads(stdout.getvalue())
        self.assertEqual(value["schema_version"], 1)
        self.assertEqual(value["failure"], "workspace_invalid")
        self.assertNotIn(os.fspath(missing), stdout.getvalue())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
