"""Contract tests for safe, provider-free interactive terminal presentation."""

from __future__ import annotations

import ast
import inspect
import json
import os
import socket
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from dataclasses import MISSING, FrozenInstanceError, fields, replace
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from unittest import mock

import story_writing_agents
from story_writing_agents import (
    AGENT_SPECS,
    CHARACTER_CUSTODIAN,
    COMMANDS,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
)
from story_writing_agents import interactive_commands as commands_module
from story_writing_agents import interactive_terminal as terminal_module
from story_writing_agents.interactive_commands import AGENT_ROUTES
from story_writing_agents.interactive_terminal import (
    INTERACTIVE_ERROR_SPECS,
    AttachmentKind,
    Choice,
    ComposeMode,
    ConfirmationResult,
    ControlOutcome,
    ControlOutcomeKind,
    InteractiveError,
    InteractiveErrorCode,
    InteractiveErrorSpec,
    InteractivePickerInput,
    InteractivePromptInput,
    InteractiveTerminal, InteractiveTranscriptViewer,
    Prompt,
    RawTerminalDriver,
    TerminalAction,
    TerminalActionKind,
    TerminalMessageKind,
    sanitize_terminal_text,
)
from story_writing_agents.interactive_terminal._render import render_plain


PUBLIC_EXPORTS = (
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
)

ERROR_DEFAULTS = {
    InteractiveErrorCode.TTY_REQUIRED: (
        "Interactive mode requires a terminal.",
        "Use the stepwise Python collaboration API without a terminal.",
    ),
    InteractiveErrorCode.WORKSPACE_NOT_INITIALIZED: (
        "No initialized story workspace was found.",
        "Run story init PATH, then try again.",
    ),
    InteractiveErrorCode.WORKSPACE_INVALID: (
        "The story workspace is invalid.",
        "Inspect the reported workspace file and repair it before retrying.",
    ),
    InteractiveErrorCode.WORKSPACE_UNSAFE: (
        "The requested workspace path is unsafe.",
        "Use a path inside the initialized workspace.",
    ),
    InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED: (
        "This workspace uses an unsupported workspace version.",
        "Migration and compatibility are unsupported; create a new workspace.",
    ),
    InteractiveErrorCode.CONFIGURATION_INVALID: (
        "Runtime configuration is invalid.",
        "Correct the reported configuration value and retry.",
    ),
    InteractiveErrorCode.COMMAND_INVALID: (
        "The interactive command is invalid.",
        "Type /help to see valid command syntax.",
    ),
    InteractiveErrorCode.AGENT_INVALID: (
        "The requested agent is invalid.",
        "Type /agents to see available agents.",
    ),
    InteractiveErrorCode.SESSION_INVALID: (
        "The story session identifier is invalid.",
        "Use letters, digits, underscores, or hyphens in a safe session name.",
    ),
    InteractiveErrorCode.SESSION_EXISTS: (
        "The story session already exists.",
        "Resume it or choose a different session name.",
    ),
    InteractiveErrorCode.SESSION_NOT_FOUND: (
        "The story session was not found.",
        "List sessions or create a new one.",
    ),
    InteractiveErrorCode.SESSION_NOT_ATTACHED: (
        "No story session is attached to this shell.",
        "Create or resume a session before using this command.",
    ),
    InteractiveErrorCode.SESSION_PAUSED: (
        "The story session is paused.",
        "Resume the session to make it ready before provider work.",
    ),
    InteractiveErrorCode.SESSION_ACCESS_BLOCKED: (
        "Story-session access is held by another live local process.",
        "Wait for that process to release access, then resume the session.",
    ),
    InteractiveErrorCode.SESSION_OWNERSHIP_UNKNOWN: (
        "Story-session access ownership cannot be verified.",
        "Do not remove the claim; inspect recovery guidance before retrying.",
    ),
    InteractiveErrorCode.CONVERSATION_REQUIRED: (
        "No conversation has current focus.",
        "Create or choose a conversation first.",
    ),
    InteractiveErrorCode.CONVERSATION_INVALID: (
        "The conversation input is invalid.",
        (
            "Provide a valid subject or a full or unique ID prefix of at "
            "least 12 characters."
        ),
    ),
    InteractiveErrorCode.CONVERSATION_NOT_FOUND: (
        "No matching conversation was found.",
        "Use `/conversation list [--all] [QUERY]` to find an ID.",
    ),
    InteractiveErrorCode.CONVERSATION_AMBIGUOUS: (
        "The conversation ID prefix is ambiguous.",
        "Use a longer prefix or the full ID.",
    ),
    InteractiveErrorCode.CONVERSATION_ARCHIVED: (
        "The conversation is archived.",
        "Choose an active conversation or create a new one.",
    ),
    InteractiveErrorCode.CONVERSATION_CREATE_FAILED: (
        "A unique conversation ID could not be created.",
        "Try creating the conversation again.",
    ),
    InteractiveErrorCode.TRANSCRIPT_INVALID: (
        "The conversation transcript is invalid.",
        "Inspect and repair the reported transcript before retrying.",
    ),
    InteractiveErrorCode.RETRIEVAL_INVALID: (
        "The agent returned an invalid retrieval action.",
        "Start a new turn; if this repeats, check provider compatibility.",
    ),
    InteractiveErrorCode.RETRIEVAL_BUDGET_EXHAUSTED: (
        "The conversation retrieval budget was exhausted.",
        "Ask a narrower question or use /search and /transcript directly.",
    ),
    InteractiveErrorCode.PROVIDER_PREFLIGHT_FAILED: (
        "Provider preflight failed.",
        "Check the provider executable, login, model, and configuration.",
    ),
    InteractiveErrorCode.PROVIDER_EXITED: (
        "The provider process exited unsuccessfully.",
        "Review the sanitized diagnostic and retry when the provider is ready.",
    ),
    InteractiveErrorCode.PROVIDER_TIMED_OUT: (
        "The provider operation timed out.",
        "Retry or increase the configured provider timeout.",
    ),
    InteractiveErrorCode.PROVIDER_PROTOCOL_ERROR: (
        "The provider returned an invalid response.",
        "Retry; if it repeats, inspect the sanitized diagnostic.",
    ),
    InteractiveErrorCode.DRAFT_INPUT_INVALID: (
        "The draft input is invalid.",
        "Provide one readable strict-UTF-8 draft and retry.",
    ),
    InteractiveErrorCode.CONSENSUS_DEADLOCK: (
        "The consensus run ended in deadlock.",
        "Revise the instruction and start a new run.",
    ),
    InteractiveErrorCode.CONSENSUS_FAILED: (
        "The consensus run failed.",
        "Fix the reported cause and start a new run.",
    ),
    InteractiveErrorCode.ARTIFACT_CONFLICT: (
        "An output artifact already exists.",
        "Choose a new output name; existing artifacts are not overwritten.",
    ),
    InteractiveErrorCode.OPERATION_CANCELLED: (
        "The operation was cancelled.",
        "The current session remains available; start again when ready.",
    ),
    InteractiveErrorCode.OPERATION_INTERRUPTED: (
        "The operation was interrupted.",
        "Inspect preserved state before starting a new operation.",
    ),
    InteractiveErrorCode.CLEANUP_FAILED: (
        "Cleanup did not complete.",
        "Inspect retained temporary or access-claim state before retrying.",
    ),
    InteractiveErrorCode.INTERNAL_ERROR: (
        "An internal error occurred.",
        "Retry once; if it repeats, report the internal_error code.",
    ),
}

_ARTIFACT_REFERENCE_GUIDANCE = (
    "Artifact references require an attached session and focused conversation. "
    "In provider-bound prompts, $ opens whole-file selection and completion; "
    "exact $$ immediately opens ranged selection, where Enter/Tab opens the "
    "highlighted source in the viewer."
)

AGENT_CASES = (
    (LEAD_ORCHESTRATOR, "lead", "Lead Orchestrator"),
    (PLOT_ARCHITECT, "plot", "Plot & Structure Architect"),
    (
        CHARACTER_CUSTODIAN,
        "character",
        "Character & World Custodian",
    ),
    (
        EXECUTION_SPECIALIST,
        "execution",
        "Tone & Execution Specialist",
    ),
)


