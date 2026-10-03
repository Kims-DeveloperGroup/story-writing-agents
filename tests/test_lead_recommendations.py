"""Public recommendation decisions and their preparation/recovery boundaries."""

from __future__ import annotations

import asyncio
import json
import os
import traceback
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

from story_writing_agents import StoryRuntime
from story_writing_agents import conversations as c
from story_writing_agents import orchestrator as owner
from story_writing_agents.agents import LEAD_ORCHESTRATOR, PLOT_ARCHITECT, EXECUTION_SPECIALIST
from story_writing_agents.specialist_routing import AuthoringActivity
from story_writing_agents.errors import BackendProtocolError
from story_writing_agents.workspace.initialization import (
    SessionArtifactBinding,
    SessionArtifactBindingError,
    compose_session_artifact_binding,
)
from story_writing_agents.writing_artifacts._storage import ArtifactRootSecurityError
from tests.recommendation_fixture import (
    DRAFT, OUTLINE, RecommendationCase, action, decision, digest,
    preparation, recommendation, rewrite_history,
)
from tests import test_lead_orchestration as lead
from tests import test_artifact_proposal_orchestration as artifact
from tests import test_lead_artifact_application as application
from tests import test_lead_story_commit as commit




class LeadRecommendationTests(RecommendationCase):






    async def test_artifact_confirm_runs_one_preparation_and_stops_at_exact_review(self):
        before = self.snapshots()
        conversation_id, turn = await self.recommend()
        request = turn.recommendation_request
        before_events = self.history(conversation_id)
        self.assertIs(request.authoring_scope.activity, AuthoringActivity.REVIEW)
        self.assertFalse(self.payload_events(conversation_id, c.CollaborationAssignmentDispatchPayload))
        self.assertEqual(self.projection(conversation_id).artifact_objectives, ())
        self.assertEqual(self.snapshots(), before)
        producer = preparation(turn)
        def observed(call, provider):
            events = self.history(conversation_id)
            decision_event = self.payload_events(conversation_id, c.LeadRecommendationDecisionPayload)[0]
            started = self.payload_events(conversation_id, c.LeadRecommendationStartedPayload)[0]
            self.assertEqual(decision_event.batch_id, started.batch_id)
            self.assertEqual(started.payload.decision_event_id, decision_event.event_id)
            self.assertEqual(started.payload.request_event_id, request.request_event_id)
            material = lead._workflow_material(call.payload)["structured_recommendation_execution"]
            self.assertEqual(call.payload["context_reuse"]["format"], "shared-objects-v1")
            self.assertLess(len(json.dumps(dict(call.payload), ensure_ascii=False)),
                            len(json.dumps(lead._expanded_context_payload(call.payload), ensure_ascii=False)))
            self.assertEqual(material["request"]["action_digest"], request.action_digest)
            self.assertEqual(material["decision"]["decision"]["kind"], "confirm")
            self.assertEqual(material["start"]["execution_attempt_id"], started.payload.execution_attempt_id)
            self.assertIn(request.instruction, json.dumps(material, ensure_ascii=False))
            self.assertEqual(sum(type(e.payload) is c.CollaborationMessagePayload and
                e.direction is c.CollaborationDirection.USER_TO_LEAD for e in events), 1)
            return producer(call, provider)
        self.backend.enqueue(observed)
        calls = len(self.backend.calls)
        with mock.patch.object(self.runtime, "lead_turn", side_effect=AssertionError("recursive public Lead")):
            prepared = await self.runtime.session(self.session_id).decide_lead_recommendation(decision(turn))
        self.assertIs(type(prepared), c.LeadTurnResult)
        self.assertEqual(len(self.backend.calls) - calls, 1)
        self.assertIs(prepared.kind, c.LeadOutcomeKind.PROPOSAL)
        self.assertIs(prepared.next_action, c.CollaborationNextAction.SUBMIT_CONFIRMATION)
        self.assertIsNone(prepared.recommendation_request)
        self.assertIsNone(prepared.artifact_application_request)
        self.assertIsNone(prepared.story_commit_request)
        self.assertEqual(prepared.proposal.authoring_scope, request.authoring_scope)
        self.assertEqual(prepared.proposal.participants, (PLOT_ARCHITECT,))
        self.assertTrue(all(item.mode.value == "review" for item in prepared.proposal.assignments))
        candidate = prepared.artifact_proposal.candidate.candidate
        operation = candidate.change_set.operations[0]
        self.assertEqual((operation.operation_kind.value, operation.path, operation.content),
                         ("replace", OUTLINE, DRAFT.encode()))
        objective = self.objective(conversation_id)
        self.assertIs(objective.state, c.ArtifactObjectiveState.OPEN)
        self.assertIs(type(objective.facts), c.ArtifactObjectiveFactsV2)
        self.assertEqual(objective.candidate_binding.candidate_digest, candidate.candidate_digest)
        self.assertEqual(objective.draft_sources, request.draft_sources)
        self.assertEqual(self.history(conversation_id)[:len(before_events)], before_events)
        projection = self.assert_action_closed(conversation_id)
        result_event = self.payload_events(conversation_id, c.LeadRecommendationResultPayload)[0]
        self.assertLess(result_event.sequence, self.history(conversation_id)[-1].sequence)
        self.assertIs(projection.current_state, c.CollaborationStepState.AWAITING_CONFIRMATION)
        self.assertEqual(self.snapshots(), before)

        for aliases, omit_source in (((), False), (("recommendation_request",), False),
                                     (("recommendation_decision",), False),
                                     (("recommendation_request", "recommendation_decision"), True)):
            cid = self.conversation("Raw confirmed drafting consultation")
            self.backend.enqueue(recommendation(recommended_action=action(kind="prepare_consultation"),
                authoring_scope=lambda call: lead._live_scope(call, activity="draft", domains=("plot_structure",))))
            offered = await self.runtime.lead_turn(self.session_id, cid, "Recommend Plot drafting from this brief.")
            self.assertIs(offered.kind, c.LeadOutcomeKind.RECOMMENDATION, offered)
            request = offered.recommendation_request
            source_ids = [item.input_id for item in request.basis.inputs]
            self.assertTrue(source_ids)
            mode = "review" if request.authoring_scope.activity is AuthoringActivity.REVIEW else "draft"
            raw_observations = []
            def raw(call, _provider):
                selected = ([] if omit_source else source_ids) + list(aliases)
                body = {"kind": "proposal", "message": request.purpose,
                    "authoring_scope": lead._compact_scope(c.authoring_scope_to_dict(request.authoring_scope)),
                    "proposal": {"assignments": [{"agent_id": agent, "mode": mode,
                        "instruction": request.instruction, "input_ids": selected} for agent in request.specialist_ids]}}
                encoded = lead.wire_answer(body, lead.wire_contract_id(call.payload))
                raw_observations.append((json.loads(encoded)["agent_action"]["task_result"]["specialist_consultation_proposal"]["assignments"][0]["input_source_ids"],
                                         lead.diagnose_json_text(encoded, call.schema)))
                return encoded
            self.backend.enqueue(raw)
            if omit_source:
                self.backend.enqueue(raw)
            calls = len(self.backend.calls)
            prepared = await self.runtime.decide_lead_recommendation(decision(offered))
            self.assertTrue(raw_observations, prepared)
            for selected, issues in raw_observations:
                self.assertEqual(selected, ([] if omit_source else source_ids) + list(aliases))
                self.assertEqual(issues, (), (aliases, issues))
            self.assertEqual(len(self.backend.calls) - calls, 2 if omit_source else 1, prepared)
            self.assert_no_downstream(cid)
            if omit_source:
                self.assertIsNone(prepared.proposal)
                self.assertTrue(prepared.validation_details)
            else:
                self.assertIs(prepared.next_action, c.CollaborationNextAction.SUBMIT_CONFIRMATION, prepared)
                self.assertEqual(prepared.proposal.authoring_scope, request.authoring_scope)
                self.assertEqual(prepared.proposal.purpose, request.purpose)
                for assignment in prepared.proposal.assignments:
                    self.assertEqual(set(assignment.input_ids), {*source_ids, "recommendation_request", "recommendation_decision"})
                    self.assertEqual(len(assignment.input_ids), len(source_ids) + 2)
            self.assertEqual(self.snapshots(), before)







    async def test_exact_stale_duplicate_and_concurrent_decisions_never_reuse_authority(self):
        conversation_id, turn = await self.recommend()
        snapshots = self.snapshots()
        choice = decision(turn)
        before, calls = self.history(conversation_id), len(self.backend.calls)
        foreign = self.conversation("Foreign decision")
        from story_writing_agents.errors import ConversationNotFoundError
        for change in ({"request_event_id": "foreign"}, {"request_tail_sequence": choice.request_tail_sequence + 1},
                       {"request_id": "foreign"}, {"action_id": "foreign"}, {"action_revision": 2},
                       {"action_digest": digest("foreign")}, {"conversation_id": foreign}, {"session_id": "foreign_session"}):
            expected = ConversationNotFoundError if "session_id" in change else BackendProtocolError
            with self.subTest(change=change), self.assertRaises(expected):
                await self.runtime.decide_lead_recommendation(replace(choice, **change))
        self.assertEqual(self.history(conversation_id), before)
        self.assertEqual(len(self.backend.calls), calls)
        entered, release = asyncio.Event(), asyncio.Event()
        producer = preparation(turn)
        async def held(call, provider):
            entered.set()
            await release.wait()
            return producer(call, provider)
        self.backend.enqueue(held)
        first = asyncio.create_task(self.runtime.decide_lead_recommendation(choice))
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(self.runtime.decide_lead_recommendation(choice))
        try:
            await asyncio.sleep(0)
            self.assertFalse(second.done())
            release.set()
            results = await asyncio.gather(first, second, return_exceptions=True)
        finally:
            release.set()
            for task in (first, second):
                if not task.done(): task.cancel()
            await asyncio.gather(first, second, return_exceptions=True)
        self.assertIs(type(results[0]), c.LeadTurnResult)
        self.assertIsInstance(results[1], BackendProtocolError)
        self.assertEqual(len(self.backend.calls), calls + 1)
        self.assertEqual(len(self.payload_events(conversation_id, c.LeadRecommendationStartedPayload)), 1)
        with self.assertRaises(BackendProtocolError):
            await self.runtime.decide_lead_recommendation(choice)
        self.assertEqual(len(self.backend.calls), calls + 1)
        self.assert_no_downstream(conversation_id)
        self.assertEqual(self.snapshots(), snapshots)



















