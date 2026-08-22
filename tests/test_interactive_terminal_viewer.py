"""Deterministic contracts for the package-internal ranged text viewer."""

from __future__ import annotations

import os
import pickle
import unittest
from dataclasses import FrozenInstanceError, fields
from types import SimpleNamespace
from unittest import mock

import story_writing_agents
from story_writing_agents import interactive_terminal as terminal_facade
from story_writing_agents.interactive_terminal import (
    InteractiveTextViewer,
    build_stream_terminal,
)
from story_writing_agents.interactive_terminal._controls import (
    append_main_input_history,
    transition_line_input,
    transition_main_command_input,
)
from story_writing_agents.interactive_terminal._driver import RawTerminalDriver
from story_writing_agents.interactive_terminal._model import (
    CapabilityProfile,
    KeyEvent,
    KeyKind,
    TerminalCapabilities,
    display_character_width,
)
from story_writing_agents.interactive_terminal._viewer_controls import (
    initialize_text_viewer,
    project_text_viewer_viewport,
    transition_text_viewer,
    transition_text_viewer_fallback,
)
from story_writing_agents.interactive_terminal._viewer_model import (
    TextViewerDocument,
    TextViewerExitKind,
    TextViewerNotice,
    TextViewerResult,
    TextViewerState,
    TextViewerViewport,
    dispose_text_viewer,
)
from story_writing_agents.interactive_terminal._viewer_render import (
    render_text_viewer,
    render_text_viewer_fallback,
)


def _capabilities(*, width: int = 80, ansi: bool = False) -> TerminalCapabilities:
    profile = CapabilityProfile.ANSI if ansi else CapabilityProfile.PLAIN
    return TerminalCapabilities(
        profile,
        width,
        input_tty=True,
        output_tty=True,
        raw_mode=True,
        cursor_controls=True,
        decorative_sgr=ansi,
    )


class _ScriptedInput:
    def __init__(self, characters: str, events: list[str]) -> None:
        self.characters = list(characters)
        self.events = events

    def isatty(self) -> bool:
        self.events.append("input.isatty")
        return True

    def fileno(self) -> int:
        self.events.append("input.fileno")
        return 7

    def read(self, size: int) -> str:
        self.events.append("input.read")
        if size != 1:
            raise AssertionError(size)
        return self.characters.pop(0) if self.characters else ""


class _RecordingOutput:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.records: list[str] = []
        self.write_failure: BaseException | None = None
        self.flush_failure: BaseException | None = None

    def isatty(self) -> bool:
        self.events.append("output.isatty")
        return True

    def fileno(self) -> int:
        self.events.append("output.fileno")
        return 8

    def write(self, text: str) -> int:
        self.events.append("output.write")
        self.records.append(text)
        if self.write_failure is not None:
            raise self.write_failure
        return len(text)

    def flush(self) -> None:
        self.events.append("output.flush")
        if self.flush_failure is not None:
            raise self.flush_failure


class _FakeTermios:
    ICANON = 0x02
    ECHO = 0x08
    VEOF = 0
    VINTR = 1
    VMIN = 4
    VTIME = 5
    TCSANOW = 10
    TCSADRAIN = 11

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.original = [0, 0, 0, self.ICANON | self.ECHO, 0, 0, [b"\x04", b"\x03", 0, 0, 9, 9]]
        self.activation_failure: BaseException | None = None
        self.restoration_failure: BaseException | None = None

    def tcgetattr(self, descriptor: int) -> list[object]:
        self.events.append("termios.get")
        value = list(self.original)
        value[6] = list(self.original[6])  # type: ignore[arg-type]
        return value

    def tcsetattr(self, descriptor: int, when: int, attributes: list[object]) -> None:
        label = "activate" if when == self.TCSANOW else "restore"
        self.events.append(f"termios.{label}")
        if when == self.TCSANOW and self.activation_failure is not None:
            raise self.activation_failure
        if when == self.TCSADRAIN and self.restoration_failure is not None:
            raise self.restoration_failure


