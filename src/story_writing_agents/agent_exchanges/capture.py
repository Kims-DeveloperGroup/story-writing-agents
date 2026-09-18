"""Fail-open recording lifecycle for one physical provider exchange."""

from __future__ import annotations

import base64
import json
import math
import threading
from enum import StrEnum
from uuid import uuid4

from ..provider_adapter import (
    ProviderInvocation,
    ProviderResponseDiagnostic,
    ProviderResult,
)
from ..workspace import Workspace
from .contracts import (
    AgentExchangeOutcome,
    AgentExchangeProvider,
    AgentExchangeRecordInput,
    AgentExchangeRequestType,
    AgentExchangeScopeKind,
)
from .storage import WorkspaceAgentExchangeStore


class AgentExchangeTerminalCode(StrEnum):
    """Closed content-safe terminal classifications for physical calls."""

    ACCEPTED = "accepted"
    PROVIDER_INVOCATION_FAILED = "provider_invocation_failed"
    PROVIDER_AUTHENTICATION_REQUIRED = "provider_authentication_required"
    PROVIDER_TIMED_OUT = "provider_timed_out"
    PROVIDER_CONTRACT_INVALID = "provider_contract_invalid"
    CANCELLED = "cancelled"
    MALFORMED_STRUCTURED_RESPONSE = "malformed_structured_response"
    RESPONSE_CORRELATION_MISMATCH = "response_correlation_mismatch"
    RESPONSE_SEMANTIC_REJECTED = "response_semantic_rejected"
    ACTION_EXHAUSTED = "action_exhausted"
    ANSWER_VALIDATION_FAILED = "answer_validation_failed"
    THREAD_PERSISTENCE_FAILED = "thread_persistence_failed"
    REFERENCE_RUNTIME_FAILED = "reference_runtime_failed"
    CONTEXT_RUNTIME_FAILED = "context_runtime_failed"


class _CaptureState(StrEnum):
    DISABLED = "disabled"
    REQUEST_RECORDED = "request_recorded"
    RESPONSE_ATTEMPTED = "response_attempted"
    TERMINAL_ATTEMPTED = "terminal_attempted"


_REJECTED_CODES = frozenset(
    {
        AgentExchangeTerminalCode.MALFORMED_STRUCTURED_RESPONSE,
        AgentExchangeTerminalCode.RESPONSE_CORRELATION_MISMATCH,
        AgentExchangeTerminalCode.RESPONSE_SEMANTIC_REJECTED,
        AgentExchangeTerminalCode.ACTION_EXHAUSTED,
        AgentExchangeTerminalCode.ANSWER_VALIDATION_FAILED,
    }
)
_PROVIDER_FAILURE_CODES = frozenset(
    {
        AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED,
        AgentExchangeTerminalCode.PROVIDER_AUTHENTICATION_REQUIRED,
        AgentExchangeTerminalCode.PROVIDER_TIMED_OUT,
        AgentExchangeTerminalCode.PROVIDER_CONTRACT_INVALID,
    }
)
_POST_RESPONSE_RUNTIME_CODES = frozenset(
    {
        AgentExchangeTerminalCode.THREAD_PERSISTENCE_FAILED,
        AgentExchangeTerminalCode.REFERENCE_RUNTIME_FAILED,
        AgentExchangeTerminalCode.CONTEXT_RUNTIME_FAILED,
    }
)


def _canonical_json(value: object) -> str:
    """Serialize one strict deterministic JSON value without ASCII escaping."""

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _request_content(invocation: ProviderInvocation) -> str:
    """Project only exact provider-neutral invocation fields."""

    if type(invocation) is not ProviderInvocation:
        raise TypeError("invocation must be a ProviderInvocation")
    return _canonical_json(
        {
            "call_kind": invocation.call_kind.value,
            "payload": dict(invocation.payload),
            "prior_thread_id": invocation.thread_id,
            "schema": dict(invocation.schema),
        }
    )


def _response_content(result: ProviderResult) -> str:
    """Project only exact completed provider-result fields."""

    if type(result) is not ProviderResult:
        raise TypeError("result must be a ProviderResult")
    usage = result.usage
    content: dict[str, object] = {
        "actual_transport_utf8_bytes": result.actual_transport_utf8_bytes,
        "response_text": result.response_text,
        "thread_id": result.thread_id,
        "usage": (
            None
            if usage is None
            else {
                "cached_input_tokens": usage.cached_input_tokens,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
            }
        ),
    }
    if result.response_diagnostic is not None:
        content["response_diagnostic"] = _response_diagnostic(
            result.response_diagnostic
        )
    return _canonical_json(content)


