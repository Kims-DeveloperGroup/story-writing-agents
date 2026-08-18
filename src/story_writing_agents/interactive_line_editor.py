"""Live, registry-backed editing for the interactive main prompt."""

from __future__ import annotations

import os
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TextIO

from .interactive_commands import COMMANDS
from .interactive_terminal import sanitize_terminal_text

try:
    import termios as _termios
except ImportError:  # pragma: no cover - exercised on non-POSIX hosts
    _termios = None


_MAIN_PROMPT_EXCLUSIONS = frozenset({"/compose", "/send", "/cancel"})
_COMPOSITION_SUGGESTION_TOKENS = ("/send", "/cancel")
_SUGGESTION_HEADER = "[suggestions]"
_CARRIAGE_RETURN = "\r"
_CLEAR_TO_END = "\x1b[J"
_CURSOR_UP = "\x1b[A"
_CURSOR_RIGHT = "\x1b[C"
_DEFAULT_TERMINAL_WIDTH = 80
_MAX_ESCAPE_SEQUENCE_LENGTH = 64


@dataclass(frozen=True, slots=True)
class SlashCommandSuggestion:
    """Identify one displayed slash token and its canonical registry token."""

    token: str
    canonical_token: str

    def __post_init__(self) -> None:
        """Validate slash-token identity without I/O, mutation, or cleanup.

        Both values are preserved exactly. Invalid types or non-slash values
        raise before the immutable suggestion can cross the editor boundary.
        """

        for name, value in (
            ("token", self.token),
            ("canonical_token", self.canonical_token),
        ):
            if not isinstance(value, str):
                raise TypeError(f"{name} must be a string")
            if not value.startswith("/"):
                raise ValueError(f"{name} must begin with a slash")


def slash_command_suggestions(
    buffer: str,
) -> tuple[SlashCommandSuggestion, ...]:
    """Project exact main-prompt prefix matches from ``COMMANDS``.

    ``buffer`` is matched whole, case-sensitively, without parsing or mutation.
    Non-leading slash input, whitespace, and no-match input return an empty
    tuple. The pure projection performs no terminal, provider, or storage I/O.
    """

    if not isinstance(buffer, str):
        raise TypeError("buffer must be a string")
    if not buffer.startswith("/") or any(
        character.isspace() for character in buffer
    ):
        return ()

    matches: list[SlashCommandSuggestion] = []
    for spec in COMMANDS:
        canonical_token = spec.syntax.split(maxsplit=1)[0]
        if canonical_token in _MAIN_PROMPT_EXCLUSIONS:
            continue
        if canonical_token.startswith(buffer):
            matches.append(
                SlashCommandSuggestion(canonical_token, canonical_token)
            )
        for alias in spec.aliases:
            if alias.startswith(buffer):
                matches.append(SlashCommandSuggestion(alias, canonical_token))
    return tuple(matches)


def composition_slash_command_suggestions(
    buffer: str,
) -> tuple[SlashCommandSuggestion, ...]:
    """Project the exact ordered composition controls for one input buffer.

    Matching is whole-buffer, case-sensitive, and prefix-only. Non-leading
    slash input, whitespace, and no-match input return an empty tuple. The pure
    projection performs no parser, terminal, provider, or storage operation.
    """

    if not isinstance(buffer, str):
        raise TypeError("buffer must be a string")
    if not buffer.startswith("/") or any(
        character.isspace() for character in buffer
    ):
        return ()
    return tuple(
        SlashCommandSuggestion(token, token)
        for token in _COMPOSITION_SUGGESTION_TOKENS
        if token.startswith(buffer)
    )


@dataclass(slots=True)
class _LiveEditorState:
    """Retain one call-local Unicode buffer, cursor, and redraw position."""

    buffer: list[str] = field(default_factory=list)
    cursor: int = 0
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


