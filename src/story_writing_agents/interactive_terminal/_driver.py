"""Own raw terminal capability, key, stream, and restoration mechanics."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import TextIO, TypeAlias

from ._model import (
    CapabilityProfile,
    ControlOutcome,
    ControlOutcomeKind,
    KeyEvent,
    KeyKind,
    LineInputState,
    MainInputControlState,
    MainInputHistoryState,
    SlashCommandSuggestion,
    TerminalCapabilities,
    display_character_width,
    sanitize_terminal_text,
)

try:
    import termios as _termios
except ImportError:  # pragma: no cover - exercised on non-POSIX hosts
    _termios = None


_SUGGESTION_HEADER = "[suggestions]"
_CARRIAGE_RETURN = "\r"
_CLEAR_TO_END = "\x1b[J"
_CURSOR_UP = "\x1b[A"
_CURSOR_RIGHT = "\x1b[C"
_DEFAULT_TERMINAL_WIDTH = 80
_MAX_ESCAPE_SEQUENCE_LENGTH = 64
_DEFAULT_TERMIOS = object()


@dataclass(slots=True)
class _LiveEditorState:
    """Retain only call-local escape decoding and redraw position."""

    escape_sequence: str | None = None
    rendered_cursor_row: int = 0


@dataclass(frozen=True, slots=True)
class _TerminalConfiguration:
    """Capture one validated POSIX terminal mode and its exact restoration."""

    input_descriptor: int
    original_attributes: list[object]
    editor_attributes: list[object]
    width: int
    eof_character: str
    interrupt_character: str
    termios_module: object


_ControlState: TypeAlias = LineInputState | MainInputControlState
_ControlTransition: TypeAlias = Callable[
    [_ControlState, KeyEvent],
    tuple[_ControlState, ControlOutcome | None],
]
_LineTransition: TypeAlias = Callable[
    [LineInputState, KeyEvent],
    tuple[LineInputState, ControlOutcome | None],
]
_MainTransition: TypeAlias = Callable[
    [MainInputControlState, KeyEvent, MainInputHistoryState],
    tuple[MainInputControlState, ControlOutcome | None],
]
_HistoryAppend: TypeAlias = Callable[
    [MainInputHistoryState, str], MainInputHistoryState
]


class RawTerminalDriver:
    """Own one stream pair's live editing and raw terminal resource lifecycle."""

    __slots__ = (
        "_fallback_read_line",
        "_history_append",
        "_history_state",
        "_input_stream",
        "_line_transition",
        "_main_transition",
        "_os_provider",
        "_output_stream",
        "_termios_provider",
    )

    def __init__(
        self,
        fallback_read_line: Callable[[str], str],
        input_stream: TextIO,
        output_stream: TextIO,
        line_transition: _LineTransition,
        main_transition: _MainTransition,
        history_append: _HistoryAppend,
        *,
        termios_provider: Callable[[], object | None] | None = None,
        os_provider: Callable[[], object] | None = None,
    ) -> None:
        """Retain borrowed fallback and terminal stream collaborators.

        Construction performs no stream access or terminal mutation.
        ``read_live_line`` resolves live-terminal capabilities at call time;
        ``read_line`` delegates ordinary fallback input, whose callback remains
        responsible for its own I/O lifecycle.
        """

        if not callable(fallback_read_line):
            raise TypeError("fallback_read_line must be callable")
        if not callable(line_transition):
            raise TypeError("line_transition must be callable")
        if not callable(main_transition):
            raise TypeError("main_transition must be callable")
        if not callable(history_append):
            raise TypeError("history_append must be callable")
        if termios_provider is not None and not callable(termios_provider):
            raise TypeError("termios_provider must be callable or None")
        if os_provider is not None and not callable(os_provider):
            raise TypeError("os_provider must be callable or None")
        self._fallback_read_line = fallback_read_line
        self._input_stream = input_stream
        self._output_stream = output_stream
        self._line_transition = line_transition
        self._main_transition = main_transition
        self._history_append = history_append
        self._history_state = MainInputHistoryState()
        self._termios_provider = (
            (lambda: _termios) if termios_provider is None else termios_provider
        )
        self._os_provider = (lambda: os) if os_provider is None else os_provider

    def read_line(self, prompt: str) -> str:
        """Use the borrowed line fallback without acquiring raw resources."""

        return self._fallback_read_line(prompt)

    def write_text(self, text: str) -> None:
        """Write and flush one payload through the sole stream owner."""

        if type(text) is not str:
            raise TypeError("terminal output must be a string")
        written = self._output_stream.write(text)
        if written is not None and written != len(text):
            raise OSError("interactive terminal write was incomplete")
        self._output_stream.flush()

    def capabilities(self) -> TerminalCapabilities:
        """Resolve the current destination profile without acquiring raw mode."""

        try:
            termios_module = self._termios_provider()
        except Exception:
            termios_module = None
        try:
            os_module = self._os_provider()
        except Exception:
            os_module = object()
        return detect_terminal_capabilities(
            self._input_stream,
            self._output_stream,
            termios_module=termios_module,
            os_module=os_module,
        )

    def read_live_line(
        self,
        prompt: str,
        suggestion_provider: Callable[
            [str], tuple[SlashCommandSuggestion, ...]
        ],
    ) -> str:
        """Read one history-free Unicode buffer with a preview provider."""

        return self._read_control_line(
            prompt,
            suggestion_provider,
            LineInputState(),
            self._line_transition,
        )

    def read_main_line(
        self,
        prompt: str,
        suggestion_provider: Callable[
            [str], tuple[SlashCommandSuggestion, ...]
        ],
    ) -> str:
        """Read one main line and retain its successful exact nonblank commit.

        Each read starts with inactive navigation over the driver's one
        process-local history store. The current preview list is not an open
        selectable overlay, so Up and Down remain history keys in this slice.
        History changes only after fallback success or live display cleanup and
        terminal restoration have completed successfully.
        """

        def transition(
            state: _ControlState,
            event: KeyEvent,
        ) -> tuple[_ControlState, ControlOutcome | None]:
            if type(state) is not MainInputControlState:
                raise TypeError("main transition requires MainInputControlState")
            return self._main_transition(state, event, self._history_state)

        line = self._read_control_line(
            prompt,
            suggestion_provider,
            MainInputControlState(),
            transition,
        )
        # Preserve the established downstream line-validation boundary. Invalid
        # fallback shapes or framing are returned for that boundary to reject,
        # and therefore never become successfully completed history entries.
        if type(line) is str and "\r" not in line and "\n" not in line:
            updated_history = self._history_append(self._history_state, line)
            if type(updated_history) is not MainInputHistoryState:
                raise TypeError(
                    "history_append must return a MainInputHistoryState"
                )
            self._history_state = updated_history
        return line

    def _read_control_line(
        self,
        prompt: str,
        suggestion_provider: Callable[
            [str], tuple[SlashCommandSuggestion, ...]
        ],
        initial_state: _ControlState,
        transition: _ControlTransition,
    ) -> str:
        """Read one exact buffer through one pure logical-state transition.

        A capable POSIX terminal is placed in call-local noncanonical/no-echo
        mode and restored after submission or every failure. Unsupported or
        safely recoverable setup uses the borrowed fallback before consuming
        input. Active failures propagate after cleanup; uncertain restoration
        raises a fatal fixed ``RuntimeError`` instead.
        """

        if not isinstance(prompt, str):
            raise TypeError("prompt must be a string")
        if not callable(suggestion_provider):
            raise TypeError("suggestion_provider must be callable")
        if type(initial_state) not in {LineInputState, MainInputControlState}:
            raise TypeError("initial_state must be an input control state")
        if not callable(transition):
            raise TypeError("transition must be callable")

        try:
            configuration = _terminal_configuration(
                self._input_stream,
                self._output_stream,
                termios_module=self._termios_provider(),
                os_module=self._os_provider(),
            )
        except Exception:
            return self._fallback_read_line(prompt)
        if configuration is None:
            return self._fallback_read_line(prompt)

        try:
            _activate_terminal(configuration)
        except BaseException as setup_failure:
            _restore_terminal_or_raise(configuration, setup_failure)
            if isinstance(setup_failure, Exception):
                return self._fallback_read_line(prompt)
            raise

        display_state = _LiveEditorState()
        line: str | None = None
        line_state: LineInputState | None = None
        active_failure: BaseException | None = None
        try:
            line, line_state = self._read_live_buffer(
                prompt,
                configuration,
                display_state,
                suggestion_provider,
                initial_state,
                transition,
            )
            self._finish_live_display(
                prompt,
                configuration.width,
                display_state,
                line_state,
            )
        except BaseException as failure:
            active_failure = failure
            try:
                self._clear_live_display(display_state, line_break=True)
            except BaseException:
                # The active failure remains authoritative when display cleanup
                # cannot use the same failed output collaborator.
                pass

        _restore_terminal_or_raise(configuration, active_failure)
        if active_failure is not None:
            raise active_failure
        assert line is not None
        assert line_state is not None
        return line

    def _read_live_buffer(
        self,
        prompt: str,
        configuration: _TerminalConfiguration,
        display_state: _LiveEditorState,
        suggestion_provider: Callable[
            [str], tuple[SlashCommandSuggestion, ...]
        ],
        initial_state: _ControlState,
        transition: _ControlTransition,
    ) -> tuple[str, LineInputState]:
        """Edit one code-point buffer until submit, interrupt, EOF, or failure.

        The immutable logical state is the sole buffer, cursor, navigation, and
        focus authority. ``display_state`` owns only physical escape decoding
        and redraw position. Stream failures propagate to the owning read
        boundary for display and attribute cleanup; no parser, story provider,
        persistence, or history append occurs here.
        """

        control_state = initial_state
        display_state.rendered_cursor_row = self._redraw_live_display(
            prompt,
            configuration.width,
            display_state,
            _control_line_state(control_state),
            suggestion_provider,
        )
        while True:
            character = self._read_character()

            if display_state.escape_sequence is not None:
                event, reprocess = _consume_escape_character(
                    display_state,
                    character,
                )
                if event is not None:
                    control_state, outcome, changed = _apply_control_event(
                        control_state,
                        event,
                        transition,
                    )
                    completed = _completed_text_or_raise(outcome)
                    if completed is not None:
                        return completed, _control_line_state(control_state)
                    if changed:
                        display_state.rendered_cursor_row = self._redraw_live_display(
                            prompt,
                            configuration.width,
                            display_state,
                            _control_line_state(control_state),
                            suggestion_provider,
                        )
                if not reprocess:
                    continue

            if character == "\x1b":
                display_state.escape_sequence = character
                continue
            event = _key_event(character, configuration)
            control_state, outcome, changed = _apply_control_event(
                control_state,
                event,
                transition,
            )
            completed = _completed_text_or_raise(outcome)
            if completed is not None:
                return completed, _control_line_state(control_state)
            if changed:
                display_state.rendered_cursor_row = self._redraw_live_display(
                    prompt,
                    configuration.width,
                    display_state,
                    _control_line_state(control_state),
                    suggestion_provider,
                )

    def _read_character(self) -> str:
        """Read one decoded code point from the borrowed active input stream.

        Physical EOF raises ``EOFError``. Invalid stream return shapes raise
        without normalization so the owning read boundary can restore terminal
        state and propagate the collaborator failure.
        """

        character = self._input_stream.read(1)
        if character == "":
            raise EOFError
        if not isinstance(character, str) or len(character) != 1:
            raise TypeError("input_stream.read(1) must return one character")
        return character

    def _redraw_live_display(
        self,
        prompt: str,
        width: int,
        display_state: _LiveEditorState,
        line_state: LineInputState,
        suggestion_provider: Callable[
            [str], tuple[SlashCommandSuggestion, ...]
        ],
    ) -> int:
        """Replace the current editor area and return its visible cursor row.

        Dynamic prompt, buffer, and registry tokens are sanitized before one
        write. Only fixed cursor controls are emitted. A writer or flush failure
        propagates for the owning read boundary to clear and restore resources.
        """

        render_width = max(1, width - 1)
        input_lines, cursor_row, cursor_column = _wrapped_editor_input(
            prompt,
            line_state.buffer,
            line_state.cursor,
            render_width,
        )
        suggestions = suggestion_provider(line_state.buffer)
        if type(suggestions) is not tuple or any(
            type(item) is not SlashCommandSuggestion for item in suggestions
        ):
            raise TypeError(
                "suggestion_provider must return SlashCommandSuggestion values"
            )
        panel_lines: list[str] = []
        if suggestions:
            panel_lines.extend(
                _wrap_editor_text(_SUGGESTION_HEADER, render_width)
            )
            for suggestion in suggestions:
                panel_lines.extend(
                    _wrap_editor_text(suggestion.token, render_width)
                )

        output_lines = [*input_lines, *panel_lines]
        move_to_top = _CURSOR_UP * display_state.rendered_cursor_row
        move_to_cursor = _CURSOR_UP * (
            len(output_lines) - 1 - cursor_row
        )
        payload = "".join(
            (
                _CARRIAGE_RETURN,
                move_to_top,
                _CARRIAGE_RETURN,
                _CLEAR_TO_END,
                "\n".join(output_lines),
                move_to_cursor,
                _CARRIAGE_RETURN,
                _CURSOR_RIGHT * cursor_column,
            )
        )
        self._write_live_text(payload)
        return cursor_row

    def _finish_live_display(
        self,
        prompt: str,
        width: int,
        display_state: _LiveEditorState,
        line_state: LineInputState,
    ) -> None:
        """Remove suggestions and leave the submitted prompt line visible.

        The exact buffer is not changed; only its sanitized width-wrapped view
        is written. Output failure propagates while terminal restoration remains
        owned by ``read_line``.
        """

        render_width = max(1, width - 1)
        input_lines, _cursor_row, _cursor_column = _wrapped_editor_input(
            prompt,
            line_state.buffer,
            len(line_state.buffer),
            render_width,
        )
        move_to_top = _CURSOR_UP * display_state.rendered_cursor_row
        payload = "".join(
            (
                _CARRIAGE_RETURN,
                move_to_top,
                _CARRIAGE_RETURN,
                _CLEAR_TO_END,
                "\n".join(input_lines),
                "\n",
            )
        )
        self._write_live_text(payload)
        display_state.rendered_cursor_row = 0

    def _clear_live_display(
        self,
        state: _LiveEditorState,
        *,
        line_break: bool,
    ) -> None:
        """Best-effort clear one active editor area after an input failure.

        ``state`` identifies the current wrapped cursor row. The output callback
        is the only mutated collaborator; failures propagate to the caller that
        decides whether an earlier active failure remains authoritative.
        """

        payload = "".join(
            (
                _CARRIAGE_RETURN,
                _CURSOR_UP * state.rendered_cursor_row,
                _CARRIAGE_RETURN,
                _CLEAR_TO_END,
                "\n" if line_break else "",
            )
        )
        self._write_live_text(payload)
        state.rendered_cursor_row = 0

    def _write_live_text(self, text: str) -> None:
        """Write and flush one trusted redraw payload or raise on short output.

        ``text`` is assembled from fixed controls and sanitized dynamic pieces.
        The borrowed output stream is not closed; short writes are surfaced so
        the active editor boundary can restore terminal state.
        """

        self.write_text(text)


