"""Check explicit synchronization repair selection against Lead task provenance."""

from __future__ import annotations

from ..conversations.events import (
    CollaborationEvent,
    CollaborationLeadTaskDecisionPayload,
    CollaborationLeadTaskTransitionPayload,
)
from ..lead_task_transitions import LeadTaskTransitionReceipt
from ..specialist_routing import AuthoringActivity, AuthoringIntent, AuthoringScope
from ..synchronization_obligations import SynchronizationCondition
from ..synchronization_repair import SynchronizationRepairSelection


def selected_synchronization_repair(
    events: tuple[CollaborationEvent, ...], receipt: LeadTaskTransitionReceipt | None,
) -> SynchronizationRepairSelection | None:
    if receipt is None or receipt.origin_kind != "task_decision":
        return None
    return next((
        event.payload.synchronization_repair for event in events
        if event.event_id == receipt.origin_event_id
        and type(event.payload) is CollaborationLeadTaskDecisionPayload
    ), None)


def repair_for_scope(
    events: tuple[CollaborationEvent, ...], scope: AuthoringScope,
) -> SynchronizationRepairSelection | None:
    receipt = next((
        event.payload.receipt for event in reversed(events)
        if type(event.payload) is CollaborationLeadTaskTransitionPayload
        and (event.payload.receipt.target_task_id, event.payload.receipt.target_task_revision)
        == (scope.task_id, scope.task_revision)
    ), None)
    return selected_synchronization_repair(events, receipt)


def require_selected_repair_target(
    selection: SynchronizationRepairSelection, scope: AuthoringScope, condition: SynchronizationCondition,
) -> None:
    expected_paths = (
        {condition.changed_path, condition.target_path}
        if scope.intent is AuthoringIntent.REVIEW_ONLY else {selection.repair_path}
    )
    if (
        scope.activity not in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE, AuthoringActivity.REVIEW}
        or set(scope.target_paths) != expected_paths
        or condition.domain not in scope.domains
    ):
        raise ValueError("The selected synchronization repair requires its exact document scope.")
