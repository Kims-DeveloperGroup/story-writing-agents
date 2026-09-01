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
    ROSTER,
)


class ActionKind(StrEnum):
    """Actions recognized by the interactive command parser."""

    IGNORED = "ignored"
    LIST_AGENTS = "list_agents"
    SEARCH = "search"
    TRANSCRIPT = "transcript"
    INSPECT = "inspect"
    EXCHANGES = "exchanges"
    HISTORY = "history"
    STATUS = "status"
    AUTH = "auth"
    SETTINGS = "settings"
    SET_LANGUAGE = "set_language"
    SET_PERSONA = "set_persona"
    RESET = "reset"
    SESSION_LIST = "session_list"
    SESSION_CREATE = "session_create"
    SESSION_OPEN = "session_open"
    SESSION_PAUSE = "session_pause"
    CONVERSATION_NEW = "conversation_new"
    CONVERSATION_LIST = "conversation_list"
    CONVERSATION_LIST_ALL = "conversation_list_all"
    CONVERSATION_USE = "conversation_use"
    CONVERSATION_SUBJECT = "conversation_subject"
    CONVERSATION_ARCHIVE = "conversation_archive"
    HELP = "help"
    QUIT = "quit"
    PARSE_ERROR = "parse_error"


@dataclass(frozen=True, slots=True)
class AgentRouteSpec:
    """Declare one canonical agent roster entry and retained display metadata."""

    agent_id: str
    prompt_alias: str
    aliases: tuple[str, ...]
    advisory_domain: str

    def __post_init__(self) -> None:
        """Validate route values without normalizing, mutating, or acquiring resources.

        Invalid identifiers, display metadata, or UTF-8 raise
        ``TypeError`` or ``ValueError`` before a route can enter the registry;
        validation owns no cleanup because it performs no external operation.
        """

        _validate_route_token("agent_id", self.agent_id)
        if self.agent_id not in ROSTER:
            raise ValueError(f"unknown canonical agent ID: {self.agent_id}")
        _validate_route_token("prompt_alias", self.prompt_alias)
        if not isinstance(self.aliases, tuple):
            raise TypeError("aliases must be a tuple")
        if not self.aliases:
            raise ValueError("aliases must not be empty")
        for alias in self.aliases:
            _validate_route_token("alias", alias)
        if len(set(self.aliases)) != len(self.aliases):
            raise ValueError("aliases must be unique within one route")
        if self.prompt_alias != self.aliases[0]:
            raise ValueError("prompt_alias must be the first alias")
        _validate_advisory_domain(self.advisory_domain)


def _validate_route_token(name: str, value: object) -> str:
    """Validate and return one named lowercase ASCII identifier token.

    Inputs are not normalized or mutated. Invalid types and values raise before
    publication, and validation acquires no resource requiring cleanup.
    """

    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if re.fullmatch(r"[a-z][a-z0-9_]*", value) is None:
        raise ValueError(f"{name} must be a lowercase ASCII identifier token")
    return value


def _validate_advisory_domain(value: object) -> str:
    """Validate and return one nonblank control-free strict-UTF-8 line.

    The input is preserved exactly. Invalid display copy raises before registry
    publication; no external state is read or left for cleanup.
    """

    if not isinstance(value, str):
        raise TypeError("advisory_domain must be a string")
    if not value or value.isspace():
        raise ValueError("advisory_domain must not be blank")
    if any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs"}
        or character in {"\u2028", "\u2029"}
        for character in value
    ):
        raise ValueError("advisory_domain must be one control-free line")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("advisory_domain must be strict UTF-8") from exc
    return value


def _validated_agent_routes(
    routes: tuple[AgentRouteSpec, ...],
) -> tuple[AgentRouteSpec, ...]:
    """Return a roster-complete globally unique registry without mutation.

    The ordered tuple is validated against ``ROSTER`` and returned unchanged.
    Invalid route types, canonical order, or duplicate metadata fail at import
    before publication; no external state exists to clean up.
    """

    if not isinstance(routes, tuple) or any(
        type(route) is not AgentRouteSpec for route in routes
    ):
        raise TypeError("agent routes must be a tuple of AgentRouteSpec")
    if tuple(route.agent_id for route in routes) != ROSTER:
        raise ValueError("agent route order must exactly match ROSTER")
    aliases = tuple(alias for route in routes for alias in route.aliases)
    if len(set(aliases)) != len(aliases):
        raise ValueError("agent aliases must be globally unique")
    return routes