def detect_terminal_capabilities(
    input_stream: TextIO,
    output_stream: TextIO,
    *,
    termios_module: object = _DEFAULT_TERMIOS,
    os_module: object = os,
) -> TerminalCapabilities:
    """Return a safe independent stream capability and width observation.

    Failures never claim a terminal feature. A reliable positive width is
    capped at 120 columns; absent or invalid size information falls back to 80.
    No stream is read or written and no terminal attribute is changed.
    """

    resolved_termios = (
        _termios if termios_module is _DEFAULT_TERMIOS else termios_module
    )
    try:
        input_tty = _stream_is_tty(input_stream)
    except Exception:
        input_tty = False
    try:
        output_tty = _stream_is_tty(output_stream)
    except Exception:
        output_tty = False

    try:
        environment = getattr(os_module, "environ", {})
    except Exception:
        environment = {}
    try:
        terminal_name = environment.get("TERM", "")
    except Exception:
        terminal_name = ""
    term_capable = _terminal_name_is_capable(terminal_name)
    try:
        platform_name = getattr(os_module, "name", "")
    except Exception:
        platform_name = ""
    input_descriptor_available = False
    if input_tty and _callable_attribute(input_stream, "fileno"):
        try:
            _stream_descriptor(input_stream)
            input_descriptor_available = True
        except Exception:
            pass
    output_descriptor_available = False
    if output_tty and _callable_attribute(output_stream, "fileno"):
        try:
            _stream_descriptor(output_stream)
            output_descriptor_available = True
        except Exception:
            pass
    raw_capable = bool(
        _termios_interface_is_available(resolved_termios)
        and platform_name == "posix"
        and term_capable
        and input_tty
        and output_tty
        and _callable_attribute(input_stream, "read")
        and input_descriptor_available
        and _callable_attribute(output_stream, "write")
        and _callable_attribute(output_stream, "flush")
        and output_descriptor_available
        and _callable_attribute(os_module, "get_terminal_size")
    )

    width = _DEFAULT_TERMINAL_WIDTH
    if output_tty and _callable_attribute(output_stream, "fileno"):
        try:
            output_descriptor = _stream_descriptor(output_stream)
            columns = os_module.get_terminal_size(output_descriptor).columns
            if (
                isinstance(columns, int)
                and not isinstance(columns, bool)
                and columns > 0
            ):
                width = min(columns, 120)
        except Exception:
            width = _DEFAULT_TERMINAL_WIDTH

    no_color = False
    try:
        no_color = "NO_COLOR" in environment
    except Exception:
        no_color = True
    decorative_sgr = raw_capable and not no_color
    if decorative_sgr:
        profile = CapabilityProfile.ANSI
    elif raw_capable:
        profile = CapabilityProfile.PLAIN
    else:
        profile = CapabilityProfile.LINE
    return TerminalCapabilities(
        profile,
        width,
        input_tty=input_tty,
        output_tty=output_tty,
        raw_mode=raw_capable,
        cursor_controls=raw_capable,
        decorative_sgr=decorative_sgr,
    )


