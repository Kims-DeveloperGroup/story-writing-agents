"""Deterministic contract tests for the interactive-terminal foundation."""

from __future__ import annotations

import ast
import asyncio
import io
import inspect
import os
import pickle
import re
import subprocess
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from types import SimpleNamespace
from typing import get_args
from unittest import mock

from story_writing_agents import interactive_line_editor as editor_facade
from story_writing_agents import interactive_terminal as terminal_facade
from story_writing_agents.interactive_line_editor import (
    InteractiveMainLineEditor,
    SlashCommandSuggestion,
)
from story_writing_agents.interactive_terminal import (
    AttachmentKind,
    ComposeMode,
    ConfirmationResult,
    InteractiveError,
    InteractiveErrorCode,
    InteractiveErrorSpec,
    InteractiveMainInput,
    InteractivePromptInput,
    InteractiveTerminal,
    TerminalAction,
    TerminalActionKind,
    TerminalMessageKind,
)
from story_writing_agents.interactive_terminal import _controls, _core, _driver, _model
from story_writing_agents.interactive_terminal._controls import (
    append_main_input_history,
    project_slash_suggestions,
    transition_line_input,
    transition_main_command_input,
    transition_main_input,
    transition_prompt_input,
)
from story_writing_agents.interactive_terminal._driver import (
    AdvisoryProgressError,
    RawTerminalDriver,
    detect_terminal_capabilities,
)
from story_writing_agents.interactive_terminal._model import (
    CapabilityProfile,
    Choice,
    ComposerState,
    ControlOutcome,
    ControlOutcomeKind,
    Field,
    KeyEvent,
    KeyKind,
    LineInputState,
    ListView,
    MainInputControlState,
    MainInputHistoryState,
    Message,
    Notice,
    Panel,
    Picker,
    Prompt,
    Record,
    RenderedFrame,
    RenderedInputFrame,
    Section,
    SemanticRole,
    SuggestionPalette,
    TerminalCapabilities,
    ViewState,
    display_character_width,
)
from story_writing_agents.interactive_terminal._render import (
    render_advisory_progress_frame,
    render_component,
    render_input_prompt,
    render_plain,
    strip_decorative_sgr,
)


TERMINAL_EXPORTS = (
    "AttachmentKind",
    "ComposeMode",
    "ConfirmationResult",
    "TerminalActionKind",
    "TerminalMessageKind",
    "InteractiveErrorCode",
    "TerminalAction",
    "InteractiveErrorSpec",
    "INTERACTIVE_ERROR_SPECS",
    "InteractiveError",
    "sanitize_terminal_text",
    "InteractiveTerminal",
)

MODEL_EXPORTS = (
    "SemanticRole",
    "ViewState",
    "CapabilityProfile",
    "KeyKind",
    "ControlOutcomeKind",
    "TerminalCapabilities",
    "KeyEvent",
    "LineInputState",
    "MainInputHistoryState",
    "MainInputControlState",
    "SlashCommandSuggestion",
    "ControlOutcome",
    "Prompt",
    "Panel",
    "Section",
    "Field",
    "Notice",
    "Message",
    "Record",
    "ListView",
    "Choice",
    "SuggestionPalette",
    "Picker",
    "Component",
    "RenderedFrame",
    "sanitize_terminal_text",
    "display_character_width",
)


class ObservedStream:
    """Expose deterministic stream capabilities without real terminal I/O."""

    def __init__(
        self,
        *,
        is_terminal: bool = True,
        descriptor: int = 7,
        tty_failure: BaseException | None = None,
    ) -> None:
        self.is_terminal = is_terminal
        self.descriptor = descriptor
        self.tty_failure = tty_failure
        self.calls: list[str] = []

    def isatty(self) -> bool:
        self.calls.append("isatty")
        if self.tty_failure is not None:
            raise self.tty_failure
        return self.is_terminal

    def fileno(self) -> int:
        self.calls.append("fileno")
        return self.descriptor

    def read(self, size: int) -> str:
        self.calls.append("read")
        raise AssertionError(f"unexpected input read of {size}")

    def write(self, text: str) -> int:
        self.calls.append("write")
        raise AssertionError(f"unexpected output write of {text!r}")

    def flush(self) -> None:
        self.calls.append("flush")
        raise AssertionError("unexpected output flush")


class CompleteTermios:
    """Provide only the complete interface needed for capability detection."""

    ECHO = 1
    ICANON = 2
    TCSADRAIN = 3
    TCSANOW = 4
    VEOF = 5
    VINTR = 6
    VMIN = 7
    VTIME = 8

    @staticmethod
    def tcgetattr(descriptor: int) -> list[object]:
        raise AssertionError(f"unexpected attribute read from {descriptor}")

    @staticmethod
    def tcsetattr(
        descriptor: int,
        when: int,
        attributes: list[object],
    ) -> None:
        raise AssertionError(
            f"unexpected attribute write to {descriptor}: {when}, {attributes!r}"
        )


def fake_os(
    *,
    terminal_name: str = "xterm-256color",
    width: object = 80,
    no_color: bool = False,
) -> SimpleNamespace:
    """Return one deterministic POSIX capability collaborator."""

    environment = {"TERM": terminal_name}
    if no_color:
        environment["NO_COLOR"] = ""

    def get_terminal_size(descriptor: int) -> os.terminal_size:
        if isinstance(width, BaseException):
            raise width
        return os.terminal_size((width, 24))  # type: ignore[arg-type]

    return SimpleNamespace(
        name="posix",
        environ=environment,
        get_terminal_size=get_terminal_size,
    )


def capability(
    profile: CapabilityProfile,
    *,
    width: int = 80,
) -> TerminalCapabilities:
    """Build one internally consistent render-capability value."""

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


def physical_width(text: str) -> int:
    """Measure sanitized unstyled text using the foundation cell rule."""

    return sum(display_character_width(character) for character in text)


