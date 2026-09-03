"""Direct contract coverage for the package-internal Git process owner."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import FrozenInstanceError, fields, is_dataclass
from pathlib import Path
from unittest import mock

from story_writing_agents.git import process as process_module
from story_writing_agents.git.contracts import GitFailureCode
from story_writing_agents.git.process import (
    GitProcessFailure,
    GitProcessResult,
    GitProcessRunner,
)


class GitProcessContractTests(unittest.TestCase):
    """Prove the internal runner exposes only bounded fixed-process values."""

    def test_exports_result_and_failure_values_are_exact(self) -> None:
        self.assertEqual(
            tuple(process_module.__all__),
            (
                "GitAlternateIndex",
                "GitProcessFailure",
                "GitProcessProfile",
                "GitProcessResult",
                "GitProcessRunner",
            ),
        )
        result = GitProcessResult(b"bounded", 7)
        self.assertTrue(is_dataclass(result))
        self.assertTrue(hasattr(result, "__slots__"))
        self.assertEqual(
            tuple(field.name for field in fields(result)), ("stdout", "returncode")
        )
        with self.assertRaises(FrozenInstanceError):
            result.returncode = 0  # type: ignore[misc]
        failure = GitProcessFailure(GitFailureCode.COMMAND_FAILED, exit_code=7)
        self.assertIs(failure.code, GitFailureCode.COMMAND_FAILED)
        self.assertEqual(failure.exit_code, 7)
        self.assertNotIn("bounded", repr(failure))

    def test_construction_is_no_io_and_rejects_nonabsolute_or_inexact_types(
        self,
    ) -> None:
        root = Path("/not/read/during/construction")
        with (
            mock.patch.object(process_module.os, "lstat") as lstat,
            mock.patch.object(process_module.subprocess, "Popen") as popen,
        ):
            runner = GitProcessRunner(root)
        self.assertEqual(runner._root, root)  # noqa: SLF001
        lstat.assert_not_called()
        popen.assert_not_called()
        for invalid in (Path("relative"), "/absolute-but-not-path", 7):
            with self.subTest(value=repr(invalid)):
                with self.assertRaises(GitProcessFailure) as caught:
                    GitProcessRunner(invalid)  # type: ignore[arg-type]
                self.assertIs(caught.exception.code, GitFailureCode.INVALID_REQUEST)

    def test_root_identity_is_checked_before_process_and_after_completion(self) -> None:
        with tempfile.TemporaryDirectory(prefix="git-process-root-") as temporary:
            root = Path(temporary).resolve()
            identity = (root.stat().st_dev, root.stat().st_ino)
            runner = GitProcessRunner(root)
            with mock.patch.object(process_module.subprocess, "Popen") as popen:
                with self.assertRaises(GitProcessFailure) as caught:
                    runner.run(
                        ("status", "--"),
                        expected_identity=(identity[0], identity[1] + 1),
                    )
            self.assertIs(caught.exception.code, GitFailureCode.UNSAFE_ROOT)
            popen.assert_not_called()

            completed = mock.Mock()
            completed.stdin = None
            completed.stdout = mock.Mock()
            completed.stderr = mock.Mock()
            completed.stdout.read.side_effect = (b"",)
            completed.stderr.read.side_effect = (b"",)
            completed.wait.return_value = 0
            completed.poll.return_value = 0

            real_verify = runner._verify_root  # noqa: SLF001
            calls = 0

            def substitute_after_start(expected: tuple[int, int]) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise GitProcessFailure(GitFailureCode.UNSAFE_ROOT)
                real_verify(expected)

            with (
                mock.patch.object(
                    process_module.subprocess, "Popen", return_value=completed
                ),
                mock.patch.object(
                    GitProcessRunner,
                    "_verify_root",
                    side_effect=substitute_after_start,
                ),
                self.assertRaises(GitProcessFailure) as caught,
            ):
                runner.run(("status", "--"), expected_identity=identity)
            self.assertIs(caught.exception.code, GitFailureCode.UNSAFE_ROOT)
            self.assertTrue(completed.stdout.close.called)
            self.assertTrue(completed.stderr.close.called)


if __name__ == "__main__":
    unittest.main()