def _response_diagnostic(
    diagnostic: ProviderResponseDiagnostic,
) -> dict[str, object]:
    """Preserve only error, status, exit_code, and stderr from completed process diagnostics."""

    if type(diagnostic) is not ProviderResponseDiagnostic:
        raise TypeError("provider response diagnostic has an invalid type")

    def stream(value: bytes) -> dict[str, str]:
        try:
            return {"encoding": "utf-8", "data": value.decode("utf-8", "strict")}
        except UnicodeDecodeError:
            return {
                "encoding": "base64",
                "data": base64.b64encode(value).decode("ascii"),
            }

    def parse_dict(text: str) -> dict[str, object] | None:
        def reject_constant(_value: str) -> None:
            raise ValueError("nonfinite JSON value")

        def finite_float(value: str) -> float:
            number = float(value)
            if not math.isfinite(number):
                raise ValueError("nonfinite JSON value")
            return number

        try:
            value = json.loads(
                text,
                parse_constant=reject_constant,
                parse_float=finite_float,
            )
            return value if isinstance(value, dict) else None
        except Exception:
            return None

    error: object = None
    status: object = None
    if diagnostic.stdout:
        decoded_text = diagnostic.stdout.decode("utf-8", "replace")
        parsed = parse_dict(decoded_text)
        if parsed is None:
            # Check JSONL event streams (e.g. Codex) for terminal error events
            for line in decoded_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                event = parse_dict(line)
                if event and event.get("type") in {"error", "turn.failed"}:
                    parsed = event
                    break

        if isinstance(parsed, dict):
            target = (
                parsed.get("result")
                if isinstance(parsed.get("result"), dict)
                else parsed
            )
            if "error" in target:
                error = target["error"]
            elif "error" in parsed:
                error = parsed["error"]
            if "status" in target:
                status = target["status"]
            elif "status" in parsed:
                status = parsed["status"]

    return {
        "error": error,
        "exit_code": diagnostic.exit_code,
        "status": status,
        "stderr": stream(diagnostic.stderr),
    }


def _terminal_content(
    outcome: AgentExchangeOutcome,
    code: AgentExchangeTerminalCode,
) -> str:
    """Serialize one closed terminal outcome without arbitrary diagnostics."""

    return _canonical_json({"code": code.value, "outcome": outcome.value})


