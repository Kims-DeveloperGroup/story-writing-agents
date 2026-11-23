"""Validate the exact Lead exploration event history."""

from __future__ import annotations

import hashlib

from ..event_contracts.authoring_records import (
    CollaborationExplorationAdoptionPayload,
    CollaborationLeadExplorationRequestPayload,
    CollaborationLeadExplorationResultPayload,
)
from ..event_contracts.control_records import (
    CollaborationProviderCallPayload,
    CollaborationStepStatePayload,
)
from ..event_contracts.execution import CollaborationMessagePayload
from ..event_contracts.lead_outcomes import CollaborationLeadOutcomeFailurePayload
from ..events import (
    CollaborationEvent as CollaborationEventV2,
    CollaborationProposal,
    encode_collaboration_event,
)
from ...specialist_routing import (
    AuthoringActivity,
    AuthoringIntent,
)


def validate_exploration_history(events: tuple[CollaborationEventV2, ...]) -> None:
    prior: dict[str, CollaborationEventV2] = {}
    exploration_turns: set[str] = set()
    for event in events:
        payload = event.payload
        if event.turn_id in exploration_turns and type(payload) not in {CollaborationMessagePayload, CollaborationLeadExplorationRequestPayload, CollaborationLeadExplorationResultPayload, CollaborationProviderCallPayload, CollaborationLeadOutcomeFailurePayload, CollaborationStepStatePayload}:
            raise ValueError('exploration history cannot grant normal workflow authority')
        if type(payload) is CollaborationLeadExplorationRequestPayload:
            user = prior.get(payload.user_event_id)
            if user is None or type(user.payload) is not CollaborationMessagePayload or user.sender.kind.value != 'user' or ((user.session_id, user.conversation_id, user.turn_id) != (event.session_id, event.conversation_id, event.turn_id)):
                raise ValueError('exploration request requires its exact current user input')
            if event.sender.kind.value != 'user':
                raise ValueError('only the user selects exploration')
            exploration_turns.add(event.turn_id)
        elif type(payload) is CollaborationLeadExplorationResultPayload:
            request = prior.get(payload.report.request_event_id)
            if request is None or type(request.payload) is not CollaborationLeadExplorationRequestPayload or (request.session_id, request.conversation_id, request.turn_id, request.step_id) != (event.session_id, event.conversation_id, event.turn_id, event.step_id):
                raise ValueError('exploration report has no matching explicit request')
            if event.sender.kind.value != 'lead' or event.sender.agent_id != 'lead_orchestrator':
                raise ValueError('exploration report requires the Lead actor')
        elif type(payload) is CollaborationExplorationAdoptionPayload:
            capture = payload.capture
            user = prior.get(capture.user_event_id)
            source = prior.get(capture.selection.result_event_id)
            if user is None or user.sender.kind.value != 'user' or (user.session_id, user.conversation_id, user.turn_id) != (event.session_id, event.conversation_id, event.turn_id) or (event.sender.kind.value != 'user'):
                raise ValueError('adoption requires its exact explicit user input')
            if capture.selection.source_conversation_id == event.conversation_id:
                if source is None or type(source.payload) is not CollaborationLeadExplorationResultPayload or source.session_id != event.session_id:
                    raise ValueError('adoption requires an available typed idea result')
                ideas = tuple(i for i in source.payload.report.ideas if i.idea_id == capture.selection.idea_id)
                if len(ideas) != 1:
                    raise ValueError('adoption idea is unavailable or ambiguous')
                capture.validate_source(ideas[0].material_text, hashlib.sha256(encode_collaboration_event(source)).hexdigest())
                reports = tuple(item for item in prior.values() if type(item.payload) is CollaborationLeadExplorationResultPayload)
                if not reports or reports[-1].event_id != source.event_id:
                    raise ValueError('adoption cannot silently reselect a stale result')
            if any((type(item.payload) is CollaborationExplorationAdoptionPayload and item.payload.capture.fresh_task_id == capture.fresh_task_id for item in prior.values())) or any((getattr(item.payload, 'authoring_scope', None) is not None and item.payload.authoring_scope.task_id == capture.fresh_task_id for item in prior.values())):
                raise ValueError('adoption cannot reuse a previous task')
        if type(payload) is CollaborationProposal and payload.authoring_scope is not None:
            capture_events = tuple((item for item in prior.values() if type(item.payload) is CollaborationExplorationAdoptionPayload and item.payload.capture.fresh_task_id == payload.authoring_scope.task_id))
            if capture_events:
                adopted = capture_events[0]
                capture = adopted.payload.capture
                inputs = tuple((i for i in payload.basis.inputs if i.input_id == 'adopted_idea'))
                if any(('adopted_idea' not in assignment.input_ids for assignment in payload.assignments)):
                    raise ValueError('every adopted-task assignment must retain the exact chosen idea')
                if len(inputs) != 1 or inputs[0].locator != f'conversation/{adopted.event_id}' or inputs[0].revision_digest != f'sha256:{capture.selected_material_digest}':
                    raise ValueError('adopted task must preserve its exact selected idea input')
                scope = payload.authoring_scope
                if scope.task_revision == 1 and (not any((type(item.payload) is CollaborationProposal and item.payload.authoring_scope is not None and (item.payload.authoring_scope.task_id == scope.task_id) for item in prior.values()))):
                    activities = ({AuthoringActivity.REVIEW} if capture.selection.intent is AuthoringIntent.REVIEW_ONLY
                              else {AuthoringActivity.DRAFT, AuthoringActivity.REVISE})
                    if scope.activity not in activities or scope.intent is not capture.selection.intent or scope.target_paths != capture.selection.target_paths or (payload.budget.rounds_spent != 0):
                        raise ValueError('adoption requires fresh work with the exact selected intent and targets')
        prior[event.event_id] = event
