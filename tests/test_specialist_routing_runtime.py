"""Runtime routing accepts typed task facts and keeps draft/review authority separate."""
import json
from dataclasses import replace
from unittest import mock

from story_writing_agents import StoryRuntime
from story_writing_agents import conversations as c
from story_writing_agents.agents import PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST, LEAD_ORCHESTRATOR
from story_writing_agents.specialist_routing import AuthoringActivity, AuthoringScope, ArtifactReviewDomain, SpecialistAssignmentMode, ReviewBasis
from story_writing_agents.writing_artifacts.repository import WritingArtifactRepository
from tests.test_lead_orchestration import (
    LeadOrchestrationTestCase, _LeadBackend, _decision, _provider_envelope,
    _workflow_material, _live_scope, _specialist_outcome, _synthesis_outcome, _visible_outcome,
)
from tests.test_artifact_proposal_orchestration import _artifact_outcome, _artifact_synthesis_outcome

OUTLINE = "core_documents/global_outline.md"
CHARACTER = "core_documents/character_sheets.md"
ROSTER = (PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST)


def scoped_proposal(*agents, activity="draft", paths=(OUTLINE,), domains=(), selected=None,
                    candidate_ids=None, kind="proposal", mutate=None):
    def respond(call, provider):
        material = _workflow_material(call.payload)
        tasks = material["authoring_tasks"]
        index = next((i for i, t in enumerate(tasks) if t["task_id"] == selected), len(tasks)-1)
        scope = _live_scope(call, activity=activity, paths=paths, domains=domains, task_index=index)
        scope["interpretation"] = "Draft from the current synthetic brief." if activity == "draft" else "Review the supplied concrete synthetic candidate."
        mode = "review" if activity == "review" else "draft"
        ids = ["user_input"] if candidate_ids is None else list(candidate_ids)
        value = {"authoring_scope": scope, "kind": kind, "message": "The scoped work requires its disclosed specialists.",
                 "scope": "story_specific", "evidence_status": "incomplete",
                 "evidence_ids": [], "artifact_intent": None,
                 "proposal": {"relation": material["answer_contract"]["proposal"]["relation"],
                              "purpose": "Produce the scoped synthetic material.", "authoring_scope": scope,
                              "assignments": [{"agent_id": agent, "instruction": "Produce the bounded material." if mode == "draft" else "Assess the supplied candidate.",
                                               "input_ids": list(dict.fromkeys(["user_input", *ids])), "mode": mode,
                                               "review_basis": None if mode == "draft" else {"schema_version":1,"candidate_input_ids":ids,"criteria":"Assess chronology and motivation."}}
                                              for agent in agents]} if kind == "proposal" else None}
        statuses = call.payload.get("core_statuses", [])
        status = next((s for s in statuses if s["locator"] in paths), statuses[0] if statuses else None)
        if status is not None: value["evidence_ids"] = [status["evidence_id"]]
        if mutate: mutate(value, material)
        return _provider_envelope(call, value)
    return respond


