"""Cohesive contracts for semantic composer and confirmation input."""

from __future__ import annotations

import inspect
import os
import pickle
import re
import unittest
from dataclasses import FrozenInstanceError, fields
from types import SimpleNamespace
from typing import get_args
from unittest import mock

import story_writing_agents
from story_writing_agents import PLOT_ARCHITECT
from story_writing_agents import interactive_terminal as terminal_facade
from story_writing_agents.interactive_terminal import (
    ComposeMode,
    ConfirmationResult,
    InteractiveTerminal,
    TerminalAction,
    TerminalActionKind,
)
from story_writing_agents.interactive_terminal import (
    _controls,
    _core,
    _driver as driver_module,
    _model,
)
from story_writing_agents.interactive_terminal._controls import (
    append_main_input_history,
    transition_composer_line,
    transition_confirmation_answer,
    transition_line_input,
    transition_main_input,
)
from story_writing_agents.interactive_terminal._driver import RawTerminalDriver
from story_writing_agents.interactive_terminal._model import (
    CapabilityProfile,
    ComposerState,
    ControlOutcome,
    ControlOutcomeKind,
    Field,
    InputPromptView,
    KeyEvent,
    LineInputState,
    MainInputHistoryState,
    Notice,
    Panel,
    Prompt,
    RenderedInputFrame,
    Section,
    SemanticRole,
    TerminalCapabilities,
    ViewState,
    display_character_width,
)
from story_writing_agents.interactive_terminal._render import (
    render_component,
    render_input_prompt,
    strip_decorative_sgr,
)


def _capability(
    profile: CapabilityProfile,
    *,
    width: int = 80,
) -> TerminalCapabilities:
    """Build one correlated deterministic input-render capability."""

    raw = profile is not CapabilityProfile.LINE
    return TerminalCapabilities(
        profile,
        width,
        input_tty=raw,
        output_tty=raw,
        raw_mode=raw,
        cursor_controls=raw,
        decorative_sgr=profile is CapabilityProfile.ANSI,
    )


def _physical_width(text: str) -> int:
    """Measure one safe unstyled line using the production cell rule."""

    return sum(display_character_width(character) for character in text)


class _ScriptedInput:
    """Expose exact code-point reads and observable TTY capabilities."""

    def __init__(self, characters: str, *, is_terminal: bool = True) -> None:
        self.characters = list(characters)
        self.is_terminal = is_terminal
        self.read_calls = 0
        self.tty_calls = 0
        self.fileno_calls = 0

    def isatty(self) -> bool:
        self.tty_calls += 1
        return self.is_terminal

    def fileno(self) -> int:
        self.fileno_calls += 1
        return 7

    def read(self, size: int) -> str:
        if size != 1:
            raise AssertionError(f"unexpected read size: {size}")
        self.read_calls += 1
        if not self.characters:
            return ""
        return self.characters.pop(0)


class _RecordingOutput:
    """Record trusted redraw writes, flushes, and capability checks."""

    def __init__(self, *, is_terminal: bool = True) -> None:
        self.is_terminal = is_terminal
        self.records: list[str] = []
        self.flush_calls = 0
        self.write_failure: BaseException | None = None
        self.flush_failure: BaseException | None = None

    def isatty(self) -> bool:
        return self.is_terminal

    def fileno(self) -> int:
        return 8

    def write(self, text: str) -> int:
        self.records.append(text)
        if self.write_failure is not None:
            raise self.write_failure
        return len(text)

    def flush(self) -> None:
        self.flush_calls += 1
        if self.flush_failure is not None:
            raise self.flush_failure


class _FakeTermios:
    """Model exact activation and restoration around one semantic read."""

    ICANON = 0x02
    ECHO = 0x08
    VEOF = 0
    VINTR = 1
    VMIN = 4
    VTIME = 5
    TCSANOW = 10
    TCSADRAIN = 11

    def __init__(self) -> None:
        self.original = [
            0,
            0,
            0,
            self.ICANON | self.ECHO | 0x40,
            0,
            0,
            [b"\x04", b"\x03", b"x", b"x", 9, 9],
        ]
        self.get_calls: list[int] = []
        self.set_calls: list[tuple[int, int, list[object]]] = []
        self.activation_failure: BaseException | None = None
        self.restoration_failure: BaseException | None = None

    def tcgetattr(self, descriptor: int) -> list[object]:
        self.get_calls.append(descriptor)
        attributes = list(self.original)
        attributes[6] = list(self.original[6])  # type: ignore[arg-type]
        return attributes

    def tcsetattr(
        self,
        descriptor: int,
        when: int,
        attributes: list[object],
    ) -> None:
        copied = list(attributes)
        copied[6] = list(attributes[6])  # type: ignore[arg-type]
        self.set_calls.append((descriptor, when, copied))
        if when == self.TCSANOW and self.activation_failure is not None:
            raise self.activation_failure
        if when == self.TCSADRAIN and self.restoration_failure is not None:
            raise self.restoration_failure


