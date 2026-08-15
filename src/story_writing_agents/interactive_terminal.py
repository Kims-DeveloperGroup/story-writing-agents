"""Safe, deterministic presentation primitives for the interactive terminal."""

from __future__ import annotations

import json
import re
import textwrap
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import KW_ONLY, dataclass
from enum import StrEnum
from types import MappingProxyType

from .agents import (
    AGENT_SPECS,
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
)
from .interactive_commands import COMMANDS, CommandSpec


class AttachmentKind(StrEnum):
    """How the current process obtained a story-session attachment."""

    CREATED = "created"
    ATTACHED = "attached"
    RESUMED = "resumed"


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
    """Stable user-visible categories selected by trusted controller code."""

    TTY_REQUIRED = "tty_required"
    WORKSPACE_NOT_INITIALIZED = "workspace_not_initialized"
    WORKSPACE_INVALID = "workspace_invalid"
    WORKSPACE_UNSAFE = "workspace_unsafe"
    CONFIGURATION_INVALID = "configuration_invalid"
    COMMAND_INVALID = "command_invalid"
    AGENT_INVALID = "agent_invalid"
    SESSION_INVALID = "session_invalid"
    SESSION_EXISTS = "session_exists"
    SESSION_NOT_FOUND = "session_not_found"
    SESSION_IN_USE = "session_in_use"
    SESSION_INACTIVE = "session_inactive"
    CONVERSATION_REQUIRED = "conversation_required"
    CONVERSATION_INVALID = "conversation_invalid"
    CONVERSATION_NOT_FOUND = "conversation_not_found"
    CONVERSATION_AMBIGUOUS = "conversation_ambiguous"
    CONVERSATION_ARCHIVED = "conversation_archived"
    CONVERSATION_CREATE_FAILED = "conversation_create_failed"
    TRANSCRIPT_INVALID = "transcript_invalid"
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


INTERACTIVE_ERROR_SPECS: Mapping[
    InteractiveErrorCode, InteractiveErrorSpec
] = MappingProxyType(
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
        InteractiveErrorCode.SESSION_IN_USE: InteractiveErrorSpec(
            "The story session is in use.",
            "Choose another session or wait for its owner to detach.",
        ),
        InteractiveErrorCode.SESSION_INACTIVE: InteractiveErrorSpec(
            "The story session is inactive.",
            "Resume the session before starting provider work.",
        ),
        InteractiveErrorCode.CONVERSATION_REQUIRED: InteractiveErrorSpec(
            "No conversation is selected.",
            "Create, list, or use a conversation first.",
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
            "Inspect retained temporary or lease state before retrying.",
        ),
        InteractiveErrorCode.INTERNAL_ERROR: InteractiveErrorSpec(
            "An internal error occurred.",
            "Retry once; if it repeats, report the internal_error code.",
        ),
    }
)


_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_AGENT_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        LEAD_ORCHESTRATOR: "lead",
        PLOT_ARCHITECT: "plot",
        CHARACTER_CUSTODIAN: "character",
        EXECUTION_SPECIALIST: "execution",
    }
)
_AGENT_DISPLAY_NAMES: Mapping[str, str] = MappingProxyType(
    {agent_id: AGENT_SPECS[agent_id].display_name for agent_id in _AGENT_ALIASES}
)
_TRUNCATION_SUFFIX = " ...[truncated]"
_SUMMARY_LIMIT = 512
_HINT_LIMIT = 256
_REDACTION = "<redacted>"


def sanitize_terminal_text(text: str) -> str:
    """Return display text whose control code points cannot affect a terminal."""

    if not isinstance(text, str):
        raise TypeError("terminal text must be a string")
    rendered: list[str] = []
    for character in text:
        if character == "\n":
            rendered.append(character)
            continue
        category = unicodedata.category(character)
        if category in {"Cc", "Cf", "Cs"} or character in {"\u2028", "\u2029"}:
            rendered.append(_visible_escape(character))
        else:
            rendered.append(character)
    return "".join(rendered)


