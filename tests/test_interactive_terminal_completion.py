"""Deterministic contracts for selectable main-command completion."""

from __future__ import annotations

import inspect
import pickle
import re
import unittest
from dataclasses import FrozenInstanceError, fields
from types import SimpleNamespace
from typing import get_args
from unittest import mock

import story_writing_agents
from story_writing_agents import interactive_terminal as terminal_facade
from story_writing_agents.interactive_terminal import _controls, _core, _driver, _model
from story_writing_agents.interactive_terminal._controls import (
    append_main_input_history,
    project_main_suggestion_palette,
    transition_line_input,
    transition_main_command_input,
)
from story_writing_agents.interactive_terminal._driver import RawTerminalDriver
from story_writing_agents.interactive_terminal._model import (
    CapabilityProfile,
    Choice,
    CommandCompletionCandidate,
    CommandCompletionProjection,
    Component,
    ControlOutcomeKind,
    KeyEvent,
    KeyKind,
    LineInputState,
    MainCommandControlState,
    MainCommandPaletteState,
    MainInputControlState,
    MainInputHistoryState,
    RenderedFrame,
    SuggestionPalette,
    TerminalCapabilities,
    ViewState,
)
from story_writing_agents.interactive_terminal._render import (
    render_component,
    strip_decorative_sgr,
)


ROOT_REPLACEMENTS = (
    "/say",
    "/to",
    "/agent",
    "/agents",
    "/language",
    "/persona",
    "/reset",
    "/settings",
    "/run",
    "/draft-file",
    "/session",
    "/conversation",
    "/search",
    "/transcript",
    "/inspect",
    "/history",
    "/status",
    "/help",
    "/?",
    "/quit",
    "/exit",
)


def capabilities(
    profile: CapabilityProfile = CapabilityProfile.ANSI,
    width: int = 80,
) -> TerminalCapabilities:
    """Build one internally consistent deterministic capability profile."""

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


class ScriptedInput:
    """Expose exact code points through one terminal descriptor."""

    def __init__(self, text: str, *, terminal: bool = True) -> None:
        self.reads = list(text)
        self.terminal = terminal
        self.calls: list[object] = []

    def isatty(self) -> bool:
        self.calls.append("isatty")
        return self.terminal

    def fileno(self) -> int:
        self.calls.append("fileno")
        return 7

    def read(self, size: int) -> str:
        self.calls.append(("read", size))
        return self.reads.pop(0) if self.reads else ""


class RecordingOutput:
    """Record the exact live redraw and flush boundaries."""

    def __init__(self, *, terminal: bool = True) -> None:
        self.terminal = terminal
        self.records: list[str] = []
        self.flushes = 0

    def isatty(self) -> bool:
        return self.terminal

    def fileno(self) -> int:
        return 8

    def write(self, text: str) -> int:
        self.records.append(text)
        return len(text)

    def flush(self) -> None:
        self.flushes += 1


class FakeTermios:
    """Model raw activation and exact restoration without a real TTY."""

    ICANON = 2
    ECHO = 8
    VEOF = 0
    VINTR = 1
    VMIN = 4
    VTIME = 5
    TCSANOW = 10
    TCSADRAIN = 11

    def __init__(self) -> None:
        self.original = [0, 0, 0, 10, 0, 0, [b"\x04", b"\x03", 0, 0, 9, 9]]
        self.set_calls: list[tuple[int, int, list[object]]] = []

    def tcgetattr(self, descriptor: int) -> list[object]:
        attributes = list(self.original)
        attributes[6] = list(self.original[6])  # type: ignore[arg-type]
        return attributes

    def tcsetattr(self, descriptor: int, when: int, attributes: list[object]) -> None:
        copied = list(attributes)
        copied[6] = list(attributes[6])  # type: ignore[arg-type]
        self.set_calls.append((descriptor, when, copied))


