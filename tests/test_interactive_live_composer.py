"""Focused behavioral tests for the interrupt-capable live composer."""

from __future__ import annotations

import asyncio
import fcntl
import os
import pty
import struct
import unittest
from dataclasses import FrozenInstanceError, fields
from types import SimpleNamespace
from unittest import mock

from story_writing_agents import interactive_terminal as terminal_facade
from story_writing_agents.interactive_terminal import _core
from story_writing_agents.interactive_terminal._controls import (
    append_main_input_history,
    transition_line_input,
    transition_main_input,
)
from story_writing_agents.interactive_terminal._driver import RawTerminalDriver
from story_writing_agents.interactive_terminal._driver import _PROGRESS_FRAME_COUNT
from story_writing_agents.interactive_terminal._live_composer import (
    LiveComposerDeliveryKind,
    LiveComposerInput,
    LiveComposerInputKind,
    LiveComposerJoinStatus,
    LiveComposerResult,
    LiveOperationBoundary,
    deliver_live_composer_input,
)
from story_writing_agents.interactive_terminal._model import (
    LineInputState,
    RenderedInputFrame,
)
from tests.test_interactive_terminal_composer import _driver


async def _pending() -> object:
    await asyncio.Event().wait()
    raise AssertionError("unreachable")


async def _joined_cancel(operation: object) -> LiveComposerJoinStatus:
    operation.cancel()  # type: ignore[attr-defined]
    try:
        await operation  # type: ignore[misc]
    except asyncio.CancelledError:
        pass
    return LiveComposerJoinStatus.JOINED


def _frame(
    state: LineInputState, frame: int, capabilities: object
) -> RenderedInputFrame:
    assert 0 <= frame < _PROGRESS_FRAME_COUNT
    width = capabilities.width  # type: ignore[attr-defined]
    text = f"{state.buffer} "
    return RenderedInputFrame(text, text, width, 1)


class LiveComposerContractTests(unittest.IsolatedAsyncioTestCase):
    """Lock immutable values and durable-boundary delivery behavior."""

    def test_contracts_are_frozen_slotted_and_fail_closed(self) -> None:
        state = LineInputState("draft", 2)
        accepted = LiveComposerInput(LiveComposerInputKind.SUBMIT, state)
        result = LiveComposerResult(
            LiveComposerDeliveryKind.REPLAN,
            state,
            text="draft",
        )

        for value in (accepted, result):
            self.assertFalse(hasattr(value, "__dict__"))
            with self.assertRaises(FrozenInstanceError):
                setattr(value, fields(value)[0].name, None)
        self.assertEqual(accepted.text, "draft")
        with self.assertRaises(TypeError):
            LiveComposerInput("submit", state)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            LiveComposerInput(LiveComposerInputKind.SUBMIT, object())  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            LiveComposerResult(LiveComposerDeliveryKind.REPLAN, state, text="changed")
        with self.assertRaises(ValueError):
            LiveComposerResult(
                LiveComposerDeliveryKind.NEXT_TURN,
                state,
                text="draft",
                operation_result=object(),
            )

    async def test_precommit_calls_injected_join_with_exact_task_before_replan(
        self,
    ) -> None:
        task = asyncio.create_task(_pending())
        observed: list[object] = []

        async def cancel_and_join(operation: object) -> LiveComposerJoinStatus:
            observed.append(operation)
            self.assertFalse(operation.done())  # type: ignore[attr-defined]
            return await _joined_cancel(operation)

        result = await deliver_live_composer_input(
            LiveComposerInput(
                LiveComposerInputKind.SUBMIT,
                LineInputState("/literal", 3),
            ),
            task,
            lambda: LiveOperationBoundary.PRECOMMIT,
            cancel_and_join,
        )

        self.assertEqual(observed, [task])
        self.assertTrue(task.done())
        self.assertEqual(result.kind, LiveComposerDeliveryKind.REPLAN)
        self.assertEqual(
            (result.text, result.state), ("/literal", LineInputState("/literal", 3))
        )

    async def test_uncertain_exception_and_invalid_join_require_recovery(self) -> None:
        async def uncertain(_operation: object) -> LiveComposerJoinStatus:
            return LiveComposerJoinStatus.UNCERTAIN

        async def invalid(_operation: object) -> object:
            return "joined"

        async def failure(_operation: object) -> LiveComposerJoinStatus:
            raise RuntimeError("join failed")

        for callback in (uncertain, invalid, failure):
            task = asyncio.create_task(asyncio.sleep(0))
            with self.subTest(callback=callback.__name__):
                result = await deliver_live_composer_input(
                    LiveComposerInput(
                        LiveComposerInputKind.SUBMIT,
                        LineInputState("do not replay", 4),
                    ),
                    task,
                    lambda: LiveOperationBoundary.PRECOMMIT,
                    callback,  # type: ignore[arg-type]
                )
                self.assertEqual(
                    result.kind,
                    LiveComposerDeliveryKind.RECOVERY_REQUIRED,
                )
                self.assertIsNone(result.text)
            await task

    async def test_postcommit_never_cancels_and_classifies_distinct_inputs(
        self,
    ) -> None:
        async def forbidden(_operation: object) -> LiveComposerJoinStatus:
            raise AssertionError("postcommit must not cancel")

        task = asyncio.create_task(asyncio.sleep(0))
        expected = {
            LiveComposerInputKind.SUBMIT: LiveComposerDeliveryKind.NEXT_TURN,
            LiveComposerInputKind.INTERRUPT: LiveComposerDeliveryKind.INTERRUPTED,
            LiveComposerInputKind.EOF: LiveComposerDeliveryKind.EOF,
        }
        for kind, delivery in expected.items():
            state = LineInputState("/still literal", 5)
            result = await deliver_live_composer_input(
                LiveComposerInput(kind, state),
                task,
                lambda: LiveOperationBoundary.POSTCOMMIT,
                forbidden,
            )
            self.assertEqual(result.kind, delivery)
            self.assertEqual(result.state, state)
            self.assertEqual(
                result.text,
                state.buffer if kind is LiveComposerInputKind.SUBMIT else None,
            )
        await task