class PublicCompatibilityFoundationTests(unittest.TestCase):
    def test_facades_export_exact_defining_objects_with_canonical_identity(
        self,
    ) -> None:
        self.assertEqual(tuple(terminal_facade.__all__), TERMINAL_EXPORTS)
        self.assertEqual(
            tuple(editor_facade.__all__),
            (
                "SlashCommandSuggestion",
                "slash_command_suggestions",
                "InteractiveMainLineEditor",
            ),
        )
        defining_objects = (
            AttachmentKind,
            ComposeMode,
            ConfirmationResult,
            TerminalActionKind,
            TerminalMessageKind,
            InteractiveErrorCode,
            TerminalAction,
            InteractiveErrorSpec,
            InteractiveError,
            InteractiveTerminal,
        )
        for value in defining_objects:
            with self.subTest(value=value.__name__):
                self.assertIs(getattr(terminal_facade, value.__name__), value)
                self.assertIs(getattr(_core, value.__name__), value)
                self.assertEqual(
                    value.__module__,
                    "story_writing_agents.interactive_terminal",
                )
        self.assertIs(
            terminal_facade.sanitize_terminal_text,
            _model.sanitize_terminal_text,
        )
        self.assertEqual(
            terminal_facade.sanitize_terminal_text.__module__,
            "story_writing_agents.interactive_terminal",
        )
        self.assertIs(SlashCommandSuggestion, _model.SlashCommandSuggestion)
        self.assertIs(terminal_facade.SlashCommandSuggestion, SlashCommandSuggestion)
        self.assertEqual(
            SlashCommandSuggestion.__module__,
            "story_writing_agents.interactive_line_editor",
        )
        self.assertEqual(
            InteractiveMainLineEditor.__module__,
            "story_writing_agents.interactive_line_editor",
        )
        self.assertIs(
            terminal_facade.InteractivePromptInput,
            InteractivePromptInput,
        )
        self.assertIs(InteractivePromptInput, _core.InteractivePromptInput)
        self.assertNotIn("InteractivePromptInput", terminal_facade.__all__)
        self.assertFalse(hasattr(__import__("story_writing_agents"), "InteractivePromptInput"))
        self.assertIs(
            terminal_facade.InteractiveMainInput,
            InteractiveMainInput,
        )
        self.assertIs(InteractiveMainInput, _core.InteractiveMainInput)
        self.assertNotIn("InteractiveMainInput", terminal_facade.__all__)
        self.assertFalse(hasattr(__import__("story_writing_agents"), "InteractiveMainInput"))
        self.assertNotIn("InteractiveMainCommitCleanup", terminal_facade.__all__)
        self.assertFalse(hasattr(terminal_facade, "InteractiveMainCommitCleanup"))
        self.assertFalse(hasattr(_core, "InteractiveMainCommitCleanup"))

    def test_public_signatures_remain_exact(self) -> None:
        positional = inspect.Parameter.POSITIONAL_OR_KEYWORD
        keyword = inspect.Parameter.KEYWORD_ONLY
        required = inspect.Parameter.empty

        def shape(value: object) -> tuple[tuple[str, object, object], ...]:
            signature = inspect.signature(value)
            return tuple(
                (parameter.name, parameter.kind, parameter.default)
                for parameter in signature.parameters.values()
                if parameter.name != "self"
            )

        expected = {
            InteractiveTerminal: (
                ("read_line", positional, required),
                ("write_text", positional, required),
                ("width", keyword, 80),
                ("main_read_line", keyword, None),
            ),
            InteractiveTerminal.read_main: (
                ("session_id", keyword, None),
                ("agent_id", keyword, None),
            ),
            InteractiveTerminal.request_conversation_subject: (),
            InteractiveTerminal.request_confirmation: (
                ("message", positional, required),
                ("default", keyword, required),
            ),
            InteractiveTerminal.compose: (
                ("mode", positional, required),
                ("agent_id", keyword, None),
            ),
            InteractiveTerminal.write_lobby_orientation: (
                ("workspace", positional, required),
            ),
            InteractiveTerminal.write_session_orientation: (
                ("workspace", positional, required),
                ("session_id", positional, required),
                ("attachment", positional, required),
                ("agent_id", positional, required),
                ("history_count", positional, None),
            ),
            InteractiveTerminal.write_conversation_scope: (
                ("session_id", keyword, required),
                ("conversation_handle", keyword, required),
                ("conversation_subject", keyword, required),
                ("agent_id", keyword, required),
            ),
            InteractiveTerminal.write_conversation_list_entry: (
                ("conversation_handle", positional, required),
                ("conversation_subject", positional, required),
                ("archived", keyword, required),
                ("focused", keyword, required),
            ),
            InteractiveTerminal.write_conversation_search_hit: (
                ("conversation_handle", positional, required),
                ("conversation_subject", positional, required),
                ("match_kind", positional, required),
                ("snippet", positional, required),
                ("archived", keyword, required),
                ("citation_turn_id", keyword, None),
                ("citation_sequence", keyword, None),
                ("citation_kind", keyword, None),
            ),
            InteractiveTerminal.write_conversation_transcript_event: (
                ("conversation_handle", positional, required),
                ("sequence", keyword, required),
                ("turn_id", keyword, required),
                ("kind", keyword, required),
                ("sender", keyword, required),
                ("recipient", keyword, required),
                ("content", keyword, required),
            ),
            InteractiveTerminal.write_agent_roster: (),
            InteractiveTerminal.write_status: (
                ("kind", positional, required),
                ("text", positional, required),
            ),
            InteractiveTerminal.write_run: (
                ("debate_id", positional, required),
                ("text", positional, required),
            ),
            InteractiveTerminal.write_agent: (
                ("agent_id", positional, required),
                ("text", positional, required),
            ),
            InteractiveTerminal.write_help: (
                ("command", positional, None),
            ),
            InteractiveTerminal.write_error: (
                ("error", positional, required),
                ("secrets", keyword, ()),
                ("debug_log_path", keyword, None),
            ),
            terminal_facade.sanitize_terminal_text: (
                ("text", positional, required),
            ),
            SlashCommandSuggestion: (
                ("token", positional, required),
                ("canonical_token", positional, required),
            ),
            editor_facade.slash_command_suggestions: (
                ("buffer", positional, required),
            ),
            InteractiveMainLineEditor: (
                ("fallback_read_line", positional, required),
                ("input_stream", positional, required),
                ("output_stream", positional, required),
            ),
            InteractiveMainLineEditor.read_line: (
                ("prompt", positional, required),
            ),
            InteractivePromptInput: (
                ("terminal", positional, required),
            ),
            InteractiveMainInput: (
                ("terminal", positional, required),
            ),
            InteractiveMainInput.read_main: (
                ("choice_provider", positional, required),
                ("session_id", keyword, None),
                ("agent_id", keyword, None),
            ),
            InteractivePromptInput.read_prompt: (
                ("prompt", positional, required),
                ("context", keyword, ()),
                ("transient", keyword, True),
            ),
        }
        for value, expected_shape in expected.items():
            with self.subTest(value=getattr(value, "__qualname__", repr(value))):
                self.assertEqual(shape(value), expected_shape)

    def test_public_values_pickle_and_preserve_isinstance_compatibility(self) -> None:
        samples = (
            AttachmentKind.CREATED,
            ComposeMode.RUN,
            ConfirmationResult.EOF,
            TerminalAction(TerminalActionKind.LINE, text="exact"),
            InteractiveErrorSpec("Summary", "Hint"),
            InteractiveError(InteractiveErrorCode.INTERNAL_ERROR),
            SlashCommandSuggestion("/agent", "/to"),
        )
        for sample in samples:
            with self.subTest(sample=sample):
                restored = pickle.loads(pickle.dumps(sample))
                self.assertEqual(restored, sample)
                self.assertIs(type(restored), type(sample))
                self.assertIsInstance(restored, type(sample))


