"""Public recommendation decisions and their preparation/recovery boundaries."""

from __future__ import annotations
import hashlib

from tests.test_collaboration_events import editorial, editorial_request
import asyncio
import json
import os
import traceback
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest import mock

from story_writing_agents import StoryRuntime
from story_writing_agents import conversations as c
from story_writing_agents import orchestrator as owner
from story_writing_agents.agents import LEAD_ORCHESTRATOR, PLOT_ARCHITECT, EXECUTION_SPECIALIST
from story_writing_agents.specialist_routing import AuthoringActivity
from story_writing_agents.errors import BackendProtocolError
from story_writing_agents.workspace.initialization import (
    SessionArtifactBinding,
    SessionArtifactBindingError,
    compose_session_artifact_binding, )
from story_writing_agents.writing_artifacts._storage import ArtifactRootSecurityError
from tests.recommendation_fixture import (
    DRAFT, OUTLINE, RecommendationCase, action, decision, digest,
    preparation, recommendation, rewrite_history,
    task_assessment, )
from tests import test_lead_orchestration as lead
from tests import test_artifact_proposal_orchestration as artifact
from tests import test_lead_artifact_application as application
from tests import test_lead_story_commit as commit




class LeadRecommendationTests(RecommendationCase):






    async def test_artifact_confirm_runs_one_preparation_and_stops_at_exact_review(self):
        self.workspace.path(OUTLINE).write_text("Outline source.\n", encoding="utf-8")
        before = self.snapshots()
        conversation_id, turn = await self.recommend()
        request = turn.recommendation_request
        before_events = self.history(conversation_id)
        self.assertIs(request.authoring_scope.activity, AuthoringActivity.REVIEW)
        self.assertFalse(self.payload_events(conversation_id, c.CollaborationAssignmentDispatchPayload))
        self.assertEqual(self.projection(conversation_id).artifact_objectives, ())
        self.assertEqual(self.snapshots(), before)
        producer = preparation(turn)
        def observed(call, provider):
            events = self.history(conversation_id)
            decision_event = self.payload_events(conversation_id, c.LeadRecommendationDecisionPayload)[0]
            started = self.payload_events(conversation_id, c.LeadRecommendationStartedPayload)[0]
            self.assertEqual(decision_event.batch_id, started.batch_id)
            self.assertEqual(started.payload.decision_event_id, decision_event.event_id)
            self.assertEqual(started.payload.request_event_id, request.request_event_id)
            initial = self.backend.calls[calls]
            self.assertIs(initial.contract.factory, call.contract.factory)
            self.assertEqual(initial.scope, call.scope)
            self.assertIs(initial.call_kind, lead.ProviderCallKind.COLLABORATION_INITIAL)
            material = lead._live_material(call)["structured_recommendation_execution"]
            self.assertEqual(initial.payload["context_reuse"]["format"], "shared-objects-v1")
            self.assertLess(len(json.dumps(dict(initial.payload), ensure_ascii=False)),
                            len(json.dumps(lead._expanded_context_payload(initial.payload),
                                           ensure_ascii=False)))
            self.assertEqual(material["request"]["action_digest"], request.action_digest)
            self.assertEqual(material["decision"]["decision"]["kind"], "confirm")
            self.assertEqual(material["start"]["execution_attempt_id"], started.payload.execution_attempt_id)
            self.assertIn(request.instruction, json.dumps(material, ensure_ascii=False))
            self.assertEqual(sum(type(e.payload) is c.CollaborationMessagePayload and
                e.direction is c.CollaborationDirection.USER_TO_LEAD for e in events), 1)
            return producer(call, provider)
        self.enqueue_preparation(turn, answer=observed)
        calls = len(self.backend.calls)
        original_qualifier = self.runtime._qualify_authoring_inputs
        qualification_starts = []
        def checked_qualifier(*args, **kwargs):
            qualification_starts.append(bool(self.payload_events(conversation_id, c.LeadRecommendationStartedPayload)))
            return original_qualifier(*args, **kwargs)
        with mock.patch.object(self.runtime, "lead_turn", side_effect=AssertionError("recursive public Lead")), \
                mock.patch.object(self.runtime, "_qualify_authoring_inputs", side_effect=checked_qualifier):
            prepared = await self.runtime.session(self.session_id).decide_lead_recommendation(decision(turn))
        self.assertEqual(qualification_starts[:1], [False])
        self.assertIs(type(prepared), c.LeadTurnResult)
        self.assertEqual(len(self.backend.calls) - calls, 3)
        self.assertIs(prepared.kind, c.LeadOutcomeKind.PROPOSAL)
        self.assertIs(prepared.next_action, c.CollaborationNextAction.SUBMIT_CONFIRMATION)
        self.assertIsNone(prepared.recommendation_request)
        self.assertIsNone(prepared.artifact_application_request)
        self.assertIsNone(prepared.story_commit_request)
        self.assertEqual(prepared.proposal.authoring_scope, request.authoring_scope)
        self.assertEqual(prepared.proposal.participants, (PLOT_ARCHITECT,))
        self.assertTrue(all(item.mode.value == "review" for item in prepared.proposal.assignments))
        candidate = prepared.artifact_proposal.candidate.candidate
        operation = candidate.change_set.operations[0]
        self.assertEqual((operation.operation_kind.value, operation.path, operation.content),
                         ("replace", OUTLINE, DRAFT.encode()))
        objective = self.objective(conversation_id)
        self.assertIs(objective.state, c.ArtifactObjectiveState.OPEN)
        self.assertIs(type(objective.facts), c.ArtifactObjectiveFactsV2)
        self.assertEqual(objective.candidate_binding.candidate_digest, candidate.candidate_digest)
        self.assertEqual(objective.draft_sources, request.draft_sources)
        self.assertEqual(self.history(conversation_id)[:len(before_events)], before_events)
        projection = self.assert_action_closed(conversation_id)
        result_event = self.payload_events(conversation_id, c.LeadRecommendationResultPayload)[0]
        self.assertLess(result_event.sequence, self.history(conversation_id)[-1].sequence)
        self.assertIs(projection.current_state, c.CollaborationStepState.AWAITING_CONFIRMATION)
        self.assertEqual(self.snapshots(), before)

        for call in self.backend.calls:
            self.assertNotIn("response_correction", lead._live_material(call))
            self.assertNotIn("structured_response_repair", call.payload)
        from tests.provider_wire_fixture import finite_fixture_selection
        for aliases, omit_source in (((), False), (("recommendation_request",), False),
                                     (("recommendation_decision",), False),
                                     (("recommendation_request", "recommendation_decision"), True)):
            cid = self.conversation("Raw confirmed drafting consultation")
            self.backend.enqueue(recommendation(recommended_action=action(kind="prepare_consultation", source_ids=("user_input",)),
                authoring_scope=lambda call: lead._live_scope(call, activity="review", domains=("plot_structure",)),
                authoring_assessment=task_assessment(foundation_ids=("user_input",), actions=('bounded review judgments', 'separate proposal preparation'))))
            offered = await self.runtime.lead_turn(self.session_id, cid,
                ("Recommend Plot review of this complete supplied candidate: " + DRAFT + "\nYou may make bounded Plot review judgments and prepare a separate Plot review proposal; preserve the supplied candidate facts."))
            self.assertIs(offered.kind, c.LeadOutcomeKind.RECOMMENDATION, offered)
            request = offered.recommendation_request
            source_ids = [item.input_id for item in request.basis.inputs]
            self.assertTrue(source_ids)
            selected = ([] if omit_source else source_ids) + list(aliases)
            review_ids = () if omit_source else tuple(source_ids)
            raw_observations = []
            def raw(call, _provider):
                packet = json.loads(preparation(offered)(call, _provider))["agent_action"]
                choices = dict(call.contract.semantic_index.policy.choices)
                proposal = packet["task_result"]["response_details"]["specialist_consultation_proposal"]
                for row in proposal["assignments"].values():
                    row.update(input_anchor_id=selected[0], optional_inputs={alias: alias in selected for alias in choices["allocation_input_ids"]})
                    row["review_basis"]["candidate_input_ids"] = finite_fixture_selection(choices["review_candidate_ids"], review_ids)
                if omit_source:
                    proposal["required_input_recipients"] = {}
                encoded = json.dumps({"agent_action": packet})
                raw_observations.append((packet, choices, call.contract.outer_inner_schema_ids, lead.diagnose_json_text(encoded, call.schema)))
                return encoded
            self.backend.enqueue(raw)
            if omit_source:
                self.backend.enqueue(raw)
            calls = len(self.backend.calls)
            prepared = await self.runtime.decide_lead_recommendation(decision(offered))
            self.assertTrue(raw_observations, prepared)
            for packet, choices, pairs, issues in raw_observations:
                self.assertIn((packet["schema_id"], packet["task_result"]["schema_id"]), pairs)
                proposal = packet["task_result"]["response_details"]["specialist_consultation_proposal"]
                self.assertEqual(set(proposal["assignments"]), set(request.specialist_ids))
                recipients = proposal["required_input_recipients"]
                self.assertEqual(recipients, {} if omit_source else dict.fromkeys(source_ids, request.specialist_ids[0]))
                for agent, row in proposal["assignments"].items():
                    self.assertEqual(row["input_anchor_id"], selected[0])
                    self.assertEqual(row["optional_inputs"], {alias: alias in selected for alias in choices["allocation_input_ids"]})
                    self.assertEqual(row["review_basis"], {"candidate_input_ids": finite_fixture_selection(choices["review_candidate_ids"], review_ids), "criteria": request.instruction})
                    edges = {row["input_anchor_id"], *review_ids, *(alias for alias, recipient in recipients.items() if recipient == agent), *(alias for alias, included in row["optional_inputs"].items() if included)}
                    self.assertEqual([alias for alias in choices["allocation_input_ids"] if alias in edges], selected)
                if omit_source:
                    prefix = "$.agent_action.task_result.response_details.specialist_consultation_proposal"
                    missing = {f"{prefix}.required_input_recipients.{alias}: required property is missing" for alias in source_ids}
                    basis_paths = tuple(f"{prefix}.assignments.{agent}.review_basis.candidate_input_ids." for agent in request.specialist_ids)
                    basis_error = "expected one of [true] (boolean), got boolean"
                    self.assertTrue(missing.issubset(issues), issues)
                    self.assertTrue(all(any(issue.startswith(path) and issue.endswith(basis_error) for issue in issues) for path in basis_paths), issues)
                    self.assertTrue(all(issue in missing or (issue.startswith(basis_paths) and issue.endswith(basis_error)) for issue in issues), issues)
                else:
                    self.assertEqual(issues, (), (aliases, issues))
            self.assertEqual(len(self.backend.calls) - calls, 2 if omit_source else 1, prepared)
            self.assert_no_downstream(cid)
            if omit_source:
                self.assertIsNone(prepared.proposal)
                self.assertTrue(prepared.validation_details)
            else:
                self.assertIs(prepared.next_action, c.CollaborationNextAction.SUBMIT_CONFIRMATION, prepared)
                self.assertEqual(prepared.proposal.authoring_scope, request.authoring_scope)
                self.assertEqual(prepared.proposal.purpose, request.purpose)
                for assignment in prepared.proposal.assignments:
                    self.assertEqual(set(assignment.input_ids), {*source_ids, "recommendation_request", "recommendation_decision"})
                    self.assertEqual(len(assignment.input_ids), len(source_ids) + 2)
            self.assertEqual(self.snapshots(), before)







    async def test_exact_stale_duplicate_and_concurrent_decisions_never_reuse_authority(self):
        self.workspace.path(OUTLINE).write_text("Outline source.\n", encoding="utf-8")
        conversation_id, turn = await self.recommend()
        snapshots = self.snapshots()
        choice = decision(turn)
        before, calls = self.history(conversation_id), len(self.backend.calls)
        foreign = self.conversation("Foreign decision")
        from story_writing_agents.errors import ConversationNotFoundError
        for change in ({"request_event_id": "foreign"}, {"request_tail_sequence": choice.request_tail_sequence + 1},
                       {"request_id": "foreign"}, {"action_id": "foreign"}, {"action_revision": 2},
                       {"action_digest": digest("foreign")}, {"conversation_id": foreign}, {"session_id": "foreign_session"}):
            expected = ConversationNotFoundError if "session_id" in change else BackendProtocolError
            with self.subTest(change=change), self.assertRaises(expected):
                await self.runtime.decide_lead_recommendation(replace(choice, **change))
        self.assertEqual(self.history(conversation_id), before)
        self.assertEqual(len(self.backend.calls), calls)
        entered, release = asyncio.Event(), asyncio.Event()
        producer = preparation(turn)
        async def held(call, provider):
            entered.set()
            await release.wait()
            return producer(call, provider)
        self.enqueue_preparation(turn, answer=held)
        first = asyncio.create_task(self.runtime.decide_lead_recommendation(choice))
        try:
            await asyncio.wait_for(entered.wait(), 2)
        except TimeoutError as failure:
            from tests.recommendation_fixture import (
                _bounded_failure_note, _stored_failure_snapshots)
            state = ('cancelled' if first.cancelled() else
                     'done_unknown' if first.done() else 'pending')
            failure.add_note(_bounded_failure_note(
                ('task', calls, entered.is_set(), state),
                *_stored_failure_snapshots(self.backend)))
            raise
        second = asyncio.create_task(self.runtime.decide_lead_recommendation(choice))
        try:
            await asyncio.sleep(0)
            self.assertFalse(second.done())
            release.set()
            results = await asyncio.gather(first, second, return_exceptions=True)
        finally:
            release.set()
            for task in (first, second):
                if not task.done(): task.cancel()
            await asyncio.gather(first, second, return_exceptions=True)
        self.assertIs(type(results[0]), c.LeadTurnResult)
        self.assertIsInstance(results[1], BackendProtocolError)
        self.assertEqual(len(self.backend.calls), calls + 3)
        self.assertEqual(len(self.payload_events(conversation_id, c.LeadRecommendationStartedPayload)), 1)
        with self.assertRaises(BackendProtocolError):
            await self.runtime.decide_lead_recommendation(choice)
        self.assertEqual(len(self.backend.calls), calls + 3)
        self.assert_no_downstream(conversation_id)
        self.assertEqual(self.snapshots(), snapshots)

        source = self.conversation("Editorial source")
        selected = await self.runtime.record_editorial_decision(self.session_id, source, editorial_request("recommendation_selected_memory"))
        current, stale = await self.recommend()
        self.assertEqual(stale.recommendation_request.basis.editorial_refs, (selected.record_ref,))
        await self.runtime.record_editorial_decision(self.session_id, source, editorial.EditorialRecordRequest("reject_recommendation_memory", editorial.EditorialAction.REJECT, "Changed explicitly.", expected_ref=selected.record_ref))
        before, calls = self.history(current), len(self.backend.calls)
        closed = await self.runtime.decide_lead_recommendation(decision(stale))
        self.assertEqual((closed.kind, self.history(current)[-1].payload.state), (c.LeadOutcomeKind.ANSWER, c.LeadRecommendationState.STALE))
        self.assertEqual(tuple(event.kind for event in self.history(current)[-2:]), (c.CollaborationEventKind.LEAD_RECOMMENDATION_DECISION, c.CollaborationEventKind.LEAD_RECOMMENDATION_RESULT))
        self.assertEqual((self.history(current)[:-2], len(self.backend.calls)), (before, calls))

        from story_writing_agents import editorial_scope as scopes
        memory_source = self.conversation("Scoped USER decisions")
        scope = scopes.EditorialScope(scopes.EditorialScopeKind.KNOWN, targets=( scopes.EditorialScopeTarget(OUTLINE),
            scopes.EditorialScopeTarget("core_documents/character_sheets.md"), ))
        draft = replace(editorial_request("scoped_public_draft").draft, scope=scope)
        selected = await self.runtime.record_editorial_decision(self.session_id, memory_source,
            editorial.EditorialRecordRequest.directive(draft, transition_reason="Retain both exact targets."))
        a_ref, original_record = selected.record_ref, selected.record
        self.assertIsNotNone(a_ref)
        work, offered = await self.recommend(self.conversation("Pinned scope preparation"))
        pin = offered.recommendation_request.basis.editorial_context_digest
        self.assertEqual(offered.recommendation_request.basis.editorial_refs, (a_ref,))
        peer_source = self.conversation("Independent source and replacement")
        independent = replace(draft, scope=scopes.EditorialScope.whole("core_documents/narrative_rules.md"))
        await self.runtime.record_editorial_decision(self.session_id, peer_source,
            editorial.EditorialRecordRequest.directive(independent, transition_reason="Independent scope."))
        state = self.runtime.inspect_editorial_state(self.session_id)
        self.assertEqual(state.context_digest((a_ref,)), pin)
        self.enqueue_preparation(offered)
        prepared = await self.runtime.decide_lead_recommendation(decision(offered))
        self.assertIs(prepared.kind, c.LeadOutcomeKind.PROPOSAL)
        self.assertEqual(prepared.proposal.basis.editorial_context_digest, pin)
        before, calls = self.history(work), len(self.backend.calls)
        changed = replace(draft, scope=scopes.EditorialScope.whole(OUTLINE))
        relation = editorial.EditorialRelationDecision(a_ref, editorial.EditorialRelationKind.REPLACE, "Replace only the outline direction.")
        replacement = await self.runtime.record_editorial_decision(self.session_id, peer_source,
            editorial.EditorialRecordRequest.directive(changed, transition_reason="Explicit partial replacement.",
                relation_decisions=(relation,), expected_editorial_context_digest=pin))
        self.assertIs(replacement.status, editorial.EditorialRecordStatus.RECORDED)
        state = self.runtime.inspect_editorial_state(self.session_id)
        self.assertEqual(next(r for r in state.current_records if r.ref == a_ref), original_record)
        self.assertEqual(state.scope_state(a_ref).status, "partial")
        self.assertNotEqual(state.context_digest((a_ref,)), pin)
        self.assertEqual(self.history(work), before)
        self.runtime = StoryRuntime(self.control_workspace, self.backend)
        self.assertEqual(self.runtime.inspect_editorial_state(self.session_id), state)
        with self.assertRaises(ValueError):
            await self.runtime.decide_lead_step(lead._decision(prepared,
                c.CollaborationConfirmationDecision.CONFIRM))
        self.assertEqual((self.history(work), len(self.backend.calls)), (before, calls))
        await self.runtime.decide_lead_step(lead._decision(prepared,
            c.CollaborationConfirmationDecision.REJECT))
        _, fresh = await self.recommend(work)
        fresh_basis = fresh.recommendation_request.basis
        self.assertEqual(fresh_basis.editorial_context_digest,
                         state.context_digest(fresh_basis.editorial_refs))
        self.enqueue_preparation(fresh)
        new_prepared = await self.runtime.decide_lead_recommendation(decision(fresh))
        self.assertEqual(new_prepared.proposal.basis.editorial_context_digest,
            fresh_basis.editorial_context_digest)
        reviewed = await self.review(new_prepared)
        self.assertIs(reviewed.artifact_proposal.state, c.ArtifactProposalState.REVIEWED)
        self.assertEqual(self.runtime.inspect_editorial_state(self.session_id).applied_facts, ())

        self.workspace.path(OUTLINE).write_text("Source-bound outline line.\n", encoding="utf-8")
        source_bytes = self.workspace.path(OUTLINE).read_bytes()
        anchored = self.conversation("Whole direction with finite replacement")
        whole = replace(draft, scope=scopes.EditorialScope.whole(OUTLINE))
        state = self.runtime.inspect_editorial_state(self.session_id)
        prior_relation = editorial.EditorialRelationDecision(replacement.record_ref,
            editorial.EditorialRelationKind.REPLACE, "Replace the earlier whole outline direction.")
        whole_result = await self.runtime.record_editorial_decision(self.session_id, anchored,
            editorial.EditorialRecordRequest.directive(whole, transition_reason="Retain the complete outline.", relation_decisions=(prior_relation,),
                expected_editorial_context_digest=state.context_digest((replacement.record_ref,))))
        state = self.runtime.inspect_editorial_state(self.session_id)
        self.assertEqual(state.scope_state(replacement.record_ref).status, "replaced")
        before, calls = self.history(anchored), len(self.backend.calls)
        refused = await self.runtime.record_editorial_decision(self.session_id, anchored,
            editorial.EditorialRecordRequest.directive(whole, transition_reason="Consumed counterpart.",
                relation_decisions=(replace(prior_relation, kind=editorial.EditorialRelationKind.COEXIST),),
                expected_editorial_context_digest=state.context_digest((replacement.record_ref,))))
        self.assertIs(refused.status, editorial.EditorialRecordStatus.STALE_REFERENCE)
        refused = await self.runtime.record_editorial_decision(self.session_id, anchored,
            editorial.EditorialRecordRequest.directive(whole, transition_reason="Consumed counterpart.",
                relation_decisions=(replace(prior_relation, kind=editorial.EditorialRelationKind.CONFLICT),),
                expected_editorial_context_digest=state.context_digest((replacement.record_ref,))))
        self.assertIs(refused.status, editorial.EditorialRecordStatus.STALE_REFERENCE)
        self.assertEqual((self.history(anchored), len(self.backend.calls)), (before, calls))
        finite = scopes.EditorialScope.selected_range(OUTLINE, 1, 1, hashlib.sha256(source_bytes).hexdigest())
        finite_draft = replace(draft, scope=finite)
        state = self.runtime.inspect_editorial_state(self.session_id)
        relation = editorial.EditorialRelationDecision(whole_result.record_ref,
            editorial.EditorialRelationKind.REPLACE, "Replace this exact source-bound line only.", scope=finite)
        replaced = await self.runtime.record_editorial_decision(self.session_id, anchored,
            editorial.EditorialRecordRequest.directive(finite_draft, transition_reason="Explicit finite replacement.", relation_decisions=(relation,),
                    expected_editorial_context_digest=state.context_digest((whole_result.record_ref,))))
        self.assertIs(replaced.status, editorial.EditorialRecordStatus.RECORDED)
        state = self.runtime.inspect_editorial_state(self.session_id)
        self.assertEqual(state.scope_state(whole_result.record_ref).status, "partial")
        drift_work, offered = await self.recommend(self.conversation("Range anchor drift"))
        def search_outline(call, _provider):
            from tests.provider_wire_fixture import indexed_response, offered_fixture_bindings
            outer, inner = offered_fixture_bindings(
                call, action_type="search_context", response_type=None, family=1)
            assert inner is None
            return indexed_response({
                "action_type": "search_context", "artifact_directory_prefix": "core_documents/",
                "queries": [{"search_text": "Source-bound outline line.",
                             "source_types": {"story_document": True}}]},
                outer_schema_id=outer, inner_schema_id=inner)
        self.backend.enqueue(
            search_outline, lead._read_history_hit, preparation(offered, read_evidence=True))
        drift_plan = await self.runtime.decide_lead_recommendation(decision(offered))
        self.assertIsNotNone(drift_plan.proposal, drift_plan)
        before, calls = self.history(drift_work), len(self.backend.calls)
        records, pin = state.current_records, state.context_digest(drift_plan.proposal.basis.editorial_refs)
        self.workspace.path(OUTLINE).write_bytes(source_bytes + b"External drift.\n")
        try:
            with self.assertRaisesRegex(ValueError, "source is unavailable or changed"):
                await self.runtime.decide_lead_step(lead._decision(drift_plan,
                    c.CollaborationConfirmationDecision.CONFIRM))
            current = self.runtime.inspect_editorial_state(self.session_id)
            self.assertEqual((current.current_records,
                current.context_digest(drift_plan.proposal.basis.editorial_refs)),
                             (records, pin))
            self.assertEqual((self.history(drift_work), len(self.backend.calls)), (before, calls))
        finally:
            self.workspace.path(OUTLINE).write_bytes(source_bytes)



















