"""Best-effort terminal projection for provider exchange capture."""

from __future__ import annotations

import json

from ..agent_context.invocation.provider import ContextProviderTerminalCode
from ..provider_adapter import ProviderInvocationError, ProviderResult
from .capture import AgentExchangeCaptureLifecycle, AgentExchangeTerminalCode

__all__ = [
    "ContextExchangeFinalizer",
    "capture_accepted_without_replacing",
    "capture_cancelled_without_replacing",
    "capture_provider_failure_without_replacing",
    "capture_provider_return_without_replacing",
    "capture_rejected_without_replacing",
    "capture_runtime_failure_without_replacing",
    "structured_rejection_code",
]

_CONTEXT_REJECTION_CODES = {
    ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE: (
        AgentExchangeTerminalCode.MALFORMED_STRUCTURED_RESPONSE
    ),
    ContextProviderTerminalCode.RESPONSE_CORRELATION_MISMATCH: (
        AgentExchangeTerminalCode.RESPONSE_CORRELATION_MISMATCH
    ),
    ContextProviderTerminalCode.RESPONSE_SEMANTIC_REJECTED: (
        AgentExchangeTerminalCode.RESPONSE_SEMANTIC_REJECTED
    ),
    ContextProviderTerminalCode.ACTION_EXHAUSTED: (
        AgentExchangeTerminalCode.ACTION_EXHAUSTED
    ),
    ContextProviderTerminalCode.ANSWER_VALIDATION_FAILED: (
        AgentExchangeTerminalCode.ANSWER_VALIDATION_FAILED
    ),
}

_CONTEXT_RUNTIME_FAILURE_CODES = {
    ContextProviderTerminalCode.REFERENCE_RUNTIME_FAILED: (
        AgentExchangeTerminalCode.REFERENCE_RUNTIME_FAILED
    ),
    ContextProviderTerminalCode.CONTEXT_RUNTIME_FAILED: (
        AgentExchangeTerminalCode.CONTEXT_RUNTIME_FAILED
    ),
}


class ContextExchangeFinalizer:
    """Adapt one capture lifecycle to the provider-neutral context protocol."""

    __slots__ = ("_lifecycle", "_validation_details", "_diagnostic_task")

    def __init__(
        self,
        lifecycle: AgentExchangeCaptureLifecycle | None,
    ) -> None:
        self._lifecycle = lifecycle
        self._validation_details: tuple[str, ...] = ()
        self._diagnostic_task: tuple[str, int] | None = None

    def set_host_task_diagnostic(self, task_id: str, task_revision: int) -> None:
        """Retain a validated host binding without changing provider input."""
        if type(task_id) is str and task_id and type(task_revision) is int and task_revision > 0:
            self._diagnostic_task = (task_id, task_revision)

    def set_validation_details(self, details: tuple[str, ...]) -> None:
        """Retain trusted findings for this rejected response."""
        self._validation_details = details

    def accepted(self) -> None:
        """Best-effort record one accepted contextual response."""

        try:
            if self._lifecycle is not None:
                self._lifecycle.accepted(diagnostic_task=self._diagnostic_task)
        except BaseException:
            pass

    def rejected(self, code: ContextProviderTerminalCode) -> None:
        """Best-effort map one provider-neutral semantic rejection."""

        try:
            mapped = _CONTEXT_REJECTION_CODES.get(code)
            if self._lifecycle is not None and mapped is not None:
                self._lifecycle.rejected(
                    mapped, validation_details=self._validation_details,
                )
        except BaseException:
            pass

    def cancelled(self) -> None:
        """Best-effort record contextual cancellation."""

        try:
            if self._lifecycle is not None:
                self._lifecycle.cancelled()
        except BaseException:
            pass

    def post_response_runtime_failure(
        self,
        code: ContextProviderTerminalCode,
    ) -> None:
        """Best-effort map one provider-neutral runtime failure."""

        try:
            mapped = _CONTEXT_RUNTIME_FAILURE_CODES.get(code)
            if self._lifecycle is not None and mapped is not None:
                self._lifecycle.post_response_runtime_failure(mapped)
        except BaseException:
            pass


def capture_provider_return_without_replacing(
    lifecycle: AgentExchangeCaptureLifecycle | None,
    result: object,
) -> bool:
    """Record the first valid provider response action, or no-response failure."""

    try:
        if type(result) is ProviderResult:
            if lifecycle is not None:
                lifecycle.record_response(result)
            return True
        if lifecycle is not None:
            lifecycle.provider_failure(
                AgentExchangeTerminalCode.PROVIDER_CONTRACT_INVALID
            )
    except BaseException:
        pass
    return False


def capture_provider_failure_without_replacing(
    lifecycle: AgentExchangeCaptureLifecycle | None,
    code: AgentExchangeTerminalCode,
    *,
    failure: ProviderInvocationError | None = None,
) -> None:
    """Best-effort record one closed pre-response provider failure."""

    try:
        if lifecycle is not None:
            diagnostic = (
                failure.response_diagnostic
                if isinstance(failure, ProviderInvocationError)
                else None
            )
            if diagnostic is None:
                lifecycle.provider_failure(code)
            else:
                lifecycle.provider_failure(code, response_diagnostic=diagnostic)
    except BaseException:
        pass


def capture_cancelled_without_replacing(
    lifecycle: AgentExchangeCaptureLifecycle | None,
) -> None:
    """Best-effort record cancellation without changing propagation."""

    try:
        if lifecycle is not None:
            lifecycle.cancelled()
    except BaseException:
        pass


def capture_rejected_without_replacing(
    lifecycle: AgentExchangeCaptureLifecycle | None,
    code: AgentExchangeTerminalCode,
    *,
    validation_details: tuple[str, ...] = (),
) -> None:
    """Best-effort record one closed response rejection with its findings."""
    try:
        if lifecycle is not None:
            lifecycle.rejected(code, validation_details=validation_details)
    except BaseException:
        pass


def capture_runtime_failure_without_replacing(
    lifecycle: AgentExchangeCaptureLifecycle | None,
    code: AgentExchangeTerminalCode,
) -> None:
    """Best-effort record one post-response runtime failure."""

    try:
        if lifecycle is not None:
            lifecycle.post_response_runtime_failure(code)
    except BaseException:
        pass


def capture_accepted_without_replacing(
    lifecycle: AgentExchangeCaptureLifecycle | None,
) -> None:
    """Best-effort record one accepted response."""

    try:
        if lifecycle is not None:
            pass # Validation-only omitted terminal record.
    except BaseException:
        pass


def structured_rejection_code(
    failure: BaseException,
) -> AgentExchangeTerminalCode:
    """Classify only typed malformed causes; never inspect error text."""

    current: BaseException | None = failure
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if isinstance(current, (json.JSONDecodeError, UnicodeError)):
            return AgentExchangeTerminalCode.MALFORMED_STRUCTURED_RESPONSE
        seen.add(id(current))
        current = current.__cause__
    return AgentExchangeTerminalCode.RESPONSE_SEMANTIC_REJECTED
