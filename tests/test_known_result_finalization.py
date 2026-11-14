from __future__ import annotations
from dataclasses import asdict
import json
from story_writing_agents import agents, conversations
from story_writing_agents.orchestrator import StoryRuntime
from story_writing_agents.agents import PLOT_ARCHITECT
from story_writing_agents.artifact_proposals import serialize_artifact_review_candidate
from story_writing_agents.conversations import (
    ArtifactProposalState,
    ArtifactReviewFailureCode,
    ArtifactReviewFailurePayload,
    ArtifactReviewedProposal,
    CollaborationConfirmationDecision,
    CollaborationNextAction,
    CollaborationProposal,
    CollaborationSpecialistAssessment,
    CollaborationStepState,
    project_collaboration_history,
)
from tests import test_lead_orchestration as lead, test_specialist_orchestration_accounting as accounting
from tests.known_result_finalization_fixture import finalize_known_result
from tests.provider_wire_fixture import selection_plan
from tests.test_artifact_proposal_orchestration import (
    ARTIFACT_FIXTURE_OUTLINE_PATH,
    ARTIFACT_FIXTURE_RULES_PATH,
    ArtifactReviewFixture,
    artifact_evidence_status_id,
    artifact_review_outcome,
)
from tests.test_lead_orchestration import (
    BudgetConsultationFixture,
    LEAD_FIXTURE_READINESS_DOCUMENTS,
    lead_confirmation_decision,
    lead_fixture_authoring_scope,
    lead_specialist_outcome,
)
from tests.test_specialist_orchestration_execution import (
    ExecutionInputFixture,
    specialist_raw_outcome,
    stop_after_durable_result_patch,
)


class KnownResultRecoveryTests(ExecutionInputFixture, lead.LeadOrchestrationTestCase):
    async def test_restarted_finalization_restores_only_this_execution_diagnostics(self) -> None:
        conversation_id = self.conversation("Restart diagnostic finalization")
        proposed = await self.proposal(conversation_id, text=accounting._REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(accounting._review_assessment()))
        private = "PRIVATE_RESTARTED_SPECIALIST_VALUE"
        self.backend.enqueue_for(agents.PLOT_ARCHITECT, specialist_raw_outcome({'message': 0,
            'assessment': 'supports', private: private}))
        self.backend.enqueue_for(agents.PLOT_ARCHITECT, lead.lead_specialist_outcome())
        self.backend.enqueue_for(agents.LEAD_ORCHESTRATOR, lead._synthesis_outcome())
        with stop_after_durable_result_patch(self.runtime):
            with self.assertRaisesRegex(RuntimeError, "durable execution result"):
                await self.runtime.decide_lead_step(
                    lead.lead_confirmation_decision(proposed, conversations.CollaborationConfirmationDecision.CONFIRM)
                )

        known = self.history(conversation_id)
        projection = conversations.project_collaboration_history(known)
        self.assertIs(projection.current_state, conversations.CollaborationStepState.RUNNING)
        self.assertEqual(len(lead._event_payloads(known, conversations.CollaborationExecutionResult)), 1)
        expected = tuple((chunk for payload in lead._submitted_calls( known) for chunk in payload.validation_details))
        self.assertTrue(expected)
        restarted_backend = lead._LeadBackend(test_case=self)
        restarted = StoryRuntime(self.control_workspace, restarted_backend)  # type: ignore[arg-type]
        restored = await restarted.lead_turn(self.session_id, conversation_id, 'Finalize the known result.')
        self.assertIs(restored.state, conversations.CollaborationStepState.COMPLETED)
        self.assertEqual(restored.validation_details, expected)
        self.assertEqual(restarted_backend.calls, [])
        complete = self.history(conversation_id)
        repeated = finalize_known_result(restarted, self.session_id, known, projection)
        self.assertEqual(repeated, restored)
        self.assertEqual(self.history(conversation_id), complete)
        self.assertNotIn(private, repr(complete))

    async def test_domain_rejection_details_survive_restarted_finalization(self) -> None:
        for rejected_role in (agents.PLOT_ARCHITECT, agents.LEAD_ORCHESTRATOR):
            with self.subTest(rejected_role=rejected_role):
                conversation_id = self.conversation(
                    f"Restart domain rejection from {rejected_role}" )
                proposed = await self.proposal(conversation_id, text=accounting._REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(accounting._review_assessment()))
                if rejected_role == agents.PLOT_ARCHITECT:
                    self.backend.enqueue_for(agents.PLOT_ARCHITECT, lead.lead_specialist_outcome(message=''))
                    self.backend.enqueue_for(
                        agents.PLOT_ARCHITECT, lead.lead_specialist_outcome(),
                    )
                else:
                    self.backend.enqueue_for(
                        agents.PLOT_ARCHITECT, lead.lead_specialist_outcome(),
                    )
                    self.backend.enqueue_for(
                        agents.LEAD_ORCHESTRATOR, lead._synthesis_outcome(message=""),
                    )
                self.backend.enqueue_for(
                    agents.LEAD_ORCHESTRATOR, lead._synthesis_outcome(),
                )
                with stop_after_durable_result_patch(self.runtime):
                    with self.assertRaisesRegex(RuntimeError, "durable execution result"):
                        await self.runtime.decide_lead_step(lead.lead_confirmation_decision(proposed,
                            conversations.CollaborationConfirmationDecision.CONFIRM))

                known = self.history(conversation_id)
                projection = conversations.project_collaboration_history(known)
                self.assertIs(projection.current_state, conversations.CollaborationStepState.RUNNING)
                self.assertEqual(len(lead._event_payloads(known, conversations.CollaborationExecutionResult)), 1)
                submitted = lead._submitted_calls(known)
                expected = tuple((detail for payload in lead._event_payloads(known,
                    conversations.CollaborationMessagePayload) for detail in payload.validation_details))
                self.assertEqual(
                    tuple(p.validation_details for p in submitted if p.validation_details),
                    (expected,))
                self.assertTrue(expected)
                requirement = (
                    r'expected pattern "[\\s\\S]"'
                    if rejected_role == agents.PLOT_ARCHITECT else
                    r'expected pattern "^[\u0000-\ud7ff\ue000-\udbff\udfff]+\\z"'
                    '\n$.agent_action.task_result.response_text: '
                    'expected at least 1 characters, got 0')
                self.assertEqual(expected[0],
                    "Original response\n$.agent_action.task_result.response_text: " + requirement)

                restarted_backend = lead._LeadBackend(test_case=self)
                restarted = StoryRuntime(
                self.control_workspace, restarted_backend,  # type: ignore[arg-type]
                )
                restored = await restarted.lead_turn(self.session_id, conversation_id, 'Finalize the known result.')
                self.assertIs(restored.state, conversations.CollaborationStepState.COMPLETED)
                self.assertEqual(restored.validation_details, expected)
                self.assertEqual(restarted_backend.calls, [])
                complete = self.history(conversation_id)
                repeated = finalize_known_result(restarted, self.session_id, known, projection)
                self.assertEqual(repeated, restored)
                self.assertEqual(self.history(conversation_id), complete)


