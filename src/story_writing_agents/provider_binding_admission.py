"""Prepare a provider binding inside the caller's generation transaction."""

from __future__ import annotations

from .errors import BackendProtocolError
from .models import AgentDefinition
from .provider_adapter import AgentExecutionProfile, ProviderBinding
from .provider_adapter_generations import AdapterGeneration, ProviderAdapterGenerations


def prepare_provider_binding_locked(
    generations: ProviderAdapterGenerations,
    definition: AgentDefinition,
    agent_id: str,
) -> tuple[AdapterGeneration, AgentExecutionProfile, ProviderBinding]:
    """Bind the selected adapter for the active execution profile."""

    generation = generations.active
    profile = generation.profiles[agent_id]
    if profile.provider != definition.backend:
        raise BackendProtocolError(
            "definition provider does not match the active execution profile"
        )
    adapter = generation.adapters[definition.backend]
    binding = adapter.bind(definition)
    if type(binding) is not ProviderBinding:
        raise BackendProtocolError(
            "provider adapter returned an inconsistent binding"
        )
    return generation, profile, binding