class InteractiveMainLineEditor:
    """Read editable lines with main defaults and safe line-input fallback.

    Public ``read_line`` preserves the main-prompt provider. The internal
    binding seam supplies a call-local provider for composition reads.
    """

    __slots__ = ("_fallback_read_line", "_input_stream", "_output_stream")

    def __init__(
        self,
        fallback_read_line: Callable[[str], str],
        input_stream: TextIO,
        output_stream: TextIO,
    ) -> None:
        """Retain borrowed fallback and terminal stream collaborators.

        Construction performs no stream access or terminal mutation. Missing
        live-terminal capabilities are resolved by ``read_line`` at call time;
        the fallback callback remains responsible for its own I/O lifecycle.
        """

        if not callable(fallback_read_line):
            raise TypeError("fallback_read_line must be callable")
        self._fallback_read_line = fallback_read_line
        self._input_stream = input_stream
        self._output_stream = output_stream

    def read_line(self, prompt: str) -> str:
        """Read and return one exact Unicode main-input buffer.

        A capable POSIX terminal is placed in call-local noncanonical/no-echo
        mode and restored after submission or every failure. Unsupported or
        safely recoverable setup uses the borrowed fallback before consuming
        input. Active failures propagate after cleanup; uncertain restoration
        raises a fatal fixed ``RuntimeError`` instead.
        """

        return self._read_line(prompt, slash_command_suggestions)

    def _read_line(
        self,
        prompt: str,
        suggestion_provider: Callable[
            [str], tuple[SlashCommandSuggestion, ...]
        ],
    ) -> str:
        """Read one line using an explicit call-local suggestion provider.

        The provider affects only transient redraws for this call and is never
        retained by the editor. Terminal capability, fallback, cleanup, and
        restoration behavior remain owned by this raw-input boundary.
        """

        if not isinstance(prompt, str):
            raise TypeError("prompt must be a string")
        if not callable(suggestion_provider):
            raise TypeError("suggestion_provider must be callable")

        try:
            configuration = _terminal_configuration(
                self._input_stream,
                self._output_stream,
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

        state = _LiveEditorState()
        line: str | None = None
        active_failure: BaseException | None = None
        try:
            line = self._read_live_line(
                prompt,
                configuration,
                state,
                suggestion_provider,
            )
            self._finish_live_display(prompt, configuration.width, state)
        except BaseException as failure:
            active_failure = failure
            try:
                self._clear_live_display(state, line_break=True)
            except BaseException:
                # The active failure remains authoritative when display cleanup
                # cannot use the same failed output collaborator.
                pass

        _restore_terminal_or_raise(configuration, active_failure)
        if active_failure is not None:
            raise active_failure
        assert line is not None
        return line

    def _read_live_line(
        self,
        prompt: str,
        configuration: _TerminalConfiguration,
        state: _LiveEditorState,
        suggestion_provider: Callable[
            [str], tuple[SlashCommandSuggestion, ...]
        ],
    ) -> str:
        """Edit one code-point buffer until submit, interrupt, EOF, or failure.

        The method borrows the active terminal configuration and mutates only
        ``state``. Stream failures propagate to ``read_line`` for display and
        attribute cleanup; no parser, provider, or persistence call occurs.
        """

        state.rendered_cursor_row = self._redraw_live_display(
            prompt,
            configuration.width,
            state,
            suggestion_provider,
        )
        while True:
            character = self._read_character()

            if state.escape_sequence is not None:
                action, reprocess = _consume_escape_character(
                    state,
                    character,
                )
                if action is not None:
                    changed = _apply_editor_action(state, action)
                    if changed:
                        state.rendered_cursor_row = self._redraw_live_display(
                            prompt,
                            configuration.width,
                            state,
                            suggestion_provider,
                        )
                if not reprocess:
                    continue

            if character == "\x1b":
                state.escape_sequence = character
                continue
            if character in {"\r", "\n"}:
                return "".join(state.buffer)
            if character == configuration.interrupt_character:
                raise KeyboardInterrupt
            if character == configuration.eof_character:
                if not state.buffer:
                    raise EOFError
                return "".join(state.buffer)
            if character in {"\x08", "\x7f"}:
                changed = _apply_editor_action(state, "backspace")
            else:
                state.buffer.insert(state.cursor, character)
                state.cursor += 1
                changed = True
            if changed:
                state.rendered_cursor_row = self._redraw_live_display(
                    prompt,
                    configuration.width,
                    state,
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
        state: _LiveEditorState,
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
            state.buffer,
            state.cursor,
            render_width,
        )
        suggestions = suggestion_provider("".join(state.buffer))
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
        move_to_top = _CURSOR_UP * state.rendered_cursor_row
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
        state: _LiveEditorState,
    ) -> None:
        """Remove suggestions and leave the submitted prompt line visible.

        The exact buffer is not changed; only its sanitized width-wrapped view
        is written. Output failure propagates while terminal restoration remains
        owned by ``read_line``.
        """

        render_width = max(1, width - 1)
        input_lines, _cursor_row, _cursor_column = _wrapped_editor_input(
            prompt,
            state.buffer,
            len(state.buffer),
            render_width,
        )
        move_to_top = _CURSOR_UP * state.rendered_cursor_row
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
        state.rendered_cursor_row = 0

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

        written = self._output_stream.write(text)
        if written is not None and written != len(text):
            raise OSError("interactive terminal write was incomplete")
        self._output_stream.flush()


def bind_suggestion_reader(
    editor: InteractiveMainLineEditor,
    provider: Callable[[str], tuple[SlashCommandSuggestion, ...]],
) -> Callable[[str], str]:
    """Bind one editor to an explicit provider without exposing its internals.

    The returned callback chooses the provider independently for every read;
    the editor retains no mode or provider state. Binding performs no stream
    access, terminal mutation, or input consumption.
    """

    reader = getattr(editor, "_read_line", None)
    if not callable(reader):
        raise TypeError("editor must support suggestion-bound reads")
    if not callable(provider):
        raise TypeError("provider must be callable")

    def read_line(prompt: str) -> str:
        return reader(prompt, provider)

    return read_line


