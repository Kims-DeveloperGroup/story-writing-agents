"""Safe, deterministic presentation primitives for the interactive terminal."""

from __future__ import annotations

import json
import re
import textwrap
import time
import unicodedata
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import KW_ONLY, dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TextIO

from ..agents import AGENT_SPECS
from ..interactive_commands import (
    AGENT_ROUTES,
    COMMANDS,
    CommandSpec,
    project_command_completion,
)
from ._controls import (
    append_main_input_history,
    completion_candidate_matches,
    initialize_picker,
    main_command_uses_choices,
    project_main_choice_completion,
    project_main_choice_palette,
    project_picker_viewport,
    project_composition_suggestions,
    project_main_suggestion_palette,
    project_slash_suggestions,
    ranged_artifact_picker_selection,
    transition_composer_line,
    transition_confirmation_answer,
    transition_main_input as transition_main_input,
    transition_main_command_input,
    transition_picker,
    transition_picker_fallback,
    transition_line_input,
    transition_prefilled_prompt_input,
    transition_prompt_input,
)
from ._driver import (
    AdvisoryProgressError as AdvisoryProgressError,
    RawTerminalDriver,
    ReferenceInputBindings,
)
from ._model import (
    CapabilityProfile,
    Choice,
    CommandCompletionCandidate,
    CommandCompletionProjection,
    ComposerState,
    Component,
    ControlOutcome,
    ControlOutcomeKind,
    Field,
    InputPromptView,
    ListView,
    MainCommandControlState,
    MainInputControlState as MainInputControlState,
    MainInputHistoryState as MainInputHistoryState,
    Message,
    Notice,
    Panel,
    Picker,
    PickerControlState,
    Prompt,
    Record,
    Section,
    SemanticRole,
    SlashCommandSuggestion,
    StartupWelcome,
    StartupWelcomeStage,
    SuggestionPalette,
    TerminalCapabilities,
    ViewState,
    sanitize_terminal_text,
)
from ._render import (
    format_agent_reply_content,
    render_component,
    render_advisory_progress_frame,
    render_input_prompt,
    render_picker_fallback,
    render_picker_viewport,
    render_startup_welcome_frame,
)
from ._reference_activation import (
    ReferenceInputFailure,
    ReferenceInputFailureKind,
    ReferenceInputProjection,
    ReferenceInputScope,
    project_reference_input,
)
from ._viewer_controls import (
    initialize_text_viewer,
    project_text_viewer_viewport,
    transition_text_viewer,
    transition_text_viewer_fallback,
)
from ._viewer_model import (
    TextViewerExitKind,
    TextViewerResult,
    TextViewerState,
    dispose_text_viewer,
)
from ._viewer_render import (
    render_text_viewer,
    render_text_viewer_fallback,
)


class AttachmentKind(StrEnum):
    """How the current process obtained a story-session attachment."""

    CREATED = "created"
    OPENED = "opened"
    REOPENED = "reopened"


class ComposeMode(StrEnum):
    """Destination of a multiline composition."""

    CHAT = "chat"
    RUN = "run"


class ConfirmationResult(StrEnum):
    """Validated answer or terminal event from one confirmation prompt."""

    YES = "yes"
    NO = "no"
    INTERRUPT = "interrupt"
    EOF = "eof"


class TerminalActionKind(StrEnum):
    """Trusted actions returned by terminal input and composition."""

    LINE = "line"
    SEND = "send"
    CANCEL = "cancel"
    INTERRUPT = "interrupt"
    EOF = "eof"


class TerminalMessageKind(StrEnum):
    """Stable prefixes for non-error terminal status records."""

    COMPOSE = "compose"
    SESSION = "session"
    CONVERSATION = "conversation"
    ROUTING = "routing"
    CONTEXT = "context"
    WAITING = "waiting"
    RESULT = "result"
    CANCELLED = "cancelled"
    HINT = "hint"


class InteractiveErrorCode(StrEnum):
    """Stable user-visible categories chosen by trusted controller code."""

    TTY_REQUIRED = "tty_required"
    WORKSPACE_NOT_INITIALIZED = "workspace_not_initialized"
    WORKSPACE_INVALID = "workspace_invalid"
    WORKSPACE_UNSAFE = "workspace_unsafe"
    WORKSPACE_VERSION_UNSUPPORTED = "workspace_version_unsupported"
    CONFIGURATION_INVALID = "configuration_invalid"
    COMMAND_INVALID = "command_invalid"
    AGENT_INVALID = "agent_invalid"
    SESSION_INVALID = "session_invalid"
    SESSION_EXISTS = "session_exists"
    SESSION_NOT_FOUND = "session_not_found"
    SESSION_NOT_ATTACHED = "session_not_attached"
    SESSION_PAUSED = "session_paused"
    SESSION_ACCESS_BLOCKED = "session_access_blocked"
    SESSION_OWNERSHIP_UNKNOWN = "session_ownership_unknown"
    CONVERSATION_REQUIRED = "conversation_required"
    CONVERSATION_INVALID = "conversation_invalid"
    CONVERSATION_NOT_FOUND = "conversation_not_found"
    CONVERSATION_AMBIGUOUS = "conversation_ambiguous"
    CONVERSATION_ARCHIVED = "conversation_archived"
    CONVERSATION_CREATE_FAILED = "conversation_create_failed"
    TRANSCRIPT_INVALID = "transcript_invalid"
    RETRIEVAL_INVALID = "retrieval_invalid"
    RETRIEVAL_BUDGET_EXHAUSTED = "retrieval_budget_exhausted"
    PROVIDER_PREFLIGHT_FAILED = "provider_preflight_failed"
    PROVIDER_EXITED = "provider_exited"
    PROVIDER_TIMED_OUT = "provider_timed_out"
    PROVIDER_PROTOCOL_ERROR = "provider_protocol_error"
    DRAFT_INPUT_INVALID = "draft_input_invalid"
    CONSENSUS_DEADLOCK = "consensus_deadlock"
    CONSENSUS_FAILED = "consensus_failed"
    ARTIFACT_CONFLICT = "artifact_conflict"
    OPERATION_CANCELLED = "operation_cancelled"
    OPERATION_INTERRUPTED = "operation_interrupted"
    CLEANUP_FAILED = "cleanup_failed"
    INTERNAL_ERROR = "internal_error"


def _validate_default_message(name: str, value: object) -> None:
    """Require one nonblank, strict-UTF-8 line of default display text."""

    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value or value.isspace():
        raise ValueError(f"{name} must not be blank")
    if "\r" in value or "\n" in value:
        raise ValueError(f"{name} must contain exactly one line")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{name} must be strict UTF-8") from exc


@dataclass(frozen=True, slots=True)
class TerminalAction:
    """One validated input or terminal composition outcome."""

    kind: TerminalActionKind
    _: KW_ONLY
    text: str | None = None
    line_count: int = 0
    character_count: int = 0

    def __post_init__(self) -> None:
        """Validate that the action payload matches its declared kind."""

        if not isinstance(self.kind, TerminalActionKind):
            raise TypeError("kind must be a TerminalActionKind")
        for name, value in (
            ("line_count", self.line_count),
            ("character_count", self.character_count),
        ):
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"{name} must be an integer")
            if value < 0:
                raise ValueError(f"{name} must not be negative")
        if self.text is not None and not isinstance(self.text, str):
            raise TypeError("text must be a string or None")

        if self.kind is TerminalActionKind.LINE:
            if self.text is None:
                raise ValueError("line actions require text")
            if "\r" in self.text or "\n" in self.text:
                raise ValueError("line action text must contain exactly one line")
            if self.line_count or self.character_count:
                raise ValueError("line actions do not carry composition counts")
            return

        if self.kind is TerminalActionKind.SEND:
            if self.text is None or not self.text or self.text.isspace():
                raise ValueError("send actions require non-blank text")
            if "\r" in self.text:
                raise ValueError("send action text must not contain carriage returns")
            expected_lines = self.text.count("\n") + 1
            if self.line_count != expected_lines:
                raise ValueError("line_count must match the submitted text")
            if self.character_count != len(self.text):
                raise ValueError("character_count must match the submitted text")
            return

        if self.text is not None:
            raise ValueError(f"{self.kind.value} actions do not carry text")
        if self.line_count == 0:
            if self.character_count != 0:
                raise ValueError("zero discarded lines require zero characters")
        elif self.character_count < self.line_count - 1:
            raise ValueError("discarded counts cannot describe joined lines")


@dataclass(frozen=True, slots=True)
class InteractiveErrorSpec:
    """Safe default copy for one stable interactive error code."""

    summary: str
    hint: str

    def __post_init__(self) -> None:
        """Validate the default summary and hint as safe single-line text."""

        _validate_default_message("summary", self.summary)
        _validate_default_message("hint", self.hint)


@dataclass(frozen=True, slots=True)
class InteractiveError:
    """A trusted error category with optional caller-supplied display copy."""

    code: InteractiveErrorCode
    _: KW_ONLY
    summary: str | None = None
    hint: str | None = None

    def __post_init__(self) -> None:
        """Require a typed code and optional string display overrides."""

        if not isinstance(self.code, InteractiveErrorCode):
            raise TypeError("code must be an InteractiveErrorCode")
        for name, value in (("summary", self.summary), ("hint", self.hint)):
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{name} must be a string or None")


