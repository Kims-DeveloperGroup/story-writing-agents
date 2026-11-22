"""Run ordinary bound-agent calls and manage their direct execution workspace."""

from __future__ import annotations

import asyncio
import contextvars
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, AbstractContextManager, asynccontextmanager
from typing import Protocol, Self, TypeVar

import story_writing_agents.provider_thread_records as provider_thread_records

from .agents import ROSTER
from .conversation_retrieval import (
    ConversationRetrievalAction,
    ConversationRetrievalActionHandler,
    ConversationRetrievalObservation,
    ConversationRetrievalReply,
    ConversationRetrievalRequest,
)
from .debug_log import DebugOperation, DebugOutcome, DebugPhase
from .errors import BackendProtocolError
from .models import AgentDefinition, AgentReply, AgentRequest, ConversationReply, ConversationRequest, Phase
from .provider_execution_workspace import ProviderExecutionWorkspace
from .provider_structured_invocation import (
    DEFERRED_DIRECT_CODEX_ACCEPTANCES as _DEFERRED_DIRECT_CODEX_ACCEPTANCES,
    DeferredDirectCodexAcceptance as _DeferredDirectCodexAcceptance,
)
from .workspace import Workspace
from .workspace.session_artifact_binding import SessionArtifactBinding, compose_session_artifact_binding
from .writing_artifacts.read_actions import (
    ReferenceAuthorizationError,
    ReferenceAuthorizationErrorCode,
    ReferenceReadInvocation,
    ReferenceReadRequest,
)


_Agent = TypeVar("_Agent", contravariant=True)
_Request = TypeVar("_Request", contravariant=True)
_Reply = TypeVar("_Reply", covariant=True)


class LogicalProviderDispatch(Protocol[_Agent, _Request, _Reply]):
    def __call__(
        self, bound_agent: _Agent, request: _Request, *,
        reference_read: ReferenceReadInvocation | None,
        execution_workspace: ProviderExecutionWorkspace | None,
    ) -> Awaitable[_Reply]: ...


class LogicalRetrievalDispatch(Protocol[_Agent]):
    def __call__(
        self, bound_agent: _Agent, request: ConversationRetrievalRequest,
        handler_method: Callable[[ConversationRetrievalAction], ConversationRetrievalObservation | ConversationRetrievalReply],
        *, reference_read: ReferenceReadInvocation | None,
        execution_workspace: ProviderExecutionWorkspace | None,
    ) -> Awaitable[ConversationRetrievalReply]: ...


class LogicalTrace(Protocol):
    def __call__(
        self, operation: DebugOperation, phase: DebugPhase, *,
        session_id: str | None = None, debate_id: str | None = None,
        conversation_id: str | None = None, agent_id: str | None = None,
        outcome: DebugOutcome | None = None,
    ) -> None: ...


class _LogicalBackend(Protocol[_Agent]):
    @property
    def workspace(self) -> Workspace: ...

    @property
    def context_provider_ensure_open(self) -> Callable[[], None]: ...

    @property
    def context_provider_ready_check(self) -> Callable[[str], None]: ...

    @property
    def logical_provider_thread_lock(self) -> Callable[[str, str, str], asyncio.Lock]: ...

    @property
    def logical_provider_trace_debug(self) -> LogicalTrace: ...

    @property
    def logical_provider_invoke(self) -> LogicalProviderDispatch[_Agent, AgentRequest, AgentReply]: ...

    @property
    def logical_provider_converse(self) -> LogicalProviderDispatch[_Agent, ConversationRequest, ConversationReply]: ...

    @property
    def logical_provider_converse_with_retrieval(self) -> LogicalRetrievalDispatch[_Agent]: ...

    def logical_thread_disposition(
        self, bound_agent: _Agent, session_id: str, *,
        execution_workspace: ProviderExecutionWorkspace | None,
    ) -> str: ...

    def logical_confirm_execution_profile(
        self, bound_agent: _Agent, *,
        context_rehydrated: bool, thread_disposition: str,
    ) -> None: ...

    def logical_record_debug_error(
        self, operation: DebugOperation, failure: BaseException, *,
        session_id: str | None = None, debate_id: str | None = None,
        conversation_id: str | None = None, agent_id: str | None = None,
        phase: DebugPhase = DebugPhase.FAILED, outcome: DebugOutcome = DebugOutcome.FAILED,
    ) -> None: ...