def make_driver(
    text: str,
    *,
    terminal: bool = True,
) -> tuple[
    RawTerminalDriver,
    ScriptedInput,
    RecordingOutput,
    mock.Mock,
    FakeTermios,
    SimpleNamespace,
]:
    """Build one driver with observable completion collaborators."""

    input_stream = ScriptedInput(text, terminal=terminal)
    output_stream = RecordingOutput(terminal=terminal)
    fallback = mock.Mock(return_value="fallback")
    termios = FakeTermios()
    observed_widths: list[int] = []

    def get_terminal_size(descriptor: int) -> object:
        observed_widths.append(descriptor)
        return SimpleNamespace(columns=40, lines=24)

    os_module = SimpleNamespace(
        name="posix" if terminal else "nt",
        environ={"TERM": "xterm-256color"},
        get_terminal_size=get_terminal_size,
        observed_widths=observed_widths,
    )
    driver = RawTerminalDriver(
        fallback,
        input_stream,  # type: ignore[arg-type]
        output_stream,  # type: ignore[arg-type]
        transition_line_input,
        transition_main_command_input,
        append_main_input_history,
        termios_provider=lambda: termios if terminal else None,
        os_provider=lambda: os_module,
    )
    return driver, input_stream, output_stream, fallback, termios, os_module


class CompletionModelContractTests(unittest.TestCase):
    """Lock immutable internal completion state and its package boundary."""

    def test_schemas_signatures_identity_pickle_and_exports_are_exact(self) -> None:
        schemas = (
            (CommandCompletionCandidate, ("position", "replacement")),
            (CommandCompletionProjection, ("position", "start", "end", "candidates")),
            (MainCommandPaletteState, ("projection", "highlighted", "snapshot")),
            (MainCommandControlState, ("main_input", "palette", "dismissal_latch")),
        )
        candidate = CommandCompletionCandidate("root.command", "/say")
        projection = CommandCompletionProjection("root.command", 0, 1, (candidate,))
        values = (
            candidate,
            projection,
            MainCommandPaletteState(projection, candidate),
            MainCommandControlState(
                MainInputControlState(LineInputState("/", 1), overlay_open=True),
                MainCommandPaletteState(projection, candidate),
            ),
        )
        for (model, names), value in zip(schemas, values):
            with self.subTest(model=model.__name__):
                self.assertEqual(tuple(inspect.signature(model).parameters), names)
                self.assertEqual(tuple(field.name for field in fields(model)), names)
                self.assertFalse(hasattr(value, "__dict__"))
                self.assertEqual(pickle.loads(pickle.dumps(value)), value)
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, names[0], None)
                self.assertEqual(
                    model.__module__,
                    "story_writing_agents.interactive_terminal._model",
                )

        self.assertEqual(
            tuple(inspect.signature(transition_main_command_input).parameters),
            ("state", "event", "history", "completion_provider"),
        )
        self.assertEqual(
            tuple(inspect.signature(project_main_suggestion_palette).parameters),
            ("state",),
        )
        names = {model.__name__ for model, _ in schemas}
        self.assertTrue(names.isdisjoint(_model.__all__))
        self.assertTrue(names.isdisjoint(terminal_facade.__all__))
        self.assertTrue(names.isdisjoint(story_writing_agents.__all__))
        self.assertTrue(all(not hasattr(terminal_facade, name) for name in names))
        self.assertNotIn("transition_main_command_input", terminal_facade.__all__)
        self.assertIs(
            terminal_facade.transition_main_command_input,
            transition_main_command_input,
        )
        self.assertNotIn(CommandCompletionCandidate, get_args(Component))
        self.assertNotIn(CommandCompletionProjection, get_args(Component))
        self.assertNotIn(MainCommandPaletteState, get_args(Component))
        self.assertNotIn(MainCommandControlState, get_args(Component))

    def test_models_reject_invalid_spans_candidates_and_state_relations(self) -> None:
        root = CommandCompletionCandidate("root", "/say")
        other = CommandCompletionCandidate("other", "/say")
        projection = CommandCompletionProjection("root", 0, 1, (root,))
        invalid = (
            (lambda: CommandCompletionCandidate("", "/say"), ValueError),
            (lambda: CommandCompletionCandidate("root", ""), ValueError),
            (lambda: CommandCompletionProjection("root", True, 1, (root,)), TypeError),
            (lambda: CommandCompletionProjection("root", -1, 1, (root,)), ValueError),
            (lambda: CommandCompletionProjection("root", 2, 1, (root,)), ValueError),
            (lambda: CommandCompletionProjection("root", 0, 1, [root]), TypeError),
            (lambda: CommandCompletionProjection("root", 0, 1, (other,)), ValueError),
            (
                lambda: CommandCompletionProjection("root", 0, 1, (root, root)),
                ValueError,
            ),
            (lambda: MainCommandPaletteState(object(), root), TypeError),
            (lambda: MainCommandPaletteState(projection, None), ValueError),
            (lambda: MainCommandPaletteState(projection, other), ValueError),
            (lambda: MainCommandControlState(object()), TypeError),
            (
                lambda: MainCommandControlState(
                    MainInputControlState(),
                    MainCommandPaletteState(projection, root),
                ),
                ValueError,
            ),
            (
                lambda: MainCommandControlState(
                    MainInputControlState(
                        LineInputState("/", 1),
                        overlay_open=True,
                    ),
                    MainCommandPaletteState(
                        CommandCompletionProjection("root", 2, 2, (root,)),
                        root,
                    ),
                ),
                ValueError,
            ),
            (
                lambda: MainCommandControlState(
                    MainInputControlState(),
                    dismissal_latch=LineInputState("x", 1),
                ),
                ValueError,
            ),
        )
        for call, error_type in invalid:
            with self.subTest(call=call):
                with self.assertRaises(error_type):
                    call()


