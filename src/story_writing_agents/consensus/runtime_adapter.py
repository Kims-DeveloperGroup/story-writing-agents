"""Runtime-owned adapter for the phase-confirmed consensus core."""

from __future__ import annotations

from ..authoring_admission import AuthoringAssessment, AuthoringAdmissionBasis, AuthoringAdmissionNeeded
from ..core_document_readiness import CoreDocumentReadinessNeeded

import re
from dataclasses import dataclass
from typing import Protocol

from ..specialist_routing import AuthoringIntent

from .contracts import (
    ConsensusConfirmationCapability,
    ConsensusContinuation,
    ConsensusContinuationMode,
    ConsensusDecision,
    ConsensusExecutionIdentity,
    ConsensusMaterialHandle,
    ConsensusPhase,
    ConsensusPhaseDecision,
    ConsensusPhaseRequest,
    ConsensusPhaseResult,
    ConsensusPhaseState,
    prepare_consensus_phase,
    prepare_next_consensus_phase,
)
from .execution import (
    ConsensusDurability,
    ConsensusExecutionCoordinator,
    ConsensusExecutionReceipt,
    ConsensusPhaseInvocation,
    ConsensusProviderResult,
)
from .serialization import (
    ConsensusInputKind,
    ConsensusRuntimeConflict,
    ConsensusRuntimeOutcomeUnknown,
    ConsensusStoredWorkflow,
    ConsensusWorkflowSubmission,
)
from .projection import (
    ConsensusEvent,
    ConsensusNextAction,
    ConsensusProjection,
    project_consensus,
)


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_DIGEST = re.compile(r"[0-9a-f]{64}")


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

    def verify_consensus_material(
        self,
        material: ConsensusMaterialHandle,
        /,
    ) -> bool: ...

    def qualify_consensus_authoring(
        self, identity: ConsensusExecutionIdentity, workflow_id: str,
        material: ConsensusMaterialHandle, assessment: AuthoringAssessment | None, *,
        input_kind: ConsensusInputKind, direction_material: ConsensusMaterialHandle | None = None,
        ruling_material: ConsensusMaterialHandle | None = None, source_revision_id: str = "r0",
        source_workflow_id: str | None = None,
        authoring_target: AuthoringTarget | None = None,
    ) -> AuthoringAdmissionBasis | AuthoringAdmissionNeeded: ...

    def qualify_consensus_readiness(
        self, identity: ConsensusExecutionIdentity, target: AuthoringTarget | None, /
    ) -> CoreDocumentReadinessBasis | CoreDocumentReadinessNeeded | None: ...

    def capture_consensus_direction(self, direction: str, /) -> ConsensusMaterialHandle: ...

    def validate_consensus_authoring(self, request: ConsensusPhaseRequest, /) -> None: ...


