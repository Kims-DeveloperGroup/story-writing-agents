"""Emit best-effort diagnostic events for the provider backend."""
from __future__ import annotations

import asyncio
from typing import Protocol

from .debug_log import (
    DebugComponent,
    DebugOperation,
    DebugOutcome,
    DebugPhase,
    WorkspaceDebugLog,
)
from .provider_adapter import AgentExecutionProfile


class _BackendDiagnosticHost(Protocol):
    @property
    def diagnostic_event_sink(self) -> WorkspaceDebugLog: ...


def trace_debug(
    self: _BackendDiagnosticHost,
    operation: DebugOperation,
    phase: DebugPhase,
    *,
    session_id: str | None = None,
    debate_id: str | None = None,
    conversation_id: str | None = None,
    agent_id: str | None = None,
    outcome: DebugOutcome | None = None,
) -> None:
    """Record a trace without allowing diagnostics to alter behavior."""

    try:
        self.diagnostic_event_sink.trace(
            component=DebugComponent.BACKEND,
            operation=operation,
            phase=phase,
            session_id=session_id,
            debate_id=debate_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            outcome=outcome,
        )
    except BaseException:
        pass


def log_execution_profile_event(
    self: _BackendDiagnosticHost,
    operation: DebugOperation,
    *,
    correlation_id: str,
    route_generation: int,
    old_profile: AgentExecutionProfile | None = None,
    new_profile: AgentExecutionProfile | None = None,
    active_profile: AgentExecutionProfile | None = None,
    activation_state: str,
    thread_disposition: str | None = None,
    context_state: str | None = None,
    recovery_required: bool = False,
) -> None:
    """Write one schema-v2 profile event without altering runtime behavior."""

    try:
        self.diagnostic_event_sink.execution_profile_event(
            operation,
            correlation_id=correlation_id,
            agent_id=(
                active_profile.agent_id
                if active_profile is not None
                else new_profile.agent_id
                if new_profile is not None
                else ""
            ),
            route_generation=route_generation,
            old_profile=old_profile,
            new_profile=new_profile,
            active_profile=active_profile,
            activation_state=activation_state,
            thread_disposition=thread_disposition,
            context_state=context_state,
            recovery_required=recovery_required,
        )
    except BaseException:
        pass


def record_debug_error(
    self: _BackendDiagnosticHost,
    operation: DebugOperation,
    failure: BaseException,
    *,
    session_id: str | None = None,
    debate_id: str | None = None,
    conversation_id: str | None = None,
    agent_id: str | None = None,
    phase: DebugPhase = DebugPhase.FAILED,
    outcome: DebugOutcome = DebugOutcome.FAILED,
) -> None:
    """Record a failure without allowing diagnostics to replace it."""

    if (
        phase is DebugPhase.FAILED
        and outcome is DebugOutcome.FAILED
        and isinstance(
            failure,
            (asyncio.CancelledError, KeyboardInterrupt, SystemExit),
        )
    ):
        phase = DebugPhase.CANCELLED
        outcome = DebugOutcome.INTERRUPTED
    try:
        self.diagnostic_event_sink.error(
            DebugComponent.BACKEND,
            operation,
            failure,
            phase=phase,
            session_id=session_id,
            debate_id=debate_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            outcome=outcome,
        )
    except BaseException:
        pass
