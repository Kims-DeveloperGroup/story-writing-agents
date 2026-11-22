"""Route provider invocations through the bound agent's selected vendor."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar

from .conversation_retrieval import (
    ConversationRetrievalAction,
    ConversationRetrievalObservation,
    ConversationRetrievalReply,
    ConversationRetrievalRequest,
)
from .errors import ConfigurationError
from .models import AgentReply, AgentRequest, ConversationReply, ConversationRequest
from .provider_adapter import ProviderAdapter
from .provider_execution_workspace import ProviderExecutionWorkspace
from .writing_artifacts.read_actions import ReferenceAuthorizationErrorCode, ReferenceReadInvocation


class _RoutedAgent(Protocol):
    @property
    def vendor(self) -> str: ...


_Agent = TypeVar("_Agent", bound=_RoutedAgent)
_AgentContra = TypeVar("_AgentContra", bound=_RoutedAgent, contravariant=True)
_Request = TypeVar("_Request", contravariant=True)
_Reply = TypeVar("_Reply", covariant=True)
_RetrievalHandler = Callable[
    [ConversationRetrievalAction],
    ConversationRetrievalObservation | ConversationRetrievalReply,
]


class ProviderRoute(Protocol[_AgentContra, _Request, _Reply]):
    def __call__(
        self,
        bound_agent: _AgentContra,
        request: _Request,
        *,
        reference_read: ReferenceReadInvocation | None,
        execution_workspace: ProviderExecutionWorkspace | None,
    ) -> Awaitable[_Reply]: ...


class NamedProviderRoute(Protocol[_AgentContra, _Request, _Reply]):
    def __call__(
        self,
        bound_agent: _AgentContra,
        request: _Request,
        *,
        reference_read: ReferenceReadInvocation | None,
        execution_workspace: ProviderExecutionWorkspace | None,
        _provider_name: str = "Codex",
    ) -> Awaitable[_Reply]: ...


class RetrievalRoute(Protocol[_AgentContra]):
    def __call__(
        self,
        bound_agent: _AgentContra,
        request: ConversationRetrievalRequest,
        handler_method: _RetrievalHandler,
        *,
        reference_read: ReferenceReadInvocation | None,
        execution_workspace: ProviderExecutionWorkspace | None,
    ) -> Awaitable[ConversationRetrievalReply]: ...


class NamedRetrievalRoute(Protocol[_AgentContra]):
    def __call__(
        self,
        bound_agent: _AgentContra,
        request: ConversationRetrievalRequest,
        handler_method: _RetrievalHandler,
        *,
        reference_read: ReferenceReadInvocation | None,
        execution_workspace: ProviderExecutionWorkspace | None,
        _provider_name: str = "Codex",
    ) -> Awaitable[ConversationRetrievalReply]: ...


class _RoutingHost(Protocol[_AgentContra]):
    @property
    def context_provider_adapter_lookup(self) -> Callable[[str], ProviderAdapter]: ...

    @property
    def routing_terminate_reference(
        self,
    ) -> Callable[[ReferenceReadInvocation, ReferenceAuthorizationErrorCode], None]: ...

    @property
    def routing_invoke_codex(self) -> NamedProviderRoute[_AgentContra, AgentRequest, AgentReply]: ...

    @property
    def routing_converse_codex(self) -> NamedProviderRoute[_AgentContra, ConversationRequest, ConversationReply]: ...

    @property
    def routing_converse_with_retrieval_codex(self) -> NamedRetrievalRoute[_AgentContra]: ...

    @property
    def routing_invoke_antigravity(self) -> ProviderRoute[_AgentContra, AgentRequest, AgentReply]: ...

    @property
    def routing_converse_antigravity(self) -> ProviderRoute[_AgentContra, ConversationRequest, ConversationReply]: ...

    @property
    def routing_converse_with_retrieval_antigravity(self) -> RetrievalRoute[_AgentContra]: ...


async def invoke_provider(
    self: _RoutingHost[_Agent],
    bound_agent: _Agent,
    request: AgentRequest,
    *,
    reference_read: ReferenceReadInvocation | None = None,
    execution_workspace: ProviderExecutionWorkspace | None = None,
) -> AgentReply:
    """Route one debate turn through the selected private adapter."""

    if bound_agent.vendor == "codex":
        return await self.routing_invoke_codex(
            bound_agent,
            request,
            reference_read=reference_read,
            execution_workspace=execution_workspace,
        )
    if bound_agent.vendor == "antigravity":
        return await self.routing_invoke_antigravity(
            bound_agent,
            request,
            reference_read=reference_read,
            execution_workspace=execution_workspace,
        )
    if reference_read is not None:
        self.routing_terminate_reference(
            reference_read,
            ReferenceAuthorizationErrorCode.PROVIDER_FAILURE,
        )
    raise ConfigurationError(
        f"agent backend is not implemented: {bound_agent.vendor}"
    )


async def invoke_antigravity(
    self: _RoutingHost[_Agent],
    bound_agent: _Agent,
    request: AgentRequest,
    *,
    reference_read: ReferenceReadInvocation | None = None,
    execution_workspace: ProviderExecutionWorkspace | None = None,
) -> AgentReply:
    """Invoke Antigravity through the shared structured-turn contract."""

    return await self.routing_invoke_codex(
        bound_agent,
        request,
        reference_read=reference_read,
        _provider_name="Antigravity",
        execution_workspace=execution_workspace,
    )


async def converse_provider(
    self: _RoutingHost[_Agent],
    bound_agent: _Agent,
    request: ConversationRequest,
    *,
    reference_read: ReferenceReadInvocation | None = None,
    execution_workspace: ProviderExecutionWorkspace | None = None,
) -> ConversationReply:
    """Route one conversation turn through the selected private adapter."""

    try:
        self.context_provider_adapter_lookup(bound_agent.vendor)
    except ConfigurationError:
        if reference_read is not None:
            self.routing_terminate_reference(
                reference_read,
                ReferenceAuthorizationErrorCode.PROVIDER_FAILURE,
            )
        raise
    if bound_agent.vendor == "codex":
        return await self.routing_converse_codex(
            bound_agent,
            request,
            reference_read=reference_read,
            execution_workspace=execution_workspace,
        )
    if bound_agent.vendor == "antigravity":
        return await self.routing_converse_antigravity(
            bound_agent,
            request,
            reference_read=reference_read,
            execution_workspace=execution_workspace,
        )
    raise ConfigurationError(
        f"agent backend is not implemented: {bound_agent.vendor}"
    )


async def converse_antigravity(
    self: _RoutingHost[_Agent],
    bound_agent: _Agent,
    request: ConversationRequest,
    *,
    reference_read: ReferenceReadInvocation | None = None,
    execution_workspace: ProviderExecutionWorkspace | None = None,
) -> ConversationReply:
    """Run one Antigravity turn through the shared conversation contract."""

    return await self.routing_converse_codex(
        bound_agent,
        request,
        reference_read=reference_read,
        _provider_name="Antigravity",
        execution_workspace=execution_workspace,
    )


async def converse_with_retrieval_provider(
    self: _RoutingHost[_Agent],
    bound_agent: _Agent,
    request: ConversationRetrievalRequest,
    handler_method: Callable[
        [ConversationRetrievalAction],
        ConversationRetrievalObservation | ConversationRetrievalReply,
    ],
    *,
    reference_read: ReferenceReadInvocation | None = None,
    execution_workspace: ProviderExecutionWorkspace | None = None,
) -> ConversationRetrievalReply:
    """Route one retrieval loop through the selected private adapter."""

    try:
        self.context_provider_adapter_lookup(bound_agent.vendor)
    except ConfigurationError:
        if reference_read is not None:
            self.routing_terminate_reference(
                reference_read,
                ReferenceAuthorizationErrorCode.PROVIDER_FAILURE,
            )
        raise
    if bound_agent.vendor == "codex":
        return await self.routing_converse_with_retrieval_codex(
            bound_agent,
            request,
            handler_method,
            reference_read=reference_read,
            execution_workspace=execution_workspace,
        )
    if bound_agent.vendor == "antigravity":
        return await self.routing_converse_with_retrieval_antigravity(
            bound_agent,
            request,
            handler_method,
            reference_read=reference_read,
            execution_workspace=execution_workspace,
        )
    raise ConfigurationError(
        f"agent backend is not implemented: {bound_agent.vendor}"
    )


async def converse_with_retrieval_antigravity(
    self: _RoutingHost[_Agent],
    bound_agent: _Agent,
    request: ConversationRetrievalRequest,
    handler_method: Callable[
        [ConversationRetrievalAction],
        ConversationRetrievalObservation | ConversationRetrievalReply,
    ],
    *,
    reference_read: ReferenceReadInvocation | None = None,
    execution_workspace: ProviderExecutionWorkspace | None = None,
) -> ConversationRetrievalReply:
    """Run the bounded retrieval loop through Antigravity."""

    return await self.routing_converse_with_retrieval_codex(
        bound_agent,
        request,
        handler_method,
        reference_read=reference_read,
        _provider_name="Antigravity",
        execution_workspace=execution_workspace,
    )
