"""Prepare explicit synchronization repair and causal reconciliation actions."""

from collections import defaultdict
from dataclasses import replace
from enum import StrEnum

from ..synchronization_assessment import SynchronizationEventRef
from ..synchronization_obligations import (
    SynchronizationConditionState,
    SynchronizationObligations,
    SynchronizationStatus,
    SynchronizationTransition,
)


class SynchronizationControlAction(StrEnum):
    START_REPAIR = "start_repair"
    REVALIDATE = "revalidate"
    RECONCILE = "reconcile"


def prepare_synchronization_control(
    obligations: SynchronizationObligations,
    action: SynchronizationControlAction,
    obligation_id: str,
    parents: tuple[SynchronizationEventRef, ...],
    reason: str,
) -> SynchronizationTransition:
    """Act on the complete observed heads and preserve every original condition."""
    if type(action) is not SynchronizationControlAction:
        raise TypeError("synchronization requires an explicit structured control action")
    if type(parents) is not tuple or not parents or len(parents) != len(set(parents)):
        raise ValueError("synchronization control needs distinct observed heads")
    available = {head.ref: head for head in obligations.heads}
    if any(ref not in available for ref in parents):
        raise ValueError("synchronization heads moved; inspect before retrying")
    selected = tuple(available[ref] for ref in parents)
    if not any(head.transition.obligation_id == obligation_id for head in selected):
        raise ValueError("synchronization control must identify an observed obligation")
    if action is not SynchronizationControlAction.RECONCILE and len(selected) != 1:
        raise ValueError("reconcile the synchronization heads before changing their state")
    if action is SynchronizationControlAction.RECONCILE and len(selected) < 2:
        raise ValueError("synchronization reconciliation requires multiple observed heads")

    grouped = defaultdict(set)
    for head in selected:
        for state in head.conditions:
            grouped[state.condition.condition_id].add(state)
    conditions = tuple(_controlled_state(states, action, reason)
                       for _, states in sorted(grouped.items()))
    if len(selected) == 1 and conditions == selected[0].conditions:
        raise ValueError("the requested synchronization action would leave the obligation unchanged")
    transition = SynchronizationTransition(
        obligation_id, max(head.transition.version for head in selected) + 1,
        parents, conditions, reason,
    )
    obligations.require_parents(transition)
    return transition


def _controlled_state(
    states: set[SynchronizationConditionState],
    action: SynchronizationControlAction,
    reason: str,
) -> SynchronizationConditionState:
    state = min(states, key=lambda item: (item.status.value, item.reason))
    if len(states) > 1:
        return replace(state, status=SynchronizationStatus.REVALIDATE, evidence=None,
                       reason="Concurrent synchronization findings require current revalidation.")
    if action is SynchronizationControlAction.REVALIDATE:
        return replace(state, status=SynchronizationStatus.REVALIDATE, evidence=None, reason=reason)
    if (action is SynchronizationControlAction.START_REPAIR
            and state.status in {SynchronizationStatus.OPEN, SynchronizationStatus.REVALIDATE}):
        return replace(state, status=SynchronizationStatus.IN_PROGRESS, evidence=None, reason=reason)
    return state