def _driver(
    characters: str,
    *,
    fallback: mock.Mock | None = None,
    input_terminal: bool = True,
    output_terminal: bool = True,
    terminal_name: str = "xterm-256color",
    no_color: bool = False,
    width: int = 80,
    output: _RecordingOutput | None = None,
    termios_double: _FakeTermios | None = None,
) -> tuple[
    RawTerminalDriver,
    _ScriptedInput,
    _RecordingOutput,
    mock.Mock,
    _FakeTermios,
    list[int],
]:
    """Build one driver with deterministic capability and raw collaborators."""

    input_stream = _ScriptedInput(characters, is_terminal=input_terminal)
    output_stream = output or _RecordingOutput(is_terminal=output_terminal)
    fallback_read = fallback or mock.Mock(return_value="fallback")
    termios_value = termios_double or _FakeTermios()
    size_calls: list[int] = []
    environment = {"TERM": terminal_name}
    if no_color:
        environment["NO_COLOR"] = ""

    def get_terminal_size(descriptor: int) -> os.terminal_size:
        size_calls.append(descriptor)
        return os.terminal_size((width, 24))

    os_double = SimpleNamespace(
        name="posix",
        environ=environment,
        get_terminal_size=get_terminal_size,
    )
    driver = RawTerminalDriver(
        fallback_read,
        input_stream,  # type: ignore[arg-type]
        output_stream,  # type: ignore[arg-type]
        transition_line_input,
        transition_main_input,
        append_main_input_history,
        termios_provider=lambda: termios_value,
        os_provider=lambda: os_double,
    )
    return (
        driver,
        input_stream,
        output_stream,
        fallback_read,
        termios_value,
        size_calls,
    )