class LeadRecommendationRecoveryTests(RecommendationCase):
    async def reference_offer(self, *, kind="prepare_consultation", objective_id=None):
        return await self.recommend(recommended_action=action(kind=kind,
            source_ids=("reference_1", "current_response"), objective_id=objective_id),
            text=f"Inspect ${{{OUTLINE}}} and recommend exactly one preparation.")





    async def test_missing_corrupt_or_unresolved_origin_accounting_never_authorizes_retry(self):
        from story_writing_agents.writing_artifacts.authorization import ReferenceAuthorizationError, ReferenceAuthorizationErrorCode, ReferenceOperationFailure
        for fault in ("missing", "corrupt", "unfinished", "uncertain"):
            conversation_id, turn = await self.reference_offer()
            error = BackendProtocolError("Synthetic unresolved preparation")
            self.backend.enqueue(lead._SubmittedFailure(error, submission_uncertain=fault == "uncertain"))
            inspect_type = type(self.runtime.artifacts)
            inspect_original = inspect_type.inspect_reference_operation
            def broken(repository, operation_id):
                history = inspect_original(repository, operation_id)
                if fault == "missing": return None
                if fault == "corrupt": raise ReferenceAuthorizationError(ReferenceAuthorizationErrorCode.EVIDENCE_CORRUPT)
                if fault == "unfinished": return replace(history, invocation_events=(history.invocation_events[0][:-1], *history.invocation_events[1:]))
                return history
            with mock.patch.object(inspect_type, "inspect_reference_operation", new=broken):
                expected = ReferenceAuthorizationError if fault == "corrupt" else ReferenceOperationFailure
                with self.assertRaises(expected):
                    await self.runtime.decide_lead_recommendation(decision(turn))
            self.assertFalse(self.payload_events(conversation_id, c.LeadRecommendationResultPayload))
            calls, history = len(self.backend.calls), self.history(conversation_id)
            restarted = StoryRuntime(self.control_workspace, self.backend)
            blocked = await restarted.lead_turn(self.session_id, conversation_id, "Do not replay uncertain work.")
            self.assertIs(blocked.next_action, c.CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME if fault == "uncertain"
                          else c.CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY)
            self.assertEqual(len(self.backend.calls), calls)
            self.assertEqual(self.history(conversation_id), history)
            self.assertIsNone(blocked.recommendation_request)
