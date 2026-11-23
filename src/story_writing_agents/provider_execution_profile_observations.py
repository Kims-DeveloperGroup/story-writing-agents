"""Observation state for active provider execution profiles."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import RLock
from threading import RLock
from typing import TypeVar

from .agents import ROSTER
from .debug_log import DebugOperation
from .errors import ConfigurationError
from .provider_adapter import AgentExecutionProfile
from .provider_adapter_generations import AdapterGeneration, ProviderAdapterGenerations


@dataclass(frozen=True, slots=True)
class _ProfileObservationSnapshot:
    confirmed: dict[str, bool]
    correlation: dict[str, str]
    thread_status: dict[str, str]


class ProviderExecutionProfileObservations:
    """Track confirmation and thread observations under the Backend's generation lock."""

    def __init__(
        self,
        startup_switched_agents: frozenset[str],
        *,
        generation_lock: RLock,
        adapter_generations: ProviderAdapterGenerations,
    ) -> None:
        self._profile_confirmed = {agent_id: False for agent_id in ROSTER}
        self._active_profile_correlation = {agent_id: "startup" for agent_id in ROSTER}
        self._profile_thread_status = {
            agent_id: (
                "fresh_pending"
                if agent_id in startup_switched_agents
                else "not_observed"
            )
            for agent_id in ROSTER
        }
        self._generation_lock = generation_lock
        self._adapter_generations = adapter_generations

    def snapshot_locked(self) -> _ProfileObservationSnapshot:
        return _ProfileObservationSnapshot(
            dict(self._profile_confirmed),
            dict(self._active_profile_correlation),
            dict(self._profile_thread_status),
        )

    def install_locked(
        self,
        previous: _ProfileObservationSnapshot,
        *,
        old_profiles: Mapping[str, AgentExecutionProfile],
        new_profiles: Mapping[str, AgentExecutionProfile],
        switched_agents: frozenset[str],
        correlation_id: str,
    ) -> None:
        self._profile_confirmed = {
            agent_id: (
                previous.confirmed[agent_id]
                if old_profiles[agent_id] == new_profiles[agent_id]
                else False
            )
            for agent_id in ROSTER
        }
        self._active_profile_correlation = {
            agent_id: (
                previous.correlation[agent_id]
                if old_profiles[agent_id] == new_profiles[agent_id]
                else correlation_id
            )
            for agent_id in ROSTER
        }
        self._profile_thread_status = {
            agent_id: (
                previous.thread_status[agent_id]
                if old_profiles[agent_id] == new_profiles[agent_id]
                else "fresh_pending"
                if agent_id in switched_agents
                else "reusable_pending"
            )
            for agent_id in ROSTER
        }

    def confirmation_locked(self, agent_id: str) -> str:
        return "confirmed" if self._profile_confirmed[agent_id] else "pending"

    def thread_status_locked(self, agent_id: str) -> str:
        return self._profile_thread_status[agent_id]

    def correlation_locked(self, agent_id: str) -> str:
        return self._active_profile_correlation[agent_id]

    def confirm_locked(self, agent_id: str, thread_disposition: str) -> bool:
        """Record a successful call and return whether it first confirmed the profile."""
        self._profile_thread_status[agent_id] = thread_disposition
        if not self._profile_confirmed[agent_id]:
            self._profile_confirmed[agent_id] = True
            return True
        return False

    def active_profile(self, agent_id: str) -> AgentExecutionProfile:
        """Return the exact active profile used for the next agent invocation."""

        if agent_id not in ROSTER:
            raise ConfigurationError(f"unknown agent: {agent_id}")
        with self._generation_lock:
            return self._adapter_generations.active.profiles[agent_id]

    def active_profiles(self) -> tuple[AgentExecutionProfile, ...]:
        """Return the active roster in canonical order."""

        with self._generation_lock:
            profiles = self._adapter_generations.active.profiles
            return tuple(profiles[agent_id] for agent_id in ROSTER)

    def confirmation(self, agent_id: str) -> str:
        """Return pending or confirmed for the active execution profile."""

        if agent_id not in ROSTER:
            raise ConfigurationError(f"unknown agent: {agent_id}")
        with self._generation_lock:
            return self.confirmation_locked(agent_id)

    def thread_status(self, agent_id: str) -> str:
        """Return the active generation's observed or pending thread status."""

        if agent_id not in ROSTER:
            raise ConfigurationError(f"unknown agent: {agent_id}")
        with self._generation_lock:
            return self.thread_status_locked(agent_id)

    def confirm_active_profile(
        self,
        profile: AgentExecutionProfile,
        generation: AdapterGeneration,
        *,
        context_rehydrated: bool,
        thread_disposition: str,
        confirmation_logger: Callable[[], Callable[..., None]],
    ) -> None:
        """Confirm first successful use of the exact active generation."""
        with self._generation_lock:
            if generation is not self._adapter_generations.active:
                return
            emit_confirmation = self.confirm_locked(
                profile.agent_id, thread_disposition
            )
        if not emit_confirmation:
            return


    def record_confirmed_profile(
        self,
        profile: AgentExecutionProfile,
        *,
        thread_disposition: str,
        context_state: str,
        route_generation: Callable[[], int],
        profile_event_logger: Callable[[], Callable[..., None]],
    ) -> None:
        """Capture current confirmation metadata and emit after unlocking."""
        with self._generation_lock:
            correlation_id = self.correlation_locked(profile.agent_id)
            route_generation = route_generation()
        profile_event_logger()(
            DebugOperation.EXECUTION_PROFILE_CONFIRMED,
            correlation_id=correlation_id,
            route_generation=route_generation,
            active_profile=profile,
            activation_state="confirmed",
            thread_disposition=thread_disposition,
            context_state=context_state,
        )
