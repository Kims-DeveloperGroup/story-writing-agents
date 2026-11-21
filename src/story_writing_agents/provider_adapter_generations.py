"""Reservation, registration, and closing state for provider adapter generations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from threading import RLock

from .config import ConfigurationSnapshot
from .interactive_settings import ProviderRouteEntry
from .provider_adapter import AgentExecutionProfile, ProviderAdapter


@dataclass(slots=True)
class _AdapterGeneration:
    """One exact adapter/profile generation retained by its live bindings."""

    generation_id: int
    configuration: ConfigurationSnapshot
    profiles: Mapping[str, AgentExecutionProfile]
    provider_routes: Mapping[str, ProviderRouteEntry]
    adapters: dict[str, ProviderAdapter]
    switched_agents: frozenset[str] = frozenset()
    binding_count: int = 0
    retired: bool = False
    closed: bool = False


AdapterGeneration = _AdapterGeneration


class ProviderAdapterGenerations:
    """Own the registry under the Backend's generation lock."""

    def __init__(self, initial: AdapterGeneration, lock: RLock) -> None:
        self.active = initial
        self._lock = lock
        self._next_generation_id = initial.generation_id + 1
        self._generations = {initial.generation_id: initial}

    def reserve_locked(self) -> tuple[int, int, Mapping[str, AgentExecutionProfile]]:
        """Reserve an ID and snapshot the active basis while the caller holds the lock."""
        generation_id = self._next_generation_id
        self._next_generation_id += 1
        return generation_id, self.active.generation_id, self.active.profiles

    def publish_locked(self, generation: AdapterGeneration) -> None:
        """Publish a generation within the caller's activation transaction."""
        pass
        self._generations[generation.generation_id] = generation

    def begin_close(self, generation: AdapterGeneration) -> bool:
        """Mark the generation closed before its adapters are closed."""
        with self._lock:
            if generation.closed:
                return False
            generation.closed = True
            self._generations.pop(generation.generation_id, None)
        return True
