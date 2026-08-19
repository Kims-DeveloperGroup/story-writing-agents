"""Contract tests for provider-free interactive command parsing."""

from __future__ import annotations

import ast
import inspect
import os
import pickle
import random
import socket
import subprocess
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from unittest import mock

import story_writing_agents
from story_writing_agents import (
    CHARACTER_CUSTODIAN,
    COMMANDS,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    ActionKind,
    CommandSpec,
    InteractiveAction,
    parse_interactive_line,
)
from story_writing_agents import interactive_commands as commands_module
from story_writing_agents.interactive_commands import (
    AGENT_ROUTES,
    AgentRouteSpec,
    CommandCompletionContext,
    project_command_completion,
)


EXPECTED_AGENT_ROUTES = (
    AgentRouteSpec(
        LEAD_ORCHESTRATOR,
        "lead",
        ("lead", "orchestrator", "lead_orchestrator"),
        "Moderation, synthesis, and finalization.",
    ),
    AgentRouteSpec(
        PLOT_ARCHITECT,
        "plot",
        ("plot", "plot_architect"),
        "Plot, structure, flow, and pacing.",
    ),
    AgentRouteSpec(
        CHARACTER_CUSTODIAN,
        "character",
        ("character", "character_custodian"),
        "Characters, relationships, world, and continuity.",
    ),
    AgentRouteSpec(
        EXECUTION_SPECIALIST,
        "execution",
        ("execution", "prose", "execution_specialist"),
        "Drafting, prose, point of view, tone, grammar, and format.",
    ),
)

AGENT_ALIASES = {
    alias: route.agent_id
    for route in EXPECTED_AGENT_ROUTES
    for alias in route.aliases
}

COMMAND_NAMES = {
    "compose": "/compose",
    "send": "/send",
    "cancel": "/cancel",
    "say": "/say",
    "to": "/to",
    "agent": "/to",
    "agents": "/agents",
    "language": "/language",
    "persona": "/persona",
    "reset": "/reset",
    "settings": "/settings",
    "run": "/run",
    "draft-file": "/draft-file",
    "session": "/session",
    "conversation": "/conversation",
    "search": "/search",
    "transcript": "/transcript",
    "inspect": "/inspect",
    "history": "/history",
    "status": "/status",
    "help": "/help",
    "?": "/help",
    "quit": "/quit",
    "exit": "/quit",
}

ACTION_FIELDS = (
    "text",
    "agent_id",
    "argument",
    "count",
    "start_sequence",
    "error",
    "suggestion",
)

FIELD_SAMPLES = {
    "text": "text",
    "agent_id": PLOT_ARCHITECT,
    "argument": "argument",
    "count": 1,
    "start_sequence": 1,
    "error": "error",
    "suggestion": "/run",
}

ACTION_RULES = {
    ActionKind.IGNORED: (set(), set()),
    ActionKind.MESSAGE: ({"text"}, {"text"}),
    ActionKind.ROUTED_MESSAGE: (
        {"agent_id", "text"},
        {"agent_id", "text"},
    ),
    ActionKind.SET_TARGET: ({"agent_id"}, {"agent_id"}),
    ActionKind.LIST_AGENTS: (set(), set()),
    ActionKind.COMPOSE: (set(), set()),
    ActionKind.SEND: (set(), set()),
    ActionKind.CANCEL: (set(), set()),
    ActionKind.RUN: ({"text"}, set()),
    ActionKind.RUN_FRESH: ({"text"}, {"text"}),
    ActionKind.DRAFT_FILE: ({"argument"}, {"argument"}),
    ActionKind.SEARCH: ({"text"}, {"text"}),
    ActionKind.TRANSCRIPT: (
        {"argument", "count", "start_sequence"},
        {"argument"},
    ),
    ActionKind.INSPECT: ({"argument"}, set()),
    ActionKind.HISTORY: ({"count"}, set()),
    ActionKind.STATUS: (set(), set()),
    ActionKind.SETTINGS: (set(), set()),
    ActionKind.SET_LANGUAGE: ({"argument"}, {"argument"}),
    ActionKind.SET_PERSONA: (
        {"agent_id", "argument"},
        {"agent_id", "argument"},
    ),
    ActionKind.RESET: (set(), set()),
    ActionKind.SESSION_LIST: (set(), set()),
    ActionKind.SESSION_CREATE: ({"argument"}, {"argument"}),
    ActionKind.SESSION_OPEN: ({"argument"}, set()),
    ActionKind.SESSION_PAUSE: (set(), set()),
    ActionKind.CONVERSATION_NEW: ({"argument"}, set()),
    ActionKind.CONVERSATION_LIST: ({"argument"}, set()),
    ActionKind.CONVERSATION_LIST_ALL: ({"argument"}, set()),
    ActionKind.CONVERSATION_USE: ({"argument"}, {"argument"}),
    ActionKind.CONVERSATION_SUBJECT: ({"argument"}, {"argument"}),
    ActionKind.CONVERSATION_ARCHIVE: ({"argument"}, set()),
    ActionKind.HELP: ({"argument"}, set()),
    ActionKind.QUIT: (set(), set()),
    ActionKind.PARSE_ERROR: (
        {"error", "suggestion"},
        {"error"},
    ),
}

_UNSPECIFIED = object()


def levenshtein(left: str, right: str) -> int:
    """Return an independent edit-distance oracle for suggestion tests."""

    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1]
                    + (left_character != right_character),
                )
            )
        previous = current
    return previous[-1]


def expected_suggestion(
    value: str,
    candidates: dict[str, str],
) -> str | None:
    """Apply the public unique-minimum and distance-two suggestion rule."""

    distances = {
        candidate: levenshtein(value, candidate) for candidate in candidates
    }
    minimum = min(distances.values())
    closest = [
        candidate
        for candidate, distance in distances.items()
        if distance == minimum
    ]
    if minimum > 2 or len(closest) != 1:
        return None
    return candidates[closest[0]]


class ParserAssertions:
    """Shared semantic assertions without coupling to error prose."""

    def assert_parse_error(
        self: unittest.TestCase,
        line: object,
        *,
        suggestion: str | None | object = _UNSPECIFIED,
    ) -> InteractiveAction:
        action = parse_interactive_line(line)  # type: ignore[arg-type]
        self.assertEqual(action.kind, ActionKind.PARSE_ERROR)
        self.assertIsInstance(action.error, str)
        assert action.error is not None
        self.assertTrue(action.error.strip())
        if suggestion is not _UNSPECIFIED:
            self.assertEqual(action.suggestion, suggestion)
        self.assertIsNone(action.text)
        self.assertIsNone(action.agent_id)
        self.assertIsNone(action.argument)
        self.assertIsNone(action.count)
        self.assertIsNone(action.start_sequence)
        self.assertEqual(action, parse_interactive_line(line))  # type: ignore[arg-type]
        return action


