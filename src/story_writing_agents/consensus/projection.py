"""Fail-closed projection of provider-independent consensus phase events."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .contracts import (
    ConsensusDecision,
    ConsensusPhase,
    ConsensusPhaseDecision,
    ConsensusPhaseRequest,
    ConsensusPhaseResult,
    ConsensusPhaseState,
)


ConsensusPayload = ConsensusPhaseRequest | ConsensusPhaseDecision | ConsensusPhaseResult


class ConsensusNextAction(StrEnum):
    """One safe action exposed by a projected consensus lineage."""

    REQUEST_DECISION = "request_decision"
    START_CONFIRMED_PHASE = "start_confirmed_phase"
    WAIT_FOR_PHASE = "wait_for_phase"
    REQUEST_NEXT_PHASE = "request_next_phase"
    INSPECT_FAILURE = "inspect_failure"
    OPERATOR_RECOVERY = "operator_recovery"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class ConsensusEvent:
    """One sequenced durable event; payloads contain no execution capability."""

    event_id: str
    sequence: int
    payload: ConsensusPayload

    def __post_init__(self) -> None:
        if not self.event_id or "\x00" in self.event_id:
            raise ValueError("consensus event identity is invalid")
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("consensus event sequence is invalid")
        if type(self.payload) not in {
            ConsensusPhaseRequest,
            ConsensusPhaseDecision,
            ConsensusPhaseResult,
        }:
            raise TypeError("consensus event payload is invalid")


@dataclass(frozen=True, slots=True)
class ConsensusProjection:
    """Content-free current state derived from a complete durable history."""

    workflow_id: str | None = None
    proposal_id: str | None = None
    revision_id: str | None = None
    candidate_digest: str | None = None
    phase: ConsensusPhase | None = None
    state: ConsensusPhaseState | None = None
    request_event_id: str | None = None
    decision_event_id: str | None = None
    result_event_id: str | None = None
    last_sequence: int = 0
    next_action: ConsensusNextAction = ConsensusNextAction.NONE
    publication_allowed: bool = False


def project_consensus(events: tuple[ConsensusEvent, ...]) -> ConsensusProjection:
    """Project a lineage, rejecting gaps, stale correlations, and replay."""

    if type(events) is not tuple or any(type(event) is not ConsensusEvent for event in events):
        raise TypeError("consensus history must be a tuple of events")
    if not events:
        return ConsensusProjection()

    seen_event_ids: set[str] = set()
    seen_confirmation_ids: set[str] = set()
    workflow_id: str | None = None
    proposal_id: str | None = None
    request: ConsensusPhaseRequest | None = None
    request_event: ConsensusEvent | None = None
    decision: ConsensusPhaseDecision | None = None
    decision_event: ConsensusEvent | None = None
    result: ConsensusPhaseResult | None = None
    result_event: ConsensusEvent | None = None
    prior_terminal: tuple[ConsensusPhaseResult, ConsensusEvent] | None = None

    for expected_sequence, event in enumerate(events, start=1):
        if event.sequence != expected_sequence or event.event_id in seen_event_ids:
            raise ValueError("consensus history is reordered or duplicated")
        seen_event_ids.add(event.event_id)
        payload = event.payload
        if type(payload) is ConsensusPhaseRequest:
            if request is not None and result is None:
                raise ValueError("consensus request supersedes an unresolved phase")
            if payload.base_tail_sequence != event.sequence - 1:
                raise ValueError("consensus request tail is stale")
            if workflow_id is None:
                workflow_id, proposal_id = payload.workflow_id, payload.proposal_id
                if payload.previous_result_event_id is not None:
                    raise ValueError("first consensus request has prior lineage")
            elif payload.workflow_id != workflow_id or payload.proposal_id != proposal_id:
                raise ValueError("consensus lineage identity changed")
            _validate_request_transition(payload, prior_terminal)
            request, request_event = payload, event
            decision = decision_event = result = result_event = None
        elif type(payload) is ConsensusPhaseDecision:
            if request is None or request_event is None or decision is not None or result is not None:
                raise ValueError("consensus decision is missing one current request")
            if payload.confirmation_id in seen_confirmation_ids:
                raise ValueError("consensus confirmation was replayed")
            seen_confirmation_ids.add(payload.confirmation_id)
            if (
                payload.phase_request_id != request.phase_request_id
                or payload.request_event_id != request_event.event_id
                or payload.request_event_sequence != request_event.sequence
                or payload.proposal_id != request.proposal_id
                or payload.revision_id != request.revision_id
                or payload.request_digest != request.request_digest
            ):
                raise ValueError("consensus decision is stale or mismatched")
            decision, decision_event = payload, event
        else:
            if request is None or decision is None or decision_event is None:
                raise ValueError("consensus result lacks its decision")
            if (
                payload.phase_request_id != request.phase_request_id
                or payload.decision_event_id != decision_event.event_id
                or payload.decision_event_sequence != decision_event.sequence
                or payload.proposal_id != request.proposal_id
                or payload.phase is not request.phase
                or payload.revision_id != request.revision_id
                or payload.request_digest != request.request_digest
            ):
                raise ValueError("consensus result is stale or mismatched")
            if request.phase is ConsensusPhase.DRAFT:
                if payload.state is ConsensusPhaseState.RUNNING and payload.candidate_digest is not None:
                    raise ValueError("draft candidate appeared before completion")
            elif request.phase is ConsensusPhase.REPAIR and payload.state is ConsensusPhaseState.COMPLETED:
                if payload.candidate_digest == request.candidate_digest:
                    raise ValueError("repair did not create a new revision candidate")
            elif payload.candidate_digest != request.candidate_digest:
                raise ValueError("consensus result candidate lineage changed")
            _validate_result_transition(payload, decision, result, result_event)
            result, result_event = payload, event
            if payload.state in _TERMINAL_STATES:
                prior_terminal = payload, event

    if request is None or request_event is None:
        raise ValueError("consensus history has no request")
    state, action = _visible_state(decision, result)
    return ConsensusProjection(
        workflow_id=workflow_id,
        proposal_id=proposal_id,
        revision_id=request.revision_id,
        candidate_digest=(result.candidate_digest if result is not None else request.candidate_digest),
        phase=request.phase,
        state=state,
        request_event_id=request_event.event_id,
        decision_event_id=decision_event.event_id if decision_event else None,
        result_event_id=result_event.event_id if result_event else None,
        last_sequence=events[-1].sequence,
        next_action=action,
        publication_allowed=False,
    )


_TERMINAL_STATES = frozenset(
    {
        ConsensusPhaseState.COMPLETED,
        ConsensusPhaseState.INTERRUPTED,
        ConsensusPhaseState.FAILED,
        ConsensusPhaseState.OUTCOME_UNKNOWN,
        ConsensusPhaseState.RECOVERY_REQUIRED,
        ConsensusPhaseState.REJECTED,
        ConsensusPhaseState.SUPERSEDED,
        ConsensusPhaseState.DEADLOCKED,
    }
)


def _validate_request_transition(
    request: ConsensusPhaseRequest,
    prior: tuple[ConsensusPhaseResult, ConsensusEvent] | None,
) -> None:
    if prior is None:
        if request.phase is not ConsensusPhase.DRAFT or request.revision_id != "r0":
            raise ValueError("consensus lineage must begin with draft revision zero")
        return
    result, event = prior
    if request.previous_result_event_id != event.event_id:
        raise ValueError("consensus request does not extend the current result")
    if result.state is ConsensusPhaseState.SUPERSEDED:
        if (
            request.phase is not result.phase
            or request.revision_id != result.revision_id
            or request.candidate_digest != result.candidate_digest
        ):
            raise ValueError("revised consensus request changed phase lineage")
        return
    if result.state is not ConsensusPhaseState.COMPLETED or result.next_phase is None:
        raise ValueError("terminal consensus state cannot advance")
    if request.phase is not result.next_phase:
        raise ValueError("consensus phase transition is invalid")
    expected_revision = result.revision_id
    if result.phase is ConsensusPhase.REPAIR:
        expected_revision = f"r{int(result.revision_id[1:]) + 1}"
    if request.revision_id != expected_revision or request.candidate_digest != result.candidate_digest:
        raise ValueError("consensus revision or candidate lineage is stale")


def _validate_result_transition(
    current: ConsensusPhaseResult,
    decision: ConsensusPhaseDecision,
    prior: ConsensusPhaseResult | None,
    prior_event: ConsensusEvent | None,
) -> None:
    if prior is None:
        if current.prior_result_event_id is not None:
            raise ValueError("first consensus result has prior result")
        required = {
            ConsensusDecision.CONFIRM: {
                ConsensusPhaseState.RUNNING,
                ConsensusPhaseState.COMPLETED,
                ConsensusPhaseState.INTERRUPTED,
                ConsensusPhaseState.FAILED,
                ConsensusPhaseState.OUTCOME_UNKNOWN,
                ConsensusPhaseState.RECOVERY_REQUIRED,
                ConsensusPhaseState.DEADLOCKED,
            },
            ConsensusDecision.REJECT: {ConsensusPhaseState.REJECTED},
            ConsensusDecision.REVISE: {ConsensusPhaseState.SUPERSEDED},
        }[decision.decision]
        if current.state not in required:
            raise ValueError("consensus decision/result state is invalid")
    else:
        if prior_event is None or current.prior_result_event_id != prior_event.event_id:
            raise ValueError("consensus result checkpoint is stale")
        if prior.state is not ConsensusPhaseState.RUNNING or current.state not in _TERMINAL_STATES:
            raise ValueError("consensus result transition is invalid")


def _visible_state(
    decision: ConsensusPhaseDecision | None,
    result: ConsensusPhaseResult | None,
) -> tuple[ConsensusPhaseState, ConsensusNextAction]:
    if decision is None:
        return ConsensusPhaseState.AWAITING_CONFIRMATION, ConsensusNextAction.REQUEST_DECISION
    if result is None:
        if decision.decision is ConsensusDecision.CONFIRM:
            return ConsensusPhaseState.CONFIRMED, ConsensusNextAction.START_CONFIRMED_PHASE
        return ConsensusPhaseState.CONFIRMED, ConsensusNextAction.WAIT_FOR_PHASE
    actions = {
        ConsensusPhaseState.RUNNING: ConsensusNextAction.WAIT_FOR_PHASE,
        ConsensusPhaseState.COMPLETED: (
            ConsensusNextAction.REQUEST_NEXT_PHASE
            if result.next_phase is not None
            else ConsensusNextAction.NONE
        ),
        ConsensusPhaseState.INTERRUPTED: ConsensusNextAction.INSPECT_FAILURE,
        ConsensusPhaseState.FAILED: ConsensusNextAction.INSPECT_FAILURE,
        ConsensusPhaseState.OUTCOME_UNKNOWN: ConsensusNextAction.OPERATOR_RECOVERY,
        ConsensusPhaseState.RECOVERY_REQUIRED: ConsensusNextAction.OPERATOR_RECOVERY,
        ConsensusPhaseState.REJECTED: ConsensusNextAction.NONE,
        ConsensusPhaseState.SUPERSEDED: ConsensusNextAction.REQUEST_NEXT_PHASE,
        ConsensusPhaseState.DEADLOCKED: ConsensusNextAction.NONE,
    }
    return result.state, actions[result.state]


__all__ = [
    "ConsensusEvent",
    "ConsensusNextAction",
    "ConsensusPayload",
    "ConsensusProjection",
    "project_consensus",
]
