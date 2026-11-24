"""Submit structured provider requests and classify or recover their replies."""

from __future__ import annotations

import asyncio
import contextvars
import json
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from typing import Any, Protocol, TypeVar

import story_writing_agents.backend_reply_decoding as backend_reply_decoding
import story_writing_agents.agent_exchanges.provider_finalization as provider_finalization
import story_writing_agents.provider_invocation_accounting as provider_invocation_accounting
import story_writing_agents.provider_manifest_thread_store as provider_manifest_thread_store
from .agents import LEAD_ORCHESTRATOR
from .provider_schema_projection import require_schema_projection
from .provider_message_catalog import WIRE_VERSION
from .provider_contract_guidance import add_contract_guidance, diagnostic_to_wire
from .structured_output_validation import carry_validation
from .validation_findings import failure_issues
from .structured_output_validation import (
    SchemaRules,
    diagnose_json_text,
    strict_json_value,
)
from .validation_findings import bounded_chunks
from .agent_context import ProviderCallAccounting, ProviderCallKind, ProviderCallState
from .agent_context.contracts.errors import ContextExecutionError, ContextExecutionErrorCode
from .agent_exchanges import AgentExchangeScopeKind
from .agent_exchanges.capture import AgentExchangeCaptureLifecycle, AgentExchangeTerminalCode
from .antigravity_adapter import AntigravityAdapter
from .conversations.session_context import (
    MAX_PROVIDER_INPUT_UTF8_BYTES,
    MAX_PROVIDER_SCHEMA_UTF8_BYTES,
    SessionContextCapacityError,
)
from .conversation_retrieval import (
    ConversationRetrievalAnswerAction,
    ConversationRetrievalReadAction,
    ConversationRetrievalSearchAction,
)
from .errors import BackendProtocolError, ConversationRetrievalSemanticError, SessionIdError
from .models import AgentDefinition, AgentReply, ConversationReply
from .provider_adapter import (
    ProviderAdapter,
    ProviderBinding,
    ProviderInvocation,
    ProviderInvocationError,
    provider_failure_accounting,
)
from .provider_error_artifacts import (
    PROVIDER_ERROR_ARTIFACT_FAILURE_CATEGORY,
    WorkspaceProviderErrorArtifacts,
)
from .reply_trace import ReplyTrace, ReplyTraceOrigin
from .provider_execution_workspace import ProviderExecutionWorkspace
from .writing_artifacts.read_actions import ReferenceAuthorizationError
from .workspace import Workspace
from .provider_adapter_generations import AdapterGeneration


@dataclass(frozen=True, slots=True)
class _DeferredDirectCodexAcceptance:
    """Delay one successful direct-call terminal until workspace exit is safe."""

    accept: Callable[[], None]
    reject: Callable[[], None]


_DEFERRED_DIRECT_CODEX_ACCEPTANCES: contextvars.ContextVar[
    list[_DeferredDirectCodexAcceptance] | None
] = contextvars.ContextVar("deferred_direct_codex_acceptances", default=None)


_StructuredReply = TypeVar("_StructuredReply")


