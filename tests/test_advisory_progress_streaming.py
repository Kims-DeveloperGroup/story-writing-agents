"""Unit and integration tests for advisory progress live stream rendering."""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from story_writing_agents.agent_context.progress import (
    ProviderStreamEvent,
    ProviderStreamEventKind,
    active_provider_stream_sink,
)
from story_writing_agents.interactive_terminal._advisory_progress import (
    AdvisoryProgressRequest,
    AdvisoryProgressState,
)
from story_writing_agents.interactive_terminal._core import (
    InteractiveAdvisoryProgress,
    InteractiveTerminal,
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
            "Thinking: Considering pacing and conflict",
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
        self.assertIn("Thinking: Structuring the dialogue\\x1b[31m ANSI stripped", rendered)
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


if __name__ == "__main__":
    unittest.main()
