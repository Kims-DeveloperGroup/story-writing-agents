"""Provider-neutral backend coordination and public bound-agent facade."""

from __future__ import annotations
import story_writing_agents.backend_reply_decoding as backend_reply_decoding
from .backend_shutdown import release_backend_resources as _release_backend_resources
from .bound_agent_logical_invocation import (
    LogicalProviderDispatch as _LogicalProviderDispatch,
    LogicalRetrievalDispatch as _LogicalRetrievalDispatch,
    LogicalTrace as _LogicalTrace,
    compose_direct_codex_binding as _compose_direct_codex_binding,
    converse as _converse_bound_agent,
    converse_logical as _converse_logical,
    converse_with_retrieval as _converse_bound_agent_with_retrieval,
    converse_with_retrieval_logical as _converse_with_retrieval_logical,
    defer_direct_codex_acceptance as _defer_direct_codex_acceptance,
    direct_codex_execution_workspace as _direct_codex_execution_workspace,
    direct_execution_workspace as _direct_execution_workspace,
    invoke as _invoke_bound_agent,
    invoke_logical as _invoke_logical,
    preflight_direct_codex_workspace as _preflight_direct_codex_workspace,
    validated_reference_read_invocation as _validated_reference_read_invocation,
)
import story_writing_agents.conversation_retrieval_wire as conversation_retrieval_wire
import story_writing_agents.provider_binding_call_lifetime as provider_binding_call_lifetime
from .provider_adapter_generations import (
    AdapterGeneration as _AdapterGeneration,
    ProviderAdapterGenerations,
)
from .provider_binding_admission import (
    prepare_provider_binding_locked as _prepare_provider_binding_locked,
)
from .provider_bound_context import (
    BoundContextProvider as _BoundContextProvider,
    ContextProviderThreadStoreAccess as _ContextProviderThreadStoreAccess,
)
from .provider_context_invocation import (
    ContextExchangeBegin as _ContextExchangeBegin,
    effective_context_continuation_fingerprints as _effective_context_continuation_fingerprints,
    invoke_context_physical as _invoke_context_physical,
)
from .provider_conversation_retrieval_loop import (
    RetrievalReferenceLoad as _RetrievalReferenceLoad,
    RetrievalStructuredInvoke as _RetrievalStructuredInvoke,
    converse_with_retrieval as _converse_with_retrieval_codex,
    taint_retrieval_thread_without_replacing_failure as _taint_retrieval_thread_without_replacing_failure,
)
import story_writing_agents.provider_direct_call_wire as provider_direct_call_wire
from .provider_direct_structured_calls import (
    DirectReferenceReadInvoke as _DirectReferenceReadInvoke,
    DirectStructuredInvoke as _DirectStructuredInvoke,
    converse_direct_structured as _converse_codex,
    invoke_direct_structured as _invoke_codex,
)
import story_writing_agents.provider_execution_profiles as provider_execution_profiles
from .provider_execution_profile_observations import ProviderExecutionProfileObservations
from .provider_invocation_routing import (
    NamedProviderRoute as _NamedProviderRoute,
    NamedRetrievalRoute as _NamedRetrievalRoute,
    ProviderRoute as _ProviderRoute,
    RetrievalRoute as _RetrievalRoute,
    converse_antigravity as _converse_antigravity,
    converse_provider as _converse_provider,
    converse_with_retrieval_antigravity as _converse_with_retrieval_antigravity,
    converse_with_retrieval_provider as _converse_with_retrieval_provider,
    invoke_antigravity as _invoke_antigravity,
    invoke_provider as _invoke_provider,
)
import story_writing_agents.provider_manifest_thread_store as provider_manifest_thread_store
from .provider_reference_read_lifecycle import (
    ReferenceReadContextLoad as _ReferenceReadContextLoad,
    ReferenceReadStructuredInvoke as _ReferenceReadStructuredInvoke,
    complete_reference_read as _complete_reference_read,
    fail_reference_read_without_replacing as _fail_reference_read_without_replacing,
    invoke_codex_after_reference_read as _invoke_codex_after_reference_read,
    load_authorized_reference_context as _load_authorized_reference_context,
    taint_reference_thread_without_replacing as _taint_reference_thread_without_replacing,
    terminate_reference_read_without_replacing as _terminate_reference_read_without_replacing,
)
import story_writing_agents.provider_session_access_transactions as provider_session_access_transactions
import story_writing_agents.provider_session_artifacts as provider_session_artifacts
from .provider_session_lifecycle_operations import (
    acquire_session_access as _acquire_session_access,
    assert_provider_work_ready as _assert_provider_work_ready,
    create_session_with_access as _create_session_with_access,
    get_session_lifecycle as _get_session_lifecycle,
    list_session_lifecycles as _list_session_lifecycles,
    pause_session_and_release_access as _pause_session_and_release_access,
    release_session_access as _release_session_access,
    reopen_session_with_access as _reopen_session_with_access,
)
import story_writing_agents.provider_session_transitions as provider_session_transitions
from .provider_structured_invocation import (
    DEFERRED_DIRECT_CODEX_ACCEPTANCES as _DEFERRED_DIRECT_CODEX_ACCEPTANCES,
    DeferredDirectCodexAcceptance as _DeferredDirectCodexAcceptance,
    LegacyExchangeBegin as _LegacyExchangeBegin,
    RejectedResponseRecorder as _RejectedResponseRecorder,
    StructuredOutputRejected as _StructuredOutputRejected,
    attach_reply_trace as _attach_reply_trace,
    attach_validation_details as _attach_validation_details,
    begin_legacy_agent_exchange as _begin_legacy_agent_exchange,
    invoke_provider_structured as _invoke_provider_structured,
    invoke_provider_structured_resilient as _invoke_provider_structured_resilient,
    observe_provider_failure as _observe_provider_failure,
    record_rejected_provider_response_without_replacing_failure as _record_rejected_provider_response_without_replacing_failure,
    stage_validation_details as _stage_validation_details,
)
import story_writing_agents.provider_thread_records as provider_thread_records
from .provider_message_catalog import WIRE_VERSION, WireShapeError
from .provider_response_mapping import (
    normalize_direct_result,
    normalize_retrieval_action,
)

import asyncio
import atexit
import contextvars
import hashlib
import inspect
import os
import threading
from .structured_output_validation import validation_failure, remap_validation, prefix_validation
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import AbstractAsyncContextManager, AbstractContextManager, asynccontextmanager, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .agents import ROSTER
from .agent_context import (
    ProviderCallAccounting,
    ProviderCallKind,
    ProviderCallState,
)
from .agent_context.contracts.errors import (
    ContextExecutionError,
    ContextExecutionErrorCode,
)
from .agent_context.invocation.provider import (
    ContextProvider,
    ContextProviderCallFinalizer,
    ContextProviderCallRequest,
    ContextProviderCallResult,
    ContextProviderTerminalCode,
    ContextReferencePreludeRequest,
    ContextReferencePreludeResult,
    BoundContextProviderCallRequest,
)
from .agent_context.manifest import ContextScope
from .agent_exchanges import AgentExchangeProvider, AgentExchangeScopeKind
from .agent_exchanges.capture import AgentExchangeCaptureLifecycle, WorkspaceAgentExchangeCapture
from .antigravity_adapter import AntigravityAdapter
from .config import ConfigurationSnapshot, ConfigManager
from .codex_adapter import CodexAdapter
from .conversations import CollaborationEventStore
from .conversation_retrieval import (
    ConversationRetrievalAction,
    ConversationRetrievalActionHandler,
    ConversationRetrievalAnswerAction,
    ConversationRetrievalObservation,
    ConversationRetrievalReply,
    ConversationRetrievalRequest,
)
from .debug_log import (
    DebugOperation,
    DebugOutcome,
    DebugPhase,
    WorkspaceDebugLog,
)
from .errors import (
    BackendProtocolError,
    ConfigurationError,
    SessionIdError,
    SessionNotFoundError,
)
from .git.session_contracts import GitSessionResourceInspection
from .models import (
    AgentDefinition,
    AgentReply,
    AgentReplyKind,
    AgentReplyNextPhase,
    AgentRequest,
    ConversationReply,
    ConversationRequest,
    Phase,
    Verdict,
)
from .interactive_settings import (
    ProviderRouteEntry,
    SettingsError,
    SettingsErrorKind,
    read_provider_route_generations,
    reconcile_provider_route_generations,
)
from .provider_auth_recovery import provider_activity
from .provider_adapter import (
    DEFAULT_PROVIDER_TIMEOUT_SECONDS,
    AgentExecutionProfile,
    ProviderAdapter,
    ProviderBinding,
    ProviderInvocation,
    ProviderInvocationError,
)
from .backend_diagnostic_events import (
    log_execution_profile_event as _emit_log_execution_profile_event,
    record_debug_error as _emit_record_debug_error,
    trace_debug as _emit_trace_debug,
)
from .provider_configuration_activation import (
    activate_provider_configuration as _activate_provider_configuration,
    discard_provider_configuration as _discard_provider_configuration,
    provider_configuration_candidate_is_active as _provider_configuration_candidate_is_active,
    record_provider_configuration_recovery_required as _record_provider_configuration_recovery_required,
    validate_provider_configuration_candidate as _validate_provider_configuration_candidate,
)
from .provider_configuration_candidate import (
    stage_provider_configuration as _stage_provider_configuration,
)
from .provider_generation_adapters import (
    create_antigravity_adapter as _create_antigravity_adapter,
    create_codex_adapter as _create_codex_adapter,
)
from .provider_process import ProcessTimedOut, stop_process
from .provider_execution_workspace import ProviderExecutionWorkspace
from .session_lifecycle import SessionLifecycleState
from .session_recovery import (
    SessionArtifactApplicationEvidence,
    SessionReviewedProposalEvidence,
)
from .session_recovery.transition_assessment import (
    SessionTransitionAssessment,
    SessionTransitionAssessor,
    SessionTransitionOperation,
)
from .writing_artifacts.read_actions import (
    ArtifactReference,
    ReferenceAuthorizationError,
    ReferenceAuthorizationErrorCode,
    ReferenceReadAction,
    ReferenceReadActionKind,
    ReferenceReadInvocation,
    ReferenceReadRequest,
    ReferenceReadResult,
)
from .workspace import Workspace
from .workspace.root_contracts import main_workspace_root as _main_workspace_root
from .workspace.session_artifact_binding import (
    SessionArtifactBinding,
    compose_session_artifact_binding as _compose_session_artifact_binding,
)
from .workspace.session_resources import (
    provision_session_resources as _provision_session_resources,
    require_ready_session_resources as _require_ready_session_resources,
)


