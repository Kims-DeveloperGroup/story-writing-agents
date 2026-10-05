"""Breaking-removal contracts for the provider-free interactive language."""

from __future__ import annotations

import inspect
import os
import pickle
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


DIRECT_EXECUTION_UNAVAILABLE = (
    "Direct story execution is unavailable in this integration stage. "
    "Session, conversation, history, search, and inspection remain available."
)

REMOVED_COMMANDS = (
    "/draft-file",
    "/say",
    "/to",
    "/agent",
    "/rag",
    "/compose",
    "/send",
    "/cancel",
)

EXPECTED_ACTION_KINDS = (
    "ignored",
    "lead_input",
    "lead_explore",
    "lead_adopt",
    "list_agents",
    "search",
    "transcript",
    "inspect",
    "exchanges",
    "history",
    "status",
    "auth",
    "settings",
    "set_language",
    "set_persona",
    "reset",
    "session_list",
    "session_create",
    "session_open",
    "session_pause",
    "conversation_new",
    "conversation_list",
    "conversation_list_all",
    "conversation_use",
    "conversation_subject",
    "conversation_archive",
    "help",
    "quit",
    "progress",
    "parse_error",
)

EXPECTED_COMMANDS = (
    CommandSpec("Writing", "/explore TEXT", (), True, False, "/explore Explore possible directions"),
    CommandSpec("Writing", "/adopt", (), True, True, "/adopt"),
    CommandSpec("Agents", "/agents", (), False, False, "/agents"),
    CommandSpec("Agents", "/auth", (), True, True, "/auth"),
    CommandSpec("Agents", "/settings", (), True, True, "/settings"),
    CommandSpec(
        "Session overrides",
        "/override language LANG | persona AGENT NAME | reset",
        (),
        False,
        False,
        "/override persona plot meticulous",
    ),
    CommandSpec(
        "Sessions",
        "/session list | create NAME | resume [NAME] | pause",
        (),
        False,
        False,
        "/session create chapter-01",
    ),
    CommandSpec(
        "Conversations",
        (
            "/conversation new [SUBJECT] | list [--all] [QUERY] | resume [ID] | "
            "subject SUBJECT | archive [ID]"
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
        "/transcript [ID [COUNT] [--from SEQUENCE]]",
        (),
        False,
        False,
        "/transcript",
    ),
    CommandSpec(
        "Inspection",
        "/inspect [DEBATE|last]",
        (),
        False,
        False,
        "/inspect last",
    ),
    CommandSpec("Inspection", "/exchanges", (), False, False, "/exchanges"),
    CommandSpec("Inspection", "/history [COUNT]", (), False, False, "/history 20"),
    CommandSpec("Inspection", "/status", (), False, False, "/status"),
    CommandSpec(
        "Inspection",
        "/help [COMMAND]",
        ("/?",),
        False,
        False,
        "/help status",
    ),
    CommandSpec(
        "Display",
        "/progress [on|off|toggle]",
        ("/toggle",),
        False,
        False,
        "/progress toggle",
    ),
    CommandSpec("Exit", "/quit", (), False, False, "/quit"),
)


class InteractiveSurfaceRetirementTests(unittest.TestCase):
    def test_action_command_and_module_surfaces_are_exact(self) -> None:
        self.assertEqual(
            tuple(member.value for member in ActionKind), EXPECTED_ACTION_KINDS
        )
        self.assertEqual(COMMANDS, EXPECTED_COMMANDS)
        self.assertEqual(
            tuple(field.name for field in fields(InteractiveAction)),
            (
                "kind",
                "text",
                "agent_id",
                "argument",
                "count",
                "start_sequence",
                "error",
                "suggestion",
            ),
        )
        self.assertEqual(
            commands_module.__all__,
            [
                "ActionKind",
                "AgentRouteSpec",
                "AGENT_ROUTES",
                "COMMANDS",
                "CommandSpec",
                "InteractiveAction",
                "parse_interactive_line",
            ],
        )
        self.assertEqual(
            tuple(
                name
                for name in story_writing_agents.__all__
                if name in commands_module.__all__
            ),
            (
                "COMMANDS",
                "ActionKind",
                "CommandSpec",
                "InteractiveAction",
                "parse_interactive_line",
            ),
        )
        for old_name in (
            "ProviderTextSpan",
            "project_provider_text_span",
            "ComposeMode",
        ):
            self.assertNotIn(old_name, commands_module.__all__)
            self.assertFalse(hasattr(commands_module, old_name))

    def test_agent_metadata_remains_but_no_action_can_route_execution(self) -> None:
        expected = (
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
        self.assertEqual(AGENT_ROUTES, expected)
        routed_values = {
            "message",
            "routed_message",
            "set_target",
            "compose",
            "send",
            "cancel",
            "run",
            "run_fresh",
            "draft_file",
            "rag",
        }
        self.assertTrue(routed_values.isdisjoint(EXPECTED_ACTION_KINDS))

    def test_values_are_frozen_slotted_keyword_only_and_strict(self) -> None:
        action = InteractiveAction(ActionKind.SEARCH, text="opaque query")
        self.assertFalse(hasattr(action, "__dict__"))
        self.assertEqual(pickle.loads(pickle.dumps(action)), action)
        with self.assertRaises(FrozenInstanceError):
            action.text = "changed"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            InteractiveAction(ActionKind.SEARCH, "query")  # type: ignore[misc]
        with self.assertRaisesRegex(ValueError, "requires: text"):
            InteractiveAction(ActionKind.SEARCH)
        with self.assertRaisesRegex(ValueError, "does not accept: agent_id"):
            InteractiveAction(ActionKind.STATUS, agent_id=PLOT_ARCHITECT)


class InteractiveParserRetirementTests(unittest.TestCase):
    def assert_unavailable(self, line: str) -> None:
        self.assertEqual(
            parse_interactive_line(line),
            InteractiveAction(
                ActionKind.PARSE_ERROR,
                error=DIRECT_EXECUTION_UNAVAILABLE,
            ),
        )

    def test_ordinary_input_is_literal_lead_input_and_removed_commands_stay_unknown(
        self,
    ) -> None:
        lead_inputs = (
            "Draft this scene.",
            "@lead critique this",
            "@plot revise pacing",
            "@character check continuity",
            "@execution draft prose",
        )
        for line in lead_inputs:
            with self.subTest(line=line):
                self.assertEqual(
                    parse_interactive_line(line),
                    InteractiveAction(ActionKind.LEAD_INPUT, text=line),
                )
        removed_inputs = (
            *(f"{command} payload" for command in REMOVED_COMMANDS),
            *(f"/help {command[1:]}" for command in REMOVED_COMMANDS),
        )
        for line in removed_inputs:
            with self.subTest(line=line):
                self.assert_unavailable(line)
        self.assertEqual(parse_interactive_line("/explore 한글 possibility"), InteractiveAction(ActionKind.LEAD_EXPLORE, text='한글 possibility'))
        self.assertEqual(parse_interactive_line("/adopt"), InteractiveAction(ActionKind.LEAD_ADOPT))
        self.assertIs(parse_interactive_line("/explore").kind, ActionKind.PARSE_ERROR)
        self.assertIs(parse_interactive_line("/adopt unexpected").kind, ActionKind.PARSE_ERROR)
        self.assertEqual(parse_interactive_line("Explore possible directions."), InteractiveAction(ActionKind.LEAD_INPUT, text='Explore possible directions.'))

    def test_run_is_an_ordinary_unknown_command(self) -> None:
        self.assertEqual(
            parse_interactive_line("/run payload"),
            InteractiveAction(ActionKind.PARSE_ERROR, error="unknown command: /run"),
        )
        self.assertEqual(
            parse_interactive_line("/help run"),
            InteractiveAction(
                ActionKind.PARSE_ERROR,
                error="unknown help command: run",
            ),
        )

    def test_retained_commands_parse_without_execution_payloads(self) -> None:
        cases = {
            "": InteractiveAction(ActionKind.IGNORED),
            "/agents": InteractiveAction(ActionKind.LIST_AGENTS),
            "/auth": InteractiveAction(ActionKind.AUTH),
            "/settings": InteractiveAction(ActionKind.SETTINGS),
            "/override language ko": InteractiveAction(
                ActionKind.SET_LANGUAGE, argument="ko"
            ),
            "/override persona plot meticulous": InteractiveAction(
                ActionKind.SET_PERSONA,
                agent_id=PLOT_ARCHITECT,
                argument="meticulous",
            ),
            "/override reset": InteractiveAction(ActionKind.RESET),
            "/session list": InteractiveAction(ActionKind.SESSION_LIST),
            "/session create novel": InteractiveAction(
                ActionKind.SESSION_CREATE, argument="novel"
            ),
            "/session resume": InteractiveAction(ActionKind.SESSION_OPEN),
            "/session pause": InteractiveAction(ActionKind.SESSION_PAUSE),
            "/conversation new Opening": InteractiveAction(
                ActionKind.CONVERSATION_NEW, argument="Opening"
            ),
            "/conversation list --all harbor": InteractiveAction(
                ActionKind.CONVERSATION_LIST_ALL, argument="harbor"
            ),
            "/conversation resume c_0123456789": InteractiveAction(
                ActionKind.CONVERSATION_USE, argument="c_0123456789"
            ),
            "/conversation subject Revised": InteractiveAction(
                ActionKind.CONVERSATION_SUBJECT, argument="Revised"
            ),
            "/conversation archive": InteractiveAction(ActionKind.CONVERSATION_ARCHIVE),
            "/search harbor": InteractiveAction(ActionKind.SEARCH, text="harbor"),
            "/transcript c_0123456789 24 --from 25": InteractiveAction(
                ActionKind.TRANSCRIPT,
                argument="c_0123456789",
                count=24,
                start_sequence=25,
            ),
            "/inspect last": InteractiveAction(ActionKind.INSPECT, argument="last"),
            "/exchanges": InteractiveAction(ActionKind.EXCHANGES),
            "/history 20": InteractiveAction(ActionKind.HISTORY, count=20),
            "/status": InteractiveAction(ActionKind.STATUS),
            "/help status": InteractiveAction(ActionKind.HELP, argument="/status"),
            "/?": InteractiveAction(ActionKind.HELP),
            "/progress": InteractiveAction(ActionKind.PROGRESS, argument="toggle"),
            "/progress on": InteractiveAction(ActionKind.PROGRESS, argument="on"),
            "/progress off": InteractiveAction(ActionKind.PROGRESS, argument="off"),
            "/toggle": InteractiveAction(ActionKind.PROGRESS, argument="toggle"),
            "/quit": InteractiveAction(ActionKind.QUIT),
        }
        for line, expected in cases.items():
            with self.subTest(line=line):
                self.assertEqual(parse_interactive_line(line), expected)

    def test_parser_is_provider_filesystem_process_and_network_free(self) -> None:
        with (
            mock.patch("builtins.open", side_effect=AssertionError("file I/O")),
            mock.patch.object(socket, "socket", side_effect=AssertionError("network")),
            mock.patch.object(subprocess, "run", side_effect=AssertionError("process")),
        ):
            for line in ("ordinary", "/run draft", "/status", "/search x"):
                parse_interactive_line(line)


class RetainedParserContractTests(unittest.TestCase):
    """Preserve validation and grammar depth for non-execution commands."""

    def assert_parse_error(self, line: object) -> InteractiveAction:
        action = parse_interactive_line(line)  # type: ignore[arg-type]
        self.assertIs(action.kind, ActionKind.PARSE_ERROR)
        self.assertIsInstance(action.error, str)
        self.assertTrue(action.error)
        return action

    def test_each_action_kind_accepts_only_its_payload_shape(self) -> None:
        rules = commands_module._ACTION_FIELD_RULES
        self.assertEqual(set(rules), set(ActionKind))
        samples = {
            "text": "value",
            "agent_id": PLOT_ARCHITECT,
            "argument": "value",
            "count": 1,
            "start_sequence": 1,
            "error": "error",
            "suggestion": "/status",
        }
        all_fields = set(samples)
        for kind, (allowed, required) in rules.items():
            valid = {name: samples[name] for name in required}
            with self.subTest(kind=kind, case="valid"):
                self.assertIs(InteractiveAction(kind, **valid).kind, kind)
            for name in required:
                missing = dict(valid)
                del missing[name]
                with (
                    self.subTest(kind=kind, missing=name),
                    self.assertRaises(ValueError),
                ):
                    InteractiveAction(kind, **missing)
            for name in all_fields.difference(allowed):
                extra = dict(valid)
                extra[name] = samples[name]
                with self.subTest(kind=kind, extra=name), self.assertRaises(ValueError):
                    InteractiveAction(kind, **extra)

    def test_action_specs_and_registry_are_immutable(self) -> None:
        with self.assertRaises(FrozenInstanceError):
            COMMANDS[0].syntax = "/changed"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            COMMANDS[0] = COMMANDS[0]  # type: ignore[index]
        with self.assertRaises(FrozenInstanceError):
            AGENT_ROUTES[0].prompt_alias = "changed"  # type: ignore[misc]

    def test_parse_failures_are_typed_values_instead_of_exceptions(self) -> None:
        for line in (7, None, "/unknown", "/session", "/history zero"):
            with self.subTest(line=line):
                self.assert_parse_error(line)

    def test_search_requires_and_preserves_one_opaque_query(self) -> None:
        for line in ("/search", "/search ", "/search\t\t"):
            with self.subTest(line=line):
                self.assert_parse_error(line)
        self.assertEqual(
            parse_interactive_line("/search  council  vessel "),
            InteractiveAction(ActionKind.SEARCH, text=" council  vessel "),
        )

    def test_transcript_valid_and_invalid_forms_are_exact(self) -> None:
        for line in ("/transcript", "/transcript   "):
            with self.subTest(line=line):
                self.assertEqual(
                    parse_interactive_line(line), InteractiveAction(ActionKind.TRANSCRIPT)
                )
        self.assertEqual(
            parse_interactive_line("/transcript c_0123456789 101 --from 00025"),
            InteractiveAction(
                ActionKind.TRANSCRIPT,
                argument="c_0123456789",
                count=101,
                start_sequence=25,
            ),
        )
        for line in (
            "/transcript --from 2",
            "/transcript id 0",
            "/transcript id +1",
            "/transcript id --from 0",
            "/transcript id 2 --from 3 extra",
        ):
            with self.subTest(line=line):
                self.assert_parse_error(line)

    def test_conversation_grammar_and_opaque_remainders_are_exact(self) -> None:
        cases = {
            "/conversation new  Subject  spaced ": InteractiveAction(
                ActionKind.CONVERSATION_NEW,
                argument=" Subject  spaced ",
            ),
            "/conversation list --all old subject ": InteractiveAction(
                ActionKind.CONVERSATION_LIST_ALL,
                argument="old subject ",
            ),
            "/conversation resume": InteractiveAction(ActionKind.CONVERSATION_USE),
            "/conversation archive ../unsafe": InteractiveAction(
                ActionKind.CONVERSATION_ARCHIVE,
                argument="../unsafe",
            ),
        }
        for line, expected in cases.items():
            with self.subTest(line=line):
                self.assertEqual(parse_interactive_line(line), expected)
        for line in (
            "/conversation",
            "/conversation use id",
            "/conversation subject",
            "/conversation list query --all",
        ):
            with self.subTest(line=line):
                self.assert_parse_error(line)

    def test_fixed_and_token_commands_enforce_arity(self) -> None:
        for line in (
            "/agents extra",
            "/status extra",
            "/auth extra",
            "/settings extra",
            "/exchanges extra",
            "/session create",
            "/session resume one two",
            "/override language ko extra",
            "/inspect one two",
            "/help status extra",
        ):
            with self.subTest(line=line):
                self.assert_parse_error(line)

    def test_history_accepts_only_positive_ascii_digit_counts(self) -> None:
        for rendered, expected in (("1", 1), ("0007", 7), ("250", 250)):
            self.assertEqual(
                parse_interactive_line(f"/history {rendered}"),
                InteractiveAction(ActionKind.HISTORY, count=expected),
            )
        for value in ("0", "00", "+1", "-1", "1_0", "١", "１"):
            with self.subTest(value=value):
                self.assert_parse_error(f"/history {value}")

    def test_line_endings_whitespace_and_original_character_zero_are_exact(
        self,
    ) -> None:
        for base in ("/status", "/search query", "ordinary"):
            for ending in ("\n", "\r\n"):
                self.assertEqual(
                    parse_interactive_line(base + ending),
                    parse_interactive_line(base),
                )
        for line in ("text\r", "text\n\n", "/search one\ntwo"):
            self.assert_parse_error(line)
        for line in ("", " \t ", "\u00a0", "\u2003\t"):
            self.assertEqual(
                parse_interactive_line(line),
                InteractiveAction(ActionKind.IGNORED),
            )
        self.assertEqual(
            parse_interactive_line(" /quit"),
            InteractiveAction(ActionKind.LEAD_INPUT, text=" /quit"),
        )

    def test_module_and_facade_import_cleanly_outside_the_repository(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(repository_root / "src")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        script = (
            "import story_writing_agents as facade; "
            "from story_writing_agents import interactive_commands as module; "
            "assert facade.parse_interactive_line is module.parse_interactive_line; "
            "assert facade.COMMANDS is module.COMMANDS; "
            "assert not hasattr(module, 'ProviderTextSpan'); "
            "print('ok')"
        )
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run(
                [sys.executable, "-c", script],
                cwd=temporary,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "ok\n")
            self.assertEqual(list(Path(temporary).iterdir()), [])


class InteractiveCompletionRetirementTests(unittest.TestCase):
    def test_root_and_help_completion_exclude_every_removed_execution_form(
        self,
    ) -> None:
        root = project_command_completion("/", 1)
        help_projection = project_command_completion("/help ", 6)
        self.assertIsNotNone(root)
        self.assertIsNotNone(help_projection)
        assert root is not None and help_projection is not None
        expected = tuple(
            item
            for spec in EXPECTED_COMMANDS
            for item in (spec.syntax.split(maxsplit=1)[0], *spec.aliases)
        )
        self.assertEqual(root.replacements, expected)
        self.assertEqual(help_projection.replacements, expected)
        for command in REMOVED_COMMANDS:
            self.assertNotIn(command, root.replacements)
            self.assertNotIn(command, help_projection.replacements)
        self.assertIsNone(project_command_completion("@plot", 5))
        self.assertIsNone(project_command_completion("ordinary", 4))

    def test_retained_fixed_completion_positions_are_exact(self) -> None:
        cases = (
            (
                "/session ",
                9,
                "session.subcommand",
                ("list", "create", "resume", "pause"),
            ),
            (
                "/conversation ",
                14,
                "conversation.subcommand",
                ("new", "list", "resume", "subject", "archive"),
            ),
            ("/override ", 10, "override.subcommand", ("language", "persona", "reset")),
            (
                "/override persona ",
                18,
                "override.persona.agent",
                tuple(route.agent_id for route in AGENT_ROUTES),
            ),
            ("/inspect ", 9, "inspect.argument", ("last",)),
        )
        for buffer, cursor, position, replacements in cases:
            with self.subTest(buffer=buffer):
                self.assertEqual(
                    project_command_completion(buffer, cursor),
                    CommandCompletionContext(position, cursor, cursor, replacements),
                )

    def test_internal_completion_value_is_strict_and_not_public(self) -> None:
        self.assertNotIn("CommandCompletionContext", commands_module.__all__)
        self.assertNotIn("project_command_completion", commands_module.__all__)
        self.assertEqual(
            tuple(inspect.signature(project_command_completion).parameters),
            ("buffer", "cursor"),
        )
        value = CommandCompletionContext("root", 0, 1, ("/help",))
        self.assertFalse(hasattr(value, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            value.position = "changed"  # type: ignore[misc]
        for buffer, cursor, error in (
            (object(), 0, TypeError),
            ("/", True, TypeError),
            ("/", -1, ValueError),
            ("/", 2, ValueError),
        ):
            with self.subTest(buffer=buffer, cursor=cursor), self.assertRaises(error):
                project_command_completion(buffer, cursor)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
