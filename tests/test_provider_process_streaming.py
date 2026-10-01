"""Unit tests for subprocess streaming transport in provider_process."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
import json
from dataclasses import replace
from pathlib import Path

from story_writing_agents import provider_process as process_module

from story_writing_agents.provider_process import (
    ProcessRequest,
    ProcessResult,
    ProcessTimedOut,
    run_process,
)


class TestProviderProcessStreaming(unittest.IsolatedAsyncioTestCase):
    """Test line-by-line streaming from provider subprocesses."""

    async def test_non_streaming_runs_normally(self) -> None:
        """Verify non-streaming requests communicate and return full stdout/stderr."""
        code = (
            "import sys; "
            "sys.stdout.write('hello\\nworld\\n'); sys.stdout.flush(); "
            "sys.stderr.write('err\\n'); sys.stderr.flush()"
        )
        request = ProcessRequest(
            command=[sys.executable, "-c", code],
            stdin=b"",
            timeout_seconds=5.0,
        )
        result = await run_process(request)
        self.assertEqual(result.stdout, b"hello\nworld\n")
        self.assertEqual(result.stderr, b"err\n")
        self.assertEqual(result.returncode, 0)
        with tempfile.TemporaryDirectory(prefix="provider-replacement-") as temporary:
            fixture = Path(temporary).resolve()
            root, state, staging = (fixture / name for name in ("session", "state", "staging"))
            for directory in (root, state, staging):
                directory.mkdir()
            schema, output, metadata, missing = (fixture / name for name in ("schema", "output", "metadata", "absent"))
            denied = (root / "story.md", root / ".git", fixture / "sibling.md", staging / "private.md", metadata)
            schema.write_bytes(b"SCHEMA\n")
            for path in denied:
                path.write_bytes(b"PRIVATE_PROTECTED_BYTES\n")
            executable = Path("/usr/bin/perl" if sys.platform == "darwin" else "/bin/cat").resolve(strict=True)
            requirements = process_module.DarwinSandboxRequirements(
                executable=executable, read_paths=(schema, root), write_paths=(output,),
                write_subpaths=(state,), metadata_paths=(metadata, missing),
                deny_subpaths=(root / ".git", staging), deny_write_subpaths=(root,))
            linked = fixture / "linked"
            linked.symlink_to(metadata)
            for invalid in ([], ("not-a-path",), (Path("relative"),), (linked,)):
                with self.assertRaises(ValueError):
                    replace(requirements, metadata_paths=invalid)
            profile = process_module._darwin_sandbox_profile(requirements)
            literal = lambda path: json.dumps(str(path), ensure_ascii=True)
            resources = (executable, schema, root, output, state, metadata, missing)
            for resource in resources:
                for ancestor in (resource, *resource.parents):
                    self.assertIn("(allow file-read-metadata (literal " + literal(ancestor) + "))", profile)
            self.assertIn('(allow file-read-metadata (literal "/etc"))', profile)
            self.assertIn("(allow file-read* (literal " + literal(root) + "))", profile)
            for path in (root, metadata, missing, fixture, staging, root / ".git"):
                self.assertNotIn("(allow file-read* (subpath " + literal(path) + "))", profile)
            self.assertNotIn("(allow file-read* (literal " + literal(metadata) + "))", profile)
            self.assertNotIn("(allow network-outbound)", profile)
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                if sys.platform != "darwin":
                    with self.assertRaises(process_module.ProcessStartFailed):
                        process_module._darwin_confined_command((str(executable), str(schema)), requirements)
                else:
                    probe = (
                        'my ($schema,$output,$state,$meta,$absent,@denied)=@ARGV; '
                        'opendir(my $d,".") or die "directory: $!"; '
                        'my @cwd=stat("."); print "cwd:$cwd[1]\\n"; '
                        'open(my $r,"<",$schema) or die "schema: $!"; '
                        'my $v=<$r>; $v eq "SCHEMA\\n" or die "schema bytes"; '
                        'open(my $o,">",$output) or die "output: $!"; print $o "OUTPUT"; close($o); '
                        'open(my $s,">","$state/cache") or die "state: $!"; print $s "STATE"; close($s); '
                        'stat($meta) or die "metadata: $!"; stat($absent); $!{ENOENT} or die "absence: $!"; '
                        'for my $p (@denied) { '
                        'open(my $r,"<",$p) and die "protected read admitted"; '
                        '($!{EPERM} || $!{EACCES}) or die "read errno: $!"; '
                        'open(my $w,">>",$p) and die "protected write admitted"; '
                        '($!{EPERM} || $!{EACCES}) or die "write errno: $!"; } '
                        'open(my $w,">","new-story.md") and die "new story write admitted"; '
                        '($!{EPERM} || $!{EACCES}) or die "create errno: $!"; print "confined\\n";')
                    async def execute(command, policy):
                        sandboxed = process_module._darwin_confined_command(command, policy)
                        process = await asyncio.create_subprocess_exec(
                            os.path.realpath(sys.executable), "-I", "-c", process_module._DESCRIPTOR_EXEC_HELPER,
                            str(descriptor), *sandboxed, pass_fds=(descriptor,),
                            env=os.environ | {"LC_ALL": "C", "LC_CTYPE": "C", "LANG": "C"},
                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                        stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
                        return process.returncode, stdout, stderr
                    command = (str(executable), "-e", probe, *(str(path) for path in (schema, output, state, metadata, missing, *denied)))
                    status, stdout, stderr = await execute(command, requirements)
                    self.assertEqual((status, stdout, stderr), (0, f"cwd:{root.stat().st_ino}\nconfined\n".encode(), b""))
                    self.assertEqual(output.read_bytes(), b"OUTPUT")
                    self.assertEqual((state / "cache").read_bytes(), b"STATE")
                    self.assertFalse((root / "new-story.md").exists())
                    root.rename(root.with_name("moved"))
                    root.mkdir()
                    replacement = root / "replacement.txt"
                    replacement.write_bytes(b"PRIVATE_REPLACEMENT_BYTES")
                    cat = Path("/bin/cat").resolve(strict=True)
                    status, stdout, stderr = await execute((str(cat), str(replacement)), replace(requirements, executable=cat))
                    self.assertNotEqual(status, 0)
                    self.assertEqual(stdout, b"")
                    self.assertNotIn(b"PRIVATE_REPLACEMENT_BYTES", stderr)
                for path in denied:
                    checked = root.with_name("moved") / path.name if path.parent == root and sys.platform == "darwin" else path
                    self.assertEqual(checked.read_bytes(), b"PRIVATE_PROTECTED_BYTES\n")
            finally:
                os.close(descriptor)

    async def test_streaming_receives_lines_in_real_time(self) -> None:
        """Verify stdout lines are dispatched to callback and collected in ProcessResult."""
        code = (
            "import sys, time\n"
            "for i in range(3):\n"
            "    sys.stdout.write(f'line {i}\\n')\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(0.01)\n"
        )
        received_lines: list[bytes] = []

        def on_line(line: bytes) -> None:
            received_lines.append(line)

        request = ProcessRequest(
            command=[sys.executable, "-c", code],
            stdin=b"",
            timeout_seconds=5.0,
            on_stdout_line=on_line,
        )
        result = await run_process(request)
        self.assertEqual(received_lines, [b"line 0\n", b"line 1\n", b"line 2\n"])
        self.assertEqual(result.stdout, b"line 0\nline 1\nline 2\n")
        self.assertEqual(result.returncode, 0)

    async def test_async_callback_is_awaited(self) -> None:
        """Verify an async callback coroutine is awaited properly."""
        code = "import sys; sys.stdout.write('alpha\\nbeta\\n'); sys.stdout.flush()"
        async_received: list[bytes] = []

        async def async_on_line(line: bytes) -> None:
            await asyncio.sleep(0.001)
            async_received.append(line)

        request = ProcessRequest(
            command=[sys.executable, "-c", code],
            stdin=b"",
            timeout_seconds=5.0,
            on_stdout_line=async_on_line,
        )
        result = await run_process(request)
        self.assertEqual(async_received, [b"alpha\n", b"beta\n"])
        self.assertEqual(result.stdout, b"alpha\nbeta\n")

    async def test_callback_exception_does_not_abort_stream_drain(self) -> None:
        """Verify exceptions in line callbacks do not interrupt stream collection."""
        code = "import sys; sys.stdout.write('one\\ntwo\\nthree\\n'); sys.stdout.flush()"
        received: list[bytes] = []

        def failing_callback(line: bytes) -> None:
            received.append(line)
            if b"two" in line:
                raise RuntimeError("simulated callback error")

        request = ProcessRequest(
            command=[sys.executable, "-c", code],
            stdin=b"",
            timeout_seconds=5.0,
            on_stdout_line=failing_callback,
        )
        result = await run_process(request)
        self.assertEqual(received, [b"one\n", b"two\n", b"three\n"])
        self.assertEqual(result.stdout, b"one\ntwo\nthree\n")
        self.assertEqual(result.returncode, 0)

    async def test_streaming_handles_lines_larger_than_64k_buffer(self) -> None:
        """Verify lines exceeding the default 64 KiB buffer are drained without ValueError."""
        # Write a line of 128 KiB (131072 bytes) plus newline, then a smaller line.
        code = (
            "import sys; "
            "sys.stdout.write('a' * 131072 + '\\nshort\\n'); "
            "sys.stdout.flush()"
        )
        received: list[bytes] = []

        request = ProcessRequest(
            command=[sys.executable, "-c", code],
            stdin=b"",
            timeout_seconds=5.0,
            on_stdout_line=lambda line: received.append(line),
        )
        result = await run_process(request)
        self.assertEqual(len(received), 2)
        self.assertEqual(len(received[0]), 131073)  # 131072 'a's + '\n'
        self.assertEqual(received[1], b"short\n")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(result.stdout), 131072 + 1 + 6)

    async def test_streaming_timeout_terminates_process(self) -> None:
        """Verify streaming calls that exceed timeout raise ProcessTimedOut."""
        code = "import time; time.sleep(10)"
        request = ProcessRequest(
            command=[sys.executable, "-c", code],
            stdin=b"",
            timeout_seconds=0.3,
            on_stdout_line=lambda _line: None,
        )
        with self.assertRaises(ProcessTimedOut):
            await run_process(request)

    async def test_future_callback_is_awaited(self) -> None:
        """Verify an awaitable that is a Future (not coroutine) is awaited properly."""
        code = "import sys; sys.stdout.write('future_line\\n'); sys.stdout.flush()"
        loop = asyncio.get_running_loop()
        future_received: list[bytes] = []

        def future_on_line(line: bytes) -> asyncio.Future[None]:
            fut: asyncio.Future[None] = loop.create_future()
            future_received.append(line)
            fut.set_result(None)
            return fut

        request = ProcessRequest(
            command=[sys.executable, "-c", code],
            stdin=b"",
            timeout_seconds=5.0,
            on_stdout_line=future_on_line,
        )
        result = await run_process(request)
        self.assertEqual(future_received, [b"future_line\n"])
        self.assertEqual(result.stdout, b"future_line\n")

    async def test_cancelled_error_in_callback_propagates(self) -> None:
        """Verify asyncio.CancelledError in callback is not swallowed by exception handler."""
        code = "import sys; sys.stdout.write('line\\n'); sys.stdout.flush()"

        def cancelling_callback(_line: bytes) -> None:
            raise asyncio.CancelledError("intentional cancel")

        request = ProcessRequest(
            command=[sys.executable, "-c", code],
            stdin=b"",
            timeout_seconds=5.0,
            on_stdout_line=cancelling_callback,
        )
        with self.assertRaises(asyncio.CancelledError):
            await run_process(request)

    async def test_callback_cancellation_cleans_up_sibling_tasks(self) -> None:
        """Verify callback cancellation ensures all internal reader/writer tasks are completed."""
        code = (
            "import sys, time\n"
            "sys.stdout.write('line1\\n')\n"
            "sys.stdout.flush()\n"
            "time.sleep(1.0)\n"
        )
        current_task = asyncio.current_task()

        def cancelling_callback(_line: bytes) -> None:
            raise asyncio.CancelledError("cancel streaming")

        request = ProcessRequest(
            command=[sys.executable, "-c", code],
            stdin=b"",
            timeout_seconds=5.0,
            on_stdout_line=cancelling_callback,
        )
        with self.assertRaises(asyncio.CancelledError):
            await run_process(request)

        # Confirm no orphan tasks were left pending in the event loop
        pending_tasks = [t for t in asyncio.all_tasks() if t is not current_task and not t.done()]
        self.assertEqual(pending_tasks, [])

    async def test_streaming_handles_multichunk_large_line_and_many_lines_in_single_chunk(self) -> None:
        """Verify multi-chunk large lines and dense multiple lines in single chunks are framed correctly."""
        code = (
            "import sys; "
            "sys.stdout.write('x' * 200000 + '\\n'); "
            "sys.stdout.write(''.join(f'item_{i}\\n' for i in range(100))); "
            "sys.stdout.flush()"
        )
        received: list[bytes] = []

        request = ProcessRequest(
            command=[sys.executable, "-c", code],
            stdin=b"",
            timeout_seconds=5.0,
            on_stdout_line=lambda line: received.append(line),
        )
        result = await run_process(request)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(received), 101)
        self.assertEqual(len(received[0]), 200001)
        self.assertEqual(received[0][-1:], b"\n")
        self.assertEqual(received[1], b"item_0\n")
        self.assertEqual(received[100], b"item_99\n")


    async def test_streaming_selects_streaming_when_stdout_has_read_without_readline(self) -> None:
        """Verify injected process streams with read but no readline attribute invoke callback."""
        received: list[bytes] = []

        class MockStdout:
            def __init__(self, data: bytes) -> None:
                self._data = data
                self._pos = 0

            async def read(self, n: int) -> bytes:
                if self._pos >= len(self._data):
                    return b""
                chunk = self._data[self._pos : self._pos + n]
                self._pos += len(chunk)
                return chunk

        class MockProcess:
            def __init__(self) -> None:
                self.stdin = None
                self.stdout = MockStdout(b"custom_line_1\ncustom_line_2\n")
                self.stderr = None
                self.returncode = 0

            async def communicate(self, _input: bytes | None = None) -> tuple[bytes, bytes]:
                raise AssertionError("communicate() should not be called when streaming is active")

            async def wait(self) -> int:
                return self.returncode

        # Explicitly verify MockStdout does NOT have readline
        self.assertFalse(hasattr(MockStdout, "readline"))

        async def fake_create_process(*_args: object, **_kwargs: object) -> MockProcess:
            return MockProcess()

        request = ProcessRequest(
            command=["fake_binary"],
            stdin=b"",
            timeout_seconds=5.0,
            on_stdout_line=lambda line: received.append(line),
        )
        result = await run_process(request, create_process=fake_create_process)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(received, [b"custom_line_1\n", b"custom_line_2\n"])
        self.assertEqual(result.stdout, b"custom_line_1\ncustom_line_2\n")


if __name__ == "__main__":
    unittest.main()