def _driver(
    characters: str,
    *,
    answers: tuple[object, ...] = ("/cancel",),
    sizes: tuple[tuple[int, int], ...] = ((80, 24),),
) -> tuple[RawTerminalDriver, _ScriptedInput, _RecordingOutput, mock.Mock, _FakeTermios, list[str]]:
    events: list[str] = []
    input_stream = _ScriptedInput(characters, events)
    output_stream = _RecordingOutput(events)
    fallback = mock.Mock(side_effect=answers)
    termios = _FakeTermios(events)
    calls = 0

    def get_terminal_size(descriptor: int) -> os.terminal_size:
        nonlocal calls
        events.append("os.geometry")
        width, height = sizes[min(calls, len(sizes) - 1)]
        calls += 1
        return os.terminal_size((width, height))

    os_double = SimpleNamespace(
        name="posix",
        environ={"TERM": "xterm-256color"},
        get_terminal_size=get_terminal_size,
    )
    driver = RawTerminalDriver(
        fallback,
        input_stream,  # type: ignore[arg-type]
        output_stream,  # type: ignore[arg-type]
        transition_line_input,
        transition_main_command_input,
        append_main_input_history,
        termios_provider=lambda: termios,
        os_provider=lambda: os_double,
    )
    return driver, input_stream, output_stream, fallback, termios, events


def _read(driver: RawTerminalDriver, state: TextViewerState) -> TextViewerResult:
    return driver.read_text_viewer(
        state,
        transition_text_viewer,
        transition_text_viewer_fallback,
        project_text_viewer_viewport,
        render_text_viewer,
        render_text_viewer_fallback,
    )


class ViewerModelContractTests(unittest.TestCase):
    def test_key_vocabulary_and_private_facade_bindings_are_exact(self) -> None:
        self.assertEqual(
            tuple(item.value for item in KeyKind),
            (
                "text", "enter", "backspace", "delete", "left", "right",
                "home", "end", "up", "down", "shift_up", "shift_down",
                "tab", "shift_tab", "escape", "interrupt", "eof",
            ),
        )
        for name in ("InteractiveTextViewer", "TextViewerExitKind", "TextViewerResult"):
            self.assertTrue(hasattr(terminal_facade, name))
            self.assertNotIn(name, terminal_facade.__all__)
            self.assertFalse(hasattr(story_writing_agents, name))

    def test_values_are_exact_frozen_slotted_and_pickleable(self) -> None:
        document = TextViewerDocument("one\ntwo", "draft.md")
        state = TextViewerState(document, 2, 1)
        result = TextViewerResult(TextViewerExitKind.SELECTED, (1, 2))
        self.assertEqual(tuple(item.value for item in TextViewerExitKind), ("selected", "cancelled", "interrupted", "eof"))
        self.assertEqual(tuple(field.name for field in fields(TextViewerResult)), ("kind", "line_range"))
        self.assertEqual(
            tuple(field.name for field in fields(TextViewerDocument)),
            ("text", "display_name", "lines", "utf8_bytes"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(TextViewerState)),
            ("document", "active_line", "anchor_line", "notice"),
        )
        self.assertFalse(hasattr(state, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            state.active_line = 1  # type: ignore[misc]
        self.assertEqual(pickle.loads(pickle.dumps(result)), result)
        self.assertEqual(state.selection, (1, 2))

    def test_lf_only_derivation_and_utf8_boundary_are_exact(self) -> None:
        self.assertEqual(TextViewerDocument("", "x").lines, ())
        self.assertEqual(TextViewerDocument("a\n", "x").lines, ("a",))
        self.assertEqual(TextViewerDocument("a\n\n", "x").lines, ("a", ""))
        self.assertEqual(TextViewerDocument("a\rb\u2028c", "x").lines, ("a\rb\u2028c",))
        maximum = "a" * (8 * 1024 * 1024)
        self.assertEqual(TextViewerDocument(maximum, "x").utf8_bytes, len(maximum))
        with self.assertRaisesRegex(ValueError, "8 MiB"):
            TextViewerDocument(maximum + "a", "x")
        for value in ("\ud800",):
            with self.assertRaisesRegex(ValueError, "valid UTF-8"):
                TextViewerDocument(value, "x")
            with self.assertRaisesRegex(ValueError, "valid UTF-8"):
                TextViewerDocument("x", value)

    def test_results_and_correlated_values_reject_bool_and_invalid_shapes(self) -> None:
        for bad in (True, 1.0, "1", None):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError)):
                    TextViewerResult(TextViewerExitKind.SELECTED, (bad, 2))  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            TextViewerResult(TextViewerExitKind.CANCELLED, (1, 1))
        with self.assertRaises(TypeError):
            TextViewerResult("selected", (1, 1))  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            dispose_text_viewer(object())  # type: ignore[arg-type]

    def test_public_reader_validates_before_terminal_io_and_disposes_each_call(self) -> None:
        read_line = mock.Mock(return_value="1-1")
        input_stream = mock.Mock()
        output_stream = mock.Mock()
        terminal = build_stream_terminal(read_line, input_stream, output_stream)
        viewer = InteractiveTextViewer(terminal)
        with mock.patch(
            "story_writing_agents.interactive_terminal._core.dispose_text_viewer",
            wraps=dispose_text_viewer,
        ) as dispose:
            for name in ("first", "second"):
                result = viewer.read_range("line", display_name=name)
                self.assertEqual(result.line_range, (1, 1))
            self.assertEqual(dispose.call_count, 2)
            self.assertIsNot(dispose.call_args_list[0].args[0], dispose.call_args_list[1].args[0])
        read_line.reset_mock()
        input_stream.reset_mock()
        output_stream.reset_mock()
        invalid_calls = (
            lambda: viewer.read_range("line", display_name="x", initial_line=2),
            lambda: viewer.read_range("line", display_name="x", initial_line=True),
            lambda: viewer.read_range("", display_name="x", initial_line=1),
            lambda: viewer.read_range("\ud800", display_name="x"),
            lambda: viewer.read_range("line", display_name="bad\nname"),
        )
        for call in invalid_calls:
            with self.subTest(call=call), self.assertRaises((TypeError, ValueError)):
                call()
        read_line.assert_not_called()
        input_stream.assert_not_called()
        output_stream.assert_not_called()