INTERACTIVE_ERROR_SPECS: Mapping[InteractiveErrorCode, InteractiveErrorSpec] = (
    MappingProxyType(
        {
            InteractiveErrorCode.TTY_REQUIRED: InteractiveErrorSpec(
                "Interactive mode requires a terminal.",
                "Use story run for non-interactive automation.",
            ),
            InteractiveErrorCode.WORKSPACE_NOT_INITIALIZED: InteractiveErrorSpec(
                "No initialized story workspace was found.",
                "Run story init PATH, then try again.",
            ),
            InteractiveErrorCode.WORKSPACE_INVALID: InteractiveErrorSpec(
                "The story workspace is invalid.",
                "Inspect the reported workspace file and repair it before retrying.",
            ),
            InteractiveErrorCode.WORKSPACE_UNSAFE: InteractiveErrorSpec(
                "The requested workspace path is unsafe.",
                "Use a path inside the initialized workspace.",
            ),
            InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED: InteractiveErrorSpec(
                "This workspace uses an unsupported older session format.",
                "Use a new workspace or follow the documented manual reset procedure.",
            ),
            InteractiveErrorCode.CONFIGURATION_INVALID: InteractiveErrorSpec(
                "Runtime configuration is invalid.",
                "Correct the reported configuration value and retry.",
            ),
            InteractiveErrorCode.COMMAND_INVALID: InteractiveErrorSpec(
                "The interactive command is invalid.",
                "Type /help to see valid command syntax.",
            ),
            InteractiveErrorCode.AGENT_INVALID: InteractiveErrorSpec(
                "The requested agent is invalid.",
                "Type /agents to see available agents.",
            ),
            InteractiveErrorCode.SESSION_INVALID: InteractiveErrorSpec(
                "The story session identifier is invalid.",
                "Use letters, digits, underscores, or hyphens in a safe session name.",
            ),
            InteractiveErrorCode.SESSION_EXISTS: InteractiveErrorSpec(
                "The story session already exists.",
                "Resume it or choose a different session name.",
            ),
            InteractiveErrorCode.SESSION_NOT_FOUND: InteractiveErrorSpec(
                "The story session was not found.",
                "List sessions or create a new one.",
            ),
            InteractiveErrorCode.SESSION_NOT_ATTACHED: InteractiveErrorSpec(
                "No story session is attached to this shell.",
                "Create or resume a session before using this command.",
            ),
            InteractiveErrorCode.SESSION_PAUSED: InteractiveErrorSpec(
                "The story session is paused.",
                "Resume the session to make it ready before provider work.",
            ),
            InteractiveErrorCode.SESSION_ACCESS_BLOCKED: InteractiveErrorSpec(
                "Story-session access is held by another live local process.",
                "Wait for that process to release access, then resume the session.",
            ),
            InteractiveErrorCode.SESSION_OWNERSHIP_UNKNOWN: InteractiveErrorSpec(
                "Story-session access ownership cannot be verified.",
                "Do not remove the claim; inspect recovery guidance before retrying.",
            ),
            InteractiveErrorCode.CONVERSATION_REQUIRED: InteractiveErrorSpec(
                "No conversation has current focus.",
                "Create or choose a conversation first.",
            ),
            InteractiveErrorCode.CONVERSATION_INVALID: InteractiveErrorSpec(
                "The conversation input is invalid.",
                (
                    "Provide a valid subject or a full or unique ID prefix of at "
                    "least 12 characters."
                ),
            ),
            InteractiveErrorCode.CONVERSATION_NOT_FOUND: InteractiveErrorSpec(
                "No matching conversation was found.",
                "Use `/conversation list [--all] [QUERY]` to find an ID.",
            ),
            InteractiveErrorCode.CONVERSATION_AMBIGUOUS: InteractiveErrorSpec(
                "The conversation ID prefix is ambiguous.",
                "Use a longer prefix or the full ID.",
            ),
            InteractiveErrorCode.CONVERSATION_ARCHIVED: InteractiveErrorSpec(
                "The conversation is archived.",
                "Choose an active conversation or create a new one.",
            ),
            InteractiveErrorCode.CONVERSATION_CREATE_FAILED: InteractiveErrorSpec(
                "A unique conversation ID could not be created.",
                "Try creating the conversation again.",
            ),
            InteractiveErrorCode.TRANSCRIPT_INVALID: InteractiveErrorSpec(
                "The conversation transcript is invalid.",
                "Inspect and repair the reported transcript before retrying.",
            ),
            InteractiveErrorCode.RETRIEVAL_INVALID: InteractiveErrorSpec(
                "The agent returned an invalid retrieval action.",
                "Start a new turn; if this repeats, check provider compatibility.",
            ),
            InteractiveErrorCode.RETRIEVAL_BUDGET_EXHAUSTED: InteractiveErrorSpec(
                "The conversation retrieval budget was exhausted.",
                "Ask a narrower question or use /search and /transcript directly.",
            ),
            InteractiveErrorCode.PROVIDER_PREFLIGHT_FAILED: InteractiveErrorSpec(
                "Provider preflight failed.",
                "Check the provider executable, login, model, and configuration.",
            ),
            InteractiveErrorCode.PROVIDER_EXITED: InteractiveErrorSpec(
                "The provider process exited unsuccessfully.",
                "Review the sanitized diagnostic and retry when the provider is ready.",
            ),
            InteractiveErrorCode.PROVIDER_TIMED_OUT: InteractiveErrorSpec(
                "The provider operation timed out.",
                "Retry or increase the configured provider timeout.",
            ),
            InteractiveErrorCode.PROVIDER_PROTOCOL_ERROR: InteractiveErrorSpec(
                "The provider returned an invalid response.",
                "Retry; if it repeats, inspect the sanitized diagnostic.",
            ),
            InteractiveErrorCode.DRAFT_INPUT_INVALID: InteractiveErrorSpec(
                "The draft input is invalid.",
                "Provide one readable strict-UTF-8 draft and retry.",
            ),
            InteractiveErrorCode.CONSENSUS_DEADLOCK: InteractiveErrorSpec(
                "The consensus run ended in deadlock.",
                "Revise the instruction and start a new run.",
            ),
            InteractiveErrorCode.CONSENSUS_FAILED: InteractiveErrorSpec(
                "The consensus run failed.",
                "Fix the reported cause and start a new run.",
            ),
            InteractiveErrorCode.ARTIFACT_CONFLICT: InteractiveErrorSpec(
                "An output artifact already exists.",
                "Choose a new output name; existing artifacts are not overwritten.",
            ),
            InteractiveErrorCode.OPERATION_CANCELLED: InteractiveErrorSpec(
                "The operation was cancelled.",
                "The current session remains available; start again when ready.",
            ),
            InteractiveErrorCode.OPERATION_INTERRUPTED: InteractiveErrorSpec(
                "The operation was interrupted.",
                "Inspect preserved state before starting a new operation.",
            ),
            InteractiveErrorCode.CLEANUP_FAILED: InteractiveErrorSpec(
                "Cleanup did not complete.",
                "Inspect retained temporary or access-claim state before retrying.",
            ),
            InteractiveErrorCode.INTERNAL_ERROR: InteractiveErrorSpec(
                "An internal error occurred.",
                "Retry once; if it repeats, report the internal_error code.",
            ),
        }
    )
)


_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_AGENT_ALIASES: Mapping[str, str] = MappingProxyType(
    {route.agent_id: route.prompt_alias for route in AGENT_ROUTES}
)
_AGENT_DISPLAY_NAMES: Mapping[str, str] = MappingProxyType(
    {agent_id: AGENT_SPECS[agent_id].display_name for agent_id in _AGENT_ALIASES}
)
_TRUNCATION_SUFFIX = " ...[truncated]"
_SUMMARY_LIMIT = 512
_HINT_LIMIT = 256
_REDACTION = "<redacted>"
_MAIN_PROMPT_EXCLUSIONS = frozenset({"/compose", "/send", "/cancel"})


_ARTIFACT_REFERENCE_GUIDANCE = (
    "Artifact references require an attached session and focused conversation. "
    "In provider-bound prompts, $ opens whole-file selection and completion; "
    "exact $$ immediately opens ranged selection, where Enter/Tab opens the "
    "highlighted source in the viewer."
)
_STARTUP_WELCOME_SEQUENCE = (
    StartupWelcome(StartupWelcomeStage.STORY),
    StartupWelcome(StartupWelcomeStage.WRITING),
    StartupWelcome(StartupWelcomeStage.AGENTS),
    StartupWelcome(StartupWelcomeStage.HANDOFF),
)


