"""Shared deterministic fixtures for the standard-library test suite."""

from __future__ import annotations

import asyncio
import inspect
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeAlias
from unittest import TestCase

from story_writing_agents import (
    AgentReply,
    AgentRequest,
    Phase,
    SPECIALIST_IDS,
    Workspace,
)
from story_writing_agents.agents import CORE_DOCUMENTS


HandlerResult: TypeAlias = AgentReply | BaseException | Awaitable[AgentReply]
RequestHandler: TypeAlias = Callable[[AgentRequest], HandlerResult]


def initialized_workspace(test_case: TestCase) -> Workspace:
    """Create an initialized temporary workspace owned by ``test_case``."""

    temporary = tempfile.TemporaryDirectory()
    test_case.addCleanup(temporary.cleanup)
    return Workspace(temporary.name).initialize()


def core_snapshot(workspace: Workspace) -> dict[str, str]:
    """Return the full allowlisted core-document state."""

    return workspace.read_core_documents(CORE_DOCUMENTS)


class ScriptedBackend:
    """Record requests and answer them with a deterministic handler."""

    def __init__(self, handler: RequestHandler) -> None:
        self.handler = handler
        self.requests: list[AgentRequest] = []

    async def invoke(self, request: AgentRequest) -> AgentReply:
        self.requests.append(request)
        result = self.handler(request)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, BaseException):
            raise result
        return result


class ReverseCompletionApprovalBackend:
    """Prove audits overlap while completing them opposite roster order."""

    def __init__(self) -> None:
        self.requests: list[AgentRequest] = []
        self.arrivals: list[str] = []
        self.completions: list[str] = []
        self._all_arrived = asyncio.Event()
        self._execution_done = asyncio.Event()
        self._character_done = asyncio.Event()

    async def invoke(self, request: AgentRequest) -> AgentReply:
        self.requests.append(request)
        if request.phase is Phase.AUDIT:
            self.arrivals.append(request.agent_id)
            if len(self.arrivals) == len(SPECIALIST_IDS):
                self._all_arrived.set()
            await asyncio.wait_for(self._all_arrived.wait(), timeout=1)

            if request.agent_id == SPECIALIST_IDS[2]:
                self.completions.append(request.agent_id)
                self._execution_done.set()
            elif request.agent_id == SPECIALIST_IDS[1]:
                await asyncio.wait_for(self._execution_done.wait(), timeout=1)
                self.completions.append(request.agent_id)
                self._character_done.set()
            else:
                await asyncio.wait_for(self._character_done.wait(), timeout=1)
                self.completions.append(request.agent_id)
            return AgentReply.approval(request.revision_id)

        if request.phase is Phase.FINALIZE:
            return AgentReply.finalization(request.revision_id)
        raise AssertionError(f"unexpected phase: {request.phase}")


def phase_requests(
    backend: ScriptedBackend | ReverseCompletionApprovalBackend,
    phase: Phase,
) -> list[AgentRequest]:
    """Select recorded requests for one phase."""

    return [request for request in backend.requests if request.phase is phase]


def all_manuscripts(workspace: Workspace) -> list[Path]:
    """List committed manuscript files, excluding workspace sentinels."""

    return sorted(
        path
        for path in workspace.path("manuscript").rglob("*")
        if path.is_file() and path.name != ".gitkeep"
    )