class SemanticInputModelTests(unittest.TestCase):
    """Lock the internal immutable schemas without expanding Component."""

    def test_models_are_internal_frozen_slotted_pickle_values(self) -> None:
        state = ComposerState(("界e\u0301", " second "))
        view = InputPromptView(
            (
                Panel("Context", (Field("Key", "Value"),)),
                Section("Details", (Notice("Ready"),)),
                Field("Lines", "2"),
                Notice("Guidance"),
            ),
            Prompt("compose[run:3]>")
        )
        frame = RenderedInputFrame("styled> ", "plain> ", 80, 1)

        self.assertEqual(
            tuple(field.name for field in fields(ComposerState)),
            ("lines", "content", "line_count", "character_count"),
        )
        self.assertEqual(state.content, "界e\u0301\n second ")
        self.assertEqual((state.line_count, state.character_count), (2, 12))
        self.assertEqual(
            tuple(field.name for field in fields(InputPromptView)),
            ("context", "prompt"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(RenderedInputFrame)),
            ("text", "plain_text", "width", "line_count"),
        )
        for value in (state, view, frame):
            with self.subTest(value=type(value).__name__):
                self.assertFalse(hasattr(value, "__dict__"))
                self.assertEqual(pickle.loads(pickle.dumps(value)), value)
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, fields(value)[0].name, None)

        self.assertEqual(
            tuple(item.value for item in ControlOutcomeKind),
            ("commit", "cancel", "interrupt", "eof", "rejected"),
        )
        self.assertNotIn(ComposerState, get_args(_model.Component))
        self.assertNotIn(InputPromptView, get_args(_model.Component))
        self.assertNotIn(RenderedInputFrame, get_args(_model.Component))
        for name in ("ComposerState", "InputPromptView", "RenderedInputFrame"):
            with self.subTest(name=name):
                self.assertNotIn(name, _model.__all__)
                self.assertFalse(hasattr(terminal_facade, name))
                self.assertFalse(hasattr(story_writing_agents, name))

    def test_models_reject_invalid_lines_context_focus_and_frames(self) -> None:
        invalid_calls = (
            lambda: ComposerState(["line"]),
            lambda: ComposerState((1,)),
            lambda: ComposerState(("two\nlines",)),
            lambda: InputPromptView([], Prompt("input")),
            lambda: InputPromptView((Prompt("nested"),), Prompt("input")),
            lambda: InputPromptView((), Notice("not prompt")),
            lambda: InputPromptView(
                (),
                Prompt("input", state=ViewState.IDLE),
            ),
            lambda: RenderedInputFrame("styled", "plain> ", 80, 1),
            lambda: RenderedInputFrame("styled> ", "plain", 80, 1),
            lambda: RenderedInputFrame("styled> \n", "plain> \n", 80, 2),
            lambda: RenderedInputFrame("styled> ", "plain> ", 0, 1),
            lambda: RenderedInputFrame("styled> ", "plain> ", 80, 2),
            lambda: RenderedInputFrame("styled> ", "plain\x1b> ", 80, 1),
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises((TypeError, ValueError)):
                    call()  # type: ignore[misc]


class PureComposerControlTests(unittest.TestCase):
    """Verify exact line-oriented composition and confirmation transitions."""

    def test_composer_send_cancel_say_near_matches_and_counts_are_exact(self) -> None:
        state = ComposerState()
        ordinary_lines = (
            "",
            " \t",
            "/send ",
            " /send",
            "/SEND",
            "/cancel ",
            " /cancel",
            "/CANCEL",
            "/say  /send",
            "/say /send extra",
            "한界e\u0301",
        )
        for line in ordinary_lines:
            state, outcome = transition_composer_line(state, line)
            self.assertIsNone(outcome)
        state, outcome = transition_composer_line(state, "/say /send")
        self.assertIsNone(outcome)
        state, outcome = transition_composer_line(state, "/say /cancel")
        self.assertIsNone(outcome)
        expected_lines = (*ordinary_lines, "/send", "/cancel")
        expected_content = "\n".join(expected_lines)
        self.assertEqual(state, ComposerState(expected_lines))
        self.assertEqual(state.content, expected_content)
        self.assertEqual(state.line_count, len(expected_lines))
        self.assertEqual(state.character_count, len(expected_content))

        unchanged, outcome = transition_composer_line(state, "/send")
        self.assertIs(unchanged, state)
        self.assertEqual(
            outcome,
            ControlOutcome(ControlOutcomeKind.COMMIT, text=expected_content),
        )
        unchanged, outcome = transition_composer_line(state, "/cancel")
        self.assertIs(unchanged, state)
        self.assertEqual(outcome, ControlOutcome(ControlOutcomeKind.CANCEL))

        whitespace = ComposerState(("", " \t"))
        unchanged, outcome = transition_composer_line(whitespace, "/send")
        self.assertIs(unchanged, whitespace)
        self.assertEqual(outcome, ControlOutcome(ControlOutcomeKind.REJECTED))

    def test_controls_normalize_safe_string_subclasses_and_reject_bad_shapes(self) -> None:
        class HostileString(str):
            def __str__(self) -> str:
                raise AssertionError("overridden __str__ must not run")

            def __contains__(self, _item: object) -> bool:
                raise AssertionError("overridden __contains__ must not run")

        state, outcome = transition_composer_line(
            ComposerState(),
            HostileString("literal"),
        )
        self.assertEqual(state, ComposerState(("literal",)))
        self.assertIs(type(state.lines[0]), str)
        self.assertIsNone(outcome)
        self.assertEqual(
            transition_confirmation_answer(
                HostileString(" YeS "),
                default=False,
            ),
            ControlOutcome(ControlOutcomeKind.COMMIT, text="yes"),
        )
        for call in (
            lambda: transition_composer_line(object(), "line"),
            lambda: transition_composer_line(ComposerState(), object()),
            lambda: transition_composer_line(ComposerState(), "two\nlines"),
            lambda: transition_confirmation_answer(object(), default=True),
            lambda: transition_confirmation_answer("yes\r", default=True),
            lambda: transition_confirmation_answer("yes", default=1),
        ):
            with self.subTest(call=call):
                with self.assertRaises((TypeError, ValueError)):
                    call()  # type: ignore[misc]

    def test_confirmation_matrix_returns_only_canonical_commits_or_rejection(
        self,
    ) -> None:
        cases = (
            ("", True, "yes"),
            (" \t\u2003", False, "no"),
            ("Y", False, "yes"),
            (" yes ", False, "yes"),
            ("N", True, "no"),
            (" nO ", True, "no"),
        )
        for answer, default, expected in cases:
            with self.subTest(answer=answer, default=default):
                self.assertEqual(
                    transition_confirmation_answer(answer, default=default),
                    ControlOutcome(ControlOutcomeKind.COMMIT, text=expected),
                )
        for answer in ("maybe", "YES!", "y es", "oui", "0"):
            with self.subTest(answer=answer):
                self.assertEqual(
                    transition_confirmation_answer(answer, default=True),
                    ControlOutcome(ControlOutcomeKind.REJECTED),
                )


class SemanticInputRenderingTests(unittest.TestCase):
    """Lock semantic tree order and safe plain/ANSI/narrow projections."""

    def test_core_projects_exact_composer_and_confirmation_trees(self) -> None:
        self.assertEqual(
            _core._composer_input_view(ComposeMode.RUN, None, ComposerState()),
            InputPromptView(
                (
                    Field("Mode", "run"),
                    Field("Next line", "1"),
                    Field("Lines", "0"),
                    Field("Characters", "0"),
                    Notice("Use /send to submit or /cancel to discard."),
                ),
                Prompt("compose[run:1]>"),
            ),
        )
        self.assertEqual(
            _core._composer_input_view(
                ComposeMode.CHAT,
                "plot",
                ComposerState(("界e\u0301",)),
            ),
            InputPromptView(
                (
                    Field("Mode", "chat"),
                    Field("Target", "plot"),
                    Field("Next line", "2"),
                    Field("Lines", "1"),
                    Field("Characters", "3"),
                    Notice("Use /send to submit or /cancel to discard."),
                ),
                Prompt("compose[chat:plot:2]>"),
            ),
        )
        self.assertEqual(
            _core._confirmation_input_view(
                "Continue?",
                default=True,
                invalid=False,
            ),
            InputPromptView(
                (
                    Field("Question", "Continue?"),
                    Field("Choices", "y/yes or n/no"),
                    Field("Default", "yes"),
                ),
                Prompt("confirm [Y/n]>")
            ),
        )
        self.assertEqual(
            _core._confirmation_input_view(
                "Continue?",
                default=False,
                invalid=True,
            ).context[1],
            Field(
                "Choices",
                "y/yes or n/no",
                role=SemanticRole.ERROR,
                state=ViewState.ERROR,
            ),
        )

    def test_wide_plain_ansi_no_color_and_narrow_bytes_are_exact(self) -> None:
        view = _core._composer_input_view(
            ComposeMode.RUN,
            None,
            ComposerState(),
        )
        plain = render_input_prompt(
            view,
            _capability(CapabilityProfile.LINE),
        )
        no_color = render_input_prompt(
            view,
            _capability(CapabilityProfile.PLAIN),
        )
        ansi = render_input_prompt(
            view,
            _capability(CapabilityProfile.ANSI),
        )
        expected_plain = (
            "Mode: run\n"
            "Next line: 1\n"
            "Lines: 0\n"
            "Characters: 0\n"
            "[info] Use /send to submit or /cancel to discard.\n"
            "compose[run:1]> "
        )
        self.assertEqual(plain, RenderedInputFrame(expected_plain, expected_plain, 80, 6))
        self.assertEqual(no_color.text, expected_plain)
        self.assertEqual(no_color.plain_text, expected_plain)
        self.assertEqual(
            ansi.text,
            "Mode: run\n"
            "Next line: 1\n"
            "Lines: 0\n"
            "Characters: 0\n"
            "\x1b[36m[info] Use /send to submit or /cancel to discard.\x1b[0m\n"
            "\x1b[35mcompose[run:1]>\x1b[0m ",
        )
        self.assertEqual(strip_decorative_sgr(ansi.text), expected_plain)
        self.assertFalse(ansi.text.endswith("\n"))
        self.assertTrue(ansi.text.endswith(" "))

        narrow = render_input_prompt(
            view,
            _capability(CapabilityProfile.LINE, width=20),
        )
        self.assertEqual(
            narrow.text,
            "Mode:\n"
            " run\n"
            "Next line:\n"
            " 1\n"
            "Lines:\n"
            " 0\n"
            "Characters:\n"
            " 0\n"
            "[info] Use /send to \n"
            "       submit or /ca\n"
            "       ncel to disca\n"
            "       rd.\n"
            "compose[run:1]> ",
        )
        self.assertEqual(narrow.line_count, 13)
        for line in narrow.text.split("\n"):
            self.assertLessEqual(_physical_width(line), 20)

    def test_hostile_text_is_sanitized_before_fixed_local_styling(self) -> None:
        view = InputPromptView(
            (
                Field("Wide", "界e\u0301\x1b]0;owned\x07\u202e"),
                Notice(
                    "bad\x1b[31m text",
                    role=SemanticRole.ERROR,
                    state=ViewState.ERROR,
                ),
            ),
            Prompt("ask\x1b[2J>"),
        )
        plain = render_input_prompt(
            view,
            _capability(CapabilityProfile.LINE, width=20),
        )
        ansi = render_input_prompt(
            view,
            _capability(CapabilityProfile.ANSI, width=20),
        )
        self.assertEqual(strip_decorative_sgr(ansi.text), plain.text)
        self.assertIn(r"\x1b]0;owned\x07", plain.text)
        self.assertIn(r"\u202e", plain.text)
        self.assertIn(r"bad\x1b[31m", plain.text)
        self.assertIn("text", plain.text)
        self.assertIn(r"ask\x1b[2J>", plain.text)
        self.assertNotIn("\x07", plain.text)
        allowed = {"\x1b[0m", "\x1b[1m", "\x1b[31m", "\x1b[35m"}
        sequences = re.findall(r"\x1b\[[0-9;]*[A-Za-z]", ansi.text)
        self.assertTrue(sequences)
        self.assertTrue(set(sequences).issubset(allowed))
        for line in strip_decorative_sgr(ansi.text).split("\n"):
            self.assertLessEqual(_physical_width(line), 20)

    def test_renderer_rejects_non_views_without_changing_generic_prompt(self) -> None:
        with self.assertRaisesRegex(TypeError, "InputPromptView"):
            render_input_prompt(
                object(),  # type: ignore[arg-type]
                _capability(CapabilityProfile.LINE),
            )
        with self.assertRaisesRegex(TypeError, "TerminalCapabilities"):
            render_input_prompt(
                InputPromptView((), Prompt("input>")),
                object(),  # type: ignore[arg-type]
            )
        generic = Prompt("input")
        self.assertEqual(
            render_component(generic, _capability(CapabilityProfile.LINE)).text,
            "input [focused] >",
        )


class SemanticInputDriverTests(unittest.TestCase):
    """Verify renderer injection, safe fallback, live trust, and cleanup."""

    def test_signature_and_line_fallback_render_once_without_history(self) -> None:
        self.assertEqual(
            tuple(inspect.signature(RawTerminalDriver.read_input_line).parameters),
            ("self", "view", "renderer", "suggestion_provider", "transient"),
        )
        signature = inspect.signature(RawTerminalDriver.read_input_line)
        self.assertIs(
            signature.parameters["transient"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        self.assertIs(signature.parameters["transient"].default, False)
        fallback = mock.Mock(return_value=" exact fallback ")
        driver, input_stream, output, _fallback, termios_double, size_calls = _driver(
            "unused",
            fallback=fallback,
            terminal_name="dumb",
        )
        view = InputPromptView((Field("Question", "Continue?"),), Prompt("confirm>"))
        renderer = mock.Mock(
            return_value=RenderedInputFrame(
                "\x1b[35mconfirm>\x1b[0m ",
                "confirm> ",
                80,
                1,
            )
        )
        provider = mock.Mock(return_value=())

        self.assertEqual(
            driver.read_input_line(view, renderer, provider),
            " exact fallback ",
        )
        renderer.assert_called_once()
        self.assertIs(renderer.call_args.args[0], view)
        self.assertIs(renderer.call_args.args[1].profile, CapabilityProfile.LINE)
        fallback.assert_called_once_with("confirm> ")
        provider.assert_not_called()
        self.assertEqual(input_stream.read_calls, 0)
        self.assertEqual(output.records, [])
        self.assertEqual(termios_double.get_calls, [])
        self.assertEqual(size_calls, [8])
        self.assertEqual(driver._history_state, MainInputHistoryState())

    def test_transient_live_input_clears_commit_events_and_provider_failure(
        self,
    ) -> None:
        view = InputPromptView((), Prompt("input>"))
        cases: tuple[tuple[str, object], ...] = (
            ("visible\n", "visible"),
            ("\x03", KeyboardInterrupt),
            ("\x04", EOFError),
            ("", EOFError),
        )
        for characters, expected in cases:
            with self.subTest(characters=repr(characters)):
                driver, _input, output, fallback, termios_double, _size = _driver(
                    characters
                )
                if isinstance(expected, str):
                    self.assertEqual(
                        driver.read_input_line(
                            view,
                            render_input_prompt,
                            lambda _buffer: (),
                            transient=True,
                        ),
                        expected,
                    )
                    self.assertEqual(output.records[-1], "\r\r\x1b[J")
                else:
                    with self.assertRaises(expected):  # type: ignore[arg-type]
                        driver.read_input_line(
                            view,
                            render_input_prompt,
                            lambda _buffer: (),
                            transient=True,
                        )
                    self.assertEqual(output.records[-1], "\r\r\x1b[J\n")
                fallback.assert_not_called()
                self.assertEqual(
                    [when for _fd, when, _attrs in termios_double.set_calls],
                    [termios_double.TCSANOW, termios_double.TCSADRAIN],
                )
                self.assertEqual(driver._history_state, MainInputHistoryState())

        driver, _input, output, fallback, termios_double, _size = _driver("x")

        def failing_provider(buffer: str) -> tuple[object, ...]:
            if buffer:
                raise RuntimeError("preview failed")
            return ()

        with self.assertRaisesRegex(RuntimeError, "^preview failed$"):
            driver.read_input_line(
                view,
                render_input_prompt,
                failing_provider,  # type: ignore[arg-type]
                transient=True,
            )
        self.assertEqual(output.records[-1], "\r\r\x1b[J\n")
        fallback.assert_not_called()
        self.assertEqual(
            [when for _fd, when, _attrs in termios_double.set_calls],
            [termios_double.TCSANOW, termios_double.TCSADRAIN],
        )
        self.assertEqual(driver._history_state, MainInputHistoryState())

    def test_transient_prompt_clears_typed_outcomes_and_transition_failure(
        self,
    ) -> None:
        view = InputPromptView((), Prompt("prompt>"))
        cases = (
            ("value\n", ControlOutcome(ControlOutcomeKind.COMMIT, text="value")),
            ("\x1b", ControlOutcome(ControlOutcomeKind.CANCEL)),
            ("\x03", ControlOutcome(ControlOutcomeKind.INTERRUPT)),
            ("\x04", ControlOutcome(ControlOutcomeKind.EOF)),
        )
        for characters, expected in cases:
            with self.subTest(characters=repr(characters)):
                driver, _input, output, fallback, termios_double, _size = _driver(
                    characters
                )
                readiness = (
                    mock.patch.object(
                        driver_module._select,
                        "select",
                        return_value=((), (), ()),
                    )
                    if characters == "\x1b"
                    else mock.patch.object(
                        driver_module._select,
                        "select",
                        wraps=driver_module._select.select,
                    )
                )
                with readiness:
                    self.assertEqual(
                        driver.read_prompt(
                            view,
                            render_input_prompt,
                            _controls.transition_prompt_input,
                            transient=True,
                        ),
                        expected,
                    )
                self.assertIn("\x1b[J", output.records[-1])
                fallback.assert_not_called()
                self.assertEqual(
                    [when for _fd, when, _attrs in termios_double.set_calls],
                    [termios_double.TCSANOW, termios_double.TCSADRAIN],
                )

        driver, _input, output, fallback, termios_double, _size = _driver("x")

        def failing_transition(
            _state: LineInputState,
            _event: KeyEvent,
        ) -> tuple[LineInputState, ControlOutcome | None]:
            raise RuntimeError("prompt transition failed")

        with self.assertRaisesRegex(RuntimeError, "^prompt transition failed$"):
            driver.read_prompt(
                view,
                render_input_prompt,
                failing_transition,
                transient=True,
            )
        self.assertEqual(output.records[-1], "\r\r\x1b[J\n")
        fallback.assert_not_called()
        self.assertEqual(
            [when for _fd, when, _attrs in termios_double.set_calls],
            [termios_double.TCSANOW, termios_double.TCSADRAIN],
        )

    def test_live_input_uses_trusted_ansi_plain_cursor_and_reserved_noops(self) -> None:
        reserved = "\x1b[A\x1b[B\t\x1b[Z\x1b"
        driver, _input, output, fallback, termios_double, size_calls = _driver(
            f"{reserved}界e\u0301\n",
        )
        view = _core._composer_input_view(ComposeMode.RUN, None, ComposerState())
        renderer = mock.Mock(wraps=render_input_prompt)
        provider = mock.Mock(return_value=())

        with mock.patch.object(
            driver_module._select,
            "select",
            side_effect=(
                ((7,), (), ()),
                ((7,), (), ()),
                ((7,), (), ()),
                ((), (), ()),
            ),
        ) as readiness:
            self.assertEqual(
                driver.read_input_line(view, renderer, provider),
                "界e\u0301",
            )
        self.assertEqual(
            readiness.call_args_list,
            [mock.call((7,), (), (), 0.05)] * 4,
        )
        renderer.assert_called_once()
        self.assertIs(renderer.call_args.args[1].profile, CapabilityProfile.ANSI)
        self.assertEqual(size_calls, [8])
        fallback.assert_not_called()
        self.assertEqual(driver._history_state, MainInputHistoryState())
        self.assertEqual(
            [when for _descriptor, when, _attributes in termios_double.set_calls],
            [termios_double.TCSANOW, termios_double.TCSADRAIN],
        )
        rendered = "".join(output.records)
        self.assertIn("\x1b[35mcompose[run:1]>\x1b[0m ", rendered)
        self.assertNotIn(r"\x1b[35mcompose", rendered)
        self.assertIn("界e\u0301", rendered)
        initial_calls = [call for call in provider.call_args_list if call == mock.call("")]
        self.assertEqual(len(initial_calls), 1)

    def test_vintr_veof_physical_eof_and_output_failures_restore(self) -> None:
        cases: tuple[tuple[str, object], ...] = (
            ("visible\x04", "visible"),
            ("\x04", EOFError),
            ("\x03", KeyboardInterrupt),
            ("", EOFError),
        )
        view = InputPromptView((), Prompt("input>"))
        for characters, expected in cases:
            with self.subTest(characters=repr(characters)):
                driver, _input, _output, fallback, termios_double, _size = _driver(
                    characters
                )
                if isinstance(expected, str):
                    self.assertEqual(
                        driver.read_input_line(view, render_input_prompt, lambda _buffer: ()),
                        expected,
                    )
                else:
                    with self.assertRaises(expected):  # type: ignore[arg-type]
                        driver.read_input_line(
                            view,
                            render_input_prompt,
                            lambda _buffer: (),
                        )
                fallback.assert_not_called()
                self.assertEqual(
                    [when for _fd, when, _attrs in termios_double.set_calls],
                    [termios_double.TCSANOW, termios_double.TCSADRAIN],
                )
                self.assertEqual(driver._history_state, MainInputHistoryState())

        output = _RecordingOutput()
        output.write_failure = OSError("write failed")
        driver, _input, _output, fallback, termios_double, _size = _driver(
            "line\n",
            output=output,
        )
        with self.assertRaisesRegex(OSError, "write failed"):
            driver.read_input_line(view, render_input_prompt, lambda _buffer: ())
        fallback.assert_not_called()
        self.assertEqual(
            [when for _fd, when, _attrs in termios_double.set_calls],
            [termios_double.TCSANOW, termios_double.TCSADRAIN],
        )

        output = _RecordingOutput()
        output.flush_failure = OSError("flush failed")
        driver, _input, _output, fallback, termios_double, _size = _driver(
            "line\n",
            output=output,
        )
        with self.assertRaisesRegex(OSError, "flush failed"):
            driver.read_input_line(view, render_input_prompt, lambda _buffer: ())
        fallback.assert_not_called()
        self.assertEqual(
            [when for _fd, when, _attrs in termios_double.set_calls],
            [termios_double.TCSANOW, termios_double.TCSADRAIN],
        )

    def test_renderer_setup_and_restoration_failures_keep_exact_precedence(self) -> None:
        view = InputPromptView((), Prompt("input>"))
        driver, input_stream, output, fallback, termios_double, _size = _driver(
            "unused"
        )
        renderer = mock.Mock(side_effect=RuntimeError("render failed"))
        with self.assertRaisesRegex(RuntimeError, "render failed"):
            driver.read_input_line(view, renderer, lambda _buffer: ())
        self.assertEqual(input_stream.read_calls, 0)
        self.assertEqual(output.records, [])
        self.assertEqual(termios_double.set_calls, [])
        fallback.assert_not_called()

        termios_double = _FakeTermios()
        termios_double.activation_failure = OSError("activate failed")
        fallback = mock.Mock(return_value="fallback once")
        driver, input_stream, _output, _fallback, termios_double, _size = _driver(
            "unused",
            fallback=fallback,
            termios_double=termios_double,
        )
        self.assertEqual(
            driver.read_input_line(view, render_input_prompt, lambda _buffer: ()),
            "fallback once",
        )
        self.assertEqual(input_stream.read_calls, 0)
        fallback.assert_called_once()
        self.assertEqual(
            [when for _fd, when, _attrs in termios_double.set_calls],
            [termios_double.TCSANOW, termios_double.TCSADRAIN],
        )

        termios_double = _FakeTermios()
        termios_double.restoration_failure = OSError("restore secret")
        driver, _input, _output, fallback, _termios, _size = _driver(
            "\x03",
            termios_double=termios_double,
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "^interactive terminal restoration failed$",
        ) as caught:
            driver.read_input_line(view, render_input_prompt, lambda _buffer: ())
        self.assertIs(caught.exception.__cause__, termios_double.restoration_failure)
        fallback.assert_not_called()

    def test_bad_renderer_provider_and_width_results_do_not_mutate_history(self) -> None:
        view = InputPromptView((), Prompt("input>"))
        driver, input_stream, output, fallback, termios_double, size_calls = _driver(
            "unused"
        )
        rendered_input = RenderedInputFrame("input> ", "input> ", 80, 1)
        provider = mock.Mock(return_value=())
        transition = mock.Mock()
        with self.assertRaisesRegex(
            ValueError,
            "^known_width is required with rendered_input$",
        ):
            driver._read_control_line(
                rendered_input.plain_text,
                provider,
                _model.LineInputState(),
                transition,
                rendered_input=rendered_input,
            )
        provider.assert_not_called()
        transition.assert_not_called()
        fallback.assert_not_called()
        self.assertEqual(input_stream.read_calls, 0)
        self.assertEqual(input_stream.tty_calls, 0)
        self.assertEqual(input_stream.fileno_calls, 0)
        self.assertEqual(output.records, [])
        self.assertEqual(output.flush_calls, 0)
        self.assertEqual(termios_double.get_calls, [])
        self.assertEqual(termios_double.set_calls, [])
        self.assertEqual(size_calls, [])
        self.assertEqual(driver._history_state, MainInputHistoryState())

        cases = (
            (mock.Mock(return_value=object()), lambda _buffer: (), TypeError),
            (
                mock.Mock(
                    return_value=RenderedInputFrame("input> ", "input> ", 79, 1)
                ),
                lambda _buffer: (),
                ValueError,
            ),
            (render_input_prompt, lambda _buffer: (object(),), TypeError),
        )
        for renderer, provider, expected in cases:
            with self.subTest(expected=expected.__name__):
                driver, _input, _output, fallback, _termios, _size = _driver("x\n")
                with self.assertRaises(expected):
                    driver.read_input_line(view, renderer, provider)  # type: ignore[arg-type]
                self.assertEqual(driver._history_state, MainInputHistoryState())
                fallback.assert_not_called()


class SemanticComposerCoreTests(unittest.TestCase):
    """Lock exact semantic events, callback counts, and reader routing."""

    def test_composer_events_are_exact_components_and_one_write_each(self) -> None:
        reads = iter(("", " \t", "/send", "message", "/send"))
        prompts: list[str] = []
        records: list[str] = []

        def read_line(prompt: str) -> str:
            prompts.append(prompt)
            return next(reads)

        with mock.patch.object(
            _core,
            "render_component",
            wraps=render_component,
        ) as render:
            action = InteractiveTerminal(read_line, records.append).compose(ComposeMode.RUN)

        self.assertEqual(
            action,
            TerminalAction(
                TerminalActionKind.SEND,
                text="\n \t\nmessage",
                line_count=3,
                character_count=11,
            ),
        )
        self.assertEqual(len(prompts), 5)
        self.assertEqual(len(records), 3)
        self.assertEqual(
            [call.args[0] for call in render.call_args_list],
            [
                Notice(
                    "Consensus run. Use /send to submit or /cancel to discard.",
                    label="compose",
                    role=SemanticRole.INFO,
                    state=ViewState.OPEN,
                ),
                Notice(
                    "Nothing to send.",
                    label="compose",
                    role=SemanticRole.ERROR,
                    state=ViewState.ERROR,
                ),
                Notice(
                    "Sending 3 lines, 11 characters to consensus.",
                    label="compose",
                    role=SemanticRole.SUCCESS,
                    state=ViewState.COMPLETED,
                ),
            ],
        )
        self.assertEqual(
            records,
            [
                "[compose] Consensus run. Use /send to submit or /cancel to discard.\n",
                "[compose] Nothing to send.\n",
                "[compose] Sending 3 lines, 11 characters to consensus.\n",
            ],
        )

    def test_chat_cancel_interrupt_eof_and_writer_failures_preserve_boundaries(
        self,
    ) -> None:
        cases = (
            ("cancel", ("draft", "/cancel"), TerminalActionKind.CANCEL),
            ("interrupt", ("draft", KeyboardInterrupt()), TerminalActionKind.INTERRUPT),
            ("eof", ("draft", EOFError()), TerminalActionKind.EOF),
        )
        for label, values, kind in cases:
            with self.subTest(label=label):
                reads = list(values)
                records: list[str] = []

                def read_line(_prompt: str) -> str:
                    value = reads.pop(0)
                    if isinstance(value, BaseException):
                        raise value
                    return value

                terminal = InteractiveTerminal(read_line, records.append)
                with mock.patch.object(
                    _core,
                    "render_component",
                    wraps=render_component,
                ) as render:
                    action = terminal.compose(
                        ComposeMode.CHAT,
                        agent_id=PLOT_ARCHITECT,
                    )
                self.assertEqual(
                    action,
                    TerminalAction(kind, line_count=1, character_count=5),
                )
                self.assertEqual(len(records), 2)
                self.assertEqual(
                    records[-1],
                    "[compose] Discarded 1 line. No message was saved or sent.\n",
                )
                self.assertEqual(
                    render.call_args_list[-1].args[0],
                    Notice(
                        "Discarded 1 line. No message was saved or sent.",
                        label="compose",
                        role=SemanticRole.WARNING,
                        state=ViewState.CANCELLED,
                    ),
                )

        reads = mock.Mock(return_value="unused")
        writer = mock.Mock(side_effect=RuntimeError("opening write failed"))
        with self.assertRaisesRegex(RuntimeError, "opening write failed"):
            InteractiveTerminal(reads, writer).compose(ComposeMode.RUN)
        reads.assert_not_called()
        writer.assert_called_once()

        reads = mock.Mock(side_effect=("message", "/send"))
        writer_calls = 0

        def fail_final(_text: str) -> None:
            nonlocal writer_calls
            writer_calls += 1
            if writer_calls == 2:
                raise RuntimeError("final write failed")

        with self.assertRaisesRegex(RuntimeError, "final write failed"):
            InteractiveTerminal(reads, fail_final).compose(ComposeMode.RUN)
        self.assertEqual(reads.call_count, 2)
        self.assertEqual(writer_calls, 2)

    def test_confirmation_attempts_hint_tree_and_no_final_event_are_exact(self) -> None:
        reads = iter(("later", " MAYBE ", "yes"))
        prompts: list[str] = []
        records: list[str] = []

        def read_line(prompt: str) -> str:
            prompts.append(prompt)
            return next(reads)

        with mock.patch.object(
            _core,
            "render_component",
            wraps=render_component,
        ) as render:
            result = InteractiveTerminal(read_line, records.append).request_confirmation(
                "Resume?",
                default=False,
            )
        self.assertIs(result, ConfirmationResult.YES)
        self.assertEqual(len(prompts), 3)
        self.assertEqual(
            prompts[0],
            "Question: Resume?\n"
            "Choices: y/yes or n/no\n"
            "Default: no\n"
            "confirm [y/N]> ",
        )
        self.assertEqual(
            prompts[1:],
            [
                "Question: Resume?\n"
                "Choices [error]: y/yes or n/no\n"
                "Default: no\n"
                "confirm [y/N]> ",
            ]
            * 2,
        )
        expected_hint = Notice(
            "Enter y, yes, n, or no.",
            label="hint",
            role=SemanticRole.ERROR,
            state=ViewState.ERROR,
        )
        self.assertEqual(
            [call.args[0] for call in render.call_args_list],
            [expected_hint, expected_hint],
        )
        self.assertEqual(
            records,
            ["[hint] Enter y, yes, n, or no.\n"] * 2,
        )

        for value, expected in (
            ("yes", ConfirmationResult.YES),
            (KeyboardInterrupt(), ConfirmationResult.INTERRUPT),
            (EOFError(), ConfirmationResult.EOF),
        ):
            with self.subTest(value=repr(value)):
                records = []

                def one_read(_prompt: str, value: object = value) -> str:
                    if isinstance(value, BaseException):
                        raise value
                    return value  # type: ignore[return-value]

                result = InteractiveTerminal(one_read, records.append).request_confirmation(
                    "Continue?",
                    default=True,
                )
                self.assertIs(result, expected)
                self.assertEqual(records, [])

    def test_contextual_and_stream_factories_keep_explicit_reader_roles(self) -> None:
        ordinary = mock.Mock(side_effect=("subject", "yes"))
        main = mock.Mock(return_value="/status")
        composition = mock.Mock(side_effect=("line", "/send"))
        records: list[str] = []
        terminal = _core.build_contextual_terminal(
            ordinary,
            records.append,
            main_read_line=main,
            composition_read_line=composition,
        )
        self.assertEqual(
            terminal.read_main(),
            TerminalAction(TerminalActionKind.LINE, text="/status"),
        )
        self.assertEqual(
            terminal.compose(ComposeMode.RUN),
            TerminalAction(
                TerminalActionKind.SEND,
                text="line",
                line_count=1,
                character_count=4,
            ),
        )
        self.assertEqual(
            terminal.request_conversation_subject(),
            TerminalAction(TerminalActionKind.LINE, text="subject"),
        )
        self.assertIs(
            terminal.request_confirmation("Continue?", default=False),
            ConfirmationResult.YES,
        )
        main.assert_called_once_with("story[lobby]> ")
        self.assertEqual(composition.call_count, 2)
        self.assertEqual(ordinary.call_count, 2)
        self.assertTrue(composition.call_args_list[0].args[0].endswith("compose[run:1]> "))
        self.assertTrue(ordinary.call_args_list[0].args[0].endswith("conversation[subject]> "))
        self.assertTrue(ordinary.call_args_list[1].args[0].endswith("confirm [y/N]> "))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
