"""Recover an exact narrative-rule approval from authoritative task events."""

from __future__ import annotations

from dataclasses import dataclass

from ...lead_task_transitions import LeadTaskAction
from ...narrative_rule_revision import NarrativeRuleRevisionSelection
from ...synchronization_assessment import SynchronizationEventRef
from ..event_contracts.authoring_records import (
    CollaborationLeadTaskDecisionPayload,
    CollaborationLeadTaskTransitionPayload,
)
from ..event_vocabulary import CollaborationActorKind
from ..events import CollaborationEvent, CollaborationMessagePayload, synchronization_event_ref


@dataclass(frozen=True, slots=True)
class NarrativeRuleRevisionTask:
    origin: CollaborationEvent
    transition: CollaborationEvent
    selection: NarrativeRuleRevisionSelection


def _conversation_index(
    events: tuple[CollaborationEvent, ...],
    conversation_id: str,
    before_sequence: int | None,
) -> dict[str, CollaborationEvent]:
    local = tuple(
        event for event in events
        if event.conversation_id == conversation_id
        and (before_sequence is None or event.sequence < before_sequence)
    )
    indexed = {event.event_id: event for event in local}
    if len(indexed) != len(local) or len({event.session_id for event in events}) > 1:
        return {}
    return indexed


def _same_batch_before(origin: CollaborationEvent, event: CollaborationEvent) -> bool:
    return (
        origin.sequence < event.sequence
        and (origin.session_id, origin.conversation_id, origin.turn_id, origin.step_id, origin.batch_id)
        == (event.session_id, event.conversation_id, event.turn_id, event.step_id, event.batch_id)
    )


def _user_control(
    event: CollaborationEvent,
    indexed: dict[str, CollaborationEvent],
) -> CollaborationEvent | None:
    payload = event.payload
    if (type(payload) is not CollaborationLeadTaskTransitionPayload
            or event.sender.kind is not CollaborationActorKind.RUNTIME
            or event.recipient.kind is not CollaborationActorKind.USER):
        return None
    receipt = payload.receipt
    origin = indexed.get(receipt.origin_event_id)
    if (receipt.origin_kind != "task_decision" or origin is None
            or type(origin.payload) is not CollaborationLeadTaskDecisionPayload
            or origin.sender.kind is not CollaborationActorKind.USER
            or origin.recipient.kind is not CollaborationActorKind.RUNTIME
            or not _same_batch_before(origin, event)):
        return None
    decision = origin.payload.decision
    if ((decision.session_id, decision.conversation_id, decision.request_id)
            != (origin.session_id, origin.conversation_id, origin.event_id)
            or (decision.action, decision.task_ref) != (receipt.action, receipt.source_task_ref)):
        return None
    return origin


def _active_revision(
    indexed: dict[str, CollaborationEvent],
    task_id: str,
    task_revision: int,
) -> NarrativeRuleRevisionTask | None:
    transitions = tuple(
        event for event in indexed.values()
        if type(event.payload) is CollaborationLeadTaskTransitionPayload
        and event.payload.receipt.target_task_id == task_id
    )
    if task_revision != 1 or len(transitions) != 1:
        return None
    transition = transitions[0]
    receipt = transition.payload.receipt
    if (receipt.target_task_revision != task_revision or receipt.action is not LeadTaskAction.NEW_TASK
            or receipt.planning_step_id != transition.step_id):
        return None
    origin = _user_control(transition, indexed)
    if origin is None:
        return None
    selection = origin.payload.narrative_rule_revision
    if selection is None:
        return None
    message = indexed.get(receipt.origin_message_event_id)
    if (message is None or type(message.payload) is not CollaborationMessagePayload
            or message.sender.kind is not CollaborationActorKind.USER
            or message.recipient.kind is not CollaborationActorKind.LEAD
            or not origin.sequence < message.sequence
            or not _same_batch_before(message, transition)
            or message.payload.content != origin.payload.decision.text):
        return None

    for event in indexed.values():
        if event.sequence <= transition.sequence or type(event.payload) is not CollaborationLeadTaskTransitionPayload:
            continue
        control = event.payload.receipt
        source = control.source_task_ref
        if (control.action in {LeadTaskAction.HOLD, LeadTaskAction.DISCARD} and source is not None
                and (source.session_id, source.conversation_id, source.task_id, source.task_revision)
                == (origin.session_id, origin.conversation_id, task_id, task_revision)
                and _user_control(event, indexed) is not None):
            return None
    return NarrativeRuleRevisionTask(origin, transition, selection)


def narrative_rule_revision_task(
    events: tuple[CollaborationEvent, ...],
    conversation_id: str,
    task_id: str,
    *,
    task_revision: int = 1,
    before_sequence: int | None = None,
) -> NarrativeRuleRevisionTask | None:
    """Read a typed approval in the current or historical task prefix."""
    return _active_revision(_conversation_index(events, conversation_id, before_sequence), task_id, task_revision)


def narrative_rule_revision_task_for_origin(
    events: tuple[CollaborationEvent, ...],
    origin_ref: SynchronizationEventRef,
    *,
    before_sequence: int | None = None,
) -> NarrativeRuleRevisionTask | None:
    """Recover a still-active approval from its exact same-session USER event reference."""
    indexed = _conversation_index(events, origin_ref.conversation_id, before_sequence)
    origin = indexed.get(origin_ref.event_id)
    if (origin is None or type(origin.payload) is not CollaborationLeadTaskDecisionPayload
            or origin.payload.narrative_rule_revision is None
            or synchronization_event_ref(origin) != origin_ref):
        return None
    transitions = tuple(
        event.payload.receipt for event in indexed.values()
        if type(event.payload) is CollaborationLeadTaskTransitionPayload
        and event.payload.receipt.origin_event_id == origin.event_id
    )
    if len(transitions) != 1 or transitions[0].target_task_id is None:
        return None
    binding = _active_revision(indexed, transitions[0].target_task_id, 1)
    return binding if binding is not None and binding.origin == origin else None