@dataclass(frozen=True, slots=True)
class ConsensusRuntimeDecision:
    """Exact structured decision bound to the current durable request tail."""

    execution_identity: ConsensusExecutionIdentity
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
            self.workflow_id,
            self.phase_request_id,
            self.proposal_id,
            self.request_event_id,
            self.confirmation_id,
        ):
            _identifier(value)
        if type(self.execution_identity) is not ConsensusExecutionIdentity:
            raise TypeError("consensus decision execution identity is invalid")
        if type(self.request_event_sequence) is not int or self.request_event_sequence < 1:
            raise ValueError("consensus decision request sequence is invalid")
        if type(self.current_tail_sequence) is not int or self.current_tail_sequence < 1:
            raise ValueError("consensus decision tail is invalid")
        if type(self.decision) is not ConsensusDecision:
            raise TypeError("consensus runtime decision is invalid")
        _digest(self.request_digest)

    @property
    def session_id(self) -> str:
        return self.execution_identity.session_id

    @property
    def conversation_id(self) -> str:
        return self.execution_identity.conversation_id


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
            "verify_consensus_material",
            "qualify_consensus_authoring",
            "capture_consensus_direction",
            "validate_consensus_authoring",
            "qualify_consensus_readiness",
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
        authoring_assessment: AuthoringAssessment | None = None,
        authoring_target: AuthoringTarget | None = None,
        authoring_direction: str | None = None,
    ) -> ConsensusRuntimeInspection | AuthoringAdmissionNeeded | CoreDocumentReadinessNeeded:
        """Submit prompt input for an execution-authored draft revision zero."""

        return self._submit(
            session_id=session_id,
            conversation_id=conversation_id,
            input_kind=ConsensusInputKind.PROMPT,
            input_material=input_material,
            authoring_assessment=authoring_assessment,
            authoring_target=authoring_target,
            authoring_direction=authoring_direction,
        )

    def submit_supplied_draft(
        self,
        *,
        session_id: str,
        conversation_id: str,
        draft_material: ConsensusMaterialHandle,
        intent: AuthoringIntent = AuthoringIntent.AUTHORING,
        authoring_assessment: AuthoringAssessment | None = None,
        authoring_target: AuthoringTarget | None = None,
        authoring_direction: str | None = None,
    ) -> ConsensusRuntimeInspection | AuthoringAdmissionNeeded | CoreDocumentReadinessNeeded:
        """Disclose authoring transformation or provider-free exact-input adoption."""

        return self._submit(
            session_id=session_id,
            conversation_id=conversation_id,
            input_kind=ConsensusInputKind.SUPPLIED_DRAFT,
            input_material=draft_material,
            authoring_assessment=authoring_assessment,
            authoring_target=authoring_target,
            authoring_direction=authoring_direction,
            intent=intent,
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
        if request.intent is AuthoringIntent.AUTHORING:
            self._port.validate_consensus_authoring(request)
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
            or decision.execution_identity != request.execution_identity
        ):
            raise ValueError("consensus runtime decision does not match the current request")

        key = self._capability_key(stored.submission, request.phase_request_id)
        if (
            decision.decision is ConsensusDecision.CONFIRM
            and key not in self._capabilities
        ):
            raise ValueError("consensus confirmation authority is unavailable; submit a fresh phase")

        if decision.decision is ConsensusDecision.CONFIRM and request.intent is AuthoringIntent.AUTHORING:
            self._port.validate_consensus_authoring(request)
        if decision.decision is ConsensusDecision.CONFIRM and request.intent is AuthoringIntent.REVIEW_ONLY:
            if self._port.verify_consensus_material(request.input_material) is not True:
                raise ValueError("review-only original material is unavailable")
        payload = ConsensusPhaseDecision(
            confirmation_id=decision.confirmation_id,
            phase_request_id=request.phase_request_id,
            request_event_id=request_event.event_id,
            request_event_sequence=request_event.sequence,
            proposal_id=request.proposal_id,
            execution_identity=request.execution_identity,
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
        except (ConsensusRuntimeOutcomeUnknown, KeyboardInterrupt):
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
                    intent=request.intent,
                    result_id=self._new_id("consensus-result"),
                    phase_request_id=request.phase_request_id,
                    decision_event_id=decision_event.event_id,
                    decision_event_sequence=decision_event.sequence,
                    proposal_id=request.proposal_id,
                    execution_identity=request.execution_identity,
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
            except (ConsensusRuntimeOutcomeUnknown, KeyboardInterrupt):
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
        except (ConsensusRuntimeOutcomeUnknown, KeyboardInterrupt):
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
        except (ConsensusRuntimeOutcomeUnknown, KeyboardInterrupt):
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
        if request.phase in (
            ConsensusPhase.INPUT_ADOPTION,
            ConsensusPhase.REVIEW_COMPLETION,
        ):
            interrupted = True
        else:
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
            intent=request.intent,
            result_id=self._new_id("consensus-interrupted"),
            phase_request_id=request.phase_request_id,
            decision_event_id=decision_event.event_id,
            decision_event_sequence=decision_event.sequence,
            proposal_id=request.proposal_id,
            execution_identity=request.execution_identity,
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
        except (ConsensusRuntimeOutcomeUnknown, KeyboardInterrupt):
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

    def request_next_phase(
        self,
        *,
        session_id: str,
        conversation_id: str,
        workflow_id: str,
    ) -> ConsensusRuntimeInspection:
        """Create one explicit separately confirmed request from a terminal tail."""

        stored = self._load(session_id, conversation_id, workflow_id)
        projection = project_consensus(stored.events)
        if projection.next_action is not ConsensusNextAction.REQUEST_NEXT_PHASE:
            raise ValueError("consensus workflow cannot request its next phase")
        request_event = _current_request_event(stored.events)
        terminal_event = stored.events[-1]
        if type(terminal_event.payload) is not ConsensusPhaseResult:
            raise ValueError("consensus workflow has no terminal phase result")
        if (
            terminal_event.payload.next_phase is ConsensusPhase.REPAIR
            and projection.continuation_mode is not None
            and projection.remaining_revision_rounds < 1
        ):
            raise ValueError("consensus continuation revision budget is exhausted")
        request, capability = prepare_next_consensus_phase(
            owner=self,
            current_request=request_event.payload,
            terminal_result=terminal_event.payload,
            terminal_event_id=terminal_event.event_id,
            terminal_sequence=terminal_event.sequence,
            phase_request_id=self._new_id("consensus-request"),
        )
        event = ConsensusEvent(
            self._new_id("consensus-event"),
            terminal_event.sequence + 1,
            request,
        )
        self._port.append_event(
            session_id,
            conversation_id,
            workflow_id,
            terminal_event.sequence,
            event,
        )
        self._capabilities[
            self._capability_key(stored.submission, request.phase_request_id)
        ] = capability
        return ConsensusRuntimeInspection(
            submission=stored.submission,
            projection=project_consensus(stored.events + (event,)),
            capability_available=True,
        )

    def continue_deadlock(
        self,
        *,
        session_id: str,
        conversation_id: str,
        source_workflow_id: str,
        source_result_event_id: str,
        expected_source_tail_sequence: int,
        mode: ConsensusContinuationMode,
        ruling_material: ConsensusMaterialHandle,
        added_revision_rounds: int,
        authoring_assessment: AuthoringAssessment | None = None,
        authoring_target: AuthoringTarget | None = None,
    ) -> ConsensusRuntimeInspection | AuthoringAdmissionNeeded | CoreDocumentReadinessNeeded:
        """Prepare a fresh confirmed repair from one exact durable deadlock."""

        if type(mode) is not ConsensusContinuationMode:
            raise TypeError("consensus continuation mode is invalid")
        _identifier(source_result_event_id)
        if (
            type(expected_source_tail_sequence) is not int
            or expected_source_tail_sequence < 1
        ):
            raise ValueError("consensus continuation source tail is invalid")
        if type(added_revision_rounds) is not int or added_revision_rounds < 1:
            raise ValueError("consensus continuation revision budget must be positive")
        source, terminal_event, source_request, candidate, conflicts = (
            self._validate_continuation_source(
                session_id=session_id,
                conversation_id=conversation_id,
                source_workflow_id=source_workflow_id,
                source_result_event_id=source_result_event_id,
                expected_source_tail_sequence=expected_source_tail_sequence,
            )
        )
        materials = (ruling_material,)
        if any(
            type(item) is not ConsensusMaterialHandle
            or not item.locator.startswith("candidate:")
            or self._port.verify_consensus_material(item) is not True
            for item in materials
        ):
            raise ValueError("consensus continuation material is unavailable")
        continuation = ConsensusContinuation(
            mode=mode,
            source_workflow_id=source_workflow_id,
            source_result_event_id=source_result_event_id,
            source_revision_id=source_request.revision_id,
            source_candidate_material=candidate,
            conflict_materials=conflicts,
            ruling_material=ruling_material,
            added_revision_rounds=added_revision_rounds,
        )
        if mode is ConsensusContinuationMode.RESUME_IN_PLACE:
            workflow_id = source_workflow_id
            proposal_id = source.submission.proposal_id
            base_tail = terminal_event.sequence
            previous_result = terminal_event.event_id
        else:
            workflow_id = self._new_id("consensus-workflow")
            proposal_id = self._new_id("consensus-proposal")
            base_tail = 0
            previous_result = None
        core_document_readiness = self._port.qualify_consensus_readiness(
            source.submission.execution_identity, authoring_target)
        if type(core_document_readiness) is CoreDocumentReadinessNeeded:
            return core_document_readiness
        qualified = self._port.qualify_consensus_authoring(
            source.submission.execution_identity,
            workflow_id,
            source.submission.input_material,
            authoring_assessment,
            input_kind=source.submission.input_kind,
            direction_material=source.submission.authoring_direction,
            ruling_material=ruling_material,
            source_revision_id=source_request.revision_id,
            source_workflow_id=source.submission.workflow_id,
            authoring_target=authoring_target,
        )
        if type(qualified) is AuthoringAdmissionNeeded:
            return qualified
        if type(qualified) is not AuthoringAdmissionBasis:
            raise TypeError("consensus continuation host did not qualify admission")
        request, capability = prepare_consensus_phase(
            authoring_admission=qualified,
            authoring_direction=source.submission.authoring_direction,
            authoring_target=authoring_target,
            core_document_readiness=core_document_readiness,
            owner=self,
            workflow_id=workflow_id,
            phase_request_id=self._new_id("consensus-request"),
            proposal_id=proposal_id,
            execution_identity=source.submission.execution_identity,
            phase=ConsensusPhase.REPAIR,
            revision_id=source_request.revision_id,
            input_material=source.submission.input_material,
            candidate_material=candidate,
            base_tail_sequence=base_tail,
            previous_result_event_id=previous_result,
            continuation=continuation,
        )
        event = ConsensusEvent(self._new_id("consensus-event"), base_tail + 1, request)
        if mode is ConsensusContinuationMode.RESUME_IN_PLACE:
            self._port.append_event(
                session_id,
                conversation_id,
                workflow_id,
                base_tail,
                event,
            )
            submission = source.submission
            events = source.events + (event,)
        else:
            # Revalidate immediately before the linked create; creation itself has
            # no provider or artifact side effect and carries only exact handles.
            current = self._load(session_id, conversation_id, source_workflow_id)
            if current.events[-1] != terminal_event:
                raise ValueError("consensus continuation source tail changed")
            submission = ConsensusWorkflowSubmission(
                authoring_admission=qualified,
                authoring_direction=source.submission.authoring_direction,
                authoring_target=authoring_target,
                core_document_readiness=core_document_readiness,
                execution_identity=source.submission.execution_identity,
                workflow_id=workflow_id,
                proposal_id=proposal_id,
                input_kind=ConsensusInputKind.CONTINUATION,
                input_material=source.submission.input_material,
                continuation=continuation,
            )
            self._port.create_workflow(submission, event)
            events = (event,)
        self._capabilities[
            self._capability_key(submission, request.phase_request_id)
        ] = capability
        return ConsensusRuntimeInspection(
            submission=submission,
            projection=project_consensus(events),
            capability_available=True,
        )

    def validate_continuation_source(
        self,
        *,
        session_id: str,
        conversation_id: str,
        source_workflow_id: str,
        source_result_event_id: str,
        expected_source_tail_sequence: int,
    ) -> None:
        """Fail closed on a stale or materially incomplete deadlock source."""

        self._validate_continuation_source(
            session_id=session_id,
            conversation_id=conversation_id,
            source_workflow_id=source_workflow_id,
            source_result_event_id=source_result_event_id,
            expected_source_tail_sequence=expected_source_tail_sequence,
        )

    def _validate_continuation_source(
        self,
        *,
        session_id: str,
        conversation_id: str,
        source_workflow_id: str,
        source_result_event_id: str,
        expected_source_tail_sequence: int,
    ) -> tuple[
        ConsensusStoredWorkflow,
        ConsensusEvent,
        ConsensusPhaseRequest,
        ConsensusMaterialHandle,
        tuple[ConsensusMaterialHandle, ...],
    ]:
        _identifier(source_result_event_id)
        if (
            type(expected_source_tail_sequence) is not int
            or expected_source_tail_sequence < 1
        ):
            raise ValueError("consensus continuation source tail is invalid")
        source = self._load(session_id, conversation_id, source_workflow_id)
        if source.submission.intent is AuthoringIntent.REVIEW_ONLY:
            raise ValueError("review-only deadlock requires a fresh authoring submission")
        projection = project_consensus(source.events)
        terminal_event = source.events[-1]
        if (
            projection.state is not ConsensusPhaseState.DEADLOCKED
            or projection.last_sequence != expected_source_tail_sequence
            or terminal_event.event_id != source_result_event_id
            or type(terminal_event.payload) is not ConsensusPhaseResult
            or terminal_event.payload.state is not ConsensusPhaseState.DEADLOCKED
            or terminal_event.payload.phase is not ConsensusPhase.SYNTHESIS
        ):
            raise ValueError(
                "consensus continuation source is not the exact deadlock tail"
            )
        source_request = _current_request(source.events)
        candidate = source_request.candidate_material
        if (
            candidate is None
            or candidate.digest != terminal_event.payload.candidate_digest
            or not candidate.locator.startswith("candidate:")
        ):
            raise ValueError("consensus continuation candidate lineage is invalid")
        audit = _latest_completed_audit(source.events)
        conflicts = tuple(
            item.conflict_material
            for item in audit.audits
            if item.conflict_material is not None
        )
        if not conflicts or any(
            self._port.verify_consensus_material(item) is not True
            for item in (candidate, *conflicts)
        ):
            raise ValueError("consensus deadlock material is unavailable")
        return source, terminal_event, source_request, candidate, conflicts

    def _submit(
        self,
        *,
        session_id: str,
        conversation_id: str,
        input_kind: ConsensusInputKind,
        input_material: ConsensusMaterialHandle,
        intent: AuthoringIntent = AuthoringIntent.AUTHORING,
        authoring_assessment: AuthoringAssessment | None = None,
        authoring_target: AuthoringTarget | None = None,
        authoring_direction: str | None = None,
    ) -> ConsensusRuntimeInspection | AuthoringAdmissionNeeded | CoreDocumentReadinessNeeded:
        _identifier(session_id)
        _identifier(conversation_id)
        if type(input_material) is not ConsensusMaterialHandle:
            raise TypeError("consensus input material handle is invalid")
        if type(intent) is not AuthoringIntent:
            raise TypeError("consensus intent is invalid")
        if intent is AuthoringIntent.REVIEW_ONLY and (
            input_kind is not ConsensusInputKind.SUPPLIED_DRAFT
            or not input_material.locator.startswith("candidate:")
            or self._port.verify_consensus_material(input_material) is not True
        ):
            raise ValueError("review-only adoption requires verified supplied material")
        if authoring_direction is not None and (type(authoring_direction) is not str or not authoring_direction.strip() or intent is AuthoringIntent.REVIEW_ONLY):
            raise ValueError("consensus authoring direction requires nonempty USER input")
        direction_material = (None if authoring_direction is None else self._port.capture_consensus_direction(authoring_direction))
        core_document_readiness = None
        if intent is AuthoringIntent.AUTHORING:
            core_document_readiness = self._port.qualify_consensus_readiness(
                ConsensusExecutionIdentity(session_id, conversation_id), authoring_target)
            if type(core_document_readiness) is CoreDocumentReadinessNeeded:
                return core_document_readiness
        elif authoring_target is not None:
            raise ValueError("review-only input cannot acquire authoring target")
        workflow_id = self._new_id("consensus-workflow")
        proposal_id = self._new_id("consensus-proposal")
        phase_request_id = self._new_id("consensus-request")
        execution_identity = ConsensusExecutionIdentity(session_id, conversation_id)
        authoring_admission = None
        if intent is AuthoringIntent.AUTHORING:
            qualified = self._port.qualify_consensus_authoring(
                execution_identity, workflow_id, input_material, authoring_assessment,
                input_kind=input_kind, direction_material=direction_material,
                authoring_target=authoring_target)
            if type(qualified) is AuthoringAdmissionNeeded:
                return qualified
            if type(qualified) is not AuthoringAdmissionBasis:
                raise TypeError("consensus host did not qualify authoring admission")
            authoring_admission = qualified
        request, capability = prepare_consensus_phase(
            authoring_admission=authoring_admission,
            authoring_direction=direction_material,
            authoring_target=authoring_target,
            core_document_readiness=core_document_readiness,
            owner=self,
            workflow_id=workflow_id,
            phase_request_id=phase_request_id,
            proposal_id=proposal_id,
            execution_identity=execution_identity,
            phase=ConsensusPhase.INPUT_ADOPTION if intent is AuthoringIntent.REVIEW_ONLY else ConsensusPhase.DRAFT,
            intent=intent,
            revision_id="r0",
            input_material=input_material,
            candidate_material=input_material if intent is AuthoringIntent.REVIEW_ONLY else None,
            base_tail_sequence=0,
        )
        request_event = ConsensusEvent(self._new_id("consensus-event"), 1, request)
        submission = ConsensusWorkflowSubmission(
            authoring_target=authoring_target, core_document_readiness=core_document_readiness,
            authoring_admission=authoring_admission,
            authoring_direction=direction_material,
            intent=intent,
            execution_identity=execution_identity,
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


def _latest_completed_audit(
    events: tuple[ConsensusEvent, ...],
) -> ConsensusPhaseResult:
    for event in reversed(events):
        payload = event.payload
        if (
            type(payload) is ConsensusPhaseResult
            and payload.state is ConsensusPhaseState.COMPLETED
            and payload.phase in {ConsensusPhase.AUDIT, ConsensusPhase.REAUDIT}
        ):
            return payload
    raise ValueError("consensus deadlock has no completed audit")


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
    "ConsensusContinuationMode",
    "ConsensusRuntimeAdapter",
    "ConsensusRuntimeDecision",
    "ConsensusRuntimeDecisionResult",
    "ConsensusRuntimeInspection",
    "ConsensusRuntimeConflict",
    "ConsensusRuntimeOutcomeUnknown",
    "ConsensusRuntimePort",
    "ConsensusStoredWorkflow",
    "ConsensusWorkflowSubmission",
]
