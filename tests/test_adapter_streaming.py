"""Antigravity calls without a stream sink and the provider stream-sink contract."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from story_writing_agents.provider_adapter import (
    ProviderInvocation,
    ProviderInvocationError,
)
from story_writing_agents.provider_process import ProcessRequest, ProcessResult
from tests.adapter_streaming_fixture import _string_result_schema, setUp as setup_streaming
from tests.antigravity_streaming_fixture import AntigravityStreamFixture


class AdapterStreamingTests(AntigravityStreamFixture, unittest.IsolatedAsyncioTestCase):
    setUp = setup_streaming

    async def test_antigravity_adapter_uses_json_when_no_stream_sink(self) -> None:
        adapter, binding = self._make_agy_binding('definition_session_agy_no_stream')

        schema = _string_result_schema('status')
        invocation = ProviderInvocation(
            session_id="session_agy_no_stream",
            schema=schema,
            payload={"task": "check"},
            thread_id=None,
            stream_sink=None,
        )

        captured_requests: list[ProcessRequest] = []

        terminal_result = {
            "conversation_id": "conv_no_stream",
            "status": "SUCCESS",
            "structured_output": {"status": "ok"},
            "json_schema": schema,
        }

        async def fake_run_process(
            request: ProcessRequest,
            **_kwargs: object,
        ) -> ProcessResult:
            captured_requests.append(request)
            return ProcessResult(
                stdout=json.dumps(terminal_result).encode("utf-8") + b"\n",
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            result = await self._invoke_antigravity(adapter, binding, invocation)

        # Output format should be json, not stream-json
        fmt_idx = captured_requests[0].command.index("--output-format")
        self.assertEqual(captured_requests[0].command[fmt_idx + 1], "json")
        self.assertIsNone(captured_requests[0].on_stdout_line)
        self.assertEqual(result.thread_id, "conv_no_stream")

    def test_provider_invocation_rejects_async_stream_sink(self) -> None:
        async def async_sink(_event: object) -> None:
            pass

        with self.assertRaises(TypeError) as exc:
            ProviderInvocation(
                session_id="session_async_sink",
                schema={"type": "object"},
                payload={},
                thread_id="thread_async_1",
                stream_sink=async_sink,)

    async def test_antigravity_adapter_surfaces_provider_stderr_on_non_zero_returncode(self) -> None:
        adapter, binding = self._make_agy_binding('session_agy_stderr_err')

        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        invocation = ProviderInvocation(
            session_id="session_agy_stderr_err",
            schema=schema,
            payload={"task": "draft"},
            thread_id=None,
            stream_sink=None,
        )

        async def fake_run_process(_request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            return ProcessResult(
                stdout=b"",
                stderr=b"error: invalid model selection (--model foo): unknown model\n",
                returncode=1,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await self._invoke_antigravity(adapter, binding, invocation)

        self.assertEqual(str(cm.exception), "Antigravity CLI exited with status 1")
        self.assertIsNotNone(cm.exception.response_diagnostic)
        self.assertIn(b"invalid model selection (--model foo): unknown model", cm.exception.response_diagnostic.stderr)
