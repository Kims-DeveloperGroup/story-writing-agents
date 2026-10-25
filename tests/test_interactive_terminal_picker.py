"""Deterministic contracts for generic package-internal picker input."""

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
from story_writing_agents import interactive_terminal as terminal_facade
from story_writing_agents.interactive_terminal import (
    Choice,
    ControlOutcome,
    ControlOutcomeKind,
    InputPresentation,
    InteractivePickerInput,
    InteractivePromptInput,
    InteractiveTerminal,
    Notice,
    Prompt,
    ViewState,
)
from story_writing_agents.interactive_terminal import (
    _controls,
    _core,
    _driver as driver_module,
    _model,
    _render,
)
from story_writing_agents.interactive_terminal._controls import (
    append_main_input_history,
    initialize_picker,
    project_picker_viewport,
    reconcile_picker,
    transition_line_input,
    transition_main_command_input,
    transition_picker,
    transition_picker_fallback,
)
from story_writing_agents.interactive_terminal._driver import RawTerminalDriver
from story_writing_agents.interactive_terminal._model import (
    CapabilityProfile,
    Component,
    Field,
    InputPromptView,
    KeyEvent,
    KeyKind,
    MainInputHistoryState,
    Panel,
    Picker,
    PickerControlState,
    PickerViewport,
    RenderedFrame,
    RenderedInputFrame,
    SemanticRole,
    Section,
    TerminalCapabilities,
    display_character_width,
)
from story_writing_agents.interactive_terminal._render import (
    render_input_prompt,
    render_picker_fallback,
    render_picker_viewport,
    strip_decorative_sgr,
)


def _capabilities(
    profile: CapabilityProfile,
    width: int = 80,
) -> TerminalCapabilities:
    """Build one correlated deterministic capability profile."""

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
    """Measure one sanitized unstyled line using production cell rules."""

    return sum(display_character_width(character) for character in text)


def _choices() -> tuple[Choice, ...]:
    """Return one ordered long vocabulary with opaque stable identities."""

    return (
        Choice("secret-1", "Alpha", description="first"),
        Choice(
            "secret-2",
            "Beta",
            enabled=False,
            disabled_reason="Unavailable",
        ),
        Choice("secret-3", "Gamma"),
        Choice("secret-4", "Delta"),
        Choice("secret-5", "Epsilon"),
        Choice("secret-6", "Zeta"),
        Choice("secret-7", "Eta"),
        Choice("secret-8", "Theta"),
        Choice("secret-9", "Iota"),
        Choice("secret-10", "Kappa"),
    )


def _transient_context() -> tuple[Panel, ...]:
    """Return one changed-only Settings diff for live FORM picker tests."""

    return (
        Panel(
            "Settings",
            (
                Section("Current", (Field("Language", "KO (Changed)"),)),
                Section(
                    "Pending",
                    (
                        Field(
                            "Language",
                            "EN (Changed)",
                            role=SemanticRole.WARNING,
                        ),
                    ),
                ),
                Notice(
                    "Runtime changes apply to this shell after Apply Changes.",
                    label="activation",
                    role=SemanticRole.INFO,
                ),
            ),
            state=ViewState.OPEN,
        ),
    )


def _long_state() -> PickerControlState:
    """Return a windowed state with selection outside the highlighted view."""

    return initialize_picker(
        Picker(
            Prompt("Choose route", instruction="Use arrows."),
            _choices(),
            highlighted_id="secret-8",
            selected_id="secret-10",
        )
    )


class _ScriptedInput:
    """Expose exact UTF-8 byte reads and observable terminal capabilities."""

    def __init__(
        self,
        characters: str,
        *,
        terminal: bool = True,
        malformed: object | None = None,
    ) -> None:
        self.characters = list(characters.encode("utf-8"))
        self.terminal = terminal
        self.malformed = malformed
        self.read_calls = 0
        self.tty_calls = 0
        self.fileno_calls = 0
        self.encoding = "utf-8"
        self.errors = "strict"
        self.buffer = self

    def isatty(self) -> bool:
        self.tty_calls += 1
        return self.terminal

    def fileno(self) -> int:
        self.fileno_calls += 1
        return 7

    def read(self, size: int) -> object:
        raise AssertionError(f"text read must not run for byte input: {size}")

    def read1(self, size: int) -> object:
        if size != 1:
            raise AssertionError(f"unexpected read size: {size}")
        self.read_calls += 1
        if self.malformed is not None:
            return self.malformed
        return bytes((self.characters.pop(0),)) if self.characters else b""


