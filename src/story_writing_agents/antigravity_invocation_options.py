"""Antigravity execution option validation and resolution."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Protocol

from .provider_adapter import AgentExecutionProfile


class _InvocationDefaults(Protocol):
    """Effective system defaults used by Antigravity invocations."""

    @property
    def model(self) -> str | None: ...

    @property
    def effort(self) -> str | None: ...


_EFFORTS = ("low", "medium", "high")


def model(explicit: str | None, system: str | None) -> str | None:
    """Return the construction-time effective system Antigravity model."""

    if explicit is not None:
        return explicit
    return system


def effort(explicit: str | None, system: str | None) -> str | None:
    """Return the construction-time effective system Antigravity effort."""

    if explicit is not None:
        return explicit
    return system


def optional_setting(value: str | None, *, label: str) -> str | None:
    """Return one flag-safe optional setting without normalizing it."""

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
        raise ValueError(f"{label} must be safe non-empty text or None")
    return value


def optional_effort(value: str | None) -> str | None:
    """Return one validated optional Antigravity effort."""

    if value is not None and (
        not isinstance(value, str) or value not in _EFFORTS
    ):
        raise ValueError(
            "Antigravity effort must be None or one of: low, medium, high"
        )
    return value


def copy_agent_settings(
    value: Mapping[str, tuple[str | None, str | None]] | None,
    *,
    agent_id_pattern: re.Pattern[str],
) -> Mapping[str, tuple[str | None, str | None]] | None:
    """Validate and defensively copy provider-specific agent primitives."""

    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("Antigravity agent settings must be a mapping")
    copied: dict[str, tuple[str | None, str | None]] = {}
    for agent_id, setting in value.items():
        if (
            not isinstance(agent_id, str)
            or agent_id_pattern.fullmatch(agent_id) is None
            or type(setting) is not tuple
            or len(setting) != 2
        ):
            raise ValueError("Antigravity agent setting is invalid")
        model = optional_setting(
            setting[0],
            label="Antigravity agent model",
        )
        effort = optional_effort(setting[1])
        copied[agent_id] = (model, effort)
    return MappingProxyType(copied)


def copy_execution_profiles(
    execution_profiles: Mapping[str, AgentExecutionProfile] | None,
) -> Mapping[str, AgentExecutionProfile] | None:
    """Capture the existing immutable execution-profile mapping."""
    if execution_profiles is None:
        return None
    return MappingProxyType(dict(execution_profiles))


def require_execution_profile_provider(
    profiles: Mapping[str, AgentExecutionProfile] | None,
    *,
    get_vendor: Callable[[], str],
) -> None:
    """Require every captured execution profile to use the adapter's provider."""
    if profiles is not None and any(
        profile.provider != get_vendor() for profile in profiles.values()
    ):
        raise ValueError(
            "Antigravity execution profiles must use the Antigravity provider"
        )


def invocation_settings(
    agent_id: str | None,
    *,
    agent_settings: Mapping[str, tuple[str | None, str | None]] | None,
    execution_profiles: Mapping[str, AgentExecutionProfile] | None,
    explicit_model: str | None,
    explicit_effort: str | None,
    system_model: str | None,
    system_effort: str | None,
    defaults: _InvocationDefaults,
) -> tuple[str | None, str | None]:
    """Resolve Antigravity flags from immutable settings for one agent."""

    if agent_id is None or agent_settings is None:
        if agent_id is None:
            return defaults.model, defaults.effort
    if execution_profiles is not None:
        profile = execution_profiles.get(agent_id)
        if profile is None:
            raise ValueError("Antigravity execution profile is unavailable")
        return profile.model, profile.effort
    if agent_settings is None:
        return defaults.model, defaults.effort
    if agent_id not in agent_settings:
        raise ValueError("Antigravity agent setting is unavailable")
    agent_model, agent_effort = agent_settings[agent_id]
    model = (
        explicit_model
        if explicit_model is not None
        else system_model
    )
    effort = (
        explicit_effort
        if explicit_effort is not None
        else agent_effort or system_effort
    )
    return model, effort
