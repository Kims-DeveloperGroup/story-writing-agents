"""Closed conversation collaboration transition policy."""

from __future__ import annotations

from types import MappingProxyType

from ..event_contracts.execution import CollaborationExecutionResult
from ..event_vocabulary import CollaborationStepState
from ..views.collaboration_history_values import CollaborationNextAction


_LEGAL_COLLABORATION_TRANSITIONS: Mapping[
    CollaborationStepState | None,
    frozenset[CollaborationStepState],
] = MappingProxyType(
    {
        None: frozenset({CollaborationStepState.AWAITING_CONFIRMATION}),
        CollaborationStepState.AWAITING_CONFIRMATION: frozenset(
            {
                CollaborationStepState.CONFIRMED,
                CollaborationStepState.REJECTED,
                CollaborationStepState.SUPERSEDED,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
        ),
        CollaborationStepState.RECONFIRMATION_REQUIRED: frozenset(
            {
                CollaborationStepState.CONFIRMED,
                CollaborationStepState.REJECTED,
                CollaborationStepState.SUPERSEDED,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
        ),
        CollaborationStepState.CONFIRMED: frozenset(
            {
                CollaborationStepState.RUNNING,
                CollaborationStepState.RECONFIRMATION_REQUIRED,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
        ),
        CollaborationStepState.RUNNING: frozenset(
            {
                CollaborationStepState.COMPLETED,
                CollaborationStepState.INTERRUPTED,
                CollaborationStepState.FAILED,
                CollaborationStepState.OUTCOME_UNKNOWN,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
        ),
        CollaborationStepState.COMPLETED: frozenset(
            {
                CollaborationStepState.AWAITING_CONFIRMATION,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
        ),
        CollaborationStepState.REJECTED: frozenset(
            {
                CollaborationStepState.AWAITING_CONFIRMATION,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
        ),
        CollaborationStepState.SUPERSEDED: frozenset(
            {
                CollaborationStepState.AWAITING_CONFIRMATION,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
        ),
        CollaborationStepState.INTERRUPTED: frozenset(
            {CollaborationStepState.RECOVERY_REQUIRED}
        ),
        CollaborationStepState.FAILED: frozenset(
            {CollaborationStepState.RECOVERY_REQUIRED}
        ),
        CollaborationStepState.OUTCOME_UNKNOWN: frozenset(
            {CollaborationStepState.RECOVERY_REQUIRED}
        ),
        CollaborationStepState.RECOVERY_REQUIRED: frozenset(),
    }
)


def validate_collaboration_transition(
    previous: CollaborationStepState | None,
    candidate: CollaborationStepState,
) -> CollaborationStepState:
    """Return a legal state transition or reject it deterministically."""

    if previous is not None and not isinstance(previous, CollaborationStepState):
        raise TypeError("previous state must be a CollaborationStepState or None")
    if not isinstance(candidate, CollaborationStepState):
        raise TypeError("candidate state must be a CollaborationStepState")
    if candidate not in _LEGAL_COLLABORATION_TRANSITIONS[previous]:
        previous_value = "none" if previous is None else previous.value
        raise ValueError(
            f"illegal collaboration transition: {previous_value} -> {candidate.value}"
        )
    return candidate


def next_action(
    state: CollaborationStepState | None,
    result: CollaborationExecutionResult | None,
) -> CollaborationNextAction:
    if state in (
        CollaborationStepState.AWAITING_CONFIRMATION,
        CollaborationStepState.RECONFIRMATION_REQUIRED,
    ):
        return CollaborationNextAction.SUBMIT_CONFIRMATION
    if state is CollaborationStepState.CONFIRMED:
        return CollaborationNextAction.START_OR_RECONFIRM
    if state is CollaborationStepState.RUNNING and result is not None:
        return CollaborationNextAction.FINALIZE_RESULT
    if state in (
        CollaborationStepState.FAILED,
        CollaborationStepState.INTERRUPTED,
        CollaborationStepState.REJECTED,
        CollaborationStepState.SUPERSEDED,
    ):
        return CollaborationNextAction.CONTINUE_WITH_LEAD
    if state is CollaborationStepState.OUTCOME_UNKNOWN:
        return CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
    if state is CollaborationStepState.RECOVERY_REQUIRED:
        return CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY
    return CollaborationNextAction.NONE