class PureViewerControlTests(unittest.TestCase):
    def test_navigation_selection_contract_reverse_contract_clamp_and_clear(self) -> None:
        state = initialize_text_viewer("1\n2\n3\n4", display_name="x", initial_line=3)
        for kind, expected in (
            (KeyKind.SHIFT_UP, (2, 3)),
            (KeyKind.SHIFT_UP, (1, 3)),
            (KeyKind.SHIFT_DOWN, (2, 3)),
            (KeyKind.SHIFT_DOWN, (3, 3)),
            (KeyKind.SHIFT_DOWN, (3, 4)),
            (KeyKind.SHIFT_DOWN, (3, 4)),
        ):
            state, outcome = transition_text_viewer(state, KeyEvent(kind))
            self.assertIsNone(outcome)
            self.assertEqual(state.selection, expected)
        state, _ = transition_text_viewer(state, KeyEvent(KeyKind.UP))
        self.assertIsNone(state.anchor_line)
        self.assertEqual(state.active_line, 3)

    def test_enter_and_all_noncommit_exits_are_distinct(self) -> None:
        state = initialize_text_viewer("one\ntwo", display_name="x", initial_line=2)
        unchanged, selected = transition_text_viewer(state, KeyEvent(KeyKind.ENTER))
        self.assertIs(unchanged, state)
        self.assertEqual(selected, TextViewerResult(TextViewerExitKind.SELECTED, (2, 2)))
        for kind, exit_kind in (
            (KeyKind.ESCAPE, TextViewerExitKind.CANCELLED),
            (KeyKind.INTERRUPT, TextViewerExitKind.INTERRUPTED),
            (KeyKind.EOF, TextViewerExitKind.EOF),
        ):
            _, result = transition_text_viewer(state, KeyEvent(kind))
            self.assertEqual(result, TextViewerResult(exit_kind))
        empty = initialize_text_viewer("", display_name="x")
        next_empty, result = transition_text_viewer(empty, KeyEvent(KeyKind.ENTER))
        self.assertIsNone(result)
        self.assertEqual(next_empty.notice, TextViewerNotice.EMPTY_DOCUMENT)

    def test_unrelated_keys_are_noop_and_viewport_tracks_height_and_selection(self) -> None:
        state = initialize_text_viewer("\n".join(str(i) for i in range(1, 11)), display_name="x", initial_line=8)
        for kind in (KeyKind.TEXT, KeyKind.LEFT, KeyKind.TAB, KeyKind.SHIFT_TAB):
            next_state, result = transition_text_viewer(state, KeyEvent(kind, text="x") if kind is KeyKind.TEXT else KeyEvent(kind))
            self.assertIs(next_state, state)
            self.assertIsNone(result)
        viewport = project_text_viewer_viewport(state, 5)
        self.assertEqual(len(viewport.lines), 3)
        self.assertLessEqual(viewport.start_line, 8)
        self.assertLessEqual(8, viewport.start_line + len(viewport.lines) - 1)  # type: ignore[operator]


