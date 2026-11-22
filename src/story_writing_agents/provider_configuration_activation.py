"""Activate, validate, and discard owned provider configuration generations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from threading import RLock
from typing import Protocol

from .agents import ROSTER
from .debug_log import DebugOperation
from .errors import BackendProtocolError
from .provider_adapter import AgentExecutionProfile
from .provider_adapter_generations import AdapterGeneration, ProviderAdapterGenerations
from .provider_execution_profile_observations import ProviderExecutionProfileObservations


class _ConfigurationCandidate(Protocol):
    @property
    def base_generation_id(self) -> int: ...

    @property
    def correlation_id(self) -> str: ...

    @property
    def previous_profiles(self) -> Mapping[str, AgentExecutionProfile]: ...

    @property
    def generation(self) -> AdapterGeneration: ...


def _require_current_quiescent_generation_locked(
    candidate: _ConfigurationCandidate,
    generations: ProviderAdapterGenerations,
    has_live_bindings: Callable[[], bool],
) -> AdapterGeneration:
    current = generations.active
    if current.generation_id != candidate.base_generation_id:
        raise BackendProtocolError("runtime configuration candidate is stale")
    if has_live_bindings():
        raise BackendProtocolError(
            "runtime configuration activation requires quiescent bindings"
        )
    return current


def activate_provider_configuration(
    candidate: _ConfigurationCandidate,
    *,
    generation_lock: RLock,
    generations: ProviderAdapterGenerations,
    profile_observations: ProviderExecutionProfileObservations,
    has_live_bindings: Callable[[], bool],
    install_backend_mirrors: Callable[[AdapterGeneration], None],
    record_profile_event: Callable[..., None],
    record_close_error: Callable[[Exception], None],
) -> None:
    """Publish one owned generation and complete post-lock activation work."""

    with generation_lock:
        current = _require_current_quiescent_generation_locked(
            candidate, generations, has_live_bindings
        )
        next_generation = candidate.generation
        old_profiles = current.profiles
        previous_observations = profile_observations.snapshot_locked()
        for agent_id in ROSTER:
            if old_profiles[agent_id] != next_generation.profiles[agent_id]:
                record_profile_event(
                    DebugOperation.EXECUTION_SETTING_CHANGE,
                    correlation_id=candidate.correlation_id,
                    route_generation=(
                        next_generation.provider_routes[agent_id].generation
                    ),
                    old_profile=old_profiles[agent_id],
                    new_profile=next_generation.profiles[agent_id],
                    activation_state="accepted",
                )
        generations.publish_locked(next_generation)
        install_backend_mirrors(next_generation)
        pass  # Validation-only omission of actual profile observation install.






        close_generation = generations.retire_locked(current)
    if close_generation is not None:
        generations.close_generation(close_generation, record_error=record_close_error)
    for agent_id in ROSTER:
        if old_profiles[agent_id] != candidate.generation.profiles[agent_id]:
            record_profile_event(
                DebugOperation.EXECUTION_PROFILE_APPLIED,
                correlation_id=candidate.correlation_id,
                route_generation=(
                    candidate.generation.provider_routes[agent_id].generation
                ),
                old_profile=old_profiles[agent_id],
                new_profile=candidate.generation.profiles[agent_id],
                activation_state="active",
            )


def validate_provider_configuration_candidate(
    candidate: _ConfigurationCandidate,
    *,
    generation_lock: RLock,
    generations: ProviderAdapterGenerations,
    has_live_bindings: Callable[[], bool],
) -> None:
    """Check an owned staged generation before settings persistence."""

    with generation_lock:
        if candidate.generation.closed:
            raise BackendProtocolError(
                "runtime configuration candidate is already closed"
            )
        _require_current_quiescent_generation_locked(
            candidate, generations, has_live_bindings
        )


def provider_configuration_candidate_is_active(
    candidate: _ConfigurationCandidate,
    *,
    generation_lock: RLock,
    generations: ProviderAdapterGenerations,
) -> bool:
    """Check whether the exact owned candidate holds the active generation."""

    with generation_lock:
        return candidate.generation is generations.active


def discard_provider_configuration(
    candidate: _ConfigurationCandidate,
    *,
    generation_lock: RLock,
    generations: ProviderAdapterGenerations,
    record_close_error: Callable[[Exception], None],
) -> None:
    """Close an inactive owned candidate through the generation registry."""

    with generation_lock:
        if candidate.generation is generations.active:
            return
    generations.close_generation(candidate.generation, record_error=record_close_error)


def record_provider_configuration_recovery_required(
    candidate: _ConfigurationCandidate,
    *,
    record_profile_event: Callable[..., None],
) -> None:
    """Record the profile changes of an owned candidate requiring recovery."""

    previous = candidate.previous_profiles
    for agent_id in ROSTER:
        active = candidate.generation.profiles[agent_id]
        if previous[agent_id] == active:
            continue
        record_profile_event(
            DebugOperation.EXECUTION_PROFILE_RECOVERY_REQUIRED,
            correlation_id=candidate.correlation_id,
            route_generation=(
                candidate.generation.provider_routes[agent_id].generation
            ),
            old_profile=previous[agent_id],
            new_profile=active,
            activation_state="recovery_required",
            recovery_required=True,
        )
