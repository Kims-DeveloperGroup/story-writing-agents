"""Process-boundary acceptance tests for the safe Git adapter."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from story_writing_agents.git import (
    GitBaselineManifest,
    GitFailureCode,
    GitManifestEntry,
    GitOperationError,
    GitOperationKind,
    GitRepositoryAdapter,
    GitRepositoryState,
)
from story_writing_agents.git import adapter as adapter_module

from tests.git_contract_fixture import digest, exception_graph


class _ReadableBytes:
    def __init__(
        self, *chunks: object, close_failure: BaseException | None = None
    ) -> None:
        self.chunks = list(chunks)
        self.calls = 0
        self.close_failure = close_failure
        self.close_calls = 0
        self.closed = False

    def read(self, size: int) -> object:
        self.calls += 1
        if not self.chunks:
            return b""
        return self.chunks.pop(0)

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True
        if self.close_failure is not None:
            raise self.close_failure


class _WritableBytes:
    def __init__(self) -> None:
        self.data = bytearray()
        self.closed = False

    def write(self, value: bytes) -> int:
        self.data.extend(value)
        return len(value)

    def close(self) -> None:
        self.closed = True


class _FakeProcess:
    def __init__(
        self,
        *,
        stdout: tuple[object, ...] = (b"",),
        stderr: tuple[object, ...] = (b"",),
        wait_results: tuple[object, ...] = (0,),
        poll_results: tuple[int | None, ...] = (None,),
    ) -> None:
        self.stdout = _ReadableBytes(*stdout)
        self.stderr = _ReadableBytes(*stderr)
        self.stdin = _WritableBytes()
        self.wait_results = list(wait_results)
        self.poll_results = list(poll_results)
        self.returncode: int | None = None
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_timeouts: list[float] = []

    def wait(self, timeout: float) -> int:
        self.wait_timeouts.append(timeout)
        result = self.wait_results.pop(0) if self.wait_results else 0
        if isinstance(result, BaseException):
            raise result
        assert isinstance(result, int)
        self.returncode = result
        return result

    def poll(self) -> int | None:
        if self.poll_results:
            return self.poll_results.pop(0)
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1


class GitAdapterProcessTests(unittest.TestCase):
    """Prove exact argv/env/process ownership and fail-closed projections."""

    def setUp(self) -> None:
        super().setUp()
        self.temporary = tempfile.TemporaryDirectory(prefix="safe-git-process-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.adapter = GitRepositoryAdapter(self.root)
        self.identity = self.adapter._bind_root()  # noqa: SLF001

    def _run_with_process(
        self,
        process: _FakeProcess,
        *,
        arguments: tuple[str, ...] = ("status", "--porcelain=v2", "--"),
        stdin: bytes = b"",
        read_only: bool = True,
        identity_environment: dict[str, str] | None = None,
    ) -> tuple[object, mock.Mock]:
        popen = mock.Mock(return_value=process)
        with mock.patch.object(adapter_module.subprocess, "Popen", popen):
            result = self.adapter._run_git(  # noqa: SLF001
                arguments,
                identity=self.identity,
                stdin=stdin,
                read_only=read_only,
                identity_environment=identity_environment,
            )
        return result, popen

    def test_exact_argv_cwd_environment_stdin_and_shell_false(self) -> None:
        caller_text = b"prose; git push --all\n$HOME"
        identity_environment = {
            "GIT_AUTHOR_NAME": "Exact Author",
            "GIT_AUTHOR_EMAIL": "author@example.invalid",
            "GIT_COMMITTER_NAME": "Exact Committer",
            "GIT_COMMITTER_EMAIL": "committer@example.invalid",
        }
        process = _FakeProcess(stdout=(b"bounded", b""), stderr=(b"private", b""))
        hostile = {
            "PATH": os.environ.get("PATH", ""),
            "GIT_DIR": "/escape",
            "GIT_WORK_TREE": "/escape",
            "GIT_CONFIG_SYSTEM": "/hostile",
            "GIT_CONFIG_GLOBAL": "/hostile",
            "GIT_SSH_COMMAND": "touch escaped",
            **identity_environment,
        }
        with mock.patch.dict(os.environ, hostile, clear=True):
            result, popen = self._run_with_process(
                process,
                arguments=("hash-object", "-w", "--no-filters", "--stdin"),
                stdin=caller_text,
                identity_environment=identity_environment,
            )

        command = popen.call_args.args[0]
        options = popen.call_args.kwargs
        self.assertEqual(
            command,
            (
                "git",
                "--no-pager",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-c",
                "commit.gpgSign=false",
                "-c",
                "tag.gpgSign=false",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "diff.external=",
                "-c",
                "pager.status=false",
                "-c",
                "pager.diff=false",
                "-c",
                "pager.log=false",
                "hash-object",
                "-w",
                "--no-filters",
                "--stdin",
            ),
        )
        self.assertNotIn(caller_text.decode(), command)
        self.assertEqual(options["cwd"], os.fspath(self.root))
        self.assertIs(options["shell"], False)
        self.assertIs(options["stdin"], subprocess.PIPE)
        self.assertIs(options["stdout"], subprocess.PIPE)
        self.assertIs(options["stderr"], subprocess.PIPE)
        environment = options["env"]
        self.assertTrue(set(identity_environment).issubset(environment))
        for forbidden in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_CONFIG_SYSTEM",
            "GIT_CONFIG_GLOBAL",
            "GIT_SSH_COMMAND",
        ):
            self.assertNotIn(forbidden, environment)
        self.assertEqual(environment["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(environment["GIT_ASKPASS"], os.devnull)
        self.assertEqual(environment["GIT_PAGER"], "cat")
        self.assertEqual(environment["GIT_OPTIONAL_LOCKS"], "0")
        self.assertEqual(bytes(process.stdin.data), caller_text)
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)
        self.assertEqual(process.stdout.close_calls, 1)
        self.assertEqual(process.stderr.close_calls, 1)
        self.assertEqual(result.stdout, b"bounded")
        self.assertEqual(result.returncode, 0)

    def test_read_write_call_omits_optional_locks_and_empty_stdin_uses_devnull(
        self,
    ) -> None:
        process = _FakeProcess()
        _, popen = self._run_with_process(process, read_only=False)
        options = popen.call_args.kwargs
        self.assertNotIn("GIT_OPTIONAL_LOCKS", options["env"])
        self.assertIs(options["stdin"], subprocess.DEVNULL)
        self.assertFalse(process.stdin.closed)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

    def test_arguments_stdin_and_identity_environment_are_strictly_bounded(
        self,
    ) -> None:
        invalid_arguments = ((), ["status"], ("status", 7))
        for arguments in invalid_arguments:
            with self.subTest(arguments=repr(arguments)):
                with self.assertRaises(adapter_module._AdapterFailure) as caught:  # noqa: SLF001
                    self.adapter._run_git(arguments, identity=self.identity)  # type: ignore[arg-type]  # noqa: SLF001
                self.assertIs(caught.exception.code, GitFailureCode.INVALID_REQUEST)
        with self.assertRaises(adapter_module._AdapterFailure) as caught:  # noqa: SLF001
            self.adapter._run_git(  # noqa: SLF001
                ("status",),
                identity=self.identity,
                stdin=b"x" * (adapter_module._MAX_STDIN_BYTES + 1),  # noqa: SLF001
            )
        self.assertIs(caught.exception.code, GitFailureCode.INVALID_REQUEST)
        with self.assertRaises(adapter_module._AdapterFailure) as caught:  # noqa: SLF001
            self.adapter._run_git(  # noqa: SLF001
                ("status",),
                identity=self.identity,
                identity_environment={"GIT_AUTHOR_NAME": "partial"},
            )
        self.assertIs(caught.exception.code, GitFailureCode.IDENTITY_INVALID)

    def test_start_failures_map_to_content_free_public_errors(self) -> None:
        secret = "caller-secret-path-and-output"
        cases = (
            (FileNotFoundError(secret), GitFailureCode.EXECUTABLE_UNAVAILABLE),
            (OSError(secret), GitFailureCode.PROCESS_START_FAILED),
        )
        for failure, expected in cases:
            with self.subTest(code=expected.value):
                with mock.patch.object(
                    adapter_module.subprocess, "Popen", side_effect=failure
                ):
                    with self.assertRaises(GitOperationError) as caught:
                        self.adapter._public_call(  # noqa: SLF001
                            GitOperationKind.STATUS,
                            lambda: self.adapter._run_git(  # noqa: SLF001
                                ("status", "--"), identity=self.identity
                            ),
                        )
                error = caught.exception
                self.assertIs(error.code, expected)
                self.assertIs(error.operation, GitOperationKind.STATUS)
                self.assertEqual(exception_graph(error), (error,))
                self.assertNotIn(secret, str(error))
                self.assertNotIn(secret, repr(error))

    def test_nonzero_required_command_preserves_only_bounded_exit_code(self) -> None:
        process = _FakeProcess(stdout=(b"secret output", b""), wait_results=(17,))
        with mock.patch.object(
            adapter_module.subprocess, "Popen", return_value=process
        ):
            with self.assertRaises(GitOperationError) as caught:
                self.adapter._public_call(  # noqa: SLF001
                    GitOperationKind.STATUS,
                    lambda: self.adapter._required_git(  # noqa: SLF001
                        ("status", "--"), identity=self.identity
                    ),
                )
        self.assertIs(caught.exception.code, GitFailureCode.COMMAND_FAILED)
        self.assertEqual(caught.exception.exit_code, 17)
        self.assertNotIn("secret output", repr(caught.exception))
        self.assertEqual(exception_graph(caught.exception), (caught.exception,))
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

    def test_timeout_terminates_then_kills_and_returns_bounded_error(self) -> None:
        timeout = subprocess.TimeoutExpired(("git", "status"), 30)
        stop_timeout = subprocess.TimeoutExpired(("git", "status"), 2)
        process = _FakeProcess(
            wait_results=(timeout, stop_timeout, 0),
            poll_results=(None,),
        )
        with mock.patch.object(
            adapter_module.subprocess, "Popen", return_value=process
        ):
            with self.assertRaises(GitOperationError) as caught:
                self.adapter._public_call(  # noqa: SLF001
                    GitOperationKind.STATUS,
                    lambda: self.adapter._run_git(  # noqa: SLF001
                        ("status", "--"), identity=self.identity
                    ),
                )
        self.assertIs(caught.exception.code, GitFailureCode.PROCESS_TIMED_OUT)
        self.assertEqual(process.terminate_calls, 1)
        self.assertEqual(process.kill_calls, 1)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)
        self.assertEqual(
            process.wait_timeouts,
            [
                adapter_module._PROCESS_TIMEOUT_SECONDS,  # noqa: SLF001
                adapter_module._PROCESS_STOP_TIMEOUT_SECONDS,  # noqa: SLF001
                adapter_module._PROCESS_STOP_TIMEOUT_SECONDS,  # noqa: SLF001
            ],
        )

    def test_oversized_or_invalid_streams_fail_without_exposing_bytes(self) -> None:
        oversized = b"s" * (adapter_module._MAX_STDERR_BYTES + 1)  # noqa: SLF001
        cases = (
            (
                _FakeProcess(stderr=(oversized, b"")),
                GitFailureCode.PROCESS_OUTPUT_EXCEEDED,
            ),
            (
                _FakeProcess(stdout=("not-bytes", b"")),
                GitFailureCode.PROCESS_START_FAILED,
            ),
            (
                _FakeProcess(stdout=(RuntimeError("not read"),)),
                GitFailureCode.PROCESS_START_FAILED,
            ),
        )
        for process, expected in cases:
            with self.subTest(code=expected.value):
                if isinstance(process.stdout.chunks[0], BaseException):
                    original = process.stdout.read

                    def fail_read(size: int) -> object:
                        raise RuntimeError("private stream failure")

                    process.stdout.read = fail_read  # type: ignore[method-assign]
                    self.addCleanup(setattr, process.stdout, "read", original)
                with mock.patch.object(
                    adapter_module.subprocess, "Popen", return_value=process
                ):
                    with self.assertRaises(GitOperationError) as caught:
                        self.adapter._public_call(  # noqa: SLF001
                            GitOperationKind.STATUS,
                            lambda: self.adapter._run_git(  # noqa: SLF001
                                ("status", "--"), identity=self.identity
                            ),
                        )
                self.assertIs(caught.exception.code, expected)
                self.assertEqual(exception_graph(caught.exception), (caught.exception,))
                self.assertNotIn("private", repr(caught.exception))
                self.assertTrue(process.stdout.closed)
                self.assertTrue(process.stderr.closed)

    def test_keyboard_interrupt_owns_process_and_maps_all_recovery_states(self) -> None:
        process = _FakeProcess(
            wait_results=(KeyboardInterrupt(), 0),
            poll_results=(None,),
        )
        with mock.patch.object(
            adapter_module.subprocess, "Popen", return_value=process
        ):
            with self.assertRaises(KeyboardInterrupt):
                self.adapter._run_git(  # noqa: SLF001
                    ("status", "--"), identity=self.identity
                )
        self.assertEqual(process.terminate_calls, 1)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

        manifest = GitBaselineManifest((GitManifestEntry("outline.md", digest(b"x")),))
        for state in GitRepositoryState:
            with self.subTest(state=state.value):
                with mock.patch.object(
                    GitRepositoryAdapter,
                    "_recover_state",
                    return_value=state,
                ):
                    with self.assertRaises(GitOperationError) as caught:
                        self.adapter._public_call(  # noqa: SLF001
                            GitOperationKind.CREATE_BASELINE,
                            lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
                            recovery_manifest=manifest,
                        )
                self.assertIs(caught.exception.code, GitFailureCode.CANCELLED)
                self.assertIs(caught.exception.repository_state, state)
                self.assertEqual(exception_graph(caught.exception), (caught.exception,))

    def test_root_identity_change_is_rejected_before_process_start(self) -> None:
        with mock.patch.object(adapter_module.subprocess, "Popen") as popen:
            with self.assertRaises(adapter_module._AdapterFailure) as caught:  # noqa: SLF001
                self.adapter._run_git(  # noqa: SLF001
                    ("status", "--"), identity=(self.identity[0], self.identity[1] + 1)
                )
        self.assertIs(caught.exception.code, GitFailureCode.UNSAFE_ROOT)
        popen.assert_not_called()

    def test_partial_process_start_and_close_failure_are_owned_and_fail_closed(
        self,
    ) -> None:
        partial = _FakeProcess()
        partial.stderr = None  # type: ignore[assignment]
        with mock.patch.object(
            adapter_module.subprocess, "Popen", return_value=partial
        ):
            with self.assertRaises(GitOperationError) as caught:
                self.adapter._public_call(  # noqa: SLF001
                    GitOperationKind.STATUS,
                    lambda: self.adapter._run_git(  # noqa: SLF001
                        ("status", "--"), identity=self.identity, stdin=b"input"
                    ),
                )
        self.assertIs(caught.exception.code, GitFailureCode.PROCESS_START_FAILED)
        self.assertTrue(partial.stdin.closed)
        self.assertTrue(partial.stdout.closed)
        self.assertEqual(partial.terminate_calls, 1)

        close_failure = _FakeProcess()
        close_failure.stdout.close_failure = OSError("private close error")
        with mock.patch.object(
            adapter_module.subprocess,
            "Popen",
            return_value=close_failure,
        ):
            with self.assertRaises(GitOperationError) as caught:
                self.adapter._public_call(  # noqa: SLF001
                    GitOperationKind.STATUS,
                    lambda: self.adapter._run_git(  # noqa: SLF001
                        ("status", "--"), identity=self.identity
                    ),
                )
        self.assertIs(caught.exception.code, GitFailureCode.PROCESS_START_FAILED)
        self.assertEqual(exception_graph(caught.exception), (caught.exception,))
        self.assertTrue(close_failure.stdout.closed)
        self.assertTrue(close_failure.stderr.closed)


if __name__ == "__main__":
    unittest.main()
