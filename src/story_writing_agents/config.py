"""Cascading global, per-agent, and per-session configuration."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .agents import AGENT_SPECS, ROSTER
from .errors import ConfigurationError
from .workspace import Workspace


_LANGUAGE = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,15}")
_CODEX_REASONING_EFFORTS: tuple[str, ...] = (
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
)
KNOWN_AGENT_BACKENDS: tuple[str, ...] = ("codex", "gemini")


@dataclass(frozen=True, slots=True)
class SystemDefaults:
    language: str
    persona: str
    max_debate_rounds: int
    codex_model: str | None = None
    codex_reasoning_effort: str | None = None


@dataclass(frozen=True, slots=True)
class AgentConfig:
    backend: str | None = None
    language: str | None = None
    persona: str | None = None
    codex_model: str | None = None
    codex_reasoning_effort: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedAgentConfig:
    agent_id: str
    backend: str
    language: str
    persona: str


@dataclass(frozen=True, slots=True)
class ConfigurationSnapshot:
    """Retain one immutable, roster-complete persisted configuration pair."""

    system_defaults: SystemDefaults
    agent_configs: Mapping[str, AgentConfig]

    def __post_init__(self) -> None:
        if type(self.system_defaults) is not SystemDefaults:
            raise TypeError("system_defaults must be a SystemDefaults")
        if not isinstance(self.agent_configs, Mapping):
            raise TypeError("agent_configs must be a mapping")
        if set(self.agent_configs) != set(ROSTER):
            raise ValueError("agent_configs must contain the complete roster")
        if any(
            type(self.agent_configs[agent_id]) is not AgentConfig
            for agent_id in ROSTER
        ):
            raise TypeError("agent_configs values must be AgentConfig values")
        object.__setattr__(
            self,
            "agent_configs",
            MappingProxyType(
                {
                    agent_id: self.agent_configs[agent_id]
                    for agent_id in ROSTER
                }
            ),
        )


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

    @staticmethod
    def validate_backend(value: Any, *, allow_none: bool = False) -> str | None:
        if value is None and allow_none:
            return None
        if not isinstance(value, str) or value not in KNOWN_AGENT_BACKENDS:
            expected = ", ".join(KNOWN_AGENT_BACKENDS)
            raise ConfigurationError(f"backend must be one of: {expected}")
        return value

    @staticmethod
    def validate_codex_model(value: Any) -> str | None:
        if value is None:
            return None
        if (
            not isinstance(value, str)
            or not value
            or value.startswith("-")
            or any(
                unicodedata.category(character) in {"Cc", "Cf", "Cs"}
                for character in value
            )
        ):
            raise ConfigurationError(
                "codex_model must be safe non-empty text or null"
            )
        return value

    @staticmethod
    def validate_codex_reasoning_effort(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or value not in _CODEX_REASONING_EFFORTS:
            expected = ", ".join(_CODEX_REASONING_EFFORTS)
            raise ConfigurationError(
                "codex_reasoning_effort must be null or one of: "
                f"{expected}"
            )
        return value

    def load_system_defaults(self) -> SystemDefaults:
        return self._validate_system_defaults(
            self.workspace.read_json("config/system_defaults.json")
        )

    def _validate_system_defaults(self, value: Any) -> SystemDefaults:
        """Validate one complete system-default document without I/O."""

        if not isinstance(value, dict):
            raise ConfigurationError("system_defaults.json must contain an object")
        unknown = set(value).difference(
            {
                "language",
                "persona",
                "max_debate_rounds",
                "codex_model",
                "codex_reasoning_effort",
            }
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
        codex_model = self.validate_codex_model(value.get("codex_model"))
        codex_reasoning_effort = self.validate_codex_reasoning_effort(
            value.get("codex_reasoning_effort")
        )
        return SystemDefaults(
            language=language,
            persona=persona,
            max_debate_rounds=rounds,
            codex_model=codex_model,
            codex_reasoning_effort=codex_reasoning_effort,
        )

    def load_agent_configs(self) -> dict[str, AgentConfig]:
        return dict(
            self._validate_agent_configs(
                self.workspace.read_json("config/agent_configs.json")
            )
        )

    def _validate_agent_configs(
        self,
        value: Any,
    ) -> Mapping[str, AgentConfig]:
        """Validate one roster-complete agent document without I/O."""

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
            unknown_fields = set(raw).difference(
                {
                    "backend",
                    "language",
                    "persona",
                    "codex_model",
                    "codex_reasoning_effort",
                }
            )
            if unknown_fields:
                raise ConfigurationError(
                    f"unknown {agent_id} config: {sorted(unknown_fields)[0]}"
                )
            backend = self.validate_backend(
                raw.get("backend"), allow_none=True
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
            codex_model = self.validate_codex_model(raw.get("codex_model"))
            codex_reasoning_effort = self.validate_codex_reasoning_effort(
                raw.get("codex_reasoning_effort")
            )
            configs[agent_id] = AgentConfig(
                backend=backend,
                language=language,
                persona=persona,
                codex_model=codex_model,
                codex_reasoning_effort=codex_reasoning_effort,
            )
        return MappingProxyType(configs)

    def validate_configuration(
        self,
        system_value: Any,
        agent_value: Any,
    ) -> ConfigurationSnapshot:
        """Validate both complete persisted documents before publication."""

        return ConfigurationSnapshot(
            self._validate_system_defaults(system_value),
            self._validate_agent_configs(agent_value),
        )

    def load_configuration_snapshot(self) -> ConfigurationSnapshot:
        """Read and validate one immutable persisted configuration pair."""

        return self.validate_configuration(
            self.workspace.read_json("config/system_defaults.json"),
            self.workspace.read_json("config/agent_configs.json"),
        )

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
        backend_overrides: Mapping[str, str] | None = None,
    ) -> ResolvedAgentConfig:
        return self.resolve_from_snapshot(
            self.load_configuration_snapshot(),
            agent_id,
            runtime_overrides,
            backend_overrides,
        )

    def resolve_from_snapshot(
        self,
        snapshot: ConfigurationSnapshot,
        agent_id: str,
        runtime_overrides: Mapping[str, Any] | None = None,
        backend_overrides: Mapping[str, str] | None = None,
    ) -> ResolvedAgentConfig:
        """Resolve live overrides over one immutable persisted snapshot."""

        if type(snapshot) is not ConfigurationSnapshot:
            raise TypeError("snapshot must be a ConfigurationSnapshot")
        if agent_id not in AGENT_SPECS:
            raise ConfigurationError(f"unknown agent: {agent_id}")
        defaults = snapshot.system_defaults
        agent = snapshot.agent_configs[agent_id]
        runtime = self.validate_runtime_overrides(runtime_overrides or {})
        runtime_agent = runtime["agents"].get(agent_id, {})
        backend_override = (backend_overrides or {}).get(agent_id)
        backend = self.validate_backend(
            backend_override or agent.backend or "codex"
        )

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
            backend=backend,
            language=language,
            persona=persona,
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
