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


if __name__ == "__main__":
    unittest.main()

