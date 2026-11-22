"""Reservations, binding lifetimes, and cleanup for provider adapter generations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import RLock

from .config import ConfigurationSnapshot
from .errors import BackendProtocolError
from .interactive_settings import ProviderRouteEntry
from .provider_adapter import AgentExecutionProfile, ProviderAdapter, ProviderBinding


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
        self.active = generation
        self._generations[generation.generation_id] = generation

    def retain_binding_locked(self, generation: AdapterGeneration) -> None:
        """Count a bound agent within the caller's binding transaction."""
        generation.binding_count += 1

    def retire_locked(self, generation: AdapterGeneration) -> AdapterGeneration | None:
        """Retire the prior generation and return it when ready to close."""
        generation.retired = True
        return generation if generation.binding_count == 0 else None

    def release_binding(
        self,
        generation: AdapterGeneration | None,
        adapter: ProviderAdapter,
        binding: ProviderBinding,
    ) -> AdapterGeneration | None:
        """Release the binding and return a retired generation with no bindings."""
        adapter.release(binding)
        if generation is None:
            return None
        with self._lock:
            generation.binding_count -= 1
            if generation.binding_count < 0:
                raise BackendProtocolError(
                    "adapter generation binding count became negative"
                )
            if generation.retired and generation.binding_count == 0:
                return generation
        return None

    def close_generation(
        self,
        generation: AdapterGeneration,
        *,
        record_error: Callable[[Exception], None],
    ) -> None:
        """Close one retired or staged generation exactly once."""
        with self._lock:
            if generation.closed:
                return
            generation.closed = True
            self._generations.pop(generation.generation_id, None)
        for adapter in generation.adapters.values():
            try:
                adapter.close()
            except Exception as exc:
                record_error(exc)
