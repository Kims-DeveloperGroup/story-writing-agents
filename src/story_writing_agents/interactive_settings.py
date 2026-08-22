"""Immutable interactive-settings state and guarded persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from .agents import ROSTER
from .config import (
    ConfigurationSnapshot,
    ConfigManager,
    SystemDefaults,
)
from .errors import (
    ConfigurationError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from .workspace import Workspace


SETTINGS_LANGUAGES: tuple[str, ...] = (
    "KO",
    "EN",
    "JA",
    "ZH",
    "ES",
    "FR",
    "DE",
    "PT",
    "IT",
    "RU",
)
SETTINGS_REASONING_EFFORTS: tuple[str, ...] = (
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
)
SETTINGS_ANTIGRAVITY_EFFORTS: tuple[str, ...] = (
    "low",
    "medium",
    "high",
)
_SYSTEM_DEFAULTS_PATH = "config/system_defaults.json"
_AGENT_CONFIGS_PATH = "config/agent_configs.json"


class SettingsErrorKind(StrEnum):
    """Fixed settings persistence failure categories."""

    VALIDATION = "validation"
    CONFLICT = "conflict"
    WRITE = "write"
    ROLLBACK = "rollback"


_SETTINGS_ERROR_MESSAGES = {
    SettingsErrorKind.VALIDATION: "settings validation failed",
    SettingsErrorKind.CONFLICT: "settings changed on disk",
    SettingsErrorKind.WRITE: "settings write failed",
    SettingsErrorKind.ROLLBACK: "settings rollback failed",
}


class SettingsError(Exception):
    """Carry one fixed settings failure without raw filesystem diagnostics."""

    def __init__(self, kind: SettingsErrorKind) -> None:
        if not isinstance(kind, SettingsErrorKind):
            raise TypeError("kind must be a SettingsErrorKind")
        self.kind = kind
        super().__init__(_SETTINGS_ERROR_MESSAGES[kind])


@dataclass(frozen=True, slots=True)
class PersonaCatalogEntry:
    """Identify one safe persona template by lexical name and relative path."""

    name: str
    relative_path: str

    def __post_init__(self) -> None:
        for field_name, value in (
            ("name", self.name),
            ("relative_path", self.relative_path),
        ):
            if type(value) is not str:
                raise TypeError(f"{field_name} must be a string")
            if not value or "\r" in value or "\n" in value:
                raise ValueError(f"{field_name} must be nonempty one-line text")


@dataclass(frozen=True, slots=True)
class SettingsBaseline:
    """Retain exact source bytes, persona identities, and validated values."""

    system_defaults_bytes: bytes
    agent_configs_bytes: bytes
    persona_catalog: tuple[PersonaCatalogEntry, ...]
    configuration: ConfigurationSnapshot

    def __post_init__(self) -> None:
        if type(self.system_defaults_bytes) is not bytes:
            raise TypeError("system_defaults_bytes must be bytes")
        if type(self.agent_configs_bytes) is not bytes:
            raise TypeError("agent_configs_bytes must be bytes")
        if type(self.persona_catalog) is not tuple or any(
            type(entry) is not PersonaCatalogEntry
            for entry in self.persona_catalog
        ):
            raise TypeError("persona_catalog must contain PersonaCatalogEntry values")
        if tuple(sorted(entry.name for entry in self.persona_catalog)) != tuple(
            entry.name for entry in self.persona_catalog
        ):
            raise ValueError("persona_catalog must be in lexical name order")
        if len({entry.name for entry in self.persona_catalog}) != len(
            self.persona_catalog
        ):
            raise ValueError("persona_catalog names must be unique")
        if type(self.configuration) is not ConfigurationSnapshot:
            raise TypeError("configuration must be a ConfigurationSnapshot")


@dataclass(frozen=True, slots=True)
class SettingsState:
    """Correlate one immutable source baseline with pending candidate values."""

    baseline: SettingsBaseline
    pending: ConfigurationSnapshot

    def __post_init__(self) -> None:
        if type(self.baseline) is not SettingsBaseline:
            raise TypeError("baseline must be a SettingsBaseline")
        if type(self.pending) is not ConfigurationSnapshot:
            raise TypeError("pending must be a ConfigurationSnapshot")


def load_settings_state(workspace: Workspace) -> SettingsState:
    """Read exact configuration bytes and the safe persona catalog once."""

    _require_workspace(workspace)
    system_bytes = _read_exact_bytes(workspace, _SYSTEM_DEFAULTS_PATH)
    agent_bytes = _read_exact_bytes(workspace, _AGENT_CONFIGS_PATH)
    catalog = _persona_catalog(workspace)
    manager = ConfigManager(workspace)
    configuration = manager.validate_configuration(
        _decode_json(system_bytes, _SYSTEM_DEFAULTS_PATH),
        _decode_json(agent_bytes, _AGENT_CONFIGS_PATH),
    )
    baseline = SettingsBaseline(
        system_bytes,
        agent_bytes,
        catalog,
        configuration,
    )
    return SettingsState(baseline, configuration)


def update_language(state: SettingsState, language: str) -> SettingsState:
    """Return pending state with one supported UI language."""

    _require_state(state)
    if type(language) is not str or language not in SETTINGS_LANGUAGES:
        raise ConfigurationError(
            "settings language must be one of: "
            + ", ".join(SETTINGS_LANGUAGES)
        )
    return _with_system_defaults(
        state,
        replace(state.pending.system_defaults, language=language),
    )


def update_persona(state: SettingsState, persona: str) -> SettingsState:
    """Return pending state with one baseline-catalog persona identity."""

    _require_state(state)
    if type(persona) is not str or persona not in {
        entry.name for entry in state.baseline.persona_catalog
    }:
        raise ConfigurationError("persona template does not exist")
    return _with_system_defaults(
        state,
        replace(state.pending.system_defaults, persona=persona),
    )


def update_max_debate_rounds(
    state: SettingsState,
    max_debate_rounds: int,
) -> SettingsState:
    """Return pending state with one nonnegative debate round limit."""

    _require_state(state)
    if (
        type(max_debate_rounds) is not int
        or max_debate_rounds < 0
    ):
        raise ConfigurationError(
            "max_debate_rounds must be a non-negative integer"
        )
    return _with_system_defaults(
        state,
        replace(
            state.pending.system_defaults,
            max_debate_rounds=max_debate_rounds,
        ),
    )


def update_agent_backend(
    state: SettingsState,
    agent_id: str,
    backend: str,
) -> SettingsState:
    """Return pending state with one validated per-agent provider route."""

    _require_state(state)
    if agent_id not in ROSTER:
        raise ConfigurationError(f"unknown agent: {agent_id}")
    validated = ConfigManager.validate_backend(backend)
    assert validated is not None
    configs = dict(state.pending.agent_configs)
    configs[agent_id] = replace(configs[agent_id], backend=validated)
    return SettingsState(
        state.baseline,
        ConfigurationSnapshot(state.pending.system_defaults, configs),
    )


def update_agent_codex_model(
    state: SettingsState,
    agent_id: str,
    codex_model: str | None,
) -> SettingsState:
    """Return pending state with one validated per-agent Codex model."""

    _require_state(state)
    if agent_id not in ROSTER:
        raise ConfigurationError(f"unknown agent: {agent_id}")
    validated = ConfigManager.validate_codex_model(codex_model)
    configs = dict(state.pending.agent_configs)
    configs[agent_id] = replace(configs[agent_id], codex_model=validated)
    return SettingsState(
        state.baseline,
        ConfigurationSnapshot(state.pending.system_defaults, configs),
    )


def update_agent_codex_reasoning_effort(
    state: SettingsState,
    agent_id: str,
    codex_reasoning_effort: str | None,
) -> SettingsState:
    """Return pending state with one validated per-agent reasoning effort."""

    _require_state(state)
    if agent_id not in ROSTER:
        raise ConfigurationError(f"unknown agent: {agent_id}")
    validated = ConfigManager.validate_codex_reasoning_effort(
        codex_reasoning_effort
    )
    configs = dict(state.pending.agent_configs)
    configs[agent_id] = replace(
        configs[agent_id],
        codex_reasoning_effort=validated,
    )
    return SettingsState(
        state.baseline,
        ConfigurationSnapshot(state.pending.system_defaults, configs),
    )


def update_agent_antigravity_model(
    state: SettingsState,
    agent_id: str,
    antigravity_model: str | None,
) -> SettingsState:
    """Return pending state with one validated per-agent Antigravity model."""

    _require_state(state)
    if agent_id not in ROSTER:
        raise ConfigurationError(f"unknown agent: {agent_id}")
    validated = ConfigManager.validate_antigravity_model(antigravity_model)
    configs = dict(state.pending.agent_configs)
    configs[agent_id] = replace(
        configs[agent_id],
        antigravity_model=validated,
    )
    return SettingsState(
        state.baseline,
        ConfigurationSnapshot(state.pending.system_defaults, configs),
    )


def update_agent_antigravity_effort(
    state: SettingsState,
    agent_id: str,
    antigravity_effort: str | None,
) -> SettingsState:
    """Return pending state with one validated per-agent Antigravity effort."""

    _require_state(state)
    if agent_id not in ROSTER:
        raise ConfigurationError(f"unknown agent: {agent_id}")
    validated = ConfigManager.validate_antigravity_effort(
        antigravity_effort
    )
    configs = dict(state.pending.agent_configs)
    configs[agent_id] = replace(
        configs[agent_id],
        antigravity_effort=validated,
    )
    return SettingsState(
        state.baseline,
        ConfigurationSnapshot(state.pending.system_defaults, configs),
    )


def save_settings_state(workspace: Workspace, state: SettingsState) -> None:
    """Validate, conflict-check, and atomically replace changed documents."""

    _require_workspace(workspace)
    _require_state(state)
    system_value = _system_value(state.pending.system_defaults)
    agent_value = _agent_value(state.pending)
    try:
        ConfigManager(workspace).validate_configuration(
            system_value,
            agent_value,
        )
    except ConfigurationError as exc:
        raise SettingsError(SettingsErrorKind.VALIDATION) from exc

    try:
        system_before = _read_exact_bytes(workspace, _SYSTEM_DEFAULTS_PATH)
        agent_before = _read_exact_bytes(workspace, _AGENT_CONFIGS_PATH)
        catalog_before = _persona_catalog(workspace)
    except Exception as exc:
        raise SettingsError(SettingsErrorKind.CONFLICT) from exc
    if (
        system_before != state.baseline.system_defaults_bytes
        or agent_before != state.baseline.agent_configs_bytes
        or catalog_before != state.baseline.persona_catalog
    ):
        raise SettingsError(SettingsErrorKind.CONFLICT)

    system_changed = (
        state.pending.system_defaults
        != state.baseline.configuration.system_defaults
    )
    agent_changed = (
        state.pending.agent_configs
        != state.baseline.configuration.agent_configs
    )
    if not system_changed and not agent_changed:
        return

    system_text = _canonical_json(system_value)
    agent_text = _canonical_json(agent_value)
    attempted: list[tuple[str, str]] = []
    try:
        if system_changed:
            attempted.append(
                (
                    _SYSTEM_DEFAULTS_PATH,
                    state.baseline.system_defaults_bytes.decode("utf-8"),
                )
            )
            workspace.write_text(_SYSTEM_DEFAULTS_PATH, system_text)
        if agent_changed:
            attempted.append(
                (
                    _AGENT_CONFIGS_PATH,
                    state.baseline.agent_configs_bytes.decode("utf-8"),
                )
            )
            workspace.write_text(_AGENT_CONFIGS_PATH, agent_text)
    except Exception as write_failure:
        rollback_failure: Exception | None = None
        for relative, original_text in reversed(attempted):
            try:
                workspace.write_text(relative, original_text)
            except Exception as failure:
                if rollback_failure is None:
                    rollback_failure = failure
        if rollback_failure is not None:
            raise SettingsError(SettingsErrorKind.ROLLBACK) from rollback_failure
        raise SettingsError(SettingsErrorKind.WRITE) from write_failure


def _with_system_defaults(
    state: SettingsState,
    defaults: SystemDefaults,
) -> SettingsState:
    """Return one state with only its pending system defaults replaced."""

    return SettingsState(
        state.baseline,
        ConfigurationSnapshot(defaults, state.pending.agent_configs),
    )


def _require_workspace(workspace: object) -> Workspace:
    """Return one validated workspace boundary."""

    if not isinstance(workspace, Workspace):
        raise TypeError("workspace must be a Workspace")
    return workspace


def _require_state(state: object) -> SettingsState:
    """Return one exact immutable settings state."""

    if type(state) is not SettingsState:
        raise TypeError("state must be a SettingsState")
    return state


def _read_exact_bytes(workspace: Workspace, relative: str) -> bytes:
    """Read one safe strict-UTF-8 file while preserving its exact bytes."""

    try:
        value = workspace.path(relative).read_bytes()
    except OSError as exc:
        raise WorkspaceFormatError(f"cannot read file: {relative}") from exc
    try:
        value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise WorkspaceFormatError(f"file is not valid UTF-8: {relative}") from exc
    return value


def _decode_json(value: bytes, relative: str) -> Any:
    """Decode one already-read strict-UTF-8 JSON document."""

    try:
        return json.loads(value.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkspaceFormatError(
            f"file is not valid JSON: {relative}: {exc.msg}"
        ) from exc


def _persona_catalog(workspace: Workspace) -> tuple[PersonaCatalogEntry, ...]:
    """Return safe lexical personas with top-level precedence."""

    selected: dict[str, PersonaCatalogEntry] = {}
    directories = (
        "config/persona_templates",
        "config/persona_templates/alternative_templates",
    )
    for relative_directory in directories:
        directory = workspace.path(relative_directory)
        try:
            entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
        except OSError as exc:
            raise WorkspaceFormatError(
                f"cannot read directory: {relative_directory}"
            ) from exc
        for entry in entries:
            if not entry.name.endswith(".md"):
                continue
            name = entry.name[:-3]
            try:
                workspace.validate_template_name(name)
            except WorkspaceSecurityError:
                continue
            relative = f"{relative_directory}/{entry.name}"
            target = workspace.path(relative)
            if target.is_file() and name not in selected:
                selected[name] = PersonaCatalogEntry(name, relative)
    return tuple(selected[name] for name in sorted(selected))


def _system_value(defaults: SystemDefaults) -> dict[str, Any]:
    """Project one system-default candidate to its complete JSON shape."""

    return {
        "language": defaults.language,
        "persona": defaults.persona,
        "max_debate_rounds": defaults.max_debate_rounds,
        "codex_model": defaults.codex_model,
        "codex_reasoning_effort": defaults.codex_reasoning_effort,
        "antigravity_model": defaults.antigravity_model,
        "antigravity_effort": defaults.antigravity_effort,
    }


def _agent_value(snapshot: ConfigurationSnapshot) -> dict[str, dict[str, Any]]:
    """Project one roster-complete agent candidate to its JSON shape."""

    return {
        agent_id: {
            "backend": snapshot.agent_configs[agent_id].backend,
            "language": snapshot.agent_configs[agent_id].language,
            "persona": snapshot.agent_configs[agent_id].persona,
            "codex_model": snapshot.agent_configs[agent_id].codex_model,
            "codex_reasoning_effort": snapshot.agent_configs[agent_id].codex_reasoning_effort,
            "antigravity_model": snapshot.agent_configs[agent_id].antigravity_model,
            "antigravity_effort": snapshot.agent_configs[agent_id].antigravity_effort,
        }
        for agent_id in ROSTER
    }


def _canonical_json(value: Any) -> str:
    """Serialize one validated settings document deterministically."""

    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
