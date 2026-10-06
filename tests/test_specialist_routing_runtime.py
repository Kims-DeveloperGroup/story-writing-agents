"""Runtime routing accepts typed task facts and keeps draft/review authority separate."""
from tests.test_collaboration_events import editorial, editorial_request
import asyncio
import json
import subprocess
from dataclasses import asdict, replace
from unittest import mock

from story_writing_agents import StoryRuntime, lead_exploration as exploration
from story_writing_agents import conversations as c
from story_writing_agents.agents import PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST, LEAD_ORCHESTRATOR
from story_writing_agents.specialist_routing import AuthoringActivity, AuthoringIntent, AuthoringScope, ArtifactReviewDomain, SpecialistAssignmentMode, ReviewBasis
from story_writing_agents.writing_artifacts.repository import WritingArtifactRepository
from story_writing_agents.interactive_terminal import _collaboration_view as view
from tests.test_lead_orchestration import _SubmittedFailure
from tests.test_lead_orchestration import (
    LeadOrchestrationTestCase, _LeadBackend, _submitted_calls, _compact_scope, _decision, _provider_envelope,
    _workflow_material, _live_scope, _specialist_outcome, _synthesis_outcome, _visible_outcome, _exploration_outcome, _adoption_selection,
)
from tests.recommendation_fixture import action, recommendation, decision as recommendation_decision
from story_writing_agents.conversations.events import shared_premises_to_dict
from tests.test_artifact_proposal_orchestration import _artifact_outcome, _artifact_synthesis_outcome, _status_id

OUTLINE = "core_documents/global_outline.md"
CHARACTER = "core_documents/character_sheets.md"
ROSTER = (PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST)


def scoped_proposal(*agents, activity="draft", paths=(OUTLINE,), domains=(), selected=None, revision=None,
                    candidate_ids=None, kind="proposal", intent=None, mutate=None):
    def respond(call, provider):
        material = _workflow_material(call.payload)
        tasks = material["authoring_tasks"]
        index = next((i for i, t in enumerate(tasks) if t["task_id"] == selected and (revision is None or t["task_revision"] == revision)), len(tasks)-1)
        assert revision is None or (tasks[index]["task_id"], tasks[index]["task_revision"]) == (selected, revision)
        scope = _live_scope(call, activity=activity, paths=paths, domains=domains, task_index=index)
        scope["interpretation"] = "Draft from the current synthetic brief." if activity == "draft" else "Review the supplied concrete synthetic candidate."
        scope["intent"] = "authoring" if intent is None else intent
        mode = "review" if activity == "review" else "draft"
        ids = ["user_input"] if candidate_ids is None else list(candidate_ids)
        value = {"authoring_scope": scope, "kind": kind, "message": "The scoped work requires its disclosed specialists.",
                 "scope": "story_specific", "evidence_status": "incomplete",
                 "evidence_ids": [], "artifact_intent": None,
                 "proposal": {"relation": "new_step", "shared_premises": [],
                              "purpose": "Produce the scoped synthetic material.", "authoring_scope": scope,
                              "assignments": [{"agent_id": agent, "instruction": "Produce the bounded material." if mode == "draft" else "Assess the supplied candidate.",
                                               "input_ids": list(dict.fromkeys(["user_input", *ids])), "mode": mode,
                                               "review_basis": None if mode == "draft" else {"schema_version":1,"candidate_input_ids":ids,"criteria":"Assess chronology and motivation."}}
                                              for agent in agents]} if kind == "proposal" else None}
        statuses = call.payload.get("core_document_statuses", [])
        status = next((s for s in statuses if s["artifact_path"] in paths), statuses[0] if statuses else None)
        if status is not None: value["evidence_ids"] = [status["evidence_id"]]
        if mutate: mutate(value, material)
        return _provider_envelope(call, value)
    return respond