AGENT_ROUTES: tuple[AgentRouteSpec, ...] = _validated_agent_routes(
    (
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
)


@dataclass(frozen=True, slots=True)
class InteractiveAction:
    """One validated, provider-neutral interactive action."""

    kind: ActionKind
    _: KW_ONLY
    text: str | None = None
    agent_id: str | None = None
    argument: str | None = None
    count: int | None = None
    start_sequence: int | None = None
    error: str | None = None
    suggestion: str | None = None

    def __post_init__(self) -> None:
        """Validate the declared action payload without external side effects."""

        if not isinstance(self.kind, ActionKind):
            raise TypeError("kind must be an ActionKind")

        values = {
            "text": self.text,
            "agent_id": self.agent_id,
            "argument": self.argument,
            "count": self.count,
            "start_sequence": self.start_sequence,
            "error": self.error,
            "suggestion": self.suggestion,
        }
        for field_name in (
            "text",
            "agent_id",
            "argument",
            "error",
            "suggestion",
        ):
            value = values[field_name]
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{field_name} must be a string or None")
        if self.count is not None and (
            not isinstance(self.count, int) or isinstance(self.count, bool)
        ):
            raise TypeError("count must be an integer or None")
        if self.start_sequence is not None and (
            not isinstance(self.start_sequence, int)
            or isinstance(self.start_sequence, bool)
        ):
            raise TypeError("start_sequence must be an integer or None")

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
        if self.start_sequence is not None and self.start_sequence <= 0:
            raise ValueError("start_sequence must be positive")
        if self.agent_id is not None and self.agent_id not in _CANONICAL_AGENTS:
            raise ValueError(f"unknown canonical agent ID: {self.agent_id}")
        if "argument" in required and self.argument == "":
            raise ValueError("argument must not be empty")
        if self.error == "":
            raise ValueError("error must not be empty")
        if self.suggestion == "":
            raise ValueError("suggestion must not be empty")


_NO_FIELDS = frozenset()
_CANONICAL_AGENTS = frozenset(route.agent_id for route in AGENT_ROUTES)
_ACTION_FIELD_RULES: dict[ActionKind, tuple[frozenset[str], frozenset[str]]] = {
    ActionKind.IGNORED: (_NO_FIELDS, _NO_FIELDS),
    ActionKind.LIST_AGENTS: (_NO_FIELDS, _NO_FIELDS),
    ActionKind.SEARCH: (frozenset({"text"}), frozenset({"text"})),
    ActionKind.TRANSCRIPT: (
        frozenset({"argument", "count", "start_sequence"}),
        frozenset({"argument"}),
    ),
    ActionKind.INSPECT: (frozenset({"argument"}), _NO_FIELDS),
    ActionKind.EXCHANGES: (_NO_FIELDS, _NO_FIELDS),
    ActionKind.HISTORY: (frozenset({"count"}), _NO_FIELDS),
    ActionKind.STATUS: (_NO_FIELDS, _NO_FIELDS),
    ActionKind.AUTH: (_NO_FIELDS, _NO_FIELDS),
    ActionKind.SETTINGS: (_NO_FIELDS, _NO_FIELDS),
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
    ActionKind.SESSION_CREATE: (
        frozenset({"argument"}),
        frozenset({"argument"}),
    ),
    ActionKind.SESSION_OPEN: (frozenset({"argument"}), _NO_FIELDS),
    ActionKind.SESSION_PAUSE: (_NO_FIELDS, _NO_FIELDS),
    ActionKind.CONVERSATION_NEW: (frozenset({"argument"}), _NO_FIELDS),
    ActionKind.CONVERSATION_LIST: (frozenset({"argument"}), _NO_FIELDS),
    ActionKind.CONVERSATION_LIST_ALL: (
        frozenset({"argument"}),
        _NO_FIELDS,
    ),
    ActionKind.CONVERSATION_USE: (
        frozenset({"argument"}),
        _NO_FIELDS,
    ),
    ActionKind.CONVERSATION_SUBJECT: (
        frozenset({"argument"}),
        frozenset({"argument"}),
    ),
    ActionKind.CONVERSATION_ARCHIVE: (frozenset({"argument"}), _NO_FIELDS),
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


@dataclass(frozen=True, slots=True)
class CommandCompletionContext:
    """Describe one cursor-active command token and ordered replacements."""

    position: str
    replacement_start: int
    replacement_end: int
    replacements: tuple[str, ...]

    def __post_init__(self) -> None:
        """Require one bounded span and exact unique replacement literals."""

        if type(self.position) is not str:
            raise TypeError("position must be a string")
        if not self.position:
            raise ValueError("position must be a nonempty string")
        for name, value in (
            ("replacement_start", self.replacement_start),
            ("replacement_end", self.replacement_end),
        ):
            if type(value) is not int:
                raise TypeError(f"{name} must be an integer")
            if value < 0:
                raise ValueError(f"{name} must not be negative")
        if self.replacement_end < self.replacement_start:
            raise ValueError("replacement_end must not precede replacement_start")
        if type(self.replacements) is not tuple or any(
            type(value) is not str for value in self.replacements
        ):
            raise TypeError("replacements must be a tuple of strings")
        if any(not value for value in self.replacements):
            raise ValueError("replacements must not contain empty strings")
        if len(set(self.replacements)) != len(self.replacements):
            raise ValueError("replacements must be unique")


COMMANDS: tuple[CommandSpec, ...] = (
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
    CommandSpec("Exit", "/quit", (), False, False, "/quit"),
)


_PERSONA_AGENT_ALIASES: dict[str, str] = {
    "lead": LEAD_ORCHESTRATOR,
    "orchestrator": LEAD_ORCHESTRATOR,
    LEAD_ORCHESTRATOR: LEAD_ORCHESTRATOR,
    "plot": PLOT_ARCHITECT,
    PLOT_ARCHITECT: PLOT_ARCHITECT,
    "character": CHARACTER_CUSTODIAN,
    CHARACTER_CUSTODIAN: CHARACTER_CUSTODIAN,
    "execution": EXECUTION_SPECIALIST,
    "prose": EXECUTION_SPECIALIST,
    EXECUTION_SPECIALIST: EXECUTION_SPECIALIST,
}

_COMMAND_ALIASES: dict[str, str] = {
    "agents": "/agents",
    "auth": "/auth",
    "override": "/override",
    "language": "/override",
    "persona": "/override",
    "reset": "/override",
    "settings": "/settings",
    "session": "/session",
    "conversation": "/conversation",
    "search": "/search",
    "transcript": "/transcript",
    "inspect": "/inspect",
    "exchanges": "/exchanges",
    "history": "/history",
    "status": "/status",
    "help": "/help",
    "?": "/help",
    "quit": "/quit",
}

_REMOVED_EXECUTION_COMMANDS = frozenset(
    {
        "agent",
        "cancel",
        "compose",
        "draft-file",
        "rag",
        "run",
        "say",
        "send",
        "to",
    }
)
_DIRECT_EXECUTION_UNAVAILABLE = (
    "Direct story execution is unavailable in this integration stage. "
    "Session, conversation, history, search, and inspection remain available."
)

_SESSION_COMMANDS: dict[str, str] = {
    "list": "/session list",
    "create": "/session create",
    "resume": "/session resume",
    "pause": "/session pause",
}

_CONVERSATION_COMMANDS: dict[str, str] = {
    "new": "/conversation new",
    "list": "/conversation list",
    "resume": "/conversation resume",
    "subject": "/conversation subject",
    "archive": "/conversation archive",
}

_OVERRIDE_COMMANDS: dict[str, str] = {
    "language": "/override language",
    "persona": "/override persona",
    "reset": "/override reset",
}

_CONVERSATION_ALL_FLAG = "--all"
_TRANSCRIPT_FROM_FLAG = "--from"
_INSPECT_LAST_ARGUMENT = "last"


def project_command_completion(
    buffer: str,
    cursor: int,
) -> CommandCompletionContext | None:
    """Project fixed command or leading-route grammar at one cursor boundary.

    The result contains grammar-owned ordered literals and the complete active
    token span. It performs no parsing, fuzzy matching, catalog lookup, I/O, or
    mutation. Dynamic grammar positions return ``None``.
    """

    if not isinstance(buffer, str):
        raise TypeError("buffer must be a string")
    buffer = str.__str__(buffer)
    if type(cursor) is not int:
        raise TypeError("cursor must be an integer")
    if cursor < 0 or cursor > len(buffer):
        raise ValueError("cursor must identify a buffer boundary")
    if not buffer.startswith("/"):
        return None

    start, end = _completion_token_span(buffer, cursor)
    active_token = buffer[start:end]
    preceding = _completion_preceding_tokens(buffer, start)

    if start == 0:
        return _completion_context(
            "root.command",
            start,
            end,
            _root_command_replacements(include_composition=False),
        )
    if preceding == ("/session",):
        return _completion_context(
            "session.subcommand",
            start,
            end,
            tuple(_SESSION_COMMANDS),
        )
    if preceding == ("/conversation",):
        return _completion_context(
            "conversation.subcommand",
            start,
            end,
            tuple(_CONVERSATION_COMMANDS),
        )
    if preceding == ("/override",):
        return _completion_context(
            "override.subcommand",
            start,
            end,
            tuple(_OVERRIDE_COMMANDS),
        )
    if preceding == ("/override", "persona"):
        return _completion_context(
            "override.persona.agent",
            start,
            end,
            _agent_completion_replacements(),
        )
    if preceding == ("/persona",):
        return _completion_context(
            "persona.agent",
            start,
            end,
            _agent_completion_replacements(),
        )
    if preceding == ("/conversation", "list"):
        if active_token and not active_token.startswith("-"):
            return None
        return _completion_context(
            "conversation.list.flag",
            start,
            end,
            (_CONVERSATION_ALL_FLAG,),
        )
    if preceding in {("/help",), ("/?",)}:
        return _completion_context(
            "help.command",
            start,
            end,
            _root_command_replacements(include_composition=True),
        )
    if preceding == ("/inspect",):
        if active_token and not _INSPECT_LAST_ARGUMENT.startswith(active_token):
            return None
        return _completion_context(
            "inspect.argument",
            start,
            end,
            (_INSPECT_LAST_ARGUMENT,),
        )
    if len(preceding) == 2 and preceding[0] == "/transcript":
        if preceding[1].startswith("--"):
            return None
        if active_token and not active_token.startswith("-"):
            return None
        return _completion_context(
            "transcript.after_id.flag",
            start,
            end,
            (_TRANSCRIPT_FROM_FLAG,),
        )
    if (
        len(preceding) == 3
        and preceding[0] == "/transcript"
        and not preceding[1].startswith("--")
        and _positive_ascii_integer(preceding[2]) is not None
    ):
        return _completion_context(
            "transcript.after_count.flag",
            start,
            end,
            (_TRANSCRIPT_FROM_FLAG,),
        )
    return None


def _completion_context(
    position: str,
    start: int,
    end: int,
    replacements: tuple[str, ...],
) -> CommandCompletionContext:
    """Build one validated command completion context without side effects."""

    return CommandCompletionContext(position, start, end, replacements)


def _completion_token_span(buffer: str, cursor: int) -> tuple[int, int]:
    """Return the maximal ASCII-delimited token active at one boundary."""

    if cursor > 0 and buffer[cursor - 1] in " \t":
        end = cursor
        while end < len(buffer) and buffer[end] not in " \t":
            end += 1
        return cursor, end
    start = cursor
    while start > 0 and buffer[start - 1] not in " \t":
        start -= 1
    end = cursor
    while end < len(buffer) and buffer[end] not in " \t":
        end += 1
    return start, end


def _completion_preceding_tokens(buffer: str, start: int) -> tuple[str, ...]:
    """Return exact complete ASCII-delimited tokens before the active span."""

    value = buffer[:start].strip(" \t")
    if not value:
        return ()
    return tuple(re.split(r"[ \t]+", value))


def _root_command_replacements(*, include_composition: bool) -> tuple[str, ...]:
    """Return canonical and alias tokens in exact public registry order."""

    del include_composition
    replacements: list[str] = []
    for spec in COMMANDS:
        replacements.append(spec.syntax.split(maxsplit=1)[0])
        replacements.extend(spec.aliases)
    return tuple(replacements)


def _agent_completion_replacements() -> tuple[str, ...]:
    """Return canonical agent identifiers in roster order."""

    return tuple(route.agent_id for route in AGENT_ROUTES)


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
    return _parse_error(_DIRECT_EXECUTION_UNAVAILABLE)


def _parse_slash_command(line: str) -> InteractiveAction:
    """Parse one slash-prefixed line into a typed action without mutation."""

    head, tail = _split_head(line)
    command = head[1:]
    if command in _REMOVED_EXECUTION_COMMANDS:
        return _parse_error(_DIRECT_EXECUTION_UNAVAILABLE)
    if command not in _COMMAND_ALIASES:
        return _parse_error(
            f"unknown command: {head}",
            _suggest(command, _COMMAND_ALIASES),
        )

    if command == "agents":
        return _no_argument_action(tail, ActionKind.LIST_AGENTS, "/agents")
    if command == "auth":
        return _no_argument_action(tail, ActionKind.AUTH, "/auth")
    if command == "override":
        return _parse_override(tail)
    if command == "language":
        return _parse_language(tail, command="/language")
    if command == "persona":
        return _parse_persona(tail, command="/persona")
    if command == "reset":
        return _no_argument_action(tail, ActionKind.RESET, "/reset")
    if command == "settings":
        return _no_argument_action(tail, ActionKind.SETTINGS, "/settings")
    if command == "session":
        return _parse_session(tail)
    if command == "conversation":
        return _parse_conversation(tail)
    if command == "search":
        return _parse_search(tail)
    if command == "transcript":
        return _parse_transcript(tail)
    if command == "inspect":
        return _parse_inspect(tail)
    if command == "exchanges":
        return _no_argument_action(tail, ActionKind.EXCHANGES, "/exchanges")
    if command == "history":
        return _parse_history(tail)
    if command == "status":
        return _no_argument_action(tail, ActionKind.STATUS, "/status")
    if command in {"help", "?"}:
        return _parse_help(tail, command)
    if command == "quit":
        return _no_argument_action(tail, ActionKind.QUIT, "/quit")

    return _parse_error(f"unsupported command: {head}")


def _parse_override(tail: str | None) -> InteractiveAction:
    """Parse the canonical session-override grammar without external state."""

    remainder = _remainder_after_ascii_delimiters(tail)
    if remainder is None:
        return _parse_error("/override requires a subcommand")

    subcommand, subcommand_tail = _split_head(remainder)
    if subcommand not in _OVERRIDE_COMMANDS:
        return _parse_error(
            f"unknown /override subcommand: {subcommand}",
            _suggest(subcommand, _OVERRIDE_COMMANDS),
        )
    if subcommand == "language":
        return _parse_language(
            subcommand_tail,
            command="/override language",
        )
    if subcommand == "persona":
        return _parse_persona(
            subcommand_tail,
            command="/override persona",
        )
    return _no_argument_action(
        subcommand_tail,
        ActionKind.RESET,
        "/override reset",
    )


def _parse_language(
    tail: str | None,
    *,
    command: str,
) -> InteractiveAction:
    """Parse exactly one control-free language token without external state."""

    tokens = _tokens(tail)
    if len(tokens) != 1:
        return _parse_error(f"{command} requires exactly one language")
    language = tokens[0]
    if not _is_valid_token(language):
        return _parse_error(f"{command} argument must be a control-free token")
    return InteractiveAction(ActionKind.SET_LANGUAGE, argument=language)


def _parse_persona(
    tail: str | None,
    *,
    command: str,
) -> InteractiveAction:
    """Parse a known agent and one control-free persona token.

    Valid input returns a persona action; all grammar failures return typed
    parse-error actions without I/O or mutation.
    """

    tokens = _tokens(tail)
    if len(tokens) != 2:
        return _parse_error(f"{command} requires an agent and one persona name")
    alias, persona = tokens
    if not _is_valid_token(alias) or not _is_valid_token(persona):
        return _parse_error(f"{command} arguments must be control-free tokens")
    agent_id = _PERSONA_AGENT_ALIASES.get(alias)
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


def _parse_session(tail: str | None) -> InteractiveAction:
    """Parse exact session subcommands and token counts without I/O.

    Valid list/create/resume/pause forms return typed actions; unknown, unsafe,
    or mis-sized forms return parse-error actions with suggestions when
    available.
    """

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
    if subcommand == "create":
        if len(tokens) != 2:
            return _parse_error("/session create requires exactly one name")
        name = tokens[1]
        if not _is_valid_token(name):
            return _parse_error("/session create name must be a control-free token")
        return InteractiveAction(ActionKind.SESSION_CREATE, argument=name)
    if subcommand == "resume":
        if len(tokens) > 2:
            return _parse_error("/session resume accepts at most one name")
        if len(tokens) == 1:
            return InteractiveAction(ActionKind.SESSION_OPEN)
        name = tokens[1]
        if not _is_valid_token(name):
            return _parse_error("/session resume name must be a control-free token")
        return InteractiveAction(ActionKind.SESSION_OPEN, argument=name)

    if len(tokens) != 1:
        return _parse_error("/session pause does not accept arguments")
    return InteractiveAction(ActionKind.SESSION_PAUSE)


def _parse_conversation(tail: str | None) -> InteractiveAction:
    """Parse the exact conversation grammar without catalog or filesystem I/O."""

    remainder = _remainder_after_ascii_delimiters(tail)
    if remainder is None:
        return _parse_error("/conversation requires a subcommand")

    subcommand, subcommand_tail = _split_head(remainder)
    if subcommand not in _CONVERSATION_COMMANDS:
        return _parse_error(
            f"unknown /conversation subcommand: {subcommand}",
            _suggest(subcommand, _CONVERSATION_COMMANDS),
        )

    if subcommand == "new":
        subject = _optional_remainder(subcommand_tail)
        return InteractiveAction(ActionKind.CONVERSATION_NEW, argument=subject)

    if subcommand == "list":
        query = _remainder_after_ascii_delimiters(subcommand_tail)
        if query is None:
            return InteractiveAction(ActionKind.CONVERSATION_LIST)
        first_token, query_tail = _split_head(query)
        if first_token == _CONVERSATION_ALL_FLAG:
            all_query = _remainder_after_ascii_delimiters(query_tail)
            if all_query is not None and _CONVERSATION_ALL_FLAG in _tokens(all_query):
                return _parse_error(
                    "/conversation list --all must appear once and first"
                )
            return InteractiveAction(
                ActionKind.CONVERSATION_LIST_ALL,
                argument=all_query,
            )
        if first_token.startswith("--"):
            return _parse_error(f"unknown /conversation list flag: {first_token}")
        if _CONVERSATION_ALL_FLAG in _tokens(query):
            return _parse_error("/conversation list --all must appear once and first")
        return InteractiveAction(ActionKind.CONVERSATION_LIST, argument=query)

    if subcommand == "resume":
        tokens = _tokens(subcommand_tail)
        if len(tokens) > 1:
            return _parse_error("/conversation resume accepts at most one ID")
        if not tokens:
            return InteractiveAction(ActionKind.CONVERSATION_USE)
        conversation_id = tokens[0]
        if not _is_valid_token(conversation_id):
            return _parse_error("/conversation resume ID must be a control-free token")
        return InteractiveAction(
            ActionKind.CONVERSATION_USE,
            argument=conversation_id,
        )

    if subcommand == "subject":
        subject = _required_remainder(subcommand_tail)
        if subject is None:
            return _parse_error("/conversation subject requires a subject")
        return InteractiveAction(
            ActionKind.CONVERSATION_SUBJECT,
            argument=subject,
        )

    tokens = _tokens(subcommand_tail)
    if len(tokens) > 1:
        return _parse_error("/conversation archive accepts at most one ID")
    if not tokens:
        return InteractiveAction(ActionKind.CONVERSATION_ARCHIVE)
    conversation_id = tokens[0]
    if not _is_valid_token(conversation_id):
        return _parse_error("/conversation archive ID must be a control-free token")
    return InteractiveAction(
        ActionKind.CONVERSATION_ARCHIVE,
        argument=conversation_id,
    )


def _parse_inspect(tail: str | None) -> InteractiveAction:
    """Parse an optional control-free debate ID into an inspect action.

    Extra or unsafe tokens return a typed parse-error action without I/O.
    """

    tokens = _tokens(tail)
    if not tokens:
        return InteractiveAction(ActionKind.INSPECT)
    if len(tokens) != 1:
        return _parse_error("/inspect accepts at most one debate identifier")
    argument = tokens[0]
    if not _is_valid_token(argument):
        return _parse_error("/inspect debate must be a control-free token")
    return InteractiveAction(ActionKind.INSPECT, argument=argument)


def _parse_search(tail: str | None) -> InteractiveAction:
    """Return one search action from required opaque text without I/O."""

    query = _required_remainder(tail)
    if query is None:
        return _parse_error("/search requires a query")
    return InteractiveAction(ActionKind.SEARCH, text=query)


def _parse_transcript(tail: str | None) -> InteractiveAction:
    """Return one transcript action from exact tokens without external state."""

    tokens = _tokens(tail)
    if not tokens:
        return _parse_error("/transcript requires a conversation ID")

    conversation_id = tokens[0]
    if conversation_id.startswith("--"):
        return _parse_error("/transcript requires an ID before any flag")
    if not _is_valid_token(conversation_id):
        return _parse_error("/transcript ID must be a control-free token")

    remaining = tokens[1:]
    count: int | None = None
    start_sequence: int | None = None
    if remaining and remaining[0] != _TRANSCRIPT_FROM_FLAG:
        if remaining[0].startswith("--"):
            return _parse_error(f"unknown /transcript flag: {remaining[0]}")
        count = _positive_ascii_integer(remaining[0])
        if count is None:
            return _parse_error("/transcript count must be positive ASCII digits")
        remaining = remaining[1:]

    if remaining:
        if remaining[0] != _TRANSCRIPT_FROM_FLAG:
            if remaining[0].startswith("--"):
                return _parse_error(f"unknown /transcript flag: {remaining[0]}")
            return _parse_error(
                "/transcript accepts one ID, optional count, and optional --from"
            )
        if len(remaining) != 2:
            return _parse_error(
                "/transcript --from must appear once, last, with one sequence"
            )
        start_sequence = _positive_ascii_integer(remaining[1])
        if start_sequence is None:
            return _parse_error(
                "/transcript start sequence must be positive ASCII digits"
            )

    return InteractiveAction(
        ActionKind.TRANSCRIPT,
        argument=conversation_id,
        count=count,
        start_sequence=start_sequence,
    )


def _parse_history(tail: str | None) -> InteractiveAction:
    """Parse an optional positive ASCII history count without I/O.

    Missing count selects the default history action; malformed, non-positive,
    or unrepresentable counts return typed parse-error actions.
    """

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


def _positive_ascii_integer(value: str) -> int | None:
    """Return one positive ASCII integer or ``None`` without side effects."""

    if not value or any(character < "0" or character > "9" for character in value):
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _parse_help(tail: str | None, command: str) -> InteractiveAction:
    """Parse optional canonical or aliased help command metadata.

    Bare help returns the overview action; invalid cardinality or unknown names
    return typed parse-error actions with a unique close suggestion when found.
    """

    tokens = _tokens(tail)
    if not tokens:
        return InteractiveAction(ActionKind.HELP)
    if len(tokens) != 1:
        return _parse_error(f"/{command} accepts at most one command")
    requested = tokens[0]
    name = requested[1:] if requested.startswith("/") else requested
    if name in _REMOVED_EXECUTION_COMMANDS:
        return _parse_error(_DIRECT_EXECUTION_UNAVAILABLE)
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
    """Return the requested action only when no argument token is present."""

    if _tokens(tail):
        return _parse_error(f"{command} does not accept arguments")
    return InteractiveAction(kind)


def _split_head(value: str) -> tuple[str, str | None]:
    """Split at the first ASCII space or tab, retaining that delimiter in tail."""

    for index, character in enumerate(value):
        if character in " \t":
            return value[:index], value[index:]
    return value, None


def _tokens(tail: str | None) -> tuple[str, ...]:
    """Return ASCII-space/tab-delimited tokens after trimming those delimiters."""

    if tail is None:
        return ()
    value = tail.strip(" \t")
    if not value:
        return ()
    return tuple(re.split(r"[ \t]+", value))


def _required_remainder(tail: str | None) -> str | None:
    """Drop one delimiter and return nonblank remaining text, else ``None``.

    Later characters, including extra leading or trailing whitespace, are
    preserved and no external state is read.
    """

    if tail is None:
        return None
    value = tail[1:]
    return None if _is_ascii_blank(value) else value


def _optional_remainder(tail: str | None) -> str | None:
    """Drop one delimiter and return optional nonblank text, else ``None``.

    Later characters, including extra leading or trailing whitespace, are
    preserved and no external state is read.
    """

    if tail is None:
        return None
    value = tail[1:]
    return None if _is_ascii_blank(value) else value


def _remainder_after_ascii_delimiters(tail: str | None) -> str | None:
    """Remove one grammar delimiter run while preserving remaining text exactly.

    Only leading ASCII spaces and tabs are syntax. The returned subcommand or
    list remainder retains all later characters, including trailing whitespace;
    no process state or external resource is read or mutated.
    """

    if tail is None:
        return None
    value = tail.lstrip(" \t")
    return value or None


def _is_ascii_blank(value: str) -> bool:
    """Report whether text is empty or contains only ASCII spaces and tabs."""

    return not value or all(character in " \t" for character in value)


def _is_valid_token(value: str) -> bool:
    """Report whether a token is nonempty and free of whitespace and controls."""

    return bool(value) and not any(
        character.isspace() or unicodedata.category(character) == "Cc"
        for character in value
    )


def _suggest_agent(value: str) -> str | None:
    """Return a uniquely close canonical persona agent, or ``None``."""

    candidates = {alias: agent_id for alias, agent_id in _PERSONA_AGENT_ALIASES.items()}
    suggestion = _suggest(value, candidates)
    return suggestion


def _suggest(value: str, candidates: dict[str, str]) -> str | None:
    """Return the mapped unique candidate within edit distance two, if any.

    Candidate mappings are read without mutation; ties, distant inputs, and an
    empty mapping return ``None``.
    """

    distances = {candidate: _levenshtein(value, candidate) for candidate in candidates}
    if not distances:
        return None
    minimum = min(distances.values())
    closest = [
        candidate for candidate, distance in distances.items() if distance == minimum
    ]
    if minimum > 2 or len(closest) != 1:
        return None
    return candidates[closest[0]]


def _levenshtein(left: str, right: str) -> int:
    """Return the insertion, deletion, and substitution edit distance."""

    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_character != right_character),
                )
            )
        previous = current
    return previous[-1]


def _parse_error(
    error: str,
    suggestion: str | None = None,
) -> InteractiveAction:
    """Return a typed parse-error action with an optional safe suggestion."""

    return InteractiveAction(
        ActionKind.PARSE_ERROR,
        error=error,
        suggestion=suggestion,
    )


__all__ = [
    "ActionKind",
    "AgentRouteSpec",
    "AGENT_ROUTES",
    "COMMANDS",
    "CommandSpec",
    "InteractiveAction",
    "parse_interactive_line",
]
