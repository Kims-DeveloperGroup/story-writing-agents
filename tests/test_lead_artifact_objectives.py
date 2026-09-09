"""Contextual draft follow-through and exact operation authority at public APIs."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from unittest import mock

from story_writing_agents import StoryRuntime
from story_writing_agents import conversations as c
from story_writing_agents.agents import PLOT_ARCHITECT
from story_writing_agents.writing_artifacts import ArtifactApplicationError, ArtifactApplicationErrorCode
from tests import test_artifact_objectives as objectives
from tests import test_lead_orchestration as lead
from tests import test_lead_artifact_application as application
from tests import test_lead_story_commit as commit


class LeadArtifactObjectiveTests(objectives.ObjectiveCase):
    async def test_abbreviated_approval_prepares_existing_empty_outline(self):
        self.assertEqual(self.workspace.path(objectives.OUTLINE).read_bytes(), b"")
        before = self.snapshots()
        conversation_id, offered = await self.start()
        prepared = await self.prepare(conversation_id)
        objective = self.objective(conversation_id)
        binding = objective.candidate_binding
        event = next(e for e in self.history(conversation_id) if e.event_id == binding.candidate_event_id)
        operation = event.payload.candidate.change_set.operations[0]
        self.assertEqual(operation.path, objectives.OUTLINE)
        self.assertEqual(operation.operation_kind.value, "replace")
        self.assertEqual(operation.content, offered.message.encode("utf-8"))
        self.assertEqual(objective.draft_sources[0].content_digest, objectives.digest(offered.message))
        self.assertEqual(binding.candidate_digest, event.payload.candidate.candidate_digest)
        self.assertIs(objective.state, c.ArtifactObjectiveState.OPEN)
        user_texts = [e.payload.content for e in self.history(conversation_id) if e.direction is c.CollaborationDirection.USER_TO_LEAD and type(e.payload) is c.CollaborationMessagePayload]
        self.assertNotIn(objectives.OUTLINE, user_texts[-1])
        self.assertNotIn(offered.message, user_texts[-1])
        self.assertEqual(self.snapshots(), before)
        self.assertIsNone(prepared.artifact_application_request)
        self.assertEqual({a for a, _ in self.backend.call_records}, {lead.LEAD_ORCHESTRATOR})

    async def test_consultation_and_review_leave_objective_open(self):
        conversation_id, _ = await self.start()
        objective_id = self.objective(conversation_id).facts.objective_id
        base = lead._proposal_outcome([{"agent_id": PLOT_ARCHITECT, "instruction": "Check the three-beat structure.", "input_ids": ["user_input"]}])
        def consult(call, provider):
            envelope = json.loads(base(call, provider))
            envelope["action"]["result"]["objective_intent"] = {"action": "continue", "objective_id": objective_id, "next_step": "consult", "draft_source_event_ids": []}
            return json.dumps(envelope)
        before = self.snapshots()
        self.backend.enqueue(consult)
        proposal = await self.runtime.lead_turn(self.session_id, conversation_id, "Review the structure first.")
        self.assertIs(self.objective(conversation_id).next_step, c.ArtifactObjectiveNextStep.CONSULT)
        self.backend.enqueue_for(PLOT_ARCHITECT, lead._specialist_outcome("supports"))
        self.backend.enqueue_for(lead.LEAD_ORCHESTRATOR, lead._synthesis_outcome())
        await self.runtime.decide_lead_step(lead._decision(proposal, c.CollaborationConfirmationDecision.CONFIRM))
        self.assertIs(self.objective(conversation_id).state, c.ArtifactObjectiveState.OPEN)
        reviewed = await self.review(await self.prepare(conversation_id, select=False))
        self.assertIsNotNone(reviewed.artifact_application_request)
        self.assertIs(self.objective(conversation_id).state, c.ArtifactObjectiveState.OPEN)
        self.assertEqual(self.snapshots(), before)

    async def test_only_matching_successful_apply_satisfies_objective(self):
        conversation_id, _ = await self.start()
        reviewed = await self.review(await self.prepare(conversation_id))
        before = self.snapshots()
        wrong = replace(application._application_decision(reviewed, c.ArtifactApplicationDecisionKind.APPLY), candidate_digest=objectives.digest("wrong"))
        with self.assertRaises(ValueError):
            await self.runtime.decide_artifact_application(wrong)
        self.assertIs(self.objective(conversation_id).state, c.ArtifactObjectiveState.OPEN)
        self.assertEqual(self.snapshots(), before)
        applied = await self.apply(reviewed)
        self.assertIs(applied.outcome.outcome, c.ArtifactApplicationOutcomeKind.APPLIED)
        objective = self.objective(conversation_id)
        self.assertIs(objective.state, c.ArtifactObjectiveState.SATISFIED)
        satisfied = next(e for e in self.history(conversation_id) if e.event_id == objective.satisfied_by_event_id)
        self.assertIsInstance(satisfied.payload, c.ArtifactApplicationResultPayload)
        self.assertEqual(satisfied.payload.application_id, applied.request.application_request.application_id)
        self.assertEqual(self.workspace.path(objectives.OUTLINE).read_text(), objectives.DRAFT)
        self.assertEqual(sum(isinstance(e.payload, c.ArtifactObjectivePayload) for e in self.history(conversation_id)), 2)
        from story_writing_agents.writing_artifacts import ArtifactApplicationState
        unchanged_payload = replace(satisfied.payload, outcome=c.ArtifactApplicationOutcomeKind.UNCHANGED,
                                    result=replace(satisfied.payload.result, state=ArtifactApplicationState.UNCHANGED))
        unchanged_history = objectives.rewrite_history(self.history(conversation_id), satisfied.event_id, payload=unchanged_payload)
        self.assertIs(c.project_collaboration_history(unchanged_history).artifact_objectives[0].state, c.ArtifactObjectiveState.SATISFIED)
        other_case = objectives.ObjectiveCase()
        other_case.setUp()
        # Adopt synchronous cleanup from this setup-only async fixture.
        for cleanup, args, kwargs in other_case._cleanups:
            self.addCleanup(cleanup, *args, **kwargs)
        other_case._cleanups.clear()
        origin, _ = await other_case.start()
        foreign, _ = await other_case.start(other_case.conversation("Other objective"))
        await other_case.apply(await other_case.review(await other_case.prepare(foreign)))
        self.assertIs(other_case.objective(origin).state, c.ArtifactObjectiveState.OPEN)
        self.assertIs(other_case.objective(foreign).state, c.ArtifactObjectiveState.SATISFIED)

    async def test_side_turn_switch_and_restart_preserve_owner_objective_without_authority(self):
        conversation_id, _ = await self.start()
        original = self.objective(conversation_id)
        self.backend.enqueue(lead._visible_outcome("answer", "A brief side answer."))
        await self.runtime.lead_turn(self.session_id, conversation_id, "What does a three-beat outline mean?")
        other = self.conversation("Separate conversation")
        self.backend.enqueue(lead._visible_outcome("answer", "The earlier draft remains visible."))
        await self.runtime.lead_turn(self.session_id, other, "Recall the earlier outline.")
        from story_writing_agents.agent_context import canonical_provider_json
        self.assertIn(original.facts.objective_id.encode(), canonical_provider_json(self.backend.calls[-1].payload))
        self.assertEqual(self.projection(other).artifact_objectives, ())
        self.assertEqual(self.objective(conversation_id), original)
        prefix = self.history(conversation_id)
        backend = lead._LeadBackend(persist_context_threads=True)
        backend.context_threads.update(self.backend.context_threads)
        self.backend = backend
        self.runtime = StoryRuntime(self.workspace, backend)
        self.assertEqual(backend.calls, [])
        self.assertEqual(self.history(conversation_id), prefix)
        before = self.snapshots()
        await self.prepare(conversation_id)
        self.assertEqual(self.objective(conversation_id).facts, original.facts)
        self.assertEqual(self.snapshots(), before)

    async def test_prepare_validation_cannot_downgrade_to_advice(self):
        from tests import test_artifact_proposal_orchestration as artifact
        conversation_id, _ = await self.start()
        before = self.objective(conversation_id)
        snapshots = self.snapshots()
        invalid_prepare = artifact._artifact_outcome(
            operations=[{"kind": "replace", "evidence_id": "not_offered", "new_text": objectives.DRAFT}],
            evidence_status="empty", evidence_ids=lambda call, material: [artifact._status_id(call, objectives.OUTLINE)],
            objective_intent={"action": "continue", "objective_id": before.facts.objective_id, "next_step": "prepare", "draft_source_event_ids": [self.projection(conversation_id).offered_draft_sources[-1].event_id]})
        self.backend.enqueue(invalid_prepare)
        self.backend.enqueue(lead._visible_outcome("answer", "The document is finished."))
        calls = len(self.backend.calls)
        result = await self.runtime.lead_turn(self.session_id, conversation_id, "Prepare that draft now.")
        self.assertEqual(len(self.backend.calls) - calls, 2)
        self.assertEqual(self.objective(conversation_id), before)
        self.assertIs(self.objective(conversation_id).state, c.ArtifactObjectiveState.OPEN)
        self.assertIsNone(result.artifact_proposal)
        self.assertIsNone(result.artifact_application_request)
        self.assertIs(result.next_action, c.CollaborationNextAction.RETRY_LEAD_TURN)
        self.assertFalse(any(isinstance(e.payload, c.ArtifactReviewCandidatePayload) for e in self.history(conversation_id)))
        self.assertEqual(self.snapshots(), snapshots)

    async def test_terminal_application_cycles_allow_fresh_reviewed_requests(self):
        for terminal in ("reject", "revise", "stale"):
            with self.subTest(terminal=terminal):
                conversation_id, _ = await self.start()
                reviewed = await self.review(await self.prepare(conversation_id))
                decision = application._application_decision(reviewed, c.ArtifactApplicationDecisionKind.REJECT if terminal == "reject" else c.ArtifactApplicationDecisionKind.REVISE if terminal == "revise" else c.ArtifactApplicationDecisionKind.APPLY, revision_request="Refine the ending." if terminal == "revise" else None)
                if terminal == "stale":
                    self.runtime = StoryRuntime(self.workspace, self.backend)
                result = await self.runtime.decide_artifact_application(decision)
                expected = {"reject": "rejected", "revise": "superseded", "stale": "stale"}[terminal]
                self.assertEqual(result.outcome.outcome.value, expected)
                again = await self.review(await self.prepare(conversation_id, select=False))
                self.assertNotEqual(again.artifact_application_request.application_request.application_id, reviewed.artifact_application_request.application_request.application_id)
                self.assertIs(self.projection(conversation_id).next_safe_action, c.CollaborationNextAction.SUBMIT_APPLICATION_DECISION)
                await self.apply(again, c.ArtifactApplicationDecisionKind.REJECT)

    async def test_terminal_commit_cycles_allow_fresh_reviewed_requests(self):
        for terminal in ("reject", "revise", "commit", "stale"):
            with self.subTest(terminal=terminal):
                case = objectives.ObjectiveCase()
                case.setUp()
                # Adopt synchronous cleanup from this setup-only async fixture.
                for cleanup, args, kwargs in case._cleanups:
                    self.addCleanup(cleanup, *args, **kwargs)
                case._cleanups.clear()
                conversation_id, _ = await case.start()
                applied = await case.apply(await case.review(await case.prepare(conversation_id)))
                self.assertIsNotNone(applied.commit_request)
                if terminal == "stale":
                    case.runtime = StoryRuntime(case.workspace, case.backend)
                kind = commit.StoryCommitDecision.COMMIT if terminal == "stale" else commit.StoryCommitDecision(terminal)
                decision = commit._commit_decision(applied, kind, revision_request="Use a clearer message." if terminal == "revise" else None)
                result = await case.runtime.decide_story_commit(decision)
                self.assertEqual(result.outcome.outcome.value, {"reject": "rejected", "revise": "superseded", "commit": "committed", "stale": "stale"}[terminal])
                next_reviewed = await case.review(await case.prepare(conversation_id, new=True, attached=True, text=objectives.DRAFT + terminal + "\n"))
                self.assertNotEqual(next_reviewed.step_id, applied.request.reviewed_proposal.step_id)
                await case.apply(next_reviewed, c.ArtifactApplicationDecisionKind.REJECT)

    async def test_pending_apply_and_commit_redisclose_exact_requests_without_calls(self):
        conversation_id, _ = await self.start()
        reviewed = await self.review(await self.prepare(conversation_id))
        for pending in (reviewed,):
            before, calls, history = self.snapshots(), len(self.backend.calls), self.history(conversation_id)
            for invoke in (lambda: self.runtime.lead_turn(self.session_id, conversation_id, "Yes, apply it."), lambda: self.runtime.propose_specialist_advisory(self.session_id, conversation_id, PLOT_ARCHITECT, "Please inspect it.")):
                result = await invoke()
                self.assertEqual(result.artifact_application_request, reviewed.artifact_application_request)
                self.assertEqual(self.history(conversation_id), history)
                self.assertEqual(len(self.backend.calls), calls)
                self.assertEqual(self.snapshots(), before)
        applied = await self.apply(reviewed)
        before, calls, history = self.snapshots(), len(self.backend.calls), self.history(conversation_id)
        for invoke in (lambda: self.runtime.lead_turn(self.session_id, conversation_id, "Commit it."), lambda: self.runtime.propose_specialist_advisory(self.session_id, conversation_id, PLOT_ARCHITECT, "Inspect it.")):
            result = await invoke()
            self.assertEqual(result.story_commit_request, applied.commit_request)
            self.assertIsNone(result.artifact_application_request)
            self.assertEqual(self.history(conversation_id), history)
            self.assertEqual(len(self.backend.calls), calls)
            self.assertEqual(self.snapshots(), before)

    async def test_unknown_and_recovery_operations_block_all_lead_entry_points(self):
        for code, expected in ((ArtifactApplicationErrorCode.INVALID_SCHEMA, c.CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME), (ArtifactApplicationErrorCode.RECOVERY_REQUIRED, c.CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY)):
            with self.subTest(code=code):
                conversation_id, _ = await self.start()
                reviewed = await self.review(await self.prepare(conversation_id))
                with mock.patch.object(self.runtime.artifacts, "apply_artifact_changes", side_effect=ArtifactApplicationError(code)):
                    await self.apply(reviewed)
                history, calls, before = self.history(conversation_id), len(self.backend.calls), self.snapshots()
                for runtime in (self.runtime, StoryRuntime(self.workspace, self.backend)):
                    for invoke in (lambda: runtime.lead_turn(self.session_id, conversation_id, "Prepare another one."), lambda: runtime.propose_specialist_advisory(self.session_id, conversation_id, PLOT_ARCHITECT, "Check again.")):
                        result = await invoke()
                        self.assertIs(result.next_action, expected)
                        self.assertIsNone(result.artifact_application_request)
                        self.assertEqual(self.history(conversation_id), history)
                        self.assertEqual(len(self.backend.calls), calls)
                        self.assertEqual(self.snapshots(), before)
                self.assertIs(self.objective(conversation_id).state, c.ArtifactObjectiveState.OPEN)

        from story_writing_agents.git.story_commit_contracts import StoryCommitError, StoryCommitFailureCode
        for code, expected in ((StoryCommitFailureCode.OUTCOME_UNKNOWN, c.CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME), (StoryCommitFailureCode.RECOVERY_REQUIRED, c.CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY)):
            with self.subTest(commit_failure=code):
                case = objectives.ObjectiveCase()
                case.setUp()
                # Adopt synchronous cleanup from this setup-only async fixture.
                for cleanup, args, kwargs in case._cleanups:
                    self.addCleanup(cleanup, *args, **kwargs)
                case._cleanups.clear()
                conversation_id, _ = await case.start()
                applied = await case.apply(await case.review(await case.prepare(conversation_id)))
                reviewed = applied.request.reviewed_proposal
                service = case.runtime._story_commit_services[(case.session_id, conversation_id, reviewed.step_id)]
                with mock.patch.object(type(service), "execute", side_effect=StoryCommitError(code)):
                    await case.runtime.decide_story_commit(commit._commit_decision(applied, commit.StoryCommitDecision.COMMIT))
                history, calls, before = case.history(conversation_id), len(case.backend.calls), case.snapshots()
                for runtime in (case.runtime, StoryRuntime(case.workspace, case.backend)):
                    for invoke in (lambda: runtime.lead_turn(case.session_id, conversation_id, "Continue."), lambda: runtime.propose_specialist_advisory(case.session_id, conversation_id, PLOT_ARCHITECT, "Inspect.")):
                        result = await invoke()
                        self.assertIs(result.next_action, expected)
                        self.assertIsNone(result.story_commit_request)
                        self.assertEqual(case.history(conversation_id), history)
                        self.assertEqual(len(case.backend.calls), calls)
                        self.assertEqual(case.snapshots(), before)

    async def test_operation_rollover_preserves_correlations_and_decision_adjacency(self):
        conversation_id, _ = await self.start()
        first = await self.review(await self.prepare(conversation_id))
        await self.apply(first, c.ArtifactApplicationDecisionKind.REJECT)
        second = await self.review(await self.prepare(conversation_id, select=False))
        await self.apply(second, c.ArtifactApplicationDecisionKind.REJECT)
        events = self.history(conversation_id)
        decisions = [e for e in events if isinstance(e.payload, c.ArtifactApplicationDecisionPayload)]
        self.assertEqual(len(decisions), 2)
        for decision in decisions:
            request, result = events[decision.sequence - 2], events[decision.sequence]
            self.assertEqual(decision.payload.confirmation_request_event_id, request.event_id)
            self.assertEqual(result.payload.decision_event_id, decision.event_id)
            self.assertNotEqual(decision.turn_id, request.turn_id)
            self.assertEqual(result.turn_id, decision.turn_id)
        bad = replace(decisions[-1].payload, confirmation_request_event_id=events[decisions[0].sequence - 2].event_id)
        with self.assertRaises(ValueError):
            c.project_collaboration_history(objectives.rewrite_history(events, decisions[-1].event_id, payload=bad))
        before = self.history(conversation_id)
        with self.assertRaises(ValueError):
            await self.apply(first, c.ArtifactApplicationDecisionKind.REJECT)
        self.assertEqual(self.history(conversation_id), before)

    async def test_objective_append_failure_is_atomic_and_releases_session_lock(self):
        conversation_id, _ = await self.start()
        before = self.objective(conversation_id)
        append = self.runtime._append_drafts
        attempted = []
        def fail_objective(expected, drafts, **kwargs):
            if any(isinstance(d.payload, c.ArtifactObjectivePayload) for d in drafts):
                attempted.append(drafts)
                raise OSError("Synthetic objective batch persistence failure")
            return append(expected, drafts, **kwargs)
        with mock.patch.object(self.runtime, "_append_drafts", side_effect=fail_objective):
            try:
                await self.prepare(conversation_id)
            except (OSError, AssertionError):
                pass
        self.assertEqual(len(attempted), 1)
        persisted_ids = {e.event_id for e in self.history(conversation_id)}
        self.assertFalse(persisted_ids.intersection(d.event_id for d in attempted[0]))
        self.assertEqual(self.objective(conversation_id), before)
        self.assertFalse(self.runtime._conversation_lock(self.session_id, conversation_id).locked())
        # A new explicit call establishes lock release without replaying the failed result.
        self.backend.enqueue(self.question())
        await asyncio.wait_for(self.runtime.lead_turn(self.session_id, conversation_id, "New explicit retry."), timeout=10)

    async def test_read_only_timeout_preserves_open_objective_and_explicit_retry(self):
        conversation_id, _ = await self.start()
        before = self.objective(conversation_id)
        self.backend.enqueue(lead._SubmittedFailure(TimeoutError("synthetic timeout"), timed_out=True))
        failed = await self.runtime.lead_turn(self.session_id, conversation_id, "Continue after this timed-out input.")
        self.assertIs(failed.next_action, c.CollaborationNextAction.RETRY_LEAD_TURN)
        self.assertEqual(self.objective(conversation_id), before)
        prefix, calls = self.history(conversation_id), len(self.backend.calls)
        self.runtime = StoryRuntime(self.workspace, self.backend)
        self.assertEqual(len(self.backend.calls), calls)
        await self.prepare(conversation_id)
        self.assertEqual(self.history(conversation_id)[:len(prefix)], prefix)
        self.assertEqual(self.objective(conversation_id).facts, before.facts)
        failures = [e.payload for e in self.history(conversation_id) if isinstance(e.payload, c.CollaborationLeadOutcomeFailurePayload)]
        self.assertEqual([f.code for f in failures], [c.CollaborationLeadOutcomeFailureCode.PROVIDER_TIMED_OUT])

    async def test_unoffered_objective_and_draft_references_fail_without_transition(self):
        conversation_id, _ = await self.start()
        other, _ = await self.start(self.conversation("Other owner"))
        objective = self.objective(conversation_id)
        foreign = self.objective(other)
        foreign_source = self.projection(other).offered_draft_sources[-1]
        for objective_id, sources in (("invented_objective", []), (foreign.facts.objective_id, []), (objective.facts.objective_id, ["invented_draft"]), (objective.facts.objective_id, [foreign_source.event_id])):
            with self.subTest(objective_id=objective_id, sources=sources):
                bad = self.question(intent={"action": "continue", "objective_id": objective_id, "next_step": "clarify", "draft_source_event_ids": sources})
                self.backend.enqueue(bad, bad)
                result = await self.runtime.lead_turn(self.session_id, conversation_id, "Try an unoffered reference.")
                self.assertIs(result.next_action, c.CollaborationNextAction.RETRY_LEAD_TURN)
                self.assertEqual(self.objective(conversation_id), objective)
                self.assertEqual(self.workspace.path(objectives.OUTLINE).read_bytes(), b"")
        from tests import test_artifact_proposal_orchestration as artifact
        source, destination = "manuscript/part-01/first.md", "manuscript/part-01/renamed.md"
        self.workspace.path(source).parent.mkdir(parents=True, exist_ok=True)
        self.workspace.path(source).write_text("Synthetic rename target.\n", encoding="utf-8")
        rename_conversation = self.conversation("Rename endpoint scope")
        def outcome(paths):
            return artifact._artifact_outcome(
                operations=lambda call, material: [{"kind": "rename", "evidence_id": artifact._reference_ids(material)[0], "suggested_destination": destination}],
                objective_intent={"action": "start", "request": "Rename the exact synthetic scene.", "target_paths": paths, "next_step": "prepare", "draft_source_event_ids": []})
        before = self.snapshots()
        wrong = outcome([source])
        self.backend.enqueue(wrong, wrong)
        refused = await self.runtime.lead_turn(self.session_id, rename_conversation, f"Rename ${{{source}}} to {destination}.")
        self.assertIs(refused.next_action, c.CollaborationNextAction.RETRY_LEAD_TURN)
        self.assertEqual(self.projection(rename_conversation).artifact_objectives, ())
        self.backend.enqueue(outcome([source, destination]))
        prepared = await self.runtime.lead_turn(self.session_id, rename_conversation, f"Prepare the complete rename of ${{{source}}} to {destination}.")
        self.assertIs(prepared.kind, c.LeadOutcomeKind.PROPOSAL)
        self.assertEqual(self.objective(rename_conversation).facts.target_paths, (source, destination))
        self.assertEqual(self.snapshots(), before)