class SemanticModelFoundationTests(unittest.TestCase):
    def test_closed_vocabulary_states_and_internal_exports_are_exact(self) -> None:
        self.assertEqual(tuple(_model.__all__), MODEL_EXPORTS)
        self.assertEqual(
            tuple(item.value for item in ViewState),
            (
                "idle",
                "focused",
                "open",
                "highlighted",
                "selected",
                "disabled",
                "loading",
                "empty",
                "error",
                "cancelled",
                "completed",
            ),
        )
        self.assertEqual(
            tuple(item.value for item in SemanticRole),
            (
                "default",
                "emphasis",
                "muted",
                "info",
                "success",
                "warning",
                "error",
                "command",
                "agent",
            ),
        )
        self.assertEqual(
            get_args(_model.Component),
            (
                Prompt,
                Panel,
                Section,
                Field,
                Notice,
                Message,
                Record,
                ListView,
                SuggestionPalette,
                Picker,
            ),
        )
        self.assertEqual(
            tuple(field.name for field in fields(LineInputState)),
            ("buffer", "cursor"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(MainInputHistoryState)),
            ("entries",),
        )
        self.assertEqual(
            tuple(field.name for field in fields(MainInputControlState)),
            ("line_state", "history_index", "saved_draft", "overlay_open"),
        )
        self.assertEqual(
            tuple(_controls.__all__),
            (
                "transition_line_input",
                "append_main_input_history",
                "transition_main_input",
                "project_slash_suggestions",
                "project_composition_suggestions",
            ),
        )
        for deferred_name in (
            "transition_palette",
            "persistent_history",
        ):
            self.assertFalse(hasattr(_controls, deferred_name))

    def test_semantic_values_are_frozen_slotted_and_reject_invalid_relations(
        self,
    ) -> None:
        empty_notice = Notice("Nothing here.", state=ViewState.EMPTY)
        choice = Choice("one", "One")
        samples = (
            capability(CapabilityProfile.LINE),
            KeyEvent(KeyKind.TEXT, text="x"),
            LineInputState("x", 1),
            SlashCommandSuggestion("/x", "/x"),
            ControlOutcome(ControlOutcomeKind.COMMIT, text="x"),
            Prompt("Input"),
            Field("Label", "Value"),
            Notice("Ready"),
            Message("user", "content"),
            Record("record", fields=(Field("Key", "Value"),)),
            ListView("Records", (), empty_notice=empty_notice),
            Section("Section", (empty_notice,)),
            Panel("Panel", (empty_notice,)),
            choice,
            SuggestionPalette("/", (choice,), highlighted_id="one"),
            Picker(Prompt("Choose"), (choice,), highlighted_id="one"),
            RenderedFrame("text", 80, 1),
        )
        for sample in samples:
            with self.subTest(sample=type(sample).__name__):
                self.assertFalse(hasattr(sample, "__dict__"))
                with self.assertRaises(FrozenInstanceError):
                    setattr(sample, fields(sample)[0].name, None)

        invalid_calls = (
            lambda: TerminalCapabilities(
                CapabilityProfile.LINE,
                80,
                input_tty=True,
                output_tty=True,
                raw_mode=True,
                cursor_controls=False,
                decorative_sgr=False,
            ),
            lambda: TerminalCapabilities(
                CapabilityProfile.PLAIN,
                80,
                input_tty=True,
                output_tty=True,
                raw_mode=True,
                cursor_controls=True,
                decorative_sgr=True,
            ),
            lambda: KeyEvent(KeyKind.TEXT),
            lambda: KeyEvent(KeyKind.ENTER, text="x"),
            lambda: LineInputState("x", 2),
            lambda: ListView("Empty", ()),
            lambda: SuggestionPalette(
                "/",
                (Choice("same", "One"), Choice("same", "Two")),
            ),
            lambda: SuggestionPalette(
                "/",
                (Choice("off", "Off", enabled=False),),
                highlighted_id="off",
            ),
            lambda: Picker(
                Prompt("Choose"),
                (Choice("off", "Off", enabled=False),),
                selected_id="off",
            ),
            lambda: Panel("Panel", (Prompt("Unsupported child"),)),
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises((TypeError, ValueError)):
                    call()


class PureControlFoundationTests(unittest.TestCase):
    def test_line_input_transitions_edit_commit_and_preserve_terminal_events(
        self,
    ) -> None:
        state = LineInputState("ac", 1)
        state, outcome = transition_line_input(
            state,
            KeyEvent(KeyKind.TEXT, text="b"),
        )
        self.assertEqual((state, outcome), (LineInputState("abc", 2), None))
        expected_states = (
            (KeyKind.LEFT, LineInputState("abc", 1)),
            (KeyKind.BACKSPACE, LineInputState("bc", 0)),
            (KeyKind.DELETE, LineInputState("c", 0)),
            (KeyKind.RIGHT, LineInputState("c", 1)),
            (KeyKind.HOME, LineInputState("c", 0)),
            (KeyKind.END, LineInputState("c", 1)),
        )
        for kind, expected in expected_states:
            state, outcome = transition_line_input(state, KeyEvent(kind))
            self.assertEqual((state, outcome), (expected, None))

        committed_state = LineInputState("visible", 3)
        unchanged, outcome = transition_line_input(
            committed_state,
            KeyEvent(KeyKind.ENTER),
        )
        self.assertIs(unchanged, committed_state)
        self.assertEqual(
            outcome,
            ControlOutcome(ControlOutcomeKind.COMMIT, text="visible"),
        )
        for kind, expected in (
            (KeyKind.INTERRUPT, ControlOutcome(ControlOutcomeKind.INTERRUPT)),
            (KeyKind.EOF, ControlOutcome(ControlOutcomeKind.COMMIT, text="visible")),
        ):
            unchanged, outcome = transition_line_input(
                committed_state,
                KeyEvent(kind),
            )
            self.assertIs(unchanged, committed_state)
            self.assertEqual(outcome, expected)
        empty = LineInputState()
        self.assertEqual(
            transition_line_input(empty, KeyEvent(KeyKind.EOF)),
            (empty, ControlOutcome(ControlOutcomeKind.EOF)),
        )

    def test_cancellable_prompt_transition_adds_only_exact_escape_cancel(self) -> None:
        state = LineInputState("draft", 2)
        unchanged, outcome = transition_prompt_input(
            state,
            KeyEvent(KeyKind.ESCAPE),
        )
        self.assertIs(unchanged, state)
        self.assertEqual(outcome, ControlOutcome(ControlOutcomeKind.CANCEL))

        for event in (
            KeyEvent(KeyKind.TEXT, text="x"),
            KeyEvent(KeyKind.LEFT),
            KeyEvent(KeyKind.ENTER),
            KeyEvent(KeyKind.INTERRUPT),
            KeyEvent(KeyKind.EOF),
            KeyEvent(KeyKind.UP),
            KeyEvent(KeyKind.TAB),
        ):
            with self.subTest(event=event):
                self.assertEqual(
                    transition_prompt_input(state, event),
                    transition_line_input(state, event),
                )
        with self.assertRaises(TypeError):
            transition_prompt_input(object(), KeyEvent(KeyKind.ESCAPE))  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            transition_prompt_input(state, object())  # type: ignore[arg-type]

    def test_reserved_later_control_keys_have_no_foundation_semantics(self) -> None:
        state = LineInputState("draft", 2)
        for kind in (
            KeyKind.UP,
            KeyKind.DOWN,
            KeyKind.SHIFT_UP,
            KeyKind.SHIFT_DOWN,
            KeyKind.TAB,
            KeyKind.SHIFT_TAB,
            KeyKind.ESCAPE,
        ):
            with self.subTest(kind=kind):
                next_state, outcome = transition_line_input(state, KeyEvent(kind))
                self.assertIs(next_state, state)
                self.assertIsNone(outcome)

        metadata = (
            ("/alpha", ("/a",)),
            ("/compose", ("/composition",)),
            ("/beta", ()),
        )
        self.assertEqual(
            project_slash_suggestions(
                "/",
                metadata,
                exclusions=frozenset({"/compose"}),
            ),
            (
                SlashCommandSuggestion("/alpha", "/alpha"),
                SlashCommandSuggestion("/a", "/alpha"),
                SlashCommandSuggestion("/beta", "/beta"),
            ),
        )
        for buffer in ("", "alpha", " /a", "/a ", "/A"):
            self.assertEqual(
                project_slash_suggestions(
                    buffer,
                    metadata,
                    exclusions=frozenset(),
                ),
                (),
            )


class RenderingFoundationTests(unittest.TestCase):
    def test_advisory_progress_frames_are_fixed_and_strict(self) -> None:
        self.assertEqual(
            tuple(render_advisory_progress_frame(index) for index in range(4)),
            ("◐", "◓", "◑", "◒"),
        )
        for invalid in (True, 1.0, "0", None):
            with self.subTest(invalid=invalid), self.assertRaises(TypeError):
                render_advisory_progress_frame(invalid)  # type: ignore[arg-type]
        for invalid in (-1, 4):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                render_advisory_progress_frame(invalid)

    def test_plain_and_ansi_are_one_sanitized_structure_with_local_fixed_sgr(
        self,
    ) -> None:
        component = Panel(
            "Unsafe\x1b]0;owned\x07",
            (
                Notice(
                    "bad\x1b[31m text",
                    role=SemanticRole.ERROR,
                    state=ViewState.ERROR,
                ),
                Field("Wide", "界e\u0301"),
            ),
        )
        plain = render_plain(component, width=40)
        ansi = render_component(
            component,
            capability(CapabilityProfile.ANSI, width=40),
        )
        self.assertEqual(strip_decorative_sgr(ansi.text), plain.text)
        self.assertIn("Unsafe\\x1b]0;owned\\x07", plain.text)
        self.assertIn("bad\\x1b[31m text", plain.text)
        self.assertNotIn("\x1b", plain.text)
        self.assertNotIn("\x07", plain.text)
        allowed = {
            "\x1b[0m",
            "\x1b[1m",
            "\x1b[31m",
            "\x1b[32m",
            "\x1b[33m",
            "\x1b[34m",
            "\x1b[35m",
            "\x1b[36m",
            "\x1b[37m",
        }
        sequences = re.findall(r"\x1b\[[0-9;]*[A-Za-z]", ansi.text)
        self.assertTrue(sequences)
        self.assertTrue(set(sequences).issubset(allowed))
        for line in ansi.text.splitlines():
            if "\x1b" in line:
                self.assertTrue(line.endswith("\x1b[0m"))
        self.assertEqual(
            strip_decorative_sgr("x\x1b[41mbackground\x1b[0m"),
            "x\x1b[41mbackground",
        )

    def test_narrow_rendering_preserves_labels_wraps_by_cells_and_stacks_fields(
        self,
    ) -> None:
        field = Field(
            "Status",
            "界界 e\u0301 value",
            role=SemanticRole.SUCCESS,
            state=ViewState.COMPLETED,
        )
        frame = render_plain(field, width=20)
        self.assertEqual(
            frame.text,
            "Status [completed] [\nsuccess]:\n 界界 e\u0301 value",
        )
        self.assertEqual(frame.line_count, 3)
        for line in frame.text.splitlines():
            self.assertLessEqual(physical_width(line), 20)
        self.assertEqual(display_character_width("界"), 2)
        self.assertEqual(display_character_width("e"), 1)
        self.assertEqual(display_character_width("\u0301"), 0)

        ansi = render_component(
            field,
            capability(CapabilityProfile.ANSI, width=20),
        )
        self.assertEqual(strip_decorative_sgr(ansi.text), frame.text)
        for line in strip_decorative_sgr(ansi.text).splitlines():
            self.assertLessEqual(physical_width(line), 20)

    def test_panels_have_bounded_ascii_splitters_across_profiles_and_consumers(
        self,
    ) -> None:
        cases = (
            ("Story chat", ViewState.IDLE, ("--- Story chat -----",)),
            ("Help", ViewState.COMPLETED, ("--- Help [completed]",)),
            (
                "Interactive error",
                ViewState.ERROR,
                ("--- Interactive erro", "    r [error] ------"),
            ),
            (
                "Agent saying",
                ViewState.COMPLETED,
                ("--- Agent saying", "    [completed] ----"),
            ),
            ("Settings", ViewState.OPEN, ("--- Settings [open]",)),
            (
                "Result",
                ViewState.COMPLETED,
                ("--- Result", "    [completed] ----"),
            ),
        )
        for title, state, expected_heading in cases:
            with self.subTest(title=title, state=state):
                panel = Panel(title, (), state=state)
                line = render_component(
                    panel,
                    capability(CapabilityProfile.LINE, width=20),
                )
                plain = render_component(
                    panel,
                    capability(CapabilityProfile.PLAIN, width=20),
                )
                ansi = render_component(
                    panel,
                    capability(CapabilityProfile.ANSI, width=20),
                )
                expected = "\n".join((*expected_heading, "-" * 20))
                self.assertEqual(line.text, expected)
                self.assertEqual(plain.text, expected)
                self.assertEqual(strip_decorative_sgr(ansi.text), expected)
                self.assertEqual(line.line_count, len(expected_heading) + 1)
                collapsed_heading = "\n".join(expected_heading).removeprefix(
                    "--- "
                ).replace("\n    ", "")
                self.assertEqual(collapsed_heading.count(title), 1)
                if state is not ViewState.IDLE:
                    self.assertEqual(expected.count(f"[{state.value}]"), 1)
                self.assertTrue(
                    all(
                        line.isascii() and physical_width(line) <= 20
                        for line in expected.splitlines()
                    )
                )

        first = render_plain(Panel("First", ()), width=20).text
        second = render_plain(Panel("Second", ()), width=20).text
        self.assertEqual(first.splitlines()[-1], "-" * 20)
        self.assertEqual(second.splitlines()[0], "--- Second ---------")
        self.assertIn(f"{'-' * 20}\n--- Second", f"{first}\n{second}")

    def test_settings_changed_only_panel_matches_plain_ansi_and_narrow_widths(
        self,
    ) -> None:
        panel = Panel(
            "Settings",
            (
                Section(
                    "Current",
                    (Field("Language", "KO (Changed)", role=SemanticRole.WARNING),),
                ),
                Section(
                    "Pending",
                    (Field("Language", "EN (Changed)", role=SemanticRole.WARNING),),
                ),
                Notice(
                    "Changed values apply only to the next shell after Save.",
                    label="activation",
                    role=SemanticRole.MUTED,
                ),
            ),
            state=ViewState.OPEN,
        )
        plain = render_plain(panel, width=20)
        ansi = render_component(panel, capability(CapabilityProfile.ANSI, width=20))
        self.assertEqual(strip_decorative_sgr(ansi.text), plain.text)
        self.assertIn("Language", plain.text)
        self.assertIn("KO (Changed)", plain.text)
        self.assertIn("EN (Changed)", plain.text)
        self.assertNotIn("Persona", plain.text)
        for line in plain.text.splitlines():
            self.assertLessEqual(physical_width(line), 20)

    def test_every_state_and_selection_disabled_empty_cue_is_plainly_visible(
        self,
    ) -> None:
        state_panel = Panel(
            "States",
            tuple(Notice(state.value, state=state) for state in ViewState),
        )
        rendered_states = render_plain(state_panel, width=40).text
        for state in ViewState:
            with self.subTest(state=state):
                if state is ViewState.IDLE:
                    self.assertIn("[info] idle", rendered_states)
                else:
                    self.assertIn(f"[{state.value}] {state.value}", rendered_states)

        empty_list = ListView(
            "Results",
            (),
            empty_notice=Notice("No results.", state=ViewState.EMPTY),
            state=ViewState.EMPTY,
        )
        self.assertEqual(
            render_plain(empty_list, width=40).text,
            "Results (0) [empty]\n  [empty] No results.",
        )
        empty_palette = render_plain(SuggestionPalette("/", ()), width=40).text
        self.assertIn("Suggestions [open]", empty_palette)
        self.assertIn("[empty] No suggestions.", empty_palette)

        choices = (
            Choice("one", "First"),
            Choice(
                "two",
                "Second",
                enabled=False,
                disabled_reason="Unavailable",
            ),
            Choice("three", "Third"),
        )
        picker = Picker(
            Prompt("Choose"),
            choices,
            highlighted_id="one",
            selected_id="three",
        )
        rendered_picker = render_plain(picker, width=40).text
        self.assertIn("> [highlighted] First", rendered_picker)
        self.assertIn("- [disabled] Second (Unavailable)", rendered_picker)
        self.assertIn("* [selected] Third", rendered_picker)


class CapabilityAndDriverFoundationTests(unittest.TestCase):
    def test_escape_reducer_discards_post_introducer_c0_and_del(self) -> None:
        for introducer in ("\x1b[", "\x1bO"):
            for value in (*range(0x20), 0x7F):
                with self.subTest(
                    introducer=repr(introducer),
                    terminator=hex(value),
                ):
                    state = _driver._LiveEditorState(
                        escape_sequence=introducer,
                    )
                    self.assertEqual(
                        _driver._consume_escape_character(state, chr(value)),
                        (None, False),
                    )
                    self.assertIsNone(state.escape_sequence)

        bare_escape = _driver._LiveEditorState(escape_sequence="\x1b")
        self.assertEqual(
            _driver._consume_escape_character(bare_escape, "x"),
            (KeyEvent(KeyKind.ESCAPE), True),
        )
        self.assertIsNone(bare_escape.escape_sequence)

    def test_escape_reducer_bounds_overlong_sequences_in_identity_state(self) -> None:
        for introducer in ("\x1b[", "\x1bO"):
            with self.subTest(introducer=repr(introducer), boundary=True):
                state = _driver._LiveEditorState(escape_sequence=introducer)
                for _index in range(61):
                    self.assertEqual(
                        _driver._consume_escape_character(state, "1"),
                        (None, False),
                    )
                self.assertEqual(state.escape_sequence, introducer + "1" * 61)
                self.assertEqual(
                    _driver._consume_escape_character(state, "1"),
                    (None, False),
                )
                self.assertIs(
                    state.escape_sequence,
                    _driver._DISCARDING_ESCAPE_SEQUENCE,
                )
                for _index in range(10_000):
                    self.assertEqual(
                        _driver._consume_escape_character(state, "1"),
                        (None, False),
                    )
                self.assertIs(
                    state.escape_sequence,
                    _driver._DISCARDING_ESCAPE_SEQUENCE,
                )

            suffixes = (
                "A",
                "B",
                "H",
                "1;2A",
                "1;2B",
                "1~",
                "3~",
                "Z",
            )
            for suffix in suffixes:
                with self.subTest(
                    introducer=repr(introducer),
                    suffix=repr(suffix),
                ):
                    state = _driver._LiveEditorState(
                        escape_sequence=_driver._DISCARDING_ESCAPE_SEQUENCE,
                    )
                    for character in suffix:
                        self.assertEqual(
                            _driver._consume_escape_character(state, character),
                            (None, False),
                        )
                    self.assertIsNone(state.escape_sequence)

            for value in (*range(0x20), 0x7F):
                with self.subTest(
                    introducer=repr(introducer),
                    control=hex(value),
                ):
                    state = _driver._LiveEditorState(
                        escape_sequence=_driver._DISCARDING_ESCAPE_SEQUENCE,
                    )
                    self.assertEqual(
                        _driver._consume_escape_character(state, chr(value)),
                        (None, False),
                    )
                    self.assertIsNone(state.escape_sequence)

    def test_capabilities_observe_streams_independently_and_select_profiles(
        self,
    ) -> None:
        cases = (
            (True, True, "xterm", False, CapabilityProfile.ANSI, True, True),
            (True, True, "xterm", True, CapabilityProfile.PLAIN, True, True),
            (True, True, "dumb", False, CapabilityProfile.LINE, True, True),
            (False, True, "xterm", False, CapabilityProfile.LINE, False, True),
            (True, False, "xterm", False, CapabilityProfile.LINE, True, False),
        )
        for (
            input_tty,
            output_tty,
            terminal_name,
            no_color,
            expected_profile,
            expected_input,
            expected_output,
        ) in cases:
            with self.subTest(
                input_tty=input_tty,
                output_tty=output_tty,
                terminal_name=terminal_name,
                no_color=no_color,
            ):
                observed = detect_terminal_capabilities(
                    ObservedStream(is_terminal=input_tty),  # type: ignore[arg-type]
                    ObservedStream(  # type: ignore[arg-type]
                        is_terminal=output_tty,
                        descriptor=8,
                    ),
                    termios_module=CompleteTermios,
                    os_module=fake_os(
                        terminal_name=terminal_name,
                        no_color=no_color,
                    ),
                )
                self.assertEqual(observed.profile, expected_profile)
                self.assertEqual(observed.input_tty, expected_input)
                self.assertEqual(observed.output_tty, expected_output)
                self.assertEqual(
                    (observed.raw_mode, observed.cursor_controls),
                    (expected_profile is not CapabilityProfile.LINE,) * 2,
                )
                self.assertEqual(
                    observed.decorative_sgr,
                    expected_profile is CapabilityProfile.ANSI,
                )

    def test_capability_width_fallback_cap_and_uncertain_results_are_safe(
        self,
    ) -> None:
        for width, expected in (
            (0, 80),
            (-1, 80),
            (OSError("unknown"), 80),
            (79, 79),
            (120, 120),
            (200, 120),
        ):
            with self.subTest(width=width):
                observed = detect_terminal_capabilities(
                    ObservedStream(),  # type: ignore[arg-type]
                    ObservedStream(descriptor=8),  # type: ignore[arg-type]
                    termios_module=CompleteTermios,
                    os_module=fake_os(width=width),
                )
                self.assertEqual(observed.width, expected)

        observed = detect_terminal_capabilities(
            ObservedStream(  # type: ignore[arg-type]
                tty_failure=RuntimeError("uncertain")
            ),
            ObservedStream(descriptor=8),  # type: ignore[arg-type]
            termios_module=CompleteTermios,
            os_module=fake_os(),
        )
        self.assertEqual(observed.profile, CapabilityProfile.LINE)
        self.assertFalse(observed.input_tty)
        self.assertTrue(observed.output_tty)
        self.assertFalse(observed.raw_mode)

    def test_driver_and_callback_constructors_perform_no_io(self) -> None:
        input_stream = ObservedStream()
        output_stream = ObservedStream(descriptor=8)
        termios_provider = mock.Mock(return_value=CompleteTermios)
        os_provider = mock.Mock(return_value=fake_os())
        fallback = mock.Mock(return_value="fallback")
        transition = mock.Mock()
        main_transition = mock.Mock()
        history_append = mock.Mock()
        driver = RawTerminalDriver(
            fallback,
            input_stream,  # type: ignore[arg-type]
            output_stream,  # type: ignore[arg-type]
            transition,
            main_transition,
            history_append,
            termios_provider=termios_provider,
            os_provider=os_provider,
        )
        self.assertIsInstance(driver, RawTerminalDriver)
        self.assertEqual(input_stream.calls, [])
        self.assertEqual(output_stream.calls, [])
        termios_provider.assert_not_called()
        os_provider.assert_not_called()

        read_line = mock.Mock(return_value="line")
        write_text = mock.Mock()
        main_read_line = mock.Mock(return_value="main")
        terminal = InteractiveTerminal(
            read_line,
            write_text,
            width=40,
            main_read_line=main_read_line,
        )
        self.assertIsInstance(terminal, InteractiveTerminal)
        read_line.assert_not_called()
        write_text.assert_not_called()
        main_read_line.assert_not_called()

        prompt_input = InteractivePromptInput(terminal)
        self.assertEqual(prompt_input.__slots__, ("_read",))
        read_line.assert_not_called()
        with self.assertRaisesRegex(TypeError, "InteractiveTerminal"):
            InteractivePromptInput(object())  # type: ignore[arg-type]

    def test_deleted_main_commit_cleanup_seam_leaves_no_terminal_state(self) -> None:
        output = io.StringIO()
        driver = RawTerminalDriver(
            lambda _prompt: "fallback",
            object(),  # type: ignore[arg-type]
            output,
            transition_line_input,
            transition_main_input,
            append_main_input_history,
        )
        self.assertEqual(driver.read_line("story[lobby]> "), "fallback")
        self.assertEqual(output.getvalue(), "")
        self.assertNotIn(
            "_clear_main_commit_display",
            InteractiveTerminal.__slots__,
        )
        self.assertNotIn("_main_commit_display", RawTerminalDriver.__slots__)
        self.assertFalse(hasattr(driver, "_main_commit_display"))
        self.assertFalse(hasattr(driver, "clear_main_commit_display"))

    def test_prompt_adapter_and_driver_fallback_are_typed_literal_and_history_free(
        self,
    ) -> None:
        reads: list[object] = ["\x1b", "/cancel", KeyboardInterrupt(), EOFError()]
        prompts: list[str] = []

        def read_line(prompt: str) -> str:
            prompts.append(prompt)
            value = reads.pop(0)
            if isinstance(value, BaseException):
                raise value
            return value

        terminal = InteractiveTerminal(read_line, lambda _text: None, width=40)
        adapter = InteractivePromptInput(terminal)
        prompt = Prompt(
            "Codex model",
            scope="settings",
            instruction="Enter exact text.",
            default="provider default",
        )
        context = (
            Panel("Settings", (Notice("Pending"),), state=ViewState.OPEN),
        )
        self.assertEqual(
            adapter.read_prompt(prompt, context=context),
            ControlOutcome(ControlOutcomeKind.CANCEL),
        )
        self.assertEqual(
            adapter.read_prompt(prompt, context=context),
            ControlOutcome(ControlOutcomeKind.COMMIT, text="/cancel"),
        )
        self.assertEqual(
            adapter.read_prompt(prompt, context=context),
            ControlOutcome(ControlOutcomeKind.INTERRUPT),
        )
        self.assertEqual(
            adapter.read_prompt(prompt, context=context),
            ControlOutcome(ControlOutcomeKind.EOF),
        )
        self.assertEqual(len(prompts), 4)
        self.assertTrue(all("Settings [open]" in value for value in prompts))
        self.assertTrue(all("Codex model" in value for value in prompts))
        rendered_context = render_plain(context[0], width=40).text
        self.assertTrue(
            all(value.startswith(f"{rendered_context}\n") for value in prompts)
        )
        self.assertTrue(
            all(
                f"{'-' * 40}\nEnter exact text." in value
                for value in prompts
            )
        )
        self.assertTrue(
            all(
                value.endswith("Codex model [settings] > ")
                for value in prompts
            )
        )
        self.assertNotIn("\x1b[", "".join(prompts))

        fallback = mock.Mock(side_effect=["\x1b", "/cancel"])
        driver = RawTerminalDriver(
            fallback,
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            transition_line_input,
            transition_main_input,
            append_main_input_history,
        )
        driver._history_state = MainInputHistoryState(("/status",))
        line_capabilities = capability(CapabilityProfile.LINE, width=40)
        renderer = mock.Mock(
            side_effect=lambda view, observed: render_input_prompt(view, observed)
        )
        view = _model.InputPromptView(context, prompt)
        with mock.patch.object(
            RawTerminalDriver,
            "capabilities",
            autospec=True,
            return_value=line_capabilities,
        ):
            self.assertEqual(
                driver.read_prompt(view, renderer, transition_prompt_input),
                ControlOutcome(ControlOutcomeKind.CANCEL),
            )
            self.assertEqual(
                driver.read_prompt(view, renderer, transition_prompt_input),
                ControlOutcome(ControlOutcomeKind.COMMIT, text="/cancel"),
            )
        self.assertEqual(driver._history_state, MainInputHistoryState(("/status",)))
        self.assertEqual(fallback.call_count, 2)
        self.assertEqual(renderer.call_count, 2)

        for failure, expected in (
            (KeyboardInterrupt(), ControlOutcome(ControlOutcomeKind.INTERRUPT)),
            (EOFError(), ControlOutcome(ControlOutcomeKind.EOF)),
        ):
            fallback.side_effect = failure
            with (
                self.subTest(failure=type(failure).__name__),
                mock.patch.object(
                    RawTerminalDriver,
                    "capabilities",
                    autospec=True,
                    return_value=line_capabilities,
                ),
            ):
                self.assertEqual(
                    driver.read_prompt(view, renderer, transition_prompt_input),
                    expected,
                )

    def test_prompt_driver_raw_delegation_validation_and_failures_are_call_local(
        self,
    ) -> None:
        fallback = mock.Mock(return_value="fallback")
        driver = RawTerminalDriver(
            fallback,
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            transition_line_input,
            transition_main_input,
            append_main_input_history,
        )
        capabilities = capability(CapabilityProfile.ANSI, width=60)
        prompt = Prompt("Maximum debate rounds", scope="settings")
        view = _model.InputPromptView((), prompt)
        frame = render_input_prompt(view, capabilities)
        renderer = mock.Mock(return_value=frame)
        configuration = _driver._TerminalConfiguration(
            7,
            8,
            [0, 0, 0, 0, 0, 0, []],
            [0, 0, 0, 0, 0, 0, []],
            60,
            None,
            None,
            0,
            object(),
        )
        with (
            mock.patch.object(
                RawTerminalDriver,
                "capabilities",
                autospec=True,
                return_value=capabilities,
            ),
            mock.patch.object(
                RawTerminalDriver,
                "_prepare_input",
                autospec=True,
                return_value=(capabilities, configuration),
            ),
            mock.patch.object(
                RawTerminalDriver,
                "_read_control_line",
                autospec=True,
                side_effect=("12", "\x1b"),
            ) as live,
        ):
            self.assertEqual(
                driver.read_prompt(view, renderer, transition_prompt_input),
                ControlOutcome(ControlOutcomeKind.COMMIT, text="12"),
            )
            self.assertEqual(
                driver.read_prompt(view, renderer, transition_prompt_input),
                ControlOutcome(ControlOutcomeKind.CANCEL),
            )
        fallback.assert_not_called()
        self.assertEqual(live.call_count, 2)
        for call in live.call_args_list:
            self.assertIs(call.args[2], _driver._empty_suggestion_preview)
            self.assertEqual(call.args[3], LineInputState())
            self.assertIs(call.args[4], transition_prompt_input)
            self.assertIs(call.kwargs["configuration"], configuration)
            self.assertEqual(call.kwargs["rendered_input"], frame)

        invalid = (
            lambda: driver.read_prompt(object(), renderer, transition_prompt_input),
            lambda: driver.read_prompt(view, object(), transition_prompt_input),
            lambda: driver.read_prompt(view, renderer, object()),
        )
        for operation in invalid:
            with self.subTest(operation=operation), self.assertRaises(TypeError):
                operation()  # type: ignore[misc]

        bad_renderer = mock.Mock(return_value=object())
        with mock.patch.object(
            RawTerminalDriver,
            "capabilities",
            autospec=True,
            return_value=capabilities,
        ):
            with self.assertRaisesRegex(TypeError, "RenderedInputFrame"):
                driver.read_prompt(view, bad_renderer, transition_prompt_input)
            with self.assertRaisesRegex(ValueError, "width"):
                driver.read_prompt(
                    view,
                    lambda _view, _capabilities: RenderedInputFrame(
                        frame.text,
                        frame.plain_text,
                        59,
                        frame.line_count,
                    ),
                    transition_prompt_input,
                )

    def _progress_driver(self) -> tuple[RawTerminalDriver, io.StringIO]:
        """Build a driver whose output can observe progress serialization."""

        output = io.StringIO()
        driver = RawTerminalDriver(
            lambda _prompt: "",
            io.StringIO(),
            output,
            transition_line_input,
            transition_main_input,
            append_main_input_history,
        )
        return driver, output

    def test_advisory_progress_is_silent_for_plain_line_and_callback_terminals(
        self,
    ) -> None:
        async def operation() -> str:
            return "answer"

        for profile in (CapabilityProfile.PLAIN, CapabilityProfile.LINE):
            with self.subTest(profile=profile):
                driver, output = self._progress_driver()
                with mock.patch.object(
                    RawTerminalDriver,
                    "capabilities",
                    autospec=True,
                    return_value=capability(profile),
                ):
                    result = asyncio.run(
                        driver.run_advisory_progress(
                            operation(), render_advisory_progress_frame
                        )
                    )
                self.assertEqual(result, "answer")
                if profile is CapabilityProfile.PLAIN:
                    self.assertIn("◐", output.getvalue())
                    self.assertNotRegex(output.getvalue(), r"\x1b\[[0-9;]*m")
                else:
                    self.assertEqual(output.getvalue(), "")
                self.assertFalse(driver._progress_active)

        callback_output: list[str] = []
        terminal = InteractiveTerminal(
            lambda _prompt: "",
            callback_output.append,
            width=40,
        )
        progress = terminal_facade.InteractiveAdvisoryProgress(terminal)
        self.assertEqual(
            asyncio.run(progress.run(operation())),
            "answer",
        )
        self.assertEqual(callback_output, [])

    def test_capable_advisory_progress_orders_frames_and_serializes_writes(
        self,
    ) -> None:
        driver, output = self._progress_driver()

        async def operation() -> str:
            await asyncio.sleep(0.38)
            driver.write_text("answer\n")
            return "done"

        with mock.patch.object(
            RawTerminalDriver,
            "capabilities",
            autospec=True,
            return_value=capability(CapabilityProfile.ANSI),
        ):
            result = asyncio.run(
                driver.run_advisory_progress(
                    operation(), render_advisory_progress_frame
                )
            )
        rendered = output.getvalue()
        self.assertEqual(result, "done")
        self.assertFalse(driver._progress_active)
        self.assertIsNone(driver._progress_renderer)
        positions = tuple(rendered.index(frame) for frame in ("◐", "◓", "◑", "◒"))
        self.assertEqual(positions, tuple(sorted(positions)))
        self.assertRegex(rendered, r"\r\x1b\[Kanswer\n\r[◐◓◑◒]")
        self.assertTrue(rendered.endswith("\r\x1b[K"))

    def test_advisory_progress_preserves_operation_failure_and_wraps_worker_failure(
        self,
    ) -> None:
        driver, _output = self._progress_driver()
        capable = mock.patch.object(
            RawTerminalDriver,
            "capabilities",
            autospec=True,
            return_value=capability(CapabilityProfile.ANSI),
        )

        async def operation_failure() -> str:
            await asyncio.sleep(0.09)
            raise ValueError("operation failed")

        with capable, mock.patch.object(
            RawTerminalDriver,
            "_clear_progress_frame",
            autospec=True,
            side_effect=OSError("clear failed"),
        ), self.assertRaisesRegex(ValueError, "operation failed"):
            asyncio.run(
                driver.run_advisory_progress(
                    operation_failure(), render_advisory_progress_frame
                )
            )
        self.assertFalse(driver._progress_active)

        async def slow_operation() -> str:
            await asyncio.sleep(0.3)
            return "never reached"

        def broken_renderer(frame: int) -> str:
            if frame:
                raise RuntimeError("tick failed")
            return "◐"

        with capable, self.assertRaises(AdvisoryProgressError):
            asyncio.run(driver.run_advisory_progress(slow_operation(), broken_renderer))
        self.assertFalse(driver._progress_active)
        self.assertIsNone(driver._progress_renderer)

        async def quick_operation() -> str:
            await asyncio.sleep(0.09)
            return "done"

        with capable, mock.patch.object(
            RawTerminalDriver,
            "_draw_progress_frame",
            autospec=True,
            side_effect=OSError("initial draw failed"),
        ), self.assertRaises(AdvisoryProgressError):
            asyncio.run(
                driver.run_advisory_progress(
                    quick_operation(), render_advisory_progress_frame
                )
            )

        class FailingOutput(io.StringIO):
            def write(self, text: str) -> int:
                if text == "answer\n":
                    raise OSError("payload write failed")
                return super().write(text)

        failing_driver = RawTerminalDriver(
            lambda _prompt: "",
            io.StringIO(),
            FailingOutput(),
            transition_line_input,
            transition_main_input,
            append_main_input_history,
        )

        async def write_failure() -> str:
            await asyncio.sleep(0.09)
            failing_driver.write_text("answer\n")
            return "unreachable"

        with capable, self.assertRaisesRegex(OSError, "payload write failed"):
            asyncio.run(
                failing_driver.run_advisory_progress(
                    write_failure(), render_advisory_progress_frame
                )
            )

        with capable, mock.patch.object(
            RawTerminalDriver,
            "_clear_progress_frame",
            autospec=True,
            side_effect=OSError("final clear failed"),
        ), self.assertRaises(AdvisoryProgressError):
            asyncio.run(
                driver.run_advisory_progress(
                    quick_operation(), render_advisory_progress_frame
                )
            )

    def test_stream_factory_builds_one_terminal_around_one_driver(self) -> None:
        driver = mock.Mock(spec=RawTerminalDriver)
        driver.capabilities.return_value = capability(
            CapabilityProfile.ANSI,
            width=120,
        )
        driver.read_line.return_value = "subject"
        driver.read_main_command_line.side_effect = (
            "/status",
            "/help",
            "/session resume alpha",
        )
        driver.read_input_line.side_effect = (
            "composition line",
            "/send",
            "yes",
        )
        driver.read_prompt.return_value = ControlOutcome(
            ControlOutcomeKind.COMMIT,
            text="model",
        )
        fallback = mock.Mock()
        input_stream = object()
        output_stream = object()
        with mock.patch.object(
            _core,
            "RawTerminalDriver",
            return_value=driver,
        ) as driver_factory:
            terminal = _core.build_stream_terminal(
                fallback,
                input_stream,  # type: ignore[arg-type]
                output_stream,  # type: ignore[arg-type]
            )

        driver_factory.assert_called_once_with(
            fallback,
            input_stream,
            output_stream,
            transition_line_input,
            transition_main_command_input,
            append_main_input_history,
        )
        self.assertIsInstance(terminal, InteractiveTerminal)
        self.assertEqual(
            terminal.read_main(),
            TerminalAction(TerminalActionKind.LINE, text="/status"),
        )
        self.assertEqual(
            terminal.compose(ComposeMode.RUN),
            TerminalAction(
                TerminalActionKind.SEND,
                text="composition line",
                line_count=1,
                character_count=16,
            ),
        )
        self.assertEqual(
            terminal.read_main(),
            TerminalAction(TerminalActionKind.LINE, text="/help"),
        )
        self.assertEqual(
            terminal.request_conversation_subject(),
            TerminalAction(TerminalActionKind.LINE, text="subject"),
        )
        self.assertIs(
            terminal.request_confirmation("Continue?", default=False),
            ConfirmationResult.YES,
        )
        self.assertEqual(
            InteractivePromptInput(terminal).read_prompt(
                Prompt("Codex model", scope="settings")
            ),
            ControlOutcome(ControlOutcomeKind.COMMIT, text="model"),
        )
        picker = Picker(
            Prompt("Resume"),
            (
                Choice("/session resume alpha", "Alpha"),
                Choice("/session resume beta", "Beta"),
            ),
        )
        choice_provider = mock.Mock(return_value=picker)
        self.assertEqual(
            InteractiveMainInput(terminal).read_main(choice_provider),
            TerminalAction(
                TerminalActionKind.LINE,
                text="/session resume alpha",
            ),
        )
        terminal.write_status(TerminalMessageKind.RESULT, "ready")
        self.assertEqual(driver.capabilities.call_count, 3)
        main_calls = driver.read_main_command_line.call_args_list
        self.assertEqual(len(main_calls), 3)
        for call in main_calls:
            self.assertEqual(call.args[0], "story[lobby]> ")
            self.assertTrue(callable(call.args[1]))
            self.assertTrue(callable(call.args[2]))
            self.assertIs(call.args[3], render_component)
        for call in main_calls[:2]:
            self.assertEqual(
                call.args[1]("/s", 2),
                _core._main_command_completion("/s", 2),
            )
        dynamic_completion = main_calls[2].args[1]
        dynamic_projector = main_calls[2].args[2]
        dynamic_state, outcome = transition_main_command_input(
            _model.MainCommandControlState(),
            KeyEvent(KeyKind.TEXT, text="/"),
            MainInputHistoryState(),
            dynamic_completion,
        )
        self.assertIsNone(outcome)
        self.assertEqual(
            dynamic_projector(dynamic_state),
            SuggestionPalette(
                "/",
                picker.choices,
                highlighted_id="/session resume alpha",
            ),
        )
        choice_provider.assert_called_once_with("/", 1)
        self.assertEqual(
            driver.read_input_line.call_args_list,
            [
                mock.call(
                    _core._composer_input_view(
                        ComposeMode.RUN,
                        None,
                        ComposerState(),
                    ),
                    render_input_prompt,
                    _controls.project_composition_suggestions,
                    transient=True,
                ),
                mock.call(
                    _core._composer_input_view(
                        ComposeMode.RUN,
                        None,
                        ComposerState(("composition line",)),
                    ),
                    render_input_prompt,
                    _controls.project_composition_suggestions,
                    transient=True,
                ),
                mock.call(
                    _core._confirmation_input_view(
                        "Continue?",
                        default=False,
                        invalid=False,
                    ),
                    render_input_prompt,
                    _core._empty_slash_suggestions,
                    transient=True,
                ),
            ],
        )
        driver.read_line.assert_called_once_with("conversation[subject]> ")
        driver.read_prompt.assert_called_once_with(
            _model.InputPromptView(
                (),
                Prompt("Codex model", scope="settings"),
            ),
            render_input_prompt,
            transition_prompt_input,
            transient=True,
        )
        self.assertEqual(
            driver.write_text.call_args_list,
            [
                mock.call(
                    "\x1b[36m[compose] Consensus run. Use /send to submit or "
                    "/cancel to discard.\x1b[0m\n"
                ),
                mock.call(
                    "\x1b[32m[compose] Sending 1 line, 16 characters to "
                    "consensus.\x1b[0m\n"
                ),
                mock.call("[result] ready\n"),
            ],
        )

    def test_compatibility_editor_contains_only_delegation_not_a_second_driver(
        self,
    ) -> None:
        input_stream = ObservedStream()
        output_stream = ObservedStream(descriptor=8)
        editor = InteractiveMainLineEditor(
            lambda prompt: "fallback",
            input_stream,  # type: ignore[arg-type]
            output_stream,  # type: ignore[arg-type]
        )
        self.assertIsInstance(editor._driver, RawTerminalDriver)
        self.assertIs(RawTerminalDriver, _core.RawTerminalDriver)
        self.assertIs(RawTerminalDriver, terminal_facade.RawTerminalDriver)

        source_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "story_writing_agents"
            / "interactive_line_editor.py"
        )
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        editor_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "InteractiveMainLineEditor"
        )
        self.assertEqual(
            tuple(
                node.name
                for node in editor_class.body
                if isinstance(node, ast.FunctionDef)
            ),
            ("__init__", "read_line", "_read_line"),
        )
        read_line = next(
            node
            for node in editor_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "read_line"
        )
        called_attributes = {
            node.func.attr
            for node in ast.walk(read_line)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertEqual(called_attributes, set())
        called_names = {
            node.func.id
            for node in ast.walk(read_line)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertEqual(called_names, {"read_main_command_line"})
        for duplicated_primitive in (
            "tcgetattr",
            "tcsetattr",
            "get_terminal_size",
            "\\x1b[J",
            "\\x1b[A",
            "\\x1b[C",
        ):
            self.assertNotIn(duplicated_primitive, source)


class PackageBoundaryFoundationTests(unittest.TestCase):
    def test_package_import_graph_is_exact_acyclic_and_clean(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        package_root = repository_root / "src" / "story_writing_agents"
        terminal_root = package_root / "interactive_terminal"
        expected_dependencies = {
            "__init__.py": {"._core"},
            "_input_lifecycle.py": set(),
            "_model.py": set(),
            "_controls.py": {"._model"},
            "_render.py": {"._model"},
            "_viewer_model.py": set(),
            "_viewer_controls.py": {"._model", "._viewer_model"},
            "_viewer_render.py": {"._model", "._viewer_model"},
            "_reference_activation.py": {
                "..writing_artifacts",
                "._model",
            },
            "_driver.py": {
                "._input_lifecycle",
                "._model",
                "._reference_activation",
                "._viewer_model",
            },
            "_core.py": {
                "..agents",
                "..interactive_commands",
                "._controls",
                "._driver",
                "._model",
                "._reference_activation",
                "._render",
                "._viewer_controls",
                "._viewer_model",
                "._viewer_render",
            },
        }
        self.assertEqual(
            {path.name for path in terminal_root.glob("*.py")},
            set(expected_dependencies),
        )
        for filename, expected in expected_dependencies.items():
            tree = ast.parse(
                (terminal_root / filename).read_text(encoding="utf-8")
            )
            dependencies = {
                "." * node.level + (node.module or "")
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.level
            }
            with self.subTest(filename=filename):
                self.assertEqual(dependencies, expected)

        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(package_root.parent)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        modules = (
            "story_writing_agents.interactive_terminal._input_lifecycle",
            "story_writing_agents.interactive_terminal._model",
            "story_writing_agents.interactive_terminal._controls",
            "story_writing_agents.interactive_terminal._render",
            "story_writing_agents.interactive_terminal._viewer_model",
            "story_writing_agents.interactive_terminal._viewer_controls",
            "story_writing_agents.interactive_terminal._viewer_render",
            "story_writing_agents.interactive_terminal._reference_activation",
            "story_writing_agents.interactive_terminal._driver",
            "story_writing_agents.interactive_terminal._core",
            "story_writing_agents.interactive_terminal",
            "story_writing_agents.interactive_line_editor",
        )
        script = (
            "import importlib; "
            f"modules = {modules!r}; "
            "loaded = tuple(importlib.import_module(name).__name__ "
            "for name in modules); "
            "assert loaded == modules; print('ok')"
        )
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run(
                [sys.executable, "-c", script],
                cwd=temporary,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "ok\n")
            self.assertEqual(list(Path(temporary).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