_CONTEXT_REFERENCE_TERMINATED_ATTRIBUTE = "_context_reference_terminated"
_ACCESS_CLAIM_GUARD = (
    f"{provider_session_artifacts.PROVIDER_SESSION_DIRECTORY}/.access-claims.lock"
)




_SAFE_STRUCTURED_FALLBACK_MESSAGE = (
    "I couldn't validate the provider's structured reply after one repair attempt, "
    "so no action was taken. You can continue the conversation or try again."
)





@dataclass(frozen=True, slots=True)
class BackendRuntimeConfigurationCandidate:
    """One fully built backend generation awaiting atomic activation."""

    owner: AgentBackend
    base_generation_id: int
    correlation_id: str
    previous_profiles: Mapping[str, AgentExecutionProfile]
    generation: _AdapterGeneration


@dataclass(frozen=True, slots=True)
class _BoundContextBinding:
    """Expose this binding's current backend operations to the contextual provider."""

    agent: BoundAgent

    @property
    def thread_store_access(self) -> _ContextProviderThreadStoreAccess:
        return self.agent.backend

    @property
    def compose_session_artifact_binding(self) -> Callable[[Path, str], SessionArtifactBinding]:
        return _compose_session_artifact_binding

    def open_provider_execution_workspace(
        self, binding: SessionArtifactBinding,
    ) -> AbstractContextManager[ProviderExecutionWorkspace]:
        return provider_execution_workspace_manager(self.agent, binding)

    @property
    def reference_call_use(
        self,
    ) -> Callable[[ReferenceReadInvocation | None], AbstractContextManager[None]]:
        return self.agent._reference_call_use

    @property
    def context_thread_lock(self) -> Callable[[ContextScope, str, str, str], asyncio.Lock]:
        return self.agent.backend._context_thread_lock

    @property
    def reference_thread_lock(
        self,
    ) -> Callable[[asyncio.Lock, ReferenceReadInvocation | None], AbstractAsyncContextManager[None]]:
        return self.agent._reference_thread_lock

    @property
    def terminate_reference_before_provider(
        self,
    ) -> Callable[[ReferenceReadInvocation | None, BaseException], None]:
        return self.agent._terminate_reference_before_provider

    def prepare_context_references(
        self, reference_read: ReferenceReadInvocation, request: ContextReferencePreludeRequest,
    ) -> Awaitable[ContextReferencePreludeResult]:
        return self.agent.backend._prepare_context_references(self.agent, reference_read, request)

    @property
    def reference_terminated_attribute(self) -> str:
        return _CONTEXT_REFERENCE_TERMINATED_ATTRIBUTE

    def invoke_context(
        self,
        request: ContextProviderCallRequest | BoundContextProviderCallRequest,
        *,
        execution_workspace: ProviderExecutionWorkspace | None,
    ) -> Awaitable[ContextProviderCallResult]:
        return self.agent.backend._invoke_context_physical(
            self.agent, request=request, execution_workspace=execution_workspace,
        )

    @property
    def terminate_reference(
        self,
    ) -> Callable[[ReferenceReadInvocation, ReferenceAuthorizationErrorCode], None]:
        return AgentBackend._terminate_reference_read_without_replacing

    def effective_context_fingerprints(
        self, policy_fingerprint: str, execution_workspace: ProviderExecutionWorkspace | None,
    ) -> tuple[str, str]:
        return AgentBackend._effective_context_continuation_fingerprints(
            self.agent, policy_fingerprint, execution_workspace,
        )


