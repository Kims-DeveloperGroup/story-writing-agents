"""Unit and integration tests for live streaming in CodexAdapter and AntigravityAdapter."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from story_writing_agents import AgentBackend, PLOT_ARCHITECT
from story_writing_agents.agent_context.progress import (
    ProviderStreamEvent,
    ProviderStreamEventKind,
)
from story_writing_agents.errors import ProviderAuthenticationRequiredError
from story_writing_agents.prompts import PromptAssembler
from story_writing_agents.provider_adapter import (
    ProviderInvocation,
    ProviderInvocationError,
)
from story_writing_agents.provider_process import ProcessRequest, ProcessResult
from tests.helpers import initialized_workspace


class AdapterStreamingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        self.backend = AgentBackend(self.workspace)
        self.addCleanup(self.backend.close)
        self.codex_adapter = self.backend._codex_adapter
        self.antigravity_adapter = self.backend._antigravity_adapter
        self.definition = PromptAssembler(self.workspace).build_definition(
            session_id="definition_session",
            agent_id=PLOT_ARCHITECT,
        )

    async def test_codex_adapter_streams_status_reasoning_and_message_events(self) -> None:
        adapter = self.codex_adapter
        binding = adapter.bind(self.definition)

        events: list[ProviderStreamEvent] = []
        sink = events.append

        schema = {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
            "additionalProperties": False,
        }
        payload = {"task": "outline"}

        invocation = ProviderInvocation(
            session_id="session_codex_stream",
            schema=schema,
            payload=payload,
            thread_id=None,
            stream_sink=sink,
        )

        stream_lines = [
            b'{"type":"thread.started","thread_id":"thread_codex_1"}\n',
            b'{"type":"turn.started"}\n',
            b'{"type":"item.started","item":{"type":"reasoning","text":"Brainstorming inciting incident"}}\n',
            b'{"type":"item.updated","item":{"type":"agent_message","text":"The kingdom was in turmoil."}}\n',
            b'{"type":"item.completed","item":{"type":"agent_message","text":" King Alden had vanished."}}\n',
            b'\n',
        ]

        async def fake_run_process(
            request: ProcessRequest,
            **_kwargs: object,
        ) -> ProcessResult:
            self.assertIsNotNone(request.on_stdout_line)
            # Write final output file expected by codex adapter
            output_flag_index = request.command.index("--output-last-message")
            output_file_path = Path(request.command[output_flag_index + 1])
            output_file_path.parent.mkdir(parents=True, exist_ok=True)
            output_file_path.write_text('{"summary":"Final plot outline"}', encoding="utf-8")

            # Simulate streaming stdout lines including an invalid line
            for line in stream_lines:
                request.on_stdout_line(line)
            request.on_stdout_line(b"not json line\n")

            return ProcessResult(
                stdout=b"".join(stream_lines),
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.codex_adapter.run_process", new=fake_run_process):
            result = await adapter.invoke(binding, invocation)

        self.assertEqual(result.response_text, '{"summary":"Final plot outline"}')
        # Verify dispatched stream events
        self.assertEqual(len(events), 4)
        self.assertEqual(events[0].kind, ProviderStreamEventKind.STATUS)
        self.assertEqual(events[0].text, "Generating response...")
        self.assertEqual(events[1].kind, ProviderStreamEventKind.THOUGHT_DELTA)
        self.assertEqual(events[1].text, "Brainstorming inciting incident")
        self.assertEqual(events[2].kind, ProviderStreamEventKind.TEXT_DELTA)
        self.assertEqual(events[2].text, "The kingdom was in turmoil.")
        self.assertEqual(events[3].kind, ProviderStreamEventKind.TEXT_DELTA)
        self.assertEqual(events[3].text, " King Alden had vanished.")

    async def test_antigravity_adapter_streams_events_and_parses_stream_json(self) -> None:
        agy_backend = AgentBackend(
            self.workspace,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
        )
        self.addCleanup(agy_backend.close)
        adapter = agy_backend._antigravity_adapter
        agy_definition = PromptAssembler(
            self.workspace,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
        ).build_definition(
            session_id="definition_session_agy",
            agent_id=PLOT_ARCHITECT,
        )
        binding = adapter.bind(agy_definition)

        events: list[ProviderStreamEvent] = []
        sink = events.append

        schema = {
            "type": "object",
            "properties": {"outline": {"type": "string"}},
            "required": ["outline"],
            "additionalProperties": False,
        }
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
            b'{"type":"text_delta","text":"The banners waved in the freezing mist."}\n',
            terminal_json_bytes,
        ]

        captured_commands: list[list[str]] = []

        async def fake_run_process(
            request: ProcessRequest,
            **_kwargs: object,
        ) -> ProcessResult:
            captured_commands.append(list(request.command))
            self.assertIsNotNone(request.on_stdout_line)
            for line in stream_lines:
                request.on_stdout_line(line)
            return ProcessResult(
                stdout=b"".join(stream_lines),
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            result = await adapter.invoke(binding, invocation)

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

    async def test_antigravity_adapter_uses_json_when_no_stream_sink(self) -> None:
        agy_backend = AgentBackend(
            self.workspace,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
        )
        self.addCleanup(agy_backend.close)
        adapter = agy_backend._antigravity_adapter
        agy_definition = PromptAssembler(
            self.workspace,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
        ).build_definition(
            session_id="definition_session_agy_no_stream",
            agent_id=PLOT_ARCHITECT,
        )
        binding = adapter.bind(agy_definition)

        schema = {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
            "additionalProperties": False,
        }
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
            result = await adapter.invoke(binding, invocation)

        # Output format should be json, not stream-json
        fmt_idx = captured_requests[0].command.index("--output-format")
        self.assertEqual(captured_requests[0].command[fmt_idx + 1], "json")
        self.assertIsNone(captured_requests[0].on_stdout_line)
        self.assertEqual(result.thread_id, "conv_no_stream")

    def _make_agy_binding(self, session_id: str):
        agy_backend = AgentBackend(
            self.workspace,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
        )
        self.addCleanup(agy_backend.close)
        adapter = agy_backend._antigravity_adapter
        agy_definition = PromptAssembler(
            self.workspace,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
        ).build_definition(
            session_id=session_id,
            agent_id=PLOT_ARCHITECT,
        )
        binding = adapter.bind(agy_definition)
        return adapter, binding

    async def test_antigravity_adapter_sanitizes_stdout_in_streamed_response_diagnostic(
        self,
    ) -> None:
        """Verify ProviderResponseDiagnostic excludes intermediate stream events for privacy."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_diag_test")
        events: list[ProviderStreamEvent] = []

        schema = {
            "type": "object",
            "properties": {"outline": {"type": "string"}},
            "required": ["outline"],
            "additionalProperties": False,
        }

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
            with mock.patch.object(adapter, "_has_current_finish", return_value=True):
                result = await adapter.invoke(binding, invocation)

        # Incomplete output attaches response_diagnostic with non-sensitive terminal output
        self.assertIsNotNone(result.response_diagnostic)
        self.assertEqual(result.response_diagnostic.stdout, stream_lines[2].strip())
        self.assertNotIn(b'{"type":"turn_start"}', result.response_diagnostic.stdout)
        self.assertNotIn(b"Thinking...", result.response_diagnostic.stdout)

    async def test_antigravity_adapter_strictly_rejects_duplicate_fields_in_streamed_result(
        self,
    ) -> None:
        """Verify duplicate fields in a streamed result object trigger strict malformed output rejection."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_dup_test")

        schema = {
            "type": "object",
            "properties": {"outline": {"type": "string"}},
            "required": ["outline"],
            "additionalProperties": False,
        }

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
            return ProcessResult(
                stdout=b"".join(stream_lines),
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await adapter.invoke(binding, invocation)
            self.assertIn("malformed JSON", str(cm.exception))

    async def test_codex_adapter_handles_structured_reasoning_and_cumulative_deltas(self) -> None:
        adapter = self.codex_adapter
        binding = adapter.bind(self.definition)
        events: list[ProviderStreamEvent] = []

        schema = {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
            "additionalProperties": False,
        }
        invocation = ProviderInvocation(
            session_id="session_codex_delta",
            schema=schema,
            payload={"task": "outline"},
            thread_id=None,
            stream_sink=events.append,
        )

        stream_lines = [
            b'{"type":"thread.started","thread_id":"thread_codex_delta"}\n',
            b'{"type":"turn.started"}\n',
            b'{"type":"item.started","item":{"id":"r1","type":"reasoning","summary":[{"type":"text","text":"Step 1"}]}}\n',
            b'{"type":"item.updated","item":{"id":"m1","type":"agent_message","text":"Chapter 1"}}\n',
            b'{"type":"item.updated","item":{"id":"m1","type":"agent_message","text":"Chapter 1: The Beginning"}}\n',
            b'{"type":"item.completed","item":{"id":"m1","type":"agent_message","text":"Chapter 1: The Beginning."}}\n',
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            output_flag_index = request.command.index("--output-last-message")
            output_file_path = Path(request.command[output_flag_index + 1])
            output_file_path.parent.mkdir(parents=True, exist_ok=True)
            output_file_path.write_text('{"summary":"Final"}', encoding="utf-8")
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return ProcessResult(
                stdout=b"".join(stream_lines),
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.codex_adapter.run_process", new=fake_run_process):
            result = await adapter.invoke(binding, invocation)

        self.assertEqual(result.response_text, '{"summary":"Final"}')
        self.assertEqual(len(events), 5)
        self.assertEqual(events[1].kind, ProviderStreamEventKind.THOUGHT_DELTA)
        self.assertEqual(events[1].text, "Step 1")
        self.assertEqual(events[2].kind, ProviderStreamEventKind.TEXT_DELTA)
        self.assertEqual(events[2].text, "Chapter 1")
        self.assertEqual(events[3].kind, ProviderStreamEventKind.TEXT_DELTA)
        self.assertEqual(events[3].text, ": The Beginning")
        self.assertEqual(events[4].kind, ProviderStreamEventKind.TEXT_DELTA)
        self.assertEqual(events[4].text, ".")

    async def test_antigravity_adapter_running_status_notice_emits_status_and_is_not_captured_as_terminal(self) -> None:
        agy_backend = AgentBackend(
            self.workspace,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
        )
        self.addCleanup(agy_backend.close)
        adapter = agy_backend._antigravity_adapter
        agy_definition = PromptAssembler(
            self.workspace,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
        ).build_definition(
            session_id="definition_session_agy_status",
            agent_id=PLOT_ARCHITECT,
        )
        binding = adapter.bind(agy_definition)
        events: list[ProviderStreamEvent] = []

        schema = {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
            "additionalProperties": False,
        }
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
            return ProcessResult(
                stdout=b"".join(stream_lines),
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            result = await adapter.invoke(binding, invocation)

        self.assertEqual(result.response_text, '{"summary":"Completed"}')
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, ProviderStreamEventKind.STATUS)
        self.assertEqual(events[0].text, "Model working...")

    async def test_antigravity_adapter_recognizes_wrapped_authentication_error(self) -> None:
        agy_backend = AgentBackend(
            self.workspace,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
        )
        self.addCleanup(agy_backend.close)
        adapter = agy_backend._antigravity_adapter
        agy_definition = PromptAssembler(
            self.workspace,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
        ).build_definition(
            session_id="session_wrapped_auth_def",
            agent_id=PLOT_ARCHITECT,
        )
        binding = adapter.bind(agy_definition)
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
                await adapter.invoke(binding, invocation)
            self.assertIsNotNone(exc_info.exception.authentication_failure)
            self.assertIsInstance(
                exc_info.exception.authentication_failure,
                ProviderAuthenticationRequiredError,
            )

    async def test_antigravity_adapter_unwraps_event_alias_result_envelope(self) -> None:
        agy_backend = AgentBackend(
            self.workspace,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
        )
        self.addCleanup(agy_backend.close)
        adapter = agy_backend._antigravity_adapter
        agy_definition = PromptAssembler(
            self.workspace,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
        ).build_definition(
            session_id="session_event_alias_def",
            agent_id=PLOT_ARCHITECT,
        )
        binding = adapter.bind(agy_definition)
        invocation = ProviderInvocation(
            session_id="session_agy_event_alias",
            schema={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"], "additionalProperties": False},
            payload={},
            thread_id="thread_alias_1",
            stream_sink=lambda _ev: None,
        )

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
            return ProcessResult(
                stdout=b"".join(stream_lines),
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            result = await adapter.invoke(binding, invocation)
        self.assertEqual(result.response_text, '{"msg":"Hello world"}')

    async def test_codex_adapter_handles_id_less_items_independently(self) -> None:
        adapter = self.codex_adapter
        binding = adapter.bind(self.definition)
        events: list[ProviderStreamEvent] = []

        invocation = ProviderInvocation(
            session_id="session_id_less",
            schema={"type": "object", "properties": {"done": {"type": "boolean"}}, "required": ["done"]},
            payload={},
            thread_id="thread_id_less",
            stream_sink=events.append,
        )

        stream_lines = [
            b'{"type":"turn.started"}\n',
            b'{"type":"item.updated","item":{"type":"reasoning","text":"Step 1"}}\n',
            b'{"type":"item.updated","item":{"type":"agent_message","text":"Step 1 result"}}\n',
            b'{"type":"turn.completed","result":{"structured_output":{"done":true}}}\n',
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            output_flag_index = request.command.index("--output-last-message")
            output_file_path = Path(request.command[output_flag_index + 1])
            output_file_path.parent.mkdir(parents=True, exist_ok=True)
            output_file_path.write_text('{"done":true}', encoding="utf-8")

            if request.on_stdout_line:
                for line in stream_lines:
                    request.on_stdout_line(line)
            return ProcessResult(
                stdout=b"".join(stream_lines),
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.codex_adapter.run_process", new=fake_run_process):
            result = await adapter.invoke(binding, invocation)

        self.assertEqual(result.response_text, '{"done":true}')
        text_events = [e for e in events if e.kind is ProviderStreamEventKind.TEXT_DELTA]
        self.assertEqual(len(text_events), 1)
        self.assertEqual(text_events[0].text, "Step 1 result")

    def test_provider_invocation_rejects_async_stream_sink(self) -> None:
        async def async_sink(_event: object) -> None:
            pass

        with self.assertRaises(TypeError) as exc:
            ProviderInvocation(
                session_id="session_async_sink",
                schema={"type": "object"},
                payload={},
                thread_id="thread_async_1",
                stream_sink=async_sink,
            )
    async def test_antigravity_adapter_rejects_multiple_terminal_results_in_stream(self) -> None:
        """Verify stream with multiple terminal results fails closed."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_multi_terminal")
        schema = {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
            "additionalProperties": False,
        }
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
            json.dumps(res2).encode("utf-8") + b"\n",
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return ProcessResult(
                stdout=b"".join(stream_lines),
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await adapter.invoke(binding, invocation)
            self.assertIn("2 terminal result events; expected exactly 1", str(cm.exception))

    async def test_antigravity_adapter_rejects_invalid_line_before_terminal_result(self) -> None:
        """Verify malformed line before valid terminal result triggers fail-closed error."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_invalid_line")
        schema = {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
            "additionalProperties": False,
        }
        invocation = ProviderInvocation(
            session_id="session_invalid_line",
            schema=schema,
            payload={},
            thread_id=None,
            stream_sink=lambda _event: None,
        )
        res = {
            "conversation_id": "c1",
            "status": "SUCCESS",
            "structured_output": {"status": "ok"},
            "json_schema": schema,
        }
        stream_lines = [
            b"not-json-line\n",
            json.dumps(res).encode("utf-8") + b"\n",
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return ProcessResult(
                stdout=b"".join(stream_lines),
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await adapter.invoke(binding, invocation)
            self.assertIn("invalid stream line: malformed JSON line", str(cm.exception))

    async def test_antigravity_adapter_rejects_trailing_data_after_terminal_result(self) -> None:
        """Verify trailing stream events after terminal result trigger fail-closed error."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_trailing_data")
        schema = {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
            "additionalProperties": False,
        }
        invocation = ProviderInvocation(
            session_id="session_trailing_data",
            schema=schema,
            payload={},
            thread_id=None,
            stream_sink=lambda _event: None,
        )
        res = {
            "conversation_id": "c1",
            "status": "SUCCESS",
            "structured_output": {"status": "ok"},
            "json_schema": schema,
        }
        stream_lines = [
            b'{"type":"turn_start"}\n',
            json.dumps(res).encode("utf-8") + b"\n",
            b'{"type":"text_delta","text":"extra trailing delta"}\n',
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return ProcessResult(
                stdout=b"".join(stream_lines),
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await adapter.invoke(binding, invocation)
            self.assertIn("trailing data after terminal result", str(cm.exception))

    async def test_antigravity_adapter_recognizes_authentication_failure_with_streaming_preamble(self) -> None:
        """Verify authentication failure is recognized when preceded by streaming preamble."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_auth_preamble")
        schema = {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
            "additionalProperties": False,
        }
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
                await adapter.invoke(binding, invocation)
            self.assertIsNotNone(cm.exception.authentication_failure)
            self.assertTrue(cm.exception.authentication_failure.replay_safe)
            self.assertFalse(cm.exception.taint_existing_thread)

    async def test_antigravity_adapter_rejects_duplicate_field_in_stream_line(self) -> None:
        """Verify duplicate fields in stream JSON lines are rejected."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_dup_field")
        schema = {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
            "additionalProperties": False,
        }
        invocation = ProviderInvocation(
            session_id="session_dup_field",
            schema=schema,
            payload={},
            thread_id=None,
            stream_sink=lambda _event: None,
        )
        res = {
            "conversation_id": "c1",
            "status": "SUCCESS",
            "structured_output": {"status": "ok"},
            "json_schema": schema,
        }
        stream_lines = [
            b'{"type":"status","text":"first","text":"duplicate"}\n',
            json.dumps(res).encode("utf-8") + b"\n",
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return ProcessResult(
                stdout=b"".join(stream_lines),
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await adapter.invoke(binding, invocation)
            self.assertIn("invalid stream line: malformed JSON line", str(cm.exception))

    async def test_antigravity_adapter_rejects_nan_or_infinity_in_stream_line(self) -> None:
        """Verify non-standard NaN / Infinity in stream JSON lines are rejected."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_nan_field")
        schema = {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
            "additionalProperties": False,
        }
        invocation = ProviderInvocation(
            session_id="session_nan_field",
            schema=schema,
            payload={},
            thread_id=None,
            stream_sink=lambda _event: None,
        )
        res = {
            "conversation_id": "c1",
            "status": "SUCCESS",
            "structured_output": {"status": "ok"},
            "json_schema": schema,
        }
        stream_lines = [
            b'{"type":"status","val":NaN}\n',
            json.dumps(res).encode("utf-8") + b"\n",
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return ProcessResult(
                stdout=b"".join(stream_lines),
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await adapter.invoke(binding, invocation)
            self.assertIn("invalid stream line: malformed JSON line", str(cm.exception))

    async def test_antigravity_adapter_rejects_recursion_error_in_stream_line(self) -> None:
        """Verify deeply nested stream JSON lines that trigger RecursionError are rejected fail-closed."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_deep_recursion")
        schema = {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
            "additionalProperties": False,
        }
        invocation = ProviderInvocation(
            session_id="session_deep_recursion",
            schema=schema,
            payload={},
            thread_id=None,
            stream_sink=lambda _event: None,
        )
        res = {
            "conversation_id": "c1",
            "status": "SUCCESS",
            "structured_output": {"status": "ok"},
            "json_schema": schema,
        }
        deep_nesting = b'{"a":' * 10000 + b'1' + b'}' * 10000
        stream_lines = [
            deep_nesting + b"\n",
            json.dumps(res).encode("utf-8") + b"\n",
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return ProcessResult(
                stdout=b"".join(stream_lines),
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await adapter.invoke(binding, invocation)
            self.assertIn("invalid stream line: malformed JSON line", str(cm.exception))

    async def test_antigravity_adapter_rejects_unknown_event_type_carrying_structured_output(self) -> None:
        """Verify non-empty unknown type/event field is rejected even when carrying structured_output."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_unknown_type_structured")
        schema = {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
            "additionalProperties": False,
        }
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
            return ProcessResult(
                stdout=b"".join(stream_lines),
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await adapter.invoke(binding, invocation)
            self.assertIn("invalid stream line: unknown event type 'custom_unknown'", str(cm.exception))

    async def test_antigravity_adapter_rejects_unknown_event_type_carrying_status_running(self) -> None:
        """Verify unknown event type is rejected even when status is 'running'."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_unknown_type_running")
        schema = {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
            "additionalProperties": False,
        }
        invocation = ProviderInvocation(
            session_id="session_unknown_type_running",
            schema=schema,
            payload={},
            thread_id=None,
            stream_sink=lambda _event: None,
        )
        res = {
            "conversation_id": "c1",
            "status": "SUCCESS",
            "structured_output": {"status": "ok"},
            "json_schema": schema,
        }
        stream_lines = [
            b'{"type":"unexpected_type","status":"running","text":"Working..."}\n',
            json.dumps(res).encode("utf-8") + b"\n",
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return ProcessResult(
                stdout=b"".join(stream_lines),
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await adapter.invoke(binding, invocation)
            self.assertIn("invalid stream line: unknown event type 'unexpected_type'", str(cm.exception))

    async def test_antigravity_adapter_fails_closed_when_stream_contains_invalid_line_before_auth_terminal(self) -> None:
        """Verify malformed/unknown stream line fails closed before auth recovery is considered."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_invalid_before_auth")
        schema = {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
            "additionalProperties": False,
        }
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
                await adapter.invoke(binding, invocation)
            # Should fail closed on invalid stream line, NOT enter auth recovery
            self.assertIn("invalid stream line: unknown event type 'bogus_event'", str(cm.exception))
            self.assertIsNone(cm.exception.authentication_failure)

    async def test_antigravity_adapter_rejects_explicit_null_type_discriminator(self) -> None:
        """Verify explicit null type discriminator is rejected fail-closed and not inferred as legacy."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_null_type")
        schema = {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
            "additionalProperties": False,
        }
        invocation = ProviderInvocation(
            session_id="session_null_type",
            schema=schema,
            payload={},
            thread_id=None,
            stream_sink=lambda _event: None,
        )
        stream_lines = [
            b'{"type":null,"structured_output":{"status":"ok"},"conversation_id":"c1"}\n',
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return ProcessResult(
                stdout=b"".join(stream_lines),
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await adapter.invoke(binding, invocation)
            self.assertIn("invalid stream line: invalid event discriminator", str(cm.exception))

    async def test_antigravity_adapter_rejects_explicit_null_event_discriminator_with_running_status(self) -> None:
        """Verify explicit null event discriminator with status 'running' is rejected fail-closed."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_null_event_running")
        schema = {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
            "additionalProperties": False,
        }
        invocation = ProviderInvocation(
            session_id="session_null_event_running",
            schema=schema,
            payload={},
            thread_id=None,
            stream_sink=lambda _event: None,
        )
        res = {
            "conversation_id": "c1",
            "status": "SUCCESS",
            "structured_output": {"status": "ok"},
            "json_schema": schema,
        }
        stream_lines = [
            b'{"event":null,"status":"running"}\n',
            json.dumps(res).encode("utf-8") + b"\n",
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return ProcessResult(
                stdout=b"".join(stream_lines),
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await adapter.invoke(binding, invocation)
            self.assertIn("invalid stream line: invalid event discriminator", str(cm.exception))

    async def test_antigravity_adapter_unwraps_result_envelope_with_whitespace_discriminator(self) -> None:
        """Verify _unwrap_result_envelope normalizes whitespace in discriminators."""
        adapter, binding = self._make_agy_binding("session_whitespace_disc")
        schema = {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
            "additionalProperties": False,
        }
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
            return ProcessResult(
                stdout=b"".join(stream_lines),
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            result = await adapter.invoke(binding, invocation)
        self.assertEqual(result.response_text, '{"status":"ok"}')

    async def test_antigravity_adapter_rejects_conflicting_event_discriminators(self) -> None:
        """Verify stream lines with conflicting type and event keys are rejected fail-closed."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_conflict_disc")
        schema = {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
            "additionalProperties": False,
        }
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
            return ProcessResult(
                stdout=b"".join(stream_lines),
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await adapter.invoke(binding, invocation)
            self.assertIn("invalid stream line: conflicting event discriminator", str(cm.exception))

    async def test_antigravity_adapter_rejects_one_null_discriminator_when_other_present(self) -> None:
        """Verify stream line with type='result' and event=null is rejected fail-closed."""
        from story_writing_agents.provider_adapter import ProviderInvocationError

        adapter, binding = self._make_agy_binding("session_one_null_disc")
        schema = {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
            "additionalProperties": False,
        }
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
            return ProcessResult(
                stdout=b"".join(stream_lines),
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            with self.assertRaises(ProviderInvocationError) as cm:
                await adapter.invoke(binding, invocation)
            self.assertIn("invalid stream line: invalid event discriminator", str(cm.exception))

    async def test_codex_adapter_streams_item_delta_and_avoids_completed_replay(self) -> None:
        """Verify CodexAdapter streams item.delta records, folds them, and avoids replay on item.completed."""
        adapter = self.codex_adapter
        binding = adapter.bind(self.definition)
        events: list[ProviderStreamEvent] = []

        schema = {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
            "additionalProperties": False,
        }
        invocation = ProviderInvocation(
            session_id="session_codex_item_delta",
            schema=schema,
            payload={"task": "draft"},
            thread_id=None,
            stream_sink=events.append,
        )

        stream_lines = [
            b'{"type":"thread.started","thread_id":"thread_codex_delta_1"}\n',
            b'{"type":"turn.started"}\n',
            b'{"type":"item.started","item":{"id":"r1","type":"reasoning"}}\n',
            b'{"type":"item.delta","item_id":"r1","delta":"Analyzing requirements..."}\n',
            b'{"type":"item.completed","item":{"id":"r1","type":"reasoning","text":"Analyzing requirements..."}}\n',
            b'{"type":"item.started","item":{"id":"m1","type":"agent_message"}}\n',
            b'{"type":"item.delta","item_id":"m1","delta":"The dawn "}\n',
            b'{"type":"item.delta","item_id":"m1","delta":"broke over "}\n',
            b'{"type":"item.delta","item_id":"m1","delta":"the valley."}\n',
            b'{"type":"item.completed","item":{"id":"m1","type":"agent_message","text":"The dawn broke over the valley."}}\n',
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            output_flag_index = request.command.index("--output-last-message")
            output_file_path = Path(request.command[output_flag_index + 1])
            output_file_path.parent.mkdir(parents=True, exist_ok=True)
            output_file_path.write_text('{"summary":"Completed story"}', encoding="utf-8")
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return ProcessResult(
                stdout=b"".join(stream_lines),
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.codex_adapter.run_process", new=fake_run_process):
            result = await adapter.invoke(binding, invocation)

        self.assertEqual(result.response_text, '{"summary":"Completed story"}')
        # Expect:
        # 0: STATUS ("Generating response...")
        # 1: THOUGHT_DELTA ("Analyzing requirements...")
        # 2: TEXT_DELTA ("The dawn ")
        # 3: TEXT_DELTA ("broke over ")
        # 4: TEXT_DELTA ("the valley.")
        # item.completed should NOT emit replay deltas because full text was already folded
        self.assertEqual(len(events), 5)
        self.assertEqual(events[0].kind, ProviderStreamEventKind.STATUS)
        self.assertEqual(events[1].kind, ProviderStreamEventKind.THOUGHT_DELTA)
        self.assertEqual(events[1].text, "Analyzing requirements...")
        self.assertEqual(events[2].kind, ProviderStreamEventKind.TEXT_DELTA)
        self.assertEqual(events[2].text, "The dawn ")
        self.assertEqual(events[3].kind, ProviderStreamEventKind.TEXT_DELTA)
        self.assertEqual(events[3].text, "broke over ")
        self.assertEqual(events[4].kind, ProviderStreamEventKind.TEXT_DELTA)
        self.assertEqual(events[4].text, "the valley.")


if __name__ == "__main__":
    unittest.main()
