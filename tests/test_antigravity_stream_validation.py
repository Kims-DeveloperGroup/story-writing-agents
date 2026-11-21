"""Antigravity stream validation and rejection tests."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from story_writing_agents.provider_adapter import ProviderInvocation
from story_writing_agents.provider_process import ProcessRequest, ProcessResult
from tests.adapter_streaming_fixture import (
    _string_result_schema,
    _successful_stream_result,
    setUp as setup_streaming,
)
from tests.antigravity_streaming_fixture import AntigravityStreamFixture


def _successful_antigravity_result(schema):
    return {
        "conversation_id": "c1",
        "status": "SUCCESS",
        "structured_output": {"status": "ok"},
        "json_schema": schema,
    }


class AntigravityStreamValidationTests(
    AntigravityStreamFixture, unittest.IsolatedAsyncioTestCase
):
    setUp = setup_streaming

    async def test_antigravity_adapter_strictly_rejects_duplicate_fields_in_streamed_result(
        self,
    ) -> None:
        """Verify duplicate fields in a streamed result object trigger strict malformed output rejection."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_dup_test")

        schema = _string_result_schema('outline')

        invocation = ProviderInvocation(
            session_id="session_dup",
            schema=schema,
            payload={},
            thread_id=None,
            stream_sink=lambda _event: None,
        )

        # Streamed result wrapped in type=result with duplicate "status" field inside result
        stream_lines = [
            b'{"type":"turn_start"}\n',
            (
                b'{"type":"result","result":{"conversation_id":"c1",'
                b'"status":"SUCCESS","status":"ERROR",'
                b'"structured_output":{"outline":"test"},'
                b'"json_schema":{"type":"object","properties":{"outline":{"type":"string"}},"required":["outline"],"additionalProperties":false}}}\n'
            ),
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await self._invoke_antigravity(adapter, binding, invocation)
            self.assertIn("malformed JSON", str(cm.exception))

    async def test_antigravity_adapter_rejects_multiple_terminal_results_in_stream(self) -> None:
        """Verify stream with multiple terminal results fails closed."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_multi_terminal")
        schema = _string_result_schema('status')
        invocation = ProviderInvocation(
            session_id="session_multi_terminal",
            schema=schema,
            payload={},
            thread_id=None,
            stream_sink=lambda _event: None,
        )

        res1 = {
            "conversation_id": "c1",
            "status": "SUCCESS",
            "structured_output": {"status": "ok1"},
            "json_schema": schema,
        }
        res2 = {
            "conversation_id": "c1",
            "status": "SUCCESS",
            "structured_output": {"status": "ok2"},
            "json_schema": schema,
        }
        stream_lines = [
            b'{"type":"turn_start"}\n',
            json.dumps(res1).encode("utf-8") + b"\n",
            json.dumps(res2).encode("utf-8") + b"\n",]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await self._invoke_antigravity(adapter, binding, invocation)
            self.assertIn("2 terminal result events; expected exactly 1", str(cm.exception))

    async def test_antigravity_adapter_rejects_invalid_line_before_terminal_result(self) -> None:
        """Verify malformed line before valid terminal result triggers fail-closed error."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_invalid_line")
        schema = _string_result_schema('status')
        invocation = ProviderInvocation(
            session_id="session_invalid_line",
            schema=schema,
            payload={},
            thread_id=None,
            stream_sink=lambda _event: None,
        )
        res = _successful_antigravity_result(schema)
        stream_lines = [
            b"not-json-line\n",
            json.dumps(res).encode("utf-8") + b"\n",
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await self._invoke_antigravity(adapter, binding, invocation)
            self.assertIn("invalid stream line: malformed JSON line", str(cm.exception))

    async def test_antigravity_adapter_rejects_trailing_data_after_terminal_result(self) -> None:
        """Verify trailing stream events after terminal result trigger fail-closed error."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_trailing_data")
        schema = _string_result_schema('status')
        invocation = ProviderInvocation(
            session_id="session_trailing_data",
            schema=schema,
            payload={},
            thread_id=None,
            stream_sink=lambda _event: None,
        )
        res = _successful_antigravity_result(schema)
        stream_lines = [
            b'{"type":"turn_start"}\n',
            json.dumps(res).encode("utf-8") + b"\n",
            b'{"type":"text_delta","text":"extra trailing delta"}\n',]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await self._invoke_antigravity(adapter, binding, invocation)
            self.assertIn("trailing data after terminal result", str(cm.exception))

    async def test_antigravity_adapter_rejects_duplicate_field_in_stream_line(self) -> None:
        """Verify duplicate fields in stream JSON lines are rejected."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_dup_field")
        schema = _string_result_schema('status')
        invocation = ProviderInvocation(
            session_id="session_dup_field",
            schema=schema,
            payload={},
            thread_id=None,
            stream_sink=lambda _event: None,
        )
        res = _successful_antigravity_result(schema)
        stream_lines = [
            b'{"type":"status","text":"first","text":"duplicate"}\n',
            json.dumps(res).encode("utf-8") + b"\n",
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await self._invoke_antigravity(adapter, binding, invocation)
            self.assertIn("invalid stream line: malformed JSON line", str(cm.exception))

    async def test_antigravity_adapter_rejects_nan_or_infinity_in_stream_line(self) -> None:
        """Verify non-standard NaN / Infinity in stream JSON lines are rejected."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_nan_field")
        schema = _string_result_schema('status')
        invocation = ProviderInvocation(
            session_id="session_nan_field",
            schema=schema,
            payload={},
            thread_id=None,
            stream_sink=lambda _event: None,
        )
        res = _successful_antigravity_result(schema)
        stream_lines = [
            b'{"type":"status","val":NaN}\n',
            json.dumps(res).encode("utf-8") + b"\n",
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await self._invoke_antigravity(adapter, binding, invocation)
            self.assertIn("invalid stream line: malformed JSON line", str(cm.exception))

    async def test_antigravity_adapter_rejects_recursion_error_in_stream_line(self) -> None:
        """Verify deeply nested stream JSON lines that trigger RecursionError are rejected fail-closed."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_deep_recursion")
        schema = _string_result_schema('status')
        invocation = ProviderInvocation(
            session_id="session_deep_recursion",
            schema=schema,
            payload={},
            thread_id=None,
            stream_sink=lambda _event: None,
        )
        res = _successful_antigravity_result(schema)
        deep_nesting = b'{"a":' * 10000 + b'1' + b'}' * 10000
        stream_lines = [
            deep_nesting + b"\n",
            json.dumps(res).encode("utf-8") + b"\n",
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await self._invoke_antigravity(adapter, binding, invocation)
            self.assertIn("invalid stream line: malformed JSON line", str(cm.exception))

    async def test_antigravity_adapter_rejects_unknown_event_type_carrying_structured_output(self) -> None:
        """Verify non-empty unknown type/event field is rejected even when carrying structured_output."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_unknown_type_structured")
        schema = _string_result_schema('status')
        invocation = ProviderInvocation(
            session_id="session_unknown_type_structured",
            schema=schema,
            payload={},
            thread_id=None,
            stream_sink=lambda _event: None,
        )
        stream_lines = [
            b'{"type":"custom_unknown","structured_output":{"status":"ok"},"conversation_id":"c1"}\n',
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await self._invoke_antigravity(adapter, binding, invocation)
            self.assertIn("invalid stream line: unknown event type 'custom_unknown'", str(cm.exception))

    async def test_antigravity_adapter_rejects_unknown_event_type_carrying_status_running(self) -> None:
        """Verify unknown event type is rejected even when status is 'running'."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_unknown_type_running")
        schema = _string_result_schema('status')
        invocation = ProviderInvocation(
            session_id="session_unknown_type_running",
            schema=schema,
            payload={},
            thread_id=None,
            stream_sink=lambda _event: None,
        )
        res = _successful_antigravity_result(schema)
        stream_lines = [
            b'{"type":"unexpected_type","status":"running","text":"Working..."}\n',
            json.dumps(res).encode("utf-8") + b"\n",
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await self._invoke_antigravity(adapter, binding, invocation)
            self.assertIn("invalid stream line: unknown event type 'unexpected_type'", str(cm.exception))

    async def test_antigravity_adapter_fails_closed_when_stream_contains_invalid_line_before_auth_terminal(self) -> None:
        """Verify malformed/unknown stream line fails closed before auth recovery is considered."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_invalid_before_auth")
        schema = _string_result_schema('status')
        invocation = ProviderInvocation(
            session_id="session_invalid_before_auth",
            schema=schema,
            payload={},
            thread_id=None,
            stream_sink=lambda _event: None,
        )
        auth_terminal = {
            "conversation_id": "",
            "status": "ERROR",
            "response": "",
            "error": "authentication failed or timed out",
            "duration_seconds": 0,
            "num_turns": 0,
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "thinking_tokens": 0,
                "cache_read_tokens": 0,
                "total_tokens": 0,
            },
        }
        stream_lines = [
            b'{"type":"bogus_event"}\n',
            json.dumps(auth_terminal).encode("utf-8") + b"\n",
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return ProcessResult(
                stdout=b"".join(stream_lines),
                stderr=b"",
                returncode=1,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await self._invoke_antigravity(adapter, binding, invocation)
            # Should fail closed on invalid stream line, NOT enter auth recovery
            self.assertIn("invalid stream line: unknown event type 'bogus_event'", str(cm.exception))
            self.assertIsNone(cm.exception.authentication_failure)

    async def test_antigravity_adapter_rejects_explicit_null_type_discriminator(self) -> None:
        """Verify explicit null type discriminator is rejected fail-closed and not inferred as legacy."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_null_type")
        schema = _string_result_schema('status')
        invocation = ProviderInvocation(
            session_id="session_null_type",
            schema=schema,
            payload={},
            thread_id=None,
            stream_sink=lambda _event: None,
        )
        stream_lines = [
            b'{"type":null,"structured_output":{"status":"ok"},"conversation_id":"c1"}\n',]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await self._invoke_antigravity(adapter, binding, invocation)
            self.assertIn("invalid stream line: invalid event discriminator", str(cm.exception))

    async def test_antigravity_adapter_rejects_explicit_null_event_discriminator_with_running_status(self) -> None:
        """Verify explicit null event discriminator with status 'running' is rejected fail-closed."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_null_event_running")
        schema = _string_result_schema('status')
        invocation = ProviderInvocation(
            session_id="session_null_event_running",
            schema=schema,
            payload={},
            thread_id=None,
            stream_sink=lambda _event: None,
        )
        res = _successful_antigravity_result(schema)
        stream_lines = [
            b'{"event":null,"status":"running"}\n',
            json.dumps(res).encode("utf-8") + b"\n",
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await self._invoke_antigravity(adapter, binding, invocation)
            self.assertIn("invalid stream line: invalid event discriminator", str(cm.exception))

    async def test_antigravity_adapter_rejects_conflicting_event_discriminators(self) -> None:
        """Verify stream lines with conflicting type and event keys are rejected fail-closed."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_conflict_disc")
        schema = _string_result_schema('status')
        invocation = ProviderInvocation(
            session_id="session_conflict_disc",
            schema=schema,
            payload={},
            thread_id=None,
            stream_sink=lambda _event: None,
        )
        stream_lines = [
            b'{"type":"result","event":"turn_start","result":{"status":"SUCCESS","conversation_id":"c1","structured_output":{"status":"ok"}}}\n',
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await self._invoke_antigravity(adapter, binding, invocation)
            self.assertIn("invalid stream line: conflicting event discriminator", str(cm.exception))

    async def test_antigravity_adapter_rejects_one_null_discriminator_when_other_present(self) -> None:
        """Verify stream line with type='result' and event=null is rejected fail-closed."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_one_null_disc")
        schema = _string_result_schema('status')
        invocation = ProviderInvocation(
            session_id="session_one_null_disc",
            schema=schema,
            payload={},
            thread_id=None,
            stream_sink=lambda _event: None,
        )
        stream_lines = [
            b'{"type":"result","event":null,"result":{"status":"SUCCESS","conversation_id":"c1","structured_output":{"status":"ok"}}}\n',
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await self._invoke_antigravity(adapter, binding, invocation)
            self.assertIn("invalid stream line: invalid event discriminator", str(cm.exception))