def _stream_is_tty(stream: TextIO) -> bool:
    """Return one exact stream TTY report or a safe false result."""

    isatty = getattr(stream, "isatty", None)
    return bool(callable(isatty) and isatty())


def _callable_attribute(owner: object, name: str) -> bool:
    """Return false when one capability attribute is absent or uncertain."""

    try:
        return callable(getattr(owner, name, None))
    except Exception:
        return False


def _termios_interface_is_available(termios_module: object | None) -> bool:
    """Return whether fixed raw-mode operations have a complete interface."""

    if termios_module is None:
        return False
    if not _callable_attribute(
        termios_module,
        "tcgetattr",
    ) or not _callable_attribute(termios_module, "tcsetattr"):
        return False
    constants = (
        "ECHO",
        "ICANON",
        "TCSADRAIN",
        "TCSANOW",
        "VEOF",
        "VINTR",
        "VMIN",
        "VTIME",
    )
    for name in constants:
        try:
            value = getattr(termios_module, name, None)
        except Exception:
            return False
        if not isinstance(value, int) or isinstance(value, bool):
            return False
    return True


def _terminal_name_is_capable(value: object) -> bool:
    """Treat missing, blank, malformed, and dumb terminal names as uncertain."""

    return bool(
        isinstance(value, str)
        and value.strip()
        and value.strip().casefold() != "dumb"
    )