class ViewerFallbackControlTests(unittest.TestCase):
    def test_callback_fallback_normalizes_string_subclass_before_reducer(self) -> None:
        class RangeAnswer(str):
            def __str__(self) -> str:
                raise AssertionError("subclass __str__ must not run")

        read_line = mock.Mock(return_value=RangeAnswer("2-1"))
        viewer = InteractiveTextViewer(
            build_stream_terminal(read_line, mock.Mock(), mock.Mock())
        )
        result = viewer.read_range("one\ntwo", display_name="fallback.md")
        self.assertEqual(
            result,
            TextViewerResult(TextViewerExitKind.SELECTED, (1, 2)),
        )
        read_line.assert_called_once()
        self.assertTrue(
            read_line.call_args.args[0].endswith(
                "Select START-END or /cancel: "
            )
        )
        self.assertIs(type(read_line.return_value), RangeAnswer)

    def test_callback_fallback_retains_validation_interrupt_and_eof_contracts(self) -> None:
        state = ("one", "callback.md")
        for value, error in (
            (object(), TypeError),
            ("1-1\r", ValueError),
            ("1-1\n", ValueError),
        ):
            with self.subTest(value=value):
                viewer = InteractiveTextViewer(
                    build_stream_terminal(
                        mock.Mock(return_value=value),
                        mock.Mock(),
                        mock.Mock(),
                    )
                )
                with self.assertRaises(error):
                    viewer.read_range(state[0], display_name=state[1])

        for failure, expected in (
            (KeyboardInterrupt(), TextViewerExitKind.INTERRUPTED),
            (EOFError(), TextViewerExitKind.EOF),
        ):
            with self.subTest(failure=type(failure).__name__):
                viewer = InteractiveTextViewer(
                    build_stream_terminal(
                        mock.Mock(side_effect=failure),
                        mock.Mock(),
                        mock.Mock(),
                    )
                )
                self.assertEqual(
                    viewer.read_range(state[0], display_name=state[1]),
                    TextViewerResult(expected),
                )

    def test_exact_ascii_grammar_reverse_normalizes_and_cancel_is_exact(self) -> None:
        state = initialize_text_viewer("a\nb\nc", display_name="x")
        _, result = transition_text_viewer_fallback(state, "3-1")
        self.assertEqual(result, TextViewerResult(TextViewerExitKind.SELECTED, (1, 3)))
        _, result = transition_text_viewer_fallback(state, "/cancel")
        self.assertEqual(result, TextViewerResult(TextViewerExitKind.CANCELLED))
        invalid = ("", "0-1", "01-1", "+1-2", " 1-2", "1-2 ", "１-2", "1–2", "/Cancel", "1", "1-4")
        for answer in invalid:
            with self.subTest(answer=answer):
                updated, result = transition_text_viewer_fallback(state, answer)
                self.assertIsNone(result)
                self.assertEqual(updated.notice, TextViewerNotice.INVALID_RANGE)

    def test_enormous_numbers_reject_without_integer_conversion(self) -> None:
        state = initialize_text_viewer("a\nb", display_name="x")
        # Python rejects conversion of this many digits. A clean validation
        # result therefore proves the lexical length guard ran first.
        updated, result = transition_text_viewer_fallback(
            state,
            f"{'9' * 10000}-1",
        )
        self.assertIsNone(result)
        self.assertEqual(updated.notice, TextViewerNotice.INVALID_RANGE)


