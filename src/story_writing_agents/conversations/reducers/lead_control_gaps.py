"""Validate safe Lead control gaps."""

from __future__ import annotations

from ..event_contracts.authoring_records import (
    CollaborationLeadDiscussionPayload,
    CollaborationLeadPublicResponsePayload,
    CollaborationLeadTaskTransitionPayload,
)
from ..event_contracts.control_records import (
    CollaborationProviderCallPayload,
    CollaborationStepStatePayload,
)
from ..event_contracts.execution import CollaborationMessagePayload
from ..event_contracts.lead_outcomes import CollaborationLeadOutcomeFailurePayload
from ..event_vocabulary import (
    CollaborationActorKind,
    CollaborationInvocationKind,
    CollaborationProviderCallState,
    CollaborationStepState,
)
from ..events import CollaborationEvent as CollaborationEventV2
from ...lead_task_transitions import LeadTaskAction


def closed_pure_discussion_control_gap_is_safe(
    request: CollaborationEventV2,
    decision: CollaborationEventV2,
    history: tuple[CollaborationEventV2, ...],
) -> bool:
    """Bridge only fully validated, known-closed read-only discussion rows."""
    prefix = tuple(event for event in history if event.sequence < decision.sequence)
    by_id = {event.event_id: event for event in prefix}
    if by_id.get(request.event_id) != request:
        return False
    gap = tuple(event for event in prefix if event.sequence > request.sequence)
    if not gap:
        return False
    excluded: set[str] = set()
    for control in prefix:
        if type(control.payload) is not CollaborationLeadTaskTransitionPayload:
            continue
        receipt = control.payload.receipt
        if receipt.action is not LeadTaskAction.DISCUSS:
            continue
        origin = by_id.get(receipt.origin_event_id)
        if (
            origin is None
            or origin.sender.kind is not CollaborationActorKind.USER
            or control.sender.kind is not CollaborationActorKind.RUNTIME
            or (origin.session_id, origin.conversation_id, origin.turn_id, origin.step_id)
            != (control.session_id, control.conversation_id, control.turn_id, control.step_id)
        ):
            continue
        current = tuple(
            event for event in prefix
            if (event.session_id, event.conversation_id, event.turn_id, event.step_id)
            == (control.session_id, control.conversation_id, control.turn_id, control.step_id)
        )
        call_events = tuple(
            event for event in current
            if type(event.payload) is CollaborationProviderCallPayload
        )
        calls = tuple(event.payload for event in call_events)
        if not calls or any(
            call.invocation_kind is not CollaborationInvocationKind.LEAD_DISCUSSION
            for call in calls
        ):
            continue
        submitted = {
            (call.invocation_id, call.call_id) for call in calls
            if call.state is CollaborationProviderCallState.SUBMITTED
        }
        if any(
            (call.invocation_id, call.call_id) not in submitted for call in calls
        ) or calls[-1].state is not CollaborationProviderCallState.SUBMITTED:
            continue
        state_events = tuple(
            event for event in current
            if type(event.payload) is CollaborationStepStatePayload
        )
        states = tuple(event.payload.state for event in state_events)
        reports = tuple(
            event for event in current
            if type(event.payload) is CollaborationLeadDiscussionPayload
            and event.payload.report.origin_control_event_id == control.event_id
            and event.payload.report.associated_task_ref == receipt.source_task_ref
            and event.sequence > call_events[-1].sequence
        )
        failures = tuple(
            event.payload for event in current
            if type(event.payload) is CollaborationLeadOutcomeFailurePayload
            and event.sequence > call_events[-1].sequence
        )
        known_failure = (
            not states or (
                states[-1] in {
                    CollaborationStepState.FAILED, CollaborationStepState.INTERRUPTED,
                }
                and state_events[-1].sequence > call_events[-1].sequence
            )
        ) and any(
            (failure.invocation_id, failure.call_id)
            == (calls[-1].invocation_id, calls[-1].call_id)
            for failure in failures
        )
        if not reports and not known_failure:
            continue
        closure_sequence = (
            reports[-1].sequence if reports else (
                state_events[-1].sequence if state_events else max(
                    event.sequence for event in current
                    if type(event.payload) is CollaborationLeadOutcomeFailurePayload
                    and event.sequence > call_events[-1].sequence
                    and (event.payload.invocation_id, event.payload.call_id)
                    == (calls[-1].invocation_id, calls[-1].call_id)
                )
            )
        )
        if any(
            event.sequence > closure_sequence
            and type(event.payload) is CollaborationStepStatePayload
            and event.payload.state in {
                CollaborationStepState.RUNNING, CollaborationStepState.OUTCOME_UNKNOWN,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
            for event in current
        ):
            continue
        for event in current:
            payload = event.payload
            if event.event_id in {control.event_id, receipt.origin_event_id}:
                excluded.add(event.event_id)
            elif type(payload) is CollaborationMessagePayload:
                if event.sender.kind is not CollaborationActorKind.USER or (
                    event.event_id == receipt.origin_message_event_id
                ):
                    excluded.add(event.event_id)
            elif type(payload) in {
                CollaborationProviderCallPayload, CollaborationLeadOutcomeFailurePayload,
                CollaborationStepStatePayload, CollaborationLeadPublicResponsePayload,
            } or event in reports:
                excluded.add(event.event_id)
    return all(event.event_id in excluded for event in gap)