def _terminal_configuration(
    input_stream: TextIO,
    output_stream: TextIO,
    *,
    termios_module: object | None,
    os_module: object,
) -> _TerminalConfiguration | None:
    """Return a complete POSIX live-editor setup or ``None`` for fallback.

    Capability inspection consumes no input and performs no output or terminal
    mutation. Ordinary descriptor, TTY, environment, or attribute failures are
    allowed to propagate to the public reader's safe fallback decision.
    """

    if termios_module is None or getattr(os_module, "name", "") != "posix":
        return None
    environment = getattr(os_module, "environ", {})
    terminal_name = environment.get("TERM", "")
    if not _terminal_name_is_capable(terminal_name):
        return None

    for stream in (input_stream, output_stream):
        isatty = getattr(stream, "isatty", None)
        fileno = getattr(stream, "fileno", None)
        if not callable(isatty) or not callable(fileno) or not isatty():
            return None
    if not callable(getattr(input_stream, "read", None)):
        return None
    if not callable(getattr(output_stream, "write", None)) or not callable(
        getattr(output_stream, "flush", None)
    ):
        return None

    input_descriptor = _stream_descriptor(input_stream)
    output_descriptor = _stream_descriptor(output_stream)
    original_attributes = termios_module.tcgetattr(input_descriptor)
    editor_attributes = list(original_attributes)
    editor_attributes[6] = list(original_attributes[6])
    editor_attributes[3] &= ~(termios_module.ICANON | termios_module.ECHO)
    editor_attributes[6][termios_module.VMIN] = 1
    editor_attributes[6][termios_module.VTIME] = 0
    eof_character = _configured_control_character(
        original_attributes[6][termios_module.VEOF],
        "VEOF",
    )
    interrupt_character = _configured_control_character(
        original_attributes[6][termios_module.VINTR],
        "VINTR",
    )
    columns = os_module.get_terminal_size(output_descriptor).columns
    width = (
        min(columns, 120)
        if isinstance(columns, int)
        and not isinstance(columns, bool)
        and columns > 0
        else _DEFAULT_TERMINAL_WIDTH
    )
    return _TerminalConfiguration(
        input_descriptor=input_descriptor,
        original_attributes=original_attributes,
        editor_attributes=editor_attributes,
        width=width,
        eof_character=eof_character,
        interrupt_character=interrupt_character,
        termios_module=termios_module,
    )


