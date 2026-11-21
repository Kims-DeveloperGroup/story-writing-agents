"""Observation state for active provider execution profiles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .agents import ROSTER
from .provider_adapter import AgentExecutionProfile


@dataclass(frozen=True, slots=True)
class _ProfileObservationSnapshot:
    confirmed: dict[str, bool]
    correlation: dict[str, str]
    thread_status: dict[str, str]


class ProviderExecutionProfileObservations:
    """Track confirmation and thread observations under the Backend's generation lock."""

    def __init__(self, startup_switched_agents: frozenset[str]) -> None:
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
