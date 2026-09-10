"""Synthetic recommendation producers and real-runtime setup; no automatic wire repair."""

from __future__ import annotations

from dataclasses import replace

from story_writing_agents import conversations as c
from story_writing_agents.agents import PLOT_ARCHITECT
from tests import test_lead_orchestration as lead
from tests import test_artifact_proposal_orchestration as artifact
from tests.test_artifact_objectives import DRAFT, OUTLINE, ObjectiveCase, digest, rewrite_history


def action(*, kind="prepare_artifact", source_ids=("current_response",),
           objective_id=None, instruction="Prepare the exact offered synthetic outline.",
           purpose="Prepare the approved outline for separate review.",
           target_paths=(OUTLINE,), specialist_ids=(PLOT_ARCHITECT,)):
    return {
        "kind": kind, "purpose": purpose, "instruction": instruction,
        "expected_result": "One exact proposal awaiting its separate review decision.",
        "source_ids": list(source_ids), "objective_id": objective_id,
        "target_paths": list(target_paths) if kind == "prepare_artifact" else [],
        "specialist_ids": list(specialist_ids) if kind == "prepare_consultation" else [],
    }


def recommendation(*, message=DRAFT, recommended_action=None, mutate=None):
    selected = action() if recommended_action is None else recommended_action

    def respond(call, _provider):
        value = {
            "kind": "recommendation", "message": message,
            "scope": "workflow_meta", "evidence_status": "not_applicable",
            "evidence_ids": [], "proposal": None, "artifact_intent": None,
            "objective_intent": None, "recommended_action": selected,
        }
        if mutate is not None:
            mutate(value)
        return lead._provider_envelope(call, value)
    return respond


def decision(turn, kind=c.CollaborationConfirmationDecision.CONFIRM, **changes):
    request = turn.recommendation_request
    values = dict(
        session_id=turn.session_id, conversation_id=turn.conversation_id,
        request_event_id=request.request_event_id, request_tail_sequence=request.tail_sequence,
        request_id=request.request_id, action_id=request.action_id,
        action_revision=request.action_revision, action_digest=request.action_digest,
        kind=kind, revision_request=("Revise only the preparation plan."
            if kind is c.CollaborationConfirmationDecision.REVISE else None),
    )
    values.update(changes)
    return c.LeadRecommendationDecision(**values)


def preparation(turn, *, text=DRAFT, mutate=None):
    request = turn.recommendation_request
    selected = [item.event_id for item in request.draft_sources]
    intent = ({"action": "start", "request": request.instruction,
               "target_paths": list(request.target_paths), "next_step": "prepare",
               "draft_source_event_ids": selected}
              if request.objective_id is None else
              {"action": "continue", "objective_id": request.objective_id,
               "next_step": "prepare", "draft_source_event_ids": selected})
    producer = artifact._artifact_outcome(
        operations=lambda call, material: [{"kind": "replace",
            "evidence_id": artifact._status_id(call, OUTLINE), "new_text": text}],
        evidence_status="empty",
        evidence_ids=lambda call, material: [artifact._status_id(call, OUTLINE)],
        objective_intent=intent,
    )
    if request.kind is c.LeadRecommendationActionKind.PREPARE_CONSULTATION:
        intent = (None if request.objective_id is None else {
            "action": "continue", "objective_id": request.objective_id,
            "next_step": "consult", "draft_source_event_ids": selected,
        })
        sources = [item.input_id for item in request.basis.inputs]
        sources += ["recommendation_request", "recommendation_decision"]
        base = lead._proposal_outcome([
            {"agent_id": item, "instruction": request.instruction, "input_ids": sources}
            for item in request.specialist_ids], purpose=request.purpose)

        def producer(call, provider):
            import json
            value = json.loads(base(call, provider))
            value["action"]["result"]["objective_intent"] = intent
            return json.dumps(value, ensure_ascii=False)

    def respond(call, provider):
        if mutate is None:
            return producer(call, provider)
        import json
        value = json.loads(producer(call, provider))
        mutate(value["action"]["result"])
        return json.dumps(value, ensure_ascii=False)
    return respond


class RecommendationCase(ObjectiveCase):
    """Disposable Git-backed workspace, public event store and controlled providers."""

    async def recommend(self, conversation_id=None, *, recommended_action=None,
                        message=DRAFT, text="Offer a synthetic outline and recommend preparing it."):
        conversation_id = conversation_id or self.conversation("Synthetic recommendation")
        self.backend.enqueue(recommendation(message=message, recommended_action=recommended_action))
        turn = await self.runtime.lead_turn(self.session_id, conversation_id, text)
        self.assertIs(turn.kind, c.LeadOutcomeKind.RECOMMENDATION, turn)
        self.assertIsNotNone(turn.recommendation_request, turn)
        return conversation_id, turn

    async def confirm(self, turn, *, text=DRAFT, through_session=False):
        self.backend.enqueue(preparation(turn, text=text))
        owner = self.runtime.session(self.session_id) if through_session else self.runtime
        return await owner.decide_lead_recommendation(decision(turn))

    def payload_events(self, conversation_id, payload_type):
        return tuple(e for e in self.history(conversation_id) if type(e.payload) is payload_type)

    def assert_no_downstream(self, conversation_id):
        excluded = {c.CollaborationAssignmentDispatchPayload,
                    c.ArtifactApplicationDecisionPayload, c.ArtifactApplicationResultPayload,
                    c.StoryCommitDecisionPayload, c.StoryCommitResultPayload}
        self.assertFalse(any(type(e.payload) in excluded for e in self.history(conversation_id)))
        self.assertTrue(all(agent == lead.LEAD_ORCHESTRATOR for agent, _ in self.backend.call_records))

    def assert_action_closed(self, conversation_id, state=c.LeadRecommendationState.COMPLETED):
        projection = self.projection(conversation_id)
        self.assertIs(projection.current_recommendation_state, state)
        result = projection.current_recommendation_result
        self.assertIsNotNone(result)
        events = {event.event_id: event for event in self.history(conversation_id)}
        if result.start_event_id is not None:
            start = events[result.start_event_id]
            source = events[result.lead_result_event_id]
            self.assertEqual((source.turn_id, source.step_id), (start.turn_id, start.step_id))
            self.assertLess(start.sequence, source.sequence)
        self.assert_no_downstream(conversation_id)
        return projection
