"""Runtime-owned adapter for the phase-confirmed consensus core."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from .contracts import (
    ConsensusConfirmationCapability,
    ConsensusDecision,
    ConsensusMaterialHandle,
    ConsensusPhase,
    ConsensusPhaseDecision,
    ConsensusPhaseRequest,
    ConsensusPhaseResult,
    ConsensusPhaseState,
    prepare_consensus_phase,
)
from .execution import (
    ConsensusDurability,
    ConsensusExecutionCoordinator,
    ConsensusExecutionReceipt,
    ConsensusPhaseInvocation,
    ConsensusProviderResult,
)
from .projection import ConsensusEvent, ConsensusProjection, project_consensus


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_DIGEST = re.compile(r"[0-9a-f]{64}")


class ConsensusInputKind(StrEnum):
    """The two accepted sources for execution-authored revision zero."""

    PROMPT = "prompt"
    SUPPLIED_DRAFT = "supplied_draft"


@dataclass(frozen=True, slots=True)
class ConsensusWorkflowSubmission:
    """Durable input identity stored beside a consensus event history."""

    session_id: str
    conversation_id: str
    workflow_id: str
    proposal_id: str
    input_kind: ConsensusInputKind
    input_material: ConsensusMaterialHandle

    def __post_init__(self) -> None:
        for value in (
            self.session_id,
            self.conversation_id,
            self.workflow_id,
            self.proposal_id,
        ):
            _identifier(value)
        if type(self.input_kind) is not ConsensusInputKind:
            raise TypeError("consensus input kind is invalid")
        if type(self.input_material) is not ConsensusMaterialHandle:
            raise TypeError("consensus submission material handle is invalid")

    @property
    def input_digest(self) -> str:
        return self.input_material.digest


@dataclass(frozen=True, slots=True)
class ConsensusStoredWorkflow:
    """One submission and its complete authoritative event history."""

    submission: ConsensusWorkflowSubmission
    events: tuple[ConsensusEvent, ...]

    def __post_init__(self) -> None:
        if type(self.submission) is not ConsensusWorkflowSubmission:
            raise TypeError("consensus stored submission is invalid")
        projection = project_consensus(self.events)
        if not self.events or type(self.events[0].payload) is not ConsensusPhaseRequest:
            raise ValueError("consensus stored workflow has no initial request")
        request = self.events[0].payload
        if (
            projection.workflow_id != self.submission.workflow_id
            or projection.proposal_id != self.submission.proposal_id
            or request.input_material != self.submission.input_material
        ):
            raise ValueError("consensus stored workflow identity is inconsistent")


class ConsensusRuntimePort(Protocol):
    """Narrow facade implemented by StoryRuntime-owned services."""

    def new_id(self, prefix: str, /) -> str: ...

    def create_workflow(
        self,
        submission: ConsensusWorkflowSubmission,
        request_event: ConsensusEvent,
        /,
    ) -> None: ...

    def load_workflow(
        self,
        session_id: str,
        conversation_id: str,
        workflow_id: str,
        /,
    ) -> ConsensusStoredWorkflow | None: ...

    def append_event(
        self,
        session_id: str,
        conversation_id: str,
        workflow_id: str,
        expected_tail_sequence: int,
        event: ConsensusEvent,
        /,
    ) -> None: ...

    async def invoke_provider(
        self,
        invocation: ConsensusPhaseInvocation,
        /,
    ) -> ConsensusProviderResult: ...

    def interrupt_provider(
        self,
        session_id: str,
        conversation_id: str,
        workflow_id: str,
        phase_request_id: str,
        /,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class ConsensusRuntimeDecision:
    """Exact structured decision bound to the current durable request tail."""

    session_id: str
    conversation_id: str
    workflow_id: str
    phase_request_id: str
    proposal_id: str
    revision_id: str
    request_digest: str
    request_event_id: str
    request_event_sequence: int
    current_tail_sequence: int
    confirmation_id: str
    decision: ConsensusDecision
    revision_request: str | None = None

    def __post_init__(self) -> None:
        for value in (
            self.session_id,
            self.conversation_id,
            self.workflow_id,
            self.phase_request_id,
            self.proposal_id,
            self.request_event_id,
            self.confirmation_id,
        ):
            _identifier(value)
        if type(self.request_event_sequence) is not int or self.request_event_sequence < 1:
            raise ValueError("consensus decision request sequence is invalid")
        if type(self.current_tail_sequence) is not int or self.current_tail_sequence < 1:
            raise ValueError("consensus decision tail is invalid")
        if type(self.decision) is not ConsensusDecision:
            raise TypeError("consensus runtime decision is invalid")
        _digest(self.request_digest)


@dataclass(frozen=True, slots=True)
class ConsensusRuntimeInspection:
    """Projection-compatible state plus process-local authority visibility."""

    submission: ConsensusWorkflowSubmission
    projection: ConsensusProjection
    capability_available: bool
    recovery_required: bool = False


@dataclass(frozen=True, slots=True)
class ConsensusRuntimeDecisionResult:
    """Result of one structured decision and any confirmed execution."""

    inspection: ConsensusRuntimeInspection
    execution: ConsensusExecutionReceipt | None
    decision_durability: ConsensusDurability


class ConsensusRuntimeAdapter:
    """Stepwise consensus API backed only by explicit StoryRuntime-owned ports."""

    __slots__ = ("_capabilities", "_port")

    def __init__(self, port: ConsensusRuntimePort) -> None:
        required = (
            "new_id",
            "create_workflow",
            "load_workflow",
            "append_event",
            "invoke_provider",
            "interrupt_provider",
        )
        if port is None or any(not callable(getattr(port, name, None)) for name in required):
            raise TypeError("consensus runtime port is incomplete")
        self._port = port
        self._capabilities: dict[
            tuple[str, str, str, str], ConsensusConfirmationCapability
        ] = {}

    def submit_prompt(
        self,
        *,
        session_id: str,
        conversation_id: str,
        input_material: ConsensusMaterialHandle,
    ) -> ConsensusRuntimeInspection:
        """Submit prompt input for an execution-authored draft revision zero."""

        return self._submit(
            session_id=session_id,
            conversation_id=conversation_id,
            input_kind=ConsensusInputKind.PROMPT,
            input_material=input_material,
        )

    def submit_supplied_draft(
        self,
        *,
        session_id: str,
        conversation_id: str,
        draft_material: ConsensusMaterialHandle,
    ) -> ConsensusRuntimeInspection:
        """Submit a complete draft for execution transformation into revision zero."""

        return self._submit(
            session_id=session_id,
            conversation_id=conversation_id,
            input_kind=ConsensusInputKind.SUPPLIED_DRAFT,
            input_material=draft_material,
        )

    def inspect(
        self,
        *,
        session_id: str,
        conversation_id: str,
        workflow_id: str,
    ) -> ConsensusRuntimeInspection:
        """Read authoritative state without reconstructing execution authority."""

        stored = self._load(session_id, conversation_id, workflow_id)
        projection = project_consensus(stored.events)
        request = _current_request(stored.events)
        key = self._capability_key(stored.submission, request.phase_request_id)
        return ConsensusRuntimeInspection(
            submission=stored.submission,
            projection=projection,
            capability_available=(
                projection.state is ConsensusPhaseState.AWAITING_CONFIRMATION
                and key in self._capabilities
            ),
            recovery_required=projection.state
            in {
                ConsensusPhaseState.OUTCOME_UNKNOWN,
                ConsensusPhaseState.RECOVERY_REQUIRED,
            },
        )

    async def decide(
        self,
        decision: ConsensusRuntimeDecision,
    ) -> ConsensusRuntimeDecisionResult:
        """Persist one exact decision and execute only a same-process Confirm."""

        if type(decision) is not ConsensusRuntimeDecision:
            raise TypeError("decision must be ConsensusRuntimeDecision")
        stored = self._load(
            decision.session_id,
            decision.conversation_id,
            decision.workflow_id,
        )
        request_event = _current_request_event(stored.events)
        request = request_event.payload
        projection = project_consensus(stored.events)
        if projection.state is not ConsensusPhaseState.AWAITING_CONFIRMATION:
            raise ValueError("consensus workflow has no pending confirmation")
        if (
            decision.current_tail_sequence != projection.last_sequence
            or decision.request_event_id != request_event.event_id
            or decision.request_event_sequence != request_event.sequence
            or decision.phase_request_id != request.phase_request_id
            or decision.proposal_id != request.proposal_id
            or decision.revision_id != request.revision_id
            or decision.request_digest != request.request_digest
        ):
            raise ValueError("consensus runtime decision does not match the current request")

        key = self._capability_key(stored.submission, request.phase_request_id)
        if (
            decision.decision is ConsensusDecision.CONFIRM
            and key not in self._capabilities
        ):
            raise ValueError("consensus confirmation authority is unavailable; submit a fresh phase")

        payload = ConsensusPhaseDecision(
            confirmation_id=decision.confirmation_id,
            phase_request_id=request.phase_request_id,
            request_event_id=request_event.event_id,
            request_event_sequence=request_event.sequence,
            proposal_id=request.proposal_id,
            revision_id=request.revision_id,
            request_digest=request.request_digest,
            decision=decision.decision,
            revision_request=decision.revision_request,
        )
        decision_event = ConsensusEvent(
            self._new_id("consensus-decision"),
            projection.last_sequence + 1,
            payload,
        )
        capability = self._capabilities.pop(key, None)
        if decision.decision is ConsensusDecision.CONFIRM and capability is None:
            raise ValueError("consensus confirmation authority was already consumed")
        try:
            self._port.append_event(
                decision.session_id,
                decision.conversation_id,
                decision.workflow_id,
                projection.last_sequence,
                decision_event,
            )
        except (Exception, KeyboardInterrupt):
            return ConsensusRuntimeDecisionResult(
                inspection=self._inspection_after_uncertain_append(stored),
                execution=None,
                decision_durability=ConsensusDurability.UNKNOWN,
            )
        after_decision = ConsensusStoredWorkflow(
            stored.submission,
            stored.events + (decision_event,),
        )

        if decision.decision is not ConsensusDecision.CONFIRM:
            state = (
                ConsensusPhaseState.REJECTED
                if decision.decision is ConsensusDecision.REJECT
                else ConsensusPhaseState.SUPERSEDED
            )
            try:
                terminal = ConsensusPhaseResult(
                    result_id=self._new_id("consensus-result"),
                    phase_request_id=request.phase_request_id,
                    decision_event_id=decision_event.event_id,
                    decision_event_sequence=decision_event.sequence,
                    proposal_id=request.proposal_id,
                    phase=request.phase,
                    revision_id=request.revision_id,
                    request_digest=request.request_digest,
                    state=state,
                    candidate_digest=request.candidate_digest,
                )
                terminal_event = ConsensusEvent(
                    terminal.result_id,
                    decision_event.sequence + 1,
                    terminal,
                )
                self._port.append_event(
                    decision.session_id,
                    decision.conversation_id,
                    decision.workflow_id,
                    decision_event.sequence,
                    terminal_event,
                )
            except (Exception, KeyboardInterrupt):
                return ConsensusRuntimeDecisionResult(
                    inspection=self._inspection_after_uncertain_append(after_decision),
                    execution=None,
                    decision_durability=ConsensusDurability.CONFIRMED,
                )
            try:
                inspection = self.inspect(
                    session_id=decision.session_id,
                    conversation_id=decision.conversation_id,
                    workflow_id=decision.workflow_id,
                )
            except (Exception, KeyboardInterrupt):
                inspection = self._inspection_after_uncertain_append(after_decision)
            return ConsensusRuntimeDecisionResult(
                inspection=inspection,
                execution=None,
                decision_durability=ConsensusDurability.CONFIRMED,
            )

        assert capability is not None
        expected_tail = [decision_event.sequence]

        def append_execution_event(event: ConsensusEvent) -> None:
            self._port.append_event(
                decision.session_id,
                decision.conversation_id,
                decision.workflow_id,
                expected_tail[0],
                event,
            )
            expected_tail[0] = event.sequence

        coordinator = ConsensusExecutionCoordinator(
            owner=self,
            append_event=append_execution_event,
            invoke_provider=self._port.invoke_provider,
            event_id_factory=lambda kind, _sequence: self._new_id(
                f"consensus-{kind}"
            ),
        )
        try:
            execution = await coordinator.execute(
                request=request,
                capability=capability,
                request_event=request_event,
                decision_event=decision_event,
                current_tail_sequence=decision_event.sequence,
            )
        except (Exception, KeyboardInterrupt):
            return ConsensusRuntimeDecisionResult(
                inspection=self._inspection_after_uncertain_append(after_decision),
                execution=None,
                decision_durability=ConsensusDurability.CONFIRMED,
            )
        try:
            inspection = self.inspect(
                session_id=decision.session_id,
                conversation_id=decision.conversation_id,
                workflow_id=decision.workflow_id,
            )
        except (Exception, KeyboardInterrupt):
            inspection = self._inspection_after_uncertain_append(after_decision)
        if execution.recovery_required:
            inspection = ConsensusRuntimeInspection(
                submission=inspection.submission,
                projection=inspection.projection,
                capability_available=False,
                recovery_required=True,
            )
        return ConsensusRuntimeDecisionResult(
            inspection=inspection,
            execution=execution,
            decision_durability=ConsensusDurability.CONFIRMED,
        )

    async def confirm(
        self,
        decision: ConsensusRuntimeDecision,
    ) -> ConsensusRuntimeDecisionResult:
        """Execute the exact pending phase only for a structured Confirm."""

        if type(decision) is not ConsensusRuntimeDecision:
            raise TypeError("decision must be ConsensusRuntimeDecision")
        if decision.decision is not ConsensusDecision.CONFIRM:
            raise ValueError("consensus confirm requires a Confirm decision")
        return await self.decide(decision)

    def interrupt(
        self,
        *,
        session_id: str,
        conversation_id: str,
        workflow_id: str,
    ) -> ConsensusRuntimeInspection:
        """Request interruption and durably close only a currently running phase."""

        stored = self._load(session_id, conversation_id, workflow_id)
        projection = project_consensus(stored.events)
        if projection.state is not ConsensusPhaseState.RUNNING:
            raise ValueError("consensus workflow is not running")
        request_event = _current_request_event(stored.events)
        decision_event = _current_decision_event(stored.events)
        running_event = stored.events[-1]
        request = request_event.payload
        self._capabilities.pop(
            self._capability_key(stored.submission, request.phase_request_id),
            None,
        )
        try:
            interrupted = self._port.interrupt_provider(
                session_id,
                conversation_id,
                workflow_id,
                request.phase_request_id,
            )
        except (Exception, KeyboardInterrupt):
            interrupted = False
        if interrupted is not True:
            return ConsensusRuntimeInspection(
                submission=stored.submission,
                projection=projection,
                capability_available=False,
                recovery_required=True,
            )
        result = ConsensusPhaseResult(
            result_id=self._new_id("consensus-interrupted"),
            phase_request_id=request.phase_request_id,
            decision_event_id=decision_event.event_id,
            decision_event_sequence=decision_event.sequence,
            proposal_id=request.proposal_id,
            phase=request.phase,
            revision_id=request.revision_id,
            request_digest=request.request_digest,
            state=ConsensusPhaseState.INTERRUPTED,
            candidate_digest=request.candidate_digest,
            prior_result_event_id=running_event.event_id,
        )
        event = ConsensusEvent(result.result_id, projection.last_sequence + 1, result)
        try:
            self._port.append_event(
                session_id,
                conversation_id,
                workflow_id,
                projection.last_sequence,
                event,
            )
        except (Exception, KeyboardInterrupt):
            return self._inspection_after_uncertain_append(stored)
        return self.inspect(
            session_id=session_id,
            conversation_id=conversation_id,
            workflow_id=workflow_id,
        )

    def resume(
        self,
        *,
        session_id: str,
        conversation_id: str,
        workflow_id: str,
    ) -> ConsensusRuntimeInspection:
        """Inspect after restart without reconstructing or replaying authority."""

        return self.inspect(
            session_id=session_id,
            conversation_id=conversation_id,
            workflow_id=workflow_id,
        )

    def _submit(
        self,
        *,
        session_id: str,
        conversation_id: str,
        input_kind: ConsensusInputKind,
        input_material: ConsensusMaterialHandle,
    ) -> ConsensusRuntimeInspection:
        _identifier(session_id)
        _identifier(conversation_id)
        if type(input_material) is not ConsensusMaterialHandle:
            raise TypeError("consensus input material handle is invalid")
        workflow_id = self._new_id("consensus-workflow")
        proposal_id = self._new_id("consensus-proposal")
        phase_request_id = self._new_id("consensus-request")
        request, capability = prepare_consensus_phase(
            owner=self,
            workflow_id=workflow_id,
            phase_request_id=phase_request_id,
            proposal_id=proposal_id,
            phase=ConsensusPhase.DRAFT,
            revision_id="r0",
            input_material=input_material,
            candidate_material=None,
            base_tail_sequence=0,
        )
        request_event = ConsensusEvent(self._new_id("consensus-event"), 1, request)
        submission = ConsensusWorkflowSubmission(
            session_id=session_id,
            conversation_id=conversation_id,
            workflow_id=workflow_id,
            proposal_id=proposal_id,
            input_kind=input_kind,
            input_material=input_material,
        )
        self._port.create_workflow(submission, request_event)
        key = self._capability_key(submission, phase_request_id)
        self._capabilities[key] = capability
        return ConsensusRuntimeInspection(
            submission=submission,
            projection=project_consensus((request_event,)),
            capability_available=True,
        )

    def _load(
        self,
        session_id: str,
        conversation_id: str,
        workflow_id: str,
    ) -> ConsensusStoredWorkflow:
        for value in (session_id, conversation_id, workflow_id):
            _identifier(value)
        stored = self._port.load_workflow(session_id, conversation_id, workflow_id)
        if type(stored) is not ConsensusStoredWorkflow:
            raise ValueError("consensus workflow does not exist")
        if (
            stored.submission.session_id != session_id
            or stored.submission.conversation_id != conversation_id
            or stored.submission.workflow_id != workflow_id
        ):
            raise ValueError("consensus workflow ownership is invalid")
        return stored

    def _inspection_after_uncertain_append(
        self,
        prior: ConsensusStoredWorkflow,
    ) -> ConsensusRuntimeInspection:
        try:
            stored = self._load(
                prior.submission.session_id,
                prior.submission.conversation_id,
                prior.submission.workflow_id,
            )
            projection = project_consensus(stored.events)
        except (Exception, KeyboardInterrupt):
            stored = prior
            projection = project_consensus(prior.events)
        return ConsensusRuntimeInspection(
            submission=stored.submission,
            projection=projection,
            capability_available=False,
            recovery_required=True,
        )

    def _new_id(self, prefix: str) -> str:
        value = self._port.new_id(prefix)
        return _identifier(value)

    @staticmethod
    def _capability_key(
        submission: ConsensusWorkflowSubmission,
        phase_request_id: str,
    ) -> tuple[str, str, str, str]:
        return (
            submission.session_id,
            submission.conversation_id,
            submission.workflow_id,
            phase_request_id,
        )


def _current_request_event(events: tuple[ConsensusEvent, ...]) -> ConsensusEvent:
    for event in reversed(events):
        if type(event.payload) is ConsensusPhaseRequest:
            return event
    raise ValueError("consensus history has no phase request")


def _current_request(events: tuple[ConsensusEvent, ...]) -> ConsensusPhaseRequest:
    return _current_request_event(events).payload


def _current_decision_event(events: tuple[ConsensusEvent, ...]) -> ConsensusEvent:
    for event in reversed(events):
        if type(event.payload) is ConsensusPhaseDecision:
            return event
    raise ValueError("consensus history has no phase decision")


def _identifier(value: object) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise ValueError("consensus runtime identifier is invalid")
    return value


def _digest(value: object) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise ValueError("consensus runtime digest is invalid")
    return value


__all__ = [
    "ConsensusInputKind",
    "ConsensusRuntimeAdapter",
    "ConsensusRuntimeDecision",
    "ConsensusRuntimeDecisionResult",
    "ConsensusRuntimeInspection",
    "ConsensusRuntimePort",
    "ConsensusStoredWorkflow",
    "ConsensusWorkflowSubmission",
]
