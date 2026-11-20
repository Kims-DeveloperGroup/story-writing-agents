"""Resolve provider execution profiles and their exact fingerprints."""

from __future__ import annotations

import hashlib
import json
from types import MappingProxyType
from typing import Mapping

from .agents import ROSTER
from .config import ConfigurationSnapshot, ConfigManager
from .provider_adapter import AgentExecutionProfile, ExecutionValueSource
from .workspace import Workspace


def resolve_execution_profiles(
    workspace: Workspace,
    configuration: ConfigurationSnapshot,
    *,
    backend_overrides: Mapping[str, str],
    explicit_model: str | None,
    explicit_antigravity_model: str | None,
    explicit_antigravity_effort: str | None,
) -> Mapping[str, AgentExecutionProfile]:
    """Resolve the sole authoritative provider/model/effort roster."""

    manager = ConfigManager(workspace)
    profiles: dict[str, AgentExecutionProfile] = {}
    defaults = configuration.system_defaults
    for agent_id in ROSTER:
        agent = configuration.agent_configs[agent_id]
        if agent_id in backend_overrides:
            provider = backend_overrides[agent_id]
            provider_source = ExecutionValueSource.LAUNCH_OVERRIDE
        elif agent.backend is not None:
            provider = agent.backend
            provider_source = ExecutionValueSource.AGENT_CONFIG
        elif defaults.default_backend is not None:
            provider = defaults.default_backend
            provider_source = ExecutionValueSource.SYSTEM_DEFAULT
        else:
            provider = "codex"
            provider_source = ExecutionValueSource.BUILT_IN_DEFAULT
        provider = manager.validate_backend(provider)
        assert provider is not None

        if provider == "codex":
            model, model_source = _effective_execution_value(
                explicit_model,
                agent.codex_model,
                defaults.codex_model,
            )
            effort, effort_source = _effective_execution_value(
                None,
                agent.codex_reasoning_effort,
                defaults.codex_reasoning_effort,
            )
        else:
            model, model_source = _effective_execution_value(
                explicit_antigravity_model,
                agent.antigravity_model,
                defaults.antigravity_model,
            )
            effort, effort_source = _effective_execution_value(
                explicit_antigravity_effort,
                agent.antigravity_effort,
                defaults.antigravity_effort,
            )
        profiles[agent_id] = AgentExecutionProfile(
            agent_id=agent_id,
            provider=provider,
            provider_source=provider_source,
            model=model,
            model_source=model_source,
            effort=effort,
            effort_source=effort_source,
        )
    return MappingProxyType(profiles)


def _effective_execution_value(
    launch_value: str | None,
    agent_value: str | None,
    system_value: str | None,
) -> tuple[str | None, ExecutionValueSource]:
    if launch_value is not None:
        return launch_value, ExecutionValueSource.LAUNCH_OVERRIDE
    if agent_value is not None:
        return agent_value, ExecutionValueSource.AGENT_CONFIG
    if system_value is not None:
        return system_value, ExecutionValueSource.SYSTEM_DEFAULT
    return None, ExecutionValueSource.PROVIDER_DEFAULT


def execution_profile_fingerprint(profile: AgentExecutionProfile) -> str:
    if type(profile) is not AgentExecutionProfile:
        raise TypeError("execution profile is invalid")
    body = json.dumps(
        {
            "effort": profile.effort,
            "effort_source": profile.effort_source.value,
            "model": profile.model,
            "model_source": profile.model_source.value,
            "provider": profile.provider,
            "provider_source": profile.provider_source.value,
        },
        allow_nan=False,
        ensure_ascii=False,
        separators=(", ", ": "),
        sort_keys=True,
    ).encode("utf-8", errors="strict")
    return "sha256:" + hashlib.sha256(body).hexdigest()


__all__ = ("execution_profile_fingerprint", "resolve_execution_profiles")
