"""Process-boundary acceptance tests for the safe Git adapter."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from threading import Event, Thread
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
from story_writing_agents.git import process as process_module
from story_writing_agents.git import session_resources as resources_module

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
        self.wait_timeouts: list[float | None] = []
        self.wait_gate: Event | None = None

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeouts.append(timeout)
        if timeout is None and self.wait_gate is not None:
            self.wait_gate.wait()
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
        if self.wait_gate is not None:
            self.wait_gate.set()


class GitAdapterProcessTests(unittest.TestCase):
    """Prove exact argv/env/process ownership and fail-closed projections."""

    def setUp(self) -> None:
        super().setUp()
        self.temporary = tempfile.TemporaryDirectory(prefix="safe-git-process-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.adapter = GitRepositoryAdapter(self.root)
        self.identity = self.adapter._bind_root()  # noqa: SLF001
        self.threads: list[Thread] = []
        self.thread_start_failure: int | None = None
        def make_thread(*args: object, **kwargs: object) -> Thread:
            thread = Thread(*args, **kwargs)
            self.threads.append(thread)
            if len(self.threads) == self.thread_start_failure:
                thread.start = mock.Mock(side_effect=RuntimeError("private thread start"))
            return thread
        thread_patch = mock.patch.object(process_module.threading, "Thread", side_effect=make_thread)
        thread_patch.start()
        self.addCleanup(thread_patch.stop)
        self.addCleanup(lambda: self.assertFalse(any(thread.is_alive() for thread in self.threads)))

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
        with mock.patch.object(process_module.subprocess, "Popen", popen):
            result = self.adapter._run_git(  # noqa: SLF001
                arguments,
                identity=self.identity,
                stdin=stdin,
                read_only=read_only,
                identity_environment=identity_environment,
            )
        return result, popen

    def _pipe_process(self, stdout=b"bounded", stderr=b"private", *, hold=False):
        process = _FakeProcess()
        for name, payload in (("stdout", stdout), ("stderr", stderr)):
            reader, writer = os.pipe()
            stream = os.fdopen(reader, "rb", buffering=0)
            setattr(process, name, stream)
            self.addCleanup(stream.close)
            os.write(writer, payload)
            if hold:
                self.addCleanup(os.close, writer)
            else:
                os.close(writer)
        return process

    def test_exact_argv_cwd_environment_stdin_and_shell_false(self) -> None:
        caller_text = b'prose; git push --all\n$HOME'
        identity_environment = {'GIT_AUTHOR_NAME': 'Exact Author', 'GIT_AUTHOR_EMAIL': 'author@example.invalid', 'GIT_COMMITTER_NAME': 'Exact Committer', 'GIT_COMMITTER_EMAIL': 'committer@example.invalid'}
        process = _FakeProcess(stdout=(b'bounded', b''), stderr=(b'private', b''))
        hostile = {'PATH': os.environ.get('PATH', ''), 'GIT_DIR': '/escape', 'GIT_WORK_TREE': '/escape', 'GIT_CONFIG_SYSTEM': '/hostile', 'GIT_CONFIG_GLOBAL': '/hostile', 'GIT_SSH_COMMAND': 'touch escaped', **identity_environment}
        with mock.patch.dict(os.environ, hostile, clear=True):
            result, popen = self._run_with_process(process, arguments=('hash-object', '-w', '--no-filters', '--stdin'), stdin=caller_text, identity_environment=identity_environment)
        command = popen.call_args.args[0]
        options = popen.call_args.kwargs
        self.assertEqual(command, ('git', '--no-pager', '-c', f'core.hooksPath={os.devnull}', '-c', 'commit.gpgSign=false', '-c', 'tag.gpgSign=false', '-c', 'core.fsmonitor=false', '-c', 'diff.external=', '-c', 'pager.status=false', '-c', 'pager.diff=false', '-c', 'pager.log=false', 'hash-object', '-w', '--no-filters', '--stdin'))
        self.assertNotIn(caller_text.decode(), command)
        self.assertEqual(options['cwd'], os.fspath(self.root))
        self.assertIs(options['shell'], False)
        self.assertIs(options['stdin'], subprocess.PIPE)
        self.assertIs(options['stdout'], subprocess.PIPE)
        self.assertIs(options['stderr'], subprocess.PIPE)
        environment = options['env']
        self.assertTrue(set(identity_environment).issubset(environment))
        for forbidden in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_CONFIG_SYSTEM', 'GIT_CONFIG_GLOBAL', 'GIT_SSH_COMMAND'):
            self.assertNotIn(forbidden, environment)
        self.assertEqual(environment['GIT_TERMINAL_PROMPT'], '0')
        self.assertEqual(environment['GIT_ASKPASS'], os.devnull)
        self.assertEqual(environment['GIT_PAGER'], 'cat')
        self.assertEqual(environment['GIT_OPTIONAL_LOCKS'], '0')
        self.assertEqual(bytes(process.stdin.data), caller_text)
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)
        self.assertEqual(process.wait_timeouts, [None])
        self.assertEqual(process.stdout.close_calls, 1)
        self.assertEqual(process.stderr.close_calls, 1)
        self.assertEqual(result.stdout, b'bounded')
        self.assertEqual(result.returncode, 0)

    def test_read_write_call_omits_optional_locks_and_empty_stdin_uses_devnull(self) -> None:
        process = _FakeProcess()
        _, popen = self._run_with_process(process, read_only=False)
        options = popen.call_args.kwargs
        self.assertNotIn('GIT_OPTIONAL_LOCKS', options['env'])
        self.assertIs(options['stdin'], subprocess.DEVNULL)
        self.assertFalse(process.stdin.closed)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

        if os.name != "posix":
            return
        process = self._pipe_process(b"x" * 96, b"y" * 64)
        descriptors = (process.stdout.fileno(), process.stderr.fileno())
        self.assertEqual(process_module._read_only_pipe_descriptors(process.stdout, process.stderr), descriptors)
        for first, second in ((process.stdout, process.stdout), (mock.Mock(fileno=lambda: True), process.stderr), (mock.Mock(), process.stderr)):
            self.assertIsNone(process_module._read_only_pipe_descriptors(first, second))
        with tempfile.TemporaryFile() as regular:
            self.assertIsNone(process_module._read_only_pipe_descriptors(regular, process.stderr))
        with mock.patch.object(process_module.os, "name", "nt"):
            self.assertIsNone(process_module._read_only_pipe_descriptors(process.stdout, process.stderr))
        before = len(self.threads)
        with mock.patch.object(process_module, "_READ_CHUNK_BYTES", 16), mock.patch.object(process_module.os, "read", wraps=os.read) as reads:
            result, _ = self._run_with_process(process)
        self.assertEqual(result.stdout, b"x" * 96)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(self.threads), before)
        self.assertEqual({call.args[0] for call in reads.call_args_list[:2]}, set(descriptors))
        self.assertTrue(all(call.args[1] == 16 for call in reads.call_args_list))
        self.assertEqual(len(process.wait_timeouts), 1)
        self.assertTrue(0 < process.wait_timeouts[0] <= process_module._PROCESS_TIMEOUT_SECONDS)
        self.assertTrue(process.stdout.closed and process.stderr.closed)
        for read_only, stdin in ((False, b""), (True, b"payload")):
            process = self._pipe_process()
            before = len(self.threads)
            result, _ = self._run_with_process(process, read_only=read_only, stdin=stdin)
            self.assertEqual((result.stdout, len(self.threads) - before, process.wait_timeouts), (b"bounded", 3, [None]))
            self.assertEqual(bytes(process.stdin.data), stdin)

        process = self._pipe_process(b"output", b"error")
        reader, writer = os.pipe()
        process.stdin = os.fdopen(writer, "wb", buffering=0)
        self.addCleanup(process.stdin.close)
        self.addCleanup(os.close, reader)
        descriptors = process_module._read_only_pipe_descriptors(process.stdout, process.stderr, process.stdin)
        self.assertEqual(descriptors, (process.stdout.fileno(), process.stderr.fileno(), process.stdin.fileno()))
        self.assertIsNone(process_module._read_only_pipe_descriptors(process.stdout, process.stderr, process.stdout))
        real_write = os.write
        writes = []
        def partial_write(descriptor, content):
            writes.append(len(content))
            if len(writes) == 1:
                raise BlockingIOError("retry nonblocking write")
            return real_write(descriptor, content[:2])
        before = len(self.threads)
        with mock.patch.object(process_module.os, "write", side_effect=partial_write):
            result, _popen = self._run_with_process(process, stdin=b"payload")
        self.assertEqual((result.stdout, result.returncode), (b"output", 0))
        self.assertEqual(os.read(reader, 7), b"payload")
        self.assertEqual(len(self.threads), before)
        self.assertGreater(len(writes), 2)
        self.assertTrue(process.stdin.closed and process.stdout.closed and process.stderr.closed)

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
                stdin=b"x" * (process_module._MAX_STDIN_BYTES + 1),  # noqa: SLF001
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
        secret = 'caller-secret-path-and-output'
        cases = ((FileNotFoundError(secret), GitFailureCode.EXECUTABLE_UNAVAILABLE), (OSError(secret), GitFailureCode.PROCESS_START_FAILED))
        for failure, expected in cases:
            with self.subTest(code=expected.value):
                with mock.patch.object(process_module.subprocess, 'Popen', side_effect=failure):
                    with self.assertRaises(GitOperationError) as caught:
                        self.adapter._public_call(GitOperationKind.STATUS, lambda: self.adapter._run_git(('status', '--'), identity=self.identity))
                error = caught.exception
                self.assertIs(error.code, expected)
                self.assertIs(error.operation, GitOperationKind.STATUS)
                self.assertEqual(exception_graph(error), (error,))
                self.assertNotIn(secret, str(error))
                self.assertNotIn(secret, repr(error))

    def test_nonzero_required_command_preserves_only_bounded_exit_code(self) -> None:
        process = _FakeProcess(stdout=(b'secret output', b''), wait_results=(17,))
        with mock.patch.object(process_module.subprocess, 'Popen', return_value=process):
            with self.assertRaises(GitOperationError) as caught:
                self.adapter._public_call(GitOperationKind.STATUS, lambda: self.adapter._required_git(('status', '--'), identity=self.identity))
        self.assertIs(caught.exception.code, GitFailureCode.COMMAND_FAILED)
        self.assertEqual(caught.exception.exit_code, 17)
        self.assertNotIn('secret output', repr(caught.exception))
        self.assertEqual(exception_graph(caught.exception), (caught.exception,))
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

    def test_timeout_terminates_then_kills_and_returns_bounded_error(self) -> None:
        stop_timeout = subprocess.TimeoutExpired(('git', 'status'), 2)
        process = _FakeProcess(wait_results=(stop_timeout, 0), poll_results=(None,))
        process.wait_gate = Event()
        with mock.patch.object(process_module, '_PROCESS_TIMEOUT_SECONDS', 0.01), mock.patch.object(process_module.subprocess, 'Popen', return_value=process):
            with self.assertRaises(GitOperationError) as caught:
                self.adapter._public_call(GitOperationKind.STATUS, lambda: self.adapter._run_git(('status', '--'), identity=self.identity))
        self.assertIs(caught.exception.code, GitFailureCode.PROCESS_TIMED_OUT)
        self.assertEqual(process.terminate_calls, 1)
        self.assertEqual(process.kill_calls, 1)
        self.assertTrue(process.wait_gate.is_set())
        self.assertEqual(process.returncode, 0)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)
        self.assertEqual(process.wait_timeouts, [None, process_module._PROCESS_STOP_TIMEOUT_SECONDS, process_module._PROCESS_STOP_TIMEOUT_SECONDS])

        if os.name != "posix":
            return
        for hold, exited in ((True, False), (True, True), (False, False)):
            process = self._pipe_process(hold=hold)
            if exited:
                process.poll_results, process.returncode = [0], 0
            process.wait_results = [stop_timeout, 0] if hold else [stop_timeout, stop_timeout, 0]
            with mock.patch.object(process_module.time, "monotonic", side_effect=(0, 30) if hold else (0, 1, 2, 3)), self.assertRaises(adapter_module._AdapterFailure) as caught:
                self._run_with_process(process)
            self.assertIs(caught.exception.code, GitFailureCode.PROCESS_TIMED_OUT)
            self.assertEqual((process.terminate_calls, process.kill_calls, process.returncode), (0, 0, 0) if exited else (1, 1, 0))
            self.assertTrue(process.stdout.closed and process.stderr.closed)
            self.assertEqual(process.wait_timeouts[-2:], [] if exited else [process_module._PROCESS_STOP_TIMEOUT_SECONDS] * 2)
            if not hold:
                self.assertTrue(0 < process.wait_timeouts[0] < process_module._PROCESS_TIMEOUT_SECONDS)

        process = self._pipe_process(hold=True)
        reader, writer = os.pipe()
        process.stdin = os.fdopen(writer, "wb", buffering=0)
        self.addCleanup(process.stdin.close)
        self.addCleanup(os.close, reader)
        process.wait_results = [stop_timeout, 0]
        with mock.patch.object(process_module.time, "monotonic", side_effect=(0, 30)), self.assertRaises(adapter_module._AdapterFailure) as caught:
            self._run_with_process(process, stdin=b"input")
        self.assertIs(caught.exception.code, GitFailureCode.PROCESS_TIMED_OUT)
        self.assertEqual((process.terminate_calls, process.kill_calls, process.returncode), (1, 1, 0))
        self.assertTrue(process.stdin.closed and process.stdout.closed and process.stderr.closed)

    def test_oversized_or_invalid_streams_fail_without_exposing_bytes(self) -> None:
        if os.name == "posix":
            for close_failure in (False, True):
                process = self._pipe_process(b"123456789", b"stderr")
                reader, writer = os.pipe()
                process.stdin = os.fdopen(writer, "wb", buffering=0)
                self.addCleanup(process.stdin.close)
                self.addCleanup(os.close, reader)
                close_stdout = process.stdout.close
                self.addCleanup(close_stdout)

                def close_stream():
                    close_stdout()
                    if close_failure:
                        raise OSError("private stream close")

                with mock.patch.object(process.stdout, "close", side_effect=close_stream), mock.patch.object(process_module, "_MAX_STDOUT_BYTES", 8), self.assertRaises(adapter_module._AdapterFailure) as caught:
                    self._run_with_process(process, stdin=b"input")
                self.assertIs(caught.exception.code, GitFailureCode.PROCESS_START_FAILED if close_failure else GitFailureCode.PROCESS_OUTPUT_EXCEEDED)
                self.assertEqual((process.terminate_calls, process.kill_calls, process.returncode), (1, 0, 0))
                self.assertTrue(process.stdin.closed and process.stdout.closed and process.stderr.closed)
        oversized = b"s" * (process_module._MAX_STDERR_BYTES + 1)  # noqa: SLF001
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
        a, b = b"a" * 40, b"b" * 40
        row_a = b"100644 blob " + a + b"       1\tfixture/a\0"
        row_b = b"100644 blob " + b + b"       1\tfixture/b\0"
        tree = row_a + row_b
        frame_a, frame_b = a + b" blob 1\nx\n", b + b" blob 1\ny\n"
        frames = frame_a + frame_b
        invalid_groups = (
            ((tree, a + b" missing\n"), (tree, b"c" * 40 + b" blob 1\nx\n" + frame_b),
             (tree, frame_b + frame_a), (tree, frame_a.replace(b" blob ", b" tree ") + frame_b)),
            tuple((tree, a + b" blob " + size + b"\nx\n" + frame_b)
                  for size in (b"-1", b"01", b"x", b"99999999999999999999", b"2")) +
            ((tree, a + b" blob"), (row_a.replace(b" 1\t", b" 5\t"), a + b" blob 5\nx"),
             (tree, a + b" blob 1\nx" + frame_b), (tree, "PRIVATE_NON_BYTES")),
            ((tree, frames + b"PRIVATE_TRAILING"), (row_a + row_a, frame_a + frame_a),
             (tree[:-1], frames), (b"PRIVATE_MALFORMED\0", frames),
             (row_a.replace(b" 1\t", b" -1\t"), frames),
             (row_a.replace(b" 1\t", b" 01\t"), frames),
             (row_a.replace(b"100644", b"100755"), frames),
             (row_a.replace(b"fixture/a", b"\xff"), frames)),
        )
        repository = object.__new__(resources_module._Repository)
        for case_number, (process, expected) in enumerate(cases):
            with self.subTest(code=expected.value):
                if isinstance(process.stdout.chunks[0], BaseException):
                    original = process.stdout.read

                    def fail_read(size: int) -> object:
                        raise RuntimeError("private stream failure")

                    process.stdout.read = fail_read  # type: ignore[method-assign]
                    self.addCleanup(setattr, process.stdout, "read", original)
                with mock.patch.object(
                    process_module.subprocess, "Popen", return_value=process
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
                for inventory, output in invalid_groups[case_number]:
                    with mock.patch.object(resources_module._Repository, "_required", side_effect=(
                        process_module.GitProcessResult(inventory, 0),
                        process_module.GitProcessResult(output, 0),
                    )), self.assertRaises(resources_module.GitSessionResourceError) as caught:
                        repository._tree_digests("0" * 40)
                    self.assertEqual(exception_graph(caught.exception), (caught.exception,))
                    self.assertIs(caught.exception.code, resources_module.GitSessionFailureCode.RECOVERY_REQUIRED)
                    self.assertNotIn("PRIVATE", repr(caught.exception))

        if os.name == "posix":
            for stdout, stderr in ((b"x" * 80, b""), (b"", b"y" * 48)):
                process = self._pipe_process(stdout, stderr)
                with (
                    mock.patch.object(process_module, "_MAX_STDOUT_BYTES", 64),
                    mock.patch.object(process_module, "_MAX_STDERR_BYTES", 32),
                    mock.patch.object(process_module, "_READ_CHUNK_BYTES", 16),
                    mock.patch.object(process_module.os, "read", wraps=os.read) as reads,
                    self.assertRaises(GitOperationError) as caught,
                ):
                    self.adapter._public_call(GitOperationKind.STATUS, lambda: self._run_with_process(process))
                self.assertIs(caught.exception.code, GitFailureCode.PROCESS_OUTPUT_EXCEEDED)
                self.assertEqual(process.returncode, 0)
                self.assertGreaterEqual(reads.call_count, 4)
                self.assertTrue(process.stdout.closed and process.stderr.closed)
                self.assertEqual(exception_graph(caught.exception), (caught.exception,))

    def test_keyboard_interrupt_owns_process_and_maps_all_recovery_states(self) -> None:
        process = _FakeProcess(wait_results=(KeyboardInterrupt(), 0), poll_results=(None,))
        with mock.patch.object(process_module.subprocess, 'Popen', return_value=process):
            with self.assertRaises(KeyboardInterrupt):
                self.adapter._run_git(('status', '--'), identity=self.identity)
        self.assertEqual(process.terminate_calls, 1)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)
        main_process = _FakeProcess()
        original_join = Thread.join
        interrupted = False

        def join(thread: Thread, timeout: float | None=None) -> None:
            nonlocal interrupted
            if timeout == process_module._PROCESS_TIMEOUT_SECONDS and (not interrupted):
                interrupted = True
                raise KeyboardInterrupt()
            original_join(thread, timeout)
        with mock.patch.object(Thread, 'join', join), mock.patch.object(process_module.subprocess, 'Popen', return_value=main_process):
            with self.assertRaises(KeyboardInterrupt):
                self.adapter._run_git(('status', '--'), identity=self.identity)
        self.assertTrue(interrupted)
        self.assertEqual(main_process.terminate_calls, 1)
        self.assertTrue(main_process.stdout.closed and main_process.stderr.closed)
        manifest = GitBaselineManifest((GitManifestEntry('outline.md', digest(b'x')),))
        for state in GitRepositoryState:
            with self.subTest(state=state.value):
                with mock.patch.object(GitRepositoryAdapter, '_recover_state', return_value=state):
                    with self.assertRaises(GitOperationError) as caught:
                        self.adapter._public_call(GitOperationKind.CREATE_BASELINE, lambda: (_ for _ in ()).throw(KeyboardInterrupt()), recovery_manifest=manifest)
                self.assertIs(caught.exception.code, GitFailureCode.CANCELLED)
                self.assertIs(caught.exception.repository_state, state)
                self.assertEqual(exception_graph(caught.exception), (caught.exception,))

        if os.name == "posix":
            process = self._pipe_process()
            with mock.patch.object(process_module.os, "read", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
                self._run_with_process(process)
            self.assertEqual(process.terminate_calls, 1)
            self.assertTrue(process.stdout.closed and process.stderr.closed)
            process = self._pipe_process()
            with mock.patch.object(process_module, "_read_only_pipe_descriptors", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
                self._run_with_process(process)
            self.assertEqual(process.terminate_calls, 1)
            self.assertTrue(process.stdout.closed and process.stderr.closed)

    def test_root_identity_change_is_rejected_before_process_start(self) -> None:
        with mock.patch.object(process_module.subprocess, 'Popen') as popen:
            with self.assertRaises(adapter_module._AdapterFailure) as caught:
                self.adapter._run_git(('status', '--'), identity=(self.identity[0], self.identity[1] + 1))
        self.assertIs(caught.exception.code, GitFailureCode.UNSAFE_ROOT)
        popen.assert_not_called()

        if os.name == "posix":
            process = self._pipe_process(b"x" * 80)
            unsafe = process_module.GitProcessFailure(GitFailureCode.UNSAFE_ROOT)
            with mock.patch.object(process_module.GitProcessRunner, "_verify_root", side_effect=(None, unsafe)), mock.patch.object(process_module, "_MAX_STDOUT_BYTES", 64), self.assertRaises(adapter_module._AdapterFailure) as caught:
                self._run_with_process(process)
            self.assertIs(caught.exception.code, GitFailureCode.UNSAFE_ROOT)
            self.assertTrue(process.stdout.closed and process.stderr.closed)

    def test_partial_process_start_and_close_failure_are_owned_and_fail_closed(self) -> None:
        partial = _FakeProcess()
        partial.stderr = None
        with mock.patch.object(process_module.subprocess, 'Popen', return_value=partial):
            with self.assertRaises(GitOperationError) as caught:
                self.adapter._public_call(GitOperationKind.STATUS, lambda: self.adapter._run_git(('status', '--'), identity=self.identity, stdin=b'input'))
        self.assertIs(caught.exception.code, GitFailureCode.PROCESS_START_FAILED)
        self.assertTrue(partial.stdin.closed)
        self.assertTrue(partial.stdout.closed)
        self.assertEqual(partial.terminate_calls, 1)
        close_failure = _FakeProcess()
        close_failure.stdout.close_failure = OSError('private close error')
        with mock.patch.object(process_module.subprocess, 'Popen', return_value=close_failure):
            with self.assertRaises(GitOperationError) as caught:
                self.adapter._public_call(GitOperationKind.STATUS, lambda: self.adapter._run_git(('status', '--'), identity=self.identity))
        self.assertIs(caught.exception.code, GitFailureCode.PROCESS_START_FAILED)
        self.assertEqual(exception_graph(caught.exception), (caught.exception,))
        self.assertTrue(close_failure.stdout.closed)
        self.assertTrue(close_failure.stderr.closed)
        for position in (1, 2, 3):
            failed_start = _FakeProcess()
            self.thread_start_failure = len(self.threads) + position
            with mock.patch.object(process_module.subprocess, 'Popen', return_value=failed_start):
                with self.assertRaises(GitOperationError) as caught:
                    self.adapter._public_call(GitOperationKind.STATUS, lambda: self.adapter._run_git(('status', '--'), identity=self.identity))
            self.assertIs(caught.exception.code, GitFailureCode.PROCESS_START_FAILED)
            self.assertEqual(exception_graph(caught.exception), (caught.exception,))
            self.assertNotIn('private thread start', repr(caught.exception))
            self.assertEqual(failed_start.terminate_calls, 1)
            self.assertTrue(failed_start.stdout.closed and failed_start.stderr.closed)
        self.thread_start_failure = None

        if os.name == "posix":
            for seam in ("register", "read", "close"):
                process = self._pipe_process()
                selector = process_module.selectors.DefaultSelector()
                self.addCleanup(selector.close)
                target = process_module.os if seam == "read" else selector
                with mock.patch.object(process_module.selectors, "DefaultSelector", return_value=selector), mock.patch.object(target, seam, side_effect=OSError("private selector failure")), self.assertRaises(GitOperationError) as caught:
                    self.adapter._public_call(GitOperationKind.STATUS, lambda: self._run_with_process(process))
                self.assertIs(caught.exception.code, GitFailureCode.PROCESS_START_FAILED)
                self.assertTrue(process.stdout.closed and process.stderr.closed)
                self.assertEqual(exception_graph(caught.exception), (caught.exception,))
                self.assertNotIn("private", repr(caught.exception))
                self.assertEqual(process.terminate_calls, 0 if seam == "close" else 1)
            process = self._pipe_process()
            reader, writer = os.pipe()
            process.stdin = os.fdopen(writer, "wb", buffering=0)
            self.addCleanup(process.stdin.close)
            self.addCleanup(os.close, reader)
            with mock.patch.object(process_module.os, "write", side_effect=BrokenPipeError("private broken pipe")), self.assertRaises(adapter_module._AdapterFailure) as caught:
                self._run_with_process(process, stdin=b"input")
            self.assertIs(caught.exception.code, GitFailureCode.PROCESS_START_FAILED)
            self.assertEqual(process.terminate_calls, 1)
            self.assertEqual(process.returncode, 0)
            self.assertTrue(process.stdin.closed and process.stdout.closed and process.stderr.closed)


if __name__ == "__main__":
    unittest.main()