class ActionContractTests(unittest.TestCase):
    def test_action_kind_has_the_exact_public_members_and_values(self) -> None:
        expected = {
            "IGNORED": "ignored",
            "MESSAGE": "message",
            "ROUTED_MESSAGE": "routed_message",
            "SET_TARGET": "set_target",
            "LIST_AGENTS": "list_agents",
            "COMPOSE": "compose",
            "SEND": "send",
            "CANCEL": "cancel",
            "RUN": "run",
            "RUN_FRESH": "run_fresh",
            "DRAFT_FILE": "draft_file",
            "SEARCH": "search",
            "TRANSCRIPT": "transcript",
            "INSPECT": "inspect",
            "HISTORY": "history",
            "STATUS": "status",
            "SETTINGS": "settings",
            "SET_LANGUAGE": "set_language",
            "SET_PERSONA": "set_persona",
            "RESET": "reset",
            "SESSION_LIST": "session_list",
            "SESSION_CREATE": "session_create",
            "SESSION_OPEN": "session_open",
            "SESSION_PAUSE": "session_pause",
            "CONVERSATION_NEW": "conversation_new",
            "CONVERSATION_LIST": "conversation_list",
            "CONVERSATION_LIST_ALL": "conversation_list_all",
            "CONVERSATION_USE": "conversation_use",
            "CONVERSATION_SUBJECT": "conversation_subject",
            "CONVERSATION_ARCHIVE": "conversation_archive",
            "HELP": "help",
            "QUIT": "quit",
            "PARSE_ERROR": "parse_error",
        }
        self.assertEqual(
            {name: member.value for name, member in ActionKind.__members__.items()},
            expected,
        )

    def test_dataclass_schemas_are_exact_keyword_only_and_slotted(self) -> None:
        self.assertEqual(
            tuple(field.name for field in fields(AgentRouteSpec)),
            ("agent_id", "prompt_alias", "aliases", "advisory_domain"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(InteractiveAction)),
            ("kind", *ACTION_FIELDS),
        )
        self.assertEqual(
            tuple(field.name for field in fields(CommandSpec)),
            (
                "group",
                "syntax",
                "aliases",
                "may_call_provider",
                "may_write_artifacts",
                "example",
            ),
        )
        with self.assertRaises(TypeError):
            InteractiveAction(ActionKind.MESSAGE, "text")  # type: ignore[misc]
        self.assertFalse(hasattr(AGENT_ROUTES[0], "__dict__"))
        self.assertFalse(hasattr(InteractiveAction(ActionKind.IGNORED), "__dict__"))
        self.assertFalse(hasattr(COMMANDS[0], "__dict__"))

    def test_agent_route_registry_is_exact_ordered_valid_and_immutable(
        self,
    ) -> None:
        self.assertIsInstance(AGENT_ROUTES, tuple)
        self.assertEqual(AGENT_ROUTES, EXPECTED_AGENT_ROUTES)
        self.assertEqual(
            tuple(route.agent_id for route in AGENT_ROUTES),
            (
                LEAD_ORCHESTRATOR,
                PLOT_ARCHITECT,
                CHARACTER_CUSTODIAN,
                EXECUTION_SPECIALIST,
            ),
        )
        aliases = tuple(
            alias for route in AGENT_ROUTES for alias in route.aliases
        )
        self.assertEqual(len(aliases), len(set(aliases)))
        for route in AGENT_ROUTES:
            with self.subTest(agent_id=route.agent_id):
                self.assertEqual(route.prompt_alias, route.aliases[0])
                self.assertRegex(route.prompt_alias, r"\A[a-z][a-z0-9_]*\Z")
                route.advisory_domain.encode("utf-8", errors="strict")

        with self.assertRaises(FrozenInstanceError):
            AGENT_ROUTES[0].prompt_alias = "changed"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            AGENT_ROUTES[0] = AGENT_ROUTES[0]  # type: ignore[index]
        with self.assertRaises(TypeError):
            AGENT_ROUTES[0].aliases[0] = "changed"  # type: ignore[index]

    def test_agent_route_specs_reject_invalid_identity_aliases_and_copy(
        self,
    ) -> None:
        valid = (
            LEAD_ORCHESTRATOR,
            "lead",
            ("lead", "orchestrator"),
            "Advisory domain.",
        )
        invalid_cases = (
            (("unknown", *valid[1:]), ValueError),
            ((valid[0], "Lead", valid[2], valid[3]), ValueError),
            ((valid[0], valid[1], ["lead"], valid[3]), TypeError),
            ((valid[0], valid[1], (), valid[3]), ValueError),
            ((valid[0], valid[1], ("lead", "lead"), valid[3]), ValueError),
            ((valid[0], valid[1], ("orchestrator", "lead"), valid[3]), ValueError),
            ((valid[0], valid[1], ("lead", "bad-alias"), valid[3]), ValueError),
            ((valid[0], valid[1], valid[2], " \t"), ValueError),
            ((valid[0], valid[1], valid[2], "unsafe\x1bcopy"), ValueError),
            ((valid[0], valid[1], valid[2], "invalid-\ud800"), ValueError),
        )
        for arguments, error_type in invalid_cases:
            with self.subTest(arguments=repr(arguments)):
                with self.assertRaises(error_type):
                    AgentRouteSpec(*arguments)  # type: ignore[arg-type]

    def test_each_action_kind_accepts_only_its_payload_shape(self) -> None:
        self.assertEqual(set(ACTION_RULES), set(ActionKind))
        for kind, (allowed, required) in ACTION_RULES.items():
            valid = {name: FIELD_SAMPLES[name] for name in required}
            with self.subTest(kind=kind, case="valid"):
                self.assertEqual(InteractiveAction(kind, **valid).kind, kind)

            for field_name in required:
                missing = dict(valid)
                del missing[field_name]
                with self.subTest(kind=kind, missing=field_name):
                    with self.assertRaises(ValueError):
                        InteractiveAction(kind, **missing)

            for field_name in set(ACTION_FIELDS).difference(allowed):
                extraneous = dict(valid)
                extraneous[field_name] = FIELD_SAMPLES[field_name]
                with self.subTest(kind=kind, extraneous=field_name):
                    with self.assertRaises(ValueError):
                        InteractiveAction(kind, **extraneous)

    def test_action_value_validation_rejects_invalid_payload_values(self) -> None:
        invalid_cases = (
            (("message",), {}, TypeError),
            ((ActionKind.MESSAGE,), {"text": 7}, TypeError),
            ((ActionKind.SET_TARGET,), {"agent_id": "unknown"}, ValueError),
            ((ActionKind.MESSAGE,), {"text": " \t"}, ValueError),
            ((ActionKind.HISTORY,), {"count": True}, TypeError),
            ((ActionKind.HISTORY,), {"count": 0}, ValueError),
            ((ActionKind.HISTORY,), {"count": -1}, ValueError),
            (
                (ActionKind.TRANSCRIPT,),
                {"argument": "id", "start_sequence": True},
                TypeError,
            ),
            (
                (ActionKind.TRANSCRIPT,),
                {"argument": "id", "start_sequence": 0},
                ValueError,
            ),
            (
                (ActionKind.TRANSCRIPT,),
                {"argument": "id", "start_sequence": -1},
                ValueError,
            ),
            ((ActionKind.DRAFT_FILE,), {"argument": ""}, ValueError),
            ((ActionKind.PARSE_ERROR,), {"error": ""}, ValueError),
            (
                (ActionKind.PARSE_ERROR,),
                {"error": "error", "suggestion": ""},
                ValueError,
            ),
        )
        for arguments, keywords, error_type in invalid_cases:
            with self.subTest(arguments=arguments, keywords=keywords):
                with self.assertRaises(error_type):
                    InteractiveAction(*arguments, **keywords)  # type: ignore[arg-type]

    def test_action_specs_and_registry_are_immutable(self) -> None:
        action = InteractiveAction(ActionKind.RUN, text="prompt")
        with self.assertRaises(FrozenInstanceError):
            action.text = "changed"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            COMMANDS[0].syntax = "/changed"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            COMMANDS[0] = COMMANDS[0]  # type: ignore[index]
        with self.assertRaises(TypeError):
            COMMANDS[4].aliases[0] = "/changed"  # type: ignore[index]


