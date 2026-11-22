"""Prepare one unpublished provider configuration generation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from threading import RLock
from types import MappingProxyType
from typing import TypeVar

from .agents import ROSTER
from .antigravity_adapter import AntigravityAdapter
from .config import ConfigurationSnapshot
from .errors import BackendProtocolError, ConfigurationError
from .interactive_settings import ProviderRouteEntry
from .provider_adapter import AgentExecutionProfile, ProviderAdapter
from .provider_adapter_generations import AdapterGeneration, ProviderAdapterGenerations


_Candidate = TypeVar("_Candidate")


def stage_provider_configuration(
    configuration: ConfigurationSnapshot,
    provider_routes: Mapping[str, ProviderRouteEntry],
    *,
    correlation_id: str,
    generation_lock: RLock,
    generations: ProviderAdapterGenerations,
    resolve_profiles: Callable[
        [ConfigurationSnapshot], Mapping[str, AgentExecutionProfile]
    ],
    create_adapters: Callable[
        [ConfigurationSnapshot, Mapping[str, AgentExecutionProfile]],
        dict[str, ProviderAdapter],
    ],
    make_candidate: Callable[
        [int, Mapping[str, AgentExecutionProfile], AdapterGeneration], _Candidate
    ],
) -> _Candidate:
    """Validate and prepare one unpublished generation for the caller."""

    if type(configuration) is not ConfigurationSnapshot:
        raise TypeError("configuration must be a ConfigurationSnapshot")
    if set(provider_routes) != set(ROSTER) or any(
        type(provider_routes[agent_id]) is not ProviderRouteEntry
        for agent_id in ROSTER
    ):
        raise ValueError("provider_routes must contain the complete roster")
    if not isinstance(correlation_id, str) or not correlation_id:
        raise ValueError("correlation_id must be non-empty text")
    profiles = resolve_profiles(configuration)
    if any(
        provider_routes[agent_id].provider != profiles[agent_id].provider
        for agent_id in ROSTER
    ):
        raise BackendProtocolError(
            "provider route generation does not match the effective profile"
        )
    adapters = create_adapters(configuration, profiles)

    def reserve() -> tuple[
        int, int, Mapping[str, AgentExecutionProfile], frozenset[str]
    ]:
        with generation_lock:
            generation_id, base_generation_id, current_profiles = (
                generations.reserve_locked()
            )
            switched_agents = frozenset(
                agent_id
                for agent_id in ROSTER
                if current_profiles[agent_id].provider
                != profiles[agent_id].provider
            )
        return generation_id, base_generation_id, current_profiles, switched_agents

    return prepare_provider_configuration_candidate(
        configuration,
        provider_routes,
        profiles,
        adapters,
        reserve=reserve,
        make_candidate=make_candidate,
    )


def prepare_provider_configuration_candidate(
    configuration: ConfigurationSnapshot,
    provider_routes: Mapping[str, ProviderRouteEntry],
    profiles: Mapping[str, AgentExecutionProfile],
    adapters: dict[str, ProviderAdapter],
    *,
    reserve: Callable[
        [], tuple[int, int, Mapping[str, AgentExecutionProfile], frozenset[str]]
    ],
    make_candidate: Callable[
        [int, Mapping[str, AgentExecutionProfile], AdapterGeneration], _Candidate
    ],
) -> _Candidate:
    """Preflight, assemble, and discard the staged adapters on failure."""
    try:
        effective_providers = {profile.provider for profile in profiles.values()}
        for provider in effective_providers:
            if not adapters[provider].executable_available():
                raise ConfigurationError(
                    f"configured provider executable is unavailable: {provider}"
                )
        antigravity = adapters["antigravity"]
        if (
            "antigravity" in effective_providers
            and isinstance(antigravity, AntigravityAdapter)
            and not antigravity.headless_capabilities_available()
        ):
            raise ConfigurationError(
                "Antigravity headless capabilities are unavailable"
            )
        generation_id, base_generation_id, current_profiles, switched_agents = (
            reserve()
        )
        generation = AdapterGeneration(
            generation_id=generation_id,
            configuration=configuration,
            profiles=profiles,
            provider_routes=MappingProxyType(dict(provider_routes)),
            adapters=adapters,
            switched_agents=switched_agents,
        )
        return make_candidate(base_generation_id, current_profiles, generation)
    except BaseException:
        for adapter in adapters.values():
            try:
                adapter.close()
            except Exception:
                pass
        raise
