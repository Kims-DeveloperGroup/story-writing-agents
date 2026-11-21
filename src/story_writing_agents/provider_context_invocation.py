"""Physical submission and continuation recovery for contextual provider calls."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from typing import Protocol, TypeVar

import story_writing_agents.agent_exchanges.provider_finalization as provider_finalization
import story_writing_agents.provider_context_thread_store as provider_context_thread_store
import story_writing_agents.provider_invocation_accounting as provider_invocation_accounting
import story_writing_agents.provider_thread_records as provider_thread_records

from .agent_context import ProviderCallAccounting, ProviderCallKind, ProviderCallState
from .agent_context.contracts.errors import ContextExecutionError, ContextExecutionErrorCode
from .agent_context.invocation.provider import (
    BoundContextProviderCallRequest,
    ContextContinuationRejection,
    ContextProviderCallRequest,
    ContextProviderCallResult,
    ContextProviderFailureFacts,
    record_context_provider_failure,
)
from .agent_context.manifest import ContextContinuationIdentity, ContextScope
from .agent_exchanges import AgentExchangeScopeKind
from .agent_exchanges.capture import AgentExchangeCaptureLifecycle, AgentExchangeTerminalCode
from .agents import LEAD_ORCHESTRATOR
from .antigravity_adapter import AntigravityAdapter
from .config import ConfigManager
from .conversations.session_context import (
    MAX_PROVIDER_INPUT_UTF8_BYTES,
    MAX_PROVIDER_PAYLOAD_UTF8_BYTES,
    MAX_PROVIDER_SCHEMA_UTF8_BYTES,
    SessionContextCapacityError,
)
from .errors import BackendProtocolError, ProviderAuthenticationRequiredError
from .interactive_settings import read_provider_route_generations
from .models import AgentDefinition
from .provider_adapter import (
    AgentExecutionProfile,
    ProviderAdapter,
    ProviderBinding,
    ProviderInvocation,
    ProviderInvocationError,
    ProviderResult,
)
from .provider_adapter_generations import AdapterGeneration
from .provider_auth_recovery import validate_provider_replay
from .provider_execution_workspace import ProviderExecutionWorkspace
from .provider_message_catalog import WIRE_VERSION
from .provider_schema_projection import require_schema_projection
from .workspace import Workspace


class _ContextInvocationAgent(Protocol):
    @property
    def definition(self) -> AgentDefinition: ...

    @property
    def vendor(self) -> str: ...

    @property
    def execution_profile(self) -> AgentExecutionProfile | None: ...

    @property
    def context_provider_generation(self) -> AdapterGeneration | None: ...

    @property
    def context_provider_binding(self) -> ProviderBinding: ...


_InvocationAgent = TypeVar("_InvocationAgent", bound=_ContextInvocationAgent)
_InvocationAgentContra = TypeVar(
    "_InvocationAgentContra", bound=_ContextInvocationAgent, contravariant=True,
)


class ContextExchangeBegin(Protocol[_InvocationAgentContra]):
    """Begin a provider exchange with the backend's concrete agent type."""

    def __call__(
        self,
        bound_agent: _InvocationAgentContra,
        *,
        session_id: str,
        scope_kind: AgentExchangeScopeKind,
        scope_id: str,
        invocation: ProviderInvocation,
        diagnostic_call_id: str | None = None,
    ) -> AgentExchangeCaptureLifecycle | None: ...


class _ContextThreadStoreAccess(Protocol):
    @property
    def workspace(self) -> Workspace: ...

    @property
    def context_provider_ready_check(self) -> Callable[[str], None]: ...

    @property
    def context_provider_owned_access_check(self) -> Callable[[Workspace, str], None]: ...


