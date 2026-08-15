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
    ComposeMode,
    ConfirmationResult,
    InteractiveError,
    InteractiveErrorCode,
    InteractiveErrorSpec,
    InteractiveTerminal,
    TerminalAction,
    TerminalActionKind,
    TerminalMessageKind,
    sanitize_terminal_text,
)


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
)

ERROR_DEFAULTS = {
    InteractiveErrorCode.TTY_REQUIRED: (
        "Interactive mode requires a terminal.",
        "Use story run for non-interactive automation.",
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
    InteractiveErrorCode.SESSION_IN_USE: (
        "The story session is in use.",
        "Choose another session or wait for its owner to detach.",
    ),
    InteractiveErrorCode.SESSION_INACTIVE: (
        "The story session is inactive.",
        "Resume the session before starting provider work.",
    ),
    InteractiveErrorCode.CONVERSATION_REQUIRED: (
        "No conversation is selected.",
        "Create, list, or use a conversation first.",
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
        "Inspect retained temporary or lease state before retrying.",
    ),
    InteractiveErrorCode.INTERNAL_ERROR: (
        "An internal error occurred.",
        "Retry once; if it repeats, report the internal_error code.",
    ),
}

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


def expected_session_orientation(
    *,
    attachment: AttachmentKind,
    display_name: str,
    history_count: int,
) -> str:
    event_word = "event" if history_count == 1 else "events"
    return (
        "Story chat\n"
        "Workspace: /stories/example\n"
        f"Session: SESSION ({attachment.value})\n"
        f"Target: {display_name}\n"
        f"History: {history_count} visible room {event_word}\n"
        "Room messages are saved locally and shared with addressed agents.\n"
        "Type /help for commands; /quit leaves the story session active.\n"
    )


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
        for name in PUBLIC_EXPORTS:
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
                ("ATTACHED", "attached"),
                ("RESUMED", "resumed"),
            ),
            ComposeMode: (("CHAT", "chat"), ("RUN", "run")),
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
                ("CONFIGURATION_INVALID", "configuration_invalid"),
                ("COMMAND_INVALID", "command_invalid"),
                ("AGENT_INVALID", "agent_invalid"),
                ("SESSION_INVALID", "session_invalid"),
                ("SESSION_EXISTS", "session_exists"),
                ("SESSION_NOT_FOUND", "session_not_found"),
                ("SESSION_IN_USE", "session_in_use"),
                ("SESSION_INACTIVE", "session_inactive"),
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
        self.assertEqual(signature.parameters["width"].default, 80)
        self.assertEqual(
            signature.parameters["width"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
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
                "selected",
            ),
        )
        self.assertTrue(
            all(
                list_signature.parameters[name].kind
                is inspect.Parameter.KEYWORD_ONLY
                for name in ("archived", "selected")
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
                search_signature.parameters[name].kind
                is inspect.Parameter.KEYWORD_ONLY
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
            tuple(
                inspect.signature(
                    InteractiveTerminal.write_agent_roster
                ).parameters
            ),
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

    def test_main_prompts_are_exact_and_return_unparsed_lines(self) -> None:
        self.assertEqual(
            tuple(
                (
                    route.agent_id,
                    route.prompt_alias,
                    terminal_module.AGENT_SPECS[route.agent_id].display_name,
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
        terminal, callbacks = make_terminal("unused")
        for session_id, agent_id in (("SESSION", None), (None, PLOT_ARCHITECT)):
            with self.subTest(session_id=session_id, agent_id=agent_id):
                with self.assertRaises(ValueError):
                    terminal.read_main(
                        session_id=session_id,
                        agent_id=agent_id,
                    )
        self.assertEqual(callbacks.prompts, [])

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
                suffix = "[Y/n] " if default else "[y/N] "
                self.assertEqual(callbacks.prompts, [f"Continue? {suffix}"])
                self.assertEqual(callbacks.records, [])

        terminal, callbacks = make_terminal("later", " MAYBE ", "yes")
        self.assertIs(
            terminal.request_confirmation("Resume?", default=False),
            ConfirmationResult.YES,
        )
        self.assertEqual(
            callbacks.prompts,
            ["Resume? [y/N] ", "Resume? [y/N] ", "Resume? [y/N] "],
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
        self.assertEqual(callbacks.prompts, ["unsafe\\x0a\\x1b? [Y/n] "])
        self.assertEqual(callbacks.records, [])

        terminal, callbacks = make_terminal(EOFError())
        self.assertIs(
            terminal.request_confirmation("End?", default=False),
            ConfirmationResult.EOF,
        )
        self.assertEqual(callbacks.prompts, ["End? [y/N] "])
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
                "Story chat\n"
                "Workspace: /stories/example\n"
                "Session: lobby\n"
                "Provider calls are unavailable in the lobby. Use "
                "/session new NAME, /session resume [NAME], or /session list.\n"
                "Type /help for commands; /quit exits.\n"
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
            cases.append((AttachmentKind.ATTACHED, *agent_case, 2))
        for history_count in (0, 1, 2):
            cases.append(
                (
                    AttachmentKind.RESUMED,
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
                "Story chat\n"
                "Workspace: /stories/example\n"
                "Session: SESSION (created)\n"
                "Target: Lead Orchestrator\n"
                "Type /help for commands; /quit leaves the story session active.\n"
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

    def test_conversation_scope_has_exact_selected_and_lobby_records(self) -> None:
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
                "[context] Session: SESSION | Conversation: c_0123456789 | "
                "Subject: Opening scene | Agent: plot\n",
                "[context] Session: SESSION | Conversation: none | "
                "Agent: lead\n",
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
        self.assertTrue(rendered.startswith("[context] "))
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
            selected=True,
        )
        terminal.write_conversation_list_entry(
            "c_fedcba9876",
            "Old\nsubject\x1b",
            archived=True,
            selected=False,
        )
        self.assertEqual(
            callbacks.records,
            [
                "[conversation] c_0123456789 | active | selected | "
                "Subject: Opening scene\n",
                "[conversation] c_fedcba9876 | archived | not selected | "
                "Subject: Old\\x0asubject\\x1b\n",
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
                selected=False,
            ),
            lambda: terminal.write_conversation_list_entry(
                "conversation",
                " ",
                archived=False,
                selected=False,
            ),
            lambda: terminal.write_conversation_list_entry(
                "conversation",
                "Subject",
                archived=True,
                selected=True,
            ),
            lambda: terminal.write_conversation_list_entry(
                "conversation",
                "Subject",
                archived=1,  # type: ignore[arg-type]
                selected=False,
            ),
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises((TypeError, ValueError)):
                    call()

    def test_search_hits_have_exact_rows_citations_and_visible_controls(
        self,
    ) -> None:
        terminal, callbacks = make_terminal(width=240)
        terminal.write_conversation_search_hit(
            "c_0123456789",
            "Opening scene",
            "subject",
            "council vessel",
            archived=False,
        )
        terminal.write_conversation_search_hit(
            "c_fedcba9876",
            "Old\nsubject",
            "event",
            "unsafe\x1b\ntext",
            archived=True,
            citation_turn_id="turn_7",
            citation_sequence=9,
            citation_kind="reply",
        )
        self.assertEqual(
            callbacks.records,
            [
                "[search c_0123456789] Status: active | Match: subject | "
                "Subject: Opening scene | Citation: conversation "
                "c_0123456789\n"
                "[snippet] council vessel\n",
                "[search c_fedcba9876] Status: archived | Match: event | "
                "Subject: Old\\x0asubject | Citation: conversation "
                "c_fedcba9876, turn turn_7, sequence 9, kind reply\n"
                "[snippet] unsafe\\x1b\\x0atext\n",
            ],
        )
        self.assertEqual(len(callbacks.records), 2)
        self.assertNotIn("\x1b", "".join(callbacks.records))

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
                *valid, archived=1  # type: ignore[arg-type]
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
            "[transcript c_0123456789] Sequence: 2 | Turn: turn_2 | "
            "Kind: reply | Sender: plot | Recipient: user\n"
        )
        self.assertEqual(
            callbacks.records,
            [
                header + "[content]  \\x09\n",
                header + "[content] first\n\n          third\n\n",
                header + "[content] \\x1b[31mred\\x00\n          last\n",
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
        _header, marker, rendered_content = narrow_callbacks.records[0].partition(
            "[content] "
        )
        self.assertEqual(marker, "[content] ")
        self.assertEqual(
            rendered_content,
            "  alpha  \n"
            "             \n"
            "            beta gamma \n"
            "          delta  \n",
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
            lambda: terminal.write_conversation_transcript_event(
                "unsafe/id", **base
            ),
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
                "[agent] Lead Orchestrator | ID: lead_orchestrator | "
                "Aliases: lead, orchestrator, lead_orchestrator | "
                "Advisory domain: Moderation, synthesis, and finalization.\n"
                "[agent] Plot & Structure Architect | ID: plot_architect | "
                "Aliases: plot, plot_architect | Advisory domain: Plot, "
                "structure, flow, and pacing.\n"
                "[agent] Character & World Custodian | ID: "
                "character_custodian | Aliases: character, "
                "character_custodian | Advisory domain: Characters, "
                "relationships, world, and continuity.\n"
                "[agent] Tone & Execution Specialist | ID: "
                "execution_specialist | Aliases: execution, prose, "
                "execution_specialist | Advisory domain: Drafting, prose, "
                "point of view, tone, grammar, and format.\n"
            ],
        )

        terminal, callbacks = make_terminal(width=44)
        terminal.write_agent_roster()
        wrapped = callbacks.records[0]
        self.assertEqual(
            [line for line in wrapped.splitlines() if line.startswith("[agent] ")],
            [
                "[agent] Lead Orchestrator | ID:",
                "[agent] Plot & Structure Architect | ID:",
                "[agent] Character & World Custodian | ID:",
                "[agent] Tone & Execution Specialist | ID:",
            ],
        )
        self.assertTrue(all(len(line) <= 44 for line in wrapped.splitlines()))

        original = terminal_module.AGENT_SPECS[LEAD_ORCHESTRATOR]
        unsafe = replace(original, display_name="Unsafe\x1bLead")
        terminal, callbacks = make_terminal(width=1000)
        with mock.patch.dict(
            terminal_module.AGENT_SPECS,
            {LEAD_ORCHESTRATOR: unsafe},
        ):
            terminal.write_agent_roster()
        self.assertIn(
            "[agent] Unsafe\\x1bLead | ID: lead_orchestrator",
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
        self.assertEqual(callbacks.records, ["[run DEBATE-1] complete\n"])

        for agent_id, alias, _display_name in AGENT_CASES:
            with self.subTest(agent_id=agent_id):
                terminal, callbacks = make_terminal()
                terminal.write_agent(agent_id, "reply")
                self.assertEqual(callbacks.records, [f"{alias}> reply\n"])

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
                "compose[chat:plot:1]> ",
                "compose[chat:plot:2]> ",
                "compose[chat:plot:3]> ",
            ],
        )
        self.assertEqual(
            callbacks.records,
            [
                "[compose] Target: plot. Use /send to submit or "
                "/cancel to discard.\n",
                "[compose] Sending 2 lines, 12 characters to plot.\n",
            ],
        )

    def test_run_composition_counts_unicode_code_points_not_bytes(self) -> None:
        terminal, callbacks = make_terminal("한글🙂", "/send")
        action = terminal.compose(ComposeMode.RUN)
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
            ["compose[run:1]> ", "compose[run:2]> "],
        )
        self.assertEqual(
            callbacks.records,
            [
                "[compose] Consensus run. Use /send to submit or "
                "/cancel to discard.\n",
                "[compose] Sending 1 line, 3 characters to consensus.\n",
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
        action = terminal.compose(ComposeMode.RUN)
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
                "compose[run:1]> ",
                "compose[run:2]> ",
                "compose[run:3]> ",
                "compose[run:3]> ",
                "compose[run:4]> ",
            ],
        )
        self.assertEqual(callbacks.records[1], "[compose] Nothing to send.\n")
        self.assertEqual(
            callbacks.records[2],
            "[compose] Sending 3 lines, 11 characters to consensus.\n",
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
        action = terminal.compose(ComposeMode.RUN)
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
        action = terminal.compose(ComposeMode.RUN)
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
                    "[compose] Discarded 1 line. "
                    "No message was saved or sent.\n",
                )

    def test_composition_validates_mode_target_and_reader_lines(self) -> None:
        invalid_calls = (
            lambda: make_terminal()[0].compose("chat"),
            lambda: make_terminal()[0].compose(ComposeMode.CHAT),
            lambda: make_terminal()[0].compose(
                ComposeMode.RUN,
                agent_id=PLOT_ARCHITECT,
            ),
            lambda: make_terminal()[0].compose(
                ComposeMode.CHAT,
                agent_id="plot",
            ),
            lambda: make_terminal(None)[0].compose(ComposeMode.RUN),
            lambda: make_terminal("two\nlines")[0].compose(ComposeMode.RUN),
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises((TypeError, ValueError)):
                    call()


class HelpAndErrorTests(unittest.TestCase):
    def test_full_help_is_the_exact_ordered_command_registry_projection(self) -> None:
        terminal, callbacks = make_terminal(width=200)
        terminal.write_help()

        expected_lines: list[str] = []
        current_group = None
        for spec in COMMANDS:
            if spec.group != current_group:
                current_group = spec.group
                expected_lines.append(f"{spec.group}:")
            aliases = ", ".join(spec.aliases) if spec.aliases else "none"
            expected_lines.extend(
                (
                    f"  {spec.syntax}",
                    f"    Aliases: {aliases}",
                    "    Calls provider: "
                    f"{'yes' if spec.may_call_provider else 'no'}",
                    "    May write artifacts: "
                    f"{'yes' if spec.may_write_artifacts else 'no'}",
                )
            )
        self.assertEqual(callbacks.records, ["\n".join(expected_lines) + "\n"])
        self.assertNotIn("Example:", callbacks.records[0])

    def test_context_help_accepts_alias_and_includes_the_registry_example(
        self,
    ) -> None:
        terminal, callbacks = make_terminal()
        terminal.write_help("/agent")
        self.assertEqual(
            callbacks.records,
            [
                "Group: Agents\n"
                "Syntax: /to NAME\n"
                "Aliases: /agent\n"
                "Calls provider: no\n"
                "May write artifacts: no\n"
                "Example: /to plot\n"
            ],
        )

        for invalid in ("agent", "", "/unknown", 1):
            with self.subTest(invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    terminal.write_help(invalid)  # type: ignore[arg-type]

    def test_help_wraps_semantic_tokens_with_stable_prefix_indentation(self) -> None:
        terminal, callbacks = make_terminal(width=32)
        terminal.write_help("/run")
        self.assertEqual(
            callbacks.records,
            [
                "Group: Consensus\n"
                "Syntax: /run [PROMPT] | /run\n"
                "        --fresh PROMPT\n"
                "Aliases: none\n"
                "Calls provider: yes\n"
                "May write artifacts: yes\n"
                "Example: /run Outline chapter\n"
                "         one\n"
            ],
        )
        for line in callbacks.records[0].splitlines():
            self.assertLessEqual(len(line), 32)

    def test_every_typed_error_code_selects_its_own_default_copy(self) -> None:
        for code in InteractiveErrorCode:
            with self.subTest(code=code):
                terminal, callbacks = make_terminal(width=1000)
                terminal.write_error(InteractiveError(code))
                spec = INTERACTIVE_ERROR_SPECS[code]
                self.assertEqual(
                    callbacks.records,
                    [
                        f"[error {code.value}] {spec.summary}\n"
                        f"[hint] {spec.hint}\n"
                    ],
                )

        terminal, _callbacks = make_terminal()
        with self.assertRaises(TypeError):
            terminal.write_error("internal_error")  # type: ignore[arg-type]

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
                "[error internal_error] raw=<redacted> escaped=<redacted> "
                "long=<redacted> short=<redacted>\n"
                "[hint] escaped-control=<redacted>\n"
            ],
        )

    def test_error_secrets_ignore_empty_values_and_reject_bad_inputs(self) -> None:
        terminal, callbacks = make_terminal(width=1000)
        error = InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
        terminal.write_error(error, secrets=("",))
        terminal.write_error(error, secrets="")
        spec = INTERACTIVE_ERROR_SPECS[InteractiveErrorCode.INTERNAL_ERROR]
        self.assertEqual(
            callbacks.records,
            [
                f"[error internal_error] {spec.summary}\n"
                f"[hint] {spec.hint}\n",
                f"[error internal_error] {spec.summary}\n"
                f"[hint] {spec.hint}\n",
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
        summary_line, hint_line = callbacks.records[0].splitlines()
        summary = summary_line.removeprefix("[error internal_error] ")
        hint = hint_line.removeprefix("[hint] ")
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
                "[error internal_error] one two\n"
                "                       three\n"
                "                       four five\n"
                "                       six seven\n"
                "[hint] alpha beta gamma delta\n"
                "       epsilon\n"
            ],
        )


class SanitizerTests(unittest.TestCase):
    def test_sanitizer_preserves_lf_markdown_and_escapes_c0_c1_del_ansi(self) -> None:
        markdown = "# Heading\n**bold** [link](target) — 한글 🙂"
        self.assertEqual(sanitize_terminal_text(markdown), markdown)

        code_points = (*range(0x20), 0x7F, *range(0x80, 0xA0))
        source = "".join(chr(value) for value in code_points)
        expected = "".join(
            "\n" if value == 0x0A else f"\\x{value:02x}"
            for value in code_points
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
            / "interactive_terminal.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        internal_dependencies = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level > 0
        }
        self.assertEqual(
            internal_dependencies,
            {"agents", "interactive_commands"},
        )
        self.assertNotIn("interactive_terminal", story_writing_agents.__all__)
        for name in PUBLIC_EXPORTS:
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
                    terminal.compose(ComposeMode.RUN).text,
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
                    selected=True,
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
