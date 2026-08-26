"""Deterministic contracts for selectable main-command completion."""

from __future__ import annotations

import inspect
import pickle
import re
import unittest
from dataclasses import FrozenInstanceError, fields, replace
from types import SimpleNamespace
from typing import get_args
from unittest import mock

import story_writing_agents
from story_writing_agents import interactive_terminal as terminal_facade
from story_writing_agents.interactive_commands import project_provider_text_span
from story_writing_agents.interactive_terminal import _controls, _core, _driver, _model
from story_writing_agents.interactive_terminal._controls import (
    append_main_input_history,
    completion_candidate_matches,
    initialize_picker,
    main_command_uses_choices,
    project_main_choice_completion,
    project_main_choice_palette,
    project_picker_viewport,
    project_main_suggestion_palette,
    ranged_artifact_picker_selection,
    transition_line_input,
    transition_main_command_input,
    transition_picker,
    transition_picker_fallback,
)
from story_writing_agents.interactive_terminal._driver import RawTerminalDriver
from story_writing_agents.interactive_terminal._reference_activation import (
    ReferenceActivationKind,
    ReferenceActivationRequest,
    ReferenceInputFailure,
    ReferenceInputFailureKind,
    ReferenceInputProjection,
    ReferenceInputScope,
    replace_reference_completion,
    replace_reference_range,
    selected_reference_request,
)
from story_writing_agents.interactive_terminal._model import (
    EMPTY_RANGED_ARTIFACT_REFERENCE_POSITION_PREFIX,
    RANGED_ARTIFACT_REFERENCE_POSITION_PREFIX,
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
    Picker,
    Prompt,
    RenderedFrame,
    SuggestionPalette,
    TerminalCapabilities,
    ViewState,
)
from story_writing_agents.interactive_terminal._render import (
    render_component,
    render_picker_fallback,
    render_picker_viewport,
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
    """Expose exact UTF-8 bytes through one terminal descriptor."""

    def __init__(self, text: str, *, terminal: bool = True) -> None:
        self.reads = list(text.encode("utf-8"))
        self.terminal = terminal
        self.calls: list[object] = []
        self.encoding = "utf-8"
        self.errors = "strict"
        self.buffer = self

    def isatty(self) -> bool:
        self.calls.append("isatty")
        return self.terminal

    def fileno(self) -> int:
        self.calls.append("fileno")
        return 7

    def read(self, size: int) -> str:
        self.calls.append(("read", size))
        raise AssertionError("text input must not run for live byte input")

    def read1(self, size: int) -> bytes:
        self.calls.append(("read1", size))
        return bytes((self.reads.pop(0),)) if self.reads else b""


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

    def fpathconf(descriptor: int, name: str) -> int:
        if descriptor != 7 or name != "PC_VDISABLE":
            raise AssertionError((descriptor, name))
        return 0

    os_module = SimpleNamespace(
        name="posix" if terminal else "nt",
        environ={"TERM": "xterm-256color"},
        get_terminal_size=get_terminal_size,
        fpathconf=fpathconf,
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

    def test_completion_matcher_preserves_literal_slash_and_reference_rules(
        self,
    ) -> None:
        reference = "${core_documents/global_outline.md}"
        cases = (
            (reference, "$", True),
            (reference, "$c", True),
            (reference, "$core_documents/", True),
            (reference, "$core_documents/g", True),
            (reference, "${core", True),
            (reference, "$C", False),
            (reference, "core_documents/", False),
            (reference, "$$", False),
            (reference, "$${core", False),
            ("/status", "/st", True),
            ("/status", "st", True),
            ("/status", "St", False),
            ("@plot", "@p", True),
            ("@plot", "plot", False),
        )
        for replacement, prefix, expected in cases:
            with self.subTest(replacement=replacement, prefix=prefix):
                self.assertIs(
                    completion_candidate_matches(replacement, prefix),
                    expected,
                )

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

    def test_artifact_reference_palette_semantics_are_exact_and_scoped(self) -> None:
        """Specialize artifact discovery without changing generic suggestions."""

        path = "core_documents/character_sheets.md"
        token = f"${{{path}}}"
        position = "artifact-reference:0"
        candidate = CommandCompletionCandidate(position, token)
        state = MainCommandControlState(
            MainInputControlState(LineInputState("$", 1), overlay_open=True),
            MainCommandPaletteState(
                CommandCompletionProjection(position, 0, 1, (candidate,)),
                candidate,
            ),
        )

        self.assertEqual(
            project_main_suggestion_palette(state),
            SuggestionPalette(
                "$",
                (Choice(f"{position}:{token}", token),),
                highlighted_id=f"{position}:{token}",
                title="Writing artifact selection",
                instruction=(
                    "Up/Down selects; Enter/Tab inserts a whole-file reference."
                ),
                empty_text=(
                    "No matching writing artifacts. Change the path fragment."
                ),
            ),
        )

        empty_position = "artifact-reference:empty:0"
        empty_state = MainCommandControlState(
            MainInputControlState(LineInputState("$", 1), overlay_open=True),
            MainCommandPaletteState(
                CommandCompletionProjection(empty_position, 0, 1, ()),
                None,
            ),
        )
        self.assertEqual(
            project_main_suggestion_palette(empty_state),
            SuggestionPalette(
                "$",
                (),
                title="Writing artifact selection",
                instruction=(
                    "Up/Down selects; Enter/Tab inserts a whole-file reference."
                ),
                empty_text=(
                    "No writing artifacts. Add a supported canonical core "
                    "document or Markdown file with a lowercase .md suffix "
                    "under manuscript/. Consensus can also publish one."
                ),
            ),
        )

        ranged_position = f"{RANGED_ARTIFACT_REFERENCE_POSITION_PREFIX}0"
        ranged_paths = (
            "manuscript/older drafts/{世界$ 章#1.md",
            "manuscript/root.md",
        )
        ranged_candidates = tuple(
            CommandCompletionCandidate(ranged_position, path)
            for path in ranged_paths
        )
        ranged_state = MainCommandControlState(
            MainInputControlState(LineInputState("$$", 2), overlay_open=True),
            MainCommandPaletteState(
                CommandCompletionProjection(
                    ranged_position,
                    0,
                    2,
                    ranged_candidates,
                ),
                ranged_candidates[0],
            ),
        )
        self.assertEqual(
            project_main_suggestion_palette(ranged_state),
            SuggestionPalette(
                "$$",
                tuple(
                    Choice(f"{ranged_position}:{path}", path)
                    for path in ranged_paths
                ),
                highlighted_id=f"{ranged_position}:{ranged_paths[0]}",
                title="Writing artifact selection",
                instruction="Up/Down selects; Enter/Tab opens the ranged viewer.",
                empty_text=(
                    "No matching writing artifacts. Change the path fragment."
                ),
            ),
        )

        empty_ranged_position = (
            f"{EMPTY_RANGED_ARTIFACT_REFERENCE_POSITION_PREFIX}0"
        )
        empty_ranged_state = MainCommandControlState(
            MainInputControlState(LineInputState("$$", 2), overlay_open=True),
            MainCommandPaletteState(
                CommandCompletionProjection(
                    empty_ranged_position,
                    0,
                    2,
                    (),
                ),
                None,
            ),
        )
        self.assertEqual(
            project_main_suggestion_palette(empty_ranged_state),
            SuggestionPalette(
                "$$",
                (),
                title="Writing artifact selection",
                instruction="Up/Down selects; Enter/Tab opens the ranged viewer.",
                empty_text=(
                    "No writing artifacts. Add a supported canonical core "
                    "document or Markdown file with a lowercase .md suffix "
                    "under manuscript/. Consensus can also publish one."
                ),
            ),
        )

    def test_ranged_picker_opens_on_second_dollar_navigates_and_reopens(
        self,
    ) -> None:
        paths = (
            "manuscript/older drafts/{世界$ 章#1.md",
            "manuscript/root.md",
        )
        projection_provider = _core._reference_projection_provider(
            lambda buffer, _cursor: ReferenceInputScope(0, len(buffer)),
            lambda: paths,
        )

        def completion_provider(
            buffer: str,
            cursor: int,
        ) -> CommandCompletionProjection | None:
            projected = projection_provider(buffer, cursor)
            return None if projected is None else projected.completion

        state = MainCommandControlState()
        for character in "$$":
            state, outcome = transition_main_command_input(
                state,
                KeyEvent(KeyKind.TEXT, text=character),
                MainInputHistoryState(),
                completion_provider,
            )
            self.assertIsNone(outcome)
        assert state.palette is not None
        self.assertEqual(state.main_input.line_state, LineInputState("$$", 2))
        self.assertTrue(
            state.palette.projection.position.startswith(
                RANGED_ARTIFACT_REFERENCE_POSITION_PREFIX
            )
        )
        self.assertEqual(state.palette.highlighted.replacement, paths[0])

        moved, outcome = transition_main_command_input(
            state,
            KeyEvent(KeyKind.DOWN),
            MainInputHistoryState(),
            completion_provider,
        )
        self.assertIsNone(outcome)
        assert moved.palette is not None
        self.assertEqual(moved.palette.highlighted.replacement, paths[1])
        projected = projection_provider("$$", 2)
        assert projected is not None and projected.completion is not None
        self.assertEqual(
            ranged_artifact_picker_selection(moved, projected.completion),
            (True, paths[1]),
        )
        for key in (KeyKind.ENTER, KeyKind.TAB):
            with self.subTest(key=key):
                unchanged, outcome = transition_main_command_input(
                    moved,
                    KeyEvent(key),
                    MainInputHistoryState(),
                    completion_provider,
                )
                self.assertIs(unchanged, moved)
                self.assertIsNone(outcome)

        dismissed, outcome = transition_main_command_input(
            moved,
            KeyEvent(KeyKind.ESCAPE),
            MainInputHistoryState(),
            completion_provider,
        )
        self.assertIsNone(outcome)
        self.assertEqual(dismissed.main_input.line_state, LineInputState("$$", 2))
        self.assertIsNone(dismissed.palette)
        reopened, outcome = transition_main_command_input(
            dismissed,
            KeyEvent(KeyKind.ENTER),
            MainInputHistoryState(),
            completion_provider,
        )
        self.assertIsNone(outcome)
        self.assertIsNotNone(reopened.palette)
        self.assertEqual(reopened.main_input.line_state, LineInputState("$$", 2))

        direct, outcome = transition_main_command_input(
            moved,
            KeyEvent(KeyKind.TEXT, text="{"),
            MainInputHistoryState(),
            completion_provider,
        )
        self.assertIsNone(outcome)
        self.assertEqual(direct.main_input.line_state, LineInputState("$${", 3))
        self.assertIsNone(direct.palette)

        for event, expected_line, keeps_ranged in (
            (KeyEvent(KeyKind.BACKSPACE), LineInputState("$", 1), False),
            (KeyEvent(KeyKind.LEFT), LineInputState("$$", 1), True),
            (KeyEvent(KeyKind.TEXT, text="x"), LineInputState("$$x", 3), False),
        ):
            with self.subTest(invalidation=event):
                edited, outcome = transition_main_command_input(
                    state,
                    event,
                    MainInputHistoryState(),
                    completion_provider,
                )
                self.assertIsNone(outcome)
                self.assertEqual(edited.main_input.line_state, expected_line)
                if edited.palette is not None:
                    self.assertEqual(
                        edited.palette.projection.position.startswith(
                            RANGED_ARTIFACT_REFERENCE_POSITION_PREFIX
                        ),
                        keeps_ranged,
                    )
                else:
                    self.assertFalse(keeps_ranged)

    def test_empty_ranged_picker_escape_reopens_without_control_outcome(self) -> None:
        projection_provider = _core._reference_projection_provider(
            lambda buffer, _cursor: ReferenceInputScope(0, len(buffer)),
            lambda: (),
        )

        def completion_provider(
            buffer: str,
            cursor: int,
        ) -> CommandCompletionProjection | None:
            projected = projection_provider(buffer, cursor)
            return None if projected is None else projected.completion

        for key in (KeyKind.ENTER, KeyKind.TAB):
            with self.subTest(key=key):
                state = MainCommandControlState()
                for character in "$$":
                    state, outcome = transition_main_command_input(
                        state,
                        KeyEvent(KeyKind.TEXT, text=character),
                        MainInputHistoryState(),
                        completion_provider,
                    )
                    self.assertIsNone(outcome)
                assert state.palette is not None
                self.assertTrue(
                    state.palette.projection.position.startswith(
                        EMPTY_RANGED_ARTIFACT_REFERENCE_POSITION_PREFIX
                    )
                )
                self.assertIsNone(state.palette.highlighted)

                dismissed, outcome = transition_main_command_input(
                    state,
                    KeyEvent(KeyKind.ESCAPE),
                    MainInputHistoryState(),
                    completion_provider,
                )
                self.assertIsNone(outcome)
                self.assertIsNone(dismissed.palette)
                self.assertEqual(
                    dismissed.dismissal_latch,
                    LineInputState("$$", 2),
                )

                reopened, outcome = transition_main_command_input(
                    dismissed,
                    KeyEvent(key),
                    MainInputHistoryState(),
                    completion_provider,
                )
                self.assertIsNone(outcome)
                assert reopened.palette is not None
                self.assertTrue(
                    reopened.palette.projection.position.startswith(
                        EMPTY_RANGED_ARTIFACT_REFERENCE_POSITION_PREFIX
                    )
                )
                self.assertEqual(
                    reopened.main_input.line_state,
                    LineInputState("$$", 2),
                )

    def test_dynamic_choices_replace_static_focus_and_commit_exact_identity(
        self,
    ) -> None:
        picker = Picker(
            Prompt("Resume"),
            (
                Choice("/session resume alpha", "Alpha"),
                Choice(
                    "/session resume disabled",
                    "Unavailable",
                    enabled=False,
                    disabled_reason="Busy",
                ),
                Choice("/session resume beta", "Beta"),
            ),
            highlighted_id="/session resume beta",
        )
        choice_provider = mock.Mock(
            side_effect=lambda buffer, cursor: project_main_choice_completion(
                buffer,
                cursor,
                picker,
            )
        )
        state, outcome = transition_main_command_input(
            MainCommandControlState(),
            KeyEvent(KeyKind.TEXT, text="/"),
            MainInputHistoryState(("/status",)),
            choice_provider,
        )

        self.assertIsNone(outcome)
        self.assertTrue(main_command_uses_choices(state))
        self.assertEqual(
            tuple(
                candidate.replacement
                for candidate in state.palette.projection.candidates  # type: ignore[union-attr]
            ),
            ("/session resume alpha", "/session resume beta"),
        )
        self.assertEqual(
            state.palette.highlighted.replacement,  # type: ignore[union-attr]
            "/session resume beta",
        )
        self.assertEqual(
            project_main_choice_palette(state, picker),
            SuggestionPalette(
                "/",
                picker.choices,
                highlighted_id="/session resume beta",
                state=ViewState.OPEN,
            ),
        )
        self.assertNotEqual(
            state.palette.projection,  # type: ignore[union-attr]
            _core._main_command_completion("/", 1),
        )

        moved_up, outcome = transition_main_command_input(
            state,
            KeyEvent(KeyKind.UP),
            MainInputHistoryState(("/status",)),
            choice_provider,
        )
        self.assertIsNone(outcome)
        self.assertEqual(
            moved_up.palette.highlighted.replacement,  # type: ignore[union-attr]
            "/session resume alpha",
        )
        moved_down, _ = transition_main_command_input(
            moved_up,
            KeyEvent(KeyKind.DOWN),
            MainInputHistoryState(("/status",)),
            choice_provider,
        )
        tabbed, outcome = transition_main_command_input(
            moved_down,
            KeyEvent(KeyKind.TAB),
            MainInputHistoryState(("/status",)),
            choice_provider,
        )
        self.assertIs(tabbed, moved_down)
        self.assertIsNone(outcome)
        committed, outcome = transition_main_command_input(
            tabbed,
            KeyEvent(KeyKind.ENTER),
            MainInputHistoryState(("/status",)),
            choice_provider,
        )
        self.assertEqual(
            outcome,
            _model.ControlOutcome(
                ControlOutcomeKind.COMMIT,
                text="/session resume beta",
            ),
        )
        self.assertEqual(committed.main_input.line_state, LineInputState("/", 1))
        self.assertIsNone(committed.palette)
        choice_provider.assert_called_once_with("/", 1)

    def test_routed_agent_palette_order_filter_case_and_aliases_are_exact(
        self,
    ) -> None:
        replacements = ("@lead", "@plot", "@character", "@execution")
        initial = self.type_text("@")
        assert initial.palette is not None
        self.assertEqual(
            tuple(
                item.replacement for item in initial.palette.projection.candidates
            ),
            replacements,
        )
        self.assertEqual(
            initial.palette.highlighted.replacement,  # type: ignore[union-attr]
            "@lead",
        )
        self.assertEqual(
            project_main_suggestion_palette(initial),
            SuggestionPalette(
                "@",
                tuple(
                    Choice(f"routed.agent:{replacement}", replacement)
                    for replacement in replacements
                ),
                highlighted_id="routed.agent:@lead",
                state=ViewState.OPEN,
            ),
        )

        partial = self.type_text("@pl")
        assert partial.palette is not None
        self.assertEqual(
            tuple(
                item.replacement for item in partial.palette.projection.candidates
            ),
            ("@plot",),
        )
        for buffer in ("@Pl", "@or", "@plot_"):
            with self.subTest(buffer=buffer):
                unmatched = self.type_text(buffer)
                self.assertIsNotNone(unmatched.palette)
                self.assertEqual(
                    unmatched.palette.projection.candidates,  # type: ignore[union-attr]
                    (),
                )
                self.assertIsNone(
                    unmatched.palette.highlighted  # type: ignore[union-attr]
                )

    def test_routed_agent_completion_is_isolated_to_the_leading_token(
        self,
    ) -> None:
        for buffer in (
            "message @pl",
            "@plot message @pl",
            "/say @pl",
        ):
            with self.subTest(buffer=buffer):
                state = self.type_text(buffer)
                self.assertIsNone(state.palette)

    def test_enter_without_highlight_and_veof_submit_visible_buffer(self) -> None:
        cases = (
            ("ordinary", KeyKind.ENTER),
            ("/xz", KeyKind.ENTER),
            ("/s", KeyKind.EOF),
        )
        for buffer, kind in cases:
            with self.subTest(buffer=buffer, kind=kind):
                initial = self.type_text(buffer)
                submitted, outcome = self.apply(initial, KeyEvent(kind))
                self.assertEqual(
                    submitted.main_input.line_state,
                    initial.main_input.line_state,
                )
                self.assertIsNone(submitted.palette)
                self.assertEqual(
                    outcome.kind,  # type: ignore[union-attr]
                    ControlOutcomeKind.COMMIT,
                )
                self.assertEqual(outcome.text, buffer)  # type: ignore[union-attr]

    def test_highlighted_enter_completes_latches_and_second_enter_commits(
        self,
    ) -> None:
        initial = self.type_text("/s")
        completed, outcome = self.apply(initial, KeyEvent(KeyKind.ENTER))
        self.assertIsNone(outcome)
        self.assertEqual(completed.main_input.line_state, LineInputState("/say ", 5))
        self.assertIsNone(completed.palette)
        self.assertEqual(completed.dismissal_latch, LineInputState("/say ", 5))

        escaped, outcome = self.apply(completed, KeyEvent(KeyKind.ESCAPE))
        self.assertIs(escaped, completed)
        self.assertIsNone(outcome)

        submitted, outcome = self.apply(escaped, KeyEvent(KeyKind.ENTER))
        self.assertEqual(submitted.main_input.line_state, LineInputState("/say ", 5))
        self.assertIsNone(submitted.palette)
        self.assertEqual(
            outcome.kind,  # type: ignore[union-attr]
            ControlOutcomeKind.COMMIT,
        )
        self.assertEqual(outcome.text, "/say ")  # type: ignore[union-attr]

    def test_enter_and_tab_share_exact_trailing_space_completion(self) -> None:
        cases = (
            ("/s", "/say "),
            ("@pl", "@plot "),
            ("/status", "/status "),
            ("/session li", "/session list "),
        )
        for kind in (KeyKind.ENTER, KeyKind.TAB):
            for buffer, expected in cases:
                with self.subTest(kind=kind, buffer=buffer):
                    initial = self.type_text(buffer)
                    completed, outcome = self.apply(initial, KeyEvent(kind))
                    self.assertIsNone(outcome)
                    self.assertEqual(
                        completed.main_input.line_state,
                        LineInputState(expected, len(expected)),
                    )

    def test_enter_and_tab_preserve_ascii_delimiter_suffix_and_cursor(self) -> None:
        for source, replacement in (("/s", "/say"), ("@pl", "@plot")):
            for kind in (KeyKind.ENTER, KeyKind.TAB):
                for delimiter in (" ", "\t"):
                    with self.subTest(
                        source=source,
                        kind=kind,
                        delimiter=repr(delimiter),
                    ):
                        initial = self.type_text(f"{source}{delimiter}tail")
                        for _ in range(5):
                            initial, outcome = self.apply(
                                initial,
                                KeyEvent(KeyKind.LEFT),
                            )
                            self.assertIsNone(outcome)
                        self.assertEqual(
                            initial.main_input.line_state.cursor,
                            len(source),
                        )
                        self.assertIsNotNone(initial.palette)

                        completed, outcome = self.apply(initial, KeyEvent(kind))
                        self.assertIsNone(outcome)
                        self.assertEqual(
                            completed.main_input.line_state,
                            LineInputState(
                                f"{replacement}{delimiter}tail",
                                len(replacement),
                            ),
                        )

    def test_tab_snapshot_exact_noop_cycle_and_escape_restore(self) -> None:
        initial = self.type_text("/session li tail")
        for _ in range(5):
            initial, outcome = self.apply(initial, KeyEvent(KeyKind.LEFT))
            self.assertIsNone(outcome)
        selected, outcome = self.apply(initial, KeyEvent(KeyKind.TAB))
        self.assertIsNone(outcome)
        self.assertEqual(
            selected.main_input.line_state,
            LineInputState("/session list tail", 13),
        )
        self.assertEqual(
            selected.palette.snapshot,  # type: ignore[union-attr]
            LineInputState("/session li tail", 11),
        )
        self.assertIs(self.apply(selected, KeyEvent(KeyKind.TAB))[0], selected)
        restored, _ = self.apply(selected, KeyEvent(KeyKind.ESCAPE))
        self.assertEqual(
            restored.main_input.line_state,
            LineInputState("/session li tail", 11),
        )
        self.assertIsNone(restored.palette)
        self.assertEqual(
            restored.dismissal_latch,
            LineInputState("/session li tail", 11),
        )
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
            LineInputState("/session list ", 14),
        )
        committed, outcome = self.apply(selected, KeyEvent(KeyKind.ENTER))
        self.assertEqual(
            outcome.kind,  # type: ignore[union-attr]
            ControlOutcomeKind.COMMIT,
        )
        self.assertEqual(outcome.text, "/session list ")  # type: ignore[union-attr]
        self.assertIsNone(committed.palette)

    def test_agent_alias_completion_uses_preferred_candidate_and_tab_span(self) -> None:
        state = self.type_text("/to pl")
        self.assertIsNotNone(state.palette)
        assert state.palette is not None
        self.assertEqual(
            (state.palette.projection.start, state.palette.projection.end),
            (4, 6),
        )
        self.assertEqual(
            tuple(item.replacement for item in state.palette.projection.candidates),
            ("plot",),
        )
        selected, outcome = self.apply(state, KeyEvent(KeyKind.TAB))
        self.assertIsNone(outcome)
        self.assertEqual(
            selected.main_input.line_state,
            LineInputState("/to plot ", 9),
        )

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

    def test_artifact_specific_plain_frames_are_exact(self) -> None:
        """Render discovery guidance while generic frames remain byte-exact above."""

        position = "artifact-reference:0"
        token = "${core_documents/character_sheets.md}"
        palette = SuggestionPalette(
            "$c",
            (Choice(position, token),),
            highlighted_id=position,
            title="Writing artifact selection",
            instruction="Up/Down selects; Enter/Tab inserts a whole-file reference.",
            empty_text="No matching writing artifacts. Change the path fragment.",
        )
        self.assertEqual(
            render_component(palette, capabilities(CapabilityProfile.PLAIN)),
            RenderedFrame(
                "Writing artifact selection [open]\n"
                "  [controls] Up/Down selects; Enter/Tab inserts a whole-file "
                "reference.\n"
                f"  > [highlighted] {token}",
                80,
                3,
            ),
        )

        empty = replace(
            palette,
            candidates=(),
            highlighted_id=None,
            empty_text=(
                "No writing artifacts. Add a supported canonical core document "
                "or Markdown file with a lowercase .md suffix under manuscript/. "
                "Consensus can also publish one."
            ),
        )
        empty_frame = render_component(
            empty,
            capabilities(CapabilityProfile.PLAIN),
        )
        self.assertEqual(
            empty_frame,
            RenderedFrame(
                "Writing artifact selection [open]\n"
                "  [controls] Up/Down selects; Enter/Tab inserts a whole-file "
                "reference.\n"
                "  [empty] No writing artifacts. Add a supported canonical core "
                "document or Markd\n"
                "  own file with a lowercase .md suffix under manuscript/. "
                "Consensus can also pub\n"
                "  lish one.",
                80,
                5,
            ),
        )
        for line in empty_frame.text.splitlines():
            with self.subTest(rendered_line=line):
                self.assertEqual(line, line.rstrip())

        ranged_path = "manuscript/older drafts/{世界$ 章#1.md"
        ranged = replace(
            palette,
            buffer="$$",
            candidates=(Choice("ranged", ranged_path),),
            highlighted_id="ranged",
            instruction="Up/Down selects; Enter/Tab opens the ranged viewer.",
        )
        self.assertEqual(
            render_component(ranged, capabilities(CapabilityProfile.PLAIN)),
            RenderedFrame(
                "Writing artifact selection [open]\n"
                "  [controls] Up/Down selects; Enter/Tab opens the ranged viewer.\n"
                f"  > [highlighted] {ranged_path}",
                80,
                3,
            ),
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

    def test_core_reference_bindings_inject_exact_matcher_and_validate_it(
        self,
    ) -> None:
        bindings = _core._reference_input_bindings(
            mock.Mock(return_value=None),
            mock.Mock(return_value="text"),
            mock.Mock(),
        )

        self.assertIs(
            bindings.completion_candidate_matches,
            completion_candidate_matches,
        )
        with self.assertRaisesRegex(
            TypeError,
            "^completion_candidate_matches must be callable$",
        ):
            replace(bindings, completion_candidate_matches=None)

    def test_reference_projection_invalid_probe_skips_catalog_io(self) -> None:
        for buffer in (
            "$${core_documents/global_outline.md",
            "$${../escape.md}",
        ):
            with self.subTest(buffer=buffer):
                scope_provider = mock.Mock(
                    return_value=ReferenceInputScope(0, len(buffer))
                )
                paths_provider = mock.Mock(
                    side_effect=AssertionError("catalog must not be read")
                )
                provider = _core._reference_projection_provider(
                    scope_provider,
                    paths_provider,
                )

                self.assertEqual(
                    provider(buffer, len(buffer)),
                    ReferenceInputProjection(
                        failure=ReferenceInputFailureKind.INVALID,
                        scope=ReferenceInputScope(0, len(buffer)),
                    ),
                )
                scope_provider.assert_called_once_with(buffer, len(buffer))
                paths_provider.assert_not_called()

    def test_reference_projection_complete_single_directive_skips_catalog_io(
        self,
    ) -> None:
        path = "core_documents/global_outline.md"
        marker = f"${{{path}}}"
        for suffix in ("", ".", ",", ")", "word", "#L2-L3", "#L2-L3word"):
            with self.subTest(suffix=suffix):
                buffer = f"use {marker}{suffix}"
                cursor = buffer.index("$") + 1
                scope_provider = mock.Mock(
                    return_value=ReferenceInputScope(0, len(buffer))
                )
                paths_provider = mock.Mock(
                    side_effect=AssertionError("catalog must not be read")
                )
                provider = _core._reference_projection_provider(
                    scope_provider,
                    paths_provider,
                )

                self.assertIsNone(provider(buffer, cursor))
                scope_provider.assert_called_once_with(buffer, cursor)
                paths_provider.assert_not_called()

    def test_reference_projection_catalog_outcomes_have_one_exact_read(self) -> None:
        path = "core_documents/global_outline.md"
        other_path = "core_documents/character_sheets.md"
        token = f"${{{path}}}"
        position = "artifact-reference:0"
        cases = (
            (
                "$",
                (path,),
                None,
                ReferenceInputProjection(
                    completion=CommandCompletionProjection(
                        position,
                        0,
                        1,
                        (CommandCompletionCandidate(position, token),),
                    ),
                    scope=ReferenceInputScope(0, 1),
                ),
            ),
            (
                "$$",
                (path,),
                None,
                ReferenceInputProjection(
                    completion=CommandCompletionProjection(
                        f"{RANGED_ARTIFACT_REFERENCE_POSITION_PREFIX}0",
                        0,
                        2,
                        (
                            CommandCompletionCandidate(
                                f"{RANGED_ARTIFACT_REFERENCE_POSITION_PREFIX}0",
                                path,
                            ),
                        ),
                    ),
                    request=ReferenceActivationRequest(
                        ReferenceActivationKind.PICK,
                        0,
                        2,
                        ReferenceInputScope(0, 2),
                        (path,),
                    ),
                    scope=ReferenceInputScope(0, 2),
                ),
            ),
            (
                f"$${{{path}}}",
                (path,),
                None,
                ReferenceInputProjection(
                    request=ReferenceActivationRequest(
                        ReferenceActivationKind.VIEW,
                        0,
                        len(f"$${{{path}}}"),
                        ReferenceInputScope(0, len(f"$${{{path}}}")),
                        path=path,
                    ),
                    scope=ReferenceInputScope(0, len(f"$${{{path}}}")),
                ),
            ),
            (
                "$",
                (),
                None,
                ReferenceInputProjection(
                    completion=CommandCompletionProjection(
                        "artifact-reference:empty:0",
                        0,
                        1,
                        (),
                    ),
                    failure=ReferenceInputFailureKind.EMPTY_SOURCES,
                    scope=ReferenceInputScope(0, 1),
                ),
            ),
            (
                "$$",
                (),
                None,
                ReferenceInputProjection(
                    completion=CommandCompletionProjection(
                        f"{EMPTY_RANGED_ARTIFACT_REFERENCE_POSITION_PREFIX}0",
                        0,
                        2,
                        (),
                    ),
                    failure=ReferenceInputFailureKind.EMPTY_SOURCES,
                    scope=ReferenceInputScope(0, 2),
                ),
            ),
            (
                "$",
                (),
                ReferenceInputFailure(ReferenceInputFailureKind.UNAVAILABLE),
                ReferenceInputProjection(
                    failure=ReferenceInputFailureKind.UNAVAILABLE,
                    scope=ReferenceInputScope(0, 1),
                ),
            ),
            (
                f"$${{{path}}}",
                (other_path,),
                None,
                ReferenceInputProjection(
                    failure=ReferenceInputFailureKind.STALE,
                    scope=ReferenceInputScope(0, len(f"$${{{path}}}")),
                ),
            ),
        )
        for buffer, paths, failure, expected in cases:
            with self.subTest(buffer=buffer, expected=expected):
                scope_provider = mock.Mock(
                    return_value=ReferenceInputScope(0, len(buffer))
                )
                paths_provider = mock.Mock(
                    return_value=paths,
                    side_effect=failure,
                )
                provider = _core._reference_projection_provider(
                    scope_provider,
                    paths_provider,
                )

                self.assertEqual(provider(buffer, len(buffer)), expected)
                scope_provider.assert_called_once_with(buffer, len(buffer))
                paths_provider.assert_called_once_with()

    def test_run_output_prefix_is_excluded_from_prompt_reference_validation(
        self,
    ) -> None:
        path = "core_documents/global_outline.md"
        prefix = "/run --output ${draft "

        def scope(buffer: str, cursor: int) -> ReferenceInputScope | None:
            span = project_provider_text_span(buffer, cursor)
            if span is None:
                return None
            return ReferenceInputScope(span.start, span.end)

        for marker, kind in (("$", "completion"), ("$$", "range")):
            line = f"{prefix}{marker}"
            for fallback in (False, True):
                with self.subTest(marker=marker, fallback=fallback):
                    scope_provider = mock.Mock(side_effect=scope)
                    paths_provider = mock.Mock(return_value=(path,))
                    provider = _core._reference_projection_provider(
                        scope_provider,
                        paths_provider,
                    )
                    if fallback:
                        projected = _driver._fallback_reference_projection(
                            line,
                            _core._reference_input_bindings(
                                provider,
                                mock.Mock(),
                                mock.Mock(),
                            ),
                        )
                    else:
                        projected = provider(line, len(line))

                    assert projected is not None
                    self.assertEqual(
                        scope_provider.call_args_list,
                        [mock.call(line, len(line))],
                    )
                    paths_provider.assert_called_once_with()
                    state = MainCommandControlState(
                        MainInputControlState(LineInputState(line, len(line)))
                    )
                    if kind == "completion":
                        assert projected.completion is not None
                        replacement = projected.completion.candidates[0].replacement
                        updated = replace_reference_completion(
                            state,
                            projected,
                            replacement,
                        )
                    else:
                        assert projected.request is not None
                        selected = selected_reference_request(
                            projected.request,
                            path,
                        )
                        updated = replace_reference_range(state, selected, 2, 3)
                        replacement = f"${{{path}}}#L2-L3"
                    self.assertEqual(
                        updated.main_input.line_state.buffer,
                        f"{prefix}{replacement}",
                    )

        invalid = f"{prefix}$${{{path}"
        invalid_paths = mock.Mock(
            side_effect=AssertionError("invalid eligible marker read catalog")
        )
        invalid_provider = _core._reference_projection_provider(scope, invalid_paths)
        invalid_scope = scope(invalid, len(invalid))
        assert invalid_scope is not None
        self.assertEqual(
            invalid_provider(invalid, len(invalid)),
            ReferenceInputProjection(
                failure=ReferenceInputFailureKind.INVALID,
                scope=invalid_scope,
            ),
        )
        invalid_paths.assert_not_called()

    def test_supported_live_read_selects_commits_appends_and_restores_once(
        self,
    ) -> None:
        driver, _input, output, fallback, termios, os_module = make_driver(
            "/s\x1b[B\t\n"
        )
        with mock.patch.object(
            _driver._select,
            "select",
            side_effect=lambda readable, writable, exceptional, _timeout: (
                readable, writable, exceptional
            ),
        ) as readiness:
            self.assertEqual(self.read(driver), "/settings ")

        fallback.assert_not_called()
        self.assertEqual(
            driver._history_state,
            MainInputHistoryState(("/settings ",)),
        )
        self.assertEqual(os_module.observed_widths, [8, 8])
        self.assertEqual(
            tuple(call[1] for call in termios.set_calls),
            (FakeTermios.TCSANOW, FakeTermios.TCSADRAIN),
        )
        readiness.assert_called_once_with((7,), (), (), 0.05)
        rendered = "".join(output.records)
        self.assertIn("Suggestions [open]", rendered)
        self.assertIn("[highlighted] /settings", rendered)
        self.assertNotIn("[suggestions]", rendered)

    def test_reference_completion_and_nested_viewers_share_one_latch_and_history(
        self,
    ) -> None:
        character = "core_documents/character_sheets.md"
        outline = "core_documents/global_outline.md"
        complex_path = "manuscript/older drafts/{世界$ 章#1.md"
        root_path = "manuscript/root.md"
        paths = (
            character,
            outline,
            complex_path,
            root_path,
        )
        cases = (
            (
                "$\t\n",
                f"${{{character}}} ",
                0,
                0,
                (),
                False,
            ),
            (
                f"use $${{{character}}}\n\x1b[1;2B\n\n",
                f"use ${{{character}}}#L1-L2",
                1,
                1,
                (character,),
                False,
            ),
            (
                "use $$\x1b[B\n\x1b[1;2B\n\n",
                f"use ${{{outline}}}#L1-L2",
                1,
                1,
                (outline,),
                True,
            ),
            (
                "use $$\t\x1b[1;2B\n\n",
                f"use ${{{character}}}#L1-L2",
                1,
                1,
                (character,),
                True,
            ),
            (
                "use $$\x1b\n\n\x1b[1;2B\n\n",
                f"use ${{{character}}}#L1-L2",
                1,
                1,
                (character,),
                True,
            ),
        )
        for (
            script,
            expected,
            screen_enters,
            screen_exits,
            reads,
            uses_ranged_picker,
        ) in cases:
            with self.subTest(expected=expected):
                driver, _input, output, fallback, termios, os_module = make_driver(
                    script
                )
                text_reader = mock.Mock(return_value="one\ntwo\nthree")
                failure_writer = mock.Mock()
                with mock.patch.object(
                    _driver._select,
                    "select",
                    side_effect=lambda readable, writable, exceptional, _timeout: (
                        readable,
                        writable,
                        exceptional,
                    ),
                ):
                    line = _core.read_main_command_line(
                        driver,
                        "main> ",
                        reference_scope_provider=lambda buffer, _cursor: (
                            ReferenceInputScope(0, len(buffer))
                        ),
                        reference_paths_provider=lambda: paths,
                        reference_text_reader=text_reader,
                        reference_failure_writer=failure_writer,
                    )

                self.assertEqual(line, expected)
                self.assertEqual(
                    driver._history_state,
                    MainInputHistoryState((expected,)),
                )
                self.assertEqual(
                    tuple(call[1] for call in termios.set_calls),
                    (FakeTermios.TCSANOW, FakeTermios.TCSADRAIN),
                )
                rendered = "".join(output.records)
                self.assertEqual(rendered.count("\x1b[?1049h"), screen_enters)
                self.assertEqual(rendered.count("\x1b[?1049l"), screen_exits)
                self.assertEqual(
                    tuple(call.args[0] for call in text_reader.call_args_list),
                    reads,
                )
                if uses_ranged_picker:
                    self.assertIn(
                        "Up/Downselects;Enter/Tabopenstherangedviewer.",
                        "".join(strip_decorative_sgr(rendered).split()),
                    )
                    for path in paths:
                        self.assertIn(
                            "".join(path.split()),
                            "".join(strip_decorative_sgr(rendered).split()),
                        )
                failure_writer.assert_not_called()
                fallback.assert_not_called()
                self.assertEqual(os_module.observed_widths[:2], [8, 8])

    def test_reference_live_fragments_filter_and_complete_without_submission(
        self,
    ) -> None:
        character, outline, rules = (
            "core_documents/character_sheets.md",
            "core_documents/global_outline.md",
            "core_documents/narrative_rules.md",
        )
        paths = (character, outline, rules)
        tokens = tuple(f"${{{path}}}" for path in paths)
        earlier = tokens[2]
        contextual = f"keep {earlier} then $c suffix"
        cases = (
            ("$", "\t", f"{tokens[0]} ", tokens),
            ("$c", "\n", f"{tokens[0]} ", tokens),
            ("$core_documents/g", "\t", f"{tokens[1]} ", (tokens[1],)),
            ("${core", "\n", f"{tokens[0]} ", tokens),
            (
                contextual,
                "\x1b[D" * len(" suffix") + "\t",
                f"keep {earlier} then {tokens[0]} suffix",
                tokens[:2],
            ),
        )
        for original, completion_input, expected, visible in cases:
            with self.subTest(original=original):
                driver, input_stream, output, fallback, termios, os_module = (
                    make_driver(original + completion_input + "\x03")
                )

                def wide_size(descriptor: int) -> object:
                    os_module.observed_widths.append(descriptor)
                    return SimpleNamespace(columns=160, lines=24)

                os_module.get_terminal_size = wide_size
                history_append = mock.Mock(wraps=append_main_input_history)
                driver._history_append = history_append
                text_reader = mock.Mock(
                    side_effect=AssertionError("completion must not read text")
                )
                failure_writer = mock.Mock()
                with mock.patch.object(
                    _driver._select,
                    "select",
                    side_effect=lambda readable, writable, exceptional, _timeout: (
                        readable,
                        writable,
                        exceptional,
                    ),
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        _core.read_main_command_line(
                            driver,
                            "main> ",
                            reference_scope_provider=lambda buffer, _cursor: (
                                ReferenceInputScope(0, len(buffer))
                            ),
                            reference_paths_provider=lambda: paths,
                            reference_text_reader=text_reader,
                            reference_failure_writer=failure_writer,
                        )

                active_frames = tuple(
                    strip_decorative_sgr(record)
                    for record in output.records
                    if f"main> {original}" in strip_decorative_sgr(record)
                    and "Writing artifact selection [open]"
                    in strip_decorative_sgr(record)
                )
                self.assertTrue(active_frames)
                active = active_frames[-1]
                suggestions = active.split(
                    "Writing artifact selection [open]",
                    1,
                )[1]
                self.assertIn(
                    "[controls] Up/Down selects; Enter/Tab inserts a whole-file "
                    "reference.",
                    suggestions,
                )
                for token in tokens:
                    if token in visible:
                        self.assertIn(token, suggestions)
                    else:
                        self.assertNotIn(token, suggestions)
                self.assertIn("[highlighted]", suggestions)
                self.assertIn(
                    f"main> {expected}",
                    strip_decorative_sgr("".join(output.records)),
                )
                history_append.assert_not_called()
                self.assertEqual(driver._history_state, MainInputHistoryState())
                self.assertEqual(input_stream.reads, [])
                text_reader.assert_not_called()
                failure_writer.assert_not_called()
                fallback.assert_not_called()
                self.assertEqual(
                    tuple(call[1] for call in termios.set_calls),
                    (FakeTermios.TCSANOW, FakeTermios.TCSADRAIN),
                )

        driver, _input, output, fallback, _termios, os_module = make_driver(
            "$C\t\x03"
        )
        os_module.get_terminal_size = lambda _descriptor: SimpleNamespace(
            columns=160,
            lines=24,
        )
        history_append = mock.Mock(wraps=append_main_input_history)
        driver._history_append = history_append
        with mock.patch.object(
            _driver._select,
            "select",
            side_effect=lambda readable, writable, exceptional, _timeout: (
                readable,
                writable,
                exceptional,
            ),
        ):
            with self.assertRaises(KeyboardInterrupt):
                _core.read_main_command_line(
                    driver,
                    "main> ",
                    reference_scope_provider=lambda buffer, _cursor: (
                        ReferenceInputScope(0, len(buffer))
                    ),
                    reference_paths_provider=lambda: paths,
                    reference_text_reader=mock.Mock(),
                    reference_failure_writer=mock.Mock(),
                )
        unmatched = next(
            strip_decorative_sgr(record)
            for record in output.records
            if "main> $C" in strip_decorative_sgr(record)
            and "Writing artifact selection [open]"
            in strip_decorative_sgr(record)
        )
        self.assertIn(
            "[empty] No matching writing artifacts. Change the path fragment.",
            unmatched,
        )
        self.assertNotIn("[highlighted]", unmatched)
        history_append.assert_not_called()
        self.assertEqual(driver._history_state, MainInputHistoryState())
        fallback.assert_not_called()

    def test_reference_live_editor_matches_directive_escape_parity(self) -> None:
        path = "manuscript/older drafts/{世界$ 章#1.md"
        other = "manuscript/root.md"
        complete = f"${{{path}}}"
        cases = (
            (f"\\${{{path}", False),
            (f"\\$${{{path}", True),
            (f"\\\\${{{path}", True),
            (f"literal {complete} then $", True),
        )
        for original, active in cases:
            with self.subTest(original=original):
                driver, _input, output, fallback, termios, os_module = make_driver(
                    original + "\x03"
                )
                os_module.get_terminal_size = lambda _descriptor: SimpleNamespace(
                    columns=160,
                    lines=24,
                )
                paths_provider = mock.Mock(return_value=(path, other))
                with mock.patch.object(
                    _driver._select,
                    "select",
                    side_effect=lambda readable, writable, exceptional, _timeout: (
                        readable,
                        writable,
                        exceptional,
                    ),
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        _core.read_main_command_line(
                            driver,
                            "main> ",
                            reference_scope_provider=lambda buffer, _cursor: (
                                ReferenceInputScope(0, len(buffer))
                            ),
                            reference_paths_provider=paths_provider,
                            reference_text_reader=mock.Mock(),
                            reference_failure_writer=mock.Mock(),
                        )

                exact_frames = tuple(
                    strip_decorative_sgr(record)
                    for record in output.records
                    if f"main> {original}" in strip_decorative_sgr(record)
                )
                self.assertTrue(exact_frames)
                self.assertEqual(
                    any(
                        "Writing artifact selection [open]" in frame
                        for frame in exact_frames
                    ),
                    active,
                )
                if active:
                    paths_provider.assert_called()
                else:
                    paths_provider.assert_not_called()
                self.assertEqual(driver._history_state, MainInputHistoryState())
                fallback.assert_not_called()
                self.assertEqual(
                    tuple(call[1] for call in termios.set_calls),
                    (FakeTermios.TCSANOW, FakeTermios.TCSADRAIN),
                )

    def test_empty_ranged_picker_renders_before_deferred_live_failure(self) -> None:
        for activation_key in ("\n", "\t"):
            with self.subTest(activation_key=repr(activation_key)):
                driver, _input, output, fallback, termios, os_module = make_driver(
                    "$$\x1b" + activation_key + activation_key + "\x03"
                )
                reader = mock.Mock(
                    side_effect=AssertionError("empty sources must not read text")
                )
                failure_records: list[tuple[ReferenceInputFailureKind, int]] = []

                def write_failure(kind: ReferenceInputFailureKind) -> None:
                    failure_records.append((kind, len(output.records)))

                with mock.patch.object(
                    _driver._select,
                    "select",
                    side_effect=lambda readable, writable, exceptional, _timeout: (
                        readable,
                        writable,
                        exceptional,
                    ),
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        _core.read_main_command_line(
                            driver,
                            "main> ",
                            reference_scope_provider=lambda buffer, _cursor: (
                                ReferenceInputScope(0, len(buffer))
                            ),
                            reference_paths_provider=lambda: (),
                            reference_text_reader=reader,
                            reference_failure_writer=write_failure,
                        )

                frames = [strip_decorative_sgr(record) for record in output.records]
                picker_index = next(
                    index
                    for index, frame in enumerate(frames)
                    if "main> $$" in frame
                    and "Writing artifact selection [open]" in frame
                    and "No writing artifacts." in frame
                )
                dismissed_index = next(
                    index
                    for index, frame in enumerate(
                        frames[picker_index + 1 :],
                        picker_index + 1,
                    )
                    if "main> $$" in frame
                    and "Writing artifact selection [open]" not in frame
                )
                reopened_index = next(
                    index
                    for index, frame in enumerate(
                        frames[dismissed_index + 1 :],
                        dismissed_index + 1,
                    )
                    if "main> $$" in frame
                    and "Writing artifact selection [open]" in frame
                    and "No writing artifacts." in frame
                )
                self.assertEqual(len(failure_records), 1)
                self.assertIs(
                    failure_records[0][0],
                    ReferenceInputFailureKind.EMPTY_SOURCES,
                )
                self.assertLess(picker_index, dismissed_index)
                self.assertLess(dismissed_index, reopened_index)
                self.assertLess(reopened_index, failure_records[0][1])
                self.assertIn(
                    "Enter/Tabopenstherangedviewer.",
                    "".join(frames[picker_index].split()),
                )
                reader.assert_not_called()
                self.assertNotIn("\x1b[?1049h", "".join(output.records))
                self.assertNotIn("\x1b[?1049l", "".join(output.records))
                self.assertEqual(driver._history_state, MainInputHistoryState())
                fallback.assert_not_called()
                self.assertEqual(os_module.observed_widths[:2], [8, 8])
                self.assertEqual(
                    tuple(call[1] for call in termios.set_calls),
                    (FakeTermios.TCSANOW, FakeTermios.TCSADRAIN),
                )

    def test_reference_fallback_retains_amendment_until_second_confirmation(
        self,
    ) -> None:
        path = "core_documents/global_outline.md"
        marker = f"use $${{{path}}}"
        driver, input_stream, output, fallback, termios, os_module = make_driver(
            "unused",
            terminal=False,
        )
        fallback.side_effect = (marker, "2-3", "")
        text_reader = mock.Mock(return_value="one\ntwo\nthree")
        paths_provider = mock.Mock(return_value=(path,))

        result = _core.read_main_command_line(
            driver,
            "main> ",
            reference_scope_provider=lambda buffer, _cursor: (
                ReferenceInputScope(0, len(buffer))
            ),
            reference_paths_provider=paths_provider,
            reference_text_reader=text_reader,
            reference_failure_writer=mock.Mock(),
        )

        expected = f"use ${{{path}}}#L2-L3"
        self.assertEqual(result, expected)
        self.assertEqual(driver._history_state, MainInputHistoryState((expected,)))
        self.assertEqual(fallback.call_count, 3)
        self.assertEqual(fallback.call_args_list[0], mock.call("main> "))
        self.assertIn(
            "Select START-END or /cancel: ",
            fallback.call_args_list[1].args[0],
        )
        self.assertEqual(
            fallback.call_args_list[2],
            mock.call(
                f"Draft retained: {expected}\n"
                "Press Enter to submit it, or type a replacement draft: "
            ),
        )
        paths_provider.assert_called_once_with()
        text_reader.assert_called_once_with(path)
        self.assertNotIn(("read", 1), input_stream.calls)
        self.assertEqual(output.records, [])
        self.assertEqual(termios.set_calls, [])
        self.assertEqual(os_module.observed_widths, [])

    def test_reference_fallback_unamended_failure_retries_before_replacement(
        self,
    ) -> None:
        path = "core_documents/global_outline.md"
        marker = f"use $${{{path}}}"
        replacement = "use a replacement draft"
        retry_prompt = (
            f"Draft retained: {marker}\n"
            "Press Enter to retry the marker without submitting, or type "
            "a replacement draft: "
        )
        driver, input_stream, output, fallback, termios, os_module = make_driver(
            "unused",
            terminal=False,
        )
        fallback.side_effect = (marker, "", replacement)
        scope_provider = mock.Mock(
            side_effect=lambda buffer, _cursor: ReferenceInputScope(0, len(buffer))
        )
        paths_provider = mock.Mock(
            side_effect=ReferenceInputFailure(ReferenceInputFailureKind.UNAVAILABLE)
        )
        text_reader = mock.Mock(side_effect=AssertionError("viewer text read"))
        failure_writer = mock.Mock()

        result = _core.read_main_command_line(
            driver,
            "main> ",
            reference_scope_provider=scope_provider,
            reference_paths_provider=paths_provider,
            reference_text_reader=text_reader,
            reference_failure_writer=failure_writer,
        )

        self.assertEqual(result, replacement)
        self.assertEqual(
            driver._history_state,
            MainInputHistoryState((replacement,)),
        )
        self.assertEqual(
            scope_provider.call_args_list,
            [
                mock.call(marker, marker.rfind("$") + 1),
                mock.call(marker, marker.rfind("$") + 1),
            ],
        )
        self.assertEqual(paths_provider.call_count, 2)
        self.assertEqual(
            failure_writer.call_args_list,
            [
                mock.call(ReferenceInputFailureKind.UNAVAILABLE),
                mock.call(ReferenceInputFailureKind.UNAVAILABLE),
            ],
        )
        self.assertEqual(
            fallback.call_args_list,
            [mock.call("main> "), mock.call(retry_prompt), mock.call(retry_prompt)],
        )
        self.assertNotIn(
            "Press Enter to submit it",
            "".join(call.args[0] for call in fallback.call_args_list),
        )
        text_reader.assert_not_called()
        self.assertNotIn(("read", 1), input_stream.calls)
        self.assertEqual(output.records, [])
        self.assertEqual(termios.set_calls, [])
        self.assertEqual(os_module.observed_widths, [])

    def test_reference_fallback_cancel_retains_marker_with_retry_prompt(self) -> None:
        path = "core_documents/global_outline.md"
        marker = f"use $${{{path}}}"
        replacement = "use a replacement draft"
        retry_prompt = (
            f"Draft retained: {marker}\n"
            "Press Enter to retry the marker without submitting, or type "
            "a replacement draft: "
        )
        driver, input_stream, output, fallback, termios, os_module = make_driver(
            "unused",
            terminal=False,
        )
        fallback.side_effect = (marker, "/cancel", replacement)
        scope_provider = mock.Mock(
            side_effect=lambda buffer, _cursor: ReferenceInputScope(0, len(buffer))
        )
        paths_provider = mock.Mock(return_value=(path,))
        text_reader = mock.Mock(return_value="one\ntwo\nthree")
        failure_writer = mock.Mock()

        result = _core.read_main_command_line(
            driver,
            "main> ",
            reference_scope_provider=scope_provider,
            reference_paths_provider=paths_provider,
            reference_text_reader=text_reader,
            reference_failure_writer=failure_writer,
        )

        self.assertEqual(result, replacement)
        self.assertEqual(
            driver._history_state,
            MainInputHistoryState((replacement,)),
        )
        self.assertEqual(
            scope_provider.call_args_list,
            [
                mock.call(marker, marker.rfind("$") + 1),
            ],
        )
        paths_provider.assert_called_once_with()
        text_reader.assert_called_once_with(path)
        failure_writer.assert_not_called()
        self.assertEqual(fallback.call_count, 3)
        self.assertIn(
            "Select START-END or /cancel: ",
            fallback.call_args_list[1].args[0],
        )
        self.assertEqual(fallback.call_args_list[2], mock.call(retry_prompt))
        self.assertNotIn(
            "Press Enter to submit it",
            fallback.call_args_list[2].args[0],
        )
        self.assertNotIn(("read", 1), input_stream.calls)
        self.assertEqual(output.records, [])
        self.assertEqual(termios.set_calls, [])
        self.assertEqual(os_module.observed_widths, [])

    def test_reference_fallback_completion_uses_bounded_span_and_preserves_suffix(
        self,
    ) -> None:
        original = "prefix $c suffix"
        path = "core_documents/global_outline.md"
        token = f"${{{path}}}"
        position = "artifact-reference:7"
        projection = CommandCompletionProjection(
            position,
            7,
            9,
            (CommandCompletionCandidate(position, token),),
        )
        prefix = original[projection.start : projection.end]
        self.assertTrue(completion_candidate_matches(token, prefix))
        self.assertFalse(token.startswith(prefix))

        driver, input_stream, output, fallback, termios, os_module = make_driver(
            "unused",
            terminal=False,
        )
        fallback.side_effect = (original, "1", "")
        projection_provider = mock.Mock(
            return_value=ReferenceInputProjection(
                completion=projection,
                scope=ReferenceInputScope(0, len(original)),
            )
        )
        failure_writer = mock.Mock()
        bindings = _core._reference_input_bindings(
            projection_provider,
            mock.Mock(side_effect=AssertionError("viewer text read")),
            failure_writer,
        )
        picker_factory = mock.Mock(wraps=bindings.initialize_picker)
        bindings = replace(bindings, initialize_picker=picker_factory)

        result = driver.read_reference_main_command_line(
            "main> ",
            _core._main_command_completion,
            project_main_suggestion_palette,
            render_component,
            bindings,
        )

        expected = f"prefix {token} suffix"
        self.assertEqual(result, expected)
        self.assertEqual(driver._history_state, MainInputHistoryState((expected,)))
        projection_provider.assert_called_once_with(
            original,
            original.rfind("$") + 1,
        )
        picker_factory.assert_called_once_with(
            (token,),
            "Select a whole-file reference",
        )
        failure_writer.assert_not_called()
        self.assertEqual(fallback.call_count, 3)
        self.assertEqual(fallback.call_args_list[0], mock.call("main> "))
        self.assertEqual(
            fallback.call_args_list[2],
            mock.call(
                f"Draft retained: {expected}\n"
                "Press Enter to submit it, or type a replacement draft: "
            ),
        )
        self.assertNotIn(("read", 1), input_stream.calls)
        self.assertEqual(output.records, [])
        self.assertEqual(termios.set_calls, [])
        self.assertEqual(os_module.observed_widths, [])

    def test_reference_fallback_embedded_markers_preserve_exact_suffixes(
        self,
    ) -> None:
        path = "core_documents/character_sheets.md"
        token = f"${{{path}}}"
        cases = (
            (
                "prefix $ suffix",
                ("1", ""),
                f"prefix {token} suffix",
                (),
            ),
            (
                "prefix $$ suffix",
                ("1", "2-3", ""),
                f"prefix {token}#L2-L3 suffix",
                (path,),
            ),
            (
                f"use $${{{path}}}.",
                ("2-3", ""),
                f"use {token}#L2-L3.",
                (path,),
            ),
        )
        for original, answers, expected, reads in cases:
            with self.subTest(original=original):
                driver, input_stream, output, fallback, termios, os_module = (
                    make_driver("unused", terminal=False)
                )
                fallback.side_effect = (original, *answers)
                scope_provider = mock.Mock(
                    side_effect=lambda buffer, _cursor: ReferenceInputScope(
                        0,
                        len(buffer),
                    )
                )
                paths_provider = mock.Mock(return_value=(path,))
                text_reader = mock.Mock(return_value="one\ntwo\nthree")
                failure_writer = mock.Mock()

                result = _core.read_main_command_line(
                    driver,
                    "main> ",
                    reference_scope_provider=scope_provider,
                    reference_paths_provider=paths_provider,
                    reference_text_reader=text_reader,
                    reference_failure_writer=failure_writer,
                )

                self.assertEqual(result, expected)
                self.assertEqual(
                    driver._history_state,
                    MainInputHistoryState((expected,)),
                )
                scope_provider.assert_called_once_with(
                    original,
                    original.rfind("$") + 1,
                )
                paths_provider.assert_called_once_with()
                self.assertEqual(
                    tuple(call.args[0] for call in text_reader.call_args_list),
                    reads,
                )
                failure_writer.assert_not_called()
                self.assertEqual(
                    fallback.call_args_list[-1],
                    mock.call(
                        f"Draft retained: {expected}\n"
                        "Press Enter to submit it, or type a replacement draft: "
                    ),
                )
                self.assertNotIn(("read", 1), input_stream.calls)
                self.assertEqual(output.records, [])
                self.assertEqual(termios.set_calls, [])
                self.assertEqual(os_module.observed_widths, [])

    def test_reference_fallback_rightmost_failure_has_exact_precedence(self) -> None:
        original = "left $ marker then right $ marker"
        replacement = "replacement draft"
        for failure in (
            ReferenceInputFailureKind.INVALID,
            ReferenceInputFailureKind.UNAVAILABLE,
        ):
            with self.subTest(failure=failure):
                driver, _input, _output, fallback, _termios, _os = make_driver(
                    "unused",
                    terminal=False,
                )
                fallback.side_effect = (original, replacement)
                projection_provider = mock.Mock(
                    return_value=ReferenceInputProjection(failure=failure)
                )
                failure_writer = mock.Mock()
                bindings = _core._reference_input_bindings(
                    projection_provider,
                    mock.Mock(side_effect=AssertionError("viewer text read")),
                    failure_writer,
                )

                result = driver.read_reference_main_command_line(
                    "main> ",
                    _core._main_command_completion,
                    project_main_suggestion_palette,
                    render_component,
                    bindings,
                )

                self.assertEqual(result, replacement)
                self.assertEqual(
                    driver._history_state,
                    MainInputHistoryState((replacement,)),
                )
                projection_provider.assert_called_once_with(
                    original,
                    original.rfind("$") + 1,
                )
                failure_writer.assert_called_once_with(failure)
                self.assertIn(original, fallback.call_args_list[1].args[0])

    def test_reference_fallback_skips_escaped_out_of_scope_and_no_dollar(
        self,
    ) -> None:
        cases = (
            ("prefix \\$ suffix", None, 1),
            ("prefix $ suffix", ReferenceInputScope(0, 6), 1),
            ("plain text", None, 0),
        )
        for line, fixed_scope, expected_projections in cases:
            with self.subTest(line=line):
                driver, input_stream, output, fallback, termios, os_module = (
                    make_driver("unused", terminal=False)
                )
                fallback.return_value = line
                scope_provider = mock.Mock(
                    side_effect=lambda buffer, _cursor: (
                        fixed_scope
                        if fixed_scope is not None
                        else ReferenceInputScope(0, len(buffer))
                    )
                )
                paths_provider = mock.Mock(
                    side_effect=AssertionError("catalog must not be read")
                )
                text_reader = mock.Mock(
                    side_effect=AssertionError("viewer text must not be read")
                )
                failure_writer = mock.Mock()

                result = _core.read_main_command_line(
                    driver,
                    "main> ",
                    reference_scope_provider=scope_provider,
                    reference_paths_provider=paths_provider,
                    reference_text_reader=text_reader,
                    reference_failure_writer=failure_writer,
                )

                self.assertEqual(result, line)
                self.assertEqual(
                    driver._history_state,
                    MainInputHistoryState((line,)),
                )
                self.assertEqual(scope_provider.call_count, expected_projections)
                if expected_projections:
                    scope_provider.assert_called_once_with(
                        line,
                        line.rfind("$") + 1,
                    )
                paths_provider.assert_not_called()
                text_reader.assert_not_called()
                failure_writer.assert_not_called()
                fallback.assert_called_once_with("main> ")
                self.assertNotIn(("read", 1), input_stream.calls)
                self.assertEqual(output.records, [])
                self.assertEqual(termios.set_calls, [])
                self.assertEqual(os_module.observed_widths, [])

    def test_reference_fallback_braced_spans_shield_nested_dollars(self) -> None:
        path = "manuscript/older drafts/{世界$ 章#1.md"
        complete_whole = f"${{{path}}}"
        unfinished_whole = complete_whole[:-1]
        complete_direct = f"$${{{path}}}"
        unfinished_direct = complete_direct[:-1]
        later = f"literal {complete_whole} then $"

        def raw_cursors(line: str) -> tuple[int, ...]:
            return tuple(
                position + 1
                for position, character in reversed(tuple(enumerate(line)))
                if character == "$"
            )

        cases = (
            (
                complete_whole,
                None,
                None,
                raw_cursors(complete_whole),
                False,
            ),
            (
                unfinished_whole,
                "completion",
                0,
                (unfinished_whole.rfind("$") + 1,),
                True,
            ),
            (
                complete_direct,
                "view",
                0,
                (complete_direct.rfind("$") + 1,),
                True,
            ),
            (
                unfinished_direct,
                "invalid",
                None,
                (unfinished_direct.rfind("$") + 1,),
                False,
            ),
            (
                f"\\{complete_whole}",
                None,
                None,
                raw_cursors(f"\\{complete_whole}"),
                False,
            ),
            (
                f"\\{unfinished_whole}",
                None,
                None,
                raw_cursors(f"\\{unfinished_whole}"),
                False,
            ),
            (
                f"\\{complete_direct}",
                None,
                None,
                raw_cursors(f"\\{complete_direct}"),
                False,
            ),
            (
                f"\\{unfinished_direct}",
                "completion",
                2,
                (f"\\{unfinished_direct}".rfind("$") + 1,),
                True,
            ),
            (
                f"\\\\{unfinished_whole}",
                "completion",
                2,
                (f"\\\\{unfinished_whole}".rfind("$") + 1,),
                True,
            ),
            (later, "completion", later.rfind("$"), (len(later),), True),
        )
        for line, outcome, expected_start, expected_cursors, reads_paths in cases:
            with self.subTest(line=line):
                scope_provider = mock.Mock(
                    side_effect=lambda buffer, _cursor: ReferenceInputScope(
                        0,
                        len(buffer),
                    )
                )
                paths_provider = mock.Mock(
                    return_value=(path, "manuscript/root.md")
                )
                projection_provider = _core._reference_projection_provider(
                    scope_provider,
                    paths_provider,
                )
                bindings = _core._reference_input_bindings(
                    projection_provider,
                    mock.Mock(side_effect=AssertionError("text reader used")),
                    mock.Mock(),
                )

                projected = _driver._fallback_reference_projection(line, bindings)

                if outcome is None:
                    self.assertIsNone(projected)
                elif outcome == "completion":
                    assert projected is not None
                    assert projected.completion is not None
                    self.assertEqual(projected.completion.start, expected_start)
                elif outcome == "view":
                    assert projected is not None and projected.request is not None
                    self.assertIs(
                        projected.request.kind,
                        ReferenceActivationKind.VIEW,
                    )
                    self.assertEqual(projected.request.start, expected_start)
                else:
                    self.assertEqual(
                        projected,
                        ReferenceInputProjection(
                            failure=ReferenceInputFailureKind.INVALID,
                            scope=ReferenceInputScope(0, len(line)),
                        ),
                    )
                self.assertEqual(
                    scope_provider.call_args_list,
                    [mock.call(line, cursor) for cursor in expected_cursors],
                )
                if reads_paths:
                    paths_provider.assert_called_once_with()
                else:
                    paths_provider.assert_not_called()

    def test_reference_fallback_resolves_two_markers_right_to_left(self) -> None:
        character = "core_documents/character_sheets.md"
        outline = "core_documents/global_outline.md"
        character_token = f"${{{character}}}"
        outline_token = f"${{{outline}}}"
        original = "compare $core_documents/g with $c"
        after_right = f"compare $core_documents/g with {character_token}"
        expected = f"compare {outline_token} with {character_token}"
        driver, input_stream, output, fallback, termios, os_module = make_driver(
            "unused",
            terminal=False,
        )
        fallback.side_effect = (original, "1", after_right, "1", "")
        scope_provider = mock.Mock(
            side_effect=lambda buffer, _cursor: ReferenceInputScope(0, len(buffer))
        )
        paths_provider = mock.Mock(return_value=(character, outline))
        text_reader = mock.Mock(side_effect=AssertionError("viewer text read"))
        failure_writer = mock.Mock()

        result = _core.read_main_command_line(
            driver,
            "main> ",
            reference_scope_provider=scope_provider,
            reference_paths_provider=paths_provider,
            reference_text_reader=text_reader,
            reference_failure_writer=failure_writer,
        )

        self.assertEqual(result, expected)
        self.assertEqual(
            driver._history_state,
            MainInputHistoryState((expected,)),
        )
        self.assertEqual(
            scope_provider.call_args_list,
            [
                mock.call(original, original.rfind("$") + 1),
                mock.call(after_right, after_right.rfind("$") + 1),
                mock.call(after_right, after_right.find("$") + 1),
            ],
        )
        self.assertEqual(paths_provider.call_count, 2)
        text_reader.assert_not_called()
        failure_writer.assert_not_called()
        self.assertEqual(fallback.call_count, 5)
        self.assertEqual(
            fallback.call_args_list[2],
            mock.call(
                f"Draft retained: {after_right}\n"
                "Press Enter to submit it, or type a replacement draft: "
            ),
        )
        self.assertIn(outline_token, fallback.call_args_list[3].args[0])
        self.assertNotIn(character_token, fallback.call_args_list[3].args[0])
        self.assertEqual(
            fallback.call_args_list[4],
            mock.call(
                f"Draft retained: {expected}\n"
                "Press Enter to submit it, or type a replacement draft: "
            ),
        )
        self.assertNotIn(("read", 1), input_stream.calls)
        self.assertEqual(output.records, [])
        self.assertEqual(termios.set_calls, [])
        self.assertEqual(os_module.observed_widths, [])

    def test_reference_fallback_unmatched_fragment_reports_invalid_and_retries(
        self,
    ) -> None:
        original = "prefix $C suffix"
        replacement = "replacement draft"
        path = "core_documents/global_outline.md"
        token = f"${{{path}}}"
        position = "artifact-reference:7"
        projection = CommandCompletionProjection(
            position,
            7,
            9,
            (CommandCompletionCandidate(position, token),),
        )
        retry_prompt = (
            f"Draft retained: {original}\n"
            "Press Enter to retry the marker without submitting, or type "
            "a replacement draft: "
        )
        driver, input_stream, output, fallback, termios, os_module = make_driver(
            "unused",
            terminal=False,
        )
        fallback.side_effect = (original, replacement)
        projection_provider = mock.Mock(
            side_effect=(
                ReferenceInputProjection(
                    completion=projection,
                    scope=ReferenceInputScope(0, len(original)),
                ),
                None,
            )
        )
        failure_writer = mock.Mock()
        bindings = _core._reference_input_bindings(
            projection_provider,
            mock.Mock(side_effect=AssertionError("viewer text read")),
            failure_writer,
        )
        picker_factory = mock.Mock(wraps=bindings.initialize_picker)
        bindings = replace(bindings, initialize_picker=picker_factory)

        result = driver.read_reference_main_command_line(
            "main> ",
            _core._main_command_completion,
            project_main_suggestion_palette,
            render_component,
            bindings,
        )

        self.assertEqual(result, replacement)
        self.assertEqual(
            driver._history_state,
            MainInputHistoryState((replacement,)),
        )
        self.assertFalse(completion_candidate_matches(token, "$C"))
        self.assertEqual(
            projection_provider.call_args_list,
            [
                mock.call(original, original.rfind("$") + 1),
            ],
        )
        failure_writer.assert_called_once_with(ReferenceInputFailureKind.INVALID)
        picker_factory.assert_not_called()
        self.assertEqual(
            fallback.call_args_list,
            [mock.call("main> "), mock.call(retry_prompt)],
        )
        self.assertNotIn(("read", 1), input_stream.calls)
        self.assertEqual(output.records, [])
        self.assertEqual(termios.set_calls, [])
        self.assertEqual(os_module.observed_widths, [])

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
                    "/s" + sequence + "\t\n"
                )
                with mock.patch.object(
                    _driver._select,
                    "select",
                    side_effect=lambda r, w, x, _t: (r, w, x),
                ):
                    self.assertEqual(self.read(driver), "/settings ")
                fallback.assert_not_called()

        driver, _input, _output, fallback, _termios, _os = make_driver("/\x1bx\n")
        with mock.patch.object(
            _driver._select,
            "select",
            side_effect=lambda r, w, x, _t: (r, w, x),
        ):
            self.assertEqual(self.read(driver), "/x")
        fallback.assert_not_called()

    def test_post_introducer_controls_never_become_main_line_actions(self) -> None:
        cases = (
            ("enter", "", "\n", "typed\n", "typed"),
            ("tab", "/s", "\t", "\n\n", "/say "),
            ("backspace", "ab", "\x08", "\n", "ab"),
            ("delete", "ab", "\x7f", "\n", "ab"),
        )
        for introducer in ("\x1b[", "\x1bO"):
            for label, prefix, terminator, suffix, expected in cases:
                with self.subTest(
                    introducer=repr(introducer),
                    control=label,
                ):
                    driver, input_stream, _output, fallback, _termios, _os = (
                        make_driver(prefix + introducer + terminator + suffix)
                    )
                    with mock.patch.object(
                        _driver._select,
                        "select",
                        side_effect=lambda r, w, x, _t: (r, w, x),
                    ) as readiness:
                        self.assertEqual(self.read(driver), expected)
                    readiness.assert_called_once_with((7,), (), (), 0.05)
                    fallback.assert_not_called()
                    self.assertEqual(input_stream.reads, [])
                    self.assertEqual(
                        driver._history_state,
                        MainInputHistoryState((expected,)),
                    )

    def test_parameterized_ss3_and_bounded_unknown_sequences_never_leak_suffix(
        self,
    ) -> None:
        sequences = (
            "\x1bO1A",
            "\x1bO" + "1" * 61 + "A",
            "\x1bO" + "1" * 62 + "A",
            "\x1b[" + "1" * 61 + "A",
            "\x1b[" + "1" * 62 + "A",
        )
        for sequence in sequences:
            with self.subTest(sequence_length=len(sequence)):
                driver, _input, output, fallback, _termios, _os = make_driver(
                    sequence + "\n"
                )
                with mock.patch.object(
                    _driver._select,
                    "select",
                    side_effect=lambda r, w, x, _t: (r, w, x),
                ) as readiness:
                    self.assertEqual(self.read(driver), "")
                readiness.assert_called_once_with((7,), (), (), 0.05)
                fallback.assert_not_called()
                self.assertEqual(driver._history_state, MainInputHistoryState())
                self.assertEqual(len(output.records), 2)

    def test_overlong_suffixes_do_not_recall_history_or_move_cursor(self) -> None:
        for introducer in ("\x1b[", "\x1bO"):
            overlong = introducer + "1" * 62
            cases = [
                ("up", overlong + "Ax\n", "x"),
                ("down", "\x1b[A" + overlong + "Bx\n", "/priorx"),
                ("home", "ab" + overlong + "Hx\n", "abx"),
            ]
            if introducer == "\x1b[":
                cases.append(
                    (
                        "delete",
                        "ab\x1b[D" + overlong + "3~x\n",
                        "axb",
                    )
                )
            for label, characters, expected in cases:
                with self.subTest(
                    introducer=repr(introducer),
                    suffix=label,
                ):
                    driver, input_stream, _output, fallback, _termios, _os = (
                        make_driver(characters)
                    )
                    driver._history_state = MainInputHistoryState(("/prior",))
                    with mock.patch.object(
                        _driver._select,
                        "select",
                        side_effect=lambda r, w, x, _t: (r, w, x),
                    ) as readiness:
                        self.assertEqual(self.read(driver), expected)
                    self.assertEqual(
                        readiness.call_count,
                        characters.count("\x1b"),
                    )
                    fallback.assert_not_called()
                    self.assertEqual(input_stream.reads, [])
                    self.assertEqual(
                        driver._history_state,
                        MainInputHistoryState(("/prior", expected)),
                    )

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

    def test_enter_completion_waits_for_second_enter_before_history_append(
        self,
    ) -> None:
        driver, input_stream, output, fallback, termios, os_module = make_driver(
            "/s\n\n"
        )
        self.assertEqual(self.read(driver), "/say ")

        fallback.assert_not_called()
        self.assertEqual(input_stream.reads, [])
        self.assertEqual(driver._history_state, MainInputHistoryState(("/say ",)))
        self.assertEqual(os_module.observed_widths, [8, 8])
        self.assertEqual(
            tuple(call[1] for call in termios.set_calls),
            (FakeTermios.TCSANOW, FakeTermios.TCSADRAIN),
        )
        rendered = "".join(output.records)
        self.assertIn("Suggestions [open]", rendered)
        self.assertNotIn("Suggestions [open]", output.records[-1])

    def test_completion_only_enter_does_not_append_before_interrupt(self) -> None:
        driver, _input, output, fallback, termios, _os = make_driver("/s\n\x03")
        history_append = mock.Mock(wraps=append_main_input_history)
        driver._history_append = history_append

        with self.assertRaises(KeyboardInterrupt):
            self.read(driver)

        history_append.assert_not_called()
        self.assertEqual(driver._history_state, MainInputHistoryState())
        fallback.assert_not_called()
        self.assertTrue(output.records[-1].endswith("\x1b[J\n"))
        self.assertEqual(
            tuple(call[1] for call in termios.set_calls),
            (FakeTermios.TCSANOW, FakeTermios.TCSADRAIN),
        )

    def test_dynamic_choice_live_commit_clears_restores_then_appends_identity(
        self,
    ) -> None:
        driver, _input, output, fallback, termios, _os = make_driver(
            "/\x1b[B\n"
        )
        events: list[tuple[str, object]] = []
        original_write = output.write
        original_set = termios.tcsetattr

        def write(text: str) -> int:
            events.append(("write", text))
            return original_write(text)

        def set_attributes(
            descriptor: int,
            when: int,
            attributes: list[object],
        ) -> None:
            events.append(("termios", when))
            original_set(descriptor, when, attributes)

        def append_history(
            history: MainInputHistoryState,
            line: str,
        ) -> MainInputHistoryState:
            events.append(("history", line))
            return append_main_input_history(history, line)

        output.write = write  # type: ignore[method-assign]
        termios.tcsetattr = set_attributes  # type: ignore[method-assign]
        driver._history_append = append_history
        picker = Picker(
            Prompt("Resume"),
            (
                Choice("/session resume alpha", "Alpha"),
                Choice("/session resume beta", "Beta"),
            ),
        )
        provider = mock.Mock(return_value=picker)
        with mock.patch.object(
            _driver._select,
            "select",
            side_effect=lambda readable, writable, exceptional, _timeout: (
                readable,
                writable,
                exceptional,
            ),
        ):
            self.assertEqual(
                _core.read_main_command_line(
                    driver,
                    "main> ",
                    choice_provider=provider,
                ),
                "/session resume beta",
            )

        self.assertEqual(
            driver._history_state,
            MainInputHistoryState(("/session resume beta",)),
        )
        self.assertEqual(
            events[-3:],
            [
                ("write", "\r\r\x1b[J"),
                ("termios", FakeTermios.TCSADRAIN),
                ("history", "/session resume beta"),
            ],
        )
        rendered = "".join(output.records)
        self.assertIn("[highlighted] Beta", rendered)
        self.assertNotIn("/say", rendered)
        self.assertNotIn("/session resume beta", rendered)
        provider.assert_called_once_with("/", 1)
        fallback.assert_not_called()

    def test_dynamic_provider_failure_clears_restores_without_history(self) -> None:
        driver, _input, output, fallback, termios, _os = make_driver("/unused")
        provider = mock.Mock(side_effect=RuntimeError("choice failure"))

        with self.assertRaisesRegex(RuntimeError, "^choice failure$"):
            _core.read_main_command_line(
                driver,
                "main> ",
                choice_provider=provider,
            )

        provider.assert_called_once_with("/", 1)
        fallback.assert_not_called()
        self.assertEqual(driver._history_state, MainInputHistoryState())
        self.assertTrue(output.records[-1].endswith("\x1b[J\n"))
        self.assertEqual(
            tuple(call[1] for call in termios.set_calls),
            (FakeTermios.TCSANOW, FakeTermios.TCSADRAIN),
        )

    def test_contextual_runtime_header_redraws_for_route_and_backspace_states(
        self,
    ) -> None:
        driver, input_stream, output, fallback, termios, _os = make_driver(
            "@plot\x7ft message\n"
        )
        observed: list[tuple[str, int]] = []

        def header(buffer: str, cursor: int) -> _model.Notice:
            observed.append((buffer, cursor))
            one_shot = buffer == "@plot" or buffer.startswith("@plot ")
            return _model.Notice(
                "Antigravity · agy-live · high"
                if one_shot
                else "Codex · provider default · provider default",
                label="one-shot runtime" if one_shot else "active runtime",
                role=_model.SemanticRole.WARNING,
                state=_model.ViewState.LOADING,
            )

        self.assertEqual(
            _core.read_main_command_line(
                driver,
                "main> ",
                input_context_provider=header,
            ),
            "@plot message",
        )

        transitions = [
            value
            for index, value in enumerate(observed)
            if index == 0 or value != observed[index - 1]
        ]
        self.assertEqual(
            transitions[:8],
            [
                ("", 0),
                ("@", 1),
                ("@p", 2),
                ("@pl", 3),
                ("@plo", 4),
                ("@plot", 5),
                ("@plo", 4),
                ("@plot", 5),
            ],
        )
        rendered = "".join(output.records)
        self.assertIn("[active runtime] Codex", rendered)
        self.assertIn("[one-shot runtime] Antigravity", rendered)
        self.assertEqual(input_stream.reads, [])
        fallback.assert_not_called()
        self.assertEqual(
            tuple(call[1] for call in termios.set_calls),
            (FakeTermios.TCSANOW, FakeTermios.TCSADRAIN),
        )

    def test_contextual_runtime_header_keeps_labels_in_no_color_narrow_and_fallback(
        self,
    ) -> None:
        for label, no_color, columns, decorative in (
            ("ansi", False, 40, True),
            ("no-color", True, 40, False),
            ("narrow", True, 24, False),
        ):
            with self.subTest(mode=label):
                driver, _input, output, fallback, termios, os_module = make_driver(
                    "x\n"
                )
                if no_color:
                    os_module.environ["NO_COLOR"] = "1"
                os_module.get_terminal_size = lambda _descriptor: SimpleNamespace(
                    columns=columns,
                    lines=24,
                )
                header = mock.Mock(
                    return_value=_model.Notice(
                        "Lead Orchestrator · Codex · model provider default · "
                        "effort provider default · pending",
                        label="active runtime",
                        role=_model.SemanticRole.WARNING,
                        state=_model.ViewState.LOADING,
                    )
                )
                self.assertEqual(
                    _core.read_main_command_line(
                        driver,
                        "main> ",
                        input_context_provider=header,
                    ),
                    "x",
                )
                rendered = "".join(output.records)
                self.assertIn("[active runtime]", rendered)
                self.assertIn("LeadOrchestrator", "".join(rendered.split()))
                self.assertEqual("\x1b[33m" in rendered, decorative)
                header.assert_any_call("", 0)
                header.assert_any_call("x", 1)
                fallback.assert_not_called()
                self.assertEqual(
                    tuple(call[1] for call in termios.set_calls),
                    (FakeTermios.TCSANOW, FakeTermios.TCSADRAIN),
                )

        driver, _input, output, fallback, termios, _os = make_driver(
            "unused",
            terminal=False,
        )
        header = mock.Mock(
            return_value=_model.Notice(
                "Attach a story session to activate an agent execution profile.",
                label="active runtime",
                role=_model.SemanticRole.MUTED,
                state=_model.ViewState.EMPTY,
            )
        )
        self.assertEqual(
            _core.read_main_command_line(
                driver,
                "main> ",
                input_context_provider=header,
            ),
            "fallback",
        )
        header.assert_called_once_with("", 0)
        fallback_prompt = fallback.call_args.args[0]
        self.assertIn("[active runtime] Attach a story session", fallback_prompt)
        self.assertTrue(fallback_prompt.endswith("main> "))
        self.assertEqual(output.records, [])
        self.assertEqual(termios.set_calls, [])

    def test_transient_picker_renderer_failure_clears_and_restores(self) -> None:
        driver, _input, output, fallback, termios, _os = make_driver("unused")
        state = initialize_picker(
            Picker(
                Prompt("Choose"),
                (Choice("opaque", "Visible"),),
            )
        )
        renderer = mock.Mock(side_effect=RuntimeError("picker render failed"))

        with self.assertRaisesRegex(RuntimeError, "^picker render failed$"):
            driver.read_picker(
                state,
                transition_picker,
                transition_picker_fallback,
                project_picker_viewport,
                renderer,
                render_picker_fallback,
                transient=True,
            )

        renderer.assert_called_once()
        fallback.assert_not_called()
        self.assertEqual(driver._history_state, MainInputHistoryState())
        self.assertEqual(output.records[-1], "\r\r\x1b[J\n")
        self.assertEqual(
            tuple(call[1] for call in termios.set_calls),
            (FakeTermios.TCSANOW, FakeTermios.TCSADRAIN),
        )

    def test_routed_enter_completion_waits_for_message_and_second_enter(
        self,
    ) -> None:
        driver, input_stream, output, fallback, termios, os_module = make_driver(
            "@pl\nmessage\n"
        )
        self.assertEqual(self.read(driver), "@plot message")

        fallback.assert_not_called()
        self.assertEqual(input_stream.reads, [])
        self.assertEqual(
            driver._history_state,
            MainInputHistoryState(("@plot message",)),
        )
        self.assertEqual(os_module.observed_widths, [8, 8])
        self.assertEqual(
            tuple(call[1] for call in termios.set_calls),
            (FakeTermios.TCSANOW, FakeTermios.TCSADRAIN),
        )
        rendered = "".join(output.records)
        self.assertIn("[highlighted] @plot", rendered)
        self.assertNotIn("Suggestions [open]", output.records[-1])

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
            self.assertEqual(self.read(driver), "/say ")
        self.assertEqual(
            tuple(call.args for call in first.call_args_list),
            (("x", 1),),
        )
        self.assertEqual(
            driver._history_state,
            MainInputHistoryState(("x", "/say ")),
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

    def test_malformed_byte_reader_shapes_share_exact_diagnostic(
        self,
    ) -> None:
        for value in (object(), b"two", "two"):
            with self.subTest(value=type(value).__name__):
                driver, input_stream, output, fallback, termios, _os = make_driver("")
                with mock.patch.object(
                    input_stream,
                    "read1",
                    return_value=value,
                ) as collaborator:
                    with self.assertRaisesRegex(
                        TypeError,
                        "^live input byte reader must return exactly one byte$",
                    ):
                        self.read(driver)

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

        dynamic = mock.Mock(side_effect=AssertionError("choices called"))
        plain, _input, plain_output, plain_fallback, _termios, _os = make_driver(
            "unused",
            terminal=False,
        )
        self.assertEqual(
            _core.read_main_command_line(
                plain,
                "main> ",
                choice_provider=dynamic,
            ),
            "fallback",
        )
        dynamic.assert_not_called()
        plain_fallback.assert_called_once_with("main> ")
        self.assertEqual(plain_output.records, [])
        self.assertEqual(
            plain._history_state,
            MainInputHistoryState(("fallback",)),
        )

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
        driver.read_main_command_line.assert_called_once()
        prompt, completion, projector, renderer = (
            driver.read_main_command_line.call_args.args
        )
        self.assertEqual(prompt, "main> ")
        self.assertEqual(completion("/s", 2), _core._main_command_completion("/s", 2))
        state = MainCommandControlState()
        for character in "/s":
            state, _ = transition_main_command_input(
                state,
                KeyEvent(KeyKind.TEXT, text=character),
                MainInputHistoryState(),
                completion,
            )
        self.assertEqual(projector(state), project_main_suggestion_palette(state))
        self.assertIs(renderer, render_component)
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