class CommandRegistryTests(unittest.TestCase):
    def test_registry_is_the_exact_ordered_twenty_one_row_contract(self) -> None:
        expected = (
            CommandSpec("Conversation", "/compose", (), False, False, "/compose"),
            CommandSpec("Conversation", "/send", (), False, False, "/send"),
            CommandSpec("Conversation", "/cancel", (), False, False, "/cancel"),
            CommandSpec(
                "Conversation",
                "/say TEXT",
                (),
                True,
                False,
                "/say /run is literal text",
            ),
            CommandSpec(
                "Agents",
                "/to NAME",
                ("/agent",),
                False,
                False,
                "/to plot",
            ),
            CommandSpec("Agents", "/agents", (), False, False, "/agents"),
            CommandSpec(
                "Agents",
                "/language LANG",
                (),
                False,
                False,
                "/language ko",
            ),
            CommandSpec(
                "Agents",
                "/persona AGENT NAME",
                (),
                False,
                False,
                "/persona plot meticulous",
            ),
            CommandSpec("Agents", "/reset", (), False, False, "/reset"),
            CommandSpec(
                "Agents",
                "/settings",
                (),
                False,
                True,
                "/settings",
            ),
            CommandSpec(
                "Consensus",
                "/run [PROMPT] | /run --fresh PROMPT",
                (),
                True,
                True,
                "/run Outline chapter one",
            ),
            CommandSpec(
                "Consensus",
                "/draft-file PATH",
                (),
                True,
                True,
                "/draft-file drafts/chapter-01.md",
            ),
            CommandSpec(
                "Sessions",
                "/session list | create NAME | open [NAME] | pause",
                (),
                False,
                False,
                "/session create chapter-01",
            ),
            CommandSpec(
                "Conversations",
                (
                    "/conversation new [SUBJECT] | list [--all] [QUERY] | "
                    "use ID | subject SUBJECT | archive [ID]"
                ),
                (),
                False,
                False,
                "/conversation new Chapter 1 opening",
            ),
            CommandSpec(
                "Inspection",
                "/search QUERY",
                (),
                False,
                False,
                "/search council vessel",
            ),
            CommandSpec(
                "Inspection",
                "/transcript ID [COUNT] [--from SEQUENCE]",
                (),
                False,
                False,
                "/transcript c_0123456789 24 --from 25",
            ),
            CommandSpec(
                "Inspection",
                "/inspect [DEBATE|last]",
                (),
                False,
                False,
                "/inspect last",
            ),
            CommandSpec(
                "Inspection",
                "/history [COUNT]",
                (),
                False,
                False,
                "/history 20",
            ),
            CommandSpec("Inspection", "/status", (), False, False, "/status"),
            CommandSpec(
                "Inspection",
                "/help [COMMAND]",
                ("/?",),
                False,
                False,
                "/help run",
            ),
            CommandSpec("Exit", "/quit", ("/exit",), False, False, "/quit"),
        )
        self.assertIsInstance(COMMANDS, tuple)
        self.assertEqual(COMMANDS, expected)

    def test_registry_names_aliases_and_capability_flags_are_consistent(
        self,
    ) -> None:
        canonical = [spec.syntax.split(" ", 1)[0] for spec in COMMANDS]
        aliases = [alias for spec in COMMANDS for alias in spec.aliases]
        self.assertEqual(len(canonical), len(set(canonical)))
        self.assertEqual(len(aliases), len(set(aliases)))
        self.assertTrue(set(canonical).isdisjoint(aliases))
        self.assertTrue(all(name.startswith("/") for name in canonical))
        self.assertTrue(all(isinstance(spec.aliases, tuple) for spec in COMMANDS))
        self.assertEqual(
            {
                name
                for name, spec in zip(canonical, COMMANDS)
                if spec.may_call_provider
            },
            {"/say", "/run", "/draft-file"},
        )
        self.assertEqual(
            {
                name
                for name, spec in zip(canonical, COMMANDS)
                if spec.may_write_artifacts
            },
            {"/settings", "/run", "/draft-file"},
        )
        settings = next(
            spec for spec in COMMANDS if spec.syntax == "/settings"
        )
        self.assertFalse(settings.may_call_provider)
        self.assertTrue(settings.may_write_artifacts)
        self.assertTrue(
            all(
                not spec.may_write_artifacts or spec.may_call_provider
                for spec in COMMANDS
                if spec is not settings
            )
        )
        self.assertNotIn("message", canonical)
        self.assertNotIn("routed_message", canonical)

    def test_advertised_run_forms_match_parser_behavior(self) -> None:
        run_spec = next(spec for spec in COMMANDS if spec.syntax.startswith("/run"))
        self.assertEqual(
            run_spec.syntax,
            "/run [PROMPT] | /run --fresh PROMPT",
        )

        cases = (
            ("/run", InteractiveAction(ActionKind.RUN)),
            ("/run PROMPT", InteractiveAction(ActionKind.RUN, text="PROMPT")),
            (
                "/run --fresh PROMPT",
                InteractiveAction(ActionKind.RUN_FRESH, text="PROMPT"),
            ),
        )
        for line, expected in cases:
            with self.subTest(line=line):
                self.assertEqual(parse_interactive_line(line), expected)

        incomplete_fresh = parse_interactive_line("/run --fresh")
        self.assertEqual(incomplete_fresh.kind, ActionKind.PARSE_ERROR)
        self.assertIsInstance(incomplete_fresh.error, str)
        self.assertTrue(incomplete_fresh.error)


