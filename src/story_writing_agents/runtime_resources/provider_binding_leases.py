"""Per-runtime binding selection, retained operation use and cancellation drain."""
from __future__ import annotations
import asyncio
from typing import Any, Callable, Coroutine
from ..errors import BackendProtocolError
from ..backend import BoundAgent, release_bound_agent


class SupersededBindingSelection(Exception):
    """Retry a selection-only request whose bind completed out of order."""

class ProviderBindingLeases:

    def __init__(self) -> None:
        self._bound_agents: dict[tuple[str, str], BoundAgent] = {}
        self._desired_binding_fingerprints: dict[str, str] = {}
        self._retiring_bound_agents: dict[tuple[str, str], BoundAgent] = {}
        self._bound_agent_uses: dict[int, tuple[BoundAgent, int]] = {}
        self._binding_tasks: dict[tuple[str, str], asyncio.Task[BoundAgent]] = {}
        self._binding_waiters: dict[tuple[str, str], int] = {}
        self._binding_lock = asyncio.Lock()

    async def acquire(self,
        definition: object,
        *,
        bind_definition: Callable[[object],
            Coroutine[Any,
                Any,
                BoundAgent
            ]
        ]
    ) -> BoundAgent:
        """Select one binding and retain it for the exact lead operation."""

        return await self.select(definition, retain_use=True,
            bind_definition=bind_definition,
        )

    async def select(self,
        definition: object,
        *,
        retain_use: bool,
        bind_definition: Callable[[object],
            Coroutine[Any,
                Any,
                BoundAgent
            ]
        ]
    ) -> BoundAgent:
        agent_id = getattr(definition, "agent_id", None)
        fingerprint = getattr(definition, "fingerprint", None)
        if type(agent_id) is not str or type(fingerprint) is not str:
            raise BackendProtocolError("agent definition identity is invalid")
        key = (agent_id, fingerprint)
        releases: list[BoundAgent] = []
        selected: BoundAgent | None = None
        async with self._binding_lock:
            self._desired_binding_fingerprints[key[0]] = key[1]
            selected = self._bound_agents.get(key)
            if selected is None:
                selected = self._retiring_bound_agents.pop(key, None)
                if selected is not None:
                    releases.extend(self._install_current_binding_locked(key, selected))
            if selected is not None:
                if retain_use:
                    self._retain_bound_agent_use_locked(selected)
                task = None
            else:
                task = self._binding_tasks.get(key)
                if task is None:
                    task = asyncio.create_task(
                        bind_definition(definition),
                        name=f"bind:{agent_id}:{fingerprint[:12]}",
                    )
                    self._binding_tasks[key] = task
                self._binding_waiters[key] = self._binding_waiters.get(key, 0) + 1

        self._release_provider_bindings(releases)
        if selected is not None:
            return selected
        assert task is not None
        task_result_handled = False
        superseded_selection = False
        try:
            bound_agent = await asyncio.shield(task)
            releases = []
            async with self._binding_lock:
                if (
                    self._binding_tasks.get(key) is task
                    and self._binding_waiters.get(key, 0) > 0
                ):
                    is_desired = (
                        self._desired_binding_fingerprints.get(key[0]) == key[1]
                    )
                    if retain_use:
                        self._retain_bound_agent_use_locked(bound_agent)
                    if is_desired:
                        releases.extend(
                            self._install_current_binding_locked(key, bound_agent)
                        )
                    elif not retain_use:
                        releases.append(bound_agent)
                        superseded_selection = True
                    else:
                        releases.extend(
                            self._mark_binding_retiring_locked(key, bound_agent)
                        )
                    if not superseded_selection:
                        selected = bound_agent
                    task_result_handled = True
            self._release_provider_bindings(releases)
            if superseded_selection:
                raise SupersededBindingSelection
            if selected is None:
                raise BackendProtocolError(
                    "completed binding lost its runtime ownership"
                )
            return selected
        finally:
            task_to_join: asyncio.Task[BoundAgent] | None = None
            async with self._binding_lock:
                remaining = self._binding_waiters.get(key, 0) - 1
                if remaining > 0:
                    self._binding_waiters[key] = remaining
                else:
                    self._binding_waiters.pop(key, None)
                    if self._binding_tasks.get(key) is task:
                        self._binding_tasks.pop(key, None)
                        if not task.done():
                            task.cancel()
                        task_to_join = task
            if task_to_join is not None:
                outcome = (await asyncio.gather(task_to_join, return_exceptions=True))[
                    0
                ]
                if not task_result_handled and not isinstance(
                    outcome,
                    BaseException,
                ):
                    async with self._binding_lock:
                        tracked = self._binding_is_tracked_locked(outcome)
                    if not tracked:
                        release_bound_agent(outcome)

    def _install_current_binding_locked(
        self,
        key: tuple[str, str],
        bound_agent: BoundAgent,
    ) -> list[BoundAgent]:
        releases: list[BoundAgent] = []
        for current_key, current in tuple(self._bound_agents.items()):
            if current_key[0] != key[0] or current_key == key:
                continue
            self._bound_agents.pop(current_key)
            if current is not bound_agent:
                releases.extend(
                    self._mark_binding_retiring_locked(current_key, current)
                )
        self._retiring_bound_agents.pop(key, None)
        self._bound_agents[key] = bound_agent
        return releases

    def _mark_binding_retiring_locked(
        self,
        key: tuple[str, str],
        bound_agent: BoundAgent,
    ) -> list[BoundAgent]:
        existing = self._retiring_bound_agents.get(key)
        if existing is bound_agent:
            return []
        use = self._bound_agent_uses.get(id(bound_agent))
        if use is not None and use[0] is bound_agent and use[1] > 0:
            self._retiring_bound_agents[key] = bound_agent
            return []
        return [bound_agent]

    def _retain_bound_agent_use_locked(self, bound_agent: BoundAgent) -> None:
        identity = id(bound_agent)
        current = self._bound_agent_uses.get(identity)
        if current is None:
            self._bound_agent_uses[identity] = (bound_agent, 1)
            return
        if current[0] is not bound_agent:
            raise BackendProtocolError("bound agent identity was reused")
        self._bound_agent_uses[identity] = (bound_agent, current[1] + 1)

    async def release_use(self,
        bound_agent: BoundAgent,
        *,
        finish_use: Callable[[BoundAgent],
            Coroutine[Any,
                Any,
                None
            ]
        ]
    ) -> None:
        """Finish one release before propagating caller cancellation."""

        task = asyncio.create_task(
            finish_use(bound_agent),
            name="release-bound-agent-use",
        )
        cancellation: asyncio.CancelledError | None = None
        interruption: BaseException | None = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as failure:
                if cancellation is None:
                    cancellation = failure
            except BaseException as failure:
                if task.done():
                    break
                if interruption is None:
                    interruption = failure

        cleanup_failure: BaseException | None = None
        if task.cancelled():
            cleanup_failure = asyncio.CancelledError()
        else:
            try:
                task.result()
            except BaseException as failure:
                cleanup_failure = failure
        if cancellation is not None:
            if cleanup_failure is not None:
                cancellation.add_note(
                    "bound-agent release failed: "
                    f"{type(cleanup_failure).__name__}"
                )
            raise cancellation
        if interruption is not None:
            if cleanup_failure is not None:
                interruption.add_note(
                    "bound-agent release failed: "
                    f"{type(cleanup_failure).__name__}"
                )
            raise interruption
        if cleanup_failure is not None:
            raise cleanup_failure

    async def decrement_retained_use(self, bound_agent: BoundAgent) -> BoundAgent | None:
        """Decrement one retained use exactly once."""

        release: BoundAgent | None = None
        async with self._binding_lock:
            identity = id(bound_agent)
            current = self._bound_agent_uses.get(identity)
            if current is None or current[0] is not bound_agent:
                raise BackendProtocolError("bound agent operation use is absent")
            remaining = current[1] - 1
            if remaining > 0:
                self._bound_agent_uses[identity] = (bound_agent, remaining)
            else:
                self._bound_agent_uses.pop(identity)
                for key, retiring in tuple(self._retiring_bound_agents.items()):
                    if retiring is bound_agent:
                        self._retiring_bound_agents.pop(key)
                        release = bound_agent
                        break
        return release

    def _binding_is_tracked_locked(self, bound_agent: object) -> bool:
        return (
            any(current is bound_agent for current in self._bound_agents.values())
            or any(
                retiring is bound_agent
                for retiring in self._retiring_bound_agents.values()
            )
            or (
                id(bound_agent) in self._bound_agent_uses
                and self._bound_agent_uses[id(bound_agent)][0] is bound_agent
            )
        )

    @staticmethod
    def _release_provider_bindings(bindings: list[BoundAgent]) -> None:
        """Release each identity once outside the runtime binding lock."""

        released: set[int] = set()
        for bound_agent in bindings:
            identity = id(bound_agent)
            if identity in released:
                continue
            released.add(identity)
            release_bound_agent(bound_agent)

    async def prepare_configuration(self) -> None:
        releases: list[BoundAgent] = []
        async with self._binding_lock:
            if self._bound_agent_uses or self._binding_tasks or self._binding_waiters:
                raise BackendProtocolError(
                    "runtime configuration requires a quiescent provider boundary"
                )
            releases.extend(self._bound_agents.values())
            releases.extend(self._retiring_bound_agents.values())
            self._bound_agents.clear()
            self._retiring_bound_agents.clear()
            self._desired_binding_fingerprints.clear()
        self._release_provider_bindings(releases)

    async def commit_configuration(self, *, activate: Callable[[], None]) -> None:
        async with self._binding_lock:
            if (
                self._bound_agents
                or self._retiring_bound_agents
                or self._bound_agent_uses
                or self._binding_tasks
                or self._binding_waiters
            ):
                raise BackendProtocolError(
                    "runtime configuration activation lost quiescence"
                )
            activate()
