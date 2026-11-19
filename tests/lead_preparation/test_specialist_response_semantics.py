"""Existing specialist response and synthesis semantic acceptance definitions."""
from __future__ import annotations

from dataclasses import asdict
from story_writing_agents import (
    ReplyTraceOrigin,
    agents,
    conversations,
    lead_provider_contracts,
    structured_output_validation,
)
from story_writing_agents.errors import BackendProtocolError
from tests import test_specialist_orchestration_accounting as accounting
from tests.lead_fixtures import preparation_data
from tests.lead_fixtures.preparation_data import events_of_kind as _events
from tests.lead_fixtures.provider_responses import (
    specialist_assignments as _assignments,
    specialist_raw_outcome,
)
import json
from story_writing_agents.lead_preparation import collaboration_response_decoding


class SpecialistResponseSemanticsDefinitions:
    async def test_invalid_specialist_shapes_repair_then_fall_back_without_halting(self) -> None:
        invalid_values = (
            ({"assessment": "supports"}, "$.agent_action.task_result.response_text: required property is missing"),
            ({"message": "Missing assessment."}, "$.agent_action.task_result.assignment_status: required property is missing"),
            ({"assessment": 0, "message": "Private state claim.", "private_state": "forbidden"},
             "$.agent_action.task_result.assignment_status: expected string, got integer"), )
        for ordinal, (value, issue) in enumerate(invalid_values, start=1):
            with self.subTest(ordinal=ordinal):
                conversation_id = self.conversation(f"Strict specialist {ordinal}")
                proposed = await self.proposal(conversation_id, text=accounting._REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(accounting._review_assessment()))
                response = specialist_raw_outcome(value)
                self.backend.enqueue_for(agents.PLOT_ARCHITECT, response)
                self.backend.enqueue_for(agents.PLOT_ARCHITECT, response)
                self.backend.enqueue_for(agents.LEAD_ORCHESTRATOR, preparation_data.synthesis_outcome())
                decision = await self.runtime.decide_lead_step(preparation_data.lead_confirmation_decision(proposed,
                    conversations.CollaborationConfirmationDecision.CONFIRM))
                self.assertIs(decision.turn.state, conversations.CollaborationStepState.COMPLETED)
                result = preparation_data.event_payloads(self.history(conversation_id), conversations.CollaborationExecutionResult)[0]
                self.assertIs(result.assignment_outcomes[0].outcome, conversations.CollaborationAssignmentOutcomeKind.SUCCEEDED)
                self.assertIs(result.synthesis_outcome, conversations.CollaborationSynthesisOutcomeKind.SUCCEEDED)
                reply = preparation_data.event_payloads(self.history(conversation_id), conversations.CollaborationSpecialistReplyPayload)[0]
                self.assertIs(reply.assessment, conversations.CollaborationSpecialistAssessment.INSUFFICIENT_EVIDENCE)
                self.assertIs(reply.trace.origin, ReplyTraceOrigin.RUNTIME_FALLBACK)
                self.assertEqual(reply.trace.action, "none")
                reports = tuple(
                    chunk for payload in preparation_data.submitted_calls(self.history(conversation_id)) if payload.state is conversations.CollaborationProviderCallState.SUBMITTED
                    for chunk in payload.validation_details )
                self.assertTrue(all(issue in chunk for chunk in reports))
                self.assertTrue(any(chunk.startswith("Original response") for chunk in reports))
                self.assertTrue(any(chunk.startswith("Repair response") for chunk in reports))
                self.assertNotIn("private_state", repr(self.history(conversation_id)))

    async def test_specialist_and_synthesis_validation_details_are_private_and_correlated(
        self,
    ) -> None:
        conversation_id = self.conversation("Rejected synthesis details")
        proposed = await self.proposal(conversation_id, text=accounting._REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(accounting._review_assessment()))
        self.backend.enqueue_for(agents.PLOT_ARCHITECT, preparation_data.lead_specialist_outcome())
        rejected = specialist_raw_outcome({'message': 0, 'PRIVATE_SYNTHESIS_KEY': 'PRIVATE_SYNTHESIS_VALUE'})
        self.backend.enqueue_for(agents.LEAD_ORCHESTRATOR, rejected)
        self.backend.enqueue_for(agents.LEAD_ORCHESTRATOR, preparation_data.synthesis_outcome())
        decision = await self.runtime.decide_lead_step(
            preparation_data.lead_confirmation_decision(proposed, conversations.CollaborationConfirmationDecision.CONFIRM)
        )
        history = self.history(conversation_id)
        submitted = preparation_data.submitted_calls(history)
        synthesis = tuple(payload for payload in submitted
                          if payload.role == agents.LEAD_ORCHESTRATOR and payload.validation_details)
        self.assertEqual(len(synthesis), 1)
        self.assertTrue(synthesis[0].validation_details[0].startswith("Original response"))
        self.assertIn('$.agent_action.task_result.response_text: expected string, got integer',
                      repr(synthesis[0].validation_details))
        self.assertIs(decision.turn.state, conversations.CollaborationStepState.COMPLETED)
        self.assertEqual(decision.turn.validation_details, tuple((
            chunk for payload in submitted for chunk in payload.validation_details)))
        self.assertNotIn("PRIVATE_SYNTHESIS_KEY", repr(history))
        self.assertNotIn("PRIVATE_SYNTHESIS_VALUE", repr(history))

    async def test_synthesis_contract_is_semantic_and_runtime_derives_attribution(self) -> None:
        synthesis_review_candidates = []
        def minimal_synthesis(call, _provider):
            synthesis_review_candidates.append(
                dict(call.contract.semantic_index.policy.choices)['review_candidate_ids'])
            self.assertNotIn("reply_event_ids", repr(call.schema))
            self.assertNotIn("unresolved_input_ids", repr(call.schema))
            for message in ("Visible.", " \t\0"):
                self.assertEqual(lead_provider_contracts.LeadProviderConstraints(continuation=True).diagnose(json.dumps({"message": message})), ())
            return preparation_data.provider_envelope(call, {"message": "The concerns remain visible."})
        conversation_id = self.conversation("Strict synthesis")
        proposed = (
            await self.proposal(conversation_id, assignments=_assignments(agents.PLOT_ARCHITECT,
            agents.CHARACTER_CUSTODIAN), text=accounting._REVIEW_BRIEF + '\n\nPlease check the story direction.',
            authoring_assessment=asdict(accounting._review_assessment()), domains=('plot_structure',
            'character_world'))
        )
        self.backend.enqueue_for(agents.PLOT_ARCHITECT, preparation_data.lead_specialist_outcome("concern"))
        self.backend.enqueue_for(agents.CHARACTER_CUSTODIAN, preparation_data.lead_specialist_outcome("insufficient_evidence"))
        self.backend.enqueue_for(agents.LEAD_ORCHESTRATOR, minimal_synthesis)
        decision = await self.runtime.decide_lead_step(preparation_data.lead_confirmation_decision(proposed, conversations.CollaborationConfirmationDecision.CONFIRM))
        self.assertIs(decision.turn.state, conversations.CollaborationStepState.COMPLETED)
        history = self.history(conversation_id)
        reply_events = _events(history, conversations.CollaborationEventKind.SPECIALIST_REPLY)
        self.assertEqual(len(reply_events), 2)
        synthesis = preparation_data.event_payloads(history, conversations.CollaborationLeadSynthesisPayload)[0]
        synthesis_call = next(event.payload for event in history if type(event.payload) is conversations.CollaborationProviderCallPayload
            and event.payload.invocation_kind is conversations.CollaborationInvocationKind.LEAD_SYNTHESIS
            and event.payload.state is conversations.CollaborationProviderCallState.SUBMITTED)
        self.assertEqual(synthesis.provider_invocation_id, synthesis_call.invocation_id)
        self.assertEqual(decision.turn.provider_invocation_id, synthesis_call.invocation_id)
        self.assertIs(synthesis.trace.origin, ReplyTraceOrigin.PROVIDER)
        self.assertEqual(synthesis.trace.action, "synthesis")
        self.assertEqual(synthesis.reply_event_ids, tuple(event.event_id for event in reply_events))
        self.assertEqual(synthesis.unresolved_input_ids, ("user_input",))
        result = preparation_data.event_payloads(history, conversations.CollaborationExecutionResult)[0]
        self.assertIs(result.synthesis_outcome, conversations.CollaborationSynthesisOutcomeKind.SUCCEEDED)
        self.assertEqual(result.synthesis_submitted_physical_calls, 1)
        duplicate = {**_assignments(agents.PLOT_ARCHITECT)[0], "mode": "review"}
        with self.assertRaises(BackendProtocolError) as caught:
            collaboration_response_decoding.decode_lead_synthesis(json.dumps({"message": "Visible.", "next_intent": {
                "purpose": "Continue the review.", "assignments": [duplicate, duplicate]}}),
                proposal=proposed.proposal, attempt_id=synthesis.execution_attempt_id,
                reply_event_ids=synthesis.reply_event_ids,
                review_candidate_ids=synthesis_review_candidates[0],
                replies=tuple(event.payload for event in reply_events))
        self.assertEqual(structured_output_validation.failure_issues(caught.exception, path="$.action.result"),
                         ("$.action.result.next_intent.assignments: agent_id values must be unique before canonical ordering",))

    async def test_synthesis_derives_non_supporting_inputs_locally(self) -> None:
        conversation_id = self.conversation("Unresolved concern")
        proposed = await self.proposal(conversation_id, text=accounting._REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(accounting._review_assessment()))
        self.backend.enqueue_for(agents.PLOT_ARCHITECT, preparation_data.lead_specialist_outcome("concern"))
        self.backend.enqueue_for(agents.LEAD_ORCHESTRATOR, preparation_data.synthesis_outcome(unresolved_input_ids=[]))

        decision = await self.runtime.decide_lead_step(
            preparation_data.lead_confirmation_decision(proposed, conversations.CollaborationConfirmationDecision.CONFIRM)
        )

        self.assertIs(decision.turn.state, conversations.CollaborationStepState.COMPLETED)
        synthesis = preparation_data.event_payloads(self.history(conversation_id), conversations.CollaborationLeadSynthesisPayload)[0]
        self.assertEqual(synthesis.unresolved_input_ids, ("user_input",))
        result = preparation_data.event_payloads(self.history(conversation_id), conversations.CollaborationExecutionResult)[0]
        self.assertIs(result.synthesis_outcome, conversations.CollaborationSynthesisOutcomeKind.SUCCEEDED)
