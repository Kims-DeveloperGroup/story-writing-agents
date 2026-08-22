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
    AgentDefinition,
    AgentReply,
    AgentRequest,
    Phase,
    SPECIALIST_IDS,
    SessionAccessState,
    SessionDurableState,
    SessionLifecycleState,
    Workspace,
)
from story_writing_agents.agents import CORE_DOCUMENTS


HandlerResult: TypeAlias = AgentReply | BaseException | Awaitable[AgentReply]
RequestHandler: TypeAlias = Callable[[AgentRequest], HandlerResult]


def initialized_workspace(test_case: TestCase) -> Workspace:
    """Create an initialized test workspace with explicit core inputs.

    Workspace initialization deliberately does not author core writing
    artifacts. Tests that exercise story-agent work supply those inputs as
    fixtures instead of relying on legacy generated placeholders.
    """

    temporary = tempfile.TemporaryDirectory()
    test_case.addCleanup(temporary.cleanup)
    workspace = Workspace(temporary.name).initialize()
    for filename in CORE_DOCUMENTS:
        # These are deliberately unprovenanced fixture inputs. Runtime code
        # cannot create canonical writing artifacts through generic Workspace
        # writes; repository-focused tests publish their own verified bytes.
        workspace.path(f"core_documents/{filename}").write_text(
            f"test input for {filename}\n",
            encoding="utf-8",
        )
    return workspace


def core_snapshot(workspace: Workspace) -> dict[str, str]:
    """Return raw fixture-only bytes for the full allowlisted core state."""

    return {
        filename: workspace.path(f"core_documents/{filename}").read_text(
            encoding="utf-8"
        )
        for filename in CORE_DOCUMENTS
    }


class _DelegatingBoundAgent:
    """Bind one fake backend to exactly one runtime agent definition."""

    def __init__(
        self,
        backend: BindingBackendMixin,
        definition: AgentDefinition,
    ) -> None:
        self.backend = backend
        self.definition = definition

    async def invoke(self, request: AgentRequest) -> AgentReply:
        if request.agent_id != self.definition.agent_id:
            raise AssertionError(
                "request was routed through the wrong bound agent: "
                f"{request.agent_id} != {self.definition.agent_id}"
            )
        self.backend.bound_invocations.append(
            (self.definition, request)
        )
        return await self.backend.invoke(request)


class BindingBackendMixin:
    """Give deterministic test backends binding and lifecycle contracts."""

    definitions: list[AgentDefinition]
    bound_invocations: list[tuple[AgentDefinition, AgentRequest]]

    async def bind(self, definition: AgentDefinition) -> _DelegatingBoundAgent:
        if not hasattr(self, "definitions"):
            self.definitions = []
        if not hasattr(self, "bound_invocations"):
            self.bound_invocations = []
        self.definitions.append(definition)
        return _DelegatingBoundAgent(self, definition)

    async def invoke(self, request: AgentRequest) -> AgentReply:
        raise NotImplementedError

    def validate_provider_configuration(self, _session_id: str) -> bool:
        """Return the exact provider-ready fact used by neutral test doubles."""

        return True

    def get_session_lifecycle(self, session_id: str) -> SessionLifecycleState:
        """Return deterministic ready access already held by this test double."""

        return self._test_lifecycle(session_id, held=True)

    def acquire_session_access(self, session_id: str) -> SessionLifecycleState:
        """Return deterministic ready access held by this test double."""

        return self._test_lifecycle(session_id, held=True)

    def release_session_access(self, session_id: str) -> SessionLifecycleState:
        """Return deterministic ready state after releasing test-double access."""

        return self._test_lifecycle(session_id, held=False)

    @staticmethod
    def _test_lifecycle(
        session_id: str,
        *,
        held: bool,
    ) -> SessionLifecycleState:
        """Build one exact immutable lifecycle result without external work."""

        return SessionLifecycleState(
            session_id=session_id,
            durable_state=SessionDurableState.READY,
            access_state=(
                SessionAccessState.HELD_HERE
                if held
                else SessionAccessState.CLAIMABLE
            ),
            owner_hostname="test-runtime" if held else None,
            owner_pid=1 if held else None,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )


class ScriptedBackend(BindingBackendMixin):
    """Record requests and answer them with a deterministic handler."""

    def __init__(self, handler: RequestHandler) -> None:
        self.handler = handler
        self.definitions: list[AgentDefinition] = []
        self.bound_invocations: list[
            tuple[AgentDefinition, AgentRequest]
        ] = []
        self.requests: list[AgentRequest] = []

    async def invoke(self, request: AgentRequest) -> AgentReply:
        self.requests.append(request)
        try:
            result = self.handler(request)
        except AssertionError as exc:
            neutral_unhandled_phase = str(exc) in {
                str(request.phase),
                f"unexpected phase: {request.phase}",
            }
            if request.phase is not Phase.DRAFT or not neutral_unhandled_phase:
                raise
            # Supplied drafts are no longer treated as already-authored
            # candidates. Most workflow tests are concerned with a later
            # phase, so their neutral backend still performs the required
            # execution-specialist authoring hop without duplicating it in
            # every handler.
            result = AgentReply.candidate(
                request.revision_id,
                request.user_payload or "agent-authored candidate",
            )
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, BaseException):
            raise result
        return result

    def close(self) -> None:
        """Match the concrete backend lifecycle for CLI substitution tests."""


class ReverseCompletionApprovalBackend(BindingBackendMixin):
    """Prove audits overlap while completing them opposite roster order."""

    def __init__(self) -> None:
        self.definitions: list[AgentDefinition] = []
        self.bound_invocations: list[
            tuple[AgentDefinition, AgentRequest]
        ] = []
        self.requests: list[AgentRequest] = []
        self.arrivals: list[str] = []
        self.completions: list[str] = []
        self._all_arrived = asyncio.Event()
        self._execution_done = asyncio.Event()
        self._character_done = asyncio.Event()

    async def invoke(self, request: AgentRequest) -> AgentReply:
        self.requests.append(request)
        if request.phase is Phase.DRAFT:
            return AgentReply.candidate(request.revision_id, request.user_payload)
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
