from __future__ import annotations
import json
from dataclasses import asdict
from story_writing_agents import agents, conversations, models
from tests import test_lead_orchestration as lead
from tests.recommendation_fixture import task_assessment


_REVIEW_DIRECTION = "Check the story direction against supplied facts/material within each assigned domain."

_REVIEW_LATITUDE = "Make bounded review judgments only; preserve supplied facts and wait for separate execution confirmation."

_REVIEW_BRIEF = ("Story facts for this constructed review: a courier must recover her brother's letter before the last ferry; "
    "her brother hid it to protect a witness. Keep these facts and all supplied material fixed. " + _REVIEW_DIRECTION + "\n" + _REVIEW_LATITUDE)

def _review_assessment():
    return task_assessment(foundation_ids=("user_input",), actions=("bounded review judgments",))

class _FailingEventStore:
    """Delegating test port that fails one selected durable append family."""

    def __init__(self, delegate, *, state=None, event_kind=None) -> None:
        self.delegate = delegate
        self.state = state
        self.event_kind = event_kind
        self.enabled = False
        self.failures = 0

    def append_batch(self, batch):
        matches_state = self.state is not None and any((getattr(event.payload, 'state',
            None) is self.state for event in batch.events))
        matches_kind = self.event_kind is not None and any((event.kind is self.event_kind for event in batch.events))
        if self.enabled and (matches_state or matches_kind):
            self.failures += 1
            raise RuntimeError("injected durable append failure")
        return self.delegate.append_batch(batch)

    def read(self, session_id, conversation_id):
        return self.delegate.read(session_id, conversation_id)

    def read_context(self, session_id, conversation_id):
        return self.delegate.read_context(session_id, conversation_id)

class SpecialistAccountingAcceptanceTests(lead.LeadOrchestrationTestCase):
    def setUp(self) -> None:
        self._execution_inputs = lead._capture_execution_inputs(super().setUp)

    async def test_local_reference_and_search_answer_count_only_provider_calls(self) -> None:
        conversation_id = self.conversation("Specialist accounting")
        self.workspace.append_agent_state(models.AgentStateEntry(agents.PLOT_ARCHITECT, self.session_id, 'debate',
            'audit', 1, 'r1', 'outcome', 'own plot state needle', '2026-09-01T00:00:00+00:00'))
        self.workspace.append_agent_state(models.AgentStateEntry(agents.CHARACTER_CUSTODIAN, self.session_id,
            'debate', 'audit', 1, 'r1', 'outcome', 'sibling reply secret', '2026-09-01T00:00:01+00:00'))
        proposed = await self.proposal( conversation_id,
            text=_REVIEW_BRIEF + "\n\n" + "Check ${core_documents/global_outline.md}#L1-L2.",
            assignments=[ {
                    "agent_id": agents.PLOT_ARCHITECT,
                    "instruction": "Search only the bounded assignment context.",
                    "input_ids": ["user_input", "reference_1"],}
            ], authoring_assessment=asdict(_review_assessment()))

        assignment = proposed.proposal.assignments[0]

        def answer_after_read(call, provider):
            self.assertNotIn("sibling reply secret", json.dumps(dict(call.payload)))
            return lead._provider_envelope(call, {'assessment': 'supports',
                'assignment_id': assignment.assignment_id, 'input_ids': list(assignment.input_ids),
                'message': 'The bounded evidence supports the direction.'}, used_handles=provider.used_handles)

        def search_current_proposal(call, _provider):
            return json.dumps({'agent_action': {'action_type': 'search_context', 'queries': [
                {'search_text': 'own plot state needle', 'source_types': ['agent_work_history']},
                {'search_text': 'sibling reply secret', 'source_types': ['agent_work_history']},
            ]}}, separators=(',', ':'))

        def read_current_proposal(call, provider):
            observation = provider.callback_payload(call)["context_observation"]
            assert (isinstance(observation, dict) and
                    observation["observation_type"] == "context_search_results")
            handle = observation["search_results"][0]["read_handle"]
            provider.used_handles = (handle,)
            return json.dumps({'agent_action': {'action_type': 'read_context',
                'read_handles': [handle]}}, separators=(',', ':'))

        self.backend.enqueue_for(agents.PLOT_ARCHITECT, search_current_proposal,
                                 read_current_proposal, answer_after_read)
        self.backend.enqueue_for(agents.LEAD_ORCHESTRATOR, lead._synthesis_outcome())
        decision = await self.runtime.decide_lead_step(
            lead._decision(proposed, conversations.CollaborationConfirmationDecision.CONFIRM)
        )

        self.assertIs(decision.turn.state, conversations.CollaborationStepState.COMPLETED)
        history = self.history(conversation_id)
        specialist_calls = tuple(
            payload
            for payload in lead._event_payloads(history, conversations.CollaborationProviderCallPayload)
            if payload.invocation_kind
            is conversations.CollaborationInvocationKind.SPECIALIST_ASSIGNMENT )
        self.assertEqual(tuple(((item.call_kind, item.state) for item in specialist_calls)), ((
            conversations.CollaborationProviderCallKind.INITIAL, conversations.CollaborationProviderCallState.PREPARED), (
            conversations.CollaborationProviderCallKind.INITIAL, conversations.CollaborationProviderCallState.SUBMITTED), (
            conversations.CollaborationProviderCallKind.CONTINUATION, conversations.CollaborationProviderCallState.PREPARED), (
            conversations.CollaborationProviderCallKind.CONTINUATION, conversations.CollaborationProviderCallState.SUBMITTED), (
            conversations.CollaborationProviderCallKind.CONTINUATION, conversations.CollaborationProviderCallState.PREPARED), (
            conversations.CollaborationProviderCallKind.CONTINUATION, conversations.CollaborationProviderCallState.SUBMITTED)))
        result = lead._event_payloads(history, conversations.CollaborationExecutionResult)[0]
        self.assertEqual(result.assignment_outcomes[0].submitted_physical_calls, 3)
        self.assertEqual(result.synthesis_submitted_physical_calls, 1)
        projection = conversations.project_collaboration_history(history)
        self.assertEqual(projection.physical_calls_spent, 6)
        self.assertLessEqual(projection.physical_calls_spent, 12)