class SuccessfulParsingTests(unittest.TestCase):
    def test_every_action_has_a_representative_parsed_form(self) -> None:
        cases = (
            ("", InteractiveAction(ActionKind.IGNORED)),
            (
                "ordinary text",
                InteractiveAction(ActionKind.MESSAGE, text="ordinary text"),
            ),
            (
                "@plot routed text",
                InteractiveAction(
                    ActionKind.ROUTED_MESSAGE,
                    agent_id=PLOT_ARCHITECT,
                    text="routed text",
                ),
            ),
            (
                "/to plot",
                InteractiveAction(ActionKind.SET_TARGET, agent_id=PLOT_ARCHITECT),
            ),
            ("/agents", InteractiveAction(ActionKind.LIST_AGENTS)),
            ("/compose", InteractiveAction(ActionKind.COMPOSE)),
            ("/send", InteractiveAction(ActionKind.SEND)),
            ("/cancel", InteractiveAction(ActionKind.CANCEL)),
            ("/run prompt", InteractiveAction(ActionKind.RUN, text="prompt")),
            (
                "/run --fresh prompt",
                InteractiveAction(ActionKind.RUN_FRESH, text="prompt"),
            ),
            (
                "/draft-file draft.md",
                InteractiveAction(ActionKind.DRAFT_FILE, argument="draft.md"),
            ),
            (
                "/search council vessel",
                InteractiveAction(ActionKind.SEARCH, text="council vessel"),
            ),
            (
                "/transcript c_0123456789 24 --from 25",
                InteractiveAction(
                    ActionKind.TRANSCRIPT,
                    argument="c_0123456789",
                    count=24,
                    start_sequence=25,
                ),
            ),
            (
                "/inspect last",
                InteractiveAction(ActionKind.INSPECT, argument="last"),
            ),
            ("/history 20", InteractiveAction(ActionKind.HISTORY, count=20)),
            ("/status", InteractiveAction(ActionKind.STATUS)),
            ("/settings", InteractiveAction(ActionKind.SETTINGS)),
            (
                "/language ko-KR",
                InteractiveAction(ActionKind.SET_LANGUAGE, argument="ko-KR"),
            ),
            (
                "/persona plot meticulous.v2",
                InteractiveAction(
                    ActionKind.SET_PERSONA,
                    agent_id=PLOT_ARCHITECT,
                    argument="meticulous.v2",
                ),
            ),
            ("/reset", InteractiveAction(ActionKind.RESET)),
            ("/session list", InteractiveAction(ActionKind.SESSION_LIST)),
            (
                "/session create chapter-01",
                InteractiveAction(ActionKind.SESSION_CREATE, argument="chapter-01"),
            ),
            (
                "/session open chapter-01",
                InteractiveAction(
                    ActionKind.SESSION_OPEN,
                    argument="chapter-01",
                ),
            ),
            ("/session pause", InteractiveAction(ActionKind.SESSION_PAUSE)),
            (
                "/conversation new Chapter 1 opening",
                InteractiveAction(
                    ActionKind.CONVERSATION_NEW,
                    argument="Chapter 1 opening",
                ),
            ),
            (
                "/conversation list motivation",
                InteractiveAction(
                    ActionKind.CONVERSATION_LIST,
                    argument="motivation",
                ),
            ),
            (
                "/conversation list --all archived",
                InteractiveAction(
                    ActionKind.CONVERSATION_LIST_ALL,
                    argument="archived",
                ),
            ),
            (
                "/conversation use c_0123456789abcdef",
                InteractiveAction(
                    ActionKind.CONVERSATION_USE,
                    argument="c_0123456789abcdef",
                ),
            ),
            (
                "/conversation subject Revised opening",
                InteractiveAction(
                    ActionKind.CONVERSATION_SUBJECT,
                    argument="Revised opening",
                ),
            ),
            (
                "/conversation archive c_0123456789abcdef",
                InteractiveAction(
                    ActionKind.CONVERSATION_ARCHIVE,
                    argument="c_0123456789abcdef",
                ),
            ),
            (
                "/help run",
                InteractiveAction(ActionKind.HELP, argument="/run"),
            ),
            ("/quit", InteractiveAction(ActionKind.QUIT)),
        )
        self.assertEqual(
            {expected.kind for _, expected in cases},
            set(ActionKind).difference({ActionKind.PARSE_ERROR}),
        )
        for line, expected in cases:
            with self.subTest(line=line):
                self.assertEqual(parse_interactive_line(line), expected)

    def test_all_agent_aliases_normalize_in_each_agent_command(self) -> None:
        for alias, agent_id in AGENT_ALIASES.items():
            cases = (
                (
                    f"@{alias} routed",
                    InteractiveAction(
                        ActionKind.ROUTED_MESSAGE,
                        agent_id=agent_id,
                        text="routed",
                    ),
                ),
                (
                    f"@{alias}\t\trouted  ",
                    InteractiveAction(
                        ActionKind.ROUTED_MESSAGE,
                        agent_id=agent_id,
                        text="\trouted  ",
                    ),
                ),
                (
                    f"/to {alias}",
                    InteractiveAction(ActionKind.SET_TARGET, agent_id=agent_id),
                ),
                (
                    f"/agent\t{alias}",
                    InteractiveAction(ActionKind.SET_TARGET, agent_id=agent_id),
                ),
                (
                    f"/persona {alias} persona-name",
                    InteractiveAction(
                        ActionKind.SET_PERSONA,
                        agent_id=agent_id,
                        argument="persona-name",
                    ),
                ),
            )
            for line, expected in cases:
                with self.subTest(alias=alias, line=line):
                    self.assertEqual(parse_interactive_line(line), expected)

    def test_command_aliases_and_help_references_normalize_to_canonical(
        self,
    ) -> None:
        self.assertEqual(
            parse_interactive_line("/agent plot"),
            InteractiveAction(ActionKind.SET_TARGET, agent_id=PLOT_ARCHITECT),
        )
        self.assertEqual(
            parse_interactive_line("/exit"),
            InteractiveAction(ActionKind.QUIT),
        )
        self.assertEqual(
            parse_interactive_line("/?"),
            InteractiveAction(ActionKind.HELP),
        )

        for name, canonical in COMMAND_NAMES.items():
            for reference in (name, f"/{name}"):
                for help_command in ("/help", "/?"):
                    line = f"{help_command} {reference}"
                    with self.subTest(line=line):
                        self.assertEqual(
                            parse_interactive_line(line),
                            InteractiveAction(ActionKind.HELP, argument=canonical),
                        )

    def test_optional_arguments_treat_ascii_blank_remainders_as_omitted(
        self,
    ) -> None:
        cases = (
            ("/run", InteractiveAction(ActionKind.RUN)),
            ("/run ", InteractiveAction(ActionKind.RUN)),
            ("/run\t\t", InteractiveAction(ActionKind.RUN)),
            ("/inspect ", InteractiveAction(ActionKind.INSPECT)),
            ("/history\t\t", InteractiveAction(ActionKind.HISTORY)),
            (
                "/session open ",
                InteractiveAction(ActionKind.SESSION_OPEN),
            ),
            (
                "/conversation new ",
                InteractiveAction(ActionKind.CONVERSATION_NEW),
            ),
            (
                "/conversation list\t\t",
                InteractiveAction(ActionKind.CONVERSATION_LIST),
            ),
            (
                "/conversation list --all ",
                InteractiveAction(ActionKind.CONVERSATION_LIST_ALL),
            ),
            (
                "/conversation archive\t",
                InteractiveAction(ActionKind.CONVERSATION_ARCHIVE),
            ),
            ("/help\t", InteractiveAction(ActionKind.HELP)),
        )
        for line, expected in cases:
            with self.subTest(line=line):
                self.assertEqual(parse_interactive_line(line), expected)

    def test_run_fresh_recognizes_the_first_non_delimiter_token(self) -> None:
        cases = (
            ("/run --fresh prompt", "prompt"),
            ("/run  --fresh  prompt  ", " prompt  "),
            ("/run\t\t--fresh\t\tprompt\t", "\tprompt\t"),
            ("/run --fresh\t prompt", " prompt"),
        )
        for line, text in cases:
            with self.subTest(line=line):
                self.assertEqual(
                    parse_interactive_line(line),
                    InteractiveAction(ActionKind.RUN_FRESH, text=text),
                )

    def test_token_arguments_accept_printable_non_whitespace_values(self) -> None:
        cases = (
            (
                "/language 한국어.v2",
                InteractiveAction(ActionKind.SET_LANGUAGE, argument="한국어.v2"),
            ),
            (
                "/persona plot meticulous.v2",
                InteractiveAction(
                    ActionKind.SET_PERSONA,
                    agent_id=PLOT_ARCHITECT,
                    argument="meticulous.v2",
                ),
            ),
            (
                "/session create chapter.v2",
                InteractiveAction(ActionKind.SESSION_CREATE, argument="chapter.v2"),
            ),
            (
                "/inspect debate.v2",
                InteractiveAction(ActionKind.INSPECT, argument="debate.v2"),
            ),
        )
        for line, expected in cases:
            with self.subTest(line=line):
                self.assertEqual(parse_interactive_line(line), expected)