class _ContextInvocationHost(_ContextThreadStoreAccess, Protocol[_InvocationAgentContra]):
    """Read the backend's authorities at the original point of use."""

    @property
    def backend_overrides(self) -> Mapping[str, str]: ...

    @property
    def context_provider_ensure_open(self) -> Callable[[], None]: ...

    @property
    def context_provider_adapter_lookup(self) -> Callable[[str], ProviderAdapter]: ...

    @property
    def context_provider_active_generation(self) -> AdapterGeneration: ...

    @property
    def context_provider_activity(self) -> Callable[[str], AbstractAsyncContextManager[int]]: ...

    @property
    def context_provider_accounting_sink(self) -> Callable[[ProviderCallAccounting], object] | None: ...

    @property
    def context_provider_timeout_check(self) -> Callable[[BaseException], bool]: ...

    @property
    def context_provider_postsubmission_check(self) -> Callable[[ProviderInvocation], None]: ...

    @property
    def context_provider_exchange_begin(self) -> ContextExchangeBegin[_InvocationAgentContra]: ...

    @property
    def context_provider_effective_fingerprints(
        self,
    ) -> Callable[[_InvocationAgentContra, str, ProviderExecutionWorkspace | None], tuple[str, str]]: ...


def _begin_context_agent_exchange(
    self: _ContextInvocationHost[_InvocationAgent],
    bound_agent: _InvocationAgent,
    *,
    scope: ContextScope,
    invocation: ProviderInvocation,
    diagnostic_call_id: str | None = None,
) -> AgentExchangeCaptureLifecycle | None:
    """Begin capture from one already validated contextual scope."""

    try:
        scope_kind = AgentExchangeScopeKind(scope.subject_kind.value)
    except BaseException:
        return None
    return self.context_provider_exchange_begin(
        bound_agent,
        session_id=scope.session_id,
        scope_kind=scope_kind,
        scope_id=scope.subject_id,
        invocation=invocation,
        diagnostic_call_id=diagnostic_call_id,
    )


def _taint_context_thread_without_replacing(
    self: _ContextThreadStoreAccess,
    scope: ContextScope,
    provider_id: str,
    definition_fingerprint: str,
    policy_fingerprint: str,
) -> None:
    """Best-effort invalidate one scoped continuation."""

    try:
        provider_context_thread_store.taint_context_thread(
            self.workspace,
            scope,
            provider_id,
            definition_fingerprint,
            policy_fingerprint,
            require_owned_access=self.context_provider_owned_access_check,
        )
    except BaseException:
        pass


def _context_replay_validator(
    self: _ContextInvocationHost[_InvocationAgent],
    bound_agent: _InvocationAgent,
    scope: ContextScope,
    definition_fingerprint: str,
    policy_fingerprint: str,
    expected_thread_id: str | None,
) -> Callable[[], bool]:
    """Capture provider-owned replay inputs before the original submission."""

    try:
        baseline = ConfigManager(self.workspace).load_configuration_baseline()
        routes = read_provider_route_generations(
            self.workspace, baseline.configuration,
            provider_overrides=self.backend_overrides,
        )
        generation = self.context_provider_active_generation
        binding_generation = bound_agent.context_provider_generation
        profile = bound_agent.execution_profile
        root_stat = self.workspace.root.stat()
        root_identity = (root_stat.st_dev, root_stat.st_ino)
    except Exception:
        return lambda: False

    def unchanged() -> bool:
        self.context_provider_ensure_open()
        self.context_provider_ready_check(scope.session_id)
        current_root = self.workspace.root.stat()
        return bool(
            (current_root.st_dev, current_root.st_ino) == root_identity
            and ConfigManager(self.workspace).load_configuration_baseline() == baseline
            and baseline.configuration == generation.configuration
            and read_provider_route_generations(
                self.workspace, baseline.configuration,
                provider_overrides=self.backend_overrides,
            ) == routes
            and self.context_provider_active_generation is generation
            and bound_agent.context_provider_generation is binding_generation
            and not generation.retired and not generation.closed
            and bound_agent.execution_profile == profile
            and provider_context_thread_store.load_context_thread(
                self.workspace,
                scope, bound_agent.vendor,
                definition_fingerprint,
                policy_fingerprint,
                require_ready=self.context_provider_ready_check,
            ) == expected_thread_id
        )

    return unchanged


