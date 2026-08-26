"""Immutable interactive-settings state and guarded persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping

from .agents import ROSTER
from .config import (
    ConfigurationBaseline,
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
_SYSTEM_DEFAULTS_PATH = "config/system_defaults.json"
_AGENT_CONFIGS_PATH = "config/agent_configs.json"
_PROVIDER_ROUTE_GENERATIONS_PATH = (
    "agent_states/orchestrator_state/provider_route_generations.json"
)
_PROVIDER_ROUTE_GENERATIONS_SCHEMA_VERSION = 1


class SettingsErrorKind(StrEnum):
    """Fixed settings persistence failure categories."""

    VALIDATION = "validation"
    CONFLICT = "conflict"
    WRITE = "write"
    ROLLBACK = "rollback"
    RECOVERY_REQUIRED = "recovery_required"


_SETTINGS_ERROR_MESSAGES = {
    SettingsErrorKind.VALIDATION: "settings validation failed",
    SettingsErrorKind.CONFLICT: "settings changed on disk",
    SettingsErrorKind.WRITE: "settings write failed",
    SettingsErrorKind.ROLLBACK: "settings rollback failed",
    SettingsErrorKind.RECOVERY_REQUIRED: "settings activation recovery is required",
}


@dataclass(frozen=True, slots=True)
class ProviderRouteEntry:
    """Durable provider identity and monotonically increasing route generation."""

    provider: str
    generation: int

    def __post_init__(self) -> None:
        ConfigManager.validate_backend(self.provider)
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError("provider route generation must be non-negative")


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
    provider_route_bytes: bytes | None = None
    provider_routes: Mapping[str, ProviderRouteEntry] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if type(self.system_defaults_bytes) is not bytes:
            raise TypeError("system_defaults_bytes must be bytes")
        if type(self.agent_configs_bytes) is not bytes:
            raise TypeError("agent_configs_bytes must be bytes")
        if type(self.persona_catalog) is not tuple or any(
            type(entry) is not PersonaCatalogEntry for entry in self.persona_catalog
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
        if (
            self.provider_route_bytes is not None
            and type(self.provider_route_bytes) is not bytes
        ):
            raise TypeError("provider_route_bytes must be bytes or None")
        if not isinstance(self.provider_routes, Mapping):
            raise TypeError("provider_routes must be a mapping")
        if self.provider_routes and set(self.provider_routes) != set(ROSTER):
            raise ValueError("provider_routes must contain the complete roster")
        if any(
            type(entry) is not ProviderRouteEntry
            for entry in self.provider_routes.values()
        ):
            raise TypeError("provider_routes values must be ProviderRouteEntry values")
        object.__setattr__(
            self,
            "provider_routes",
            MappingProxyType(dict(self.provider_routes)),
        )

    @property
    def configuration_baseline(self) -> ConfigurationBaseline:
        """Return the config-owned exact-byte baseline for final persistence."""

        return ConfigurationBaseline(
            self.system_defaults_bytes,
            self.agent_configs_bytes,
            self.configuration,
        )


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


@dataclass(frozen=True, slots=True)
class PreparedSettingsSave:
    """Validated exact inputs for one conflict-safe three-file transaction."""

    configuration: ConfigurationSnapshot
    system_before: bytes
    agent_before: bytes
    route_before: bytes | None
    system_after: bytes
    agent_after: bytes
    route_after: bytes | None
    provider_routes: Mapping[str, ProviderRouteEntry]
    switched_agents: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.configuration) is not ConfigurationSnapshot:
            raise TypeError("configuration must be a ConfigurationSnapshot")
        for name in (
            "system_before",
            "agent_before",
            "system_after",
            "agent_after",
        ):
            if type(getattr(self, name)) is not bytes:
                raise TypeError(f"{name} must be bytes")
        if self.route_before is not None and type(self.route_before) is not bytes:
            raise TypeError("route_before must be bytes or None")
        if self.route_after is not None and type(self.route_after) is not bytes:
            raise TypeError("route_after must be bytes or None")
        if set(self.provider_routes) != set(ROSTER):
            raise ValueError("provider_routes must contain the complete roster")
        if type(self.switched_agents) is not tuple or any(
            agent_id not in ROSTER for agent_id in self.switched_agents
        ):
            raise ValueError("switched_agents must contain canonical agent IDs")
        object.__setattr__(
            self,
            "provider_routes",
            MappingProxyType(dict(self.provider_routes)),
        )


def load_settings_state(workspace: Workspace) -> SettingsState:
    """Read exact configuration bytes and the safe persona catalog once."""

    _require_workspace(workspace)
    configuration_baseline = ConfigManager(workspace).load_configuration_baseline()
    catalog = _persona_catalog(workspace)
    route_bytes, provider_routes = read_provider_route_generations(
        workspace,
        configuration_baseline.configuration,
    )
    baseline = SettingsBaseline(
        configuration_baseline.system_defaults_bytes,
        configuration_baseline.agent_configs_bytes,
        catalog,
        configuration_baseline.configuration,
        route_bytes,
        provider_routes,
    )
    return SettingsState(baseline, configuration_baseline.configuration)


def update_language(state: SettingsState, language: str) -> SettingsState:
    """Return pending state with one supported UI language."""

    _require_state(state)
    if type(language) is not str or language not in SETTINGS_LANGUAGES:
        raise ConfigurationError(
            "settings language must be one of: " + ", ".join(SETTINGS_LANGUAGES)
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
    if type(max_debate_rounds) is not int or max_debate_rounds < 0:
        raise ConfigurationError("max_debate_rounds must be a non-negative integer")
    return _with_system_defaults(
        state,
        replace(
            state.pending.system_defaults,
            max_debate_rounds=max_debate_rounds,
        ),
    )


def update_conversation_storage_backend(
    state: SettingsState,
    backend: str,
) -> SettingsState:
    """Return pending state with one independently validated storage backend."""

    _require_state(state)
    validated = ConfigManager.validate_conversation_storage_backend(backend)
    assert validated is not None
    return _with_system_defaults(
        state,
        replace(
            state.pending.system_defaults,
            conversation_storage_backend=validated,
        ),
    )


def update_show_message_details(
    state: SettingsState,
    enabled: bool,
) -> SettingsState:
    """Return pending state with one strict message presentation preference."""

    _require_state(state)
    validated = ConfigManager.validate_show_message_details(enabled)
    return _with_system_defaults(
        state,
        replace(
            state.pending.system_defaults,
            show_message_details=validated,
        ),
    )


def update_conversation_rag_provider(
    state: SettingsState,
    provider: str | None,
) -> SettingsState:
    """Return pending state with one validated optional local RAG provider."""

    _require_state(state)
    validated = ConfigManager.validate_conversation_rag_provider(
        provider,
        allow_none=True,
    )
    defaults = state.pending.system_defaults
    return _with_system_defaults(
        state,
        replace(
            defaults,
            conversation_rag_provider=validated,
            conversation_rag_automatic=(
                defaults.conversation_rag_automatic if validated == "local" else False
            ),
        ),
    )


def update_conversation_rag_automatic(
    state: SettingsState,
    automatic: bool,
) -> SettingsState:
    """Return pending state with one valid local-only automatic RAG policy."""

    _require_state(state)
    validated = ConfigManager.validate_conversation_rag_automatic(automatic)
    if validated and state.pending.system_defaults.conversation_rag_provider != "local":
        raise ConfigurationError(
            "conversation_rag_automatic requires the local RAG provider"
        )
    return _with_system_defaults(
        state,
        replace(
            state.pending.system_defaults,
            conversation_rag_automatic=validated,
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


def establish_default_backend(workspace: Workspace, backend: str) -> bool:
    """Persist a valid missing default and report whether a write occurred.

    Authentication remains the caller's responsibility. An existing default
    returns ``False`` without modifying either configuration document.
    """

    _require_workspace(workspace)
    validated = ConfigManager.validate_backend(backend)
    assert validated is not None
    state = load_settings_state(workspace)
    if state.pending.system_defaults.default_backend is not None:
        return False
    pending = _with_system_defaults(
        state,
        replace(
            state.pending.system_defaults,
            default_backend=validated,
        ),
    )
    save_settings_state(workspace, pending)
    return True


def establish_settings_default_backend(
    workspace: Workspace,
    state: SettingsState,
    backend: str,
    *,
    provider_overrides: Mapping[str, str] | None = None,
) -> SettingsState:
    """Rebase a missing provider default in memory for the final settings CAS."""

    _require_workspace(workspace)
    _require_state(state)
    validated = ConfigManager.validate_backend(backend)
    assert validated is not None
    baseline_configuration = state.baseline.configuration
    if baseline_configuration.system_defaults.default_backend is not None:
        return state
    pending_default = state.pending.system_defaults.default_backend
    if pending_default is not None:
        return state
    rebased_pending = ConfigurationSnapshot(
        replace(
            state.pending.system_defaults,
            default_backend=validated,
        ),
        state.pending.agent_configs,
    )
    return SettingsState(state.baseline, rebased_pending)


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
    validated = ConfigManager.validate_codex_reasoning_effort(codex_reasoning_effort)
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
    validated = ConfigManager.validate_antigravity_effort(antigravity_effort)
    configs = dict(state.pending.agent_configs)
    configs[agent_id] = replace(
        configs[agent_id],
        antigravity_effort=validated,
    )
    return SettingsState(
        state.baseline,
        ConfigurationSnapshot(state.pending.system_defaults, configs),
    )


def prepare_settings_save(
    workspace: Workspace,
    state: SettingsState,
    *,
    provider_overrides: Mapping[str, str] | None = None,
) -> PreparedSettingsSave:
    """Validate and capture exact conflict/rollback inputs without mutation."""

    _require_workspace(workspace)
    _require_state(state)
    manager = ConfigManager(workspace)
    system_value = manager.system_defaults_value(state.pending.system_defaults)
    agent_value = manager.agent_configs_value(state.pending)
    try:
        manager.validate_configuration(system_value, agent_value)
    except ConfigurationError as exc:
        raise SettingsError(SettingsErrorKind.VALIDATION) from exc

    try:
        configuration_baseline = manager.load_configuration_baseline()
        catalog_before = _persona_catalog(workspace)
    except Exception as exc:
        raise SettingsError(SettingsErrorKind.CONFLICT) from exc
    system_before = configuration_baseline.system_defaults_bytes
    agent_before = configuration_baseline.agent_configs_bytes
    if (
        system_before != state.baseline.system_defaults_bytes
        or agent_before != state.baseline.agent_configs_bytes
        or catalog_before != state.baseline.persona_catalog
    ):
        raise SettingsError(SettingsErrorKind.CONFLICT)

    route_target = workspace.path(_PROVIDER_ROUTE_GENERATIONS_PATH)
    route_before = (
        _read_exact_bytes(workspace, _PROVIDER_ROUTE_GENERATIONS_PATH)
        if route_target.exists()
        else None
    )
    if route_before != state.baseline.provider_route_bytes:
        raise SettingsError(SettingsErrorKind.CONFLICT)
    overrides = _validated_provider_overrides(provider_overrides)
    baseline_routes = (
        state.baseline.provider_routes
        if state.baseline.provider_routes
        else _initial_provider_routes(
            state.baseline.configuration,
            provider_overrides=overrides,
        )
    )
    next_routes: dict[str, ProviderRouteEntry] = {}
    switched: list[str] = []
    for agent_id in ROSTER:
        before = baseline_routes[agent_id]
        provider = _configured_provider(
            state.pending,
            agent_id,
            provider_overrides=overrides,
        )
        if provider == before.provider:
            next_routes[agent_id] = before
        else:
            switched.append(agent_id)
            next_routes[agent_id] = ProviderRouteEntry(
                provider,
                before.generation + 1,
            )
    return PreparedSettingsSave(
        configuration=state.pending,
        system_before=system_before,
        agent_before=agent_before,
        route_before=route_before,
        system_after=_canonical_json(system_value).encode("utf-8"),
        agent_after=_canonical_json(agent_value).encode("utf-8"),
        route_after=(
            None
            if route_before is None and not switched
            else _provider_routes_bytes(next_routes)
        ),
        provider_routes=next_routes,
        switched_agents=tuple(switched),
    )


def validate_settings_persona_catalog(
    workspace: Workspace,
    baseline: SettingsBaseline,
) -> None:
    """Raise the fixed conflict failure when the safe persona catalog changed."""

    _require_workspace(workspace)
    if type(baseline) is not SettingsBaseline:
        raise TypeError("baseline must be a SettingsBaseline")
    try:
        catalog_before = _persona_catalog(workspace)
    except Exception as exc:
        raise SettingsError(SettingsErrorKind.CONFLICT) from exc
    if catalog_before != baseline.persona_catalog:
        raise SettingsError(SettingsErrorKind.CONFLICT)


def commit_prepared_settings_save(
    workspace: Workspace,
    prepared: PreparedSettingsSave,
) -> None:
    """Publish one prepared configuration and route state with rollback."""

    _require_workspace(workspace)
    if type(prepared) is not PreparedSettingsSave:
        raise TypeError("prepared must be a PreparedSettingsSave")
    current_route = workspace.path(_PROVIDER_ROUTE_GENERATIONS_PATH)
    observed_route = (
        _read_exact_bytes(workspace, _PROVIDER_ROUTE_GENERATIONS_PATH)
        if current_route.exists()
        else None
    )
    system_current = _read_exact_bytes(workspace, _SYSTEM_DEFAULTS_PATH)
    agent_current = _read_exact_bytes(workspace, _AGENT_CONFIGS_PATH)
    configuration_is_before = (
        system_current == prepared.system_before
        and agent_current == prepared.agent_before
    )
    configuration_is_after = (
        system_current == prepared.system_after
        and agent_current == prepared.agent_after
    )
    if (
        not (configuration_is_before or configuration_is_after)
        or observed_route != prepared.route_before
    ):
        raise SettingsError(SettingsErrorKind.CONFLICT)
    writes: list[tuple[str, bytes, bytes | None]] = []
    if configuration_is_before:
        writes.extend(
            (
                (_SYSTEM_DEFAULTS_PATH, prepared.system_after, prepared.system_before),
                (_AGENT_CONFIGS_PATH, prepared.agent_after, prepared.agent_before),
            )
        )
    if prepared.route_after is not None:
        writes.append(
            (
                _PROVIDER_ROUTE_GENERATIONS_PATH,
                prepared.route_after,
                prepared.route_before,
            )
        )
    attempted: list[tuple[str, bytes | None]] = (
        [
            (_SYSTEM_DEFAULTS_PATH, prepared.system_before),
            (_AGENT_CONFIGS_PATH, prepared.agent_before),
        ]
        if configuration_is_after and not configuration_is_before
        else []
    )
    try:
        for relative, after, before in writes:
            if after == before:
                continue
            attempted.append((relative, before))
            workspace.write_text(relative, after.decode("utf-8"))
    except Exception as write_failure:
        rollback_failure: Exception | None = None
        for relative, original in reversed(attempted):
            try:
                if original is None:
                    workspace.path(relative).unlink(missing_ok=True)
                else:
                    workspace.write_text(relative, original.decode("utf-8"))
            except Exception as failure:
                if rollback_failure is None:
                    rollback_failure = failure
        if rollback_failure is not None:
            raise SettingsError(SettingsErrorKind.ROLLBACK) from rollback_failure
        raise SettingsError(SettingsErrorKind.WRITE) from write_failure


def restore_prepared_settings_save(
    workspace: Workspace,
    prepared: PreparedSettingsSave,
) -> None:
    """Restore exact pre-save bytes after a verified post-persist failure."""

    _require_workspace(workspace)
    if type(prepared) is not PreparedSettingsSave:
        raise TypeError("prepared must be a PreparedSettingsSave")
    try:
        route_target = workspace.path(_PROVIDER_ROUTE_GENERATIONS_PATH)
        observed_route = (
            _read_exact_bytes(workspace, _PROVIDER_ROUTE_GENERATIONS_PATH)
            if route_target.exists()
            else None
        )
        expected_route = (
            prepared.route_after
            if prepared.route_after is not None
            else prepared.route_before
        )
        system_current = _read_exact_bytes(workspace, _SYSTEM_DEFAULTS_PATH)
        agent_current = _read_exact_bytes(workspace, _AGENT_CONFIGS_PATH)
        if (
            system_current == prepared.system_before
            and agent_current == prepared.agent_before
            and observed_route == prepared.route_before
        ):
            return
        if (
            system_current != prepared.system_after
            or agent_current != prepared.agent_after
            or observed_route not in {prepared.route_before, expected_route}
        ):
            raise SettingsError(SettingsErrorKind.RECOVERY_REQUIRED)
        workspace.write_text(
            _SYSTEM_DEFAULTS_PATH,
            prepared.system_before.decode("utf-8"),
        )
        workspace.write_text(
            _AGENT_CONFIGS_PATH,
            prepared.agent_before.decode("utf-8"),
        )
        if prepared.route_before is None:
            workspace.path(_PROVIDER_ROUTE_GENERATIONS_PATH).unlink(missing_ok=True)
        else:
            workspace.write_text(
                _PROVIDER_ROUTE_GENERATIONS_PATH,
                prepared.route_before.decode("utf-8"),
            )
        restored_route = (
            _read_exact_bytes(workspace, _PROVIDER_ROUTE_GENERATIONS_PATH)
            if workspace.path(_PROVIDER_ROUTE_GENERATIONS_PATH).exists()
            else None
        )
        if (
            _read_exact_bytes(workspace, _SYSTEM_DEFAULTS_PATH)
            != prepared.system_before
            or _read_exact_bytes(workspace, _AGENT_CONFIGS_PATH)
            != prepared.agent_before
            or restored_route != prepared.route_before
        ):
            raise SettingsError(SettingsErrorKind.RECOVERY_REQUIRED)
    except SettingsError:
        raise
    except Exception as exc:
        raise SettingsError(SettingsErrorKind.RECOVERY_REQUIRED) from exc


def save_settings_state(workspace: Workspace, state: SettingsState) -> None:
    """Compatibility delegate for the prepared settings transaction."""

    commit_prepared_settings_save(workspace, prepare_settings_save(workspace, state))


def reconcile_provider_route_generations(
    workspace: Workspace,
    configuration: ConfigurationSnapshot,
    *,
    provider_overrides: Mapping[str, str] | None = None,
) -> tuple[bytes, Mapping[str, ProviderRouteEntry]]:
    """Load durable routes and safely advance manual provider changes."""

    _require_workspace(workspace)
    if type(configuration) is not ConfigurationSnapshot:
        raise TypeError("configuration must be a ConfigurationSnapshot")
    target = workspace.path(_PROVIDER_ROUTE_GENERATIONS_PATH)
    before = (
        _read_exact_bytes(workspace, _PROVIDER_ROUTE_GENERATIONS_PATH)
        if target.exists()
        else None
    )
    overrides = _validated_provider_overrides(provider_overrides)
    routes = (
        _initial_provider_routes(
            configuration,
            provider_overrides=overrides,
        )
        if before is None
        else _decode_provider_routes(before)
    )
    reconciled: dict[str, ProviderRouteEntry] = {}
    changed = before is None
    for agent_id in ROSTER:
        current = routes[agent_id]
        provider = _configured_provider(
            configuration,
            agent_id,
            provider_overrides=overrides,
        )
        if current.provider == provider:
            reconciled[agent_id] = current
        else:
            changed = True
            reconciled[agent_id] = ProviderRouteEntry(
                provider,
                current.generation + 1,
            )
    expected = _provider_routes_bytes(reconciled)
    if changed or before != expected:
        try:
            workspace.write_text(
                _PROVIDER_ROUTE_GENERATIONS_PATH,
                expected.decode("utf-8"),
            )
        except Exception as exc:
            raise SettingsError(SettingsErrorKind.RECOVERY_REQUIRED) from exc
    return expected, MappingProxyType(reconciled)


def read_provider_route_generations(
    workspace: Workspace,
    configuration: ConfigurationSnapshot,
    *,
    provider_overrides: Mapping[str, str] | None = None,
) -> tuple[bytes | None, Mapping[str, ProviderRouteEntry]]:
    """Read route state without mutation, deriving generation zero when absent."""

    _require_workspace(workspace)
    if type(configuration) is not ConfigurationSnapshot:
        raise TypeError("configuration must be a ConfigurationSnapshot")
    target = workspace.path(_PROVIDER_ROUTE_GENERATIONS_PATH)
    if not target.exists():
        return None, _initial_provider_routes(
            configuration,
            provider_overrides=_validated_provider_overrides(provider_overrides),
        )
    content = _read_exact_bytes(workspace, _PROVIDER_ROUTE_GENERATIONS_PATH)
    return content, _decode_provider_routes(content)


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


def _configured_provider(
    snapshot: ConfigurationSnapshot,
    agent_id: str,
    *,
    provider_overrides: Mapping[str, str] | None = None,
) -> str:
    """Resolve the effective provider route with explicit launch precedence."""

    override = (provider_overrides or {}).get(agent_id)
    agent_provider = snapshot.agent_configs[agent_id].backend
    return (
        override
        or agent_provider
        or snapshot.system_defaults.default_backend
        or "codex"
    )


def _initial_provider_routes(
    snapshot: ConfigurationSnapshot,
    *,
    provider_overrides: Mapping[str, str] | None = None,
) -> Mapping[str, ProviderRouteEntry]:
    """Return generation-zero routes for a workspace without durable state."""

    return MappingProxyType(
        {
            agent_id: ProviderRouteEntry(
                _configured_provider(
                    snapshot,
                    agent_id,
                    provider_overrides=provider_overrides,
                ),
                0,
            )
            for agent_id in ROSTER
        }
    )


def _validated_provider_overrides(
    provider_overrides: Mapping[str, str] | None,
) -> Mapping[str, str]:
    """Copy one optional partial launch-provider map after exact validation."""

    if provider_overrides is None:
        return MappingProxyType({})
    if not isinstance(provider_overrides, Mapping):
        raise TypeError("provider_overrides must be a mapping or None")
    unknown = set(provider_overrides).difference(ROSTER)
    if unknown:
        raise ConfigurationError(
            f"unknown backend override agent: {sorted(unknown)[0]}"
        )
    return MappingProxyType(
        {
            agent_id: ConfigManager.validate_backend(provider)
            for agent_id, provider in provider_overrides.items()
        }
    )


def _provider_routes_bytes(
    routes: Mapping[str, ProviderRouteEntry],
) -> bytes:
    """Serialize one roster-complete route state in canonical schema-v1 form."""

    if set(routes) != set(ROSTER):
        raise ValueError("provider routes must contain the complete roster")
    return _canonical_json(
        {
            "schema_version": _PROVIDER_ROUTE_GENERATIONS_SCHEMA_VERSION,
            "agents": {
                agent_id: {
                    "provider": routes[agent_id].provider,
                    "generation": routes[agent_id].generation,
                }
                for agent_id in ROSTER
            },
        }
    ).encode("utf-8")


def _decode_provider_routes(
    content: bytes,
) -> Mapping[str, ProviderRouteEntry]:
    """Validate and copy exact durable schema-v1 route generation state."""

    value = _decode_json(content, _PROVIDER_ROUTE_GENERATIONS_PATH)
    if not isinstance(value, dict) or set(value) != {"schema_version", "agents"}:
        raise WorkspaceFormatError("provider route generations have an invalid schema")
    if value["schema_version"] != _PROVIDER_ROUTE_GENERATIONS_SCHEMA_VERSION:
        raise WorkspaceFormatError(
            "provider route generations have an unsupported schema version"
        )
    agents = value["agents"]
    if not isinstance(agents, dict) or set(agents) != set(ROSTER):
        raise WorkspaceFormatError(
            "provider route generations must contain the complete roster"
        )
    routes: dict[str, ProviderRouteEntry] = {}
    try:
        for agent_id in ROSTER:
            raw = agents[agent_id]
            if not isinstance(raw, dict) or set(raw) != {"provider", "generation"}:
                raise ValueError
            routes[agent_id] = ProviderRouteEntry(
                provider=raw["provider"],
                generation=raw["generation"],
            )
    except (ConfigurationError, TypeError, ValueError) as exc:
        raise WorkspaceFormatError(
            "provider route generations contain an invalid entry"
        ) from exc
    return MappingProxyType(routes)


def _canonical_json(value: Any) -> str:
    """Serialize one validated settings document deterministically."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
