"""Shared runtime fixture for specialist routing domain cases."""
from __future__ import annotations

import json
from story_writing_agents import conversations as c
from story_writing_agents.agents import (
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
)
from story_writing_agents.specialist_routing import AuthoringIntent, SpecialistAssignmentMode
from story_writing_agents.writing_artifacts.repository import WritingArtifactRepository
from tests.lead_fixtures.preparation_data import (
    fixture_assessment as _fixture_assessment,
    submitted_calls as _submitted_calls,
    synthesis_outcome as _synthesis_outcome,
    lead_confirmation_decision,
    lead_specialist_outcome,
    scoped_proposal,
)
from tests.lead_fixtures.runtime_setup import LeadOrchestrationTestCase
from tests.provider_wire_fixture import finite_fixture_selection, selection_plan


ADMISSION_BRIEF = ( "Nora must recover her brother's letter before a public oath. "
    "His disappearance motivates her search; this city has no magic. "
    "Draft three outline scenes preserving that motive and rule. "
    "Invent supporting dialogue and minor places; leave the ending unresolved." )

ROSTER = (PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST)

class SpecialistRoutingRuntimeCase(LeadOrchestrationTestCase):
    def _assert_recorded_provider_size_bounds(self, services, *, session_ids=None):
        from story_writing_agents.agent_context.sources.session_history import SessionHistorySourceAdapter
        from story_writing_agents.agent_context.accounting import ProviderCallAccounting, ProviderPayloadAccounting
        from story_writing_agents.conversations import session_context as session_context_module
        submitted = tuple(
            call for session_id in ((self.session_id,) if session_ids is None else session_ids)
            for call in _submitted_calls(SessionHistorySourceAdapter(services.catalog, services.event_store, session_id).capture().events()))
        self.assertTrue(submitted)
        self.assertEqual(len(submitted), len(self.backend.calls))
        self.assertCountEqual((call.call_id for call in submitted), (request.call_id for _agent, request in self.backend.call_records))
        recorded_sizes = []
        for call in submitted:
            self.assertIs(type(call.accounting), ProviderCallAccounting)
            payload = call.accounting.payload
            self.assertIs(type(payload), ProviderPayloadAccounting)
            sizes = (payload.schema_utf8_bytes, payload.payload_utf8_bytes, payload.serialized_input_utf8_bytes)
            self.assertTrue(all(type(size) is int for size in sizes), sizes)
            self.assertLessEqual(sizes[0], session_context_module.MAX_PROVIDER_SCHEMA_UTF8_BYTES, sizes)
            self.assertLessEqual(sizes[1], session_context_module.MAX_PROVIDER_PAYLOAD_UTF8_BYTES, sizes)
            self.assertLessEqual(sizes[2], session_context_module.MAX_PROVIDER_INPUT_UTF8_BYTES, sizes)
            recorded_sizes.append(sizes)
        print("Recorded provider UTF-8 maxima:", len(recorded_sizes), tuple(max(row[i] for row in recorded_sizes) for i in range(3)))

    def _assert_continuation(self, result, field):
        self.assertIsNone(getattr(result, field))
        self.assertIs(result.next_action, c.CollaborationNextAction.CONTINUE_WITH_LEAD)

    def _native_scoped(self, *agents, **values):
        mutate = values.pop('mutate', None)
        def respond(call, provider):
            choices = dict(call.contract.semantic_index.policy.choices)
            constraints = call.contract.factory.constraints
            closed_review = constraints.adoption_intent is AuthoringIntent.REVIEW_ONLY or (
                constraints.scope is not None
                and constraints.scope.intent is AuthoringIntent.REVIEW_ONLY)
            native = {'family': int(values.get('intent') == 'review_only' and not closed_review),
                      'input_choices': choices['input_ids'],
                      'source_choices': choices['input_ids'] + choices['historical_user_source_ids'], 'anchors': {}, 'recipients': {}}
            def prepare(value, material):
                if mutate is not None:
                    mutate(value, material)
                proposal = value['proposal']
                rows = proposal['assignments']
                native['anchors'].update((row['agent_id'], row['input_ids'][0]) for row in rows)
                required = tuple(source for source in choices['required_input_ids']
                                 if value['authoring_scope']['intent'] != 'review_only'
                                 or source != 'user_input')
                recipients = {source: next(row['agent_id'] for row in rows
                                           if source in row['input_ids']) for source in required}
                proposal['required_input_recipients'] = recipients
            writer = scoped_proposal(*agents, **values, mutate=prepare, native_inputs=native)
            frame = json.loads(writer(call, provider))
            details = frame['agent_action']['task_result']['response_details']
            for premise in details['specialist_consultation_proposal']['shared_premises']:
                premise['basis_input_ids'] = finite_fixture_selection(
                    choices['input_ids'], premise['basis_input_ids'])
            return json.dumps(frame, ensure_ascii=False)
        return respond

    async def proposal_for(self, conversation, response=None, text=ADMISSION_BRIEF, adoption=None, *, selection_group="consultation.authoring.content"):
        writer = self._native_scoped if text == ADMISSION_BRIEF else scoped_proposal
        self.backend.enqueue(response or writer(
            PLOT_ARCHITECT, authoring_assessment=_fixture_assessment() if text == ADMISSION_BRIEF else None,
            domains=("plot_structure",),), control_plan=selection_plan(selection_group))
        return await self.runtime.lead_turn(self.session_id, conversation, text, adoption=adoption)

    async def finish_draft(self, conversation, *, synthesis=None, **kwargs):
        proposed = await self.proposal_for(conversation, **kwargs)
        self.assertIs(proposed.kind, c.LeadOutcomeKind.PROPOSAL, proposed)
        for assignment in proposed.proposal.assignments:
            status = {SpecialistAssignmentMode.DRAFT: "drafted", SpecialistAssignmentMode.CONSTRAINT_ASSESSMENT: "constraints_assessed",
                      SpecialistAssignmentMode.REVIEW: "supports"}[assignment.mode]
            self.backend.enqueue_for(assignment.specialist_id, lead_specialist_outcome(status, "Summons leads choice; choice causes consequence."))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, synthesis or _synthesis_outcome(message="The draft material is available for integration; no artifact changed."))
        result = await self.runtime.decide_lead_step(lead_confirmation_decision(proposed, c.CollaborationConfirmationDecision.CONFIRM))
        if result.turn.kind is c.LeadOutcomeKind.RECOMMENDATION:
            history = self.history(conversation)
            c.project_collaboration_history(history)
            source = next(event.payload for event in reversed(history) if type(event.payload) is c.CollaborationProposal and event.step_id == proposed.proposal.step_id)
            terminal = next(event.payload for event in reversed(history) if type(event.payload) is c.CollaborationStepStatePayload and event.step_id == proposed.proposal.step_id and event.payload.proposal_digest == proposed.proposal.proposal_digest)
            self.assertEqual(source, proposed.proposal)
            self.assertIs(terminal.state, c.CollaborationStepState.COMPLETED, (result.turn.kind, result.turn.message, result.turn.validation_details, [(e.payload.assessment, e.payload.message[:500]) for e in history if type(e.payload) is c.CollaborationSpecialistReplyPayload], [(e.payload.synthesis_outcome, e.payload.synthesis_submitted_physical_calls, e.payload.overall_outcome) for e in history if type(e.payload) is c.CollaborationExecutionResult], [(e.payload.scope, e.payload.outcome, e.payload.submitted_physical_calls) for e in history if type(e.payload) is c.CollaborationTerminalOutcomePayload], getattr(self, "_synthesis_checks", None)))
        else:
            self.assertIs(result.turn.state, c.CollaborationStepState.COMPLETED)
        return proposed, result

    def artifact_bytes(self): return tuple( (item.path, item.content)
            for item in WritingArtifactRepository(self.workspace.root).capture_complete_state() )

    def _transition_receipt(self, decision):
        event = next(event for event in self.history(decision.conversation_id)
            if type(event.payload) is c.CollaborationLeadTaskTransitionPayload
            and event.payload.receipt.origin_event_id == decision.request_id)
        self.assertEqual(event.payload.receipt.origin_kind, "task_decision")
        self.assertEqual(event.payload.receipt.action, decision.action)
        self.assertEqual(event.payload.receipt.source_task_ref, decision.task_ref)
        return event

    def _assert_selected_profile(self, call, pair):
        self.assertEqual(call.contract.factory.rule_seed.contract_id, "lead-response-v9")
        self.assertFalse(call.contract.factory.schema_group_protocol)
        self.assertIsNone(call.contract.schema_group_phase)
        self.assertEqual(call.contract.semantic_index.policy.task_revisions, (pair,))