class _LogicalBoundAgent(Protocol):
    @property
    def backend(self) -> _LogicalBackend[Self]: ...

    @property
    def definition(self) -> AgentDefinition: ...

    @property
    def vendor(self) -> str: ...

    def logical_reference_call_use(
        self, reference_read: ReferenceReadInvocation | None,
    ) -> AbstractContextManager[None]: ...

    def logical_reference_thread_lock(
        self, lock: asyncio.Lock, reference_read: ReferenceReadInvocation | None,
    ) -> AbstractAsyncContextManager[None]: ...

    def logical_terminate_reference_before_provider(
        self, reference_read: ReferenceReadInvocation | None, failure: BaseException,
    ) -> None: ...

    @property
    def logical_force_terminate_reference(self) -> Callable[[ReferenceReadInvocation, ReferenceAuthorizationErrorCode], None]: ...

    def logical_validate_conversation(self, request: ConversationRequest) -> ConversationRequest: ...

    def logical_validate_retrieval(self, request: ConversationRetrievalRequest) -> ConversationRetrievalRequest: ...

    def logical_compose_direct_codex_binding(self, session_id: str) -> SessionArtifactBinding: ...

    @property
    def logical_open_provider_execution_workspace(
        self,
    ) -> Callable[[Self, SessionArtifactBinding], AbstractContextManager[ProviderExecutionWorkspace]]: ...


@asynccontextmanager
async def direct_execution_workspace(
    self: _LogicalBoundAgent,
    session_id: str,
    reference_read: ReferenceReadInvocation | None,
) -> AsyncIterator[ProviderExecutionWorkspace]:
    """Open direct-call authority and retire pre-entry references on loss."""

    entered = False
    body_failed = False
    generation_lost = False
    deferred: list[_DeferredDirectCodexAcceptance] = []
    token: contextvars.Token[list[_DeferredDirectCodexAcceptance] | None] | None = (
        None
    )
    try:
        binding = await asyncio.to_thread(
            self.logical_compose_direct_codex_binding,
            session_id,
        )
        binding = binding.transfer_to_current_thread()
        with self.logical_open_provider_execution_workspace(self, binding) as workspace:
            entered = True
            token = _DEFERRED_DIRECT_CODEX_ACCEPTANCES.set(deferred)
            try:
                yield workspace
            except BaseException:
                body_failed = True
                try:
                    workspace.require_current()
                except BaseException:
                    generation_lost = True
                raise
            finally:
                _DEFERRED_DIRECT_CODEX_ACCEPTANCES.reset(token)
                token = None
    except BaseException as failure:
        for acceptance in deferred:
            if body_failed and not generation_lost:
                acceptance.accept()
            else:
                acceptance.reject()
        raise
    else:
        for acceptance in deferred:
            acceptance.accept()


@asynccontextmanager
async def direct_codex_execution_workspace(
    self: _LogicalBoundAgent,
    session_id: str,
    reference_read: ReferenceReadInvocation | None,
) -> AsyncIterator[ProviderExecutionWorkspace]:
    """Compatibility delegate for the generalized direct-call authority."""

    async with direct_execution_workspace(
        self,
        session_id,
        reference_read,
    ) as workspace:
        yield workspace


def preflight_direct_codex_workspace(self: _LogicalBoundAgent, session_id: str) -> None:
    """Reject unavailable logical work before opening a physical workspace."""

    self.backend.context_provider_ensure_open()
    self.backend.context_provider_ready_check(session_id)


def compose_direct_codex_binding(self: _LogicalBoundAgent, session_id: str) -> SessionArtifactBinding:
    """Run blocking direct-call preflight and binding off the event loop."""

    preflight_direct_codex_workspace(self, session_id)
    return compose_session_artifact_binding(
        self.backend.workspace.root,
        session_id,
    )


