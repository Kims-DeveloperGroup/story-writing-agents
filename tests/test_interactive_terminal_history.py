"""Deterministic contracts for bounded process-local main-input history."""

from __future__ import annotations

import inspect
import pickle
import unittest
from dataclasses import FrozenInstanceError, fields
from types import SimpleNamespace
from unittest import mock

import story_writing_agents
from story_writing_agents import interactive_terminal as terminal_facade
from story_writing_agents.interactive_terminal import _controls, _model
from story_writing_agents.interactive_terminal._controls import (
    append_main_input_history,
    transition_line_input,
    transition_main_command_input,
    transition_main_input,
)
from story_writing_agents.interactive_terminal._driver import RawTerminalDriver
from story_writing_agents.interactive_terminal._model import (
    ControlOutcome,
    ControlOutcomeKind,
    KeyEvent,
    KeyKind,
    LineInputState,
    MainInputControlState,
    MainInputHistoryState,
)


class _NonTerminalStream:
    """Expose a deterministic line-fallback capability without stream I/O."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def isatty(self) -> bool:
        self.calls.append("isatty")
        return False

    def read(self, size: int) -> str:
        self.calls.append("read")
        raise AssertionError(f"unexpected stream read of {size}")

    def write(self, text: str) -> int:
        self.calls.append("write")
        raise AssertionError(f"unexpected stream write of {text!r}")

    def flush(self) -> None:
        self.calls.append("flush")
        raise AssertionError("unexpected stream flush")


class _HostileString(str):
    """Reject ordinary conversion and containment hooks when invoked."""

    def __str__(self) -> str:
        raise AssertionError("overridden __str__ must not be called")

    def __contains__(self, value: object) -> bool:
        raise AssertionError("overridden __contains__ must not be called")


def _fallback_driver(
    fallback: mock.Mock,
    *,
    history_append=append_main_input_history,
) -> tuple[RawTerminalDriver, _NonTerminalStream, _NonTerminalStream]:
    """Build one driver forced through its ordinary line fallback."""

    input_stream = _NonTerminalStream()
    output_stream = _NonTerminalStream()
    driver = RawTerminalDriver(
        fallback,
        input_stream,  # type: ignore[arg-type]
        output_stream,  # type: ignore[arg-type]
        transition_line_input,
        transition_main_command_input,
        history_append,
        termios_provider=lambda: None,
        os_provider=lambda: SimpleNamespace(name="nt", environ={}),
    )
    return driver, input_stream, output_stream


class MainInputHistoryContractTests(unittest.TestCase):
    """Lock immutable values and pure bounded navigation behavior."""

    def test_internal_contracts_are_exact_frozen_picklable_and_unlisted(
        self,
    ) -> None:
        self.assertEqual(
            tuple(inspect.signature(MainInputHistoryState).parameters),
            ("entries",),
        )
        self.assertEqual(
            tuple(inspect.signature(MainInputControlState).parameters),
            ("line_state", "history_index", "saved_draft", "overlay_open"),
        )
        self.assertEqual(
            tuple(inspect.signature(append_main_input_history).parameters),
            ("history", "text"),
        )
        self.assertEqual(
            tuple(inspect.signature(transition_main_input).parameters),
            ("state", "event", "history"),
        )
        self.assertEqual(
            tuple(inspect.signature(RawTerminalDriver).parameters),
            (
                "fallback_read_line",
                "input_stream",
                "output_stream",
                "line_transition",
                "main_transition",
                "history_append",
                "termios_provider",
                "os_provider",
            ),
        )
        self.assertEqual(
            tuple(inspect.signature(RawTerminalDriver.read_main_line).parameters),
            ("self", "prompt", "suggestion_provider"),
        )
        self.assertEqual(
            tuple(
                inspect.signature(
                    RawTerminalDriver.read_main_command_line
                ).parameters
            ),
            (
                "self",
                "prompt",
                "completion_provider",
                "palette_projector",
                "palette_renderer",
                "initial_alerts",
            ),
        )
        self.assertEqual(
            tuple(inspect.signature(RawTerminalDriver.read_live_line).parameters),
            ("self", "prompt", "suggestion_provider"),
        )
        self.assertEqual(
            tuple(
                inspect.signature(
                    RawTerminalDriver.write_accepted_input
                ).parameters
            ),
            ("self", "text"),
        )
        self.assertEqual(
            RawTerminalDriver.__slots__,
            (
                "_fallback_read_line",
                "_fallback_capabilities",
                "_history_append",
                "_history_state", "_output_history", "_timeline_enabled", "_timeline_rows", "_timeline_cursor_row",
                "_timeline_origin", "_timeline_geometry", "_timeline_suspended", "_timeline_inline_depth", "_timeline_operation",
                "_inspector_anchored", "_inspector_top", "_inspector_width",
                "_input_stream",
                "_input_latch",
                "_input_session_active",
                "_input_session_configuration",
                "_line_transition",
                "_live_capabilities",
                "_live_configuration",
                "_main_transition",
                "_native_input_handoff_active",
                "_os_provider",
                "_output_stream",
                "_progress_active",
                "_progress_frame",
                "_progress_renderer",
                "_progress_rows",
                "_termios_provider",
                "_typeahead_buffer",
            ),
        )
        self.assertEqual(
            tuple(field.name for field in fields(MainInputHistoryState)),
            ("entries",),
        )
        self.assertEqual(
            tuple(field.name for field in fields(MainInputControlState)),
            ("line_state", "history_index", "saved_draft", "overlay_open"),
        )

        history = MainInputHistoryState(("/status",))
        control = MainInputControlState(LineInputState("draft", 2))
        for value in (history, control):
            with self.subTest(value=type(value).__name__):
                self.assertFalse(hasattr(value, "__dict__"))
                restored = pickle.loads(pickle.dumps(value))
                self.assertIs(type(restored), type(value))
                self.assertEqual(restored, value)
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, fields(value)[0].name, None)

        self.assertIs(terminal_facade.MainInputHistoryState, MainInputHistoryState)
        self.assertIs(terminal_facade.MainInputControlState, MainInputControlState)
        self.assertIs(
            terminal_facade.append_main_input_history,
            append_main_input_history,
        )
        self.assertIs(terminal_facade.transition_main_input, transition_main_input)
        self.assertIs(_model.MainInputHistoryState, MainInputHistoryState)
        self.assertIs(_controls.transition_main_input, transition_main_input)
        self.assertEqual(
            MainInputHistoryState.__module__,
            "story_writing_agents.interactive_terminal._model",
        )
        self.assertEqual(
            transition_main_input.__module__,
            "story_writing_agents.interactive_terminal._controls",
        )
        internal_names = {
            "MainInputHistoryState",
            "MainInputControlState",
            "append_main_input_history",
            "transition_main_input",
        }
        self.assertTrue(internal_names.isdisjoint(terminal_facade.__all__))
        self.assertTrue(internal_names.isdisjoint(story_writing_agents.__all__))
        self.assertTrue(
            all(not hasattr(story_writing_agents, name) for name in internal_names)
        )

    def test_models_reject_invalid_history_and_navigation_relations(self) -> None:
        invalid_history = (
            (lambda: MainInputHistoryState([]), TypeError),
            (lambda: MainInputHistoryState(("valid", 1)), TypeError),
            (lambda: MainInputHistoryState((_HostileString("valid"),)), TypeError),
            (lambda: MainInputHistoryState(("",)), ValueError),
            (lambda: MainInputHistoryState((" \t",)), ValueError),
            (
                lambda: MainInputHistoryState(
                    tuple(f"entry-{index}" for index in range(101))
                ),
                ValueError,
            ),
        )
        for call, error_type in invalid_history:
            with self.subTest(call=call):
                with self.assertRaises(error_type):
                    call()

        draft = LineInputState("draft", 2)
        invalid_control = (
            (lambda: MainInputControlState("draft"), TypeError),
            (lambda: MainInputControlState(history_index=True, saved_draft=draft), TypeError),
            (lambda: MainInputControlState(history_index=-1, saved_draft=draft), ValueError),
            (lambda: MainInputControlState(history_index=0), ValueError),
            (lambda: MainInputControlState(saved_draft=draft), ValueError),
            (lambda: MainInputControlState(overlay_open=1), TypeError),
        )
        for call, error_type in invalid_control:
            with self.subTest(call=call):
                with self.assertRaises(error_type):
                    call()

        active = MainInputControlState(draft, 1, LineInputState())
        with self.assertRaisesRegex(
            ValueError,
            "^history_index must identify a stored history entry$",
        ):
            transition_main_input(
                active,
                KeyEvent(KeyKind.UP),
                MainInputHistoryState(("only",)),
            )
        for arguments in (
            (object(), KeyEvent(KeyKind.UP), MainInputHistoryState()),
            (MainInputControlState(), object(), MainInputHistoryState()),
            (MainInputControlState(), KeyEvent(KeyKind.UP), object()),
        ):
            with self.assertRaises(TypeError):
                transition_main_input(*arguments)  # type: ignore[arg-type]

    def test_append_preserves_exact_nonblank_values_duplicates_and_cap(self) -> None:
        empty = MainInputHistoryState()
        for text in ("", " ", "\t", " \t "):
            with self.subTest(text=repr(text)):
                self.assertIs(append_main_input_history(empty, text), empty)

        values = (
            "  /unknown exact  ",
            "한글 advisory 🌊",
            "/quit",
            "duplicate",
            "duplicate",
        )
        history = empty
        for value in values:
            history = append_main_input_history(history, value)
        self.assertEqual(history.entries, values)
        with self.assertRaises(TypeError):
            append_main_input_history(object(), "value")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            append_main_input_history(history, _HostileString("value"))

        capped = MainInputHistoryState()
        for index in range(1, 102):
            capped = append_main_input_history(capped, f"entry-{index:03d}")
        self.assertEqual(len(capped.entries), 100)
        self.assertEqual(capped.entries[0], "entry-002")
        self.assertEqual(capped.entries[-1], "entry-101")
        self.assertNotIn("entry-001", capped.entries)

    def test_up_down_bounds_restore_exact_saved_draft_and_cursor(self) -> None:
        history = MainInputHistoryState(("one", "  two  ", "three"))
        draft = LineInputState("draft-tail", 6)
        state = MainInputControlState(draft)

        state, outcome = transition_main_input(
            state,
            KeyEvent(KeyKind.UP),
            history,
        )
        self.assertIsNone(outcome)
        self.assertEqual(state.line_state, LineInputState("three", 5))
        self.assertEqual(state.history_index, 2)
        self.assertIs(state.saved_draft, draft)
        for expected_index, expected_text in ((1, "  two  "), (0, "one")):
            state, outcome = transition_main_input(
                state,
                KeyEvent(KeyKind.UP),
                history,
            )
            self.assertIsNone(outcome)
            self.assertEqual(state.history_index, expected_index)
            self.assertEqual(
                state.line_state,
                LineInputState(expected_text, len(expected_text)),
            )
            self.assertIs(state.saved_draft, draft)

        oldest = state
        state, outcome = transition_main_input(
            state,
            KeyEvent(KeyKind.UP),
            history,
        )
        self.assertIs(state, oldest)
        self.assertIsNone(outcome)
        for expected_index, expected_text in ((1, "  two  "), (2, "three")):
            state, outcome = transition_main_input(
                state,
                KeyEvent(KeyKind.DOWN),
                history,
            )
            self.assertIsNone(outcome)
            self.assertEqual(state.history_index, expected_index)
            self.assertEqual(
                state.line_state,
                LineInputState(expected_text, len(expected_text)),
            )

        state, outcome = transition_main_input(
            state,
            KeyEvent(KeyKind.DOWN),
            history,
        )
        self.assertIsNone(outcome)
        self.assertIs(state.line_state, draft)
        self.assertIsNone(state.history_index)
        self.assertIsNone(state.saved_draft)
        inactive = state
        state, outcome = transition_main_input(
            state,
            KeyEvent(KeyKind.DOWN),
            history,
        )
        self.assertIs(state, inactive)
        self.assertIsNone(outcome)

    def test_recalled_edits_are_copies_and_overlay_reserved_keys_are_isolated(
        self,
    ) -> None:
        history = MainInputHistoryState(("old", "newest"))
        draft = LineInputState("draft", 2)
        state, _ = transition_main_input(
            MainInputControlState(draft),
            KeyEvent(KeyKind.UP),
            history,
        )
        saved_draft = state.saved_draft
        for event in (
            KeyEvent(KeyKind.LEFT),
            KeyEvent(KeyKind.BACKSPACE),
            KeyEvent(KeyKind.TEXT, text="X"),
        ):
            state, outcome = transition_main_input(state, event, history)
            self.assertIsNone(outcome)
        self.assertEqual(state.line_state, LineInputState("neweXt", 5))
        self.assertEqual(state.history_index, 1)
        self.assertIs(state.saved_draft, saved_draft)
        self.assertEqual(history.entries, ("old", "newest"))

        committed, outcome = transition_main_input(
            state,
            KeyEvent(KeyKind.ENTER),
            history,
        )
        self.assertIs(committed, state)
        self.assertEqual(
            outcome,
            ControlOutcome(ControlOutcomeKind.COMMIT, text="neweXt"),
        )
        updated = append_main_input_history(history, outcome.text)  # type: ignore[arg-type]
        self.assertEqual(updated.entries, ("old", "newest", "neweXt"))

        overlay = MainInputControlState(
            LineInputState("visible", 3),
            1,
            draft,
            overlay_open=True,
        )
        for kind in (KeyKind.UP, KeyKind.DOWN):
            with self.subTest(kind=kind):
                unchanged, outcome = transition_main_input(
                    overlay,
                    KeyEvent(kind),
                    history,
                )
                self.assertIs(unchanged, overlay)
                self.assertIsNone(outcome)

        preview_closed = MainInputControlState(LineInputState("/", 1))
        self.assertFalse(preview_closed.overlay_open)
        recalled, _ = transition_main_input(
            preview_closed,
            KeyEvent(KeyKind.UP),
            history,
        )
        self.assertEqual(recalled.line_state, LineInputState("newest", 6))
        for kind in (KeyKind.TAB, KeyKind.SHIFT_TAB, KeyKind.ESCAPE):
            with self.subTest(kind=kind):
                unchanged, outcome = transition_main_input(
                    preview_closed,
                    KeyEvent(kind),
                    history,
                )
                self.assertIs(unchanged, preview_closed)
                self.assertIsNone(outcome)


class DriverHistoryBoundaryTests(unittest.TestCase):
    """Verify one fallback-capable driver owns exactly one isolated store."""

    def test_main_fallback_records_once_while_live_and_ordinary_stay_isolated(
        self,
    ) -> None:
        fallback = mock.Mock(
            side_effect=("  /invalid  ", "composition", "ordinary")
        )
        driver, input_stream, output_stream = _fallback_driver(fallback)

        self.assertEqual(driver.read_main_line("main> ", lambda _text: ()), "  /invalid  ")
        self.assertEqual(driver.read_live_line("compose> ", lambda _text: ()), "composition")
        self.assertEqual(driver.read_line("subject> "), "ordinary")
        self.assertEqual(
            driver._history_state,
            MainInputHistoryState(("  /invalid  ",)),
        )
        self.assertEqual(
            fallback.call_args_list,
            [mock.call("main> "), mock.call("compose> "), mock.call("subject> ")],
        )
        self.assertNotIn("read", input_stream.calls)
        self.assertEqual(output_stream.calls.count("write"), 0)

        other, _other_input, _other_output = _fallback_driver(
            mock.Mock(return_value="other")
        )
        self.assertEqual(other._history_state, MainInputHistoryState())
        self.assertIsNot(other._history_state, driver._history_state)

    def test_fallback_excludes_blank_framing_and_type_failures(self) -> None:
        marker = object()
        values = ("", " \t", "bad\nline", "bad\rline", marker)
        fallback = mock.Mock(side_effect=values)
        driver, _input, _output = _fallback_driver(fallback)
        for value in values:
            with self.subTest(value=repr(value)):
                returned = driver.read_main_line("main> ", lambda _text: ())
                self.assertIs(returned, value)
                self.assertEqual(driver._history_state, MainInputHistoryState())

        for failure in (KeyboardInterrupt(), EOFError(), OSError("read failed")):
            with self.subTest(failure=type(failure).__name__):
                failed, _input, _output = _fallback_driver(
                    mock.Mock(side_effect=failure)
                )
                with self.assertRaises(type(failure)):
                    failed.read_main_line("main> ", lambda _text: ())
                self.assertEqual(failed._history_state, MainInputHistoryState())

    def test_hostile_string_subclass_is_returned_and_stored_as_base_text_once(
        self,
    ) -> None:
        value = _HostileString("  /unknown 🌊  ")
        driver, _input, _output = _fallback_driver(mock.Mock(return_value=value))

        returned = driver.read_main_line("main> ", lambda _text: ())

        self.assertIs(returned, value)
        self.assertEqual(
            driver._history_state,
            MainInputHistoryState(("  /unknown 🌊  ",)),
        )
        self.assertIs(type(driver._history_state.entries[0]), str)
        recalled, outcome = transition_main_input(
            MainInputControlState(),
            KeyEvent(KeyKind.UP),
            driver._history_state,
        )
        self.assertIsNone(outcome)
        self.assertEqual(
            recalled.line_state,
            LineInputState("  /unknown 🌊  ", len("  /unknown 🌊  ")),
        )
        self.assertEqual(driver._history_state.entries.count("  /unknown 🌊  "), 1)

    def test_history_append_collaborator_failures_do_not_replace_the_store(
        self,
    ) -> None:
        for append, error_type in (
            (mock.Mock(return_value=object()), TypeError),
            (mock.Mock(side_effect=RuntimeError("append failed")), RuntimeError),
        ):
            with self.subTest(error_type=error_type.__name__):
                driver, _input, _output = _fallback_driver(
                    mock.Mock(return_value="candidate"),
                    history_append=append,
                )
                before = driver._history_state
                with self.assertRaises(error_type):
                    driver.read_main_line("main> ", lambda _text: ())
                self.assertIs(driver._history_state, before)


if __name__ == "__main__":
    unittest.main()