class BoundAgent:
    """One immutable definition bound to its concrete vendor implementation."""

    def __init__(
        self,
        owner: AgentBackend,
        definition: AgentDefinition,
        staging_directory: Path,
        *,
        _provider_binding: ProviderBinding | None = None,
        _adapter_generation: _AdapterGeneration | None = None,
        _execution_profile: AgentExecutionProfile | None = None,
    ) -> None:
        self.backend = owner
        self.definition = definition
        self.vendor = definition.backend
        self.staging_directory = staging_directory
        if _provider_binding is None:
            self._provider_binding = ProviderBinding(
                vendor=self.vendor,
                definition=definition,
                staging_directory=staging_directory,
            )
        elif (
            type(_provider_binding) is not ProviderBinding
            or _provider_binding.vendor != self.vendor
            or _provider_binding.definition is not definition
            or _provider_binding.staging_directory != staging_directory
        ):
            raise BackendProtocolError(
                "provider adapter returned an inconsistent binding"
            )
        else:
            self._provider_binding = _provider_binding
        self._adapter_generation = _adapter_generation
        self.execution_profile = _execution_profile
        if self._adapter_generation is not None:
            if (
                self.execution_profile is None
                or self.execution_profile.agent_id != definition.agent_id
                or self.execution_profile.provider != definition.backend
            ):
                raise BackendProtocolError(
                    "bound agent execution profile is inconsistent"
                )
        self.call_lifetime = provider_binding_call_lifetime.ProviderBindingCallLifetime()

    @property
    def context_provider_generation(self) -> _AdapterGeneration | None:
        return self._adapter_generation

    @property
    def context_provider_binding(self) -> ProviderBinding:
        return self._provider_binding

    @contextmanager
    def _reference_call_use(
        self,
        reference_read: ReferenceReadInvocation | None,
    ) -> Iterator[None]:
        """Retire a valid coordinator when call-resource entry itself fails."""

        entered = False
        try:
            with self.call_lifetime.use(
                is_backend_closed=lambda: self.backend._closed,
                finalize=lambda: self.backend._finalize_bound_agent(self),
            ):
                entered = True
                yield
        except BaseException as exc:
            if not entered:
                self._terminate_reference_before_provider(reference_read, exc)
            raise

    @asynccontextmanager
    async def _reference_thread_lock(
        self,
        lock: asyncio.Lock,
        reference_read: ReferenceReadInvocation | None,
    ) -> AsyncIterator[None]:
        """Retire a valid coordinator only when async lock entry fails."""

        entered = False
        try:
            async with lock:
                entered = True
                yield
        except BaseException as exc:
            if not entered:
                self._terminate_reference_before_provider(reference_read, exc)
            raise

    def _retire(self) -> None:
        """Reject later calls and release after the last entered call exits."""
        if self.call_lifetime.retire():
            self.backend._finalize_bound_agent(self)

    def context_provider(
        self,
        reference_read: ReferenceReadInvocation | None = None,
    ) -> ContextProvider:
        """Return the package-internal physical seam for contextual execution."""

        return _BoundContextProvider(_BoundContextBinding(self), reference_read)

    _direct_execution_workspace = _direct_execution_workspace

    _direct_codex_execution_workspace = _direct_codex_execution_workspace

    _preflight_direct_codex_workspace = _preflight_direct_codex_workspace

    _compose_direct_codex_binding = _compose_direct_codex_binding

    _defer_direct_codex_acceptance = staticmethod(_defer_direct_codex_acceptance)

    def _context_provider(
        self,
        reference_read: ReferenceReadInvocation | None = None,
    ) -> ContextProvider:
        """Compatibility delegate for the original package-internal seam."""

        return self.context_provider(reference_read)

    def decode_context_conversation_answer(
        self,
        request: ConversationRequest,
        text: str,
    ) -> ConversationReply:
        """Purely decode one correlated contextual conversation answer."""

        validated = self._validated_conversation_request(request)
        return backend_reply_decoding.decode_conversation_reply(
            validated,
            text,
            provider_name=("Codex" if self.vendor == "codex" else "Antigravity"),
        )

    def decode_context_retrieval_answer(
        self,
        request: ConversationRetrievalRequest,
        text: str,
    ) -> ConversationRetrievalAnswerAction:
        """Purely decode one terminal contextual retrieval envelope."""

        validated = self._validated_conversation_retrieval_request(request)
        action = backend_reply_decoding.decode_conversation_retrieval_action(
            text,
            request=validated,
            action_index=1,
            provider_name=("Codex" if self.vendor == "codex" else "Antigravity"),
        )
        if type(action) is not ConversationRetrievalAnswerAction or (
            action.session_id != validated.session_id
            or action.conversation_id != validated.conversation_id
            or action.turn_id != validated.turn_id
        ):
            raise BackendProtocolError(
                "contextual retrieval answer does not match its request"
            )
        return action

    def decode_context_agent_answer(
        self,
        request: AgentRequest,
        text: str,
    ) -> AgentReply:
        """Purely decode one phase- and revision-correlated debate answer."""

        if type(request) is not AgentRequest or (
            request.agent_id != self.definition.agent_id
            or type(request.phase) is not Phase
            or type(request.revision_id) is not str
            or not request.revision_id
        ):
            raise BackendProtocolError(
                "agent request is invalid for contextual answer decoding"
            )
        reply = backend_reply_decoding.decode_reply(
            request.phase,
            text,
            revision_id=request.revision_id,
            provider_name=("Codex" if self.vendor == "codex" else "Antigravity"),
        )
        if reply.revision_id != request.revision_id:
            raise BackendProtocolError(
                "contextual agent answer revision does not match its request"
            )
        return reply

    invoke = _invoke_bound_agent

    _invoke_logical = _invoke_logical

    converse = _converse_bound_agent

    _converse_logical = _converse_logical

    converse_with_retrieval = _converse_bound_agent_with_retrieval

    _converse_with_retrieval_logical = _converse_with_retrieval_logical

    _validated_reference_read_invocation = _validated_reference_read_invocation

    @staticmethod
    def _terminate_reference_before_provider(
        reference_read: ReferenceReadInvocation | None,
        failure: BaseException,
    ) -> None:
        """Retire a real coordinator after pre-provider validation failure."""

        if reference_read is None:
            return
        request = getattr(reference_read, "request", None)
        if type(request) is not ReferenceReadRequest:
            return
        for name in (
            "handle_reference_read_action",
            "record_result_received",
            "accept",
            "terminate",
        ):
            method = getattr(reference_read, name, None)
            if method is None or not callable(method):
                return
            method_call = getattr(method, "__call__", None)
            if (
                inspect.iscoroutinefunction(method)
                or inspect.isasyncgenfunction(method)
                or inspect.iscoroutinefunction(method_call)
                or inspect.isasyncgenfunction(method_call)
            ):
                return
        if isinstance(failure, asyncio.CancelledError):
            outcome = ReferenceAuthorizationErrorCode.CANCELLED
        elif isinstance(failure, ReferenceAuthorizationError):
            outcome = failure.code
        else:
            outcome = ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE
        AgentBackend._terminate_reference_read_without_replacing(
            reference_read,
            outcome,
        )

    def _validated_conversation_request(
        self,
        request: ConversationRequest,
    ) -> ConversationRequest:
        """Return a canonical request compatible with this immutable binding.

        Validation performs no I/O. A wrong caller type raises ``TypeError``;
        malformed request or binding data and route mismatches raise
        ``BackendProtocolError``.
        """

        if type(request) is not ConversationRequest:
            raise TypeError("request must be a ConversationRequest")
        definition = self.definition
        if (
            type(definition) is not AgentDefinition
            or not isinstance(definition.agent_id, str)
            or definition.agent_id not in ROSTER
            or not isinstance(definition.backend, str)
            or not definition.backend
            or self.vendor != definition.backend
            or not isinstance(definition.fingerprint, str)
            or provider_thread_records.PROVIDER_FINGERPRINT_PATTERN.fullmatch(definition.fingerprint) is None
        ):
            raise BackendProtocolError(
                "bound agent definition is invalid for conversation routing"
            )
        try:
            validated = ConversationRequest(
                session_id=request.session_id,
                conversation_id=request.conversation_id,
                turn_id=request.turn_id,
                agent_id=request.agent_id,
                content=request.content,
                context=request.context,
                story_session_context=request.story_session_context,
            )
        except (AttributeError, SessionIdError, TypeError, ValueError) as exc:
            raise BackendProtocolError("conversation request is invalid") from exc
        pass  # Validation-only omission of wrong-bound-agent rejection.




        return validated

    def _validated_conversation_retrieval_request(
        self,
        request: ConversationRetrievalRequest,
    ) -> ConversationRetrievalRequest:
        """Return one canonical retrieval request before provider mutation.

        Exact request type, definition compatibility, correlation, content,
        and context are validated without I/O. Invalid request contracts become
        ``BackendProtocolError`` while a wrong caller type remains ``TypeError``.
        """

        if type(request) is not ConversationRetrievalRequest:
            raise TypeError("request must be a ConversationRetrievalRequest")
        definition = self.definition
        if (
            type(definition) is not AgentDefinition
            or not isinstance(definition.agent_id, str)
            or definition.agent_id not in ROSTER
            or not isinstance(definition.backend, str)
            or not definition.backend
            or self.vendor != definition.backend
            or not isinstance(definition.fingerprint, str)
            or provider_thread_records.PROVIDER_FINGERPRINT_PATTERN.fullmatch(definition.fingerprint) is None
        ):
            raise BackendProtocolError(
                "bound agent definition is invalid for conversation retrieval"
            )
        try:
            validated = ConversationRetrievalRequest(
                session_id=request.session_id,
                conversation_id=request.conversation_id,
                turn_id=request.turn_id,
                agent_id=request.agent_id,
                content=request.content,
                context=request.context,
                story_session_context=request.story_session_context,
                rag_context=request.rag_context,
            )
        except (AttributeError, SessionIdError, TypeError, ValueError) as exc:
            raise BackendProtocolError(
                "conversation retrieval request is invalid"
            ) from exc
        if validated.agent_id != definition.agent_id:
            raise BackendProtocolError(
                "request was routed through the wrong bound agent: "
                f"{validated.agent_id} != {definition.agent_id}"
            )
        return validated


    logical_reference_call_use = _reference_call_use
    logical_reference_thread_lock = _reference_thread_lock
    logical_terminate_reference_before_provider = _terminate_reference_before_provider
    logical_validate_conversation = _validated_conversation_request
    logical_validate_retrieval = _validated_conversation_retrieval_request
    logical_compose_direct_codex_binding = _compose_direct_codex_binding

    @property
    def logical_open_provider_execution_workspace(
        self,
    ) -> Callable[[BoundAgent, SessionArtifactBinding], AbstractContextManager[ProviderExecutionWorkspace]]:
        return provider_execution_workspace_manager

    @property
    def logical_force_terminate_reference(
        self,
    ) -> Callable[[ReferenceReadInvocation, ReferenceAuthorizationErrorCode], None]:
        return AgentBackend._terminate_reference_read_without_replacing


