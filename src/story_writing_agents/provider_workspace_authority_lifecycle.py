"""Open provider workspaces and hand off correlated reference context."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator

from .agent_context.invocation.provider import (
    ContextReferencePreludeRequest,
    ContextReferencePreludeResult,
)
from .antigravity_adapter import AntigravityAdapter
from .errors import BackendProtocolError
from .provider_adapter import ProviderAdapter, ProviderBinding
from .provider_adapter_generations import AdapterGeneration
from .provider_execution_workspace import ProviderExecutionWorkspace
from .workspace.session_artifact_binding import SessionArtifactBinding
from .writing_artifacts.read_actions import (
    ReferenceAuthorizationError,
    ReferenceAuthorizationErrorCode,
    ReferenceReadInvocation,
    ReferenceReadRequest,
)


def open_bound_provider_execution_workspace(
    binding: SessionArtifactBinding,
    *,
    vendor: Callable[[], str],
    current_generation: Callable[[], AdapterGeneration | None],
    fallback_adapter: Callable[[], ProviderAdapter],
    provider_binding: Callable[[], ProviderBinding],
    antigravity_adapter_type: Callable[[], type[AntigravityAdapter]],
) -> Iterator[ProviderExecutionWorkspace]:
    if vendor() == "antigravity":
        try:
            generation = current_generation()
            adapter = (
                fallback_adapter()
                if generation is None
                else generation.adapters["antigravity"]
            )
            if type(adapter) is not antigravity_adapter_type():
                raise OSError("Antigravity provider adapter is invalid")
            role = adapter.session_role_projection(
                provider_binding()
            )
            with binding.open_ready_resources() as access:
                pass
        except OSError as failure:
            raise BackendProtocolError(
                "Antigravity session role projection is unavailable"
            ) from failure
    with binding.open_provider_execution_workspace() as workspace:
        yield workspace


async def prepare_context_reference_handoff(
    reference_read: ReferenceReadInvocation,
    request: ContextReferencePreludeRequest,
    *,
    load_prelude: Callable[[], Awaitable[ContextReferencePreludeResult]],
) -> ContextReferencePreludeResult:
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
    prelude = await load_prelude()
    return prelude