class LeadRecommendationRecoveryTests(RecommendationCase):
    async def reference_offer(self, *, kind="prepare_consultation", objective_id=None):
        return await self.recommend(recommended_action=action(kind=kind, source_ids=("reference_1", "current_response"), objective_id=objective_id),
            text=f"Inspect ${{{OUTLINE}}} and recommend exactly one preparation.")





    async def test_missing_corrupt_or_unresolved_origin_accounting_never_authorizes_retry(self):
        from story_writing_agents.writing_artifacts.authorization import ReferenceAuthorizationError, ReferenceAuthorizationErrorCode, ReferenceOperationFailure
        for fault in ("missing", "corrupt", "unfinished", "uncertain"):
            conversation_id, turn = await self.reference_offer()
            request_event = self.payload_events(conversation_id, c.LeadRecommendationRequestPayload)[-1]
            self.assertIsNotNone(turn.provider_invocation_id)
            self.assertEqual(self.runtime._recommendation_turn_result(request_event).provider_invocation_id, turn.provider_invocation_id)
            error = BackendProtocolError("Synthetic unresolved preparation")
            self.backend.enqueue(lead._SubmittedFailure(error, submission_uncertain=fault == "uncertain"))
            inspect_type = type(self.runtime.artifacts)
            inspect_original = inspect_type.inspect_reference_operation
            def broken(repository, operation_id):
                history = inspect_original(repository, operation_id)
                if fault == "missing": return None
                if fault == "corrupt": raise ReferenceAuthorizationError(ReferenceAuthorizationErrorCode.EVIDENCE_CORRUPT)
                if fault == "unfinished": return replace(history, invocation_events=(history.invocation_events[0][:-1], *history.invocation_events[1:]))
                return history
            with mock.patch.object(inspect_type, "inspect_reference_operation", new=broken):
                expected = ReferenceAuthorizationError if fault == "corrupt" else ReferenceOperationFailure
                with self.assertRaises(expected):
                    await self.runtime.decide_lead_recommendation(decision(turn))
            self.assertFalse(self.payload_events(conversation_id, c.LeadRecommendationResultPayload))
            calls, history = len(self.backend.calls), self.history(conversation_id)
            restarted = StoryRuntime(self.control_workspace, self.backend)
            blocked = await restarted.lead_turn(self.session_id, conversation_id, "Do not replay uncertain work.")
            self.assertIsNone(blocked.provider_invocation_id)
            self.assertIs(blocked.next_action, c.CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME if fault == "uncertain"
                          else c.CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY)
            self.assertEqual(len(self.backend.calls), calls)
            self.assertEqual(self.history(conversation_id), history)
            self.assertIsNone(blocked.recommendation_request)