def defer_direct_codex_acceptance(
    accept: Callable[[], None],
    reject: Callable[[], None],
) -> None:
    """Run a logical terminal action only after direct authority validates."""

    deferred = _DEFERRED_DIRECT_CODEX_ACCEPTANCES.get()
    if deferred is None:
        accept()
        return
    deferred.append(_DeferredDirectCodexAcceptance(accept=accept, reject=reject))


async def invoke(
    self: _LogicalBoundAgent,
    request: AgentRequest,
    *,
    reference_read: ReferenceReadInvocation | None = None,
) -> AgentReply:
    """Invoke the selected vendor while serializing its persistent thread."""

    if self.vendor in {"codex", "antigravity"}:
        if type(request) is not AgentRequest:
            failure = TypeError("request must be an AgentRequest")
            self.logical_terminate_reference_before_provider(reference_read, failure)
            raise failure
        if request.agent_id != self.definition.agent_id:
            if reference_read is not None:
                self.logical_force_terminate_reference(
                    reference_read,
                    ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH,
                )
            raise BackendProtocolError(
                "request was routed through the wrong bound agent: "
                f"{request.agent_id} != {self.definition.agent_id}"
            )
        if reference_read is not None:
            if (
                type(request.phase) is not Phase
                or type(request.round_number) is not int
                or request.round_number < 0
                or request.agent_id not in ROSTER
            ):
                self.logical_force_terminate_reference(
                    reference_read,
                    ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
                )
                raise BackendProtocolError(
                    "agent request is invalid for reference routing"
                )
            validated_reference_read_invocation(
                self,
                reference_read,
                agent_id=request.agent_id,
                phase=request.phase.value,
                round_number=request.round_number,
            )
        async with direct_execution_workspace(
            self,
            request.session_id,
            reference_read,
        ) as workspace:
            return await invoke_logical(
                self,
                request,
                reference_read=reference_read,
                execution_workspace=workspace,
            )
    return await invoke_logical(
        self,
        request,
        reference_read=reference_read,
        execution_workspace=None,
    )


async def invoke_logical(
    self: _LogicalBoundAgent,
    request: AgentRequest,
    *,
    reference_read: ReferenceReadInvocation | None,
    execution_workspace: ProviderExecutionWorkspace | None,
) -> AgentReply:
    """Implement the unchanged public invocation under private authority."""

    with self.logical_reference_call_use(reference_read):
        if reference_read is not None and type(request) is not AgentRequest:
            failure = TypeError("request must be an AgentRequest")
            self.logical_terminate_reference_before_provider(reference_read, failure)
            raise failure
        if request.agent_id != self.definition.agent_id:
            if reference_read is not None:
                self.logical_force_terminate_reference(
                    reference_read,
                    ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH,
                )
            raise BackendProtocolError(
                "request was routed through the wrong bound agent: "
                f"{request.agent_id} != {self.definition.agent_id}"
            )
        if reference_read is not None:
            if (
                type(request.phase) is not Phase
                or type(request.round_number) is not int
                or request.round_number < 0
                or request.agent_id not in ROSTER
            ):
                self.logical_force_terminate_reference(
                    reference_read,
                    ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
                )
                raise BackendProtocolError(
                    "agent request is invalid for reference routing"
                )
            reference_read = validated_reference_read_invocation(
                self,
                reference_read,
                agent_id=request.agent_id,
                phase=request.phase.value,
                round_number=request.round_number,
            )
        try:
            lock = self.backend.logical_provider_thread_lock(
                request.session_id,
                self.vendor,
                self.definition.agent_id,
            )
        except BaseException as exc:
            self.logical_terminate_reference_before_provider(reference_read, exc)
            raise
        async with self.logical_reference_thread_lock(lock, reference_read):
            self.backend.logical_provider_trace_debug(
                DebugOperation.PROVIDER_INVOCATION,
                DebugPhase.STARTED,
                session_id=request.session_id,
                debate_id=request.debate_id,
                agent_id=self.definition.agent_id,
            )
            thread_disposition = self.backend.logical_thread_disposition(
                self,
                request.session_id,
                execution_workspace=execution_workspace,
            )
            try:
                reply = await self.backend.logical_provider_invoke(
                    self,
                    request,
                    reference_read=reference_read,
                    execution_workspace=execution_workspace,
                )
            except asyncio.CancelledError as exc:
                self.backend.logical_record_debug_error(
                    DebugOperation.PROVIDER_INVOCATION,
                    exc,
                    session_id=request.session_id,
                    debate_id=request.debate_id,
                    agent_id=self.definition.agent_id,
                    phase=DebugPhase.CANCELLED,
                    outcome=DebugOutcome.INTERRUPTED,
                )
                raise
            except BaseException as exc:
                self.backend.logical_record_debug_error(
                    DebugOperation.PROVIDER_INVOCATION,
                    exc,
                    session_id=request.session_id,
                    debate_id=request.debate_id,
                    agent_id=self.definition.agent_id,
                )
                raise
            defer_direct_codex_acceptance(
                lambda: (
                    self.backend.logical_confirm_execution_profile(
                        self,
                        context_rehydrated=(
                            request.story_session_context is not None
                        ),
                        thread_disposition=thread_disposition,
                    ),
                    self.backend.logical_provider_trace_debug(
                        DebugOperation.PROVIDER_INVOCATION,
                        DebugPhase.SUCCEEDED,
                        session_id=request.session_id,
                        debate_id=request.debate_id,
                        agent_id=self.definition.agent_id,
                        outcome=DebugOutcome.COMPLETED,
                    ),
                ),
                lambda: None,
            )
            return reply