def _terminal_configuration(
    input_stream: TextIO,
    output_stream: TextIO,
) -> _TerminalConfiguration | None:
    """Return a complete POSIX live-editor setup or ``None`` for fallback.

    Capability inspection consumes no input and performs no output or terminal
    mutation. Ordinary descriptor, TTY, environment, or attribute failures are
    allowed to propagate to the public reader's safe fallback decision.
    """

    if _termios is None or os.name != "posix":
        return None
    terminal_name = os.environ.get("TERM", "")
    if not terminal_name or terminal_name.casefold() == "dumb":
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
    original_attributes = _termios.tcgetattr(input_descriptor)
    editor_attributes = list(original_attributes)
    editor_attributes[6] = list(original_attributes[6])
    editor_attributes[3] &= ~(_termios.ICANON | _termios.ECHO)
    editor_attributes[6][_termios.VMIN] = 1
    editor_attributes[6][_termios.VTIME] = 0
    eof_character = _configured_control_character(
        original_attributes[6][_termios.VEOF],
        "VEOF",
    )
    interrupt_character = _configured_control_character(
        original_attributes[6][_termios.VINTR],
        "VINTR",
    )
    columns = os.get_terminal_size(output_descriptor).columns
    width = columns if columns > 0 else _DEFAULT_TERMINAL_WIDTH
    return _TerminalConfiguration(
        input_descriptor=input_descriptor,
        original_attributes=original_attributes,
        editor_attributes=editor_attributes,
        width=width,
        eof_character=eof_character,
        interrupt_character=interrupt_character,
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

    assert _termios is not None
    _termios.tcsetattr(
        configuration.input_descriptor,
        _termios.TCSANOW,
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

    assert _termios is not None
    try:
        _termios.tcsetattr(
            configuration.input_descriptor,
            _termios.TCSADRAIN,
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
) -> tuple[str | None, bool]:
    """Consume one escape-sequence code point into an editor action decision.

    The returned pair is ``(action, reprocess)``. Unknown, up, and down
    sequences are discarded; terminal-control characters may be reprocessed by
    the ordinary input path. No buffer character is inserted here.
    """

    sequence = state.escape_sequence
    assert sequence is not None

    if sequence == "\x1b":
        if character in {"[", "O"}:
            state.escape_sequence = sequence + character
            return None, False
        state.escape_sequence = None
        return None, ord(character) < 0x20 or character == "\x7f"

    sequence += character
    state.escape_sequence = sequence
    if ord(character) < 0x20 or character == "\x7f":
        state.escape_sequence = None
        return None, True

    if sequence.startswith("\x1bO"):
        state.escape_sequence = None
        return _escape_action(sequence), False

    if "@" <= character <= "~":
        state.escape_sequence = None
        return _escape_action(sequence), False
    if len(sequence) >= _MAX_ESCAPE_SEQUENCE_LENGTH:
        # Keep consuming the unknown CSI until its final byte so no suffix can
        # leak into the submitted buffer.
        state.escape_sequence = "\x1b["
    return None, False


def _escape_action(sequence: str) -> str | None:
    """Map supported fixed cursor sequences to internal editor action names.

    Unknown, up, and down sequences return ``None`` and never reach the buffer.
    The pure mapping emits no terminal output and owns no cleanup.
    """

    return {
        "\x1b[D": "left",
        "\x1b[C": "right",
        "\x1b[H": "home",
        "\x1b[F": "end",
        "\x1b[1~": "home",
        "\x1b[7~": "home",
        "\x1b[4~": "end",
        "\x1b[8~": "end",
        "\x1b[3~": "delete",
        "\x1bOH": "home",
        "\x1bOF": "end",
    }.get(sequence)


def _apply_editor_action(state: _LiveEditorState, action: str) -> bool:
    """Apply one trusted cursor/deletion action and report visible change.

    Buffer mutation removes at most one Python code point. Unknown action names
    raise before mutation; the function performs no I/O or resource cleanup.
    """

    if action == "left":
        if state.cursor == 0:
            return False
        state.cursor -= 1
        return True
    if action == "right":
        if state.cursor == len(state.buffer):
            return False
        state.cursor += 1
        return True
    if action == "home":
        if state.cursor == 0:
            return False
        state.cursor = 0
        return True
    if action == "end":
        if state.cursor == len(state.buffer):
            return False
        state.cursor = len(state.buffer)
        return True
    if action == "backspace":
        if state.cursor == 0:
            return False
        del state.buffer[state.cursor - 1]
        state.cursor -= 1
        return True
    if action == "delete":
        if state.cursor == len(state.buffer):
            return False
        del state.buffer[state.cursor]
        return True
    raise ValueError(f"unsupported editor action: {action}")


def _wrapped_editor_input(
    prompt: str,
    buffer: list[str],
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

    if unicodedata.combining(character) or unicodedata.category(character) in {
        "Cf",
        "Mn",
        "Me",
    }:
        return 0
    if unicodedata.east_asian_width(character) in {"W", "F"}:
        return 2
    return 1


__all__ = [
    "SlashCommandSuggestion",
    "slash_command_suggestions",
    "InteractiveMainLineEditor",
]