class ViewerRenderingTests(unittest.TestCase):
    def test_empty_one_and_two_row_frames_show_fixed_default_guidance(self) -> None:
        state = initialize_text_viewer("", display_name="empty.md")
        one_row = render_text_viewer(
            project_text_viewer_viewport(state, 1),
            _capabilities(width=20, ansi=True),
        )
        self.assertEqual(one_row.text, "No selectable lines…")
        self.assertEqual(one_row.line_count, 1)
        self.assertNotIn("\x1b", one_row.text)

        two_rows = render_text_viewer(
            project_text_viewer_viewport(state, 2),
            _capabilities(width=80, ansi=True),
        )
        self.assertEqual(
            two_rows.text,
            "[viewer] empty.md | 0 lines\n"
            "No selectable lines; cancel to close.",
        )
        self.assertEqual(two_rows.line_count, 2)
        self.assertNotIn("\x1b", two_rows.text)

        three_rows = render_text_viewer(
            project_text_viewer_viewport(state, 3),
            _capabilities(width=80),
        )
        self.assertEqual(three_rows.text.count("<empty document>"), 1)
        self.assertTrue(
            three_rows.text.endswith("No selectable lines; cancel to close.")
        )

    def test_rendering_is_height_bounded_one_row_per_source_and_sanitized(self) -> None:
        state = initialize_text_viewer("one\rhidden\n界e\u0301\nlong" + "x" * 100, display_name="bad\x1b]0;owned\x07")
        viewport = project_text_viewer_viewport(state, 4)
        frame = render_text_viewer(viewport, _capabilities(width=18))
        self.assertLessEqual(frame.line_count, 4)
        self.assertEqual(frame.text.count("\n") + 1, frame.line_count)
        self.assertNotIn("\r", frame.text)
        self.assertNotIn("\x07", frame.text)
        self.assertNotIn("\x1b]", frame.text)
        self.assertIn(r"\x0d", frame.text)
        for line in frame.text.splitlines():
            self.assertLessEqual(sum(display_character_width(ch) for ch in line), 18)

    def test_clipping_handles_narrow_wide_combining_long_and_fixed_ansi_only(self) -> None:
        state = initialize_text_viewer("界e\u0301" + "x" * 100, display_name="x")
        viewport = project_text_viewer_viewport(state, 1)
        narrow = render_text_viewer(viewport, _capabilities(width=1))
        self.assertEqual(narrow.text, "…")
        plain = render_text_viewer(viewport, _capabilities(width=12))
        ansi = render_text_viewer(viewport, _capabilities(width=12, ansi=True))
        self.assertIn("…", plain.text)
        self.assertNotIn("\x1b", plain.text)
        self.assertIn("\x1b[1;36m", ansi.text)
        self.assertTrue(ansi.text.endswith("\x1b[0m"))

    def test_fallback_is_plain_complete_and_empty_has_no_phantom_row(self) -> None:
        state = initialize_text_viewer("a\nb", display_name="x")
        frame = render_text_viewer_fallback(state, _capabilities(width=40, ansi=True))
        self.assertIn(" 1 | a", frame.plain_text)
        self.assertIn(" 2 | b", frame.plain_text)
        self.assertTrue(frame.plain_text.endswith("Select START-END or /cancel: "))
        self.assertEqual(frame.text, frame.plain_text)
        self.assertNotIn("\x1b", frame.text)
        empty = render_text_viewer(project_text_viewer_viewport(initialize_text_viewer("", display_name="x"), 3), _capabilities(width=40))
        self.assertEqual(empty.text.count("<empty document>"), 1)