class InteractiveTerminal:
    """Render plain terminal records and collect one input operation at a time."""

    __slots__ = (
        "_accepted_input_write",
        "_capability_provider",
        "_composition_read_line",
        "_composition_reference_read_line",
        "_confirmation_read_line",
        "_main_choice_read_line",
        "_main_reference_read_line",
        "_main_read_line",
        "_picker_read",
        "_prompt_read",
        "_startup_notice",
        "_startup_result_notice",
        "_startup_welcome",
        "_text_viewer_read",
        "_advisory_progress",
        "_debate_progress",
        "_read_line",
        "_width",
        "_write_text",
    )

    def __init__(
        self,
        read_line: Callable[[str], str],
        write_text: Callable[[str], None],
        *,
        width: int = 80,
        main_read_line: Callable[[str], str] | None = None,
    ) -> None:
        """Bind borrowed synchronous I/O callbacks and a fixed wrap width.

        The established ``read_line`` callback remains responsible for subject,
        confirmation, and public-constructor composition input. An optional
        keyword-only ``main_read_line`` handles only completed main-prompt lines
        and defaults to ``read_line``. Construction performs no I/O or resource
        acquisition.
        """

        if not callable(read_line):
            raise TypeError("read_line must be callable")
        if not callable(write_text):
            raise TypeError("write_text must be callable")
        if main_read_line is not None and not callable(main_read_line):
            raise TypeError("main_read_line must be callable or None")
        if not isinstance(width, int) or isinstance(width, bool):
            raise TypeError("width must be an integer")
        if width < 20:
            raise ValueError("width must be at least 20")
        self._accepted_input_write: Callable[[str], None] = lambda _text: None
        self._read_line = read_line
        self._main_read_line = read_line if main_read_line is None else main_read_line
        self._main_choice_read_line = (
            lambda prompt, _choice_provider: self._main_read_line(prompt)
        )
        self._main_reference_read_line = (
            lambda prompt, choice_provider, _scope, _paths, _text: (
                self._main_choice_read_line(prompt, choice_provider)
            )
        )
        self._composition_read_line = _plain_input_reader(read_line, width)
        self._composition_reference_read_line = (
            lambda view, _scope, _paths, _text: self._composition_read_line(view)
        )
        self._confirmation_read_line = _plain_input_reader(read_line, width)
        self._picker_read = _plain_picker_reader(read_line, write_text, width)
        self._prompt_read = _plain_prompt_reader(read_line, width)
        self._startup_welcome = lambda _sleeper: self._write_startup_welcome()
        self._startup_notice = lambda notice, _sleeper: self._write_component(notice)
        self._startup_result_notice = (
            lambda notice, _sleeper: self._write_startup_result_notice(notice)
        )
        self._text_viewer_read = _plain_text_viewer_reader(read_line, width)
        self._advisory_progress = _plain_advisory_progress
        self._debate_progress = _plain_debate_progress
        self._write_text = write_text
        self._width = width
        self._capability_provider = lambda: _plain_capabilities(width)

    def read_main(
        self,
        *,
        session_id: str | None = None,
        agent_id: str | None = None,
    ) -> TerminalAction:
        """Return one unparsed completed main line or typed terminal event.

        The validated scope selects the exact prompt. The borrowed main reader
        owns any live-editing resources and cleanup; this facade only maps
        ``KeyboardInterrupt`` and ``EOFError`` and validates the returned line.
        Other collaborator failures propagate without parsing or mutation.
        """

        prompt = _main_prompt(session_id=session_id, agent_id=agent_id)
        try:
            line = self._main_read_line(prompt)
        except KeyboardInterrupt:
            return TerminalAction(TerminalActionKind.INTERRUPT)
        except EOFError:
            return TerminalAction(TerminalActionKind.EOF)
        return TerminalAction(TerminalActionKind.LINE, text=_validated_input_line(line))

    def request_conversation_subject(self) -> TerminalAction:
        """Read one unparsed subject line or return interrupt/EOF without output.

        The call owns no buffer and performs one synchronous read using the
        fixed subject prompt. Callback and line-framing failures propagate;
        no catalog, default, focus, or cleanup resource is mutated here.
        """

        try:
            line = self._read_line("conversation[subject]> ")
        except KeyboardInterrupt:
            return TerminalAction(TerminalActionKind.INTERRUPT)
        except EOFError:
            return TerminalAction(TerminalActionKind.EOF)
        return TerminalAction(TerminalActionKind.LINE, text=_validated_input_line(line))

    def request_confirmation(
        self,
        message: str,
        *,
        default: bool,
    ) -> ConfirmationResult:
        """Read y/yes/n/no, applying the shown default without other effects.

        The message is sanitized into one prompt line. Invalid answers print a
        fixed hint and retry; Ctrl-C and EOF return typed outcomes, while
        callback and framing failures propagate without being reclassified.
        """

        prompt_message = _inline_text(_required_text("message", message))
        if type(default) is not bool:
            raise TypeError("default must be a bool")
        invalid = False
        while True:
            try:
                answer = _validated_input_line(
                    self._confirmation_read_line(
                        _confirmation_input_view(
                            prompt_message,
                            default=default,
                            invalid=invalid,
                        )
                    )
                )
            except KeyboardInterrupt:
                return ConfirmationResult.INTERRUPT
            except EOFError:
                return ConfirmationResult.EOF

            outcome = transition_confirmation_answer(answer, default=default)
            if outcome.kind is ControlOutcomeKind.COMMIT:
                assert outcome.text is not None
                return ConfirmationResult(outcome.text)
            if outcome.kind is not ControlOutcomeKind.REJECTED:
                raise RuntimeError(
                    "confirmation transition returned an unsupported outcome"
                )
            self._write_component(
                Notice(
                    "Enter y, yes, n, or no.",
                    label="hint",
                    role=SemanticRole.ERROR,
                    state=ViewState.ERROR,
                )
            )
            invalid = True

    def compose(
        self,
        mode: ComposeMode,
        *,
        agent_id: str | None = None,
    ) -> TerminalAction:
        """Collect one exact chat or consensus composition synchronously."""

        return self._compose(
            mode,
            agent_id=agent_id,
            read_input=self._composition_read_line,
        )

    def _compose(
        self,
        mode: ComposeMode,
        *,
        agent_id: str | None,
        read_input: Callable[[InputPromptView], str],
    ) -> TerminalAction:
        """Collect composition through one selected package-internal reader."""

        if not callable(read_input):
            raise TypeError("read_input must be callable")

        alias = _compose_alias(mode, agent_id)
        if mode is ComposeMode.CHAT:
            assert alias is not None
            introduction = (
                f"Target: {alias}. Use /send to submit or /cancel to discard."
            )
        else:
            introduction = "Consensus run. Use /send to submit or /cancel to discard."
        self._write_component(
            Notice(
                introduction,
                label="compose",
                role=SemanticRole.INFO,
                state=ViewState.OPEN,
            )
        )

        state = ComposerState()
        while True:
            try:
                line = _validated_input_line(
                    read_input(_composer_input_view(mode, alias, state))
                )
            except KeyboardInterrupt:
                return self._record_discarded_composition(
                    TerminalActionKind.INTERRUPT,
                    state,
                )
            except EOFError:
                return self._record_discarded_composition(
                    TerminalActionKind.EOF,
                    state,
                )

            state, outcome = transition_composer_line(state, line)
            if outcome is None:
                continue
            if outcome.kind is ControlOutcomeKind.REJECTED:
                self._write_component(
                    Notice(
                        "Nothing to send.",
                        label="compose",
                        role=SemanticRole.ERROR,
                        state=ViewState.ERROR,
                    )
                )
                continue
            if outcome.kind is ControlOutcomeKind.COMMIT:
                assert outcome.text == state.content
                action = TerminalAction(
                    TerminalActionKind.SEND,
                    text=state.content,
                    line_count=state.line_count,
                    character_count=state.character_count,
                )
                target = alias if alias is not None else "consensus"
                self._write_component(
                    Notice(
                        f"Sending {action.line_count} "
                        f"{_plural(action.line_count, 'line')}, "
                        f"{action.character_count} "
                        f"{_plural(action.character_count, 'character')} "
                        f"to {target}.",
                        label="compose",
                        role=SemanticRole.SUCCESS,
                        state=ViewState.COMPLETED,
                    )
                )
                return action
            if outcome.kind is ControlOutcomeKind.CANCEL:
                return self._record_discarded_composition(
                    TerminalActionKind.CANCEL,
                    state,
                )
            raise RuntimeError("composer transition returned an unsupported outcome")

    def write_lobby_orientation(self, workspace: str) -> None:
        """Render the stable startup orientation for an unattached lobby."""

        workspace_text = _inline_text(_required_text("workspace", workspace))
        self._write_component(
            Panel(
                "Story chat",
                (
                    Field("Workspace", workspace_text),
                    Field("Session", "lobby"),
                    Notice(
                        (
                            "Provider calls are unavailable in the lobby. Use "
                            "/session create NAME, /session resume [NAME], or "
                            "/session list."
                        ),
                        role=SemanticRole.WARNING,
                    ),
                    Notice(_ARTIFACT_REFERENCE_GUIDANCE),
                    Notice("Type /help for commands; /quit exits."),
                ),
            )
        )

    def write_session_orientation(
        self,
        workspace: str,
        session_id: str,
        attachment: AttachmentKind,
        agent_id: str,
        history_count: int | None = None,
    ) -> None:
        """Render attached scope, optionally disclosing known room history.

        Integer counts preserve the established history disclosure. ``None``
        omits both history lines for callers that do not yet own conversation
        focus; rendering performs no persistence or resource mutation.
        """

        workspace_text = _inline_text(_required_text("workspace", workspace))
        safe_session = _safe_identifier("session_id", session_id)
        if not isinstance(attachment, AttachmentKind):
            raise TypeError("attachment must be an AttachmentKind")
        _agent_alias(agent_id)
        if history_count is not None and (
            not isinstance(history_count, int) or isinstance(history_count, bool)
        ):
            raise TypeError("history_count must be an integer or None")
        if history_count is not None and history_count < 0:
            raise ValueError("history_count must not be negative")

        items: list[Field | Notice] = [
            Field("Workspace", workspace_text),
            Field("Session", f"{safe_session} ({attachment.value})"),
            Field("Target", _AGENT_DISPLAY_NAMES[agent_id]),
        ]
        if history_count is not None:
            items.extend(
                (
                    Field(
                        "History",
                        (
                            f"{history_count} visible room "
                            f"{_plural(history_count, 'event')}"
                        ),
                    ),
                    Notice(
                        (
                            "Room messages are saved locally and shared with "
                            "addressed agents."
                        )
                    ),
                )
            )
        items.append(Notice(_ARTIFACT_REFERENCE_GUIDANCE))
        items.append(
            Notice("Type /help for commands; /quit releases access without pausing.")
        )
        self._write_component(Panel("Story chat", tuple(items)))

    def write_conversation_scope(
        self,
        *,
        session_id: str,
        conversation_handle: str | None,
        conversation_subject: str | None,
        agent_id: str,
    ) -> None:
        """Render one bounded attached-prompt scope without changing state.

        A nullable handle and subject must be absent together for the explicit
        conversation-lobby form. Supplied focus values are validated, sanitized,
        and width-wrapped with the stable short agent alias before one terminal
        record is written; writer failures propagate without cleanup here.
        """

        safe_session = _safe_identifier("session_id", session_id)
        agent_alias = _agent_alias(agent_id)
        if conversation_handle is None and conversation_subject is None:
            fields = (
                Field("Session", safe_session),
                Field("Conversation", "none"),
                Field("Agent", agent_alias),
            )
        elif conversation_handle is None or conversation_subject is None:
            raise ValueError(
                "conversation_handle and conversation_subject must be absent together"
            )
        else:
            safe_handle = _safe_identifier(
                "conversation_handle",
                conversation_handle,
            )
            safe_subject = _inline_text(
                _required_text("conversation_subject", conversation_subject)
            )
            fields = (
                Field("Session", safe_session),
                Field("Conversation", safe_handle),
                Field("Subject", safe_subject),
                Field("Agent", agent_alias),
            )
        self._write_component(Panel("Conversation scope", fields))

    def write_conversation_list_entry(
        self,
        conversation_handle: str,
        conversation_subject: str,
        *,
        archived: bool,
        focused: bool,
    ) -> None:
        """Render one bounded catalog row from validated display metadata.

        ``archived`` chooses the lifecycle label and ``focused`` marks the
        current in-memory focus; an archived focused row is rejected. Values
        are sanitized and written once, with no catalog, default, or focus mutation.
        """

        safe_handle = _safe_identifier(
            "conversation_handle",
            conversation_handle,
        )
        safe_subject = _inline_text(
            _required_text("conversation_subject", conversation_subject)
        )
        if type(archived) is not bool:
            raise TypeError("archived must be a bool")
        if type(focused) is not bool:
            raise TypeError("focused must be a bool")
        if archived and focused:
            raise ValueError("an archived conversation cannot be focused")
        status = "archived" if archived else "active"
        focus = "focused" if focused else "not focused"
        self._write_component(
            Record(
                f"conversation {safe_handle}",
                fields=(
                    Field("Status", status),
                    Field("Focus", focus),
                    Field("Subject", safe_subject),
                ),
                role=(SemanticRole.MUTED if archived else SemanticRole.DEFAULT),
                state=ViewState.COMPLETED,
            )
        )

    def write_conversation_search_hit(
        self,
        conversation_handle: str,
        conversation_subject: str,
        match_kind: str,
        snippet: str,
        *,
        archived: bool,
        citation_turn_id: str | None = None,
        citation_sequence: int | None = None,
        citation_kind: str | None = None,
    ) -> None:
        """Render one bounded search hit from primitive validated values.

        Citation event fields must be present together. All caller text is
        sanitized and width-wrapped into one LF-ended output record; this
        method owns no search, persistence, or other resource lifecycle.
        """

        safe_handle = _strict_safe_identifier(
            "conversation_handle",
            conversation_handle,
        )
        safe_subject = _inline_text(
            _strict_required_text(
                "conversation_subject",
                conversation_subject,
            )
        )
        safe_match_kind = _inline_text(_strict_required_text("match_kind", match_kind))
        safe_snippet = _inline_text(_strict_required_text("snippet", snippet))
        if type(archived) is not bool:
            raise TypeError("archived must be a bool")

        citation_values = (
            citation_turn_id,
            citation_sequence,
            citation_kind,
        )
        if all(value is None for value in citation_values):
            citation = f"conversation {safe_handle}"
        elif any(value is None for value in citation_values):
            raise ValueError(
                "citation turn ID, sequence, and kind must be present together"
            )
        else:
            safe_turn_id = _strict_safe_identifier(
                "citation_turn_id",
                citation_turn_id,
            )
            safe_sequence = _positive_event_sequence(
                "citation_sequence",
                citation_sequence,
            )
            safe_citation_kind = _inline_text(
                _strict_required_text("citation_kind", citation_kind)
            )
            citation = (
                f"conversation {safe_handle}, turn {safe_turn_id}, "
                f"sequence {safe_sequence}, kind {safe_citation_kind}"
            )

        status = "archived" if archived else "active"
        self._write_component(
            Record(
                f"search {safe_handle}",
                fields=(
                    Field("Status", status),
                    Field("Match", safe_match_kind),
                    Field("Subject", safe_subject),
                    Field("Citation", citation),
                    Field("Snippet", safe_snippet),
                ),
                role=(SemanticRole.MUTED if archived else SemanticRole.DEFAULT),
                state=ViewState.COMPLETED,
            )
        )

    def write_conversation_transcript_event(
        self,
        conversation_handle: str,
        *,
        sequence: int,
        turn_id: str,
        kind: str,
        sender: str,
        recipient: str,
        content: str,
    ) -> None:
        """Render one chronological event from primitive validated values.

        Identity and sequence metadata are validated before the event header.
        Content LFs are preserved with safe continuation indentation while the
        sanitized event remains one callback record. No resource is retained.
        """

        safe_handle = _strict_safe_identifier(
            "conversation_handle",
            conversation_handle,
        )
        safe_sequence = _positive_event_sequence("sequence", sequence)
        safe_turn_id = _strict_safe_identifier("turn_id", turn_id)
        safe_kind = _inline_text(_strict_required_text("kind", kind))
        safe_sender = _inline_text(_strict_required_text("sender", sender))
        safe_recipient = _inline_text(_strict_required_text("recipient", recipient))
        safe_content = sanitize_terminal_text(
            _strict_nonempty_terminal_content(content)
        )
        self._write_component(
            Record(
                f"transcript {safe_handle}",
                fields=(
                    Field("Sequence", str(safe_sequence)),
                    Field("Turn", safe_turn_id),
                    Field("Kind", safe_kind),
                    Field("Sender", safe_sender),
                    Field("Recipient", safe_recipient),
                    Field("Content", ""),
                ),
                content=safe_content,
                state=ViewState.COMPLETED,
            )
        )

    def write_agent_roster(self) -> None:
        """Render every advisory route in registry order without mutation.

        Canonical IDs, display names, aliases, and advisory domains come from
        immutable trusted metadata, are width-wrapped, sanitized, and written
        as one block. Writer failures propagate; this method acquires no other
        resource and therefore owns no cleanup.
        """

        records = tuple(
            Record(
                "agent",
                title=AGENT_SPECS[route.agent_id].display_name,
                fields=(
                    Field("ID", route.agent_id),
                    Field("Aliases", ", ".join(route.aliases)),
                    Field("Advisory domain", route.advisory_domain),
                ),
                role=SemanticRole.AGENT,
                state=ViewState.COMPLETED,
            )
            for route in AGENT_ROUTES
        )
        self._write_component(
            ListView(
                "Agents",
                records,
                total_count=len(records),
                state=ViewState.COMPLETED,
            )
        )

    def write_status(self, kind: TerminalMessageKind, text: str) -> None:
        """Write one trusted status prefix followed by safe display text."""

        if not isinstance(kind, TerminalMessageKind):
            raise TypeError("kind must be a TerminalMessageKind")
        self._write_block(f"[{kind.value}] {_required_text('text', text)}")

    def write_run(self, debate_id: str, text: str) -> None:
        """Write one trusted consensus-run record."""

        safe_id = _safe_identifier("debate_id", debate_id)
        self._write_component(
            Record(
                f"run {safe_id}",
                content=_required_text("text", text),
            )
        )

    def write_agent(self, agent_id: str, text: str) -> None:
        """Write one validated and presentation-formatted agent response."""

        alias = _agent_alias(agent_id)
        self._write_component(
            Message(
                alias,
                format_agent_reply_content(_required_text("text", text)),
                role=SemanticRole.AGENT,
                state=ViewState.COMPLETED,
            )
        )

    def write_help(self, command: str | None = None) -> None:
        """Render full or contextual help from the parser-owned registry."""

        if command is None:
            sections: tuple[Section, ...] = (
                Section(
                    "Writing artifact references",
                    (Notice(_ARTIFACT_REFERENCE_GUIDANCE),),
                    state=ViewState.COMPLETED,
                ),
            )
            for group in dict.fromkeys(spec.group for spec in COMMANDS):
                records = tuple(
                    Record(
                        "command",
                        title=spec.syntax,
                        fields=(
                            Field("Aliases", _aliases_text(spec)),
                            Field("Calls provider", _yes_no(spec.may_call_provider)),
                            Field(
                                "May write artifacts",
                                _yes_no(spec.may_write_artifacts),
                            ),
                        ),
                        role=SemanticRole.COMMAND,
                        state=ViewState.COMPLETED,
                    )
                    for spec in COMMANDS
                    if spec.group == group
                )
                sections += (
                    Section(
                        group,
                        (
                            ListView(
                                "Commands",
                                records,
                                total_count=len(records),
                                state=ViewState.COMPLETED,
                            ),
                        ),
                        state=ViewState.COMPLETED,
                    ),
                )
            component = Panel(
                "Help",
                sections,
                state=ViewState.COMPLETED,
            )
        else:
            spec = _find_command(command, COMMANDS)
            component = Panel(
                "Help",
                (
                    Record(
                        "command",
                        fields=(
                            Field("Group", spec.group),
                            Field("Syntax", spec.syntax),
                            Field("Aliases", _aliases_text(spec)),
                            Field("Calls provider", _yes_no(spec.may_call_provider)),
                            Field(
                                "May write artifacts",
                                _yes_no(spec.may_write_artifacts),
                            ),
                            Field("Example", spec.example),
                        ),
                        role=SemanticRole.COMMAND,
                        state=ViewState.COMPLETED,
                    ),
                ),
                state=ViewState.COMPLETED,
            )
        self._write_component(component)

    def write_error(
        self,
        error: InteractiveError,
        *,
        secrets: Iterable[str] = (),
        debug_log_path: str | None = None,
    ) -> None:
        """Render one typed, redacted error, hint, and optional debug-log path."""

        if not isinstance(error, InteractiveError):
            raise TypeError("error must be an InteractiveError")
        if debug_log_path is not None:
            _validate_default_message("debug_log_path", debug_log_path)
        spec = INTERACTIVE_ERROR_SPECS[error.code]
        variants = _redaction_variants(secrets)
        summary = _prepare_error_message(
            error.summary,
            spec.summary,
            variants,
            _SUMMARY_LIMIT,
        )
        hint = _prepare_error_message(
            error.hint,
            spec.hint,
            variants,
            _HINT_LIMIT,
        )
        notices = [
            Notice(
                summary,
                label=f"error {error.code.value}",
                role=SemanticRole.ERROR,
                state=ViewState.ERROR,
            ),
            Notice(hint, label="hint"),
        ]
        if debug_log_path is not None:
            notices.append(
                Notice(
                    sanitize_terminal_text(debug_log_path),
                    label="debug log",
                )
            )
        self._write_component(
            Panel(
                "Interactive error",
                tuple(notices),
                state=ViewState.ERROR,
            )
        )

    def _record_discarded_composition(
        self,
        kind: TerminalActionKind,
        state: ComposerState,
    ) -> TerminalAction:
        """Report a discarded compose buffer and return its count-only action."""

        action = TerminalAction(
            kind,
            line_count=state.line_count,
            character_count=state.character_count,
        )
        self._write_component(
            Notice(
                f"Discarded {action.line_count} "
                f"{_plural(action.line_count, 'line')}. "
                "No message was saved or sent.",
                label="compose",
                role=SemanticRole.WARNING,
                state=ViewState.CANCELLED,
            )
        )
        return action

    def _full_help_lines(
        self,
        commands: tuple[CommandSpec, ...],
    ) -> list[str]:
        """Project ordered command metadata into grouped full-help lines."""

        lines: list[str] = []
        current_group: str | None = None
        for spec in commands:
            if spec.group != current_group:
                current_group = spec.group
                lines.append(f"{spec.group}:")
            lines.extend(_wrap_prefixed("  ", spec.syntax, self._width))
            lines.extend(
                _wrap_prefixed(
                    "    Aliases: ",
                    _aliases_text(spec),
                    self._width,
                )
            )
            lines.extend(
                _wrap_prefixed(
                    "    Calls provider: ",
                    _yes_no(spec.may_call_provider),
                    self._width,
                )
            )
            lines.extend(
                _wrap_prefixed(
                    "    May write artifacts: ",
                    _yes_no(spec.may_write_artifacts),
                    self._width,
                )
            )
        return lines

    def _context_help_lines(self, spec: CommandSpec) -> list[str]:
        """Project one command specification into contextual-help lines."""

        fields = (
            ("Group: ", spec.group),
            ("Syntax: ", spec.syntax),
            ("Aliases: ", _aliases_text(spec)),
            ("Calls provider: ", _yes_no(spec.may_call_provider)),
            ("May write artifacts: ", _yes_no(spec.may_write_artifacts)),
            ("Example: ", spec.example),
        )
        lines: list[str] = []
        for prefix, value in fields:
            lines.extend(_wrap_prefixed(prefix, value, self._width))
        return lines

    def _write_block(self, text: str) -> None:
        """Sanitize one rendered block and append exactly one record newline."""

        self._write_text(f"{sanitize_terminal_text(text)}\n")

    def _write_component(self, component: Component) -> None:
        """Render and write one semantic component through the bound callback."""

        frame = render_component(component, self._capability_provider())
        self._write_text(f"{frame.text}\n")

    def _write_accepted_input(self, component: Component) -> None:
        """Render one accepted input through the bound mode-aware callback."""

        frame = render_component(component, self._capability_provider())
        self._accepted_input_write(f"{frame.text}\n")

    def _write_startup_welcome(self) -> None:
        """Write one prefix-free final welcome through the callback boundary."""

        capabilities = self._capability_provider()
        frame = render_startup_welcome_frame(
            _STARTUP_WELCOME_SEQUENCE[-1],
            capabilities,
            capabilities.width,
        )
        self._write_text(f"{frame.text}\n")

    def _write_startup_result_notice(self, notice: Notice) -> None:
        """Establish a fresh callback boundary before one static result."""

        self._write_text("\n")
        self._write_component(notice)


