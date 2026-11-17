"""Contextual draft follow-through and exact operation authority at public APIs."""
from __future__ import annotations
from tests.provider_wire_fixture import selection_plan
import hashlib
from tests.test_collaboration_events import editorial, editorial_request
import unittest
import json
from dataclasses import asdict, replace
from unittest import mock
from story_writing_agents import StoryRuntime, editorial_scope as scopes
from story_writing_agents.lead_preparation import call_rule_factory
from story_writing_agents import conversations as c
from story_writing_agents.agents import PLOT_ARCHITECT
from story_writing_agents.core_document_readiness import AuthoringTarget
from tests import test_artifact_objectives as objectives
from tests.lead_fixtures import artifact_review as artifact
from tests.lead_fixtures import preparation_data, provider_responses
from story_writing_agents.agents import LEAD_ORCHESTRATOR, EXECUTION_SPECIALIST
from tests.provider_wire_fixture import scenario_input
from story_writing_agents.specialist_routing import (
    ArtifactReviewDomain,
    AuthoringActivity,
)
from tests import test_lead_artifact_application_authority as application
from tests import test_lead_story_commit_authority as commit
from tests.recommendation_fixture import RecommendationCase, action, recommendation, task_assessment
from tests.lead_fixtures.specialist_routing import ROSTER




from tests.recommendation_fixture import preparation_close_observer, source_start_inputs















