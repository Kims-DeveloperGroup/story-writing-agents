"""Effective Lead control history and terminal state drafts."""

from __future__ import annotations

from .lead_task_transitions import LeadTaskAction
from .runtime_resources import collaboration_event_journal as event_journal
from .conversations import (
    CollaborationEvent,
    CollaborationEventDraft,
    CollaborationEventKind,
    CollaborationLeadDiscussionPayload,
    CollaborationLeadOutcomeFailurePayload,
    CollaborationLeadTaskTransitionPayload,
    CollaborationMessagePayload,
    CollaborationProposal,
    CollaborationProviderCallPayload,
    CollaborationProviderCallState,
    CollaborationStepState,
    CollaborationStepStatePayload,
    project_collaboration_history,
)


def effective_lead_control_events(
    events: tuple[CollaborationEvent, ...],
) -> tuple[CollaborationEvent, ...]:
    """Retain the exact execution tail across validated, known-closed discussion."""
    project_collaboration_history(events)
    excluded: set[str] = set()
    for control in events:
        if (type(control.payload) is not CollaborationLeadTaskTransitionPayload
                or control.payload.receipt.action is not LeadTaskAction.DISCUSS):
            continue
        receipt = control.payload.receipt
        current = tuple(event for event in events
                        if (event.turn_id, event.step_id) == (control.turn_id, control.step_id))
        reports = tuple(event for event in current
                        if type(event.payload) is CollaborationLeadDiscussionPayload
                        and event.payload.report.origin_control_event_id == control.event_id)
        states = tuple(event.payload.state for event in current
                       if type(event.payload) is CollaborationStepStatePayload)
        failures = tuple(event.payload for event in current
                         if type(event.payload) is CollaborationLeadOutcomeFailurePayload)
        calls = tuple(event.payload for event in current
                      if type(event.payload) is CollaborationProviderCallPayload)
        closed_failure = bool(failures and (not states or states[-1] in {
            CollaborationStepState.FAILED, CollaborationStepState.INTERRUPTED,
        }) and (not calls or (calls[-1].state is CollaborationProviderCallState.SUBMITTED and any(
            (failure.invocation_id, failure.call_id) == (calls[-1].invocation_id, calls[-1].call_id)
            for failure in failures))))
        if not reports and not closed_failure:
            continue
        for event in current:
            if (type(event.payload) is CollaborationMessagePayload
                    and event.sender == event_journal.USER_ACTOR
                    and event.event_id != receipt.origin_message_event_id):
                continue
            excluded.add(event.event_id)
    return tuple(event for event in events if event.event_id not in excluded)


def terminal_state_draft(
    *,
    events: tuple[CollaborationEvent, ...],
    turn_id: str,
    proposal: CollaborationProposal,
    result_event_id: str,
    state: CollaborationStepState,
) -> CollaborationEventDraft:
    return event_journal.draft_with_id(
        event_id=event_journal.deterministic_id("e", result_event_id, state.value),
        session_id=events[-1].session_id,
        conversation_id=events[-1].conversation_id,
        turn_id=turn_id,
        step_id=proposal.step_id,
        kind=CollaborationEventKind.STEP_STATE,
        sender=event_journal.RUNTIME_ACTOR,
        recipient=event_journal.USER_ACTOR,
        payload=CollaborationStepStatePayload(
            state=state,
            proposal_id=proposal.proposal_id,
            proposal_revision=proposal.proposal_revision,
            proposal_digest=proposal.proposal_digest,
            execution_result_event_id=(
                None
                if state is CollaborationStepState.RECOVERY_REQUIRED
                else result_event_id
            ),
        ),
    )
