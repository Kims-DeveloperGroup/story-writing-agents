"""Contract tests for provider-free interactive command parsing."""

from __future__ import annotations

import ast
import os
import random
import socket
import subprocess
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from unittest import mock

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


AGENT_ALIASES = {
    "lead": LEAD_ORCHESTRATOR,
    "orchestrator": LEAD_ORCHESTRATOR,
    "lead_orchestrator": LEAD_ORCHESTRATOR,
    "plot": PLOT_ARCHITECT,
    "plot_architect": PLOT_ARCHITECT,
    "character": CHARACTER_CUSTODIAN,
    "character_custodian": CHARACTER_CUSTODIAN,
    "execution": EXECUTION_SPECIALIST,
    "prose": EXECUTION_SPECIALIST,
    "execution_specialist": EXECUTION_SPECIALIST,
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
    "run": "/run",
    "draft-file": "/draft-file",
    "session": "/session",
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
    "error",
    "suggestion",
)

FIELD_SAMPLES = {
    "text": "text",
    "agent_id": PLOT_ARCHITECT,
    "argument": "argument",
    "count": 1,
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
    ActionKind.INSPECT: ({"argument"}, set()),
    ActionKind.HISTORY: ({"count"}, set()),
    ActionKind.STATUS: (set(), set()),
    ActionKind.SET_LANGUAGE: ({"argument"}, {"argument"}),
    ActionKind.SET_PERSONA: (
        {"agent_id", "argument"},
        {"agent_id", "argument"},
    ),
    ActionKind.RESET: (set(), set()),
    ActionKind.SESSION_LIST: (set(), set()),
    ActionKind.SESSION_NEW: ({"argument"}, {"argument"}),
    ActionKind.SESSION_RESUME: ({"argument"}, set()),
    ActionKind.SESSION_END: (set(), set()),
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
            "INSPECT": "inspect",
            "HISTORY": "history",
            "STATUS": "status",
            "SET_LANGUAGE": "set_language",
            "SET_PERSONA": "set_persona",
            "RESET": "reset",
            "SESSION_LIST": "session_list",
            "SESSION_NEW": "session_new",
            "SESSION_RESUME": "session_resume",
            "SESSION_END": "session_end",
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
        self.assertFalse(hasattr(InteractiveAction(ActionKind.IGNORED), "__dict__"))
        self.assertFalse(hasattr(COMMANDS[0], "__dict__"))

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
    def test_registry_is_the_exact_ordered_seventeen_row_contract(self) -> None:
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
                "Consensus",
                "/run [--fresh] [PROMPT]",
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
                "/session list | new NAME | resume [NAME] | end",
                (),
                False,
                False,
                "/session new chapter-01",
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
            {"/run", "/draft-file"},
        )
        self.assertTrue(
            all(
                not spec.may_write_artifacts or spec.may_call_provider
                for spec in COMMANDS
            )
        )
        self.assertNotIn("message", canonical)
        self.assertNotIn("routed_message", canonical)


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
                "/inspect last",
                InteractiveAction(ActionKind.INSPECT, argument="last"),
            ),
            ("/history 20", InteractiveAction(ActionKind.HISTORY, count=20)),
            ("/status", InteractiveAction(ActionKind.STATUS)),
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
                "/session new chapter-01",
                InteractiveAction(ActionKind.SESSION_NEW, argument="chapter-01"),
            ),
            (
                "/session resume chapter-01",
                InteractiveAction(
                    ActionKind.SESSION_RESUME,
                    argument="chapter-01",
                ),
            ),
            ("/session end", InteractiveAction(ActionKind.SESSION_END)),
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
                "/session resume ",
                InteractiveAction(ActionKind.SESSION_RESUME),
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
                "/session new chapter.v2",
                InteractiveAction(ActionKind.SESSION_NEW, argument="chapter.v2"),
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

    def test_issue_22_and_23_commands_remain_unknown_in_this_leaf(self) -> None:
        for line in ("/conversation", "/search", "/transcript"):
            with self.subTest(line=line):
                action = self.assert_parse_error(line)
                assert action.error is not None
                self.assertIn("unknown", action.error.lower())

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
            "/session list",
            "/session end",
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
            "/session new",
            "/session new   ",
            "/session new name extra",
            "/session resume name extra",
            "/session end extra",
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
            "/session new chapter\vone",
            "/session resume chapter\u2003one",
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
        expected_exports = {
            "ActionKind",
            "InteractiveAction",
            "CommandSpec",
            "COMMANDS",
            "parse_interactive_line",
        }
        self.assertEqual(set(commands_module.__all__), expected_exports)

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
