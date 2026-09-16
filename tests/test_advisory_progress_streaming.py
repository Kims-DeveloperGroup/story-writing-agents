"""Unit and integration tests for advisory progress live stream rendering."""

from __future__ import annotations

import asyncio
import io
import unittest
from unittest import mock

from story_writing_agents.agent_context.progress import (
    ContextActionProgressEvent,
    ContextActionProgressKind,
    ProviderStreamEvent,
    ProviderStreamEventKind,
    active_provider_stream_sink,
)
from story_writing_agents.interactive_terminal import (
    _core,
    _driver,
)
from story_writing_agents.interactive_terminal._advisory_progress import (
    AdvisoryProgressRequest,
    AdvisoryProgressState,
)
from story_writing_agents.interactive_terminal._core import (
    InteractiveAdvisoryProgress,
    InteractiveTerminal,
)
from story_writing_agents.interactive_terminal._driver import RawTerminalDriver
from story_writing_agents.interactive_terminal._model import (
    CapabilityProfile,
    TerminalCapabilities,
)
from story_writing_agents.provider_adapter import ProviderInvocation


class AdvisoryProgressStreamingTests(unittest.TestCase):
    def test_observe_stream_status_and_deltas(self) -> None:
        state = AdvisoryProgressState()
        state.begin((AdvisoryProgressRequest("agent_a", "op_1", "Drafting scene"),))

        state.observe_stream(
            ProviderStreamEvent(
                kind=ProviderStreamEventKind.STATUS,
                agent_id="agent_a",
                text="Connecting to model...",
                operation_id="op_1",
            )
        )
        self.assertEqual(
            state.stream_status.get(("agent_a", "op_1")),
            "Connecting to model...",
        )

        state.observe_stream(
            ProviderStreamEvent(
                kind=ProviderStreamEventKind.THOUGHT_DELTA,
                agent_id="agent_a",
                text="Considering pacing and conflict",
                operation_id="op_1",
            )
        )
        self.assertEqual(
            state.stream_status.get(("agent_a", "op_1")),
            "Thinking...",
        )

        state.observe_stream(
            ProviderStreamEvent(
                kind=ProviderStreamEventKind.TEXT_DELTA,
                agent_id="agent_a",
                text="The door opened ",
                operation_id="op_1",
            )
        )
        state.observe_stream(
            ProviderStreamEvent(
                kind=ProviderStreamEventKind.TEXT_DELTA,
                agent_id="agent_a",
                text="with a shudder.",
                operation_id="op_1",
            )
        )
        self.assertEqual(
            state.stream_preview.get(("agent_a", "op_1")),
            "The door opened with a shudder.",
        )

    def test_observe_stream_bounds_preview_buffer_to_500_chars(self) -> None:
        state = AdvisoryProgressState()
        state.begin((AdvisoryProgressRequest("agent_a", "op_1", "Generating text"),))

        for i in range(10):
            state.observe_stream(
                ProviderStreamEvent(
                    kind=ProviderStreamEventKind.TEXT_DELTA,
                    agent_id="agent_a",
                    text=f"chunk_{i:02d}_" + ("x" * 90) + "\n",
                    operation_id="op_1",
                )
            )

        preview = state.stream_preview.get(("agent_a", "op_1"), "")
        self.assertLessEqual(len(preview), 500)
        self.assertIn("chunk_09_", preview)
        self.assertNotIn("chunk_00_", preview)

    def test_observe_stream_infers_active_request_when_operation_id_is_none(self) -> None:
        state = AdvisoryProgressState()
        state.begin((AdvisoryProgressRequest("lead_orchestrator", "turn_123", "Lead turn"),))

        state.observe_stream(
            ProviderStreamEvent(
                kind=ProviderStreamEventKind.STATUS,
                agent_id="lead_orchestrator",
                text="Generating response...",
                operation_id=None,
            )
        )
        self.assertEqual(
            state.stream_status.get(("lead_orchestrator", "turn_123")),
            "Generating response...",
        )

    def test_render_formats_bounded_preview_and_status(self) -> None:
        state = AdvisoryProgressState()
        state.begin((AdvisoryProgressRequest("author_agent", "op_99", "Writing chapter"),))

        state.observe_stream(
            ProviderStreamEvent(
                kind=ProviderStreamEventKind.THOUGHT_DELTA,
                agent_id="author_agent",
                text="Structuring the dialogue\x1b[31m ANSI stripped",
                operation_id="op_99",
            )
        )
        state.observe_stream(
            ProviderStreamEvent(
                kind=ProviderStreamEventKind.TEXT_DELTA,
                agent_id="author_agent",
                text="Line 1: The cold wind howled.\nLine 2: Rain lashed the panes.\nLine 3: Shadows lengthened.",
                operation_id="op_99",
            )
        )

        rendered = state.render(frame=0)
        self.assertIn("author_agent", rendered)
        self.assertIn("Request: Writing chapter", rendered)
        self.assertIn("Thinking...", rendered)
        self.assertNotIn("Structuring the dialogue", rendered)
        # Verify preview contains the last 2 lines
        self.assertIn("Preview: Line 2: Rain lashed the panes.", rendered)
        self.assertIn("         Line 3: Shadows lengthened.", rendered)
        # Line 1 should not be in the 2-line preview
        self.assertNotIn("Line 1: The cold wind howled.", rendered)

    def test_finish_and_begin_clear_all_transient_stream_state(self) -> None:
        state = AdvisoryProgressState()
        state.begin((AdvisoryProgressRequest("agent_x", "op_1", "Task 1"),))
        state.observe_stream(
            ProviderStreamEvent(
                kind=ProviderStreamEventKind.STATUS,
                agent_id="agent_x",
                text="Working...",
                operation_id="op_1",
            )
        )
        state.observe_stream(
            ProviderStreamEvent(
                kind=ProviderStreamEventKind.TEXT_DELTA,
                agent_id="agent_x",
                text="In-progress text",
                operation_id="op_1",
            )
        )
        self.assertTrue(state.stream_status)
        self.assertTrue(state.stream_preview)

        state.finish()
        self.assertEqual(state.stream_status, {})
        self.assertEqual(state.stream_preview, {})

        state.begin((AdvisoryProgressRequest("agent_x", "op_2", "Task 2"),))
        self.assertEqual(state.stream_status, {})
        self.assertEqual(state.stream_preview, {})


class InteractiveAdvisoryProgressIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_stream_sink_lifecycle_and_invocation_inheritance(self) -> None:
        # Before run: active_provider_stream_sink() is None
        self.assertIsNone(active_provider_stream_sink())

        # An invocation outside interactive progress has stream_sink=None
        inv_outside = ProviderInvocation(
            session_id="session_test",
            schema={},
            payload={},
            thread_id=None,
        )
        self.assertIsNone(inv_outside.stream_sink)

        mock_terminal = mock.create_autospec(InteractiveTerminal, instance=True)
        async def fake_advisory_progress(coro):
            return await coro
        mock_terminal._advisory_progress = fake_advisory_progress

        progress = InteractiveAdvisoryProgress(mock_terminal)

        async def inner_operation() -> None:
            # Inside run(): active_provider_stream_sink() is set!
            active_sink = active_provider_stream_sink()
            self.assertIsNotNone(active_sink)
            self.assertEqual(active_sink, progress._state.observe_stream)

            # ProviderInvocation automatically acquires active_sink
            inv_inside = ProviderInvocation(
                session_id="session_test",
                schema={},
                payload={},
                thread_id=None,
            )
            self.assertIs(inv_inside.stream_sink, active_sink)

            # Feeding stream event to inherited stream_sink reaches progress state
            inv_inside.stream_sink(
                ProviderStreamEvent(
                    kind=ProviderStreamEventKind.TEXT_DELTA,
                    agent_id="active_agent",
                    text="Streaming preview chunk",
                )
            )
            self.assertIn(
                "Streaming preview chunk",
                progress._state.stream_preview.get(("active_agent", ""), ""),
            )

        progress.begin()
        await progress.run(inner_operation())
        progress.finish()

        # After run completes: active_provider_stream_sink() is reset to None
        self.assertIsNone(active_provider_stream_sink())

    async def test_bound_stream_sink_context_manager_and_task_propagation(self) -> None:
        """Verify bound_stream_sink context manager propagates into asyncio.create_task."""
        mock_terminal = mock.create_autospec(InteractiveTerminal, instance=True)
        progress = InteractiveAdvisoryProgress(mock_terminal)

        # Before binding: active sink is None
        self.assertIsNone(active_provider_stream_sink())

        task_captured_sink = None

        async def subtask() -> None:
            nonlocal task_captured_sink
            task_captured_sink = active_provider_stream_sink()

        with progress.bound_stream_sink():
            # In caller context: active sink is set
            self.assertEqual(active_provider_stream_sink(), progress._state.observe_stream)
            # Spawn asyncio task inside with-block
            task = asyncio.create_task(subtask())
            await task

        # Subtask inherited the active stream sink from copied context
        self.assertEqual(task_captured_sink, progress._state.observe_stream)
        # Exiting with-block resets sink
        self.assertIsNone(active_provider_stream_sink())

    def test_lead_policy_operation_task_inherits_stream_sink(self) -> None:
        """Verify InteractiveLeadAdapter._run_operation passes active stream sink to task."""
        from story_writing_agents.interactive_controller._lead_policy import InteractiveLeadAdapter
        from story_writing_agents.orchestrator import RuntimeSession

        runner = asyncio.Runner()
        self.addCleanup(runner.close)

        mock_terminal = mock.create_autospec(InteractiveTerminal, instance=True)
        async def fake_advisory_progress(coro):
            return await coro
        mock_terminal._advisory_progress = fake_advisory_progress
        progress = InteractiveAdvisoryProgress(mock_terminal)

        adapter = InteractiveLeadAdapter(
            RuntimeSession.__new__(RuntimeSession),
            runner,
            progress,
            mock.MagicMock(),
            mock.MagicMock(),
            mock.MagicMock(),
            recover_authentication=lambda _key: True,
        )

        observed_sink_in_operation = None

        async def fake_operation() -> str:
            nonlocal observed_sink_in_operation
            observed_sink_in_operation = active_provider_stream_sink()
            return "ok"

        result = adapter._run_operation(fake_operation())
        self.assertEqual(result, "ok")
        self.assertEqual(observed_sink_in_operation, progress._state.observe_stream)
        self.assertIsNone(active_provider_stream_sink())

    def test_observe_stream_status_capped_and_single_line(self) -> None:
        state = AdvisoryProgressState()
        state.begin((AdvisoryProgressRequest("agent_a", "op_1", "Drafting scene"),))

        long_multiline_status = "Line 1: Working on scene\nLine 2: " + ("x" * 200)
        state.observe_stream(
            ProviderStreamEvent(
                kind=ProviderStreamEventKind.STATUS,
                agent_id="agent_a",
                text=long_multiline_status,
                operation_id="op_1",
            )
        )
        stored_status = state.stream_status.get(("agent_a", "op_1"))
        self.assertEqual(stored_status, "Line 1: Working on scene")
        self.assertLessEqual(len(stored_status), 120)

        very_long_single_line = "y" * 200
        state.observe_stream(
            ProviderStreamEvent(
                kind=ProviderStreamEventKind.STATUS,
                agent_id="agent_a",
                text=very_long_single_line,
                operation_id="op_1",
            )
        )
        stored_status2 = state.stream_status.get(("agent_a", "op_1"))
        self.assertEqual(len(stored_status2), 120)
        self.assertEqual(stored_status2, "y" * 120)

    def test_observe_stream_infers_op_id_from_context_action_events(self) -> None:
        state = AdvisoryProgressState()
        state.begin()  # no initial requests

        context_event = ContextActionProgressEvent(
            kind=ContextActionProgressKind.READ_COMPLETED,
            agent_id="lead_orchestrator",
            operation_id="lead_turn_42",
            action_index=1,
        )
        state.observe(context_event)

        state.observe_stream(
            ProviderStreamEvent(
                kind=ProviderStreamEventKind.STATUS,
                agent_id="lead_orchestrator",
                text="Searching artifacts...",
                operation_id=None,
            )
        )
        self.assertEqual(
            state.stream_status.get(("lead_orchestrator", "lead_turn_42")),
            "Searching artifacts...",
        )

    def test_observe_migrates_provisional_stream_state_when_authoritative_action_arrives(self) -> None:
        state = AdvisoryProgressState()
        state.begin()  # no initial requests

        # Stream event arrives first (no operation_id known yet)
        state.observe_stream(
            ProviderStreamEvent(
                kind=ProviderStreamEventKind.STATUS,
                agent_id="lead_orchestrator",
                text="Drafting initial outline...",
                operation_id=None,
            )
        )
        state.observe_stream(
            ProviderStreamEvent(
                kind=ProviderStreamEventKind.TEXT_DELTA,
                agent_id="lead_orchestrator",
                text="Chapter 1 overview",
                operation_id=None,
            )
        )
        self.assertIn(("lead_orchestrator", ""), state.stream_status)
        self.assertIn(("lead_orchestrator", ""), state.stream_preview)

        # Later, authoritative context action arrives
        context_event = ContextActionProgressEvent(
            kind=ContextActionProgressKind.READ_COMPLETED,
            agent_id="lead_orchestrator",
            operation_id="lead_turn_99",
            action_index=1,
        )
        state.observe(context_event)

        # Provisional key should be migrated
        self.assertNotIn(("lead_orchestrator", ""), state.stream_status)
        self.assertNotIn(("lead_orchestrator", ""), state.stream_preview)
        self.assertEqual(
            state.stream_status.get(("lead_orchestrator", "lead_turn_99")),
            "Drafting initial outline...",
        )
        self.assertEqual(
            state.stream_preview.get(("lead_orchestrator", "lead_turn_99")),
            "Chapter 1 overview",
        )

    def test_plain_terminal_does_not_bind_stream_sink(self) -> None:
        mock_terminal = mock.create_autospec(InteractiveTerminal, instance=True)
        from story_writing_agents.interactive_terminal._core import _plain_advisory_progress
        mock_terminal._advisory_progress = _plain_advisory_progress
        progress = InteractiveAdvisoryProgress(mock_terminal)

        # bound_stream_sink on plain terminal should not bind
        with progress.bound_stream_sink():
            self.assertIsNone(active_provider_stream_sink())

        # run on plain terminal should not bind
        observed_sink = "sentinel"
        async def fake_op():
            nonlocal observed_sink
            observed_sink = active_provider_stream_sink()
            return 42

        runner = asyncio.Runner()
        self.addCleanup(runner.close)
        result = runner.run(progress.run(fake_op()))
        self.assertEqual(result, 42)
        self.assertIsNone(observed_sink)

    def test_non_raw_stream_terminal_does_not_bind_stream_sink(self) -> None:
        mock_terminal = mock.create_autospec(InteractiveTerminal, instance=True)
        # Custom progress runner (like build_stream_terminal installs)
        async def custom_advisory_progress(op):
            return await op
        mock_terminal._advisory_progress = custom_advisory_progress
        # But capabilities indicate non-raw mode (e.g. line mode or dumb terminal)
        mock_caps = mock.MagicMock()
        mock_caps.raw_mode = False
        mock_terminal._capability_provider.return_value = mock_caps

        progress = InteractiveAdvisoryProgress(mock_terminal)

        with progress.bound_stream_sink():
            self.assertIsNone(active_provider_stream_sink())

        observed_sink = "sentinel"
        async def fake_op():
            nonlocal observed_sink
            observed_sink = active_provider_stream_sink()
            return 99

        runner = asyncio.Runner()
        self.addCleanup(runner.close)
        result = runner.run(progress.run(fake_op()))
        self.assertEqual(result, 99)
        self.assertIsNone(observed_sink)

    def test_capable_raw_terminal_renders_stream_preview_and_clears_cleanly(self) -> None:
        """Verify stream status/preview updates render on a raw terminal and are cleared cleanly."""
        output = io.StringIO()
        real_sleep = asyncio.sleep

        async def controlled_sleep(_delay: float) -> None:
            await real_sleep(0)

        with (
            mock.patch.object(
                RawTerminalDriver,
                "capabilities",
                autospec=True,
                return_value=TerminalCapabilities(
                    CapabilityProfile.ANSI,
                    80,
                    input_tty=True,
                    output_tty=True,
                    raw_mode=True,
                    cursor_controls=True,
                    decorative_sgr=True,
                ),
            ),
            mock.patch.object(
                _driver.asyncio,
                "sleep",
                side_effect=controlled_sleep,
            ),
        ):
            terminal = _core.build_stream_terminal(
                lambda _prompt: "",
                io.StringIO(),
                output,
            )
            progress = InteractiveAdvisoryProgress(terminal)
            progress.begin(
                (AdvisoryProgressRequest("lead_orchestrator", "lead_turn_1", "Consulting specialist..."),)
            )

            async def streaming_operation() -> str:
                sink = active_provider_stream_sink()
                self.assertIsNotNone(sink)
                sink(
                    ProviderStreamEvent(
                        ProviderStreamEventKind.STATUS,
                        "lead_orchestrator",
                        "Synthesizing outline...",
                        operation_id="lead_turn_1",
                    )
                )
                sink(
                    ProviderStreamEvent(
                        ProviderStreamEventKind.TEXT_DELTA,
                        "lead_orchestrator",
                        "Chapter 1: The First Dawn",
                        operation_id="lead_turn_1",
                    )
                )
                await real_sleep(0.05)
                return "ok"

            runner = asyncio.Runner()
            self.addCleanup(runner.close)
            result = runner.run(progress.run(streaming_operation()))
            # Final output is written after progress completes and clears
            terminal.write_agent("lead_orchestrator", "Final novel outline committed.\n")

        self.assertEqual(result, "ok")
        rendered = output.getvalue()
        # Preview and status were rendered into the terminal stream
        self.assertIn("Synthesizing outline...", rendered)
        self.assertIn("Chapter 1: The First Dawn", rendered)
        # Clear sequence \r\x1b[K occurred and preceded the final output
        clear_sequence = "\r\x1b[K"
        self.assertIn(clear_sequence, rendered)
        clear_pos = rendered.rindex(clear_sequence)
        final_pos = rendered.index("Final novel outline committed.")
        self.assertGreater(final_pos, clear_pos)


if __name__ == "__main__":
    unittest.main()