class LeadDefaultBudgetRefusalTests(unittest.IsolatedAsyncioTestCase):
    async def _assert_default_budget_refusal(self, path, text, brief, native_inputs):
        """Same three-role source under DEFAULT grouping has six calls and cannot buy the next two."""
        case = RecommendationCase()
        case.addCleanup = self.addCleanup
        case.setUp()
        await case.record_book_readiness(seed_before_capture=True)
        conversation_id = case.conversation("Default grouped budget boundary")
        call_start = len(case.backend.calls)
        execution_notes = []
        original_persistence = case.runtime.collaboration_result_publication._persist_collaboration_results_with_generation_pin
        def observed_persistence(**arguments):
            try:
                proposal = arguments["proposal"]
                rows = [(assignment.specialist_id, result) for assignment, result in
                    zip(proposal.assignments, arguments["assignment_results"], strict=True)][:3]
                if arguments["synthesis_result"] is not None:
                    rows.append((LEAD_ORCHESTRATOR, arguments["synthesis_result"]))
                execution_notes.append({"results": [{"agent": agent, "value_type": type(result.value).__name__,
                    "failure_type": None if result.failure is None else type(result.failure).__name__,
                    "failure_message": None if result.failure is None else str(result.failure)[:500],
                    "submitted_physical_calls": result.submitted_physical_calls} for agent, result in rows],
                    "observed_return_count": len(arguments["assignment_results"]) + (arguments["synthesis_result"] is not None),
                    "synthesis_max_physical_calls": proposal.budget.synthesis_max_physical_calls})
            except Exception as diagnostic_error:
                execution_notes.append({"diagnostic_error_type": type(diagnostic_error).__name__})
            return original_persistence(**arguments)
        original_result = preparation_data.live_result
        with mock.patch.object(case.runtime.collaboration_result_publication, "_persist_collaboration_results_with_generation_pin", new=observed_persistence):
            with mock.patch.object(preparation_data, 'live_result', side_effect=lambda call, value: original_result(
                call, {**value, "message": brief} if value.get("kind") == "proposal" else value)):
                try:
                    await case.start(conversation_id, message=text, target_path=None, specialist_ids=ROSTER,
                                     authoring_paths=(path,), native_inputs=native_inputs, response_text=brief)
                except AssertionError as error:
                    error.add_note("Execution results: " + json.dumps({"observations": execution_notes,
                        "submitted_calls": len(case.backend.calls) - call_start}, ensure_ascii=True))
                    raise
        self.assertEqual(len(case.backend.calls) - call_start, 6)
        first = case.backend.calls[call_start:call_start + 2]
        self.assertEqual(tuple(call.contract.schema_group_phase.value for call in first), ("select", "generate"))
        self.assertTrue(all(call.contract.factory.schema_group_protocol for call in first))
        source = case.projection(conversation_id).offered_draft_sources[-1]
        history = case.history(conversation_id)
        source_event = next(event for event in history if event.event_id == source.event_id)
        scope = source_event.payload.authoring_scope
        source_task = next(task for task in case.runtime.inspect_lead_collaboration(
            case.session_id, conversation_id).available_tasks if task.ref.task_id == scope.task_id)
        self.assertEqual((source_task.ref.task_id, source_task.ref.task_revision), (scope.task_id, scope.task_revision))
        self.assertEqual(source.content_digest, objectives.digest(text))
        self.assertEqual(case.projection(conversation_id).physical_calls_spent, 6)
        before_calls, before_files = len(case.backend.calls), case.snapshots()
        reports_before_refusal = case.runtime.lead_group_reports(case.session_id, conversation_id)
        objectives_before_refusal = case.projection(conversation_id).artifact_objectives
        self.assertEqual(len(objectives_before_refusal), 1)
        self.assertIs(objectives_before_refusal[0].state, c.ArtifactObjectiveState.OPEN)
        self.assertEqual(objectives_before_refusal[0].facts.target_paths, (path,))
        case.backend.enqueue(control_plan=())
        refused = await case.runtime.lead_turn(case.session_id, conversation_id,
            "Approve the previous draft and prepare it as a new document." + f" Target: {path}. {brief}"
            + "\nSupplied selected draft:\n" + text
            + "\nYou may give bounded artifact review judgments and prepare a separate exact proposal; preserve supplied facts. Execution requires separate confirmation.")
        self.assertEqual(len(case.backend.calls), before_calls)
        self.assertEqual(case.runtime.lead_group_reports(case.session_id, conversation_id),
                         reports_before_refusal)
        self.assertEqual((refused.kind, refused.next_action),
                         (c.LeadOutcomeKind.RECOMMENDATION, c.CollaborationNextAction.RETRY_LEAD_TURN))
        self.assertIsNone(refused.recommendation_request)
        self.assertIsNone(refused.proposal)
        self.assertIsNone(refused.artifact_application_request)
        self.assertIsNone(refused.story_commit_request)
        self.assertEqual(tuple(event.payload.code for event in case.payload_events(
            conversation_id, c.CollaborationLeadOutcomeFailurePayload)),
            (c.CollaborationLeadOutcomeFailureCode.REQUEST_NOT_SUBMITTED,))
        self.assertEqual(case.history(conversation_id)[:len(history)], history)
        self.assertEqual(next(event for event in case.history(conversation_id)
                              if event.event_id == source.event_id), source_event)
        self.assertEqual(case.projection(conversation_id).offered_draft_sources[-1], source)
        retained = next(task for task in case.runtime.inspect_lead_collaboration(
            case.session_id, conversation_id).available_tasks if task.ref.task_id == scope.task_id)
        self.assertEqual(retained.ref.task_revision, scope.task_revision)
        self.assertEqual(retained.ref, source_task.ref)
        self.assertEqual((retained.status, len(retained.submitted_call_ids)), (source_task.status, 6))
        self.assertEqual(case.projection(conversation_id).artifact_objectives, objectives_before_refusal)
        self.assertEqual(case.snapshots(), before_files)

    async def test_default_grouped_budget_refuses_unfunded_artifact_preparation(self):
        path = "manuscript/part-01/approved-draft.md"
        text = "# 승인된 초안\r\nThe bridge collapsed at 09:00. At 21:00 Mina claims she crossed it; her account is deliberately unreliable.\r\n" + objectives.DRAFT
        brief = "Reader effect: unease. Goal: restrained tension. Protect unreliable narration and unresolved allegiance; change only rhythm and gestures; exclude exposition and new crossings. Success: preserve ambiguity while exposing factual uncertainty."
        await self._assert_default_budget_refusal(path, text, brief, source_start_inputs)