class InteractiveSemanticOutput:
    """Bind one terminal's ordinary and mode-gated semantic write paths."""

    __slots__ = ("_write", "_write_accepted")

    def __init__(self, terminal: InteractiveTerminal) -> None:
        """Bind semantic output without inspecting capabilities or streams."""

        if not isinstance(terminal, InteractiveTerminal):
            raise TypeError("terminal must be an InteractiveTerminal")
        self._write: Callable[[Component], None] = terminal._write_component
        self._write_accepted: Callable[[Component], None] = (
            terminal._write_accepted_input
        )

    def write_component(self, component: Component) -> None:
        """Delegate one closed semantic component to exactly one terminal write."""

        self._write(component)

    def write_accepted_input(self, component: Component) -> None:
        """Delegate accepted input without exposing terminal mode to its caller."""

        self._write_accepted(component)


class InteractiveStartupPresentation:
    """Bind the fixed welcome and auth-notice presentation lifecycle."""

    __slots__ = ("_show_notice", "_show_result", "_show_welcome", "_sleeper")

    def __init__(
        self,
        terminal: InteractiveTerminal,
        *,
        sleeper: Callable[[float], object] = time.sleep,
    ) -> None:
        """Retain only terminal-owned startup callbacks and injected timing."""

        if not isinstance(terminal, InteractiveTerminal):
            raise TypeError("terminal must be an InteractiveTerminal")
        if not callable(sleeper):
            raise TypeError("sleeper must be callable")
        self._show_welcome: Callable[[Callable[[float], object]], None] = (
            terminal._startup_welcome
        )
        self._show_notice: Callable[[Notice, Callable[[float], object]], None] = (
            terminal._startup_notice
        )
        self._show_result: Callable[[Notice, Callable[[float], object]], None] = (
            terminal._startup_result_notice
        )
        self._sleeper = sleeper

    def show_welcome(self) -> None:
        """Run the fixed bounded ceremony or one meaningful static rendering."""

        self._show_welcome(self._sleeper)

    def show_notice(self, notice: Notice) -> None:
        """Show one bounded auth notice and clear any capable live frame."""

        if type(notice) is not Notice:
            raise TypeError("notice must be a Notice")
        self._show_notice(notice, self._sleeper)

    def show_result(self, notice: Notice) -> None:
        """Reclaim a fresh line, then show and clear one auth result."""

        if type(notice) is not Notice:
            raise TypeError("notice must be a Notice")
        self._show_result(notice, self._sleeper)


