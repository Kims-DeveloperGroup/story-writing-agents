"""Closed recommendation records, exact provenance and historical compatibility."""

from __future__ import annotations

import dataclasses
import json
import pickle
from dataclasses import replace

from story_writing_agents import authoring_admission as admission, conversations as c
from tests.recommendation_fixture import (
    DRAFT, OUTLINE, RecommendationCase, action, decision, digest,
    preparation, recommendation, rewrite_history,
    task_assessment, )
from story_writing_agents.lead_schema_groups import LeadGroupPhase
from tests.provider_wire_fixture import selection_plan
from tests import test_lead_orchestration as lead


class RecommendationActionContractTests(RecommendationCase):
    async def test_action_values_and_decisions_are_closed_exact_and_immutable(self):
        self.workspace.path(OUTLINE).write_text("Outline first line.\nOutline second line.\n", encoding="utf-8")
        conversation_id, turn = await self.recommend()
        request = turn.recommendation_request
        choice = decision(turn)
        await self.confirm(turn)
        projection = self.projection(conversation_id)
        semantic = c.LeadRecommendedAction(c.LeadRecommendationActionKind.PREPARE_ARTIFACT,
            "Purpose", "Instruction", "Expected result", ("current_response",), (OUTLINE,))
        expected = [
            (semantic, "kind purpose instruction expected_result source_ids target_paths specialist_ids objective_id"),
            (request, "request_event_id request_id action_id action_revision kind purpose instruction expected_result target_paths specialist_ids objective_id source_recommendation_event_id source_recommendation_digest draft_sources basis source_fingerprint physical_calls_spent max_preparation_physical_calls tail_sequence user_impact source_proposal_id source_proposal_revision source_proposal_digest source_execution_attempt_id authoring_scope action_digest"),
            (choice, "session_id conversation_id request_event_id request_tail_sequence request_id action_id action_revision action_digest kind revision_request"),
            (projection.current_recommendation_decision, "decision"),
            (projection.current_recommendation_start, "start_event_id request_event_id decision_event_id request_id action_id action_revision action_digest execution_attempt_id"),
            (projection.current_recommendation_result, "request_event_id request_id action_id action_revision action_digest state message decision_event_id start_event_id lead_result_event_id superseding_user_event_id"),
        ]
        self.assertEqual(tuple(item.value for item in c.LeadRecommendationActionKind),
                         ("prepare_artifact", "prepare_consultation"))
        self.assertEqual(tuple(item.value for item in c.LeadRecommendationState),
                         ("pending", "running", "completed", "rejected", "superseded", "stale", "failed", "interrupted"))
        for value, names in expected:
            with self.subTest(value=type(value).__name__):
                self.assertEqual(tuple(f.name for f in dataclasses.fields(value)), tuple(names.split()))
                self.assertFalse(hasattr(value, "__dict__"))
                self.assertEqual(pickle.loads(pickle.dumps(value)), value)
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    setattr(value, names.split()[0], None)
        for changes in ({"kind": "prepare_artifact"}, {"source_ids": ()},
                        {"source_ids": ("current_response",) * 2}, {"target_paths": ()},
                        {"target_paths": ("../escape.md",)}, {"specialist_ids": ("plot_architect",)}):
            with self.subTest(changes=changes), self.assertRaises((TypeError, ValueError)):
                replace(semantic, **changes)
        for changes in ({"kind": "confirm"}, {"action_revision": True},
                        {"request_tail_sequence": 0}, {"action_digest": "unbound"},
                        {"revision_request": "not permitted"},
                        {"kind": c.CollaborationConfirmationDecision.REVISE, "revision_request": " "}):
            with self.subTest(changes=changes), self.assertRaises((TypeError, ValueError)):
                replace(choice, **changes)
        for changes in ({"source_proposal_id": "partial"}, {"source_execution_attempt_id": "orphan"},
                        {"max_preparation_physical_calls": 12}):
            with self.subTest(changes=changes), self.assertRaises((TypeError, ValueError)):
                replace(request, **changes)
        self.assertNotEqual(replace(request, instruction="Different instruction").action_digest, request.action_digest)

    async def test_scoped_request_digest_and_legacy_absent_scope_remain_distinct(self):
        conversation_id, turn = await self.recommend()
        request = turn.recommendation_request
        event = self.history(conversation_id)[-1]
        scoped_wire = c.encode_collaboration_event(event)
        self.assertEqual(c.decode_collaboration_event(scoped_wire), event)
        self.assertTrue(next(field for field in dataclasses.fields(request)
                             if field.name == "authoring_scope").kw_only)
        changed_scope = replace(request.authoring_scope, interpretation="Another concrete candidate review interpretation.")
        self.assertRaisesRegex(ValueError, "changed its exact task or scope", replace, request, authoring_scope=changed_scope)
        original_admission = request.basis.authoring_admission
        changed_admission = admission.qualify_authoring_assessment( original_admission.assessment, sources=original_admission.sources,
            request_digest=original_admission.request_digest, task_id=changed_scope.task_id, task_revision=changed_scope.task_revision,
            scope_digest=admission.admission_digest(c.authoring_scope_to_dict(changed_scope)), )
        changed = replace(request, authoring_scope=changed_scope, basis=replace(request.basis, authoring_admission=changed_admission))
        self.assertNotEqual(changed.action_digest, request.action_digest)
        with self.assertRaises(ValueError):
            replace(request, authoring_scope=None)
        missing_admission = replace(request.basis, authoring_admission=None)
        self.assertRaisesRegex(ValueError, "qualified authoring admission", replace, request, basis=missing_admission)
        unqualified_event = json.loads(scoped_wire)
        unqualified_event["payload"]["basis"].pop("authoring_admission")
        self.assertRaisesRegex(ValueError, "qualified authoring admission", c.decode_collaboration_event,
            json.dumps(unqualified_event, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8", errors="strict"))
        current_draft = event.to_draft()
        current_wire = c.encode_collaboration_event_draft(current_draft)
        self.assertIn("authoring_scope", json.loads(current_wire)["payload"])
        self.assertEqual(c.encode_collaboration_event_draft(c.decode_collaboration_event_draft(current_wire)), current_wire)
        unqualified_draft = json.loads(current_wire)
        unqualified_draft["payload"]["basis"].pop("authoring_admission")
        self.assertRaisesRegex(ValueError, "qualified authoring admission", c.decode_collaboration_event_draft,
            json.dumps(unqualified_draft, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8", errors="strict"))
        self.assertEqual((request.authoring_scope.schema_version, request.basis.schema_version), (3, 2))
        self.assertEqual(replace(request).action_digest, request.action_digest)
        self.assertEqual(self.history(conversation_id)[-1], event)

    async def test_current_response_alias_binds_exact_whole_message_before_action(self):
        message = "초안 e\u0301\r\n" + DRAFT + "\n"
        conversation_id, turn = await self.recommend(message=message)
        events = self.history(conversation_id)
        request = turn.recommendation_request
        source = next(e for e in events if e.event_id == request.source_recommendation_event_id)
        self.assertEqual(source.payload.message, message)
        self.assertEqual(request.source_recommendation_digest, digest(message))
        self.assertNotEqual(request.source_recommendation_digest, digest(message.replace("\r\n", "\n")))
        self.assertEqual(request.draft_sources, (c.ArtifactDraftSource(conversation_id, source.event_id, digest(message)),))
        self.assertEqual((source.batch_id, source.sequence + 1), (events[-1].batch_id, request.tail_sequence))
        self.assertEqual(request.request_event_id, events[-1].event_id)
        self.assertEqual(request.basis.inputs[0].input_id, source.event_id)
        self.assertNotIn(b"current_response", c.encode_collaboration_event(events[-1]))
        self.assertEqual((request.source_proposal_id, request.source_proposal_revision,
                          request.source_proposal_digest, request.source_execution_attempt_id), (None,) * 4)
        # Ordered offered sources remain whole-message snapshots, including the new reply.
        self.backend.enqueue(recommendation(message="Second whole reply", recommended_action=action( source_ids=(source.event_id, "current_response")), authoring_scope=c.authoring_scope_to_dict(request.authoring_scope),
            authoring_assessment=task_assessment(foundation_ids=("user_input",), actions=('bounded review judgments', 'separate proposal preparation'))), control_plan=selection_plan("prepare.artifact"))
        second = await self.runtime.lead_turn(self.session_id, conversation_id, ("Finished candidate:\n" + message + "\nYou may make bounded review judgments and prepare a separate proposal; preserve supplied facts.\n" + "Offer another plan."))
        self.assertEqual(second.recommendation_request.authoring_scope, request.authoring_scope)
        self.assertEqual(tuple(s.content_digest for s in second.recommendation_request.draft_sources),
                         (digest("Second whole reply"), digest(message)))
        for sources, objective_id, bad_route in ((("foreign_event",), None, False),
            (("current_response", "current_response"), None, False), (("current_response",),
            "foreign_objective", False), (("current_response",), None, True)):
            cid = self.conversation("Rejected source aliases")
            bad = recommendation(recommended_action=action(objective_id=objective_id),
                authoring_assessment=task_assessment(foundation_ids=("user_input",), actions=('bounded review judgments', 'separate proposal preparation')))
            if sources != ("current_response",):
                def bad(call, provider, encode=bad, sources=sources):
                    native = json.loads(encode(call, provider))
                    details = native["agent_action"]["task_result"]["response_details"]
                    selected = json.dumps(details["proposal_drafting_request"]["source_ids"])
                    member = '"current_response": true'
                    self.assertEqual(selected.count(member), 1)
                    replacement = ('"foreign_event": true' if sources == ("foreign_event",)
                                   else member + "," + member)
                    text = json.dumps(native)
                    field = '"source_ids": ' + selected
                    self.assertEqual(text.count(field), 1)
                    return text.replace(field, '"source_ids": ' + selected.replace(
                        member, replacement, 1), 1)
            if bad_route:
                bad = recommendation(recommended_action=action(source_ids=sources, kind="prepare_consultation", specialist_ids=("plot_architect",)),
                    authoring_scope=lambda call: lead._live_scope(
                        call, activity="review", paths=(OUTLINE,), domains=("plot_structure",)),
                    authoring_assessment=task_assessment(foundation_ids=("user_input",), actions=('bounded review judgments', 'separate proposal preparation')))
                def bad(call, provider, encode=bad):
                    native = json.loads(encode(call, provider))
                    selection = native['agent_action']['task_result']['response_details'][
                        'proposal_drafting_request']['specialist_ids']
                    selection['left']['plot_architect'] = False
                    selection['right']['left']['character_custodian'] = True
                    return json.dumps(native)
            self.backend.enqueue(bad, control_plan=selection_plan(
                "prepare.consultation.authoring" if bad_route else "prepare.artifact"))
            self.backend.enqueue(bad)
            result = await self.runtime.lead_turn(self.session_id, cid, ("Finished candidate:\n" + message + "\nYou may make bounded review judgments and prepare a separate proposal; preserve supplied facts.\n" + "Reject undisclosed aliases."))
            self.assertIsNone(result.recommendation_request)
            self.assertFalse(self.payload_events(cid, c.LeadRecommendationRequestPayload))
            if bad_route:
                issue = ("$.agent_action.task_result.response_details.proposal_drafting_request."
                         "specialist_ids.left.plot_architect: expected one of [true] "
                         "(boolean), got boolean")
                self.assertEqual(tuple(detail.partition("\n")[2] for detail in result.validation_details),
                                 (issue, issue))
            self.assert_no_downstream(cid)

    async def test_confirm_origin_v2_and_continuing_v1_preserve_exact_wire_facts(self):
        def checked(offer):
            producer = preparation(offer)
            def response(call, provider):
                raw = lead.wire_response(producer(call, provider), lead.wire_contract_id(call.payload))
                self.assertEqual(lead.diagnose_json_text(raw, call.schema), ())
                for change in ("absent", "step", "identity", "scope"):
                    bad = json.loads(raw)
                    body = bad["agent_action"]["task_result"]["response_details"]
                    intent = body["artifact_change_proposal"]["document_change_request_update"]
                    if change == "absent":
                        del body["artifact_change_proposal"]["document_change_request_update"]
                    elif change == "step":
                        intent["requested_workflow_step"] = "consult_specialists"
                    elif change == "scope":
                        body["task_scope"]["task"] = {"id": "foreign_task", "revision": 1}
                    elif intent["update_type"] == "start_request":
                        intent["target_artifact_paths"] = {"core_documents/character_sheets.md": True}
                    else:
                        intent["document_change_request_id"] = "foreign_objective"
                    self.assertTrue(lead.diagnose_json_text(json.dumps(bad), call.schema), change)
                return raw
            return response
        self.workspace.path(OUTLINE).write_text("Outline first line.\nOutline second line.\n", encoding="utf-8")
        conversation_id, turn = await self.recommend()
        original = tuple(c.encode_collaboration_event(e) for e in self.history(conversation_id))
        self.enqueue_preparation(turn, answer=checked(turn))
        confirmed = await self.runtime.decide_lead_recommendation(decision(turn))
        objective = self.objective(conversation_id)
        self.assertIs(type(objective.facts), c.ArtifactObjectiveFactsV2)
        self.assertFalse(hasattr(objective.facts, "origin_user_event_id"))
        self.assertEqual(objective.facts.origin_recommendation_request_event_id, turn.recommendation_request.request_event_id)
        event = self.payload_events(conversation_id, c.LeadRecommendationDecisionPayload)[0]
        self.assertEqual(objective.facts.origin_recommendation_decision_event_id, event.event_id)
        self.assertEqual(objective.facts.request, turn.recommendation_request.instruction)
        self.assertEqual(objective.draft_sources, turn.recommendation_request.draft_sources)
        self.assertIs(objective.state, c.ArtifactObjectiveState.OPEN)
        self.assertEqual(tuple(c.encode_collaboration_event(e) for e in self.history(conversation_id)[:len(original)]), original)
        self.assertIsNotNone(confirmed.confirmation_request_id)
        objective_event = self.payload_events(conversation_id, c.ArtifactObjectivePayloadV2)[0]
        wire = c.encode_collaboration_event(objective_event)
        self.assertIn(b'"payload_type":"artifact_objective_v2"', wire)
        self.assertEqual(c.decode_collaboration_event(wire), objective_event)
        other, _ = await self.start()
        before = self.objective(other).facts
        old_event = self.payload_events(other, c.ArtifactObjectivePayload)[0]
        old_wire = c.encode_collaboration_event(old_event)
        _, offer = await self.recommend(other, recommended_action=action(objective_id=before.objective_id))
        self.enqueue_preparation(offer, answer=checked(offer))
        continued = await self.runtime.decide_lead_recommendation(decision(offer))
        self.assertIsNotNone(continued.confirmation_request_id, continued)
        self.assertIsNotNone(continued.turn.artifact_proposal, continued)
        self.assertEqual(self.objective(other).facts, before)
        self.assertIs(type(self.objective(other).facts), c.ArtifactObjectiveFacts)
        self.assertEqual(c.encode_collaboration_event(self.history(other)[old_event.sequence - 1]), old_wire)
        self.assertFalse(self.payload_events(other, c.ArtifactObjectivePayloadV2))

    async def test_history_rejects_foreign_origin_tail_and_nonatomic_action_batches(self):
        self.workspace.path(OUTLINE).write_text("Outline first line.\nOutline second line.\n", encoding="utf-8")
        conversation_id, turn = await self.recommend()
        await self.confirm(turn)
        events = self.history(conversation_id)
        requests = self.payload_events(conversation_id, c.LeadRecommendationRequestPayload)
        request_event = requests[0]
        start = self.payload_events(conversation_id, c.LeadRecommendationStartedPayload)[0]
        result = self.payload_events(conversation_id, c.LeadRecommendationResultPayload)[0]
        objective = self.payload_events(conversation_id, c.ArtifactObjectivePayloadV2)[0]
        mutations = (
            (request_event, replace(request_event.payload, source_recommendation_event_id="foreign")),
            (request_event, replace(request_event.payload, tail_sequence=request_event.sequence + 1)),
            (start, replace(start.payload, decision_event_id="foreign")),
            (result, replace(result.payload, start_event_id="foreign")),
            (result, replace(result.payload, lead_result_event_id=request_event.event_id)),
            (objective, replace(objective.payload, facts=replace(objective.payload.facts, origin_recommendation_decision_event_id="foreign"))),
        )
        for event, payload in mutations:
            with self.subTest(event=event.kind, payload=payload), self.assertRaises(ValueError):
                c.project_collaboration_history(rewrite_history(events, event.event_id, payload=payload))
        # Re-signing cannot turn either half of the required atomic publication into valid history.
        for event in (request_event, start, result):
            prefix = tuple(e for e in events if e.sequence < event.sequence - event.batch_ordinal)
            c.project_collaboration_history(prefix)
            candidate = c.CollaborationEventBatch("split_batch", len(prefix), (event.to_draft(),))
            with self.subTest(split=event.kind), self.assertRaises(c.CollaborationAppendError) as caught:
                c.validate_collaboration_candidate_batch(prefix, candidate)
            self.assertIs(caught.exception.failure, c.CollaborationAppendFailure.INVALID_TRANSITION)
        for event in (request_event, start, result):
            with self.subTest(direction=event.kind), self.assertRaises(ValueError):
                replace(event.to_draft(), sender=c.CollaborationActor(c.CollaborationActorKind.USER))

    async def test_recommendation_and_objective_v2_round_trip_through_both_stores_and_archive(self):
        from story_writing_agents.conversation_storage import ConversationStorageBackend, ConversationStorageCapability, WorkspaceConversationStorageMutationCoordinator
        from story_writing_agents.conversation_storage.migration import transfer_conversation_storage, ConversationStorageTransferOutcome
        from story_writing_agents.conversation_storage.sqlite import provision_sqlite_conversation_storage, compose_sqlite_conversation_storage
        from story_writing_agents.conversation_storage.archive import serialize_conversation_archive, deserialize_conversation_archive
        from tests.conversation_archive_fixture import archive_conversation, archive_from_conversations
        self.workspace.path(OUTLINE).write_text("Outline first line.\nOutline second line.\n", encoding="utf-8")
        conversation_id, turn = await self.recommend()
        pending = self.history(conversation_id)
        await self.confirm(turn)
        complete = self.history(conversation_id)
        for history in (pending, complete):
            for event in history:
                wire = c.encode_collaboration_event(event)
                self.assertEqual(c.encode_collaboration_event(c.decode_collaboration_event(wire)), wire)
            archive = archive_from_conversations(archive_conversation(session_id=self.session_id,
                conversation_id=conversation_id, events=history))
            wire = serialize_conversation_archive(archive)
            self.assertEqual(serialize_conversation_archive(deserialize_conversation_archive(wire)), wire)
        coordinator = WorkspaceConversationStorageMutationCoordinator(self.control_workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            provisioned = provision_sqlite_conversation_storage(self.control_workspace, lease=lease)
        self.assertIs(provisioned.capability, ConversationStorageCapability.READY)
        transfer = transfer_conversation_storage(self.control_workspace, source_backend=ConversationStorageBackend.FILESYSTEM,
            destination_backend=ConversationStorageBackend.SQLITE)
        self.assertIs(transfer.outcome, ConversationStorageTransferOutcome.COMPLETED)
        restored = compose_sqlite_conversation_storage(self.control_workspace).event_store.read(self.session_id, conversation_id)
        self.assertEqual(restored, complete)
        self.assertEqual(c.project_collaboration_history(restored), c.project_collaboration_history(complete))
        self.assertNotIn("capability", repr(c.project_collaboration_history(restored)))

    async def test_readable_and_session_context_expose_exact_origin_without_private_authority(self):
        from story_writing_agents.agent_context.sources import SessionHistorySourceAdapter
        conversation_id, turn = await self.recommend()
        request = turn.recommendation_request
        before = self.history(conversation_id)
        readable = c.project_collaboration_readable_history(before)
        item = next(item for item in readable if type(item.payload) is c.LeadRecommendationRequestPayload)
        fields = {field.key: field.text for field in item.fields}
        self.assertEqual(fields, {
            c.CollaborationReadableFieldKey.LEAD_RECOMMENDATION_PURPOSE: request.purpose,
            c.CollaborationReadableFieldKey.LEAD_RECOMMENDATION_INSTRUCTION: request.instruction,
            c.CollaborationReadableFieldKey.LEAD_RECOMMENDATION_RESULT: request.expected_result,
            c.CollaborationReadableFieldKey.LEAD_RECOMMENDATION_IMPACT: request.user_impact,
            c.CollaborationReadableFieldKey.LEAD_RECOMMENDATION_SOURCES: "\n".join(i.locator for i in request.basis.inputs),
            c.CollaborationReadableFieldKey.LEAD_RECOMMENDATION_TARGETS: OUTLINE,
            c.CollaborationReadableFieldKey.LEAD_RECOMMENDATION_COST: f"{request.physical_calls_spent} spent; at most {request.max_preparation_physical_calls} preparation calls",
        })
        services = self.runtime.conversation_storage_services
        delivery = SessionHistorySourceAdapter(services.catalog, services.event_store, self.session_id).capture().delivery()
        encoded = json.dumps(delivery, default=str)
        self.assertIn(request.request_event_id, encoded)
        self.assertIn(request.instruction, encoded)
        self.assertNotIn("_RecommendationCapability", encoded)
        self.assertEqual(self.history(conversation_id), before)
        # Decisions/revision and running/terminal states have their own readable fields.
        self.backend.enqueue(lambda call, _: lead.lead_fixture_response(call, {
            'response_type': 'clarification_question', 'response_text': 'How do the four roles cooperate?',
            'response_details': {'used_evidence_ids': {}}}, family=0))
        revised = await self.runtime.decide_lead_recommendation(decision(turn, c.CollaborationConfirmationDecision.REVISE))
        self.assertIsNone(revised.proposal)
        self.assertIsNone(revised.artifact_proposal)
        self.assertEqual(revised.discussion.response_text, "How do the four roles cooperate?")
        view = self.runtime.inspect_lead_collaboration(self.session_id, conversation_id)
        self.assertIs(view.current_recommendation_state, c.LeadRecommendationState.STALE)
        self.assertEqual(view.discussion_context.source_task_ref.task_id, request.authoring_scope.task_id)
        retained = next(item for item in view.available_tasks if item.ref.task_id == request.authoring_scope.task_id)
        self.assertEqual(retained.scope, request.authoring_scope)
        self.assertEqual(self.history(conversation_id)[:len(before)], before)
        self.assertEqual(len(lead._submitted_calls(self.history(conversation_id)[len(before):])), 1)
        keys = {f.key for r in c.project_collaboration_readable_history(self.history(conversation_id)) for f in r.fields}
        self.assertTrue({c.CollaborationReadableFieldKey.LEAD_RECOMMENDATION_DECISION,
                         c.CollaborationReadableFieldKey.LEAD_RECOMMENDATION_REVISION,
                         c.CollaborationReadableFieldKey.LEAD_RECOMMENDATION_STATE} <= keys)

    async def test_legacy_judgments_synthesis_and_runtime_notices_never_gain_action_authority(self):
        from tests.collaboration_contract_fixture import executed_history
        # Historical judgment/synthesis codecs stay unchanged and have no action field.
        executed, *_ = executed_history()
        for event in executed:
            wire = c.encode_collaboration_event(event)
            self.assertEqual(c.encode_collaboration_event(c.decode_collaboration_event(wire)), wire)
            if type(event.payload) is c.CollaborationLeadSynthesisPayload:
                self.assertFalse(hasattr(event.payload, "recommended_action"))
        self.assertIsNone(c.project_collaboration_history(executed).current_recommendation_request)
        conversation_id = self.conversation("Runtime recovery recommendation")
        self.backend.enqueue(control_plan=((0, LeadGroupPhase.SELECT,
            lead._SubmittedFailure(lead.BackendProtocolError("synthetic provider failure"))),))
        result = await self.runtime.lead_turn(self.session_id, conversation_id, "A normal read-only question.")
        self.assertIsNone(result.recommendation_request)
        self.assertFalse(self.payload_events(conversation_id, c.LeadRecommendationRequestPayload))
        self.assert_no_downstream(conversation_id)