class InvalidInputTests(ParserAssertions, unittest.TestCase):
    def test_parse_failures_are_typed_values_instead_of_exceptions(self) -> None:
        cases = (
            7,
            None,
            "/unknown",
            "/say",
            "@plot",
            "/to",
            "/run --fresh",
            "/draft-file",
            "/session",
            "/history zero",
        )
        for line in cases:
            with self.subTest(line=line):
                self.assert_parse_error(line)

    def test_search_requires_and_preserves_one_opaque_query(self) -> None:
        for line in ("/search", "/search ", "/search\t\t"):
            with self.subTest(line=line):
                self.assert_parse_error(line)

        cases = (
            ("/search council vessel", "council vessel"),
            ("/search  council  vessel ", " council  vessel "),
            ("/search\t\tcouncil\tvessel\t", "\tcouncil\tvessel\t"),
            ("/search --from 25", "--from 25"),
        )
        for line, query in cases:
            with self.subTest(line=line):
                self.assertEqual(
                    parse_interactive_line(line),
                    InteractiveAction(ActionKind.SEARCH, text=query),
                )

    def test_transcript_accepts_valid_forms_without_service_limits(self) -> None:
        cases = (
            (
                "/transcript c_0123456789",
                InteractiveAction(
                    ActionKind.TRANSCRIPT,
                    argument="c_0123456789",
                ),
            ),
            (
                "/transcript c_0123456789 24",
                InteractiveAction(
                    ActionKind.TRANSCRIPT,
                    argument="c_0123456789",
                    count=24,
                ),
            ),
            (
                "/transcript c_0123456789 --from 25",
                InteractiveAction(
                    ActionKind.TRANSCRIPT,
                    argument="c_0123456789",
                    start_sequence=25,
                ),
            ),
            (
                "/transcript c_0123456789 101 --from 00025",
                InteractiveAction(
                    ActionKind.TRANSCRIPT,
                    argument="c_0123456789",
                    count=101,
                    start_sequence=25,
                ),
            ),
        )
        for line, expected in cases:
            with self.subTest(line=line):
                self.assertEqual(parse_interactive_line(line), expected)

    def test_transcript_rejects_invalid_arity_flags_and_numeric_tokens(self) -> None:
        cases = (
            "/transcript",
            "/transcript --from 2",
            "/transcript id count",
            "/transcript id 0",
            "/transcript id +1",
            "/transcript id 1_0",
            "/transcript id ١",
            "/transcript id １",
            "/transcript id 2 3",
            "/transcript id --unknown 2",
            "/transcript id 2 --unknown 3",
            "/transcript id --from",
            "/transcript id --from 0",
            "/transcript id --from +1",
            "/transcript id --from ١",
            "/transcript id --from 2 3",
            "/transcript id --from 2 --from 3",
            "/transcript id 2 --from 3 extra",
            "/transcript id 2 --from 3 --from 4",
        )
        for line in cases:
            with self.subTest(line=line):
                self.assert_parse_error(line)

    def test_conversation_grammar_and_opaque_remainders_are_exact(self) -> None:
        cases = (
            (
                "/conversation new",
                InteractiveAction(ActionKind.CONVERSATION_NEW),
            ),
            (
                "/conversation new  Subject  with  spacing ",
                InteractiveAction(
                    ActionKind.CONVERSATION_NEW,
                    argument=" Subject  with  spacing ",
                ),
            ),
            (
                "/conversation list",
                InteractiveAction(ActionKind.CONVERSATION_LIST),
            ),
            (
                "/conversation list  Mara  motivation ",
                InteractiveAction(
                    ActionKind.CONVERSATION_LIST,
                    argument="Mara  motivation ",
                ),
            ),
            (
                "/conversation list --all",
                InteractiveAction(ActionKind.CONVERSATION_LIST_ALL),
            ),
            (
                "/conversation list  --all  old subject ",
                InteractiveAction(
                    ActionKind.CONVERSATION_LIST_ALL,
                    argument="old subject ",
                ),
            ),
            (
                "/conversation use ../unsafe",
                InteractiveAction(
                    ActionKind.CONVERSATION_USE,
                    argument="../unsafe",
                ),
            ),
            (
                "/conversation subject \u00a0",
                InteractiveAction(
                    ActionKind.CONVERSATION_SUBJECT,
                    argument="\u00a0",
                ),
            ),
            (
                "/conversation archive ../unsafe",
                InteractiveAction(
                    ActionKind.CONVERSATION_ARCHIVE,
                    argument="../unsafe",
                ),
            ),
        )
        for line, expected in cases:
            with self.subTest(line=line):
                self.assertEqual(parse_interactive_line(line), expected)

    def test_conversation_subcommands_enforce_arity_and_flag_position(self) -> None:
        cases = (
            "/conversation",
            "/conversation unknown",
            "/conversation use",
            "/conversation use one two",
            "/conversation subject",
            "/conversation subject   ",
            "/conversation archive one two",
            "/conversation list --unknown",
            "/conversation list --all --all",
            "/conversation list query --all",
            "/conversation list --all query --all",
        )
        for line in cases:
            with self.subTest(line=line):
                self.assert_parse_error(line)

    def test_case_sensitive_and_missing_delimiter_near_misses_fail(self) -> None:
        cases = (
            "/RUN",
            "/Agents",
            "@Plot text",
            "/saytext",
            "/run--fresh prompt",
            "@plottext",
            "/say\u00a0text",
        )
        for line in cases:
            with self.subTest(line=line):
                self.assert_parse_error(line)

    def test_required_remainders_reject_missing_or_ascii_blank_values(self) -> None:
        cases = (
            "/say",
            "/say   ",
            "/say\t\t",
            "@plot",
            "@plot   ",
            "/draft-file",
            "/draft-file\t\t",
            "/run --fresh",
            "/run --fresh   ",
            "/run  --fresh\t\t",
        )
        for line in cases:
            with self.subTest(line=line):
                self.assert_parse_error(line)

    def test_fixed_commands_reject_extra_arguments(self) -> None:
        for command in (
            "/agents",
            "/compose",
            "/send",
            "/cancel",
            "/status",
            "/reset",
            "/settings",
            "/session list",
            "/session pause",
            "/quit",
            "/exit",
        ):
            line = f"{command} extra"
            with self.subTest(line=line):
                self.assert_parse_error(line)

    def test_token_commands_enforce_arity(self) -> None:
        cases = (
            "/to",
            "/to plot extra",
            "/agent",
            "/inspect one two",
            "/history 1 2",
            "/language ko extra",
            "/persona plot",
            "/persona plot name extra",
            "/session unknown",
            "/session list extra",
            "/session create",
            "/session create   ",
            "/session create name extra",
            "/session open name extra",
            "/session pause extra",
            "/help run extra",
            "/? run extra",
        )
        for line in cases:
            with self.subTest(line=line):
                self.assert_parse_error(line)

    def test_unknown_or_incomplete_run_flags_fail(self) -> None:
        for line in (
            "/run --stale prompt",
            "/run --freshish prompt",
            "/run  --unknown prompt",
            "/run\t-- prompt",
        ):
            with self.subTest(line=line):
                self.assert_parse_error(line)

    def test_history_accepts_only_positive_ascii_digit_counts(self) -> None:
        invalid = (
            "0",
            "00",
            "+1",
            "-1",
            "1.0",
            "1_0",
            "١",
            "１",
        )
        for value in invalid:
            with self.subTest(value=value):
                self.assert_parse_error(f"/history {value}")

    def test_token_arguments_reject_unicode_whitespace_and_controls(self) -> None:
        cases = (
            "/language ko\u00a0KR",
            "/language ko\x00KR",
            "/persona plot meticulous\x7f",
            "/session create chapter\vone",
            "/session open chapter\u2003one",
            "/inspect debate\x00one",
            "/to pl\x00ot",
        )
        for line in cases:
            with self.subTest(line=line):
                self.assert_parse_error(line)