class InteractiveMainInput:
    """Bind call-local main choices without expanding the public terminal."""

    __slots__ = (
        "_read",
        "_reference_paths_provider",
        "_reference_scope_provider",
        "_reference_text_reader",
    )

    def __init__(self, terminal: InteractiveTerminal) -> None:
        """Retain only one terminal's package-internal main-input capability."""

        if not isinstance(terminal, InteractiveTerminal):
            raise TypeError("terminal must be an InteractiveTerminal")
        self._read: Callable[
            [
                str,
                Callable[[str, int], Picker | None],
                Callable[[str, int], ReferenceInputScope | None] | None,
                Callable[[], tuple[str, ...]] | None,
                Callable[[str], str] | None,
            ],
            str,
        ] = terminal._main_reference_read_line
        self._reference_scope_provider: (
            Callable[[str, int], ReferenceInputScope | None] | None
        ) = None
        self._reference_paths_provider: Callable[[], tuple[str, ...]] | None = None
        self._reference_text_reader: Callable[[str], str] | None = None

    def read_main(
        self,
        choice_provider: Callable[[str, int], Picker | None],
        *,
        session_id: str | None = None,
        agent_id: str | None = None,
    ) -> TerminalAction:
        """Return one completed main line using call-local semantic choices."""

        return self._read_main(
            choice_provider,
            session_id=session_id,
            agent_id=agent_id,
            reference_scope_provider=self._reference_scope_provider,
            reference_paths_provider=self._reference_paths_provider,
            reference_text_reader=self._reference_text_reader,
        )

    def read_main_with_references(
        self,
        choice_provider: Callable[[str, int], Picker | None],
        reference_scope_provider: Callable[[str, int], ReferenceInputScope | None],
        reference_paths_provider: Callable[[], tuple[str, ...]],
        reference_text_reader: Callable[[str], str],
        *,
        session_id: str | None = None,
        agent_id: str | None = None,
    ) -> TerminalAction:
        """Return one main line with private call-local reference activation."""

        collaborators = (
            reference_scope_provider,
            reference_paths_provider,
            reference_text_reader,
        )
        if not all(callable(value) for value in collaborators):
            raise TypeError("reference collaborators must be callable")
        if any(
            value is not None
            for value in (
                self._reference_scope_provider,
                self._reference_paths_provider,
                self._reference_text_reader,
            )
        ):
            raise RuntimeError("reference main input is already active")
        self._reference_scope_provider = reference_scope_provider
        self._reference_paths_provider = reference_paths_provider
        self._reference_text_reader = reference_text_reader
        try:
            return self.read_main(
                choice_provider,
                session_id=session_id,
                agent_id=agent_id,
            )
        finally:
            self._reference_scope_provider = None
            self._reference_paths_provider = None
            self._reference_text_reader = None

    def _read_main(
        self,
        choice_provider: Callable[[str, int], Picker | None],
        *,
        session_id: str | None,
        agent_id: str | None,
        reference_scope_provider: (
            Callable[[str, int], ReferenceInputScope | None] | None
        ),
        reference_paths_provider: Callable[[], tuple[str, ...]] | None,
        reference_text_reader: Callable[[str], str] | None,
    ) -> TerminalAction:
        """Implement main input through one selected package-internal route."""

        if not callable(choice_provider):
            raise TypeError("choice_provider must be callable")
        reference_collaborators = (
            reference_scope_provider,
            reference_paths_provider,
            reference_text_reader,
        )
        if any(value is not None for value in reference_collaborators) and not all(
            callable(value) for value in reference_collaborators
        ):
            raise ValueError(
                "reference scope, paths, and text collaborators must be supplied together"
            )
        prompt = _main_prompt(session_id=session_id, agent_id=agent_id)
        try:
            line = self._read(
                prompt,
                choice_provider,
                reference_scope_provider,
                reference_paths_provider,
                reference_text_reader,
            )
        except KeyboardInterrupt:
            return TerminalAction(TerminalActionKind.INTERRUPT)
        except EOFError:
            return TerminalAction(TerminalActionKind.EOF)
        return TerminalAction(
            TerminalActionKind.LINE,
            text=_validated_input_line(line),
        )


class InteractiveCompositionInput:
    """Bind RUN/CHAT composition to call-local reference collaborators."""

    __slots__ = ("_terminal",)

    def __init__(self, terminal: InteractiveTerminal) -> None:
        if not isinstance(terminal, InteractiveTerminal):
            raise TypeError("terminal must be an InteractiveTerminal")
        self._terminal = terminal

    def compose(
        self,
        mode: ComposeMode,
        *,
        agent_id: str | None = None,
        reference_scope_provider: Callable[[str, int], ReferenceInputScope | None],
        reference_paths_provider: Callable[[], tuple[str, ...]],
        reference_text_reader: Callable[[str], str],
    ) -> TerminalAction:
        """Collect one composition without expanding the public terminal API."""

        for name, collaborator in (
            ("reference_scope_provider", reference_scope_provider),
            ("reference_paths_provider", reference_paths_provider),
            ("reference_text_reader", reference_text_reader),
        ):
            if not callable(collaborator):
                raise TypeError(f"{name} must be callable")
        return self._terminal._compose(
            mode,
            agent_id=agent_id,
            read_input=lambda view: self._terminal._composition_reference_read_line(
                view,
                reference_scope_provider,
                reference_paths_provider,
                reference_text_reader,
            ),
        )


class InteractivePickerInput:
    """Retain only one terminal's package-internal picker input capability."""

    __slots__ = ("_read",)

    def __init__(self, terminal: InteractiveTerminal) -> None:
        """Bind picker input without inspecting capabilities or acquiring I/O."""

        if not isinstance(terminal, InteractiveTerminal):
            raise TypeError("terminal must be an InteractiveTerminal")
        self._read: Callable[[PickerControlState], ControlOutcome] = (
            terminal._picker_read
        )

    def read_picker(
        self,
        prompt: Prompt,
        choices: tuple[Choice, ...],
        *,
        highlighted_id: str | None = None,
        selected_id: str | None = None,
        notice: Notice | None = None,
        state: ViewState = ViewState.OPEN,
        transient: bool = True,
    ) -> ControlOutcome:
        """Read one semantic single-select value without invoking a consumer."""

        if type(transient) is not bool:
            raise TypeError("transient must be a bool")

        picker = Picker(
            prompt,
            choices,
            highlighted_id=highlighted_id,
            selected_id=selected_id,
            notice=notice,
            state=state,
        )
        initial_state = initialize_picker(picker)
        outcome = self._read(initial_state, transient=True)
        if type(outcome) is not ControlOutcome:
            raise TypeError("picker reader must return a ControlOutcome")
        return outcome