class InteractiveTerminal:
    """Render plain terminal records and collect one input operation at a time."""

    __slots__ = ("_read_line", "_width", "_write_text")

    def __init__(
        self,
        read_line: Callable[[str], str],
        write_text: Callable[[str], None],
        *,
        width: int = 80,
    ) -> None:
        """Bind borrowed synchronous I/O callbacks and a fixed wrap width."""

        if not callable(read_line):
            raise TypeError("read_line must be callable")
        if not callable(write_text):
            raise TypeError("write_text must be callable")
        if not isinstance(width, int) or isinstance(width, bool):
            raise TypeError("width must be an integer")
        if width < 20:
            raise ValueError("width must be at least 20")
        self._read_line = read_line
        self._write_text = write_text
        self._width = width

    def read_main(
        self,
        *,
        session_id: str | None = None,
        agent_id: str | None = None,
    ) -> TerminalAction:
        """Read one unparsed main-prompt line or terminal control event."""

        prompt = _main_prompt(session_id=session_id, agent_id=agent_id)
        try:
            line = self._read_line(prompt)
        except KeyboardInterrupt:
            return TerminalAction(TerminalActionKind.INTERRUPT)
        except EOFError:
            return TerminalAction(TerminalActionKind.EOF)
        return TerminalAction(TerminalActionKind.LINE, text=_validated_input_line(line))

    def request_conversation_subject(self) -> TerminalAction:
        """Read one unparsed subject line or return interrupt/EOF without output.

        The call owns no buffer and performs one synchronous read using the
        fixed subject prompt. Callback and line-framing failures propagate;
        no catalog, selection, or cleanup resource is mutated here.
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
        suffix = "[Y/n] " if default else "[y/N] "
        prompt = f"{prompt_message} {suffix}"
        while True:
            try:
                answer = _validated_input_line(self._read_line(prompt))
            except KeyboardInterrupt:
                return ConfirmationResult.INTERRUPT
            except EOFError:
                return ConfirmationResult.EOF

            normalized = answer.strip().casefold()
            if not normalized:
                return (
                    ConfirmationResult.YES
                    if default
                    else ConfirmationResult.NO
                )
            if normalized in {"y", "yes"}:
                return ConfirmationResult.YES
            if normalized in {"n", "no"}:
                return ConfirmationResult.NO
            self._write_block("[hint] Enter y, yes, n, or no.")

    def compose(
        self,
        mode: ComposeMode,
        *,
        agent_id: str | None = None,
    ) -> TerminalAction:
        """Collect one exact chat or consensus composition synchronously."""

        alias = _compose_alias(mode, agent_id)
        if mode is ComposeMode.CHAT:
            assert alias is not None
            introduction = (
                f"Target: {alias}. Use /send to submit or /cancel to discard."
            )
        else:
            introduction = (
                "Consensus run. Use /send to submit or /cancel to discard."
            )
        self.write_status(TerminalMessageKind.COMPOSE, introduction)

        lines: list[str] = []
        while True:
            prompt = _compose_prompt(mode, alias, len(lines) + 1)
            try:
                line = _validated_input_line(self._read_line(prompt))
            except KeyboardInterrupt:
                return self._record_discarded_composition(
                    TerminalActionKind.INTERRUPT,
                    lines,
                )
            except EOFError:
                return self._record_discarded_composition(
                    TerminalActionKind.EOF,
                    lines,
                )

            if line == "/send":
                content = "\n".join(lines)
                if not content or content.isspace():
                    self.write_status(
                        TerminalMessageKind.COMPOSE,
                        "Nothing to send.",
                    )
                    continue
                action = TerminalAction(
                    TerminalActionKind.SEND,
                    text=content,
                    line_count=len(lines),
                    character_count=len(content),
                )
                target = alias if alias is not None else "consensus"
                self.write_status(
                    TerminalMessageKind.COMPOSE,
                    (
                        f"Sending {action.line_count} "
                        f"{_plural(action.line_count, 'line')}, "
                        f"{action.character_count} "
                        f"{_plural(action.character_count, 'character')} "
                        f"to {target}."
                    ),
                )
                return action

            if line == "/cancel":
                return self._record_discarded_composition(
                    TerminalActionKind.CANCEL,
                    lines,
                )
            if line == "/say /send":
                line = "/send"
            elif line == "/say /cancel":
                line = "/cancel"
            lines.append(line)

    def write_lobby_orientation(self, workspace: str) -> None:
        """Render the stable startup orientation for an unattached lobby."""

        workspace_text = _inline_text(_required_text("workspace", workspace))
        self._write_block(
            "\n".join(
                (
                    "Story chat",
                    f"Workspace: {workspace_text}",
                    "Session: lobby",
                    (
                        "Provider calls are unavailable in the lobby. Use "
                        "/session new NAME, /session resume [NAME], or "
                        "/session list."
                    ),
                    "Type /help for commands; /quit exits.",
                )
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
        selection; rendering performs no persistence or resource mutation.
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

        lines = [
            "Story chat",
            f"Workspace: {workspace_text}",
            f"Session: {safe_session} ({attachment.value})",
            f"Target: {_AGENT_DISPLAY_NAMES[agent_id]}",
        ]
        if history_count is not None:
            lines.extend(
                (
                    (
                        f"History: {history_count} visible room "
                        f"{_plural(history_count, 'event')}"
                    ),
                    (
                        "Room messages are saved locally and shared with "
                        "addressed agents."
                    ),
                )
            )
        lines.append(
            "Type /help for commands; /quit leaves the story session active."
        )
        self._write_block("\n".join(lines))

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
        conversation-lobby form. Selected values are validated, sanitized,
        and width-wrapped with the stable short agent alias before one terminal
        record is written; writer failures propagate without cleanup here.
        """

        safe_session = _safe_identifier("session_id", session_id)
        agent_alias = _agent_alias(agent_id)
        if conversation_handle is None and conversation_subject is None:
            scope = (
                f"Session: {safe_session} | Conversation: none | "
                f"Agent: {agent_alias}"
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
            scope = (
                f"Session: {safe_session} | Conversation: {safe_handle} | "
                f"Subject: {safe_subject} | Agent: {agent_alias}"
            )
        self._write_block(
            "\n".join(_wrap_prefixed("[context] ", scope, self._width))
        )

    def write_conversation_list_entry(
        self,
        conversation_handle: str,
        conversation_subject: str,
        *,
        archived: bool,
        selected: bool,
    ) -> None:
        """Render one bounded catalog row from validated display metadata.

        ``archived`` selects the lifecycle label and ``selected`` marks the
        current in-memory choice; an archived selected row is rejected. Values
        are sanitized and written once, with no catalog or selection mutation.
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
        if type(selected) is not bool:
            raise TypeError("selected must be a bool")
        if archived and selected:
            raise ValueError("an archived conversation cannot be selected")
        status = "archived" if archived else "active"
        selection = "selected" if selected else "not selected"
        entry = (
            f"{safe_handle} | {status} | {selection} | Subject: {safe_subject}"
        )
        self._write_block(
            "\n".join(
                _wrap_prefixed("[conversation] ", entry, self._width)
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
        self._write_block(f"[run {safe_id}] {_required_text('text', text)}")

    def write_agent(self, agent_id: str, text: str) -> None:
        """Write one validated agent response without parsing its content."""

        alias = _agent_alias(agent_id)
        self._write_block(f"{alias}> {_required_text('text', text)}")

    def write_help(self, command: str | None = None) -> None:
        """Render full or contextual help from the parser-owned registry."""

        if command is None:
            lines = self._full_help_lines(COMMANDS)
        else:
            spec = _find_command(command, COMMANDS)
            lines = self._context_help_lines(spec)
        self._write_block("\n".join(lines))

    def write_error(
        self,
        error: InteractiveError,
        *,
        secrets: Iterable[str] = (),
    ) -> None:
        """Render one typed, redacted, bounded error and actionable hint."""

        if not isinstance(error, InteractiveError):
            raise TypeError("error must be an InteractiveError")
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
        lines = _wrap_prefixed(
            f"[error {error.code.value}] ",
            summary,
            self._width,
        )
        lines.extend(_wrap_prefixed("[hint] ", hint, self._width))
        self._write_block("\n".join(lines))

    def _record_discarded_composition(
        self,
        kind: TerminalActionKind,
        lines: list[str],
    ) -> TerminalAction:
        """Report a discarded compose buffer and return its count-only action."""

        content = "\n".join(lines)
        action = TerminalAction(
            kind,
            line_count=len(lines),
            character_count=len(content),
        )
        self.write_status(
            TerminalMessageKind.COMPOSE,
            (
                f"Discarded {action.line_count} "
                f"{_plural(action.line_count, 'line')}. "
                "No message was saved or sent."
            ),
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


def _visible_escape(character: str) -> str:
    """Format one unsafe code point as a lowercase visible ASCII escape."""

    value = ord(character)
    if value <= 0xFF:
        return f"\\x{value:02x}"
    if value <= 0xFFFF:
        return f"\\u{value:04x}"
    return f"\\U{value:08x}"


def _safe_identifier(name: str, value: object) -> str:
    """Return a string that satisfies the shared safe-identifier grammar."""

    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError(
            f"{name} must match [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}"
        )
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
]
