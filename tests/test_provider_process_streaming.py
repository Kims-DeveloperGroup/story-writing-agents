"""Unit tests for subprocess streaming transport in provider_process."""

from __future__ import annotations

import asyncio
import sys
import unittest

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
        large_payload = "a" * 131072
        code = (
            f"import sys; sys.stdout.write('{large_payload}\\nshort\\n'); "
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
        chunk_line = "x" * 200000 + "\n"
        small_lines = "".join(f"item_{i}\n" for i in range(100))
        code = (
            f"import sys; sys.stdout.write({chunk_line!r} + {small_lines!r}); "
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