class CompletionControlTests(unittest.TestCase):
    """Verify selectable projections remain pure and history-aware."""

    @staticmethod
    def apply(
        state: MainCommandControlState,
        event: KeyEvent,
        history: MainInputHistoryState = MainInputHistoryState(),
    ) -> tuple[MainCommandControlState, object]:
        return transition_main_command_input(
            state,
            event,
            history,
            _core._main_command_completion,
        )

    def type_text(self, text: str) -> MainCommandControlState:
        state = MainCommandControlState()
        for character in text:
            state, outcome = self.apply(
                state,
                KeyEvent(KeyKind.TEXT, text=character),
            )
            self.assertIsNone(outcome)
        return state

    def test_initial_filter_highlight_clamp_and_palette_projection_are_exact(
        self,
    ) -> None:
        state = self.type_text("/s")
        assert state.palette is not None
        self.assertEqual(
            tuple(item.replacement for item in state.palette.projection.candidates),
            ("/say", "/settings", "/session", "/search", "/status"),
        )
        self.assertEqual(
            state.palette.highlighted.replacement,  # type: ignore[union-attr]
            "/say",
        )
        moved, _ = self.apply(state, KeyEvent(KeyKind.DOWN))
        self.assertEqual(
            moved.palette.highlighted.replacement,  # type: ignore[union-attr]
            "/settings",
        )
        for _ in range(10):
            moved, _ = self.apply(moved, KeyEvent(KeyKind.DOWN))
        self.assertEqual(
            moved.palette.highlighted.replacement,  # type: ignore[union-attr]
            "/status",
        )
        self.assertIs(self.apply(moved, KeyEvent(KeyKind.DOWN))[0], moved)
        for _ in range(10):
            moved, _ = self.apply(moved, KeyEvent(KeyKind.UP))
        self.assertEqual(
            moved.palette.highlighted.replacement,  # type: ignore[union-attr]
            "/say",
        )
        self.assertIs(self.apply(moved, KeyEvent(KeyKind.UP))[0], moved)

        palette = project_main_suggestion_palette(state)
        self.assertEqual(
            palette,
            SuggestionPalette(
                "/s",
                tuple(
                    Choice(f"root.command:{replacement}", replacement)
                    for replacement in (
                        "/say",
                        "/settings",
                        "/session",
                        "/search",
                        "/status",
                    )
                ),
                highlighted_id="root.command:/say",
                state=ViewState.OPEN,
            ),
        )

    def test_tab_snapshot_exact_noop_cycle_and_escape_restore(self) -> None:
        initial = self.type_text("/s")
        selected, outcome = self.apply(initial, KeyEvent(KeyKind.TAB))
        self.assertIsNone(outcome)
        self.assertEqual(selected.main_input.line_state, LineInputState("/say", 4))
        self.assertEqual(
            selected.palette.snapshot,  # type: ignore[union-attr]
            LineInputState("/s", 2),
        )
        self.assertIs(self.apply(selected, KeyEvent(KeyKind.TAB))[0], selected)
        restored, _ = self.apply(selected, KeyEvent(KeyKind.ESCAPE))
        self.assertEqual(restored.main_input.line_state, LineInputState("/s", 2))
        self.assertIsNone(restored.palette)
        self.assertEqual(restored.dismissal_latch, LineInputState("/s", 2))
        self.assertIs(self.apply(restored, KeyEvent(KeyKind.TAB))[0], restored)

        changed, _ = self.apply(restored, KeyEvent(KeyKind.TEXT, text="e"))
        self.assertIsNone(changed.dismissal_latch)
        reopened, _ = self.apply(changed, KeyEvent(KeyKind.BACKSPACE))
        self.assertIsNotNone(reopened.palette)

        empty = self.type_text("/xz")
        self.assertIsNotNone(empty.palette)
        self.assertIsNone(empty.palette.highlighted)  # type: ignore[union-attr]
        self.assertIs(self.apply(empty, KeyEvent(KeyKind.TAB))[0], empty)

    def test_shift_tab_is_identity_and_history_waits_for_palette_close(self) -> None:
        history = MainInputHistoryState(("/status",))
        open_state = self.type_text("/")
        self.assertIs(
            self.apply(open_state, KeyEvent(KeyKind.SHIFT_TAB), history)[0],
            open_state,
        )
        moved, _ = self.apply(open_state, KeyEvent(KeyKind.UP), history)
        self.assertEqual(moved.main_input.line_state, LineInputState("/", 1))
        self.assertIsNotNone(moved.palette)
        closed, _ = self.apply(moved, KeyEvent(KeyKind.ESCAPE), history)
        recalled, _ = self.apply(closed, KeyEvent(KeyKind.UP), history)
        self.assertEqual(recalled.main_input.line_state, LineInputState("/status", 7))
        self.assertEqual(recalled.main_input.saved_draft, LineInputState("/", 1))
        self.assertIsNotNone(recalled.palette)
        restored, _ = self.apply(recalled, KeyEvent(KeyKind.ESCAPE), history)
        restored, _ = self.apply(restored, KeyEvent(KeyKind.DOWN), history)
        self.assertEqual(restored.main_input.line_state, LineInputState("/", 1))

    def test_editing_span_preserves_suffix_and_commit_is_exact(self) -> None:
        state = self.type_text("/session list")
        for _ in range(4):
            state, _ = self.apply(state, KeyEvent(KeyKind.LEFT))
        assert state.palette is not None
        self.assertEqual(
            (state.palette.projection.start, state.palette.projection.end),
            (9, 13),
        )
        selected, _ = self.apply(state, KeyEvent(KeyKind.TAB))
        self.assertEqual(
            selected.main_input.line_state,
            LineInputState("/session list", 13),
        )
        committed, outcome = self.apply(selected, KeyEvent(KeyKind.ENTER))
        self.assertEqual(
            outcome.kind,  # type: ignore[union-attr]
            ControlOutcomeKind.COMMIT,
        )
        self.assertEqual(outcome.text, "/session list")  # type: ignore[union-attr]
        self.assertIsNone(committed.palette)

    def test_provider_validation_is_eager_and_has_no_hidden_retry(self) -> None:
        state = MainCommandControlState()
        provider = mock.Mock(return_value=object())
        with self.assertRaisesRegex(
            TypeError,
            "^completion_provider must return a CommandCompletionProjection or None$",
        ):
            transition_main_command_input(
                state,
                KeyEvent(KeyKind.TEXT, text="/"),
                MainInputHistoryState(),
                provider,
            )
        provider.assert_called_once_with("/", 1)
        for arguments in (
            (
                object(),
                KeyEvent(KeyKind.TAB),
                MainInputHistoryState(),
                lambda _b, _c: None,
            ),
            (state, object(), MainInputHistoryState(), lambda _b, _c: None),
            (state, KeyEvent(KeyKind.TAB), object(), lambda _b, _c: None),
            (state, KeyEvent(KeyKind.TAB), MainInputHistoryState(), None),
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(TypeError):
                    transition_main_command_input(*arguments)  # type: ignore[arg-type]


class CompletionRenderingTests(unittest.TestCase):
    """Lock the semantic palette's deterministic wide, narrow, and safe bytes."""

    PALETTE = SuggestionPalette(
        "/s",
        (
            Choice("root.command:/say", "/say"),
            Choice("root.command:/status", "/status"),
        ),
        highlighted_id="root.command:/status",
        state=ViewState.OPEN,
    )

    def test_plain_ansi_no_color_and_narrow_frames_are_exact(self) -> None:
        plain = render_component(self.PALETTE, capabilities(CapabilityProfile.PLAIN))
        ansi = render_component(self.PALETTE, capabilities(CapabilityProfile.ANSI))
        line = render_component(self.PALETTE, capabilities(CapabilityProfile.LINE))
        narrow = render_component(
            self.PALETTE,
            capabilities(CapabilityProfile.PLAIN, 18),
        )
        self.assertEqual(
            plain,
            RenderedFrame(
                "Suggestions [open]\n  - /say\n  > [highlighted] /status",
                80,
                3,
            ),
        )
        self.assertEqual(line.text, plain.text)
        self.assertEqual(strip_decorative_sgr(ansi.text), plain.text)
        self.assertEqual(
            ansi.text,
            "\x1b[35mSuggestions [open]\x1b[0m\n"
            "  - /say\n"
            "\x1b[36m  > [highlighted] /status\x1b[0m",
        )
        self.assertEqual(
            narrow.text,
            "Suggestions [open]\n- /say\n> [highlighted] /s\n"
            "                ta\n                tu\n                s",
        )

    def test_untrusted_labels_are_sanitized_before_style_and_width(self) -> None:
        hostile = SuggestionPalette(
            "buffer\x1b]0;title\x07",
            (
                Choice("safe", "/한글 e\u0301 🌊\x1b[31m\u202e"),
            ),
            highlighted_id="safe",
        )
        plain = render_component(hostile, capabilities(CapabilityProfile.PLAIN, 20))
        ansi = render_component(hostile, capabilities(CapabilityProfile.ANSI, 20))
        self.assertEqual(strip_decorative_sgr(ansi.text), plain.text)
        self.assertIn("/한", plain.text)
        self.assertIn("글 ", plain.text)
        self.assertIn("é ", plain.text)
        self.assertIn("\\x1b[31m\\u202e", "".join(plain.text.split()))
        self.assertNotIn("\x1b", plain.text)
        self.assertNotIn("\u202e", plain.text)
        residue = re.sub(r"\x1b\[[0-9;]*m", "", ansi.text)
        self.assertNotIn("\x1b", residue)


class CompletionDriverTests(unittest.TestCase):
    """Exercise the supported driver seam, fallback, decoding, and cleanup."""

    @staticmethod
    def read(driver: RawTerminalDriver) -> str:
        return driver.read_main_command_line(
            "main> ",
            _core._main_command_completion,
            project_main_suggestion_palette,
            render_component,
        )

    def test_supported_live_read_selects_commits_appends_and_restores_once(
        self,
    ) -> None:
        driver, _input, output, fallback, termios, os_module = make_driver(
            "/\x1b[B\t\n"
        )
        with mock.patch.object(
            _driver._select,
            "select",
            side_effect=lambda readable, writable, exceptional, _timeout: (
                readable, writable, exceptional
            ),
        ) as readiness:
            self.assertEqual(self.read(driver), "/to")

        fallback.assert_not_called()
        self.assertEqual(driver._history_state, MainInputHistoryState(("/to",)))
        self.assertEqual(os_module.observed_widths, [8])
        self.assertEqual(
            tuple(call[1] for call in termios.set_calls),
            (FakeTermios.TCSANOW, FakeTermios.TCSADRAIN),
        )
        readiness.assert_called_once_with((7,), (), (), 0.05)
        rendered = "".join(output.records)
        self.assertIn("Suggestions [open]", rendered)
        self.assertIn("[highlighted] /to", rendered)
        self.assertNotIn("[suggestions]", rendered)

    def test_standalone_escape_closes_palette_and_exact_readiness_timeout(self) -> None:
        driver, _input, output, fallback, _termios, _os = make_driver("/\x1b\n")
        with mock.patch.object(
            _driver._select,
            "select",
            return_value=((), (), ()),
        ) as readiness:
            self.assertEqual(self.read(driver), "/")
        readiness.assert_called_once_with((7,), (), (), 0.05)
        fallback.assert_not_called()
        self.assertIn("Suggestions [open]", "".join(output.records))
        self.assertNotIn("Suggestions [open]", output.records[-1])

    def test_csi_application_and_ordinary_escape_bytes_reprocess_exactly(self) -> None:
        for sequence in ("\x1b[B", "\x1bOB"):
            with self.subTest(sequence=repr(sequence)):
                driver, _input, _output, fallback, _termios, _os = make_driver(
                    "/" + sequence + "\t\n"
                )
                with mock.patch.object(
                    _driver._select,
                    "select",
                    side_effect=lambda r, w, x, _t: (r, w, x),
                ):
                    self.assertEqual(self.read(driver), "/to")
                fallback.assert_not_called()

        driver, _input, _output, fallback, _termios, _os = make_driver("/\x1bx\n")
        with mock.patch.object(
            _driver._select,
            "select",
            side_effect=lambda r, w, x, _t: (r, w, x),
        ):
            self.assertEqual(self.read(driver), "/x")
        fallback.assert_not_called()

    def test_shift_tab_and_unknown_sequences_are_noops_without_extra_redraw(
        self,
    ) -> None:
        driver, _input, output, fallback, _termios, _os = make_driver(
            "\x1b[Z\x1b[999~\n"
        )
        with mock.patch.object(
            _driver._select,
            "select",
            side_effect=lambda r, w, x, _t: (r, w, x),
        ):
            self.assertEqual(self.read(driver), "")
        fallback.assert_not_called()
        self.assertEqual(len(output.records), 2)

    def test_interrupt_veof_and_physical_eof_restore_without_failed_append(
        self,
    ) -> None:
        cases = (
            ("text\x04", "text", None, MainInputHistoryState(("text",))),
            ("\x03", None, KeyboardInterrupt, MainInputHistoryState()),
            ("\x04", None, EOFError, MainInputHistoryState()),
            ("", None, EOFError, MainInputHistoryState()),
        )
        for text, expected, error_type, history in cases:
            with self.subTest(text=repr(text)):
                driver, _input, _output, fallback, termios, _os = make_driver(text)
                if error_type is None:
                    self.assertEqual(self.read(driver), expected)
                else:
                    with self.assertRaises(error_type):
                        self.read(driver)
                fallback.assert_not_called()
                self.assertEqual(driver._history_state, history)
                self.assertEqual(
                    tuple(call[1] for call in termios.set_calls),
                    (FakeTermios.TCSANOW, FakeTermios.TCSADRAIN),
                )

    def test_completion_provider_is_call_local_across_reads_on_one_driver(self) -> None:
        driver, _input, _output, fallback, _termios, _os = make_driver("x\n/\t\n")
        first = mock.Mock(return_value=None)
        with mock.patch.object(
            _driver._select,
            "select",
            side_effect=lambda r, w, x, _t: (r, w, x),
        ):
            self.assertEqual(
                driver.read_main_command_line(
                    "first> ",
                    first,
                    project_main_suggestion_palette,
                    render_component,
                ),
                "x",
            )
            self.assertEqual(self.read(driver), "/say")
        self.assertEqual(
            tuple(call.args for call in first.call_args_list),
            (("x", 1),),
        )
        self.assertEqual(
            driver._history_state,
            MainInputHistoryState(("x", "/say")),
        )
        fallback.assert_not_called()

    def test_supported_read_incrementally_decodes_utf8_without_text_readahead(
        self,
    ) -> None:
        driver, input_stream, _output, fallback, termios, _os = make_driver("")
        encoded = "界e\u0301\n".encode("utf-8")
        read_one = mock.Mock(side_effect=tuple(bytes((value,)) for value in encoded))
        input_stream.buffer = SimpleNamespace(read1=read_one)  # type: ignore[attr-defined]
        input_stream.encoding = "utf-8"  # type: ignore[attr-defined]
        input_stream.errors = "strict"  # type: ignore[attr-defined]

        self.assertEqual(self.read(driver), "界e\u0301")

        self.assertEqual(read_one.call_args_list, [mock.call(1)] * len(encoded))
        self.assertFalse(any(call == ("read", 1) for call in input_stream.calls))
        self.assertEqual(
            driver._history_state,
            MainInputHistoryState(("界e\u0301",)),
        )
        fallback.assert_not_called()
        self.assertEqual(
            tuple(call[1] for call in termios.set_calls),
            (FakeTermios.TCSANOW, FakeTermios.TCSADRAIN),
        )

    def test_malformed_default_and_incremental_readers_share_exact_diagnostic(
        self,
    ) -> None:
        for source in ("default", "incremental"):
            with self.subTest(source=source):
                driver, input_stream, output, fallback, termios, _os = make_driver("")
                incremental_reader = mock.Mock(return_value="two")
                reader_patch = (
                    mock.patch.object(
                        _driver,
                        "_single_character_reader",
                        return_value=incremental_reader,
                    )
                    if source == "incremental"
                    else mock.patch.object(
                        input_stream,
                        "read",
                        return_value=object(),
                    )
                )

                with reader_patch as collaborator:
                    with self.assertRaisesRegex(
                        TypeError,
                        "^input reader must return one character$",
                    ):
                        self.read(driver)

                if source == "incremental":
                    collaborator.assert_called_once_with(input_stream)
                    incremental_reader.assert_called_once_with()
                else:
                    collaborator.assert_called_once_with(1)
                fallback.assert_not_called()
                self.assertEqual(driver._history_state, MainInputHistoryState())
                self.assertEqual(
                    tuple(call[1] for call in termios.set_calls),
                    (FakeTermios.TCSANOW, FakeTermios.TCSADRAIN),
                )
                self.assertEqual(output.records[-1], "\r\r\x1b[J\n")

    def test_line_fallback_bypasses_all_completion_and_live_collaborators(self) -> None:
        driver, input_stream, output, fallback, termios, os_module = make_driver(
            "unused",
            terminal=False,
        )
        completion = mock.Mock(side_effect=AssertionError("completion called"))
        projector = mock.Mock(side_effect=AssertionError("projector called"))
        renderer = mock.Mock(side_effect=AssertionError("renderer called"))
        self.assertEqual(
            driver.read_main_command_line("main> ", completion, projector, renderer),
            "fallback",
        )
        fallback.assert_called_once_with("main> ")
        completion.assert_not_called()
        projector.assert_not_called()
        renderer.assert_not_called()
        self.assertEqual(driver._history_state, MainInputHistoryState(("fallback",)))
        self.assertNotIn(("read", 1), input_stream.calls)
        self.assertEqual(output.records, [])
        self.assertEqual(termios.set_calls, [])
        self.assertEqual(os_module.observed_widths, [])

        dumb, dumb_input, dumb_output, dumb_fallback, dumb_termios, dumb_os = (
            make_driver("unused")
        )
        dumb_os.environ = {"TERM": "dumb"}
        completion = mock.Mock(side_effect=AssertionError("completion called"))
        projector = mock.Mock(side_effect=AssertionError("projector called"))
        renderer = mock.Mock(side_effect=AssertionError("renderer called"))
        self.assertEqual(
            dumb.read_main_command_line(
                "main> ",
                completion,
                projector,
                renderer,
            ),
            "fallback",
        )
        dumb_fallback.assert_called_once_with("main> ")
        completion.assert_not_called()
        projector.assert_not_called()
        renderer.assert_not_called()
        self.assertNotIn(("read", 1), dumb_input.calls)
        self.assertEqual(dumb_output.records, [])
        self.assertEqual(dumb_termios.set_calls, [])
        self.assertEqual(dumb_os.observed_widths, [8])

    def test_completion_projector_and_renderer_failures_restore_without_history(
        self,
    ) -> None:
        cases = (
            (
                mock.Mock(return_value=object()),
                project_main_suggestion_palette,
                render_component,
                "completion_provider must return a CommandCompletionProjection or None",
            ),
            (
                _core._main_command_completion,
                mock.Mock(return_value=object()),
                render_component,
                "palette_projector must return a SuggestionPalette or None",
            ),
            (
                _core._main_command_completion,
                project_main_suggestion_palette,
                mock.Mock(return_value=object()),
                "palette_renderer must return a RenderedFrame",
            ),
        )
        for completion, projector, renderer, message in cases:
            with self.subTest(message=message):
                driver, _input, _output, fallback, termios, _os = make_driver("/\n")
                with self.assertRaisesRegex(TypeError, f"^{re.escape(message)}$"):
                    driver.read_main_command_line(
                        "main> ", completion, projector, renderer
                    )
                fallback.assert_not_called()
                self.assertEqual(driver._history_state, MainInputHistoryState())
                self.assertEqual(
                    tuple(call[1] for call in termios.set_calls),
                    (FakeTermios.TCSANOW, FakeTermios.TCSADRAIN),
                )

    def test_core_adapter_uses_only_the_supported_driver_method(self) -> None:
        driver = mock.Mock(spec=RawTerminalDriver)
        driver.read_main_command_line.return_value = "/status"
        self.assertEqual(_core.read_main_command_line(driver, "main> "), "/status")
        driver.read_main_command_line.assert_called_once_with(
            "main> ",
            _core._main_command_completion,
            project_main_suggestion_palette,
            render_component,
        )
        with self.assertRaises(TypeError):
            _core.read_main_command_line(object(), "main> ")  # type: ignore[arg-type]

    def test_supported_method_validates_call_local_collaborators_before_io(
        self,
    ) -> None:
        for index, expected in enumerate(
            ("completion_provider", "palette_projector", "palette_renderer")
        ):
            driver, input_stream, output, fallback, termios, os_module = make_driver("")
            collaborators: list[object] = [
                lambda _b, _c: None,
                lambda _s: None,
                render_component,
            ]
            collaborators[index] = None
            with self.assertRaisesRegex(TypeError, f"^{expected} must be callable$"):
                driver.read_main_command_line(
                    "main> ", *collaborators  # type: ignore[arg-type]
                )
            self.assertEqual(input_stream.calls, [])
            self.assertEqual(output.records, [])
            fallback.assert_not_called()
            self.assertEqual(termios.set_calls, [])
            self.assertEqual(os_module.observed_widths, [])


if __name__ == "__main__":
    unittest.main()
