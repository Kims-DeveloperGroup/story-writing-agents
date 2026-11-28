"""Antigravity stream event and terminal result delivery tests."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from story_writing_agents.agent_context.progress import (
    ProviderStreamEvent,
    ProviderStreamEventKind,
)
from story_writing_agents.errors import ProviderAuthenticationRequiredError
from story_writing_agents.provider_adapter import (
    ProviderInvocation,
    ProviderInvocationError,
)
from story_writing_agents.provider_process import ProcessRequest, ProcessResult
from tests.adapter_streaming_fixture import (
    _string_result_schema,
    _successful_stream_result,
    setUp as setup_streaming,
)
from tests.antigravity_streaming_fixture import AntigravityStreamFixture


class AntigravityStreamDeliveryTests(
    AntigravityStreamFixture, unittest.IsolatedAsyncioTestCase
):
    setUp = setup_streaming

    async def test_antigravity_adapter_streams_events_and_parses_stream_json(self) -> None:
        adapter, binding = self._make_agy_binding('definition_session_agy')

        events: list[ProviderStreamEvent] = []
        sink = events.append

        schema = _string_result_schema('outline')
        payload = {"task": "draft"}

        invocation = ProviderInvocation(
            session_id="session_agy_stream",
            schema=schema,
            payload=payload,
            thread_id=None,
            stream_sink=sink,
        )

        terminal_result = {
            "conversation_id": "conv_agy_999",
            "status": "SUCCESS",
            "structured_output": {"outline": "Chapter 1: The Gathering Storm"},
            "json_schema": schema,
        }
        terminal_json_bytes = json.dumps(terminal_result).encode("utf-8") + b"\n"

        stream_lines = [
            b'{"type":"turn_start"}\n',
            b'{"type":"thought","text":"Assessing historical parallels..."}\n',
            b'{"type":"agent_message","text":"The banners waved in the freezing mist."}\n',
            terminal_json_bytes,]

        captured_commands: list[list[str]] = []

        async def fake_run_process(
            request: ProcessRequest,
            **_kwargs: object,
        ) -> ProcessResult:
            captured_commands.append(list(request.command))
            self.assertIsNotNone(request.on_stdout_line)
            for line in stream_lines:
                request.on_stdout_line(line)
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            result = await self._invoke_antigravity(adapter, binding, invocation)

        # Check command selected stream-json
        self.assertIn("--output-format", captured_commands[0])
        fmt_idx = captured_commands[0].index("--output-format")
        self.assertEqual(captured_commands[0][fmt_idx + 1], "stream-json")

        # Verify parsed terminal result
        self.assertEqual(result.thread_id, "conv_agy_999")
        parsed_response = json.loads(result.response_text)
        self.assertEqual(parsed_response, {"outline": "Chapter 1: The Gathering Storm"})

        # Verify dispatched stream events
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0].kind, ProviderStreamEventKind.STATUS)
        self.assertEqual(events[0].text, "Generating response...")
        self.assertEqual(events[1].kind, ProviderStreamEventKind.THOUGHT_DELTA)
        self.assertEqual(events[1].text, "Assessing historical parallels...")
        self.assertEqual(events[2].kind, ProviderStreamEventKind.TEXT_DELTA)
        self.assertEqual(events[2].text, "The banners waved in the freezing mist.")

    async def test_antigravity_adapter_sanitizes_stdout_in_streamed_response_diagnostic(
        self,
    ) -> None:
        """Verify ProviderResponseDiagnostic excludes intermediate stream events for privacy."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_diag_test")
        events: list[ProviderStreamEvent] = []

        schema = _string_result_schema('outline')

        invocation = ProviderInvocation(
            session_id="session_diag",
            schema=schema,
            payload={},
            thread_id=None,
            stream_sink=events.append,
        )

        stream_lines = [
            b'{"type":"turn_start"}\n',
            b'{"type":"thought","text":"Thinking..."}\n',
            (
                b'{"conversation_id": "conv_diag", "status": "SUCCESS", '
                b'"structured_output": {"outline": "Recovered"}, '
                b'"json_schema": {"type": "object", "properties": {"outline": {"type": "string"}}, "required": ["outline"], "additionalProperties": false}}\n'
            ),
        ]
        full_stdout = b"".join(stream_lines)

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return ProcessResult(
                stdout=full_stdout,
                stderr=b"[agy] print timeout after 10s with turn in progress; returning partial output\n",
                returncode=0,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with mock.patch("story_writing_agents.antigravity_native_recovery.has_current_finish", return_value=True):
                result = await self._invoke_antigravity(adapter, binding, invocation)

        # Incomplete output attaches response_diagnostic with non-sensitive terminal output
        self.assertIsNotNone(result.response_diagnostic)
        self.assertEqual(result.response_diagnostic.stdout, stream_lines[2].strip())
        self.assertNotIn(b'{"type":"turn_start"}', result.response_diagnostic.stdout)
        self.assertNotIn(b"Thinking...", result.response_diagnostic.stdout)

    async def test_antigravity_adapter_running_status_notice_emits_status_and_is_not_captured_as_terminal(self) -> None:
        adapter, binding = self._make_agy_binding('definition_session_agy_status')
        events: list[ProviderStreamEvent] = []

        schema = _string_result_schema('summary')
        invocation = ProviderInvocation(
            session_id="session_agy_running",
            schema=schema,
            payload={},
            thread_id="thread_run_1",
            stream_sink=events.append,
        )

        stream_lines = [
            b'{"conversation_id":"thread_run_1","status":"running","message":"Model working..."}\n',
            (
                b'{"conversation_id":"thread_run_1","status":"SUCCESS",'
                b'"structured_output":{"summary":"Completed"},'
                b'"json_schema":{"type":"object","properties":{"summary":{"type":"string"}},"required":["summary"],"additionalProperties":false}}\n'
            ),
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            result = await self._invoke_antigravity(adapter, binding, invocation)

        self.assertEqual(result.response_text, '{"summary":"Completed"}')
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, ProviderStreamEventKind.STATUS)
        self.assertEqual(events[0].text, "Model working...")

    async def test_antigravity_adapter_recognizes_wrapped_authentication_error(self) -> None:
        adapter, binding = self._make_agy_binding('session_wrapped_auth_def')
        invocation = ProviderInvocation(
            session_id="session_agy_wrapped_auth",
            schema={"type": "object"},
            payload={},
            thread_id="thread_wrapped_auth_1",
            stream_sink=lambda _event: None,
        )

        wrapped_auth_lines = [
            b'{"type":"result","result":{"status":"ERROR","error":{"code":401,"message":"401 Unauthorized"}}}\n',
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            if request.on_stdout_line:
                for line in wrapped_auth_lines:
                    request.on_stdout_line(line)
            return ProcessResult(
                stdout=b"".join(wrapped_auth_lines),
                stderr=b"",
                returncode=1,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as exc_info:
                await self._invoke_antigravity(adapter, binding, invocation)
            self.assertIsNotNone(exc_info.exception.authentication_failure)
            self.assertIsInstance(
                exc_info.exception.authentication_failure,
                ProviderAuthenticationRequiredError,)

    async def test_antigravity_adapter_unwraps_event_alias_result_envelope(self) -> None:
        adapter, binding = self._make_agy_binding('session_event_alias_def')
        invocation = ProviderInvocation(
            session_id="session_agy_event_alias",
            schema=_string_result_schema('msg'),
            payload={},
            thread_id="thread_alias_1",
            stream_sink=lambda _ev: None,)

        stream_lines = [
            (
                b'{"event":"result","result":{"status":"SUCCESS","conversation_id":"thread_alias_1",'
                b'"structured_output":{"msg":"Hello world"},'
                b'"json_schema":{"type":"object","properties":{"msg":{"type":"string"}},"required":["msg"],"additionalProperties":false}}}\n'
            ),
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            if request.on_stdout_line:
                for line in stream_lines:
                    request.on_stdout_line(line)
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            result = await self._invoke_antigravity(adapter, binding, invocation)
        self.assertEqual(result.response_text, '{"msg":"Hello world"}')

    async def test_antigravity_adapter_recognizes_authentication_failure_with_streaming_preamble(self) -> None:
        """Verify authentication failure is recognized when preceded by streaming preamble."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_auth_preamble")
        schema = _string_result_schema('status')
        invocation = ProviderInvocation(
            session_id="session_auth_preamble",
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
            b'{"type":"turn_start"}\n',
            b'{"type":"status","text":"Connecting..."}\n',
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
            self.assertIsNotNone(cm.exception.authentication_failure)
            self.assertTrue(cm.exception.authentication_failure.replay_safe)
            self.assertFalse(cm.exception.taint_existing_thread)

    async def test_antigravity_adapter_unwraps_result_envelope_with_whitespace_discriminator(self) -> None:
        """Verify _unwrap_result_envelope normalizes whitespace in discriminators."""
        adapter, binding = self._make_agy_binding("session_whitespace_disc")
        schema = _string_result_schema('status')
        invocation = ProviderInvocation(
            session_id="session_whitespace_disc",
            schema=schema,
            payload={},
            thread_id="thread_ws_1",
            stream_sink=lambda _event: None,
        )
        stream_lines = [
            (
                b'{"type":" result ","result":{"status":"SUCCESS","conversation_id":"thread_ws_1",'
                b'"structured_output":{"status":"ok"},'
                b'"json_schema":{"type":"object","properties":{"status":{"type":"string"}},"required":["status"],"additionalProperties":false}}}\n'
            ),
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            result = await self._invoke_antigravity(adapter, binding, invocation)
        self.assertEqual(result.response_text, '{"status":"ok"}')

    async def test_antigravity_adapter_streams_native_step_update_and_init_events(self) -> None:
        adapter, binding = self._make_agy_binding('session_agy_native_stream')

        schema = _string_result_schema('reply')
        events: list[ProviderStreamEvent] = []
        invocation = ProviderInvocation(
            session_id="session_agy_native_stream",
            schema=schema,
            payload={"task": "draft"},
            thread_id=None,
            stream_sink=events.append,
        )

        stream_lines = [
            b'{"event":"init","conversation_id":"conv_native_1","init":{"model":"gemini-3.8-flash-high"}}\n',
            b'{"event":"step_update","step_update":{"conversation_id":"conv_native_1","step_index":0,"state":"DONE","step_type":"user_input"}}\n',
            b'{"event":"step_update","step_update":{"conversation_id":"conv_native_1","step_index":1,"state":"ACTIVE","step_type":"thought","thought_delta":"Analyzing the premise..."}}\n',
            b'{"event":"step_update","step_update":{"conversation_id":"conv_native_1","step_index":1,"state":"ACTIVE","step_type":"agent_response","text_delta":"First part "}}\n',
            b'{"event":"step_update","step_update":{"conversation_id":"conv_native_1","step_index":1,"state":"ACTIVE","step_type":"agent_response","text_delta":"second part."}}\n',
            b'{"event":"step_update","step_update":{"conversation_id":"conv_native_1","step_index":1,"state":"DONE","step_type":"agent_response","text_delta":"\\n"}}\n',
            json.dumps({
                "event": "result",
                "result": {
                    "conversation_id": "conv_native_1",
                    "status": "SUCCESS",
                    "structured_output": {"reply": "First part second part.\n"},
                    "json_schema": schema,
                },
            }).encode("utf-8") + b"\n",]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            self.assertIsNotNone(request.on_stdout_line)
            for line in stream_lines:
                request.on_stdout_line(line)
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            result = await self._invoke_antigravity(adapter, binding, invocation)

        self.assertEqual(result.thread_id, "conv_native_1")
        self.assertEqual(json.loads(result.response_text), {"reply": "First part second part.\n"})
        self.assertEqual(len(events), 5)
        self.assertEqual(events[0].kind, ProviderStreamEventKind.STATUS)
        self.assertEqual(events[0].text, "Generating response...")
        self.assertEqual(events[1].kind, ProviderStreamEventKind.THOUGHT_DELTA)
        self.assertEqual(events[1].text, "Analyzing the premise...")
        self.assertEqual(events[2].kind, ProviderStreamEventKind.TEXT_DELTA)
        self.assertEqual(events[2].text, "First part ")
        self.assertEqual(events[3].kind, ProviderStreamEventKind.TEXT_DELTA)
        self.assertEqual(events[3].text, "second part.")
        self.assertEqual(events[4].kind, ProviderStreamEventKind.TEXT_DELTA)
        self.assertEqual(events[4].text, "\n")

    async def test_antigravity_adapter_surfaces_provider_error_on_non_zero_returncode(self) -> None:
        adapter, binding = self._make_agy_binding('session_agy_err_code')

        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        events: list[ProviderStreamEvent] = []
        invocation = ProviderInvocation(
            session_id="session_agy_err_code",
            schema=schema,
            payload={"task": "draft"},
            thread_id=None,
            stream_sink=events.append,
        )

        error_result = json.dumps({
            "event": "result",
            "result": {
                "conversation_id": "conv_err_1",
                "status": "ERROR",
                "response": "Native refusal",
                "error": "No capacity available for model gpt-oss-120b-medium on the server",},
        }).encode("utf-8") + b"\n"

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            if request.on_stdout_line:
                request.on_stdout_line(error_result)
            return ProcessResult(
                stdout=error_result,
                stderr=b"warning: --mode plan has no effect\nerror: No capacity available for model gpt-oss-120b-medium on the server\n",
                returncode=1,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await self._invoke_antigravity(adapter, binding, invocation)

        self.assertEqual(str(cm.exception), "Antigravity CLI exited with status 1")
        diagnostic = cm.exception.response_diagnostic
        self.assertIsNotNone(diagnostic)
        self.assertEqual(diagnostic.response_text, "Native refusal")
        # Verify status event was emitted to stream sink
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, ProviderStreamEventKind.STATUS)
        self.assertIn("No capacity available", events[0].text)

    async def test_antigravity_adapter_replays_stdout_when_no_streaming_callbacks_delivered(self) -> None:
        adapter, binding = self._make_agy_binding('session_agy_fallback')

        schema = {"type": "object", "properties": {"reply": {"type": "string"}}}
        events: list[ProviderStreamEvent] = []
        invocation = ProviderInvocation(
            session_id="session_agy_fallback",
            schema=schema,
            payload={"task": "draft"},
            thread_id=None,
            stream_sink=events.append,
        )

        stream_stdout = (
            b'{"event":"init","init":{"conversation_id":"conv_fallback_1"}}\n'
            b'{"event":"step_update","step_update":{"conversation_id":"conv_fallback_1","step_index":1,"state":"DONE","step_type":"agent_response","text_delta":"Fallback text."}}\n'
            + json.dumps({
                "event": "result",
                "result": {
                    "conversation_id": "conv_fallback_1",
                    "status": "SUCCESS",
                    "structured_output": {"reply": "Fallback text."},
                    "json_schema": schema,
                },
            }).encode("utf-8") + b"\n"
        )

        async def fake_run_process(_request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            # Simulate fallback transport: on_stdout_line is present but communicate() was used
            return ProcessResult(
                stdout=stream_stdout,
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            result = await self._invoke_antigravity(adapter, binding, invocation)

        self.assertEqual(result.thread_id, "conv_fallback_1")
        self.assertEqual(json.loads(result.response_text), {"reply": "Fallback text."})
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].kind, ProviderStreamEventKind.STATUS)
        self.assertEqual(events[1].kind, ProviderStreamEventKind.TEXT_DELTA)
        self.assertEqual(events[1].text, "Fallback text.")

    async def test_antigravity_adapter_thought_step_never_emits_text_delta_and_non_agent_response_steps_ignored(self) -> None:
        adapter, binding = self._make_agy_binding("session_agy_step_privacy")
        events: list[ProviderStreamEvent] = []

        invocation = ProviderInvocation(
            session_id="session_agy_step_privacy",
            schema={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
            payload={},
            thread_id="thread_privacy_1",
            stream_sink=events.append,
        )

        stream_lines = [
            b'{"event":"step_update","step_update":{"step_type":"thought","thought_delta":"thinking","text_delta":"leaked_thought"}}\n',
            b'{"event":"step_update","step_update":{"step_type":"user_input","text_delta":"echoed_user_prompt"}}\n',
            b'{"event":"step_update","step_update":{"step_type":"agent_response","text_delta":"valid_response"}}\n',
            (
                b'{"event":"result","result":{"status":"SUCCESS","conversation_id":"thread_privacy_1",'
                b'"structured_output":{"msg":"done"},'
                b'"json_schema":{"type":"object","properties":{"msg":{"type":"string"}},"required":["msg"]}}}\n'
            ),
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            self.assertIsNotNone(request.on_stdout_line)
            for line in stream_lines:
                request.on_stdout_line(line)
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            await self._invoke_antigravity(adapter, binding, invocation)

        text_deltas = [ev.text for ev in events if ev.kind == ProviderStreamEventKind.TEXT_DELTA]
        thought_deltas = [ev.text for ev in events if ev.kind == ProviderStreamEventKind.THOUGHT_DELTA]
        self.assertEqual(text_deltas, ["valid_response"])
        self.assertEqual(thought_deltas, ["thinking"])

    async def test_antigravity_adapter_extracts_status_and_error_from_legacy_terminal_shape(self) -> None:
        """Verify legacy terminal error event without result envelope emits stream error status."""
        adapter, binding = self._make_agy_binding("session_legacy_error")
        events: list[ProviderStreamEvent] = []
        schema = _string_result_schema('status')
        invocation = ProviderInvocation(
            session_id="session_legacy_error",
            schema=schema,
            payload={},
            thread_id=None,
            stream_sink=events.append,
        )

        stream_lines = [
            b'{"type":"turn_start"}\n',
            b'{"conversation_id":"c1","status":"ERROR","error":"Quota exceeded for tier"}\n',]

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
            with self.assertRaises(Exception):
                await self._invoke_antigravity(adapter, binding, invocation)

        status_events = [ev for ev in events if ev.kind == ProviderStreamEventKind.STATUS]
        self.assertTrue(any("Error: Quota exceeded for tier" in ev.text for ev in status_events))

    async def test_antigravity_adapter_legacy_text_delta_fails_closed_on_non_string_payload(self) -> None:
        """Verify legacy event extractor validates text payload as string before emitting TEXT_DELTA."""
        adapter, binding = self._make_agy_binding("session_non_str_payload")
        events: list[ProviderStreamEvent] = []

        invocation = ProviderInvocation(
            session_id="session_non_str_payload",
            schema={"type": "object"},
            payload={},
            thread_id="thread_agy_non_str",
            stream_sink=events.append,
        )

        terminal_result = {
            "conversation_id": "thread_agy_non_str",
            "status": "SUCCESS",
            "structured_output": {},
            "json_schema": {"type": "object"},
        }
        stream_lines = [
            b'{"type":"agent_message","text":12345}\n',
            json.dumps(terminal_result).encode("utf-8") + b"\n",
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            await self._invoke_antigravity(adapter, binding, invocation)

        text_deltas = [e for e in events if e.kind == ProviderStreamEventKind.TEXT_DELTA]
        self.assertEqual(len(text_deltas), 0)

    async def test_antigravity_adapter_generic_untyped_text_delta_ignored_without_agent_response_shape(self) -> None:
        """Verify generic text_delta without step_type='agent_response' is ignored, while explicit shapes emit."""
        adapter, binding = self._make_agy_binding("session_untyped_delta")
        events: list[ProviderStreamEvent] = []

        invocation = ProviderInvocation(
            session_id="session_untyped_delta",
            schema={"type": "object"},
            payload={},
            thread_id="thread_agy_untyped",
            stream_sink=events.append,
        )

        terminal_result = {
            "conversation_id": "thread_agy_untyped",
            "status": "SUCCESS",
            "structured_output": {},
            "json_schema": {"type": "object"},
        }
        stream_lines = [
            b'{"type":"text_delta","text":"generic text ignored"}\n',
            b'{"type":"content_delta","content":"generic content ignored"}\n',
            b'{"type":"text_delta","step_type":"agent_response","text":"typed delta accepted"}\n',
            b'{"type":"agent_message","text":"agent message accepted"}\n',
            b'{"type":"agent_response","text":"agent response accepted"}\n',
            b'{"type":"agent_message","step_type":"thought","text":"mismatched step_type ignored"}\n',
            json.dumps(terminal_result).encode("utf-8") + b"\n",
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            await self._invoke_antigravity(adapter, binding, invocation)

        text_deltas = [e.text for e in events if e.kind == ProviderStreamEventKind.TEXT_DELTA]
        self.assertEqual(
            text_deltas,
            [
                "typed delta accepted",
                "agent message accepted",
                "agent response accepted",],
        )

    async def test_antigravity_stream_extracts_validated_string_message_and_avoids_dict_leak(self) -> None:
        """Verify dictionary-shaped error objects only extract message and do not stringify dict details."""
        from story_writing_agents.agent_context.progress import ProviderStreamEventKind

        adapter, binding = self._make_agy_binding("session_err_dict_leak")
        schema = _string_result_schema('status')
        events = []
        invocation = ProviderInvocation(
            session_id="session_err_dict_leak",
            schema=schema,
            payload={},
            thread_id=None,
            stream_sink=events.append,
        )
        stream_lines = [
            b'{"type":"status","status":"running","message":{"message":"step failed","secret_token":"leak"}}\n',
            b'{"type":"step_update","step_update":{"step_type":"error_message","error":{"message":"error detail","auth_key":"secret"}}}\n',
            b'{"type":"result","result":{"status":"ERROR","conversation_id":"c1","error":{"message":"terminal failure","private_data":"hidden"}}}\n',
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
            with self.assertRaises(Exception):
                await self._invoke_antigravity(adapter, binding, invocation)

        status_events = [e.text for e in events if e.kind == ProviderStreamEventKind.STATUS]
        for text in status_events:
            self.assertNotIn("secret_token", text)
            self.assertNotIn("auth_key", text)
            self.assertNotIn("private_data", text)
        self.assertIn("step failed", status_events)
        self.assertIn("Error: error detail", status_events)
        self.assertIn("Error: terminal failure", status_events)
