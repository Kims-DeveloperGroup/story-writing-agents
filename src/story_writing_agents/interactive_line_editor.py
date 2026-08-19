"""Compatibility facade for the unified interactive terminal line editor."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TextIO

from .interactive_commands import COMMANDS
from .interactive_terminal import (
    RawTerminalDriver,
    SlashCommandSuggestion,
    append_main_input_history,
    project_composition_suggestions,
    project_slash_suggestions,
    transition_line_input,
    transition_main_input,
)

try:
    import termios as _termios
except ImportError:  # pragma: no cover - exercised on non-POSIX hosts
    _termios = None


_MAIN_PROMPT_EXCLUSIONS = frozenset({"/compose", "/send", "/cancel"})


def slash_command_suggestions(
    buffer: str,
) -> tuple[SlashCommandSuggestion, ...]:
    """Project exact main-prompt prefix matches from ``COMMANDS``.

    ``buffer`` is matched whole, case-sensitively, without parsing or mutation.
    Non-leading slash input, whitespace, and no-match input return an empty
    tuple. The pure projection performs no terminal, provider, or storage I/O.
    """

    command_tokens = tuple(
        (spec.syntax.split(maxsplit=1)[0], spec.aliases) for spec in COMMANDS
    )
    return project_slash_suggestions(
        buffer,
        command_tokens,
        exclusions=_MAIN_PROMPT_EXCLUSIONS,
    )


def composition_slash_command_suggestions(
    buffer: str,
) -> tuple[SlashCommandSuggestion, ...]:
    """Project the exact ordered composition controls for one input buffer."""

    return project_composition_suggestions(buffer)


class InteractiveMainLineEditor:
    """Read contextual editable lines through the unified raw driver."""

    __slots__ = ("_driver",)

    def __init__(
        self,
        fallback_read_line: Callable[[str], str],
        input_stream: TextIO,
        output_stream: TextIO,
    ) -> None:
        """Retain borrowed fallback and terminal stream collaborators.

        Construction performs no stream access or terminal mutation. Missing
        live-terminal capabilities are resolved by each contextual read; the
        fallback callback remains responsible for its own I/O lifecycle.
        """

        self._driver = RawTerminalDriver(
            fallback_read_line,
            input_stream,
            output_stream,
            transition_line_input,
            transition_main_input,
            append_main_input_history,
            termios_provider=lambda: _termios,
            os_provider=lambda: os,
        )

    def read_line(self, prompt: str) -> str:
        """Read and return one exact Unicode main-input buffer."""

        return self._driver.read_main_line(prompt, slash_command_suggestions)

    def _read_line(
        self,
        prompt: str,
        suggestion_provider: Callable[
            [str], tuple[SlashCommandSuggestion, ...]
        ],
    ) -> str:
        """Read one line using an explicit call-local suggestion provider."""

        return self._driver.read_live_line(prompt, suggestion_provider)


def bind_suggestion_reader(
    editor: InteractiveMainLineEditor,
    provider: Callable[
        [str], tuple[SlashCommandSuggestion, ...]
    ],
) -> Callable[[str], str]:
    """Bind a validated call-local provider to one compatibility editor."""

    if type(editor) is not InteractiveMainLineEditor:
        raise TypeError("editor must be an InteractiveMainLineEditor")
    if not callable(provider):
        raise TypeError("provider must be callable")
    return lambda prompt: editor._read_line(prompt, provider)


SlashCommandSuggestion.__module__ = __name__
InteractiveMainLineEditor.__module__ = __name__


__all__ = [
    "SlashCommandSuggestion",
    "slash_command_suggestions",
    "InteractiveMainLineEditor",
]