class _StructuredOutputRejected(BackendProtocolError):
    """Identify a returned provider value rejected by structured decoding."""

    def __init__(self, message: str, details: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.validation_details = details


DEFERRED_DIRECT_CODEX_ACCEPTANCES = _DEFERRED_DIRECT_CODEX_ACCEPTANCES
DeferredDirectCodexAcceptance = _DeferredDirectCodexAcceptance
StructuredOutputRejected = _StructuredOutputRejected


class _StructuredAgent(Protocol):
    @property
    def definition(self) -> AgentDefinition: ...

    @property
    def vendor(self) -> str: ...

    @property
    def context_provider_generation(self) -> AdapterGeneration | None: ...

    @property
    def context_provider_binding(self) -> ProviderBinding: ...


_Agent = TypeVar("_Agent", bound=_StructuredAgent)
_AgentContra = TypeVar("_AgentContra", bound=_StructuredAgent, contravariant=True)


class LegacyExchangeBegin(Protocol[_AgentContra]):
    def __call__(
        self, bound_agent: _AgentContra, *, session_id: str,
        debate_id: str | None, conversation_id: str | None, invocation: ProviderInvocation,
    ) -> AgentExchangeCaptureLifecycle | None: ...


class RejectedResponseRecorder(Protocol[_AgentContra]):
    def __call__(
        self, bound_agent: _AgentContra, *, session_id: str, response_text: str,
        debate_id: str | None, conversation_id: str | None,
    ) -> None: ...


class _ExchangeBegin(Protocol[_AgentContra]):
    def __call__(
        self, bound_agent: _AgentContra, *, session_id: str, scope_kind: AgentExchangeScopeKind,
        scope_id: str, invocation: ProviderInvocation,
    ) -> AgentExchangeCaptureLifecycle | None: ...


class _StructuredThreadAccess(Protocol):
    @property
    def workspace(self) -> Workspace: ...


class _StructuredAccountingSink(Protocol):
    @property
    def context_provider_accounting_sink(self) -> Callable[[ProviderCallAccounting], object] | None: ...


class _StructuredInvocationHost(_StructuredThreadAccess, _StructuredAccountingSink, Protocol[_AgentContra]):
    """Access the backend's current provider authorities at each existing call site."""

    @property
    def legacy_provider_exchange_begin(self) -> LegacyExchangeBegin[_AgentContra]: ...

    @property
    def legacy_provider_rejected_response_recorder(self) -> RejectedResponseRecorder[_AgentContra]: ...

    @property
    def legacy_provider_continuation_fingerprint(
        self,
    ) -> Callable[[_AgentContra, ProviderExecutionWorkspace | None], str]: ...

    @property
    def context_provider_exchange_begin(self) -> _ExchangeBegin[_AgentContra]: ...

    @property
    def context_provider_ensure_open(self) -> Callable[[], None]: ...

    @property
    def context_provider_ready_check(self) -> Callable[[str], None]: ...

    @property
    def context_provider_adapter_lookup(self) -> Callable[[str], ProviderAdapter]: ...

    @property
    def context_provider_owned_access_check(self) -> Callable[[Workspace, str], None]: ...

    @property
    def context_provider_activity(self) -> Callable[[str], AbstractAsyncContextManager[int]]: ...

    @property
    def context_provider_timeout_check(self) -> Callable[[BaseException], bool]: ...

    @property
    def context_provider_postsubmission_check(self) -> Callable[[ProviderInvocation], None]: ...


def record_rejected_provider_response_without_replacing_failure(
    self: _StructuredThreadAccess,
    bound_agent: _StructuredAgent,
    *,
    session_id: str,
    response_text: str,
    debate_id: str | None,
    conversation_id: str | None,
) -> None:
    """Best-effort retain one completed response rejected by decoding."""

    try:
        WorkspaceProviderErrorArtifacts(self.workspace).record_rejected_response(
            provider=bound_agent.vendor,
            agent_id=bound_agent.definition.agent_id,
            session_id=session_id,
            debate_id=debate_id,
            conversation_id=conversation_id,
            response_text=response_text,
            failure_category=PROVIDER_ERROR_ARTIFACT_FAILURE_CATEGORY,
        )
    except BaseException:
        pass


def observe_provider_failure(
    self: _StructuredAccountingSink,
    invocation: ProviderInvocation,
    failure: BaseException,
) -> None:
    """Complete one prepared record from validated adapter failure facts."""

    accounting = provider_invocation_accounting.complete_failure_accounting(
        invocation, failure,
    )
    if accounting is None:
        return
    provider_invocation_accounting.observe_accounting(
        accounting, self.context_provider_accounting_sink,
    )


def begin_legacy_agent_exchange(
    self: _StructuredInvocationHost[_Agent],
    bound_agent: _Agent,
    *,
    session_id: str,
    debate_id: str | None,
    conversation_id: str | None,
    invocation: ProviderInvocation,
) -> AgentExchangeCaptureLifecycle | None:
    """Begin capture only when one exact legacy scope is available."""

    if debate_id is not None and conversation_id is None:
        scope_kind = AgentExchangeScopeKind.DEBATE
        scope_id = debate_id
    elif conversation_id is not None and debate_id is None:
        scope_kind = AgentExchangeScopeKind.CONVERSATION
        scope_id = conversation_id
    else:
        return None
    return self.context_provider_exchange_begin(
        bound_agent,
        session_id=session_id,
        scope_kind=scope_kind,
        scope_id=scope_id,
        invocation=invocation,
    )


async def invoke_provider_structured_resilient(
    self: _StructuredInvocationHost[_Agent],
    bound_agent: _Agent,
    *,
    session_id: str,
    schema: Mapping[str, Any],
    payload: Mapping[str, Any],
    decode: Callable[[str], _StructuredReply],
    fallback: Callable[[], _StructuredReply],
    debate_id: str | None = None,
    conversation_id: str | None = None,
    best_effort_taint: bool = False,
    call_kind: ProviderCallKind = ProviderCallKind.UNCLASSIFIED,
    trace_role: str | None = None,
    execution_workspace: ProviderExecutionWorkspace | None = None,
    allow_repair: bool = True,
    repair_schema: Mapping[str, Any] | None = None,
    repair_payload: Mapping[str, Any] | None = None,
    repair_decode: Callable[[str], _StructuredReply] | None = None,
    on_provider_attempt: Callable[[], None] | None = None,
    on_fallback: Callable[[], None] | None = None,
) -> _StructuredReply:
    """Repair one rejected structured value, then return a safe local reply."""

    arguments = {
        "session_id": session_id,
        "debate_id": debate_id,
        "conversation_id": conversation_id,
        "schema": schema,
        "decode": decode,
        "best_effort_taint": best_effort_taint,
        "call_kind": call_kind,
        "execution_workspace": execution_workspace,
    }
    try:
        if on_provider_attempt is not None:
            on_provider_attempt()
        value = await invoke_provider_structured(
            self,
            bound_agent,
            payload=payload,
            **arguments,
        )
        return attach_reply_trace(
            value,
            role=trace_role,
            payload=payload,
            origin=ReplyTraceOrigin.PROVIDER,
        )
    except _StructuredOutputRejected as original_failure:
        original_details = original_failure.validation_details
        if not allow_repair:
            if on_fallback is not None:
                on_fallback()
            return attach_reply_trace(
                attach_validation_details(
                    fallback(), stage_validation_details(
                        original_details, "Original response",
                    ),
                ),
                role=trace_role,
                payload=payload,
                origin=ReplyTraceOrigin.RUNTIME_FALLBACK,
            )
        repaired_payload = dict(
            payload if repair_payload is None else repair_payload
        )
        repaired_payload["structured_response_repair"] = {
            "instruction": (
                "The prior structured response was rejected. Return the answer "
                "again with exactly the required schema fields. Do not add metadata."
            )
        }
    repair_arguments = dict(arguments)
    if repair_schema is not None:
        repair_arguments["schema"] = repair_schema
    if repair_decode is not None:
        repair_arguments["decode"] = repair_decode
    try:
        if on_provider_attempt is not None:
            on_provider_attempt()
        value = await invoke_provider_structured(
            self,
            bound_agent,
            payload=repaired_payload,
            **repair_arguments,
        )
        return attach_reply_trace(
            attach_validation_details(
                value, stage_validation_details(
                    original_details, "Original response",
                ),
            ),
            role=trace_role,
            payload=repaired_payload,
            origin=ReplyTraceOrigin.RECOVERED,
        )
    except _StructuredOutputRejected as repair_failure:
        if on_fallback is not None:
            on_fallback()
        return attach_reply_trace(
            attach_validation_details(
                fallback(), (
                    *stage_validation_details(
                        original_details, "Original response",
                    ),
                    *stage_validation_details(
                        repair_failure.validation_details, "Repair response",
                    ),
                ),
            ),
            role=trace_role,
            payload=repaired_payload,
            origin=ReplyTraceOrigin.RUNTIME_FALLBACK,
        )


def stage_validation_details(
    details: tuple[str, ...], stage: str,
) -> tuple[str, ...]:
    return tuple(
        item.replace("Structured response", stage, 1)
        for item in details
    )


def attach_validation_details(
    value: _StructuredReply, details: tuple[str, ...],
) -> _StructuredReply:
    if type(value) in {
        AgentReply, ConversationReply,
        ConversationRetrievalSearchAction,
        ConversationRetrievalReadAction,
        ConversationRetrievalAnswerAction,
    }:
        return replace(value, validation_details=details)
    return value


def attach_reply_trace(
    value: _StructuredReply,
    *,
    role: str | None,
    payload: Mapping[str, Any],
    origin: ReplyTraceOrigin,
) -> _StructuredReply:
    """Attach content-free construction facts to supported public replies."""

    if role is None or type(value) not in {
        AgentReply,
        ConversationReply,
        ConversationRetrievalSearchAction,
        ConversationRetrievalReadAction,
        ConversationRetrievalAnswerAction,
    }:
        return value
    if type(value) is AgentReply:
        action = value.kind.value
    elif type(value) is ConversationReply:
        action = "reply"
    else:
        action = value.kind.value
    labels = {
        "prior_agent_outcomes": "agent_state",
        "conversation_history": "conversation_history",
        "debate_history": "debate_history",
        "conversation_retrieval_observation": "retrieval_observation",
        "rag_context": "retrieval_index",
        "story_session_context": "story_session",
        "untrusted_attached_references": "writing_artifacts",
    }
    has_current_input = any(
        key in payload and payload[key] not in (None, "", [], (), {})
        for key in ("candidate_text", "user_input_text", "task_instructions")
    )
    context_sources = (("current_input",) if has_current_input else ()) + tuple(
        label
        for key, label in labels.items()
        if key in payload and payload[key] not in (None, [], (), {})
    )
    trace = ReplyTrace(
        role=role,
        action=(
            "none"
            if origin is ReplyTraceOrigin.RUNTIME_FALLBACK
            else action
        ),
        context_sources=context_sources,
        recovered_fields=(
            ()
            if origin is ReplyTraceOrigin.PROVIDER
            else ("structured_reply",)
        ),
        origin=origin,
    )
    return replace(value, trace=trace)


async def invoke_provider_structured(
    self: _StructuredInvocationHost[_Agent],
    bound_agent: _Agent,
    *,
    session_id: str,
    debate_id: str | None = None,
    conversation_id: str | None = None,
    schema: Mapping[str, Any],
    payload: Mapping[str, Any],
    decode: Callable[[str], _StructuredReply],
    best_effort_taint: bool = False,
    call_kind: ProviderCallKind = ProviderCallKind.UNCLASSIFIED,
    execution_workspace: ProviderExecutionWorkspace | None = None,
) -> _StructuredReply:
    """Coordinate one structured call through the selected provider adapter.

    Transport and diagnostic construction remain adapter-owned; this method
    does not inspect or reproduce transport streams. It applies the
    adapter's thread-taint decision, decodes the domain response, and
    persists the validated thread only after successful decoding.
    """

    self.context_provider_ensure_open()
    session = session_id
    self.context_provider_ready_check(session)
    continuation_fingerprint = self.legacy_provider_continuation_fingerprint(
        bound_agent,
        execution_workspace,
    )
    stored = provider_manifest_thread_store.load_manifest_thread(
        self.workspace,
        session,
        bound_agent.definition,
        fingerprint=continuation_fingerprint,
    )
    thread_id = stored["thread_id"] if stored is not None else None

    def taint_thread() -> None:
        if not best_effort_taint:
            provider_manifest_thread_store.taint_manifest_thread(
                self.workspace,
                session,
                bound_agent.definition,
                fingerprint=continuation_fingerprint,
            )
            return
        try:
            provider_manifest_thread_store.taint_manifest_thread(
                self.workspace,
                session,
                bound_agent.definition,
                fingerprint=continuation_fingerprint,
            )
        except BaseException:
            pass

    generation = bound_agent.context_provider_generation
    adapter = (
        self.context_provider_adapter_lookup(bound_agent.vendor)
        if generation is None
        else generation.adapters[bound_agent.vendor]
    )
    if payload.get("protocol_version") == WIRE_VERSION:
        payload = add_contract_guidance(payload, schema)
    component_paths, mandatory_reference_paths = provider_invocation_accounting.provider_component_paths(
        payload,
        call_kind=call_kind,
    )
    bound_native_schema = (AntigravityAdapter.native_output_schema(schema)
                           if bound_agent.vendor == "antigravity" else schema)
    if (bound_agent.definition.agent_id == LEAD_ORCHESTRATOR
            and "agent_action" in schema.get("properties", {})):
        require_schema_projection(
            schema, bound_native_schema, declared_lowering=(
                "integer_singleton_bounds" if bound_agent.vendor == "antigravity" else "identity"
            ),
        )
    invocation = ProviderInvocation(
        session_id=session,
        schema=(
            bound_native_schema
        ),
        payload=payload,
        thread_id=thread_id,
        call_kind=call_kind,
        component_paths=component_paths,
        mandatory_reference_paths=mandatory_reference_paths,
        binding_components=(bound_agent.context_provider_binding.binding_components),
        execution_workspace=execution_workspace,
    )
    if (bound_agent.definition.agent_id == LEAD_ORCHESTRATOR
            and "agent_action" in schema.get("properties", {})):
        if invocation.payload_accounting is None:
            raise ContextExecutionError(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        if (
            invocation.payload_accounting.schema_utf8_bytes > MAX_PROVIDER_SCHEMA_UTF8_BYTES
            or invocation.payload_accounting.serialized_input_utf8_bytes
            > MAX_PROVIDER_INPUT_UTF8_BYTES
        ):
            raise SessionContextCapacityError()
    provider_diagnostic: str | None = None
    provider_cause: BaseException | None = None
    exchange = self.legacy_provider_exchange_begin(
        bound_agent,
        session_id=session,
        debate_id=debate_id,
        conversation_id=conversation_id,
        invocation=invocation,
    )
    try:
        async with self.context_provider_activity(bound_agent.vendor) as auth_generation:
            result = await provider_invocation_accounting.timed_provider_invoke(
                adapter,
                bound_agent.context_provider_binding,
                invocation,
            )
            self.context_provider_postsubmission_check(invocation)
        provider_finalization.capture_provider_return_without_replacing(exchange, result)
    except ProviderInvocationError as exc:
        timed_out = self.context_provider_timeout_check(exc)
        try:
            observe_provider_failure(self, invocation, exc)
            if stored is not None and (exc.taint_existing_thread or timed_out):
                taint_thread()
        finally:
            provider_finalization.capture_provider_failure_without_replacing(
                exchange,
                (
                    AgentExchangeTerminalCode.PROVIDER_AUTHENTICATION_REQUIRED
                    if exc.authentication_failure is not None
                    else AgentExchangeTerminalCode.PROVIDER_TIMED_OUT
                    if timed_out
                    else AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED
                ),
                failure=exc,
            )
        if exc.authentication_failure is not None:
            exc.authentication_failure.auth_generation = auth_generation
            raise exc.authentication_failure from None
        provider_diagnostic = exc.diagnostic
        provider_cause = exc.__cause__
    except asyncio.CancelledError as exc:
        try:
            observe_provider_failure(self, invocation, exc)
            if stored is not None:
                taint_thread()
        finally:
            provider_finalization.capture_cancelled_without_replacing(exchange)
        raise
    except BaseException as exc:
        try:
            observe_provider_failure(self, invocation, exc)
            accounting = provider_failure_accounting(exc)
            if (
                stored is not None
                and accounting.state is ProviderCallState.SUBMITTED
            ):
                taint_thread()
        finally:
            provider_finalization.capture_provider_failure_without_replacing(
                exchange,
                (
                    AgentExchangeTerminalCode.PROVIDER_TIMED_OUT
                    if self.context_provider_timeout_check(exc)
                    else AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED
                ),
            )
        raise
    else:
        accounting = invocation.call_accounting
        provider_invocation_accounting.observe_accounting(
            (
                None
                if accounting is None
                else replace(
                    accounting,
                    state=ProviderCallState.SUBMITTED,
                    actual_transport_utf8_bytes=(
                        result.actual_transport_utf8_bytes
                    ),
                    usage=result.usage,
                    usage_observation=result.usage_observation,
                    elapsed_ns=result.elapsed_ns,
                )
            ),
            self.context_provider_accounting_sink,
        )
    if provider_diagnostic is not None:
        translated = BackendProtocolError(provider_diagnostic)
        if provider_cause is None:
            raise translated from None
        raise translated from provider_cause
    schema_issues: tuple[str, ...] = ()
    try:
        try:
            self.context_provider_postsubmission_check(invocation)
            try:
                reply = decode(result.response_text)
            except (TypeError, ValueError, SessionIdError) as model_failure:
                if not failure_issues(model_failure):
                    raise
                raise carry_validation(
                    BackendProtocolError("provider result violates its property contract"),
                    model_failure,
                ) from model_failure
            if schema:
                try:
                    rules = SchemaRules.declare(
                        schema, maximum_bytes=MAX_PROVIDER_SCHEMA_UTF8_BYTES,
                    )
                except (TypeError, ValueError, RecursionError):
                    schema_issues = diagnose_json_text(result.response_text, schema)
                else:
                    try:
                        rules.normalized_value(strict_json_value(result.response_text, exact_numbers=True))
                    except (TypeError, ValueError, RecursionError) as schema_failure:
                        schema_issues = failure_issues(schema_failure) or (
                            "$: expected a value matching the current result contract",
                        )
            if schema_issues:
                raise BackendProtocolError(
                    "provider structured response has an invalid schema"
                )
        except BackendProtocolError as failure:
            schema_issues = (
                diagnose_json_text(result.response_text, schema)
                if schema
                else ()
            )
            if isinstance(failure, backend_reply_decoding.PhaseReplyConstraintError):
                schema_issues = (*schema_issues, failure.issue)
            try:
                self.legacy_provider_rejected_response_recorder(
                    bound_agent,
                    session_id=session,
                    debate_id=debate_id,
                    conversation_id=conversation_id,
                    response_text=result.response_text,
                )
                if stored is not None:
                    taint_thread()
            finally:
                malformed_precheck = schema_issues in {
                    ("$: malformed JSON",),
                    ("$: duplicate object key",),
                }
                details = bounded_chunks(tuple(diagnostic_to_wire(issue) for issue in (
                    schema_issues or failure_issues(failure) or
                    ("$: provider result decoder failed without a property diagnostic",)
                )), prefix="Structured response")
                provider_finalization.capture_rejected_without_replacing(
                    exchange,
                    AgentExchangeTerminalCode.MALFORMED_STRUCTURED_RESPONSE
                    if malformed_precheck else provider_finalization.structured_rejection_code(failure),
                    validation_details=details,
                )
            raise _StructuredOutputRejected(str(failure), details) from failure
        except (json.JSONDecodeError, UnicodeError):
            self.legacy_provider_rejected_response_recorder(
                bound_agent,
                session_id=session,
                debate_id=debate_id,
                conversation_id=conversation_id,
                response_text=result.response_text,
            )
            raise
        except (ConversationRetrievalSemanticError, ReferenceAuthorizationError) as semantic_failure:
            provider_finalization.capture_rejected_without_replacing(
            exchange,
            AgentExchangeTerminalCode.RESPONSE_SEMANTIC_REJECTED,
            validation_details=bounded_chunks(failure_issues(semantic_failure)),
        )
            raise
        except asyncio.CancelledError:
            provider_finalization.capture_cancelled_without_replacing(exchange)
            raise
        except BaseException as failure:
            try:
                if (
                    stored is not None
                    and provider_failure_accounting(failure).state
                    is ProviderCallState.SUBMITTED
                ):
                    taint_thread()
            finally:
                provider_finalization.capture_runtime_failure_without_replacing(
                    exchange,
                    AgentExchangeTerminalCode.CONTEXT_RUNTIME_FAILED,
                )
            raise
        try:
            self.context_provider_postsubmission_check(invocation)
            provider_manifest_thread_store.store_manifest_thread(
                self.workspace,
                session,
                bound_agent.definition,
                result.thread_id,
                tainted=False,
                fingerprint=continuation_fingerprint,
                require_owned_access=self.context_provider_owned_access_check,
            )
            self.context_provider_postsubmission_check(invocation)
        except asyncio.CancelledError:
            provider_finalization.capture_cancelled_without_replacing(exchange)
            raise
        except BaseException:
            try:
                taint_thread()
            finally:
                provider_finalization.capture_runtime_failure_without_replacing(
                    exchange,
                    AgentExchangeTerminalCode.THREAD_PERSISTENCE_FAILED,
                )
            raise
        deferred = _DEFERRED_DIRECT_CODEX_ACCEPTANCES.get()
        if deferred is None:
            provider_finalization.capture_accepted_without_replacing(exchange)
        else:
            def reject_deferred_acceptance() -> None:
                try:
                    taint_thread()
                finally:
                    provider_finalization.capture_runtime_failure_without_replacing(
                        exchange,
                        AgentExchangeTerminalCode.THREAD_PERSISTENCE_FAILED,
                    )

            deferred.append(
                _DeferredDirectCodexAcceptance(
                    accept=lambda: provider_finalization.capture_accepted_without_replacing(
                        exchange
                    ),
                    reject=reject_deferred_acceptance,
                )
            )
        return reply
    except (json.JSONDecodeError, UnicodeError) as exc:
        try:
            if stored is not None:
                taint_thread()
        finally:
            provider_finalization.capture_rejected_without_replacing(
                exchange,
                AgentExchangeTerminalCode.MALFORMED_STRUCTURED_RESPONSE,
                validation_details=bounded_chunks(
                    (diagnose_json_text(result.response_text, schema) if schema else ()) or ('$: malformed JSON',),
                    prefix='Structured response',
                ),
            )
        diagnostic = (
            "Codex CLI returned malformed structured output"
            if bound_agent.vendor == "codex"
            else "Antigravity CLI returned malformed structured output"
        )
        issues = (
            schema_issues
            or (diagnose_json_text(result.response_text, schema) if schema else ())
            or ("$: malformed JSON",)
        )
        details = bounded_chunks(
            issues,
            prefix="Structured response",
        )
        raise _StructuredOutputRejected(diagnostic, details) from exc
