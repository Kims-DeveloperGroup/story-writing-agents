"""Visible ordinary lead outcomes retain their exact next-action contracts."""

from __future__ import annotations

from dataclasses import asdict

from story_writing_agents.agents import (
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
    PLOT_ARCHITECT,
)
from story_writing_agents.conversations import (
    CollaborationEventKind,
    CollaborationNextAction,
    CollaborationStepState,
    LeadOutcomeKind,
    project_collaboration_history,
)
from tests.lead_fixtures.preparation_data import visible_outcome as _visible_outcome
from tests.lead_fixtures.runtime_setup import LeadOrchestrationTestCase
from tests.provider_wire_fixture import selection_plan


class OrdinaryLeadTurnOutcomeTests(LeadOrchestrationTestCase):
    async def test_all_five_outcomes_have_exact_visible_next_actions(self) -> None:
        from tests.lead_fixtures.specialist_routing import ADMISSION_BRIEF as _ADMISSION_BRIEF
        from tests.lead_fixtures.preparation_data import (
            fixture_assessment as _fixture_assessment,
        )
        assessment = asdict(_fixture_assessment())
        qualified_request = _ADMISSION_BRIEF + "\nPlease check story direction."
        from tests.recommendation_fixture import action
        cases = (('answer', CollaborationNextAction.CONTINUE_WITH_LEAD, 'answer.workflow'), ('question',
            CollaborationNextAction.REPLY_TO_LEAD, 'question.workflow'), ('critique', CollaborationNextAction.CONTINUE_WITH_LEAD, 'critique.workflow'),
            ('recommendation', CollaborationNextAction.SUBMIT_RECOMMENDATION_DECISION, 'prepare.consultation.authoring'))
        for kind, next_action, selection_group in cases:
            with self.subTest(kind=kind):
                conversation_id = self.conversation(f"Outcome {kind}")
                from tests.recommendation_fixture import recommendation
                self.backend.enqueue(recommendation(recommended_action=action(
                    kind="prepare_consultation", source_ids=("user_input",),
                    specialist_ids=( PLOT_ARCHITECT , CHARACTER_CUSTODIAN , EXECUTION_SPECIALIST , ),
                ), mutate=lambda value: value.update(authoring_assessment=assessment),)
                    if kind == "recommendation" else _visible_outcome(kind),
                    control_plan=selection_plan(selection_group))
                result = await self.runtime.session(self.session_id).lead_turn(conversation_id, (
                    _ADMISSION_BRIEF + '\n' if kind == 'recommendation' else '') +
                    f'Return a {kind}. Finished candidate: ' 'a summons causes a choice and its consequence.')
                self.assertIs(result.kind, LeadOutcomeKind(kind))
                self.assertIsNone(result.proposal)
                self.assertIsNone(result.state)
                self.assertIs(result.next_action, next_action)
                history = self.history(conversation_id)
                expected_tail = (
                    (CollaborationEventKind.LEAD_GROUP_TRACE, CollaborationEventKind.MESSAGE,
                     CollaborationEventKind.LEAD_RECOMMENDATION_REQUEST)
                    if kind == 'recommendation'
                    else (CollaborationEventKind.MESSAGE, CollaborationEventKind.LEAD_GROUP_TRACE)
                )
                self.assertEqual(tuple((event.kind for event in history)), (
                    CollaborationEventKind.MESSAGE,
                    CollaborationEventKind.PROVIDER_CALL_ACCOUNTING, CollaborationEventKind.PROVIDER_CALL_ACCOUNTING,
                    CollaborationEventKind.PROVIDER_CALL_ACCOUNTING, CollaborationEventKind.PROVIDER_CALL_ACCOUNTING,
                    CollaborationEventKind.LEAD_PUBLIC_RESPONSE,
                ) + expected_tail)
                self.assertEqual(project_collaboration_history(history).physical_calls_spent, 2,)

        proposal_id = self.conversation("Proposal")
        proposed = await self.proposal(proposal_id, text=qualified_request, authoring_assessment=assessment,)
        self.assertIs(proposed.kind, LeadOutcomeKind.PROPOSAL)
        self.assertIs(proposed.state, CollaborationStepState.AWAITING_CONFIRMATION)
        self.assertIs(proposed.next_action, CollaborationNextAction.SUBMIT_CONFIRMATION,)