class PreservationAndPrecedenceTests(ParserAssertions, unittest.TestCase):
    def test_one_terminal_line_ending_is_removed(self) -> None:
        bases = (
            "ordinary  text ",
            "/run prompt",
            "@plot message",
            "/quit",
            "",
        )
        for base in bases:
            expected = parse_interactive_line(base)
            for ending in ("\n", "\r\n"):
                with self.subTest(base=base, ending=repr(ending)):
                    self.assertEqual(parse_interactive_line(base + ending), expected)

    def test_every_remaining_carriage_return_or_line_feed_fails(self) -> None:
        cases = (
            "text\r",
            "text\n\n",
            "text\r\n\r\n",
            "text\n\r",
            "/say first\nsecond",
            "/run first\rsecond",
            "@plot first\nsecond",
            "/language k\ro",
        )
        for line in cases:
            with self.subTest(line=repr(line)):
                self.assert_parse_error(line)

    def test_whitespace_only_lines_are_ignored_including_unicode(self) -> None:
        for line in ("", " ", "\t", " \t ", "\u00a0", "\u2003\t"):
            with self.subTest(line=repr(line)):
                self.assertEqual(
                    parse_interactive_line(line),
                    InteractiveAction(ActionKind.IGNORED),
                )

    def test_classification_uses_original_character_zero(self) -> None:
        for line in (
            " /quit",
            "\t@plot text",
            "\u00a0/run prompt",
            "\u2003@plot text",
        ):
            with self.subTest(line=repr(line)):
                self.assertEqual(
                    parse_interactive_line(line),
                    InteractiveAction(ActionKind.MESSAGE, text=line),
                )

    def test_say_routing_and_opaque_commands_preserve_literal_remainders(
        self,
    ) -> None:
        cases = (
            (
                "/say @plot /quit",
                InteractiveAction(ActionKind.MESSAGE, text="@plot /quit"),
            ),
            (
                "/say  literal\ttext  ",
                InteractiveAction(ActionKind.MESSAGE, text=" literal\ttext  "),
            ),
            (
                "@plot /quit",
                InteractiveAction(
                    ActionKind.ROUTED_MESSAGE,
                    agent_id=PLOT_ARCHITECT,
                    text="/quit",
                ),
            ),
            (
                "/run  prompt  ",
                InteractiveAction(ActionKind.RUN, text=" prompt  "),
            ),
            (
                '/draft-file  "a b";$(touch marker) ',
                InteractiveAction(
                    ActionKind.DRAFT_FILE,
                    argument=' "a b";$(touch marker) ',
                ),
            ),
        )
        for line, expected in cases:
            with self.subTest(line=line):
                self.assertEqual(parse_interactive_line(line), expected)


class SuggestionTests(ParserAssertions, unittest.TestCase):
    def test_threshold_ties_case_and_candidate_namespaces_are_exact(self) -> None:
        cases = (
            ("/rnx", "/run"),
            ("/ruunx", "/run"),
            ("/rxxxx", None),
            ("/agens", None),
            ("/euit", None),
            ("/Run", "/run"),
            ("/RUN", None),
            ("@plto text", "@plot_architect"),
            ("@plxxx text", None),
            ("@lat text", None),
            ("/help rnx", "/run"),
            ("/help agens", None),
            ("/session lest", "/session list"),
        )
        for line, suggestion in cases:
            with self.subTest(line=line):
                self.assert_parse_error(line, suggestion=suggestion)

    def test_bounded_mutations_follow_an_independent_distance_oracle(self) -> None:
        rng = random.Random(0xC0DEC)
        alphabet = "abcdefghijklmnopqrstuvwxyz_-"

        command_mutations: set[str] = set()
        for candidate in COMMAND_NAMES:
            if len(candidate) > 1:
                index = rng.randrange(len(candidate))
                command_mutations.add(candidate[:index] + candidate[index + 1 :])
            index = rng.randrange(len(candidate) + 1)
            command_mutations.add(
                candidate[:index] + rng.choice(alphabet) + candidate[index:]
            )
        for mutation in sorted(command_mutations):
            if mutation in COMMAND_NAMES:
                continue
            with self.subTest(namespace="command", mutation=mutation):
                self.assert_parse_error(
                    f"/{mutation}",
                    suggestion=expected_suggestion(mutation, COMMAND_NAMES),
                )

        agent_candidates = {
            alias: f"@{agent_id}" for alias, agent_id in AGENT_ALIASES.items()
        }
        agent_mutations: set[str] = set()
        for candidate in agent_candidates:
            index = rng.randrange(len(candidate))
            agent_mutations.add(candidate[:index] + candidate[index + 1 :])
            index = rng.randrange(len(candidate) + 1)
            agent_mutations.add(
                candidate[:index] + rng.choice(alphabet) + candidate[index:]
            )
        for mutation in sorted(agent_mutations):
            if not mutation or mutation in agent_candidates:
                continue
            with self.subTest(namespace="agent", mutation=mutation):
                self.assert_parse_error(
                    f"@{mutation} text",
                    suggestion=expected_suggestion(mutation, agent_candidates),
                )


