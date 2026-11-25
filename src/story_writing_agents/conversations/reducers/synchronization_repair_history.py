"""Replay disclosed synchronization repair from exact Apply and USER task events."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ...lead_task_transitions import LeadTaskAction
from ...synchronization_assessment import (
    SynchronizationAssessment,
    SynchronizationEventRef,
    SynchronizationFinding,
    SynchronizationJudgment,
)
from ...synchronization_obligations import (
    SynchronizationCondition,
    SynchronizationObligations,
    SynchronizationStatus,
)
from ...synchronization_repair import SynchronizationRepairSelection
from ..event_contracts.artifact_apply import (
    ArtifactApplicationDecisionPayload,
    ArtifactApplicationRequestPayload,
    ArtifactApplicationResultPayload,
    ConditionalArtifactApplicationIssue,
)
from ..event_contracts.authoring_records import (
    CollaborationLeadTaskDecisionPayload,
    CollaborationLeadTaskTransitionPayload,
)
from ..event_contracts.story_commits import (
    StoryCommitDecisionPayload,
    StoryCommitRequestPayload,
    StoryCommitResultPayload,
)
from ..event_contracts.synchronization import SynchronizationPayload
from ..event_vocabulary import (
    ArtifactApplicationDecisionKind,
    ArtifactApplicationNextAction,
    ArtifactApplicationOutcomeKind,
    CollaborationActorKind,
    CollaborationExecutionOutcomeKind,
    CollaborationStepState,
    StoryCommitOutcomeKind,
)
from ..events import (
    CollaborationEvent,
    CollaborationExecutionResult,
    CollaborationStepStatePayload,
    synchronization_event_ref,
)
from .synchronization_history import SynchronizationHistory


_SUCCESSFUL_APPLY = frozenset({ArtifactApplicationOutcomeKind.APPLIED, ArtifactApplicationOutcomeKind.UNCHANGED})
_OPEN_REPAIR = frozenset({SynchronizationStatus.OPEN, SynchronizationStatus.IN_PROGRESS, SynchronizationStatus.REVALIDATE})
_RESOLVED_REPAIR = {
    SynchronizationStatus.RESOLVED: SynchronizationJudgment.NO_CHANGE,
    SynchronizationStatus.NOT_APPLICABLE: SynchronizationJudgment.NOT_APPLICABLE,
}
_EventIndex = dict[tuple[str, str], CollaborationEvent]


@dataclass(frozen=True, slots=True)
class SynchronizationRepairWork:
    """A disclosed condition and its unique current head after known Apply."""

    application_id: str
    application: SynchronizationEventRef
    head: SynchronizationEventRef
    condition: SynchronizationCondition
    status: SynchronizationStatus
    task_id: str
    task_revision: int


def _event_index(events: tuple[CollaborationEvent, ...]) -> _EventIndex:
    return {(event.conversation_id, event.event_id): event for event in events}


def _known_conditional_result(event: CollaborationEvent) -> bool:
    payload = event.payload
    return (
        type(payload) is ArtifactApplicationResultPayload
        and payload.outcome in _SUCCESSFUL_APPLY
        and payload.next_action is ArtifactApplicationNextAction.CONTINUE_SYNCHRONIZATION_REPAIR
        and payload.result is not None
    )


def _conditional_request(
    result: CollaborationEvent, events: _EventIndex,
) -> CollaborationEvent | None:
    if not _known_conditional_result(result):
        return None
    payload = result.payload
    assert type(payload) is ArtifactApplicationResultPayload and payload.result is not None
    decision = events.get((result.conversation_id, payload.decision_event_id))
    if (
        decision is None
        or type(decision.payload) is not ArtifactApplicationDecisionPayload
        or decision.sender.kind is not CollaborationActorKind.USER
        or decision.payload.decision is not ArtifactApplicationDecisionKind.APPLY
        or decision.payload.application_id != payload.application_id
    ):
        return None
    request = events.get((result.conversation_id, decision.payload.confirmation_request_event_id))
    if (
        request is None
        or type(request.payload) is not ArtifactApplicationRequestPayload
        or request.payload.conditional is None
        or not request.sequence < decision.sequence < result.sequence
        or request.step_id != result.step_id
        or decision.step_id != result.step_id
        or request.payload.confirmation_request_id != decision.payload.confirmation_request_id
        or request.payload.confirmation_request_id != payload.confirmation_request_id
        or request.payload.application_request.application_id != payload.application_id
        or request.payload.application_request_digest != decision.payload.application_request_digest
        or request.payload.application_request_digest != payload.application_request_digest
        or request.payload.application_request.provenance != payload.result.provenance
    ):
        return None
    return request


def _disclosed_issues(
    request: CollaborationEvent,
    assessed: SynchronizationEventRef,
    events: _EventIndex,
) -> tuple[ConditionalArtifactApplicationIssue, ...] | None:
    payload = request.payload
    assert type(payload) is ArtifactApplicationRequestPayload
    disclosed = payload.conditional
    if disclosed is None or disclosed.assessment != assessed:
        return None
    for issue in disclosed.issues:
        event = events.get((issue.head.conversation_id, issue.head.event_id))
        if (
            event is None
            or synchronization_event_ref(event) != issue.head
            or (event.conversation_id == request.conversation_id and event.sequence >= request.sequence)
            or type(event.payload) is not SynchronizationPayload
            or event.payload.transition is None
            or not any(
                state.condition == issue.condition and state.status is issue.status
                for state in event.payload.transition.conditions
            )
        ):
            return None
    return disclosed.issues


def _applied_disclosure(
    result: CollaborationEvent,
    events: _EventIndex,
    assessments: dict[SynchronizationEventRef, SynchronizationAssessment],
) -> tuple[SynchronizationAssessment, tuple[ConditionalArtifactApplicationIssue, ...]] | None:
    request = _conditional_request(result, events)
    if request is None:
        return None
    assert type(request.payload) is ArtifactApplicationRequestPayload and request.payload.conditional is not None
    ref = synchronization_event_ref(result)
    actuals = [
        assessment for assessment in assessments.values()
        if assessment.basis.application is not None and assessment.basis.application.result == ref
    ]
    if len(actuals) != 1:
        return None
    actual = actuals[0]
    binding = actual.basis.application
    assert binding is not None and type(result.payload) is ArtifactApplicationResultPayload
    assessed = request.payload.conditional.assessment
    if (
        binding.application_id != result.payload.application_id
        or assessments.get(assessed) != replace(actual, basis=replace(actual.basis, application=None))
    ):
        return None
    issues = _disclosed_issues(request, assessed, events)
    return None if issues is None else (actual, issues)


def has_blocking_applied_synchronization(
    events: tuple[CollaborationEvent, ...], synchronization: SynchronizationHistory,
) -> bool:
    """Keep known conditional Applies blocked until exact current evidence closes their issues."""

    results = tuple(event for event in events if _known_conditional_result(event))
    if not results:
        return False
    index = _event_index(events)
    if not synchronization.complete or len(index) != len(events) or len({event.session_id for event in events}) != 1:
        return True
    assessments = dict(synchronization.assessments)
    closed = _resolved_conditions(synchronization.obligations, assessments)
    for result in results:
        disclosure = _applied_disclosure(result, index, assessments)
        if disclosure is None or any(issue.condition not in closed for issue in disclosure[1]):
            return True
    return False


def has_competing_artifact_offer(events: tuple[CollaborationEvent, ...]) -> bool:
    """Identify exact Apply or Commit requests still awaiting the user."""
    decisions = {
        (event.conversation_id, event.payload.confirmation_request_event_id)
        for event in events
        if type(event.payload) in {ArtifactApplicationDecisionPayload, StoryCommitDecisionPayload}
    }
    return any(
        type(event.payload) in {ArtifactApplicationRequestPayload, StoryCommitRequestPayload}
        and (event.conversation_id, event.event_id) not in decisions
        for event in events
    )


def has_unsettled_synchronization_write(
    events: tuple[CollaborationEvent, ...],
    *,
    active_application_decision: SynchronizationEventRef | None = None,
) -> bool:
    """Block fresh work while a confirmed Apply or Commit has an unsettled outcome."""

    applies = {
        (event.conversation_id, event.payload.decision_event_id)
        for event in events if type(event.payload) is ArtifactApplicationResultPayload
    }
    commits = {
        (event.conversation_id, event.payload.decision_event_id)
        for event in events if type(event.payload) is StoryCommitResultPayload
    }
    for event in events:
        payload, key = event.payload, (event.conversation_id, event.event_id)
        if type(payload) is ArtifactApplicationDecisionPayload and payload.decision is ArtifactApplicationDecisionKind.APPLY:
            if key not in applies and (
                active_application_decision is None
                or synchronization_event_ref(event) != active_application_decision
            ):
                return True
        if type(payload) is StoryCommitDecisionPayload and payload.decision.value == "commit" and key not in commits:
            return True
        if type(payload) is ArtifactApplicationResultPayload and payload.outcome in {
            ArtifactApplicationOutcomeKind.OUTCOME_UNKNOWN, ArtifactApplicationOutcomeKind.RECOVERY_REQUIRED,
        }:
            return True
        if type(payload) is StoryCommitResultPayload and payload.outcome in {
            StoryCommitOutcomeKind.OUTCOME_UNKNOWN, StoryCommitOutcomeKind.RECOVERY_REQUIRED,
        }:
            return True
    return False


def project_synchronization_repair_work(
    events: tuple[CollaborationEvent, ...],
    synchronization: SynchronizationHistory,
    *,
    active_application_decision: SynchronizationEventRef | None = None,
) -> tuple[SynchronizationRepairWork, ...]:
    """Expose relevant live conditions for known exactly disclosed applications."""

    if not synchronization.complete or has_unsettled_synchronization_write(
        events, active_application_decision=active_application_decision,
    ):
        return ()
    index = _event_index(events)
    if len(index) != len(events) or len({event.session_id for event in events}) > 1:
        raise ValueError("synchronization repair history must be one exact session")
    assessments = dict(synchronization.assessments)
    forked = synchronization.obligations.forked_conditions
    current = {
        state.condition.condition_id: (head.ref, state)
        for head in synchronization.obligations.heads for state in head.conditions
        if state.condition.condition_id not in forked and state.status in _OPEN_REPAIR
    }
    work: list[SynchronizationRepairWork] = []
    for applied in events:
        if not _known_conditional_result(applied):
            continue
        disclosure = _applied_disclosure(applied, index, assessments)
        if disclosure is None:
            continue
        actual, issues = disclosure
        binding = actual.basis.application
        assert binding is not None
        for issue in issues:
            available = current.get(issue.condition.condition_id)
            if available is None or available[1].condition != issue.condition:
                continue
            head, state = available
            work.append(SynchronizationRepairWork(
                binding.application_id, binding.result, head, issue.condition, state.status,
                actual.basis.task_id, actual.basis.task_revision,
            ))
    return tuple(work)


def require_synchronization_repair_scope(
    selection: SynchronizationRepairSelection,
    work: tuple[SynchronizationRepairWork, ...],
) -> SynchronizationRepairWork:
    if type(selection) is not SynchronizationRepairSelection:
        raise TypeError("synchronization repair requires an exact structured selection")
    for item in work:
        if (item.application, item.condition.condition_id, item.head) != (
            selection.application, selection.condition_id, selection.head,
        ):
            continue
        if selection.repair_path not in {item.condition.changed_path, item.condition.target_path}:
            break
        return item
    raise ValueError("synchronization repair scope is stale, unresolved, or unrelated")


def _repair_task_bindings(
    events: tuple[CollaborationEvent, ...], conversation_id: str,
) -> dict[str, tuple[CollaborationEvent, SynchronizationRepairSelection]]:
    origins: dict[str, CollaborationEvent] = {}
    bindings: dict[str, tuple[CollaborationEvent, SynchronizationRepairSelection]] = {}
    history = sorted(
        (event for event in events if event.conversation_id == conversation_id),
        key=lambda event: event.sequence,
    )
    for event in history:
        payload = event.payload
        if type(payload) is CollaborationLeadTaskDecisionPayload and event.sender.kind is CollaborationActorKind.USER:
            if payload.decision.action is LeadTaskAction.NEW_TASK and payload.synchronization_repair is not None:
                origins[event.event_id] = event
        elif type(payload) is CollaborationLeadTaskTransitionPayload:
            receipt = payload.receipt
            origin = origins.get(receipt.origin_event_id)
            if (
                origin is not None and event.sender.kind is CollaborationActorKind.RUNTIME
                and receipt.origin_kind == "task_decision" and receipt.action is LeadTaskAction.NEW_TASK
                and receipt.target_task_id is not None and receipt.target_task_revision == 1
                and receipt.planning_step_id == event.step_id and origin.batch_id == event.batch_id
                and origin.turn_id == event.turn_id and origin.sequence < event.sequence
            ):
                bindings[receipt.target_task_id] = (event, origin.payload.synchronization_repair)
    return bindings


def synchronization_repair_selection_for_task(
    events: tuple[CollaborationEvent, ...], conversation_id: str, task_id: str, *, before_sequence: int,
) -> SynchronizationRepairSelection | None:
    """Read the historic explicit selection for a later proposal in that task."""

    binding = _repair_task_bindings(events, conversation_id).get(task_id)
    if binding is None or binding[0].sequence >= before_sequence:
        return None
    return binding[1]


def recorded_synchronization_repair_condition(
    selection: SynchronizationRepairSelection,
    events: _EventIndex,
) -> SynchronizationCondition | None:
    """Check a declared task against its historical disclosure and causal head."""

    source = events.get((selection.application.conversation_id, selection.application.event_id))
    if source is None or synchronization_event_ref(source) != selection.application:
        return None
    request = _conditional_request(source, events)
    if request is None:
        return None
    assert type(request.payload) is ArtifactApplicationRequestPayload and request.payload.conditional is not None
    issues = _disclosed_issues(request, request.payload.conditional.assessment, events)
    issue = next((item for item in issues or () if item.condition.condition_id == selection.condition_id), None)
    if issue is None or selection.repair_path not in {issue.condition.changed_path, issue.condition.target_path}:
        return None

    unvisited = [selection.head]
    checked: set[SynchronizationEventRef] = set()
    while unvisited:
        ref = unvisited.pop()
        if ref in checked:
            continue
        checked.add(ref)
        event = events.get((ref.conversation_id, ref.event_id))
        if (
            event is None or synchronization_event_ref(event) != ref
            or type(event.payload) is not SynchronizationPayload or event.payload.transition is None
            or not any(state.condition == issue.condition for state in event.payload.transition.conditions)
        ):
            continue
        if ref == issue.head:
            return issue.condition
        unvisited.extend(event.payload.transition.parents)
    return None


def _started_repair(
    events: tuple[CollaborationEvent, ...], result: ArtifactApplicationResultPayload | None,
) -> tuple[CollaborationEvent, SynchronizationRepairSelection] | None:
    if result is None or result.next_action is not ArtifactApplicationNextAction.CONTINUE_SYNCHRONIZATION_REPAIR:
        return None
    applied = next((event for event in reversed(events) if event.payload == result), None)
    if applied is None:
        return None
    latest_task = next((
        event for event in reversed(events)
        if event.sequence > applied.sequence and type(event.payload) is CollaborationLeadTaskTransitionPayload
        and event.payload.receipt.action in {LeadTaskAction.NEW_TASK, LeadTaskAction.RESUME}
    ), None)
    if latest_task is None:
        return None
    binding = _repair_task_bindings(events, applied.conversation_id).get(latest_task.payload.receipt.target_task_id)
    if binding is None or binding[0] != latest_task or binding[1].application != synchronization_event_ref(applied):
        return None
    index = _event_index(events)
    request = _conditional_request(applied, index)
    if request is None:
        return None
    assert type(request.payload) is ArtifactApplicationRequestPayload and request.payload.conditional is not None
    selection = binding[1]
    if selection.head.conversation_id == applied.conversation_id:
        head = index.get((selection.head.conversation_id, selection.head.event_id))
        if (
            head is None or head.sequence >= latest_task.sequence or synchronization_event_ref(head) != selection.head
            or type(head.payload) is not SynchronizationPayload or head.payload.transition is None
            or not any(
                state.condition.condition_id == selection.condition_id
                for state in head.payload.transition.conditions
            )
        ):
            return None
    if any(
        issue.condition.condition_id == selection.condition_id
        and selection.repair_path in {issue.condition.changed_path, issue.condition.target_path}
        for issue in request.payload.conditional.issues
    ):
        return binding
    return None


def has_started_synchronization_repair(
    events: tuple[CollaborationEvent, ...], result: ArtifactApplicationResultPayload | None,
) -> bool:
    """Let an exact related task proceed after a known Apply in its conversation."""
    return _started_repair(events, result) is not None


def has_verified_synchronization_review_after(
    events: tuple[CollaborationEvent, ...], result: ArtifactApplicationResultPayload | None,
    *, control_tail: CollaborationEvent,
) -> bool:
    """Only a completed exact read-only repair review opens a prior Apply reprepare gap."""
    binding = _started_repair(events, result)
    if binding is None:
        return False
    origin, selection = binding
    terminal = control_tail.payload
    if type(terminal) is not CollaborationStepStatePayload or terminal.state is not CollaborationStepState.COMPLETED:
        return False
    execution = next((event for event in events if event.event_id == terminal.execution_result_event_id), None)
    if (
        execution is None or type(execution.payload) is not CollaborationExecutionResult
        or not origin.sequence < execution.sequence < control_tail.sequence
        or execution.step_id != control_tail.step_id
        or execution.payload.overall_outcome is not CollaborationExecutionOutcomeKind.SUCCEEDED
        or (execution.payload.proposal_id, execution.payload.proposal_revision, execution.payload.proposal_digest)
        != (terminal.proposal_id, terminal.proposal_revision, terminal.proposal_digest)
    ):
        return False
    assert type(origin.payload) is CollaborationLeadTaskTransitionPayload
    receipt = origin.payload.receipt
    return any(
        event.batch_id == execution.batch_id and origin.sequence < event.sequence < execution.sequence
        and (event.turn_id, event.step_id) == (execution.turn_id, execution.step_id)
        and type(event.payload) is SynchronizationPayload and event.payload.assessment is not None
        and event.payload.assessment.basis.candidate is None
        and event.payload.assessment.basis.review_synthesis is not None
        and event.payload.assessment.basis.review_synthesis.event_id == execution.payload.synthesis_result_event_id
        and (event.payload.assessment.basis.task_id, event.payload.assessment.basis.task_revision)
        == (receipt.target_task_id, receipt.target_task_revision)
        and any(
            selection.condition_id in finding.resolves and selection.repair_path in finding.pair
            for finding in event.payload.assessment.findings
        )
        for event in events
    )


def _resolved_conditions(
    obligations: SynchronizationObligations,
    assessments: dict[SynchronizationEventRef, SynchronizationAssessment],
) -> frozenset[SynchronizationCondition]:
    forked = obligations.forked_conditions
    closed: set[SynchronizationCondition] = set()
    for head in obligations.heads:
        for state in head.conditions:
            condition, judgment = state.condition, _RESOLVED_REPAIR.get(state.status)
            if condition.condition_id in forked or judgment is None or state.evidence is None:
                continue
            proof = assessments.get(state.evidence)
            if proof is not None and not proof.basis.projected and any(
                finding.judgment is judgment and finding.domain is condition.domain
                and set(finding.pair) == {condition.changed_path, condition.target_path}
                and condition.condition_id in finding.resolves
                for finding in proof.findings
            ):
                closed.add(condition)
    return frozenset(closed)


def resolved_conditional_findings(
    request: CollaborationEvent,
    projected_ref: SynchronizationEventRef,
    assessment: SynchronizationAssessment,
    obligations: SynchronizationObligations,
    assessments: dict[SynchronizationEventRef, SynchronizationAssessment],
    events: _EventIndex,
) -> tuple[SynchronizationFinding, ...] | None:
    """Find historically disclosed discrepancies with exact current resolution."""

    issues = _disclosed_issues(request, projected_ref, events)
    if issues is None:
        return None
    disclosed = {issue.condition for issue in issues}
    matching = {
        finding: condition
        for finding in assessment.findings if finding.judgment is SynchronizationJudgment.NEEDS_CHANGE
        for condition in disclosed
        if condition == SynchronizationCondition.from_finding(condition.condition_id, projected_ref, finding)
    }
    if set(matching.values()) != disclosed:
        return None
    closed = _resolved_conditions(obligations, assessments)
    return tuple(finding for finding, condition in matching.items() if condition in closed)