class RawLiveComposerTests(unittest.IsolatedAsyncioTestCase):
    """Exercise capable, incapable, and failure terminal lifecycles."""

    async def test_literal_slash_input_edits_during_operation_and_preserves_cursor(
        self,
    ) -> None:
        driver, input_stream, _output, fallback, termios, _size = _driver(
            "X\x1b[D/status\n"
        )
        rendered: list[LineInputState] = []

        def renderer(
            state: LineInputState, frame: int, capabilities: object
        ) -> RenderedInputFrame:
            rendered.append(state)
            return _frame(state, frame, capabilities)

        with mock.patch(
            "story_writing_agents.interactive_terminal._driver._select.select",
            side_effect=lambda *_args: ((7,), (), ())
            if input_stream.characters
            else ((), (), ()),
        ):
            result = await driver.run_live_composer(
                _pending(),
                "story> ",
                renderer,
                lambda: LiveOperationBoundary.PRECOMMIT,
                _joined_cancel,
                initial_state=LineInputState("ab", 1),
            )

        self.assertEqual(result.kind, LiveComposerDeliveryKind.REPLAN)
        self.assertEqual(result.state, LineInputState("a/statusXb", 8))
        self.assertEqual(result.text, "a/statusXb")
        self.assertIn(LineInputState("aXb", 2), rendered)
        self.assertIn(LineInputState("a/statusXb", 8), rendered)
        self.assertFalse(fallback.called)
        self.assertEqual([call[1] for call in termios.set_calls], [10, 11])

    async def test_operation_first_completion_preserves_initial_draft_and_result(
        self,
    ) -> None:
        driver, _input, _output, fallback, _termios, _size = _driver("")
        initial = LineInputState("unfinished draft", 4)

        with mock.patch(
            "story_writing_agents.interactive_terminal._driver._select.select",
            return_value=((), (), ()),
        ):
            result = await driver.run_live_composer(
                asyncio.sleep(0, result={"done": True}),
                "story> ",
                _frame,
                lambda: LiveOperationBoundary.PRECOMMIT,
                _joined_cancel,
                initial_state=initial,
            )

        self.assertEqual(result.kind, LiveComposerDeliveryKind.OPERATION_COMPLETED)
        self.assertIs(result.state, initial)
        self.assertEqual(result.operation_result, {"done": True})
        self.assertFalse(fallback.called)

    async def test_postcommit_waits_for_finalization_and_never_calls_cancel(
        self,
    ) -> None:
        driver, input_stream, _output, _fallback, _termios, _size = _driver("next\n")
        finalized = asyncio.Event()
        cancel = mock.AsyncMock(side_effect=AssertionError("must not cancel"))

        async def operation() -> str:
            await asyncio.sleep(0.01)
            finalized.set()
            return "published"

        with mock.patch(
            "story_writing_agents.interactive_terminal._driver._select.select",
            side_effect=lambda *_args: ((7,), (), ())
            if input_stream.characters
            else ((), (), ()),
        ):
            result = await driver.run_live_composer(
                operation(),
                "story> ",
                _frame,
                lambda: LiveOperationBoundary.POSTCOMMIT,
                cancel,
            )

        self.assertTrue(finalized.is_set())
        cancel.assert_not_awaited()
        self.assertEqual(result.kind, LiveComposerDeliveryKind.NEXT_TURN)
        self.assertEqual((result.text, result.state.cursor), ("next", 4))

    async def test_postcommit_failure_and_cancellation_map_to_recovery(self) -> None:
        async def failed() -> object:
            await asyncio.sleep(0.001)
            raise RuntimeError("finalization failed")

        async def cancelled() -> object:
            await asyncio.sleep(0.001)
            raise asyncio.CancelledError

        for operation in (failed, cancelled):
            driver, input_stream, _output, _fallback, _termios, _size = _driver("x\n")
            cancel = mock.AsyncMock(side_effect=AssertionError("must not cancel"))
            with (
                self.subTest(operation=operation.__name__),
                mock.patch(
                    "story_writing_agents.interactive_terminal._driver._select.select",
                    side_effect=lambda *_args: ((7,), (), ())
                    if input_stream.characters
                    else ((), (), ()),
                ),
            ):
                result = await driver.run_live_composer(
                    operation(),
                    "story> ",
                    _frame,
                    lambda: LiveOperationBoundary.POSTCOMMIT,
                    cancel,
                )
            self.assertEqual(result.kind, LiveComposerDeliveryKind.RECOVERY_REQUIRED)
            cancel.assert_not_awaited()

    async def test_interrupt_eof_and_submit_remain_distinct(self) -> None:
        cases = (
            ("\x03", LiveComposerDeliveryKind.INTERRUPTED),
            ("\x04", LiveComposerDeliveryKind.EOF),
            ("\n", LiveComposerDeliveryKind.REPLAN),
        )
        for characters, expected in cases:
            driver, input_stream, _output, _fallback, _termios, _size = _driver(
                characters
            )
            with (
                self.subTest(characters=characters.encode()),
                mock.patch(
                    "story_writing_agents.interactive_terminal._driver._select.select",
                    side_effect=lambda *_args: ((7,), (), ())
                    if input_stream.characters
                    else ((), (), ()),
                ),
            ):
                result = await driver.run_live_composer(
                    _pending(),
                    "story> ",
                    _frame,
                    lambda: LiveOperationBoundary.PRECOMMIT,
                    _joined_cancel,
                )
            self.assertEqual(result.kind, expected)

    async def test_incapable_fallback_never_reads_and_preserves_state(self) -> None:
        fallback = mock.Mock(side_effect=AssertionError("input must not be read"))
        driver, input_stream, _output, _fallback, _termios, _size = _driver(
            "ignored",
            fallback=fallback,
            input_terminal=False,
        )
        initial = LineInputState("kept", 2)
        result = await driver.run_live_composer(
            asyncio.sleep(0, result=17),
            "story> ",
            _frame,
            lambda: LiveOperationBoundary.PRECOMMIT,
            _joined_cancel,
            initial_state=initial,
        )
        self.assertEqual(
            result,
            LiveComposerResult(
                LiveComposerDeliveryKind.OPERATION_COMPLETED,
                initial,
                operation_result=17,
            ),
        )
        self.assertEqual(input_stream.read_calls, 0)
        fallback.assert_not_called()

    async def test_terminal_write_failure_restores_and_joins_operation(self) -> None:
        driver, _input, output, _fallback, termios, _size = _driver("")
        output.write_failure = OSError("write failed")
        observed: list[object] = []

        async def cancel(operation: object) -> LiveComposerJoinStatus:
            observed.append(operation)
            return await _joined_cancel(operation)

        with self.assertRaisesRegex(OSError, "write failed"):
            await driver.run_live_composer(
                _pending(),
                "story> ",
                _frame,
                lambda: LiveOperationBoundary.PRECOMMIT,
                cancel,
            )
        self.assertEqual(len(observed), 1)
        self.assertTrue(observed[0].done())  # type: ignore[attr-defined]
        self.assertEqual([call[1] for call in termios.set_calls], [10, 11])

    async def test_terminal_setup_read_and_restore_faults_join_without_task_leak(
        self,
    ) -> None:
        cases = ("setup", "read", "restore")
        for case in cases:
            driver, input_stream, _output, _fallback, termios, _size = _driver("x")
            if case == "setup":
                termios.activation_failure = OSError("setup failed")
            elif case == "read":
                input_stream.read1 = mock.Mock(side_effect=OSError("read failed"))  # type: ignore[method-assign]
            else:
                termios.restoration_failure = OSError("restore failed")
            observed: list[object] = []

            async def cancel(operation: object) -> LiveComposerJoinStatus:
                observed.append(operation)
                return await _joined_cancel(operation)

            expected_error = RuntimeError if case == "restore" else OSError
            expected_message = (
                "interactive terminal restoration failed"
                if case == "restore"
                else f"{case} failed"
            )
            with (
                self.subTest(case=case),
                mock.patch(
                    "story_writing_agents.interactive_terminal._driver._select.select",
                    return_value=((7,), (), ()),
                ),
                self.assertRaisesRegex(expected_error, expected_message),
            ):
                await driver.run_live_composer(
                    _pending(),
                    "story> ",
                    _frame,
                    lambda: LiveOperationBoundary.PRECOMMIT,
                    cancel,
                )
            self.assertEqual(len(observed), 1)
            self.assertTrue(observed[0].done())  # type: ignore[attr-defined]
            self.assertEqual(termios.set_calls[-1][1], 11)

    @unittest.skipUnless(os.name == "posix", "requires a POSIX pseudo-terminal")
    async def test_posix_pty_selects_live_capability_without_mutating_terminal(
        self,
    ) -> None:
        master_descriptor, slave_descriptor = pty.openpty()
        input_stream = os.fdopen(os.dup(slave_descriptor), "r", encoding="utf-8")
        output_stream = os.fdopen(os.dup(slave_descriptor), "w", encoding="utf-8")
        try:
            fcntl.ioctl(
                slave_descriptor,
                __import__("termios").TIOCSWINSZ,
                struct.pack("HHHH", 24, 80, 0, 0),
            )
            driver = RawTerminalDriver(
                lambda _prompt: "fallback",
                input_stream,
                output_stream,
                transition_line_input,
                transition_main_input,
                append_main_input_history,
                os_provider=lambda: SimpleNamespace(
                    name="posix",
                    environ={"TERM": "xterm-256color"},
                    get_terminal_size=lambda _descriptor: os.terminal_size((80, 24)),
                    fpathconf=os.fpathconf,
                ),
            )
            original = __import__("termios").tcgetattr(slave_descriptor)
            capabilities = driver.capabilities()
            prepared, configuration = driver._prepare_input(capabilities)

            self.assertTrue(prepared.raw_mode)
            self.assertIsNotNone(configuration)
            self.assertEqual(configuration.width, 80)  # type: ignore[union-attr]
            self.assertEqual(
                __import__("termios").tcgetattr(slave_descriptor), original
            )
        finally:
            input_stream.close()
            output_stream.close()
            os.close(master_descriptor)
            os.close(slave_descriptor)

    async def test_active_progress_and_input_latch_reject_overlap(self) -> None:
        driver, _input, _output, _fallback, _termios, _size = _driver("")
        driver._progress_active = True
        operation = mock.Mock()
        operation.__await__ = mock.Mock(return_value=iter(()))
        with self.assertRaisesRegex(RuntimeError, "cannot overlap terminal progress"):
            await driver.run_live_composer(
                operation,
                "story> ",
                _frame,
                lambda: LiveOperationBoundary.PRECOMMIT,
                _joined_cancel,
            )

        driver._progress_active = False
        with driver._input_latch.input_operation():
            with self.assertRaises(RuntimeError):
                await driver.run_live_composer(
                    operation,
                    "story> ",
                    _frame,
                    lambda: LiveOperationBoundary.PRECOMMIT,
                    _joined_cancel,
                )


class LiveComposerFacadeTests(unittest.TestCase):
    def test_package_exports_remain_intentionally_closed(self) -> None:
        internal_names = {
            "InteractiveLiveComposer",
            "LiveComposerDeliveryKind",
            "LiveComposerInput",
            "LiveComposerInputKind",
            "LiveComposerJoinStatus",
            "LiveComposerResult",
            "LiveOperationBoundary",
        }
        self.assertTrue(internal_names.isdisjoint(terminal_facade.__all__))
        self.assertTrue(internal_names.isdisjoint(_core.__all__))
        self.assertTrue(
            internal_names.isdisjoint(__import__("story_writing_agents").__all__)
        )


if __name__ == "__main__":
    unittest.main()