class DeterministicPropertyTests(ParserAssertions, unittest.TestCase):
    def test_bounded_ordinary_and_opaque_inputs_are_preserved(self) -> None:
        rng = random.Random(0x51DE)
        alphabet = "abcXYZ019 !#$%&'()*+,-.:;<=>?[\\]^_`{|}~\t"
        for index in range(100):
            tail = "".join(rng.choice(alphabet) for _ in range(rng.randrange(1, 32)))
            ordinary = f"m{tail}"
            with self.subTest(case="ordinary", index=index):
                self.assertEqual(
                    parse_interactive_line(ordinary),
                    InteractiveAction(ActionKind.MESSAGE, text=ordinary),
                )

            opaque = f"p{tail}"
            for prefix, kind, field_name in (
                ("/say ", ActionKind.MESSAGE, "text"),
                ("/run ", ActionKind.RUN, "text"),
                ("/draft-file ", ActionKind.DRAFT_FILE, "argument"),
            ):
                expected = InteractiveAction(kind, **{field_name: opaque})
                with self.subTest(case=prefix, index=index):
                    self.assertEqual(parse_interactive_line(prefix + opaque), expected)

    def test_positive_count_range_and_zero_padding_are_deterministic(self) -> None:
        for count in range(1, 251):
            for rendered in (str(count), f"{count:04d}"):
                with self.subTest(count=count, rendered=rendered):
                    self.assertEqual(
                        parse_interactive_line(f"/history {rendered}"),
                        InteractiveAction(ActionKind.HISTORY, count=count),
                    )

    def test_line_break_insertion_always_fails_except_one_terminal_ending(
        self,
    ) -> None:
        bases = ("message", "/run prompt", "@plot routed", "/language ko")
        for base in bases:
            for index in range(len(base) + 1):
                for line_break in ("\n", "\r", "\r\n"):
                    candidate = base[:index] + line_break + base[index:]
                    if index == len(base) and line_break in {"\n", "\r\n"}:
                        expected = parse_interactive_line(base)
                        with self.subTest(
                            base=base,
                            index=index,
                            line_break=repr(line_break),
                        ):
                            self.assertEqual(
                                parse_interactive_line(candidate),
                                expected,
                            )
                    else:
                        with self.subTest(
                            base=base,
                            index=index,
                            line_break=repr(line_break),
                        ):
                            self.assert_parse_error(candidate)

    def test_leading_ascii_delimiters_turn_commands_into_messages(self) -> None:
        for base in ("/quit", "/run prompt", "@plot text"):
            for prefix in (" ", "\t", " \t"):
                line = prefix + base
                with self.subTest(line=repr(line)):
                    self.assertEqual(
                        parse_interactive_line(line),
                        InteractiveAction(ActionKind.MESSAGE, text=line),
                    )


class ParserBoundaryTests(unittest.TestCase):
    def test_direct_module_public_surface_and_dependency_are_minimal(self) -> None:
        expected_exports = (
            "ActionKind",
            "AgentRouteSpec",
            "AGENT_ROUTES",
            "COMMANDS",
            "CommandSpec",
            "InteractiveAction",
            "parse_interactive_line",
        )
        self.assertEqual(tuple(commands_module.__all__), expected_exports)
        for name in ("AgentRouteSpec", "AGENT_ROUTES"):
            with self.subTest(facade_name=name):
                self.assertNotIn(name, story_writing_agents.__all__)
                self.assertFalse(hasattr(story_writing_agents, name))

        source_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "story_writing_agents"
            / "interactive_commands.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        internal_dependencies = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level > 0
        }
        self.assertEqual(internal_dependencies, {"agents"})

    def test_parsing_does_not_use_process_network_or_filesystem_writes(self) -> None:
        cwd_before = Path.cwd()
        environment_before = os.environ.copy()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = tuple(root.iterdir())
            with (
                mock.patch.object(subprocess, "Popen") as popen,
                mock.patch.object(subprocess, "run") as run,
                mock.patch.object(socket, "create_connection") as connect,
                mock.patch.object(Path, "write_text") as write_text,
                mock.patch.object(Path, "write_bytes") as write_bytes,
            ):
                for line in (
                    "ordinary",
                    "@plot routed",
                    "/run prompt",
                    f"/draft-file {root / 'missing.md'}",
                    "/search",
                    "/quit",
                ):
                    parse_interactive_line(line)
                popen.assert_not_called()
                run.assert_not_called()
                connect.assert_not_called()
                write_text.assert_not_called()
                write_bytes.assert_not_called()
            self.assertEqual(tuple(root.iterdir()), before)
        self.assertEqual(Path.cwd(), cwd_before)
        self.assertEqual(os.environ, environment_before)

    def test_module_and_facade_import_cleanly_outside_the_repository(self) -> None:
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
            "from story_writing_agents import interactive_commands as module; "
            "assert facade.parse_interactive_line is module.parse_interactive_line; "
            "assert facade.COMMANDS is module.COMMANDS; "
            "assert 'AgentRouteSpec' not in facade.__all__; "
            "assert 'AGENT_ROUTES' not in facade.__all__; "
            "assert not hasattr(facade, 'AgentRouteSpec'); "
            "assert not hasattr(facade, 'AGENT_ROUTES'); "
            "assert tuple(route.agent_id for route in module.AGENT_ROUTES) == "
            "tuple(facade.ROSTER); "
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


