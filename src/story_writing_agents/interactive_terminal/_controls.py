"""Pure foundational control transitions for interactive terminal input."""

from __future__ import annotations

from ._model import (
    ControlOutcome,
    ControlOutcomeKind,
    KeyEvent,
    KeyKind,
    LineInputState,
    SlashCommandSuggestion,
)


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


__all__ = ["transition_line_input", "project_slash_suggestions"]