class AgentExchangeCaptureLifecycle:
    """Own fail-open response and terminal transitions for one recorded request."""

    __slots__ = (
        "_agent_id",
        "_exchange_id",
        "_lock",
        "_provider",
        "_scope_id",
        "_scope_kind",
        "_session_id",
        "_state",
        "_store",
    )

    def __init__(
        self,
        *,
        store: WorkspaceAgentExchangeStore | None,
        session_id: str | None,
        scope_kind: AgentExchangeScopeKind | None,
        scope_id: str | None,
        agent_id: str | None,
        provider: AgentExchangeProvider | None,
        exchange_id: str | None,
        enabled: bool,
    ) -> None:
        self._store = store
        self._session_id = session_id
        self._scope_kind = scope_kind
        self._scope_id = scope_id
        self._agent_id = agent_id
        self._provider = provider
        self._exchange_id = exchange_id
        self._state = (
            _CaptureState.REQUEST_RECORDED if enabled else _CaptureState.DISABLED
        )
        self._lock = threading.Lock()

    @classmethod
    def _disabled(cls) -> AgentExchangeCaptureLifecycle:
        return cls(
            store=None,
            session_id=None,
            scope_kind=None,
            scope_id=None,
            agent_id=None,
            provider=None,
            exchange_id=None,
            enabled=False,
        )

    @property
    def enabled(self) -> bool:
        """Return whether the initial request was durably recorded."""

        return self._state is not _CaptureState.DISABLED

    @property
    def exchange_id(self) -> str | None:
        """Return the shared correlation identity, absent when capture is disabled."""

        return self._exchange_id

    def _record(self, request_type: AgentExchangeRequestType, content: str) -> None:
        store = self._store
        session_id = self._session_id
        scope_kind = self._scope_kind
        scope_id = self._scope_id
        agent_id = self._agent_id
        provider = self._provider
        exchange_id = self._exchange_id
        if (
            store is None
            or session_id is None
            or scope_kind is None
            or scope_id is None
            or agent_id is None
            or provider is None
            or exchange_id is None
        ):
            return
        store.append(
            AgentExchangeRecordInput(
                session_id=session_id,
                scope_kind=scope_kind,
                scope_id=scope_id,
                agent_id=agent_id,
                provider=provider,
                exchange_id=exchange_id,
                request_type=request_type,
                content=content,
            )
        )

    def record_response(self, result: ProviderResult) -> None:
        """Attempt the raw completed response once without affecting its caller."""

        try:
            with self._lock:
                if self._state is not _CaptureState.REQUEST_RECORDED:
                    return
                self._state = _CaptureState.RESPONSE_ATTEMPTED
                self._record(
                    AgentExchangeRequestType.RESPONSE,
                    _response_content(result),
                )
        except BaseException:
            pass

    def _terminal(
        self,
        outcome: AgentExchangeOutcome,
        code: AgentExchangeTerminalCode,
        *,
        response_required: bool,
    ) -> None:
        try:
            if type(code) is not AgentExchangeTerminalCode:
                return
            with self._lock:
                allowed_state = (
                    _CaptureState.RESPONSE_ATTEMPTED
                    if response_required
                    else _CaptureState.REQUEST_RECORDED
                )
                if self._state is not allowed_state:
                    return
                self._state = _CaptureState.TERMINAL_ATTEMPTED
                self._record(
                    AgentExchangeRequestType.OUTCOME,
                    _terminal_content(outcome, code),
                )
        except BaseException:
            pass

    def accepted(self) -> None:
        """Attempt one accepted terminal after a completed response."""

        self._terminal(
            AgentExchangeOutcome.ACCEPTED,
            AgentExchangeTerminalCode.ACCEPTED,
            response_required=True,
        )

    def rejected(self, code: AgentExchangeTerminalCode) -> None:
        """Attempt one closed response-rejection terminal."""

        if type(code) is not AgentExchangeTerminalCode or code not in _REJECTED_CODES:
            return
        self._terminal(
            AgentExchangeOutcome.REJECTED,
            code,
            response_required=True,
        )

    def provider_failure(
        self,
        code: AgentExchangeTerminalCode,
        *,
        response_diagnostic: ProviderResponseDiagnostic | None = None,
    ) -> None:
        """Attempt native failure output and its closed terminal exactly once."""

        if (
            type(code) is not AgentExchangeTerminalCode
            or code not in _PROVIDER_FAILURE_CODES
        ):
            return
        try:
            with self._lock:
                if self._state is not _CaptureState.REQUEST_RECORDED:
                    return
                # Failure bytes never authorize a normal response transition.
                self._state = _CaptureState.TERMINAL_ATTEMPTED
                try:
                    content: dict[str, object] = {"provider_failure": True}
                    if response_diagnostic is not None:
                        content["response_diagnostic"] = _response_diagnostic(
                            response_diagnostic
                        )
                    self._record(
                        AgentExchangeRequestType.RESPONSE,
                        _canonical_json(content),
                    )
                except BaseException:
                    pass
                self._record(
                    AgentExchangeRequestType.OUTCOME,
                    _terminal_content(AgentExchangeOutcome.PROVIDER_FAILURE, code),
                )
        except BaseException:
            pass

    def cancelled(self) -> None:
        """Attempt one cancellation terminal before or after a response."""

        try:
            with self._lock:
                if self._state not in {
                    _CaptureState.REQUEST_RECORDED,
                    _CaptureState.RESPONSE_ATTEMPTED,
                }:
                    return
                self._state = _CaptureState.TERMINAL_ATTEMPTED
                self._record(
                    AgentExchangeRequestType.OUTCOME,
                    _terminal_content(
                        AgentExchangeOutcome.CANCELLED,
                        AgentExchangeTerminalCode.CANCELLED,
                    ),
                )
        except BaseException:
            pass

    def post_response_runtime_failure(
        self,
        code: AgentExchangeTerminalCode,
    ) -> None:
        """Attempt one closed runtime-failure terminal after a response."""

        if (
            type(code) is not AgentExchangeTerminalCode
            or code not in _POST_RESPONSE_RUNTIME_CODES
        ):
            return
        self._terminal(
            AgentExchangeOutcome.POST_RESPONSE_RUNTIME_FAILURE,
            code,
            response_required=True,
        )


class WorkspaceAgentExchangeCapture:
    """Begin fail-open exchange capture for one workspace-owned durable store."""

    __slots__ = ("_store",)

    def __init__(self, workspace: Workspace) -> None:
        self._store = WorkspaceAgentExchangeStore(workspace)

    def begin(
        self,
        *,
        session_id: str,
        scope_kind: AgentExchangeScopeKind,
        scope_id: str,
        agent_id: str,
        provider: AgentExchangeProvider,
        invocation: ProviderInvocation,
    ) -> AgentExchangeCaptureLifecycle:
        """Record one exact request before invocation or return a disabled token."""

        try:
            exchange_id = f"exchange_{uuid4().hex}"
            self._store.append(
                AgentExchangeRecordInput(
                    session_id=session_id,
                    scope_kind=scope_kind,
                    scope_id=scope_id,
                    agent_id=agent_id,
                    provider=provider,
                    exchange_id=exchange_id,
                    request_type=AgentExchangeRequestType.REQUEST,
                    content=_request_content(invocation),
                )
            )
            return AgentExchangeCaptureLifecycle(
                store=self._store,
                session_id=session_id,
                scope_kind=scope_kind,
                scope_id=scope_id,
                agent_id=agent_id,
                provider=provider,
                exchange_id=exchange_id,
                enabled=True,
            )
        except BaseException:
            return AgentExchangeCaptureLifecycle._disabled()


__all__ = [
    "AgentExchangeCaptureLifecycle",
    "AgentExchangeTerminalCode",
    "WorkspaceAgentExchangeCapture",
]
