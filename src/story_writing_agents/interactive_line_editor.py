"""Compatibility facade for the unified interactive terminal line editor."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TextIO

from .interactive_commands import COMMANDS
from .interactive_terminal import (
    RawTerminalDriver,
    SlashCommandSuggestion,
    project_slash_suggestions,
    transition_line_input,
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


class InteractiveMainLineEditor:
    """Read one editable main-prompt line through the unified raw driver."""

    __slots__ = ("_driver",)

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

        self._driver = RawTerminalDriver(
            fallback_read_line,
            input_stream,
            output_stream,
            slash_command_suggestions,
            transition_line_input,
            termios_provider=lambda: _termios,
            os_provider=lambda: os,
        )

    def read_line(self, prompt: str) -> str:
        """Read and return one exact Unicode main-input buffer."""

        return self._driver.read_main_line(prompt)


SlashCommandSuggestion.__module__ = __name__
InteractiveMainLineEditor.__module__ = __name__


__all__ = [
    "SlashCommandSuggestion",
    "slash_command_suggestions",
    "InteractiveMainLineEditor",
]