class SpecialistRoutingRuntimeTests(LeadOrchestrationTestCase):
    def _assert_continuation(self, result, field):
        self.assertIsNone(getattr(result, field))
        self.assertIs(result.next_action, c.CollaborationNextAction.CONTINUE_WITH_LEAD)
    async def proposal_for(self, conversation, response=None, text="The synthetic brief begins with a summons, then a choice, then its consequence.", adoption=None):
        self.backend.enqueue(response or scoped_proposal(PLOT_ARCHITECT))
        return await self.runtime.lead_turn(self.session_id, conversation, text, adoption=adoption)

    async def finish_draft(self, conversation, *, synthesis=None, **kwargs):
        proposed = await self.proposal_for(conversation, **kwargs)
        self.assertIs(proposed.kind, c.LeadOutcomeKind.PROPOSAL, proposed)
        for assignment in proposed.proposal.assignments:
            status = {SpecialistAssignmentMode.DRAFT: "drafted", SpecialistAssignmentMode.CONSTRAINT_ASSESSMENT: "constraints_assessed"}[assignment.mode]
            self.backend.enqueue_for(assignment.specialist_id, _specialist_outcome(status, "Summons leads choice; choice causes consequence."))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, synthesis or _synthesis_outcome(message="The draft material is available for integration; no artifact changed."))
        result = await self.runtime.decide_lead_step(_decision(proposed, c.CollaborationConfirmationDecision.CONFIRM))
        if result.turn.kind is c.LeadOutcomeKind.RECOMMENDATION:
            history = self.history(conversation)
            c.project_collaboration_history(history)
            source = next(event.payload for event in reversed(history) if type(event.payload) is c.CollaborationProposal and event.step_id == proposed.proposal.step_id)
            terminal = next(event.payload for event in reversed(history) if type(event.payload) is c.CollaborationStepStatePayload and event.step_id == proposed.proposal.step_id and event.payload.proposal_digest == proposed.proposal.proposal_digest)
            self.assertEqual(source, proposed.proposal)
            self.assertIs(terminal.state, c.CollaborationStepState.COMPLETED)
        else:
            self.assertIs(result.turn.state, c.CollaborationStepState.COMPLETED)
        return proposed, result

    async def test_empty_target_adequate_brief_produces_draft_without_writes(self):
        self.workspace.path(OUTLINE).write_bytes(b"")
        conversation = self.conversation()
        before = self.artifact_bytes()
        def recommend(call, provider):
            material = _workflow_material(call.payload)
            draft_reply = next(reply["reply_event_id"] for reply in material["replies"] if reply["mode"] == "draft")
            return _synthesis_outcome(message=material["authoring_scope"]["interpretation"],
                recommended_action=action(source_ids=(draft_reply,), target_paths=(OUTLINE,)))(call, provider)
        proposed, result = await self.finish_draft(conversation, synthesis=recommend)
        self.assertEqual(proposed.proposal.participants, (PLOT_ARCHITECT,))
        replies = [e.payload for e in self.history(conversation) if type(e.payload) is c.CollaborationSpecialistReplyPayload]
        self.assertEqual(len(replies), 1)
        self.assertIs(replies[0].assessment, c.CollaborationSpecialistAssessment.DRAFTED)
        self.assertIs(replies[0].mode, SpecialistAssignmentMode.DRAFT)
        self.assertEqual(self.artifact_bytes(), before)
        self.assertIsNone(result.turn.artifact_application_request)
        synthesis = next(e.payload for e in self.history(conversation) if type(e.payload) is c.CollaborationLeadSynthesisPayload)
        self.assertEqual(synthesis.unresolved_input_ids, ())
        from tests.test_lead_artifact_application import _application_decision
        response = _artifact_outcome(
            operations=lambda call, material: [{"kind": "replace", "evidence_id": _status_id(call, OUTLINE), "new_text": "Synthetic reviewed outline.\n"}],
            evidence_status="empty", evidence_ids=lambda call, material: [_status_id(call, OUTLINE)],
            objective_intent={"action": "start", "target_paths": [OUTLINE], "next_step": "prepare"})
        def prepare(call, provider):
            value = json.loads(response(call, provider))
            value["action"]["result"]["authoring_scope"] = _compact_scope(c.authoring_scope_to_dict(proposed.proposal.authoring_scope))
            return json.dumps(value)
        self.backend.enqueue(prepare)
        review = await self.runtime.decide_lead_recommendation(recommendation_decision(result.turn))
        self.assertEqual(review.proposal.participants, (PLOT_ARCHITECT,))
        self.assertIs(review.proposal.assignments[0].mode, SpecialistAssignmentMode.REVIEW)
        self.assertEqual(review.proposal.budget.rounds_spent, 1)
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome("supports"))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _artifact_synthesis_outcome())
        reviewed = await self.runtime.decide_lead_step(_decision(review, c.CollaborationConfirmationDecision.CONFIRM))
        await self.runtime.decide_artifact_application(_application_decision(reviewed.turn, c.ArtifactApplicationDecisionKind.REJECT))
        prefix = self.history(conversation)
        completed = c.project_collaboration_history(prefix)
        self.assertIs(completed.current_artifact_application_result.outcome, c.ArtifactApplicationOutcomeKind.REJECTED)
        self.assertEqual(completed.rounds_spent, 2)
        memory = await self.runtime.record_editorial_decision(self.session_id, conversation, editorial_request("completed_task_memory"))
        self.assertIs(memory.status, editorial.EditorialRecordStatus.RECORDED)
        unchanged = c.project_collaboration_history(self.history(conversation))
        self.assertEqual((unchanged.rounds_spent, unchanged.physical_calls_spent), (completed.rounds_spent, completed.physical_calls_spent))
        self.assertGreaterEqual(12 - completed.physical_calls_spent, 4)
        self.assertEqual(self.artifact_bytes(), before)
        def successor(value, material):
            self.assertEqual([(t["task_id"], t["task_revision"]) for t in material["authoring_tasks"]], [(proposed.proposal.authoring_scope.task_id, 2)])
            self.assertEqual(material["available_proposal_drafting_source_ids"], [])
        blocked = await self.proposal_for(conversation, scoped_proposal(PLOT_ARCHITECT, selected=proposed.proposal.authoring_scope.task_id,
            revision=2, activity="review", mutate=successor))
        self.assertIsNone(blocked.proposal, blocked)
        self.assertFalse(any(e.kind is c.CollaborationEventKind.ASSIGNMENT_DISPATCH for e in self.history(conversation)[len(prefix):]))
        self.assertEqual(self.history(conversation)[:len(prefix)], prefix)

    async def test_empty_inventory_does_not_add_unaffected_owners_and_mixed_scope_does(self):
        for path in (OUTLINE, CHARACTER, "core_documents/narrative_rules.md", "core_documents/part_outlines.md"):
            self.workspace.path(path).write_bytes(b"")
        single = await self.proposal_for(self.conversation())
        self.assertEqual(single.proposal.participants, (PLOT_ARCHITECT,))
        mixed = await self.proposal_for(self.conversation(), scoped_proposal(*ROSTER, paths=("manuscript/part-01/opening.md",), domains=("plot_structure", "character_world")))
        self.assertEqual(mixed.proposal.participants, ROSTER)
        self.assertEqual(mixed.proposal.authoring_scope.domains, tuple(ArtifactReviewDomain))
        self.assertTrue(all(agent == LEAD_ORCHESTRATOR for agent, _ in self.backend.call_records))

    async def test_declared_substantive_direct_outcomes_fail_closed(self):
        for kind in ("answer", "recommendation", "critique"):
            with self.subTest(kind=kind):
                conversation = self.conversation()
                result = await self.proposal_for(conversation, scoped_proposal(PLOT_ARCHITECT, kind=kind))
                self._assert_continuation(result, 'proposal')
                self.assertFalse(any(e.kind is c.CollaborationEventKind.ASSIGNMENT_DISPATCH for e in self.history(conversation)))
        conversation = self.conversation()
        self.backend.enqueue(_visible_outcome("question", "Which existing task should this status request concern?"))
        question = await self.runtime.lead_turn(self.session_id, conversation, "Clarify which task is active.")
        self.assertIs(question.kind, c.LeadOutcomeKind.QUESTION)

    async def test_review_rejects_empty_reference_but_accepts_explicit_user_candidate(self):
        self.workspace.path(OUTLINE).write_bytes(b"")
        bad = await self.proposal_for(self.conversation(), scoped_proposal(PLOT_ARCHITECT, activity="review", candidate_ids=("reference_1",)), text="Assess the concrete candidate ${core_documents/global_outline.md}.")
        self.assertIsNone(bad.proposal)
        conversation = self.conversation()
        original, before = "완성 원고.\r\nCafé.  \r\n", self.artifact_bytes()
        good = await self.proposal_for(conversation, scoped_proposal(PLOT_ARCHITECT, activity="review", intent="review_only"), text=original)
        self.assertIs(good.kind, c.LeadOutcomeKind.PROPOSAL)
        self.assertEqual(good.proposal.assignments[0].review_basis.candidate_input_ids, ("user_input",))
        self.assertIs(good.proposal.authoring_scope.intent, AuthoringIntent.REVIEW_ONLY)
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome())
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome(message="Suggested edits remain findings until a fresh authoring request."))
        reviewed = await self.runtime.decide_lead_step(_decision(good, c.CollaborationConfirmationDecision.CONFIRM))
        self.assertEqual([item["text"].encode() for agent, call in self.backend.call_records if agent == PLOT_ARCHITECT for item in _workflow_material(call.payload)["inputs"] if item["input_id"] == "user_input"], [original.encode()])
        self.assertEqual(self.artifact_bytes(), before)
        self.assertTrue(all(request is None for request in (reviewed.turn.artifact_application_request, reviewed.turn.story_commit_request)))
        self.assertFalse(any(type(e.payload) in (c.ArtifactObjectivePayload, c.ArtifactObjectivePayloadV2) for e in self.history(conversation)))

    async def test_wrong_task_pair_rejects_before_dispatch(self):
        def wrong_pair(value, material): value["authoring_scope"]["task_revision"] += 1
        result = await self.proposal_for(
            self.conversation(), scoped_proposal(PLOT_ARCHITECT, mutate=wrong_pair)
        )
        self._assert_continuation(result, 'proposal')

    async def test_cross_conversation_selection_preserves_scope_and_rejects_workflow_relabel(self):
        original_conversation = self.conversation()
        original = await self.proposal_for(original_conversation)
        prefix = self.history(original_conversation)
        selected = original.proposal.authoring_scope.task_id
        def relabel(value, material):
            value["authoring_scope"].update(activity="workflow", target_paths=[], domains=[])
        bad = await self.proposal_for(self.conversation(), scoped_proposal(PLOT_ARCHITECT, selected=selected, kind="answer", mutate=relabel))
        self._assert_continuation(bad, 'proposal')
        continued = await self.proposal_for(self.conversation(), scoped_proposal(PLOT_ARCHITECT, selected=selected))
        self.assertIsNotNone(continued.proposal, continued)
        self.assertEqual(continued.proposal.authoring_scope.task_id, selected)
        self.assertEqual(self.history(original_conversation), prefix)
        source, foreign_focus = self.conversation("Current provisional source"), self.conversation("Foreign focus")
        self.backend.enqueue(_exploration_outcome())
        await self.runtime.lead_turn(self.session_id, source, "Explore alternatives.", interaction_mode=exploration.LeadInteractionMode.EXPLORATION)
        event = next(event for event in self.history(source) if event.kind is c.CollaborationEventKind.LEAD_EXPLORATION_RESULT)
        selection = _adoption_selection(source, event, (OUTLINE,))
        before = (self.history(source), self.history(foreign_focus), len(self.backend.calls))
        for focus, choice in ((foreign_focus, selection), (source, replace(selection, material_digest="0" * 64)), (source, replace(selection, result_event_id="stale-current-result"))):
            with self.assertRaises(ValueError):
                await self.runtime.lead_turn(self.session_id, focus, "Adopt the selected whole idea.", adoption=choice)
        self.assertEqual((self.history(source), self.history(foreign_focus), len(self.backend.calls)), before)

    async def test_draft_task_cannot_skip_current_revision_drafting_via_review_artifact_intent(self):
        conversation = self.conversation()
        pending = await self.proposal_for(conversation)
        task_id = pending.proposal.authoring_scope.task_id
        self.workspace.path(OUTLINE).unlink()
        response = _artifact_outcome(operations=[{"kind":"create", "suggested_path":OUTLINE, "new_text":"Current synthetic candidate."}], evidence_status="missing", evidence_ids=lambda call,m:[next(x["evidence_id"] for x in call.payload["core_document_statuses"] if x["artifact_path"] == OUTLINE)])
        def selected_review(call, provider):
            value = json.loads(response(call, provider))
            material = _workflow_material(call.payload)
            index = next(i for i,t in enumerate(material["authoring_tasks"]) if t["task_id"] == task_id)
            value["action"]["result"]["authoring_scope"] = _compact_scope(
                _live_scope(call, activity="review", paths=(OUTLINE,), task_index=index)
            )
            return json.dumps(value)
        self.backend.enqueue(selected_review)
        result = await self.runtime.lead_turn(self.session_id, self.conversation(), "Integrate that pending task into the supplied candidate.")
        self._assert_continuation(result, 'artifact_proposal')

    async def test_provider_free_advisory_requires_scope_and_separate_confirmation(self):
        conversation = self.conversation()
        with self.assertRaisesRegex(ValueError, "explicit authoring scope"):
            await self.runtime.propose_specialist_advisory(self.session_id, conversation, PLOT_ARCHITECT, "Draft a synthetic outline.")
        scope = AuthoringScope(AuthoringActivity.DRAFT, (OUTLINE,), (), "Draft a synthetic outline.", "provided_task", 1)
        result = await self.runtime.propose_specialist_advisory(self.session_id, conversation, PLOT_ARCHITECT, "A summons causes a choice and its consequence.", authoring_scope=scope)
        self.assertEqual(self.backend.calls, [])
        self.assertIs(result.state, c.CollaborationStepState.AWAITING_CONFIRMATION)
        self.assertIs(result.proposal.assignments[0].mode, SpecialistAssignmentMode.DRAFT)

    def artifact_bytes(self):
        return tuple(
            (item.path, item.content)
            for item in WritingArtifactRepository(
                self.workspace.root
            ).capture_complete_state()
        )

    async def test_draft_integrates_exact_all_role_review_apply_and_storage_parity(self):
        subprocess.run(['git', 'add', '--', 'core_documents', 'manuscript'], cwd=self.workspace.root, check=True)
        subprocess.run(['git', 'commit', '-m', 'Seed lead commit test fixtures'], cwd=self.workspace.root, check=True, stdout=subprocess.DEVNULL)
        from tests.test_lead_artifact_application import _application_decision
        from tests.test_lead_story_commit import _commit_decision, StoryCommitDecision
        from tests.test_artifact_proposal_orchestration import (
            WorkspaceConversationStorageMutationCoordinator, provision_sqlite_conversation_storage,
            transfer_conversation_storage, verify_conversation_storage,
            ConversationStorageBackend, ConversationStorageTransferOutcome,
            compose_sqlite_conversation_storage,
        )
        path = "manuscript/part-01/integrated.md"
        conversation = self.conversation()
        before = self.artifact_bytes()
        premise = "Synthetic tidal city 한글\r\nKeep the stated limit.  "
        self.backend.enqueue(_exploration_outcome(premise))
        explored = await self.runtime.lead_turn(self.session_id, conversation, "Explore a provisional setting.", interaction_mode=exploration.LeadInteractionMode.EXPLORATION)
        self.assertIs(explored.kind, c.LeadOutcomeKind.EXPLORATION)
        self.assertEqual(tuple(getattr(explored, key) for key in ("proposal", "artifact_proposal", "artifact_application_request", "story_commit_request", "recommendation_request", "confirmation_request_id")), (None,) * 6)
        self.assertEqual(self.artifact_bytes(), before)
        exploration_prefix = self.history(conversation)
        self.assertEqual(c.project_collaboration_history(exploration_prefix).artifact_objectives, ())
        result_event = next(e for e in exploration_prefix if type(e.payload) is c.CollaborationLeadExplorationResultPayload)
        selection = _adoption_selection(conversation, result_event, (path,))
        self.assertEqual(tuple((call.call_id, call.invocation_kind) for call in _submitted_calls(exploration_prefix)), ((explored.exploration.provider_call_id, c.CollaborationInvocationKind.LEAD_EXPLORATION),))
        def mixed(value, material):
            scope = value["authoring_scope"]
            scope["contributions"] = [{"domain": domain, "role": "draft_owner" if domain == "tone_execution" else "constraint_assessor"} for domain in ("plot_structure", "character_world", "tone_execution")]
            scope["dependency_slots"] = [{"slot_id": "setting", "dependent_domains": ["tone_execution"]}]
            value["proposal"]["shared_premises"] = [{"slot_id": "setting", "selected_premise": premise, "basis_input_ids": ["adopted_idea"]}]
            for assignment in value["proposal"]["assignments"]:
                assignment["input_ids"].append("adopted_idea")
                assignment["mode"] = "draft" if assignment["agent_id"] == EXECUTION_SPECIALIST else "constraint_assessment"
        def recommend(call, provider):
            material = _workflow_material(call.payload)
            draft_reply = next(reply["reply_event_id"] for reply in material["replies"] if reply["mode"] == "draft")
            return _synthesis_outcome(recommended_action=action(target_paths=(path,), source_ids=("user_input", "adopted_idea", draft_reply)))(call, provider)
        drafted, draft_result = await self.finish_draft(conversation, text=premise, adoption=selection, response=scoped_proposal(
            *ROSTER, paths=(path,), domains=("plot_structure", "character_world"), mutate=mixed),
            synthesis=recommend)
        self.assertEqual(tuple(assignment.mode for assignment in drafted.proposal.assignments), (SpecialistAssignmentMode.CONSTRAINT_ASSESSMENT, SpecialistAssignmentMode.CONSTRAINT_ASSESSMENT, SpecialistAssignmentMode.DRAFT))
        capture = drafted.proposal.basis.shared_premises
        adoption_event = next(e for e in self.history(conversation) if type(e.payload) is c.CollaborationExplorationAdoptionPayload)
        self.assertEqual(adoption_event.payload.capture.selection, selection)
        self.assertEqual(adoption_event.payload.capture.selected_text.encode("utf-8"), premise.encode("utf-8"))
        self.assertEqual((drafted.proposal.authoring_scope.task_id, drafted.proposal.authoring_scope.task_revision), (adoption_event.payload.capture.fresh_task_id, 1))
        self.assertIs(drafted.interaction_mode, exploration.LeadInteractionMode.ORCHESTRATION)
        self.assertEqual(capture[0].selected_premise, premise)
        self.assertEqual(capture[0].sources[0].source_conversation_id, conversation)
        self.assertEqual(draft_result.turn.recommendation_request.source_proposal_digest, drafted.proposal.proposal_digest)
        source = capture[0].sources[0].input_revision
        disclosures = (premise, "draft_owner", "constraint_assessor", "setting", "plot_structure", "character_world", "tone_execution",
            source.input_id, source.kind.value, source.locator, "whole input", source.state.value, source.revision_digest, conversation)
        panels = (view.project_lead_turn_result(drafted), view.project_lead_turn_result(draft_result.turn),
            view.project_collaboration_event(next(event for event in self.history(conversation)
                if event.step_id == drafted.step_id and getattr(event.payload, "state", None) is c.CollaborationStepState.AWAITING_CONFIRMATION),
                proposals={drafted.proposal.proposal_id: drafted.proposal}))
        self.assertTrue(all(json.dumps(value, ensure_ascii=False)[1:-1] in json.dumps(asdict(panel), ensure_ascii=False)
            for panel in panels for value in disclosures))
        task_id = drafted.proposal.authoring_scope.task_id
        draft_prefix = self.history(conversation)
        response = _artifact_outcome(
            operations=[{"kind":"create", "suggested_path":path,
                         "new_text":"Summons leads to a choice; the choice causes a consequence.\n"}],
            evidence_status="incomplete", evidence_ids=lambda call,m:[call.payload["core_document_statuses"][0]["evidence_id"]])
        def integrate(call, provider):
            value = json.loads(response(call, provider))
            scope = c.authoring_scope_to_dict(drafted.proposal.authoring_scope)
            value["action"]["result"]["artifact_intent"]["objective_intent"] = {"action": "start", "target_paths": [path], "next_step": "prepare"}
            value["action"]["result"]["authoring_scope"] = _compact_scope(scope)
            return json.dumps(value)
        self.backend.enqueue(integrate)
        proposed = await self.runtime.decide_lead_recommendation(recommendation_decision(draft_result.turn))
        self.assertIs(proposed.kind, c.LeadOutcomeKind.PROPOSAL)
        self.assertEqual(proposed.proposal.participants, ROSTER)
        self.assertEqual(proposed.proposal.basis.shared_premises, capture)
        self.assertEqual(proposed.proposal.budget.rounds_spent, 1)
        candidate_inputs = [x for x in proposed.proposal.basis.inputs if x.kind is c.CollaborationInputKind.ARTIFACT_CANDIDATE]
        self.assertEqual(len(candidate_inputs),1)
        for assignment in proposed.proposal.assignments:
            self.assertIs(assignment.mode, SpecialistAssignmentMode.REVIEW)
            self.assertEqual(assignment.review_basis.candidate_input_ids, (candidate_inputs[0].input_id,))
            self.backend.enqueue_for(assignment.specialist_id, _specialist_outcome("supports", "The exact integrated candidate preserves the assigned domain."))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _artifact_synthesis_outcome())
        reviewed = await self.runtime.decide_lead_step(_decision(proposed,c.CollaborationConfirmationDecision.CONFIRM))
        self.assertEqual(len(_submitted_calls(self.history(conversation)[len(exploration_prefix):])), 10, [(actor, call.call_kind.value) for actor, call in self.backend.call_records])
        review_frames = [_workflow_material(call.payload) for actor, call in self.backend.call_records if actor in ROSTER][-3:]
        review_candidates = [next(item["artifact_candidate"] for item in frame["inputs"] if item["input_id"] == candidate_inputs[0].input_id) for frame in review_frames]
        self.assertEqual(review_candidates, [review_candidates[0]] * len(ROSTER))
        self.assertEqual([frame["shared_premises"] for frame in review_frames], [shared_premises_to_dict(capture)] * len(ROSTER))
        self.assertEqual([frame["assignment"]["review_basis"]["candidate_input_ids"] for frame in review_frames], [[candidate_inputs[0].input_id]] * len(ROSTER))
        adopted_inputs = [item for actor, call in self.backend.call_records if actor in ROSTER for item in _workflow_material(call.payload)["inputs"] if item["input_id"] == "adopted_idea"]
        self.assertEqual([item["conversation_event"]["capture"]["selected_text"].encode("utf-8") for item in adopted_inputs], [premise.encode("utf-8")] * (2 * len(ROSTER)))
        self.assertEqual(self.artifact_bytes(),before)
        self.assertIsNotNone(reviewed.turn.artifact_application_request)
        self.assertEqual(self.history(conversation)[:len(draft_prefix)],draft_prefix)
        applied = await self.runtime.decide_artifact_application(_application_decision(reviewed.turn,c.ArtifactApplicationDecisionKind.APPLY))
        self.assertTrue(self.workspace.path(path).read_text().startswith("Summons leads"))
        self.assertIsNotNone(applied.commit_request)
        await self.runtime.decide_story_commit(_commit_decision(applied, StoryCommitDecision.REJECT))
        for failure, code in ((ConnectionError("Known adoption failure"), c.CollaborationLeadOutcomeFailureCode.PROVIDER_FAILED), (asyncio.CancelledError(), c.CollaborationLeadOutcomeFailureCode.CANCELLED)):
            self.backend.enqueue(_SubmittedFailure(failure))
            if code is c.CollaborationLeadOutcomeFailureCode.CANCELLED:
                with self.assertRaises(asyncio.CancelledError):
                    await self.runtime.lead_turn(self.session_id, conversation, "Draft adopted idea.", adoption=selection)
            else:
                failed = await self.runtime.lead_turn(self.session_id, conversation, "Draft adopted idea.", adoption=selection)
                self.assertIs(failed.next_action, c.CollaborationNextAction.RETRY_LEAD_TURN)
            failed_history = self.history(conversation)
            submitted = _submitted_calls(failed_history)
            closure = next(e.payload for e in reversed(failed_history) if type(e.payload) is c.CollaborationLeadOutcomeFailurePayload)
            self.assertEqual((closure.code, closure.call_id, closure.invocation_id), (code, submitted[-1].call_id, submitted[-1].invocation_id))
            self.assertIs(c.project_collaboration_history(failed_history).next_safe_action, c.CollaborationNextAction.RETRY_LEAD_TURN)
            self.backend.enqueue(_visible_outcome("answer", "Known failure safely closed."))
            resumed = await self.runtime.lead_turn(self.session_id, conversation, "Report status.")
            self.assertIs(resumed.kind, c.LeadOutcomeKind.ANSWER)
            self.assertEqual(resumed.message, "Known failure safely closed.")
            self.assertEqual(self.history(conversation)[:len(failed_history)], failed_history)
            self.assertEqual(len(_submitted_calls(self.history(conversation))), len(submitted) + 1)
            self.assertEqual((resumed.proposal, resumed.artifact_proposal, resumed.artifact_application_request, resumed.story_commit_request), (None,) * 4)
        history = self.history(conversation)
        self.assertTrue(any(type(e.payload) is c.ArtifactApplicationResultPayload and e.payload.outcome is c.ArtifactApplicationOutcomeKind.APPLIED for e in history))
        self.assertEqual(applied.commit_request.application_result_event_id, next(e.event_id for e in history if type(e.payload) is c.ArtifactApplicationResultPayload and e.payload.outcome is c.ArtifactApplicationOutcomeKind.APPLIED))
        offered = []
        def status(call,provider):
            offered.extend(_workflow_material(call.payload)["authoring_tasks"])
            return _visible_outcome("answer","The previous artifact application is recorded.")(call,provider)
        self.backend.enqueue(status)
        await self.runtime.lead_turn(self.session_id,self.conversation(),"Report the recorded application status.")
        self.assertNotIn(task_id,{item["task_id"] for item in offered})
        coordinator=WorkspaceConversationStorageMutationCoordinator(self.control_workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            provision_sqlite_conversation_storage(self.control_workspace,lease=lease)
        transfer=transfer_conversation_storage(self.control_workspace,source_backend=ConversationStorageBackend.FILESYSTEM,destination_backend=ConversationStorageBackend.SQLITE)
        self.assertIs(transfer.outcome,ConversationStorageTransferOutcome.COMPLETED)
        verified=verify_conversation_storage(self.control_workspace,source_backend=ConversationStorageBackend.FILESYSTEM,destination_backend=ConversationStorageBackend.SQLITE)
        self.assertIs(verified.outcome,ConversationStorageTransferOutcome.COMPLETED)
        persisted=compose_sqlite_conversation_storage(self.control_workspace).event_store.read(self.session_id,conversation)
        self.assertEqual(persisted,history)
        self.assertEqual(tuple(c.encode_collaboration_event(e) for e in persisted),tuple(c.encode_collaboration_event(e) for e in history))

    async def test_archived_task_is_context_without_routable_authority(self):
        archived = self.conversation()
        proposal = await self.proposal_for(archived)
        before = self.history(archived)
        self.runtime.conversation_storage_services.catalog.archive(self.session_id, archived)
        offered=[]
        def answer(call,provider):
            offered.extend(_workflow_material(call.payload)["authoring_tasks"])
            return _visible_outcome("answer", "The archived conversation remains readable history.")(call,provider)
        self.backend.enqueue(answer)
        await self.runtime.lead_turn(self.session_id,self.conversation(),"Report active task status.")
        self.assertNotIn(proposal.proposal.authoring_scope.task_id,{item["task_id"] for item in offered})
        self.assertEqual(self.history(archived),before)

    async def test_old_drafted_revision_cannot_authorize_revised_candidate(self):
        conversation=self.conversation()
        from tests.test_lead_artifact_application import _application_decision
        before = self.artifact_bytes()
        path = "manuscript/part-01/revised-candidate.md"
        def two_owners(value, material):
            next(item for item in value["authoring_scope"]["contributions"] if item["domain"] == "tone_execution")["role"] = "constraint_assessor"
            next(item for item in value["proposal"]["assignments"] if item["agent_id"] == EXECUTION_SPECIALIST)["mode"] = "constraint_assessment"
        original, _ = await self.finish_draft(conversation, response=scoped_proposal(
            *ROSTER, domains=("character_world", "tone_execution"), mutate=two_owners))
        old_source = c.project_collaboration_history(self.history(conversation)).offered_draft_sources[-1].event_id
        task_id=original.proposal.authoring_scope.task_id
        drifted = await self.proposal_for(conversation, recommendation(recommended_action=action(source_ids=(old_source,), target_paths=(OUTLINE,), specialist_ids=ROSTER), authoring_scope=lambda call: dict(next(t["current_scope"] for t in _workflow_material(call.payload)["authoring_tasks"] if t["task_id"] == task_id and t["task_revision"] == 1), domains=["plot_structure", "character_world", "tone_execution"], contributions=[{"domain": domain, "role": "draft_owner" if domain == "plot_structure" else "constraint_assessor"} for domain in ("plot_structure", "character_world", "tone_execution")])))
        self.assertIsNone(drifted.recommendation_request, drifted)
        self._assert_continuation(drifted, "proposal")
        ready = await self.proposal_for(conversation, recommendation(recommended_action=action(
            source_ids=(old_source,), target_paths=(path,), specialist_ids=ROSTER),
            authoring_scope=c.authoring_scope_to_dict(original.proposal.authoring_scope)))
        self.assertIsNotNone(ready.recommendation_request, ready)
        response = _artifact_outcome(operations=[{"kind": "create", "suggested_path": path, "new_text": "Synthetic chronology remains explicit.\n"}],
            evidence_status="incomplete", evidence_ids=lambda call, material: [call.payload["core_document_statuses"][0]["evidence_id"]])
        def integrate(call, provider):
            value = json.loads(response(call, provider))
            scope = c.authoring_scope_to_dict(original.proposal.authoring_scope)
            value["action"]["result"]["artifact_intent"]["objective_intent"] = {"action": "start", "target_paths": [path], "next_step": "prepare"}
            value["action"]["result"]["authoring_scope"] = _compact_scope(scope)
            return json.dumps(value)
        self.backend.enqueue(integrate)
        review = await self.runtime.decide_lead_recommendation(recommendation_decision(ready))
        self.assertIsNotNone(review.proposal, review)
        self.assertEqual(review.proposal.participants, ROSTER)
        for assignment in review.proposal.assignments:
            self.backend.enqueue_for(assignment.specialist_id, _specialist_outcome("supports", "The candidate preserves the reviewed chronology and motivation."))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _artifact_synthesis_outcome())
        reviewed = await self.runtime.decide_lead_step(_decision(review, c.CollaborationConfirmationDecision.CONFIRM))
        review_frames = [_workflow_material(call.payload) for actor, call in self.backend.call_records if actor in ROSTER][-len(ROSTER):]
        review_candidates = [next(item["artifact_candidate"] for item in frame["inputs"] if item["input_id"] == frame["assignment"]["review_basis"]["candidate_input_ids"][0]) for frame in review_frames]
        self.assertEqual(review_candidates, [review_candidates[0]] * len(ROSTER))
        await self.runtime.decide_artifact_application(_application_decision(reviewed.turn, c.ArtifactApplicationDecisionKind.REJECT))
        self.assertEqual(self.artifact_bytes(), before)
        completed_prefix = self.history(conversation)
        completed = c.project_collaboration_history(completed_prefix)
        self.assertIs(completed.current_artifact_application_result.outcome, c.ArtifactApplicationOutcomeKind.REJECTED)
        self.assertEqual(completed.rounds_spent, 2)
        self.assertLess(12 - completed.physical_calls_spent, len(review.proposal.participants) + 2)
        direct_review = await self.proposal_for(conversation, scoped_proposal(*ROSTER,
            selected=task_id, revision=2, activity="review", domains=("character_world", "tone_execution"),
            mutate=lambda value, material: value["authoring_scope"].update(contributions=c.authoring_scope_to_dict(original.proposal.authoring_scope)["contributions"])))
        self._assert_continuation(direct_review, "proposal")
        self.assertFalse(any(e.kind is c.CollaborationEventKind.ASSIGNMENT_DISPATCH for e in self.history(conversation)[len(completed_prefix):]))
        def successor(value, material):
            self.assertEqual([(t["task_id"], t["task_revision"]) for t in material["authoring_tasks"]], [(task_id, 2)])
            self.assertEqual(material["available_proposal_drafting_source_ids"], [])
        pending, revised = await self.finish_draft(conversation, response=scoped_proposal(PLOT_ARCHITECT, selected=task_id, revision=2, mutate=successor))
        self.assertIsNotNone(pending.proposal, pending)
        self.assertEqual(pending.proposal.authoring_scope.task_id, task_id)
        self.assertIs(revised.turn.state, c.CollaborationStepState.COMPLETED)
        self.assertEqual(revised.proposal.authoring_scope.task_revision,2)
        self.assertEqual(self.history(conversation)[:len(completed_prefix)], completed_prefix)
        def cross_product(value,material):
            offered=material["authoring_tasks"]
            self.assertTrue(any(t["task_revision"]==2 for t in offered))
            self.assertEqual(value["authoring_scope"]["task_revision"],1)
            value["authoring_scope"]["task_revision"]=2
        invalid=await self.proposal_for(self.conversation(),scoped_proposal(PLOT_ARCHITECT,mutate=cross_product))
        self._assert_continuation(invalid, 'proposal')
        self.workspace.path(OUTLINE).unlink()
        def stale(call, provider):
            self.assertIn(old_source, _workflow_material(call.payload)["available_proposal_drafting_source_ids"])
            current = next(t["current_scope"] for t in _workflow_material(call.payload)["authoring_tasks"] if t["task_id"] == task_id and t["task_revision"] == 2)
            self.assertEqual((current["task_id"], current["task_revision"]), (task_id, 2))
            return recommendation(message="Exclude the earlier revision draft.", recommended_action=action(source_ids=(old_source,), target_paths=(OUTLINE,), specialist_ids=(PLOT_ARCHITECT,)), authoring_scope=current)(call, provider)
        self.backend.enqueue(stale)
        rejected=await self.runtime.lead_turn(self.session_id,conversation,"Use the earlier draft for this revised candidate.")
        self.assertIsNone(rejected.recommendation_request, rejected)
        self._assert_continuation(rejected, 'artifact_proposal')

    async def test_legacy_pending_confirmation_requires_fresh_scoped_proposal_without_rewrite(self):
        from types import SimpleNamespace
        from tests import collaboration_contract_fixture as fixture
        from story_writing_agents.errors import BackendProtocolError
        conversation=self.conversation()
        batch=fixture.contract_batches(conversation)["proposal"]
        batch=replace(batch,events=tuple(replace(e,session_id=self.session_id) for e in batch.events))
        self.runtime.conversation_storage_services.event_store.append_batch(batch)
        history=self.history(conversation)
        proposal=next(e.payload for e in history if type(e.payload) is c.CollaborationProposal)
        self.assertIsNone(proposal.authoring_scope)
        state=next(e.payload for e in history if type(e.payload) is c.CollaborationStepStatePayload)
        pending=SimpleNamespace(session_id=self.session_id,conversation_id=conversation,step_id=proposal.step_id,
                                proposal=proposal,confirmation_request_id=state.confirmation_request_id)
        before=tuple(c.encode_collaboration_event(e) for e in history)
        with self.assertRaisesRegex(BackendProtocolError,"regeneration and confirmation"):
            await self.runtime.decide_lead_step(_decision(pending,c.CollaborationConfirmationDecision.CONFIRM))
        self.assertEqual(self.backend.calls,[])
        self.assertEqual(tuple(c.encode_collaboration_event(e) for e in self.history(conversation)),before)
        fresh=await self.proposal_for(conversation)
        self.assertIsNotNone(fresh.proposal.authoring_scope)
        self.assertNotEqual(fresh.confirmation_request_id,pending.confirmation_request_id)
        self.assertEqual(tuple(c.encode_collaboration_event(e) for e in self.history(conversation)[:len(history)]),before)
