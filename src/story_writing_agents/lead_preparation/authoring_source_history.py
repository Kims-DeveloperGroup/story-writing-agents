"""Qualify authoring history sources and remaining front-call authority."""
from __future__ import annotations

from ..authoring_admission import AdmissionSource
from ..authoring_admission import AdmissionSourceKind
from ..specialist_routing import AuthoringActivity
from ..authoring_admission import AuthoringAdmissionBasis
from ..specialist_routing import AuthoringIntent
from ..specialist_routing import AuthoringScope
from ..errors import BackendProtocolError
from ..conversations import COLLABORATION_PHYSICAL_CALL_LIMIT
from ..conversations import COLLABORATION_ROUND_LIMIT
from collections.abc import Callable
from ..conversations import CollaborationEvent
from ..conversations import CollaborationEvent as CollaborationEventV2
from ..conversations import CollaborationInputKind
from ..conversations import CollaborationInputRevision
from ..conversations import CollaborationLeadTaskTransitionPayload
from ..conversations import CollaborationMessagePayload
from ..conversations import CollaborationProviderCallPayload
from ..conversations import CollaborationProviderCallState
from ..conversations import CollaborationStepState
from ..conversations import CollaborationStepStatePayload
from ..conversations import LeadRecommendationRequestPayload
from ..lead_task_transitions import LeadTaskAction
from ..conversations.session_context import SessionContextCapacityError
from ..lead_preparation import authoring_authority
from ..runtime_resources import collaboration_event_journal as event_journal
import hashlib
from ..conversations import project_collaboration_history

__all__ = ('ordinary_front_call_cap', 'historical_authoring_users',)


def ordinary_front_call_cap(
    session_id: str,
    conversation_id: str,
    *,
    minimum_completion_calls: int = 1,
    read_collaboration_events: Callable[[str, str], tuple[CollaborationEventV2, ...]],
    read_session_authoring_events: Callable[[str], tuple[CollaborationEvent, ...]],
) -> int | None:
    """Keep every offered completed v3 task's preparation/review floor intact."""
    events = read_collaboration_events(session_id, conversation_id)
    tasks = authoring_authority.authoring_tasks(read_session_authoring_events(session_id))
    caps = []
    for scope in tasks.values():
        if scope.schema_version != 3 or scope.activity not in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}:
            continue
        try:
            source, _synthesis, projected = authoring_authority.completed_authoring_source(scope, events)
        except BackendProtocolError:
            continue  # Foreign/uncompleted execution authority is never imported.
        related_steps = {event.step_id for event in events
                         if type(event.payload) is LeadRecommendationRequestPayload
                         and event.payload.source_proposal_id == source.proposal_id
                         and event.payload.source_proposal_revision == source.proposal_revision
                         and event.payload.source_proposal_digest == source.proposal_digest}
        related_steps.discard(source.step_id)
        rounds = projected.rounds_spent + sum(
            1 for event in events if event.step_id in related_steps
            and type(event.payload) is CollaborationStepStatePayload
            and event.payload.state is CollaborationStepState.RUNNING
        )
        if rounds >= COLLABORATION_ROUND_LIMIT:
            caps.append(0)
            continue
        spent = projected.physical_calls_spent + sum(
            1 for event in events if event.step_id in related_steps
            and type(event.payload) is CollaborationProviderCallPayload
            and event.payload.state is CollaborationProviderCallState.SUBMITTED
        )
        read = int(authoring_authority.has_present_references(source.basis.inputs))
        available = COLLABORATION_PHYSICAL_CALL_LIMIT - spent - (
            authoring_authority.final_review_floor(scope, source.basis.inputs, preparation=False) + 1 + read
        )
        caps.append(min(minimum_completion_calls + read, available))
    return min(caps) if caps else None


def historical_authoring_users(
    session_id: str,
    conversation_id: str,
    scope: AuthoringScope | None,
    inputs: tuple[CollaborationInputRevision, ...],
    *,
    events: tuple[CollaborationEvent, ...] | None = None,
    admission: AuthoringAdmissionBasis | None = None,
    read_collaboration_events: Callable[[str, str], tuple[CollaborationEventV2, ...]],
) -> tuple[tuple[AdmissionSource, str], ...]:
    """Capture local task-bound USER originals independently of assignment inputs."""
    if scope is None or scope.intent is not AuthoringIntent.AUTHORING:
        return ()
    records = (read_collaboration_events(session_id, conversation_id)
        if events is None else events)
    if any((event.session_id, event.conversation_id) != (session_id, conversation_id) for event in records):
        raise ValueError("historical USER sources must belong to the local conversation")
    projection = project_collaboration_history(records)
    transition_users: set[str] = set()
    task_views = tuple(view for view in projection.available_tasks
        if (view.scope.task_id, view.scope.task_revision) == (scope.task_id, scope.task_revision))
    views = tuple(view for view in task_views
        if view.status == "active" or (admission is not None and view.status == "protected"))
    if not task_views:
        for event in records:
            if type(event.payload) is not CollaborationLeadTaskTransitionPayload:
                continue
            receipt = event.payload.receipt
            ref = receipt.source_task_ref
            if (receipt.action is LeadTaskAction.RESUME and ref is not None
                    and (receipt.target_task_id, receipt.target_task_revision) == (scope.task_id, scope.task_revision)
                    and (ref.session_id, ref.conversation_id) == (session_id, conversation_id)):
                views = tuple(view for view in projection.available_tasks if view.ref == ref
                    and view.status in {"active", "held", "protected"})
                if len(views) == 1:
                    transition_users.update(item.event_id for item in records
                        if type(item.payload) is CollaborationMessagePayload and item.sender == event_journal.USER_ACTOR
                        and item.recipient == event_journal.LEAD_ACTOR and item.turn_id == event.turn_id
                        and item.step_id == receipt.planning_step_id)
    ids = set(views[0].source_event_ids) if len(views) == 1 else set()
    ids.update(transition_users)
    ordinary = {item.input_id for item in inputs}
    requested = (None if admission is None else {source.source_id for source in admission.sources
        if source.kind is AdmissionSourceKind.USER and source.source_id not in ordinary})
    if requested is not None and not requested.issubset(ids):
        raise ValueError("historical USER basis is outside the current task lineage")
    excluded = {item.locator for item in inputs if item.kind is CollaborationInputKind.USER_INPUT}
    selected = []
    for event in records:
        if event.event_id not in ids or (requested is not None and event.event_id not in requested):
            continue
        if (type(event.payload) is not CollaborationMessagePayload or event.sender != event_journal.USER_ACTOR
                or event.recipient != event_journal.LEAD_ACTOR):
            raise ValueError("historical authoring source is not a literal USER origin")
        locator = f"conversation/{event.turn_id}/user_input"
        if locator in excluded:
            continue
        text = event.payload.content
        selected.append((AdmissionSource(event.event_id, AdmissionSourceKind.USER, locator,
            "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest(), "literal_user",
            len(text.encode("utf-8"))), text))
    if len(selected) + len(inputs) > 64:
        raise SessionContextCapacityError()
    if admission is not None:
        expected = {source.source_id: source for source in admission.sources if source.source_id in (requested or ())}
        if {source.source_id: source for source, _text in selected} != expected:
            raise ValueError("historical USER admission bytes or identity changed")
    return tuple(selected)