def _stream_descriptor(stream: TextIO) -> int:
    """Return one nonnegative integer descriptor without touching its stream.

    Invalid descriptor values raise before terminal mutation, allowing the
    owning public reader to use its established fallback safely.
    """

    descriptor = stream.fileno()
    if (
        not isinstance(descriptor, int)
        or isinstance(descriptor, bool)
        or descriptor < 0
    ):
        raise ValueError("terminal descriptor must be a nonnegative integer")
    return descriptor


def _configured_control_character(value: object, name: str) -> str:
    """Decode one termios control character for active input comparison.

    The returned code point is not displayed. Unsupported termios shapes raise
    before mode activation so fallback can proceed without cleanup.
    """

    if isinstance(value, bytes) and len(value) == 1:
        return value.decode("latin-1")
    if isinstance(value, str) and len(value) == 1:
        return value
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 255:
        return chr(value)
    raise ValueError(f"{name} must be one terminal control character")


def _activate_terminal(configuration: _TerminalConfiguration) -> None:
    """Apply one prepared noncanonical/no-echo input mode without reading.

    Setup failure propagates to the owner, which restores the captured original
    attributes before deciding whether fallback is still safe.
    """

    termios_module = configuration.termios_module
    termios_module.tcsetattr(
        configuration.input_descriptor,
        termios_module.TCSANOW,
        configuration.editor_attributes,
    )