async def converse(
    self: _LogicalBoundAgent,
    request: ConversationRequest,
    *,
    reference_read: ReferenceReadInvocation | None = None,
) -> ConversationReply:
    """Run one validated visible conversation turn on this agent thread."""

    if self.vendor in {"codex", "antigravity"}:
        try:
            validated = self.logical_validate_conversation(request)
        except BaseException as failure:
            self.logical_terminate_reference_before_provider(reference_read, failure)
            raise
        if reference_read is not None:
            validated_reference_read_invocation(
                self,
                reference_read,
                agent_id=validated.agent_id,
                phase="conversation",
                round_number=None,
            )
        async with direct_execution_workspace(
            self,
            validated.session_id,
            reference_read,
        ) as workspace:
            return await converse_logical(
                self,
                validated,
                reference_read=reference_read,
                execution_workspace=workspace,
            )
    return await converse_logical(
        self,
        request,
        reference_read=reference_read,
        execution_workspace=None,
    )


async def converse_logical(
    self: _LogicalBoundAgent,
    request: ConversationRequest,
    *,
    reference_read: ReferenceReadInvocation | None,
    execution_workspace: ProviderExecutionWorkspace | None,
) -> ConversationReply:
    """Implement one conversation under optional internal authority."""

    with self.logical_reference_call_use(reference_read):
        try:
            validated = self.logical_validate_conversation(request)
        except BaseException as exc:
            self.logical_terminate_reference_before_provider(reference_read, exc)
            raise
        if reference_read is not None:
            reference_read = validated_reference_read_invocation(
                self,
                reference_read,
                agent_id=validated.agent_id,
                phase="conversation",
                round_number=None,
            )
        try:
            lock = self.backend.logical_provider_thread_lock(
                validated.session_id,
                self.vendor,
                self.definition.agent_id,
            )
        except BaseException as exc:
            self.logical_terminate_reference_before_provider(reference_read, exc)
            raise
        async with self.logical_reference_thread_lock(lock, reference_read):
            self.backend.logical_provider_trace_debug(
                DebugOperation.PROVIDER_INVOCATION,
                DebugPhase.STARTED,
                session_id=validated.session_id,
                conversation_id=validated.conversation_id,
                agent_id=self.definition.agent_id,
            )
            thread_disposition = self.backend.logical_thread_disposition(
                self,
                validated.session_id,
                execution_workspace=execution_workspace,
            )
            try:
                reply = await self.backend.logical_provider_converse(
                    self,
                    validated,
                    reference_read=reference_read,
                    execution_workspace=execution_workspace,
                )
            except asyncio.CancelledError as exc:
                self.backend.logical_record_debug_error(
                    DebugOperation.PROVIDER_INVOCATION,
                    exc,
                    session_id=validated.session_id,
                    conversation_id=validated.conversation_id,
                    agent_id=self.definition.agent_id,
                    phase=DebugPhase.CANCELLED,
                    outcome=DebugOutcome.INTERRUPTED,
                )
                raise
            except BaseException as exc:
                self.backend.logical_record_debug_error(
                    DebugOperation.PROVIDER_INVOCATION,
                    exc,
                    session_id=validated.session_id,
                    conversation_id=validated.conversation_id,
                    agent_id=self.definition.agent_id,
                )
                raise
            defer_direct_codex_acceptance(
                lambda: (
                    self.backend.logical_confirm_execution_profile(
                        self,
                        context_rehydrated=(
                            validated.story_session_context is not None
                        ),
                        thread_disposition=thread_disposition,
                    ),
                    self.backend.logical_provider_trace_debug(
                        DebugOperation.PROVIDER_INVOCATION,
                        DebugPhase.SUCCEEDED,
                        session_id=validated.session_id,
                        conversation_id=validated.conversation_id,
                        agent_id=self.definition.agent_id,
                        outcome=DebugOutcome.COMPLETED,
                    ),
                ),
                lambda: None,
            )
            return reply