def effective_context_continuation_fingerprints(
    bound_agent: _ContextInvocationAgent,
    policy_fingerprint: str,
    execution_workspace: ProviderExecutionWorkspace | None,
) -> tuple[str, str]:
    """Return existing-shaped context keys proven for this generation."""

    definition_fingerprint = "sha256:" + hashlib.sha256((WIRE_VERSION + "\0" + bound_agent.definition.fingerprint).encode("utf-8")).hexdigest()
    if bound_agent.vendor not in {"codex", "antigravity"}:
        return definition_fingerprint, policy_fingerprint
    if type(execution_workspace) is not ProviderExecutionWorkspace:
        raise ContextExecutionError(ContextExecutionErrorCode.PROVIDER_CONTRACT)
    return (
        execution_workspace.continuation_digest(
            "context-definition",
            definition_fingerprint,
        ),
        execution_workspace.continuation_digest(
            "context-policy",
            policy_fingerprint,
        ),
    )


async def invoke_context_physical(
    self: _ContextInvocationHost[_InvocationAgent], bound_agent: _InvocationAgent, *,
    request: ContextProviderCallRequest | BoundContextProviderCallRequest,
    execution_workspace: ProviderExecutionWorkspace | None = None,
) -> ContextProviderCallResult:
    """Build and submit one exact contextual ProviderInvocation."""

    if type(request) not in {ContextProviderCallRequest, BoundContextProviderCallRequest}:
        raise ContextExecutionError(ContextExecutionErrorCode.PROVIDER_CONTRACT)
    scope, policy_fingerprint = request.scope, request.policy_fingerprint
    expected_thread_id, call_kind = request.expected_thread_id, request.call_kind
    payload, component_paths = request.payload, request.component_paths
    mandatory_reference_paths, continuation_identity = request.mandatory_reference_paths, request.continuation_identity
    if type(request) is BoundContextProviderCallRequest:
        request.contract.require_current_offer(request.contract.offers)
        schema = request.contract.schema_value()
    else:
        if scope.agent_id == LEAD_ORCHESTRATOR:
            raise ContextExecutionError(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        schema = request.schema
    self.context_provider_ensure_open()
    if (
        type(scope) is not ContextScope
        or scope.agent_id != bound_agent.definition.agent_id
        or type(call_kind) is not ProviderCallKind
        or call_kind is ProviderCallKind.UNCLASSIFIED
    ):
        raise ContextExecutionError(ContextExecutionErrorCode.PROVIDER_CONTRACT)
    session = self.workspace.validate_identifier(scope.session_id)
    if continuation_identity is not None and (
        type(continuation_identity) is not ContextContinuationIdentity
        or not continuation_identity.accepts(scope)
    ):
        raise ContextExecutionError(ContextExecutionErrorCode.PROVIDER_CONTRACT)
    storage_scope = (
        scope if continuation_identity is None
        else continuation_identity.checkpoint_scope()
    )
    definition_fingerprint, effective_policy_fingerprint = (
        self.context_provider_effective_fingerprints(
            bound_agent,
            policy_fingerprint,
            execution_workspace,
        )
    )
    self.context_provider_ready_check(session)
    stored_thread_id = provider_context_thread_store.load_context_thread(
        self.workspace,
        storage_scope,
        bound_agent.vendor,
        definition_fingerprint,
        effective_policy_fingerprint,
        require_ready=self.context_provider_ready_check,
    )
    if stored_thread_id != expected_thread_id:
        raise ContextExecutionError(ContextExecutionErrorCode.CONTINUATION_MISMATCH)

    generation = bound_agent.context_provider_generation
    adapter = (
        self.context_provider_adapter_lookup(bound_agent.vendor)
        if generation is None
        else generation.adapters[bound_agent.vendor]
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
    if type(request) is BoundContextProviderCallRequest:
        request.contract.require_projection(
            bound_native_schema,
            declared_lowering=(
                "integer_singleton_bounds"
                if bound_agent.vendor == "antigravity"
                else "identity"
            ),
        )
    invocation = ProviderInvocation(
        session_id=session,
        schema=(
            bound_native_schema
        ),
        payload=payload,
        thread_id=expected_thread_id,
        call_kind=call_kind,
        component_paths=component_paths,
        mandatory_reference_paths=mandatory_reference_paths,
        binding_components=bound_agent.context_provider_binding.binding_components,
        execution_workspace=execution_workspace, context_witness=request.context_witness, context_anchor=request.context_anchor, context_call_id=(request.call_id if type(request) is BoundContextProviderCallRequest and request.contract.schema_group_phase is not None else None))
    if invocation.call_accounting is None or invocation.payload_json is None:
        raise ContextExecutionError(ContextExecutionErrorCode.PROVIDER_CONTRACT)
    if bound_agent.definition.agent_id == LEAD_ORCHESTRATOR and "agent_action" in schema.get("properties", {}):
        if (
            invocation.payload_accounting is None
            or invocation.payload_accounting.schema_utf8_bytes
            > MAX_PROVIDER_SCHEMA_UTF8_BYTES
            or invocation.payload_accounting.serialized_input_utf8_bytes
            > MAX_PROVIDER_INPUT_UTF8_BYTES
        ):
            raise SessionContextCapacityError()
    elif continuation_identity is not None and len(invocation.payload_json) > MAX_PROVIDER_PAYLOAD_UTF8_BYTES:
        raise SessionContextCapacityError(
            stage="provider_payload", actual_utf8_bytes=len(invocation.payload_json)
        )

    replay_validator = _context_replay_validator(
        self,
        bound_agent,
        storage_scope,
        definition_fingerprint,
        effective_policy_fingerprint,
        expected_thread_id,
    )
    exchange = None
    try:
        async with self.context_provider_activity(bound_agent.vendor) as auth_generation:
            validate_provider_replay()
            exchange = _begin_context_agent_exchange(
                self,
                bound_agent, scope=scope, invocation=invocation,
                diagnostic_call_id=request.call_id,
            )
            if type(request) is BoundContextProviderCallRequest:
                request.contract.require_submission(
                    invocation.payload,
                    invocation.schema,
                    declared_lowering=(
                        "integer_singleton_bounds"
                        if bound_agent.vendor == "antigravity"
                        else "identity"
                    ),
                )
            result = await provider_invocation_accounting.timed_provider_invoke(
                adapter, bound_agent.context_provider_binding, invocation,
            )
            self.context_provider_postsubmission_check(invocation)
        provider_finalization.capture_provider_return_without_replacing(exchange, result)
    except ProviderInvocationError as failure:
        accounting = provider_invocation_accounting.complete_failure_accounting(
            invocation, failure,
        )
        provider_invocation_accounting.observe_accounting(
            accounting, self.context_provider_accounting_sink,
        )
        submitted = (
            accounting is not None
            and accounting.state is ProviderCallState.SUBMITTED
        )
        authentication = failure.authentication_failure
        timed_out = self.context_provider_timeout_check(failure)
        known_auth_rejection = (
            authentication is not None
            and authentication.replay_safe
            and not timed_out
        )
        taint = bool(
            failure.taint_existing_thread or (submitted and not known_auth_rejection)
        )
        if taint:
            _taint_context_thread_without_replacing(
                self,
                storage_scope,
                bound_agent.vendor,
                definition_fingerprint,
                effective_policy_fingerprint,
            )
        translated = authentication or BackendProtocolError(failure.diagnostic)
        if authentication is not None:
            authentication.auth_generation = auth_generation
            authentication.attach_replay_validator(replay_validator)
        record_context_provider_failure(
            translated,
            ContextProviderFailureFacts(
                accounting,
                timed_out=timed_out,
                taint_existing_thread=taint,
                submission_uncertain=(
                    authentication is not None and not known_auth_rejection
                ),
                continuation_rejection=(
                    ContextContinuationRejection(failure.continuation_rejection.value)
                    if expected_thread_id is not None
                    and failure.continuation_rejection is not None
                    and not timed_out
                    else None
                ),
                native_context_check=failure.native_context_check,
            ),
        )
        provider_finalization.capture_provider_failure_without_replacing(
            exchange,
            (
                AgentExchangeTerminalCode.PROVIDER_AUTHENTICATION_REQUIRED
                if failure.authentication_failure is not None
                else AgentExchangeTerminalCode.PROVIDER_TIMED_OUT
                if timed_out
                else AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED
            ),
            failure=failure,
        )
        raise translated from failure
    except ProviderAuthenticationRequiredError:
        # A replay veto carries the already-recorded original physical facts.
        raise
    except asyncio.CancelledError as failure:
        accounting = provider_invocation_accounting.complete_failure_accounting(
            invocation, failure,
        )
        provider_invocation_accounting.observe_accounting(
            accounting, self.context_provider_accounting_sink,
        )
        submitted = (
            accounting is not None
            and accounting.state is ProviderCallState.SUBMITTED
        )
        if submitted:
            _taint_context_thread_without_replacing(
                self,
                storage_scope,
                bound_agent.vendor,
                definition_fingerprint,
                effective_policy_fingerprint,
            )
        record_context_provider_failure(
            failure,
            ContextProviderFailureFacts(
                accounting,
                taint_existing_thread=submitted,
            ),
        )
        provider_finalization.capture_cancelled_without_replacing(exchange)
        raise
    except BaseException as failure:
        accounting = provider_invocation_accounting.complete_failure_accounting(
            invocation, failure,
        )
        provider_invocation_accounting.observe_accounting(
            accounting, self.context_provider_accounting_sink,
        )
        submitted = (
            accounting is not None
            and accounting.state is ProviderCallState.SUBMITTED
        )
        if submitted:
            _taint_context_thread_without_replacing(
                self,
                storage_scope,
                bound_agent.vendor,
                definition_fingerprint,
                effective_policy_fingerprint,
            )
        record_context_provider_failure(
            failure,
            ContextProviderFailureFacts(
                accounting,
                timed_out=self.context_provider_timeout_check(failure),
                taint_existing_thread=submitted,
            ),
        )
        provider_finalization.capture_provider_failure_without_replacing(
            exchange,
            (
                AgentExchangeTerminalCode.PROVIDER_TIMED_OUT
                if self.context_provider_timeout_check(failure)
                else AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED
            ),
        )
        raise

    prepared = invocation.call_accounting
    assert prepared is not None
    accounting = replace(
        prepared,
        state=ProviderCallState.SUBMITTED,
        actual_transport_utf8_bytes=(
            result.actual_transport_utf8_bytes
            if type(result) is ProviderResult
            else None
        ),
        usage=result.usage if type(result) is ProviderResult else None,
        usage_observation=result.usage_observation if type(result) is ProviderResult else None,
        elapsed_ns=result.elapsed_ns if type(result) is ProviderResult else None,
    )
    provider_invocation_accounting.observe_accounting(
        accounting, self.context_provider_accounting_sink,
    )
    if type(result) is not ProviderResult or type(result.response_text) is not str:
        failure = ContextExecutionError(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        record_context_provider_failure(
            failure,
            ContextProviderFailureFacts(
                accounting,
                taint_existing_thread=True,
            ),
        )
        _taint_context_thread_without_replacing(
            self,
            storage_scope,
            bound_agent.vendor,
            definition_fingerprint,
            effective_policy_fingerprint,
        )
        if type(result) is ProviderResult:
            provider_finalization.capture_rejected_without_replacing(
                exchange,
                AgentExchangeTerminalCode.MALFORMED_STRUCTURED_RESPONSE,
            )
        raise failure
    if (
        type(result.thread_id) is not str
        or provider_thread_records.PROVIDER_THREAD_ID_PATTERN.fullmatch(result.thread_id) is None
    ):
        failure = ContextExecutionError(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        record_context_provider_failure(
            failure,
            ContextProviderFailureFacts(
                accounting,
                taint_existing_thread=True,
            ),
        )
        _taint_context_thread_without_replacing(
            self,
            storage_scope,
            bound_agent.vendor,
            definition_fingerprint,
            effective_policy_fingerprint,
        )
        provider_finalization.capture_rejected_without_replacing(
            exchange,
            AgentExchangeTerminalCode.RESPONSE_CORRELATION_MISMATCH,
        )
        raise failure
    if expected_thread_id is not None and result.thread_id != expected_thread_id:
        failure = ContextExecutionError(
            ContextExecutionErrorCode.CONTINUATION_MISMATCH
        )
        record_context_provider_failure(
            failure,
            ContextProviderFailureFacts(
                accounting,
                taint_existing_thread=True,
            ),
        )
        _taint_context_thread_without_replacing(
            self,
            storage_scope,
            bound_agent.vendor,
            definition_fingerprint,
            effective_policy_fingerprint,
        )
        provider_finalization.capture_rejected_without_replacing(
            exchange,
            AgentExchangeTerminalCode.RESPONSE_CORRELATION_MISMATCH,
        )
        raise failure
    native_check = result.native_context_check
    completed_without_post_witness = (
        invocation.context_witness is not None and native_check is not None
        and native_check.outcome == "unavailable"
        and native_check.stage.value == "post_complete"
        and native_check.pre_submit_verified
        and native_check.call_id == invocation.context_call_id
        and native_check.prior_call_id == invocation.context_witness.call_id
        and native_check.normal_exit_code == 0
        and native_check.observed_thread_id == result.thread_id == invocation.thread_id)
    if (native_check is not None and native_check.outcome == "contradiction"
            or invocation.context_witness is not None and result.context_witness is None
            and not completed_without_post_witness):
        failure = ContextExecutionError(ContextExecutionErrorCode.CONTINUATION_MISMATCH)
        record_context_provider_failure(failure, ContextProviderFailureFacts(
            accounting, taint_existing_thread=True,
            continuation_rejection=ContextContinuationRejection.UNAVAILABLE,
            native_context_check=result.native_context_check))
        _taint_context_thread_without_replacing(
            self,
            storage_scope, bound_agent.vendor, definition_fingerprint, effective_policy_fingerprint)
        provider_finalization.capture_rejected_without_replacing(
            exchange, AgentExchangeTerminalCode.CONTEXT_RUNTIME_FAILED,
        )
        raise failure from result.native_context_cause
    try:
        self.context_provider_postsubmission_check(invocation)
        provider_context_thread_store.store_context_thread(
            self.workspace,
            storage_scope,
            bound_agent.vendor,
            definition_fingerprint,
            effective_policy_fingerprint,
            result.thread_id,
            tainted=False,
            require_owned_access=self.context_provider_owned_access_check,
        )
        self.context_provider_postsubmission_check(invocation)
    except BaseException as failure:
        try:
            record_context_provider_failure(
                failure,
                ContextProviderFailureFacts(
                    accounting,
                    taint_existing_thread=True,
                ),
            )
            _taint_context_thread_without_replacing(
                self,
                storage_scope,
                bound_agent.vendor,
                definition_fingerprint,
                effective_policy_fingerprint,
            )
        finally:
            if isinstance(failure, asyncio.CancelledError):
                provider_finalization.capture_cancelled_without_replacing(exchange)
            else:
                provider_finalization.capture_runtime_failure_without_replacing(
                    exchange,
                    AgentExchangeTerminalCode.THREAD_PERSISTENCE_FAILED,
                )
        raise
    try:
        self.context_provider_postsubmission_check(invocation)
        return ContextProviderCallResult(
            result.response_text,
            result.thread_id,
            accounting,
        provider_finalization.ContextExchangeFinalizer(exchange),
        context_witness=result.context_witness,
        native_context_check=result.native_context_check)
    except asyncio.CancelledError:
        provider_finalization.capture_cancelled_without_replacing(exchange)
        raise
    except BaseException as failure:
        try:
            record_context_provider_failure(
                failure,
                ContextProviderFailureFacts(
                    accounting,
                    taint_existing_thread=True,
                ),
            )
            _taint_context_thread_without_replacing(
                self,
                storage_scope,
                bound_agent.vendor,
                definition_fingerprint,
                effective_policy_fingerprint,
            )
        finally:
            provider_finalization.capture_runtime_failure_without_replacing(
                exchange,
                AgentExchangeTerminalCode.CONTEXT_RUNTIME_FAILED,
            )
        raise