def _restore_terminal_or_raise(
    configuration: _TerminalConfiguration,
    active_failure: BaseException | None,
) -> None:
    """Restore exact captured attributes or raise fatal cleanup uncertainty.

    Restoration is attempted once for normal, setup-failure, and active-failure
    paths. Its failure takes precedence over ``active_failure`` and is exposed
    only through fixed local diagnostics; no stream is closed here.
    """

    termios_module = configuration.termios_module
    try:
        termios_module.tcsetattr(
            configuration.input_descriptor,
            termios_module.TCSADRAIN,
            configuration.original_attributes,
        )
    except BaseException as restoration_failure:
        fatal = RuntimeError("interactive terminal restoration failed")
        if active_failure is not None:
            fatal.add_note(
                "Restoration failed while another interactive input failure "
                "was active."
            )
        raise fatal from restoration_failure


def _consume_escape_character(
    state: _LiveEditorState,
    character: str,
) -> tuple[KeyEvent | None, bool]:
    """Consume one escape-sequence code point into a normalized key decision.

    The returned pair is ``(event, reprocess)``. Known CSI and application
    sequences become exact key kinds. Unknown sequence suffixes are discarded;
    a standalone Escape before a non-sequence character becomes an Escape
    event and the following character is reprocessed normally.
    """

    sequence = state.escape_sequence
    assert sequence is not None

    if sequence == "\x1b":
        if character in {"[", "O"}:
            state.escape_sequence = sequence + character
            return None, False
        state.escape_sequence = None
        return KeyEvent(KeyKind.ESCAPE), True

    sequence += character
    state.escape_sequence = sequence
    if ord(character) < 0x20 or character == "\x7f":
        state.escape_sequence = None
        return None, True

    if sequence.startswith("\x1bO"):
        state.escape_sequence = None
        return _escape_event(sequence), False

    if "@" <= character <= "~":
        state.escape_sequence = None
        return _escape_event(sequence), False
    if len(sequence) >= _MAX_ESCAPE_SEQUENCE_LENGTH:
        # Keep consuming the unknown CSI until its final byte so no suffix can
        # leak into the submitted buffer.
        state.escape_sequence = "\x1b["
    return None, False


