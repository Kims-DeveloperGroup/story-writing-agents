"""Public compatibility facade for the unified interactive terminal."""

from __future__ import annotations

from ._core import (
    INTERACTIVE_ERROR_SPECS,
    AttachmentKind,
    ComposeMode,
    ConfirmationResult,
    Field,
    InteractiveError,
    InteractiveErrorCode,
    InteractiveErrorSpec,
    InteractiveSemanticOutput,
    InteractiveTerminal,
    ListView,
    MainInputControlState,
    MainInputHistoryState,
    Message,
    Notice,
    Panel,
    Record,
    Section,
    SemanticRole,
    TerminalAction,
    TerminalActionKind,
    TerminalMessageKind,
    ViewState,
    RawTerminalDriver,
    SlashCommandSuggestion,
    append_main_input_history,
    build_contextual_terminal,
    build_stream_terminal,
    project_composition_suggestions,
    project_slash_suggestions,
    sanitize_terminal_text,
    transition_line_input,
    transition_main_input,
)


_PUBLIC_TYPES = (
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
for _public_type in _PUBLIC_TYPES:
    _public_type.__module__ = __name__
sanitize_terminal_text.__module__ = __name__
del _public_type


__all__ = [
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
]