class SpecialistRoutingRuntimeTests(LeadOrchestrationTestCase):
    async def proposal_for(self, conversation, response=None, text="The synthetic brief begins with a summons, then a choice, then its consequence."):
        self.backend.enqueue(response or scoped_proposal(PLOT_ARCHITECT))
        return await self.runtime.lead_turn(self.session_id, conversation, text)

    async def finish_draft(self, conversation, **kwargs):
        proposed = await self.proposal_for(conversation, **kwargs)
        self.assertIs(proposed.kind, c.LeadOutcomeKind.PROPOSAL)
        for agent in proposed.proposal.participants:
            self.backend.enqueue_for(agent, _specialist_outcome("drafted", "Summons leads to a choice; the choice causes a consequence."))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome(message="The draft material is available for integration; no artifact changed."))
        result = await self.runtime.decide_lead_step(_decision(proposed, c.CollaborationConfirmationDecision.CONFIRM))
        self.assertIs(result.turn.state, c.CollaborationStepState.COMPLETED)
        return proposed, result

    async def test_empty_target_adequate_brief_produces_draft_without_writes(self):
        self.workspace.path(OUTLINE).write_bytes(b"")
        conversation = self.conversation()
        before = self.artifact_bytes()
        proposed, result = await self.finish_draft(conversation)
        self.assertEqual(proposed.proposal.participants, (PLOT_ARCHITECT,))
        replies = [e.payload for e in self.history(conversation) if type(e.payload) is c.CollaborationSpecialistReplyPayload]
        self.assertEqual(len(replies), 1)
        self.assertIs(replies[0].assessment, c.CollaborationSpecialistAssessment.DRAFTED)
        self.assertIs(replies[0].mode, SpecialistAssignmentMode.DRAFT)
        self.assertEqual(self.artifact_bytes(), before)
        self.assertIsNone(result.turn.artifact_application_request)
        synthesis = next(e.payload for e in self.history(conversation) if type(e.payload) is c.CollaborationLeadSynthesisPayload)
        self.assertEqual(synthesis.unresolved_input_ids, ())

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
                self.assertIsNone(result.proposal)
                self.assertIs(result.next_action, c.CollaborationNextAction.RETRY_LEAD_TURN)
                self.assertFalse(any(e.kind is c.CollaborationEventKind.ASSIGNMENT_DISPATCH for e in self.history(conversation)))
        conversation = self.conversation()
        self.backend.enqueue(_visible_outcome("question", "Which existing task should this status request concern?"))
        question = await self.runtime.lead_turn(self.session_id, conversation, "Clarify which task is active.")
        self.assertIs(question.kind, c.LeadOutcomeKind.QUESTION)

    async def test_review_rejects_empty_reference_but_accepts_explicit_user_candidate(self):
        self.workspace.path(OUTLINE).write_bytes(b"")
        bad = await self.proposal_for(self.conversation(), scoped_proposal(PLOT_ARCHITECT, activity="review", candidate_ids=("reference_1",)), text="Assess the concrete candidate ${core_documents/global_outline.md}.")
        self.assertIsNone(bad.proposal)
        good = await self.proposal_for(self.conversation(), scoped_proposal(PLOT_ARCHITECT, activity="review", candidate_ids=("user_input",)), text="Candidate: a summons precedes a choice, and the consequence follows the choice.")
        self.assertIs(good.kind, c.LeadOutcomeKind.PROPOSAL)
        self.assertEqual(good.proposal.assignments[0].review_basis.candidate_input_ids, ("user_input",))

    async def test_wrong_task_pair_and_scope_mismatch_reject_before_dispatch(self):
        def wrong_pair(value, material): value["authoring_scope"]["task_revision"] += 1
        def mismatch(value, material): value["proposal"]["authoring_scope"] = dict(value["authoring_scope"], interpretation="Different task interpretation.")
        for mutation in (wrong_pair, mismatch):
            with self.subTest(mutation=mutation.__name__):
                result = await self.proposal_for(self.conversation(), scoped_proposal(PLOT_ARCHITECT, mutate=mutation))
                self.assertIsNone(result.proposal)
                self.assertIs(result.next_action, c.CollaborationNextAction.RETRY_LEAD_TURN)

    async def test_cross_conversation_selection_preserves_scope_and_rejects_workflow_relabel(self):
        original_conversation = self.conversation()
        original = await self.proposal_for(original_conversation)
        prefix = self.history(original_conversation)
        selected = original.proposal.authoring_scope.task_id
        def relabel(value, material):
            value["authoring_scope"].update(activity="workflow", target_paths=[], domains=[])
        bad = await self.proposal_for(self.conversation(), scoped_proposal(PLOT_ARCHITECT, selected=selected, kind="answer", mutate=relabel))
        self.assertIsNone(bad.proposal)
        self.assertIs(bad.next_action, c.CollaborationNextAction.RETRY_LEAD_TURN)
        continued = await self.proposal_for(self.conversation(), scoped_proposal(PLOT_ARCHITECT, selected=selected))
        self.assertEqual(continued.proposal.authoring_scope.task_id, selected)
        self.assertEqual(self.history(original_conversation), prefix)

    async def test_draft_task_cannot_skip_current_revision_drafting_via_review_artifact_intent(self):
        conversation = self.conversation()
        pending = await self.proposal_for(conversation)
        task_id = pending.proposal.authoring_scope.task_id
        self.workspace.path(OUTLINE).unlink()
        response = _artifact_outcome(operations=[{"kind":"create", "suggested_path":OUTLINE, "new_text":"Current synthetic candidate."}], evidence_status="missing", evidence_ids=lambda call,m:[next(x["evidence_id"] for x in call.payload["core_statuses"] if x["locator"] == OUTLINE)])
        def selected_review(call, provider):
            value = json.loads(response(call, provider))
            material = _workflow_material(call.payload)
            index = next(i for i,t in enumerate(material["authoring_tasks"]) if t["task_id"] == task_id)
            value["action"]["result"]["authoring_scope"] = _live_scope(call, activity="review", paths=(OUTLINE,), task_index=index)
            return json.dumps(value)
        self.backend.enqueue(selected_review)
        result = await self.runtime.lead_turn(self.session_id, self.conversation(), "Integrate that pending task into the supplied candidate.")
        self.assertIsNone(result.artifact_proposal)
        self.assertIs(result.next_action, c.CollaborationNextAction.RETRY_LEAD_TURN)

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
        from tests.test_lead_artifact_application import _application_decision
        from tests.test_artifact_proposal_orchestration import (
            WorkspaceConversationStorageMutationCoordinator, provision_sqlite_conversation_storage,
            transfer_conversation_storage, verify_conversation_storage,
            ConversationStorageBackend, ConversationStorageTransferOutcome,
            compose_sqlite_conversation_storage,
        )
        path = "manuscript/part-01/integrated.md"
        conversation = self.conversation()
        before = self.artifact_bytes()
        drafted, _ = await self.finish_draft(conversation, response=scoped_proposal(
            *ROSTER, paths=(path,), domains=("plot_structure", "character_world")))
        task_id = drafted.proposal.authoring_scope.task_id
        draft_prefix = self.history(conversation)
        response = _artifact_outcome(
            operations=[{"kind":"create", "suggested_path":path,
                         "new_text":"Summons leads to a choice; the choice causes a consequence.\n"}],
            evidence_status="incomplete", evidence_ids=lambda call,m:[call.payload["core_statuses"][0]["evidence_id"]])
        def integrate(call, provider):
            value = json.loads(response(call, provider))
            material = _workflow_material(call.payload)
            index = next(i for i,t in enumerate(material["authoring_tasks"]) if t["task_id"] == task_id)
            value["action"]["result"]["authoring_scope"] = _live_scope(
                call, activity="review", paths=(path,), domains=("plot_structure", "character_world"), task_index=index)
            return json.dumps(value)
        self.backend.enqueue(integrate)
        proposed = await self.runtime.lead_turn(self.session_id, conversation, "Integrate the completed drafts into one exact candidate for all affected reviews.")
        self.assertIs(proposed.kind, c.LeadOutcomeKind.PROPOSAL)
        self.assertEqual(proposed.proposal.participants, ROSTER)
        candidate_inputs = [x for x in proposed.proposal.basis.inputs if x.kind is c.CollaborationInputKind.ARTIFACT_CANDIDATE]
        self.assertEqual(len(candidate_inputs),1)
        for assignment in proposed.proposal.assignments:
            self.assertIs(assignment.mode, SpecialistAssignmentMode.REVIEW)
            self.assertEqual(assignment.review_basis.candidate_input_ids, (candidate_inputs[0].input_id,))
            self.backend.enqueue_for(assignment.specialist_id, _specialist_outcome("supports", "The exact integrated candidate preserves the assigned domain."))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _artifact_synthesis_outcome())
        reviewed = await self.runtime.decide_lead_step(_decision(proposed,c.CollaborationConfirmationDecision.CONFIRM))
        self.assertEqual(self.artifact_bytes(),before)
        self.assertIsNotNone(reviewed.turn.artifact_application_request)
        self.assertEqual(self.history(conversation)[:len(draft_prefix)],draft_prefix)
        applied = await self.runtime.decide_artifact_application(_application_decision(reviewed.turn,c.ArtifactApplicationDecisionKind.APPLY))
        self.assertTrue(self.workspace.path(path).read_text().startswith("Summons leads"))
        history = self.history(conversation)
        self.assertTrue(any(type(e.payload) is c.ArtifactApplicationResultPayload and e.payload.outcome is c.ArtifactApplicationOutcomeKind.APPLIED for e in history))
        offered = []
        def status(call,provider):
            offered.extend(_workflow_material(call.payload)["authoring_tasks"])
            return _visible_outcome("answer","The previous artifact application is recorded.")(call,provider)
        self.backend.enqueue(status)
        await self.runtime.lead_turn(self.session_id,self.conversation(),"Report the recorded application status.")
        self.assertNotIn(task_id,{item["task_id"] for item in offered})
        coordinator=WorkspaceConversationStorageMutationCoordinator(self.workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            provision_sqlite_conversation_storage(self.workspace,lease=lease)
        transfer=transfer_conversation_storage(self.workspace,source_backend=ConversationStorageBackend.FILESYSTEM,destination_backend=ConversationStorageBackend.SQLITE)
        self.assertIs(transfer.outcome,ConversationStorageTransferOutcome.COMPLETED)
        verified=verify_conversation_storage(self.workspace,source_backend=ConversationStorageBackend.FILESYSTEM,destination_backend=ConversationStorageBackend.SQLITE)
        self.assertIs(verified.outcome,ConversationStorageTransferOutcome.COMPLETED)
        persisted=compose_sqlite_conversation_storage(self.workspace).event_store.read(self.session_id,conversation)
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
        original,_=await self.finish_draft(conversation)
        task_id=original.proposal.authoring_scope.task_id
        pending=await self.proposal_for(conversation,scoped_proposal(PLOT_ARCHITECT,selected=task_id))
        self.backend.enqueue(scoped_proposal(PLOT_ARCHITECT,selected=task_id))
        revised=await self.runtime.decide_lead_step(_decision(pending,c.CollaborationConfirmationDecision.REVISE))
        self.assertEqual(revised.turn.proposal.authoring_scope.task_revision,2)
        def cross_product(value,material):
            offered=material["authoring_tasks"]
            self.assertTrue(any(t["task_revision"]==2 for t in offered))
            self.assertEqual(value["authoring_scope"]["task_revision"],1)
            value["authoring_scope"]["task_revision"]=2
        invalid=await self.proposal_for(self.conversation(),scoped_proposal(PLOT_ARCHITECT,mutate=cross_product))
        self.assertIsNone(invalid.proposal)
        self.assertIs(invalid.next_action,c.CollaborationNextAction.RETRY_LEAD_TURN)
        self.workspace.path(OUTLINE).unlink()
        response=_artifact_outcome(operations=[{"kind":"create","suggested_path":OUTLINE,"new_text":"Candidate for the revised task."}],evidence_status="missing",evidence_ids=lambda call,m:[next(x["evidence_id"] for x in call.payload["core_statuses"] if x["locator"]==OUTLINE)])
        def stale(call,provider):
            result=json.loads(response(call,provider))
            material=_workflow_material(call.payload)
            index=next(i for i,t in enumerate(material["authoring_tasks"]) if t["task_id"]==task_id)
            result["action"]["result"]["authoring_scope"]=_live_scope(call,activity="review",paths=(OUTLINE,),task_index=index)
            return json.dumps(result)
        self.backend.enqueue(stale)
        rejected=await self.runtime.lead_turn(self.session_id,self.conversation(),"Use the earlier draft for this revised candidate.")
        self.assertIsNone(rejected.artifact_proposal)
        self.assertIs(rejected.next_action,c.CollaborationNextAction.RETRY_LEAD_TURN)

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
