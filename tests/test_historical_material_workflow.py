"""Independent live historical-material and fresh-review workflow."""
import asyncio
import json
import story_writing_agents.lead_task_transitions as task
import tempfile
from dataclasses import (
    asdict, replace,
)
from story_writing_agents import (
    StoryRuntime, Workspace, authoring_admission as admission, conversations as c,
)
from story_writing_agents.agents import (
    LEAD_ORCHESTRATOR, PLOT_ARCHITECT,
)
from story_writing_agents.specialist_routing import (
    SpecialistAssignmentMode,
)
from tests.helpers import (
    materialize_pristine_initialized_workspace,
)
from tests.provider_wire_fixture import (
    author_positive_fixture_review_bases, lead_fixture_assignments, lead_fixture_bound_scope, lead_fixture_response, lead_fixture_result, contract_id as wire_contract_id, result as wire_result,
)
from tests.recommendation_fixture import (
    DRAFT, action, recommendation, decision as recommendation_decision,
)
from tests.test_artifact_proposal_orchestration import (
    artifact_review_outcome, _artifact_synthesis_outcome, artifact_evidence_status_id,
)
from tests.test_lead_orchestration import (
    _SubmittedFailure, _compact_scope, lead_confirmation_decision, _discussion_reply, _live_material, lead_specialist_outcome, _submitted_calls, _synthesis_outcome, _workflow_material,
)
from tests.test_specialist_routing_runtime import (
    AgentBackend, OUTLINE, _ADMISSION_BRIEF, _SpecialistRoutingRuntimeCase, _fixture_assessment,
)
from unittest import (
    mock,
)


