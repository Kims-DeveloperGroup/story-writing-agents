"""Contextual invocation lifecycle for one bound provider."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, AbstractContextManager, ExitStack, asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Protocol

import story_writing_agents.provider_context_thread_store as provider_context_thread_store

from .agent_context.contracts.errors import ContextExecutionError, ContextExecutionErrorCode
from .agent_context.invocation.provider import (
    BoundContextProviderCallRequest,
    ContextProviderCallRequest,
    ContextProviderCallResult,
    ContextReferencePreludeRequest,
    ContextReferencePreludeResult,
)
from .agent_context.manifest import ContextContinuationIdentity, ContextScope
from .errors import BackendProtocolError
from .models import AgentDefinition
from .provider_execution_workspace import ProviderExecutionWorkspace
from .workspace import Workspace
from .workspace.session_artifact_binding import SessionArtifactBinding
from .writing_artifacts.read_actions import (
    ReferenceAuthorizationError,
    ReferenceAuthorizationErrorCode,
    ReferenceReadInvocation,
    ReferenceReadRequest,
)


class ContextProviderThreadStoreAccess(Protocol):
    """Workspace and current access checks for the provider's thread store."""

    @property
    def workspace(self) -> Workspace: ...

    @property
    def context_provider_ready_check(self) -> Callable[[str], None]: ...

    @property
    def context_provider_owned_access_check(self) -> Callable[[Workspace, str], None]: ...


class _BoundContextIdentity(Protocol):
    @property
    def definition(self) -> AgentDefinition: ...

    @property
    def vendor(self) -> str: ...


class _BoundContextBinding(Protocol):
    """Backend-owned operations used at each point of the invocation lifecycle."""

    @property
    def agent(self) -> _BoundContextIdentity: ...

    @property
    def thread_store_access(self) -> ContextProviderThreadStoreAccess: ...

    @property
    def compose_session_artifact_binding(self) -> Callable[[Path, str], SessionArtifactBinding]: ...

    def open_provider_execution_workspace(
        self, binding: SessionArtifactBinding,
    ) -> AbstractContextManager[ProviderExecutionWorkspace]: ...

    @property
    def reference_call_use(
        self,
    ) -> Callable[[ReferenceReadInvocation | None], AbstractContextManager[None]]: ...

    @property
    def context_thread_lock(self) -> Callable[[ContextScope, str, str, str], asyncio.Lock]: ...

    @property
    def reference_thread_lock(
        self,
    ) -> Callable[[asyncio.Lock, ReferenceReadInvocation | None], AbstractAsyncContextManager[None]]: ...

    @property
    def terminate_reference_before_provider(
        self,
    ) -> Callable[[ReferenceReadInvocation | None, BaseException], None]: ...

    def prepare_context_references(
        self, reference_read: ReferenceReadInvocation, request: ContextReferencePreludeRequest,
    ) -> Awaitable[ContextReferencePreludeResult]: ...

    @property
    def reference_terminated_attribute(self) -> str: ...

    def invoke_context(
        self,
        request: ContextProviderCallRequest | BoundContextProviderCallRequest,
        *,
        execution_workspace: ProviderExecutionWorkspace | None,
    ) -> Awaitable[ContextProviderCallResult]: ...

    @property
    def terminate_reference(
        self,
    ) -> Callable[[ReferenceReadInvocation, ReferenceAuthorizationErrorCode], None]: ...

    def effective_context_fingerprints(
        self, policy_fingerprint: str, execution_workspace: ProviderExecutionWorkspace | None,
    ) -> tuple[str, str]: ...