class InteractivePromptInput:
    """Retain only one terminal's package-internal cancellable line input."""

    __slots__ = ("_read",)

    def __init__(self, terminal: InteractiveTerminal) -> None:
        """Bind prompt input without inspecting capabilities or acquiring I/O."""

        if not isinstance(terminal, InteractiveTerminal):
            raise TypeError("terminal must be an InteractiveTerminal")
        self._read: Callable[[InputPromptView], ControlOutcome] = terminal._prompt_read

    def read_prompt(
        self,
        prompt: Prompt,
        *,
        context: tuple[Panel | Section | Field | Notice, ...] = (),
        initial_value: str | None = None,
        transient: bool = True,
    ) -> ControlOutcome:
        """Read one exact line with a distinct nested-cancel outcome."""

        if type(transient) is not bool:
            raise TypeError("transient must be a bool")

        view = InputPromptView(context, prompt, initial_value=initial_value)
        outcome = self._read(view, transient=True)
        if type(outcome) is not ControlOutcome:
            raise TypeError("prompt reader must return a ControlOutcome")
        return outcome


class InteractiveTextViewer:
    """Bind one stateless supplied-text range viewer without storage access."""

    __slots__ = ("_read",)

    def __init__(self, terminal: InteractiveTerminal) -> None:
        """Retain only one terminal's package-internal viewer capability."""

        if not isinstance(terminal, InteractiveTerminal):
            raise TypeError("terminal must be an InteractiveTerminal")
        self._read: Callable[[TextViewerState], TextViewerResult] = (
            terminal._text_viewer_read
        )

    def read_range(
        self,
        text: str,
        *,
        display_name: str,
        initial_line: int | None = None,
    ) -> TextViewerResult:
        """Return one inclusive line range or a typed noncommit outcome."""

        state = initialize_text_viewer(
            text,
            display_name=display_name,
            initial_line=initial_line,
        )
        try:
            outcome = self._read(state)
            if type(outcome) is not TextViewerResult:
                raise TypeError("viewer reader must return a TextViewerResult")
            return outcome
        finally:
            dispose_text_viewer(state)


class InteractiveAdvisoryProgress:
    """Retain one terminal's package-internal advisory progress session."""

    __slots__ = ("_run",)

    def __init__(self, terminal: InteractiveTerminal) -> None:
        """Bind progress lifecycle without inspecting streams or capabilities."""

        if not isinstance(terminal, InteractiveTerminal):
            raise TypeError("terminal must be an InteractiveTerminal")
        self._run: Callable[[Awaitable[object]], Awaitable[object]] = (
            terminal._advisory_progress
        )

    async def run(self, operation: Awaitable[object]) -> object:
        """Join one advisory operation with the terminal-owned indicator."""

        return await self._run(operation)


class InteractiveDebateProgress:
    """Retain one terminal's package-internal consensus progress session."""

    __slots__ = ("_run",)

    def __init__(self, terminal: InteractiveTerminal) -> None:
        """Bind consensus progress lifecycle without inspecting streams."""

        if not isinstance(terminal, InteractiveTerminal):
            raise TypeError("terminal must be an InteractiveTerminal")
        self._run: Callable[[Awaitable[object]], Awaitable[object]] = (
            terminal._debate_progress
        )

    async def run(self, operation: Awaitable[object]) -> object:
        """Join one consensus operation with the terminal-owned indicator."""

        return await self._run(operation)


def build_contextual_terminal(
    read_line: Callable[[str], str],
    write_text: Callable[[str], None],
    *,
    main_read_line: Callable[[str], str],
    composition_read_line: Callable[[str], str],
    width: int = 80,
) -> InteractiveTerminal:
    """Construct a terminal with explicit main, composition, and plain reads.

    Public construction continues routing composition through ``read_line``.
    This internal seam binds the contextual callbacks without inspecting
    prompts, acquiring resources, or changing the public constructor.
    """

    if not callable(composition_read_line):
        raise TypeError("composition_read_line must be callable")
    terminal = InteractiveTerminal(
        read_line,
        write_text,
        width=width,
        main_read_line=main_read_line,
    )
    terminal._composition_read_line = _plain_input_reader(
        composition_read_line,
        width,
    )
    return terminal


def _plain_capabilities(width: int) -> TerminalCapabilities:
    """Return the deterministic resource-free callback rendering profile."""

    return TerminalCapabilities(
        CapabilityProfile.LINE,
        width,
        input_tty=False,
        output_tty=False,
        raw_mode=False,
        cursor_controls=False,
        decorative_sgr=False,
    )


def _plain_input_reader(
    read_line: Callable[[str], str],
    width: int,
) -> Callable[[InputPromptView], str]:
    """Adapt one borrowed string reader to deterministic semantic input."""

    if not callable(read_line):
        raise TypeError("read_line must be callable")
    capabilities = _plain_capabilities(width)

    def read_input(view: InputPromptView) -> str:
        frame = render_input_prompt(view, capabilities)
        return read_line(frame.plain_text)

    return read_input


def _plain_prompt_reader(
    read_line: Callable[[str], str],
    width: int,
) -> Callable[[InputPromptView], ControlOutcome]:
    """Adapt one borrowed line callback to cancellable semantic input."""

    if not callable(read_line):
        raise TypeError("read_line must be callable")
    capabilities = _plain_capabilities(width)

    def read_prompt(
        view: InputPromptView,
        *,
        transient: bool = True,
    ) -> ControlOutcome:
        if type(transient) is not bool:
            raise TypeError("transient must be a bool")
        frame = render_input_prompt(view, capabilities)
        try:
            line = _validated_input_line(read_line(frame.plain_text))
        except KeyboardInterrupt:
            return ControlOutcome(ControlOutcomeKind.INTERRUPT)
        except EOFError:
            return ControlOutcome(ControlOutcomeKind.EOF)
        if line == "\x1b":
            return ControlOutcome(ControlOutcomeKind.CANCEL)
        if line == "" and view.initial_value is not None:
            line = view.initial_value
        return ControlOutcome(ControlOutcomeKind.COMMIT, text=line)

    return read_prompt


async def _plain_advisory_progress(operation: Awaitable[object]) -> object:
    """Keep callback and non-TTY advisory execution visually silent."""

    return await operation


async def _plain_debate_progress(operation: Awaitable[object]) -> object:
    """Keep callback and non-TTY consensus execution visually silent."""

    return await operation


def _plain_picker_reader(
    read_line: Callable[[str], str],
    write_text: Callable[[str], None],
    width: int,
) -> Callable[[PickerControlState], ControlOutcome]:
    """Adapt borrowed callbacks to deterministic complete-list picker input."""

    if not callable(read_line):
        raise TypeError("read_line must be callable")
    if not callable(write_text):
        raise TypeError("write_text must be callable")
    capabilities = _plain_capabilities(width)

    def read_picker(
        initial_state: PickerControlState,
        *,
        transient: bool = True,
    ) -> ControlOutcome:
        if type(initial_state) is not PickerControlState:
            raise TypeError("state must be a PickerControlState")
        if type(transient) is not bool:
            raise TypeError("transient must be a bool")
        picker_state = initial_state
        while True:
            frame = render_picker_fallback(picker_state, capabilities)
            try:
                answer = _validated_input_line(read_line(frame.plain_text))
            except KeyboardInterrupt:
                return ControlOutcome(ControlOutcomeKind.INTERRUPT)
            except EOFError:
                return ControlOutcome(ControlOutcomeKind.EOF)
            picker_state, outcome = transition_picker_fallback(
                picker_state,
                answer,
            )
            if outcome.kind is ControlOutcomeKind.REJECTED:
                continue
            if (
                outcome.kind
                in {
                    ControlOutcomeKind.COMMIT,
                    ControlOutcomeKind.CANCEL,
                }
                and not transient
            ):
                final_frame = render_picker_fallback(
                    picker_state,
                    capabilities,
                )
                write_text(f"{final_frame.text}\n")
            return outcome

    return read_picker


def _plain_text_viewer_reader(
    read_line: Callable[[str], str],
    width: int,
) -> Callable[[TextViewerState], TextViewerResult]:
    """Adapt one ordinary line callback to the strict ranged viewer fallback."""

    if not callable(read_line):
        raise TypeError("read_line must be callable")
    capabilities = _plain_capabilities(width)

    def read_text_viewer(state: TextViewerState) -> TextViewerResult:
        if type(state) is not TextViewerState:
            raise TypeError("state must be a TextViewerState")
        current = state
        while True:
            frame = render_text_viewer_fallback(current, capabilities)
            try:
                callback_value = read_line(frame.plain_text)
                if isinstance(callback_value, str):
                    callback_value = str.__str__(callback_value)
                answer = _validated_input_line(callback_value)
            except KeyboardInterrupt:
                return TextViewerResult(TextViewerExitKind.INTERRUPTED)
            except EOFError:
                return TextViewerResult(TextViewerExitKind.EOF)
            current, outcome = transition_text_viewer_fallback(current, answer)
            if outcome is not None:
                return outcome

    return read_text_viewer


def _empty_slash_suggestions(
    buffer: str,
) -> tuple[SlashCommandSuggestion, ...]:
    """Return the fixed empty preview set for confirmation input."""

    if type(buffer) is not str:
        raise TypeError("buffer must be a string")
    return ()


def _main_slash_suggestions(
    buffer: str,
) -> tuple[SlashCommandSuggestion, ...]:
    """Project the current canonical command registry for the live driver."""

    command_tokens = tuple(
        (spec.syntax.split(maxsplit=1)[0], spec.aliases) for spec in COMMANDS
    )
    return project_slash_suggestions(
        buffer,
        command_tokens,
        exclusions=_MAIN_PROMPT_EXCLUSIONS,
    )


def _main_command_completion(
    buffer: str,
    cursor: int,
) -> CommandCompletionProjection | None:
    """Adapt command-owned grammar context to terminal completion values."""

    context = project_command_completion(buffer, cursor)
    if context is None:
        return None
    return CommandCompletionProjection(
        context.position,
        context.replacement_start,
        context.replacement_end,
        tuple(
            CommandCompletionCandidate(context.position, replacement)
            for replacement in context.replacements
        ),
    )


def _reference_projection_provider(
    scope_provider: Callable[[str, int], ReferenceInputScope | None],
    paths_provider: Callable[[], tuple[str, ...]],
) -> Callable[[str, int], ReferenceInputProjection | None]:
    """Defer repository listing until one eligible active marker exists."""

    def project(buffer: str, cursor: int) -> ReferenceInputProjection | None:
        scope = scope_provider(buffer, cursor)
        if scope is not None and type(scope) is not ReferenceInputScope:
            raise TypeError(
                "reference scope provider must return a ReferenceInputScope or None"
            )
        probe = project_reference_input(buffer, cursor, scope)
        if probe is None:
            return None
        if probe.failure is ReferenceInputFailureKind.INVALID:
            return probe
        try:
            paths = paths_provider()
        except ReferenceInputFailure as failure:
            return project_reference_input(
                buffer,
                cursor,
                scope,
                failure=failure.kind,
            )
        if type(paths) is not tuple or any(type(path) is not str for path in paths):
            raise TypeError("reference paths provider must return a tuple of strings")
        return project_reference_input(buffer, cursor, scope, paths)

    return project


