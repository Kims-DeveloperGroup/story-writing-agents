"""Codex provider streaming and event folding tests."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from story_writing_agents import PLOT_ARCHITECT
from story_writing_agents.agent_context.progress import (
    ProviderStreamEvent,
    ProviderStreamEventKind,
)
from story_writing_agents.prompts import PromptAssembler
from story_writing_agents.provider_adapter import ProviderInvocation
from story_writing_agents.provider_process import ProcessRequest, ProcessResult
from tests.adapter_streaming_fixture import (
    _invoke_codex,
    _string_result_schema,
    _successful_stream_result,
    setUp as setup_streaming,
)


class CodexAdapterStreamingTests(unittest.IsolatedAsyncioTestCase):
    setUp = setup_streaming
    _invoke_codex = _invoke_codex

    async def test_codex_adapter_streams_status_reasoning_and_message_events(self) -> None:
        adapter = self.codex_adapter
        binding = adapter.bind(self.definition)

        events: list[ProviderStreamEvent] = []
        sink = events.append

        schema = _string_result_schema('summary')
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
            b'\n',]

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

            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.codex_adapter.run_process", new=fake_run_process):
            result = await self._invoke_codex(binding, invocation)

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

        schema = _string_result_schema('summary')
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
            b'{"type":"item.updated","item":{"type":"agent_message","text":"Valid text"}}\n',]

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
                await self._invoke_codex(binding, invocation)
            except Exception:
                pass

        emitted_texts = [e.text for e in events]
        self.assertNotIn("Leaked", emitted_texts)
        self.assertNotIn("Secret", emitted_texts)
        self.assertNotIn("Val", emitted_texts)
        self.assertIn("Generating response...", emitted_texts)
        self.assertIn("Valid text", emitted_texts)

    async def test_codex_adapter_handles_structured_reasoning_and_cumulative_deltas(self) -> None:
        adapter = self.codex_adapter
        binding = adapter.bind(self.definition)
        events: list[ProviderStreamEvent] = []

        schema = _string_result_schema('summary')
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
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.codex_adapter.run_process", new=fake_run_process):
            result = await self._invoke_codex(binding, invocation)

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
            b'{"type":"turn.completed","result":{"structured_output":{"done":true}}}\n',]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            output_flag_index = request.command.index("--output-last-message")
            output_file_path = Path(request.command[output_flag_index + 1])
            output_file_path.parent.mkdir(parents=True, exist_ok=True)
            output_file_path.write_text('{"done":true}', encoding="utf-8")

            if request.on_stdout_line:
                for line in stream_lines:
                    request.on_stdout_line(line)
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.codex_adapter.run_process", new=fake_run_process):
            result = await self._invoke_codex(binding, invocation)

        self.assertEqual(result.response_text, '{"done":true}')
        text_events = [e for e in events if e.kind is ProviderStreamEventKind.TEXT_DELTA]
        self.assertEqual(len(text_events), 1)
        self.assertEqual(text_events[0].text, "Step 1 result")

    async def test_codex_adapter_streams_item_delta_and_avoids_completed_replay(self) -> None:
        """Verify CodexAdapter streams item.delta records, folds them, and avoids replay on item.completed."""
        adapter = self.codex_adapter
        binding = adapter.bind(self.definition)
        events: list[ProviderStreamEvent] = []

        schema = _string_result_schema('summary')
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
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.codex_adapter.run_process", new=fake_run_process):
            result = await self._invoke_codex(binding, invocation)

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

        schema = _string_result_schema('summary')
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
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.codex_adapter.run_process", new=fake_run_process):
            result = await self._invoke_codex(binding, invocation)

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

        schema = _string_result_schema('summary')
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
            b'{"type":"item.delta","item_id":"m_typed","delta":"part two"}\n',]

        async def fake_run_process(request: ProcessRequest, **_kwargs: object) -> ProcessResult:
            output_flag_index = request.command.index("--output-last-message")
            output_file_path = Path(request.command[output_flag_index + 1])
            output_file_path.parent.mkdir(parents=True, exist_ok=True)
            output_file_path.write_text('{"summary":"Done"}', encoding="utf-8")
            for line in stream_lines:
                if request.on_stdout_line:
                    request.on_stdout_line(line)
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.codex_adapter.run_process", new=fake_run_process):
            result = await self._invoke_codex(binding, invocation)

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

        schema = _string_result_schema('summary')
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
            return _successful_stream_result(stream_lines)

        with mock.patch("story_writing_agents.codex_adapter.run_process", new=fake_run_process):
            result = await self._invoke_codex(binding, invocation)

        self.assertEqual(result.response_text, '{"summary":"Done"}')
        # Exactly 1 event should have been emitted (the matching valid delta):
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, ProviderStreamEventKind.TEXT_DELTA)
        self.assertEqual(events[0].text, "clean valid text")

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
            await self._invoke_codex(binding, invocation)

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
            b'{"type":"item.delta","item_id":"m1","delta":"visible"}\n',]

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
            await self._invoke_codex(binding, invocation)

        # "secret" and "ignored" must be rejected fail-closed; only "visible" is emitted
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, ProviderStreamEventKind.TEXT_DELTA)
        self.assertEqual(events[0].text, "visible")

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
            await self._invoke_codex(binding, invocation)

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
            await self._invoke_codex(binding, invocation)

        # Only initial texts from item.started should be emitted; both IDs retired
        emitted_texts = [ev.text for ev in events if ev.kind == ProviderStreamEventKind.TEXT_DELTA]
        self.assertEqual(emitted_texts, ["text_a", "text_b"])

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
            await self._invoke_codex(binding, invocation)

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
            await self._invoke_codex(binding, invocation)

        emitted_texts = [ev.text for ev in events if ev.kind == ProviderStreamEventKind.TEXT_DELTA]
        self.assertEqual(emitted_texts, ["first part"])

    def test_codex_adapter_extract_stream_text_ignores_non_string_values(self) -> None:
        """Verify extract_stream_text accepts only string text and ignores non-string metadata."""
        from story_writing_agents.codex_event_decoding import extract_stream_text

        self.assertEqual(extract_stream_text("valid"), "valid")
        self.assertEqual(extract_stream_text({"text": "hello"}), "hello")
        self.assertEqual(extract_stream_text({"text": 123}), "")
        self.assertEqual(extract_stream_text({"content": {"nested": "obj"}}), "")
        self.assertEqual(extract_stream_text([{"text": "a"}, {"text": 42}, "b"]), "ab")