class HistoricalMaterialWorkflowTests(_SpecialistRoutingRuntimeCase):
    def _set_up_workspace(self):
        temporary = tempfile.TemporaryDirectory(prefix="historical-material-")
        self.addCleanup(temporary.cleanup)
        self.control_workspace = materialize_pristine_initialized_workspace(f"{temporary.name}/story")
        self.session_id = "historical_input_session"
        backend = AgentBackend(self.control_workspace)
        self.addCleanup(backend.close)
        ready = backend.provision_session_resources(self.session_id)
        self.session_resources = ready
        self.workspace = Workspace(ready.identity.worktree_root).require_ready()

    async def test_prior_draft_and_rejected_response_remain_input_only_in_new_task(self):
        from story_writing_agents.conversations import session_context as session_context_module
        self.enterContext(mock.patch.object(session_context_module, "MAX_SESSION_CONTEXT_UTF8_BYTES", 512 * 1024))
        assessment = asdict(_fixture_assessment())
        from tests.test_lead_artifact_application_authority import _application_decision
        before = self.artifact_bytes()
        prior = self.conversation("Historical drafting input remains separate from fresh review proof")
        first, _ = await self.finish_draft(prior)
        original_history = self.history(prior)
        source = c.project_collaboration_history(original_history).offered_draft_sources[-1]
        fresh_scope = replace(first.proposal.authoring_scope, task_revision=2)
        view = self.runtime.inspect_lead_collaboration(self.session_id, prior)
        selected_task = next(item for item in view.available_tasks if item.ref.task_id == fresh_scope.task_id)
        source_conversation = self.conversation("Same-session foreign public message")
        invalid = json.loads(_discussion_reply("Selected rejected whole explanation."))
        invalid["agent_action"]["task_result"]["used_evidence_ids"] = "invalid array"
        self.backend.enqueue(json.dumps(invalid))
        self.backend.enqueue(json.dumps(invalid))
        discussed = await self.runtime.decide_lead_task(task.LeadTaskDecision.for_conversation(
            self.session_id, source_conversation, task.LeadTaskAction.DISCUSS, text="Discuss independently.", ))
        rejected_text = discussed.turn.public_responses[-1]
        self.assertIs(rejected_text.response.acceptance, task.LeadPublicResponseAcceptance.REJECTED)
        self.assertEqual(rejected_text.ref.conversation_id, source_conversation)
        self.assertEqual(c.project_collaboration_history(self.history(source_conversation)).offered_draft_sources, ())
        self.assertEqual(c.project_collaboration_history(self.history(prior)).offered_draft_sources[-1], source)
        self.assertEqual(self.history(prior)[:len(original_history)], original_history)
        original_history = self.history(prior)
        selected_writer = recommendation(message=fresh_scope.interpretation, recommended_action=action(
            kind="prepare_consultation", source_ids=(source.event_id,)),
                authoring_scope=c.authoring_scope_to_dict(fresh_scope),
            authoring_assessment=_fixture_assessment(foundation_ids=("user_input", "selected_material_1")),
        )
        def selected_recommendation(call, provider):
            self._assert_selected_profile(call, (fresh_scope.task_id, 2))
            return selected_writer(call, provider)
        self.backend.enqueue(selected_recommendation)
        selected = await self.runtime.decide_lead_task(task.LeadTaskDecision.from_task( selected_task.ref, task.LeadTaskAction.RESUME,
                text=_ADMISSION_BRIEF + "\nUse the earlier draft with this selected explanation.", material_refs=(rejected_text.ref,), ))
        suggested = selected.turn
        request = suggested.recommendation_request
        self.assertIsNotNone(request, suggested)
        self.assertEqual((request.authoring_scope, request.draft_sources), (fresh_scope, (source,)))
        selected_admission = next(item for item in request.basis.authoring_admission.sources
            if item.source_id == "selected_material_1")
        self.assertEqual((selected_admission.kind, selected_admission.original_authority),
            (admission.AdmissionSourceKind.MATERIAL, "rejected_public_response"))
        prior_input = next(item for item in request.basis.inputs if (item.locator, item.revision_digest) == ("conversation/" + source.event_id, source.content_digest))
        pending_history, pending_calls = self.history(prior), len(self.backend.calls)
        pending_task = next(item for item in self.runtime.inspect_lead_collaboration(self.session_id, prior).available_tasks if item.ref.task_id == fresh_scope.task_id)
        dismissed = await self.runtime.decide_lead_recommendation(recommendation_decision(suggested, c.CollaborationConfirmationDecision.REJECT))
        self.assertEqual((len(self.backend.calls), dismissed.proposal, dismissed.recommendation_request), (pending_calls, None, None))
        rejected_suffix = self.history(prior)[len(pending_history):]
        self.assertEqual(_submitted_calls(rejected_suffix), ())
        self.assertFalse(any(event.kind is c.CollaborationEventKind.ASSIGNMENT_DISPATCH or (type(event.payload) is c.CollaborationStepStatePayload and event.payload.state is c.CollaborationStepState.RUNNING) for event in rejected_suffix))
        self.assertIs(c.project_collaboration_history(self.history(prior)).current_recommendation_state, c.LeadRecommendationState.REJECTED)
        material_view = self.runtime.inspect_lead_collaboration(self.session_id, prior)
        historical_source_task = next(item for item in material_view.available_tasks if item.ref.task_id == fresh_scope.task_id)
        self.assertEqual((historical_source_task.scope, historical_source_task.submitted_call_ids), (pending_task.scope, pending_task.submitted_call_ids))
        old_material = next(item for item in material_view.selectable_materials if item.ref.event_id == source.event_id and item.ref.conversation_id == prior)
        self.assertEqual((old_material.ref.kind, old_material.ref.material_digest, old_material.text),
            (task.LeadMaterialKind.DRAFT, source.content_digest.removeprefix("sha256:"), next(event.payload.message for event in original_history if event.event_id == source.event_id)))
        historical_task = task.LeadTaskDecision.from_task(historical_source_task.ref, task.LeadTaskAction.NEW_TASK,
            text=_ADMISSION_BRIEF + "\nUse the selected material as historical input only.", material_refs=(rejected_text.ref, old_material.ref))
        def historical_recommendation(call, provider):
            receipt = self._transition_receipt(historical_task).payload.receipt
            self.assertNotEqual(receipt.target_task_id, historical_source_task.ref.task_id)
            self.assertEqual(receipt.target_task_revision, 1)
            issued_scope = replace(first.proposal.authoring_scope, task_id=receipt.target_task_id, task_revision=receipt.target_task_revision)
            self._assert_selected_profile(call, (issued_scope.task_id, issued_scope.task_revision))
            return recommendation(message=issued_scope.interpretation, recommended_action=action(kind="prepare_consultation", source_ids=("user_input", "selected_material_1", "selected_material_2")),
                authoring_scope=c.authoring_scope_to_dict(issued_scope), authoring_assessment=_fixture_assessment(foundation_ids=("user_input", "selected_material_1", "selected_material_2")))(call, provider)
        self.backend.enqueue(historical_recommendation)
        selected = await self.runtime.decide_lead_task(historical_task)
        suggested, request = selected.turn, selected.turn.recommendation_request
        self.assertIsNotNone(request, suggested)
        fresh_scope = request.authoring_scope
        historical_receipt = self._transition_receipt(historical_task).payload.receipt
        self.assertEqual((fresh_scope, request.draft_sources), (replace(first.proposal.authoring_scope, task_id=historical_receipt.target_task_id, task_revision=historical_receipt.target_task_revision), ()))
        selected_admission = next(item for item in request.basis.authoring_admission.sources if item.source_id == "selected_material_1")
        self.assertEqual((selected_admission.kind, selected_admission.original_authority), (admission.AdmissionSourceKind.MATERIAL, "rejected_public_response"))
        def prepare_historical(call, provider):
            material = _workflow_material(provider.callback_payload(call))
            self.assertEqual(material["structured_recommendation_execution"]["request"]["authoring_scope"],
                {key: value for key, value in c.authoring_scope_to_dict(fresh_scope).items()
                 if key not in {"task_id", "task_revision"}})
            native = wire_result({"authoring_assessment": asdict(request.basis.authoring_admission.assessment), "kind": "proposal", "message": request.purpose,
                "authoring_scope": _compact_scope(c.authoring_scope_to_dict(fresh_scope)),
                "proposal": {"shared_premises": [], "assignments": [{"agent_id": PLOT_ARCHITECT, "mode": "draft", "instruction": request.instruction,
                    "input_ids": [item.input_id for item in request.basis.inputs]}]}}, wire_contract_id(call.payload))
            from tests.provider_wire_fixture import lead_fixture_assessment
            choices = dict(call.contract.semantic_index.policy.choices)
            native['task_scope'] = lead_fixture_bound_scope(
                call, native.pop('writing_task_scope'), record=True)
            native['authoring_assessment'] = lead_fixture_assessment(
                native['authoring_assessment'], source_choices=choices['input_ids'] + choices['historical_user_source_ids'])
            proposal = native['specialist_consultation_proposal']
            allocation = lead_fixture_assignments(
                author_positive_fixture_review_bases(
                    proposal['assignments'],
                    candidate_choices=choices['review_candidate_ids']),
                input_choices=choices['allocation_input_ids'],
                review_choices=choices['review_candidate_ids'],
                anchors={PLOT_ARCHITECT: request.basis.inputs[0].input_id},
                recipients={source: PLOT_ARCHITECT for source in choices['required_input_ids']})
            proposal.update(allocation)
            return lead_fixture_response(call, lead_fixture_result(native), family=0)
        self.backend.enqueue(prepare_historical)
        prepared = await self.runtime.decide_lead_recommendation(recommendation_decision(suggested))
        self.assertIs(prepared.kind, c.LeadOutcomeKind.PROPOSAL, (prepared.message[:1200], prepared.state, prepared.validation_details[:6]))
        self.assertEqual(prepared.proposal.authoring_scope, fresh_scope)
        self.assertEqual(prepared.proposal.budget.rounds_spent, 0)
        self.assertFalse(any(event.kind is c.CollaborationEventKind.ASSIGNMENT_DISPATCH for event in self.history(prior)[len(original_history):]))
        prepared_history = self.history(prior)
        self.assertIsNotNone(prepared.proposal.document_node, prepared)
        self.assertEqual((prepared.proposal.document_node.task_ref.task_id, prepared.proposal.document_node.task_ref.task_revision), (fresh_scope.task_id, fresh_scope.task_revision))
        self.assertEqual((prepared.proposal.budget.physical_calls_spent, prepared.proposal.budget.rounds_spent), c.document_lineage_cost(prepared_history, prepared.proposal.document_node, current_step_id=prepared.proposal.step_id)[:2])
        preserved_task = next(item for item in self.runtime.inspect_lead_collaboration(self.session_id, prior).available_tasks if item.ref == historical_source_task.ref)
        self.assertEqual(preserved_task.submitted_call_ids, historical_source_task.submitted_call_ids)
        self.assertEqual(len(_submitted_calls(prepared_history[len(original_history):])), 3)
        self.assertEqual(len(_submitted_calls(prepared_history)), len(_submitted_calls(original_history)) + 3)
        draft_capture = next(event for event in prepared_history if event.event_id in historical_receipt.material_capture_event_ids and event.payload.capture.source == old_material.ref)
        historical_input = next(item for item in prepared.proposal.basis.inputs if item.locator == "conversation/" + draft_capture.event_id)
        self.assertEqual((draft_capture.payload.capture.selected_text, historical_input.revision_digest), (old_material.text, prior_input.revision_digest))
        selected_input = next(item for item in prepared.proposal.basis.inputs if item.input_id == "selected_material_1")
        transition = next(event.payload.receipt for event in self.history(prior)
                          if event.event_id == selected.transition_event_id)
        capture = next(event for event in self.history(prior) if event.event_id in transition.material_capture_event_ids and event.payload.capture.source == rejected_text.ref)
        self.assertEqual((capture.payload.capture.source, capture.payload.capture.selected_text),
                         (rejected_text.ref, "Selected rejected whole explanation."))
        self.assertEqual(tuple((event.payload.capture.origin_event_id, event.payload.capture.target_task_id, event.payload.capture.target_task_revision)
            for event in (capture, draft_capture)), ((historical_task.request_id, fresh_scope.task_id, fresh_scope.task_revision),) * 2)
        front = next(_workflow_material(call.payload) for agent, call in reversed(self.backend.call_records)
                     if agent == LEAD_ORCHESTRATOR and _workflow_material(call.payload).get("selected_task_materials"))
        self.assertEqual(
            [(row["capture_event_id"], row["input_id"], row["source"]["session_id"], row["source"]["conversation_id"], row["source"]["event_id"], row["source"]["material_digest"]) for row in front["selected_task_materials"]],
            [(event.event_id, item.input_id, ref.session_id, ref.conversation_id, ref.event_id, ref.material_digest)
             for event, item, ref in ((capture, selected_input, rejected_text.ref), (draft_capture, historical_input, old_material.ref))])
        self.assertEqual((selected_input.kind, selected_input.locator, selected_input.revision_digest),
                         (c.CollaborationInputKind.CONVERSATION_EVENT, "conversation/" + capture.event_id,
                          "sha256:" + rejected_text.ref.material_digest))
        self.assertEqual((selected_admission.source_identity, selected_admission.revision_digest, selected_admission.utf8_bytes),
            (selected_input.locator, selected_input.revision_digest, len(capture.payload.capture.selected_text.encode("utf-8"))))
        self.assertEqual(next(item for item in prepared.proposal.basis.authoring_admission.sources
            if item.source_id == selected_input.input_id), selected_admission)
        self.assertIn(selected_input.input_id, prepared.proposal.assignments[0].input_ids)
        self.assertEqual((historical_input.kind, historical_input.locator, historical_input.revision_digest), (c.CollaborationInputKind.CONVERSATION_EVENT, "conversation/" + draft_capture.event_id, "sha256:" + old_material.ref.material_digest))
        self.backend.enqueue_for(PLOT_ARCHITECT, lead_specialist_outcome("drafted", "A fresh chronology follows the selected historical material."))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome())
        drafted = await self.runtime.decide_lead_step(lead_confirmation_decision(prepared, c.CollaborationConfirmationDecision.CONFIRM))
        self.assertIs(drafted.turn.state, c.CollaborationStepState.COMPLETED, (drafted.turn.message[:1200], drafted.turn.validation_details[:6]))
        delivered_historical = next(item for agent, call in reversed(self.backend.call_records) if agent == PLOT_ARCHITECT for item in _workflow_material(call.payload)["inputs"] if (item["input_id"], item["locator"], item["revision_digest"]) == (historical_input.input_id, historical_input.locator, historical_input.revision_digest))
        self.assertEqual((delivered_historical["text"], delivered_historical["selected_material_source"]), (old_material.text, task.task_value_to_dict(old_material.ref)))
        delivered = next(item for agent, call in reversed(self.backend.call_records) if agent == PLOT_ARCHITECT
                         for item in _workflow_material(call.payload)["inputs"] if item["input_id"] == selected_input.input_id)
        self.assertEqual((delivered["text"], delivered["selected_material_source"]),
                         (capture.payload.capture.selected_text, task.task_value_to_dict(capture.payload.capture.source)))
        current = c.project_collaboration_history(self.history(prior)).offered_draft_sources[-1]
        self.assertNotEqual(current.event_id, source.event_id)
        self.assertEqual(self.history(prior)[:len(original_history)], original_history)
        fresh_ready = await self.proposal_for(prior, recommendation(recommended_action=action(source_ids=(
            current.event_id,)), authoring_scope=c.authoring_scope_to_dict(fresh_scope),
            mutate=lambda value: value.update(authoring_assessment=assessment),
        ), selection_group='prepare.artifact')
        self.assertIsNotNone(fresh_ready.recommendation_request, fresh_ready)
        def integrate_fresh(call, provider):
            return artifact_review_outcome(
                operations=lambda call, material, *, input_payload: [{"kind": "replace",
                    "evidence_id": artifact_evidence_status_id(input_payload, OUTLINE), "new_text": DRAFT}],
                evidence_status="incomplete",
                evidence_ids=lambda call, material, *, input_payload: [
                    input_payload["core_document_statuses"][0]["evidence_id"]],
                objective_intent={"action": "start", "target_paths": {OUTLINE: True},
                    "next_step": "prepare"},
                authoring_scope=lambda call: _live_material(call)[
                    "structured_recommendation_execution"]["request"]["authoring_scope"],
                authoring_assessment=assessment,
            )(call, provider)
        self.backend.enqueue(integrate_fresh)
        fresh_review = await self.runtime.decide_lead_recommendation(recommendation_decision(fresh_ready))
        self.assertEqual(None if fresh_review.proposal is None else (fresh_review.proposal.participants, fresh_review.proposal.assignments[0].mode), ((PLOT_ARCHITECT,), SpecialistAssignmentMode.REVIEW), fresh_review)
        self.backend.enqueue_for(PLOT_ARCHITECT, lead_specialist_outcome("supports"))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _artifact_synthesis_outcome())
        final = await self.runtime.decide_lead_step(lead_confirmation_decision(fresh_review, c.CollaborationConfirmationDecision.CONFIRM))
        self.assertIsNotNone(final.turn.artifact_proposal, final)
        self.assertIs(final.turn.artifact_proposal.state, c.ArtifactProposalState.REVIEWED, final.turn)
        self.assertEqual(self.artifact_bytes(), before)
        pending_view = self.runtime.inspect_lead_collaboration(self.session_id, prior)
        protected = next(item for item in pending_view.available_tasks if item.ref.task_id == fresh_scope.task_id)
        prefix, spent = self.history(prior), len(_submitted_calls(self.history(prior)))
        with self.assertRaises(ValueError):
            await self.runtime.decide_lead_task(task.LeadTaskDecision.from_task(protected.ref, task.LeadTaskAction.HOLD))
        self.assertEqual(self.history(prior), prefix)
        def readonly_discussion(call, _provider):
            self.assertIsNone(call.contract.schema_group_phase)
            return lead_fixture_response(call, lead_fixture_result({"response_type": "answer",
                "response_text": "This answer grants no artifact action.", "used_evidence_ids": {}}), family=0)
        self.backend.enqueue(readonly_discussion)
        readonly = await self.runtime.decide_lead_task(task.LeadTaskDecision.from_task(
            protected.ref, task.LeadTaskAction.DISCUSS, text="Discuss without resolving Apply.", ))
        after_discussion = self.runtime.inspect_lead_collaboration(self.session_id, prior)
        self.assertEqual(after_discussion.current_artifact_application_request, pending_view.current_artifact_application_request)
        self.assertEqual((after_discussion.rounds_spent, after_discussion.physical_calls_spent),
                         (pending_view.rounds_spent, pending_view.physical_calls_spent))
        self.assertEqual(len(_submitted_calls(self.history(prior))), spent + 1)
        self.assertIsNone(readonly.turn.proposal)
        self.assertEqual((readonly.turn.next_action, readonly.turn.artifact_application_request),
                         (c.CollaborationNextAction.SUBMIT_APPLICATION_DECISION,
                          pending_view.current_artifact_application_request))
        self.assertEqual(self.artifact_bytes(), before)
        for failure in (_SubmittedFailure(RuntimeError("known discussion failure")),
                        _SubmittedFailure(asyncio.CancelledError("known discussion cancellation"))):
            prefix = self.history(prior)
            self.backend.enqueue(failure)
            decision = task.LeadTaskDecision.from_task(protected.ref, task.LeadTaskAction.DISCUSS, text="Inspect without changing the pending Apply.")
            if isinstance(failure.failure, asyncio.CancelledError):
                with self.assertRaises(asyncio.CancelledError):
                    await self.runtime.decide_lead_task(decision)
            else:
                await self.runtime.decide_lead_task(decision)
            restored_pending = StoryRuntime(self.control_workspace, self.backend).inspect_lead_collaboration(
                self.session_id, prior,)
            self.assertEqual(restored_pending.current_artifact_application_request, pending_view.current_artifact_application_request)
            self.assertEqual(len(_submitted_calls(self.history(prior)[len(prefix):])), 1)
            self.assertEqual(self.history(prior)[:len(prefix)], prefix)
            self.assertEqual(self.artifact_bytes(), before)
        await self.runtime.decide_artifact_application(_application_decision(final.turn, c.ArtifactApplicationDecisionKind.REJECT))
        captured = self.history(prior)
        from story_writing_agents.conversation_storage.archive import serialize_conversation_archive, deserialize_conversation_archive
        from tests.conversation_archive_fixture import archive_conversation, archive_from_conversations
        archive = archive_from_conversations(archive_conversation(session_id=self.session_id, conversation_id=prior, events=captured), archive_conversation(session_id=self.session_id,
            conversation_id=source_conversation,
            events=self.history(source_conversation)))
        self.assertEqual(deserialize_conversation_archive(serialize_conversation_archive(archive)), archive)
        from story_writing_agents.conversation_storage import ConversationStorageBackend, WorkspaceConversationStorageMutationCoordinator
        from story_writing_agents.conversation_storage.migration import transfer_conversation_storage
        from story_writing_agents.conversation_storage.sqlite import provision_sqlite_conversation_storage, compose_sqlite_conversation_storage
        with WorkspaceConversationStorageMutationCoordinator(self.control_workspace).borrow(timeout_seconds=2.0) as lease:
            provision_sqlite_conversation_storage(self.control_workspace, lease=lease)
        transfer_conversation_storage(self.control_workspace, source_backend=ConversationStorageBackend.FILESYSTEM, destination_backend=ConversationStorageBackend.SQLITE)
        restored = compose_sqlite_conversation_storage(self.control_workspace).event_store.read(self.session_id, prior)
        self.assertEqual(restored, captured)
        self.assertEqual(next(event.payload.basis.authoring_admission for event in restored
            if event.event_id == request.request_event_id), request.basis.authoring_admission)
        self.assertEqual(c.project_collaboration_history(restored), c.project_collaboration_history(captured))
        restarted = StoryRuntime(self.control_workspace, backend=self.backend)
        self.assertEqual(restarted.conversation_storage_services.event_store.read(self.session_id, prior), captured)
        # Recorded fake-transport sizes verify guards, not actual model capacity.
        self._assert_recorded_provider_size_bounds(restarted.conversation_storage_services)