async def converse_with_retrieval(
    self: _LogicalBoundAgent,
    request: ConversationRetrievalRequest,
    handler: ConversationRetrievalActionHandler,
    *,
    reference_read: ReferenceReadInvocation | None = None,
) -> ConversationRetrievalReply:
    """Run one bounded retrieval loop under the persistent-thread lock.

    ``request`` and the strictly synchronous runtime ``handler`` are
    validated before provider work. One lock covers all provider calls and
    handler invocations; a valid answer returns its correlated reply, while
    provider, protocol, handler, timeout, and cancellation failures
    propagate with incomplete observation state tainted when required.
    """

    if self.vendor in {"codex", "antigravity"}:
        try:
            validated = self.logical_validate_retrieval(request)
        except BaseException as failure:
            self.logical_terminate_reference_before_provider(reference_read, failure)
            raise
        if reference_read is not None:
            validated_reference_read_invocation(
                self,
                reference_read,
                agent_id=validated.agent_id,
                phase="conversation_retrieval",
                round_number=None,
            )
        async with direct_execution_workspace(
            self,
            validated.session_id,
            reference_read,
        ) as workspace:
            return await converse_with_retrieval_logical(
                self,
                validated,
                handler,
                reference_read=reference_read,
                execution_workspace=workspace,
            )
    return await converse_with_retrieval_logical(
        self,
        request,
        handler,
        reference_read=reference_read,
        execution_workspace=None,
    )


