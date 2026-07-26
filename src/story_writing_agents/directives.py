"""Exact, standalone runtime directive parsing and persistence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from .agents import ROSTER
from .config import ConfigManager
from .errors import DirectiveError
from .workspace import Workspace


class DirectiveKind(StrEnum):
    LANGUAGE = "language"
    PERSONA = "persona"
    RESET = "reset"


@dataclass(frozen=True, slots=True)
class RuntimeDirective:
    kind: DirectiveKind
    language: str | None = None
    agent_id: str | None = None
    persona: str | None = None


_LANGUAGE_DIRECTIVE = re.compile(
    r"\[SYS: LANG=(?P<language>[A-Za-z][A-Za-z0-9-]{0,15})\]"
)
_PERSONA_DIRECTIVE = re.compile(
    r"\[OVERRIDE: (?P<agent>[A-Za-z0-9_]+) "
    r"Persona=(?P<persona>[A-Za-z0-9][A-Za-z0-9_.-]{0,63})\]"
)


def parse_directive(text: str) -> RuntimeDirective | None:
    """Parse only a complete directive message.

    No whitespace is stripped. A directive-looking substring embedded in user
    content therefore remains ordinary content.
    """

    if text == "[RESET: ALL]":
        return RuntimeDirective(kind=DirectiveKind.RESET)
    match = _LANGUAGE_DIRECTIVE.fullmatch(text)
    if match is not None:
        return RuntimeDirective(
            kind=DirectiveKind.LANGUAGE,
            language=match.group("language"),
        )
    match = _PERSONA_DIRECTIVE.fullmatch(text)
    if match is not None:
        return RuntimeDirective(
            kind=DirectiveKind.PERSONA,
            agent_id=match.group("agent"),
            persona=match.group("persona"),
        )
    return None


def apply_directive(
    workspace: Workspace,
    session_id: str,
    text: str,
) -> dict[str, object]:
    """Validate and atomically persist a standalone session directive."""

    directive = parse_directive(text)
    if directive is None:
        raise DirectiveError("message is not an exact standalone directive")

    manager = ConfigManager(workspace)
    if directive.kind is DirectiveKind.RESET:
        candidate: dict[str, object] = {
            "language": None,
            "persona": None,
            "agents": {},
        }
    else:
        current = manager.validate_runtime_overrides(
            workspace.load_session_overrides(session_id)
        )
        candidate = {
            "language": current["language"],
            "persona": current["persona"],
            "agents": {
                agent_id: dict(values)
                for agent_id, values in current["agents"].items()
            },
        }
        if directive.kind is DirectiveKind.LANGUAGE:
            candidate["language"] = directive.language
        elif directive.kind is DirectiveKind.PERSONA:
            if directive.agent_id not in ROSTER:
                raise DirectiveError(f"unknown agent: {directive.agent_id}")
            agent_overrides = candidate["agents"]
            assert isinstance(agent_overrides, dict)
            existing = dict(agent_overrides.get(directive.agent_id, {}))
            existing["persona"] = directive.persona
            agent_overrides[directive.agent_id] = existing

    try:
        validated = manager.validate_runtime_overrides(candidate)
    except Exception as exc:
        raise DirectiveError(str(exc)) from exc
    workspace.save_session_overrides(session_id, validated)
    return validated