class ViewerDriverTests(unittest.TestCase):
    def test_configured_controls_precede_escape_enter_and_ordinary_key_roles(self) -> None:
        cases = (
            ("vintr_escape", "\x1b", "\x1b", "\x04", TextViewerExitKind.INTERRUPTED),
            ("veof_escape", "\x1b", "\x03", "\x1b", TextViewerExitKind.EOF),
            ("equal_text", "q", "q", "q", TextViewerExitKind.INTERRUPTED),
            ("vintr_cr", "\r", "\r", "\x04", TextViewerExitKind.INTERRUPTED),
            ("vintr_lf", "\n", "\n", "\x04", TextViewerExitKind.INTERRUPTED),
            ("veof_cr", "\r", "\x03", "\r", TextViewerExitKind.EOF),
            ("veof_lf", "\n", "\x03", "\n", TextViewerExitKind.EOF),
            ("vintr_tab", "\t", "\t", "\x04", TextViewerExitKind.INTERRUPTED),
            ("veof_backspace", "\x7f", "\x03", "\x7f", TextViewerExitKind.EOF),
        )
        for label, character, vintr, veof, expected in cases:
            with self.subTest(label=label):
                driver, _input, output, fallback, termios, events = _driver(
                    character
                )
                termios.original[6][termios.VINTR] = vintr.encode("utf-8")  # type: ignore[index]
                termios.original[6][termios.VEOF] = veof.encode("utf-8")  # type: ignore[index]
                with mock.patch(
                    "story_writing_agents.interactive_terminal._driver._escape_input_is_ready",
                    side_effect=AssertionError(
                        "configured control must not begin an escape sequence"
                    ),
                ) as escape_ready:
                    result = _read(
                        driver,
                        initialize_text_viewer("one\ntwo", display_name="x"),
                    )
                self.assertEqual(result, TextViewerResult(expected))
                fallback.assert_not_called()
                escape_ready.assert_not_called()
                self.assertEqual(events.count("input.read"), 1)
                rendered = "".join(output.records)
                self.assertEqual(rendered.count("\x1b[?1049h"), 1)
                self.assertEqual(rendered.count("\x1b[?1049l"), 1)
                self.assertEqual(events.count("termios.restore"), 1)

    def test_live_shift_sequences_resize_every_nonexit_and_restore_screen_once(self) -> None:
        driver, _input, output, fallback, termios, events = _driver(
            "\x1b[1;2B\x1b[1;2B\x1b[1;2Ax\n",
            sizes=((40, 8), (30, 6), (20, 5), (18, 4), (16, 3)),
        )
        with mock.patch(
            "story_writing_agents.interactive_terminal._driver._escape_input_is_ready",
            return_value=True,
        ):
            result = _read(driver, initialize_text_viewer("1\n2\n3", display_name="x"))
        self.assertEqual(result.line_range, (1, 2))
        fallback.assert_not_called()
        rendered = "".join(output.records)
        self.assertEqual(rendered.count("\x1b[?1049h"), 1)
        self.assertEqual(rendered.count("\x1b[?1049l"), 1)
        self.assertNotIn("\x1b[?25", rendered)
        self.assertEqual(events.count("os.geometry"), 6)
        self.assertEqual(events.count("termios.activate"), 1)
        self.assertEqual(events.count("termios.restore"), 1)
        self.assertEqual(termios.original[3], _FakeTermios.ICANON | _FakeTermios.ECHO)

    def test_live_escape_interrupt_eof_and_physical_eof_are_distinct(self) -> None:
        cases = (("\x1b", TextViewerExitKind.CANCELLED), ("\x03", TextViewerExitKind.INTERRUPTED), ("\x04", TextViewerExitKind.EOF), ("", TextViewerExitKind.EOF))
        for characters, expected in cases:
            with self.subTest(expected=expected, physical=not characters):
                driver, *_ = _driver(characters)
                patch = mock.patch(
                    "story_writing_agents.interactive_terminal._driver._escape_input_is_ready",
                    return_value=False,
                )
                with patch:
                    result = _read(driver, initialize_text_viewer("x", display_name="x"))
                self.assertEqual(result.kind, expected)

    def test_empty_enter_remains_open_until_cancel(self) -> None:
        driver, _input, output, *_ = _driver("\n\x1b")
        with mock.patch("story_writing_agents.interactive_terminal._driver._escape_input_is_ready", return_value=False):
            result = _read(driver, initialize_text_viewer("", display_name="x"))
        self.assertEqual(result.kind, TextViewerExitKind.CANCELLED)
        self.assertGreaterEqual("".join(output.records).count("No selectable lines"), 2)

    def test_unsupported_capability_falls_back_with_visible_retry(self) -> None:
        driver, input_stream, _output, fallback, _termios, events = _driver("", answers=("0-1", "2-1"))
        driver._os_provider = lambda: SimpleNamespace(name="posix", environ={"TERM": "dumb"})  # type: ignore[attr-defined]
        result = _read(driver, initialize_text_viewer("a\nb", display_name="x"))
        self.assertEqual(result.line_range, (1, 2))
        self.assertEqual(fallback.call_count, 2)
        self.assertIn("valid one-based", fallback.call_args_list[1].args[0])
        self.assertNotIn("input.read", events)
        self.assertEqual(input_stream.characters, [])

    def test_setup_or_active_failure_never_falls_back_and_cleanup_precedence_is_exact(self) -> None:
        driver, _input, output, fallback, termios, events = _driver("\n")
        termios.activation_failure = RuntimeError("activate")
        with self.assertRaisesRegex(RuntimeError, "activate"):
            _read(driver, initialize_text_viewer("x", display_name="x"))
        fallback.assert_not_called()
        self.assertEqual(events.count("termios.restore"), 1)

        driver, _input, output, fallback, termios, events = _driver("\n")
        active = RuntimeError("render")
        screen = RuntimeError("screen")
        restore = RuntimeError("restore")
        termios.restoration_failure = restore
        writes = 0

        def fail_writes(text: str) -> int:
            nonlocal writes
            writes += 1
            output.records.append(text)
            if writes == 2:
                raise active
            if "?1049l" in text:
                raise screen
            return len(text)

        output.write = fail_writes  # type: ignore[method-assign]
        with self.assertRaisesRegex(RuntimeError, "restoration failed") as raised:
            _read(driver, initialize_text_viewer("x", display_name="x"))
        self.assertIs(raised.exception.__cause__, restore)
        self.assertTrue(any("another interactive input failure" in note for note in raised.exception.__notes__))
        fallback.assert_not_called()
        self.assertEqual(events.count("termios.restore"), 1)

        driver, _input, output, fallback, _termios, _events = _driver("\n")
        writes = 0

        def fail_screen_over_active(text: str) -> int:
            nonlocal writes
            writes += 1
            output.records.append(text)
            if writes == 2:
                raise active
            if "?1049l" in text:
                raise screen
            return len(text)

        output.write = fail_screen_over_active  # type: ignore[method-assign]
        with self.assertRaisesRegex(RuntimeError, "screen") as raised:
            _read(driver, initialize_text_viewer("x", display_name="x"))
        self.assertIs(raised.exception, screen)
        fallback.assert_not_called()

    def test_invalid_collaborator_output_cleans_up_and_does_not_fallback(self) -> None:
        driver, *_rest = _driver("x")
        state = initialize_text_viewer("x", display_name="x")
        with self.assertRaisesRegex(TypeError, "viewport projector"):
            driver.read_text_viewer(
                state,
                transition_text_viewer,
                transition_text_viewer_fallback,
                lambda _state, _height: object(),  # type: ignore[arg-type]
                render_text_viewer,
                render_text_viewer_fallback,
            )

    def test_read_render_flush_and_geometry_failures_keep_terminal_cleanup(self) -> None:
        state = initialize_text_viewer("x", display_name="x")

        driver, input_stream, output, fallback, _termios, events = _driver("x")
        input_stream.read = mock.Mock(side_effect=RuntimeError("read"))  # type: ignore[method-assign]
        with self.assertRaisesRegex(RuntimeError, "read"):
            _read(driver, state)
        fallback.assert_not_called()
        self.assertEqual(events.count("termios.restore"), 1)
        self.assertEqual("".join(output.records).count("\x1b[?1049l"), 1)

        driver, _input, output, fallback, _termios, events = _driver("x")
        with self.assertRaisesRegex(RuntimeError, "render"):
            driver.read_text_viewer(
                state,
                transition_text_viewer,
                transition_text_viewer_fallback,
                project_text_viewer_viewport,
                mock.Mock(side_effect=RuntimeError("render")),
                render_text_viewer_fallback,
            )
        fallback.assert_not_called()
        self.assertEqual(events.count("termios.restore"), 1)
        self.assertEqual("".join(output.records).count("\x1b[?1049l"), 1)

        driver, _input, output, fallback, _termios, events = _driver("x")
        output.flush_failure = RuntimeError("flush")
        with self.assertRaisesRegex(RuntimeError, "flush"):
            _read(driver, state)
        fallback.assert_not_called()
        self.assertEqual(events.count("termios.restore"), 1)

        driver, _input, output, fallback, _termios, events = _driver("\n")
        geometry_calls = 0

        def failing_geometry(_descriptor: int) -> os.terminal_size:
            nonlocal geometry_calls
            geometry_calls += 1
            if geometry_calls > 1:
                raise OSError("resize unavailable")
            return os.terminal_size((40, 8))

        driver._os_provider().get_terminal_size = failing_geometry  # type: ignore[attr-defined]
        result = _read(driver, state)
        self.assertEqual(result.line_range, (1, 1))
        fallback.assert_not_called()
        self.assertGreaterEqual(geometry_calls, 2)
        self.assertEqual(events.count("termios.restore"), 1)
        self.assertEqual("".join(output.records).count("\x1b[?1049l"), 1)


if __name__ == "__main__":
    unittest.main()
