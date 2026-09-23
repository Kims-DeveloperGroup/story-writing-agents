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

    async def test_codex_adapter_rejects_duplicate_keys_and_nonfinite_values_in_stream(self) -> None:
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
            session_id="session_codex_stream_strict",
            schema=schema,
            payload=payload,
            thread_id=None,
            stream_sink=sink,
        )

        malformed_stream_lines = [
            b'{"type":"reasoning","type":"agent_message","item":{"type":"agent_message","text":"Leaked"}}\n',
            b'{"type":"item.updated","item":{"type":"reasoning","type":"agent_message","text":"Secret"}}\n',
            b'{"type":"item.updated","item":{"type":"agent_message","text":"Val","score":NaN}}\n',
            b'{"type":"turn.started"}\n',
            b'{"type":"item.updated","item":{"type":"agent_message","text":"Valid text"}}\n',
        ]

        async def fake_run_process(
            request: ProcessRequest,
            **_kwargs: object,
        ) -> ProcessResult:
            output_flag_index = request.command.index("--output-last-message")
            output_file_path = Path(request.command[output_flag_index + 1])
            output_file_path.parent.mkdir(parents=True, exist_ok=True)
            output_file_path.write_text('{"summary":"Valid"}', encoding="utf-8")

            assert request.on_stdout_line is not None
            for line in malformed_stream_lines:
                request.on_stdout_line(line)

            return ProcessResult(
                stdout=b"".join(malformed_stream_lines),
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.codex_adapter.run_process", new=fake_run_process):
            try:
                await adapter.invoke(binding, invocation)
            except Exception:
                pass

        emitted_texts = [e.text for e in events]
        self.assertNotIn("Leaked", emitted_texts)
        self.assertNotIn("Secret", emitted_texts)
        self.assertNotIn("Val", emitted_texts)
        self.assertIn("Generating response...", emitted_texts)
        self.assertIn("Valid text", emitted_texts)

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
            b'{"type":"agent_message","text":"The banners waved in the freezing mist."}\n',
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

    async def test_codex_adapter_item_delta_derives_nested_type_and_fails_closed_on_unknown(self) -> None:
        """Verify item.delta derives kind from nested item.type and fails closed on unknown/untyped items."""
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
            session_id="session_codex_nested_delta",
            schema=schema,
            payload={"task": "draft"},
            thread_id=None,
            stream_sink=events.append,
        )

        stream_lines = [
            b'{"type":"thread.started","thread_id":"thread_nested_1"}\n',
            # Nested reasoning delta without prior item.started: must be THOUGHT_DELTA, not TEXT_DELTA
            b'{"type":"item.delta","item":{"id":"r_nested","type":"reasoning","delta":"Internal plan"}}\n',
            # Unknown item type: must fail closed and NOT emit any event
            b'{"type":"item.delta","item":{"id":"tool_1","type":"custom_tool","delta":"do_something()"}}\n',
            # Untyped delta without prior classification: must fail closed and NOT emit
            b'{"type":"item.delta","item_id":"untyped_1","delta":"untyped leak?"}\n',
            # Nested agent_message delta: must be TEXT_DELTA
            b'{"type":"item.delta","item":{"id":"msg_nested","type":"agent_message","delta":"Visible story"}}\n',
            # Completion of msg_nested: should clean up and not replay
            b'{"type":"item.completed","item":{"id":"msg_nested","type":"agent_message","text":"Visible story"}}\n',
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            output_flag_index = request.command.index("--output-last-message")
            output_file_path = Path(request.command[output_flag_index + 1])
            output_file_path.parent.mkdir(parents=True, exist_ok=True)
            output_file_path.write_text('{"summary":"Done"}', encoding="utf-8")
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

        self.assertEqual(result.response_text, '{"summary":"Done"}')
        # Exactly 2 events should have been emitted:
        # 0: THOUGHT_DELTA ("Internal plan")
        # 1: TEXT_DELTA ("Visible story")
        # Unknown/untyped items were rejected fail-closed, and item.completed had zero replay
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].kind, ProviderStreamEventKind.THOUGHT_DELTA)
        self.assertEqual(events[0].text, "Internal plan")
        self.assertEqual(events[1].kind, ProviderStreamEventKind.TEXT_DELTA)
        self.assertEqual(events[1].text, "Visible story")

    async def test_codex_adapter_rejects_conflicting_or_mismatched_item_type_on_delta(self) -> None:
        """Verify delta with mismatched or unknown explicit type fails closed even if item_id was classified."""
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
            session_id="session_codex_mismatch",
            schema=schema,
            payload={"task": "draft"},
            thread_id=None,
            stream_sink=events.append,
        )

        stream_lines = [
            b'{"type":"thread.started","thread_id":"thread_mismatch_1"}\n',
            # Item started as agent_message
            b'{"type":"item.started","item":{"id":"m_typed","type":"agent_message"}}\n',
            # Mismatched delta claiming reasoning on agent_message: must fail closed (do NOT emit)
            b'{"type":"item.delta","item_id":"m_typed","item_type":"reasoning","delta":"leaked thought?"}\n',
            # Unknown item_type on delta: must fail closed (do NOT emit)
            b'{"type":"item.delta","item_id":"m_typed","item_type":"custom_tool","delta":"tool text"}\n',
            # Matching delta with explicit agent_message type: must emit TEXT_DELTA
            b'{"type":"item.delta","item_id":"m_typed","item_type":"agent_message","delta":"Valid text "}\n',
            # Delta without explicit type falls back to remembered agent_message: must emit TEXT_DELTA
            b'{"type":"item.delta","item_id":"m_typed","delta":"part two"}\n',
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            output_flag_index = request.command.index("--output-last-message")
            output_file_path = Path(request.command[output_flag_index + 1])
            output_file_path.parent.mkdir(parents=True, exist_ok=True)
            output_file_path.write_text('{"summary":"Done"}', encoding="utf-8")
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

        self.assertEqual(result.response_text, '{"summary":"Done"}')
        # Exactly 2 events should have been emitted (the matching and remembered agent_message deltas):
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].kind, ProviderStreamEventKind.TEXT_DELTA)
        self.assertEqual(events[0].text, "Valid text ")
        self.assertEqual(events[1].kind, ProviderStreamEventKind.TEXT_DELTA)
        self.assertEqual(events[1].text, "part two")

    async def test_codex_adapter_rejects_conflicting_top_level_and_nested_descriptors(self) -> None:
        """Verify delta or item events with conflicting top-level and nested type/id fail closed."""
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
            session_id="session_codex_conflict",
            schema=schema,
            payload={"task": "draft"},
            thread_id=None,
            stream_sink=events.append,
        )

        stream_lines = [
            b'{"type":"thread.started","thread_id":"thread_conflict_1"}\n',
            # Conflicting top-level item_type (agent_message) and nested type (reasoning) on delta: reject fail-closed
            b'{"type":"item.delta","item_id":"i1","item_type":"agent_message","item":{"id":"i1","type":"reasoning"},"delta":"secret thought"}\n',
            # Conflicting top-level item_type (reasoning) and nested type (agent_message) on delta: reject fail-closed
            b'{"type":"item.delta","item_id":"i2","item_type":"reasoning","item":{"id":"i2","type":"agent_message"},"delta":"secret text"}\n',
            # Conflicting top-level item_id (i3) and nested id (i4) on delta: reject fail-closed
            b'{"type":"item.delta","item_id":"i3","item":{"id":"i4","type":"agent_message"},"delta":"id mismatch"}\n',
            # Conflicting top-level item_type and nested type on item.updated: reject fail-closed
            b'{"type":"item.updated","item_id":"i5","item_type":"agent_message","item":{"id":"i5","type":"reasoning","text":"secret update"}}\n',
            # Conflicting top-level item_id and nested id on item.updated: reject fail-closed
            b'{"type":"item.updated","item_id":"i6","item":{"id":"i7","type":"agent_message","text":"id mismatch update"}}\n',
            # Conflicting nested type and item_type aliases on delta: reject fail-closed
            b'{"type":"item.delta","item":{"id":"i9","type":"agent_message","item_type":"reasoning"},"delta":"alias type conflict"}\n',
            # Conflicting nested id and item_id aliases on delta: reject fail-closed
            b'{"type":"item.delta","item":{"id":"i10","item_id":"i11","type":"agent_message"},"delta":"alias id conflict"}\n',
            # Conflicting nested delta and text aliases on delta: reject fail-closed
            b'{"type":"item.delta","item_id":"i12","item_type":"agent_message","item":{"delta":"part a","text":"part b"}}\n',
            # Conflicting nested type and item_type aliases on item.updated: reject fail-closed
            b'{"type":"item.updated","item":{"id":"i13","type":"agent_message","item_type":"reasoning","text":"alias update type conflict"}}\n',
            # Conflicting nested id and item_id aliases on item.updated: reject fail-closed
            b'{"type":"item.updated","item":{"id":"i14","item_id":"i15","type":"agent_message","text":"alias update id conflict"}}\n',
            # Valid item delta with matching top-level and nested types: must emit TEXT_DELTA
            b'{"type":"item.delta","item_id":"i8","item_type":"agent_message","item":{"id":"i8","type":"agent_message"},"delta":"clean valid text"}\n',
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            output_flag_index = request.command.index("--output-last-message")
            output_file_path = Path(request.command[output_flag_index + 1])
            output_file_path.parent.mkdir(parents=True, exist_ok=True)
            output_file_path.write_text('{"summary":"Done"}', encoding="utf-8")
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

        self.assertEqual(result.response_text, '{"summary":"Done"}')
        # Exactly 1 event should have been emitted (the matching valid delta):
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, ProviderStreamEventKind.TEXT_DELTA)
        self.assertEqual(events[0].text, "clean valid text")

    async def test_antigravity_adapter_streams_native_step_update_and_init_events(self) -> None:
        agy_backend = AgentBackend(
            self.workspace,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
        )
        self.addCleanup(agy_backend.close)
        adapter = agy_backend._antigravity_adapter
        definition = PromptAssembler(
            self.workspace,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
        ).build_definition(
            session_id="session_agy_native_stream",
            agent_id=PLOT_ARCHITECT,
        )
        binding = adapter.bind(definition)

        schema = {
            "type": "object",
            "properties": {"reply": {"type": "string"}},
            "required": ["reply"],
            "additionalProperties": False,
        }
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
            }).encode("utf-8") + b"\n",
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
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
        agy_backend = AgentBackend(
            self.workspace,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
        )
        self.addCleanup(agy_backend.close)
        adapter = agy_backend._antigravity_adapter
        definition = PromptAssembler(
            self.workspace,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
        ).build_definition(
            session_id="session_agy_err_code",
            agent_id=PLOT_ARCHITECT,
        )
        binding = adapter.bind(definition)

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
                "response": "",
                "error": "No capacity available for model gpt-oss-120b-medium on the server",
            },
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
                await adapter.invoke(binding, invocation)

        self.assertEqual(str(cm.exception), "Antigravity CLI exited with status 1")
        # Verify status event was emitted to stream sink
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, ProviderStreamEventKind.STATUS)
        self.assertIn("No capacity available", events[0].text)

    async def test_antigravity_adapter_surfaces_provider_stderr_on_non_zero_returncode(self) -> None:
        agy_backend = AgentBackend(
            self.workspace,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
        )
        self.addCleanup(agy_backend.close)
        adapter = agy_backend._antigravity_adapter
        definition = PromptAssembler(
            self.workspace,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
        ).build_definition(
            session_id="session_agy_stderr_err",
            agent_id=PLOT_ARCHITECT,
        )
        binding = adapter.bind(definition)

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
                await adapter.invoke(binding, invocation)

        self.assertEqual(str(cm.exception), "Antigravity CLI exited with status 1")
        self.assertIsNotNone(cm.exception.response_diagnostic)
        self.assertIn(b"invalid model selection (--model foo): unknown model", cm.exception.response_diagnostic.stderr)

    async def test_antigravity_adapter_replays_stdout_when_no_streaming_callbacks_delivered(self) -> None:
        agy_backend = AgentBackend(
            self.workspace,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
        )
        self.addCleanup(agy_backend.close)
        adapter = agy_backend._antigravity_adapter
        definition = PromptAssembler(
            self.workspace,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
        ).build_definition(
            session_id="session_agy_fallback",
            agent_id=PLOT_ARCHITECT,
        )
        binding = adapter.bind(definition)

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
            result = await adapter.invoke(binding, invocation)

        self.assertEqual(result.thread_id, "conv_fallback_1")
        self.assertEqual(json.loads(result.response_text), {"reply": "Fallback text."})
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].kind, ProviderStreamEventKind.STATUS)
        self.assertEqual(events[1].kind, ProviderStreamEventKind.TEXT_DELTA)
        self.assertEqual(events[1].text, "Fallback text.")

    async def test_codex_adapter_rejects_invalid_utf8_in_stream_line(self) -> None:
        definition = PromptAssembler(self.workspace).build_definition(
            session_id="session_codex_utf8",
            agent_id=PLOT_ARCHITECT,
        )
        binding = self.codex_adapter.bind(definition)
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        events: list[ProviderStreamEvent] = []
        invocation = ProviderInvocation(
            session_id="session_codex_utf8",
            schema=schema,
            payload={"task": "draft"},
            thread_id=None,
            stream_sink=events.append,
        )

        stream_lines = [
            b'{"type":"turn.started"}\n',
            b'{"type":"item.delta","item_id":"i1","item_type":"agent_message","delta":"\xff\xfe"}\n',
            b'{"type":"item.delta","item_id":"i1","item_type":"agent_message","delta":"Valid text"}\n',
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            self.assertIsNotNone(request.on_stdout_line)
            for line in stream_lines:
                request.on_stdout_line(line)
            output_flag_index = request.command.index("--output-last-message")
            output_file_path = Path(request.command[output_flag_index + 1])
            output_file_path.parent.mkdir(parents=True, exist_ok=True)
            output_file_path.write_text('{"summary":"Final plot outline"}', encoding="utf-8")
            return ProcessResult(
                stdout=b'{"type":"thread.started","thread_id":"t_utf8"}\n{"type":"turn.completed"}\n',
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.codex_adapter.run_process", new=fake_run_process):
            await self.codex_adapter.invoke(binding, invocation)

        # Invalid UTF-8 line must be skipped and not emitted
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].kind, ProviderStreamEventKind.STATUS)
        self.assertEqual(events[1].kind, ProviderStreamEventKind.TEXT_DELTA)
        self.assertEqual(events[1].text, "Valid text")

    async def test_codex_adapter_rejects_explicit_null_or_non_string_item_type_on_delta(self) -> None:
        definition = PromptAssembler(self.workspace).build_definition(
            session_id="session_codex_null_desc",
            agent_id=PLOT_ARCHITECT,
        )
        binding = self.codex_adapter.bind(definition)
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        events: list[ProviderStreamEvent] = []
        invocation = ProviderInvocation(
            session_id="session_codex_null_desc",
            schema=schema,
            payload={"task": "draft"},
            thread_id=None,
            stream_sink=events.append,
        )

        stream_lines = [
            # First classify item m1 as agent_message
            b'{"type":"item.started","item_id":"m1","item_type":"agent_message","item":{"content":""}}\n',
            # Then send item.delta with explicit null item_type
            b'{"type":"item.delta","item_id":"m1","item_type":null,"delta":"secret"}\n',
            # Send item.delta with non-string item_id
            b'{"type":"item.delta","item_id":123,"item_type":"agent_message","delta":"ignored"}\n',
            # Send valid item.delta with omitted item_type (allowed to use remembered kind)
            b'{"type":"item.delta","item_id":"m1","delta":"visible"}\n',
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            self.assertIsNotNone(request.on_stdout_line)
            for line in stream_lines:
                request.on_stdout_line(line)
            output_flag_index = request.command.index("--output-last-message")
            output_file_path = Path(request.command[output_flag_index + 1])
            output_file_path.parent.mkdir(parents=True, exist_ok=True)
            output_file_path.write_text('{"summary":"Final plot outline"}', encoding="utf-8")
            return ProcessResult(
                stdout=b'{"type":"thread.started","thread_id":"t_null"}\n{"type":"turn.completed"}\n',
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.codex_adapter.run_process", new=fake_run_process):
            await self.codex_adapter.invoke(binding, invocation)

        # "secret" and "ignored" must be rejected fail-closed; only "visible" is emitted
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, ProviderStreamEventKind.TEXT_DELTA)
        self.assertEqual(events[0].text, "visible")

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
            return ProcessResult(
                stdout=b"".join(stream_lines),
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            await adapter.invoke(binding, invocation)

        text_deltas = [ev.text for ev in events if ev.kind == ProviderStreamEventKind.TEXT_DELTA]
        thought_deltas = [ev.text for ev in events if ev.kind == ProviderStreamEventKind.THOUGHT_DELTA]
        self.assertEqual(text_deltas, ["valid_response"])
        self.assertEqual(thought_deltas, ["thinking"])

    async def test_codex_adapter_item_completed_retires_state_on_conflicting_descriptor(self) -> None:
        adapter = self.codex_adapter
        binding = adapter.bind(self.definition)
        events: list[ProviderStreamEvent] = []

        invocation = ProviderInvocation(
            session_id="session_codex_retire",
            schema={"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]},
            payload={},
            thread_id="thread_retire_1",
            stream_sink=events.append,
        )

        stream_lines = [
            b'{"type":"turn.started"}\n',
            b'{"type":"item.started","item":{"id":"m1","type":"agent_message","text":"start"}}\n',
            # Conflicting completed item: claims reasoning when started as agent_message
            b'{"type":"item.completed","item":{"id":"m1","type":"reasoning","text":"conflict"}}\n',
            # Later id-only delta should not reuse retired state
            b'{"type":"item.delta","item_id":"m1","delta":"stale_delta"}\n',
            b'{"type":"turn.completed"}\n',
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            self.assertIsNotNone(request.on_stdout_line)
            for line in stream_lines:
                request.on_stdout_line(line)
            output_flag_index = request.command.index("--output-last-message")
            output_file_path = Path(request.command[output_flag_index + 1])
            output_file_path.parent.mkdir(parents=True, exist_ok=True)
            output_file_path.write_text('{"summary":"Completed"}', encoding="utf-8")
            return ProcessResult(
                stdout=b'{"type":"thread.started","thread_id":"thread_retire_1"}\n{"type":"turn.completed"}\n',
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.codex_adapter.run_process", new=fake_run_process):
            await self.codex_adapter.invoke(binding, invocation)

        # Only "start" from item.started should be emitted; conflict and stale delta must be ignored
        emitted_texts = [ev.text for ev in events if ev.kind == ProviderStreamEventKind.TEXT_DELTA]
        self.assertEqual(emitted_texts, ["start"])

    async def test_codex_adapter_retires_both_candidate_ids_on_conflicting_id_descriptor(self) -> None:
        adapter = self.codex_adapter
        binding = adapter.bind(self.definition)
        events: list[ProviderStreamEvent] = []

        invocation = ProviderInvocation(
            session_id="session_codex_retire_both",
            schema={"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]},
            payload={},
            thread_id="thread_retire_both",
            stream_sink=events.append,
        )

        stream_lines = [
            b'{"type":"turn.started"}\n',
            b'{"type":"item.started","item":{"id":"id_a","type":"agent_message","text":"text_a"}}\n',
            b'{"type":"item.started","item":{"id":"id_b","type":"agent_message","text":"text_b"}}\n',
            # Conflicting completed item: top-level id is id_a, but nested item.id is id_b
            b'{"type":"item.completed","id":"id_a","item":{"id":"id_b","type":"agent_message","text":"both"}}\n',
            # Later id-only deltas for both id_a and id_b should be ignored
            b'{"type":"item.delta","item_id":"id_a","delta":"stale_delta_a"}\n',
            b'{"type":"item.delta","item_id":"id_b","delta":"stale_delta_b"}\n',
            b'{"type":"turn.completed"}\n',
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            self.assertIsNotNone(request.on_stdout_line)
            for line in stream_lines:
                request.on_stdout_line(line)
            output_flag_index = request.command.index("--output-last-message")
            output_file_path = Path(request.command[output_flag_index + 1])
            output_file_path.parent.mkdir(parents=True, exist_ok=True)
            output_file_path.write_text('{"summary":"Completed"}', encoding="utf-8")
            return ProcessResult(
                stdout=b'{"type":"thread.started","thread_id":"thread_retire_both"}\n{"type":"turn.completed"}\n',
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.codex_adapter.run_process", new=fake_run_process):
            await self.codex_adapter.invoke(binding, invocation)

        # Only initial texts from item.started should be emitted; both IDs retired
        emitted_texts = [ev.text for ev in events if ev.kind == ProviderStreamEventKind.TEXT_DELTA]
        self.assertEqual(emitted_texts, ["text_a", "text_b"])

    async def test_antigravity_adapter_extracts_status_and_error_from_legacy_terminal_shape(self) -> None:
        """Verify legacy terminal error event without result envelope emits stream error status."""
        adapter, binding = self._make_agy_binding("session_legacy_error")
        events: list[ProviderStreamEvent] = []
        schema = {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
            "additionalProperties": False,
        }
        invocation = ProviderInvocation(
            session_id="session_legacy_error",
            schema=schema,
            payload={},
            thread_id=None,
            stream_sink=events.append,
        )

        stream_lines = [
            b'{"type":"turn_start"}\n',
            b'{"conversation_id":"c1","status":"ERROR","error":"Quota exceeded for tier"}\n',
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
                await adapter.invoke(binding, invocation)

        status_events = [ev for ev in events if ev.kind == ProviderStreamEventKind.STATUS]
        self.assertTrue(any("Error: Quota exceeded for tier" in ev.text for ev in status_events))

    async def test_codex_adapter_retires_top_level_id_on_item_completed_with_missing_nested_item(self) -> None:
        """Verify item.completed without nested item dict retires top-level ID."""
        binding = self.codex_adapter.bind(self.definition)
        events: list[ProviderStreamEvent] = []
        invocation = ProviderInvocation(
            session_id="session_retire_nondict",
            schema={"type": "object"},
            payload={},
            thread_id="thread_retire_nondict",
            stream_sink=events.append,
        )

        stream_lines = [
            b'{"type":"turn.started"}\n',
            b'{"type":"item.started","item":{"id":"m1","type":"agent_message","text":"hello"}}\n',
            # item.completed has top-level item_id but item is null / missing
            b'{"type":"item.completed","item_id":"m1"}\n',
            # Later delta for m1 must be ignored
            b'{"type":"item.delta","item_id":"m1","delta":"stale"}\n',
            b'{"type":"turn.completed"}\n',
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            output_flag_index = request.command.index("--output-last-message")
            output_file_path = Path(request.command[output_flag_index + 1])
            output_file_path.parent.mkdir(parents=True, exist_ok=True)
            output_file_path.write_text('{"summary":"Done"}', encoding="utf-8")
            return ProcessResult(
                stdout=b'{"type":"thread.started","thread_id":"thread_retire_nondict"}\n{"type":"turn.completed"}\n',
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.codex_adapter.run_process", new=fake_run_process):
            await self.codex_adapter.invoke(binding, invocation)

        emitted_texts = [ev.text for ev in events if ev.kind == ProviderStreamEventKind.TEXT_DELTA]
        self.assertEqual(emitted_texts, ["hello"])

    async def test_codex_adapter_rejects_resurrected_completed_item_id_with_explicit_type(self) -> None:
        """Verify completed item IDs cannot be resurrected even with explicit item_type."""
        binding = self.codex_adapter.bind(self.definition)
        events: list[ProviderStreamEvent] = []
        invocation = ProviderInvocation(
            session_id="session_tombstone_resurrect",
            schema={"type": "object"},
            payload={},
            thread_id="thread_tombstone_1",
            stream_sink=events.append,
        )

        stream_lines = [
            b'{"type":"turn.started"}\n',
            b'{"type":"item.started","item":{"id":"item_done","type":"agent_message","text":"first part"}}\n',
            b'{"type":"item.completed","item":{"id":"item_done","type":"agent_message","text":"first part"}}\n',
            # Resurrection attempt with explicit type
            b'{"type":"item.started","item":{"id":"item_done","type":"agent_message","text":"resurrected"}}\n',
            b'{"type":"item.delta","item_id":"item_done","item_type":"agent_message","delta":" delta"}\n',
            b'{"type":"turn.completed"}\n',
        ]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            output_flag_index = request.command.index("--output-last-message")
            output_file_path = Path(request.command[output_flag_index + 1])
            output_file_path.parent.mkdir(parents=True, exist_ok=True)
            output_file_path.write_text('{"summary":"Done"}', encoding="utf-8")
            return ProcessResult(
                stdout=b'{"type":"thread.started","thread_id":"thread_tombstone_1"}\n{"type":"turn.completed"}\n',
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.codex_adapter.run_process", new=fake_run_process):
            await self.codex_adapter.invoke(binding, invocation)

        emitted_texts = [ev.text for ev in events if ev.kind == ProviderStreamEventKind.TEXT_DELTA]
        self.assertEqual(emitted_texts, ["first part"])

    def test_codex_adapter_extract_stream_text_ignores_non_string_values(self) -> None:
        """Verify _extract_stream_text accepts only string text and ignores non-string metadata."""
        from story_writing_agents.codex_adapter import _extract_stream_text

        self.assertEqual(_extract_stream_text("valid"), "valid")
        self.assertEqual(_extract_stream_text({"text": "hello"}), "hello")
        self.assertEqual(_extract_stream_text({"text": 123}), "")
        self.assertEqual(_extract_stream_text({"content": {"nested": "obj"}}), "")
        self.assertEqual(_extract_stream_text([{"text": "a"}, {"text": 42}, "b"]), "ab")

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
            return ProcessResult(
                stdout=b"".join(stream_lines),
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            await adapter.invoke(binding, invocation)

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
            return ProcessResult(
                stdout=b"".join(stream_lines),
                stderr=b"",
                returncode=0,
            )

        with mock.patch("story_writing_agents.antigravity_adapter.run_process", new=fake_run_process):
            await adapter.invoke(binding, invocation)

        text_deltas = [e.text for e in events if e.kind == ProviderStreamEventKind.TEXT_DELTA]
        self.assertEqual(
            text_deltas,
            [
                "typed delta accepted",
                "agent message accepted",
                "agent response accepted",
            ],
        )


if __name__ == "__main__":
    unittest.main()