class KnownResultBudgetTests(BudgetConsultationFixture, lead.LeadOrchestrationTestCase):
    async def test_unaffordable_continuation_finalizes_exact_result_without_authority(self):
        await self.record_book_readiness()
        artifacts = {path: path.read_bytes() for path in self.workspace.root.rglob("*.md")}
        await self._budget_consultation(infeasible=True)
        self.assertEqual({path: path.read_bytes() for path in self.workspace.root.rglob("*.md")}, artifacts)
        conversation, proposed, decision_result, history, synthesis, result = await self._budget_consultation()
        turn = decision_result.turn
        self.assertIs(turn.state, CollaborationStepState.COMPLETED)
        self.assertIs(turn.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD)
        self.assertIsNone(turn.proposal)
        self.assertIsNone(turn.confirmation_request_id)
        self.assertIn(synthesis.payload.message, turn.message)
        self.assertIn("exceeds its remaining call budget", turn.message)
        self.assertEqual(len([e for e in history if type(e.payload) is CollaborationProposal]), 1)
        self.assertEqual(project_collaboration_history(history).physical_calls_spent, 10)
        self.assertEqual(history[-2].payload.content, turn.message)
        self.assertIs(project_collaboration_history(history).current_state, CollaborationStepState.COMPLETED)
        before = tuple(self.backend.calls)
        with self.assertRaises(ValueError):
            await self.runtime.decide_lead_step(lead_confirmation_decision(proposed, CollaborationConfirmationDecision.CONFIRM))
        self.assertEqual(tuple(self.backend.calls), before)
        self.assertEqual(self.history(conversation), history)
        self.assertEqual({path: path.read_bytes() for path in artifacts}, artifacts)