class ScriptedCallbacks:
    """Borrowed synchronous callbacks with observable prompts and records."""

    def __init__(self, *reads: object) -> None:
        self.reads = list(reads)
        self.prompts: list[str] = []
        self.records: list[str] = []

    def read_line(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.reads:
            raise AssertionError(f"unexpected input request: {prompt}")
        value = self.reads.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value  # type: ignore[return-value]

    def write_text(self, text: str) -> None:
        self.records.append(text)


def make_terminal(
    *reads: object,
    width: int = 80,
) -> tuple[InteractiveTerminal, ScriptedCallbacks]:
    callbacks = ScriptedCallbacks(*reads)
    terminal = InteractiveTerminal(
        callbacks.read_line,
        callbacks.write_text,
        width=width,
    )
    return terminal, callbacks


def expected_composer_prompt(
    mode: str,
    *,
    line: int,
    line_count: int,
    character_count: int,
    target: str | None = None,
) -> str:
    """Return the exact wide callback projection for one composer read."""

    fields = [f"Mode: {mode}"]
    if target is not None:
        fields.append(f"Target: {target}")
    fields.extend(
        (
            f"Next line: {line}",
            f"Lines: {line_count}",
            f"Characters: {character_count}",
            "[info] Use /send to submit or /cancel to discard.",
        )
    )
    scope = f"chat:{target}:{line}" if target is not None else f"run:{line}"
    return "\n".join(fields) + f"\ncompose[{scope}]> "


def expected_confirmation_prompt(
    message: str,
    *,
    default: bool,
    invalid: bool = False,
) -> str:
    """Return the exact wide callback projection for one confirmation read."""

    suffix = "[Y/n]" if default else "[y/N]"
    choices = "Choices [error]" if invalid else "Choices"
    default_text = "yes" if default else "no"
    return (
        f"Question: {message}\n"
        f"{choices}: y/yes or n/no\n"
        f"Default: {default_text}\n"
        f"confirm {suffix}> "
    )


def expected_session_orientation(
    *,
    attachment: AttachmentKind,
    display_name: str,
    history_count: int,
) -> str:
    event_word = "event" if history_count == 1 else "events"
    return (
        f"--- Story chat {'-' * 65}\n"
        "  Workspace: /stories/example\n"
        f"  Session: SESSION ({attachment.value})\n"
        f"  Target: {display_name}\n"
        f"  History: {history_count} visible room {event_word}\n"
        "  [info] Room messages are saved locally and shared with addressed agents.\n"
        "  [info] Artifact references require an attached session and focused "
        "conversatio\n"
        "         n. In provider-bound prompts, $ opens whole-file selection and "
        "completi\n"
        "         on; exact $$ immediately opens ranged selection, where Enter/Tab "
        "opens \n"
        "         the highlighted source in the viewer.\n"
        "  [info] Type /help for commands; /quit releases access without pausing.\n"
        f"{'-' * 80}\n"
    )


def expected_error_record(
    code: InteractiveErrorCode,
    *,
    width: int,
    summary: str | None = None,
    hint: str | None = None,
    debug_log_path: str | None = None,
) -> str:
    """Render the exact stabilized semantic tree for one typed error."""

    spec = INTERACTIVE_ERROR_SPECS[code]
    notices = [
        terminal_module.Notice(
            spec.summary if summary is None else summary,
            label=f"error {code.value}",
            role=terminal_module.SemanticRole.ERROR,
            state=terminal_module.ViewState.ERROR,
        ),
        terminal_module.Notice(
            spec.hint if hint is None else hint,
            label="hint",
        ),
    ]
    if debug_log_path is not None:
        notices.append(terminal_module.Notice(debug_log_path, label="debug log"))
    component = terminal_module.Panel(
        "Interactive error",
        tuple(notices),
        state=terminal_module.ViewState.ERROR,
    )
    return f"{render_plain(component, width=width).text}\n"


def visible_escape(character: str) -> str:
    value = ord(character)
    if value <= 0xFF:
        return f"\\x{value:02x}"
    if value <= 0xFFFF:
        return f"\\u{value:04x}"
    return f"\\U{value:08x}"


class PublicContractTests(unittest.TestCase):
    def test_direct_module_public_surface_is_exact_and_ordered(self) -> None:
        self.assertEqual(tuple(terminal_module.__all__), PUBLIC_EXPORTS)
        self.assertNotIn("build_contextual_terminal", terminal_module.__all__)
        self.assertIs(terminal_module.InteractiveTranscriptViewer, InteractiveTranscriptViewer)
        self.assertEqual(tuple(inspect.signature(InteractiveTranscriptViewer.open).parameters),
            ("self", "events", "request", "result", "continuation", "show_details", "history_events"))
        for name in PUBLIC_EXPORTS[:-1]:
            with self.subTest(name=name):
                self.assertIs(getattr(terminal_module, name), globals()[name])
        for changed_callable in (
            ConfirmationResult,
            InteractiveTerminal.request_conversation_subject,
            InteractiveTerminal.request_confirmation,
            InteractiveTerminal.write_session_orientation,
            InteractiveTerminal.write_conversation_scope,
            InteractiveTerminal.write_conversation_list_entry,
            InteractiveTerminal.write_conversation_search_hit,
            InteractiveTerminal.write_conversation_transcript_event,
            InteractiveTerminal.write_agent_roster,
        ):
            with self.subTest(docstring=changed_callable.__qualname__):
                self.assertTrue(inspect.getdoc(changed_callable))

    def test_enums_have_exact_ordered_members_and_values(self) -> None:
        expected = {
            AttachmentKind: (
                ("CREATED", "created"),
                ("OPENED", "opened"),
                ("REOPENED", "reopened"),
            ),
            ComposeMode: (("CHAT", "chat"),),
            ConfirmationResult: (
                ("YES", "yes"),
                ("NO", "no"),
                ("INTERRUPT", "interrupt"),
                ("EOF", "eof"),
            ),
            TerminalActionKind: (
                ("LINE", "line"),
                ("SEND", "send"),
                ("CANCEL", "cancel"),
                ("INTERRUPT", "interrupt"),
                ("EOF", "eof"),
            ),
            TerminalMessageKind: (
                ("COMPOSE", "compose"),
                ("SESSION", "session"),
                ("CONVERSATION", "conversation"),
                ("ROUTING", "routing"),
                ("CONTEXT", "context"),
                ("WAITING", "waiting"),
                ("RESULT", "result"),
                ("CANCELLED", "cancelled"),
                ("HINT", "hint"),
            ),
            InteractiveErrorCode: (
                ("TTY_REQUIRED", "tty_required"),
                ("WORKSPACE_NOT_INITIALIZED", "workspace_not_initialized"),
                ("WORKSPACE_INVALID", "workspace_invalid"),
                ("WORKSPACE_UNSAFE", "workspace_unsafe"),
                (
                    "WORKSPACE_VERSION_UNSUPPORTED",
                    "workspace_version_unsupported",
                ),
                ("CONFIGURATION_INVALID", "configuration_invalid"),
                ("COMMAND_INVALID", "command_invalid"),
                ("AGENT_INVALID", "agent_invalid"),
                ("SESSION_INVALID", "session_invalid"),
                ("SESSION_EXISTS", "session_exists"),
                ("SESSION_NOT_FOUND", "session_not_found"),
                ("SESSION_NOT_ATTACHED", "session_not_attached"),
                ("SESSION_PAUSED", "session_paused"),
                ("SESSION_ACCESS_BLOCKED", "session_access_blocked"),
                ("SESSION_OWNERSHIP_UNKNOWN", "session_ownership_unknown"),
                ("CONVERSATION_REQUIRED", "conversation_required"),
                ("CONVERSATION_INVALID", "conversation_invalid"),
                ("CONVERSATION_NOT_FOUND", "conversation_not_found"),
                ("CONVERSATION_AMBIGUOUS", "conversation_ambiguous"),
                ("CONVERSATION_ARCHIVED", "conversation_archived"),
                (
                    "CONVERSATION_CREATE_FAILED",
                    "conversation_create_failed",
                ),
                ("TRANSCRIPT_INVALID", "transcript_invalid"),
                ("RETRIEVAL_INVALID", "retrieval_invalid"),
                (
                    "RETRIEVAL_BUDGET_EXHAUSTED",
                    "retrieval_budget_exhausted",
                ),
                ("PROVIDER_PREFLIGHT_FAILED", "provider_preflight_failed"),
                ("PROVIDER_EXITED", "provider_exited"),
                ("PROVIDER_TIMED_OUT", "provider_timed_out"),
                ("PROVIDER_PROTOCOL_ERROR", "provider_protocol_error"),
                ("DRAFT_INPUT_INVALID", "draft_input_invalid"),
                ("CONSENSUS_DEADLOCK", "consensus_deadlock"),
                ("CONSENSUS_FAILED", "consensus_failed"),
                ("ARTIFACT_CONFLICT", "artifact_conflict"),
                ("OPERATION_CANCELLED", "operation_cancelled"),
                ("OPERATION_INTERRUPTED", "operation_interrupted"),
                ("CLEANUP_FAILED", "cleanup_failed"),
                ("INTERNAL_ERROR", "internal_error"),
            ),
        }
        for enum_type, members in expected.items():
            with self.subTest(enum_type=enum_type.__name__):
                self.assertTrue(issubclass(enum_type, StrEnum))
                self.assertEqual(
                    tuple((item.name, item.value) for item in enum_type),
                    members,
                )

    def test_dataclass_schemas_defaults_keyword_rules_and_storage_are_exact(
        self,
    ) -> None:
        expected_fields = {
            TerminalAction: (
                ("kind", False, MISSING),
                ("text", True, None),
                ("line_count", True, 0),
                ("character_count", True, 0),
            ),
            InteractiveErrorSpec: (
                ("summary", False, MISSING),
                ("hint", False, MISSING),
            ),
            InteractiveError: (
                ("code", False, MISSING),
                ("summary", True, None),
                ("hint", True, None),
            ),
        }
        instances = {
            TerminalAction: TerminalAction(TerminalActionKind.CANCEL),
            InteractiveErrorSpec: InteractiveErrorSpec("Summary", "Hint"),
            InteractiveError: InteractiveError(InteractiveErrorCode.INTERNAL_ERROR),
        }
        for value_type, expected in expected_fields.items():
            with self.subTest(value_type=value_type.__name__):
                actual = tuple(
                    (field.name, field.kw_only, field.default)
                    for field in fields(value_type)
                )
                self.assertEqual(actual, expected)
                self.assertTrue(value_type.__dataclass_params__.frozen)
                self.assertTrue(hasattr(value_type, "__slots__"))
                self.assertFalse(hasattr(instances[value_type], "__dict__"))

        with self.assertRaises(TypeError):
            TerminalAction(TerminalActionKind.LINE, "text")  # type: ignore[misc]
        with self.assertRaises(TypeError):
            InteractiveError(  # type: ignore[misc]
                InteractiveErrorCode.INTERNAL_ERROR,
                "summary",
            )
        with self.assertRaises(FrozenInstanceError):
            instances[TerminalAction].line_count = 2  # type: ignore[misc]

    def test_terminal_actions_accept_every_representative_valid_state(self) -> None:
        cases = (
            TerminalAction(TerminalActionKind.LINE, text=""),
            TerminalAction(TerminalActionKind.LINE, text="text"),
            TerminalAction(
                TerminalActionKind.SEND,
                text="one\ntwo",
                line_count=2,
                character_count=7,
            ),
            TerminalAction(TerminalActionKind.CANCEL),
            TerminalAction(
                TerminalActionKind.CANCEL,
                line_count=2,
                character_count=1,
            ),
            TerminalAction(
                TerminalActionKind.INTERRUPT,
                line_count=1,
                character_count=0,
            ),
            TerminalAction(TerminalActionKind.EOF),
        )
        self.assertEqual(
            tuple(action.kind for action in cases),
            (
                TerminalActionKind.LINE,
                TerminalActionKind.LINE,
                TerminalActionKind.SEND,
                TerminalActionKind.CANCEL,
                TerminalActionKind.CANCEL,
                TerminalActionKind.INTERRUPT,
                TerminalActionKind.EOF,
            ),
        )

    def test_terminal_actions_reject_invalid_kinds_content_and_counts(self) -> None:
        cases = (
            (("line",), {}, TypeError),
            ((TerminalActionKind.LINE,), {}, ValueError),
            ((TerminalActionKind.LINE,), {"text": "one\ntwo"}, ValueError),
            (
                (TerminalActionKind.LINE,),
                {"text": "one", "line_count": 1},
                ValueError,
            ),
            (
                (TerminalActionKind.SEND,),
                {"text": " ", "line_count": 1, "character_count": 1},
                ValueError,
            ),
            (
                (TerminalActionKind.SEND,),
                {"text": "one", "line_count": 2, "character_count": 3},
                ValueError,
            ),
            (
                (TerminalActionKind.SEND,),
                {"text": "one", "line_count": 1, "character_count": 2},
                ValueError,
            ),
            (
                (TerminalActionKind.SEND,),
                {"text": "one\r", "line_count": 1, "character_count": 4},
                ValueError,
            ),
            ((TerminalActionKind.CANCEL,), {"text": "draft"}, ValueError),
            (
                (TerminalActionKind.CANCEL,),
                {"line_count": 2, "character_count": 0},
                ValueError,
            ),
            ((TerminalActionKind.EOF,), {"line_count": True}, TypeError),
            ((TerminalActionKind.EOF,), {"line_count": -1}, ValueError),
            (
                (TerminalActionKind.EOF,),
                {"character_count": 1},
                ValueError,
            ),
        )
        for arguments, keywords, error_type in cases:
            with self.subTest(arguments=arguments, keywords=keywords):
                with self.assertRaises(error_type):
                    TerminalAction(*arguments, **keywords)  # type: ignore[arg-type]

    def test_error_specs_are_exhaustive_immutable_and_safe_defaults(self) -> None:
        self.assertIsInstance(INTERACTIVE_ERROR_SPECS, MappingProxyType)
        self.assertEqual(
            tuple(INTERACTIVE_ERROR_SPECS),
            tuple(InteractiveErrorCode),
        )
        self.assertEqual(
            {
                code: (spec.summary, spec.hint)
                for code, spec in INTERACTIVE_ERROR_SPECS.items()
            },
            ERROR_DEFAULTS,
        )
        for code, spec in INTERACTIVE_ERROR_SPECS.items():
            with self.subTest(code=code):
                self.assertIsInstance(spec, InteractiveErrorSpec)
                for value in (spec.summary, spec.hint):
                    self.assertTrue(value.strip())
                    self.assertNotIn("\r", value)
                    self.assertNotIn("\n", value)
                    value.encode("utf-8", errors="strict")
        with self.assertRaises(TypeError):
            INTERACTIVE_ERROR_SPECS[InteractiveErrorCode.INTERNAL_ERROR] = (
                InteractiveErrorSpec("Other", "Other")
            )

        invalid_specs = (
            ((None, "Hint"), TypeError),
            ((" ", "Hint"), ValueError),
            (("Summary\nmore", "Hint"), ValueError),
            (("Summary", "\ud800"), ValueError),
        )
        for arguments, error_type in invalid_specs:
            with self.subTest(arguments=arguments):
                with self.assertRaises(error_type):
                    InteractiveErrorSpec(*arguments)  # type: ignore[arg-type]

        with self.assertRaises(TypeError):
            InteractiveError("internal_error")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            InteractiveError(
                InteractiveErrorCode.INTERNAL_ERROR,
                summary=1,  # type: ignore[arg-type]
            )


class PromptAndRenderingTests(unittest.TestCase):
    def test_constructor_requires_callbacks_and_width_at_least_twenty(self) -> None:
        signature = inspect.signature(InteractiveTerminal)
        self.assertEqual(
            tuple(signature.parameters),
            ("read_line", "write_text", "width", "main_read_line"),
        )
        self.assertEqual(signature.parameters["width"].default, 80)
        self.assertEqual(
            signature.parameters["width"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        self.assertIsNone(signature.parameters["main_read_line"].default)
        self.assertEqual(
            signature.parameters["main_read_line"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        contextual_signature = inspect.signature(
            terminal_module.build_contextual_terminal
        )
        self.assertEqual(
            tuple(contextual_signature.parameters),
            (
                "read_line",
                "write_text",
                "main_read_line",
                "composition_read_line",
                "width",
            ),
        )
        self.assertTrue(
            all(
                contextual_signature.parameters[name].kind
                is inspect.Parameter.KEYWORD_ONLY
                for name in ("main_read_line", "composition_read_line", "width")
            )
        )
        self.assertEqual(contextual_signature.parameters["width"].default, 80)
        self.assertTrue(inspect.getdoc(terminal_module.build_contextual_terminal))
        subject_signature = inspect.signature(
            InteractiveTerminal.request_conversation_subject
        )
        self.assertEqual(tuple(subject_signature.parameters), ("self",))
        scope_signature = inspect.signature(
            InteractiveTerminal.write_conversation_scope
        )
        self.assertEqual(
            tuple(scope_signature.parameters),
            (
                "self",
                "session_id",
                "conversation_handle",
                "conversation_subject",
                "agent_id",
            ),
        )
        self.assertTrue(
            all(
                parameter.kind is inspect.Parameter.KEYWORD_ONLY
                for parameter in tuple(scope_signature.parameters.values())[1:]
            )
        )
        list_signature = inspect.signature(
            InteractiveTerminal.write_conversation_list_entry
        )
        self.assertEqual(
            tuple(list_signature.parameters),
            (
                "self",
                "conversation_handle",
                "conversation_subject",
                "archived",
                "focused",
            ),
        )
        self.assertTrue(
            all(
                list_signature.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
                for name in ("archived", "focused")
            )
        )
        search_signature = inspect.signature(
            InteractiveTerminal.write_conversation_search_hit
        )
        self.assertEqual(
            tuple(search_signature.parameters),
            (
                "self",
                "conversation_handle",
                "conversation_subject",
                "match_kind",
                "snippet",
                "archived",
                "citation_turn_id",
                "citation_sequence",
                "citation_kind",
            ),
        )
        self.assertTrue(
            all(
                search_signature.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
                for name in (
                    "archived",
                    "citation_turn_id",
                    "citation_sequence",
                    "citation_kind",
                )
            )
        )
        transcript_signature = inspect.signature(
            InteractiveTerminal.write_conversation_transcript_event
        )
        self.assertEqual(
            tuple(transcript_signature.parameters),
            (
                "self",
                "conversation_handle",
                "sequence",
                "turn_id",
                "kind",
                "sender",
                "recipient",
                "content",
            ),
        )
        self.assertTrue(
            all(
                transcript_signature.parameters[name].kind
                is inspect.Parameter.KEYWORD_ONLY
                for name in tuple(transcript_signature.parameters)[2:]
            )
        )
        self.assertEqual(
            tuple(inspect.signature(InteractiveTerminal.write_agent_roster).parameters),
            ("self",),
        )

        valid, _ = make_terminal(width=20)
        self.assertIsInstance(valid, InteractiveTerminal)
        for read_line, write_text, width, error_type in (
            (None, lambda text: None, 80, TypeError),
            (lambda prompt: "", None, 80, TypeError),
            (lambda prompt: "", lambda text: None, True, TypeError),
            (lambda prompt: "", lambda text: None, 19, ValueError),
        ):
            with self.subTest(width=width, read_line=read_line):
                with self.assertRaises(error_type):
                    InteractiveTerminal(  # type: ignore[arg-type]
                        read_line,
                        write_text,
                        width=width,
                    )
        with self.assertRaises(TypeError):
            InteractiveTerminal(
                lambda prompt: "",
                lambda text: None,
                main_read_line=object(),  # type: ignore[arg-type]
            )

    def test_semantic_adapters_always_request_transient_driver_lifecycle(self) -> None:
        picker_signature = inspect.signature(RawTerminalDriver.read_picker)
        prompt_signature = inspect.signature(RawTerminalDriver.read_prompt)
        for signature in (picker_signature, prompt_signature):
            with self.subTest(signature=signature):
                self.assertIn("transient", signature.parameters)
                self.assertEqual(signature.parameters["transient"].default, False)
                self.assertIs(
                    signature.parameters["transient"].kind,
                    inspect.Parameter.KEYWORD_ONLY,
                )

        terminal, _callbacks = make_terminal()
        picker_calls: list[dict[str, object]] = []
        prompt_calls: list[dict[str, object]] = []

        def picker_reader(_state, **kwargs):
            picker_calls.append(kwargs)
            return ControlOutcome(ControlOutcomeKind.COMMIT, text="choice")

        def prompt_reader(_view, **kwargs):
            prompt_calls.append(kwargs)
            return ControlOutcome(ControlOutcomeKind.COMMIT, text="line")

        terminal._picker_read = picker_reader
        terminal._prompt_read = prompt_reader
        picker = InteractivePickerInput(terminal)
        prompt = InteractivePromptInput(terminal)
        choice = Choice("choice", "Choice")

        self.assertEqual(
            picker.read_picker(Prompt("Picker"), (choice,), transient=True).text,
            "choice",
        )
        self.assertEqual(
            picker.read_picker(Prompt("Picker"), (choice,)).text,
            "choice",
        )
        self.assertEqual(
            prompt.read_prompt(Prompt("Prompt"), transient=True).text,
            "line",
        )
        self.assertEqual(prompt.read_prompt(Prompt("Prompt")).text, "line")
        self.assertEqual(
            picker_calls,
            [{"transient": True}, {"transient": True}],
        )
        self.assertEqual(
            prompt_calls,
            [{"transient": True}, {"transient": True}],
        )

    def test_main_prompts_are_exact_and_return_unparsed_lines(self) -> None:
        self.assertEqual(
            tuple(
                (
                    route.agent_id,
                    route.prompt_alias,
                    AGENT_SPECS[route.agent_id].display_name,
                )
                for route in AGENT_ROUTES
            ),
            AGENT_CASES,
        )
        terminal, callbacks = make_terminal("/run untouched")
        self.assertEqual(
            terminal.read_main(),
            TerminalAction(TerminalActionKind.LINE, text="/run untouched"),
        )
        self.assertEqual(callbacks.prompts, ["story[lobby]> "])
        self.assertEqual(callbacks.records, [])

        for agent_id, alias, _display_name in AGENT_CASES:
            with self.subTest(agent_id=agent_id):
                terminal, callbacks = make_terminal("message")
                action = terminal.read_main(
                    session_id="SESSION",
                    agent_id=agent_id,
                )
                self.assertEqual(
                    action,
                    TerminalAction(TerminalActionKind.LINE, text="message"),
                )
                self.assertEqual(
                    callbacks.prompts,
                    [f"story[SESSION:{alias}]> "],
                )

    def test_optional_main_reader_is_used_only_for_the_main_prompt(self) -> None:
        general_callbacks = ScriptedCallbacks("subject", "ordinary content", "/send")
        main_callbacks = ScriptedCallbacks("/status")
        terminal = InteractiveTerminal(
            general_callbacks.read_line,
            general_callbacks.write_text,
            main_read_line=main_callbacks.read_line,
        )

        self.assertEqual(
            terminal.read_main(),
            TerminalAction(TerminalActionKind.LINE, text="/status"),
        )
        self.assertEqual(
            terminal.request_conversation_subject(),
            TerminalAction(TerminalActionKind.LINE, text="subject"),
        )
        self.assertEqual(
            terminal.compose(ComposeMode.CHAT, agent_id=LEAD_ORCHESTRATOR),
            TerminalAction(
                TerminalActionKind.SEND,
                text="ordinary content",
                line_count=1,
                character_count=16,
            ),
        )
        self.assertEqual(main_callbacks.prompts, ["story[lobby]> "])
        self.assertEqual(
            general_callbacks.prompts,
            [
                "conversation[subject]> ",
                expected_composer_prompt(
                    "chat",
                    target="lead",
                    line=1,
                    line_count=0,
                    character_count=0,
                ),
                expected_composer_prompt(
                    "chat",
                    target="lead",
                    line=2,
                    line_count=1,
                    character_count=16,
                ),
            ],
        )
        self.assertEqual(main_callbacks.records, [])
        self.assertEqual(
            general_callbacks.records,
            [
                "[compose] Target: lead. Use /send to submit or /cancel to discard.\n",
                "[compose] Sending 1 line, 16 characters to lead.\n",
            ],
        )

    def test_contextual_factory_routes_each_operation_without_prompt_inference(
        self,
    ) -> None:
        ordinary_callbacks = ScriptedCallbacks("subject", "yes")
        main_callbacks = ScriptedCallbacks("/status")
        composition_callbacks = ScriptedCallbacks(
            "run text",
            "/send",
            "chat text",
            "/send",
        )
        terminal = terminal_module.build_contextual_terminal(
            ordinary_callbacks.read_line,
            ordinary_callbacks.write_text,
            main_read_line=main_callbacks.read_line,
            composition_read_line=composition_callbacks.read_line,
        )

        self.assertEqual(
            terminal.read_main(),
            TerminalAction(TerminalActionKind.LINE, text="/status"),
        )
        self.assertEqual(
            terminal.compose(ComposeMode.CHAT, agent_id=LEAD_ORCHESTRATOR),
            TerminalAction(
                TerminalActionKind.SEND,
                text="run text",
                line_count=1,
                character_count=8,
            ),
        )
        self.assertEqual(
            terminal.compose(ComposeMode.CHAT, agent_id=PLOT_ARCHITECT),
            TerminalAction(
                TerminalActionKind.SEND,
                text="chat text",
                line_count=1,
                character_count=9,
            ),
        )
        self.assertEqual(
            terminal.request_conversation_subject(),
            TerminalAction(TerminalActionKind.LINE, text="subject"),
        )
        self.assertIs(
            terminal.request_confirmation("Continue?", default=False),
            ConfirmationResult.YES,
        )

        self.assertEqual(main_callbacks.prompts, ["story[lobby]> "])
        self.assertEqual(
            composition_callbacks.prompts,
            [
                expected_composer_prompt(
                    "chat",
                    target="lead",
                    line=1,
                    line_count=0,
                    character_count=0,
                ),
                expected_composer_prompt(
                    "chat",
                    target="lead",
                    line=2,
                    line_count=1,
                    character_count=8,
                ),
                expected_composer_prompt(
                    "chat",
                    target="plot",
                    line=1,
                    line_count=0,
                    character_count=0,
                ),
                expected_composer_prompt(
                    "chat",
                    target="plot",
                    line=2,
                    line_count=1,
                    character_count=9,
                ),
            ],
        )
        self.assertEqual(
            ordinary_callbacks.prompts,
            [
                "conversation[subject]> ",
                expected_confirmation_prompt("Continue?", default=False),
            ],
        )

    def test_main_read_maps_terminal_events_and_propagates_bad_collaborators(
        self,
    ) -> None:
        for event, expected_kind in (
            (KeyboardInterrupt(), TerminalActionKind.INTERRUPT),
            (EOFError(), TerminalActionKind.EOF),
        ):
            with self.subTest(expected_kind=expected_kind):
                terminal, callbacks = make_terminal(event)
                self.assertEqual(
                    terminal.read_main(),
                    TerminalAction(expected_kind),
                )
                self.assertEqual(callbacks.prompts, ["story[lobby]> "])

        failure = RuntimeError("reader failed")
        terminal, _callbacks = make_terminal(failure)
        with self.assertRaisesRegex(RuntimeError, "reader failed"):
            terminal.read_main()
        for invalid in (None, "two\nlines", "carriage\rreturn"):
            with self.subTest(invalid=repr(invalid)):
                terminal, _callbacks = make_terminal(invalid)
                with self.assertRaises((TypeError, ValueError)):
                    terminal.read_main()
        terminal, callbacks = make_terminal("attached")
        self.assertEqual(
            terminal.read_main(session_id="SESSION"),
            TerminalAction(TerminalActionKind.LINE, text="attached"),
        )
        self.assertEqual(callbacks.prompts, ["story[SESSION]> "])
        terminal, callbacks = make_terminal("unused")
        with self.assertRaises(ValueError):
            terminal.read_main(agent_id=PLOT_ARCHITECT)
        self.assertEqual(callbacks.prompts, [])

    def test_private_reference_main_input_is_call_local_and_cleans_after_failure(
        self,
    ) -> None:
        terminal, callbacks = make_terminal("literal $")
        direct = terminal_module.InteractiveMainInput(terminal)
        choice_provider = mock.Mock(return_value=None)
        scope_provider = mock.Mock(return_value=None)
        paths_provider = mock.Mock(return_value=())
        text_reader = mock.Mock(return_value="unused")

        self.assertEqual(
            direct.read_main(choice_provider),
            TerminalAction(TerminalActionKind.LINE, text="literal $"),
        )
        self.assertEqual(callbacks.prompts, ["story[lobby]> "])
        scope_provider.assert_not_called()
        paths_provider.assert_not_called()
        text_reader.assert_not_called()

        observed: list[tuple[object, ...]] = []
        failure = RuntimeError("reference reader failed")

        def failing_read(*arguments: object) -> str:
            observed.append(arguments)
            if len(observed) == 1:
                with self.assertRaisesRegex(RuntimeError, "already active"):
                    direct.read_main_with_references(
                        choice_provider,
                        scope_provider,
                        paths_provider,
                        text_reader,
                    )
                raise failure
            return "recovered $"

        terminal._main_reference_read_line = failing_read
        direct = terminal_module.InteractiveMainInput(terminal)
        with self.assertRaises(RuntimeError) as raised:
            direct.read_main_with_references(
                choice_provider,
                scope_provider,
                paths_provider,
                text_reader,
                session_id="session",
                agent_id=PLOT_ARCHITECT,
            )
        self.assertIs(raised.exception, failure)
        self.assertEqual(len(observed), 1)
        prompt, choices, scope, paths, text = observed[0]
        self.assertEqual(prompt, "story[session:plot]> ")
        self.assertIs(choices, choice_provider)
        self.assertIs(scope, scope_provider)
        self.assertIs(paths, paths_provider)
        self.assertIs(text, text_reader)

        self.assertEqual(
            direct.read_main_with_references(
                choice_provider,
                scope_provider,
                paths_provider,
                text_reader,
            ),
            TerminalAction(TerminalActionKind.LINE, text="recovered $"),
        )
        self.assertEqual(len(observed), 2)

    def test_subject_prompt_is_exact_call_local_and_returns_typed_events(
        self,
    ) -> None:
        terminal, callbacks = make_terminal("  Chapter 1 opening  ")
        self.assertEqual(
            terminal.request_conversation_subject(),
            TerminalAction(
                TerminalActionKind.LINE,
                text="  Chapter 1 opening  ",
            ),
        )
        self.assertEqual(callbacks.prompts, ["conversation[subject]> "])
        self.assertEqual(callbacks.records, [])

        for event, expected in (
            (KeyboardInterrupt(), TerminalActionKind.INTERRUPT),
            (EOFError(), TerminalActionKind.EOF),
        ):
            with self.subTest(event=expected):
                terminal, callbacks = make_terminal(event)
                self.assertEqual(
                    terminal.request_conversation_subject(),
                    TerminalAction(expected),
                )
                self.assertEqual(
                    callbacks.prompts,
                    ["conversation[subject]> "],
                )
                self.assertEqual(callbacks.records, [])

        for value, error_type in (
            (None, TypeError),
            ("two\nlines", ValueError),
            (RuntimeError("reader failed"), RuntimeError),
        ):
            with self.subTest(value=repr(value)):
                terminal, _callbacks = make_terminal(value)
                with self.assertRaises(error_type):
                    terminal.request_conversation_subject()

    def test_confirmation_normalizes_answers_defaults_and_reprompts(self) -> None:
        cases = (
            (True, "", ConfirmationResult.YES),
            (False, "\u2003\u00a0", ConfirmationResult.NO),
            (False, "\u2003YeS\u00a0", ConfirmationResult.YES),
            (True, " N ", ConfirmationResult.NO),
            (True, "no", ConfirmationResult.NO),
            (False, "y", ConfirmationResult.YES),
        )
        for default, answer, expected in cases:
            with self.subTest(default=default, answer=answer):
                terminal, callbacks = make_terminal(answer)
                self.assertIs(
                    terminal.request_confirmation("Continue?", default=default),
                    expected,
                )
                self.assertEqual(
                    callbacks.prompts,
                    [expected_confirmation_prompt("Continue?", default=default)],
                )
                self.assertEqual(callbacks.records, [])

        terminal, callbacks = make_terminal("later", " MAYBE ", "yes")
        self.assertIs(
            terminal.request_confirmation("Resume?", default=False),
            ConfirmationResult.YES,
        )
        self.assertEqual(
            callbacks.prompts,
            [
                expected_confirmation_prompt("Resume?", default=False),
                expected_confirmation_prompt(
                    "Resume?",
                    default=False,
                    invalid=True,
                ),
                expected_confirmation_prompt(
                    "Resume?",
                    default=False,
                    invalid=True,
                ),
            ],
        )
        self.assertEqual(
            callbacks.records,
            [
                "[hint] Enter y, yes, n, or no.\n",
                "[hint] Enter y, yes, n, or no.\n",
            ],
        )

    def test_confirmation_sanitizes_prompts_and_returns_terminal_events(self) -> None:
        terminal, callbacks = make_terminal(KeyboardInterrupt())
        self.assertIs(
            terminal.request_confirmation("unsafe\n\x1b?", default=True),
            ConfirmationResult.INTERRUPT,
        )
        self.assertEqual(
            callbacks.prompts,
            [
                expected_confirmation_prompt(
                    "unsafe\\x0a\\x1b?",
                    default=True,
                )
            ],
        )
        self.assertEqual(callbacks.records, [])

        terminal, callbacks = make_terminal(EOFError())
        self.assertIs(
            terminal.request_confirmation("End?", default=False),
            ConfirmationResult.EOF,
        )
        self.assertEqual(
            callbacks.prompts,
            [expected_confirmation_prompt("End?", default=False)],
        )
        self.assertEqual(callbacks.records, [])

    def test_confirmation_rejects_bad_inputs_and_propagates_collaborator_failures(
        self,
    ) -> None:
        terminal, callbacks = make_terminal("unused")
        for message, default, error_type in (
            ("", True, ValueError),
            (" \t", True, ValueError),
            (None, True, TypeError),
            ("Continue?", 1, TypeError),
        ):
            with self.subTest(message=message, default=default):
                with self.assertRaises(error_type):
                    terminal.request_confirmation(  # type: ignore[arg-type]
                        message,
                        default=default,
                    )
        self.assertEqual(callbacks.prompts, [])

        for value, error_type in (
            (None, TypeError),
            ("two\nlines", ValueError),
            (RuntimeError("reader failed"), RuntimeError),
        ):
            with self.subTest(value=repr(value)):
                terminal, _callbacks = make_terminal(value)
                with self.assertRaises(error_type):
                    terminal.request_confirmation("Continue?", default=True)

        callbacks = ScriptedCallbacks("invalid")

        def failing_writer(_text: str) -> None:
            raise RuntimeError("writer failed")

        terminal = InteractiveTerminal(callbacks.read_line, failing_writer)
        with self.assertRaisesRegex(RuntimeError, "writer failed"):
            terminal.request_confirmation("Continue?", default=True)

    def test_lobby_orientation_is_one_exact_sanitized_record(self) -> None:
        terminal, callbacks = make_terminal()
        terminal.write_lobby_orientation("/stories/example")
        self.assertEqual(
            callbacks.records,
            [
                f"--- Story chat {'-' * 65}\n"
                "  Workspace: /stories/example\n"
                "  Session: lobby\n"
                "  [warning] Provider calls are unavailable in the lobby. Use "
                "/session create NAM\n"
                "            E, /session resume [NAME], or /session list.\n"
                "  [info] Artifact references require an attached session and focused "
                "conversatio\n"
                "         n. In provider-bound prompts, $ opens whole-file selection "
                "and completi\n"
                "         on; exact $$ immediately opens ranged selection, where "
                "Enter/Tab opens \n"
                "         the highlighted source in the viewer.\n"
                "  [info] Type /help for commands; /quit exits.\n"
                f"{'-' * 80}\n"
            ],
        )

        terminal, callbacks = make_terminal()
        terminal.write_lobby_orientation("safe\npath\x1b")
        self.assertIn("Workspace: safe\\x0apath\\x1b\n", callbacks.records[0])
        self.assertEqual(len(callbacks.records), 1)

    def test_session_orientation_covers_attachments_agents_and_pluralization(
        self,
    ) -> None:
        cases = []
        for attachment in AttachmentKind:
            cases.append((attachment, *AGENT_CASES[0], 1))
        for agent_case in AGENT_CASES:
            cases.append((AttachmentKind.OPENED, *agent_case, 2))
        for history_count in (0, 1, 2):
            cases.append(
                (
                    AttachmentKind.REOPENED,
                    *AGENT_CASES[1],
                    history_count,
                )
            )

        for attachment, agent_id, _alias, display_name, history_count in cases:
            with self.subTest(
                attachment=attachment,
                agent_id=agent_id,
                history_count=history_count,
            ):
                terminal, callbacks = make_terminal()
                terminal.write_session_orientation(
                    "/stories/example",
                    "SESSION",
                    attachment,
                    agent_id,
                    history_count,
                )
                self.assertEqual(
                    callbacks.records,
                    [
                        expected_session_orientation(
                            attachment=attachment,
                            display_name=display_name,
                            history_count=history_count,
                        )
                    ],
                )

    def test_session_orientation_omits_unknown_history_without_changing_counts(
        self,
    ) -> None:
        terminal, callbacks = make_terminal()
        terminal.write_session_orientation(
            "/stories/example",
            "SESSION",
            AttachmentKind.CREATED,
            LEAD_ORCHESTRATOR,
        )
        self.assertEqual(
            callbacks.records,
            [
                f"--- Story chat {'-' * 65}\n"
                "  Workspace: /stories/example\n"
                "  Session: SESSION (created)\n"
                "  Target: Lead Orchestrator\n"
                "  [info] Artifact references require an attached session and focused "
                "conversatio\n"
                "         n. In provider-bound prompts, $ opens whole-file selection "
                "and completi\n"
                "         on; exact $$ immediately opens ranged selection, where "
                "Enter/Tab opens \n"
                "         the highlighted source in the viewer.\n"
                "  [info] Type /help for commands; /quit releases access without pausing.\n"
                f"{'-' * 80}\n"
            ],
        )
        self.assertNotIn("History:", callbacks.records[0])
        self.assertNotIn("Room messages", callbacks.records[0])

        terminal, callbacks = make_terminal()
        terminal.write_session_orientation(
            "/stories/example",
            "SESSION",
            AttachmentKind.CREATED,
            LEAD_ORCHESTRATOR,
            0,
        )
        self.assertIn("History: 0 visible room events\n", callbacks.records[0])
        self.assertIn("Room messages are saved locally", callbacks.records[0])

    def test_conversation_scope_has_exact_focused_and_lobby_records(self) -> None:
        terminal, callbacks = make_terminal(width=200)
        terminal.write_conversation_scope(
            session_id="SESSION",
            conversation_handle="c_0123456789",
            conversation_subject="Opening scene",
            agent_id=PLOT_ARCHITECT,
        )
        terminal.write_conversation_scope(
            session_id="SESSION",
            conversation_handle=None,
            conversation_subject=None,
            agent_id=LEAD_ORCHESTRATOR,
        )
        self.assertEqual(
            callbacks.records,
            [
                f"--- Conversation scope {'-' * 177}\n"
                "  Session: SESSION\n"
                "  Conversation: c_0123456789\n"
                "  Subject: Opening scene\n"
                "  Agent: plot\n"
                f"{'-' * 200}\n",
                f"--- Conversation scope {'-' * 177}\n"
                "  Session: SESSION\n"
                "  Conversation: none\n"
                "  Agent: lead\n"
                f"{'-' * 200}\n",
            ],
        )

        terminal, callbacks = make_terminal(width=35)
        terminal.write_conversation_scope(
            session_id="SESSION",
            conversation_handle="c_0123456789",
            conversation_subject="Unsafe\nsubject\x1b and more words",
            agent_id=LEAD_ORCHESTRATOR,
        )
        rendered = callbacks.records[0]
        self.assertIn("Subject:", rendered)
        self.assertIn("Unsafe\\x0asubject\\x1b", rendered)
        self.assertTrue(rendered.startswith("--- Conversation scope"))
        self.assertTrue(rendered.endswith("\n"))
        rendered.encode("utf-8", errors="strict")

    def test_conversation_list_records_are_exact_bounded_and_validated(
        self,
    ) -> None:
        terminal, callbacks = make_terminal(width=200)
        terminal.write_conversation_list_entry(
            "c_0123456789",
            "Opening scene",
            archived=False,
            focused=True,
        )
        terminal.write_conversation_list_entry(
            "c_fedcba9876",
            "Old\nsubject\x1b",
            archived=True,
            focused=False,
        )
        self.assertEqual(
            callbacks.records,
            [
                "[conversation c_0123456789]\n"
                "  Status: active\n"
                "  Focus: focused\n"
                "  Subject: Opening scene\n",
                "[conversation c_fedcba9876]\n"
                "  Status: archived\n"
                "  Focus: not focused\n"
                "  Subject: Old\\x0asubject\\x1b\n",
            ],
        )

        invalid_calls = (
            lambda: terminal.write_conversation_scope(
                session_id="SESSION",
                conversation_handle="conversation",
                conversation_subject=None,
                agent_id=LEAD_ORCHESTRATOR,
            ),
            lambda: terminal.write_conversation_scope(
                session_id="unsafe/id",
                conversation_handle=None,
                conversation_subject=None,
                agent_id=LEAD_ORCHESTRATOR,
            ),
            lambda: terminal.write_conversation_list_entry(
                "unsafe/id",
                "Subject",
                archived=False,
                focused=False,
            ),
            lambda: terminal.write_conversation_list_entry(
                "conversation",
                " ",
                archived=False,
                focused=False,
            ),
            lambda: terminal.write_conversation_list_entry(
                "conversation",
                "Subject",
                archived=True,
                focused=True,
            ),
            lambda: terminal.write_conversation_list_entry(
                "conversation",
                "Subject",
                archived=1,  # type: ignore[arg-type]
                focused=False,
            ),
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises((TypeError, ValueError)):
                    call()

    def test_search_hits_have_exact_rows_citations_and_visible_controls(self) -> None:
        terminal, callbacks = make_terminal(width=240)
        terminal.write_conversation_search_hit("c_0123456789", "Opening scene", "subject", "council vessel", archived=False)
        terminal.write_conversation_search_hit("c_fedcba9876", "Old\nsubject", "event", "unsafe\x1b\ntext", archived=True,
            citation_turn_id="turn_7", citation_sequence=9, citation_kind="reply")
        self.assertEqual(len(callbacks.records), 2)
        first, second = callbacks.records
        self.assertTrue(first.startswith("╭─ search c_0123456789 "))
        self.assertTrue(second.startswith("╭─ search c_fedcba9876 "))
        self.assertLess(first.index("council vessel"), first.index("Metadata"))
        self.assertLess(second.index("unsafe"), second.index("Metadata"))
        self.assertIn("Status: active", first); self.assertIn("Match: subject", first)
        self.assertIn("Subject: Opening scene", first); self.assertIn("Status: archived", second)
        self.assertIn("Subject: Old\\x0asubject", second); self.assertIn("unsafe\\x1b\\x0atext", second)
        self.assertIn("Citation: conversation c_fedcba9876, turn turn_7, sequence 9, kind reply", second)
        self.assertNotIn("\x1b", first + second)
        self.assertTrue(all(len(row) <= 120 for row in (first + second).splitlines()))


    def test_search_hits_reject_invalid_primitives_and_partial_citations(
        self,
    ) -> None:
        terminal, callbacks = make_terminal()
        valid = ("c_0123456789", "Subject", "event", "snippet")
        invalid_calls = (
            lambda: terminal.write_conversation_search_hit(
                "unsafe/id", *valid[1:], archived=False
            ),
            lambda: terminal.write_conversation_search_hit(
                valid[0], " ", *valid[2:], archived=False
            ),
            lambda: terminal.write_conversation_search_hit(
                *valid[:2], "", valid[3], archived=False
            ),
            lambda: terminal.write_conversation_search_hit(
                *valid[:3], "", archived=False
            ),
            lambda: terminal.write_conversation_search_hit(
                *valid,
                archived=1,  # type: ignore[arg-type]
            ),
            lambda: terminal.write_conversation_search_hit(
                *valid,
                archived=False,
                citation_turn_id="turn_1",
            ),
            lambda: terminal.write_conversation_search_hit(
                *valid,
                archived=False,
                citation_turn_id="turn_1",
                citation_sequence=True,
                citation_kind="reply",
            ),
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises((TypeError, ValueError)):
                    call()
        self.assertEqual(callbacks.records, [])

    def test_transcript_events_preserve_whitespace_lines_and_escape_controls(
        self,
    ) -> None:
        terminal, callbacks = make_terminal(width=240)
        common = {
            "sequence": 2,
            "turn_id": "turn_2",
            "kind": "reply",
            "sender": "plot",
            "recipient": "user",
        }
        terminal.write_conversation_transcript_event(
            "c_0123456789",
            **common,
            content=" \t",
        )
        terminal.write_conversation_transcript_event(
            "c_0123456789",
            **common,
            content="first\n\nthird\n",
        )
        terminal.write_conversation_transcript_event(
            "c_0123456789",
            **common,
            content="\x1b[31mred\x00\nlast",
        )
        header = (
            "[transcript c_0123456789]\n"
            "  Sequence: 2\n"
            "  Turn: turn_2\n"
            "  Kind: reply\n"
            "  Sender: plot\n"
            "  Recipient: user\n"
            "  Content:\n"
        )
        self.assertEqual(
            callbacks.records,
            [
                header + "   \\x09\n",
                header + "  first\n\n  third\n\n",
                header + "  \\x1b[31mred\\x00\n  last\n",
            ],
        )
        self.assertEqual(len(callbacks.records), 3)
        self.assertNotIn("\x1b", "".join(callbacks.records))
        self.assertNotIn("\x00", "".join(callbacks.records))

        narrow_terminal, narrow_callbacks = make_terminal(width=24)
        narrow_terminal.write_conversation_transcript_event(
            "c_0123456789",
            **common,
            content="  alpha  \n   \n  beta gamma delta  ",
        )
        self.assertEqual(len(narrow_callbacks.records), 1)
        self.assertEqual(
            narrow_callbacks.records,
            [
                "[transcript c_0123456789\n"
                "]\n"
                " Sequence:\n"
                "  2\n"
                " Turn:\n"
                "  turn_2\n"
                " Kind:\n"
                "  reply\n"
                " Sender:\n"
                "  plot\n"
                " Recipient:\n"
                "  user\n"
                " Content:\n"
                "\n"
                "   alpha  \n"
                "    \n"
                "   beta gamma delta  \n"
            ],
        )

    def test_transcript_events_require_exact_nonempty_primitive_content(
        self,
    ) -> None:
        class TextSubclass(str):
            """Distinguish rejected string subclasses from built-in strings."""

        terminal, callbacks = make_terminal()
        base = {
            "sequence": 1,
            "turn_id": "turn_1",
            "kind": "message",
            "sender": "user",
            "recipient": "plot",
            "content": "body",
        }
        invalid_calls = (
            lambda: terminal.write_conversation_transcript_event("unsafe/id", **base),
            lambda: terminal.write_conversation_transcript_event(
                "c_0123456789", **{**base, "sequence": True}
            ),
            lambda: terminal.write_conversation_transcript_event(
                "c_0123456789", **{**base, "sequence": 0}
            ),
            lambda: terminal.write_conversation_transcript_event(
                "c_0123456789", **{**base, "turn_id": "unsafe/id"}
            ),
            lambda: terminal.write_conversation_transcript_event(
                "c_0123456789", **{**base, "kind": " "}
            ),
            lambda: terminal.write_conversation_transcript_event(
                "c_0123456789", **{**base, "content": ""}
            ),
            lambda: terminal.write_conversation_transcript_event(
                "c_0123456789", **{**base, "content": TextSubclass("body")}
            ),
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises((TypeError, ValueError)):
                    call()
        self.assertEqual(callbacks.records, [])

    def test_agent_roster_is_exact_ordered_wrapped_and_sanitized(self) -> None:
        terminal, callbacks = make_terminal(width=1000)
        terminal.write_agent_roster()
        self.assertEqual(
            callbacks.records,
            [
                "Agents (4)\n"
                "  [agent] Lead Orchestrator\n"
                "    ID: lead_orchestrator\n"
                "    Aliases: lead, orchestrator, lead_orchestrator\n"
                "    Advisory domain: Moderation, synthesis, and finalization.\n"
                "  [agent] Plot & Structure Architect\n"
                "    ID: plot_architect\n"
                "    Aliases: plot, plot_architect\n"
                "    Advisory domain: Plot, structure, flow, and pacing.\n"
                "  [agent] Character & World Custodian\n"
                "    ID: character_custodian\n"
                "    Aliases: character, character_custodian\n"
                "    Advisory domain: Characters, relationships, world, and "
                "continuity.\n"
                "  [agent] Tone & Execution Specialist\n"
                "    ID: execution_specialist\n"
                "    Aliases: execution, prose, execution_specialist\n"
                "    Advisory domain: Drafting, prose, point of view, tone, "
                "grammar, and format.\n"
            ],
        )

        terminal, callbacks = make_terminal(width=44)
        terminal.write_agent_roster()
        wrapped = callbacks.records[0]
        self.assertEqual(
            len(
                [line for line in wrapped.splitlines() if line.startswith("  [agent] ")]
            ),
            4,
        )
        self.assertIn("Agents (4)\n", wrapped)
        self.assertTrue(all(len(line) <= 44 for line in wrapped.splitlines()))

        original = AGENT_SPECS[LEAD_ORCHESTRATOR]
        unsafe = replace(original, display_name="Unsafe\x1bLead")
        terminal, callbacks = make_terminal(width=1000)
        with mock.patch.dict(
            AGENT_SPECS,
            {LEAD_ORCHESTRATOR: unsafe},
        ):
            terminal.write_agent_roster()
        self.assertIn(
            "[agent] Unsafe\\x1bLead\n    ID: lead_orchestrator",
            callbacks.records[0],
        )
        self.assertNotIn("\x1b", callbacks.records[0])

    def test_status_run_and_agent_records_use_exact_trusted_prefixes(self) -> None:
        for kind in TerminalMessageKind:
            with self.subTest(kind=kind):
                terminal, callbacks = make_terminal()
                terminal.write_status(kind, "ready")
                self.assertEqual(callbacks.records, [f"[{kind.value}] ready\n"])

        terminal, callbacks = make_terminal()
        terminal.write_run("DEBATE-1", "complete")
        self.assertEqual(callbacks.records, ["[run DEBATE-1]\n  complete\n"])

        for agent_id, alias, _display_name in AGENT_CASES:
            with self.subTest(agent_id=agent_id):
                terminal, callbacks = make_terminal()
                terminal.write_agent(agent_id, "reply")
                self.assertEqual(len(callbacks.records), 1)
                self.assertTrue(callbacks.records[0].startswith(f"╭─ {alias} "))
                self.assertIn("│ reply", callbacks.records[0])
                self.assertTrue(callbacks.records[0].splitlines()[-2].startswith("╰"))

    def test_every_written_block_is_sanitized_and_gets_one_record_lf(self) -> None:
        terminal, callbacks = make_terminal()
        terminal.write_status(
            TerminalMessageKind.CONTEXT,
            "# safe Markdown\n\x1b[31mred",
        )
        self.assertEqual(
            callbacks.records,
            ["[context] # safe Markdown\n\\x1b[31mred\n"],
        )
        self.assertEqual(callbacks.records[0].count("\n"), 2)

    def test_rendering_validation_and_writer_failures_propagate(self) -> None:
        terminal, _callbacks = make_terminal()
        invalid_calls = (
            lambda: terminal.write_status("hint", "text"),
            lambda: terminal.write_status(TerminalMessageKind.HINT, " "),
            lambda: terminal.write_run("unsafe/id", "text"),
            lambda: terminal.write_run("run", ""),
            lambda: terminal.write_agent("plot", "text"),
            lambda: terminal.write_agent(PLOT_ARCHITECT, "\t"),
            lambda: terminal.write_session_orientation(
                "/stories/example",
                "SESSION",
                "created",  # type: ignore[arg-type]
                PLOT_ARCHITECT,
                0,
            ),
            lambda: terminal.write_session_orientation(
                "/stories/example",
                "SESSION",
                AttachmentKind.CREATED,
                PLOT_ARCHITECT,
                True,
            ),
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises((TypeError, ValueError)):
                    call()

        def failing_writer(_text: str) -> None:
            raise RuntimeError("writer failed")

        terminal = InteractiveTerminal(lambda prompt: "", failing_writer)
        with self.assertRaisesRegex(RuntimeError, "writer failed"):
            terminal.write_status(TerminalMessageKind.RESULT, "complete")


class CompositionTests(unittest.TestCase):
    def test_chat_composition_has_exact_prompts_status_and_counts(self) -> None:
        terminal, callbacks = make_terminal("First", "Second", "/send")
        action = terminal.compose(ComposeMode.CHAT, agent_id=PLOT_ARCHITECT)
        self.assertEqual(
            action,
            TerminalAction(
                TerminalActionKind.SEND,
                text="First\nSecond",
                line_count=2,
                character_count=12,
            ),
        )
        self.assertEqual(
            callbacks.prompts,
            [
                expected_composer_prompt(
                    "chat",
                    target="plot",
                    line=1,
                    line_count=0,
                    character_count=0,
                ),
                expected_composer_prompt(
                    "chat",
                    target="plot",
                    line=2,
                    line_count=1,
                    character_count=5,
                ),
                expected_composer_prompt(
                    "chat",
                    target="plot",
                    line=3,
                    line_count=2,
                    character_count=12,
                ),
            ],
        )
        self.assertEqual(
            callbacks.records,
            [
                "[compose] Target: plot. Use /send to submit or /cancel to discard.\n",
                "[compose] Sending 2 lines, 12 characters to plot.\n",
            ],
        )

    def test_chat_composition_counts_unicode_code_points_not_bytes(self) -> None:
        terminal, callbacks = make_terminal("한글🙂", "/send")
        action = terminal.compose(ComposeMode.CHAT, agent_id=LEAD_ORCHESTRATOR)
        self.assertEqual(
            action,
            TerminalAction(
                TerminalActionKind.SEND,
                text="한글🙂",
                line_count=1,
                character_count=3,
            ),
        )
        self.assertEqual(
            callbacks.prompts,
            [
                expected_composer_prompt(
                    "chat",
                    target="lead",
                    line=1,
                    line_count=0,
                    character_count=0,
                ),
                expected_composer_prompt(
                    "chat",
                    target="lead",
                    line=2,
                    line_count=1,
                    character_count=3,
                ),
            ],
        )
        self.assertEqual(
            callbacks.records,
            [
                "[compose] Target: lead. Use /send to submit or /cancel to discard.\n",
                "[compose] Sending 1 line, 3 characters to lead.\n",
            ],
        )

    def test_blank_only_send_retries_without_advancing_the_prompt(self) -> None:
        terminal, callbacks = make_terminal(
            "",
            " \t",
            "/send",
            "message",
            "/send",
        )
        action = terminal.compose(ComposeMode.CHAT, agent_id=LEAD_ORCHESTRATOR)
        self.assertEqual(
            action,
            TerminalAction(
                TerminalActionKind.SEND,
                text="\n \t\nmessage",
                line_count=3,
                character_count=11,
            ),
        )
        self.assertEqual(
            callbacks.prompts,
            [
                expected_composer_prompt(
                    "chat",
                    target="lead",
                    line=1,
                    line_count=0,
                    character_count=0,
                ),
                expected_composer_prompt(
                    "chat",
                    target="lead",
                    line=2,
                    line_count=1,
                    character_count=0,
                ),
                expected_composer_prompt(
                    "chat",
                    target="lead",
                    line=3,
                    line_count=2,
                    character_count=3,
                ),
                expected_composer_prompt(
                    "chat",
                    target="lead",
                    line=3,
                    line_count=2,
                    character_count=3,
                ),
                expected_composer_prompt(
                    "chat",
                    target="lead",
                    line=4,
                    line_count=3,
                    character_count=11,
                ),
            ],
        )
        self.assertEqual(callbacks.records[1], "[compose] Nothing to send.\n")
        self.assertEqual(
            callbacks.records[2],
            "[compose] Sending 3 lines, 11 characters to lead.\n",
        )

    def test_say_escape_appends_the_two_literal_control_lines(self) -> None:
        terminal, _callbacks = make_terminal(
            "/say /send",
            "/say /cancel",
            "/send",
        )
        self.assertEqual(
            terminal.compose(ComposeMode.CHAT, agent_id=LEAD_ORCHESTRATOR),
            TerminalAction(
                TerminalActionKind.SEND,
                text="/send\n/cancel",
                line_count=2,
                character_count=13,
            ),
        )

    def test_control_near_misses_are_preserved_as_ordinary_lines(self) -> None:
        near_misses = (
            "/send ",
            " /send",
            "/SEND",
            "/cancel ",
            " /cancel",
            "/CANCEL",
            "/say  /send",
            "/say /send extra",
        )
        terminal, _callbacks = make_terminal(*near_misses, "/send")
        action = terminal.compose(ComposeMode.CHAT, agent_id=LEAD_ORCHESTRATOR)
        expected_text = "\n".join(near_misses)
        self.assertEqual(
            action,
            TerminalAction(
                TerminalActionKind.SEND,
                text=expected_text,
                line_count=len(near_misses),
                character_count=len(expected_text),
            ),
        )

    def test_cancel_discards_all_lines_without_returning_content(self) -> None:
        terminal, callbacks = make_terminal("draft", "", "/cancel")
        action = terminal.compose(ComposeMode.CHAT, agent_id=LEAD_ORCHESTRATOR)
        self.assertEqual(
            action,
            TerminalAction(
                TerminalActionKind.CANCEL,
                line_count=2,
                character_count=6,
            ),
        )
        self.assertIsNone(action.text)
        self.assertEqual(
            callbacks.records[-1],
            "[compose] Discarded 2 lines. No message was saved or sent.\n",
        )

        terminal, callbacks = make_terminal("/cancel")
        self.assertEqual(
            terminal.compose(ComposeMode.CHAT, agent_id=PLOT_ARCHITECT),
            TerminalAction(TerminalActionKind.CANCEL),
        )
        self.assertEqual(
            callbacks.records[-1],
            "[compose] Discarded 0 lines. No message was saved or sent.\n",
        )

    def test_interrupt_and_eof_discard_local_content_without_returning_it(
        self,
    ) -> None:
        for event, kind in (
            (KeyboardInterrupt(), TerminalActionKind.INTERRUPT),
            (EOFError(), TerminalActionKind.EOF),
        ):
            with self.subTest(kind=kind):
                terminal, callbacks = make_terminal("한🙂", event)
                action = terminal.compose(
                    ComposeMode.CHAT,
                    agent_id=CHARACTER_CUSTODIAN,
                )
                self.assertEqual(
                    action,
                    TerminalAction(
                        kind,
                        line_count=1,
                        character_count=2,
                    ),
                )
                self.assertIsNone(action.text)
                self.assertEqual(
                    callbacks.records[-1],
                    "[compose] Discarded 1 line. No message was saved or sent.\n",
                )

    def test_composition_validates_mode_target_and_reader_lines(self) -> None:
        invalid_calls = (
            lambda: make_terminal()[0].compose("chat"),
            lambda: make_terminal()[0].compose(ComposeMode.CHAT),
            lambda: make_terminal()[0].compose(
                ComposeMode.CHAT,
                agent_id="plot",
            ),
            lambda: make_terminal(None)[0].compose(
                ComposeMode.CHAT, agent_id=PLOT_ARCHITECT
            ),
            lambda: make_terminal("two\nlines")[0].compose(
                ComposeMode.CHAT, agent_id=PLOT_ARCHITECT
            ),
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises((TypeError, ValueError)):
                    call()


class HelpAndErrorTests(unittest.TestCase):
    def test_full_help_is_the_exact_ordered_command_registry_projection(self) -> None:
        terminal, callbacks = make_terminal(width=200)
        terminal.write_help()

        sections = (
            terminal_module.Section(
                "Writing artifact references",
                (terminal_module.Notice(_ARTIFACT_REFERENCE_GUIDANCE),),
                state=terminal_module.ViewState.COMPLETED,
            ),
            *tuple(
                terminal_module.Section(
                    group,
                    (
                        terminal_module.ListView(
                            "Commands",
                            tuple(
                                terminal_module.Record(
                                    "command",
                                    title=spec.syntax,
                                    fields=(
                                        terminal_module.Field(
                                            "Aliases",
                                            ", ".join(spec.aliases)
                                            if spec.aliases
                                            else "none",
                                        ),
                                        terminal_module.Field(
                                            "Calls provider",
                                            "yes" if spec.may_call_provider else "no",
                                        ),
                                        terminal_module.Field(
                                            "May write artifacts",
                                            "yes" if spec.may_write_artifacts else "no",
                                        ),
                                    ),
                                    role=terminal_module.SemanticRole.COMMAND,
                                    state=terminal_module.ViewState.COMPLETED,
                                )
                                for spec in COMMANDS
                                if spec.group == group
                            ),
                            total_count=sum(spec.group == group for spec in COMMANDS),
                            state=terminal_module.ViewState.COMPLETED,
                        ),
                    ),
                    state=terminal_module.ViewState.COMPLETED,
                )
                for group in dict.fromkeys(spec.group for spec in COMMANDS)
            ),
        )
        expected = terminal_module.Panel(
            "Help",
            sections,
            state=terminal_module.ViewState.COMPLETED,
        )
        self.assertEqual(
            callbacks.records,
            [f"{render_plain(expected, width=200).text}\n"],
        )
        self.assertNotIn("Example:", callbacks.records[0])
        self.assertEqual(callbacks.records[0].count("Session overrides"), 1)
        self.assertEqual(
            callbacks.records[0].count(
                "/override language LANG | persona AGENT NAME | reset"
            ),
            1,
        )
        for hidden in ("/language LANG", "/persona AGENT NAME", "/exit"):
            with self.subTest(hidden=hidden):
                self.assertNotIn(hidden, callbacks.records[0])

    def test_context_help_uses_retained_registry_command(
        self,
    ) -> None:
        terminal, callbacks = make_terminal()
        terminal.write_help("/agents")
        rendered = callbacks.records[0]
        self.assertIn("Syntax: /agents", rendered)
        self.assertIn("Calls provider: no", rendered)
        self.assertIn("May write artifacts: no", rendered)
        self.assertNotIn("/agent\n", rendered)

        for invalid in ("agent", "", "/unknown", 1):
            with self.subTest(invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    terminal.write_help(invalid)  # type: ignore[arg-type]

    def test_help_wraps_semantic_tokens_with_stable_prefix_indentation(self) -> None:
        terminal, callbacks = make_terminal(width=32)
        terminal.write_help("/override")
        rendered = callbacks.records[0]
        self.assertTrue(rendered.startswith(f"--- Help {'-' * 23}\n"))
        self.assertIn(" [command]\n", rendered)
        self.assertIn("  Syntax:\n", rendered)
        self.assertIn("   /override language LANG | per\n", rendered)
        self.assertNotIn("/run", rendered)
        for line in callbacks.records[0].splitlines():
            self.assertLessEqual(len(line), 32)

    def test_every_typed_error_code_selects_its_own_default_copy(self) -> None:
        for code in InteractiveErrorCode:
            with self.subTest(code=code):
                terminal, callbacks = make_terminal(width=1000)
                terminal.write_error(InteractiveError(code))
                INTERACTIVE_ERROR_SPECS[code]
                self.assertEqual(
                    callbacks.records,
                    [expected_error_record(code, width=1000)],
                )

        terminal, _callbacks = make_terminal()
        with self.assertRaises(TypeError):
            terminal.write_error("internal_error")  # type: ignore[arg-type]

    def test_error_debug_log_notice_is_additive_validated_and_byte_compatible(
        self,
    ) -> None:
        parameters = inspect.signature(InteractiveTerminal.write_error).parameters
        self.assertEqual(
            tuple(parameters),
            ("self", "error", "secrets", "debug_log_path"),
        )
        self.assertIs(
            parameters["debug_log_path"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        self.assertIsNone(parameters["debug_log_path"].default)

        error = InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
        terminal, callbacks = make_terminal(width=1000)
        terminal.write_error(error)
        terminal.write_error(error, debug_log_path=None)
        expected_default = expected_error_record(
            InteractiveErrorCode.INTERNAL_ERROR,
            width=1000,
        )
        self.assertEqual(callbacks.records, [expected_default, expected_default])

        debug_notice = "If available, workspace-relative: debug_logs/runtime.jsonl"
        terminal.write_error(error, debug_log_path=debug_notice)
        self.assertEqual(
            callbacks.records[-1],
            expected_error_record(
                InteractiveErrorCode.INTERNAL_ERROR,
                width=1000,
                debug_log_path=debug_notice,
            ),
        )
        self.assertEqual(callbacks.records[-1].count("[debug log]"), 1)

        for invalid in ("", "   ", "one\ntwo", "one\rtwo", "\ud800", 1):
            with self.subTest(invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    terminal.write_error(  # type: ignore[arg-type]
                        error,
                        debug_log_path=invalid,
                    )

    def test_error_redaction_covers_raw_escaped_and_overlapping_secrets(
        self,
    ) -> None:
        multiline_secret = "alpha\nbeta"
        escaped_secret = json.dumps(multiline_secret)[1:-1]
        terminal, callbacks = make_terminal(width=1000)
        terminal.write_error(
            InteractiveError(
                InteractiveErrorCode.INTERNAL_ERROR,
                summary=(
                    f"raw={multiline_secret} escaped={escaped_secret} "
                    "long=token-long short=token"
                ),
                hint="escaped-control=\\x1b[31mTOKEN",
            ),
            secrets=(
                multiline_secret,
                "token",
                "token-long",
                "\x1b[31mTOKEN",
            ),
        )
        self.assertEqual(
            callbacks.records,
            [
                f"--- Interactive error [error] {'-' * 970}\n"
                "  [error internal_error] raw=<redacted> escaped=<redacted> "
                "long=<redacted> short=<redacted>\n"
                "  [hint] escaped-control=<redacted>\n"
                f"{'-' * 1000}\n"
            ],
        )

    def test_error_secrets_ignore_empty_values_and_reject_bad_inputs(self) -> None:
        terminal, callbacks = make_terminal(width=1000)
        error = InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
        terminal.write_error(error, secrets=("",))
        terminal.write_error(error, secrets="")
        INTERACTIVE_ERROR_SPECS[InteractiveErrorCode.INTERNAL_ERROR]
        self.assertEqual(
            callbacks.records,
            [
                expected_error_record(
                    InteractiveErrorCode.INTERNAL_ERROR,
                    width=1000,
                ),
                expected_error_record(
                    InteractiveErrorCode.INTERNAL_ERROR,
                    width=1000,
                ),
            ],
        )

        for secrets in (None, 1, ("valid", 2)):
            with self.subTest(secrets=secrets):
                with self.assertRaises(TypeError):
                    terminal.write_error(  # type: ignore[arg-type]
                        error,
                        secrets=secrets,
                    )

    def test_error_copy_is_single_line_sanitized_bounded_and_suffixed(self) -> None:
        terminal, callbacks = make_terminal(width=1000)
        terminal.write_error(
            InteractiveError(
                InteractiveErrorCode.INTERNAL_ERROR,
                summary="s" * 600,
                hint="h" * 300,
            )
        )
        title_line, summary_line, hint_line, bottom_line = callbacks.records[
            0
        ].splitlines()
        self.assertEqual(
            title_line,
            f"--- Interactive error [error] {'-' * 970}",
        )
        self.assertEqual(bottom_line, "-" * 1000)
        summary = summary_line.removeprefix("  [error internal_error] ")
        hint = hint_line.removeprefix("  [hint] ")
        self.assertEqual(len(summary), 512)
        self.assertEqual(len(hint), 256)
        self.assertTrue(summary.endswith(" ...[truncated]"))
        self.assertTrue(hint.endswith(" ...[truncated]"))

        terminal, callbacks = make_terminal(width=32)
        terminal.write_error(
            InteractiveError(
                InteractiveErrorCode.INTERNAL_ERROR,
                summary="one two three four five six seven",
                hint="alpha beta gamma delta epsilon",
            )
        )
        self.assertEqual(
            callbacks.records,
            [
                "--- Interactive error [error]\n"
                " [error internal_error] one two \n"
                "                        three fo\n"
                "                        ur five \n"
                "                        six seve\n"
                "                        n\n"
                " [hint] alpha beta gamma delta e\n"
                "        psilon\n"
                f"{'-' * 32}\n"
            ],
        )


class SanitizerTests(unittest.TestCase):
    def test_sanitizer_preserves_lf_markdown_and_escapes_c0_c1_del_ansi(self) -> None:
        markdown = "# Heading\n**bold** [link](target) — 한글 🙂"
        self.assertEqual(sanitize_terminal_text(markdown), markdown)

        code_points = (*range(0x20), 0x7F, *range(0x80, 0xA0))
        source = "".join(chr(value) for value in code_points)
        expected = "".join(
            "\n" if value == 0x0A else f"\\x{value:02x}" for value in code_points
        )
        self.assertEqual(sanitize_terminal_text(source), expected)
        self.assertEqual(
            sanitize_terminal_text("\x1b[31mred\x1b[0m"),
            "\\x1b[31mred\\x1b[0m",
        )
        with self.assertRaises(TypeError):
            sanitize_terminal_text(None)  # type: ignore[arg-type]

    def test_all_format_surrogate_and_line_separator_output_is_strict_utf8(
        self,
    ) -> None:
        unsafe = [
            chr(value)
            for value in range(sys.maxunicode + 1)
            if unicodedata.category(chr(value)) in {"Cf", "Cs"}
        ]
        unsafe.extend(("\u2028", "\u2029"))
        source = "".join(unsafe)
        expected = "".join(visible_escape(character) for character in unsafe)
        rendered = sanitize_terminal_text(source)
        self.assertEqual(rendered, expected)
        rendered.encode("utf-8", errors="strict")
        self.assertEqual(sanitize_terminal_text(rendered), rendered)


class TerminalBoundaryTests(unittest.TestCase):
    def test_dependency_boundary_is_exact_and_terminal_names_stay_off_facade(
        self,
    ) -> None:
        source_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "story_writing_agents"
            / "interactive_terminal"
            / "__init__.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        internal_dependencies = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level > 0
        }
        self.assertEqual(
            internal_dependencies,
            {"_advisory_progress", "_core"},
        )
        self.assertNotIn("interactive_terminal", story_writing_agents.__all__)
        self.assertFalse(hasattr(story_writing_agents, "InteractiveTranscriptViewer"))
        for name in PUBLIC_EXPORTS[:-1]:
            with self.subTest(name=name):
                self.assertNotIn(name, story_writing_agents.__all__)
                self.assertFalse(hasattr(story_writing_agents, name))

    def test_terminal_operations_do_not_parse_call_provider_or_persist(self) -> None:
        cwd_before = Path.cwd()
        environment_before = os.environ.copy()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = tuple(root.iterdir())
            callbacks = ScriptedCallbacks(
                "raw /run",
                "subject",
                "message",
                "/send",
            )
            terminal = InteractiveTerminal(
                callbacks.read_line,
                callbacks.write_text,
            )
            with (
                mock.patch.object(
                    commands_module,
                    "parse_interactive_line",
                ) as parse_line,
                mock.patch.object(subprocess, "Popen") as popen,
                mock.patch.object(subprocess, "run") as run,
                mock.patch.object(socket, "create_connection") as connect,
                mock.patch.object(Path, "open") as path_open,
                mock.patch.object(Path, "write_text") as write_text,
                mock.patch.object(Path, "write_bytes") as write_bytes,
                mock.patch.object(Path, "mkdir") as mkdir,
            ):
                self.assertEqual(
                    terminal.read_main().text,
                    "raw /run",
                )
                self.assertEqual(
                    terminal.request_conversation_subject().text,
                    "subject",
                )
                self.assertEqual(
                    terminal.compose(
                        ComposeMode.CHAT, agent_id=LEAD_ORCHESTRATOR
                    ).text,
                    "message",
                )
                terminal.write_conversation_scope(
                    session_id="session",
                    conversation_handle="c_0123456789",
                    conversation_subject="Subject",
                    agent_id=LEAD_ORCHESTRATOR,
                )
                terminal.write_conversation_list_entry(
                    "c_0123456789",
                    "Subject",
                    archived=False,
                    focused=True,
                )
                terminal.write_conversation_search_hit(
                    "c_0123456789",
                    "Subject",
                    "subject",
                    "snippet",
                    archived=False,
                )
                terminal.write_conversation_transcript_event(
                    "c_0123456789",
                    sequence=1,
                    turn_id="turn_1",
                    kind="message",
                    sender="user",
                    recipient="plot",
                    content="body",
                )
                terminal.write_agent_roster()
                terminal.write_help()
                terminal.write_error(
                    InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
                )
                parse_line.assert_not_called()
                popen.assert_not_called()
                run.assert_not_called()
                connect.assert_not_called()
                path_open.assert_not_called()
                write_text.assert_not_called()
                write_bytes.assert_not_called()
                mkdir.assert_not_called()
            self.assertEqual(tuple(root.iterdir()), before)
        self.assertEqual(Path.cwd(), cwd_before)
        self.assertEqual(os.environ, environment_before)

    def test_direct_module_import_is_clean_isolated_and_not_facade_exported(
        self,
    ) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        source_path = str(repository_root / "src")
        existing_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            source_path
            if not existing_pythonpath
            else os.pathsep.join((source_path, existing_pythonpath))
        )
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        script = (
            "import story_writing_agents as facade; "
            f"expected = {PUBLIC_EXPORTS!r}; "
            "assert all(name not in facade.__all__ for name in expected); "
            "assert all(not hasattr(facade, name) for name in expected); "
            "from story_writing_agents import interactive_terminal as module; "
            "assert tuple(module.__all__) == expected; "
            "assert tuple(module.INTERACTIVE_ERROR_SPECS) == "
            "tuple(module.InteractiveErrorCode); "
            "print('ok')"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            completed = subprocess.run(
                [sys.executable, "-c", script],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "ok\n")
            self.assertEqual(list(root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
