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
from tests.recommendation_fixture import RecommendationCase, action, recommendation
from tests.test_specialist_routing_runtime import ROSTER


class LeadArtifactObjectiveTests(RecommendationCase):


    async def test_only_matching_successful_apply_satisfies_objective(self):
        path = "manuscript/part-01/approved-draft.md"
        text = "# 승인된 초안\r\n" + objectives.DRAFT
        pristine = self.snapshots()
        conversation_id, _ = await self.start(message=text, target_path=None, specialist_ids=ROSTER)
        source = self.projection(conversation_id).offered_draft_sources[-1]
        scope = next(e.payload.authoring_scope for e in self.history(conversation_id) if e.event_id == source.event_id)
        self.assertEqual(scope.target_paths, ())
        self.assertEqual(self.projection(conversation_id).artifact_objectives, ())
        self.backend.enqueue(recommendation(message="Prepare the approved draft as a new document.",
            recommended_action=action(source_ids=(source.event_id,),
                target_paths=(path,), instruction="Preserve the exact approved draft.", specialist_ids=ROSTER),
            authoring_scope=lambda call: lead._live_scope(call, activity="review", paths=(path,),
                domains=tuple(domain.value for domain in scope.domains), task_index=0)))
        offered = await self.runtime.lead_turn(self.session_id, conversation_id,
            "Approve the previous draft and prepare it as a new document.")
        self.assertIs(offered.kind, c.LeadOutcomeKind.RECOMMENDATION, offered)
        request = offered.recommendation_request
        self.assertIsNone(request.objective_id)
        self.assertEqual(self.projection(conversation_id).artifact_objectives, ())
        self.assertEqual(request.draft_sources, (source,))
        self.assertEqual((request.authoring_scope.task_id, request.authoring_scope.task_revision),
                         (scope.task_id, scope.task_revision))
        self.assertEqual(source.content_digest, objectives.digest(text))
        calls = len(self.backend.calls)
        self.runtime = StoryRuntime(self.control_workspace, self.backend)
        prepared = await self.confirm(offered, text=text)
        self.assertEqual(len(self.backend.calls), calls + 1)
        self.assertIs(prepared.kind, c.LeadOutcomeKind.PROPOSAL, prepared)
        self.assertEqual(prepared.proposal.participants, ROSTER)
        self.assertEqual(prepared.proposal.authoring_scope, request.authoring_scope)
        historical = next(item for item in prepared.proposal.basis.inputs if item.input_id == source.event_id)
        self.assertIs(historical.kind, c.CollaborationInputKind.CONVERSATION_EVENT)
        self.assertEqual(historical.revision_digest, source.content_digest)
        operation = prepared.artifact_proposal.candidate.candidate.change_set.operations[0]
        self.assertEqual((operation.operation_kind.value, operation.path, operation.content),
                         ("create", path, text.encode()))
        self.assertEqual(self.objective(conversation_id).draft_sources, (source,))
        reviewed = await self.review(prepared)
        self.assertEqual(reviewed.artifact_application_request.application_request.candidate_digest,
                         prepared.artifact_proposal.candidate.candidate.candidate_digest)
        self.assertEqual(self.objective(conversation_id).candidate_binding.candidate_digest,
                         reviewed.artifact_application_request.application_request.candidate_digest)
        self.assertTrue(all(not turn.validation_details for turn in (offered, prepared, reviewed)))
        self.assertEqual(self.snapshots(), pristine)
        self.assertFalse(self.workspace.path(path).exists())
        self.assertIsNone(reviewed.story_commit_request)
        for call in self.backend.calls:
            self.assertNotIn("response_correction", lead._workflow_material(call.payload))
            self.assertNotIn("structured_response_repair", call.payload)
        dispatches = self.payload_events(conversation_id, c.CollaborationAssignmentDispatchPayload)
        self.assertEqual([e.payload.assignment.mode.value for e in dispatches].count("draft"), len(ROSTER))
        self.assertEqual([e.payload.assignment.mode.value for e in dispatches].count("review"), len(ROSTER))
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
        self.assertEqual(self.workspace.path(path).read_bytes(), text.encode())
        self.assertEqual(self.snapshots()[1:], pristine[1:])
        self.assertIsNotNone(applied.commit_request)
        rejected = await self.runtime.decide_story_commit(commit._commit_decision(applied, commit.StoryCommitDecision.REJECT))
        self.assertIs(rejected.outcome.outcome, commit.StoryCommitOutcomeKind.REJECTED)
        self.assertEqual(self.snapshots()[1:], pristine[1:])
        self.assertEqual({type(e.payload) for e in self.history(conversation_id)
                          if e.kind is c.CollaborationEventKind.ARTIFACT_OBJECTIVE}, {c.ArtifactObjectivePayloadV2})
        self.assertEqual(self.projection(conversation_id).artifact_objectives, (objective,))
        self.assertEqual(objective.facts.origin_recommendation_request_event_id, request.request_event_id)
        self.assertEqual(objective.facts.origin_recommendation_decision_event_id,
                         self.payload_events(conversation_id, c.LeadRecommendationDecisionPayload)[0].event_id)
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