class BoundContextProvider:
    """Bind the service-owned contextual protocol to one backend binding."""

    def __init__(
        self,
        binding: _BoundContextBinding,
        reference_read: ReferenceReadInvocation | None,
    ) -> None:
        bound_agent = binding.agent
        self._binding = binding
        self._bound_agent = bound_agent
        self._reference_read = reference_read
        self._reference_prepared = False
        self._reference_terminal = False
        self._active_scope: ContextScope | None = None
        self._continuation_identity: ContextContinuationIdentity | None = None
        self._active_policy_fingerprint: str | None = None
        self._active_execution_workspace: ProviderExecutionWorkspace | None = None
        if reference_read is None:
            self.reference_operation_id = None
        else:
            request = getattr(reference_read, "request", None)
            methods = (
                "handle_reference_read_action",
                "record_result_received",
                "accept",
                "terminate",
            )
            if type(request) is not ReferenceReadRequest or any(
                not callable(getattr(reference_read, name, None)) for name in methods
            ):
                raise TypeError(
                    "reference_read must provide a synchronous ReferenceReadInvocation"
                )
            for name in methods:
                method = getattr(reference_read, name)
                method_call = getattr(method, "__call__", None)
                if (
                    inspect.iscoroutinefunction(method)
                    or inspect.isasyncgenfunction(method)
                    or inspect.iscoroutinefunction(method_call)
                    or inspect.isasyncgenfunction(method_call)
                ):
                    raise TypeError(
                        "reference_read must provide synchronous lifecycle methods"
                    )
            if request.correlation.agent_id != bound_agent.definition.agent_id:
                raise ReferenceAuthorizationError(
                    ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH
                )
            self.reference_operation_id = request.correlation.operation_id

    @property
    def provider_id(self) -> str:
        return self._bound_agent.vendor

    @property
    def definition_fingerprint(self) -> str:
        return f"sha256:{self._bound_agent.definition.fingerprint}"

    @asynccontextmanager
    async def invocation(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
        *,
        continuation_identity: ContextContinuationIdentity | None = None,
        execution_workspace: ProviderExecutionWorkspace | None = None,
    ) -> AsyncIterator[None]:
        if (
            type(scope) is not ContextScope
            or scope.agent_id != self._bound_agent.definition.agent_id
            or type(policy_fingerprint) is not str
            or self._active_scope is not None
        ):
            raise ContextExecutionError(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        entered = False
        try:
            with ExitStack() as workspace_stack:
                active_execution_workspace = execution_workspace
                if (
                    self.provider_id in {"codex", "antigravity"}
                    and active_execution_workspace is None
                ):
                    binding = self._binding.compose_session_artifact_binding(
                        self._binding.thread_store_access.workspace.root,
                        scope.session_id,
                    )
                    active_execution_workspace = workspace_stack.enter_context(
                        self._binding.open_provider_execution_workspace(binding)
                    )
                definition_fingerprint, effective_policy_fingerprint = (
                    self._effective_context_fingerprints(
                        policy_fingerprint,
                        active_execution_workspace,
                    )
                )
                with self._binding.reference_call_use(self._reference_read):
                    if continuation_identity is not None and (
                        type(continuation_identity)
                        is not ContextContinuationIdentity
                        or not continuation_identity.accepts(scope)
                    ):
                        raise ContextExecutionError(
                            ContextExecutionErrorCode.PROVIDER_CONTRACT
                        )
                    storage_scope = (
                        scope
                        if continuation_identity is None
                        else continuation_identity.checkpoint_scope()
                    )
                    lock = self._binding.context_thread_lock(
                        storage_scope,
                        self.provider_id,
                        definition_fingerprint,
                        effective_policy_fingerprint,
                    )
                    async with self._binding.reference_thread_lock(
                        lock,
                        self._reference_read,
                    ):
                        self._active_scope = scope
                        self._continuation_identity = continuation_identity
                        self._active_policy_fingerprint = policy_fingerprint
                        self._active_execution_workspace = (
                            active_execution_workspace
                        )
                        entered = True
                        body_failure: BaseException | None = None
                        try:
                            try:
                                yield
                            except BaseException as failure:
                                body_failure = failure
                                raise
                        finally:
                            try:
                                if active_execution_workspace is not None:
                                    active_execution_workspace.require_current()
                            except BaseException as validation_failure:
                                try:
                                    backend = self._binding.thread_store_access
                                    provider_context_thread_store.taint_context_thread(
                                        backend.workspace,
                                        storage_scope,
                                        self.provider_id,
                                        definition_fingerprint,
                                        effective_policy_fingerprint,
                                        require_owned_access=backend.context_provider_owned_access_check,
                                    )
                                except BaseException:
                                    pass
                                if body_failure is None:
                                    raise
                                try:
                                    body_failure.add_note(
                                        "provider execution workspace exit validation "
                                        "failed: "
                                        f"{type(validation_failure).__name__}"
                                    )
                                except BaseException:
                                    pass
                            finally:
                                self._active_scope = None
                                self._continuation_identity = None
                                self._active_policy_fingerprint = None
                                self._active_execution_workspace = None
        except BaseException as failure:
            if not entered:
                self._binding.terminate_reference_before_provider(
                    self._reference_read,
                    failure,
                )
            raise

    def _require_active(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
    ) -> None:
        if (
            scope != self._active_scope
            or policy_fingerprint != self._active_policy_fingerprint
        ):
            raise ContextExecutionError(ContextExecutionErrorCode.PROVIDER_CONTRACT)

    def _active_workspace(
        self,
        supplied: ProviderExecutionWorkspace | None,
    ) -> ProviderExecutionWorkspace | None:
        """Resolve only the capability owned by this active invocation."""

        active = self._active_execution_workspace
        if self.provider_id not in {"codex", "antigravity"}:
            return supplied
        if type(active) is not ProviderExecutionWorkspace or (
            supplied is not None and supplied is not active
        ):
            raise ContextExecutionError(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        active.require_current()
        return active

    def inspect_context_thread(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
        *,
        execution_workspace: ProviderExecutionWorkspace | None = None,
    ) -> str | None:
        self._require_active(scope, policy_fingerprint)
        execution_workspace = self._active_workspace(execution_workspace)
        definition_fingerprint, effective_policy_fingerprint = (
            self._effective_context_fingerprints(
                policy_fingerprint,
                execution_workspace,
            )
        )
        backend = self._binding.thread_store_access
        return provider_context_thread_store.load_context_thread(
            backend.workspace,
            scope if self._continuation_identity is None
            else self._continuation_identity.checkpoint_scope(),
            self.provider_id,
            definition_fingerprint,
            effective_policy_fingerprint,
            require_ready=backend.context_provider_ready_check,
        )

    def require_current_execution_workspace(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
    ) -> None:
        """Revalidate the capability retained by this active invocation."""

        self._require_active(scope, policy_fingerprint)
        self._active_workspace(None)

    async def prepare_references(
        self,
        request: ContextReferencePreludeRequest,
    ) -> ContextReferencePreludeResult:
        self._require_active(request.scope, request.policy_fingerprint)
        if request.continuation_identity != self._continuation_identity:
            raise ContextExecutionError(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        execution_workspace = self._active_workspace(request.execution_workspace)
        if execution_workspace is not request.execution_workspace:
            request = replace(
                request,
                execution_workspace=execution_workspace,
            )
        if (
            self._reference_read is None
            or self._reference_prepared
            or self._reference_terminal
            or request.reference_operation_id != self.reference_operation_id
        ):
            raise ReferenceAuthorizationError(
                ReferenceAuthorizationErrorCode.INVALID_TRANSITION
            )
        try:
            result = await self._binding.prepare_context_references(
                self._reference_read,
                request,
            )
        except BaseException as failure:
            if (
                getattr(
                    failure,
                    self._binding.reference_terminated_attribute,
                    False,
                )
                is True
            ):
                self._reference_terminal = True
            raise
        self._active_workspace(execution_workspace)
        self._reference_prepared = True
        return result

    async def invoke_context(
        self,
        request: ContextProviderCallRequest | BoundContextProviderCallRequest,
    ) -> ContextProviderCallResult:
        if type(request) not in {ContextProviderCallRequest, BoundContextProviderCallRequest}:
            raise ContextExecutionError(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        self._require_active(request.scope, request.policy_fingerprint)
        if request.continuation_identity != self._continuation_identity:
            raise ContextExecutionError(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        execution_workspace = self._active_workspace(request.execution_workspace)
        result = await self._binding.invoke_context(
            request,
            execution_workspace=execution_workspace,
        )
        self._active_workspace(execution_workspace)
        return result

    def complete_references(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
    ) -> None:
        self._require_active(scope, policy_fingerprint)
        execution_workspace = self._active_workspace(None)
        if self._reference_read is None:
            return
        if not self._reference_prepared or self._reference_terminal:
            raise ReferenceAuthorizationError(
                ReferenceAuthorizationErrorCode.INVALID_TRANSITION
            )
        try:
            for name in ("record_result_received", "accept"):
                if execution_workspace is not None:
                    execution_workspace.require_current()
                outcome = getattr(self._reference_read, name)()
                if inspect.isawaitable(outcome):
                    if inspect.iscoroutine(outcome):
                        outcome.close()
                    raise BackendProtocolError(
                        f"reference read {name}() returned an awaitable"
                    )
                if inspect.isasyncgen(outcome):
                    raise BackendProtocolError(
                        f"reference read {name}() returned an async generator"
                    )
                if outcome is not None:
                    raise BackendProtocolError(
                        f"reference read {name}() returned a value"
                    )
        except BaseException:
            self._terminate_reference(ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE)
            raise
        self._reference_terminal = True

    def abort_references(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
        *,
        cancelled: bool,
        provider_failure: bool,
    ) -> None:
        self._require_active(scope, policy_fingerprint)
        self._active_workspace(None)
        if self._reference_read is None or self._reference_terminal:
            return
        if cancelled:
            outcome = ReferenceAuthorizationErrorCode.CANCELLED
        elif provider_failure:
            outcome = ReferenceAuthorizationErrorCode.PROVIDER_FAILURE
        else:
            outcome = ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE
        self._terminate_reference(outcome)

    def _terminate_reference(
        self,
        outcome: ReferenceAuthorizationErrorCode,
    ) -> None:
        assert self._reference_read is not None
        self._reference_terminal = True
        self._binding.terminate_reference(
            self._reference_read,
            outcome,
        )

    def taint_context_thread(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
        *,
        execution_workspace: ProviderExecutionWorkspace | None = None,
    ) -> None:
        self._require_active(scope, policy_fingerprint)
        execution_workspace = self._active_workspace(execution_workspace)
        definition_fingerprint, effective_policy_fingerprint = (
            self._effective_context_fingerprints(
                policy_fingerprint,
                execution_workspace,
            )
        )
        backend = self._binding.thread_store_access
        provider_context_thread_store.taint_context_thread(
            backend.workspace,
            scope if self._continuation_identity is None
            else self._continuation_identity.checkpoint_scope(),
            self.provider_id,
            definition_fingerprint,
            effective_policy_fingerprint,
            require_owned_access=backend.context_provider_owned_access_check,
        )

    def _effective_context_fingerprints(
        self,
        policy_fingerprint: str,
        execution_workspace: ProviderExecutionWorkspace | None,
    ) -> tuple[str, str]:
        """Bind Codex continuation compatibility to one session generation."""

        return self._binding.effective_context_fingerprints(
            policy_fingerprint,
            execution_workspace,
        )
