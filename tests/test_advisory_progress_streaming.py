"""Unit and integration tests for advisory progress live stream rendering."""

from __future__ import annotations

import asyncio
import io
import unittest
from unittest import mock

from story_writing_agents.agent_context.actions import ContextSourceBoundaryKind
from story_writing_agents.agent_context.progress import (
    ContextActionProgressEvent,
    ContextActionProgressKind,
    ContextActionProgressSource,
    ContextSourceBoundary,
    ContextSourceKind,
    ProviderStreamEvent,
    ProviderStreamEventKind,
    active_provider_stream_sink,
)
from story_writing_agents.errors import BackendProtocolError
from story_writing_agents.interactive_terminal import (
    _core,
    _driver,
)
from story_writing_agents.interactive_terminal._advisory_progress import (
    AdvisoryProgressRequest,
    AdvisoryProgressState,
    bind_advisory_progress,
    reset_advisory_progress,
)
from story_writing_agents.interactive_terminal._core import (
    InteractiveAdvisoryProgress,
    InteractiveTerminal,
)
from story_writing_agents.interactive_terminal._driver import RawTerminalDriver
from story_writing_agents.interactive_terminal._model import (
    CapabilityProfile,
    KeyEvent,
    KeyKind,
    MultiAgentScrollState,
    NestedScrollViewport,
    TerminalCapabilities,
    ViewState,
)
from story_writing_agents.interactive_terminal._controls import (
    append_main_input_history,
    transition_line_input,
    transition_main_input,
    transition_nested_scroll,
)
from story_writing_agents.interactive_terminal._viewer_render import render_nested_scroll_view
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
                text=(
                    "Line 1: The cold wind howled.\n"
                    "Line 2: Rain lashed the panes.\n"
                    "Line 3: Shadows lengthened.\n"
                    "Line 4: A floorboard creaked.\n"
                    "Line 5: Dust danced in the air.\n"
                    "Line 6: The key turned slowly."
                ),
                operation_id="op_99",
            )
        )

        rendered = state.render(frame=0)
        self.assertIn("author_agent", rendered)
        self.assertIn("Request: Writing chapter", rendered)
        self.assertIn("Thinking...", rendered)
        self.assertNotIn("Structuring the dialogue", rendered)
        # Verify preview contains the last 5 lines
        self.assertIn("Preview: Line 2: Rain lashed the panes.", rendered)
        self.assertIn("         Line 3: Shadows lengthened.", rendered)
        self.assertIn("         Line 4: A floorboard creaked.", rendered)
        self.assertIn("         Line 5: Dust danced in the air.", rendered)
        self.assertIn("         Line 6: The key turned slowly.", rendered)
        # Line 1 should not be in the 5-line preview
        self.assertNotIn("Line 1: The cold wind howled.", rendered)

    def test_long_single_line_preview_wraps_across_multiple_lines(self) -> None:
        state = AdvisoryProgressState()
        state.begin((AdvisoryProgressRequest("author_agent", "op_1", "Writing paragraph"),))
        long_paragraph = (
            "The rain fell softly upon the cobblestone streets of the old city, "
            "washing away the dust of a long summer. In the distance, a clock tower "
            "struck midnight, its deep chime echoing through the narrow alleys where "
            "shadows danced in the flickering gaslight and footsteps faded into silence."
        )
        state.observe_stream(
            ProviderStreamEvent(
                kind=ProviderStreamEventKind.TEXT_DELTA,
                agent_id="author_agent",
                text=long_paragraph,
                operation_id="op_1",
            )
        )
        rendered = state.render(0)
        lines = rendered.splitlines()
        preview_lines = [line for line in lines if "Preview:" in line or line.startswith("           ")]
        self.assertGreaterEqual(len(preview_lines), 2)
        self.assertLessEqual(len(preview_lines), 5)

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

    def test_render_collapses_multiple_sources_into_summary_line(self) -> None:
        state = AdvisoryProgressState()
        state.begin((AdvisoryProgressRequest("agent_searcher", "op_1", "Searching files"),))
        boundary = ContextSourceBoundary(ContextSourceBoundaryKind.ARTIFACT_LINES, start=1, end=50)
        sources = tuple(
            ContextActionProgressSource(
                ContextSourceKind.WRITING_ARTIFACT,
                f"chapters/chapter_{i:02d}.md",
                boundary,
            )
            for i in range(10)
        )
        state.observe(
            ContextActionProgressEvent(
                ContextActionProgressKind.SEARCH_COMPLETED,
                "agent_searcher",
                "op_1",
                1,
                sources=sources,
                truncated=False,
            )
        )
        rendered = state.render(0)
        self.assertIn("Found: 10 sources (latest: chapters/chapter_09.md · lines 1–50)", rendered)
        self.assertNotIn("chapters/chapter_00.md", rendered)
        lines = rendered.splitlines()
        self.assertLessEqual(len(lines), 8)

    def test_render_height_is_bounded_to_10_lines(self) -> None:
        state = AdvisoryProgressState()
        state.begin(
            (
                AdvisoryProgressRequest("agent_1", "op_1", "Task 1"),
                AdvisoryProgressRequest("agent_2", "op_2", "Task 2"),
                AdvisoryProgressRequest("agent_3", "op_3", "Task 3"),
            )
        )
        for i in range(1, 4):
            state.observe_stream(
                ProviderStreamEvent(
                    ProviderStreamEventKind.STATUS,
                    f"agent_{i}",
                    f"Status for agent {i}",
                    operation_id=f"op_{i}",
                )
            )
            state.observe_stream(
                ProviderStreamEvent(
                    ProviderStreamEventKind.TEXT_DELTA,
                    f"agent_{i}",
                    f"Preview for agent {i}\nLine 2 for agent {i}",
                    operation_id=f"op_{i}",
                )
            )
        rendered = state.render(0)
        lines = rendered.splitlines()
        self.assertLessEqual(len(lines), 10)

    def test_last_error_message_captured_and_persisted_across_finish(self) -> None:
        state = AdvisoryProgressState()
        state.begin((AdvisoryProgressRequest("agent_err", "op_err", "Error task"),))
        self.assertIsNone(state.last_error_message)

        state.observe_stream(
            ProviderStreamEvent(
                ProviderStreamEventKind.STATUS,
                "agent_err",
                "Error: No capacity available for model gpt-oss-120b-medium on the server",
                operation_id="op_err",
            )
        )
        self.assertEqual(
            state.last_error_message,
            "Error: No capacity available for model gpt-oss-120b-medium on the server",
        )

        state.finish()
        self.assertEqual(
            state.last_error_message,
            "Error: No capacity available for model gpt-oss-120b-medium on the server",
        )

        state.begin((AdvisoryProgressRequest("agent_err", "op_err_2", "New task"),))
        self.assertIsNone(state.last_error_message)


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
        state.observe_stream(
            ProviderStreamEvent(
                kind=ProviderStreamEventKind.STATUS,
                agent_id="lead_orchestrator",
                text="Error: initial failure",
                operation_id=None,
            )
        )
        self.assertIn(("lead_orchestrator", ""), state.stream_status)
        self.assertIn(("lead_orchestrator", ""), state.stream_preview)
        self.assertIn(("lead_orchestrator", ""), state.agent_errors)

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
        self.assertNotIn(("lead_orchestrator", ""), state.agent_errors)
        self.assertEqual(
            state.stream_status.get(("lead_orchestrator", "lead_turn_99")),
            "Error: initial failure",
        )
        self.assertEqual(
            state.stream_preview.get(("lead_orchestrator", "lead_turn_99")),
            "Chapter 1 overview",
        )
        self.assertEqual(
            state.agent_errors.get(("lead_orchestrator", "lead_turn_99")),
            "Error: initial failure",
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

    def test_nested_scroll_navigation_and_focus_switching(self) -> None:
        view1 = NestedScrollViewport(
            view_id="lead:op1",
            title="Lead",
            total_lines=50,
            start_line=1,
            visible_lines=tuple(f"Line {i}" for i in range(1, 7)),
            visible_height=6,
            is_focused=True,
            state=ViewState.ERROR,
            auto_scroll=False,
        )
        view2 = NestedScrollViewport(
            view_id="spec:op2",
            title="Specialist",
            total_lines=30,
            start_line=10,
            visible_lines=tuple(f"Spec {i}" for i in range(10, 16)),
            visible_height=6,
            is_focused=False,
            state=ViewState.ERROR,
            auto_scroll=False,
        )
        state = MultiAgentScrollState((view1, view2), focused_index=0)
        self.assertIsNotNone(state.focused_view)
        self.assertEqual(state.focused_view.title, "Lead")

        # Scroll down
        down_state = transition_nested_scroll(state, KeyEvent(KeyKind.DOWN))
        self.assertIsNotNone(down_state.focused_view)
        self.assertEqual(down_state.focused_view.start_line, 2)

        # Scroll up
        up_state = transition_nested_scroll(down_state, KeyEvent(KeyKind.UP))
        self.assertIsNotNone(up_state.focused_view)
        self.assertEqual(up_state.focused_view.start_line, 1)

        # Page down (Shift Down)
        pgdn_state = transition_nested_scroll(up_state, KeyEvent(KeyKind.SHIFT_DOWN))
        self.assertIsNotNone(pgdn_state.focused_view)
        self.assertEqual(pgdn_state.focused_view.start_line, 6)

        # End (scrolls to max_start = 50 - 6 + 1 = 45 and auto_scroll becomes True)
        end_state = transition_nested_scroll(pgdn_state, KeyEvent(KeyKind.END))
        self.assertIsNotNone(end_state.focused_view)
        self.assertEqual(end_state.focused_view.start_line, 45)
        self.assertTrue(end_state.focused_view.auto_scroll)

        # Home (scrolls to 1 and auto_scroll becomes False)
        home_state = transition_nested_scroll(end_state, KeyEvent(KeyKind.HOME))
        self.assertIsNotNone(home_state.focused_view)
        self.assertEqual(home_state.focused_view.start_line, 1)
        self.assertFalse(home_state.focused_view.auto_scroll)

        # Tab switches to Specialist (index 1)
        tab_state = transition_nested_scroll(home_state, KeyEvent(KeyKind.TAB))
        self.assertEqual(tab_state.focused_index, 1)
        self.assertIsNotNone(tab_state.focused_view)
        self.assertEqual(tab_state.focused_view.title, "Specialist")

        # ShiftTab switches back to Lead (index 0)
        backtab_state = transition_nested_scroll(tab_state, KeyEvent(KeyKind.SHIFT_TAB))
        self.assertEqual(backtab_state.focused_index, 0)
        self.assertIsNotNone(backtab_state.focused_view)
        self.assertEqual(backtab_state.focused_view.title, "Lead")

    def test_render_nested_scroll_view_scrollbar_geometry(self) -> None:
        view = NestedScrollViewport(
            view_id="lead:op1",
            title="Lead",
            total_lines=20,
            start_line=5,
            visible_lines=tuple(f"Content {i}" for i in range(5, 11)),
            visible_height=6,
            is_focused=True,
            state=ViewState.ERROR,
            auto_scroll=False,
        )
        state = MultiAgentScrollState((view,), focused_index=0)
        capabilities = TerminalCapabilities(
            CapabilityProfile.ANSI,
            80,
            input_tty=True,
            output_tty=True,
            raw_mode=True,
            cursor_controls=True,
            decorative_sgr=True,
        )
        rendered = render_nested_scroll_view(state, capabilities)
        rows = rendered.text.split("\n")
        # 1 header line + 6 body lines + 1 footer line = 8 rows (strictly <= 10)
        self.assertEqual(len(rows), 8)
        self.assertIn("[► Lead]", rows[0])
        self.assertIn("(5–10 of 20)", rows[0])
        # Scrollbar thumb █ in body lines
        has_thumb = any("█" in r for r in rows[1:7])
        self.assertTrue(has_thumb)
        # Footer navigation hints
        self.assertIn("↑/↓: scroll", rows[-1])
        self.assertIn("Tab: switch agent", rows[-1])

    def test_to_scroll_state_projection(self) -> None:
        mock_terminal = mock.create_autospec(InteractiveTerminal, instance=True)
        progress = InteractiveAdvisoryProgress(mock_terminal)
        progress.begin(
            (AdvisoryProgressRequest("lead", "turn_1", "Leading..."),)
        )
        progress.observe_stream(
            ProviderStreamEvent(
                ProviderStreamEventKind.STATUS,
                "lead",
                "Analyzing query...",
                operation_id="turn_1",
            )
        )
        progress.observe_stream(
            ProviderStreamEvent(
                ProviderStreamEventKind.TEXT_DELTA,
                "lead",
                "Line 1\nLine 2\nLine 3\nLine 4\nLine 5\nLine 6\nLine 7\n",
                operation_id="turn_1",
            )
        )
        scroll_state = progress.to_scroll_state(visible_height=4, state=ViewState.ERROR)
        self.assertEqual(len(scroll_state.views), 1)
        v = scroll_state.views[0]
        self.assertEqual(v.title, "lead")
        self.assertGreaterEqual(v.total_lines, 7)
        self.assertEqual(v.visible_height, 4)
        self.assertEqual(v.state, ViewState.ERROR)

    def test_failure_turn_retains_scroll_box_and_success_clears_it(self) -> None:
        capabilities = TerminalCapabilities(
            CapabilityProfile.ANSI,
            80,
            input_tty=True,
            output_tty=True,
            raw_mode=True,
            cursor_controls=True,
            decorative_sgr=True,
        )
        real_sleep = asyncio.sleep

        async def fast_sleep(_delay: float) -> None:
            await real_sleep(0)

        # 1. Success case: clears frame cleanly
        success_output = io.StringIO()
        with (
            mock.patch.object(RawTerminalDriver, "capabilities", return_value=capabilities),
            mock.patch.object(_driver.asyncio, "sleep", side_effect=fast_sleep),
        ):
            driver = RawTerminalDriver(
                lambda _prompt: "",
                io.StringIO(),
                success_output,
                lambda s, e: (s, None),
                lambda s, e, h: (s, None),
                lambda h, t: h,
            )
            async def successful_op() -> dict:
                await real_sleep(0.01)
                return {"status": "ok"}

            res = asyncio.run(
                driver.run_advisory_progress(
                    successful_op(),
                    lambda _f: "Running task...",
                    is_failure=lambda r: r.get("status") != "ok",
                )
            )
            self.assertEqual(res, {"status": "ok"})
            self.assertIn("\r\x1b[K", success_output.getvalue())

        # 2. Failure case: retains frame on screen, does not clear
        fail_output = io.StringIO()
        with (
            mock.patch.object(RawTerminalDriver, "capabilities", return_value=capabilities),
            mock.patch.object(_driver.asyncio, "sleep", side_effect=fast_sleep),
        ):
            input_stream = io.StringIO("\r")
            driver = RawTerminalDriver(
                lambda _prompt: "",
                input_stream,
                fail_output,
                lambda s, e: (s, None),
                lambda s, e, h: (s, None),
                lambda h, t: h,
            )
            async def failing_op() -> dict:
                await real_sleep(0.01)
                return {"status": "error", "next_action": "retry_lead_turn"}

            view = NestedScrollViewport(
                view_id="lead:1",
                title="Lead",
                total_lines=10,
                start_line=1,
                visible_lines=("Error occurred line 1", "Error occurred line 2"),
                visible_height=2,
                is_focused=True,
                state=ViewState.ERROR,
            )
            scroll_state = MultiAgentScrollState((view,), 0)

            res = asyncio.run(
                driver.run_advisory_progress(
                    failing_op(),
                    lambda _f: "Running task...",
                    scroll_state_provider=lambda: scroll_state,
                    is_failure=lambda r: r.get("next_action") == "retry_lead_turn",
                    nested_scroll_transition=transition_nested_scroll,
                    nested_scroll_renderer=render_nested_scroll_view,
                )
            )
            self.assertEqual(res["next_action"], "retry_lead_turn")
            rendered_fail = fail_output.getvalue()
            self.assertIn("[► Lead]", rendered_fail)
            self.assertIn("Error occurred line 1", rendered_fail)

    def test_to_scroll_state_fallback_view_on_error_with_no_requests(self) -> None:
        mock_terminal = mock.create_autospec(InteractiveTerminal, instance=True)
        progress = InteractiveAdvisoryProgress(mock_terminal)
        progress.begin()
        progress.record_error("Provider connection timeout.")

        scroll_state = progress.to_scroll_state(visible_height=4, state=ViewState.ERROR)
        self.assertEqual(len(scroll_state.views), 1)
        fallback = scroll_state.views[0]
        self.assertEqual(fallback.title, "Lead")
        self.assertEqual(fallback.state, ViewState.ERROR)
        self.assertIn("Provider connection timeout.", fallback.visible_lines)

    def test_to_scroll_state_incorporates_last_error_message(self) -> None:
        mock_terminal = mock.create_autospec(InteractiveTerminal, instance=True)
        progress = InteractiveAdvisoryProgress(mock_terminal)
        progress.begin(
            (AdvisoryProgressRequest("lead", "turn_1", "Leading..."),)
        )
        progress.observe_stream(
            ProviderStreamEvent(
                ProviderStreamEventKind.STATUS,
                "lead",
                "Working...",
                operation_id="turn_1",
            )
        )
        progress.record_error("Model capacity exceeded.")

        scroll_state = progress.to_scroll_state(visible_height=6, state=ViewState.ERROR)
        self.assertEqual(len(scroll_state.views), 1)
        lead_view = scroll_state.views[0]
        self.assertTrue(any("Model capacity exceeded." in line for line in lead_view.visible_lines))

    def test_run_advisory_progress_retains_frame_on_exception(self) -> None:
        capabilities = TerminalCapabilities(
            CapabilityProfile.ANSI,
            80,
            input_tty=True,
            output_tty=True,
            raw_mode=True,
            cursor_controls=True,
            decorative_sgr=True,
        )
        real_sleep = asyncio.sleep

        async def fast_sleep(_delay: float) -> None:
            await real_sleep(0)

        fail_output = io.StringIO()
        with (
            mock.patch.object(RawTerminalDriver, "capabilities", return_value=capabilities),
            mock.patch.object(_driver.asyncio, "sleep", side_effect=fast_sleep),
        ):
            input_stream = io.StringIO("\r")
            driver = RawTerminalDriver(
                lambda _prompt: "",
                input_stream,
                fail_output,
                lambda s, e: (s, None),
                lambda s, e, h: (s, None),
                lambda h, t: h,
            )

            async def crashing_op() -> None:
                await real_sleep(0.01)
                raise RuntimeError("Backend connection refused")

            def provider(failure: BaseException | object | None = None) -> MultiAgentScrollState:
                msg = str(failure) if failure is not None else "Unknown error"
                v = NestedScrollViewport(
                    view_id="lead:1",
                    title="Lead",
                    total_lines=1,
                    start_line=1,
                    visible_lines=(msg,),
                    visible_height=2,
                    is_focused=True,
                    state=ViewState.ERROR,
                )
                return MultiAgentScrollState((v,), 0)

            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(
                    driver.run_advisory_progress(
                        crashing_op(),
                        lambda _f: "Connecting...",
                        scroll_state_provider=provider,
                        nested_scroll_transition=transition_nested_scroll,
                        nested_scroll_renderer=render_nested_scroll_view,
                    )
                )
            self.assertIn("Backend connection refused", str(ctx.exception))
            rendered = fail_output.getvalue()
            self.assertIn("[► Lead]", rendered)
            self.assertIn("Backend connection refused", rendered)

    def test_transition_nested_scroll_toggles_collapsed_on_t_key(self) -> None:
        v = NestedScrollViewport(
            view_id="lead:1",
            title="Lead",
            total_lines=5,
            start_line=1,
            visible_lines=("line 1", "line 2"),
            visible_height=2,
            is_focused=True,
            state=ViewState.ERROR,
        )
        state = MultiAgentScrollState((v,), 0, collapsed=False)
        self.assertFalse(state.collapsed)

        # Press 't' -> toggled to collapsed
        t_event = KeyEvent(KeyKind.TEXT, text="t")
        toggled = transition_nested_scroll(state, t_event)
        self.assertTrue(toggled.collapsed)

        # Press 'T' -> toggled back to expanded
        cap_t_event = KeyEvent(KeyKind.TEXT, text="T")
        expanded = transition_nested_scroll(toggled, cap_t_event)
        self.assertFalse(expanded.collapsed)

        # Navigation preserves collapsed state
        down_event = KeyEvent(KeyKind.DOWN)
        scrolled = transition_nested_scroll(toggled, down_event)
        self.assertTrue(scrolled.collapsed)

    def test_render_nested_scroll_view_collapsed_mode(self) -> None:
        capabilities = TerminalCapabilities(
            CapabilityProfile.ANSI,
            80,
            input_tty=True,
            output_tty=True,
            raw_mode=True,
            cursor_controls=True,
            decorative_sgr=True,
        )
        v = NestedScrollViewport(
            view_id="lead:1",
            title="Lead",
            total_lines=10,
            start_line=1,
            visible_lines=("First error line", "Second line"),
            visible_height=4,
            is_focused=True,
            state=ViewState.ERROR,
        )

        # Expanded state
        expanded_state = MultiAgentScrollState((v,), 0, collapsed=False)
        expanded_frame = render_nested_scroll_view(expanded_state, capabilities)
        self.assertGreater(expanded_frame.line_count, 1)
        self.assertIn("t: toggle view", expanded_frame.text)
        self.assertIn("┌─ [► Lead]", expanded_frame.text)

        # Collapsed state
        collapsed_state = MultiAgentScrollState((v,), 0, collapsed=True)
        collapsed_frame = render_nested_scroll_view(collapsed_state, capabilities)
        self.assertEqual(collapsed_frame.line_count, 1)
        self.assertEqual(len(collapsed_frame.text.splitlines()), 1)
        self.assertIn("press 't' to expand", collapsed_frame.text)
        self.assertIn("[! Lead]", collapsed_frame.text)
        self.assertIn("First error line", collapsed_frame.text)

    def test_advisory_progress_state_render_collapsed(self) -> None:
        state = AdvisoryProgressState()
        state.begin((AdvisoryProgressRequest("lead", "1", "Drafting"),))
        state.record_error("Something went wrong")

        # Default expanded
        expanded_render = state.render(0)
        self.assertIn("Working", expanded_render)
        self.assertIn("lead", expanded_render)
        self.assertGreater(len(expanded_render.splitlines()), 1)

        # Toggled collapsed
        state.toggle_collapsed()
        self.assertTrue(state.collapsed)
        collapsed_render = state.render(0)
        self.assertEqual(len(collapsed_render.splitlines()), 1)
        self.assertIn("lead", collapsed_render)

    def test_interactive_terminal_and_progress_toggle(self) -> None:
        terminal = InteractiveTerminal(lambda p: "", lambda t: "", width=80)
        self.assertFalse(terminal.progress_frame_collapsed)

        progress = InteractiveAdvisoryProgress(terminal)
        self.assertFalse(progress.collapsed)

        # Toggle via terminal
        terminal.toggle_progress_frame()
        self.assertTrue(terminal.progress_frame_collapsed)

        # Progress syncs collapsed mode
        progress.begin((AdvisoryProgressRequest("lead", "1", "Testing"),))
        scroll_state = progress.to_scroll_state()
        self.assertFalse(scroll_state.collapsed)

        progress.collapsed = True
        scroll_state_collapsed = progress.to_scroll_state()
        self.assertTrue(scroll_state_collapsed.collapsed)

    def test_failure_snapshot_retained_across_turns_until_dismissed(self) -> None:
        state = AdvisoryProgressState()
        key = ("lead", "op1")
        req = AdvisoryProgressRequest("lead", "op1", "Working")
        state.begin((req,))
        state.agent_buffers[key] = ["Line 1", "Line 2", "Error: failed operation"]
        state.record_error("Fatal connection timeout")

        # Snapshot the failure
        state.snapshot_failure()
        self.assertIsNotNone(state.failure_snapshot)

        # Start a new turn - regular buffers cleared, failure_snapshot retained
        req2 = AdvisoryProgressRequest("lead", "op2", "Next turn")
        state.begin((req2,))
        self.assertNotIn(key, state.agent_buffers)
        self.assertIsNotNone(state.failure_snapshot)

        # InteractiveAdvisoryProgress falls back to failure_snapshot when agent_buffers is empty
        terminal = InteractiveTerminal(lambda p: "", lambda t: "", width=80)
        progress = InteractiveAdvisoryProgress(terminal)
        progress._state = state
        scroll_state = progress.to_scroll_state()
        self.assertEqual(len(scroll_state.views), 1)
        self.assertIn("Line 1", scroll_state.views[0].visible_lines)

        # Explicitly dismiss the snapshot
        progress.dismiss_failure_snapshot()
        self.assertIsNone(state.failure_snapshot)

    def test_thought_delta_coalesces_consecutive_thinking_rows_and_bounds_buffer(self) -> None:
        state = AdvisoryProgressState()
        req = AdvisoryProgressRequest("lead", "op1", "Thinking task")
        state.begin((req,))

        # Feed 10 consecutive thought deltas
        for _ in range(10):
            event = ProviderStreamEvent(
                ProviderStreamEventKind.THOUGHT_DELTA,
                text="reasoning...",
                agent_id="lead",
                operation_id="op1",
            )
            state.observe_stream(event)

        key = ("lead", "op1")
        # Should have coalesced into a single "Thinking..." row
        self.assertEqual(state.agent_buffers[key], ["Thinking..."])

        # Feed 2500 lines to verify 2,000 line bounding
        for i in range(2500):
            event = ProviderStreamEvent(
                ProviderStreamEventKind.TEXT_DELTA,
                text=f"Line {i}\n",
                agent_id="lead",
                operation_id="op1",
            )
            state.observe_stream(event)

        # Buffer must be capped at 2,000 lines
        self.assertLessEqual(len(state.agent_buffers[key]), 2000)

    def test_transition_nested_scroll_aliases_and_dynamic_visible_lines(self) -> None:
        all_lines = tuple(f"Line {i + 1}" for i in range(20))
        v1 = NestedScrollViewport(
            view_id="lead:1",
            title="Lead",
            total_lines=20,
            start_line=1,
            visible_lines=all_lines[:5],
            visible_height=5,
            is_focused=True,
            state=ViewState.IDLE,
            buffer_lines=all_lines,
        )
        v2 = NestedScrollViewport(
            view_id="critic:2",
            title="Critic",
            total_lines=20,
            start_line=1,
            visible_lines=all_lines[:5],
            visible_height=5,
            is_focused=False,
            state=ViewState.IDLE,
            buffer_lines=all_lines,
        )
        state = MultiAgentScrollState((v1, v2), 0)

        # Test tab navigation aliases: ']' advances tab, '[' recedes tab
        state_tab_next = transition_nested_scroll(state, KeyEvent(KeyKind.TEXT, text="]"))
        self.assertEqual(state_tab_next.focused_index, 1)
        state_tab_prev = transition_nested_scroll(state_tab_next, KeyEvent(KeyKind.TEXT, text="["))
        self.assertEqual(state_tab_prev.focused_index, 0)

        # Test 'j' (down) scrolls and dynamically updates visible_lines
        state_j = transition_nested_scroll(state, KeyEvent(KeyKind.TEXT, text="j"))
        focused_j = state_j.views[state_j.focused_index]
        self.assertEqual(focused_j.start_line, 2)
        self.assertEqual(focused_j.visible_lines, all_lines[1:6])

        # Test 'k' (up) scrolls back and dynamically updates visible_lines
        state_k = transition_nested_scroll(state_j, KeyEvent(KeyKind.TEXT, text="k"))
        focused_k = state_k.views[state_k.focused_index]
        self.assertEqual(focused_k.start_line, 1)
        self.assertEqual(focused_k.visible_lines, all_lines[:5])

        # Test 'G' (bottom/end) scrolls to bottom and updates visible_lines
        state_end = transition_nested_scroll(state, KeyEvent(KeyKind.TEXT, text="G"))
        focused_end = state_end.views[state_end.focused_index]
        self.assertEqual(focused_end.start_line, 16)
        self.assertEqual(focused_end.visible_lines, all_lines[15:20])

        # Test 'g' (top/home) scrolls back to line 1 and updates visible_lines
        state_home = transition_nested_scroll(state_end, KeyEvent(KeyKind.TEXT, text="g"))
        focused_home = state_home.views[state_home.focused_index]
        self.assertEqual(focused_home.start_line, 1)
        self.assertEqual(focused_home.visible_lines, all_lines[:5])

        # Test PageDown (mapped to SHIFT_DOWN) and PageUp (mapped to SHIFT_UP)
        state_pagedown = transition_nested_scroll(state, KeyEvent(KeyKind.SHIFT_DOWN))
        focused_pagedown = state_pagedown.views[state_pagedown.focused_index]
        self.assertEqual(focused_pagedown.start_line, 5)
        self.assertEqual(focused_pagedown.visible_lines, all_lines[4:9])

        state_pageup = transition_nested_scroll(state_pagedown, KeyEvent(KeyKind.SHIFT_UP))
        focused_pageup = state_pageup.views[state_pageup.focused_index]
        self.assertEqual(focused_pageup.start_line, 1)
        self.assertEqual(focused_pageup.visible_lines, all_lines[:5])

    def test_run_progress_frames_invokes_toggle_callback_on_t_key(self) -> None:
        driver = RawTerminalDriver(
            lambda _p: "",
            io.StringIO(),
            io.StringIO(),
            transition_line_input,
            transition_main_input,
            append_main_input_history,
        )
        configuration = _driver._TerminalConfiguration(
            0,
            1,
            [0, 0, 0, 0, 0, 0, []],
            [0, 0, 0, 0, 0, 0, []],
            80,
            None,
            None,
            0,
            object(),
        )
        toggled: list[bool] = []
        def on_toggle() -> bool:
            toggled.append(True)
            return True

        frames: list[int] = []
        def renderer(frame: int) -> str:
            frames.append(frame)
            return f"frame {frame}"

        read_chars = ["t", "x"]
        def fake_read(*_args: object, **_kwargs: object) -> str:
            return read_chars.pop(0)

        def fake_select(*_args: object, **_kwargs: object) -> tuple[list[int], list[int], list[int]]:
            if read_chars:
                return ([0], [], [])
            return ([], [], [])

        async def run_test() -> None:
            task = asyncio.create_task(
                driver._run_progress_frames(
                    renderer,
                    configuration=configuration,
                    toggle_callback=on_toggle,
                )
            )
            await asyncio.sleep(0.12)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        with (
            mock.patch.object(RawTerminalDriver, "_read_character", side_effect=fake_read),
            mock.patch("story_writing_agents.interactive_terminal._driver._select.select", side_effect=fake_select),
            mock.patch("story_writing_agents.interactive_terminal._driver._configured_control_event", return_value=None),
        ):
            asyncio.run(run_test())

        self.assertTrue(len(toggled) >= 1)

    def test_interactive_terminal_toggle_syncs_with_active_state(self) -> None:
        terminal = InteractiveTerminal(lambda p: "", lambda t: "", width=80)
        state = AdvisoryProgressState()
        token = bind_advisory_progress(state)
        try:
            self.assertFalse(terminal.progress_frame_collapsed)
            self.assertFalse(state.collapsed)

            # Toggle on terminal -> state.collapsed becomes True
            terminal.toggle_progress_frame()
            self.assertTrue(terminal.progress_frame_collapsed)
            self.assertTrue(state.collapsed)

            # Set property on terminal -> state.collapsed updates
            terminal.progress_frame_collapsed = False
            self.assertFalse(terminal.progress_frame_collapsed)
            self.assertFalse(state.collapsed)
        finally:
            reset_advisory_progress(token)

    def test_status_buffer_bounded_and_text_delta_does_not_mutate_status_row(self) -> None:
        state = AdvisoryProgressState()
        key = ("test_agent", "op1")
        # 1. Emit 2005 status events -> buffer capped at 2000
        for i in range(2005):
            state.observe_stream(
                ProviderStreamEvent(
                    ProviderStreamEventKind.STATUS,
                    "test_agent",
                    f"Status {i}",
                    operation_id="op1",
                )
            )
        buf = state.agent_buffers[key]
        self.assertEqual(len(buf), 2000)
        self.assertEqual(buf[-1], "Status: Status 2004")

        # 2. Emit text delta -> must be appended as a new row, NOT merged into "Status: Status 2004"
        state.observe_stream(
            ProviderStreamEvent(
                ProviderStreamEventKind.TEXT_DELTA,
                "test_agent",
                "Hello world",
                operation_id="op1",
            )
        )
        self.assertEqual(len(buf), 2000)
        self.assertEqual(buf[-2], "Status: Status 2004")
        self.assertEqual(buf[-1], "Hello world")

        # 3. Consecutive text deltas should merge with previous text row
        state.observe_stream(
            ProviderStreamEvent(
                ProviderStreamEventKind.TEXT_DELTA,
                "test_agent",
                "!",
                operation_id="op1",
            )
        )
        self.assertEqual(buf[-1], "Hello world!")

    def test_finish_clears_live_agent_buffers_while_preserving_snapshot(self) -> None:
        state = AdvisoryProgressState()
        key = ("test_agent", "op1")
        state.observe_stream(
            ProviderStreamEvent(
                ProviderStreamEventKind.STATUS,
                "test_agent",
                "Error: something broke",
                operation_id="op1",
            )
        )
        self.assertTrue(key in state.agent_buffers)
        self.assertIsNotNone(state.failure_snapshot)

        # finish() must clear live buffers
        state.finish()
        self.assertEqual(len(state.agent_buffers), 0)
        # But failure snapshot remains
        self.assertIsNotNone(state.failure_snapshot)
        snap_buffers, snap_err = state.failure_snapshot
        self.assertIn(key, snap_buffers)
        self.assertEqual(snap_err, "Error: something broke")

    def test_collapsed_render_includes_status_and_expansion_hint(self) -> None:
        state = AdvisoryProgressState()
        state.collapsed = True
        state.begin((AdvisoryProgressRequest("agent_writer", "op1", "Drafting scene"),))
        state.observe_stream(
            ProviderStreamEvent(
                ProviderStreamEventKind.STATUS,
                "agent_writer",
                "Writing paragraph 2",
                operation_id="op1",
            )
        )
        rendered = state.render(0)
        self.assertIn("agent_writer", rendered)
        self.assertIn("Writing paragraph 2", rendered)
        self.assertIn("(press 't' to expand)", rendered)

    def test_nested_scroll_view_renders_on_narrow_terminals_without_error(self) -> None:
        from story_writing_agents.interactive_terminal._model import (
            CapabilityProfile,
            MultiAgentScrollState,
            NestedScrollViewport,
            TerminalCapabilities,
        )
        from story_writing_agents.interactive_terminal._viewer_render import (
            render_nested_scroll_view,
        )

        viewport = NestedScrollViewport(
            view_id="a:1",
            title="Agent",
            total_lines=5,
            start_line=1,
            visible_lines=("line 1", "line 2"),
            visible_height=2,
            is_focused=True,
            buffer_lines=("line 1", "line 2", "line 3", "line 4", "line 5"),
        )
        state = MultiAgentScrollState((viewport,), 0)

        # Test valid terminal widths 1 through 5
        for w in range(1, 6):
            caps = TerminalCapabilities(
                CapabilityProfile.ANSI,
                w,
                input_tty=True,
                output_tty=True,
                raw_mode=True,
                cursor_controls=True,
                decorative_sgr=True,
            )
            frame = render_nested_scroll_view(state, caps)
            self.assertIsNotNone(frame.text)

    def test_default_is_failure_recognizes_failed_state(self) -> None:
        from dataclasses import dataclass
        from story_writing_agents.interactive_terminal._core import _default_is_failure

        @dataclass
        class FakeTurn:
            state: str

        self.assertTrue(_default_is_failure(FakeTurn("failed")))
        self.assertTrue(_default_is_failure(FakeTurn("outcome_unknown")))
        self.assertTrue(_default_is_failure(FakeTurn("recovery_required")))
        self.assertFalse(_default_is_failure(FakeTurn("completed")))

    def test_text_delta_chunks_long_line_and_bounds_rows(self) -> None:
        """Verify long newline-free TEXT_DELTA is split into bounded <=2000 char chunks and <=2000 rows."""
        state = AdvisoryProgressState()
        key = ("agent1", "op1")
        # Send a 5000-char single-line text delta
        long_text = "x" * 5000
        state.observe_stream(
            ProviderStreamEvent(ProviderStreamEventKind.TEXT_DELTA, "agent1", long_text, operation_id="op1")
        )
        buf = state.agent_buffers[key]
        # Should be split into chunks <= 2000 chars each: 2000, 2000, 1000
        self.assertEqual(len(buf), 3)
        for chunk in buf:
            self.assertLessEqual(len(chunk), 2000)

        # Now send 2500 lines to test buffer row bounding
        state.observe_stream(
            ProviderStreamEvent(ProviderStreamEventKind.TEXT_DELTA, "agent1", "\n".join(["line"] * 2500), operation_id="op1")
        )
        self.assertLessEqual(len(state.agent_buffers[key]), 2000)

    def test_collapsed_rendering_resolves_active_agent_from_events_without_requests(self) -> None:
        """Verify collapsed rendering displays active agent and status even without explicit requests."""
        state = AdvisoryProgressState()
        state.collapsed = True
        state.begin(())  # No explicit requests
        state.observe_stream(
            ProviderStreamEvent(
                ProviderStreamEventKind.STATUS,
                "lead_agent",
                "Analyzing outline...",
                operation_id="lead_op",
            )
        )
        rendered = state.render(0)
        self.assertIn("lead_agent", rendered)
        self.assertIn("Analyzing outline...", rendered)
        self.assertIn("(press 't' to expand)", rendered)

    def test_to_scroll_state_retains_errors_per_agent_and_unassociated_in_dedicated_tab(self) -> None:
        """Verify multi-agent errors are retained by agent_id and unassociated errors get a dedicated tab."""
        from story_writing_agents.interactive_terminal._core import InteractiveAdvisoryProgress, InteractiveTerminal
        from story_writing_agents.interactive_terminal._model import ViewState

        terminal = InteractiveTerminal(lambda p: "", lambda t: "", width=80)
        progress = InteractiveAdvisoryProgress(terminal)
        state = progress._state

        # Two agents
        key1 = ("agent_ok", "op1")
        key2 = ("agent_err", "op2")
        state.agent_buffers[key1] = ["Agent OK line 1", "Agent OK line 2"]
        state.agent_buffers[key2] = ["Agent Err line 1"]

        # Simulate agent_err failing
        state.observe_stream(
            ProviderStreamEvent(
                ProviderStreamEventKind.STATUS,
                "agent_err",
                "error: token quota exceeded",
                operation_id="op2",
            )
        )

        scroll_state = progress.to_scroll_state(state=ViewState.ERROR)
        self.assertEqual(len(scroll_state.views), 2)
        # Agent OK buffer should NOT have the error
        view_ok = next(v for v in scroll_state.views if v.title == "agent_ok")
        self.assertNotIn("error: token quota exceeded", "\n".join(view_ok.buffer_lines))

        # Agent Err buffer should have the error
        view_err = next(v for v in scroll_state.views if v.title == "agent_err")
        self.assertIn("error: token quota exceeded", "\n".join(view_err.buffer_lines))

        # Now test unassociated error with multiple agents
        state.agent_errors.clear()
        state.last_error_message = "Unexpected runtime panic"
        scroll_state_unassoc = progress.to_scroll_state(state=ViewState.ERROR)
        # Should have agent_ok, agent_err, and dedicated Lead Error tab
        self.assertEqual(len(scroll_state_unassoc.views), 3)
        lead_err_view = scroll_state_unassoc.views[-1]
        self.assertEqual(lead_err_view.title, "Lead Error")
        self.assertIn("Unexpected runtime panic", "\n".join(lead_err_view.buffer_lines))

    def test_run_nested_scroll_inspector_calls_toggle_callback_on_mode_change(self) -> None:
        """Verify toggle_callback is called when 't' toggles collapsed mode in nested scroll inspector."""
        from story_writing_agents.interactive_terminal._controls import transition_nested_scroll
        from story_writing_agents.interactive_terminal._driver import RawTerminalDriver
        from story_writing_agents.interactive_terminal._model import (
            MultiAgentScrollState,
            NestedScrollViewport,
            RenderedInputFrame,
            KeyEvent,
            KeyKind,
        )

        driver = RawTerminalDriver(
            lambda _prompt: "",
            io.StringIO(),
            io.StringIO(),
            lambda s, e: (s, None),
            lambda s, e, h: (s, None),
            lambda h, t: h,
        )
        viewport = NestedScrollViewport(
            view_id="a:1",
            title="Agent",
            total_lines=5,
            start_line=1,
            visible_lines=("line 1",),
            visible_height=1,
            is_focused=True,
            buffer_lines=("line 1",),
        )
        initial_state = MultiAgentScrollState((viewport,), 0, collapsed=False)

        toggle_called = []
        def on_toggle():
            toggle_called.append(True)
            return True

        renderer = lambda state, caps: RenderedInputFrame("preview", 1)

        # Mock _prepare_input to return a fake configuration
        fake_config = mock.MagicMock()
        fake_caps = mock.MagicMock()

        # Simulate user pressing 't' then 'q'
        with mock.patch.object(RawTerminalDriver, "_observe_input_capabilities", return_value=mock.MagicMock()), \
             mock.patch.object(RawTerminalDriver, "_prepare_input", return_value=(fake_caps, fake_config)), \
             mock.patch("story_writing_agents.interactive_terminal._driver._activate_terminal"), \
             mock.patch("story_writing_agents.interactive_terminal._driver._restore_terminal_or_raise"), \
             mock.patch.object(RawTerminalDriver, "_redraw_nested_scroll_frame"), \
             mock.patch.object(RawTerminalDriver, "_read_character", side_effect=["t", "q"]):
            driver.run_nested_scroll_inspector(
                initial_state,
                transition_nested_scroll,
                renderer,
                toggle_callback=on_toggle,
            )

        self.assertEqual(len(toggle_called), 1)

    def test_is_failure_raising_resets_progress_state(self) -> None:
        """Verify driver progress state is reset even if is_failure predicate raises."""
        from story_writing_agents.interactive_terminal._driver import RawTerminalDriver
        from story_writing_agents.interactive_terminal._model import (
            CapabilityProfile,
            TerminalCapabilities,
        )
        driver = RawTerminalDriver(
            lambda _prompt: "",
            io.StringIO(),
            io.StringIO(),
            lambda s, e: (s, None),
            lambda s, e, h: (s, None),
            lambda h, t: h,
        )

        async def dummy_op():
            return "done"

        def broken_is_failure(_res):
            raise ValueError("predicate crashed")

        caps = TerminalCapabilities(
            CapabilityProfile.ANSI,
            80,
            input_tty=True,
            output_tty=True,
            raw_mode=True,
            cursor_controls=True,
            decorative_sgr=True,
        )
        with mock.patch.object(RawTerminalDriver, "capabilities", return_value=caps):
            with self.assertRaises(ValueError):
                asyncio.run(
                    driver.run_advisory_progress(
                        dummy_op(),
                        lambda _f: "frame",
                        is_failure=broken_is_failure,
                    )
                )
        self.assertFalse(driver._progress_active)
        self.assertIsNone(driver._progress_renderer)

    def test_setup_failure_closes_operation_coroutine(self) -> None:
        """Verify operation coroutine is closed if activation and restoration fail during setup."""
        from story_writing_agents.interactive_terminal._driver import RawTerminalDriver
        from story_writing_agents.interactive_terminal._model import (
            CapabilityProfile,
            TerminalCapabilities,
        )
        driver = RawTerminalDriver(
            lambda _prompt: "",
            io.StringIO(),
            io.StringIO(),
            lambda s, e: (s, None),
            lambda s, e, h: (s, None),
            lambda h, t: h,
        )

        closed = []
        async def dummy_op():
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                pass
            finally:
                closed.append(True)

        coro = dummy_op()
        # Mock _prepare_input to return valid config and toggle_callback so it attempts activation
        fake_config = mock.MagicMock()
        fake_caps = mock.MagicMock()
        caps = TerminalCapabilities(
            CapabilityProfile.ANSI,
            80,
            input_tty=True,
            output_tty=True,
            raw_mode=True,
            cursor_controls=True,
            decorative_sgr=True,
        )

        with mock.patch.object(RawTerminalDriver, "capabilities", return_value=caps), \
             mock.patch.object(RawTerminalDriver, "_observe_input_capabilities", return_value=mock.MagicMock()), \
             mock.patch.object(RawTerminalDriver, "_prepare_input", return_value=(fake_caps, fake_config)), \
             mock.patch("story_writing_agents.interactive_terminal._driver._activate_terminal", side_effect=RuntimeError("activation failed")), \
             mock.patch("story_writing_agents.interactive_terminal._driver._restore_terminal_or_raise", side_effect=RuntimeError("restoration failed")):
            with self.assertRaises(RuntimeError):
                asyncio.run(
                    driver.run_advisory_progress(
                        coro,
                        lambda _f: "frame",
                        toggle_callback=lambda: False,
                    )
                )

    def test_nested_inspector_failure_is_preserved_and_not_swallowed(self) -> None:
        """Verify nested inspector failure is propagated and not swallowed into a newline."""
        from story_writing_agents.interactive_terminal._driver import RawTerminalDriver
        from story_writing_agents.interactive_terminal._model import (
            CapabilityProfile,
            MultiAgentScrollState,
            NestedScrollViewport,
            TerminalCapabilities,
        )

        driver = RawTerminalDriver(
            lambda _prompt: "",
            io.StringIO(),
            io.StringIO(),
            lambda s, e: (s, None),
            lambda s, e, h: (s, None),
            lambda h, t: h,
        )

        async def failing_op():
            raise RuntimeError("provider failed")

        viewport = NestedScrollViewport(
            view_id="a:1",
            title="Agent",
            total_lines=5,
            start_line=1,
            visible_lines=("line 1",),
            visible_height=1,
            is_focused=True,
            buffer_lines=("line 1",),
        )
        scroll_state = MultiAgentScrollState((viewport,), 0)
        caps = TerminalCapabilities(
            CapabilityProfile.ANSI,
            80,
            input_tty=True,
            output_tty=True,
            raw_mode=True,
            cursor_controls=True,
            decorative_sgr=True,
        )

        with mock.patch.object(RawTerminalDriver, "capabilities", return_value=caps), \
             mock.patch.object(
                RawTerminalDriver,
                "run_nested_scroll_inspector",
                side_effect=RuntimeError("viewer terminal restoration failed"),
            ):
            with self.assertRaises(RuntimeError) as exc_info:
                asyncio.run(
                    driver.run_advisory_progress(
                        failing_op(),
                        lambda _f: "frame",
                        scroll_state_provider=lambda _f: scroll_state,
                        nested_scroll_transition=mock.MagicMock(),
                        nested_scroll_renderer=mock.MagicMock(),
                    )
                )
            self.assertEqual(str(exc_info.exception), "viewer terminal restoration failed")

    def test_render_nested_scroll_view_empty_state_returns_valid_frame(self) -> None:
        """Verify empty view state produces a RenderedFrame with line_count=1 without ValueError."""
        caps = TerminalCapabilities(
            CapabilityProfile.ANSI,
            80,
            input_tty=True,
            output_tty=True,
            raw_mode=True,
            cursor_controls=True,
            decorative_sgr=True,
        )
        scroll_state = MultiAgentScrollState((), 0)
        frame = render_nested_scroll_view(scroll_state, caps)
        self.assertEqual(frame.line_count, 1)
        self.assertEqual(frame.text, "")

    def test_text_delta_multiline_rechunks_all_long_lines(self) -> None:
        """Verify multiline TEXT_DELTA chunks all lines exceeding 2000 chars, not just the last one."""
        state = AdvisoryProgressState()
        key = ("agent1", "op1")
        # Send multiline text delta where first line is 3000 chars, second is 4000 chars, third is 100 chars
        multiline_text = ("a" * 3000) + "\n" + ("b" * 4000) + "\n" + ("c" * 100)
        state.observe_stream(
            ProviderStreamEvent(ProviderStreamEventKind.TEXT_DELTA, "agent1", multiline_text, operation_id="op1")
        )
        buf = state.agent_buffers[key]
        for line in buf:
            self.assertLessEqual(len(line), 2000)

    def test_terminal_restoration_raising_resets_progress_state_and_clears_frame(self) -> None:
        """Verify driver progress state is reset and frame cleared even if terminal restoration raises."""
        driver = RawTerminalDriver(
            lambda _prompt: "",
            io.StringIO(),
            io.StringIO(),
            lambda s, e: (s, None),
            lambda s, e, h: (s, None),
            lambda h, t: h,
        )

        async def dummy_op():
            return "done"

        caps = TerminalCapabilities(
            CapabilityProfile.ANSI,
            80,
            input_tty=True,
            output_tty=True,
            raw_mode=True,
            cursor_controls=True,
            decorative_sgr=True,
        )
        with mock.patch.object(RawTerminalDriver, "capabilities", return_value=caps), \
             mock.patch.object(RawTerminalDriver, "_prepare_input", return_value=(caps, mock.MagicMock())), \
             mock.patch("story_writing_agents.interactive_terminal._driver._activate_terminal"), \
             mock.patch(
                 "story_writing_agents.interactive_terminal._driver._restore_terminal_or_raise",
                 side_effect=RuntimeError("interactive terminal restoration failed"),
             ):
            with self.assertRaises(RuntimeError) as exc_info:
                asyncio.run(
                    driver.run_advisory_progress(
                        dummy_op(),
                        lambda _f: "frame",
                        toggle_callback=lambda: None,
                    )
                )
            self.assertIn("restoration failed", str(exc_info.exception))
        self.assertFalse(driver._progress_active)
        self.assertIsNone(driver._progress_renderer)
        self.assertEqual(driver._progress_rows, 0)

    async def test_interactive_progress_does_not_dismiss_failure_snapshot_on_none_result(self) -> None:
        mock_terminal = mock.create_autospec(InteractiveTerminal, instance=True)
        mock_terminal.raw_mode = True

        async def fake_run(op, scroll_state_provider=None, is_failure=None):
            return await op

        mock_terminal._advisory_progress = fake_run

        progress = InteractiveAdvisoryProgress(mock_terminal)
        progress._state.record_error("Simulated initial auth required error")
        progress._state.snapshot_failure()
        self.assertIsNotNone(progress._state.failure_snapshot)

        async def none_op():
            return None

        result = await progress.run(none_op())
        self.assertIsNone(result)
        # Failure snapshot should NOT have been dismissed when result is None
        self.assertIsNotNone(progress._state.failure_snapshot)
        self.assertEqual(progress._state.last_error_message, "Simulated initial auth required error")

        async def success_op():
            class SuccessTurn:
                status = "completed"
            return SuccessTurn()

        result2 = await progress.run(success_op())
        self.assertEqual(result2.status, "completed")
        # Now snapshot is dismissed on actual success
        self.assertIsNone(progress._state.failure_snapshot)

    async def test_interactive_progress_sanitizes_unexpected_exceptions(self) -> None:
        mock_terminal = mock.create_autospec(InteractiveTerminal, instance=True)
        mock_terminal.raw_mode = True
        mock_terminal.progress_frame_collapsed = False

        captured_provider = None

        async def fake_run(op, scroll_state_provider=None, is_failure=None):
            nonlocal captured_provider
            captured_provider = scroll_state_provider
            return await op

        mock_terminal._advisory_progress = fake_run

        progress = InteractiveAdvisoryProgress(mock_terminal)

        # 1. Unexpected exception raised by operation: message is sanitized
        async def failing_op():
            raise RuntimeError("sensitive database path: /var/db/secret.sqlite")

        with self.assertRaises(RuntimeError):
            await progress.run(failing_op())

        self.assertEqual(
            progress._state.last_error_message,
            "Unexpected failure during operation.",
        )

        # 2. Allowlisted BackendProtocolError raised by operation: message is preserved
        progress._state.last_error_message = None

        async def protocol_op():
            raise BackendProtocolError("Codex CLI exited with status 1")

        with self.assertRaises(BackendProtocolError):
            await progress.run(protocol_op())

        self.assertEqual(
            progress._state.last_error_message,
            "Codex CLI exited with status 1",
        )

        # 3. scroll_state_provider with unexpected exception: sanitized
        progress._state.last_error_message = None

        async def dummy_op():
            return None

        await progress.run(dummy_op())
        self.assertIsNotNone(captured_provider)

        captured_provider(RuntimeError("leak /private/tmp/keys.txt"))
        self.assertEqual(
            progress._state.last_error_message,
            "Unexpected failure during operation.",
        )

        # 4. scroll_state_provider with BackendProtocolError: preserved
        progress._state.last_error_message = None
        captured_provider(BackendProtocolError("Transport closed prematurely"))
        self.assertEqual(
            progress._state.last_error_message,
            "Transport closed prematurely",
        )

    def test_run_progress_frames_preserves_typeahead_characters(self) -> None:
        driver = RawTerminalDriver(
            lambda _p: "",
            io.StringIO(),
            io.StringIO(),
            transition_line_input,
            transition_main_input,
            append_main_input_history,
        )
        configuration = _driver._TerminalConfiguration(
            0,
            1,
            [0, 0, 0, 0, 0, 0, []],
            [0, 0, 0, 0, 0, 0, []],
            80,
            None,
            None,
            0,
            object(),
        )
        toggled: list[bool] = []

        def on_toggle() -> bool:
            toggled.append(True)
            return True

        frames: list[int] = []

        def renderer(frame: int) -> str:
            frames.append(frame)
            return f"frame {frame}"

        read_chars = ["a", "t", "b", "c"]

        def fake_read(*_args: object, **kwargs: object) -> str:
            # Fake live reader returns next queued char
            return read_chars.pop(0)

        def fake_select(*_args: object, **_kwargs: object) -> tuple[list[int], list[int], list[int]]:
            if read_chars:
                return ([0], [], [])
            return ([], [], [])

        async def run_test() -> None:
            task = asyncio.create_task(
                driver._run_progress_frames(
                    renderer,
                    configuration=configuration,
                    toggle_callback=on_toggle,
                )
            )
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        with (
            mock.patch.object(RawTerminalDriver, "_read_character", side_effect=fake_read),
            mock.patch("story_writing_agents.interactive_terminal._driver._select.select", side_effect=fake_select),
            mock.patch("story_writing_agents.interactive_terminal._driver._configured_control_event", return_value=None),
        ):
            asyncio.run(run_test())

        # Toggle callback was invoked for 't'
        self.assertEqual(len(toggled), 1)
        # Non-toggle characters 'a', 'b', 'c' are preserved in typeahead buffer
        self.assertEqual(list(driver._typeahead_buffer), ["a", "b", "c"])

    def test_run_advisory_progress_propagates_activation_failure_and_closes_operation(self) -> None:
        driver = RawTerminalDriver(
            lambda _p: "",
            io.StringIO(),
            io.StringIO(),
            transition_line_input,
            transition_main_input,
            append_main_input_history,
        )
        fake_caps = TerminalCapabilities(
            CapabilityProfile.ANSI,
            80,
            input_tty=True,
            output_tty=True,
            raw_mode=True,
            cursor_controls=True,
            decorative_sgr=True,
        )
        configuration = _driver._TerminalConfiguration(
            0,
            1,
            [0, 0, 0, 0, 0, 0, []],
            [0, 0, 0, 0, 0, 0, []],
            80,
            None,
            None,
            0,
            mock.MagicMock(),
        )

        async def sample_op():
            await asyncio.sleep(1)

        coro = sample_op()

        with (
            mock.patch.object(RawTerminalDriver, "capabilities", return_value=fake_caps),
            mock.patch.object(RawTerminalDriver, "_observe_input_capabilities", return_value=fake_caps),
            mock.patch.object(RawTerminalDriver, "_prepare_input", return_value=(fake_caps, configuration)),
            mock.patch("story_writing_agents.interactive_terminal._driver._activate_terminal", side_effect=OSError("tcsetattr failed")),
            mock.patch("story_writing_agents.interactive_terminal._driver._restore_terminal_or_raise") as mock_restore,
        ):
            with self.assertRaisesRegex(OSError, "tcsetattr failed"):
                asyncio.run(
                    driver.run_advisory_progress(
                        coro,
                        lambda _f: "progress",
                        toggle_callback=lambda: True,
                    )
                )

            mock_restore.assert_called_once()

    def test_run_advisory_progress_propagates_draw_restoration_failure_and_closes_operation(self) -> None:
        driver = RawTerminalDriver(
            lambda _p: "",
            io.StringIO(),
            io.StringIO(),
            transition_line_input,
            transition_main_input,
            append_main_input_history,
        )
        fake_caps = TerminalCapabilities(
            CapabilityProfile.ANSI,
            80,
            input_tty=True,
            output_tty=True,
            raw_mode=True,
            cursor_controls=True,
            decorative_sgr=True,
        )
        configuration = _driver._TerminalConfiguration(
            0,
            1,
            [0, 0, 0, 0, 0, 0, []],
            [0, 0, 0, 0, 0, 0, []],
            80,
            None,
            None,
            0,
            mock.MagicMock(),
        )

        async def sample_op():
            await asyncio.sleep(1)

        coro = sample_op()

        with (
            mock.patch.object(RawTerminalDriver, "capabilities", return_value=fake_caps),
            mock.patch.object(RawTerminalDriver, "_observe_input_capabilities", return_value=fake_caps),
            mock.patch.object(RawTerminalDriver, "_prepare_input", return_value=(fake_caps, configuration)),
            mock.patch("story_writing_agents.interactive_terminal._driver._activate_terminal"),
            mock.patch.object(RawTerminalDriver, "_draw_progress_frame", side_effect=ValueError("draw boom")),
            mock.patch("story_writing_agents.interactive_terminal._driver._restore_terminal_or_raise", side_effect=RuntimeError("restoration failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "restoration failed"):
                asyncio.run(
                    driver.run_advisory_progress(
                        coro,
                        lambda _f: "progress",
                        toggle_callback=lambda: True,
                    )
                )

    def test_redraw_nested_scroll_frame_validates_type_width_and_safety(self) -> None:
        driver = RawTerminalDriver(
            lambda _p: "",
            io.StringIO(),
            io.StringIO(),
            transition_line_input,
            transition_main_input,
            append_main_input_history,
        )
        caps = TerminalCapabilities(
            CapabilityProfile.ANSI,
            40,
            input_tty=True,
            output_tty=True,
            raw_mode=True,
            cursor_controls=True,
            decorative_sgr=True,
        )
        state = MultiAgentScrollState(views=())

        # Non-callable renderer
        with self.assertRaises(TypeError):
            driver._redraw_nested_scroll_frame(state, caps, "not callable")  # type: ignore[arg-type]

        # Renderer returns non-RenderedFrame
        with self.assertRaises(TypeError):
            driver._redraw_nested_scroll_frame(state, caps, lambda _s, _c: "string frame")  # type: ignore[return-value]

        # Renderer returns width mismatch
        from story_writing_agents.interactive_terminal._model import RenderedFrame
        with self.assertRaises(ValueError):
            driver._redraw_nested_scroll_frame(state, caps, lambda _s, _c: RenderedFrame("line", 80, 1))

        # Renderer returns text with unauthorized escape sequence
        with self.assertRaises(ValueError):
            driver._redraw_nested_scroll_frame(state, caps, lambda _s, _c: RenderedFrame("line\x1b[31m", 40, 1))

        # Valid frame succeeds and writes text
        valid_frame = RenderedFrame("safe line", 40, 1)
        driver._redraw_nested_scroll_frame(state, caps, lambda _s, _c: valid_frame)
        self.assertEqual(driver._progress_rows, 1)

    def test_draw_progress_frame_tracks_rows_before_write_for_error_cleanup(self) -> None:
        out = io.StringIO()
        driver = RawTerminalDriver(
            lambda _p: "",
            io.StringIO(),
            out,
            transition_line_input,
            transition_main_input,
            append_main_input_history,
        )
        driver._progress_frame = 0

        # When _write_raw_text raises, _progress_rows is already set to row count
        with mock.patch.object(RawTerminalDriver, "_write_raw_text", side_effect=OSError("write incomplete")):
            with self.assertRaises(OSError):
                driver._draw_progress_frame(lambda _f: "line1\nline2\nline3")

        self.assertEqual(driver._progress_rows, 3)

        # Calling _clear_progress_frame cleans up those 3 rows and resets _progress_rows to 0
        driver._clear_progress_frame()
        self.assertEqual(driver._progress_rows, 0)
        self.assertIn("\r\x1b[K", out.getvalue())

    def test_nested_scroll_inspector_preserves_buffered_typeahead(self) -> None:
        driver = RawTerminalDriver(
            lambda _p: "",
            io.StringIO(),
            io.StringIO(),
            transition_line_input,
            transition_main_input,
            append_main_input_history,
        )
        caps = TerminalCapabilities(
            CapabilityProfile.ANSI,
            80,
            input_tty=True,
            output_tty=True,
            raw_mode=True,
            cursor_controls=True,
            decorative_sgr=True,
        )
        configuration = _driver._TerminalConfiguration(
            0, 1, [0]*7, [0]*7, 80, None, None, 0, mock.MagicMock(),
        )
        driver._typeahead_buffer.extend(["c", "m", "d"])

        # Inspector receives 'q' from live reader
        with (
            mock.patch("story_writing_agents.interactive_terminal._driver._activate_terminal"),
            mock.patch("story_writing_agents.interactive_terminal._driver._restore_terminal_or_raise"),
            mock.patch.object(RawTerminalDriver, "_read_character", side_effect=lambda consume_typeahead=True: "q"),
            mock.patch.object(RawTerminalDriver, "_redraw_nested_scroll_frame"),
        ):
            driver.run_nested_scroll_inspector(
                MultiAgentScrollState(views=()),
                lambda s, _e: s,
                mock.MagicMock(),
            )

        # Typeahead buffer was NOT drained by inspector
        self.assertEqual(list(driver._typeahead_buffer), ["c", "m", "d"])

    def test_run_progress_frames_propagates_eof_and_interrupt_without_advisory_error_wrapping(self) -> None:
        from story_writing_agents.interactive_terminal._driver import AdvisoryProgressError

        driver = RawTerminalDriver(
            lambda _p: "",
            io.StringIO(),
            io.StringIO(),
            transition_line_input,
            transition_main_input,
            append_main_input_history,
        )
        fake_caps = TerminalCapabilities(
            CapabilityProfile.ANSI,
            80,
            input_tty=True,
            output_tty=True,
            raw_mode=True,
            cursor_controls=True,
            decorative_sgr=True,
        )
        configuration = _driver._TerminalConfiguration(
            0, 1, [0]*7, [0]*7, 80, None, None, 0, mock.MagicMock(),
        )

        async def infinite_op():
            await asyncio.sleep(100)

        # When progress frame worker encounters EOF, run_advisory_progress propagates EOFError directly
        with (
            mock.patch.object(RawTerminalDriver, "capabilities", return_value=fake_caps),
            mock.patch.object(RawTerminalDriver, "_observe_input_capabilities", return_value=fake_caps),
            mock.patch.object(RawTerminalDriver, "_prepare_input", return_value=(fake_caps, configuration)),
            mock.patch("story_writing_agents.interactive_terminal._driver._activate_terminal"),
            mock.patch("story_writing_agents.interactive_terminal._driver._restore_terminal_or_raise"),
            mock.patch.object(RawTerminalDriver, "_draw_progress_frame"),
            mock.patch.object(RawTerminalDriver, "_clear_progress_frame"),
            mock.patch.object(RawTerminalDriver, "_run_progress_frames", side_effect=EOFError()),
        ):
            with self.assertRaises(EOFError):
                asyncio.run(driver.run_advisory_progress(infinite_op(), lambda _f: "frame"))

        # When progress frame worker encounters KeyboardInterrupt, run_advisory_progress propagates KeyboardInterrupt directly
        with (
            mock.patch.object(RawTerminalDriver, "capabilities", return_value=fake_caps),
            mock.patch.object(RawTerminalDriver, "_observe_input_capabilities", return_value=fake_caps),
            mock.patch.object(RawTerminalDriver, "_prepare_input", return_value=(fake_caps, configuration)),
            mock.patch("story_writing_agents.interactive_terminal._driver._activate_terminal"),
            mock.patch("story_writing_agents.interactive_terminal._driver._restore_terminal_or_raise"),
            mock.patch.object(RawTerminalDriver, "_draw_progress_frame"),
            mock.patch.object(RawTerminalDriver, "_clear_progress_frame"),
            mock.patch.object(RawTerminalDriver, "_run_progress_frames", side_effect=KeyboardInterrupt()),
        ):
            with self.assertRaises(KeyboardInterrupt):
                asyncio.run(driver.run_advisory_progress(infinite_op(), lambda _f: "frame"))

    def test_is_content_safe_failure_rejects_unrelated_class_named_backend_protocol_error(self) -> None:
        from story_writing_agents.interactive_terminal._core import _is_content_safe_failure
        from story_writing_agents.errors import BackendProtocolError as RealBackendProtocolError

        class FakeBackendProtocolError(Exception):
            pass
        FakeBackendProtocolError.__name__ = "BackendProtocolError"
        FakeBackendProtocolError.__qualname__ = "BackendProtocolError"

        self.assertFalse(_is_content_safe_failure(FakeBackendProtocolError("secret paths")))
        self.assertTrue(_is_content_safe_failure(RealBackendProtocolError("safe message")))

    def test_advisory_progress_finish_refreshes_snapshot_from_final_failed_state(self) -> None:
        state = AdvisoryProgressState()
        state.begin((AdvisoryProgressRequest("agent_1", "op_1", "Drafting"),))
        state.record_error("Initial error")
        self.assertIsNotNone(state.failure_snapshot)
        self.assertEqual(state.failure_snapshot[1], "Initial error")

        # More stream lines arrive after initial error
        state.observe_stream(
            ProviderStreamEvent(
                kind=ProviderStreamEventKind.TEXT_DELTA,
                agent_id="agent_1",
                text="Additional streamed paragraph.\n",
                operation_id="op_1",
            )
        )
        self.assertIn("Additional streamed paragraph.", state.agent_buffers.get(("agent_1", "op_1"), []))

        # finish() refreshes the snapshot before clearing live buffers
        state.finish()
        self.assertIsNotNone(state.failure_snapshot)
        snapshot_buffers, last_err = state.failure_snapshot
        self.assertEqual(last_err, "Initial error")
        self.assertIn(("agent_1", "op_1"), snapshot_buffers)
        self.assertIn("Additional streamed paragraph.", snapshot_buffers[("agent_1", "op_1")])
        # Live buffer itself was cleared
        self.assertEqual(len(state.agent_buffers), 0)

    def test_nested_scroll_viewport_h_offset_validation_and_default(self) -> None:
        viewport = NestedScrollViewport(
            view_id="test:1",
            title="Agent",
            total_lines=1,
            start_line=1,
            visible_lines=("line 1",),
            visible_height=1,
            is_focused=True,
        )
        self.assertEqual(viewport.h_offset, 0)

        viewport_with_offset = NestedScrollViewport(
            view_id="test:1",
            title="Agent",
            total_lines=1,
            start_line=1,
            visible_lines=("line 1",),
            visible_height=1,
            is_focused=True,
            h_offset=8,
        )
        self.assertEqual(viewport_with_offset.h_offset, 8)

        with self.assertRaises(ValueError):
            NestedScrollViewport(
                view_id="test:1",
                title="Agent",
                total_lines=1,
                start_line=1,
                visible_lines=("line 1",),
                visible_height=1,
                is_focused=True,
                h_offset=-1,
            )

        with self.assertRaises(ValueError):
            NestedScrollViewport(
                view_id="test:1",
                title="Agent",
                total_lines=1,
                start_line=1,
                visible_lines=("line 1",),
                visible_height=1,
                is_focused=True,
                h_offset="invalid",  # type: ignore[arg-type]
            )

    def test_clip_cells_horizontal_scroll_offset_and_markers(self) -> None:
        from story_writing_agents.interactive_terminal._viewer_render import _clip_cells

        # offset == 0 preserves standard ellipsis truncation
        clipped_0 = _clip_cells("abcdefghijklmnopqrstuvwxyz", 10, offset=0)
        self.assertEqual(clipped_0, "abcdefghi…")

        # offset > 0 with both left and right overflow
        clipped_pan = _clip_cells("abcdefghijklmnopqrstuvwxyz", 10, offset=4)
        self.assertEqual(clipped_pan, "←efghijkl→")
        self.assertEqual(len(clipped_pan), 10)

        # offset > 0 with left overflow only (end of line fits within width)
        clipped_end = _clip_cells("abcdefgh", 10, offset=2)
        self.assertEqual(clipped_end, "←cdefgh")
        self.assertTrue(len(clipped_end) <= 10)

        # offset > 0 with all text scrolled off to the left
        clipped_empty_window = _clip_cells("abc", 10, offset=5)
        self.assertEqual(clipped_empty_window, "←")

        # empty line returns empty string regardless of offset
        self.assertEqual(_clip_cells("", 10, offset=4), "")

        # width == 1 returns left marker when content exists
        self.assertEqual(_clip_cells("abcdef", 1, offset=2), "←")

        # width == 2 returns both markers when right overflow exists
        self.assertEqual(_clip_cells("abcdef", 2, offset=2), "←→")

        # negative or invalid offset raises ValueError
        with self.assertRaises(ValueError):
            _clip_cells("abc", 10, offset=-1)
        with self.assertRaises(ValueError):
            _clip_cells("abc", 10, offset="invalid")  # type: ignore[arg-type]

    def test_transition_nested_scroll_horizontal_scrolling(self) -> None:
        viewport_1 = NestedScrollViewport(
            view_id="a1:1",
            title="Agent 1",
            total_lines=10,
            start_line=1,
            visible_lines=("line 1", "line 2"),
            visible_height=2,
            is_focused=True,
            h_offset=0,
        )
        viewport_2 = NestedScrollViewport(
            view_id="a2:1",
            title="Agent 2",
            total_lines=10,
            start_line=1,
            visible_lines=("line 1", "line 2"),
            visible_height=2,
            is_focused=False,
            h_offset=0,
        )
        state = MultiAgentScrollState((viewport_1, viewport_2), 0)

        # Pan right with RIGHT key
        state_r1 = transition_nested_scroll(state, KeyEvent(KeyKind.RIGHT))
        self.assertEqual(state_r1.views[0].h_offset, 4)
        self.assertEqual(state_r1.views[1].h_offset, 0)

        # Pan right with 'l' key
        state_r2 = transition_nested_scroll(state_r1, KeyEvent(KeyKind.TEXT, text="l"))
        self.assertEqual(state_r2.views[0].h_offset, 8)

        # Pan left with LEFT key
        state_l1 = transition_nested_scroll(state_r2, KeyEvent(KeyKind.LEFT))
        self.assertEqual(state_l1.views[0].h_offset, 4)

        # Pan left with 'h' key
        state_l2 = transition_nested_scroll(state_l1, KeyEvent(KeyKind.TEXT, text="h"))
        self.assertEqual(state_l2.views[0].h_offset, 0)

        # Pan left at 0 remains 0 (bounded)
        state_l3 = transition_nested_scroll(state_l2, KeyEvent(KeyKind.LEFT))
        self.assertEqual(state_l3.views[0].h_offset, 0)

        # Tab switches focused agent and preserves each agent's h_offset
        state_tab = transition_nested_scroll(state_r2, KeyEvent(KeyKind.TAB))
        self.assertEqual(state_tab.focused_index, 1)
        self.assertEqual(state_tab.views[0].h_offset, 8)
        self.assertEqual(state_tab.views[1].h_offset, 0)

        # Pan on agent 2 adjusts agent 2's h_offset independently
        state_a2_pan = transition_nested_scroll(state_tab, KeyEvent(KeyKind.RIGHT))
        self.assertEqual(state_a2_pan.views[0].h_offset, 8)
        self.assertEqual(state_a2_pan.views[1].h_offset, 4)

    def test_render_nested_scroll_view_with_horizontal_offset(self) -> None:
        long_line = "This is a very long text line that easily exceeds standard terminal widths."
        viewport = NestedScrollViewport(
            view_id="a:1",
            title="Agent",
            total_lines=1,
            start_line=1,
            visible_lines=(long_line,),
            visible_height=1,
            is_focused=True,
            h_offset=10,
        )
        state = MultiAgentScrollState((viewport,), 0)
        caps = TerminalCapabilities(
            CapabilityProfile.ANSI,
            30,
            input_tty=True,
            output_tty=True,
            raw_mode=True,
            cursor_controls=True,
            decorative_sgr=True,
        )
        rendered = render_nested_scroll_view(state, caps)
        self.assertEqual(rendered.h_offset, 10)
        rows = rendered.text.split("\n")
        # Body line (row index 1) contains overflow indicators
        body_row = rows[1]
        self.assertIn("←", body_row)
        self.assertIn("→", body_row)
        # Footer contains pan hint
        footer_row = rows[-1]
        self.assertIn("←/→: pan", footer_row)

    def test_run_advisory_progress_frame_persists_when_keep_frame_true(self) -> None:
        capabilities = TerminalCapabilities(
            CapabilityProfile.ANSI,
            80,
            input_tty=True,
            output_tty=True,
            raw_mode=True,
            cursor_controls=True,
            decorative_sgr=True,
        )
        real_sleep = asyncio.sleep

        async def fast_sleep(_delay: float) -> None:
            await real_sleep(0)

        output = io.StringIO()
        with (
            mock.patch.object(RawTerminalDriver, "capabilities", return_value=capabilities),
            mock.patch.object(_driver.asyncio, "sleep", side_effect=fast_sleep),
        ):
            driver = RawTerminalDriver(
                lambda _prompt: "",
                io.StringIO(),
                output,
                lambda s, e: (s, None),
                lambda s, e, h: (s, None),
                lambda h, t: h,
            )

            async def successful_op() -> dict:
                await real_sleep(0.01)
                return {"status": "ok"}

            res = asyncio.run(
                driver.run_advisory_progress(
                    successful_op(),
                    lambda _f: "Running task...",
                    keep_frame=True,
                )
            )
            self.assertEqual(res, {"status": "ok"})
            val = output.getvalue()
            # The frame text is written and ended with newline \r\n (retained), not cleared at end
            self.assertIn("Running task...", val)
            self.assertTrue(val.endswith("\r\n"))

    def test_run_advisory_progress_frame_clears_when_keep_frame_false(self) -> None:
        capabilities = TerminalCapabilities(
            CapabilityProfile.ANSI,
            80,
            input_tty=True,
            output_tty=True,
            raw_mode=True,
            cursor_controls=True,
            decorative_sgr=True,
        )
        real_sleep = asyncio.sleep

        async def fast_sleep(_delay: float) -> None:
            await real_sleep(0)

        output = io.StringIO()
        with (
            mock.patch.object(RawTerminalDriver, "capabilities", return_value=capabilities),
            mock.patch.object(_driver.asyncio, "sleep", side_effect=fast_sleep),
        ):
            driver = RawTerminalDriver(
                lambda _prompt: "",
                io.StringIO(),
                output,
                lambda s, e: (s, None),
                lambda s, e, h: (s, None),
                lambda h, t: h,
            )

            async def successful_op() -> dict:
                await real_sleep(0.01)
                return {"status": "ok"}

            res = asyncio.run(
                driver.run_advisory_progress(
                    successful_op(),
                    lambda _f: "Running task...",
                    keep_frame=False,
                )
            )
            self.assertEqual(res, {"status": "ok"})
            val = output.getvalue()
            self.assertTrue(val.endswith("\r\x1b[K"))

    def test_terminal_progress_mode_persists_frame_when_on_and_clears_when_off(self) -> None:
        from story_writing_agents.interactive_terminal._core import build_stream_terminal

        capabilities = TerminalCapabilities(
            CapabilityProfile.ANSI,
            80,
            input_tty=True,
            output_tty=True,
            raw_mode=True,
            cursor_controls=True,
            decorative_sgr=True,
        )
        real_sleep = asyncio.sleep

        async def fast_sleep(_delay: float) -> None:
            await real_sleep(0)

        # 1. When progress_frame_collapsed is False and an active turn is started (begin()), frame persists
        out_on = io.StringIO()
        with (
            mock.patch.object(RawTerminalDriver, "capabilities", return_value=capabilities),
            mock.patch.object(_driver.asyncio, "sleep", side_effect=fast_sleep),
        ):
            terminal_on = build_stream_terminal(lambda _p: "", io.StringIO(), out_on)
            terminal_on.progress_frame_collapsed = False
            progress_on = InteractiveAdvisoryProgress(terminal_on)
            progress_on.begin()

            async def op_on() -> str:
                await real_sleep(0.01)
                return "done"

            res_on = asyncio.run(progress_on.run(op_on()))
            self.assertEqual(res_on, "done")
            self.assertTrue(out_on.getvalue().endswith("\r\n"))

        # 2. When progress_frame_collapsed is True (progress == off), frame clears even on active turns
        out_off = io.StringIO()
        with (
            mock.patch.object(RawTerminalDriver, "capabilities", return_value=capabilities),
            mock.patch.object(_driver.asyncio, "sleep", side_effect=fast_sleep),
        ):
            terminal_off = build_stream_terminal(lambda _p: "", io.StringIO(), out_off)
            terminal_off.progress_frame_collapsed = True
            progress_off = InteractiveAdvisoryProgress(terminal_off)
            progress_off.begin()

            async def op_off() -> str:
                await real_sleep(0.01)
                return "done"

            res_off = asyncio.run(progress_off.run(op_off()))
            self.assertEqual(res_off, "done")
            self.assertTrue(out_off.getvalue().endswith("\r\x1b[K"))

    def test_progress_run_without_begin_clears_by_default_even_when_progress_on(self) -> None:
        from story_writing_agents.interactive_terminal._core import build_stream_terminal

        capabilities = TerminalCapabilities(
            CapabilityProfile.ANSI,
            80,
            input_tty=True,
            output_tty=True,
            raw_mode=True,
            cursor_controls=True,
            decorative_sgr=True,
        )
        real_sleep = asyncio.sleep

        async def fast_sleep(_delay: float) -> None:
            await real_sleep(0)

        output = io.StringIO()
        with (
            mock.patch.object(RawTerminalDriver, "capabilities", return_value=capabilities),
            mock.patch.object(_driver.asyncio, "sleep", side_effect=fast_sleep),
        ):
            terminal = build_stream_terminal(lambda _p: "", io.StringIO(), output)
            terminal.progress_frame_collapsed = False
            progress = InteractiveAdvisoryProgress(terminal)

            async def startup_op() -> str:
                await real_sleep(0.01)
                return "ready"

            # When begin() is NOT called (transient preparation/startup), progress clears
            res = asyncio.run(progress.run(startup_op()))
            self.assertEqual(res, "ready")
            self.assertTrue(output.getvalue().endswith("\r\x1b[K"))

    def test_progress_run_explicit_keep_frame_overrides(self) -> None:
        from story_writing_agents.interactive_terminal._core import build_stream_terminal

        capabilities = TerminalCapabilities(
            CapabilityProfile.ANSI,
            80,
            input_tty=True,
            output_tty=True,
            raw_mode=True,
            cursor_controls=True,
            decorative_sgr=True,
        )
        real_sleep = asyncio.sleep

        async def fast_sleep(_delay: float) -> None:
            await real_sleep(0)

        # 1. keep_frame=False clears even when begin() was called and progress == on
        out1 = io.StringIO()
        with (
            mock.patch.object(RawTerminalDriver, "capabilities", return_value=capabilities),
            mock.patch.object(_driver.asyncio, "sleep", side_effect=fast_sleep),
        ):
            terminal = build_stream_terminal(lambda _p: "", io.StringIO(), out1)
            terminal.progress_frame_collapsed = False
            progress = InteractiveAdvisoryProgress(terminal)
            progress.begin()

            async def op1() -> str:
                await real_sleep(0.01)
                return "op1"

            res1 = asyncio.run(progress.run(op1(), keep_frame=False))
            self.assertEqual(res1, "op1")
            self.assertTrue(out1.getvalue().endswith("\r\x1b[K"))

        # 2. keep_frame=True persists even when begin() was NOT called
        out2 = io.StringIO()
        with (
            mock.patch.object(RawTerminalDriver, "capabilities", return_value=capabilities),
            mock.patch.object(_driver.asyncio, "sleep", side_effect=fast_sleep),
        ):
            terminal2 = build_stream_terminal(lambda _p: "", io.StringIO(), out2)
            terminal2.progress_frame_collapsed = True
            progress2 = InteractiveAdvisoryProgress(terminal2)

            async def op2() -> str:
                await real_sleep(0.01)
                return "op2"

            res2 = asyncio.run(progress2.run(op2(), keep_frame=True))
            self.assertEqual(res2, "op2")
            self.assertTrue(out2.getvalue().endswith("\r\n"))

    def test_success_response_invokes_nested_scroll_inspector_with_keep_frame_true(self) -> None:
        """Verify successful operation with keep_frame=True invokes run_nested_scroll_inspector with keep_frame=True."""
        capabilities = TerminalCapabilities(
            CapabilityProfile.ANSI,
            80,
            input_tty=True,
            output_tty=True,
            raw_mode=True,
            cursor_controls=True,
            decorative_sgr=True,
        )
        out = io.StringIO()
        driver = RawTerminalDriver(
            lambda _p: "",
            io.StringIO(),
            out,
            lambda s, e: (s, None),
            lambda s, e, h: (s, None),
            lambda h, t: h,
        )
        view = NestedScrollViewport(
            view_id="lead:1",
            title="Lead",
            total_lines=5,
            start_line=1,
            visible_lines=("Done step 1",),
            visible_height=1,
            is_focused=True,
            state=ViewState.IDLE,
        )
        scroll_state = MultiAgentScrollState((view,), 0)

        async def successful_op() -> dict:
            return {"status": "ok"}

        inspector_called = []

        def fake_inspector(state, transition, renderer, toggle_callback=None, keep_frame=False):
            inspector_called.append({"state": state, "keep_frame": keep_frame})
            return state

        with (
            mock.patch.object(RawTerminalDriver, "capabilities", return_value=capabilities),
            mock.patch.object(RawTerminalDriver, "run_nested_scroll_inspector", side_effect=fake_inspector),
        ):
            res = asyncio.run(
                driver.run_advisory_progress(
                    successful_op(),
                    lambda _f: "Running...",
                    scroll_state_provider=lambda _r: scroll_state,
                    nested_scroll_transition=mock.MagicMock(),
                    nested_scroll_renderer=mock.MagicMock(),
                    keep_frame=True,
                )
            )
            self.assertEqual(res, {"status": "ok"})
            self.assertEqual(len(inspector_called), 1)
            self.assertEqual(inspector_called[0]["state"], scroll_state)
            self.assertTrue(inspector_called[0]["keep_frame"])

    def test_nested_scroll_inspector_focus_cycling_and_frame_retention_on_success(self) -> None:
        """Verify inspector cycles focus across viewports on Tab and retains frame with newline when keep_frame=True."""
        out = io.StringIO()
        driver = RawTerminalDriver(
            lambda _p: "",
            io.StringIO(),
            out,
            lambda s, e: (s, None),
            lambda s, e, h: (s, None),
            lambda h, t: h,
        )
        v1 = NestedScrollViewport(
            view_id="a1:1",
            title="Agent 1",
            total_lines=3,
            start_line=1,
            visible_lines=("agent 1 line",),
            visible_height=1,
            is_focused=True,
            state=ViewState.IDLE,
        )
        v2 = NestedScrollViewport(
            view_id="a2:1",
            title="Agent 2",
            total_lines=3,
            start_line=1,
            visible_lines=("agent 2 line",),
            visible_height=1,
            is_focused=False,
            state=ViewState.IDLE,
        )
        initial_state = MultiAgentScrollState((v1, v2), 0)

        fake_config = mock.MagicMock()
        fake_caps = TerminalCapabilities(
            CapabilityProfile.ANSI,
            80,
            input_tty=True,
            output_tty=True,
            raw_mode=True,
            cursor_controls=True,
            decorative_sgr=True,
        )

        # User presses Tab (cycle focus to Agent 2) then q (exit inspector)
        with (
            mock.patch.object(RawTerminalDriver, "_observe_input_capabilities", return_value=fake_caps),
            mock.patch.object(RawTerminalDriver, "_prepare_input", return_value=(fake_caps, fake_config)),
            mock.patch("story_writing_agents.interactive_terminal._driver._activate_terminal"),
            mock.patch("story_writing_agents.interactive_terminal._driver._restore_terminal_or_raise"),
            mock.patch.object(RawTerminalDriver, "_read_character", side_effect=["\t", "q"]),
            mock.patch.object(RawTerminalDriver, "_clear_progress_frame") as mock_clear,
        ):
            final_state = driver.run_nested_scroll_inspector(
                initial_state,
                transition_nested_scroll,
                render_nested_scroll_view,
                keep_frame=True,
            )

        # Tab switched focus to agent 2
        self.assertEqual(final_state.focused_index, 1)
        self.assertFalse(final_state.views[0].is_focused)
        self.assertTrue(final_state.views[1].is_focused)

        # _clear_progress_frame was called once during Tab redraw, but NOT upon exit because keep_frame=True
        self.assertEqual(mock_clear.call_count, 1)
        self.assertTrue(out.getvalue().endswith("\r\n"))

    def test_provide_scroll_state_on_success_sets_idle_state_and_does_not_record_error(self) -> None:
        """Verify success response passes ViewState.IDLE and does not record result message as an error."""
        from story_writing_agents.interactive_terminal._core import build_stream_terminal

        terminal = build_stream_terminal(lambda _p: "", io.StringIO(), io.StringIO())
        terminal.progress_frame_collapsed = False
        progress = InteractiveAdvisoryProgress(terminal)
        progress.begin(
            (AdvisoryProgressRequest("lead", "turn_1", "Leading..."),)
        )

        class SuccessResult:
            status = "completed"
            message = "Draft generated successfully."

        result_obj = SuccessResult()

        captured_scroll_state = []

        async def fake_advisory_progress(op, renderer=None, scroll_state_provider=None, **kwargs):
            res = await op
            if scroll_state_provider is not None:
                state = scroll_state_provider(res)
                captured_scroll_state.append(state)
            return res

        progress._run = fake_advisory_progress

        async def op():
            return result_obj

        res = asyncio.run(progress.run(op(), is_failure=lambda r: getattr(r, "status", "") != "completed"))
        self.assertIs(res, result_obj)
        self.assertIsNone(progress._state.last_error_message)
        self.assertEqual(len(captured_scroll_state), 1)
        scroll_state = captured_scroll_state[0]
        self.assertTrue(len(scroll_state.views) >= 1)
        for view in scroll_state.views:
            self.assertEqual(view.state, ViewState.IDLE)

    def test_provisional_buffer_migration_prepends_rows_preserving_chronology(self) -> None:
        """Verify provisional rows are prepended before authoritative rows on migration."""
        state = AdvisoryProgressState()
        state.begin()
        state.agent_buffers[("lead", "")] = ["prov row 1", "prov row 2"]
        state.agent_buffers[("lead", "op_1")] = ["auth row 1", "auth row 2"]

        event = ContextActionProgressEvent(
            kind=ContextActionProgressKind.READ_COMPLETED,
            agent_id="lead",
            operation_id="op_1",
            action_index=1,
        )
        state.observe(event)

        merged = state.agent_buffers.get(("lead", "op_1"))
        self.assertEqual(
            merged,
            ["prov row 1", "prov row 2", "auth row 1", "auth row 2"],
        )

    def test_benign_status_prose_is_not_marked_as_error(self) -> None:
        """Verify benign prose like 'Checking capacity' or 'No errors found' does not set last_error_message."""
        state = AdvisoryProgressState()
        state.begin((AdvisoryProgressRequest("agent_1", "op_1", "Task 1"),))

        # Benign status with 'capacity'
        state.observe_stream(
            ProviderStreamEvent(
                ProviderStreamEventKind.STATUS,
                "agent_1",
                "Checking capacity for selected models",
                operation_id="op_1",
            )
        )
        self.assertIsNone(state.last_error_message)
        self.assertNotIn(("agent_1", "op_1"), state.agent_errors)

        # Benign status with 'error'
        state.observe_stream(
            ProviderStreamEvent(
                ProviderStreamEventKind.STATUS,
                "agent_1",
                "No errors found in generated output",
                operation_id="op_1",
            )
        )
        self.assertIsNone(state.last_error_message)
        self.assertNotIn(("agent_1", "op_1"), state.agent_errors)

        # Explicit error prefix
        state.observe_stream(
            ProviderStreamEvent(
                ProviderStreamEventKind.STATUS,
                "agent_1",
                "Error: Backend service overloaded",
                operation_id="op_1",
            )
        )
        self.assertEqual(state.last_error_message, "Error: Backend service overloaded")
        self.assertEqual(state.agent_errors.get(("agent_1", "op_1")), "Error: Backend service overloaded")

    def test_advisory_progress_frame_renders_full_request_without_truncation(self) -> None:
        """Verify agent card renders full request without 80-char truncation."""
        state = AdvisoryProgressState()
        long_req = "Analyze chapter 1 for stylistic consistency, character voice adherence, and pacing without altering the overall plot outline."
        self.assertGreater(len(long_req), 80)
        state.begin((AdvisoryProgressRequest("editor", "op_edit", long_req),))

        frame = state.render(0)
        self.assertIn(f"  Request: {long_req}", frame)

    def test_successful_run_clears_live_errors_preventing_snapshot_resurrection(self) -> None:
        """Verify success clears live error fields so finish() does not recreate a stale snapshot."""
        from story_writing_agents.interactive_terminal._core import build_stream_terminal

        terminal = build_stream_terminal(lambda _p: "", io.StringIO(), io.StringIO())
        progress = InteractiveAdvisoryProgress(terminal)

        # Simulate previous failed turn that recorded an error
        progress._state.record_error("Previous turn auth failure")
        self.assertEqual(progress._state.last_error_message, "Previous turn auth failure")
        self.assertIsNotNone(progress._state.failure_snapshot)

        async def successful_retry():
            return {"status": "ok"}

        async def fake_advisory_progress(op, **kwargs):
            return await op

        progress._run = fake_advisory_progress

        res = asyncio.run(
            progress.run(
                successful_retry(),
                is_failure=lambda r: r.get("status") != "ok",
            )
        )
        self.assertEqual(res, {"status": "ok"})
        # Live error message and snapshot should be cleared
        self.assertIsNone(progress._state.last_error_message)
        self.assertEqual(len(progress._state.agent_errors), 0)
        self.assertIsNone(progress._state.failure_snapshot)

        # Subsequent finish() should NOT resurrect snapshot
        progress.finish()
        self.assertIsNone(progress._state.failure_snapshot)



