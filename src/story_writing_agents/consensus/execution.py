"""Provider-neutral execution of separately confirmed consensus phases."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ..agents import EXECUTION_SPECIALIST, LEAD_ORCHESTRATOR
from .contracts import (
    CONSENSUS_AUDITORS,
    ConsensusAudit,
    ConsensusAuditVerdict,
    ConsensusConfirmationCapability,
    ConsensusExecutionIdentity,
    ConsensusMaterialHandle,
    ConsensusPhase,
    ConsensusPhaseDecision,
    ConsensusPhaseRequest,
    ConsensusPhaseResult,
    ConsensusPhaseState,
)
from .projection import ConsensusEvent


class ConsensusProviderOutcomeUnknown(Exception):
    """The provider callback may have completed but cannot prove its outcome."""


class ConsensusDurability(StrEnum):
    """Known durability of an attempted append without unsafe inference."""

    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"
    NOT_ATTEMPTED = "not_attempted"


class ConsensusPhaseOutputKind(StrEnum):
    """Closed semantic shape returned by a trusted provider adapter."""

    CANDIDATE = "candidate"
    AUDIT = "audit"
    CONFLICT = "conflict"
    SYNTHESIS = "synthesis"
    FINALIZATION = "finalization"
    DEADLOCK = "deadlock"
    FAILURE = "failure"


@dataclass(frozen=True, slots=True)
class ConsensusPhaseInvocation:
    """Exact content-free assignment supplied to a provider adapter."""

    workflow_id: str
    phase_request_id: str
    proposal_id: str
    execution_identity: ConsensusExecutionIdentity
    phase: ConsensusPhase
    revision_id: str
    input_material: ConsensusMaterialHandle
    candidate_material: ConsensusMaterialHandle | None
    agent_id: str
    ordinal: int

    def __post_init__(self) -> None:
        if type(self.execution_identity) is not ConsensusExecutionIdentity:
            raise TypeError("consensus invocation execution identity is invalid")

    @property
    def input_digest(self) -> str:
        return self.input_material.digest

    @property
    def candidate_digest(self) -> str | None:
        return None if self.candidate_material is None else self.candidate_material.digest


@dataclass(frozen=True, slots=True)
class ConsensusProviderResult:
    """Validated digest-only result returned by a provider adapter."""

    kind: ConsensusPhaseOutputKind
    result_digest: str
    candidate_material: ConsensusMaterialHandle | None
    audit_verdict: ConsensusAuditVerdict | None = None
    next_phase: ConsensusPhase | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not ConsensusPhaseOutputKind:
            raise TypeError("consensus provider output kind is invalid")
        _digest(self.result_digest)
        if self.candidate_material is not None and type(self.candidate_material) is not ConsensusMaterialHandle:
            raise TypeError("consensus provider candidate handle is invalid")
        if self.audit_verdict is not None and type(self.audit_verdict) is not ConsensusAuditVerdict:
            raise TypeError("consensus provider audit verdict is invalid")
        if self.next_phase is not None and type(self.next_phase) is not ConsensusPhase:
            raise TypeError("consensus provider next phase is invalid")
        if self.kind in {
            ConsensusPhaseOutputKind.AUDIT,
            ConsensusPhaseOutputKind.CONFLICT,
        }:
            expected_verdict = (
                ConsensusAuditVerdict.APPROVE
                if self.kind is ConsensusPhaseOutputKind.AUDIT
                else ConsensusAuditVerdict.CONFLICT
            )
            if (
                self.candidate_material is None
                or self.audit_verdict is not expected_verdict
                or self.next_phase is not None
            ):
                raise ValueError("consensus audit output is incomplete")
        elif self.kind is ConsensusPhaseOutputKind.SYNTHESIS:
            if self.candidate_material is None or self.audit_verdict is not None or self.next_phase not in {ConsensusPhase.REPAIR, ConsensusPhase.FINALIZATION}:
                raise ValueError("consensus synthesis output is incomplete")
        elif self.kind in {
            ConsensusPhaseOutputKind.CANDIDATE,
            ConsensusPhaseOutputKind.FINALIZATION,
            ConsensusPhaseOutputKind.DEADLOCK,
        }:
            if self.candidate_material is None or self.audit_verdict is not None or self.next_phase is not None:
                raise ValueError("consensus provider output shape is invalid")
        elif self.kind is ConsensusPhaseOutputKind.FAILURE:
            if self.candidate_material is not None or self.audit_verdict is not None or self.next_phase is not None:
                raise ValueError("consensus provider failure output is invalid")

    @property
    def candidate_digest(self) -> str | None:
        return None if self.candidate_material is None else self.candidate_material.digest

    @property
    def deadlocked(self) -> bool:
        return self.kind is ConsensusPhaseOutputKind.DEADLOCK


class ConsensusEventAppender(Protocol):
    """Append exactly one event or raise when durability is uncertain."""

    def __call__(self, event: ConsensusEvent, /) -> None: ...


class ConsensusProviderInvoker(Protocol):
    """Execute exactly one provider-neutral consensus assignment."""

    async def __call__(self, invocation: ConsensusPhaseInvocation, /) -> ConsensusProviderResult: ...


class ConsensusEventIdFactory(Protocol):
    """Return a new event identifier for one event kind and sequence."""

    def __call__(self, kind: str, sequence: int, /) -> str: ...


@dataclass(frozen=True, slots=True)
class ConsensusExecutionReceipt:
    """Structured execution and durability outcome for a later runtime adapter."""

    running_event: ConsensusEvent
    terminal_event: ConsensusEvent
    state: ConsensusPhaseState
    running_durability: ConsensusDurability
    terminal_durability: ConsensusDurability
    provider_invocations: tuple[ConsensusPhaseInvocation, ...]
    provider_results: tuple[ConsensusProviderResult, ...]

    @property
    def recovery_required(self) -> bool:
        return self.state is ConsensusPhaseState.RECOVERY_REQUIRED


class ConsensusExecutionCoordinator:
    """Consume one runtime capability and execute one exact confirmed phase."""

    __slots__ = ("_append", "_id_factory", "_invoke", "_owner")

    def __init__(
        self,
        *,
        owner: object,
        append_event: ConsensusEventAppender,
        invoke_provider: ConsensusProviderInvoker,
        event_id_factory: ConsensusEventIdFactory,
    ) -> None:
        if owner is None:
            raise TypeError("consensus execution owner is required")
        if not callable(append_event) or not callable(invoke_provider) or not callable(event_id_factory):
            raise TypeError("consensus execution ports must be callable")
        self._owner = owner
        self._append = append_event
        self._invoke = invoke_provider
        self._id_factory = event_id_factory

    async def execute(
        self,
        *,
        request: ConsensusPhaseRequest,
        capability: ConsensusConfirmationCapability,
        request_event: ConsensusEvent,
        decision_event: ConsensusEvent,
        current_tail_sequence: int,
    ) -> ConsensusExecutionReceipt:
        """Execute once after exact confirmation and fresh tail revalidation."""

        if type(request) is not ConsensusPhaseRequest:
            raise TypeError("consensus execution request is invalid")
        if type(capability) is not ConsensusConfirmationCapability:
            raise TypeError("consensus execution capability is invalid")
        if type(request_event) is not ConsensusEvent or type(request_event.payload) is not ConsensusPhaseRequest:
            raise TypeError("consensus execution request event is invalid")
        if request_event.payload != request:
            raise ValueError("consensus execution request event does not match its request")
        if type(decision_event) is not ConsensusEvent or type(decision_event.payload) is not ConsensusPhaseDecision:
            raise TypeError("consensus execution decision event is invalid")
        if type(current_tail_sequence) is not int or current_tail_sequence != decision_event.sequence:
            raise ValueError("consensus execution tail has drifted")
        decision = decision_event.payload
        if (
            request_event.sequence != request.base_tail_sequence + 1
            or decision_event.sequence != request_event.sequence + 1
            or decision.request_event_id != request_event.event_id
            or decision_event.event_id == request_event.event_id
        ):
            raise ValueError("consensus execution decision is not at the expected tail")
        if decision.request_event_sequence != request_event.sequence:
            raise ValueError("consensus execution request event is stale")

        running_sequence = current_tail_sequence + 1
        terminal_sequence = running_sequence + 1
        running_id = self._new_event_id("running", running_sequence)
        terminal_id = self._new_event_id("terminal", terminal_sequence)
        if len(
            {
                request_event.event_id,
                decision_event.event_id,
                running_id,
                terminal_id,
            }
        ) != 4:
            raise ValueError("consensus event id factory returned a duplicate identity")
        running_result = self._result(
            request=request,
            decision_event=decision_event,
            result_id=running_id,
            state=ConsensusPhaseState.RUNNING,
            candidate_digest=request.candidate_digest,
        )
        running_event = ConsensusEvent(running_id, running_sequence, running_result)

        capability.consume(self._owner, decision)
        try:
            self._append(running_event)
        except (Exception, KeyboardInterrupt):
            recovery = self._result(
                request=request,
                decision_event=decision_event,
                result_id=terminal_id,
                state=ConsensusPhaseState.RECOVERY_REQUIRED,
                candidate_digest=request.candidate_digest,
            )
            return ConsensusExecutionReceipt(
                running_event=running_event,
                terminal_event=ConsensusEvent(terminal_id, terminal_sequence, recovery),
                state=ConsensusPhaseState.RECOVERY_REQUIRED,
                running_durability=ConsensusDurability.UNKNOWN,
                terminal_durability=ConsensusDurability.NOT_ATTEMPTED,
                provider_invocations=(),
                provider_results=(),
            )

        invocations: list[ConsensusPhaseInvocation] = []
        outputs: list[ConsensusProviderResult] = []
        try:
            result = await self._invoke_phase(
                request,
                decision_event,
                running_id,
                terminal_id,
                invocations,
                outputs,
            )
        except ConsensusProviderOutcomeUnknown:
            result = self._result(
                request=request,
                decision_event=decision_event,
                result_id=terminal_id,
                state=ConsensusPhaseState.OUTCOME_UNKNOWN,
                candidate_digest=request.candidate_digest,
                prior_result_event_id=running_id,
            )
        except (asyncio.CancelledError, KeyboardInterrupt, InterruptedError):
            result = self._result(
                request=request,
                decision_event=decision_event,
                result_id=terminal_id,
                state=ConsensusPhaseState.INTERRUPTED,
                candidate_digest=request.candidate_digest,
                prior_result_event_id=running_id,
            )
        except Exception:
            result = self._result(
                request=request,
                decision_event=decision_event,
                result_id=terminal_id,
                state=ConsensusPhaseState.FAILED,
                candidate_digest=request.candidate_digest,
                prior_result_event_id=running_id,
            )

        terminal_event = ConsensusEvent(terminal_id, terminal_sequence, result)
        try:
            self._append(terminal_event)
        except (Exception, KeyboardInterrupt):
            return ConsensusExecutionReceipt(
                running_event=running_event,
                terminal_event=terminal_event,
                state=ConsensusPhaseState.RECOVERY_REQUIRED,
                running_durability=ConsensusDurability.CONFIRMED,
                terminal_durability=ConsensusDurability.UNKNOWN,
                provider_invocations=tuple(invocations),
                provider_results=tuple(outputs),
            )
        return ConsensusExecutionReceipt(
            running_event=running_event,
            terminal_event=terminal_event,
            state=result.state,
            running_durability=ConsensusDurability.CONFIRMED,
            terminal_durability=ConsensusDurability.CONFIRMED,
            provider_invocations=tuple(invocations),
            provider_results=tuple(outputs),
        )

    async def _invoke_phase(
        self,
        request: ConsensusPhaseRequest,
        decision_event: ConsensusEvent,
        running_event_id: str,
        terminal_event_id: str,
        invocations: list[ConsensusPhaseInvocation],
        outputs: list[ConsensusProviderResult],
    ) -> ConsensusPhaseResult:
        agents = (
            CONSENSUS_AUDITORS
            if request.phase in {ConsensusPhase.AUDIT, ConsensusPhase.REAUDIT}
            else (_phase_agent(request.phase),)
        )
        for ordinal, agent_id in enumerate(agents, start=1):
            invocation = ConsensusPhaseInvocation(
                workflow_id=request.workflow_id,
                phase_request_id=request.phase_request_id,
                proposal_id=request.proposal_id,
                execution_identity=request.execution_identity,
                phase=request.phase,
                revision_id=request.revision_id,
                input_material=request.input_material,
                candidate_material=request.candidate_material,
                agent_id=agent_id,
                ordinal=ordinal,
            )
            invocations.append(invocation)
            output = await self._invoke(invocation)
            if type(output) is not ConsensusProviderResult:
                raise TypeError("consensus provider result is invalid")
            _validate_provider_result(request, output)
            outputs.append(output)
            if output.kind is ConsensusPhaseOutputKind.FAILURE:
                raise RuntimeError("consensus provider reported a definite failure")

        if request.phase in {ConsensusPhase.AUDIT, ConsensusPhase.REAUDIT}:
            audits = tuple(
                ConsensusAudit(
                    specialist_id=invocation.agent_id,
                    revision_id=request.revision_id,
                    candidate_digest=request.candidate_digest or "",
                    verdict=output.audit_verdict,
                    audit_digest=output.result_digest,
                )
                for invocation, output in zip(invocations, outputs, strict=True)
            )
            return ConsensusPhaseResult(
                result_id=terminal_event_id,
                phase_request_id=request.phase_request_id,
                decision_event_id=decision_event.event_id,
                decision_event_sequence=decision_event.sequence,
                proposal_id=request.proposal_id,
                execution_identity=request.execution_identity,
                phase=request.phase,
                revision_id=request.revision_id,
                request_digest=request.request_digest,
                state=ConsensusPhaseState.COMPLETED,
                candidate_digest=request.candidate_digest,
                audits=audits,
                next_phase=ConsensusPhase.SYNTHESIS,
                prior_result_event_id=running_event_id,
            )

        output = outputs[0]
        state = (
            ConsensusPhaseState.DEADLOCKED
            if request.phase is ConsensusPhase.SYNTHESIS and output.deadlocked
            else ConsensusPhaseState.COMPLETED
        )
        return ConsensusPhaseResult(
            result_id=terminal_event_id,
            phase_request_id=request.phase_request_id,
            decision_event_id=decision_event.event_id,
            decision_event_sequence=decision_event.sequence,
            proposal_id=request.proposal_id,
            execution_identity=request.execution_identity,
            phase=request.phase,
            revision_id=request.revision_id,
            request_digest=request.request_digest,
            state=state,
            candidate_digest=output.candidate_digest,
            next_phase=None if state is ConsensusPhaseState.DEADLOCKED else _next_phase(request.phase, output),
            prior_result_event_id=running_event_id,
        )

    def _result(
        self,
        *,
        request: ConsensusPhaseRequest,
        decision_event: ConsensusEvent,
        result_id: str,
        state: ConsensusPhaseState,
        candidate_digest: str | None,
        prior_result_event_id: str | None = None,
    ) -> ConsensusPhaseResult:
        return ConsensusPhaseResult(
            result_id=result_id,
            phase_request_id=request.phase_request_id,
            decision_event_id=decision_event.event_id,
            decision_event_sequence=decision_event.sequence,
            proposal_id=request.proposal_id,
            execution_identity=request.execution_identity,
            phase=request.phase,
            revision_id=request.revision_id,
            request_digest=request.request_digest,
            state=state,
            candidate_digest=candidate_digest,
            prior_result_event_id=prior_result_event_id,
        )

    def _new_event_id(self, kind: str, sequence: int) -> str:
        event_id = self._id_factory(kind, sequence)
        if type(event_id) is not str or not event_id or "\x00" in event_id:
            raise ValueError("consensus event id factory returned an invalid identity")
        return event_id


def _validate_provider_result(
    request: ConsensusPhaseRequest,
    result: ConsensusProviderResult,
) -> None:
    if result.kind is ConsensusPhaseOutputKind.FAILURE:
        return
    if request.phase in {ConsensusPhase.AUDIT, ConsensusPhase.REAUDIT}:
        if (
            result.kind
            not in {
                ConsensusPhaseOutputKind.AUDIT,
                ConsensusPhaseOutputKind.CONFLICT,
            }
            or result.candidate_material != request.candidate_material
            or result.audit_verdict is None
            or result.next_phase is not None
        ):
            raise ValueError("consensus audit provider result is invalid")
        return
    if result.audit_verdict is not None:
        raise ValueError("nonaudit consensus result cannot vote")
    if request.phase is ConsensusPhase.DRAFT:
        valid = (
            result.kind is ConsensusPhaseOutputKind.CANDIDATE
            and result.candidate_material != request.input_material
        )
    elif request.phase is ConsensusPhase.REPAIR:
        valid = (
            result.kind is ConsensusPhaseOutputKind.CANDIDATE
            and result.candidate_material != request.candidate_material
        )
    elif request.phase is ConsensusPhase.SYNTHESIS:
        valid = result.candidate_material == request.candidate_material and (
            result.kind
            in {
                ConsensusPhaseOutputKind.SYNTHESIS,
                ConsensusPhaseOutputKind.DEADLOCK,
            }
        )
    else:
        valid = (
            result.kind is ConsensusPhaseOutputKind.FINALIZATION
            and result.candidate_material == request.candidate_material
        )
    if not valid:
        raise ValueError("consensus provider result does not match its phase")


def _phase_agent(phase: ConsensusPhase) -> str:
    if phase in {ConsensusPhase.DRAFT, ConsensusPhase.REPAIR}:
        return EXECUTION_SPECIALIST
    if phase in {ConsensusPhase.SYNTHESIS, ConsensusPhase.FINALIZATION}:
        return LEAD_ORCHESTRATOR
    raise ValueError("audit phases require specialist fan-out")


def _next_phase(
    phase: ConsensusPhase,
    result: ConsensusProviderResult,
) -> ConsensusPhase | None:
    return {
        ConsensusPhase.DRAFT: ConsensusPhase.AUDIT,
        ConsensusPhase.AUDIT: ConsensusPhase.SYNTHESIS,
        ConsensusPhase.REAUDIT: ConsensusPhase.SYNTHESIS,
        ConsensusPhase.REPAIR: ConsensusPhase.REAUDIT,
        ConsensusPhase.SYNTHESIS: result.next_phase,
        ConsensusPhase.FINALIZATION: None,
    }[phase]


def _digest(value: object) -> str:
    if type(value) is not str or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("consensus provider digest is invalid")
    return value


def _optional_digest(value: object | None) -> str | None:
    return None if value is None else _digest(value)


__all__ = [
    "ConsensusEventAppender",
    "ConsensusEventIdFactory",
    "ConsensusDurability",
    "ConsensusExecutionCoordinator",
    "ConsensusExecutionReceipt",
    "ConsensusPhaseInvocation",
    "ConsensusPhaseOutputKind",
    "ConsensusProviderInvoker",
    "ConsensusProviderOutcomeUnknown",
    "ConsensusProviderResult",
]
