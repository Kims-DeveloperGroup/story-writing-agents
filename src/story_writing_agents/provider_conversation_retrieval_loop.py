"""Run the shared provider conversation retrieval loop."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import Any, Protocol, TypeVar

import story_writing_agents.backend_reply_decoding as backend_reply_decoding
import story_writing_agents.conversation_retrieval_wire as conversation_retrieval_wire
import story_writing_agents.provider_manifest_thread_store as provider_manifest_thread_store
import story_writing_agents.provider_retrieval_handler_outcomes as provider_retrieval_handler_outcomes
from .provider_message_catalog import RETRIEVAL_ACTIONS
from .provider_request_mapping import project_direct_payload
from .agent_context import ProviderCallKind
from .agent_context.invocation.provider import ContextReferencePreludeResult
from .conversation_retrieval import (
    CONVERSATION_RETRIEVAL_POLICY,
    ConversationRetrievalAction,
    ConversationRetrievalActionKind,
    ConversationRetrievalAnswerAction,
    ConversationRetrievalObservation,
    ConversationRetrievalReadAction,
    ConversationRetrievalReadObservation,
    ConversationRetrievalReply,
    ConversationRetrievalRequest,
    ConversationRetrievalSearchAction,
    ConversationRetrievalSearchObservation,
    serialize_conversation_retrieval_observation,
)
from .errors import BackendProtocolError
from .models import AgentDefinition
from .reply_trace import ReplyTrace, ReplyTraceOrigin
from .provider_execution_workspace import ProviderExecutionWorkspace
from .writing_artifacts.read_actions import (
    ReferenceAuthorizationErrorCode,
    ReferenceReadInvocation,
)
from .workspace import Workspace
from .provider_reference_read_lifecycle import (
    complete_reference_read,
    fail_reference_read_without_replacing,
    terminate_reference_read_without_replacing,
)


class _RetrievalAgent(Protocol):
    @property
    def definition(self) -> AgentDefinition: ...


_Agent = TypeVar("_Agent", bound=_RetrievalAgent)
_AgentContra = TypeVar("_AgentContra", bound=_RetrievalAgent, contravariant=True)


class RetrievalReferenceLoad(Protocol[_AgentContra]):
    def __call__(
        self, bound_agent: _AgentContra, *, session_id: str, conversation_id: str,
        reference_read: ReferenceReadInvocation, provider_name: str,
        execution_workspace: ProviderExecutionWorkspace | None,
        continuation_fingerprint: str,
    ) -> Awaitable[ContextReferencePreludeResult]: ...


class RetrievalStructuredInvoke(Protocol[_AgentContra]):
    def __call__(
        self,
        bound_agent: _AgentContra,
        *,
        session_id: str,
        conversation_id: str,
        schema: Mapping[str, Any],
        payload: Mapping[str, Any],
        decode: Callable[[str], ConversationRetrievalAction],
        fallback: Callable[[], ConversationRetrievalAction],
        best_effort_taint: bool,
        call_kind: ProviderCallKind,
        trace_role: str,
        allow_repair: bool,
        repair_schema: Mapping[str, Any],
        repair_payload: Mapping[str, Any],
        repair_decode: Callable[[str], ConversationRetrievalAction],
        on_provider_attempt: Callable[[], None],
        on_fallback: Callable[[], None],
        execution_workspace: ProviderExecutionWorkspace | None,
    ) -> Awaitable[ConversationRetrievalAction]: ...


class _RetrievalWorkspace(Protocol):
    @property
    def workspace(self) -> Workspace: ...


class _RetrievalHost(_RetrievalWorkspace, Protocol[_AgentContra]):
    @property
    def context_provider_ensure_open(self) -> Callable[[], None]: ...

    @property
    def legacy_provider_continuation_fingerprint(
        self,
    ) -> Callable[[_AgentContra, ProviderExecutionWorkspace | None], str]: ...

    @property
    def retrieval_reference_load(self) -> RetrievalReferenceLoad[_AgentContra]: ...

    @property
    def retrieval_structured_invoke(self) -> RetrievalStructuredInvoke[_AgentContra]: ...

    @property
    def retrieval_fallback_message(self) -> str: ...


async def converse_with_retrieval(
    self: _RetrievalHost[_Agent],
    bound_agent: _Agent,
    request: ConversationRetrievalRequest,
    handler_method: Callable[
        [ConversationRetrievalAction],
        ConversationRetrievalObservation | ConversationRetrievalReply,
    ],
    *,
    reference_read: ReferenceReadInvocation | None = None,
    _provider_name: str = "Codex",
    execution_workspace: ProviderExecutionWorkspace | None = None,
) -> ConversationRetrievalReply:
    """Run the fixed Codex action loop without inter-call lock changes.

    Initial and continuation payloads use the accepted exact schemas. Each
    decoded action reaches the synchronous handler once; observations are
    canonically framed for the next call and a terminal handler reply is
    returned. Provider metadata is the only mutation here. Once a
    nonterminal action is accepted, every later failure or cancellation
    taints the compatible thread before the original failure is re-raised.
    """

    self.context_provider_ensure_open()
    continuation_fingerprint = self.legacy_provider_continuation_fingerprint(
        bound_agent,
        execution_workspace,
    )
    reference_context: tuple[Mapping[str, object], ...] | None = None
    if reference_read is not None:
        prelude = await self.retrieval_reference_load(
            bound_agent,
            session_id=request.session_id,
            conversation_id=request.conversation_id,
            reference_read=reference_read,
            provider_name=_provider_name,
            execution_workspace=execution_workspace,
            continuation_fingerprint=continuation_fingerprint,
        )
        reference_context = prelude.context
    observation_frame: str | None = None
    after_read = False
    pending_observation = False
    reference_provider_failure = False
    trace_sources: list[str] = []
    diagnostic_chunks: list[str] = []
    recovered = False
    physical_provider_calls = 0
    used_local_fallback = False

    def count_provider_call() -> None:
        nonlocal physical_provider_calls
        physical_provider_calls += 1

    def reject_fallback_references() -> None:
        """Keep local fallback delivery separate from reference acceptance."""

        nonlocal used_local_fallback
        used_local_fallback = True
        if reference_read is not None:
            terminate_reference_read_without_replacing(
                reference_read,
                ReferenceAuthorizationErrorCode.PROVIDER_FAILURE,
            )

    try:
        for action_index in range(
            1,
            CONVERSATION_RETRIEVAL_POLICY.max_provider_calls + 1,
        ):
            remaining_provider_calls = (
                CONVERSATION_RETRIEVAL_POLICY.max_provider_calls
                - physical_provider_calls
            )
            if remaining_provider_calls < 1:
                break
            allowed_actions = conversation_retrieval_wire.allowed_actions(
                action_index=action_index,
                has_observation=observation_frame is not None,
                after_read=after_read,
                final_provider_call=remaining_provider_calls == 1,
            )
            if observation_frame is None:
                call_kind = ProviderCallKind.CONVERSATION_RETRIEVAL_INITIAL
                payload = conversation_retrieval_wire.initial_payload(
                    request,
                    allowed_actions=allowed_actions,
                )
                if reference_context is not None:
                    payload["untrusted_attached_references"] = project_direct_payload({"untrusted_writing_artifact_context": reference_context})["untrusted_attached_references"]
                    reference_context = None
            else:
                call_kind = ProviderCallKind.CONVERSATION_RETRIEVAL_CONTINUATION
                payload = conversation_retrieval_wire.continuation_payload(
                    request,
                    action_index=action_index,
                    allowed_actions=allowed_actions,
                    observation=observation_frame,
                )
            repair_allowed_actions = (
                (ConversationRetrievalActionKind.ANSWER,)
                if remaining_provider_calls == 2
                else allowed_actions
            )
            repair_payload = dict(payload)
            repair_control = dict(repair_payload["conversation_retrieval_control"])
            repair_control["allowed_action_types"] = [
                RETRIEVAL_ACTIONS[action.value] for action in repair_allowed_actions
            ]
            repair_payload["conversation_retrieval_control"] = repair_control
            reference_provider_failure = True
            action = await self.retrieval_structured_invoke(
                bound_agent,
                session_id=request.session_id,
                conversation_id=request.conversation_id,
                schema=conversation_retrieval_wire.retrieval_action_schema(
                    allowed_actions=allowed_actions,
                ),
                payload=payload,
                decode=lambda response_text: (
                    backend_reply_decoding.decode_conversation_retrieval_action(
                        response_text,
                        request=request,
                        action_index=action_index,
                        provider_name=_provider_name,
                        allowed_actions=allowed_actions,
                    )
                ),
                fallback=lambda: ConversationRetrievalAnswerAction(
                    kind=ConversationRetrievalActionKind.ANSWER,
                    session_id=request.session_id,
                    conversation_id=request.conversation_id,
                    turn_id=request.turn_id,
                    action_index=action_index,
                    content=self.retrieval_fallback_message,
                    citation_ids=(),
                ),
                best_effort_taint=reference_read is not None,
                call_kind=call_kind,
                trace_role=request.agent_id,
                allow_repair=remaining_provider_calls > 1,
                repair_schema=conversation_retrieval_wire.retrieval_action_schema(
                    allowed_actions=repair_allowed_actions,
                ),
                repair_payload=repair_payload,
                repair_decode=lambda response_text: (
                    backend_reply_decoding.decode_conversation_retrieval_action(
                        response_text,
                        request=request,
                        action_index=action_index,
                        provider_name=_provider_name,
                        allowed_actions=repair_allowed_actions,
                    )
                ),
                on_provider_attempt=count_provider_call,
                on_fallback=reject_fallback_references,
                execution_workspace=execution_workspace,
            )
            diagnostic_chunks.extend(action.validation_details)
            if action.trace is not None:
                trace_sources.extend(action.trace.context_sources)
                recovered = recovered or (
                    action.trace.origin is ReplyTraceOrigin.RECOVERED
                )
                if type(action) is ConversationRetrievalAnswerAction:
                    origin = action.trace.origin
                    recovered_fields = action.trace.recovered_fields
                    if recovered and origin is ReplyTraceOrigin.PROVIDER:
                        origin = ReplyTraceOrigin.RECOVERED
                        recovered_fields = ("structured_reply",)
                    action = replace(
                        action,
                        trace=ReplyTrace(
                            role=request.agent_id,
                            action=action.trace.action,
                            context_sources=tuple(
                                dict.fromkeys(trace_sources)
                            ),
                            recovered_fields=recovered_fields,
                            origin=origin,
                        ),
                    )
            reference_provider_failure = False
            if type(action) in {
                ConversationRetrievalSearchAction,
                ConversationRetrievalReadAction,
            }:
                pending_observation = True

            outcome = handler_method(action)
            if inspect.isawaitable(outcome):
                if inspect.iscoroutine(outcome):
                    outcome.close()
                del outcome
                raise BackendProtocolError(
                    "conversation retrieval handler returned an awaitable"
                )
            if inspect.isasyncgen(outcome):
                del outcome
                raise BackendProtocolError(
                    "conversation retrieval handler returned an async generator"
                )

            if type(action) is ConversationRetrievalAnswerAction:
                if type(outcome) is not ConversationRetrievalReply:
                    raise BackendProtocolError(
                        "conversation retrieval answer handler returned an "
                        "invalid reply"
                    )
                try:
                    outcome = provider_retrieval_handler_outcomes.canonicalize_reply(
                        outcome, action, diagnostic_chunks
                    )
                except Exception as exc:
                    raise BackendProtocolError(
                        "conversation retrieval answer handler returned an "
                        "invalid reply"
                    ) from exc
                if (
                    outcome.session_id != request.session_id
                    or outcome.conversation_id != request.conversation_id
                    or outcome.turn_id != request.turn_id
                ):
                    raise BackendProtocolError(
                        "conversation retrieval handler reply correlation changed"
                    )
                pending_observation = False
                if reference_read is not None and not used_local_fallback:
                    complete_reference_read(
                        self,
                        reference_read,
                        session_id=request.session_id,
                        definition=bound_agent.definition,
                        continuation_fingerprint=continuation_fingerprint,
                        execution_workspace=execution_workspace,
                    )
                return outcome

            if type(action) is ConversationRetrievalSearchAction:
                expected_observation_type = ConversationRetrievalSearchObservation
            else:
                assert type(action) is ConversationRetrievalReadAction
                expected_observation_type = ConversationRetrievalReadObservation
            if type(outcome) is not expected_observation_type:
                raise BackendProtocolError(
                    "conversation retrieval handler returned an invalid observation"
                )
            try:
                outcome = provider_retrieval_handler_outcomes.canonicalize_observation(
                    outcome
                )
            except Exception as exc:
                raise BackendProtocolError(
                    "conversation retrieval handler returned an invalid observation"
                ) from exc
            if (
                outcome.session_id != request.session_id
                or outcome.conversation_id != request.conversation_id
                or outcome.turn_id != request.turn_id
                or outcome.action_index != action.action_index
            ):
                raise BackendProtocolError(
                    "conversation retrieval handler observation correlation changed"
                )
            observation_frame = serialize_conversation_retrieval_observation(
                outcome
            )
            if type(action) is ConversationRetrievalReadAction:
                after_read = True
        reference_provider_failure = True
        raise BackendProtocolError(
            "conversation retrieval ended without a terminal answer"
        )
    except BaseException as exc:
        if pending_observation:
            taint_retrieval_thread_without_replacing_failure(
                self,
                request.session_id,
                bound_agent.definition,
                fingerprint=continuation_fingerprint,
            )
        if reference_read is not None:
            fail_reference_read_without_replacing(
                self,
                reference_read,
                exc,
                session_id=request.session_id,
                definition=bound_agent.definition,
                provider_failure=reference_provider_failure,
                continuation_fingerprint=continuation_fingerprint,
            )
        raise


def taint_retrieval_thread_without_replacing_failure(
    self: _RetrievalWorkspace,
    session_id: str,
    definition: AgentDefinition,
    *,
    fingerprint: str | None = None,
) -> None:
    """Best-effort taint an incomplete retrieval thread without masking failure."""

    try:
        provider_manifest_thread_store.taint_manifest_thread(
            self.workspace,
            session_id,
            definition,
            fingerprint=fingerprint,
        )
    except BaseException:
        pass
