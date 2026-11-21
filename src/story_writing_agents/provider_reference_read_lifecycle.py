"""Load authorized provider references and finish their read lifecycle."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol, TypeVar

import story_writing_agents.provider_manifest_thread_store as provider_manifest_thread_store

from .agent_context import ProviderCallKind
from .agent_context.invocation.provider import ContextReferencePreludeRequest, ContextReferencePreludeResult
from .errors import BackendProtocolError
from .models import AgentDefinition
from .provider_execution_workspace import ProviderExecutionWorkspace
from .provider_request_mapping import project_direct_payload
from .workspace import Workspace
from .writing_artifacts.read_actions import (
    ReferenceAuthorizationError,
    ReferenceAuthorizationErrorCode,
    ReferenceReadAction,
    ReferenceReadActionKind,
    ReferenceReadInvocation,
    ReferenceReadResult,
)


class _ReferenceReadAgent(Protocol):
    @property
    def definition(self) -> AgentDefinition: ...


_Agent = TypeVar("_Agent", bound=_ReferenceReadAgent)
_AgentContra = TypeVar("_AgentContra", bound=_ReferenceReadAgent, contravariant=True)
_StructuredReply = TypeVar("_StructuredReply")


class ReferenceReadContextLoad(Protocol[_AgentContra]):
    def __call__(
        self,
        bound_agent: _AgentContra,
        *,
        session_id: str,
        debate_id: str | None,
        conversation_id: str | None,
        reference_read: ReferenceReadInvocation,
        provider_name: str,
        execution_workspace: ProviderExecutionWorkspace | None,
        continuation_fingerprint: str,
    ) -> Awaitable[ContextReferencePreludeResult]: ...


class ReferenceReadStructuredInvoke(Protocol[_AgentContra]):
    def __call__(
        self,
        bound_agent: _AgentContra,
        *,
        session_id: str,
        debate_id: str | None,
        conversation_id: str | None,
        schema: Mapping[str, Any],
        payload: Mapping[str, Any],
        decode: Callable[[str], _StructuredReply],
        fallback: Callable[[], _StructuredReply],
        best_effort_taint: bool,
        call_kind: ProviderCallKind,
        trace_role: str,
        on_fallback: Callable[[], None],
        execution_workspace: ProviderExecutionWorkspace | None,
    ) -> Awaitable[_StructuredReply]: ...


class _ReferenceReadWorkspace(Protocol):
    @property
    def workspace(self) -> Workspace: ...


class _ReferenceReadHost(_ReferenceReadWorkspace, Protocol[_AgentContra]):
    """Resolve the original backend operations when the read needs them."""

    @property
    def reference_read_continuation_fingerprint(
        self,
    ) -> Callable[[_AgentContra, ProviderExecutionWorkspace | None], str]: ...

    @property
    def reference_read_load_context(self) -> ReferenceReadContextLoad[_AgentContra]: ...

    @property
    def reference_read_invoke_structured(self) -> ReferenceReadStructuredInvoke[_AgentContra]: ...


async def invoke_codex_after_reference_read(
    self: _ReferenceReadHost[_Agent],
    bound_agent: _Agent,
    *,
    session_id: str,
    debate_id: str | None = None,
    conversation_id: str | None = None,
    reference_read: ReferenceReadInvocation,
    schema: Mapping[str, Any],
    payload: Mapping[str, Any],
    decode: Callable[[str], _StructuredReply],
    fallback: Callable[[], _StructuredReply],
    provider_name: str,
    call_kind: ProviderCallKind,
    execution_workspace: ProviderExecutionWorkspace | None = None,
) -> _StructuredReply:
    """Run one mandatory read prelude and one substantive provider call."""

    continuation_fingerprint = self.reference_read_continuation_fingerprint(
        bound_agent,
        execution_workspace,
    )
    prelude = await self.reference_read_load_context(
        bound_agent,
        session_id=session_id,
        debate_id=debate_id,
        conversation_id=conversation_id,
        reference_read=reference_read,
        provider_name=provider_name,
        execution_workspace=execution_workspace,
        continuation_fingerprint=continuation_fingerprint,
    )
    context = prelude.context
    framed_payload = dict(payload)
    framed_payload["untrusted_attached_references"] = project_direct_payload({"untrusted_writing_artifact_context": context})["untrusted_attached_references"]
    used_local_fallback = False

    def reject_fallback_references() -> None:
        """Retire references when no provider response was accepted."""

        nonlocal used_local_fallback
        used_local_fallback = True
        terminate_reference_read_without_replacing(
            reference_read,
            ReferenceAuthorizationErrorCode.PROVIDER_FAILURE,
        )

    try:
        reply = await self.reference_read_invoke_structured(
            bound_agent,
            session_id=session_id,
            debate_id=debate_id,
            conversation_id=conversation_id,
            schema=schema,
            payload=framed_payload,
            decode=decode,
            fallback=fallback,
            best_effort_taint=True,
            call_kind=call_kind,
            trace_role=bound_agent.definition.agent_id,
            on_fallback=reject_fallback_references,
            execution_workspace=execution_workspace,
        )
    except BaseException as exc:
        fail_reference_read_without_replacing(
            self,
            reference_read,
            exc,
            session_id=session_id,
            definition=bound_agent.definition,
            provider_failure=True,
            continuation_fingerprint=continuation_fingerprint,
        )
        raise
    if not used_local_fallback:
        complete_reference_read(
            self,
            reference_read,
            session_id=session_id,
            definition=bound_agent.definition,
            continuation_fingerprint=continuation_fingerprint,
            execution_workspace=execution_workspace,
        )
    return reply


async def load_authorized_reference_context(
    self: _ReferenceReadWorkspace,
    bound_agent: _ReferenceReadAgent,
    *,
    session_id: str,
    debate_id: str | None = None,
    conversation_id: str | None = None,
    reference_read: ReferenceReadInvocation,
    provider_name: str,
    execution_workspace: ProviderExecutionWorkspace | None = None,
    continuation_fingerprint: str,
    prelude_request: ContextReferencePreludeRequest | None = None,
) -> ContextReferencePreludeResult:
    """Read the already-authorized references and transiently frame their bytes."""

    request = reference_read.request
    del debate_id, conversation_id, provider_name
    try:
        action = ReferenceReadAction(
            ReferenceReadActionKind.READ_WRITING_ARTIFACTS,
            request.correlation,
            request.references,
        )
        result = reference_read.handle_reference_read_action(action)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            del result
            raise BackendProtocolError(
                "reference read handler returned an awaitable"
            )
        if inspect.isasyncgen(result):
            del result
            raise BackendProtocolError(
                "reference read handler returned an async generator"
            )
        if type(result) is not ReferenceReadResult or result.request != request:
            if type(result) is ReferenceReadResult:
                result.close()
            raise BackendProtocolError(
                "reference read handler returned an invalid result"
            )
        with result:
            prelude = ContextReferencePreludeResult.from_read_result(
                result, request=prelude_request,
            )
        if execution_workspace is not None:
            execution_workspace.require_current()
        return prelude
    except BaseException as exc:
        fail_reference_read_without_replacing(
            self,
            reference_read,
            exc,
            session_id=session_id,
            definition=bound_agent.definition,
            provider_failure=False,
            continuation_fingerprint=continuation_fingerprint,
        )
        raise


def complete_reference_read(
    self: _ReferenceReadWorkspace,
    reference_read: ReferenceReadInvocation,
    *,
    session_id: str,
    definition: AgentDefinition,
    continuation_fingerprint: str,
    execution_workspace: ProviderExecutionWorkspace | None,
) -> None:
    """Record and accept one structurally valid substantive result."""

    try:
        for name in ("record_result_received", "accept"):
            if execution_workspace is not None:
                execution_workspace.require_current()
            outcome = getattr(reference_read, name)()
            if inspect.isawaitable(outcome):
                if inspect.iscoroutine(outcome):
                    outcome.close()
                del outcome
                raise BackendProtocolError(
                    f"reference read {name}() returned an awaitable"
                )
            if inspect.isasyncgen(outcome):
                del outcome
                raise BackendProtocolError(
                    f"reference read {name}() returned an async generator"
                )
            if outcome is not None:
                raise BackendProtocolError(
                    f"reference read {name}() returned a value"
                )
    except BaseException as exc:
        fail_reference_read_without_replacing(
            self,
            reference_read,
            exc,
            session_id=session_id,
            definition=definition,
            provider_failure=False,
            continuation_fingerprint=continuation_fingerprint,
        )
        raise


def fail_reference_read_without_replacing(
    self: _ReferenceReadWorkspace,
    reference_read: ReferenceReadInvocation,
    failure: BaseException,
    *,
    session_id: str,
    definition: AgentDefinition,
    provider_failure: bool,
    continuation_fingerprint: str,
    taint: bool = True,
) -> None:
    """Best-effort terminate and taint while preserving a primary failure."""

    if isinstance(failure, asyncio.CancelledError):
        outcome = ReferenceAuthorizationErrorCode.CANCELLED
    elif isinstance(failure, ReferenceAuthorizationError):
        outcome = failure.code
    elif provider_failure:
        outcome = ReferenceAuthorizationErrorCode.PROVIDER_FAILURE
    else:
        outcome = ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE
    if taint:
        taint_reference_thread_without_replacing(
            self,
            reference_read,
            session_id=session_id,
            definition=definition,
            continuation_fingerprint=continuation_fingerprint,
        )


def terminate_reference_read_without_replacing(
    reference_read: ReferenceReadInvocation,
    outcome: ReferenceAuthorizationErrorCode,
) -> None:
    """Best-effort terminate a coordinator without masking its caller."""

    try:
        result = reference_read.terminate(outcome)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            del result
        elif inspect.isasyncgen(result):
            del result
    except BaseException:
        pass


def taint_reference_thread_without_replacing(
    self: _ReferenceReadWorkspace,
    reference_read: ReferenceReadInvocation,
    *,
    session_id: str,
    definition: AgentDefinition,
    continuation_fingerprint: str,
) -> None:
    """Best-effort taint a thread after a successful persisted prelude."""

    del reference_read
    try:
        provider_manifest_thread_store.taint_manifest_thread(
            self.workspace,
            session_id,
            definition,
            fingerprint=continuation_fingerprint,
        )
    except BaseException:
        pass
