"""Pure foundational control transitions for interactive terminal input."""

from __future__ import annotations

from ._model import (
    ControlOutcome,
    ControlOutcomeKind,
    KeyEvent,
    KeyKind,
    LineInputState,
    MainInputControlState,
    MainInputHistoryState,
    SlashCommandSuggestion,
)


_COMPOSITION_SUGGESTION_TOKENS = ("/send", "/cancel")
_MAIN_INPUT_HISTORY_LIMIT = 100


def transition_line_input(
    state: LineInputState,
    event: KeyEvent,
) -> tuple[LineInputState, ControlOutcome | None]:
    """Apply one normalized editing key without I/O, parsing, or persistence.

    History, palette navigation, and picker behavior are deliberately absent
    from this foundation. Their reserved keys leave the state unchanged until
    their owning delivery slices extend the pure control boundary.
    """

    if type(state) is not LineInputState:
        raise TypeError("state must be a LineInputState")
    if type(event) is not KeyEvent:
        raise TypeError("event must be a KeyEvent")

    buffer = state.buffer
    cursor = state.cursor
    kind = event.kind
    if kind is KeyKind.TEXT:
        assert event.text is not None
        return (
            LineInputState(
                f"{buffer[:cursor]}{event.text}{buffer[cursor:]}",
                cursor + 1,
            ),
            None,
        )
    if kind is KeyKind.BACKSPACE:
        if cursor == 0:
            return state, None
        return (
            LineInputState(
                f"{buffer[: cursor - 1]}{buffer[cursor:]}",
                cursor - 1,
            ),
            None,
        )
    if kind is KeyKind.DELETE:
        if cursor == len(buffer):
            return state, None
        return LineInputState(f"{buffer[:cursor]}{buffer[cursor + 1:]}", cursor), None
    if kind is KeyKind.LEFT:
        return (LineInputState(buffer, max(0, cursor - 1)), None)
    if kind is KeyKind.RIGHT:
        return (LineInputState(buffer, min(len(buffer), cursor + 1)), None)
    if kind is KeyKind.HOME:
        return LineInputState(buffer, 0), None
    if kind is KeyKind.END:
        return LineInputState(buffer, len(buffer)), None
    if kind is KeyKind.ENTER:
        return state, ControlOutcome(ControlOutcomeKind.COMMIT, text=buffer)
    if kind is KeyKind.INTERRUPT:
        return state, ControlOutcome(ControlOutcomeKind.INTERRUPT)
    if kind is KeyKind.EOF:
        if buffer:
            return state, ControlOutcome(ControlOutcomeKind.COMMIT, text=buffer)
        return state, ControlOutcome(ControlOutcomeKind.EOF)
    return state, None


def append_main_input_history(
    history: MainInputHistoryState,
    text: str,
) -> MainInputHistoryState:
    """Append one exact nonblank main commit with oldest-first eviction."""

    if type(history) is not MainInputHistoryState:
        raise TypeError("history must be a MainInputHistoryState")
    if type(text) is not str:
        raise TypeError("text must be a string")
    if not text or text.isspace():
        return history
    entries = (*history.entries, text)
    return MainInputHistoryState(entries[-_MAIN_INPUT_HISTORY_LIMIT:])


def transition_main_input(
    state: MainInputControlState,
    event: KeyEvent,
    history: MainInputHistoryState,
) -> tuple[MainInputControlState, ControlOutcome | None]:
    """Apply one main-input key with pure history and overlay routing."""

    if type(state) is not MainInputControlState:
        raise TypeError("state must be a MainInputControlState")
    if type(event) is not KeyEvent:
        raise TypeError("event must be a KeyEvent")
    if type(history) is not MainInputHistoryState:
        raise TypeError("history must be a MainInputHistoryState")
    if state.history_index is not None and state.history_index >= len(history.entries):
        raise ValueError("history_index must identify a stored history entry")

    if event.kind in {KeyKind.UP, KeyKind.DOWN}:
        if state.overlay_open:
            return state, None
        if event.kind is KeyKind.UP:
            if not history.entries:
                return state, None
            if state.history_index == 0:
                return state, None
            history_index = (
                len(history.entries) - 1
                if state.history_index is None
                else max(0, state.history_index - 1)
            )
            saved_draft = (
                state.line_state
                if state.saved_draft is None
                else state.saved_draft
            )
            recalled = history.entries[history_index]
            return (
                MainInputControlState(
                    LineInputState(recalled, len(recalled)),
                    history_index,
                    saved_draft,
                    overlay_open=state.overlay_open,
                ),
                None,
            )
        if state.history_index is None:
            return state, None
        if state.history_index == len(history.entries) - 1:
            assert state.saved_draft is not None
            return (
                MainInputControlState(
                    state.saved_draft,
                    overlay_open=state.overlay_open,
                ),
                None,
            )
        history_index = state.history_index + 1
        recalled = history.entries[history_index]
        return (
            MainInputControlState(
                LineInputState(recalled, len(recalled)),
                history_index,
                state.saved_draft,
                overlay_open=state.overlay_open,
            ),
            None,
        )

    line_state, outcome = transition_line_input(state.line_state, event)
    if line_state is state.line_state:
        return state, outcome
    return (
        MainInputControlState(
            line_state,
            state.history_index,
            state.saved_draft,
            overlay_open=state.overlay_open,
        ),
        outcome,
    )


def project_slash_suggestions(
    buffer: str,
    command_tokens: tuple[tuple[str, tuple[str, ...]], ...],
    *,
    exclusions: frozenset[str],
) -> tuple[SlashCommandSuggestion, ...]:
    """Project exact case-sensitive prefix matches from supplied metadata.

    The caller remains the owner of canonical command metadata. This pure
    projection does not parse the buffer and has no provider, filesystem,
    terminal, lifecycle, or persistence dependency.
    """

    if not isinstance(buffer, str):
        raise TypeError("buffer must be a string")
    if type(command_tokens) is not tuple:
        raise TypeError("command_tokens must be a tuple")
    if not isinstance(exclusions, frozenset) or any(
        type(value) is not str for value in exclusions
    ):
        raise TypeError("exclusions must be a frozenset of strings")
    if not buffer.startswith("/") or any(
        character.isspace() for character in buffer
    ):
        return ()

    matches: list[SlashCommandSuggestion] = []
    for entry in command_tokens:
        if (
            type(entry) is not tuple
            or len(entry) != 2
            or type(entry[0]) is not str
            or type(entry[1]) is not tuple
            or any(type(alias) is not str for alias in entry[1])
        ):
            raise TypeError("each command token entry must contain a token and aliases")
        canonical_token, aliases = entry
        if canonical_token in exclusions:
            continue
        if canonical_token.startswith(buffer):
            matches.append(
                SlashCommandSuggestion(canonical_token, canonical_token)
            )
        for alias in aliases:
            if alias.startswith(buffer):
                matches.append(SlashCommandSuggestion(alias, canonical_token))
    return tuple(matches)


def project_composition_suggestions(
    buffer: str,
) -> tuple[SlashCommandSuggestion, ...]:
    """Project the fixed ordered composition controls for one input buffer.

    Matching is whole-buffer, case-sensitive, and prefix-only. This pure
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


__all__ = [
    "transition_line_input",
    "append_main_input_history",
    "transition_main_input",
    "project_slash_suggestions",
    "project_composition_suggestions",
]
