"""Contextual draft follow-through and exact operation authority at public APIs."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from unittest import mock

from story_writing_agents import StoryRuntime
from story_writing_agents import conversations as c
from story_writing_agents.agents import PLOT_ARCHITECT
from story_writing_agents.writing_artifacts import ArtifactApplicationError, ArtifactApplicationErrorCode
from tests import test_artifact_objectives as objectives
from tests import test_lead_orchestration as lead
from tests import test_lead_artifact_application as application
from tests import test_lead_story_commit as commit


class LeadArtifactObjectiveTests(objectives.ObjectiveCase):


    async def test_only_matching_successful_apply_satisfies_objective(self):
        conversation_id, _ = await self.start()
        reviewed = await self.review(await self.prepare(conversation_id))
        before = self.snapshots()
        wrong = replace(application._application_decision(reviewed, c.ArtifactApplicationDecisionKind.APPLY), candidate_digest=objectives.digest("wrong"))
        with self.assertRaises(ValueError):
            await self.runtime.decide_artifact_application(wrong)
        self.assertIs(self.objective(conversation_id).state, c.ArtifactObjectiveState.OPEN)
        self.assertEqual(self.snapshots(), before)
        applied = await self.apply(reviewed)
        self.assertIs(applied.outcome.outcome, c.ArtifactApplicationOutcomeKind.APPLIED)
        objective = self.objective(conversation_id)
        self.assertIs(objective.state, c.ArtifactObjectiveState.SATISFIED)
        satisfied = next(e for e in self.history(conversation_id) if e.event_id == objective.satisfied_by_event_id)
        self.assertIsInstance(satisfied.payload, c.ArtifactApplicationResultPayload)
        self.assertEqual(satisfied.payload.application_id, applied.request.application_request.application_id)
        self.assertEqual(self.workspace.path(objectives.OUTLINE).read_text(), objectives.DRAFT)
        self.assertEqual(sum(isinstance(e.payload, c.ArtifactObjectivePayload) for e in self.history(conversation_id)), 2)
        from story_writing_agents.writing_artifacts import ArtifactApplicationState
        unchanged_payload = replace(satisfied.payload, outcome=c.ArtifactApplicationOutcomeKind.UNCHANGED,
                                    result=replace(satisfied.payload.result, state=ArtifactApplicationState.UNCHANGED))
        unchanged_history = objectives.rewrite_history(self.history(conversation_id), satisfied.event_id, payload=unchanged_payload)
        self.assertIs(c.project_collaboration_history(unchanged_history).artifact_objectives[0].state, c.ArtifactObjectiveState.SATISFIED)
        other_case = objectives.ObjectiveCase()
        other_case.setUp()
        # Adopt synchronous cleanup from this setup-only async fixture.
        for cleanup, args, kwargs in other_case._cleanups:
            self.addCleanup(cleanup, *args, **kwargs)
        other_case._cleanups.clear()
        origin, _ = await other_case.start()
        foreign, _ = await other_case.start(other_case.conversation("Other objective"))
        await other_case.apply(await other_case.review(await other_case.prepare(foreign)))
        self.assertIs(other_case.objective(origin).state, c.ArtifactObjectiveState.OPEN)
        self.assertIs(other_case.objective(foreign).state, c.ArtifactObjectiveState.SATISFIED)











    def current_prepare_response(self, intent, *, task_objective_id=None, mutate=None):
        from tests import test_artifact_proposal_orchestration as artifact
        base = artifact._artifact_outcome(
            operations=lambda call, _material: [{"kind": "replace", "evidence_id": artifact._status_id(call, objectives.OUTLINE), "new_text": objectives.DRAFT}],
            evidence_status="empty", evidence_ids=lambda call, _material: [artifact._status_id(call, objectives.OUTLINE)],
            objective_intent=intent)
        def respond(call, provider):
            envelope = json.loads(base(call, provider))
            value = envelope["action"]["result"]
            value["authoring_scope"] = lead._compact_scope(
                self.scope(
                    call,
                    intent={"objective_id": task_objective_id or intent.get("objective_id")},
                )
            )
            if mutate is not None:
                mutate(value)
            return json.dumps(envelope)
        return respond