def _escape_event(sequence: str) -> KeyEvent | None:
    """Map supported fixed cursor sequences to normalized key events.

    Unknown sequences return ``None`` and never reach the buffer. The pure
    mapping emits no terminal output and owns no cleanup.
    """

    kinds = {
        "\x1b[A": KeyKind.UP,
        "\x1b[B": KeyKind.DOWN,
        "\x1b[C": KeyKind.RIGHT,
        "\x1b[D": KeyKind.LEFT,
        "\x1b[H": KeyKind.HOME,
        "\x1b[F": KeyKind.END,
        "\x1b[1~": KeyKind.HOME,
        "\x1b[7~": KeyKind.HOME,
        "\x1b[4~": KeyKind.END,
        "\x1b[8~": KeyKind.END,
        "\x1b[3~": KeyKind.DELETE,
        "\x1b[Z": KeyKind.SHIFT_TAB,
        "\x1bOA": KeyKind.UP,
        "\x1bOB": KeyKind.DOWN,
        "\x1bOC": KeyKind.RIGHT,
        "\x1bOD": KeyKind.LEFT,
        "\x1bOH": KeyKind.HOME,
        "\x1bOF": KeyKind.END,
    }
    try:
        return KeyEvent(kinds[sequence])
    except KeyError:
        return None


def _key_event(
    character: str,
    configuration: _TerminalConfiguration,
) -> KeyEvent:
    """Normalize one non-escape terminal character without external effects."""

    if character in {"\r", "\n"}:
        return KeyEvent(KeyKind.ENTER)
    if character == configuration.interrupt_character:
        return KeyEvent(KeyKind.INTERRUPT)
    if character == configuration.eof_character:
        return KeyEvent(KeyKind.EOF)
    if character in {"\x08", "\x7f"}:
        return KeyEvent(KeyKind.BACKSPACE)
    if character == "\t":
        return KeyEvent(KeyKind.TAB)
    return KeyEvent(KeyKind.TEXT, text=character)


def _apply_control_event(
    state: _ControlState,
    event: KeyEvent,
    transition: _ControlTransition,
) -> tuple[_ControlState, ControlOutcome | None, bool]:
    """Apply one pure transition and report whether its visible line changed."""

    previous_line = _control_line_state(state)
    updated, outcome = transition(state, event)
    if type(updated) is not type(state):
        raise TypeError("transition must preserve the input control state type")
    if outcome is not None and type(outcome) is not ControlOutcome:
        raise TypeError("transition outcome must be a ControlOutcome or None")
    return updated, outcome, _control_line_state(updated) != previous_line


def _control_line_state(state: _ControlState) -> LineInputState:
    """Return the sole immutable editable line from one logical control."""

    if type(state) is LineInputState:
        return state
    if type(state) is MainInputControlState:
        return state.line_state
    raise TypeError("state must be an input control state")


