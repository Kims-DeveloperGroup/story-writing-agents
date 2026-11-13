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
from .conversations import (
    CollaborationInvocationKind,
    CollaborationLeadJudgmentPayload,
    CollaborationLeadSynthesisPayload,
    CollaborationLeadTaskDecisionPayload,
    LeadRecommendationStartedPayload,
)
from .conversations.events import (
    CollaborationLeadExplorationResultPayload,
    CollaborationPremiseDecisionPayload,
)
from .lead_premise_decisions import PremiseDecisionAction


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


def has_unstarted_lead_request(
    events: tuple[CollaborationEvent, ...],
) -> bool:
    """Detect any durable request lacking a later same-turn invocation marker."""

    settled_origin_ids: set[str] = set()
    settled_controls = {
        event.event_id for event in events
        if type(event.payload) is CollaborationPremiseDecisionPayload
        and event.payload.selection.action in {PremiseDecisionAction.REJECT, PremiseDecisionAction.DEFER}
        and event.sender == event_journal.USER_ACTOR and event.recipient == event_journal.LEAD_ACTOR
    }
    if settled_controls:
        current_report_event = None
        message_origins = {}
        for event in events:
            if (
                type(event.payload) is CollaborationLeadSynthesisPayload
                and event.payload.premise_report is not None
            ):
                current_report_event = event
            elif (
                type(event.payload) is CollaborationMessagePayload
                and event.sender == event_journal.USER_ACTOR and event.recipient == event_journal.LEAD_ACTOR
            ):
                key = (
                    event.session_id, event.conversation_id, event.turn_id, event.step_id, event.batch_id,
                )
                message_origins.setdefault(key, []).append(event)
            elif event.event_id in settled_controls and current_report_event is not None:
                selection = event.payload.selection
                report = current_report_event.payload.premise_report
                origins = message_origins.get(
                    (
                        event.session_id, event.conversation_id, event.turn_id,
                        event.step_id, event.batch_id,
                    ), (),
                )
                if (
                    len(origins) == 1
                    and origins[0].sequence < event.sequence
                    and (current_report_event.session_id, current_report_event.conversation_id)
                    == (event.session_id, event.conversation_id)
                    and report.source_conversation_id == event.conversation_id
                    and (
                        selection.source_conversation_id, selection.report_event_id,
                        selection.report_digest, selection.question_id,
                    ) == (
                        event.conversation_id, current_report_event.event_id,
                        report.report_digest, report.spec.question_id,
                    )
                ):
                    settled_origin_ids.add(origins[0].event_id)
    marked_turns: set[str] = set()
    for control in events:
        if type(control.payload) is not CollaborationLeadTaskTransitionPayload:
            continue
        receipt = control.payload.receipt
        if receipt.action not in {LeadTaskAction.HOLD, LeadTaskAction.DISCARD}:
            continue
        origin = next((event for event in events if event.event_id == receipt.origin_event_id), None)
        if origin is not None and type(origin.payload) is CollaborationLeadTaskDecisionPayload and (
            origin.session_id, origin.conversation_id, origin.turn_id, origin.step_id, origin.batch_id,
        ) == (control.session_id, control.conversation_id, control.turn_id, control.step_id, control.batch_id):
            if receipt.origin_message_event_id is not None:
                settled_origin_ids.add(receipt.origin_message_event_id)
    for event in reversed(events):
        if event.event_id in settled_origin_ids:
            continue
        if (
            (
                type(event.payload) is CollaborationProviderCallPayload
                and event.payload.invocation_kind
                is CollaborationInvocationKind.LEAD
            )
            or (
                event.kind is CollaborationEventKind.MESSAGE
                and event.sender == event_journal.LEAD_ACTOR
                and event.recipient == event_journal.USER_ACTOR
                and type(event.payload) is CollaborationLeadJudgmentPayload
            )
            or type(event.payload) in {CollaborationLeadOutcomeFailurePayload, CollaborationLeadExplorationResultPayload, CollaborationLeadDiscussionPayload}
        ):
            marked_turns.add(event.turn_id)
            continue
        if (
            ((type(event.payload) is CollaborationMessagePayload
              and event.sender == event_journal.USER_ACTOR and event.recipient == event_journal.LEAD_ACTOR)
             or type(event.payload) is LeadRecommendationStartedPayload)
            and event.turn_id not in marked_turns
        ):
            return True
    return False


def unmatched_lead_call_state(
    events: tuple[CollaborationEvent, ...],
) -> CollaborationProviderCallState | None:
    """Return the strongest durable state of an unmatched Lead provider call."""

    completed_invocations: set[tuple[str, str]] = set()
    prepared = False
    for event in reversed(events):
        correlation = (event.step_id, event.turn_id)
        if (
            event.kind is CollaborationEventKind.MESSAGE
            and event.sender == event_journal.LEAD_ACTOR
            and event.recipient == event_journal.USER_ACTOR
            and type(event.payload) is CollaborationLeadJudgmentPayload
        ) or type(event.payload) in {CollaborationLeadOutcomeFailurePayload, CollaborationLeadExplorationResultPayload, CollaborationLeadDiscussionPayload}:
            completed_invocations.add(correlation)
            continue
        if (
            type(event.payload) is CollaborationProviderCallPayload
            and event.payload.invocation_kind in {CollaborationInvocationKind.LEAD, CollaborationInvocationKind.LEAD_EXPLORATION, CollaborationInvocationKind.LEAD_DISCUSSION}
            and correlation not in completed_invocations
        ):
            if event.payload.state is CollaborationProviderCallState.SUBMITTED:
                return CollaborationProviderCallState.SUBMITTED
            prepared = True
    return CollaborationProviderCallState.PREPARED if prepared else None
