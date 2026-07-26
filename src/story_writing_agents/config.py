"""Cascading global, per-agent, and per-session configuration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from .agents import AGENT_SPECS, ROSTER
from .errors import ConfigurationError
from .workspace import Workspace


_LANGUAGE = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,15}")


@dataclass(frozen=True, slots=True)
class SystemDefaults:
    language: str
    persona: str
    max_debate_rounds: int


@dataclass(frozen=True, slots=True)
class AgentConfig:
    language: str | None = None
    persona: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedAgentConfig:
    agent_id: str
    language: str
    persona: str
    domain_rules: str


class ConfigManager:
    """Loads configuration on demand so filesystem changes are immediately visible."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    @staticmethod
    def validate_language(value: Any, *, allow_none: bool = False) -> str | None:
        if value is None and allow_none:
            return None
        if not isinstance(value, str) or _LANGUAGE.fullmatch(value) is None:
            raise ConfigurationError(
                "language must match [A-Za-z][A-Za-z0-9-]{0,15}"
            )
        return value

    def load_system_defaults(self) -> SystemDefaults:
        value = self.workspace.read_json("config/system_defaults.json")
        if not isinstance(value, dict):
            raise ConfigurationError("system_defaults.json must contain an object")
        unknown = set(value).difference(
            {"language", "persona", "max_debate_rounds"}
        )
        if unknown:
            raise ConfigurationError(
                f"unknown system default: {sorted(unknown)[0]}"
            )
        language = self.validate_language(value.get("language"))
        persona = self._validate_persona(value.get("persona"))
        rounds = value.get("max_debate_rounds", 1)
        if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 0:
            raise ConfigurationError("max_debate_rounds must be a non-negative integer")
        return SystemDefaults(
            language=language,
            persona=persona,
            max_debate_rounds=rounds,
        )

    def load_agent_configs(self) -> dict[str, AgentConfig]:
        value = self.workspace.read_json("config/agent_configs.json")
        if not isinstance(value, dict):
            raise ConfigurationError("agent_configs.json must contain an object")
        unknown = set(value).difference(ROSTER)
        if unknown:
            raise ConfigurationError(f"unknown agent config: {sorted(unknown)[0]}")

        configs: dict[str, AgentConfig] = {}
        for agent_id in ROSTER:
            raw = value.get(agent_id, {})
            if not isinstance(raw, dict):
                raise ConfigurationError(f"{agent_id} config must contain an object")
            unknown_fields = set(raw).difference({"language", "persona"})
            if unknown_fields:
                raise ConfigurationError(
                    f"unknown {agent_id} config: {sorted(unknown_fields)[0]}"
                )
            language = self.validate_language(
                raw.get("language"), allow_none=True
            )
            persona_value = raw.get("persona")
            persona = (
                None
                if persona_value is None
                else self._validate_persona(persona_value)
            )
            configs[agent_id] = AgentConfig(language=language, persona=persona)
        return configs

    def validate_runtime_overrides(
        self, value: Mapping[str, Any] | Any
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ConfigurationError("runtime overrides must contain an object")
        unknown_top = set(value).difference({"language", "persona", "agents"})
        if unknown_top:
            raise ConfigurationError(
                f"unknown runtime override: {sorted(unknown_top)[0]}"
            )
        language = self.validate_language(value.get("language"), allow_none=True)
        persona_value = value.get("persona")
        persona = (
            None
            if persona_value is None
            else self._validate_persona(persona_value)
        )
        agents_value = value.get("agents", {})
        if not isinstance(agents_value, Mapping):
            raise ConfigurationError("runtime agent overrides must contain an object")
        unknown_agents = set(agents_value).difference(ROSTER)
        if unknown_agents:
            raise ConfigurationError(
                f"unknown runtime override agent: {sorted(unknown_agents)[0]}"
            )

        agents: dict[str, dict[str, str | None]] = {}
        for agent_id, raw in agents_value.items():
            if not isinstance(raw, Mapping):
                raise ConfigurationError(
                    f"{agent_id} runtime override must contain an object"
                )
            unknown_fields = set(raw).difference({"language", "persona"})
            if unknown_fields:
                raise ConfigurationError(
                    f"unknown {agent_id} override: {sorted(unknown_fields)[0]}"
                )
            agent_language = self.validate_language(
                raw.get("language"), allow_none=True
            )
            agent_persona_value = raw.get("persona")
            agent_persona = (
                None
                if agent_persona_value is None
                else self._validate_persona(agent_persona_value)
            )
            agents[agent_id] = {
                "language": agent_language,
                "persona": agent_persona,
            }
        return {"language": language, "persona": persona, "agents": agents}

    def resolve(
        self,
        agent_id: str,
        runtime_overrides: Mapping[str, Any] | None = None,
    ) -> ResolvedAgentConfig:
        if agent_id not in AGENT_SPECS:
            raise ConfigurationError(f"unknown agent: {agent_id}")
        defaults = self.load_system_defaults()
        agent = self.load_agent_configs()[agent_id]
        runtime = self.validate_runtime_overrides(runtime_overrides or {})
        runtime_agent = runtime["agents"].get(agent_id, {})

        language = (
            runtime_agent.get("language")
            or runtime["language"]
            or agent.language
            or defaults.language
        )
        persona = (
            runtime_agent.get("persona")
            or runtime["persona"]
            or agent.persona
            or defaults.persona
        )
        return ResolvedAgentConfig(
            agent_id=agent_id,
            language=language,
            persona=persona,
            domain_rules=AGENT_SPECS[agent_id].domain_rules,
        )

    def _validate_persona(self, value: Any) -> str:
        if not isinstance(value, str):
            raise ConfigurationError("persona must be a template name")
        try:
            name = self.workspace.validate_template_name(value)
        except Exception as exc:
            raise ConfigurationError("persona template name is unsafe") from exc
        if not self.workspace.persona_exists(name):
            raise ConfigurationError(f"persona template does not exist: {name}")
        return name