def _completed_text_or_raise(outcome: ControlOutcome | None) -> str | None:
    """Return committed text or raise the established terminal event."""

    if outcome is None:
        return None
    if outcome.kind is ControlOutcomeKind.COMMIT:
        assert outcome.text is not None
        return outcome.text
    if outcome.kind is ControlOutcomeKind.INTERRUPT:
        raise KeyboardInterrupt
    if outcome.kind is ControlOutcomeKind.EOF:
        raise EOFError
    raise ValueError(f"unsupported control outcome: {outcome.kind.value}")


def _wrapped_editor_input(
    prompt: str,
    buffer: str,
    cursor: int,
    width: int,
) -> tuple[list[str], int, int]:
    """Wrap sanitized prompt/input and locate the code-point cursor in cells.

    ``buffer`` is not normalized or mutated. The result contains safe display
    lines plus a zero-based row and column for fixed-control cursor placement;
    no terminal or external resource is accessed.
    """

    if cursor < 0 or cursor > len(buffer):
        raise ValueError("cursor must identify a buffer boundary")
    safe_prompt = _editor_display_text(prompt)
    safe_before_cursor = "".join(
        _editor_display_text(character) for character in buffer[:cursor]
    )
    safe_after_cursor = "".join(
        _editor_display_text(character) for character in buffer[cursor:]
    )
    safe_text = safe_prompt + safe_before_cursor + safe_after_cursor
    cursor_offset = len(safe_prompt) + len(safe_before_cursor)
    return _wrap_safe_text_with_cursor(safe_text, cursor_offset, width)


def _wrap_editor_text(text: str, width: int) -> list[str]:
    """Return sanitized display lines whose ordinary cell width is bounded.

    Newlines and other controls become visible escapes. The pure wrapper uses
    standard-library Unicode metadata only and performs no output or mutation.
    """

    safe_text = _editor_display_text(text)
    lines, _row, _column = _wrap_safe_text_with_cursor(
        safe_text,
        len(safe_text),
        width,
    )
    return lines


def _wrap_safe_text_with_cursor(
    safe_text: str,
    cursor_offset: int,
    width: int,
) -> tuple[list[str], int, int]:
    """Wrap already-sanitized text and map one string boundary to terminal cells.

    Combining marks use zero cells, East Asian wide/fullwidth characters use
    two, and other visible characters use one. The function is deterministic,
    standard-library-only, and side-effect free.
    """

    if not isinstance(width, int) or isinstance(width, bool) or width <= 0:
        raise ValueError("width must be a positive integer")
    if cursor_offset < 0 or cursor_offset > len(safe_text):
        raise ValueError("cursor_offset must identify a text boundary")

    lines = [""]
    line_widths = [0]
    cursor_position: tuple[int, int] | None = None
    for index, character in enumerate(safe_text):
        character_width = _display_character_width(character)
        if line_widths[-1] and line_widths[-1] + character_width > width:
            lines.append("")
            line_widths.append(0)
        if index == cursor_offset:
            cursor_position = (len(lines) - 1, line_widths[-1])
        lines[-1] += character
        line_widths[-1] += character_width
    if cursor_position is None:
        cursor_position = (len(lines) - 1, line_widths[-1])
    return lines, cursor_position[0], cursor_position[1]


def _editor_display_text(text: str) -> str:
    """Return one terminal-safe display value with line breaks made visible.

    The original text remains untouched for submission. Sanitization performs
    no I/O and ensures untrusted controls cannot become redraw instructions.
    """

    return sanitize_terminal_text(text).replace("\n", "\\x0a")


def _display_character_width(character: str) -> int:
    """Return deterministic terminal cells for one sanitized display character.

    Combining/enclosing marks occupy zero cells, East Asian wide/fullwidth
    characters occupy two, and every other safe visible character occupies one.
    The helper performs no I/O and adds no grapheme-cluster dependency.
    """

    return display_character_width(character)


__all__ = [
    "RawTerminalDriver",
    "detect_terminal_capabilities",
]
