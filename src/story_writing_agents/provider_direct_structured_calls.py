"""Build and submit one provider debate or conversation request."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol, TypeVar

import story_writing_agents.backend_reply_decoding as backend_reply_decoding
import story_writing_agents.provider_direct_call_wire as provider_direct_call_wire

from .agent_context import ProviderCallKind
from .errors import BackendProtocolError
from .models import (
    AgentReply,
    AgentReplyKind,
    AgentReplyNextPhase,
    AgentRequest,
    ConversationReply,
    ConversationRequest,
    Phase,
    Verdict,
)
from .provider_execution_workspace import ProviderExecutionWorkspace
from .writing_artifacts.read_actions import ReferenceReadInvocation


_Agent = TypeVar("_Agent")
_AgentContra = TypeVar("_AgentContra", contravariant=True)
_Reply = TypeVar("_Reply")


class DirectStructuredInvoke(Protocol[_AgentContra]):
    def __call__(
        self,
        bound_agent: _AgentContra,
        *,
        session_id: str,
        schema: Mapping[str, Any],
        payload: Mapping[str, Any],
        decode: Callable[[str], _Reply],
        fallback: Callable[[], _Reply],
        call_kind: ProviderCallKind,
        trace_role: str,
        execution_workspace: ProviderExecutionWorkspace | None,
        debate_id: str | None = None,
        conversation_id: str | None = None,
    ) -> Awaitable[_Reply]: ...


class DirectReferenceReadInvoke(Protocol[_AgentContra]):
    def __call__(
        self,
        bound_agent: _AgentContra,
        *,
        session_id: str,
        reference_read: ReferenceReadInvocation,
        schema: Mapping[str, Any],
        payload: Mapping[str, Any],
        decode: Callable[[str], _Reply],
        fallback: Callable[[], _Reply],
        provider_name: str,
        call_kind: ProviderCallKind,
        execution_workspace: ProviderExecutionWorkspace | None,
        debate_id: str | None = None,
        conversation_id: str | None = None,
    ) -> Awaitable[_Reply]: ...


class _DirectStructuredHost(Protocol[_AgentContra]):
    @property
    def context_provider_ensure_open(self) -> Callable[[], None]: ...

    @property
    def direct_structured_invoke(self) -> DirectStructuredInvoke[_AgentContra]: ...

    @property
    def direct_reference_read_invoke(self) -> DirectReferenceReadInvoke[_AgentContra]: ...

    @property
    def direct_structured_fallback_message(self) -> str: ...


async def invoke_direct_structured(
    self: _DirectStructuredHost[_Agent],
    bound_agent: _Agent,
    request: AgentRequest,
    *,
    reference_read: ReferenceReadInvocation | None = None,
    _provider_name: str = "Codex",
    execution_workspace: ProviderExecutionWorkspace | None = None,
) -> AgentReply:
    """Invoke Codex with conditional context projection and safe diagnostics.

    The request is serialized without changing the established key set unless an
    included conversation window is present. Provider and staging resources
    retain their established lifecycle; protocol, process, and decode errors
    use provider-safe diagnostics that never reproduce response values.
    """

    self.context_provider_ensure_open()

    def decode(response_text: str) -> AgentReply:
        """Decode and correlate one phase-specific reply without I/O.

        Revision mismatch or missing phase-required content raises
        ``BackendProtocolError``.
        """

        reply = backend_reply_decoding.decode_reply(
            request.phase,
            response_text,
            revision_id=request.revision_id,
            provider_name=_provider_name,
        )
        if request.expected_reviewed_proposal is not None and (
            reply.reviewed_proposal_id, reply.reviewed_proposal_digest
        ) != request.expected_reviewed_proposal:
            raise backend_reply_decoding.PhaseReplyConstraintError("expected_identity")
        if reply.kind is AgentReplyKind.SYNTHESIS:
            allowed_next = {AgentReplyNextPhase.REVIEW_COMPLETION} if request.review_only else {AgentReplyNextPhase.REPAIR, AgentReplyNextPhase.FINALIZATION}
            if reply.next_phase not in allowed_next:
                raise backend_reply_decoding.PhaseReplyConstraintError("next")
        if reply.revision_id != request.revision_id:
            raise BackendProtocolError(
                f"{_provider_name} reply revision_id does not match its request"
            )
        if (
            request.phase
            in {
                Phase.DRAFT,
                Phase.PROPOSE_REVISION,
                Phase.SYNTHESIZE,
            }
            and not reply.content
        ):
            raise BackendProtocolError(
                f"{_provider_name} candidate response must contain text"
            )
        if (
            request.phase is Phase.AUDIT
            and reply.verdict is Verdict.CONFLICT
            and not reply.content
        ):
            raise BackendProtocolError(
                f"{_provider_name} conflict response must contain a critique"
            )
        return reply

    def fallback() -> AgentReply:
        if request.phase is Phase.AUDIT:
            return AgentReply(
                kind=AgentReplyKind.AUDIT,
                revision_id=request.revision_id,
                content=self.direct_structured_fallback_message,
                verdict=Verdict.CONFLICT,
            )
        if request.phase in {
            Phase.DRAFT,
            Phase.PROPOSE_REVISION,
            Phase.SYNTHESIZE,
            Phase.FINALIZE,
        }:
            return AgentReply.deadlock(
                request.revision_id,
                self.direct_structured_fallback_message,
            )
        raise BackendProtocolError("agent request phase has no safe fallback")

    schema = provider_direct_call_wire.phase_output_schema(request.phase, expected_reviewed_proposal=request.expected_reviewed_proposal, review_only=request.review_only)
    payload = provider_direct_call_wire.phase_request_payload(request)
    if reference_read is None:
        return await self.direct_structured_invoke(
            bound_agent,
            session_id=request.session_id,
            debate_id=request.debate_id,
            schema=schema,
            payload=payload,
            decode=decode,
            fallback=fallback,
            call_kind=ProviderCallKind.DEBATE,
            trace_role=request.agent_id,
            execution_workspace=execution_workspace,
        )
    return await self.direct_reference_read_invoke(
        bound_agent,
        session_id=request.session_id,
        debate_id=request.debate_id,
        reference_read=reference_read,
        schema=schema,
        payload=payload,
        decode=decode,
        fallback=fallback,
        provider_name=_provider_name,
        call_kind=ProviderCallKind.DEBATE,
        execution_workspace=execution_workspace,
    )


async def converse_direct_structured(
    self: _DirectStructuredHost[_Agent],
    bound_agent: _Agent,
    request: ConversationRequest,
    *,
    reference_read: ReferenceReadInvocation | None = None,
    _provider_name: str = "Codex",
    execution_workspace: ProviderExecutionWorkspace | None = None,
) -> ConversationReply:
    """Run one Codex turn with the exact conversation payload and schema.

    Provider-thread mutation, cancellation cleanup, and protocol failures
    retain the behavior of the shared structured invocation path.
    """

    self.context_provider_ensure_open()
    schema = provider_direct_call_wire.conversation_output_schema()
    payload = provider_direct_call_wire.conversation_request_payload(request)

    def decode(response_text: str) -> ConversationReply:
        return backend_reply_decoding.decode_conversation_reply(
            request,
            response_text,
            provider_name=_provider_name,
        )

    def fallback() -> ConversationReply:
        return ConversationReply(
            session_id=request.session_id,
            conversation_id=request.conversation_id,
            turn_id=request.turn_id,
            content=self.direct_structured_fallback_message,
        )

    if reference_read is None:
        return await self.direct_structured_invoke(
            bound_agent,
            session_id=request.session_id,
            conversation_id=request.conversation_id,
            schema=schema,
            payload=payload,
            decode=decode,
            fallback=fallback,
            call_kind=ProviderCallKind.CONVERSATION,
            trace_role=request.agent_id,
            execution_workspace=execution_workspace,
        )
    return await self.direct_reference_read_invoke(
        bound_agent,
        session_id=request.session_id,
        conversation_id=request.conversation_id,
        reference_read=reference_read,
        schema=schema,
        payload=payload,
        decode=decode,
        fallback=fallback,
        provider_name=_provider_name,
        call_kind=ProviderCallKind.CONVERSATION,
        execution_workspace=execution_workspace,
    )