def _reference_picker_state(
    values: tuple[str, ...],
    instruction: str,
) -> PickerControlState:
    """Construct one deterministic generic picker for paths or tokens."""

    if type(values) is not tuple or any(type(value) is not str for value in values):
        raise TypeError("reference picker values must be a tuple of strings")
    picker = Picker(
        Prompt("writing artifact", instruction=instruction),
        tuple(Choice(value, value) for value in values),
    )
    return initialize_picker(picker)


def _reference_input_bindings(
    projection_provider: Callable[[str, int], ReferenceInputProjection | None],
    text_reader: Callable[[str], str],
    failure_writer: Callable[[ReferenceInputFailureKind], None],
) -> ReferenceInputBindings:
    """Bind pure controls/renderers to one call-local driver interaction."""

    return ReferenceInputBindings(
        projection_provider,
        completion_candidate_matches,
        ranged_artifact_picker_selection,
        text_reader,
        failure_writer,
        _reference_picker_state,
        transition_picker,
        transition_picker_fallback,
        project_picker_viewport,
        render_picker_viewport,
        render_picker_fallback,
        lambda text, path: initialize_text_viewer(text, display_name=path),
        transition_text_viewer,
        transition_text_viewer_fallback,
        project_text_viewer_viewport,
        render_text_viewer,
        render_text_viewer_fallback,
    )


def _reference_input_error(kind: ReferenceInputFailureKind) -> InteractiveError:
    """Map one content-free activation failure to fixed actionable copy."""

    if not isinstance(kind, ReferenceInputFailureKind):
        raise TypeError("kind must be a ReferenceInputFailureKind")
    copy = {
        ReferenceInputFailureKind.EMPTY_SOURCES: (
            "No current writing artifacts are available.",
            "Add a supported canonical core document or Markdown file with a "
            "lowercase .md suffix under manuscript/. Consensus can also "
            "publish one.",
        ),
        ReferenceInputFailureKind.STALE: (
            "The selected writing artifact is no longer current.",
            "Type $ again to refresh the canonical artifact list.",
        ),
        ReferenceInputFailureKind.UNREADABLE: (
            "The selected writing artifact cannot be viewed safely.",
            "Inspect its size and UTF-8 repository state, then refresh the list.",
        ),
        ReferenceInputFailureKind.UNAVAILABLE: (
            "The writing-artifact repository is unavailable.",
            "Inspect workspace recovery guidance, then retry discovery.",
        ),
        ReferenceInputFailureKind.INVALID: (
            "The artifact reference cannot be inserted.",
            "Remove the active marker or choose a nonconflicting current artifact.",
        ),
    }[kind]
    code = (
        InteractiveErrorCode.COMMAND_INVALID
        if kind in {
            ReferenceInputFailureKind.EMPTY_SOURCES,
            ReferenceInputFailureKind.INVALID,
        }
        else InteractiveErrorCode.WORKSPACE_INVALID
    )
    return InteractiveError(code, summary=copy[0], hint=copy[1])


def read_main_command_line(
    driver: RawTerminalDriver,
    prompt: str,
    *,
    choice_provider: Callable[[str, int], Picker | None] | None = None,
    reference_scope_provider: Callable[[str, int], ReferenceInputScope | None]
    | None = None,
    reference_paths_provider: Callable[[], tuple[str, ...]] | None = None,
    reference_text_reader: Callable[[str], str] | None = None,
    reference_failure_writer: (
        Callable[[ReferenceInputFailureKind], None] | None
    ) = None,
) -> str:
    """Read one canonical selectable main command through the sole driver."""

    if not isinstance(driver, RawTerminalDriver):
        raise TypeError("driver must be a RawTerminalDriver")
    if choice_provider is not None and not callable(choice_provider):
        raise TypeError("choice_provider must be callable or None")
    reference_collaborators = (
        reference_scope_provider,
        reference_paths_provider,
        reference_text_reader,
        reference_failure_writer,
    )
    if any(value is not None for value in reference_collaborators) and not all(
        callable(value) for value in reference_collaborators
    ):
        raise ValueError(
            "reference scope, paths, text, and failure collaborators must be supplied together"
        )

    reference_projection = (
        None
        if reference_scope_provider is None
        else _reference_projection_provider(
            reference_scope_provider,
            reference_paths_provider,
        )
    )
    reference_bindings = (
        None
        if reference_projection is None
        else _reference_input_bindings(
            reference_projection,
            reference_text_reader,
            reference_failure_writer,
        )
    )

    active_pickers: dict[tuple[str, int], Picker] = {}

    def completion_provider(
        buffer: str,
        cursor: int,
    ) -> CommandCompletionProjection | None:
        if reference_projection is not None:
            projected_reference = reference_projection(buffer, cursor)
            if projected_reference is not None:
                active_pickers.clear()
                return projected_reference.completion
        picker = None if choice_provider is None else choice_provider(buffer, cursor)
        if picker is None:
            active_pickers.clear()
            return _main_command_completion(buffer, cursor)
        projection = project_main_choice_completion(buffer, cursor, picker)
        active_pickers.clear()
        active_pickers[(buffer, cursor)] = picker
        return projection

    def palette_projector(
        state: MainCommandControlState,
    ) -> SuggestionPalette | None:
        if not main_command_uses_choices(state):
            return project_main_suggestion_palette(state)
        line = state.main_input.line_state
        picker = active_pickers.get((line.buffer, line.cursor))
        if picker is None:
            raise RuntimeError("active main choices are not correlated")
        return project_main_choice_palette(state, picker)

    if reference_bindings is None:
        return driver.read_main_command_line(
            prompt,
            completion_provider,
            palette_projector,
            render_component,
        )
    return driver.read_reference_main_command_line(
        prompt,
        completion_provider,
        palette_projector,
        render_component,
        reference_bindings,
    )


def read_reference_composition_line(
    driver: RawTerminalDriver,
    view: InputPromptView,
    scope_provider: Callable[[str, int], ReferenceInputScope | None],
    paths_provider: Callable[[], tuple[str, ...]],
    text_reader: Callable[[str], str],
    failure_writer: Callable[[ReferenceInputFailureKind], None],
) -> str:
    """Read one RUN/CHAT line through the shared reference input route."""

    if not isinstance(driver, RawTerminalDriver):
        raise TypeError("driver must be a RawTerminalDriver")
    projection = _reference_projection_provider(scope_provider, paths_provider)
    bindings = _reference_input_bindings(
        projection,
        text_reader,
        failure_writer,
    )

    def completion_provider(
        buffer: str,
        cursor: int,
    ) -> CommandCompletionProjection | None:
        projected = projection(buffer, cursor)
        return None if projected is None else projected.completion

    return driver.read_reference_input_line(
        view,
        render_input_prompt,
        project_composition_suggestions,
        completion_provider,
        project_main_suggestion_palette,
        render_component,
        bindings,
    )


def build_stream_terminal(
    fallback_read_line: Callable[[str], str],
    input_stream: TextIO,
    output_stream: TextIO,
) -> InteractiveTerminal:
    """Build the real CLI terminal around one stream-owning raw driver."""

    driver = RawTerminalDriver(
        fallback_read_line,
        input_stream,
        output_stream,
        transition_line_input,
        transition_main_command_input,
        append_main_input_history,
    )
    capabilities = driver.capabilities()
    terminal = InteractiveTerminal(
        driver.read_line,
        driver.write_text,
        width=max(20, capabilities.width),
        main_read_line=lambda prompt: read_main_command_line(driver, prompt),
    )
    terminal._accepted_input_write = driver.write_accepted_input
    terminal._main_choice_read_line = lambda prompt, provider: (
        read_main_command_line(
            driver,
            prompt,
            choice_provider=provider,
        )
    )
    terminal._main_reference_read_line = (
        lambda prompt, choices, scope, paths, text: read_main_command_line(
            driver,
            prompt,
            choice_provider=choices,
            reference_scope_provider=scope,
            reference_paths_provider=paths,
            reference_text_reader=text,
            reference_failure_writer=(
                None
                if scope is None
                else lambda kind: terminal.write_error(_reference_input_error(kind))
            ),
        )
    )
    terminal._composition_read_line = lambda view: driver.read_input_line(
        view,
        render_input_prompt,
        project_composition_suggestions,
        transient=True,
    )
    terminal._composition_reference_read_line = (
        lambda view, scope, paths, text: read_reference_composition_line(
            driver,
            view,
            scope,
            paths,
            text,
            lambda kind: terminal.write_error(_reference_input_error(kind)),
        )
    )
    terminal._confirmation_read_line = lambda view: driver.read_input_line(
        view,
        render_input_prompt,
        _empty_slash_suggestions,
        transient=True,
    )

    def read_picker(state, *, transient=True):
        if type(transient) is not bool:
            raise TypeError("transient must be a bool")
        arguments = (
            state,
            transition_picker,
            transition_picker_fallback,
            project_picker_viewport,
            render_picker_viewport,
            render_picker_fallback,
        )
        return driver.read_picker(*arguments, transient=True)

    def read_prompt(view, *, transient=True):
        if type(transient) is not bool:
            raise TypeError("transient must be a bool")
        transition = (
            transition_prefilled_prompt_input
            if view.initial_value is not None
            else transition_prompt_input
        )
        arguments = (view, render_input_prompt, transition)
        return driver.read_prompt(*arguments, transient=True)

    terminal._picker_read = read_picker
    terminal._prompt_read = read_prompt
    terminal._startup_welcome = lambda sleeper: driver.run_startup_welcome(
        lambda index, current, physical_width: render_startup_welcome_frame(
            _STARTUP_WELCOME_SEQUENCE[index],
            current,
            physical_width,
        ),
        sleeper,
    )
    terminal._startup_notice = lambda notice, sleeper: driver.run_startup_notice(
        lambda current: render_component(notice, current),
        sleeper,
    )
    terminal._startup_result_notice = (
        lambda notice, sleeper: driver.run_startup_result_notice(
            lambda current: render_component(notice, current),
            sleeper,
        )
    )
    terminal._text_viewer_read = lambda state: driver.read_text_viewer(
        state,
        transition_text_viewer,
        transition_text_viewer_fallback,
        project_text_viewer_viewport,
        render_text_viewer,
        render_text_viewer_fallback,
    )

    async def run_advisory_progress(operation):
        return await driver.run_advisory_progress(
            operation,
            render_advisory_progress_frame,
        )

    async def run_debate_progress(operation):
        return await driver.run_advisory_progress(
            operation,
            render_advisory_progress_frame,
        )

    terminal._advisory_progress = run_advisory_progress
    terminal._debate_progress = run_debate_progress
    terminal._capability_provider = driver.capabilities
    return terminal


