"""Deterministic contracts for the package-internal ranged text viewer."""

from __future__ import annotations

import codecs
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
from story_writing_agents.interactive_terminal import _driver as driver_module
from story_writing_agents.interactive_terminal import (
    _viewer_model as viewer_model_module,
)
from story_writing_agents.interactive_terminal import (
    _viewer_render as viewer_render_module,
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
        self.characters = list(characters.encode("utf-8"))
        self.events = events
        self.encoding = "utf-8"
        self.errors = "strict"
        self.buffer = self

    def isatty(self) -> bool:
        self.events.append("input.isatty")
        return True

    def fileno(self) -> int:
        self.events.append("input.fileno")
        return 7

    def read(self, size: int) -> str:
        raise AssertionError(f"text read must not run for byte input: {size}")

    def read1(self, size: int) -> bytes:
        self.events.append("input.read")
        if size != 1:
            raise AssertionError(size)
        return bytes((self.characters.pop(0),)) if self.characters else b""


class _ByteInput:
    """Expose exact bytes through one declared incremental text codec."""

    def __init__(self, raw: bytes, *, encoding: str, errors: str) -> None:
        self.raw = list(raw)
        self.encoding = encoding
        self.errors = errors
        self.read_calls = 0
        self.buffer = SimpleNamespace(read1=self.read_one)

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 7

    def read(self, size: int) -> str:
        raise AssertionError(f"text read must not run for byte input: {size}")

    def read_one(self, size: int) -> bytes:
        if size != 1:
            raise AssertionError(size)
        self.read_calls += 1
        return bytes((self.raw.pop(0),)) if self.raw else b""


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
    disabled_control: object = 0,
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

    def fpathconf(descriptor: int, name: str) -> object:
        events.append("os.fpathconf")
        if descriptor != 7 or name != "PC_VDISABLE":
            raise AssertionError((descriptor, name))
        if isinstance(disabled_control, BaseException):
            raise disabled_control
        return disabled_control

    os_double = SimpleNamespace(
        name="posix",
        environ={"TERM": "xterm-256color"},
        get_terminal_size=get_terminal_size,
        fpathconf=fpathconf,
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


def _codec_driver(
    raw: bytes,
    *,
    encoding: str,
    errors: str,
    vintr: bytes = b"\x03",
    veof: bytes = b"\x04",
    disabled_control: int = 0,
) -> tuple[
    RawTerminalDriver,
    _ByteInput,
    _RecordingOutput,
    mock.Mock,
    _FakeTermios,
    list[str],
]:
    """Build one byte-backed raw driver with an exact declared codec."""

    events: list[str] = []
    input_stream = _ByteInput(raw, encoding=encoding, errors=errors)
    output = _RecordingOutput(events)
    fallback = mock.Mock(return_value="1-1")
    termios = _FakeTermios(events)
    termios.original[6][termios.VINTR] = vintr  # type: ignore[index]
    termios.original[6][termios.VEOF] = veof  # type: ignore[index]

    def fpathconf(descriptor: int, name: str) -> int:
        events.append("os.fpathconf")
        if descriptor != 7 or name != "PC_VDISABLE":
            raise AssertionError((descriptor, name))
        return disabled_control

    os_double = SimpleNamespace(
        name="posix",
        environ={"TERM": "xterm-256color"},
        get_terminal_size=lambda _descriptor: os.terminal_size((40, 8)),
        fpathconf=fpathconf,
    )
    driver = RawTerminalDriver(
        fallback,
        input_stream,  # type: ignore[arg-type]
        output,  # type: ignore[arg-type]
        transition_line_input,
        transition_main_command_input,
        append_main_input_history,
        termios_provider=lambda: termios,
        os_provider=lambda: os_double,
    )
    return driver, input_stream, output, fallback, termios, events


class ViewerModelContractTests(unittest.TestCase):
    def test_logical_line_limit_is_exact_and_rejects_before_materialization_io(self) -> None:
        accepted_text = "\n" * 65_536
        document = TextViewerDocument(accepted_text, "lines.md")
        self.assertEqual(document.line_count, 65_536)
        self.assertEqual(document.lines[0], "")
        self.assertEqual(document.lines[-1], "")
        del document, accepted_text

        read_line = mock.Mock(return_value="1-1")
        input_stream = mock.Mock()
        output_stream = mock.Mock()
        viewer = InteractiveTextViewer(
            build_stream_terminal(read_line, input_stream, output_stream)
        )
        with (
            mock.patch.object(
                viewer_model_module,
                "_validate_fallback_projection",
                side_effect=AssertionError("projection must not run"),
            ) as projection,
            mock.patch.object(
                viewer_model_module,
                "tuple",
                side_effect=AssertionError("line tuple must not materialize"),
                create=True,
            ) as tuple_constructor,
            self.assertRaisesRegex(ValueError, "65,536 logical-line limit"),
        ):
            viewer.read_range("\n" * 65_537, display_name="lines.md")
        projection.assert_not_called()
        tuple_constructor.assert_not_called()
        read_line.assert_not_called()
        input_stream.assert_not_called()
        output_stream.assert_not_called()

    def test_fallback_projection_limit_is_exact_complete_and_precedes_all_io(self) -> None:
        limit = 16 * 1024 * 1024
        display_name = "x"
        fixed_bytes = sum(
            (
                len("[viewer]  | lines 1-1"),
                len(display_name),
                7,  # One one-digit numbered-record prefix.
                len("Enter a valid one-based START-END range."),
                len("Select START-END or /cancel: "),
                3,  # Newlines between header, record, notice, and prompt.
            )
        )
        controls, ordinary = divmod(limit - fixed_bytes, 4)
        accepted_text = "\x00" * controls + "a" * ordinary
        self.assertLess(len(accepted_text.encode("utf-8")), 8 * 1024 * 1024)
        document = TextViewerDocument(accepted_text, display_name)
        state = TextViewerState(
            document,
            1,
            1,
            TextViewerNotice.INVALID_RANGE,
        )
        frame = render_text_viewer_fallback(
            state,
            _capabilities(width=20, ansi=True),
        )
        self.assertEqual(len(frame.plain_text.encode("utf-8")), limit)
        self.assertEqual(frame.text, frame.plain_text)
        self.assertNotIn("\x1b", frame.text)
        del frame, state, document

        read_line = mock.Mock(return_value="1-1")
        input_stream = mock.Mock()
        output_stream = mock.Mock()
        viewer = InteractiveTextViewer(
            build_stream_terminal(read_line, input_stream, output_stream)
        )
        with (
            mock.patch.object(
                viewer_model_module,
                "tuple",
                side_effect=AssertionError("line tuple must not materialize"),
                create=True,
            ) as tuple_constructor,
            self.assertRaisesRegex(ValueError, "16 MiB UTF-8 limit"),
        ):
            viewer.read_range(
                accepted_text + "a",
                display_name=display_name,
            )
        tuple_constructor.assert_not_called()
        read_line.assert_not_called()
        input_stream.assert_not_called()
        output_stream.assert_not_called()
        del accepted_text

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

    def test_viewport_capacity_and_canonical_projection_are_exact(self) -> None:
        def viewport(height: int, count: int) -> TextViewerViewport:
            return TextViewerViewport(
                "x",
                3,
                height,
                1,
                tuple(str(value) for value in range(1, count + 1)),
                1,
                None,
                None,
            )

        for height in (1, 2, 3):
            with self.subTest(height=height, accepted=1):
                self.assertEqual(len(viewport(height, 1).lines), 1)
            with self.subTest(height=height, rejected=2):
                with self.assertRaisesRegex(
                    ValueError,
                    "viewport lines must fit inside the frame height",
                ):
                    viewport(height, 2)
        self.assertEqual(len(viewport(4, 2).lines), 2)
        with self.assertRaisesRegex(
            ValueError,
            "viewport lines must fit inside the frame height",
        ):
            viewport(4, 3)

        state = initialize_text_viewer("1\n2\n3\n4\n5", display_name="x")
        for height, expected in ((1, 1), (2, 1), (3, 1), (4, 2), (5, 3)):
            with self.subTest(projected_height=height):
                projected = project_text_viewer_viewport(state, height)
                self.assertEqual(len(projected.lines), expected)

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

    def test_empty_fallback_invalid_answers_retain_empty_guidance_until_cancel(self) -> None:
        state = initialize_text_viewer("", display_name="empty.md")
        for answer in ("", "1-1", "0-0", "bad", " 1-1 "):
            with self.subTest(answer=answer):
                state, result = transition_text_viewer_fallback(state, answer)
                self.assertIsNone(result)
                self.assertEqual(state.notice, TextViewerNotice.EMPTY_DOCUMENT)
                frame = render_text_viewer_fallback(
                    state,
                    _capabilities(width=80),
                )
                self.assertIn(
                    "No selectable lines; cancel to close.",
                    frame.plain_text,
                )
                self.assertNotIn("valid one-based", frame.plain_text)
        state, result = transition_text_viewer_fallback(state, "/cancel")
        self.assertEqual(result, TextViewerResult(TextViewerExitKind.CANCELLED))
        self.assertEqual(state.notice, TextViewerNotice.EMPTY_DOCUMENT)


class ViewerRenderingTests(unittest.TestCase):
    def test_fallback_preserves_complete_sanitized_long_records_and_prompt(self) -> None:
        tail = "start\r\x1b\u2028\x00" + "x" * 2_048 + "TAIL"
        state = initialize_text_viewer(
            "\n".join((*("line" for _ in range(11)), tail)),
            display_name="name\x1b.md",
            initial_line=12,
        )
        frame = render_text_viewer_fallback(
            state,
            _capabilities(width=12, ansi=True),
        )
        self.assertIn(r"[viewer] name\x1b.md | line 12/12", frame.plain_text)
        record = next(
            line for line in frame.plain_text.splitlines() if line.startswith(">  12 |")
        )
        self.assertTrue(record.endswith("TAIL"))
        self.assertIn(r"\x0d", record)
        self.assertIn(r"\x1b", record)
        self.assertIn(r"\u2028", record)
        self.assertIn(r"\x00", record)
        self.assertNotIn("…", record)
        self.assertGreater(
            sum(display_character_width(character) for character in record),
            frame.width,
        )
        self.assertTrue(frame.plain_text.endswith("Select START-END or /cancel: "))
        self.assertEqual(frame.text, frame.plain_text)
        for unsafe in ("\r", "\x1b", "\u2028", "\x00"):
            self.assertNotIn(unsafe, frame.text)

    def test_live_clipping_has_exact_suffix_independent_operation_bounds(self) -> None:
        sanitizer = viewer_render_module.sanitize_terminal_text
        width_function = viewer_render_module.display_character_width

        def measured(content: str) -> tuple[str, int, int]:
            state = initialize_text_viewer(
                content,
                display_name="bounded.md",
            )
            viewport = project_text_viewer_viewport(state, 1)
            with (
                mock.patch.object(
                    viewer_render_module,
                    "sanitize_terminal_text",
                    wraps=sanitizer,
                ) as sanitize_calls,
                mock.patch.object(
                    viewer_render_module,
                    "display_character_width",
                    wraps=width_function,
                ) as width_calls,
            ):
                frame = render_text_viewer(
                    viewport,
                    _capabilities(width=20),
                )
            self.assertTrue(
                all(
                    type(call.args[0]) is str and len(call.args[0]) == 1
                    for call in sanitize_calls.call_args_list
                )
            )
            return frame.text, sanitize_calls.call_count, width_calls.call_count

        positive_prefix = "a" * 1_024
        positive_small = measured(positive_prefix)
        positive_large_text = positive_prefix + "b" * (2 * 1024 * 1024)
        positive_large = measured(positive_large_text)
        self.assertEqual(positive_small, positive_large)
        self.assertEqual(positive_small[1], 21)
        self.assertEqual(positive_small[2], 22)
        del positive_large_text

        combining_prefix = "\u0301" * 1_024
        combining_small = measured(combining_prefix)
        combining_large_text = combining_prefix + "\u0301" * (2 * 1024 * 1024)
        combining_large = measured(combining_large_text)
        self.assertEqual(combining_small, combining_large)
        self.assertEqual(combining_small[1], 276)
        self.assertEqual(combining_small[2], 277)
        del combining_large_text

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
    def test_safe_byte_codec_canonical_and_alias_matrix_enters_live_mode(self) -> None:
        cases = (
            ("ascii", b"\x03", b"\x03", b"\x04", TextViewerExitKind.INTERRUPTED),
            ("us-ascii", b"\x03", b"\x03", b"\x04", TextViewerExitKind.INTERRUPTED),
            ("646", b"\x03", b"\x03", b"\x04", TextViewerExitKind.INTERRUPTED),
            ("utf-8", b"\x04", b"\x03", b"\x04", TextViewerExitKind.EOF),
            ("utf8", b"\x04", b"\x03", b"\x04", TextViewerExitKind.EOF),
            ("u8", b"\x04", b"\x03", b"\x04", TextViewerExitKind.EOF),
            ("iso8859-1", b"\xe9", b"\xe9", b"\x04", TextViewerExitKind.INTERRUPTED),
            ("latin1", b"\xe9", b"\xe9", b"\x04", TextViewerExitKind.INTERRUPTED),
            ("iso-8859-1", b"\xe9", b"\xe9", b"\x04", TextViewerExitKind.INTERRUPTED),
        )
        for encoding, raw, vintr, veof, expected in cases:
            with self.subTest(encoding=encoding):
                driver, input_stream, output, fallback, _termios, events = (
                    _codec_driver(
                        raw,
                        encoding=encoding,
                        errors="strict",
                        vintr=vintr,
                        veof=veof,
                    )
                )
                result = _read(
                    driver,
                    initialize_text_viewer("one", display_name="x"),
                )
                self.assertEqual(result, TextViewerResult(expected))
                self.assertEqual(input_stream.read_calls, 1)
                fallback.assert_not_called()
                self.assertEqual(events.count("os.fpathconf"), 1)
                self.assertEqual(events.count("termios.activate"), 1)
                self.assertEqual(events.count("termios.restore"), 1)
                rendered = "".join(output.records)
                self.assertEqual(rendered.count("\x1b[?1049h"), 1)
                self.assertEqual(rendered.count("\x1b[?1049l"), 1)

    def test_utf8_context_dependent_controls_fallback_before_live_mutation(self) -> None:
        controls = (0x80, 0xBF, 0xC2, 0xC3, 0xA9, 0xF4)
        for control_name in ("VINTR", "VEOF"):
            for value in controls:
                with self.subTest(control=control_name, value=hex(value)):
                    arguments = (
                        {"vintr": bytes((value,))}
                        if control_name == "VINTR"
                        else {"veof": bytes((value,))}
                    )
                    driver, input_stream, output, fallback, _termios, events = (
                        _codec_driver(
                            b"",
                            encoding="utf-8",
                            errors="surrogateescape",
                            **arguments,
                        )
                    )
                    result = _read(
                        driver,
                        initialize_text_viewer("one", display_name="x"),
                    )
                    self.assertEqual(result.line_range, (1, 1))
                    fallback.assert_called_once()
                    self.assertEqual(input_stream.read_calls, 0)
                    self.assertEqual(events.count("os.fpathconf"), 1)
                    self.assertEqual(events.count("termios.get"), 1)
                    self.assertNotIn("termios.activate", events)
                    self.assertEqual(output.records, [])

    def test_utf8_surrogateescape_safe_control_edges_remain_live(self) -> None:
        for value in (0x03, 0xC0, 0xC1, 0xF5, 0xFF):
            with self.subTest(value=hex(value)):
                raw = bytes((value,))
                driver, input_stream, output, fallback, _termios, events = (
                    _codec_driver(
                        raw,
                        encoding="utf-8",
                        errors="surrogateescape",
                        vintr=raw,
                    )
                )
                result = _read(
                    driver,
                    initialize_text_viewer("one", display_name="x"),
                )
                self.assertEqual(
                    result,
                    TextViewerResult(TextViewerExitKind.INTERRUPTED),
                )
                self.assertEqual(input_stream.read_calls, 1)
                fallback.assert_not_called()
                self.assertEqual(events.count("termios.activate"), 1)
                self.assertEqual(events.count("termios.restore"), 1)
                rendered = "".join(output.records)
                self.assertEqual(rendered.count("\x1b[?1049h"), 1)
                self.assertEqual(rendered.count("\x1b[?1049l"), 1)

    def test_control_terminated_and_parameterized_sequences_are_unsupported(self) -> None:
        state = initialize_text_viewer("one\ntwo", display_name="x")

        def run(
            characters: str,
        ) -> tuple[TextViewerResult, list[KeyEvent], int]:
            driver, _input, _output, fallback, _termios, _events = _driver(
                characters
            )
            seen: list[KeyEvent] = []
            renderer = mock.Mock(wraps=render_text_viewer)

            def observe(
                current: TextViewerState,
                event: KeyEvent,
            ) -> tuple[TextViewerState, TextViewerResult | None]:
                seen.append(event)
                return transition_text_viewer(current, event)

            with mock.patch.object(
                driver_module,
                "_escape_input_is_ready",
                return_value=True,
            ):
                result = driver.read_text_viewer(
                    state,
                    observe,
                    transition_text_viewer_fallback,
                    project_text_viewer_viewport,
                    renderer,
                    render_text_viewer_fallback,
                )
            fallback.assert_not_called()
            return result, seen, renderer.call_count

        malformed, malformed_events, malformed_renders = run("\x1bO1A\n")
        self.assertEqual(malformed.line_range, (1, 1))
        self.assertEqual(malformed_events, [KeyEvent(KeyKind.ENTER)])
        self.assertNotIn(KeyEvent(KeyKind.TEXT, text="A"), malformed_events)
        self.assertEqual(malformed_renders, 2)

        for introducer in ("\x1b[", "\x1bO"):
            with self.subTest(introducer=repr(introducer)):
                selected, events, renders = run(
                    introducer + "\n\x1b[1;2B\n"
                )
                self.assertEqual(selected.line_range, (1, 2))
                self.assertEqual(
                    events,
                    [KeyEvent(KeyKind.SHIFT_DOWN), KeyEvent(KeyKind.ENTER)],
                )
                self.assertEqual(renders, 3)

        valid, valid_events, valid_renders = run("\x1bOB\x1bOA\n")
        self.assertEqual(valid.line_range, (1, 1))
        self.assertEqual(
            valid_events,
            [
                KeyEvent(KeyKind.DOWN),
                KeyEvent(KeyKind.UP),
                KeyEvent(KeyKind.ENTER),
            ],
        )
        self.assertEqual(valid_renders, 3)

    def test_overlong_suffixes_do_not_move_or_anchor_before_real_shift_down(
        self,
    ) -> None:
        state = initialize_text_viewer("one\ntwo\nthree", display_name="x")
        sequences = (
            ("csi_down", "\x1b[" + "1" * 62 + "B"),
            ("ss3_down", "\x1bO" + "1" * 62 + "B"),
            ("csi_shift_up", "\x1b[" + "1" * 62 + "1;2A"),
            ("csi_shift_down", "\x1b[" + "1" * 62 + "1;2B"),
        )
        for label, overlong in sequences:
            with self.subTest(suffix=label):
                driver, input_stream, _output, fallback, _termios, _events = (
                    _driver(overlong + "\x1b[1;2B\n")
                )
                seen: list[KeyEvent] = []
                renderer = mock.Mock(wraps=render_text_viewer)

                def observe(
                    current: TextViewerState,
                    event: KeyEvent,
                ) -> tuple[TextViewerState, TextViewerResult | None]:
                    seen.append(event)
                    return transition_text_viewer(current, event)

                with mock.patch.object(
                    driver_module,
                    "_escape_input_is_ready",
                    return_value=True,
                ) as readiness:
                    result = driver.read_text_viewer(
                        state,
                        observe,
                        transition_text_viewer_fallback,
                        project_text_viewer_viewport,
                        renderer,
                        render_text_viewer_fallback,
                    )
                self.assertEqual(result.line_range, (1, 2))
                self.assertEqual(
                    seen,
                    [KeyEvent(KeyKind.SHIFT_DOWN), KeyEvent(KeyKind.ENTER)],
                )
                self.assertEqual(renderer.call_count, 3)
                self.assertEqual(readiness.call_count, 2)
                self.assertEqual(input_stream.characters, [])
                fallback.assert_not_called()

    def test_physical_eof_while_discarding_overlong_sequence_is_typed(self) -> None:
        for introducer in ("\x1b[", "\x1bO"):
            with self.subTest(introducer=repr(introducer)):
                characters = introducer + "1" * 62
                driver, _input, output, fallback, _termios, events = _driver(
                    characters
                )
                with mock.patch.object(
                    driver_module,
                    "_escape_input_is_ready",
                    return_value=True,
                ) as readiness:
                    result = _read(
                        driver,
                        initialize_text_viewer("one\ntwo", display_name="x"),
                    )
                self.assertEqual(result, TextViewerResult(TextViewerExitKind.EOF))
                readiness.assert_called_once_with(7)
                fallback.assert_not_called()
                self.assertEqual(events.count("input.read"), len(characters) + 1)
                rendered = "".join(output.records)
                self.assertEqual(rendered.count("\x1b[?1049h"), 1)
                self.assertEqual(rendered.count("\x1b[?1049l"), 1)
                self.assertEqual(events.count("termios.restore"), 1)

    def test_unsupported_byte_codec_matrix_falls_back_before_live_seams(self) -> None:
        encodings = (
            "iso2022_jp",
            "iso2022_jp_1",
            "iso2022_jp_2",
            "iso2022_jp_2004",
            "iso2022_jp_3",
            "iso2022_jp_ext",
            "iso2022_kr",
            "hz",
            "utf-7",
            "utf-8-sig",
            "raw_unicode_escape",
        )
        for encoding in encodings:
            with self.subTest(encoding=encoding):
                try:
                    codecs.lookup(encoding)
                except LookupError as failure:
                    self.fail(
                        f"required built-in codec {encoding!r} is missing: "
                        f"{failure}"
                    )
                driver, input_stream, output, fallback, _termios, events = (
                    _codec_driver(
                        b"\x03",
                        encoding=encoding,
                        errors="strict",
                        vintr=b"\xff",
                        veof=b"\xff",
                        disabled_control=255,
                    )
                )
                result = _read(
                    driver,
                    initialize_text_viewer("one", display_name="x"),
                )
                self.assertEqual(
                    result,
                    TextViewerResult(TextViewerExitKind.SELECTED, (1, 1)),
                )
                fallback.assert_called_once()
                self.assertEqual(input_stream.read_calls, 0)
                self.assertNotIn("os.fpathconf", events)
                self.assertNotIn("termios.get", events)
                self.assertNotIn("termios.activate", events)
                self.assertNotIn("termios.restore", events)
                self.assertEqual(output.records, [])

    def test_failed_codec_lookup_precedes_disabled_control_inspection(self) -> None:
        driver, input_stream, output, fallback, _termios, events = _codec_driver(
            b"\x03",
            encoding="story-writing-agents-missing-codec",
            errors="strict",
            vintr=b"\xff",
            veof=b"\xff",
            disabled_control=255,
        )
        result = _read(
            driver,
            initialize_text_viewer("one", display_name="x"),
        )
        self.assertEqual(
            result,
            TextViewerResult(TextViewerExitKind.SELECTED, (1, 1)),
        )
        fallback.assert_called_once()
        self.assertEqual(input_stream.read_calls, 0)
        self.assertNotIn("os.fpathconf", events)
        self.assertNotIn("termios.get", events)
        self.assertNotIn("termios.activate", events)
        self.assertNotIn("termios.restore", events)
        self.assertEqual(output.records, [])

    def test_byte_codec_controls_round_trip_and_disabled_bytes_precede_decode(self) -> None:
        cases = (
            (
                "ascii_vintr",
                b"\x03",
                "utf-8",
                "strict",
                b"\x03",
                b"\x04",
                TextViewerExitKind.INTERRUPTED,
            ),
            (
                "latin1_vintr",
                b"\xe9",
                "latin-1",
                "strict",
                b"\xe9",
                b"\x04",
                TextViewerExitKind.INTERRUPTED,
            ),
            (
                "latin1_veof",
                b"\xea",
                "latin-1",
                "strict",
                b"\x03",
                b"\xea",
                TextViewerExitKind.EOF,
            ),
            (
                "surrogateescape_vintr",
                b"\xff",
                "utf-8",
                "surrogateescape",
                b"\xff",
                b"\x04",
                TextViewerExitKind.INTERRUPTED,
            ),
            (
                "surrogateescape_veof",
                b"\xfe",
                "utf-8",
                "surrogateescape",
                b"\x03",
                b"\xfe",
                TextViewerExitKind.EOF,
            ),
        )
        for label, raw, encoding, errors, vintr, veof, expected in cases:
            with self.subTest(label=label):
                driver, input_stream, output, fallback, _termios, events = (
                    _codec_driver(
                        raw,
                        encoding=encoding,
                        errors=errors,
                        vintr=vintr,
                        veof=veof,
                    )
                )
                result = _read(
                    driver,
                    initialize_text_viewer("one", display_name="x"),
                )
                self.assertEqual(result, TextViewerResult(expected))
                self.assertEqual(input_stream.read_calls, 1)
                fallback.assert_not_called()
                rendered = "".join(output.records)
                self.assertEqual(rendered.count("\x1b[?1049h"), 1)
                self.assertEqual(rendered.count("\x1b[?1049l"), 1)
                self.assertEqual(events.count("termios.restore"), 1)

        driver, input_stream, output, fallback, _termios, events = _codec_driver(
            b"\n",
            encoding="utf-8",
            errors="strict",
            vintr=b"\xff",
            veof=b"\xff",
            disabled_control=255,
        )
        result = _read(
            driver,
            initialize_text_viewer("one", display_name="x"),
        )
        self.assertEqual(
            result,
            TextViewerResult(TextViewerExitKind.SELECTED, (1, 1)),
        )
        self.assertEqual(input_stream.read_calls, 1)
        fallback.assert_not_called()
        self.assertEqual(events.count("termios.activate"), 1)
        self.assertEqual(events.count("termios.restore"), 1)
        self.assertEqual("".join(output.records).count("\x1b[?1049h"), 1)

    def test_invalid_control_sequences_and_unsupported_codec_fallback_before_activation(self) -> None:
        cases = (
            ("incomplete_utf8", b"\xc3", "utf-8", "strict"),
            ("ignored_utf8", b"\xff", "utf-8", "ignore"),
            ("replacement_utf8", b"\xff", "utf-8", "replace"),
            ("lossy_utf8", b"\xff", "utf-8", "backslashreplace"),
            ("incompatible_ascii", b"\x80", "ascii", "strict"),
            ("unsupported_utf16", b"\x03", "utf-16", "strict"),
        )
        for label, control, encoding, errors in cases:
            with self.subTest(label=label):
                driver, input_stream, output, fallback, _termios, events = (
                    _codec_driver(
                        b"",
                        encoding=encoding,
                        errors=errors,
                        vintr=control,
                    )
                )
                result = _read(
                    driver,
                    initialize_text_viewer("one", display_name="x"),
                )
                self.assertEqual(
                    result,
                    TextViewerResult(TextViewerExitKind.SELECTED, (1, 1)),
                )
                fallback.assert_called_once()
                self.assertEqual(input_stream.read_calls, 0)
                self.assertNotIn("termios.activate", events)
                self.assertNotIn("termios.restore", events)
                self.assertEqual(output.records, [])

    def test_post_activation_interrupt_restores_without_screen_or_fallback(self) -> None:
        driver, _input, output, fallback, _termios, events = _driver("\n")
        activate = driver_module._activate_terminal

        def activate_then_interrupt(configuration: object) -> None:
            activate(configuration)  # type: ignore[arg-type]
            raise KeyboardInterrupt

        with mock.patch.object(
            driver_module,
            "_activate_terminal",
            side_effect=activate_then_interrupt,
        ):
            with self.assertRaises(KeyboardInterrupt):
                _read(
                    driver,
                    initialize_text_viewer("one", display_name="x"),
                )
        fallback.assert_not_called()
        self.assertEqual(events.count("termios.activate"), 1)
        self.assertEqual(events.count("termios.restore"), 1)
        rendered = "".join(output.records)
        self.assertNotIn("\x1b[?1049h", rendered)
        self.assertNotIn("\x1b[?1049l", rendered)

    def test_screen_exit_interrupt_restores_and_precedes_active_failure(self) -> None:
        driver, input_stream, output, fallback, _termios, events = _driver("x")
        active = RuntimeError("active")
        input_stream.read1 = mock.Mock(side_effect=active)  # type: ignore[method-assign]
        writes = output.write

        def interrupt_after_exit_write(text: str) -> int:
            written = writes(text)
            if text == "\x1b[?1049l":
                raise KeyboardInterrupt
            return written

        output.write = interrupt_after_exit_write  # type: ignore[method-assign]
        with self.assertRaises(KeyboardInterrupt):
            _read(driver, initialize_text_viewer("one", display_name="x"))
        fallback.assert_not_called()
        rendered = "".join(output.records)
        self.assertEqual(rendered.count("\x1b[?1049h"), 1)
        self.assertEqual(rendered.count("\x1b[?1049l"), 1)
        self.assertEqual(events.count("termios.restore"), 1)

    def test_escape_readiness_interrupt_is_typed_and_false_is_cancelled(self) -> None:
        for readiness, expected in (
            (KeyboardInterrupt(), TextViewerExitKind.INTERRUPTED),
            (False, TextViewerExitKind.CANCELLED),
        ):
            with self.subTest(expected=expected):
                driver, _input, output, fallback, _termios, events = _driver(
                    "\x1b"
                )
                patch_arguments = (
                    {"side_effect": readiness}
                    if isinstance(readiness, BaseException)
                    else {"return_value": readiness}
                )
                with mock.patch.object(
                    driver_module,
                    "_escape_input_is_ready",
                    **patch_arguments,
                ):
                    result = _read(
                        driver,
                        initialize_text_viewer("one", display_name="x"),
                    )
                self.assertEqual(result, TextViewerResult(expected))
                fallback.assert_not_called()
                rendered = "".join(output.records)
                self.assertEqual(rendered.count("\x1b[?1049h"), 1)
                self.assertEqual(rendered.count("\x1b[?1049l"), 1)
                self.assertEqual(events.count("termios.restore"), 1)

    def test_partial_escape_physical_eof_is_eof_without_cancel_frame(self) -> None:
        for characters in ("\x1b[", "\x1bO"):
            with self.subTest(characters=repr(characters)):
                driver, _input, output, fallback, _termios, events = _driver(
                    characters
                )
                with mock.patch.object(
                    driver_module,
                    "_escape_input_is_ready",
                    return_value=True,
                ) as readiness:
                    result = _read(
                        driver,
                        initialize_text_viewer("one", display_name="x"),
                    )
                self.assertEqual(
                    result,
                    TextViewerResult(TextViewerExitKind.EOF),
                )
                readiness.assert_called_once_with(7)
                fallback.assert_not_called()
                rendered = "".join(output.records)
                self.assertNotIn("[cancelled]", rendered)
                self.assertEqual(rendered.count("\x1b[?1049h"), 1)
                self.assertEqual(rendered.count("\x1b[?1049l"), 1)
                self.assertEqual(events.count("input.read"), 3)
                self.assertEqual(events.count("termios.restore"), 1)

    def test_disabled_controls_are_absent_for_viewer_and_shared_editor(self) -> None:
        for disabled_value in (0, 255):
            disabled_character = chr(disabled_value)
            disabled_bytes = bytes((disabled_value,))
            for disabled_names in (("vintr",), ("veof",), ("vintr", "veof")):
                with self.subTest(
                    disabled=disabled_value,
                    controls=disabled_names,
                ):
                    driver, input_stream, output, fallback, termios, events = _driver(
                        disabled_character + "\n",
                        disabled_control=disabled_value,
                    )
                    termios.original[6][termios.VINTR] = (  # type: ignore[index]
                        disabled_bytes if "vintr" in disabled_names else b"\x03"
                    )
                    termios.original[6][termios.VEOF] = (  # type: ignore[index]
                        disabled_bytes if "veof" in disabled_names else b"\x04"
                    )
                    seen: list[KeyKind] = []

                    def observe_transition(
                        state: TextViewerState,
                        event: KeyEvent,
                    ) -> tuple[TextViewerState, TextViewerResult | None]:
                        seen.append(event.kind)
                        return transition_text_viewer(state, event)

                    result = driver.read_text_viewer(
                        initialize_text_viewer("one", display_name="x"),
                        observe_transition,
                        transition_text_viewer_fallback,
                        project_text_viewer_viewport,
                        render_text_viewer,
                        render_text_viewer_fallback,
                    )
                    self.assertEqual(
                        result,
                        TextViewerResult(TextViewerExitKind.SELECTED, (1, 1)),
                    )
                    self.assertEqual(seen, [KeyKind.TEXT, KeyKind.ENTER])
                    fallback.assert_not_called()
                    self.assertEqual(events.count("os.fpathconf"), 1)
                    self.assertEqual(events.count("termios.restore"), 1)
                    rendered = "".join(output.records)
                    self.assertEqual(rendered.count("\x1b[?1049h"), 1)
                    self.assertEqual(rendered.count("\x1b[?1049l"), 1)

                    configuration = driver_module._terminal_configuration(
                        input_stream,
                        output,
                        termios_module=termios,
                        os_module=driver._os_provider(),
                        width=40,
                        decoder_spec=driver._input_latch.live_reader.spec,
                    )
                    assert configuration is not None
                    if "vintr" in disabled_names:
                        self.assertIsNone(configuration.interrupt_character)
                    else:
                        self.assertEqual(
                            driver_module._key_event("\x03", configuration),
                            KeyEvent(KeyKind.INTERRUPT),
                        )
                    if "veof" in disabled_names:
                        self.assertIsNone(configuration.eof_character)
                    else:
                        self.assertEqual(
                            driver_module._key_event("\x04", configuration),
                            KeyEvent(KeyKind.EOF),
                        )
                    self.assertEqual(
                        driver_module._key_event(
                            disabled_character,
                            configuration,
                        ),
                        KeyEvent(KeyKind.TEXT, text=disabled_character),
                    )
                    self.assertEqual(
                        driver_module._key_event("\n", configuration),
                        KeyEvent(KeyKind.ENTER),
                    )
                    self.assertEqual(
                        driver_module._key_event("\t", configuration),
                        KeyEvent(KeyKind.TAB),
                    )
                    self.assertEqual(
                        driver_module._key_event("\x7f", configuration),
                        KeyEvent(KeyKind.BACKSPACE),
                    )

    def test_pc_vdisable_query_failure_falls_back_before_activation(self) -> None:
        for disabled_control in (
            OSError("unavailable"),
            ValueError("query failed"),
        ):
            with self.subTest(error=type(disabled_control).__name__):
                driver, _input, output, fallback, _termios, events = _driver(
                    "",
                    answers=("1-1",),
                    disabled_control=disabled_control,
                )
                result = _read(
                    driver,
                    initialize_text_viewer("one", display_name="x"),
                )
                self.assertEqual(result.line_range, (1, 1))
                fallback.assert_called_once()
                self.assertEqual(events.count("os.fpathconf"), 1)
                self.assertNotIn("termios.activate", events)
                self.assertEqual(output.records, [])

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

    def test_configured_controls_precede_pending_escape_sequences(self) -> None:
        for introducer in ("\x1b[", "\x1bO"):
            for pending_label, pending in (
                ("short", introducer),
                ("overlong", introducer + "1" * 62),
            ):
                for label, character, expected in (
                    ("vintr", "\x03", TextViewerExitKind.INTERRUPTED),
                    ("veof", "\x04", TextViewerExitKind.EOF),
                ):
                    with self.subTest(
                        introducer=repr(introducer),
                        pending=pending_label,
                        control=label,
                    ):
                        driver, _input, output, fallback, termios, events = _driver(
                            pending + character
                        )
                        termios.original[6][termios.VINTR] = b"\x03"  # type: ignore[index]
                        termios.original[6][termios.VEOF] = b"\x04"  # type: ignore[index]
                        with mock.patch.object(
                            driver_module,
                            "_escape_input_is_ready",
                            return_value=True,
                        ) as readiness:
                            result = _read(
                                driver,
                                initialize_text_viewer("one\ntwo", display_name="x"),
                            )
                        self.assertEqual(result, TextViewerResult(expected))
                        readiness.assert_called_once_with(7)
                        fallback.assert_not_called()
                        self.assertEqual(
                            events.count("input.read"),
                            len(pending) + 1,
                        )
                        rendered = "".join(output.records)
                        self.assertEqual(rendered.count("\x1b[?1049h"), 1)
                        self.assertEqual(rendered.count("\x1b[?1049l"), 1)
                        self.assertEqual(events.count("termios.restore"), 1)

    def test_live_shift_sequences_resize_every_nonexit_and_restore_screen_once(self) -> None:
        driver, _input, output, fallback, termios, events = _driver(
            "\x1b[1;2B\x1b[1;2B\x1b[1;2Ax\n",
            sizes=((40, 8), (40, 8), (30, 6), (20, 5), (18, 4), (16, 3)),
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
        self.assertEqual(events.count("os.geometry"), 7)
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
        input_stream.read1 = mock.Mock(side_effect=RuntimeError("read"))  # type: ignore[method-assign]
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
            if geometry_calls > 2:
                raise OSError("resize unavailable")
            return os.terminal_size((40, 8))

        driver._os_provider().get_terminal_size = failing_geometry  # type: ignore[attr-defined]
        result = _read(driver, state)
        self.assertEqual(result.line_range, (1, 1))
        fallback.assert_not_called()
        self.assertEqual(geometry_calls, 3)
        self.assertEqual(events.count("termios.restore"), 1)
        self.assertEqual("".join(output.records).count("\x1b[?1049l"), 1)


if __name__ == "__main__":
    unittest.main()