class CommandCompletionProjectionTests(unittest.TestCase):
    """Lock the provider-free fixed command grammar at exact cursor spans."""

    ROOT = (
        "/say",
        "/to",
        "/agent",
        "/agents",
        "/language",
        "/persona",
        "/reset",
        "/settings",
        "/run",
        "/draft-file",
        "/session",
        "/conversation",
        "/search",
        "/transcript",
        "/inspect",
        "/history",
        "/status",
        "/help",
        "/?",
        "/quit",
        "/exit",
    )
    HELP = ("/compose", "/send", "/cancel", *ROOT)
    AGENTS = (
        "lead",
        "orchestrator",
        "lead_orchestrator",
        "plot",
        "plot_architect",
        "character",
        "character_custodian",
        "execution",
        "prose",
        "execution_specialist",
    )

    def test_context_model_is_exact_frozen_slotted_picklable_and_internal(self) -> None:
        self.assertEqual(
            tuple(inspect.signature(CommandCompletionContext).parameters),
            ("position", "replacement_start", "replacement_end", "replacements"),
        )
        value = CommandCompletionContext("root.command", 0, 1, self.ROOT)
        self.assertFalse(hasattr(value, "__dict__"))
        self.assertEqual(pickle.loads(pickle.dumps(value)), value)
        with self.assertRaises(FrozenInstanceError):
            value.position = "changed"  # type: ignore[misc]
        self.assertNotIn("CommandCompletionContext", commands_module.__all__)
        self.assertNotIn("project_command_completion", commands_module.__all__)
        self.assertNotIn("CommandCompletionContext", story_writing_agents.__all__)
        self.assertFalse(hasattr(story_writing_agents, "CommandCompletionContext"))

    def test_exact_ordered_fixed_grammar_positions(self) -> None:
        cases = (
            ("/", 1, "root.command", 0, 1, self.ROOT),
            (
                "/session ",
                9,
                "session.subcommand",
                9,
                9,
                ("list", "create", "open", "pause"),
            ),
            (
                "/conversation ",
                14,
                "conversation.subcommand",
                14,
                14,
                ("new", "list", "use", "subject", "archive"),
            ),
            ("/to ", 4, "target.agent", 4, 4, self.AGENTS),
            ("/agent ", 7, "target.agent", 7, 7, self.AGENTS),
            ("/persona ", 9, "persona.agent", 9, 9, self.AGENTS),
            ("/run ", 5, "run.flag", 5, 5, ("--fresh",)),
            ("/conversation list ", 19, "conversation.list.flag", 19, 19, ("--all",)),
            ("/help ", 6, "help.command", 6, 6, self.HELP),
            ("/? ", 3, "help.command", 3, 3, self.HELP),
            ("/inspect ", 9, "inspect.argument", 9, 9, ("last",)),
            ("/transcript c ", 14, "transcript.after_id.flag", 14, 14, ("--from",)),
            (
                "/transcript c 24 ",
                17,
                "transcript.after_count.flag",
                17,
                17,
                ("--from",),
            ),
        )
        for buffer, cursor, position, start, end, replacements in cases:
            with self.subTest(buffer=buffer):
                self.assertEqual(
                    project_command_completion(buffer, cursor),
                    CommandCompletionContext(position, start, end, replacements),
                )

    def test_complete_token_spans_include_suffix_at_and_after_delimiters(self) -> None:
        cases = (
            ("/session list", 9, "session.subcommand", 9, 13),
            ("/help run", 6, "help.command", 6, 9),
            ("/run --fresh", 5, "run.flag", 5, 12),
            ("/session create tail", 11, "session.subcommand", 9, 15),
            ("/session\tlist", 9, "session.subcommand", 9, 13),
        )
        for buffer, cursor, position, start, end in cases:
            with self.subTest(buffer=buffer, cursor=cursor):
                projected = project_command_completion(buffer, cursor)
                self.assertIsNotNone(projected)
                assert projected is not None
                self.assertEqual(
                    (
                        projected.position,
                        projected.replacement_start,
                        projected.replacement_end,
                    ),
                    (position, start, end),
                )

        fixed_cases = (
            ("/run --fresh", (5, 8, 12), "run.flag", 5, 12, ("--fresh",)),
            (
                "/conversation list --all",
                (19, 22, 24),
                "conversation.list.flag",
                19,
                24,
                ("--all",),
            ),
            (
                "/transcript c --from",
                (14, 17, 20),
                "transcript.after_id.flag",
                14,
                20,
                ("--from",),
            ),
            (
                "/transcript c 24 --from",
                (17, 20, 23),
                "transcript.after_count.flag",
                17,
                23,
                ("--from",),
            ),
            ("/inspect la", (9, 10, 11), "inspect.argument", 9, 11, ("last",)),
            ("/inspect last", (9, 11, 13), "inspect.argument", 9, 13, ("last",)),
        )
        for buffer, cursors, position, start, end, replacements in fixed_cases:
            for cursor in cursors:
                with self.subTest(buffer=buffer, cursor=cursor):
                    self.assertEqual(
                        project_command_completion(buffer, cursor),
                        CommandCompletionContext(
                            position,
                            start,
                            end,
                            replacements,
                        ),
                    )

    def test_dynamic_and_invalid_grammar_positions_have_no_projection(self) -> None:
        cases = (
            ("", 0), ("ordinary", 3), (" /", 2), ("/say ", 5),
            ("/to plot ", 9), ("/persona plot ", 14),
            ("/conversation use ", 18), ("/transcript --from ", 19),
            ("/transcript c nope ", 19), ("/transcript c 0 ", 16),
            ("/session list ", 14), ("/HELP ", 6),
        )
        for buffer, cursor in cases:
            with self.subTest(buffer=buffer, cursor=cursor):
                self.assertIsNone(project_command_completion(buffer, cursor))

        free_cases = (
            ("/run prompt", (5, 8, 11)),
            ("/conversation list query", (19, 21, 24)),
            ("/transcript c 24", (14, 15, 16)),
            ("/inspect debate", (9, 12, 15)),
        )
        for buffer, cursors in free_cases:
            for cursor in cursors:
                with self.subTest(buffer=buffer, cursor=cursor):
                    self.assertIsNone(
                        project_command_completion(buffer, cursor)
                    )

    def test_partial_tokens_preserve_full_order_for_control_side_filtering(
        self,
    ) -> None:
        for buffer, cursor, position, replacements in (
            ("/s", 2, "root.command", self.ROOT),
            (
                "/session cr",
                11,
                "session.subcommand",
                ("list", "create", "open", "pause"),
            ),
            ("/to pl", 6, "target.agent", self.AGENTS),
            ("/help ru", 8, "help.command", self.HELP),
            ("/inspect la", 11, "inspect.argument", ("last",)),
            ("/run --f", 8, "run.flag", ("--fresh",)),
        ):
            with self.subTest(buffer=buffer):
                projected = project_command_completion(buffer, cursor)
                self.assertIsNotNone(projected)
                assert projected is not None
                self.assertEqual(projected.position, position)
                self.assertEqual(projected.replacements, replacements)

    def test_inputs_and_context_relations_are_validated_exactly(self) -> None:
        invalid_contexts = (
            ((1, 0, 0, ("x",)), TypeError),
            (("", 0, 0, ("x",)), ValueError),
            (("p", True, 0, ("x",)), TypeError),
            (("p", -1, 0, ("x",)), ValueError),
            (("p", 2, 1, ("x",)), ValueError),
            (("p", 0, 0, ["x"]), TypeError),
            (("p", 0, 0, ("",)), ValueError),
            (("p", 0, 0, ("x", "x")), ValueError),
        )
        for arguments, error_type in invalid_contexts:
            with self.subTest(arguments=arguments):
                with self.assertRaises(error_type):
                    CommandCompletionContext(*arguments)  # type: ignore[arg-type]
        for buffer, cursor, error_type in (
            (object(), 0, TypeError), ("/", True, TypeError),
            ("/", -1, ValueError), ("/", 2, ValueError),
        ):
            with self.subTest(buffer=buffer, cursor=cursor):
                with self.assertRaises(error_type):
                    project_command_completion(buffer, cursor)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
