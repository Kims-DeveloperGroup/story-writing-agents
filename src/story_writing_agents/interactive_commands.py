"""Pure parsing and metadata for the interactive command language."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import KW_ONLY, dataclass
from enum import StrEnum

from .agents import (
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
)


class ActionKind(StrEnum):
    """Actions recognized by the interactive command parser."""

    IGNORED = "ignored"
    MESSAGE = "message"
    ROUTED_MESSAGE = "routed_message"
    SET_TARGET = "set_target"
    LIST_AGENTS = "list_agents"
    COMPOSE = "compose"
    SEND = "send"
    CANCEL = "cancel"
    RUN = "run"
    RUN_FRESH = "run_fresh"
    DRAFT_FILE = "draft_file"
    INSPECT = "inspect"
    HISTORY = "history"
    STATUS = "status"
    SET_LANGUAGE = "set_language"
    SET_PERSONA = "set_persona"
    RESET = "reset"
    SESSION_LIST = "session_list"
    SESSION_NEW = "session_new"
    SESSION_RESUME = "session_resume"
    SESSION_END = "session_end"
    HELP = "help"
    QUIT = "quit"
    PARSE_ERROR = "parse_error"


@dataclass(frozen=True, slots=True)
class InteractiveAction:
    """One validated, provider-neutral interactive action."""

    kind: ActionKind
    _: KW_ONLY
    text: str | None = None
    agent_id: str | None = None
    argument: str | None = None
    count: int | None = None
    error: str | None = None
    suggestion: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ActionKind):
            raise TypeError("kind must be an ActionKind")

        values = {
            "text": self.text,
            "agent_id": self.agent_id,
            "argument": self.argument,
            "count": self.count,
            "error": self.error,
            "suggestion": self.suggestion,
        }
        for field_name in ("text", "agent_id", "argument", "error", "suggestion"):
            value = values[field_name]
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{field_name} must be a string or None")
        if self.count is not None and (
            not isinstance(self.count, int) or isinstance(self.count, bool)
        ):
            raise TypeError("count must be an integer or None")

        allowed, required = _ACTION_FIELD_RULES[self.kind]
        supplied = {name for name, value in values.items() if value is not None}
        unexpected = sorted(supplied.difference(allowed))
        if unexpected:
            fields = ", ".join(unexpected)
            raise ValueError(f"{self.kind.value} action does not accept: {fields}")
        missing = sorted(required.difference(supplied))
        if missing:
            fields = ", ".join(missing)
            raise ValueError(f"{self.kind.value} action requires: {fields}")

        if self.text is not None and _is_ascii_blank(self.text):
            raise ValueError("text must contain non-whitespace content")
        if self.count is not None and self.count <= 0:
            raise ValueError("count must be positive")
        if self.agent_id is not None and self.agent_id not in _CANONICAL_AGENTS:
            raise ValueError(f"unknown canonical agent ID: {self.agent_id}")
        if "argument" in required and self.argument == "":
            raise ValueError("argument must not be empty")
        if self.error == "":
            raise ValueError("error must not be empty")
        if self.suggestion == "":
            raise ValueError("suggestion must not be empty")


_NO_FIELDS = frozenset()
_CANONICAL_AGENTS = frozenset(
    {
        LEAD_ORCHESTRATOR,
        PLOT_ARCHITECT,
        CHARACTER_CUSTODIAN,
        EXECUTION_SPECIALIST,
    }
)
_ACTION_FIELD_RULES: dict[
    ActionKind, tuple[frozenset[str], frozenset[str]]
] = {
    ActionKind.IGNORED: (_NO_FIELDS, _NO_FIELDS),
    ActionKind.MESSAGE: (frozenset({"text"}), frozenset({"text"})),
    ActionKind.ROUTED_MESSAGE: (
        frozenset({"agent_id", "text"}),
        frozenset({"agent_id", "text"}),
    ),
    ActionKind.SET_TARGET: (
        frozenset({"agent_id"}),
        frozenset({"agent_id"}),
    ),
    ActionKind.LIST_AGENTS: (_NO_FIELDS, _NO_FIELDS),
    ActionKind.COMPOSE: (_NO_FIELDS, _NO_FIELDS),
    ActionKind.SEND: (_NO_FIELDS, _NO_FIELDS),
    ActionKind.CANCEL: (_NO_FIELDS, _NO_FIELDS),
    ActionKind.RUN: (frozenset({"text"}), _NO_FIELDS),
    ActionKind.RUN_FRESH: (frozenset({"text"}), frozenset({"text"})),
    ActionKind.DRAFT_FILE: (
        frozenset({"argument"}),
        frozenset({"argument"}),
    ),
    ActionKind.INSPECT: (frozenset({"argument"}), _NO_FIELDS),
    ActionKind.HISTORY: (frozenset({"count"}), _NO_FIELDS),
    ActionKind.STATUS: (_NO_FIELDS, _NO_FIELDS),
    ActionKind.SET_LANGUAGE: (
        frozenset({"argument"}),
        frozenset({"argument"}),
    ),
    ActionKind.SET_PERSONA: (
        frozenset({"agent_id", "argument"}),
        frozenset({"agent_id", "argument"}),
    ),
    ActionKind.RESET: (_NO_FIELDS, _NO_FIELDS),
    ActionKind.SESSION_LIST: (_NO_FIELDS, _NO_FIELDS),
    ActionKind.SESSION_NEW: (
        frozenset({"argument"}),
        frozenset({"argument"}),
    ),
    ActionKind.SESSION_RESUME: (frozenset({"argument"}), _NO_FIELDS),
    ActionKind.SESSION_END: (_NO_FIELDS, _NO_FIELDS),
    ActionKind.HELP: (frozenset({"argument"}), _NO_FIELDS),
    ActionKind.QUIT: (_NO_FIELDS, _NO_FIELDS),
    ActionKind.PARSE_ERROR: (
        frozenset({"error", "suggestion"}),
        frozenset({"error"}),
    ),
}


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """Static help and capability metadata for one interactive command."""

    group: str
    syntax: str
    aliases: tuple[str, ...]
    may_call_provider: bool
    may_write_artifacts: bool
    example: str


COMMANDS: tuple[CommandSpec, ...] = (
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
    CommandSpec("Agents", "/to NAME", ("/agent",), False, False, "/to plot"),
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


_AGENT_ALIASES: dict[str, str] = {
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

_COMMAND_ALIASES: dict[str, str] = {
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

_SESSION_COMMANDS: dict[str, str] = {
    "list": "/session list",
    "new": "/session new",
    "resume": "/session resume",
    "end": "/session end",
}

def parse_interactive_line(line: str) -> InteractiveAction:
    """Parse one input line without performing I/O or raising parse errors."""

    if not isinstance(line, str):
        return _parse_error("interactive input must be a string")

    normalized = line
    if normalized.endswith("\n"):
        normalized = normalized[:-1]
        if normalized.endswith("\r"):
            normalized = normalized[:-1]
    if "\r" in normalized or "\n" in normalized:
        return _parse_error("interactive input must contain exactly one line")

    if not normalized or normalized.isspace():
        return InteractiveAction(ActionKind.IGNORED)
    if normalized[0] == "/":
        return _parse_slash_command(normalized)
    if normalized[0] == "@":
        return _parse_routed_message(normalized)
    return InteractiveAction(ActionKind.MESSAGE, text=normalized)


def _parse_slash_command(line: str) -> InteractiveAction:
    head, tail = _split_head(line)
    command = head[1:]
    if command not in _COMMAND_ALIASES:
        return _parse_error(
            f"unknown command: {head}",
            _suggest(command, _COMMAND_ALIASES),
        )

    if command == "compose":
        return _no_argument_action(tail, ActionKind.COMPOSE, "/compose")
    if command == "send":
        return _no_argument_action(tail, ActionKind.SEND, "/send")
    if command == "cancel":
        return _no_argument_action(tail, ActionKind.CANCEL, "/cancel")
    if command == "say":
        text = _required_remainder(tail)
        if text is None:
            return _parse_error("/say requires text")
        return InteractiveAction(ActionKind.MESSAGE, text=text)
    if command in {"to", "agent"}:
        return _parse_target(tail, command)
    if command == "agents":
        return _no_argument_action(tail, ActionKind.LIST_AGENTS, "/agents")
    if command == "language":
        return _parse_language(tail)
    if command == "persona":
        return _parse_persona(tail)
    if command == "reset":
        return _no_argument_action(tail, ActionKind.RESET, "/reset")
    if command == "run":
        return _parse_run(tail)
    if command == "draft-file":
        argument = _required_remainder(tail)
        if argument is None:
            return _parse_error("/draft-file requires a path")
        return InteractiveAction(ActionKind.DRAFT_FILE, argument=argument)
    if command == "session":
        return _parse_session(tail)
    if command == "inspect":
        return _parse_inspect(tail)
    if command == "history":
        return _parse_history(tail)
    if command == "status":
        return _no_argument_action(tail, ActionKind.STATUS, "/status")
    if command in {"help", "?"}:
        return _parse_help(tail, command)
    if command in {"quit", "exit"}:
        return _no_argument_action(tail, ActionKind.QUIT, f"/{command}")

    return _parse_error(f"unsupported command: {head}")


def _parse_routed_message(line: str) -> InteractiveAction:
    head, tail = _split_head(line)
    alias = head[1:]
    agent_id = _AGENT_ALIASES.get(alias)
    if agent_id is None:
        return _parse_error(
            f"unknown agent: {head}",
            _suggest_agent(alias),
        )
    text = _required_remainder(tail)
    if text is None:
        return _parse_error(f"{head} requires a message")
    return InteractiveAction(
        ActionKind.ROUTED_MESSAGE,
        agent_id=agent_id,
        text=text,
    )


def _parse_target(tail: str | None, command: str) -> InteractiveAction:
    tokens = _tokens(tail)
    if len(tokens) != 1:
        return _parse_error(f"/{command} requires exactly one agent")
    alias = tokens[0]
    if not _is_valid_token(alias):
        return _parse_error(f"/{command} agent must be a control-free token")
    agent_id = _AGENT_ALIASES.get(alias)
    if agent_id is None:
        return _parse_error(
            f"unknown agent: {alias}",
            _suggest_agent(alias),
        )
    return InteractiveAction(ActionKind.SET_TARGET, agent_id=agent_id)


def _parse_language(tail: str | None) -> InteractiveAction:
    tokens = _tokens(tail)
    if len(tokens) != 1:
        return _parse_error("/language requires exactly one language")
    language = tokens[0]
    if not _is_valid_token(language):
        return _parse_error("/language argument must be a control-free token")
    return InteractiveAction(ActionKind.SET_LANGUAGE, argument=language)


def _parse_persona(tail: str | None) -> InteractiveAction:
    tokens = _tokens(tail)
    if len(tokens) != 2:
        return _parse_error("/persona requires an agent and one persona name")
    alias, persona = tokens
    if not _is_valid_token(alias) or not _is_valid_token(persona):
        return _parse_error("/persona arguments must be control-free tokens")
    agent_id = _AGENT_ALIASES.get(alias)
    if agent_id is None:
        return _parse_error(
            f"unknown agent: {alias}",
            _suggest_agent(alias),
        )
    return InteractiveAction(
        ActionKind.SET_PERSONA,
        agent_id=agent_id,
        argument=persona,
    )


def _parse_run(tail: str | None) -> InteractiveAction:
    prompt = _optional_remainder(tail)
    if prompt is None:
        return InteractiveAction(ActionKind.RUN)

    first_start = 0
    while first_start < len(prompt) and prompt[first_start] in " \t":
        first_start += 1
    first_end = first_start
    while first_end < len(prompt) and prompt[first_end] not in " \t":
        first_end += 1
    first_token = prompt[first_start:first_end]
    if not first_token.startswith("--"):
        return InteractiveAction(ActionKind.RUN, text=prompt)
    if first_token != "--fresh":
        return _parse_error(f"unknown /run flag: {first_token}")
    if first_end == len(prompt):
        return _parse_error("/run --fresh requires a prompt")

    fresh_prompt = prompt[first_end + 1 :]
    if _is_ascii_blank(fresh_prompt):
        return _parse_error("/run --fresh requires a prompt")
    return InteractiveAction(ActionKind.RUN_FRESH, text=fresh_prompt)


def _parse_session(tail: str | None) -> InteractiveAction:
    tokens = _tokens(tail)
    if not tokens:
        return _parse_error("/session requires a subcommand")

    subcommand = tokens[0]
    if subcommand not in _SESSION_COMMANDS:
        return _parse_error(
            f"unknown /session subcommand: {subcommand}",
            _suggest(subcommand, _SESSION_COMMANDS),
        )
    if subcommand == "list":
        if len(tokens) != 1:
            return _parse_error("/session list does not accept arguments")
        return InteractiveAction(ActionKind.SESSION_LIST)
    if subcommand == "new":
        if len(tokens) != 2:
            return _parse_error("/session new requires exactly one name")
        name = tokens[1]
        if not _is_valid_token(name):
            return _parse_error("/session new name must be a control-free token")
        return InteractiveAction(ActionKind.SESSION_NEW, argument=name)
    if subcommand == "resume":
        if len(tokens) > 2:
            return _parse_error("/session resume accepts at most one name")
        if len(tokens) == 1:
            return InteractiveAction(ActionKind.SESSION_RESUME)
        name = tokens[1]
        if not _is_valid_token(name):
            return _parse_error(
                "/session resume name must be a control-free token"
            )
        return InteractiveAction(ActionKind.SESSION_RESUME, argument=name)

    if len(tokens) != 1:
        return _parse_error("/session end does not accept arguments")
    return InteractiveAction(ActionKind.SESSION_END)


def _parse_inspect(tail: str | None) -> InteractiveAction:
    tokens = _tokens(tail)
    if not tokens:
        return InteractiveAction(ActionKind.INSPECT)
    if len(tokens) != 1:
        return _parse_error("/inspect accepts at most one debate identifier")
    argument = tokens[0]
    if not _is_valid_token(argument):
        return _parse_error("/inspect debate must be a control-free token")
    return InteractiveAction(ActionKind.INSPECT, argument=argument)


def _parse_history(tail: str | None) -> InteractiveAction:
    tokens = _tokens(tail)
    if not tokens:
        return InteractiveAction(ActionKind.HISTORY)
    if len(tokens) != 1:
        return _parse_error("/history accepts at most one count")
    value = tokens[0]
    if not value or any(character < "0" or character > "9" for character in value):
        return _parse_error("/history count must be positive ASCII digits")
    try:
        count = int(value)
    except ValueError:
        return _parse_error("/history count is too large")
    if count <= 0:
        return _parse_error("/history count must be positive ASCII digits")
    return InteractiveAction(ActionKind.HISTORY, count=count)


def _parse_help(tail: str | None, command: str) -> InteractiveAction:
    tokens = _tokens(tail)
    if not tokens:
        return InteractiveAction(ActionKind.HELP)
    if len(tokens) != 1:
        return _parse_error(f"/{command} accepts at most one command")
    requested = tokens[0]
    name = requested[1:] if requested.startswith("/") else requested
    canonical = _COMMAND_ALIASES.get(name)
    if canonical is None:
        return _parse_error(
            f"unknown help command: {requested}",
            _suggest(name, _COMMAND_ALIASES),
        )
    return InteractiveAction(ActionKind.HELP, argument=canonical)


def _no_argument_action(
    tail: str | None,
    kind: ActionKind,
    command: str,
) -> InteractiveAction:
    if _tokens(tail):
        return _parse_error(f"{command} does not accept arguments")
    return InteractiveAction(kind)


def _split_head(value: str) -> tuple[str, str | None]:
    for index, character in enumerate(value):
        if character in " \t":
            return value[:index], value[index:]
    return value, None


def _tokens(tail: str | None) -> tuple[str, ...]:
    if tail is None:
        return ()
    value = tail.strip(" \t")
    if not value:
        return ()
    return tuple(re.split(r"[ \t]+", value))


def _required_remainder(tail: str | None) -> str | None:
    if tail is None:
        return None
    value = tail[1:]
    return None if _is_ascii_blank(value) else value


def _optional_remainder(tail: str | None) -> str | None:
    if tail is None:
        return None
    value = tail[1:]
    return None if _is_ascii_blank(value) else value


def _is_ascii_blank(value: str) -> bool:
    return not value or all(character in " \t" for character in value)


def _is_valid_token(value: str) -> bool:
    return bool(value) and not any(
        character.isspace() or unicodedata.category(character) == "Cc"
        for character in value
    )


def _suggest_agent(value: str) -> str | None:
    canonical = _suggest(value, _AGENT_ALIASES)
    return None if canonical is None else f"@{canonical}"


def _suggest(value: str, candidates: dict[str, str]) -> str | None:
    distances = {
        candidate: _levenshtein(value, candidate) for candidate in candidates
    }
    if not distances:
        return None
    minimum = min(distances.values())
    closest = [
        candidate
        for candidate, distance in distances.items()
        if distance == minimum
    ]
    if minimum > 2 or len(closest) != 1:
        return None
    return candidates[closest[0]]


def _levenshtein(left: str, right: str) -> int:
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


def _parse_error(
    error: str,
    suggestion: str | None = None,
) -> InteractiveAction:
    return InteractiveAction(
        ActionKind.PARSE_ERROR,
        error=error,
        suggestion=suggestion,
    )


__all__ = [
    "ActionKind",
    "COMMANDS",
    "CommandSpec",
    "InteractiveAction",
    "parse_interactive_line",
]