class _RecordingOutput:
    """Record exact picker redraw writes, flushes, and capability checks."""

    def __init__(self, *, terminal: bool = True) -> None:
        self.terminal = terminal
        self.records: list[str] = []
        self.flush_calls = 0
        self.write_failure: BaseException | None = None
        self.flush_failure: BaseException | None = None

    def isatty(self) -> bool:
        return self.terminal

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
    """Model activation and exact restoration around one raw picker read."""

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
    os_name: str = "posix",
    terminal_name: str = "xterm-256color",
    no_color: bool = False,
    widths: tuple[int, ...] = (80,),
    output: _RecordingOutput | None = None,
    termios_double: _FakeTermios | None = None,
    malformed_input: object | None = None,
) -> tuple[
    RawTerminalDriver,
    _ScriptedInput,
    _RecordingOutput,
    mock.Mock,
    _FakeTermios,
    list[int],
]:
    """Build one picker-capable driver with deterministic collaborators."""

    input_stream = _ScriptedInput(
        characters,
        terminal=input_terminal,
        malformed=malformed_input,
    )
    output_stream = output or _RecordingOutput(terminal=output_terminal)
    fallback_read = fallback or mock.Mock(return_value="/cancel")
    termios_value = termios_double or _FakeTermios()
    size_calls: list[int] = []
    environment = {"TERM": terminal_name}
    if no_color:
        environment["NO_COLOR"] = ""

    def get_terminal_size(descriptor: int) -> os.terminal_size:
        size_calls.append(descriptor)
        width = widths[min(len(size_calls) - 1, len(widths) - 1)]
        return os.terminal_size((width, 24))

    def fpathconf(descriptor: int, name: str) -> int:
        if descriptor != 7 or name != "PC_VDISABLE":
            raise AssertionError((descriptor, name))
        return 0

    os_double = SimpleNamespace(
        name=os_name,
        environ=environment,
        get_terminal_size=get_terminal_size,
        fpathconf=fpathconf,
    )
    driver = RawTerminalDriver(
        fallback_read,
        input_stream,  # type: ignore[arg-type]
        output_stream,  # type: ignore[arg-type]
        transition_line_input,
        transition_main_command_input,
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


def _read_picker(
    driver: RawTerminalDriver,
    state: PickerControlState,
    *,
    transient: bool = False,
) -> ControlOutcome:
    """Call the supported picker driver with the production pure seams."""

    return driver.read_picker(
        state,
        transition_picker,
        transition_picker_fallback,
        project_picker_viewport,
        render_picker_viewport,
        render_picker_fallback,
        transient=transient,
    )


class PickerModelContractTests(unittest.TestCase):
    """Lock immutable picker-control values and their package boundary."""

    def test_schemas_signatures_identity_pickle_and_exports_are_exact(self) -> None:
        state = _long_state()
        viewport = project_picker_viewport(state)
        schemas = (
            (PickerControlState, ("picker", "window_start"), state),
            (PickerViewport, ("picker", "start", "stop"), viewport),
        )
        for schema, names, value in schemas:
            with self.subTest(schema=schema.__name__):
                self.assertEqual(tuple(inspect.signature(schema).parameters), names)
                self.assertEqual(tuple(field.name for field in fields(schema)), names)
                self.assertFalse(hasattr(value, "__dict__"))
                self.assertEqual(pickle.loads(pickle.dumps(value)), value)
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, names[0], None)
                self.assertEqual(
                    schema.__module__,
                    "story_writing_agents.interactive_terminal._model",
                )

        functions = (
            (initialize_picker, ("picker",)),
            (reconcile_picker, ("state", "choices")),
            (transition_picker, ("state", "event")),
            (transition_picker_fallback, ("state", "answer")),
            (project_picker_viewport, ("state",)),
            (render_picker_viewport, ("viewport", "capabilities")),
            (render_picker_fallback, ("state", "capabilities")),
        )
        for function, names in functions:
            with self.subTest(function=function.__name__):
                self.assertEqual(tuple(inspect.signature(function).parameters), names)

        self.assertEqual(
            tuple(inspect.signature(RawTerminalDriver.read_picker).parameters),
            (
                "self",
                "state",
                "transition",
                "fallback_transition",
                "viewport_projector",
                "viewport_renderer",
                "fallback_renderer",
                "transient",
            ),
        )
        self.assertEqual(
            tuple(inspect.signature(InteractivePickerInput).parameters),
            ("terminal",),
        )
        self.assertEqual(
            tuple(inspect.signature(InteractivePickerInput.read_picker).parameters),
            (
                "self",
                "prompt",
                "choices",
                "presentation",
                "transient_context",
                "visible_choice_limit",
                "highlighted_id",
                "selected_id",
                "notice",
                "state",
                "transient",
            ),
        )
        self.assertEqual(InteractivePickerInput.__slots__, ("_read",))
        self.assertEqual(
            tuple(inspect.signature(InteractivePromptInput).parameters),
            ("terminal",),
        )
        self.assertEqual(
            tuple(inspect.signature(InteractivePromptInput.read_prompt).parameters),
            (
                "self",
                "prompt",
                "presentation",
                "context",
                "initial_value",
                "transient",
            ),
        )
        self.assertEqual(InteractivePromptInput.__slots__, ("_read",))

        internal_names = {"PickerControlState", "PickerViewport"}
        self.assertTrue(internal_names.isdisjoint(_model.__all__))
        self.assertTrue(internal_names.isdisjoint(terminal_facade.__all__))
        self.assertTrue(internal_names.isdisjoint(story_writing_agents.__all__))
        self.assertTrue(all(not hasattr(terminal_facade, name) for name in internal_names))
        self.assertNotIn(PickerControlState, get_args(Component))
        self.assertNotIn(PickerViewport, get_args(Component))
        self.assertIs(terminal_facade.InteractivePickerInput, InteractivePickerInput)
        self.assertIs(InteractivePickerInput, _core.InteractivePickerInput)
        self.assertIs(terminal_facade.InteractivePromptInput, InteractivePromptInput)
        self.assertIs(InteractivePromptInput, _core.InteractivePromptInput)
        self.assertIs(terminal_facade.InputPresentation, InputPresentation)
        self.assertIs(InputPresentation, _model.InputPresentation)
        self.assertNotIn("InteractivePickerInput", terminal_facade.__all__)
        self.assertNotIn("InteractivePromptInput", terminal_facade.__all__)
        self.assertNotIn("InputPresentation", terminal_facade.__all__)
        self.assertFalse(hasattr(story_writing_agents, "InteractivePickerInput"))
        for name, value in (
            ("Choice", Choice),
            ("Prompt", Prompt),
            ("Notice", Notice),
            ("ControlOutcome", ControlOutcome),
            ("ControlOutcomeKind", ControlOutcomeKind),
            ("ViewState", ViewState),
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(terminal_facade, name), value)
                self.assertNotIn(name, terminal_facade.__all__)

    def test_choice_form_summary_and_inline_roles_are_strict_immutable_values(
        self,
    ) -> None:
        signature = inspect.signature(Choice)
        parameter = signature.parameters["warning_suffix"]
        self.assertIs(parameter.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIsNone(parameter.default)
        summary_parameter = signature.parameters["form_summary"]
        self.assertIs(summary_parameter.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIsNone(summary_parameter.default)
        summary_role_parameter = signature.parameters["form_summary_role"]
        self.assertIs(
            summary_role_parameter.kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        self.assertIs(summary_role_parameter.default, SemanticRole.DEFAULT)
        role_parameter = signature.parameters["description_role"]
        self.assertIs(role_parameter.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIs(role_parameter.default, SemanticRole.DEFAULT)
        self.assertEqual(
            tuple(field.name for field in fields(Choice)),
            (
                "identity",
                "label",
                "warning_suffix",
                "form_summary",
                "form_summary_role",
                "description",
                "description_role",
                "enabled",
                "disabled_reason",
            ),
        )

        default = Choice("save", "Save")
        explicit_none = Choice("save", "Save", warning_suffix=None)
        explicit_default = Choice(
            "save",
            "Save",
            form_summary=None,
            form_summary_role=SemanticRole.DEFAULT,
            description_role=SemanticRole.DEFAULT,
        )
        warned = Choice("save", "Save", warning_suffix="(*Required)")
        self.assertEqual(default, explicit_none)
        self.assertEqual(default, explicit_default)
        self.assertEqual(hash(default), hash(explicit_default))
        self.assertNotEqual(default, warned)
        self.assertEqual(warned.warning_suffix, "(*Required)")
        summarized = Choice(
            "save",
            "Save",
            form_summary="Pending: yes",
            form_summary_role=SemanticRole.WARNING,
        )
        self.assertEqual(summarized.form_summary, "Pending: yes")
        self.assertIs(summarized.form_summary_role, SemanticRole.WARNING)
        self.assertNotEqual(default, summarized)
        self.assertIsInstance(hash(summarized), int)
        described = Choice(
            "save",
            "Save",
            description="Writes settings.",
            description_role=SemanticRole.WARNING,
        )
        self.assertIs(described.description_role, SemanticRole.WARNING)
        self.assertNotEqual(default, described)
        self.assertFalse(hasattr(described, "__dict__"))
        self.assertEqual(pickle.loads(pickle.dumps(described)), described)
        self.assertEqual(pickle.loads(pickle.dumps(summarized)), summarized)
        with self.assertRaises(FrozenInstanceError):
            summarized.form_summary = None  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            described.description_role = SemanticRole.DEFAULT  # type: ignore[misc]
        self.assertIsNone(
            Choice("save", "Save", description="Writes settings.").warning_suffix
        )
        self.assertIs(
            Choice(
                "save",
                "Save",
                description_role=SemanticRole.ERROR,
            ).description_role,
            SemanticRole.ERROR,
        )

        with self.assertRaises(TypeError):
            Choice("save", "Save", "(*Required)")  # type: ignore[misc]
        with self.assertRaises(TypeError):
            Choice("save", "Save", None, "Current: yes")  # type: ignore[misc]
        for invalid, exception in (
            (1, TypeError),
            ("", ValueError),
            ("   ", ValueError),
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(exception):
                    Choice("save", "Save", warning_suffix=invalid)  # type: ignore[arg-type]
                with self.assertRaises(exception):
                    Choice("save", "Save", form_summary=invalid)  # type: ignore[arg-type]
        for invalid in ("muted", ViewState.IDLE, object()):
            with self.subTest(invalid_summary_role=invalid), self.assertRaisesRegex(
                TypeError,
                "form_summary_role must be a SemanticRole",
            ):
                Choice(
                    "save",
                    "Save",
                    form_summary_role=invalid,  # type: ignore[arg-type]
                )
        for invalid in ("warning", ViewState.ERROR, object()):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                TypeError,
                "description_role must be a SemanticRole",
            ):
                Choice("save", "Save", description_role=invalid)  # type: ignore[arg-type]

    def test_models_reject_invalid_state_and_viewport_relations(self) -> None:
        enabled = Choice("one", "One")
        disabled = Choice(
            "off",
            "Off",
            enabled=False,
            disabled_reason="Unavailable",
        )
        invalid = (
            lambda: PickerControlState("picker"),
            lambda: PickerControlState(Picker(Prompt("Choose"), (enabled,)), True),
            lambda: PickerControlState(Picker(Prompt("Choose"), (enabled,)), -1),
            lambda: PickerControlState(Picker(Prompt("Choose"), ()), 1),
            lambda: PickerControlState(
                Picker(Prompt("Choose"), (enabled,), state=ViewState.EMPTY)
            ),
            lambda: PickerControlState(Picker(Prompt("Choose"), ())),
            lambda: PickerControlState(
                Picker(Prompt("Choose"), (disabled,), state=ViewState.OPEN)
            ),
            lambda: PickerControlState(
                Picker(Prompt("Choose"), (enabled,), state=ViewState.DISABLED)
            ),
            lambda: PickerControlState(
                Picker(Prompt("Choose"), (enabled,), state=ViewState.ERROR)
            ),
            lambda: PickerControlState(
                Picker(Prompt("Choose"), (enabled,), state=ViewState.COMPLETED)
            ),
            lambda: PickerViewport("picker", 0, 0),
            lambda: PickerViewport(Picker(Prompt("Choose"), (enabled,)), True, 1),
            lambda: PickerViewport(Picker(Prompt("Choose"), (enabled,)), -1, 1),
            lambda: PickerViewport(Picker(Prompt("Choose"), (enabled,)), 1, 0),
            lambda: PickerViewport(Picker(Prompt("Choose"), (enabled,)), 0, 2),
            lambda: PickerViewport(Picker(Prompt("Choose"), _choices()), 0, 8),
            lambda: PickerViewport(Picker(Prompt("Choose"), (enabled,)), 0, 0),
            lambda: PickerViewport(
                Picker(Prompt("Choose"), (enabled,), highlighted_id="one"),
                0,
                0,
            ),
        )
        for call in invalid:
            with self.subTest(call=call):
                with self.assertRaises((TypeError, ValueError)):
                    call()  # type: ignore[misc]

    def test_form_presentation_and_visible_limits_are_strict_values(self) -> None:
        choices = (Choice("one", "One"),)
        transient_context = _transient_context()
        picker = Picker(
            Prompt("Choose"),
            choices,
            presentation=InputPresentation.FORM,
            transient_context=transient_context,
            visible_choice_limit=9,
        )
        prompt = InputPromptView(
            (),
            Prompt("Value"),
            presentation=InputPresentation.FORM,
            initial_value="seed",
        )
        self.assertIs(picker.presentation, InputPresentation.FORM)
        self.assertEqual(picker.transient_context, transient_context)
        self.assertEqual(picker.visible_choice_limit, 9)
        self.assertIs(prompt.presentation, InputPresentation.FORM)
        self.assertEqual(prompt.initial_value, "seed")
        self.assertEqual(Picker(Prompt("Choose"), choices).visible_choice_limit, 7)
        self.assertIs(
            Picker(Prompt("Choose"), choices).presentation,
            InputPresentation.STANDARD,
        )
        self.assertEqual(Picker(Prompt("Choose"), choices).transient_context, ())
        self.assertIs(
            InputPromptView((), Prompt("Value")).presentation,
            InputPresentation.STANDARD,
        )
        self.assertEqual(
            tuple(
                Picker(
                    Prompt("Choose"),
                    choices,
                    visible_choice_limit=limit,
                ).visible_choice_limit
                for limit in (1, 9)
            ),
            (1, 9),
        )

        for limit, exception in (
            (True, TypeError),
            (1.0, TypeError),
            (0, ValueError),
            (10, ValueError),
        ):
            with self.subTest(limit=limit), self.assertRaises(exception):
                Picker(
                    Prompt("Choose"),
                    choices,
                    visible_choice_limit=limit,  # type: ignore[arg-type]
                )
        for value in ("form", ViewState.OPEN, object()):
            with self.subTest(presentation=value):
                with self.assertRaisesRegex(TypeError, "InputPresentation"):
                    Picker(
                        Prompt("Choose"),
                        choices,
                        presentation=value,  # type: ignore[arg-type]
                    )
                with self.assertRaisesRegex(TypeError, "InputPresentation"):
                    InputPromptView(
                        (),
                        Prompt("Value"),
                        presentation=value,  # type: ignore[arg-type]
                    )

        transient_parameter = inspect.signature(Picker).parameters[
            "transient_context"
        ]
        self.assertIs(transient_parameter.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertEqual(transient_parameter.default, ())
        self.assertIn("transient_context", tuple(field.name for field in fields(Picker)))
        self.assertEqual(pickle.loads(pickle.dumps(picker)), picker)
        self.assertFalse(hasattr(picker, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            picker.transient_context = ()  # type: ignore[misc]

        exact_context = (
            transient_context[0],
            Section("Scope", (Notice("Section notice"),)),
            Field("Current value", "one"),
            Notice("Notice"),
        )
        self.assertEqual(
            Picker(
                Prompt("Choose"),
                choices,
                presentation=InputPresentation.FORM,
                transient_context=exact_context,
            ).transient_context,
            exact_context,
        )
        for invalid_context in (
            [transient_context[0]],
            (Choice("bad", "Unsupported"),),
            (object(),),
        ):
            with self.subTest(invalid_context=invalid_context), self.assertRaisesRegex(
                TypeError,
                "transient_context",
            ):
                Picker(
                    Prompt("Choose"),
                    choices,
                    presentation=InputPresentation.FORM,
                    transient_context=invalid_context,  # type: ignore[arg-type]
                )
        with self.assertRaisesRegex(ValueError, "form presentation"):
            Picker(
                Prompt("Choose"),
                choices,
                transient_context=transient_context,
            )
        for namespace in (_model.__all__, terminal_facade.__all__, story_writing_agents.__all__):
            self.assertNotIn("transient_context", namespace)


class PurePickerControlTests(unittest.TestCase):
    """Cover initialization, reconciliation, navigation, and fallback purity."""

    def test_initialization_precedence_empty_disabled_and_error_are_exact(self) -> None:
        choices = (
            Choice("one", "One"),
            Choice("two", "Two"),
        )
        cases = (
            (
                Picker(
                    Prompt("Choose"),
                    choices,
                    highlighted_id="two",
                    selected_id="one",
                ),
                "two",
            ),
            (Picker(Prompt("Choose"), choices, selected_id="two"), "two"),
            (Picker(Prompt("Choose"), choices), "one"),
        )
        for picker, expected in cases:
            with self.subTest(expected=expected):
                state = initialize_picker(picker)
                self.assertEqual(state.picker.highlighted_id, expected)
                self.assertEqual(state.picker.choices, choices)
                self.assertIs(state.picker.state, ViewState.OPEN)
                self.assertEqual(state.window_start, 0)

        empty = initialize_picker(Picker(Prompt("Choose"), ()))
        self.assertEqual(
            empty,
            PickerControlState(
                Picker(
                    Prompt("Choose"),
                    (),
                    notice=Notice(
                        "No choices available.",
                        label="empty",
                        role=SemanticRole.MUTED,
                        state=ViewState.EMPTY,
                    ),
                    state=ViewState.EMPTY,
                )
            ),
        )
        disabled_choices = (
            Choice("off-1", "Off one", enabled=False, disabled_reason="No"),
            Choice("off-2", "Off two", enabled=False),
        )
        disabled = initialize_picker(Picker(Prompt("Choose"), disabled_choices))
        self.assertIs(disabled.picker.state, ViewState.DISABLED)
        self.assertIsNone(disabled.picker.highlighted_id)

        error_notice = Notice(
            "Trusted failure.",
            label="error",
            role=SemanticRole.ERROR,
            state=ViewState.ERROR,
        )
        error = initialize_picker(
            Picker(
                Prompt("Choose"),
                choices,
                notice=error_notice,
                state=ViewState.ERROR,
            )
        )
        self.assertIs(error.picker.state, ViewState.ERROR)
        self.assertIs(error.picker.notice, error_notice)
        self.assertEqual(error.picker.highlighted_id, "one")

    def test_reconciliation_preserves_ids_falls_by_ordinal_and_clamps_window(self) -> None:
        original = initialize_picker(
            Picker(
                Prompt("Choose"),
                _choices(),
                highlighted_id="secret-8",
                selected_id="secret-10",
            )
        )
        self.assertEqual(original.window_start, 1)
        reordered = (
            _choices()[9],
            *_choices()[:7],
            _choices()[8],
            _choices()[7],
        )
        preserved = reconcile_picker(original, reordered)
        self.assertEqual(preserved.picker.highlighted_id, "secret-8")
        self.assertEqual(preserved.picker.selected_id, "secret-10")
        self.assertEqual(preserved.picker.choices, reordered)
        self.assertEqual(preserved.window_start, 3)

        selected_disabled = tuple(
            Choice(
                choice.identity,
                choice.label,
                description=choice.description,
                enabled=False,
                disabled_reason="No longer available",
            )
            if choice.identity == "secret-10"
            else choice
            for choice in _choices()
        )
        disabled_selection = reconcile_picker(original, selected_disabled)
        self.assertEqual(disabled_selection.picker.highlighted_id, "secret-8")
        self.assertIsNone(disabled_selection.picker.selected_id)

        refreshed = (
            Choice("new-1", "New one"),
            Choice("new-2", "New two", enabled=False),
            Choice("new-3", "New three"),
        )
        forward = reconcile_picker(original, refreshed)
        self.assertEqual(forward.picker.highlighted_id, "new-3")
        self.assertIsNone(forward.picker.selected_id)
        self.assertEqual(forward.window_start, 0)

        preceding = reconcile_picker(
            original,
            (
                Choice("before", "Before"),
                Choice("disabled", "Disabled", enabled=False),
            ),
        )
        self.assertEqual(preceding.picker.highlighted_id, "before")
        self.assertIsNone(preceding.picker.selected_id)

        empty = reconcile_picker(original, ())
        self.assertIs(empty.picker.state, ViewState.EMPTY)
        self.assertEqual(empty.window_start, 0)
        all_disabled = reconcile_picker(
            original,
            (Choice("off", "Off", enabled=False, disabled_reason="No"),),
        )
        self.assertIs(all_disabled.picker.state, ViewState.DISABLED)
        self.assertIsNone(all_disabled.picker.highlighted_id)
        self.assertEqual(all_disabled.window_start, 0)

        with self.assertRaisesRegex(ValueError, "unique"):
            reconcile_picker(
                original,
                (Choice("same", "One"), Choice("same", "Two")),
            )
        completed, _outcome = transition_picker(
            initialize_picker(Picker(Prompt("Choose"), (Choice("one", "One"),))),
            KeyEvent(KeyKind.ENTER),
        )
        with self.assertRaisesRegex(ValueError, "cannot be reconciled"):
            reconcile_picker(completed, (Choice("one", "One"),))

    def test_navigation_skips_disabled_clamps_and_keeps_selection_distinct(self) -> None:
        choices = (
            Choice("one", "One"),
            Choice("off", "Off", enabled=False),
            Choice("three", "Three"),
            Choice("four", "Four"),
        )
        state = initialize_picker(
            Picker(Prompt("Choose"), choices, selected_id="four")
        )
        self.assertEqual(state.picker.highlighted_id, "four")
        state, outcome = transition_picker(state, KeyEvent(KeyKind.UP))
        self.assertIsNone(outcome)
        self.assertEqual(state.picker.highlighted_id, "three")
        self.assertEqual(state.picker.selected_id, "four")
        state, _ = transition_picker(state, KeyEvent(KeyKind.UP))
        self.assertEqual(state.picker.highlighted_id, "one")
        unchanged, outcome = transition_picker(state, KeyEvent(KeyKind.UP))
        self.assertIs(unchanged, state)
        self.assertIsNone(outcome)
        state, _ = transition_picker(state, KeyEvent(KeyKind.DOWN))
        self.assertEqual(state.picker.highlighted_id, "three")

        completed, outcome = transition_picker(state, KeyEvent(KeyKind.ENTER))
        self.assertEqual(
            outcome,
            ControlOutcome(ControlOutcomeKind.COMMIT, text="three"),
        )
        self.assertIs(completed.picker.state, ViewState.COMPLETED)
        self.assertEqual(completed.picker.highlighted_id, "three")
        self.assertEqual(completed.picker.selected_id, "three")

        prior = initialize_picker(
            Picker(Prompt("Choose"), choices, selected_id="four")
        )
        cancelled, outcome = transition_picker(prior, KeyEvent(KeyKind.ESCAPE))
        self.assertEqual(outcome, ControlOutcome(ControlOutcomeKind.CANCEL))
        self.assertIs(cancelled.picker.state, ViewState.CANCELLED)
        self.assertEqual(cancelled.picker.selected_id, "four")
        for kind, expected in (
            (KeyKind.INTERRUPT, ControlOutcomeKind.INTERRUPT),
            (KeyKind.EOF, ControlOutcomeKind.EOF),
        ):
            with self.subTest(kind=kind):
                unchanged, outcome = transition_picker(prior, KeyEvent(kind))
                self.assertIs(unchanged, prior)
                self.assertEqual(outcome, ControlOutcome(expected))

    def test_unrelated_keys_and_uncommittable_states_are_exact_noops(self) -> None:
        state = initialize_picker(Picker(Prompt("Choose"), (Choice("one", "One"),)))
        for event in (
            KeyEvent(KeyKind.TEXT, text="x"),
            KeyEvent(KeyKind.LEFT),
            KeyEvent(KeyKind.RIGHT),
            KeyEvent(KeyKind.HOME),
            KeyEvent(KeyKind.END),
            KeyEvent(KeyKind.TAB),
            KeyEvent(KeyKind.SHIFT_TAB),
            KeyEvent(KeyKind.BACKSPACE),
            KeyEvent(KeyKind.DELETE),
        ):
            with self.subTest(event=event.kind):
                unchanged, outcome = transition_picker(state, event)
                self.assertIs(unchanged, state)
                self.assertIsNone(outcome)

        for blocked in (
            initialize_picker(Picker(Prompt("Choose"), ())),
            initialize_picker(
                Picker(
                    Prompt("Choose"),
                    (Choice("off", "Off", enabled=False),),
                )
            ),
            initialize_picker(
                Picker(
                    Prompt("Choose"),
                    (Choice("one", "One"),),
                    notice=Notice("Failure", state=ViewState.ERROR),
                    state=ViewState.ERROR,
                )
            ),
        ):
            with self.subTest(state=blocked.picker.state):
                unchanged, outcome = transition_picker(
                    blocked,
                    KeyEvent(KeyKind.ENTER),
                )
                self.assertIs(unchanged, blocked)
                self.assertIsNone(outcome)

    def test_viewport_is_max_seven_and_moves_only_as_needed(self) -> None:
        state = initialize_picker(Picker(Prompt("Choose"), _choices()))
        self.assertEqual(project_picker_viewport(state), PickerViewport(state.picker, 0, 7))
        expected = (
            ("secret-3", 0, 7),
            ("secret-4", 0, 7),
            ("secret-5", 0, 7),
            ("secret-6", 0, 7),
            ("secret-7", 0, 7),
            ("secret-8", 1, 8),
            ("secret-9", 2, 9),
            ("secret-10", 3, 10),
        )
        for identity, start, stop in expected:
            state, outcome = transition_picker(state, KeyEvent(KeyKind.DOWN))
            self.assertIsNone(outcome)
            self.assertEqual(state.picker.highlighted_id, identity)
            self.assertEqual(project_picker_viewport(state).start, start)
            self.assertEqual(project_picker_viewport(state).stop, stop)
        state, _ = transition_picker(state, KeyEvent(KeyKind.UP))
        self.assertEqual(project_picker_viewport(state).start, 3)
        for _index in range(6):
            state, _ = transition_picker(state, KeyEvent(KeyKind.UP))
        self.assertEqual(state.picker.highlighted_id, "secret-3")
        self.assertEqual(project_picker_viewport(state).start, 2)
        state, _ = transition_picker(state, KeyEvent(KeyKind.UP))
        self.assertEqual(state.picker.highlighted_id, "secret-1")
        self.assertEqual(project_picker_viewport(state).start, 0)

    def test_form_limit_controls_viewport_and_survives_all_state_transitions(
        self,
    ) -> None:
        transient_context = _transient_context()
        picker = Picker(
            Prompt("Settings"),
            _choices(),
            presentation=InputPresentation.FORM,
            transient_context=transient_context,
            visible_choice_limit=3,
            highlighted_id="secret-8",
            selected_id="secret-1",
        )
        state = initialize_picker(picker)
        self.assertEqual(
            project_picker_viewport(state),
            PickerViewport(state.picker, 5, 8),
        )
        self.assertIs(state.picker.presentation, InputPresentation.FORM)
        self.assertEqual(state.picker.transient_context, transient_context)
        self.assertEqual(state.picker.visible_choice_limit, 3)

        moved, outcome = transition_picker(state, KeyEvent(KeyKind.DOWN))
        self.assertIsNone(outcome)
        self.assertEqual(moved.picker.highlighted_id, "secret-9")
        self.assertEqual(moved.picker.transient_context, transient_context)
        self.assertEqual(project_picker_viewport(moved).start, 6)
        reconciled = reconcile_picker(moved, _choices()[1:])
        self.assertIs(reconciled.picker.presentation, InputPresentation.FORM)
        self.assertEqual(reconciled.picker.transient_context, transient_context)
        self.assertEqual(reconciled.picker.visible_choice_limit, 3)
        self.assertEqual(
            (
                project_picker_viewport(reconciled).start,
                project_picker_viewport(reconciled).stop,
            ),
            (6, 9),
        )

        completed, outcome = transition_picker(
            reconciled,
            KeyEvent(KeyKind.ENTER),
        )
        self.assertEqual(outcome.text, "secret-9")
        self.assertIs(completed.picker.presentation, InputPresentation.FORM)
        self.assertEqual(completed.picker.transient_context, transient_context)
        self.assertEqual(completed.picker.visible_choice_limit, 3)

        rejected, outcome = transition_picker_fallback(state, "0")
        self.assertIs(outcome.kind, ControlOutcomeKind.REJECTED)
        self.assertIs(rejected.picker.presentation, InputPresentation.FORM)
        self.assertEqual(rejected.picker.transient_context, transient_context)
        self.assertEqual(rejected.picker.visible_choice_limit, 3)
        cancelled, outcome = transition_picker_fallback(rejected, "/cancel")
        self.assertIs(outcome.kind, ControlOutcomeKind.CANCEL)
        self.assertIs(cancelled.picker.presentation, InputPresentation.FORM)
        self.assertEqual(cancelled.picker.transient_context, transient_context)
        self.assertEqual(cancelled.picker.visible_choice_limit, 3)

        live_cancelled, outcome = transition_picker(
            state,
            KeyEvent(KeyKind.ESCAPE),
        )
        self.assertIs(outcome.kind, ControlOutcomeKind.CANCEL)
        self.assertEqual(
            live_cancelled.picker.transient_context,
            transient_context,
        )

        fallback_committed, outcome = transition_picker_fallback(state, "1")
        self.assertIs(outcome.kind, ControlOutcomeKind.COMMIT)
        self.assertEqual(
            fallback_committed.picker.transient_context,
            transient_context,
        )

    def test_fallback_grammar_blank_cancel_ordinals_and_huge_values_are_exact(self) -> None:
        initial = initialize_picker(
            Picker(
                Prompt("Choose"),
                _choices()[:3],
                highlighted_id="secret-3",
                selected_id="secret-1",
            )
        )
        committed, outcome = transition_picker_fallback(initial, "")
        self.assertEqual(
            outcome,
            ControlOutcome(ControlOutcomeKind.COMMIT, text="secret-3"),
        )
        self.assertIs(committed.picker.state, ViewState.COMPLETED)

        committed, outcome = transition_picker_fallback(initial, "1")
        self.assertEqual(outcome.text, "secret-1")
        self.assertEqual(committed.picker.selected_id, "secret-1")
        committed, outcome = transition_picker_fallback(initial, "3")
        self.assertEqual(
            outcome,
            ControlOutcome(ControlOutcomeKind.COMMIT, text="secret-3"),
        )
        self.assertEqual(committed.picker.selected_id, "secret-3")
        cancelled, outcome = transition_picker_fallback(initial, "/cancel")
        self.assertEqual(outcome, ControlOutcome(ControlOutcomeKind.CANCEL))
        self.assertIs(cancelled.picker.state, ViewState.CANCELLED)
        self.assertEqual(cancelled.picker.selected_id, "secret-1")

        invalid_answers = (
            "0",
            "00",
            "01",
            "+1",
            "-1",
            " 1",
            "1 ",
            "\t",
            "١",
            "１",
            "one",
            "Alpha",
            "secret-1",
            "/CANCEL",
            "/cancel ",
            "2",
            "4",
            "9" * 10_000,
        )
        for answer in invalid_answers:
            with self.subTest(answer=answer[:20]):
                rejected, outcome = transition_picker_fallback(initial, answer)
                self.assertEqual(outcome, ControlOutcome(ControlOutcomeKind.REJECTED))
                self.assertEqual(rejected.picker.selected_id, "secret-1")
                self.assertEqual(rejected.picker.highlighted_id, "secret-3")
                self.assertEqual(
                    rejected.picker.notice,
                    Notice(
                        "Enter an enabled choice number, press Enter for the "
                        "highlighted choice, or enter /cancel.",
                        label="validation",
                        role=SemanticRole.ERROR,
                        state=ViewState.ERROR,
                    ),
                )

        without_highlight = initialize_picker(Picker(Prompt("Choose"), ()))
        rejected, outcome = transition_picker_fallback(without_highlight, "")
        self.assertEqual(outcome.kind, ControlOutcomeKind.REJECTED)
        self.assertIs(rejected.picker.state, ViewState.EMPTY)

    def test_huge_fallback_ordinal_rejects_before_integer_conversion(self) -> None:
        initial = initialize_picker(
            Picker(
                Prompt("Choose"),
                _choices()[:3],
                highlighted_id="secret-3",
                selected_id="secret-1",
            )
        )
        with mock.patch.object(
            _controls,
            "int",
            create=True,
            side_effect=AssertionError("integer conversion must not run"),
        ) as integer_conversion:
            rejected, outcome = transition_picker_fallback(
                initial,
                "9" * 10_000,
            )

        integer_conversion.assert_not_called()
        self.assertEqual(outcome, ControlOutcome(ControlOutcomeKind.REJECTED))
        self.assertEqual(
            rejected,
            PickerControlState(
                Picker(
                    initial.picker.prompt,
                    initial.picker.choices,
                    highlighted_id="secret-3",
                    selected_id="secret-1",
                    notice=Notice(
                        "Enter an enabled choice number, press Enter for the "
                        "highlighted choice, or enter /cancel.",
                        label="validation",
                        role=SemanticRole.ERROR,
                        state=ViewState.ERROR,
                    ),
                    state=ViewState.OPEN,
                ),
                initial.window_start,
            ),
        )


class PickerRenderingTests(unittest.TestCase):
    """Lock exact semantic picker frames across capability profiles."""

    def test_wide_plain_ansi_and_complete_fallback_bytes_are_exact(self) -> None:
        state = _long_state()
        viewport = project_picker_viewport(state)
        plain = render_picker_viewport(
            viewport,
            _capabilities(CapabilityProfile.PLAIN),
        )
        expected_plain = (
            "Choose route [focused] [open] >\n"
            "  Use arrows.\n"
            "Choices (highlighted 8/10; showing 2-8/10) [open]\n"
            "  2. [disabled] Beta (disabled: Unavailable)\n"
            "  3. [available] Gamma\n"
            "  4. [available] Delta\n"
            "  5. [available] Epsilon\n"
            "  6. [available] Zeta\n"
            "  7. [available] Eta\n"
            "  8. [highlighted] Theta\n"
            "  Selected outside view: 10. [selected] Kappa"
        )
        self.assertEqual(plain, RenderedFrame(expected_plain, 80, 11))

        ansi = render_picker_viewport(
            viewport,
            _capabilities(CapabilityProfile.ANSI),
        )
        self.assertEqual(strip_decorative_sgr(ansi.text), expected_plain)
        self.assertEqual(
            ansi.text,
            "\x1b[35mChoose route [focused] [open] >\x1b[0m\n"
            "\x1b[37m  Use arrows.\x1b[0m\n"
            "\x1b[1mChoices (highlighted 8/10; showing 2-8/10) [open]\x1b[0m\n"
            "\x1b[37m  2. [disabled] Beta (disabled: Unavailable)\x1b[0m\n"
            "  3. [available] Gamma\n"
            "  4. [available] Delta\n"
            "  5. [available] Epsilon\n"
            "  6. [available] Zeta\n"
            "  7. [available] Eta\n"
            "\x1b[1m\x1b[7m  8. [highlighted] Theta\x1b[0m\n"
            "\x1b[32m  Selected outside view: 10. [selected] Kappa\x1b[0m",
        )
        allowed = {
            "\x1b[0m",
            "\x1b[1m",
            "\x1b[7m",
            "\x1b[31m",
            "\x1b[32m",
            "\x1b[33m",
            "\x1b[34m",
            "\x1b[35m",
            "\x1b[36m",
            "\x1b[37m",
        }
        self.assertTrue(
            set(re.findall(r"\x1b\[[0-9;]*[A-Za-z]", ansi.text)).issubset(allowed)
        )

        fallback = render_picker_fallback(
            state,
            _capabilities(CapabilityProfile.LINE),
        )
        self.assertEqual(
            fallback.text,
            "Choices (highlighted 8/10) [open]\n"
            "  1. [available] Alpha — first\n"
            "  2. [disabled] Beta (disabled: Unavailable)\n"
            "  3. [available] Gamma\n"
            "  4. [available] Delta\n"
            "  5. [available] Epsilon\n"
            "  6. [available] Zeta\n"
            "  7. [available] Eta\n"
            "  8. [highlighted] Theta\n"
            "  9. [available] Iota\n"
            "  10. [selected] Kappa\n"
            "[input] Enter a choice number, press Enter for the highlighted choice, or enter \n"
            "        /cancel.\n"
            "Use arrows.\n"
            "Choose route > ",
        )
        self.assertEqual(fallback.plain_text, fallback.text)
        self.assertEqual((fallback.width, fallback.line_count), (80, 15))
        for identity in (choice.identity for choice in _choices()):
            self.assertNotIn(identity, plain.text)
            self.assertNotIn(identity, ansi.text)
            self.assertNotIn(identity, fallback.text)

        explicit_default_choices = (
            Choice(
                "secret-1",
                "Alpha",
                description="first",
                description_role=SemanticRole.DEFAULT,
            ),
            *_choices()[1:],
        )
        explicit_default = initialize_picker(
            Picker(
                state.picker.prompt,
                explicit_default_choices,
                highlighted_id=state.picker.highlighted_id,
                selected_id=state.picker.selected_id,
            )
        )
        self.assertEqual(
            render_picker_fallback(
                explicit_default,
                _capabilities(CapabilityProfile.LINE),
            ),
            fallback,
        )
        omitted_description = _render.render_component(
            Picker(Prompt("Choose"), (Choice("one", "One"),)),
            _capabilities(CapabilityProfile.ANSI),
        )
        inert_role = _render.render_component(
            Picker(
                Prompt("Choose"),
                (
                    Choice(
                        "one",
                        "One",
                        description_role=SemanticRole.ERROR,
                    ),
                ),
            ),
            _capabilities(CapabilityProfile.ANSI),
        )
        self.assertEqual(inert_role, omitted_description)

    def test_form_picker_plain_ansi_and_fallback_are_exact_and_uncluttered(
        self,
    ) -> None:
        choices = (
            Choice(
                "language",
                "Language",
                form_summary="Current: KO",
                form_summary_role=SemanticRole.MUTED,
            ),
            Choice(
                "persona",
                "Persona",
                form_summary="Pending: noir",
                form_summary_role=SemanticRole.WARNING,
            ),
            Choice(
                "save",
                "Apply Changes",
                warning_suffix="(*Required)",
                description="Save pending settings.",
            ),
            Choice(
                "off",
                "Unavailable",
                form_summary="Pending: unavailable",
                form_summary_role=SemanticRole.WARNING,
                enabled=False,
                disabled_reason="Not supported.",
            ),
        )
        state = initialize_picker(
            Picker(
                Prompt(
                    "Settings",
                    scope="Workspace defaults",
                    instruction="Choose a setting to edit.",
                ),
                choices,
                presentation=InputPresentation.FORM,
                visible_choice_limit=4,
                highlighted_id="save",
                selected_id="persona",
            )
        )
        expected = (
            "Settings\n"
            "Context: Workspace defaults\n"
            "\n"
            "Choose a setting to edit.\n"
            "\n"
            "Choices 3/4\n"
            "  1. Language                  — Current: KO\n"
            "  2. Persona (selected)        — Pending: noir\n"
            "> 3. Apply Changes (*Required)\n"
            "     Save pending settings.\n"
            "  4. Unavailable (disabled)    — Pending: unavailable\n"
            "     Disabled: Not supported."
        )
        plain = render_picker_viewport(
            project_picker_viewport(state),
            _capabilities(CapabilityProfile.PLAIN),
        )
        ansi = render_picker_viewport(
            project_picker_viewport(state),
            _capabilities(CapabilityProfile.ANSI),
        )
        self.assertEqual(plain.text, expected)
        self.assertEqual(strip_decorative_sgr(ansi.text), expected)
        self.assertIn("— \x1b[37mCurrent: KO\x1b[0m", ansi.text)
        self.assertIn(
            "\x1b[32m\x1b[1m\x1b[7m  2. \x1b[0m\x1b[32mPersona (selected)        — \x1b[0m"
            "\x1b[33mPending: noir\x1b[0m",
            ansi.text,
        )
        self.assertIn(
            "\x1b[37m  4. Unavailable (disabled)    — \x1b[0m"
            "\x1b[33mPending: unavailable\x1b[0m",
            ansi.text,
        )
        self.assertIn(
            "\x1b[36m\x1b[1m\x1b[7m> 3. \x1b[0m\x1b[36mApply Changes \x1b[0m"
            "\x1b[33m(*Required)\x1b[0m",
            ansi.text,
        )

        fallback = render_picker_fallback(
            state,
            _capabilities(CapabilityProfile.LINE),
        )
        self.assertEqual(
            fallback.text,
            expected
            + "\n\nEnter a choice number, press Enter for the active choice, "
            "or enter /cancel.\nChoice > ",
        )
        self.assertEqual(fallback.plain_text, fallback.text)
        for noisy in (
            "[available]",
            "[highlighted]",
            "[focused]",
            "[open]",
            "showing",
            "highlighted ",
        ):
            with self.subTest(noisy=noisy):
                self.assertNotIn(noisy, expected)

    def test_form_summary_exact_twenty_cell_boundary_uses_complete_picker(
        self,
    ) -> None:
        choices = (
            Choice(
                "short",
                "A",
                form_summary="Current: 12345678901234567890",
                form_summary_role=SemanticRole.MUTED,
            ),
            Choice(
                "long",
                "Longest label",
                form_summary="Pending: two",
                form_summary_role=SemanticRole.WARNING,
            ),
        )
        state = initialize_picker(
            Picker(
                Prompt("Settings"),
                choices,
                presentation=InputPresentation.FORM,
                visible_choice_limit=1,
                highlighted_id="short",
            )
        )
        self.assertEqual(project_picker_viewport(state).stop, 1)

        inline = render_picker_viewport(
            project_picker_viewport(state),
            _capabilities(CapabilityProfile.PLAIN, 41),
        )
        self.assertEqual(
            inline.text,
            "Settings\n\n"
            "Choices 1/2\n"
            "> 1. A             — Current: 12345678901\n"
            "                     234567890",
        )
        inline_summary = next(
            line for line in inline.text.splitlines() if "Current:" in line
        )
        self.assertEqual(
            _physical_width(inline_summary[: inline_summary.index("Current:")]),
            21,
        )
        self.assertEqual((inline.width, inline.line_count), (41, 5))

        moved, outcome = transition_picker(state, KeyEvent(KeyKind.DOWN))
        self.assertIsNone(outcome)
        self.assertEqual(project_picker_viewport(moved).start, 1)
        moved_inline = render_picker_viewport(
            project_picker_viewport(moved),
            _capabilities(CapabilityProfile.PLAIN, 41),
        )
        pending_line = next(
            line for line in moved_inline.text.splitlines() if "Pending:" in line
        )
        self.assertEqual(pending_line, "> 2. Longest label — Pending: two")
        self.assertEqual(
            _physical_width(pending_line[: pending_line.index("Pending:")]),
            21,
        )

        stacked = render_picker_viewport(
            project_picker_viewport(state),
            _capabilities(CapabilityProfile.PLAIN, 40),
        )
        self.assertEqual(
            stacked.text,
            "Settings\n\n"
            "Choices 1/2\n"
            "> 1. A\n"
            "     Current: 12345678901234567890",
        )
        self.assertNotIn(" — ", stacked.text)
        self.assertEqual((stacked.width, stacked.line_count), (40, 5))

        inline_fallback = render_picker_fallback(
            state,
            _capabilities(CapabilityProfile.LINE, 41),
        )
        stacked_fallback = render_picker_fallback(
            state,
            _capabilities(CapabilityProfile.LINE, 40),
        )
        self.assertIn(
            "> 1. A             — Current: 12345678901\n"
            "                     234567890\n"
            "  2. Longest label — Pending: two",
            inline_fallback.text,
        )
        self.assertNotIn(" — ", stacked_fallback.text)
        self.assertIn(
            "> 1. A\n"
            "     Current: 12345678901234567890\n"
            "  2. Longest label\n"
            "     Pending: two",
            stacked_fallback.text,
        )
        for frame in (inline_fallback, stacked_fallback):
            self.assertEqual(frame.line_count, len(frame.text.splitlines()))

    def test_form_summary_unicode_controls_wrap_and_keep_independent_roles(
        self,
    ) -> None:
        choices = (
            Choice(
                "current",
                "Current e\u0301",
                form_summary="Current: one",
                form_summary_role=SemanticRole.MUTED,
            ),
            Choice(
                "pending",
                "Pending 界e\u0301",
                form_summary=(
                    "Pending: 界e\u0301 danger\x1b[31m\x00 across rows"
                ),
                form_summary_role=SemanticRole.WARNING,
            ),
            Choice(
                "disabled",
                "Disabled",
                form_summary="Pending: three",
                form_summary_role=SemanticRole.WARNING,
                enabled=False,
                disabled_reason="Unavailable",
            ),
        )
        state = initialize_picker(
            Picker(
                Prompt("Settings"),
                choices,
                presentation=InputPresentation.FORM,
                highlighted_id="current",
                selected_id="pending",
            )
        )
        plain = render_picker_viewport(
            project_picker_viewport(state),
            _capabilities(CapabilityProfile.PLAIN, 52),
        )
        ansi = render_picker_viewport(
            project_picker_viewport(state),
            _capabilities(CapabilityProfile.ANSI, 52),
        )
        self.assertEqual(strip_decorative_sgr(ansi.text), plain.text)
        compact = "".join(plain.text.split())
        self.assertIn("Currente\u0301—Current:one", compact)
        self.assertIn(
            r"Pending界é(selected)—Pending:界édanger\x1b[31m\x00acrossrows",
            compact,
        )
        summary_starts = []
        for line in plain.text.splitlines():
            if " — " not in line:
                continue
            token = "Current:" if "Current:" in line else "Pending:"
            summary_starts.append(
                _physical_width(line[: line.index(token)])
            )
        self.assertEqual(len(summary_starts), 3)
        self.assertEqual(len(set(summary_starts)), 1)
        self.assertNotIn("\x1b[31m", plain.text)
        self.assertNotIn("\x00", plain.text)
        self.assertIn(
            "\x1b[36m\x1b[1m\x1b[7m> 1. \x1b[0m\x1b[36mCurrent e\u0301",
            ansi.text,
        )
        self.assertIn("\x1b[37mCurrent: one\x1b[0m", ansi.text)
        long_pending_lines = tuple(
            line
            for line in ansi.text.splitlines()
            if "Pending: 界" in line or "b[31m\\x00 across rows" in line
        )
        self.assertEqual(len(long_pending_lines), 2)
        self.assertTrue(
            all("\x1b[33m" in line for line in long_pending_lines)
        )
        self.assertIn("\x1b[33mPending: three\x1b[0m", ansi.text)
        for line in plain.text.splitlines():
            self.assertLessEqual(_physical_width(line), 52)
        self.assertEqual(plain.line_count, len(plain.text.splitlines()))

        narrow = render_picker_viewport(
            project_picker_viewport(state),
            _capabilities(CapabilityProfile.PLAIN, 24),
        )
        fallback = render_picker_fallback(
            state,
            _capabilities(CapabilityProfile.LINE, 24),
        )
        for rendered in (narrow, fallback):
            rendered_compact = "".join(rendered.text.split())
            self.assertIn(
                r"Pending:界édanger\x1b[31m\x00acrossrows",
                rendered_compact,
            )
            self.assertNotIn("\x1b", rendered.text)
            self.assertNotIn("\x00", rendered.text)
            self.assertEqual(rendered.line_count, len(rendered.text.splitlines()))
            for line in rendered.text.splitlines():
                self.assertLessEqual(_physical_width(line), 24)

    def test_form_summary_is_inert_for_standard_static_and_palette_rendering(
        self,
    ) -> None:
        summarized = Choice(
            "one",
            "One",
            form_summary="Must stay hidden",
            form_summary_role=SemanticRole.ERROR,
            description="Visible detail",
        )
        ordinary = Choice("one", "One", description="Visible detail")
        summarized_picker = Picker(
            Prompt("Choose"),
            (summarized,),
            highlighted_id="one",
        )
        ordinary_picker = Picker(
            Prompt("Choose"),
            (ordinary,),
            highlighted_id="one",
        )
        for profile in (CapabilityProfile.PLAIN, CapabilityProfile.ANSI):
            with self.subTest(profile=profile):
                capabilities = _capabilities(profile)
                self.assertEqual(
                    render_picker_viewport(
                        project_picker_viewport(initialize_picker(summarized_picker)),
                        capabilities,
                    ),
                    render_picker_viewport(
                        project_picker_viewport(initialize_picker(ordinary_picker)),
                        capabilities,
                    ),
                )
        self.assertEqual(
            render_picker_fallback(
                initialize_picker(summarized_picker),
                _capabilities(CapabilityProfile.LINE),
            ),
            render_picker_fallback(
                initialize_picker(ordinary_picker),
                _capabilities(CapabilityProfile.LINE),
            ),
        )

        static_summarized = Picker(
            Prompt("Choose"),
            (summarized,),
            presentation=InputPresentation.FORM,
            highlighted_id="one",
        )
        static_ordinary = Picker(
            Prompt("Choose"),
            (ordinary,),
            presentation=InputPresentation.FORM,
            highlighted_id="one",
        )
        summarized_palette = _model.SuggestionPalette(
            "/",
            (summarized,),
            highlighted_id="one",
        )
        ordinary_palette = _model.SuggestionPalette(
            "/",
            (ordinary,),
            highlighted_id="one",
        )
        for left, right in (
            (static_summarized, static_ordinary),
            (summarized_palette, ordinary_palette),
        ):
            for profile in (CapabilityProfile.PLAIN, CapabilityProfile.ANSI):
                with self.subTest(component=type(left).__name__, profile=profile):
                    rendered = _render.render_component(
                        left,
                        _capabilities(profile),
                    )
                    self.assertEqual(
                        rendered,
                        _render.render_component(right, _capabilities(profile)),
                    )
                    self.assertNotIn("Must stay hidden", rendered.text)
                    self.assertIn("Visible detail", rendered.text)

    def test_form_transient_context_is_live_only_ordered_and_counted(self) -> None:
        context = _transient_context()
        picker = Picker(
            Prompt(
                "Apply changes?",
                instruction="Choose YES to apply changes, or NO to return.",
            ),
            (Choice("yes", "YES"), Choice("no", "NO")),
            presentation=InputPresentation.FORM,
            transient_context=context,
            highlighted_id="yes",
        )
        state = initialize_picker(picker)
        without_context = initialize_picker(
            Picker(
                picker.prompt,
                picker.choices,
                presentation=InputPresentation.FORM,
                highlighted_id="yes",
            )
        )

        for profile in (CapabilityProfile.PLAIN, CapabilityProfile.ANSI):
            with self.subTest(profile=profile):
                capabilities = _capabilities(profile)
                rendered = render_picker_viewport(
                    project_picker_viewport(state),
                    capabilities,
                )
                context_frame = _render.render_component(
                    context[0],
                    capabilities,
                )
                picker_frame = render_picker_viewport(
                    project_picker_viewport(without_context),
                    capabilities,
                )
                self.assertEqual(
                    rendered.text,
                    f"{context_frame.text}\n\n{picker_frame.text}",
                )
                self.assertEqual(
                    rendered.line_count,
                    context_frame.line_count + 1 + picker_frame.line_count,
                )
                self.assertEqual(rendered.line_count, len(rendered.text.splitlines()))
                self.assertLess(
                    rendered.text.index("Current"),
                    rendered.text.index("Apply changes?"),
                )
                self.assertLess(
                    rendered.text.index("Apply changes?"),
                    rendered.text.index("Choices 1/2"),
                )

        ansi = render_picker_viewport(
            project_picker_viewport(state),
            _capabilities(CapabilityProfile.ANSI),
        )
        plain = render_picker_viewport(
            project_picker_viewport(state),
            _capabilities(CapabilityProfile.PLAIN),
        )
        self.assertEqual(strip_decorative_sgr(ansi.text), plain.text)

        narrow = render_picker_viewport(
            project_picker_viewport(state),
            _capabilities(CapabilityProfile.PLAIN, 20),
        )
        self.assertEqual(narrow.line_count, len(narrow.text.splitlines()))
        self.assertLess(narrow.text.index("Current"), narrow.text.index("Apply changes?"))
        for line in narrow.text.splitlines():
            self.assertLessEqual(_physical_width(line), 20)

        fallback = render_picker_fallback(
            state,
            _capabilities(CapabilityProfile.LINE),
        )
        fallback_without_context = render_picker_fallback(
            without_context,
            _capabilities(CapabilityProfile.LINE),
        )
        self.assertEqual(fallback, fallback_without_context)
        static = _render.render_component(
            state.picker,
            _capabilities(CapabilityProfile.LINE),
        )
        static_without_context = _render.render_component(
            without_context.picker,
            _capabilities(CapabilityProfile.LINE),
        )
        self.assertEqual(static, static_without_context)
        for omitted in ("Current", "Pending", "(Changed)", "[activation]"):
            with self.subTest(omitted=omitted):
                self.assertNotIn(omitted, fallback.text)
                self.assertNotIn(omitted, static.text)

    def test_form_picker_window_uses_absolute_position_and_wraps_safely(self) -> None:
        state = initialize_picker(
            Picker(
                Prompt("Agent settings"),
                tuple(Choice(str(index), f"Choice {index}") for index in range(1, 11)),
                presentation=InputPresentation.FORM,
                visible_choice_limit=3,
                highlighted_id="8",
                selected_id="1",
            )
        )
        viewport = project_picker_viewport(state)
        self.assertEqual((viewport.start, viewport.stop), (5, 8))
        wide = render_picker_viewport(
            viewport,
            _capabilities(CapabilityProfile.PLAIN),
        )
        self.assertEqual(
            wide.text,
            "Agent settings\n\n"
            "Choices 8/10\n"
            "  6. Choice 6\n"
            "  7. Choice 7\n"
            "> 8. Choice 8\n"
            "  Selected: 1. Choice 1",
        )
        narrow_plain = render_picker_viewport(
            viewport,
            _capabilities(CapabilityProfile.PLAIN, 12),
        )
        narrow_ansi = render_picker_viewport(
            viewport,
            _capabilities(CapabilityProfile.ANSI, 12),
        )
        self.assertEqual(strip_decorative_sgr(narrow_ansi.text), narrow_plain.text)
        self.assertIn("Choices 8/10", narrow_plain.text)
        self.assertNotIn("showing", narrow_plain.text)
        for line in narrow_plain.text.splitlines():
            self.assertLessEqual(_physical_width(line), 12)

    def test_form_input_prompt_separates_regions_and_preserves_initial_value(
        self,
    ) -> None:
        view = InputPromptView(
            (
                Notice(
                    "Use 1-99.",
                    label="validation",
                    role=SemanticRole.ERROR,
                    state=ViewState.ERROR,
                ),
            ),
            Prompt(
                "Maximum debate rounds",
                scope="Settings",
                instruction="Enter a whole number.",
                default="1",
            ),
            presentation=InputPresentation.FORM,
            initial_value="12",
        )
        expected = (
            "Maximum debate rounds\n"
            "Context: Settings\n"
            "\n"
            "Enter a whole number.\n"
            "Default: 1\n"
            "\n"
            "[validation] Use 1-99.\n"
            "\n"
            "Current value: 12\n"
            "\n"
            "New value > "
        )
        plain = render_input_prompt(
            view,
            _capabilities(CapabilityProfile.PLAIN),
        )
        ansi = render_input_prompt(
            view,
            _capabilities(CapabilityProfile.ANSI),
        )
        line = render_input_prompt(
            view,
            _capabilities(CapabilityProfile.LINE),
        )
        self.assertEqual(plain.text, expected)
        self.assertEqual(line.text, expected)
        self.assertEqual(strip_decorative_sgr(ansi.text), expected)
        self.assertTrue(plain.text.endswith("New value > "))
        self.assertNotIn("[focused]", plain.text)

        narrow = render_input_prompt(
            view,
            _capabilities(CapabilityProfile.PLAIN, 16),
        )
        self.assertIn("Current value:", narrow.text.splitlines())
        self.assertIn("12", narrow.text.splitlines())
        for rendered_line in narrow.text.splitlines():
            self.assertLessEqual(_physical_width(rendered_line), 16)

    def test_narrow_no_color_combined_cues_and_width_change_preserve_facts(self) -> None:
        state = _long_state()
        viewport = project_picker_viewport(state)
        narrow = render_picker_viewport(
            viewport,
            _capabilities(CapabilityProfile.PLAIN, 20),
        )
        self.assertEqual(
            narrow.text,
            "Choose route [focuse\n"
            "d] [open] >\n"
            "  Use arrows.\n"
            "Choices (highlighted\n"
            " 8/10; showing 2-8/1\n"
            "0) [open]\n"
            " 2. [disabled] Beta \n"
            "               (disa\n"
            "               bled:\n"
            "                Unav\n"
            "               ailab\n"
            "               le)\n"
            " 3. [available] Gamm\n"
            "                a\n"
            " 4. [available] Delt\n"
            "                a\n"
            " 5. [available] Epsi\n"
            "                lon\n"
            " 6. [available] Zeta\n"
            " 7. [available] Eta\n"
            " 8. [highlighted] Th\n"
            "                  et\n"
            "                  a\n"
            " Selected outside vi\n"
            "ew: 10. [selected] K\n"
            "   appa",
        )
        self.assertEqual(narrow.line_count, 26)
        for line in narrow.text.splitlines():
            self.assertLessEqual(_physical_width(line), 20)
        no_color = render_picker_viewport(
            viewport,
            _capabilities(CapabilityProfile.PLAIN, 80),
        )
        self.assertNotIn("\x1b", no_color.text)
        self.assertEqual(state.picker.highlighted_id, "secret-8")
        self.assertEqual(state.picker.selected_id, "secret-10")

        combined_state = initialize_picker(
            Picker(
                Prompt("Pick"),
                (Choice("hidden", "界e\u0301"),),
                highlighted_id="hidden",
                selected_id="hidden",
            )
        )
        combined = render_picker_viewport(
            project_picker_viewport(combined_state),
            _capabilities(CapabilityProfile.PLAIN, 20),
        )
        self.assertEqual(
            combined.text,
            "Pick [focused] [open\n"
            "] >\n"
            "Choices (highlighted\n"
            " 1/1) [open]\n"
            " 1. [selected] [high\n"
            "lighted] 界e\u0301",
        )
        self.assertNotIn("hidden", combined.text)
        for line in combined.text.splitlines():
            self.assertLessEqual(_physical_width(line), 20)

    def test_warning_suffix_numbered_profiles_wrap_and_preserve_state_precedence(
        self,
    ) -> None:
        choices = (
            Choice("save", "Save", warning_suffix="(*Required)"),
            Choice(
                "disabled",
                "Disabled",
                warning_suffix="(*Required)",
                enabled=False,
                disabled_reason="Unavailable",
            ),
            Choice("selected", "Selected", warning_suffix="(*Required)"),
            Choice("highlighted", "Highlighted", warning_suffix="(*Required)"),
        )
        state = initialize_picker(
            Picker(
                Prompt("Choose"),
                choices,
                highlighted_id="highlighted",
                selected_id="selected",
            )
        )
        viewport = project_picker_viewport(state)
        expected_plain = (
            "Choose [focused] [open] >\n"
            "Choices (highlighted 4/4) [open]\n"
            "  1. [available] Save (*Required)\n"
            "  2. [disabled] Disabled (*Required) (disabled: Unavailable)\n"
            "  3. [selected] Selected (*Required)\n"
            "  4. [highlighted] Highlighted (*Required)"
        )
        plain = render_picker_viewport(
            viewport,
            _capabilities(CapabilityProfile.PLAIN),
        )
        ansi = render_picker_viewport(
            viewport,
            _capabilities(CapabilityProfile.ANSI),
        )
        self.assertEqual(plain.text, expected_plain)
        self.assertNotIn("\x1b", plain.text)
        self.assertEqual(strip_decorative_sgr(ansi.text), expected_plain)
        self.assertIn(
            "  1. [available] Save \x1b[33m(*Required)\x1b[0m",
            ansi.text,
        )
        self.assertEqual(ansi.text.count("\x1b[33m"), 1)
        self.assertIn(
            "\x1b[37m  2. [disabled] Disabled (*Required) "
            "(disabled: Unavailable)\x1b[0m",
            ansi.text,
        )
        self.assertIn(
            "\x1b[1m\x1b[7m  3. [selected] Selected (*Required)\x1b[0m",
            ansi.text,
        )
        self.assertIn(
            "\x1b[1m\x1b[7m  4. [highlighted] Highlighted (*Required)\x1b[0m",
            ansi.text,
        )

        fallback = render_picker_fallback(
            state,
            _capabilities(CapabilityProfile.LINE),
        )
        self.assertEqual(
            fallback.text,
            "Choices (highlighted 4/4) [open]\n"
            "  1. [available] Save (*Required)\n"
            "  2. [disabled] Disabled (*Required) (disabled: Unavailable)\n"
            "  3. [selected] Selected (*Required)\n"
            "  4. [highlighted] Highlighted (*Required)\n"
            "[input] Enter a choice number, press Enter for the highlighted choice, or enter \n"
            "        /cancel.\n"
            "Choose > ",
        )
        self.assertEqual(fallback.plain_text, fallback.text)

        narrow_plain = render_picker_viewport(
            viewport,
            _capabilities(CapabilityProfile.PLAIN, 24),
        )
        narrow_ansi = render_picker_viewport(
            viewport,
            _capabilities(CapabilityProfile.ANSI, 24),
        )
        self.assertIn(" 1. [available] Save (*R", narrow_plain.text.splitlines())
        self.assertIn("                equired)", narrow_plain.text.splitlines())
        self.assertEqual(strip_decorative_sgr(narrow_ansi.text), narrow_plain.text)
        self.assertEqual(narrow_ansi.text.count("\x1b[33m"), 2)
        for line in narrow_plain.text.splitlines():
            self.assertLessEqual(_physical_width(line), 24)

        combined = initialize_picker(
            Picker(
                Prompt("Choose"),
                (Choice("save", "Save", warning_suffix="(*Required)"),),
                highlighted_id="save",
                selected_id="save",
            )
        )
        combined_ansi = render_picker_viewport(
            project_picker_viewport(combined),
            _capabilities(CapabilityProfile.ANSI),
        )
        self.assertIn(
            "\x1b[1m\x1b[7m  1. [selected] [highlighted] Save (*Required)\x1b[0m",
            combined_ansi.text,
        )
        self.assertNotIn("\x1b[33m", combined_ansi.text)

    def test_warning_suffix_sanitizes_controls_and_excludes_description_style(
        self,
    ) -> None:
        choices = (
            Choice(
                "save",
                "Save",
                warning_suffix="(*Required)",
                description="Writes settings.",
            ),
            Choice(
                "hostile",
                "Hostile",
                warning_suffix="(*Required)\x1b[31m\x00",
            ),
            Choice("continue", "Continue"),
        )
        state = initialize_picker(
            Picker(
                Prompt("Choose"),
                choices,
                highlighted_id="continue",
            )
        )
        viewport = project_picker_viewport(state)
        plain = render_picker_viewport(
            viewport,
            _capabilities(CapabilityProfile.PLAIN),
        )
        ansi = render_picker_viewport(
            viewport,
            _capabilities(CapabilityProfile.ANSI),
        )

        description_line = (
            "  1. [available] Save (*Required) \u2014 Writes settings."
        )
        hostile_line = (
            "  2. [available] Hostile (*Required)\\x1b[31m\\x00"
        )
        self.assertIn(description_line, plain.text.splitlines())
        self.assertIn(hostile_line, plain.text.splitlines())
        self.assertNotIn("\x1b", plain.text)
        self.assertNotIn("\x00", plain.text)
        self.assertEqual(strip_decorative_sgr(ansi.text), plain.text)
        self.assertIn(
            "  1. [available] Save \x1b[33m(*Required)\x1b[0m "
            "\u2014 Writes settings.",
            ansi.text.splitlines(),
        )
        self.assertIn(
            "  2. [available] Hostile "
            "\x1b[33m(*Required)\\x1b[31m\\x00\x1b[0m",
            ansi.text.splitlines(),
        )
        self.assertEqual(ansi.text.count("\x1b[33m"), 2)
        self.assertNotIn("\x1b[31m", ansi.text)
        self.assertNotIn("\x00", ansi.text)

    def test_description_roles_span_profiles_components_and_state_precedence(
        self,
    ) -> None:
        styled = Choice(
            "styled",
            "Role",
            warning_suffix="(*Required-long*)",
            description="界e\u0301 danger\x1b[31m\x00 across rows",
            description_role=SemanticRole.ERROR,
        )
        focus = Choice("focus", "Focus")
        picker = Picker(
            Prompt("Choose"),
            (styled, focus),
            highlighted_id="focus",
        )
        palette = _model.SuggestionPalette(
            "/",
            (styled, focus),
            highlighted_id="focus",
        )
        for component in (picker, palette):
            with self.subTest(component=type(component).__name__):
                plain = _render.render_component(
                    component,
                    _capabilities(CapabilityProfile.PLAIN, 20),
                )
                ansi = _render.render_component(
                    component,
                    _capabilities(CapabilityProfile.ANSI, 20),
                )
                line = _render.render_component(
                    component,
                    _capabilities(CapabilityProfile.LINE, 20),
                )
                self.assertEqual(strip_decorative_sgr(ansi.text), plain.text)
                self.assertEqual(line.text, plain.text)
                self.assertNotIn("\x1b", plain.text)
                self.assertNotIn("\x1b", line.text)
                self.assertIn(r"\x1b[31m\x00", plain.text.replace("\n", ""))
                self.assertGreaterEqual(ansi.text.count("\x1b[33m"), 2)
                self.assertGreaterEqual(ansi.text.count("\x1b[31m"), 2)
                for rendered_line in plain.text.splitlines():
                    self.assertLessEqual(_physical_width(rendered_line), 20)

        state = initialize_picker(picker)
        fallback = render_picker_fallback(
            state,
            _capabilities(CapabilityProfile.LINE, 24),
        )
        self.assertEqual(fallback.text, fallback.plain_text)
        self.assertNotIn("\x1b", fallback.text)
        self.assertIn(r"\x1b[", fallback.text)
        self.assertIn(r"31m\x00", fallback.text)
        for rendered_line in fallback.text.splitlines():
            self.assertLessEqual(_physical_width(rendered_line), 24)

        available = _render.render_component(
            Picker(
                Prompt("Pick"),
                (
                    Choice(
                        "option",
                        "Option",
                        warning_suffix="(*Required)",
                        description="Detail",
                        description_role=SemanticRole.ERROR,
                    ),
                    focus,
                ),
                highlighted_id="focus",
            ),
            _capabilities(CapabilityProfile.ANSI),
        )
        self.assertIn(
            "  - Option \x1b[33m(*Required)\x1b[0m — "
            "\x1b[31mDetail\x1b[0m",
            available.text.splitlines(),
        )

        precedence = (
            (
                "disabled",
                Picker(
                    Prompt("Pick"),
                    (
                        Choice(
                            "option",
                            "Option",
                            description="Detail",
                            description_role=SemanticRole.ERROR,
                            enabled=False,
                            disabled_reason="Unavailable",
                        ),
                    ),
                ),
                "\x1b[37m",
            ),
            (
                "selected",
                Picker(
                    Prompt("Pick"),
                    (
                        Choice(
                            "option",
                            "Option",
                            description="Detail",
                            description_role=SemanticRole.ERROR,
                        ),
                    ),
                    selected_id="option",
                ),
                "\x1b[1m\x1b[7m",
            ),
            (
                "highlighted",
                Picker(
                    Prompt("Pick"),
                    (
                        Choice(
                            "option",
                            "Option",
                            description="Detail",
                            description_role=SemanticRole.ERROR,
                        ),
                    ),
                    highlighted_id="option",
                ),
                "\x1b[1m\x1b[7m",
            ),
            (
                "selected-highlighted",
                Picker(
                    Prompt("Pick"),
                    (
                        Choice(
                            "option",
                            "Option",
                            description="Detail",
                            description_role=SemanticRole.ERROR,
                        ),
                    ),
                    highlighted_id="option",
                    selected_id="option",
                ),
                "\x1b[1m\x1b[7m",
            ),
        )
        for label, component, expected_sgr in precedence:
            with self.subTest(precedence=label):
                plain = _render.render_component(
                    component,
                    _capabilities(CapabilityProfile.PLAIN),
                )
                ansi = _render.render_component(
                    component,
                    _capabilities(CapabilityProfile.ANSI),
                )
                choice_line = next(
                    line for line in plain.text.splitlines() if "Option" in line
                )
                styled_line = next(
                    line for line in ansi.text.splitlines() if "Option" in line
                )
                self.assertEqual(
                    styled_line,
                    f"{expected_sgr}{choice_line}\x1b[0m",
                )
                self.assertNotIn("\x1b[31m", styled_line)

    def test_warning_suffix_component_palette_and_selected_outside_projection(
        self,
    ) -> None:
        save = Choice(
            "save",
            "Save",
            warning_suffix="(*Required)",
            description="Discard pending values",
            description_role=SemanticRole.ERROR,
        )
        continue_choice = Choice("continue", "Continue")
        components = (
            Picker(
                Prompt("Choose"),
                (save, continue_choice),
                highlighted_id="continue",
            ),
            _model.SuggestionPalette(
                "/",
                (save, continue_choice),
                highlighted_id="continue",
            ),
        )
        for component in components:
            with self.subTest(component=type(component).__name__):
                plain = _render.render_component(
                    component,
                    _capabilities(CapabilityProfile.PLAIN),
                )
                ansi = _render.render_component(
                    component,
                    _capabilities(CapabilityProfile.ANSI),
                )
                self.assertIn(
                    "  - Save (*Required) — Discard pending values",
                    plain.text.splitlines(),
                )
                self.assertEqual(strip_decorative_sgr(ansi.text), plain.text)
                self.assertIn(
                    "  - Save \x1b[33m(*Required)\x1b[0m — "
                    "\x1b[31mDiscard pending values\x1b[0m",
                    ansi.text.splitlines(),
                )

        outside_choices = (save,) + tuple(
            Choice(str(index), f"Choice {index}") for index in range(2, 9)
        )
        outside = initialize_picker(
            Picker(
                Prompt("Choose"),
                outside_choices,
                highlighted_id="8",
                selected_id="save",
            )
        )
        outside_ansi = render_picker_viewport(
            project_picker_viewport(outside),
            _capabilities(CapabilityProfile.ANSI),
        )
        self.assertIn(
            "\x1b[32m  Selected outside view: 1. [selected] "
            "Save (*Required)\x1b[0m",
            outside_ansi.text,
        )
        self.assertNotIn("Discard pending values", outside_ansi.text)
        self.assertNotIn("\x1b[33m", outside_ansi.text)
        self.assertNotIn("\x1b[31m", outside_ansi.text)

    def test_hostile_empty_disabled_error_and_terminal_states_remain_visible(self) -> None:
        hostile = Choice(
            "opaque-id",
            "bad\x1b]0;owned\x07\u202e界e\u0301",
            description="desc\x00",
            enabled=False,
            disabled_reason="why\x1b[31m",
        )
        disabled = initialize_picker(Picker(Prompt("Unsafe\x1b"), (hostile,)))
        frame = render_picker_viewport(
            project_picker_viewport(disabled),
            _capabilities(CapabilityProfile.ANSI, 40),
        )
        plain = strip_decorative_sgr(frame.text)
        self.assertIn(r"Unsafe\x1b", plain)
        self.assertIn(r"bad\x1b]0;owned\x07\u202", plain)
        self.assertIn("e界e\u0301", plain)
        self.assertIn(r"desc\x00", plain)
        self.assertIn(r"why\x1b[31m", plain)
        self.assertNotIn("opaque-id", plain)
        self.assertNotIn("\x1b]", frame.text)
        self.assertNotIn("\x07", frame.text)

        empty = initialize_picker(Picker(Prompt("Choose"), ()))
        self.assertEqual(
            render_picker_viewport(
                project_picker_viewport(empty),
                _capabilities(CapabilityProfile.PLAIN),
            ).text,
            "Choose [focused] [empty] >\n"
            "Choices (0 items) [empty]\n"
            "  [empty] No choices available.",
        )
        error = initialize_picker(
            Picker(
                Prompt("Choose"),
                (Choice("one", "One"),),
                notice=Notice(
                    "Trusted error.",
                    label="error",
                    role=SemanticRole.ERROR,
                    state=ViewState.ERROR,
                ),
                state=ViewState.ERROR,
            )
        )
        self.assertIn(
            "[error] Trusted error.",
            render_picker_viewport(
                project_picker_viewport(error),
                _capabilities(CapabilityProfile.PLAIN),
            ).text,
        )
        completed, _ = transition_picker(
            initialize_picker(Picker(Prompt("Choose"), (Choice("one", "One"),))),
            KeyEvent(KeyKind.ENTER),
        )
        cancelled, _ = transition_picker(
            initialize_picker(Picker(Prompt("Choose"), (Choice("one", "One"),))),
            KeyEvent(KeyKind.ESCAPE),
        )
        self.assertNotIn(
            "[completed]",
            render_picker_viewport(
                project_picker_viewport(completed),
                _capabilities(CapabilityProfile.PLAIN),
            ).text,
        )
        self.assertIn(
            "[cancelled]",
            render_picker_viewport(
                project_picker_viewport(cancelled),
                _capabilities(CapabilityProfile.PLAIN),
            ).text,
        )


class PickerDriverTests(unittest.TestCase):
    """Verify the dedicated picker lifecycle, fallback, cleanup, and isolation."""

    def test_live_navigation_commit_final_frame_and_restoration_are_exact(self) -> None:
        state = initialize_picker(Picker(Prompt("Choose"), _choices()[:3]))
        driver, _input, output, fallback, termios_double, size_calls = _driver(
            "\x1b[B\n",
            widths=(40,),
        )
        driver._history_state = MainInputHistoryState(("/status",))
        with mock.patch.object(
            driver_module._select,
            "select",
            return_value=((7,), (), ()),
        ) as readiness:
            outcome = _read_picker(driver, state)

        self.assertEqual(
            outcome,
            ControlOutcome(ControlOutcomeKind.COMMIT, text="secret-3"),
        )
        readiness.assert_called_once_with((7,), (), (), 0.05)
        fallback.assert_not_called()
        self.assertEqual(size_calls, [8, 8, 8, 8, 8])
        self.assertEqual(
            [when for _fd, when, _attributes in termios_double.set_calls],
            [termios_double.TCSANOW, termios_double.TCSADRAIN],
        )
        self.assertEqual(driver._history_state, MainInputHistoryState(("/status",)))
        self.assertEqual(len(output.records), 4)
        self.assertEqual(output.flush_calls, 4)
        rendered = "".join(output.records)
        self.assertIn("[open]", rendered)
        self.assertNotIn("[completed]", rendered)
        self.assertIn("[selected] [highlighted] Gamma", rendered)
        self.assertTrue(rendered.endswith("\n"))
        self.assertNotIn("secret-", rendered)
        for forbidden in ("\x1b[?1049", "\x1b[2J", "\x1b[3J", "\x1b]", "\x1b[M"):
            self.assertNotIn(forbidden, rendered)

        from tests._external_view_support import _raw_driver, PortableTerminalScreen
        for case in ("plain", "reviewing", "choices", "blocked", "blocked_form"):
            reviewing, shrinking, blocked = case == "reviewing", case == "choices", case.startswith("blocked")
            navigation = "\x1b[17~\x1b[B\x1b" if reviewing else ""
            decision = "\r\x1b" if blocked else "\x1b[B\r"
            height = 18 if shrinking else 24
            managed, incoming, output, fallback, termios = _raw_driver("seed\r" + navigation + decision, width=120, height=height)
            if reviewing:
                managed._os_provider().environ["NO_COLOR"] = "1"
            screen = PortableTerminalScreen(width=120, height=height)
            screen.primary[:] = [[" "] * 120 for _ in range(height)]
            screen.cursor = (0, 0); screen.history.clear()
            original_write = output.write
            def paint(value):
                result = original_write(value)
                screen.write(value.replace("\n", "\r\n"))
                if "\x1b[6n" in value:
                    row, column = screen.cursor
                    incoming.characters[:0] = list(f"\x1b[{row + 1};{column + 1}R".encode())
                return result
            output.write = paint
            self.assertEqual(managed.read_main_line("main> ", lambda _: ()), "seed")
            if reviewing:
                managed.write_component(_model.Message("User", "OLDER_INPUT"))
                managed.write_component(_model.Message("Lead", "\n".join(f"OLDER_ROW_{row}" for row in range(35))))
                timeline = managed._output_history
                timeline.focus_visible(timeline.render(managed.semantic_capabilities(), 23))
                timeline.handle_key(KeyEvent(KeyKind.HOME)); timeline.handle_key(KeyEvent(KeyKind.ENTER))
                managed._paint_timeline()
            user = _model.Message("User", "USER_VISIBLE", presentation=_model.MessagePresentation.INLINE if shrinking else _model.MessagePresentation.CHAT_BLOCK)
            proposal = Panel("Proposal", (Field("Target", "TARGET_VISIBLE"), Field("Reviewers", "REVIEWERS_VISIBLE"), Field("Authority", "AUTHORITY_VISIBLE")))
            with managed.submission("session", "conversation", user):
                managed.write_component(proposal)
                if not shrinking:
                    view = _model.NestedScrollViewport("review:one", "JSON review", 3, 1, ("JSON_ONE",), 1, False, buffer_lines=("JSON_ONE", "JSON_TWO", "JSON_THREE"))
                    managed._output_history.retain(_model.MultiAgentScrollState((view,), 0)); managed._paint_timeline()
                before = tuple(map(tuple, screen.primary))
                choices = tuple(Choice(str(index), f"CHOICE_{index:02}") for index in range(20)) if shrinking else (Choice("confirm", "Confirm"), Choice("reject", "Reject"), Choice("revise", "Revise"))
                pending = initialize_picker(Picker(Prompt("CONFIRM_REVIEW", instruction="Explain immutable review authority. " * 180 if blocked else None), choices, presentation=InputPresentation.FORM if case == "blocked_form" else InputPresentation.STANDARD, visible_choice_limit=9 if shrinking else 7, transient_context=()))
                self.assertEqual(pending.picker.transient_context, ())
                original_read = RawTerminalDriver._read_character
                decisions = []
                def checked_read(owner, **kwargs):
                    visible = "\n".join("".join(row) for row in screen.primary)
                    self.assertEqual(visible.count("USER_VISIBLE"), 1, visible)
                    for marker in ("TARGET_VISIBLE", "REVIEWERS_VISIBLE", "AUTHORITY_VISIBLE"):
                        self.assertIn(marker, visible)
                    if blocked:
                        self.assertIn("resize", visible.lower()); self.assertIn("esc", visible.lower())
                    else:
                        self.assertIn("CONFIRM_REVIEW", visible)
                    if shrinking:
                        self.assertGreater(sum(choice.label in visible for choice in choices), 0)
                        self.assertLess(sum(choice.label in visible for choice in choices), 9)
                    self.assertFalse(any("USER_VISIBLE" in row for row in screen.history))
                    character = original_read(owner, **kwargs)
                    if character == "\r":
                        decisions.append(visible)
                        if reviewing:
                            transcript = "".join(output.records)
                            self.assertGreater(transcript.rfind(driver_module._MOUSE_DISABLE), transcript.rfind(driver_module._MOUSE_ENABLE))
                    return character
                with (mock.patch.object(RawTerminalDriver, "_read_character", autospec=True, side_effect=checked_read),
                      mock.patch.object(driver_module._select, "select", side_effect=lambda read, write, error, _timeout: (read if incoming.characters else (), write, error))):
                    outcome = _read_picker(managed, pending, transient=True)
                expected = ControlOutcome(ControlOutcomeKind.CANCEL) if blocked else ControlOutcome(ControlOutcomeKind.COMMIT, text="1" if shrinking else "reject")
                self.assertEqual(outcome, expected)
                self.assertEqual(len(decisions), 1)
                self.assertEqual(tuple(map(tuple, screen.primary)), before)
                if shrinking:
                    self.assertEqual(pending.picker.visible_choice_limit, 9)
                    self.assertEqual(managed._output_history.frames, ())
                else:
                    self.assertEqual(managed._output_history.frames[0].view.start_line, 2 if reviewing else 1)
                self.assertEqual(sum(entry.component == user for entry in managed._output_history.entries), 1)
                self.assertEqual(managed._history_state, MainInputHistoryState(("seed",)))
                self.assertEqual(list(managed._typeahead_buffer), [])
                fallback.assert_not_called()
                self.assertEqual(termios.events.count("activate"), termios.events.count("restore"))
                self.assertEqual("".join(output.records).count(driver_module._MOUSE_ENABLE), int(reviewing))

    def test_control_terminated_and_parameterized_sequences_are_discarded(self) -> None:
        state = initialize_picker(
            Picker(
                Prompt("Choose"),
                (Choice("one", "One"), Choice("two", "Two")),
            )
        )

        def run(characters: str) -> tuple[ControlOutcome, list[KeyEvent]]:
            driver, _input, _output, fallback, _termios, _sizes = _driver(
                characters
            )
            seen: list[KeyEvent] = []

            def observe(
                current: PickerControlState,
                event: KeyEvent,
            ) -> tuple[PickerControlState, ControlOutcome | None]:
                seen.append(event)
                return transition_picker(current, event)

            with mock.patch.object(
                driver_module._select,
                "select",
                side_effect=lambda r, w, x, _t: (r, w, x),
            ):
                outcome = driver.read_picker(
                    state,
                    observe,
                    transition_picker_fallback,
                    project_picker_viewport,
                    render_picker_viewport,
                    render_picker_fallback,
                )
            fallback.assert_not_called()
            return outcome, seen

        malformed, malformed_events = run("\x1bO1A\n")
        self.assertEqual(
            malformed,
            ControlOutcome(ControlOutcomeKind.COMMIT, text="one"),
        )
        self.assertEqual(malformed_events, [KeyEvent(KeyKind.ENTER)])
        self.assertNotIn(KeyEvent(KeyKind.TEXT, text="A"), malformed_events)

        for introducer, down in (
            ("\x1b[", "\x1b[B"),
            ("\x1bO", "\x1bOB"),
        ):
            with self.subTest(introducer=repr(introducer)):
                outcome, events = run(introducer + "\n" + down + "\n")
                self.assertEqual(
                    outcome,
                    ControlOutcome(ControlOutcomeKind.COMMIT, text="two"),
                )
                self.assertEqual(
                    events,
                    [KeyEvent(KeyKind.DOWN), KeyEvent(KeyKind.ENTER)],
                )

        valid, valid_events = run("\x1bOB\x1bOA\n")
        self.assertEqual(
            valid,
            ControlOutcome(ControlOutcomeKind.COMMIT, text="one"),
        )
        self.assertEqual(
            valid_events,
            [
                KeyEvent(KeyKind.DOWN),
                KeyEvent(KeyKind.UP),
                KeyEvent(KeyKind.ENTER),
            ],
        )

    def test_overlong_down_suffix_is_discarded_before_real_down(self) -> None:
        state = initialize_picker(
            Picker(
                Prompt("Choose"),
                (
                    Choice("one", "One"),
                    Choice("two", "Two"),
                    Choice("three", "Three"),
                ),
            )
        )
        for introducer in ("\x1b[", "\x1bO"):
            with self.subTest(introducer=repr(introducer)):
                characters = introducer + "1" * 62 + "B\x1b[B\n"
                driver, input_stream, _output, fallback, _termios, _sizes = (
                    _driver(characters)
                )
                seen: list[KeyEvent] = []

                def observe(
                    current: PickerControlState,
                    event: KeyEvent,
                ) -> tuple[PickerControlState, ControlOutcome | None]:
                    seen.append(event)
                    return transition_picker(current, event)

                with mock.patch.object(
                    driver_module._select,
                    "select",
                    side_effect=lambda r, w, x, _t: (r, w, x),
                ) as readiness:
                    outcome = driver.read_picker(
                        state,
                        observe,
                        transition_picker_fallback,
                        project_picker_viewport,
                        render_picker_viewport,
                        render_picker_fallback,
                    )
                self.assertEqual(
                    outcome,
                    ControlOutcome(ControlOutcomeKind.COMMIT, text="two"),
                )
                self.assertEqual(
                    seen,
                    [KeyEvent(KeyKind.DOWN), KeyEvent(KeyKind.ENTER)],
                )
                self.assertEqual(readiness.call_count, 2)
                self.assertEqual(input_stream.characters, [])
                fallback.assert_not_called()

    def test_noop_keys_do_not_redraw_and_each_real_redraw_observes_width(self) -> None:
        state = initialize_picker(
            Picker(
                Prompt("Choose"),
                (Choice("one", "One"), Choice("two", "Two")),
            )
        )
        driver, _input, output, fallback, termios_double, size_calls = _driver(
            "x\x1b[B\x1b[A\n",
            widths=(80, 80, 40, 20, 120, 120),
        )
        renderer = mock.Mock(wraps=render_picker_viewport)
        projector = mock.Mock(wraps=project_picker_viewport)
        with mock.patch.object(
            driver_module._select,
            "select",
            side_effect=(((7,), (), ()), ((7,), (), ())),
        ) as readiness:
            outcome = driver.read_picker(
                state,
                transition_picker,
                transition_picker_fallback,
                projector,
                renderer,
                render_picker_fallback,
            )
        self.assertEqual(outcome.text, "one")
        self.assertEqual(readiness.call_count, 2)
        self.assertEqual(size_calls, [8, 8, 8, 8, 8, 8])
        self.assertEqual(
            [call.args[1].width for call in renderer.call_args_list],
            [39, 19, 119, 119],
        )
        self.assertEqual(projector.call_count, 4)
        self.assertEqual(len(output.records), 5)
        self.assertEqual(output.flush_calls, 5)
        fallback.assert_not_called()
        self.assertEqual(
            [when for _fd, when, _attrs in termios_double.set_calls],
            [termios_double.TCSANOW, termios_double.TCSADRAIN],
        )

    def test_form_summary_resize_cleanup_uses_latest_stacked_frame_count(
        self,
    ) -> None:
        state = initialize_picker(
            Picker(
                Prompt("Settings"),
                (
                    Choice(
                        "short",
                        "A",
                        form_summary="Current: 12345678901234567890",
                        form_summary_role=SemanticRole.MUTED,
                    ),
                    Choice(
                        "long",
                        "Longest label",
                        form_summary="Pending: two",
                        form_summary_role=SemanticRole.WARNING,
                    ),
                ),
                presentation=InputPresentation.FORM,
                highlighted_id="short",
            )
        )
        driver, _input, output, fallback, termios_double, _sizes = _driver(
            "\x1b[B\n",
            widths=(80, 80, 42, 41, 41, 41),
            no_color=True,
        )
        renderer = mock.Mock(wraps=render_picker_viewport)
        with mock.patch.object(
            driver_module._select,
            "select",
            return_value=((7,), (), ()),
        ):
            outcome = driver.read_picker(
                state,
                transition_picker,
                transition_picker_fallback,
                project_picker_viewport,
                renderer,
                render_picker_fallback,
                transient=True,
            )

        self.assertEqual(
            outcome,
            ControlOutcome(ControlOutcomeKind.COMMIT, text="long"),
        )
        rendered_widths = tuple(
            call.args[1].width for call in renderer.call_args_list
        )
        self.assertIn(41, rendered_widths)
        self.assertIn(40, rendered_widths)
        rendered = strip_decorative_sgr("".join(output.records))
        self.assertIn(" — Current:", rendered)
        self.assertIn("\n     Current:", rendered)
        latest_frame = render_picker_viewport(*renderer.call_args_list[-1].args)
        self.assertEqual(
            output.records[-1],
            "\r"
            + "\x1b[A" * (latest_frame.line_count - 1)
            + "\r\x1b[J",
        )
        self.assertEqual(
            latest_frame.line_count,
            len(latest_frame.text.splitlines()),
        )
        fallback.assert_not_called()
        self.assertEqual(
            [when for _fd, when, _attrs in termios_double.set_calls],
            [termios_double.TCSANOW, termios_double.TCSADRAIN],
        )

    def test_escape_interrupt_veof_and_physical_eof_have_exact_final_frames(self) -> None:
        state = initialize_picker(
            Picker(
                Prompt("Choose"),
                (Choice("one", "One"),),
                selected_id="one",
            )
        )
        cases = (
            ("\x1b", (((), (), ()),), ControlOutcomeKind.CANCEL, "[cancelled]"),
            ("\x1b[", (((7,), (), ()),), ControlOutcomeKind.EOF, None),
            ("\x1bO", (((7,), (), ()),), ControlOutcomeKind.EOF, None),
            ("\x03", (), ControlOutcomeKind.INTERRUPT, None),
            ("\x04", (), ControlOutcomeKind.EOF, None),
            ("", (), ControlOutcomeKind.EOF, None),
        )
        for characters, readiness_results, expected, final_marker in cases:
            with self.subTest(characters=repr(characters)):
                driver, _input, output, fallback, termios_double, _sizes = _driver(
                    characters,
                    widths=(40,),
                )
                readiness_patch = mock.patch.object(
                    driver_module._select,
                    "select",
                    side_effect=readiness_results,
                )
                with readiness_patch:
                    outcome = _read_picker(driver, state)
                self.assertEqual(outcome.kind, expected)
                fallback.assert_not_called()
                self.assertEqual(
                    [when for _fd, when, _attrs in termios_double.set_calls],
                    [termios_double.TCSANOW, termios_double.TCSADRAIN],
                )
                rendered = "".join(output.records)
                if final_marker is None:
                    self.assertNotIn("[completed]", rendered)
                    self.assertNotIn("[cancelled]", rendered)
                    self.assertEqual(
                        output.records[-1],
                        "\r\x1b[A\x1b[A\r\x1b[J",
                    )
                else:
                    self.assertIn(final_marker, rendered)
                    self.assertIn("[selected] [highlighted] One", rendered)
                    self.assertTrue(rendered.endswith("\n"))

    def test_transient_form_context_cleanup_uses_latest_frame_for_every_exit(
        self,
    ) -> None:
        state = initialize_picker(
            Picker(
                Prompt("Apply changes?"),
                (Choice("yes", "YES"), Choice("no", "NO")),
                presentation=InputPresentation.FORM,
                transient_context=_transient_context(),
                highlighted_id="yes",
            )
        )
        cases = (
            ("yes", "\n", ControlOutcomeKind.COMMIT, "yes", False, 80),
            ("no", "\x1b[B\n", ControlOutcomeKind.COMMIT, "no", True, 80),
            ("escape", "\x1b", ControlOutcomeKind.CANCEL, None, False, 24),
            ("interrupt", "\x03", ControlOutcomeKind.INTERRUPT, None, False, 80),
            ("veof", "\x04", ControlOutcomeKind.EOF, None, True, 80),
            ("physical-eof", "", ControlOutcomeKind.EOF, None, False, 24),
        )
        for label, characters, kind, text, no_color, width in cases:
            with self.subTest(label=label):
                driver, _input, output, fallback, termios_double, _sizes = _driver(
                    characters,
                    widths=(width,),
                    no_color=no_color,
                )
                renderer = mock.Mock(wraps=render_picker_viewport)
                with mock.patch.object(
                    driver_module._select,
                    "select",
                    return_value=(
                        ((), (), ())
                        if label == "escape"
                        else ((7,), (), ())
                    ),
                ):
                    outcome = driver.read_picker(
                        state,
                        transition_picker,
                        transition_picker_fallback,
                        project_picker_viewport,
                        renderer,
                        render_picker_fallback,
                        transient=True,
                    )

                self.assertEqual(outcome.kind, kind)
                self.assertEqual(outcome.text, text)
                fallback.assert_not_called()
                latest_frame = render_picker_viewport(
                    *renderer.call_args_list[-1].args
                )
                self.assertEqual(
                    output.records[-1],
                    "\r"
                    + "\x1b[A" * (latest_frame.line_count - 1)
                    + "\r\x1b[J",
                )
                rendered = "".join(output.records)
                self.assertIn("Current", rendered)
                self.assertIn("Pending", rendered)
                self.assertIn("Apply changes?", rendered)
                self.assertNotIn("[completed]", rendered)
                self.assertNotIn("[cancelled]", rendered)
                self.assertEqual(
                    [when for _fd, when, _attrs in termios_double.set_calls],
                    [termios_double.TCSANOW, termios_double.TCSADRAIN],
                )

        driver, _input, output, fallback, _termios, _sizes = _driver(
            "\x1b[B\n",
            widths=(80, 80, 80, 20, 20, 20),
        )
        renderer = mock.Mock(wraps=render_picker_viewport)
        with mock.patch.object(
            driver_module._select,
            "select",
            return_value=((7,), (), ()),
        ):
            outcome = driver.read_picker(
                state,
                transition_picker,
                transition_picker_fallback,
                project_picker_viewport,
                renderer,
                render_picker_fallback,
                transient=True,
            )
        self.assertEqual(outcome.text, "no")
        rendered_widths = tuple(call.args[1].width for call in renderer.call_args_list)
        self.assertGreater(len(set(rendered_widths)), 1)
        latest_frame = render_picker_viewport(*renderer.call_args_list[-1].args)
        self.assertEqual(
            output.records[-1],
            "\r" + "\x1b[A" * (latest_frame.line_count - 1) + "\r\x1b[J",
        )
        fallback.assert_not_called()

    def test_transient_form_context_failures_clear_last_trusted_frame_and_restore(
        self,
    ) -> None:
        state = initialize_picker(
            Picker(
                Prompt("Discard changes?"),
                (Choice("yes", "YES"), Choice("no", "NO")),
                presentation=InputPresentation.FORM,
                transient_context=_transient_context(),
                highlighted_id="yes",
            )
        )
        capabilities = _capabilities(CapabilityProfile.ANSI, 79)
        initial_frame = render_picker_viewport(
            project_picker_viewport(state),
            capabilities,
        )
        expected_failure_clear = (
            "\r"
            + "\x1b[A" * (initial_frame.line_count - 1)
            + "\r\x1b[J\n"
        )

        driver, _input, output, fallback, termios_double, _sizes = _driver(
            "\x1b[B",
            widths=(80,),
        )
        renderer = mock.Mock(
            side_effect=(initial_frame, RuntimeError("render failed"))
        )
        with (
            mock.patch.object(
                driver_module._select,
                "select",
                return_value=((7,), (), ()),
            ),
            self.assertRaisesRegex(RuntimeError, "render failed"),
        ):
            driver.read_picker(
                state,
                transition_picker,
                transition_picker_fallback,
                project_picker_viewport,
                renderer,
                render_picker_fallback,
                transient=True,
            )
        self.assertEqual(output.records[-1], expected_failure_clear)
        fallback.assert_not_called()
        self.assertEqual(
            [when for _fd, when, _attrs in termios_double.set_calls],
            [termios_double.TCSANOW, termios_double.TCSADRAIN],
        )

        output = _RecordingOutput()
        write_calls = 0

        def fail_second_write(text: str) -> int:
            nonlocal write_calls
            write_calls += 1
            output.records.append(text)
            if write_calls == 2:
                raise OSError("write failed")
            return len(text)

        output.write = fail_second_write  # type: ignore[method-assign]
        driver, _input, _bound_output, fallback, termios_double, _sizes = _driver(
            "\x1b[B",
            widths=(80,),
            output=output,
        )
        with (
            mock.patch.object(
                driver_module._select,
                "select",
                return_value=((7,), (), ()),
            ),
            self.assertRaisesRegex(OSError, "write failed"),
        ):
            _read_picker(driver, state, transient=True)
        self.assertEqual(output.records[-1], expected_failure_clear)
        self.assertEqual(write_calls, 3)
        fallback.assert_not_called()
        self.assertEqual(
            [when for _fd, when, _attrs in termios_double.set_calls],
            [termios_double.TCSANOW, termios_double.TCSADRAIN],
        )

        restoring_termios = _FakeTermios()
        restoring_termios.restoration_failure = OSError("restore secret")
        driver, _input, output, fallback, _termios, _sizes = _driver(
            "\x03",
            widths=(80,),
            termios_double=restoring_termios,
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "^interactive terminal restoration failed$",
        ) as caught:
            _read_picker(driver, state, transient=True)
        self.assertIs(caught.exception.__cause__, restoring_termios.restoration_failure)
        self.assertEqual(
            output.records[-1],
            expected_failure_clear.removesuffix("\n"),
        )
        fallback.assert_not_called()

    def test_fallback_retries_complete_numbered_frames_and_never_uses_history(self) -> None:
        answers = mock.Mock(side_effect=("2", "9" * 10_000, "3"))
        driver, input_stream, output, _fallback, termios_double, size_calls = _driver(
            "unused",
            fallback=answers,
            input_terminal=False,
            output_terminal=False,
            os_name="nt",
        )
        driver._history_state = MainInputHistoryState(("/status",))
        state = initialize_picker(Picker(Prompt("Choose"), _choices()[:3]))
        outcome = _read_picker(driver, state)

        self.assertEqual(outcome.text, "secret-3")
        self.assertEqual(answers.call_count, 3)
        prompts = [call.args[0] for call in answers.call_args_list]
        self.assertNotIn("[validation]", prompts[0])
        for prompt in prompts[1:]:
            self.assertEqual(prompt.count("[validation]"), 1)
            self.assertEqual(
                prompt.count("enabled choice number"),
                1,
            )
        for prompt in prompts:
            self.assertIn("1. [highlighted] Alpha", prompt)
            self.assertIn("2. [disabled] Beta", prompt)
            self.assertIn("3. [available] Gamma", prompt)
            self.assertNotIn("secret-", prompt)
        self.assertEqual(len(output.records), 1)
        self.assertNotIn("[completed]", output.records[0])
        self.assertIn("3. [selected] [highlighted] Gamma", output.records[0])
        self.assertEqual(output.flush_calls, 1)
        self.assertEqual(input_stream.read_calls, 0)
        self.assertEqual(termios_double.set_calls, [])
        self.assertEqual(size_calls, [])
        self.assertEqual(driver._history_state, MainInputHistoryState(("/status",)))

    def test_dumb_nonterminal_and_nonposix_profiles_fallback_before_raw_input(self) -> None:
        cases = (
            (True, True, "posix", "dumb", [8]),
            (False, True, "posix", "xterm-256color", [8]),
            (True, False, "posix", "xterm-256color", []),
            (True, True, "nt", "xterm-256color", [8]),
        )
        for input_tty, output_tty, os_name, terminal_name, expected_sizes in cases:
            with self.subTest(
                input_tty=input_tty,
                output_tty=output_tty,
                os_name=os_name,
                terminal_name=terminal_name,
            ):
                fallback = mock.Mock(return_value="/cancel")
                driver, input_stream, output, _fallback, termios_double, sizes = _driver(
                    "unused",
                    fallback=fallback,
                    input_terminal=input_tty,
                    output_terminal=output_tty,
                    os_name=os_name,
                    terminal_name=terminal_name,
                )
                outcome = _read_picker(
                    driver,
                    initialize_picker(
                        Picker(Prompt("Choose"), (Choice("one", "One"),))
                    ),
                )
                self.assertEqual(outcome.kind, ControlOutcomeKind.CANCEL)
                fallback.assert_called_once()
                self.assertNotIn("secret", fallback.call_args.args[0])
                self.assertEqual(input_stream.read_calls, 0)
                self.assertEqual(termios_double.set_calls, [])
                self.assertEqual(sizes, expected_sizes)
                self.assertEqual(len(output.records), 1)
                self.assertIn("[cancelled]", output.records[0])

    def test_fallback_interrupt_and_eof_return_without_final_frame(self) -> None:
        for failure, expected in (
            (KeyboardInterrupt(), ControlOutcomeKind.INTERRUPT),
            (EOFError(), ControlOutcomeKind.EOF),
        ):
            with self.subTest(expected=expected):
                fallback = mock.Mock(side_effect=failure)
                driver, input_stream, output, _fallback, termios_double, _sizes = (
                    _driver(
                        "unused",
                        fallback=fallback,
                        input_terminal=False,
                        output_terminal=False,
                        os_name="nt",
                    )
                )
                outcome = _read_picker(
                    driver,
                    initialize_picker(
                        Picker(Prompt("Choose"), (Choice("one", "One"),))
                    ),
                )
                self.assertEqual(outcome.kind, expected)
                fallback.assert_called_once()
                self.assertEqual(input_stream.read_calls, 0)
                self.assertEqual(output.records, [])
                self.assertEqual(termios_double.set_calls, [])

    def test_validation_and_bad_collaborators_fail_before_terminal_acquisition(self) -> None:
        state = initialize_picker(Picker(Prompt("Choose"), (Choice("one", "One"),)))
        collaborators = (
            (None, transition_picker_fallback, project_picker_viewport, render_picker_viewport, render_picker_fallback, "transition"),
            (transition_picker, None, project_picker_viewport, render_picker_viewport, render_picker_fallback, "fallback_transition"),
            (transition_picker, transition_picker_fallback, None, render_picker_viewport, render_picker_fallback, "viewport_projector"),
            (transition_picker, transition_picker_fallback, project_picker_viewport, None, render_picker_fallback, "viewport_renderer"),
            (transition_picker, transition_picker_fallback, project_picker_viewport, render_picker_viewport, None, "fallback_renderer"),
        )
        for transition, fallback_transition, projector, renderer, fallback_renderer, name in collaborators:
            with self.subTest(name=name):
                driver, input_stream, output, fallback, termios_double, sizes = _driver("")
                with self.assertRaisesRegex(TypeError, name):
                    driver.read_picker(
                        state,
                        transition,  # type: ignore[arg-type]
                        fallback_transition,  # type: ignore[arg-type]
                        projector,  # type: ignore[arg-type]
                        renderer,  # type: ignore[arg-type]
                        fallback_renderer,  # type: ignore[arg-type]
                    )
                self.assertEqual(input_stream.tty_calls, 0)
                self.assertEqual(input_stream.fileno_calls, 0)
                self.assertEqual(input_stream.read_calls, 0)
                self.assertEqual(output.records, [])
                self.assertEqual(termios_double.set_calls, [])
                self.assertEqual(sizes, [])
                fallback.assert_not_called()

        driver, input_stream, output, fallback, termios_double, sizes = _driver("")
        with self.assertRaisesRegex(TypeError, "PickerControlState"):
            driver.read_picker(  # type: ignore[arg-type]
                object(),
                transition_picker,
                transition_picker_fallback,
                project_picker_viewport,
                render_picker_viewport,
                render_picker_fallback,
            )
        self.assertEqual(input_stream.tty_calls, 0)
        self.assertEqual(output.records, [])
        self.assertEqual(termios_double.set_calls, [])
        self.assertEqual(sizes, [])
        fallback.assert_not_called()

    def test_bad_projection_render_and_fallback_results_restore_without_retry(self) -> None:
        state = initialize_picker(Picker(Prompt("Choose"), (Choice("one", "One"),)))
        live_cases = (
            (
                mock.Mock(return_value=object()),
                render_picker_viewport,
                "viewport_projector must return a PickerViewport",
            ),
            (
                project_picker_viewport,
                mock.Mock(return_value=object()),
                "viewport_renderer must return a RenderedFrame",
            ),
            (
                project_picker_viewport,
                mock.Mock(return_value=RenderedFrame("wrong", 78, 1)),
                "rendered picker width must match the live width",
            ),
        )
        for projector, renderer, message in live_cases:
            with self.subTest(message=message):
                driver, _input, _output, fallback, termios_double, _sizes = _driver(
                    "\n"
                )
                with self.assertRaisesRegex(
                    (TypeError, ValueError),
                    message,
                ):
                    driver.read_picker(
                        state,
                        transition_picker,
                        transition_picker_fallback,
                        projector,
                        renderer,
                        render_picker_fallback,
                    )
                fallback.assert_not_called()
                self.assertEqual(
                    [when for _fd, when, _attrs in termios_double.set_calls],
                    [termios_double.TCSANOW, termios_double.TCSADRAIN],
                )

        fallback_cases = (
            (
                mock.Mock(return_value=object()),
                transition_picker_fallback,
                "fallback_renderer must return a RenderedInputFrame",
            ),
            (
                mock.Mock(return_value=RenderedInputFrame("pick> ", "pick> ", 79, 1)),
                transition_picker_fallback,
                "rendered picker fallback width must match capabilities",
            ),
            (
                render_picker_fallback,
                mock.Mock(
                    return_value=(object(), ControlOutcome(ControlOutcomeKind.CANCEL))
                ),
                "fallback_transition must return a PickerControlState",
            ),
        )
        for renderer, fallback_transition, message in fallback_cases:
            with self.subTest(message=message):
                fallback = mock.Mock(return_value="/cancel")
                driver, input_stream, output, _fallback, termios_double, _sizes = (
                    _driver(
                        "unused",
                        fallback=fallback,
                        input_terminal=False,
                        output_terminal=False,
                        os_name="nt",
                    )
                )
                with self.assertRaisesRegex((TypeError, ValueError), message):
                    driver.read_picker(
                        state,
                        transition_picker,
                        fallback_transition,
                        project_picker_viewport,
                        render_picker_viewport,
                        renderer,
                    )
                self.assertEqual(input_stream.read_calls, 0)
                self.assertEqual(output.records, [])
                self.assertEqual(termios_double.set_calls, [])
                if renderer is render_picker_fallback:
                    fallback.assert_called_once()
                else:
                    fallback.assert_not_called()

    def test_uncertain_capability_falls_back_without_consuming_raw_input(self) -> None:
        fallback = mock.Mock(return_value="/cancel")
        driver, input_stream, output, _fallback, termios_double, _sizes = _driver(
            "unused",
            fallback=fallback,
        )
        driver._os_provider = mock.Mock(side_effect=RuntimeError("uncertain OS"))
        outcome = _read_picker(
            driver,
            initialize_picker(Picker(Prompt("Choose"), (Choice("one", "One"),))),
        )
        self.assertEqual(outcome.kind, ControlOutcomeKind.CANCEL)
        fallback.assert_called_once()
        self.assertEqual(input_stream.read_calls, 0)
        self.assertEqual(termios_double.set_calls, [])
        self.assertEqual(len(output.records), 1)
        self.assertIn("[cancelled]", output.records[0])

    def test_render_read_write_flush_and_restoration_failures_restore_exactly(self) -> None:
        state = initialize_picker(Picker(Prompt("Choose"), (Choice("one", "One"),)))

        driver, _input, output, fallback, termios_double, _sizes = _driver("\n")
        renderer = mock.Mock(side_effect=RuntimeError("render failed"))
        with self.assertRaisesRegex(RuntimeError, "render failed"):
            driver.read_picker(
                state,
                transition_picker,
                transition_picker_fallback,
                project_picker_viewport,
                renderer,
                render_picker_fallback,
            )
        fallback.assert_not_called()
        self.assertEqual(
            [when for _fd, when, _attrs in termios_double.set_calls],
            [termios_double.TCSANOW, termios_double.TCSADRAIN],
        )
        self.assertEqual(output.records, ["\r\r\x1b[J\n"])

        for failure_kind in ("write", "flush"):
            with self.subTest(failure=failure_kind):
                output = _RecordingOutput()
                setattr(output, f"{failure_kind}_failure", OSError(f"{failure_kind} failed"))
                driver, _input, _output, fallback, termios_double, _sizes = _driver(
                    "\n",
                    output=output,
                )
                with self.assertRaisesRegex(OSError, f"{failure_kind} failed"):
                    _read_picker(driver, state)
                fallback.assert_not_called()
                self.assertEqual(
                    [when for _fd, when, _attrs in termios_double.set_calls],
                    [termios_double.TCSANOW, termios_double.TCSADRAIN],
                )

        driver, _input, output, fallback, termios_double, _sizes = _driver(
            "unused",
            malformed_input=object(),
        )
        with self.assertRaisesRegex(
            TypeError,
            "live input byte reader must return exactly one byte",
        ):
            _read_picker(driver, state)
        fallback.assert_not_called()
        self.assertEqual(
            [when for _fd, when, _attrs in termios_double.set_calls],
            [termios_double.TCSANOW, termios_double.TCSADRAIN],
        )
        self.assertEqual(
            output.records[-1],
            "\r\x1b[A\x1b[A\r\x1b[J\n",
        )

        termios_double = _FakeTermios()
        termios_double.restoration_failure = OSError("restore secret")
        driver, _input, output, fallback, _termios, _sizes = _driver(
            "\x03",
            termios_double=termios_double,
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "^interactive terminal restoration failed$",
        ) as caught:
            _read_picker(driver, state)
        self.assertIs(caught.exception.__cause__, termios_double.restoration_failure)
        fallback.assert_not_called()
        self.assertNotIn("[completed]", "".join(output.records))

    def test_activation_failure_restores_without_switching_to_fallback(self) -> None:
        termios_double = _FakeTermios()
        termios_double.activation_failure = OSError("activate failed")
        fallback = mock.Mock(return_value="1")
        driver, input_stream, output, _fallback, termios_double, _sizes = _driver(
            "unused",
            fallback=fallback,
            termios_double=termios_double,
        )
        with self.assertRaisesRegex(OSError, "^activate failed$"):
            _read_picker(
                driver,
                initialize_picker(
                    Picker(Prompt("Choose"), (Choice("one", "One"),))
                ),
            )
        fallback.assert_not_called()
        self.assertEqual(input_stream.read_calls, 0)
        self.assertEqual(
            [when for _fd, when, _attrs in termios_double.set_calls],
            [termios_double.TCSANOW, termios_double.TCSADRAIN],
        )
        self.assertEqual(output.records, [])


class PickerAdapterCompatibilityTests(unittest.TestCase):
    """Lock callback/stream binding and unchanged public/module boundaries."""

    def test_callback_adapter_is_resource_free_retries_without_synthetic_output(self) -> None:
        prompts: list[str] = []
        answers = iter(("2", ""))
        writes: list[str] = []
        choices = (
            Choice(
                "secret-1",
                "Alpha",
                form_summary="Current: alpha",
                form_summary_role=SemanticRole.MUTED,
            ),
            Choice(
                "secret-2",
                "Beta",
                enabled=False,
                disabled_reason="Unavailable",
            ),
            Choice(
                "secret-3",
                "Gamma",
                form_summary="Pending: gamma",
                form_summary_role=SemanticRole.WARNING,
            ),
        )

        def read_line(prompt: str) -> str:
            prompts.append(prompt)
            return next(answers)

        terminal = InteractiveTerminal(read_line, writes.append, width=40)
        adapter = InteractivePickerInput(terminal)
        self.assertEqual(prompts, [])
        self.assertEqual(writes, [])
        outcome = adapter.read_picker(
            Prompt("Choose"),
            choices,
            presentation=InputPresentation.FORM,
            transient_context=_transient_context(),
            highlighted_id="secret-3",
            selected_id="secret-1",
        )
        self.assertEqual(
            outcome,
            ControlOutcome(ControlOutcomeKind.COMMIT, text="secret-3"),
        )
        self.assertEqual(len(prompts), 2)
        self.assertNotIn("[validation]", prompts[0])
        self.assertEqual(prompts[1].count("[validation]"), 1)
        for prompt in prompts:
            self.assertIn("1. Alpha (selected)", prompt)
            self.assertIn("2. Beta (disabled)", prompt)
            self.assertIn("> 3. Gamma", prompt)
            self.assertIn("Current: alpha", prompt)
            self.assertIn("Pending: gamma", prompt)
            self.assertNotIn("secret-", prompt)
            self.assertNotIn("KO (Changed)", prompt)
            self.assertNotIn("EN (Changed)", prompt)
            self.assertNotIn("(Changed)", prompt)
            self.assertNotIn("[activation]", prompt)
        self.assertEqual(writes, [])

    def test_adapters_forward_form_presentation_limit_context_and_initial_value(
        self,
    ) -> None:
        terminal = InteractiveTerminal(
            lambda prompt: (_ for _ in ()).throw(
                AssertionError(f"unexpected line read: {prompt}")
            ),
            lambda _text: None,
        )
        picker_states: list[PickerControlState] = []
        prompt_views: list[InputPromptView] = []

        def read_picker(
            state: PickerControlState,
            *,
            transient: bool = True,
        ) -> ControlOutcome:
            self.assertTrue(transient)
            picker_states.append(state)
            return ControlOutcome(ControlOutcomeKind.CANCEL)

        def read_prompt(
            view: InputPromptView,
            *,
            transient: bool = True,
        ) -> ControlOutcome:
            self.assertTrue(transient)
            prompt_views.append(view)
            return ControlOutcome(ControlOutcomeKind.CANCEL)

        terminal._picker_read = read_picker
        terminal._prompt_read = read_prompt
        picker_outcome = InteractivePickerInput(terminal).read_picker(
            Prompt("Settings"),
            _choices(),
            presentation=InputPresentation.FORM,
            transient_context=_transient_context(),
            visible_choice_limit=9,
            highlighted_id="secret-8",
        )
        notice = Notice("Pending values.", role=SemanticRole.WARNING)
        prompt_outcome = InteractivePromptInput(terminal).read_prompt(
            Prompt("Maximum debate rounds"),
            presentation=InputPresentation.FORM,
            context=(notice,),
            initial_value="12",
        )

        self.assertIs(picker_outcome.kind, ControlOutcomeKind.CANCEL)
        self.assertIs(prompt_outcome.kind, ControlOutcomeKind.CANCEL)
        self.assertEqual(len(picker_states), 1)
        self.assertIs(
            picker_states[0].picker.presentation,
            InputPresentation.FORM,
        )
        self.assertEqual(picker_states[0].picker.visible_choice_limit, 9)
        self.assertEqual(picker_states[0].picker.highlighted_id, "secret-8")
        self.assertEqual(
            picker_states[0].picker.transient_context,
            _transient_context(),
        )
        self.assertEqual(
            prompt_views,
            [
                InputPromptView(
                    (notice,),
                    Prompt("Maximum debate rounds"),
                    presentation=InputPresentation.FORM,
                    initial_value="12",
                )
            ],
        )

    def test_callback_terminal_events_failures_and_validation_do_not_synthesize_output(self) -> None:
        choices = (Choice("one", "One"),)
        for failure, expected in (
            (KeyboardInterrupt(), ControlOutcomeKind.INTERRUPT),
            (EOFError(), ControlOutcomeKind.EOF),
        ):
            with self.subTest(expected=expected):
                writes: list[str] = []

                def fail(_prompt: str, value: BaseException = failure) -> str:
                    raise value

                outcome = InteractivePickerInput(
                    InteractiveTerminal(fail, writes.append)
                ).read_picker(Prompt("Choose"), choices)
                self.assertEqual(outcome.kind, expected)
                self.assertEqual(writes, [])

        writer = mock.Mock(side_effect=RuntimeError("writer failed"))
        outcome = InteractivePickerInput(
            InteractiveTerminal(lambda _prompt: "1", writer)
        ).read_picker(Prompt("Choose"), choices)
        self.assertEqual(
            outcome,
            ControlOutcome(ControlOutcomeKind.COMMIT, text="one"),
        )
        writer.assert_not_called()

        read_line = mock.Mock(side_effect=AssertionError("must not read"))
        write_text = mock.Mock(side_effect=AssertionError("must not write"))
        adapter = InteractivePickerInput(InteractiveTerminal(read_line, write_text))
        with self.assertRaises((TypeError, ValueError)):
            adapter.read_picker(
                Prompt("Choose"),
                (Choice("same", "One"), Choice("same", "Two")),
            )
        read_line.assert_not_called()
        write_text.assert_not_called()
        with self.assertRaisesRegex(TypeError, "InteractiveTerminal"):
            InteractivePickerInput(object())  # type: ignore[arg-type]

    def test_stream_factory_binds_picker_to_the_sole_raw_driver(self) -> None:
        driver = mock.Mock(spec=RawTerminalDriver)
        driver.capabilities.return_value = _capabilities(CapabilityProfile.ANSI, 100)
        driver.read_picker.return_value = ControlOutcome(
            ControlOutcomeKind.COMMIT,
            text="one",
        )
        fallback = mock.Mock()
        input_stream = object()
        output_stream = object()
        with mock.patch.object(_core, "RawTerminalDriver", return_value=driver) as factory:
            terminal = _core.build_stream_terminal(
                fallback,
                input_stream,  # type: ignore[arg-type]
                output_stream,  # type: ignore[arg-type]
            )
        factory.assert_called_once_with(
            fallback,
            input_stream,
            output_stream,
            transition_line_input,
            transition_main_command_input,
            append_main_input_history,
        )
        outcome = InteractivePickerInput(terminal).read_picker(
            Prompt("Choose"),
            (Choice("one", "One"),),
        )
        self.assertEqual(outcome.text, "one")
        driver.read_picker.assert_called_once_with(
            initialize_picker(Picker(Prompt("Choose"), (Choice("one", "One"),))),
            transition_picker,
            transition_picker_fallback,
            project_picker_viewport,
            render_picker_viewport,
            render_picker_fallback,
            transient=True,
        )
        self.assertEqual(factory.call_count, 1)

    def test_public_exports_line_state_and_dependency_edges_remain_unchanged(self) -> None:
        self.assertEqual(
            tuple(terminal_facade.__all__),
            (
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
                "InteractiveTranscriptViewer",
            ),
        )
        self.assertNotIn(PickerControlState, get_args(driver_module._ControlState))
        self.assertNotIn(PickerViewport, get_args(driver_module._ControlState))
        self.assertNotIn("render_picker_viewport", driver_module.__dict__)
        self.assertNotIn("render_picker_fallback", driver_module.__dict__)
        self.assertNotIn("read_picker", InteractiveTerminal.__dict__)
        self.assertEqual(
            tuple(inspect.signature(InteractiveTerminal).parameters),
            ("read_line", "write_text", "width", "main_read_line"),
        )
        for name in (
            "initialize_picker",
            "reconcile_picker",
            "transition_picker",
            "transition_picker_fallback",
            "project_picker_viewport",
        ):
            self.assertNotIn(name, _controls.__all__)
        for name in ("render_picker_viewport", "render_picker_fallback"):
            self.assertNotIn(name, _render.__all__)
