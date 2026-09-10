"""Public recommendation decisions and their preparation/recovery boundaries."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from unittest import mock

from story_writing_agents import StoryRuntime
from story_writing_agents import conversations as c
from story_writing_agents import orchestrator as owner
from story_writing_agents.agents import LEAD_ORCHESTRATOR, PLOT_ARCHITECT
from story_writing_agents.errors import BackendProtocolError
from tests.recommendation_fixture import (
    DRAFT, OUTLINE, RecommendationCase, action, decision, digest,
    preparation, recommendation, rewrite_history,
)
from tests import test_lead_orchestration as lead
from tests import test_artifact_proposal_orchestration as artifact
from tests import test_lead_artifact_application as application
from tests import test_lead_story_commit as commit


class LeadRecommendationTests(RecommendationCase):
    async def test_fresh_outcome_schema_requires_typed_actions_only_for_recommendations(self):
        conversation_id, turn = await self.recommend()
        call = self.backend.calls[-1]
        expected = {"scope", "evidence_status", "evidence_ids", "kind", "message", "proposal",
                    "artifact_intent", "objective_intent", "recommended_action"}
        value = json.loads(recommendation()(call, None))["action"]["result"]
        self.assertEqual(set(value), expected)
        self.assertIn("recommended_action", repr(call.schema))
        self.assertIsNone(self.projection(conversation_id).current_proposal)
        invalid = []
        missing = dict(value)
        missing.pop("recommended_action")
        invalid.append(missing)
        invalid += [dict(value, recommended_action=None),
                    dict(value, kind="answer"),
                    dict(value, recommended_action={**value["recommended_action"], "apply": True}),
                    dict(value, objective_intent={"action": "start", "request": "Unconfirmed task",
                         "target_paths": [OUTLINE], "next_step": "clarify", "draft_source_event_ids": []})]
        for index, bad in enumerate(invalid):
            with self.subTest(index=index):
                cid = self.conversation(f"Invalid typed recommendation {index}")
                def response(request, _provider, body=bad):
                    return lead._provider_envelope(request, body)
                self.backend.enqueue(response)
                self.backend.enqueue(response)
                result = await self.runtime.lead_turn(self.session_id, cid, "Reject a malformed action.")
                self.assertIsNone(result.recommendation_request)
                self.assertIs(result.next_action, c.CollaborationNextAction.RETRY_LEAD_TURN)
                self.assertEqual(self.projection(cid).artifact_objectives, ())
                self.assert_no_downstream(cid)

    async def test_artifact_confirm_runs_one_preparation_and_stops_at_exact_review(self):
        before = self.snapshots()
        conversation_id, turn = await self.recommend()
        request = turn.recommendation_request
        before_events = self.history(conversation_id)
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
            material = lead._workflow_material(call.payload)["structured_recommendation_execution"]
            self.assertEqual(material["request"]["action_digest"], request.action_digest)
            self.assertEqual(material["decision"]["decision"]["kind"], "confirm")
            self.assertEqual(material["start"]["execution_attempt_id"], started.payload.execution_attempt_id)
            self.assertIn(request.instruction, json.dumps(material, ensure_ascii=False))
            self.assertEqual(sum(type(e.payload) is c.CollaborationMessagePayload and
                e.direction is c.CollaborationDirection.USER_TO_LEAD for e in events), 1)
            return producer(call, provider)
        self.backend.enqueue(observed)
        calls = len(self.backend.calls)
        with mock.patch.object(self.runtime, "lead_turn", side_effect=AssertionError("recursive public Lead")):
            prepared = await self.runtime.session(self.session_id).decide_lead_recommendation(decision(turn))
        self.assertIs(type(prepared), c.LeadTurnResult)
        self.assertEqual(len(self.backend.calls) - calls, 1)
        self.assertIs(prepared.kind, c.LeadOutcomeKind.PROPOSAL)
        self.assertIs(prepared.next_action, c.CollaborationNextAction.SUBMIT_CONFIRMATION)
        self.assertIsNone(prepared.recommendation_request)
        self.assertIsNone(prepared.artifact_application_request)
        self.assertIsNone(prepared.story_commit_request)
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

    async def test_consultation_confirm_without_objective_stops_at_matching_proposal(self):
        conversation_id, turn = await self.recommend(recommended_action=action(
            kind="prepare_consultation", instruction="Prepare this consultation; ${core_documents/narrative_rules.md} is text only."))
        request = turn.recommendation_request
        before, calls, references = self.snapshots(), len(self.backend.calls), len(self.backend.reference_calls)
        result = await self.confirm(turn, through_session=True)
        self.assertIs(type(result), c.LeadTurnResult)
        self.assertEqual(len(self.backend.calls) - calls, 1)
        self.assertEqual(len(self.backend.reference_calls), references)
        self.assertEqual(result.proposal.purpose, request.purpose)
        self.assertEqual(tuple(a.specialist_id for a in result.proposal.assignments), request.specialist_ids)
        expected_sources = {i.input_id for i in request.basis.inputs} | {"recommendation_request", "recommendation_decision"}
        self.assertEqual({i for a in result.proposal.assignments for i in a.input_ids}, expected_sources)
        self.assertEqual(self.projection(conversation_id).artifact_objectives, ())
        self.assertIsNone(result.artifact_proposal)
        self.assert_action_closed(conversation_id)
        self.assertEqual(self.snapshots(), before)

    async def test_confirmed_continuation_preserves_existing_objective_facts(self):
        conversation_id, _ = await self.start()
        candidate = await self.prepare(conversation_id)
        await self.runtime.decide_lead_step(lead._decision(candidate, c.CollaborationConfirmationDecision.REJECT))
        previous = self.objective(conversation_id)
        # No new draft alias: the immutable request must disclose inherited selected drafts now.
        _, turn = await self.recommend(conversation_id, recommended_action=action(
            source_ids=("user_input",), objective_id=previous.facts.objective_id))
        request = turn.recommendation_request
        self.assertEqual(request.draft_sources, previous.draft_sources)
        self.assertTrue({s.event_id for s in request.draft_sources} <= {i.input_id for i in request.basis.inputs})
        await self.confirm(turn)
        current = self.objective(conversation_id)
        self.assertEqual(current.facts, previous.facts)
        self.assertEqual(current.draft_sources, request.draft_sources)
        transition = next(e for e in self.history(conversation_id) if e.event_id == current.latest_event_id)
        self.assertEqual(transition.payload.previous_objective_event_id, previous.latest_event_id)
        self.assertIs(type(current.facts), c.ArtifactObjectiveFacts)
        self.assert_action_closed(conversation_id)

    async def test_exact_stale_duplicate_and_concurrent_decisions_never_reuse_authority(self):
        conversation_id, turn = await self.recommend()
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
        self.backend.enqueue(held)
        first = asyncio.create_task(self.runtime.decide_lead_recommendation(choice))
        await asyncio.wait_for(entered.wait(), 2)
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
        self.assertEqual(len(self.backend.calls), calls + 1)
        self.assertEqual(len(self.payload_events(conversation_id, c.LeadRecommendationStartedPayload)), 1)
        with self.assertRaises(BackendProtocolError):
            await self.runtime.decide_lead_recommendation(choice)
        self.assertEqual(len(self.backend.calls), calls + 1)

    async def test_input_profile_route_and_budget_drift_stop_before_submission(self):
        for mode in ("content", "identical_bytes_metadata", "profile", "policy"):
            conversation_id, turn = await self.recommend()
            calls = len(self.backend.calls)
            path = self.workspace.path(OUTLINE)
            scope = mock.patch.object(self.backend, "execution_profile_fingerprint", return_value=digest("changed profile")) if mode == "profile" else (
                mock.patch.object(owner, "_BUDGET_POLICY_FINGERPRINT", digest("changed policy")) if mode == "policy" else mock.patch.object(self.backend, "backend_overrides", {}))
            if mode == "content": path.write_text("Changed after disclosure.\n")
            elif mode == "identical_bytes_metadata":
                sibling = path.with_suffix(".tmp")
                sibling.write_bytes(path.read_bytes())
                sibling.replace(path)
            with scope:
                result = await self.runtime.decide_lead_recommendation(decision(turn))
            self.assertEqual(len(self.backend.calls), calls)
            self.assertIsNone(result.recommendation_request)
            self.assert_action_closed(conversation_id, c.LeadRecommendationState.STALE)
            path.write_bytes(b"")
        with mock.patch.object(owner, "_CONTEXT_INVOCATION_CALL_LIMIT", 1):
            conversation_id, turn = await self.recommend()
        self.assertEqual(turn.recommendation_request.max_preparation_physical_calls, 1)
        self.backend.enqueue(lead._search_history)
        calls = len(self.backend.calls)
        result = await self.runtime.decide_lead_recommendation(decision(turn))
        self.assertLessEqual(len(self.backend.calls) - calls, 1)
        self.assertIsNone(result.artifact_proposal)
        self.assert_no_downstream(conversation_id)

    async def test_reject_closes_without_calls_or_objective_transition(self):
        conversation_id, _ = await self.start()
        objective = self.objective(conversation_id)
        _, turn = await self.recommend(conversation_id, recommended_action=action(objective_id=objective.facts.objective_id))
        before, calls = self.snapshots(), len(self.backend.calls)
        result = await self.runtime.decide_lead_recommendation(decision(turn, c.CollaborationConfirmationDecision.REJECT))
        self.assertIs(result.kind, c.LeadOutcomeKind.ANSWER)
        self.assertEqual(len(self.backend.calls), calls)
        self.assertEqual(self.objective(conversation_id), objective)
        self.assertFalse(self.payload_events(conversation_id, c.LeadRecommendationStartedPayload))
        self.assert_action_closed(conversation_id, c.LeadRecommendationState.REJECTED)
        self.assertEqual(self.snapshots(), before)

    async def test_revise_supersedes_and_requires_fresh_confirmation(self):
        conversation_id, turn = await self.recommend()
        request = turn.recommendation_request
        before, calls, refs = self.snapshots(), len(self.backend.calls), len(self.backend.reference_calls)
        self.backend.enqueue(recommendation(message="Revised synthetic outline", recommended_action=action()))
        revised = await self.runtime.decide_lead_recommendation(decision(turn, c.CollaborationConfirmationDecision.REVISE,
            revision_request="Keep the draft; ${core_documents/narrative_rules.md} remains undisclosed text."))
        self.assertEqual(len(self.backend.calls) - calls, 1)
        self.assertEqual(len(self.backend.reference_calls), refs)
        self.assertIsNone(revised.artifact_proposal)
        self.assertEqual(self.projection(conversation_id).artifact_objectives, ())
        new = revised.recommendation_request
        self.assertEqual((new.action_id, new.action_revision), (request.action_id, request.action_revision + 1))
        self.assertNotEqual(new.action_digest, request.action_digest)
        self.assertGreater(new.physical_calls_spent, request.physical_calls_spent)
        origin = self.payload_events(conversation_id, c.LeadRecommendationDecisionPayload)[0]
        self.assertIs(origin.payload.decision.kind, c.CollaborationConfirmationDecision.REVISE)
        closure = self.payload_events(conversation_id, c.LeadRecommendationResultPayload)[0]
        self.assertIs(closure.payload.state, c.LeadRecommendationState.SUPERSEDED)
        self.assertLess(closure.sequence, new.tail_sequence)
        with self.assertRaises(BackendProtocolError):
            await self.runtime.decide_lead_recommendation(decision(turn))
        self.assertEqual(len(self.backend.calls), calls + 1)
        await self.confirm(revised, text="Revised synthetic outline")
        self.assertIs(type(self.objective(conversation_id).facts), c.ArtifactObjectiveFactsV2)
        self.assertEqual(self.objective(conversation_id).facts.origin_recommendation_request_event_id, new.request_event_id)
        self.assertEqual(self.snapshots(), before)
        self.assert_no_downstream(conversation_id)

    async def test_ordinary_input_supersedes_with_real_user_origin_and_contextual_approval(self):
        conversation_id, turn = await self.recommend()
        request = turn.recommendation_request
        before = self.snapshots()
        source = request.draft_sources[0]
        self.backend.enqueue(artifact._artifact_outcome(
            operations=lambda call, _: [{"kind": "replace", "evidence_id": artifact._status_id(call, OUTLINE), "new_text": DRAFT}],
            evidence_status="empty", evidence_ids=lambda call, _: [artifact._status_id(call, OUTLINE)],
            objective_intent={"action": "start", "request": "Prepare the approved draft.", "target_paths": [OUTLINE],
                "next_step": "prepare", "draft_source_event_ids": [source.event_id]}))
        result = await self.runtime.lead_turn(self.session_id, conversation_id, "네, 그 초안 그대로 준비해 줘.")
        self.assertIsNotNone(result.artifact_proposal)
        objective = self.objective(conversation_id)
        self.assertIs(type(objective.facts), c.ArtifactObjectiveFacts)
        events = self.history(conversation_id)
        user = next(e for e in events if e.event_id == objective.facts.origin_user_event_id)
        closure = self.payload_events(conversation_id, c.LeadRecommendationResultPayload)[0]
        self.assertEqual((closure.payload.superseding_user_event_id, closure.batch_id), (user.event_id, user.batch_id))
        self.assertEqual(closure.sequence, user.sequence + 1)
        self.assertNotIn(OUTLINE, user.payload.content)
        self.assertNotIn(DRAFT, user.payload.content)
        self.assertFalse(self.payload_events(conversation_id, c.LeadRecommendationDecisionPayload))
        self.assertFalse(self.payload_events(conversation_id, c.LeadRecommendationStartedPayload))
        self.assertEqual(objective.draft_sources, request.draft_sources)
        self.assertEqual(self.snapshots(), before)
        self.assert_no_downstream(conversation_id)

    async def test_preparation_outputs_obey_action_scope_and_never_loop(self):
        for mode in ("changed_target", "changed_source", "new_recommendation", "advice", "question"):
            conversation_id, turn = await self.recommend()
            before, calls = self.snapshots(), len(self.backend.calls)
            if mode == "question": response = lead._visible_outcome("question", "Which ending should resolve the missing choice?")
            elif mode == "advice": response = lead._visible_outcome("answer", "Already finished.")
            elif mode == "new_recommendation": response = recommendation(message="Confirm this preparation again.")
            elif mode == "changed_source": response = preparation(turn, mutate=lambda v: v["objective_intent"].update(draft_source_event_ids=[]))
            else: response = preparation(turn, mutate=lambda v: v["objective_intent"].update(target_paths=["core_documents/narrative_rules.md"]))
            self.backend.enqueue(response)
            if mode != "question": self.backend.enqueue(response)
            result = await self.runtime.decide_lead_recommendation(decision(turn))
            self.assertLessEqual(len(self.backend.calls) - calls, 2)
            self.assertIsNone(result.recommendation_request)
            self.assertIsNone(result.artifact_proposal)
            self.assertEqual(self.projection(conversation_id).artifact_objectives, ())
            self.assertEqual(self.snapshots(), before)
            self.assert_no_downstream(conversation_id)
            if mode == "question":
                self.assertIs(result.kind, c.LeadOutcomeKind.QUESTION)
                self.assertIs(result.next_action, c.CollaborationNextAction.REPLY_TO_LEAD)
                self.assert_action_closed(conversation_id)

    async def test_pending_apply_commit_and_uncertainty_take_precedence_over_recommendation(self):
        from story_writing_agents.writing_artifacts import ArtifactApplicationError, ArtifactApplicationErrorCode
        conversation_id, turn = await self.recommend()
        reviewed = await self.review(await self.confirm(turn))
        before, calls, history = self.snapshots(), len(self.backend.calls), self.history(conversation_id)
        result = await self.runtime.decide_lead_recommendation(decision(turn))
        self.assertEqual(result.artifact_application_request, reviewed.artifact_application_request)
        self.assertIsNone(result.recommendation_request)
        self.assertEqual(self.history(conversation_id), history)
        self.assertEqual(len(self.backend.calls), calls)
        self.assertEqual(self.snapshots(), before)
        applied = await self.apply(reviewed)
        history, calls = self.history(conversation_id), len(self.backend.calls)
        result = await self.runtime.decide_lead_recommendation(decision(turn))
        self.assertEqual(result.story_commit_request, applied.commit_request)
        self.assertIsNone(result.recommendation_request)
        self.assertEqual(self.history(conversation_id), history)
        self.assertEqual(len(self.backend.calls), calls)
        await self.reject_commit(applied)
        other, offered = await self.recommend()
        # Restore the disposable artifact to empty before accepting the new request.
        # This new request intentionally observes the resulting drift and is closed stale.
        self.workspace.path(OUTLINE).write_bytes(b"")
        stale = await self.runtime.decide_lead_recommendation(decision(offered))
        self.assertIsNone(stale.recommendation_request)
        _, offered = await self.recommend(other)
        reviewed = await self.review(await self.confirm(offered))
        with mock.patch.object(self.runtime.artifacts, "apply_artifact_changes",
                               side_effect=ArtifactApplicationError(ArtifactApplicationErrorCode.RECOVERY_REQUIRED)):
            await self.apply(reviewed)
        before, calls = self.history(other), len(self.backend.calls)
        for runtime in (self.runtime, StoryRuntime(self.workspace, self.backend)):
            blocked = await runtime.decide_lead_recommendation(decision(offered))
            self.assertIs(blocked.next_action, c.CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY)
            self.assertIsNone(blocked.recommendation_request)
            self.assertEqual(self.history(other), before)
            self.assertEqual(len(self.backend.calls), calls)


class LeadRecommendationSynthesisTests(RecommendationCase):
    async def consultation(self, *, actionable, pause=False, mixed=False):
        conversation_id = self.conversation("Synthetic synthesis")
        proposed = await self.proposal(conversation_id)
        self.backend.enqueue_for(PLOT_ARCHITECT, lead._specialist_outcome())
        selected = action() if actionable else None
        base = lead._synthesis_outcome(message=DRAFT, recommended_action=selected)
        def synthesis(call, provider):
            value = json.loads(base(call, provider))
            result = value["action"]["result"]
            self.assertEqual(set(result), {"scope", "evidence_status", "evidence_ids", "message",
                "next_intent", "reply_event_ids", "unresolved_input_ids", "recommended_action"})
            if mixed:
                material = lead._workflow_material(call.payload)
                result["next_intent"] = {"relation": "continue_lineage", "purpose": "Review again.",
                    "assignments": [{"agent_id": PLOT_ARCHITECT, "instruction": "Review all sources.",
                        "input_ids": [*(i["input_id"] for i in material["inputs"]), *result["reply_event_ids"]]}]}
            return json.dumps(value, ensure_ascii=False)
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, synthesis)
        if pause:
            with mock.patch.object(self.runtime, "_finalize_known_result_locked", side_effect=RuntimeError("known result paused")):
                with self.assertRaisesRegex(RuntimeError, "known result paused"):
                    await self.runtime.decide_lead_step(lead._decision(proposed, c.CollaborationConfirmationDecision.CONFIRM))
            result = None
        else:
            result = await self.runtime.decide_lead_step(lead._decision(proposed, c.CollaborationConfirmationDecision.CONFIRM))
        return conversation_id, proposed, result

    async def test_actionable_synthesis_requests_after_terminal_and_plain_synthesis_is_answer(self):
        before = self.snapshots()
        for actionable in (False, True):
            conversation_id, proposed, result = await self.consultation(actionable=actionable)
            turn = result.turn
            self.assertIs(turn.kind, c.LeadOutcomeKind.RECOMMENDATION if actionable else c.LeadOutcomeKind.ANSWER)
            events = self.history(conversation_id)
            synthesis = self.payload_events(conversation_id, c.CollaborationLeadSynthesisPayload)[0]
            self.assertFalse(hasattr(synthesis.payload, "recommended_action"))
            if actionable:
                request = turn.recommendation_request
                self.assertEqual((request.source_proposal_id, request.source_proposal_revision,
                    request.source_proposal_digest, request.source_execution_attempt_id),
                    (proposed.proposal.proposal_id, proposed.proposal.proposal_revision,
                     proposed.proposal.proposal_digest, synthesis.payload.execution_attempt_id))
                self.assertEqual(request.source_recommendation_event_id, synthesis.event_id)
                self.assertEqual(request.source_recommendation_digest, digest(DRAFT))
                self.assertNotEqual(turn.step_id, synthesis.step_id)
                self.assertEqual(events[-1].batch_id, synthesis.batch_id)
                self.assertIs(events[-2].payload.state, c.CollaborationStepState.COMPLETED)
                self.assertEqual(events[-2].step_id, proposed.step_id)
                self.assertEqual(request.physical_calls_spent, 3)
                original = tuple(c.encode_collaboration_event(e) for e in events)
                prepared = await self.confirm(turn)
                self.assertEqual(prepared.step_id, turn.step_id)
                self.assertEqual(tuple(c.encode_collaboration_event(e) for e in self.history(conversation_id)[:len(events)]), original)
                self.assertEqual(self.objective(conversation_id).draft_sources, request.draft_sources)
            else:
                self.assertIsNone(turn.recommendation_request)
                self.assertIs(turn.state, c.CollaborationStepState.COMPLETED)
            self.assertEqual(self.snapshots(), before)

    async def test_synthesis_next_intent_and_action_are_exclusive_with_bounded_accounting(self):
        conversation_id, _, mixed = await self.consultation(actionable=True, mixed=True)
        self.assertIsNone(mixed.turn.recommendation_request)
        result = self.payload_events(conversation_id, c.CollaborationExecutionResult)[0].payload
        self.assertIs(result.synthesis_outcome, c.CollaborationSynthesisOutcomeKind.FAILED)
        self.assertFalse(self.payload_events(conversation_id, c.LeadRecommendationRequestPayload))
        self.assertEqual(result.synthesis_submitted_physical_calls, 1)
        # Source consultation calls and its failed/replayed attempts all remain in the new step's budget.
        other, proposed, result = await self.consultation(actionable=True)
        request = result.turn.recommendation_request
        events = self.history(other)
        submitted = sum(type(e.payload) is c.CollaborationProviderCallPayload and
                        e.payload.state is c.CollaborationProviderCallState.SUBMITTED for e in events)
        self.assertEqual(request.physical_calls_spent, submitted)
        self.assertLessEqual(request.physical_calls_spent + request.max_preparation_physical_calls, 12)
        self.backend.enqueue(recommendation(message=DRAFT))
        revised = await self.runtime.decide_lead_recommendation(decision(result.turn, c.CollaborationConfirmationDecision.REVISE))
        self.assertEqual(revised.recommendation_request.physical_calls_spent, submitted + 1)
        self.assertEqual(revised.step_id, result.turn.step_id)
        self.assertEqual(len(self.payload_events(other, c.CollaborationExecutionResult)), 1)
        self.assertEqual(len(self.payload_events(other, c.CollaborationAssignmentDispatchPayload)), 1)

    async def test_known_synthesis_result_finalizes_action_once_without_provider_replay(self):
        conversation_id, proposed, _ = await self.consultation(actionable=True, pause=True)
        known = self.history(conversation_id)
        projection = c.project_collaboration_history(known)
        self.assertIs(projection.current_recommendation_state, c.LeadRecommendationState.PENDING)
        backend = lead._LeadBackend()
        restarted = StoryRuntime(self.workspace, backend)
        for _ in range(2):
            restored = restarted._finalize_known_result_locked(known, projection)
            self.assertEqual(restored.recommendation_request, projection.current_recommendation_request)
            self.assertEqual(self.history(conversation_id), known)
            self.assertEqual(backend.calls, [])
        request_event = known[-1]
        for changes in ({"source_proposal_id": "foreign"}, {"source_execution_attempt_id": "foreign"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                c.project_collaboration_history(rewrite_history(known, request_event.event_id,
                    payload=replace(request_event.payload, **changes)))
        # No replay occurs merely from rehydrating a pending action.
        self.assertFalse(hasattr(restarted.session(self.session_id), "recommendation_capability"))


class LeadRecommendationRecoveryTests(RecommendationCase):
    async def reference_offer(self, *, kind="prepare_consultation", objective_id=None):
        return await self.recommend(recommended_action=action(kind=kind,
            source_ids=("reference_1", "current_response"), objective_id=objective_id),
            text=f"Inspect ${{{OUTLINE}}} and recommend exactly one preparation.")

    def assert_failed_origin(self, conversation_id, turn, code):
        events = self.history(conversation_id)
        started = self.payload_events(conversation_id, c.LeadRecommendationStartedPayload)[-1]
        failures = self.payload_events(conversation_id, c.CollaborationLeadOutcomeFailurePayload)
        self.assertEqual(len(failures), 1)
        failure = failures[0]
        self.assertIs(failure.payload.code, code)
        self.assertEqual((failure.turn_id, failure.step_id), (started.turn_id, started.step_id))
        calls = [e for e in events if e.turn_id == started.turn_id and type(e.payload) is c.CollaborationProviderCallPayload]
        if calls:
            self.assertEqual(failure.payload.call_id, calls[-1].payload.call_id)
            self.assertEqual(failure.payload.invocation_id, calls[-1].payload.invocation_id)
        self.assertEqual(sum(type(e.payload) is c.CollaborationMessagePayload and
            e.direction is c.CollaborationDirection.USER_TO_LEAD for e in events), 1)
        self.assert_no_downstream(conversation_id)
        return failure

    async def test_confirmed_unstarted_restart_requires_new_request_and_confirmation(self):
        conversation_id, turn = await self.recommend()
        before = len(self.backend.calls)
        with mock.patch.object(self.runtime, "_run_confirmed_lead_preparation_locked", side_effect=RuntimeError("before preparation")):
            with self.assertRaisesRegex(RuntimeError, "before preparation"):
                await self.runtime.decide_lead_recommendation(decision(turn))
        known = self.history(conversation_id)
        self.assertIs(self.projection(conversation_id).current_recommendation_state, c.LeadRecommendationState.RUNNING)
        restarted = StoryRuntime(self.workspace, self.backend)
        self.assertEqual(self.history(conversation_id), known)
        closed = await restarted.decide_lead_recommendation(decision(turn))
        self.assertIs(closed.next_action, c.CollaborationNextAction.CONTINUE_WITH_LEAD)
        self.assertEqual(len(self.backend.calls), before)
        self.assert_failed_origin(conversation_id, turn, c.CollaborationLeadOutcomeFailureCode.REQUEST_NOT_SUBMITTED)
        self.assert_action_closed(conversation_id, c.LeadRecommendationState.INTERRUPTED)
        with self.assertRaises(BackendProtocolError):
            await restarted.decide_lead_recommendation(decision(turn))
        self.runtime = restarted
        _, fresh = await self.recommend(conversation_id)
        self.assertNotEqual(fresh.recommendation_request.request_id, turn.recommendation_request.request_id)
        self.assertEqual(len(self.payload_events(conversation_id, c.LeadRecommendationStartedPayload)), 1)
        await self.confirm(fresh)
        self.assertEqual(len(self.payload_events(conversation_id, c.LeadRecommendationStartedPayload)), 2)

    async def test_unsubmitted_attempt_requires_exact_confirm_origin_without_fake_user_event(self):
        for with_objective in (False, True):
            conversation_id = self.conversation("Unsubmitted exact origin")
            objective = None
            if with_objective:
                await self.start(conversation_id)
                objective = self.objective(conversation_id).facts
            _, turn = await self.recommend(conversation_id, recommended_action=action(
                kind="prepare_consultation", objective_id=None if objective is None else objective.objective_id))
            calls = len(self.backend.calls)
            error = BackendProtocolError("Synthetic binding rejected before submission")
            with mock.patch.object(self.backend, "bind", side_effect=error):
                result = await self.runtime.decide_lead_recommendation(decision(turn))
            self.assertEqual(len(self.backend.calls), calls)
            failure = self.payload_events(conversation_id, c.CollaborationLeadOutcomeFailurePayload)[0]
            self.assertIs(failure.payload.code, c.CollaborationLeadOutcomeFailureCode.REQUEST_NOT_SUBMITTED)
            self.assertIsNone(failure.payload.call_id)
            self.assertIsNone(failure.payload.invocation_id)
            self.assert_action_closed(conversation_id, c.LeadRecommendationState.INTERRUPTED)
            if objective is not None: self.assertEqual(self.objective(conversation_id).facts, objective)
            self.assertIsNone(result.recommendation_request)
            self.assertEqual(len(self.payload_events(conversation_id, c.LeadRecommendationDecisionPayload)), 1)

    async def test_submitted_failure_and_timeout_finalize_only_exact_terminal_reference_origin(self):
        from story_writing_agents.writing_artifacts.authorization import ReferenceInvocationState, ReferenceOperationState, ReferenceOperationFailure
        for timed_out in (False, True):
            conversation_id, turn = await self.reference_offer()
            before_calls, before_refs = len(self.backend.calls), len(self.backend.reference_calls)
            error = BackendProtocolError("Synthetic preparation provider failed")
            self.backend.enqueue(lead._SubmittedFailure(error, timed_out=timed_out))
            result = await self.runtime.decide_lead_recommendation(decision(turn))
            code = c.CollaborationLeadOutcomeFailureCode.PROVIDER_TIMED_OUT if timed_out else c.CollaborationLeadOutcomeFailureCode.PROVIDER_FAILED
            failure = self.assert_failed_origin(conversation_id, turn, code)
            self.assertIs(result.next_action, c.CollaborationNextAction.CONTINUE_WITH_LEAD)
            self.assert_action_closed(conversation_id, c.LeadRecommendationState.FAILED)
            closure = self.payload_events(conversation_id, c.LeadRecommendationResultPayload)[0]
            self.assertLess(failure.sequence, closure.sequence)
            self.assertEqual((len(self.backend.calls) - before_calls, len(self.backend.reference_calls) - before_refs), (1, 1))
            reference = self.runtime.inspect_reference_operation(self.backend.providers[-1].reference_operation_id)
            self.assertIs(reference.operation_events[-1].operation_state, ReferenceOperationState.TERMINATED)
            self.assertTrue(reference.invocation_events)
            for invocation in reference.invocation_events:
                self.assertIs(invocation[-1].invocation_state, ReferenceInvocationState.TERMINATED)
                self.assertEqual(invocation[-1].correlation.operation_id, reference.manifest.operation_id)
                self.assertEqual(invocation[-1].correlation.phase, "lead")
            old = self.history(conversation_id)
            restarted = StoryRuntime(self.workspace, self.backend)
            with self.assertRaises(BackendProtocolError):
                await restarted.decide_lead_recommendation(decision(turn))
            self.assertEqual(self.history(conversation_id), old)
        conversation_id, turn = await self.reference_offer()
        self.backend.enqueue(lead._SubmittedFailure(BackendProtocolError("Synthetic contradictory timeout"), timed_out=True))
        store_type = type(self.runtime.conversation_storage_services.event_store)
        append = store_type.append_batch
        def contradictory(store, batch):
            drafts = tuple(replace(e, payload=replace(e.payload, code=c.CollaborationLeadOutcomeFailureCode.PROVIDER_FAILED))
                if type(e.payload) is c.CollaborationLeadOutcomeFailurePayload else e for e in batch.events)
            return append(store, replace(batch, events=drafts))
        with mock.patch.object(store_type, "append_batch", new=contradictory):
            with self.assertRaises(ReferenceOperationFailure):
                await self.runtime.decide_lead_recommendation(decision(turn))
        self.assertFalse(self.payload_events(conversation_id, c.LeadRecommendationResultPayload))

    async def test_missing_corrupt_or_unresolved_origin_accounting_never_authorizes_retry(self):
        from story_writing_agents.writing_artifacts.authorization import ReferenceAuthorizationError, ReferenceAuthorizationErrorCode, ReferenceOperationFailure
        for fault in ("missing", "corrupt", "unfinished", "uncertain"):
            conversation_id, turn = await self.reference_offer()
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
            restarted = StoryRuntime(self.workspace, self.backend)
            blocked = await restarted.lead_turn(self.session_id, conversation_id, "Do not replay uncertain work.")
            self.assertIs(blocked.next_action, c.CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME if fault == "uncertain"
                          else c.CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY)
            self.assertEqual(len(self.backend.calls), calls)
            self.assertEqual(self.history(conversation_id), history)
            self.assertIsNone(blocked.recommendation_request)

    async def test_primary_reference_capacity_and_cancellation_keep_their_precedence(self):
        from story_writing_agents.conversations.session_context import SessionContextCapacityError
        from story_writing_agents.writing_artifacts.authorization import ReferenceAuthorizationError, ReferenceAuthorizationErrorCode, ReferenceOperationFailure
        errors = (ReferenceAuthorizationError(ReferenceAuthorizationErrorCode.DRIFT),
                  ReferenceAuthorizationError(ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH),
                  SessionContextCapacityError(), asyncio.CancelledError())
        for error in errors:
            conversation_id, turn = await self.reference_offer()
            self.backend.enqueue(lead._SubmittedFailure(error))
            expected = ReferenceOperationFailure if isinstance(error, ReferenceAuthorizationError) else type(error)
            with self.assertRaises(expected) as caught:
                await self.runtime.decide_lead_recommendation(decision(turn))
            if isinstance(error, ReferenceAuthorizationError):
                self.assertIs(caught.exception.__cause__, error)
                self.assertIs(caught.exception.code, error.code)
            else: self.assertIs(caught.exception, error)
            self.assertFalse(self.payload_events(conversation_id, c.LeadRecommendationResultPayload))
            self.assert_no_downstream(conversation_id)

    async def test_history_and_action_closure_failures_remain_blocked_until_local_finalization(self):
        for referenced, fail_lead in ((False, False), (True, False), (True, True)):
            conversation_id, turn = await self.reference_offer() if referenced else await self.recommend()
            self.backend.enqueue(lead._SubmittedFailure(BackendProtocolError("Synthetic closure failure")))
            store_type = type(self.runtime.conversation_storage_services.event_store)
            append = store_type.append_batch
            target = c.CollaborationLeadOutcomeFailurePayload if fail_lead else c.LeadRecommendationResultPayload
            def unavailable(store, batch):
                if any(type(e.payload) is target for e in batch.events):
                    raise OSError("Synthetic action history unavailable")
                return append(store, batch)
            with mock.patch.object(store_type, "append_batch", new=unavailable):
                if fail_lead:
                    blocked = await self.runtime.decide_lead_recommendation(decision(turn))
                    self.assertIs(blocked.next_action, c.CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY)
                else:
                    with self.assertRaisesRegex(OSError, "Synthetic action history"):
                        await self.runtime.decide_lead_recommendation(decision(turn))
            history, calls = self.history(conversation_id), len(self.backend.calls)
            self.assertFalse(self.payload_events(conversation_id, c.LeadRecommendationResultPayload))
            restarted = StoryRuntime(self.workspace, self.backend)
            result = await restarted.lead_turn(self.session_id, conversation_id, "Finalize known local failure only.")
            self.assertEqual(len(self.backend.calls), calls)
            self.assertIsNone(result.recommendation_request)
            if referenced:
                self.assertIn(result.next_action, (c.CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY,
                                                  c.CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME))
                self.assertEqual(self.history(conversation_id), history)
            else:
                self.assertIs(result.next_action, c.CollaborationNextAction.CONTINUE_WITH_LEAD)
                self.assert_action_closed(conversation_id, c.LeadRecommendationState.FAILED)
                with self.assertRaises(BackendProtocolError):
                    await restarted.decide_lead_recommendation(decision(turn))
                self.assertEqual(len(self.payload_events(conversation_id, c.LeadRecommendationResultPayload)), 1)


class LeadRecommendationAuthRecoveryTests(RecommendationCase):
    def auth_provider(self, *, prelude_failure=None):
        """Real context recovery with a synthetic native pre-execution rejection."""
        from contextlib import ExitStack, contextmanager
        from story_writing_agents.errors import ProviderAuthenticationRequiredError
        from story_writing_agents.agent_context.service import context_provider_failure_facts, record_context_provider_failure
        from story_writing_agents.provider_auth_recovery import provider_activity, validate_provider_replay
        invoke = lead._LeadContextProvider.invoke_context
        prepare = lead._LeadContextProvider.prepare_references
        pending = [prelude_failure] if prelude_failure is not None else []

        async def invoke_with_activity(provider, request):
            async with provider_activity(provider.provider_id) as generation:
                validate_provider_replay()
                try:
                    return await invoke(provider, request)
                except ProviderAuthenticationRequiredError as failure:
                    failure.auth_generation = generation
                    facts = context_provider_failure_facts(failure)
                    record_context_provider_failure(failure, replace(facts,
                        taint_existing_thread=(not failure.replay_safe or facts.timed_out),
                        submission_uncertain=(not failure.replay_safe or facts.submission_uncertain)))
                    raise

        async def prepare_with_activity(provider, request):
            async with provider_activity(provider.provider_id) as generation:
                validate_provider_replay()
                if pending:
                    failure = pending.pop()
                    provider.backend.reference_calls.append(request)
                    accounting = provider._account({"type": "object"}, {"synthetic_reference_count": 1},
                        call_kind=lead.ProviderCallKind.WRITING_ARTIFACT_REFERENCE_READ)
                    failure.auth_generation = generation
                    record_context_provider_failure(failure, lead.ContextProviderFailureFacts(accounting))
                    raise failure
                return await prepare(provider, request)

        @contextmanager
        def scope():
            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(lead._LeadContextProvider, "provider_id", "antigravity"))
                stack.enter_context(mock.patch.object(lead._LeadContextProvider, "invoke_context", new=invoke_with_activity))
                stack.enter_context(mock.patch.object(lead._LeadContextProvider, "prepare_references", new=prepare_with_activity))
                stack.enter_context(mock.patch.object(self.backend, "backend_overrides", {LEAD_ORCHESTRATOR: "antigravity"}))
                yield
        return scope()

    async def test_safe_auth_replay_preserves_confirm_and_revise_origins_and_budget(self):
        from tests.test_provider_auth_recovery import auth_failure, drive_recovery
        from story_writing_agents.agent_context import canonical_provider_json
        for choice, prelude in ((c.CollaborationConfirmationDecision.CONFIRM, False),
                                (c.CollaborationConfirmationDecision.REVISE, False),
                                (c.CollaborationConfirmationDecision.CONFIRM, True)):
            with self.subTest(choice=choice, prelude=prelude):
                with self.auth_provider():
                    conversation_id, turn = await self.recommend(recommended_action=action(
                        source_ids=("reference_1", "current_response") if prelude else ("current_response",)),
                        text=f"Review ${{{OUTLINE}}} before recommending." if prelude else "Offer a synthetic preparation.")
                error = auth_failure()
                producer = recommendation(message=DRAFT) if choice is c.CollaborationConfirmationDecision.REVISE else preparation(turn)
                if prelude:
                    self.backend.enqueue(producer)
                else:
                    self.backend.enqueue(lead._SubmittedFailure(error), producer)
                before_calls, before_refs, before = len(self.backend.calls), len(self.backend.reference_calls), self.snapshots()
                with self.auth_provider(prelude_failure=error if prelude else None):
                    result, keys = await drive_recovery(self.runtime.decide_lead_recommendation(decision(turn, choice)))
                self.assertEqual(keys, [("antigravity", 0)])
                physical = self.backend.calls[before_calls:]
                references = self.backend.reference_calls[before_refs:]
                self.assertEqual((len(physical), len(references)), (1, 2) if prelude else (2, 0))
                first, replay = references if prelude else physical
                self.assertNotEqual(first.call_id, replay.call_id)
                self.assertEqual(replace(first, call_id=replay.call_id), replay)
                if not prelude:
                    self.assertEqual(canonical_provider_json(first.payload), canonical_provider_json(replay.payload))
                self.assertEqual(len(self.payload_events(conversation_id, c.LeadRecommendationDecisionPayload)), 1)
                self.assertEqual(len(self.payload_events(conversation_id, c.LeadRecommendationStartedPayload)), 1)
                if choice is c.CollaborationConfirmationDecision.REVISE:
                    self.assertIsNotNone(result.recommendation_request)
                    self.assertIsNone(result.artifact_proposal)
                    self.assertEqual(result.recommendation_request.physical_calls_spent,
                                     turn.recommendation_request.physical_calls_spent + 2)
                else:
                    self.assertIsNotNone(result.artifact_proposal)
                    self.assertIsNone(result.recommendation_request)
                    self.assert_action_closed(conversation_id)
                start = self.payload_events(conversation_id, c.LeadRecommendationStartedPayload)[0]
                submitted = [e.payload for e in self.history(conversation_id) if e.turn_id == start.turn_id
                    and type(e.payload) is c.CollaborationProviderCallPayload
                    and e.payload.state is c.CollaborationProviderCallState.SUBMITTED]
                self.assertEqual(len(submitted), len(physical) + len(references))
                self.assertEqual(len({p.call_id for p in submitted}), len(submitted))
                self.assertLessEqual(len(submitted), turn.recommendation_request.max_preparation_physical_calls)
                self.assertEqual(self.snapshots(), before)
                self.assert_no_downstream(conversation_id)

    async def test_auth_replay_veto_cancellation_and_uncertainty_never_restore_action_authority(self):
        from tests.test_provider_auth_recovery import auth_failure, drive_recovery
        from story_writing_agents.errors import ProviderAuthenticationRequiredError
        from story_writing_agents.agent_context.service import context_provider_failure_facts
        for mode in ("admission_veto", "source_drift", "declined", "repeated", "unsafe_codex", "timeout", "cancel"):
            conversation_id = self.conversation("Synthetic authentication boundary")
            allowed = [True]
            error = auth_failure("codex" if mode == "unsafe_codex" else "antigravity",
                                 safe=mode != "unsafe_codex", validator=lambda: allowed[0])
            with self.auth_provider():
                _, turn = await self.recommend(conversation_id)
                repeated = auth_failure() if mode == "repeated" else preparation(turn)
                self.backend.enqueue(lead._SubmittedFailure(error, timed_out=mode == "timeout",
                    submission_uncertain=mode in {"timeout", "unsafe_codex"}),
                    lead._SubmittedFailure(repeated) if mode == "repeated" else repeated)
                before = len(self.backend.calls)
                def callback(key):
                    if mode == "admission_veto": allowed[0] = False
                    if mode == "source_drift": self.workspace.path(OUTLINE).write_text("Changed during authentication.\n")
                    if mode == "cancel": raise asyncio.CancelledError()
                    return mode != "declined"
                choice = (c.CollaborationConfirmationDecision.REVISE if mode in {"admission_veto", "repeated", "timeout"}
                          else c.CollaborationConfirmationDecision.CONFIRM)
                with self.assertRaises(asyncio.CancelledError if mode == "cancel" else ProviderAuthenticationRequiredError):
                    await drive_recovery(self.runtime.decide_lead_recommendation(decision(turn, choice)), callback)
                self.assertEqual(len(self.backend.calls) - before, 2 if mode == "repeated" else 1)
                self.assertEqual(len(self.payload_events(conversation_id, c.LeadRecommendationStartedPayload)), 1)
                self.assertFalse(self.payload_events(conversation_id, c.ArtifactReviewCandidatePayload))
                self.assertEqual(self.projection(conversation_id).artifact_objectives, ())
                self.assertFalse(error.validate_replay())
                if mode == "timeout": self.assertTrue(context_provider_failure_facts(error).timed_out)
                old_calls = len(self.backend.calls)
                restarted = StoryRuntime(self.workspace, self.backend)
                self.assertEqual(len(self.backend.calls), old_calls)
                self.assert_no_downstream(conversation_id)
                self.workspace.path(OUTLINE).write_bytes(b"")
        # A replayed reference prelude must still leave room for the substantive preparation.
        with self.auth_provider(), mock.patch.object(owner, "_CONTEXT_INVOCATION_CALL_LIMIT", 2):
            conversation_id, turn = await self.recommend(recommended_action=action(
                source_ids=("reference_1", "current_response")), text=f"Read ${{{OUTLINE}}} and offer a preparation.")
        error = auth_failure()
        self.backend.enqueue(preparation(turn))
        before_calls, before_refs = len(self.backend.calls), len(self.backend.reference_calls)
        with self.auth_provider(prelude_failure=error), self.assertRaises(ProviderAuthenticationRequiredError):
            await drive_recovery(self.runtime.decide_lead_recommendation(decision(turn)))
        self.assertEqual(len(self.backend.calls), before_calls)
        self.assertEqual(len(self.backend.reference_calls), before_refs + 1)
        self.assertFalse(self.payload_events(conversation_id, c.ArtifactReviewCandidatePayload))
