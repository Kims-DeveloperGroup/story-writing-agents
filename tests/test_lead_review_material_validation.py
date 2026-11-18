"""Existing runtime acceptance cases for LeadReviewMaterialValidationTests."""
from __future__ import annotations

from tests.lead_fixtures.specialist_routing import SpecialistRoutingRuntimeCase
from tests.lead_fixtures.specialist_routing import ADMISSION_BRIEF as _ADMISSION_BRIEF
from dataclasses import replace
from story_writing_agents import StoryRuntime, conversations as c, lead_exploration as exploration
from story_writing_agents.agents import LEAD_ORCHESTRATOR, PLOT_ARCHITECT
from story_writing_agents.specialist_routing import AuthoringIntent
from tests.lead_fixtures.preparation_data import (
    OUTLINE,
    adoption_selection as _adoption_selection,
    current_exploration_outcome as _current_exploration_outcome,
    fixture_assessment as _fixture_assessment,
    synthesis_outcome as _synthesis_outcome,
    lead_confirmation_decision,
    lead_specialist_outcome,
    scoped_proposal,
)
from tests.lead_fixtures.provider_responses import workflow_material as _workflow_material
from tests.provider_wire_fixture import selection_plan


class LeadReviewMaterialValidationTests(SpecialistRoutingRuntimeCase):
    async def test_review_rejects_empty_reference_but_accepts_explicit_user_candidate(self):
        self.workspace.path(OUTLINE).write_bytes(b"")
        bad_conversation = self.conversation("Rejected empty review source")
        bad = await self._rejected_pair(bad_conversation,
            "Assess the concrete candidate ${core_documents/global_outline.md}.",
            scoped_proposal(PLOT_ARCHITECT, activity="review", candidate_ids=("reference_1",)),
            selection_group='consultation.authoring.review')
        self.assertIsNone(bad.proposal)
        conversation = bad_conversation
        original, before = "완성 원고.\r\nCafé.  \r\n", self.artifact_bytes()
        good = await self.proposal_for(conversation, self._native_scoped(
                PLOT_ARCHITECT, activity='review', intent='review_only',
                domains=('plot_structure',)), text=original, selection_group='consultation.review_only')
        self.assertIs(good.kind, c.LeadOutcomeKind.PROPOSAL)
        self.assertEqual(good.proposal.assignments[0].review_basis.candidate_input_ids, ("user_input",))
        self.assertIs(good.proposal.authoring_scope.intent, AuthoringIntent.REVIEW_ONLY)
        self.backend.enqueue_for(PLOT_ARCHITECT, lead_specialist_outcome())
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome(message="Suggested edits remain findings until a fresh authoring request."))
        reviewed = await self.runtime.decide_lead_step(lead_confirmation_decision(good, c.CollaborationConfirmationDecision.CONFIRM))
        self.assertEqual([item["text"].encode() for agent, call in self.backend.call_records if agent == PLOT_ARCHITECT for item in _workflow_material(call.payload)["inputs"] if item["input_id"] == "user_input"], [original.encode()])
        self.assertEqual(self.artifact_bytes(), before)
        self.assertTrue(all(request is None for request in (reviewed.turn.artifact_application_request, reviewed.turn.story_commit_request)))
        self.assertFalse(any(type(e.payload) in (c.ArtifactObjectivePayload, c.ArtifactObjectivePayloadV2) for e in self.history(conversation)))

        self.backend.enqueue(self._native_scoped(PLOT_ARCHITECT, paths=(OUTLINE,), domains=('plot_structure',), authoring_assessment=_fixture_assessment()), control_plan=selection_plan('consultation.authoring.content'))
        drafted = await self.runtime.lead_turn(self.session_id, conversation, _ADMISSION_BRIEF)
        self.assertIsNotNone(drafted.proposal, (drafted.message, drafted.validation_details, drafted.public_responses))
        self.assertEqual((drafted.proposal.authoring_scope.intent, drafted.proposal.authoring_scope.task_revision, self.artifact_bytes()), (AuthoringIntent.AUTHORING, 1, before))
        self.assertNotEqual(drafted.proposal.authoring_scope.task_id, good.proposal.authoring_scope.task_id)
        source, owner = self.conversation("Review source A"), self.conversation("Selected review owner B")
        supplied = "Unused prefix.\n" + original + "\nUnused suffix."
        self.backend.enqueue(_current_exploration_outcome(supplied))
        await self.runtime.lead_turn(self.session_id, source, "Explore supplied review alternatives.",
                                     interaction_mode=exploration.LeadInteractionMode.EXPLORATION)
        event = next(e for e in self.history(source) if type(e.payload) is c.CollaborationLeadExplorationResultPayload)
        start = len("Unused prefix.\n")
        selection = replace(_adoption_selection(source, event, (OUTLINE,)), start_character=start, end_character=start + len(original), intent=AuthoringIntent.REVIEW_ONLY)
        selected = await self.proposal_for(owner, self._native_scoped(
            PLOT_ARCHITECT, domains=("plot_structure",), activity="review",
            intent="review_only", candidate_ids=("adopted_idea",),
            mutate=lambda value, _material: value["proposal"]["assignments"][0].update(
                input_ids=["adopted_idea"])), adoption=selection, selection_group='consultation.review_only')
        self.assertIsNotNone(selected.proposal, selected)
        self.assertEqual(selected.proposal.assignments[0].review_basis.candidate_input_ids, ("adopted_idea",))
        self.backend.enqueue_for(PLOT_ARCHITECT, lead_specialist_outcome())
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome(message="Selected supplied review findings."))
        result = await self.runtime.decide_lead_step(lead_confirmation_decision(selected, c.CollaborationConfirmationDecision.CONFIRM))
        delivered = [item["conversation_event"]["capture"]["selected_text"]
                     for agent, call in self.backend.call_records
                     if agent == PLOT_ARCHITECT for item in _workflow_material(call.payload)["inputs"]
                     if item["input_id"] == "adopted_idea"]
        self.assertEqual(delivered, [original])
        self.assertEqual((result.turn.message, result.turn.artifact_application_request,
                          result.turn.story_commit_request), ("Selected supplied review findings.", None, None))
        retained = self.history(owner)
        projection = c.project_collaboration_history(retained)
        self.assertIsNotNone(projection.execution_result)
        restarted = StoryRuntime(self.control_workspace, backend=self.backend)
        self.assertEqual(restarted.conversation_storage_services.event_store.read(self.session_id, owner), retained)
        self.assertEqual(restarted.inspect_lead_collaboration(self.session_id, owner).execution_result,
                         projection.execution_result)
        self.assertEqual(self.artifact_bytes(), before)
        self.assertFalse(restarted.inspect_lead_collaboration(self.session_id, owner).artifact_objectives)