async def converse_with_retrieval_logical(
    self: _LogicalBoundAgent,
    request: ConversationRetrievalRequest,
    handler: ConversationRetrievalActionHandler,
    *,
    reference_read: ReferenceReadInvocation | None,
    execution_workspace: ProviderExecutionWorkspace | None,
) -> ConversationRetrievalReply:
    """Implement one retrieval loop under optional internal authority."""

    with self.logical_reference_call_use(reference_read):
        try:
            validated = self.logical_validate_retrieval(request)
            handler_method = getattr(
                handler,
                "handle_conversation_retrieval_action",
                None,
            )
            if handler_method is None or not callable(handler_method):
                raise TypeError(
                    "handler must provide handle_conversation_retrieval_action()"
                )
            handler_call = getattr(handler_method, "__call__", None)
            if (
                inspect.iscoroutinefunction(handler_method)
                or inspect.isasyncgenfunction(handler_method)
                or inspect.iscoroutinefunction(handler_call)
                or inspect.isasyncgenfunction(handler_call)
            ):
                raise TypeError(
                    "conversation retrieval handler must be synchronous"
                )
        except BaseException as exc:
            self.logical_terminate_reference_before_provider(reference_read, exc)
            raise
        if reference_read is not None:
            reference_read = validated_reference_read_invocation(
                self,
                reference_read,
                agent_id=validated.agent_id,
                phase="conversation_retrieval",
                round_number=None,
            )
        try:
            lock = self.backend.logical_provider_thread_lock(
                validated.session_id,
                self.vendor,
                self.definition.agent_id,
            )
        except BaseException as exc:
            self.logical_terminate_reference_before_provider(reference_read, exc)
            raise
        async with self.logical_reference_thread_lock(lock, reference_read):
            self.backend.logical_provider_trace_debug(
                DebugOperation.PROVIDER_INVOCATION,
                DebugPhase.STARTED,
                session_id=validated.session_id,
                conversation_id=validated.conversation_id,
                agent_id=self.definition.agent_id,
            )
            thread_disposition = self.backend.logical_thread_disposition(
                self,
                validated.session_id,
                execution_workspace=execution_workspace,
            )
            try:
                reply = await self.backend.logical_provider_converse_with_retrieval(
                    self,
                    validated,
                    handler_method,
                    reference_read=reference_read,
                    execution_workspace=execution_workspace,
                )
            except asyncio.CancelledError as exc:
                self.backend.logical_record_debug_error(
                    DebugOperation.PROVIDER_INVOCATION,
                    exc,
                    session_id=validated.session_id,
                    conversation_id=validated.conversation_id,
                    agent_id=self.definition.agent_id,
                    phase=DebugPhase.CANCELLED,
                    outcome=DebugOutcome.INTERRUPTED,
                )
                raise
            except BaseException as exc:
                self.backend.logical_record_debug_error(
                    DebugOperation.PROVIDER_INVOCATION,
                    exc,
                    session_id=validated.session_id,
                    conversation_id=validated.conversation_id,
                    agent_id=self.definition.agent_id,
                )
                raise
            defer_direct_codex_acceptance(
                lambda: (
                    self.backend.logical_confirm_execution_profile(
                        self,
                        context_rehydrated=(
                            validated.story_session_context is not None
                        ),
                        thread_disposition=thread_disposition,
                    ),
                    self.backend.logical_provider_trace_debug(
                        DebugOperation.PROVIDER_INVOCATION,
                        DebugPhase.SUCCEEDED,
                        session_id=validated.session_id,
                        conversation_id=validated.conversation_id,
                        agent_id=self.definition.agent_id,
                        outcome=DebugOutcome.COMPLETED,
                    ),
                ),
                lambda: None,
            )
            return reply


def validated_reference_read_invocation(
    self: _LogicalBoundAgent,
    reference_read: ReferenceReadInvocation,
    *,
    agent_id: str,
    phase: str,
    round_number: int | None,
) -> ReferenceReadInvocation:
    """Validate one synchronous exact coordinator before provider work."""

    request = getattr(reference_read, "request", None)
    if type(request) is not ReferenceReadRequest:
        raise TypeError("reference_read must expose a ReferenceReadRequest")
    methods = (
        "handle_reference_read_action",
        "record_result_received",
        "accept",
        "terminate",
    )
    for name in methods:
        method = getattr(reference_read, name, None)
        if method is None or not callable(method):
            raise TypeError(f"reference_read must provide synchronous {name}()")
        method_call = getattr(method, "__call__", None)
        if (
            inspect.iscoroutinefunction(method)
            or inspect.isasyncgenfunction(method)
            or inspect.iscoroutinefunction(method_call)
            or inspect.isasyncgenfunction(method_call)
        ):
            raise TypeError(f"reference_read {name}() must be synchronous")
    definition = self.definition
    if (
        type(definition) is not AgentDefinition
        or definition.agent_id not in ROSTER
        or definition.agent_id != agent_id
        or not isinstance(definition.backend, str)
        or not definition.backend
        or self.vendor != definition.backend
        or not isinstance(definition.fingerprint, str)
        or provider_thread_records.PROVIDER_FINGERPRINT_PATTERN.fullmatch(definition.fingerprint) is None
    ):
        self.logical_force_terminate_reference(
            reference_read,
            ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
        )
        raise BackendProtocolError(
            "bound agent definition is invalid for reference routing"
        )
    correlation = request.correlation
    if (
        correlation.agent_id != agent_id
        or correlation.phase != phase
        or correlation.round_number != round_number
    ):
        self.logical_force_terminate_reference(
            reference_read,
            ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH,
        )
        raise ReferenceAuthorizationError(
            ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH
        )
    return reference_read