class AgentBackend:
    """Concrete coordinator that routes bound agents to configured vendors."""

    def __init__(
        self,
        workspace: Workspace | str | Path,
        *,
        executable: str = "codex",
        model: str | None = None,
        timeout_seconds: float = DEFAULT_PROVIDER_TIMEOUT_SECONDS,
        backend_overrides: Mapping[str, str] | None = None,
        antigravity_executable: str = "agy",
        antigravity_model: str | None = None,
        antigravity_effort: str | None = None,
    ) -> None:
        self.workspace = (
            workspace if isinstance(workspace, Workspace) else Workspace(workspace)
        )
        self._control_workspace_root = _main_workspace_root(self.workspace.root)
        self._debug_log = WorkspaceDebugLog(
            self.workspace,
            defer_until_session=True,
        )
        self._agent_exchange_capture = WorkspaceAgentExchangeCapture(self.workspace)
        self._context_accounting_sink: (
            Callable[[ProviderCallAccounting], object] | None
        ) = None
        if not isinstance(executable, str) or not executable:
            raise ValueError("Codex executable must be non-empty text")
        manager = ConfigManager(self.workspace)
        configuration = manager.load_configuration_snapshot()
        raw_overrides = dict(backend_overrides or {})
        unknown_agents = set(raw_overrides).difference(ROSTER)
        if unknown_agents:
            failure = ConfigurationError(
                f"unknown backend override agent: {sorted(unknown_agents)[0]}"
            )
            self._record_debug_error(DebugOperation.CONFIGURATION, failure)
            raise failure
        try:
            self.backend_overrides = MappingProxyType(
                {
                    agent_id: manager.validate_backend(backend)
                    for agent_id, backend in raw_overrides.items()
                }
            )
        except BaseException as exc:
            self._record_debug_error(DebugOperation.CONFIGURATION, exc)
            raise
        route_before_bytes, routes_before = read_provider_route_generations(
            self.workspace,
            configuration,
            provider_overrides=self.backend_overrides,
        )
        _route_bytes, provider_routes = reconcile_provider_route_generations(
            self.workspace,
            configuration,
            provider_overrides=self.backend_overrides,
            persist_missing=False,
        )
        startup_switched_agents = frozenset(
            agent_id
            for agent_id in ROSTER
            if route_before_bytes is not None
            and routes_before[agent_id].provider != provider_routes[agent_id].provider
        )
        self._trace_debug(
            DebugOperation.CONFIGURATION,
            DebugPhase.STARTED,
        )
        try:
            explicit_model = manager.validate_codex_model(model)
        except ConfigurationError as exc:
            failure = ValueError("Codex model must be safe non-empty text or None")
            self._record_debug_error(DebugOperation.CONFIGURATION, failure)
            raise failure from exc
        try:
            explicit_antigravity_model = manager.validate_antigravity_model(
                antigravity_model
            )
        except ConfigurationError as exc:
            failure = ValueError(
                "Antigravity model must be safe non-empty text or None"
            )
            self._record_debug_error(DebugOperation.CONFIGURATION, failure)
            raise failure from exc
        try:
            explicit_antigravity_effort = manager.validate_antigravity_effort(
                antigravity_effort
            )
        except ConfigurationError as exc:
            failure = ValueError(
                "Antigravity effort must be None or one of: low, medium, high"
            )
            self._record_debug_error(DebugOperation.CONFIGURATION, failure)
            raise failure from exc
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            failure = ValueError("Codex timeout must be a positive number")
            self._record_debug_error(DebugOperation.CONFIGURATION, failure)
            raise failure

        self._configuration_snapshot = configuration
        self.executable = executable
        self._explicit_model = explicit_model
        self._explicit_antigravity_model = explicit_antigravity_model
        self._explicit_antigravity_effort = explicit_antigravity_effort
        self.antigravity_executable = antigravity_executable
        self.timeout_seconds = float(timeout_seconds)
        self._generation_lock = threading.RLock()
        self._provider_routes = MappingProxyType(dict(provider_routes))
        profiles = provider_execution_profiles.resolve_execution_profiles(
            self.workspace,
            configuration,
            backend_overrides=self.backend_overrides,
            explicit_model=self._explicit_model,
            explicit_antigravity_model=self._explicit_antigravity_model,
            explicit_antigravity_effort=self._explicit_antigravity_effort,
        )
        if any(
            provider_routes[agent_id].provider != profiles[agent_id].provider
            for agent_id in ROSTER
        ):
            raise SettingsError(SettingsErrorKind.RECOVERY_REQUIRED)
        try:
            self._codex_adapter = _create_codex_adapter(
                executable=executable,
                timeout_seconds=float(timeout_seconds),
                configuration=configuration,
                explicit_model=explicit_model,
                control_root=self._control_workspace_root,
                profiles=profiles,
            )
            self._antigravity_adapter = _create_antigravity_adapter(
                executable=antigravity_executable,
                timeout_seconds=float(timeout_seconds),
                state_root=(
                    self._control_workspace_root
                    / provider_session_artifacts.PROVIDER_SESSION_DIRECTORY
                    / ".antigravity"
                ),
                explicit_model=explicit_antigravity_model,
                explicit_effort=explicit_antigravity_effort,
                configuration=configuration,
                profiles=profiles,
            )
        except BaseException as exc:
            self._record_debug_error(DebugOperation.CONFIGURATION, exc)
            raise
        self._adapters: dict[str, ProviderAdapter] = {
            self._codex_adapter.vendor: self._codex_adapter,
            self._antigravity_adapter.vendor: self._antigravity_adapter,
        }
        initial_generation = _AdapterGeneration(
            generation_id=0,
            configuration=configuration,
            profiles=profiles,
            provider_routes=self._provider_routes,
            adapters=self._adapters,
            switched_agents=startup_switched_agents,
        )
        self._adapter_generations = ProviderAdapterGenerations(
            initial_generation, self._generation_lock
        )
        self._profile_observations = ProviderExecutionProfileObservations(
            startup_switched_agents
        )
        self.antigravity_model = self._antigravity_adapter.model
        self.antigravity_effort = self._antigravity_adapter.effort
        self.model = self._codex_adapter.model
        self._codex_reasoning_effort = self._codex_adapter.reasoning_effort
        self._access_claim_tokens: dict[str, str] = {}
        self._access_transactions = provider_session_access_transactions.ProviderSessionAccessTransactions(
            self._access_claim_tokens, _ACCESS_CLAIM_GUARD,
        )
        self._session_transitions = provider_session_transitions.ProviderSessionTransitions(
            self._access_transactions, self._access_claim_tokens, _ACCESS_CLAIM_GUARD,
        )
        self._thread_locks: dict[tuple[str, str, str], asyncio.Lock] = {}
        self._context_thread_locks: dict[tuple[str, ...], asyncio.Lock] = {}
        self._staging_directories = self._codex_adapter.staging_directories
        self._bound_agents: set[BoundAgent] = set()
        self._closed = False
        self._close_cleanup_complete = False
        atexit.register(self.close)
        self._trace_debug(
            DebugOperation.CONFIGURATION,
            DebugPhase.SUCCEEDED,
            outcome=DebugOutcome.READY,
        )
        startup_applied_profiles = tuple(
            (
                self._adapter_generations.active.profiles[agent_id],
                self._provider_routes[agent_id].generation,
            )
            for agent_id in ROSTER
        )
        for profile, route_generation in startup_applied_profiles:
            self._log_execution_profile_event(
                DebugOperation.EXECUTION_PROFILE_APPLIED,
                correlation_id="startup",
                route_generation=route_generation,
                active_profile=profile,
                activation_state="active",
            )

    def execution_profile(self, agent_id: str) -> AgentExecutionProfile:
        """Return the exact active profile used for the next agent invocation."""

        if agent_id not in ROSTER:
            raise ConfigurationError(f"unknown agent: {agent_id}")
        with self._generation_lock:
            return self._adapter_generations.active.profiles[agent_id]

    def execution_profile_fingerprint(self, agent_id: str) -> str:
        """Return provider/model/effort and source facts without prompt content."""

        return provider_execution_profiles.execution_profile_fingerprint(
            self.execution_profile(agent_id)
        )

    def execution_profiles(self) -> tuple[AgentExecutionProfile, ...]:
        """Return the active roster in canonical order."""

        with self._generation_lock:
            profiles = self._adapter_generations.active.profiles
            return tuple(profiles[agent_id] for agent_id in ROSTER)

    def execution_profile_confirmation(self, agent_id: str) -> str:
        """Return pending or confirmed for the active execution profile."""

        if agent_id not in ROSTER:
            raise ConfigurationError(f"unknown agent: {agent_id}")
        with self._generation_lock:
            return self._profile_observations.confirmation_locked(agent_id)

    def execution_profile_thread_status(self, agent_id: str) -> str:
        """Return the active generation's observed or pending thread status."""

        if agent_id not in ROSTER:
            raise ConfigurationError(f"unknown agent: {agent_id}")
        with self._generation_lock:
            return self._profile_observations.thread_status_locked(agent_id)

    def preview_execution_profile(
        self,
        configuration: ConfigurationSnapshot,
        agent_id: str,
    ) -> AgentExecutionProfile:
        """Resolve one candidate profile without adapter or durable mutation."""

        if type(configuration) is not ConfigurationSnapshot:
            raise TypeError("configuration must be a ConfigurationSnapshot")
        if agent_id not in ROSTER:
            raise ConfigurationError(f"unknown agent: {agent_id}")
        return provider_execution_profiles.resolve_execution_profiles(
            self.workspace,
            configuration,
            backend_overrides=self.backend_overrides,
            explicit_model=self._explicit_model,
            explicit_antigravity_model=self._explicit_antigravity_model,
            explicit_antigravity_effort=self._explicit_antigravity_effort,
        )[agent_id]

    def _confirm_execution_profile(
        self,
        bound_agent: BoundAgent,
        *,
        context_rehydrated: bool,
        thread_disposition: str,
    ) -> None:
        """Confirm an active profile only after its whole logical call succeeds."""

        profile = bound_agent.execution_profile
        generation = bound_agent._adapter_generation
        if profile is None or generation is None:
            return
        with self._generation_lock:
            if generation is not self._adapter_generations.active:
                return
            emit_confirmation = self._profile_observations.confirm_locked(
                profile.agent_id, thread_disposition
            )
        if not emit_confirmation:
            return
        self._log_execution_profile_confirmed(
            profile,
            thread_disposition=thread_disposition,
            context_state=("rehydrated" if context_rehydrated else "unavailable"),
        )

    def _logical_thread_disposition(
        self,
        bound_agent: BoundAgent,
        session_id: str,
        *,
        execution_workspace: ProviderExecutionWorkspace | None = None,
    ) -> str:
        """Capture logical-turn thread state before local reference loading."""

        generation = bound_agent._adapter_generation
        try:
            stored = provider_manifest_thread_store.load_manifest_thread(
                self.workspace,
                session_id,
                bound_agent.definition,
                fingerprint=self._legacy_continuation_fingerprint(
                    bound_agent,
                    execution_workspace,
                ),
            )
        except SessionNotFoundError:
            stored = None
        if (
            stored is None
            and generation is not None
            and bound_agent.definition.agent_id in generation.switched_agents
        ):
            return "fresh_after_provider_switch"
        return "fresh" if stored is None else "reused"

    def provider_route_generations(self) -> Mapping[str, int]:
        """Return the current shell's captured durable route generations."""

        with self._generation_lock:
            return MappingProxyType(
                {
                    agent_id: self._provider_routes[agent_id].generation
                    for agent_id in ROSTER
                }
            )

    def stage_runtime_configuration(
        self,
        configuration: ConfigurationSnapshot,
        provider_routes: Mapping[str, ProviderRouteEntry],
        *,
        correlation_id: str,
    ) -> BackendRuntimeConfigurationCandidate:
        """Build and preflight one isolated adapter generation."""

        self._ensure_open()

        def resolve_profiles(
            configuration: ConfigurationSnapshot,
        ) -> Mapping[str, AgentExecutionProfile]:
            return provider_execution_profiles.resolve_execution_profiles(
                self.workspace,
                configuration,
                backend_overrides=self.backend_overrides,
                explicit_model=self._explicit_model,
                explicit_antigravity_model=self._explicit_antigravity_model,
                explicit_antigravity_effort=self._explicit_antigravity_effort,
            )

        def make_candidate(
            base_generation_id: int,
            current_profiles: Mapping[str, AgentExecutionProfile],
            generation: _AdapterGeneration,
        ) -> BackendRuntimeConfigurationCandidate:
            return BackendRuntimeConfigurationCandidate(
                owner=self,
                base_generation_id=base_generation_id,
                correlation_id=correlation_id,
                previous_profiles=current_profiles,
                generation=generation,
            )

        return _stage_provider_configuration(
            configuration,
            provider_routes,
            correlation_id=correlation_id,
            generation_lock=self._generation_lock,
            generations=self._adapter_generations,
            resolve_profiles=resolve_profiles,
            create_adapters=lambda configuration, profiles: self._create_adapters(
                configuration, profiles
            ),
            make_candidate=make_candidate,
        )

    def commit_runtime_configuration(
        self,
        candidate: BackendRuntimeConfigurationCandidate,
    ) -> tuple[AgentExecutionProfile, ...]:
        """Atomically activate one staged generation after runtime quiescence."""

        if not self._owns_runtime_configuration_candidate(candidate):
            raise TypeError("candidate does not belong to this backend")
        _activate_provider_configuration(
            candidate,
            generation_lock=self._generation_lock,
            generations=self._adapter_generations,
            profile_observations=self._profile_observations,
            has_live_bindings=lambda: bool(self._bound_agents),
            install_backend_mirrors=self._install_runtime_configuration_mirrors_locked,
            record_profile_event=lambda operation, **fields: self._log_execution_profile_event(
                operation, **fields
            ),
            record_close_error=lambda exc: self._record_debug_error(DebugOperation.CLEANUP, exc),
        )
        return self.execution_profiles()

    def validate_runtime_configuration_candidate(
        self,
        candidate: BackendRuntimeConfigurationCandidate,
    ) -> None:
        """Prove one staged generation can still activate before persistence."""

        if not self._owns_runtime_configuration_candidate(candidate):
            raise TypeError("candidate does not belong to this backend")
        _validate_provider_configuration_candidate(
            candidate,
            generation_lock=self._generation_lock,
            generations=self._adapter_generations,
            has_live_bindings=lambda: bool(self._bound_agents),
        )

    def runtime_configuration_candidate_is_active(
        self,
        candidate: BackendRuntimeConfigurationCandidate,
    ) -> bool:
        """Return whether one exact candidate generation became active."""

        if not self._owns_runtime_configuration_candidate(candidate):
            raise TypeError("candidate does not belong to this backend")
        return _provider_configuration_candidate_is_active(
            candidate,
            generation_lock=self._generation_lock,
            generations=self._adapter_generations,
        )

    def discard_runtime_configuration(
        self,
        candidate: BackendRuntimeConfigurationCandidate,
    ) -> None:
        """Close one uncommitted staged generation idempotently."""

        if not self._owns_runtime_configuration_candidate(candidate):
            raise TypeError("candidate does not belong to this backend")
        _discard_provider_configuration(
            candidate,
            generation_lock=self._generation_lock,
            generations=self._adapter_generations,
            record_close_error=lambda exc: self._record_debug_error(DebugOperation.CLEANUP, exc),
        )

    def record_runtime_configuration_recovery_required(
        self,
        candidate: BackendRuntimeConfigurationCandidate,
    ) -> None:
        """Persist fixed recovery-required profile facts without exception prose."""

        if not self._owns_runtime_configuration_candidate(candidate):
            return
        _record_provider_configuration_recovery_required(
            candidate,
            record_profile_event=lambda operation, **fields: self._log_execution_profile_event(
                operation, **fields
            ),
        )

    def _owns_runtime_configuration_candidate(
        self, candidate: BackendRuntimeConfigurationCandidate
    ) -> bool:
        return type(candidate) is BackendRuntimeConfigurationCandidate and candidate.owner is self

    def _install_runtime_configuration_mirrors_locked(
        self, next_generation: _AdapterGeneration
    ) -> None:
        """Update every compatibility mirror within the generation transaction."""
        self._configuration_snapshot = next_generation.configuration
        self._provider_routes = next_generation.provider_routes
        self._adapters = next_generation.adapters
        self._codex_adapter = next_generation.adapters["codex"]
        self._antigravity_adapter = next_generation.adapters["antigravity"]
        self._staging_directories = self._codex_adapter.staging_directories
        self.model = self._codex_adapter.model
        self._codex_reasoning_effort = self._codex_adapter.reasoning_effort
        self.antigravity_model = self._antigravity_adapter.model
        self.antigravity_effort = self._antigravity_adapter.effort

    def _create_adapters(
        self,
        configuration: ConfigurationSnapshot,
        profiles: Mapping[str, AgentExecutionProfile],
    ) -> dict[str, ProviderAdapter]:
        """Construct isolated provider adapters from exact resolved profiles."""

        codex = _create_codex_adapter(
            executable=self.executable,
            timeout_seconds=self.timeout_seconds,
            configuration=configuration,
            explicit_model=self._explicit_model,
            control_root=self._control_workspace_root,
            profiles=profiles,
        )
        antigravity = _create_antigravity_adapter(
            executable=self.antigravity_executable,
            timeout_seconds=self.timeout_seconds,
            state_root=(
                self._control_workspace_root
                / provider_session_artifacts.PROVIDER_SESSION_DIRECTORY
                / ".antigravity"
            ),
            explicit_model=self._explicit_antigravity_model,
            explicit_effort=self._explicit_antigravity_effort,
            configuration=configuration,
            profiles=profiles,
        )
        return {codex.vendor: codex, antigravity.vendor: antigravity}

    @property
    def diagnostic_event_sink(self) -> WorkspaceDebugLog:
        """Return the current sink for backend diagnostic events."""
        return self._debug_log

    _trace_debug = _emit_trace_debug

    _log_execution_profile_event = _emit_log_execution_profile_event

    def _log_execution_profile_confirmed(
        self,
        profile: AgentExecutionProfile,
        *,
        thread_disposition: str,
        context_state: str,
    ) -> None:
        """Record first successful use of one active profile generation."""

        with self._generation_lock:
            correlation_id = self._profile_observations.correlation_locked(profile.agent_id)
            route_generation = self._provider_routes[profile.agent_id].generation
        self._log_execution_profile_event(
            DebugOperation.EXECUTION_PROFILE_CONFIRMED,
            correlation_id=correlation_id,
            route_generation=route_generation,
            active_profile=profile,
            activation_state="confirmed",
            thread_disposition=thread_disposition,
            context_state=context_state,
        )

    _record_debug_error = _emit_record_debug_error

    async def bind(self, definition: AgentDefinition) -> BoundAgent:
        """Verify and stage a configured vendor binding."""

        self._ensure_open()
        agent_id = definition.agent_id
        self._trace_debug(
            DebugOperation.BACKEND_LIFECYCLE,
            DebugPhase.STARTED,
            agent_id=agent_id,
        )
        try:
            with self._generation_lock:
                generation, profile, binding = _prepare_provider_binding_locked(
                    self._adapter_generations, definition, agent_id
                )
                bound = BoundAgent(
                    self,
                    definition,
                    binding.staging_directory,
                    _provider_binding=binding,
                    _adapter_generation=generation,
                    _execution_profile=profile,
                )
                self._adapter_generations.retain_binding_locked(generation)
                self._bound_agents.add(bound)
        except BaseException as exc:
            self._record_debug_error(
                DebugOperation.BACKEND_LIFECYCLE,
                exc,
                agent_id=agent_id,
            )
            raise
        self._trace_debug(
            DebugOperation.BACKEND_LIFECYCLE,
            DebugPhase.SUCCEEDED,
            agent_id=agent_id,
            outcome=DebugOutcome.READY,
        )
        return bound

    @property
    def session_lifecycle_ensure_open(self) -> Callable[[], None]:
        return self._ensure_open

    @property
    def session_lifecycle_trace_debug(self) -> _LogicalTrace:
        return self._trace_debug

    @property
    def session_lifecycle_record_debug_error(self) -> Callable[..., None]:
        return self._record_debug_error

    @property
    def session_lifecycle_access_transactions(
        self,
    ) -> provider_session_access_transactions.ProviderSessionAccessTransactions:
        return self._access_transactions

    @property
    def session_lifecycle_transitions(self) -> provider_session_transitions.ProviderSessionTransitions:
        return self._session_transitions

    get_session_lifecycle = _get_session_lifecycle

    list_session_lifecycles = _list_session_lifecycles

    def provision_session_resources(
        self,
        session_id: str,
    ) -> GitSessionResourceInspection:
        """Provision and verify one exact Git triple without provider work."""

        self._ensure_open()
        session = self.workspace.validate_identifier(session_id)
        provisioned = _provision_session_resources(self.workspace.root, session)
        verified = _require_ready_session_resources(self.workspace.root, session)
        if verified != provisioned:
            raise BackendProtocolError(
                "session resources changed during provisioning"
            )
        return provisioned

    def assess_session_transition(
        self,
        *,
        lifecycle_state: SessionLifecycleState,
        requested_operation: SessionTransitionOperation,
        conversation_id: str | None = None,
        collaboration_store: CollaborationEventStore | None = None,
        reviewed_proposal: SessionReviewedProposalEvidence | None = None,
        artifact_application: SessionArtifactApplicationEvidence | None = None,
    ) -> SessionTransitionAssessment:
        """Delegate once to transition assessment and return its exact result."""

        self._ensure_open()
        if type(requested_operation) is not SessionTransitionOperation:
            raise BackendProtocolError(
                "session transition operation is invalid"
            )
        assessment = SessionTransitionAssessor(self.workspace.root).inspect(
            lifecycle_state=lifecycle_state,
            conversation_id=conversation_id,
            collaboration_store=collaboration_store,
            reviewed_proposal=reviewed_proposal,
            artifact_application=artifact_application,
        )
        if (
            type(assessment) is not SessionTransitionAssessment
            or assessment.session_id != lifecycle_state.session_id
        ):
            raise BackendProtocolError(
                "session transition assessment is invalid"
            )
        return assessment

    def validate_provider_configuration(self, session_id: str) -> bool:
        """Validate every provider route without lifecycle or provider mutation.

        Success returns exact ``True``. Invalid configuration or an unavailable
        provider executable raises its existing typed failure.
        """

        self._ensure_open()
        session = self.workspace.validate_identifier(session_id)
        self._trace_debug(
            DebugOperation.CONFIGURATION,
            DebugPhase.STARTED,
            session_id=session,
        )
        try:
            self._preflight_session(session)
        except BaseException as exc:
            self._record_debug_error(
                DebugOperation.CONFIGURATION,
                exc,
                session_id=session,
            )
            raise
        self._trace_debug(
            DebugOperation.CONFIGURATION,
            DebugPhase.SUCCEEDED,
            session_id=session,
            outcome=DebugOutcome.READY,
        )
        return True

    acquire_session_access = _acquire_session_access

    release_session_access = _release_session_access

    create_session_with_access = _create_session_with_access

    reopen_session_with_access = _reopen_session_with_access

    pause_session_and_release_access = _pause_session_and_release_access

    def close(self) -> None:
        """Retire bindings, then release claims and adapters after calls exit.

        Codex removes ephemeral staging; Antigravity intentionally retains
        provider-native state for later backend instances.
        """

        if self._closed:
            return
        self._trace_debug(
            DebugOperation.CLEANUP,
            DebugPhase.STARTED,
        )
        self._closed = True
        for bound_agent in tuple(self._bound_agents):
            bound_agent._retire()
        self._finalize_close_if_ready()

    def _finalize_bound_agent(self, bound_agent: BoundAgent) -> None:
        """Release one exact retired binding and finish a pending close."""

        if bound_agent not in self._bound_agents:
            return
        self._bound_agents.remove(bound_agent)
        generation = bound_agent._adapter_generation
        adapter = (
            self._adapter_for(bound_agent.vendor)
            if generation is None
            else generation.adapters[bound_agent.vendor]
        )
        close_generation = self._adapter_generations.release_binding(
            generation, adapter, bound_agent._provider_binding
        )
        if close_generation is not None:
            self._adapter_generations.close_generation(
                close_generation,
                record_error=lambda exc: self._record_debug_error(DebugOperation.CLEANUP, exc),
            )
        self._finalize_close_if_ready()

    def _discard_staging_directory(self, directory: Path) -> None:
        """Remove one staging resource at most once."""

        self._codex_adapter.discard_staging_directory(directory)

    def _finalize_close_if_ready(self) -> None:
        """Clean backend-global resources once no binding can still use them."""

        if not self._closed or self._bound_agents or self._close_cleanup_complete:
            return
        self._close_cleanup_complete = True
        release_failed = _release_backend_resources(
            claim_tokens=lambda: self._access_claim_tokens,
            thread_locks=lambda: self._thread_locks,
            context_thread_locks=lambda: self._context_thread_locks,
            active_adapters=lambda: self._adapters.values(),
            release_access_claim=lambda session, token: self._access_transactions.release_access_claim(
                self.workspace, session, token
            ),
            record_error=lambda exc, **fields: self._record_debug_error(
                DebugOperation.CLEANUP, exc, **fields
            ),
        )
        self._trace_debug(
            DebugOperation.CLEANUP,
            DebugPhase.FAILED if release_failed else DebugPhase.SUCCEEDED,
            outcome=(DebugOutcome.FAILED if release_failed else DebugOutcome.COMPLETED),
        )

    _invoke_codex_after_reference_read = _invoke_codex_after_reference_read
    _load_authorized_reference_context = _load_authorized_reference_context
    _complete_reference_read = _complete_reference_read
    _fail_reference_read_without_replacing = _fail_reference_read_without_replacing
    _terminate_reference_read_without_replacing = staticmethod(_terminate_reference_read_without_replacing)
    _taint_reference_thread_without_replacing = _taint_reference_thread_without_replacing

    @property
    def reference_read_continuation_fingerprint(
        self,
    ) -> Callable[[BoundAgent, ProviderExecutionWorkspace | None], str]:
        return self._legacy_continuation_fingerprint

    @property
    def reference_read_load_context(self) -> _ReferenceReadContextLoad[BoundAgent]:
        return self._load_authorized_reference_context

    legacy_provider_continuation_fingerprint = reference_read_continuation_fingerprint

    @property
    def legacy_provider_exchange_begin(self) -> _LegacyExchangeBegin[BoundAgent]:
        return self._begin_legacy_agent_exchange

    @property
    def legacy_provider_rejected_response_recorder(self) -> _RejectedResponseRecorder[BoundAgent]:
        return self._record_rejected_provider_response_without_replacing_failure

    @property
    def reference_read_invoke_structured(self) -> _ReferenceReadStructuredInvoke[BoundAgent]:
        return self._invoke_provider_structured_resilient

    @staticmethod
    def _artifact_reference_payload(
        reference: ArtifactReference,
    ) -> dict[str, object]:
        line_range = reference.line_range
        return {
            "path": reference.path,
            "line_range": (
                None
                if line_range is None
                else {"start": line_range.start, "end": line_range.end}
            ),
        }


    @property
    def routing_terminate_reference(
        self,
    ) -> Callable[[ReferenceReadInvocation, ReferenceAuthorizationErrorCode], None]:
        return self._terminate_reference_read_without_replacing

    @property
    def routing_invoke_codex(self) -> _NamedProviderRoute[BoundAgent, AgentRequest, AgentReply]:
        return self._invoke_codex

    @property
    def routing_converse_codex(self) -> _NamedProviderRoute[BoundAgent, ConversationRequest, ConversationReply]:
        return self._converse_codex

    @property
    def routing_converse_with_retrieval_codex(self) -> _NamedRetrievalRoute[BoundAgent]:
        return self._converse_with_retrieval_codex

    @property
    def routing_invoke_antigravity(self) -> _ProviderRoute[BoundAgent, AgentRequest, AgentReply]:
        return self._invoke_antigravity

    @property
    def routing_converse_antigravity(self) -> _ProviderRoute[BoundAgent, ConversationRequest, ConversationReply]:
        return self._converse_antigravity

    @property
    def routing_converse_with_retrieval_antigravity(self) -> _RetrievalRoute[BoundAgent]:
        return self._converse_with_retrieval_antigravity

    _invoke_provider = _invoke_provider

    _invoke_antigravity = _invoke_antigravity

    @property
    def direct_structured_invoke(self) -> _DirectStructuredInvoke[BoundAgent]:
        return self._invoke_provider_structured_resilient

    @property
    def direct_reference_read_invoke(self) -> _DirectReferenceReadInvoke[BoundAgent]:
        return self._invoke_codex_after_reference_read

    _invoke_codex = _invoke_codex

    _converse_provider = _converse_provider

    _converse_antigravity = _converse_antigravity

    _converse_codex = _converse_codex

    _converse_with_retrieval_provider = _converse_with_retrieval_provider

    _converse_with_retrieval_antigravity = _converse_with_retrieval_antigravity

    @property
    def retrieval_reference_load(self) -> _RetrievalReferenceLoad[BoundAgent]:
        return self._load_authorized_reference_context

    @property
    def retrieval_structured_invoke(self) -> _RetrievalStructuredInvoke[BoundAgent]:
        return self._invoke_provider_structured_resilient

    @property
    def retrieval_fallback_message(self) -> str:
        return _SAFE_STRUCTURED_FALLBACK_MESSAGE

    direct_structured_fallback_message = retrieval_fallback_message

    _converse_with_retrieval_codex = _converse_with_retrieval_codex

    _taint_retrieval_thread_without_replacing_failure = _taint_retrieval_thread_without_replacing_failure

    _record_rejected_provider_response_without_replacing_failure = _record_rejected_provider_response_without_replacing_failure

    _observe_provider_failure = _observe_provider_failure

    def _begin_agent_exchange(
        self,
        bound_agent: BoundAgent,
        *,
        session_id: str,
        scope_kind: AgentExchangeScopeKind,
        scope_id: str,
        invocation: ProviderInvocation,
        diagnostic_call_id: str | None = None,
    ) -> AgentExchangeCaptureLifecycle | None:
        """Begin one exact capture without affecting provider execution."""

        try:
            return self._agent_exchange_capture.begin(
                session_id=session_id,
                scope_kind=scope_kind,
                scope_id=scope_id,
                agent_id=bound_agent.definition.agent_id,
                provider=AgentExchangeProvider(bound_agent.vendor),
                invocation=invocation,
                diagnostic_call_id=diagnostic_call_id,
            )
        except BaseException:
            return None

    _begin_legacy_agent_exchange = _begin_legacy_agent_exchange

    @staticmethod
    def _accept_context_finalizer_without_replacing(
        finalizer: ContextProviderCallFinalizer,
    ) -> None:
        """Best-effort accept one backend-owned contextual prelude."""

        try:
            finalizer.accepted()
        except BaseException:
            pass

    @staticmethod
    def _reject_context_finalizer_without_replacing(
        finalizer: ContextProviderCallFinalizer,
        code: ContextProviderTerminalCode,
    ) -> None:
        """Best-effort reject one backend-owned contextual prelude."""

        try:
            finalizer.rejected(code)
        except BaseException:
            pass

    @staticmethod
    def _cancel_context_finalizer_without_replacing(
        finalizer: ContextProviderCallFinalizer,
    ) -> None:
        """Best-effort cancel one backend-owned contextual prelude."""

        try:
            finalizer.cancelled()
        except BaseException:
            pass

    @staticmethod
    def _fail_context_finalizer_without_replacing(
        finalizer: ContextProviderCallFinalizer,
        code: ContextProviderTerminalCode,
    ) -> None:
        """Best-effort fail one backend-owned contextual prelude."""

        try:
            finalizer.post_response_runtime_failure(code)
        except BaseException:
            pass

    _invoke_provider_structured_resilient = _invoke_provider_structured_resilient

    _stage_validation_details = staticmethod(_stage_validation_details)

    _attach_validation_details = staticmethod(_attach_validation_details)

    _attach_reply_trace = staticmethod(_attach_reply_trace)
    @staticmethod
    def _legacy_continuation_fingerprint(
        bound_agent: BoundAgent,
        execution_workspace: ProviderExecutionWorkspace | None,
    ) -> str:
        """Bind workspace-backed thread compatibility without changing its schema."""

        fingerprint = hashlib.sha256((WIRE_VERSION + "\0" + bound_agent.definition.fingerprint).encode("utf-8")).hexdigest()
        if bound_agent.vendor not in {"codex", "antigravity"}:
            return fingerprint
        if type(execution_workspace) is not ProviderExecutionWorkspace:
            raise BackendProtocolError("provider execution workspace is unavailable")
        return execution_workspace.continuation_digest(
            "legacy-definition-policy",
            fingerprint,
        ).removeprefix("sha256:")

    @staticmethod
    def _require_postsubmission_workspace(
        invocation: ProviderInvocation,
    ) -> None:
        """Override apparent transport success when workspace authority was lost."""

        workspace = invocation.execution_workspace
        if workspace is None:
            return
        try:
            workspace.require_current()
        except BaseException as failure:
            record_provider_failure_accounting(
                failure,
                state=ProviderCallState.SUBMITTED,
                actual_transport_utf8_bytes=(
                    None
                    if invocation.payload_json is None
                    else len(invocation.payload_json)
                ),
            )
            raise

    _invoke_provider_structured = _invoke_provider_structured

    @staticmethod
    def _context_failure_timed_out(failure: BaseException) -> bool:
        """Recognize explicit process or provider timeout facts in the cause chain."""

        current: BaseException | None = failure
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            if isinstance(current, ProcessTimedOut) or (
                isinstance(current, ProviderInvocationError) and current.timed_out
            ):
                return True
            seen.add(id(current))
            current = current.__cause__
        return False

    _effective_context_continuation_fingerprints = staticmethod(
        _effective_context_continuation_fingerprints,
    )

    _invoke_context_physical = _invoke_context_physical

    async def _prepare_context_references(
        self,
        bound_agent: BoundAgent,
        reference_read: ReferenceReadInvocation,
        request: ContextReferencePreludeRequest,
    ) -> ContextReferencePreludeResult:
        """Run the established exact reference-read action on a scoped thread."""

        reference_request = reference_read.request
        if (
            type(reference_request) is not ReferenceReadRequest
            or reference_request.correlation.operation_id
            != request.reference_operation_id
            or reference_request.correlation.agent_id != request.scope.agent_id
        ):
            raise ReferenceAuthorizationError(
                ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH
            )
        prelude = await self._load_authorized_reference_context(
            bound_agent,
            session_id=request.scope.session_id,
            prelude_request=request,
            reference_read=reference_read,
            execution_workspace=request.execution_workspace,
            continuation_fingerprint=self._legacy_continuation_fingerprint(
                bound_agent, request.execution_workspace,
            ),
            provider_name=(
                "Codex" if bound_agent.vendor == "codex" else "Antigravity"
            ),
        )
        return prelude

    def _context_thread_lock(
        self,
        scope: ContextScope,
        provider_id: str,
        definition_fingerprint: str,
        policy_fingerprint: str,
    ) -> asyncio.Lock:
        """Return the process-local lock for one complete contextual lifecycle."""

        self._ensure_open()
        if type(scope) is not ContextScope:
            raise ContextExecutionError(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        session = self.workspace.validate_identifier(scope.session_id)
        key = (
            scope.workspace_id,
            session,
            scope.subject_kind.value,
            scope.subject_id,
            scope.agent_id,
            provider_id,
            definition_fingerprint,
            policy_fingerprint,
        )
        lock = self._context_thread_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._context_thread_locks[key] = lock
        return lock

    def _thread_lock(
        self,
        session_id: str,
        vendor: str,
        agent_id: str,
    ) -> asyncio.Lock:
        self._ensure_open()
        session = self.workspace.validate_identifier(session_id)
        key = (session, vendor, agent_id)
        lock = self._thread_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._thread_locks[key] = lock
        return lock

    def _ensure_open(self) -> None:
        if self._closed:
            raise BackendProtocolError("agent backend is closed")

    def _adapter_for(self, vendor: str) -> ProviderAdapter:
        """Return one registered provider adapter with the established error."""

        adapter = self._adapters.get(vendor)
        if adapter is None:
            raise ConfigurationError(f"agent backend is not implemented: {vendor}")
        return adapter

    def _stage_codex_definition(self, definition: AgentDefinition) -> Path:
        """Compatibility delegate for the former backend-private helper."""

        return self._codex_adapter.stage_definition(definition)

    def _codex_command(
        self,
        staging_directory: Path,
        schema_path: Path,
        output_path: Path,
        thread_id: str | None,
        agent_id: str | None = None,
    ) -> list[str]:
        """Compatibility delegate preserving the former private test seam."""

        return self._codex_adapter.command(
            staging_directory,
            schema_path,
            output_path,
            thread_id,
            agent_id,
        )

    def _codex_invocation_settings(
        self,
        agent_id: str | None,
    ) -> tuple[str | None, str | None]:
        """Compatibility delegate for construction-snapshot Codex flags."""

        return self._codex_adapter.invocation_settings(agent_id)

    @staticmethod
    async def _stop_process(process: asyncio.subprocess.Process) -> None:
        """Compatibility delegate for the shared process cleanup primitive."""

        await stop_process(process, wait_for=asyncio.wait_for)



    @staticmethod
    def _parse_events(stdout: bytes) -> tuple[dict[str, Any], ...]:
        """Compatibility delegate for Codex JSONL event parsing."""

        try:
            return CodexAdapter.parse_events(stdout)
        except ProviderInvocationError as exc:
            raise BackendProtocolError(exc.diagnostic) from exc

    @staticmethod
    def _started_thread(events: tuple[dict[str, Any], ...]) -> str | None:
        """Compatibility delegate for Codex thread-start extraction."""

        return CodexAdapter.started_thread(events)


    def _preflight_session(self, session_id: str) -> None:
        """Validate configured provider routes without acquiring access."""

        runtime_overrides = self.workspace.load_session_overrides(session_id)
        manager = ConfigManager(self.workspace)
        resolved_backends: set[str] = set()
        for agent_id in ROSTER:
            resolved = manager.resolve_from_snapshot(
                self._configuration_snapshot,
                agent_id,
                runtime_overrides,
                self.backend_overrides,
            )
            resolved_backends.add(resolved.backend)
            self._adapter_for(resolved.backend)
        for vendor in resolved_backends:
            adapter = self._adapter_for(vendor)
            if not adapter.executable_available():
                if vendor == "codex":
                    raise BackendProtocolError(
                        f"Codex CLI executable was not found: {self.executable}"
                    )
                raise BackendProtocolError("Antigravity CLI executable was not found")
            if (
                vendor == "antigravity"
                and not self._antigravity_adapter.headless_capabilities_available()
            ):
                raise BackendProtocolError(
                    "Antigravity CLI headless capabilities could not be verified"
                )

    @property
    def context_provider_ensure_open(self) -> Callable[[], None]:
        return self._ensure_open

    @property
    def context_provider_adapter_lookup(self) -> Callable[[str], ProviderAdapter]:
        return self._adapter_for

    @property
    def context_provider_active_generation(self) -> _AdapterGeneration:
        return self._adapter_generations.active

    @property
    def context_provider_activity(self) -> Callable[[str], AbstractAsyncContextManager[int]]:
        return provider_activity

    @property
    def context_provider_accounting_sink(self) -> Callable[[ProviderCallAccounting], object] | None:
        return self._context_accounting_sink

    @property
    def context_provider_timeout_check(self) -> Callable[[BaseException], bool]:
        return self._context_failure_timed_out

    @property
    def context_provider_postsubmission_check(self) -> Callable[[ProviderInvocation], None]:
        return self._require_postsubmission_workspace

    @property
    def context_provider_exchange_begin(self) -> _ContextExchangeBegin[BoundAgent]:
        return self._begin_agent_exchange

    @property
    def context_provider_effective_fingerprints(
        self,
    ) -> Callable[[BoundAgent, str, ProviderExecutionWorkspace | None], tuple[str, str]]:
        return self._effective_context_continuation_fingerprints

    @property
    def context_provider_ready_check(self) -> Callable[[str], None]:
        return self._assert_provider_work_ready

    @property
    def context_provider_owned_access_check(self) -> Callable[[Workspace, str], None]:
        return self._access_transactions.assert_owned_access

    _assert_provider_work_ready = _assert_provider_work_ready


    logical_thread_disposition = _logical_thread_disposition
    logical_confirm_execution_profile = _confirm_execution_profile
    logical_record_debug_error = _record_debug_error

    @property
    def logical_provider_thread_lock(self) -> Callable[[str, str, str], asyncio.Lock]:
        return self._thread_lock

    @property
    def logical_provider_trace_debug(self) -> _LogicalTrace:
        return self._trace_debug

    @property
    def logical_provider_invoke(self) -> _LogicalProviderDispatch[BoundAgent, AgentRequest, AgentReply]:
        return self._invoke_provider

    @property
    def logical_provider_converse(
        self,
    ) -> _LogicalProviderDispatch[BoundAgent, ConversationRequest, ConversationReply]:
        return self._converse_provider

    @property
    def logical_provider_converse_with_retrieval(self) -> _LogicalRetrievalDispatch[BoundAgent]:
        return self._converse_with_retrieval_provider


@contextmanager
def provider_execution_workspace_manager(
    bound_agent: BoundAgent,
    binding: SessionArtifactBinding,
) -> Iterator[ProviderExecutionWorkspace]:
    """Open one workspace-backed provider capability with host-owned setup."""

    if (
        type(bound_agent) is not BoundAgent
        or type(binding) is not SessionArtifactBinding
        or bound_agent.vendor not in {"codex", "antigravity"}
    ):
        raise BackendProtocolError("provider execution workspace is unavailable")
    if bound_agent.vendor == "antigravity":
        try:
            generation = bound_agent._adapter_generation
            adapter = (
                bound_agent.backend._adapter_for("antigravity")
                if generation is None
                else generation.adapters["antigravity"]
            )
            if type(adapter) is not AntigravityAdapter:
                raise OSError("Antigravity provider adapter is invalid")
            role = adapter.session_role_projection(
                bound_agent._provider_binding
            )
            with binding.open_ready_resources() as access:
                access.install_provider_role(role)
        except OSError as failure:
            raise BackendProtocolError(
                "Antigravity session role projection is unavailable"
            ) from failure
    with binding.open_provider_execution_workspace() as workspace:
        yield workspace


async def invoke_story_bound_agent(
    bound_agent: BoundAgent,
    request: AgentRequest,
    execution_workspace: ProviderExecutionWorkspace,
    *,
    reference_read: ReferenceReadInvocation | None = None,
) -> AgentReply:
    """Invoke one StoryRuntime call through the internal capability seam."""

    if type(bound_agent) is not BoundAgent:
        raise TypeError("bound_agent must be exact")
    if type(execution_workspace) is not ProviderExecutionWorkspace:
        raise TypeError("execution_workspace must be exact")
    return await bound_agent._invoke_logical(
        request,
        reference_read=reference_read,
        execution_workspace=execution_workspace,
    )


def release_bound_agent(bound_agent: object) -> None:
    """Retire one concrete package binding without exposing backend internals."""

    if isinstance(bound_agent, BoundAgent):
        bound_agent._retire()


def backend_configuration_snapshot(
    backend: AgentBackend,
) -> ConfigurationSnapshot:
    """Return one backend's immutable construction-time configuration."""

    if not isinstance(backend, AgentBackend):
        raise TypeError("backend must be an AgentBackend")
    return backend._configuration_snapshot
