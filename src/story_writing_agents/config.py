"""Cascading global, per-agent, and per-session configuration."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping

from .agents import AGENT_SPECS, ROSTER
from .errors import ConfigurationError, WorkspaceFormatError
from .workspace import Workspace


_LANGUAGE = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,15}")
_CODEX_REASONING_EFFORT = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_ANTIGRAVITY_EFFORTS: tuple[str, ...] = ("low", "medium", "high")
KNOWN_AGENT_BACKENDS: tuple[str, ...] = ("codex", "antigravity")
KNOWN_CONVERSATION_STORAGE_BACKENDS: tuple[str, ...] = ("filesystem", "sqlite")
_SYSTEM_DEFAULTS_PATH = "config/system_defaults.json"
_AGENT_CONFIGS_PATH = "config/agent_configs.json"


@dataclass(frozen=True, slots=True)
class SystemDefaults:
    language: str
    persona: str
    max_debate_rounds: int
    codex_model: str | None = None
    codex_reasoning_effort: str | None = None
    antigravity_model: str | None = None
    antigravity_effort: str | None = None
    default_backend: str | None = None
    conversation_storage_backend: str | None = None
    show_message_details: bool = False


@dataclass(frozen=True, slots=True)
class AgentConfig:
    backend: str | None = None
    language: str | None = None
    persona: str | None = None
    codex_model: str | None = None
    codex_reasoning_effort: str | None = None
    antigravity_model: str | None = None
    antigravity_effort: str | None = None


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


class ConfigurationCommitFailure(StrEnum):
    """Fixed failure categories for one complete configuration CAS."""

    VALIDATION = "validation"
    CONFLICT = "conflict"
    WRITE = "write"
    ROLLBACK = "rollback"


class ConfigurationCommitError(Exception):
    """Carry one sanitized complete-configuration commit failure."""

    def __init__(self, failure: ConfigurationCommitFailure) -> None:
        if type(failure) is not ConfigurationCommitFailure:
            raise TypeError("failure must be a ConfigurationCommitFailure")
        self.failure = failure
        super().__init__(f"configuration commit failed: {failure.value}")


@dataclass(frozen=True, slots=True)
class ConfigurationBaseline:
    """Retain exact persisted bytes and their validated complete value."""

    system_defaults_bytes: bytes
    agent_configs_bytes: bytes
    configuration: ConfigurationSnapshot

    def __post_init__(self) -> None:
        if type(self.system_defaults_bytes) is not bytes:
            raise TypeError("system_defaults_bytes must be bytes")
        if type(self.agent_configs_bytes) is not bytes:
            raise TypeError("agent_configs_bytes must be bytes")
        if type(self.configuration) is not ConfigurationSnapshot:
            raise TypeError("configuration must be a ConfigurationSnapshot")


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
        if value == "gemini":
            raise ConfigurationError(
                "backend 'gemini' is no longer supported; use 'antigravity'"
            )
        if not isinstance(value, str) or value not in KNOWN_AGENT_BACKENDS:
            expected = ", ".join(KNOWN_AGENT_BACKENDS)
            raise ConfigurationError(f"backend must be one of: {expected}")
        return value

    @staticmethod
    def validate_conversation_storage_backend(
        value: Any,
        *,
        allow_none: bool = False,
    ) -> str | None:
        """Validate storage independently from agent provider routing."""

        if value is None and allow_none:
            return None
        if (
            not isinstance(value, str)
            or value not in KNOWN_CONVERSATION_STORAGE_BACKENDS
        ):
            expected = ", ".join(KNOWN_CONVERSATION_STORAGE_BACKENDS)
            raise ConfigurationError(
                f"conversation_storage_backend must be one of: {expected}"
            )
        return value

    @staticmethod
    def validate_show_message_details(value: Any) -> bool:
        """Validate one strict compact/detailed presentation preference."""

        if type(value) is not bool:
            raise ConfigurationError(
                "show_message_details must be a boolean"
            )
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
        if (
            not isinstance(value, str)
            or _CODEX_REASONING_EFFORT.fullmatch(value) is None
        ):
            raise ConfigurationError(
                "codex_reasoning_effort must be null or match "
                "[a-z][a-z0-9_-]{0,31}"
            )
        return value

    @staticmethod
    def validate_antigravity_model(value: Any) -> str | None:
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
                "antigravity_model must be safe non-empty text or null"
            )
        return value

    @staticmethod
    def validate_antigravity_effort(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or value not in _ANTIGRAVITY_EFFORTS:
            expected = ", ".join(_ANTIGRAVITY_EFFORTS)
            raise ConfigurationError(
                "antigravity_effort must be null or one of: "
                f"{expected}"
            )
        return value

    def load_system_defaults(self) -> SystemDefaults:
        return self._validate_system_defaults(
            self.workspace.read_json(_SYSTEM_DEFAULTS_PATH)
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
                "antigravity_model",
                "antigravity_effort",
                "default_backend",
                "conversation_storage_backend",
                "show_message_details",
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
        antigravity_model = self.validate_antigravity_model(
            value.get("antigravity_model")
        )
        antigravity_effort = self.validate_antigravity_effort(
            value.get("antigravity_effort")
        )
        default_backend = self.validate_backend(
            value.get("default_backend"), allow_none=True
        )
        conversation_storage_backend = self.validate_conversation_storage_backend(
            value.get("conversation_storage_backend"),
            allow_none=True,
        )
        show_message_details = self.validate_show_message_details(
            value.get("show_message_details", False)
        )
        return SystemDefaults(
            language=language,
            persona=persona,
            max_debate_rounds=rounds,
            codex_model=codex_model,
            codex_reasoning_effort=codex_reasoning_effort,
            antigravity_model=antigravity_model,
            antigravity_effort=antigravity_effort,
            default_backend=default_backend,
            conversation_storage_backend=conversation_storage_backend,
            show_message_details=show_message_details,
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
                    "antigravity_model",
                    "antigravity_effort",
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
            antigravity_model = self.validate_antigravity_model(
                raw.get("antigravity_model")
            )
            antigravity_effort = self.validate_antigravity_effort(
                raw.get("antigravity_effort")
            )
            configs[agent_id] = AgentConfig(
                backend=backend,
                language=language,
                persona=persona,
                codex_model=codex_model,
                codex_reasoning_effort=codex_reasoning_effort,
                antigravity_model=antigravity_model,
                antigravity_effort=antigravity_effort,
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

        return self.load_configuration_baseline().configuration

    def load_configuration_baseline(self) -> ConfigurationBaseline:
        """Read exact bytes once and validate their complete document pair."""

        system_bytes = self._read_exact_bytes(_SYSTEM_DEFAULTS_PATH)
        agent_bytes = self._read_exact_bytes(_AGENT_CONFIGS_PATH)
        configuration = self.validate_configuration(
            self._decode_json(system_bytes, _SYSTEM_DEFAULTS_PATH),
            self._decode_json(agent_bytes, _AGENT_CONFIGS_PATH),
        )
        return ConfigurationBaseline(
            system_defaults_bytes=system_bytes,
            agent_configs_bytes=agent_bytes,
            configuration=configuration,
        )

    def commit_configuration(
        self,
        baseline: ConfigurationBaseline,
        configuration: ConfigurationSnapshot,
    ) -> bool:
        """Compare exact bytes and publish both canonical complete documents.

        ``True`` means at least one canonical document was written. A conflict
        or failed write preserves the caller's baseline whenever rollback can
        be completed; every failure is content-free.
        """

        if type(baseline) is not ConfigurationBaseline:
            raise TypeError("baseline must be a ConfigurationBaseline")
        if type(configuration) is not ConfigurationSnapshot:
            raise TypeError("configuration must be a ConfigurationSnapshot")
        system_value = self.system_defaults_value(configuration.system_defaults)
        agent_value = self.agent_configs_value(configuration)
        try:
            self.validate_configuration(system_value, agent_value)
        except ConfigurationError as exc:
            raise ConfigurationCommitError(
                ConfigurationCommitFailure.VALIDATION
            ) from exc

        try:
            system_before = self._read_exact_bytes(_SYSTEM_DEFAULTS_PATH)
            agent_before = self._read_exact_bytes(_AGENT_CONFIGS_PATH)
        except Exception as exc:
            raise ConfigurationCommitError(
                ConfigurationCommitFailure.CONFLICT
            ) from exc
        if (
            system_before != baseline.system_defaults_bytes
            or agent_before != baseline.agent_configs_bytes
        ):
            raise ConfigurationCommitError(
                ConfigurationCommitFailure.CONFLICT
            )

        system_bytes = self._canonical_json(system_value).encode("utf-8")
        agent_bytes = self._canonical_json(agent_value).encode("utf-8")
        writes = tuple(
            (relative, current, replacement)
            for relative, current, replacement in (
                (_SYSTEM_DEFAULTS_PATH, system_before, system_bytes),
                (_AGENT_CONFIGS_PATH, agent_before, agent_bytes),
            )
            if current != replacement
        )
        if not writes:
            return False

        attempted: list[tuple[str, bytes]] = []
        try:
            for relative, current, replacement in writes:
                attempted.append((relative, current))
                self.workspace.write_text(
                    relative,
                    replacement.decode("utf-8", errors="strict"),
                )
        except Exception as write_failure:
            rollback_failure: Exception | None = None
            for relative, original in reversed(attempted):
                try:
                    self.workspace.write_text(
                        relative,
                        original.decode("utf-8", errors="strict"),
                    )
                except Exception as failure:
                    if rollback_failure is None:
                        rollback_failure = failure
            if rollback_failure is not None:
                raise ConfigurationCommitError(
                    ConfigurationCommitFailure.ROLLBACK
                ) from rollback_failure
            raise ConfigurationCommitError(
                ConfigurationCommitFailure.WRITE
            ) from write_failure
        return True

    @staticmethod
    def system_defaults_value(defaults: SystemDefaults) -> dict[str, Any]:
        """Project one system-default value to the canonical ten-key shape."""

        if type(defaults) is not SystemDefaults:
            raise TypeError("defaults must be a SystemDefaults")
        storage_backend = defaults.conversation_storage_backend or "filesystem"
        return {
            "language": defaults.language,
            "persona": defaults.persona,
            "max_debate_rounds": defaults.max_debate_rounds,
            "codex_model": defaults.codex_model,
            "codex_reasoning_effort": defaults.codex_reasoning_effort,
            "antigravity_model": defaults.antigravity_model,
            "antigravity_effort": defaults.antigravity_effort,
            "default_backend": defaults.default_backend,
            "conversation_storage_backend": storage_backend,
            "show_message_details": defaults.show_message_details,
        }

    @staticmethod
    def agent_configs_value(
        snapshot: ConfigurationSnapshot,
    ) -> dict[str, dict[str, Any]]:
        """Project one roster-complete agent candidate to its JSON shape."""

        if type(snapshot) is not ConfigurationSnapshot:
            raise TypeError("snapshot must be a ConfigurationSnapshot")
        return {
            agent_id: {
                "backend": snapshot.agent_configs[agent_id].backend,
                "language": snapshot.agent_configs[agent_id].language,
                "persona": snapshot.agent_configs[agent_id].persona,
                "codex_model": snapshot.agent_configs[agent_id].codex_model,
                "codex_reasoning_effort": (
                    snapshot.agent_configs[agent_id].codex_reasoning_effort
                ),
                "antigravity_model": (
                    snapshot.agent_configs[agent_id].antigravity_model
                ),
                "antigravity_effort": (
                    snapshot.agent_configs[agent_id].antigravity_effort
                ),
            }
            for agent_id in ROSTER
        }

    def _read_exact_bytes(self, relative: str) -> bytes:
        """Read one strict-UTF-8 configuration file without normalizing bytes."""

        try:
            value = self.workspace.path(relative).read_bytes()
        except OSError as exc:
            raise WorkspaceFormatError(f"cannot read file: {relative}") from exc
        try:
            value.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise WorkspaceFormatError(
                f"file is not valid UTF-8: {relative}"
            ) from exc
        return value

    @staticmethod
    def _decode_json(value: bytes, relative: str) -> Any:
        try:
            return json.loads(value.decode("utf-8", errors="strict"))
        except json.JSONDecodeError as exc:
            raise WorkspaceFormatError(
                f"file is not valid JSON: {relative}: {exc.msg}"
            ) from exc

    @staticmethod
    def _canonical_json(value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"

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
        if backend_override is not None:
            backend_value = backend_override
        elif agent.backend is not None:
            backend_value = agent.backend
        elif defaults.default_backend is not None:
            backend_value = defaults.default_backend
        else:
            backend_value = "codex"
        backend = self.validate_backend(backend_value)

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
