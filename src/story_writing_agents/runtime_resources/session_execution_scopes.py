"""Per-runtime active continuation membership, conversation locks and consensus tasks."""
from __future__ import annotations
import asyncio
from typing import AsyncIterator, Callable
from ..errors import BackendProtocolError
from ..agent_context.continuation import ContextContinuationTransaction
from ..agents import LEAD_ORCHESTRATOR
from contextlib import asynccontextmanager


class SessionExecutionScopes:

    def __init__(self) -> None:
        self._conversation_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._lead_continuations: dict[str, ContextContinuationTransaction] = {}
        self._consensus_provider_tasks: dict[
            tuple[str, str, str, str], set[asyncio.Task[object]]
        ] = {}

    @asynccontextmanager
    async def lead_session_lifecycle(self,
        session_id: str,
        *,
        continuation_factory: Callable[[str,
                str
            ],
            ContextContinuationTransaction
        ]
    ) -> AsyncIterator[None]:
        transaction = continuation_factory(
            session_id, LEAD_ORCHESTRATOR,
        )
        async with transaction:
            self._lead_continuations[session_id] = transaction
            try:
                yield
            finally:
                del self._lead_continuations[session_id]

    def conversation_lock(self, session_id: str, conversation_id: str) -> asyncio.Lock:
        key = (session_id, conversation_id)
        lock = self._conversation_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._conversation_locks[key] = lock
        return lock

    def cancel_provider_tasks(self,
        session_id: str,
        conversation_id: str,
        workflow_id: str,
        phase_request_id: str,
        /
    ) -> bool:
        """Cancel only the exact active consensus provider task."""

        tasks = self._consensus_provider_tasks.get(
            (session_id, conversation_id, workflow_id, phase_request_id)
        )
        active = () if tasks is None else tuple(task for task in tasks if not task.done())
        if not active:
            return False
        for task in active:
            task.cancel()
        return True

    def register_provider_task(self, key: tuple[str, str, str, str]) -> asyncio.Task[object]:
        current_task = asyncio.current_task()
        if current_task is None:
            raise BackendProtocolError("consensus provider invocation has no task")
        active_tasks = self._consensus_provider_tasks.setdefault(key, set())
        if current_task in active_tasks:
            raise BackendProtocolError("consensus provider invocation is already active")
        active_tasks.add(current_task)
        return current_task

    def unregister_provider_task(self,
        key: tuple[str,
            str,
            str,
            str
        ],
        current_task: asyncio.Task[object]
    ) -> None:
        active_tasks = self._consensus_provider_tasks[key]
        active_tasks.discard(current_task)
        if not active_tasks:
            self._consensus_provider_tasks.pop(key, None)

    def lead_continuation(self, session_id: str) -> ContextContinuationTransaction:
        return self._lead_continuations[session_id]

    def optional_lead_continuation(self, session_id: str) -> ContextContinuationTransaction | None:
        return self._lead_continuations.get(session_id)
