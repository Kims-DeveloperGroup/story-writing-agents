"""Prepare one unpublished provider configuration generation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import TypeVar

from .antigravity_adapter import AntigravityAdapter
from .config import ConfigurationSnapshot
from .errors import ConfigurationError
from .interactive_settings import ProviderRouteEntry
from .provider_adapter import AgentExecutionProfile, ProviderAdapter
from .provider_adapter_generations import AdapterGeneration


_Candidate = TypeVar("_Candidate")


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