def _safe_identifier(name: str, value: object) -> str:
    """Return a string that satisfies the shared safe-identifier grammar."""

    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{name} must match [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}")
    return value


def _strict_safe_identifier(name: str, value: object) -> str:
    """Return one exact-string identifier without I/O or normalization."""

    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    return _safe_identifier(name, value)


def _strict_required_text(name: str, value: object) -> str:
    """Return one exact nonblank primitive string without mutation."""

    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if not value or value.isspace():
        raise ValueError(f"{name} must not be blank")
    return value


def _strict_nonempty_terminal_content(value: object) -> str:
    """Return exact nonempty event content for safe terminal presentation.

    Whitespace and LF structure remain valid content. The pure validator
    rejects subclasses and empty input but leaves control escaping to the
    terminal sanitizer.
    """

    if type(value) is not str:
        raise TypeError("content must be a string")
    if not value:
        raise ValueError("content must not be empty")
    return value


def _positive_event_sequence(name: str, value: object) -> int:
    """Return one exact positive event sequence without external effects."""

    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _agent_alias(agent_id: object) -> str:
    """Resolve one canonical agent ID to its stable prompt alias."""

    if not isinstance(agent_id, str):
        raise TypeError("agent_id must be a string")
    try:
        return _AGENT_ALIASES[agent_id]
    except KeyError as exc:
        raise ValueError(f"unknown canonical agent ID: {agent_id}") from exc


def _required_text(name: str, value: object) -> str:
    """Return a nonblank string suitable for caller-supplied display text."""

    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value or value.isspace():
        raise ValueError(f"{name} must not be blank")
    return value


def _validated_input_line(value: object) -> str:
    """Return one callback line after rejecting nonstrings and CR/LF framing."""

    if not isinstance(value, str):
        raise TypeError("read_line must return a string")
    if "\r" in value or "\n" in value:
        raise ValueError("read_line must return one line without CR or LF")
    return value


def _main_prompt(*, session_id: str | None, agent_id: str | None) -> str:
    """Build the exact lobby or attached main prompt."""

    if session_id is None and agent_id is None:
        return "story[lobby]> "
    if session_id is None or agent_id is None:
        raise ValueError("attached prompts require both session_id and agent_id")
    safe_session = _safe_identifier("session_id", session_id)
    return f"story[{safe_session}:{_agent_alias(agent_id)}]> "


def _compose_alias(mode: object, agent_id: str | None) -> str | None:
    """Validate a compose destination and return its chat alias when needed."""

    if not isinstance(mode, ComposeMode):
        raise TypeError("mode must be a ComposeMode")
    if mode is ComposeMode.CHAT:
        if agent_id is None:
            raise ValueError("chat composition requires an agent_id")
        return _agent_alias(agent_id)
    if agent_id is not None:
        raise ValueError("run composition does not accept an agent_id")
    return None


def _compose_prompt(mode: ComposeMode, alias: str | None, line_number: int) -> str:
    """Build the exact prompt for one numbered composition line."""

    if mode is ComposeMode.CHAT:
        assert alias is not None
        return f"compose[chat:{alias}:{line_number}]> "
    return f"compose[run:{line_number}]> "


def _composer_input_view(
    mode: ComposeMode,
    alias: str | None,
    state: ComposerState,
) -> InputPromptView:
    """Project exact composer facts before one focused numbered prompt."""

    items: list[Field | Notice] = [Field("Mode", mode.value)]
    if alias is not None:
        items.append(Field("Target", alias))
    items.extend(
        (
            Field("Next line", str(state.line_count + 1)),
            Field("Lines", str(state.line_count)),
            Field("Characters", str(state.character_count)),
            Notice("Use /send to submit or /cancel to discard."),
        )
    )
    identity = _compose_prompt(mode, alias, state.line_count + 1).removesuffix(" ")
    return InputPromptView(tuple(items), Prompt(identity))


def _confirmation_input_view(
    message: str,
    *,
    default: bool,
    invalid: bool,
) -> InputPromptView:
    """Project question, choices, default, invalid state, and focused answer."""

    if type(default) is not bool:
        raise TypeError("default must be a bool")
    if type(invalid) is not bool:
        raise TypeError("invalid must be a bool")
    choice_role = SemanticRole.ERROR if invalid else SemanticRole.DEFAULT
    choice_state = ViewState.ERROR if invalid else ViewState.IDLE
    suffix = "[Y/n]" if default else "[y/N]"
    return InputPromptView(
        (
            Field("Question", message),
            Field(
                "Choices",
                "y/yes or n/no",
                role=choice_role,
                state=choice_state,
            ),
            Field("Default", "yes" if default else "no"),
        ),
        Prompt(f"confirm {suffix}>"),
    )


def _plural(count: int, singular: str) -> str:
    """Return a simple singular or plural label for a validated count."""

    return singular if count == 1 else f"{singular}s"


def _inline_text(text: str) -> str:
    """Sanitize text for one display line, visibly escaping structural LFs."""

    return sanitize_terminal_text(text).replace("\n", "\\x0a")


def _aliases_text(spec: CommandSpec) -> str:
    """Format declared command aliases or the stable empty marker."""

    return ", ".join(spec.aliases) if spec.aliases else "none"


def _yes_no(value: bool) -> str:
    """Format a trusted capability flag for help output."""

    return "yes" if value else "no"


def _find_command(
    command: object,
    commands: tuple[CommandSpec, ...],
) -> CommandSpec:
    """Find one command by canonical token or declared alias."""

    if not isinstance(command, str):
        raise TypeError("command must be a string or None")
    if not command:
        raise ValueError("command must not be empty")
    for spec in commands:
        canonical = spec.syntax.split(" ", 1)[0]
        if command == canonical or command in spec.aliases:
            return spec
    raise ValueError(f"unknown interactive command: {command}")


def _wrap_prefixed(prefix: str, value: str, width: int) -> list[str]:
    """Wrap terminal-owned prose with stable hanging indentation."""

    wrapper = textwrap.TextWrapper(
        width=width,
        initial_indent=prefix,
        subsequent_indent=" " * len(prefix),
        break_long_words=False,
        break_on_hyphens=False,
        replace_whitespace=False,
        drop_whitespace=True,
    )
    return wrapper.wrap(value) or [prefix.rstrip()]


def _wrap_preserved_content_lines(
    prefix: str,
    value: str,
    width: int,
) -> list[str]:
    """Wrap content while preserving each LF as a safe continued line.

    The first source line receives ``prefix`` and nonempty later source lines
    receive matching indentation. Empty source lines remain empty. The result
    retains trailing source lines and boundary whitespace; callback ownership
    remains with the caller.
    """

    continuation_prefix = " " * len(prefix)
    rendered: list[str] = []
    for line_number, content_line in enumerate(value.split("\n")):
        line_prefix = prefix if line_number == 0 else continuation_prefix
        wrapper = textwrap.TextWrapper(
            width=width,
            initial_indent=line_prefix,
            subsequent_indent=continuation_prefix,
            break_long_words=False,
            break_on_hyphens=False,
            replace_whitespace=False,
            drop_whitespace=False,
        )
        rendered.extend(wrapper.wrap(content_line) or [line_prefix.rstrip()])
    return rendered


def _redaction_variants(secrets: Iterable[str]) -> tuple[str, ...]:
    """Build deterministic raw, escaped, and normalized secret spellings."""

    if isinstance(secrets, str):
        candidates = (secrets,)
    else:
        try:
            candidates = tuple(secrets)
        except TypeError as exc:
            raise TypeError("secrets must be an iterable of strings") from exc
    raw_variants: set[str] = set()
    for secret in candidates:
        if not isinstance(secret, str):
            raise TypeError("each secret must be a string")
        if not secret:
            continue
        raw_variants.add(secret)
        raw_variants.add(json.dumps(secret, ensure_ascii=True)[1:-1])
        raw_variants.add(json.dumps(secret, ensure_ascii=False)[1:-1])

    all_variants = set(raw_variants)
    for variant in tuple(raw_variants):
        sanitized = sanitize_terminal_text(variant)
        all_variants.add(sanitized)
        collapsed = _single_line(sanitized)
        if collapsed:
            all_variants.add(collapsed)
        normalized = _control_normalized(variant)
        if normalized:
            all_variants.add(normalized)
    all_variants.discard("")
    return tuple(sorted(all_variants, key=lambda value: (-len(value), value)))


def _control_normalized(text: str) -> str:
    """Replace unsafe controls with spaces and collapse the result to one line."""

    normalized: list[str] = []
    for character in text:
        category = unicodedata.category(character)
        if category in {"Cc", "Cf", "Cs"} or character in {"\u2028", "\u2029"}:
            normalized.append(" ")
        else:
            normalized.append(character)
    return _single_line("".join(normalized))


def _prepare_error_message(
    custom: str | None,
    default: str,
    redactions: tuple[str, ...],
    limit: int,
) -> str:
    """Resolve, redact, sanitize, collapse, and bound one error message."""

    source = default if custom is None or not custom or custom.isspace() else custom
    for secret in redactions:
        source = source.replace(secret, _REDACTION)
    rendered = sanitize_terminal_text(source)
    for secret in redactions:
        rendered = rendered.replace(secret, _REDACTION)
    rendered = _single_line(rendered)
    if not rendered:
        rendered = default
    return _truncate(rendered, limit)


def _single_line(text: str) -> str:
    """Collapse arbitrary whitespace into one trimmed display line."""

    return " ".join(text.split())


def _truncate(text: str, limit: int) -> str:
    """Cap text at a code-point limit using the stable truncation marker."""

    if len(text) <= limit:
        return text
    return f"{text[: limit - len(_TRUNCATION_SUFFIX)]}{_TRUNCATION_SUFFIX}"


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
    "InteractiveTextViewer",
    "TextViewerExitKind",
    "TextViewerResult",
    "build_contextual_terminal",
    "build_stream_terminal",
    "project_composition_suggestions",
]