class KnownResultAssessmentTests(ArtifactReviewFixture, lead.LeadOrchestrationTestCase):
    async def test_all_assessments_are_preserved_and_partial_failure_never_reviews(
        self,
    ) -> None:
        from tests.recommendation_fixture import task_assessment
        await self.record_book_readiness()
        assessments = (
            "supports",
            "concern",
            "conflict",
            "insufficient_evidence", )
        for ordinal, assessment in enumerate(assessments, start=1):
            with self.subTest(assessment=assessment):
                conversation_id = self.conversation(f"Assessment {ordinal}")
                proposed, decided = await self.reviewed_create(
                    conversation_id,
                    assessment=assessment, )
                reviewed = decided.turn.artifact_proposal.reviewed_proposal
                self.assertEqual(
                    reviewed.reviews[0].assessment,
                    CollaborationSpecialistAssessment(assessment), )
                expected_unresolved = (
                    ()
                    if assessment == "supports"
                    else proposed.proposal.assignments[0].input_ids )
                self.assertEqual(reviewed.unresolved_input_ids, expected_unresolved)
                self.assertEqual(
                    reviewed.limitations,
                    (
                        "No additional limitations were reported."
                        if not expected_unresolved
                        else "Unresolved reviewer inputs remain: "
                        + ", ".join(expected_unresolved)
                        + "."
                    ),
                )

        self.workspace.path(ARTIFACT_FIXTURE_RULES_PATH).write_text(dict(LEAD_FIXTURE_READINESS_DOCUMENTS)[ARTIFACT_FIXTURE_RULES_PATH], encoding="utf-8")
        await self.record_book_readiness(preserve_documents=True)
        failed_id = self.conversation("Partial reviewer failure")
        self.backend.enqueue(
            artifact_review_outcome(
                operations=[ { "kind" : "create" , "new_text" : "New scene.\n" , "suggested_path" : "manuscript/part-02/new.md" , } ],
                evidence_status="incomplete",
                evidence_ids=lambda call, _material, *, input_payload: [artifact_evidence_status_id(input_payload, ARTIFACT_FIXTURE_OUTLINE_PATH)],
                additional_review_domains=("plot_structure",),
                authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments', 'separate proposal preparation'))),

                authoring_scope=lambda call: lead_fixture_authoring_scope(
                    call, activity='review', domains=('plot_structure', 'tone_execution'),
                    paths=('manuscript/part-02/new.md',), contributions=[
                        {'domain': domain, 'role': 'constraint_assessor'}
                        for domain in ('plot_structure', 'tone_execution')]),)
        , control_plan=selection_plan('artifact.changes'))
        proposed = await self.runtime.lead_turn(
            self.session_id,
            failed_id,
            ("Prepare a new scene candidate." + '\nSupplied requested candidate content:\n' + json.dumps(['New scene.\n'])
             + '\nYou may give bounded artifact review judgments and prepare separate proposals; preserve supplied facts. Revision and execution require separate exact confirmation.'),
        )
        self.assertIsNotNone(proposed.proposal, (
            proposed.kind, proposed.next_action, proposed.state, proposed.validation_details,
            proposed.artifact_proposal is not None,
        ))
        bad_calls = []
        def malformed_reviewer(call, _provider):
            from story_writing_agents.structured_output_validation import diagnose_json_text
            from tests.provider_wire_fixture import contract_id, response
            self.assertEqual(contract_id(call.payload), "specialist-assignment-response-v3")
            self.assertFalse(hasattr(call, "contract"))
            value = { "assessment": "supports", "assignment_id": "wrong_assignment", "input_ids": [], "message": "Malformed reviewer identity.", }
            encoded = response(json.dumps({"action": {"kind": "answer", "result": value, "used_handles": []}}), contract_id(call.payload))
            self.assertTrue(diagnose_json_text(encoded, call.schema))
            bad_calls.append(call)
            return encoded
        for assignment in proposed.proposal.assignments:
            response = (malformed_reviewer if assignment.specialist_id == PLOT_ARCHITECT
                else lead_specialist_outcome("supports"))
            self.backend.enqueue_for(assignment.specialist_id, response)
            if assignment.specialist_id == PLOT_ARCHITECT:
                self.backend.enqueue_for(assignment.specialist_id, response)
        review_call_start = len(self.backend.call_records)
        failed = await self.runtime.decide_lead_step(
            lead_confirmation_decision(proposed, CollaborationConfirmationDecision.CONFIRM) )
        self.assertIs(failed.turn.artifact_proposal.state, ArtifactProposalState.FAILED)
        self.assertIsNone(failed.turn.artifact_proposal.reviewed_proposal)
        self.assertEqual(
            self.artifact_events(failed_id, ArtifactReviewedProposal),
            (), )
        failures = self.artifact_events(failed_id, ArtifactReviewFailurePayload)
        self.assertEqual(failures[-1].code, ArtifactReviewFailureCode.REVIEW_INCOMPLETE)
        candidate_value = json.loads(
            serialize_artifact_review_candidate(
                proposed.artifact_proposal.candidate.candidate
            ) )
        specialist_calls = self.backend.call_records[review_call_start:]
        self.assertEqual(len(bad_calls), 2)
        self.assertCountEqual(
            tuple(agent_id for agent_id, _call in specialist_calls),
            tuple(item.specialist_id for item in proposed.proposal.assignments) + (PLOT_ARCHITECT,), )
        from tests.provider_wire_fixture import artifact_candidate as wire_candidate
        from tests.test_agent_context_accounting import _expanded_context_payload
        for assignment in proposed.proposal.assignments:
            with self.subTest(specialist=assignment.specialist_id):
                for call in (call for agent_id, call in specialist_calls
                             if agent_id == assignment.specialist_id):
                    material = (_expanded_context_payload(call.payload) if "shared_context_objects" in call.payload else call.payload)["task_input"]
                    if call is bad_calls[1]:
                        self.assertEqual(set(material), {"original_task_input", "response_correction"})
                        material = material["original_task_input"]
                    disclosed = next(item["artifact_candidate"] for item in material["inputs"]
                                     if item["input_source_type"] == "artifact_candidate")
                    self.assertEqual(disclosed, wire_candidate(candidate_value))
