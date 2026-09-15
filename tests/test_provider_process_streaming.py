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


if __name__ == "__main__":
    unittest.main()
