"""Unit and integration tests for live streaming in CodexAdapter and AntigravityAdapter."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from story_writing_agents import AgentBackend, PLOT_ARCHITECT
from story_writing_agents.agent_context import (
    ProviderStreamEvent,
    ProviderStreamEventKind,
)
from story_writing_agents.prompts import PromptAssembler
from story_writing_agents.provider_adapter import ProviderInvocation
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

    async def test_antigravity_adapter_preserves_full_stdout_in_response_diagnostic(
        self,
    ) -> None:
        """Verify ProviderResponseDiagnostic retains full process stdout containing stream events."""
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

        # Incomplete output attaches response_diagnostic with FULL process stdout
        self.assertIsNotNone(result.response_diagnostic)
        self.assertEqual(result.response_diagnostic.stdout, full_stdout)
        self.assertIn(b'{"type":"turn_start"}', result.response_diagnostic.stdout)

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
            self.assertEqual(str(cm.exception), "Antigravity CLI returned malformed JSON")


if __name__ == "__main__":
    unittest.main()
