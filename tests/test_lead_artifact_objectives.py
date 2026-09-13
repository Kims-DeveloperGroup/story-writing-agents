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
        selected = next(e for e in self.history(conversation_id)
                        if e.event_id == objective.draft_sources[0].event_id)
        self.assertIsInstance(selected.payload, c.CollaborationLeadSynthesisPayload)
        self.assertEqual(selected.payload.message, objectives.DRAFT)
        self.assertEqual(operation.path, objectives.OUTLINE)
        self.assertEqual(operation.operation_kind.value, "replace")
        self.assertEqual(operation.content, selected.payload.message.encode("utf-8"))
        self.assertEqual(objective.draft_sources[0].content_digest, objectives.digest(selected.payload.message))
        self.assertNotEqual(offered.message, selected.payload.message)
        self.assertEqual(binding.candidate_digest, event.payload.candidate.candidate_digest)
        self.assertIs(objective.state, c.ArtifactObjectiveState.OPEN)
        user_texts = [e.payload.content for e in self.history(conversation_id) if e.direction is c.CollaborationDirection.USER_TO_LEAD and type(e.payload) is c.CollaborationMessagePayload]
        self.assertNotIn(objectives.OUTLINE, user_texts[-1])
        self.assertNotIn(selected.payload.message, user_texts[-1])
        self.assertEqual(self.snapshots(), before)
        self.assertIsNone(prepared.artifact_application_request)
        self.assertEqual([a for a, _ in self.backend.call_records], [lead.LEAD_ORCHESTRATOR, PLOT_ARCHITECT, lead.LEAD_ORCHESTRATOR, lead.LEAD_ORCHESTRATOR])

    async def test_consultation_and_review_leave_objective_open(self):
        conversation_id, _ = await self.start()
        objective_id = self.objective(conversation_id).facts.objective_id
        base = lead._proposal_outcome([{"agent_id": PLOT_ARCHITECT, "instruction": "Check the three-beat structure.", "input_ids": ["user_input"]}])
        def consult(call, provider):
            envelope = json.loads(base(call, provider))
            intent = {"action": "continue", "objective_id": objective_id, "next_step": "consult", "draft_source_event_ids": []}
            envelope["action"]["result"]["proposal"]["objective_intent"] = lead._compact_objective(intent)
            scope = self.scope(call, intent=intent)
            envelope["action"]["result"]["authoring_scope"] = lead._compact_scope(scope)
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
        origin_request = (
            f"Draft {objectives.OUTLINE} from this brief: a lantern vanishes, "
            "its keeper is found, and it returns."
        )
        origin_judgment = next(
            event.payload
            for event in self.history(conversation_id)
            if isinstance(event.payload, c.CollaborationLeadJudgmentPayload)
        )
        self.assertEqual(original.facts.request, origin_request)
        self.assertNotEqual(original.facts.request, origin_judgment.message)
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
        from story_writing_agents.artifact_proposals import ArtifactProposalError, ArtifactProposalErrorCode
        conversation_id, _ = await self.start()
        before = self.objective(conversation_id)
        snapshots = self.snapshots()
        invalid_prepare = artifact._artifact_outcome(
            operations=[{"kind": "replace", "evidence_id": "not_offered", "new_text": objectives.DRAFT}],
            evidence_status="empty", evidence_ids=lambda call, material: [artifact._status_id(call, objectives.OUTLINE)],
            objective_intent={"action": "continue", "objective_id": before.facts.objective_id, "next_step": "prepare", "draft_source_event_ids": [self.projection(conversation_id).offered_draft_sources[-1].event_id]})
        def current_task_prepare(call, provider):
            envelope = json.loads(invalid_prepare(call, provider))
            envelope["action"]["result"]["authoring_scope"] = lead._compact_scope(
                self.scope(call, intent={"objective_id": before.facts.objective_id})
            )
            return json.dumps(envelope)
        self.backend.enqueue(current_task_prepare)
        self.backend.enqueue(lead._visible_outcome("answer", "The document is finished."))
        calls = len(self.backend.calls)
        construct = self.runtime._construct_artifact_review_candidate
        attempted, rejected = [], []
        def observe_construction(**kwargs):
            attempted.append(kwargs["intent"])
            try:
                return construct(**kwargs)
            except ArtifactProposalError as error:
                rejected.append(error.code)
                raise
        with mock.patch.object(self.runtime, "_construct_artifact_review_candidate", side_effect=observe_construction):
            result = await self.runtime.lead_turn(self.session_id, conversation_id, "Prepare that draft now.")
        self.assertEqual(len(attempted), 1)
        self.assertEqual(attempted[0].operations[0].evidence_id, "not_offered")
        self.assertEqual(rejected, [ArtifactProposalErrorCode.INVALID_EVIDENCE])
        self.assertEqual(len(self.backend.calls) - calls, 2)
        self.assertEqual(self.objective(conversation_id), before)
        self.assertIs(self.objective(conversation_id).state, c.ArtifactObjectiveState.OPEN)
        self.assertIsNone(result.artifact_proposal)
        self.assertIsNone(result.artifact_application_request)
        self.assertIs(result.next_action, c.CollaborationNextAction.CONTINUE_WITH_LEAD)
        self.assertIn("after one repair attempt", result.message)
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
        prepared = await self.prepare(conversation_id)
        reviewed = await self.review(prepared)
        for pending in (reviewed,):
            before, calls, history = self.snapshots(), len(self.backend.calls), self.history(conversation_id)
            for invoke in (lambda: self.runtime.lead_turn(self.session_id, conversation_id, "Yes, apply it."), lambda: self.runtime.propose_specialist_advisory(self.session_id, conversation_id, PLOT_ARCHITECT, "Please inspect it.", authoring_scope=prepared.proposal.authoring_scope, review_basis=prepared.proposal.assignments[0].review_basis)):
                result = await invoke()
                self.assertEqual(result.artifact_application_request, reviewed.artifact_application_request)
                self.assertEqual(self.history(conversation_id), history)
                self.assertEqual(len(self.backend.calls), calls)
                self.assertEqual(self.snapshots(), before)
        applied = await self.apply(reviewed)
        before, calls, history = self.snapshots(), len(self.backend.calls), self.history(conversation_id)
        for invoke in (lambda: self.runtime.lead_turn(self.session_id, conversation_id, "Commit it."), lambda: self.runtime.propose_specialist_advisory(self.session_id, conversation_id, PLOT_ARCHITECT, "Inspect it.", authoring_scope=prepared.proposal.authoring_scope, review_basis=prepared.proposal.assignments[0].review_basis)):
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
                prepared = await self.prepare(conversation_id)
                reviewed = await self.review(prepared)
                with mock.patch.object(self.runtime.artifacts, "apply_artifact_changes", side_effect=ArtifactApplicationError(code)):
                    await self.apply(reviewed)
                history, calls, before = self.history(conversation_id), len(self.backend.calls), self.snapshots()
                for runtime in (self.runtime, StoryRuntime(self.workspace, self.backend)):
                    for invoke in (lambda: runtime.lead_turn(self.session_id, conversation_id, "Prepare another one."), lambda: runtime.propose_specialist_advisory(self.session_id, conversation_id, PLOT_ARCHITECT, "Check again.", authoring_scope=prepared.proposal.authoring_scope, review_basis=prepared.proposal.assignments[0].review_basis)):
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
                prepared = await case.prepare(conversation_id)
                applied = await case.apply(await case.review(prepared))
                reviewed = applied.request.reviewed_proposal
                service = case.runtime._story_commit_services[(case.session_id, conversation_id, reviewed.step_id)]
                with mock.patch.object(type(service), "execute", side_effect=StoryCommitError(code)):
                    await case.runtime.decide_story_commit(commit._commit_decision(applied, commit.StoryCommitDecision.COMMIT))
                history, calls, before = case.history(conversation_id), len(case.backend.calls), case.snapshots()
                for runtime in (case.runtime, StoryRuntime(case.workspace, case.backend)):
                    for invoke in (lambda: runtime.lead_turn(case.session_id, conversation_id, "Continue."), lambda: runtime.propose_specialist_advisory(case.session_id, conversation_id, PLOT_ARCHITECT, "Inspect.", authoring_scope=prepared.proposal.authoring_scope, review_basis=prepared.proposal.assignments[0].review_basis)):
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

    async def test_unoffered_objectives_and_target_selectors_fail_without_transition(self):
        conversation_id, _ = await self.start()
        other, _ = await self.start(self.conversation("Other owner"))
        objective = self.objective(conversation_id)
        foreign = self.objective(other)
        for objective_id in ("invented_objective", foreign.facts.objective_id):
            with self.subTest(objective_id=objective_id):
                bad = self.question(intent={"action": "continue", "objective_id": objective_id, "next_step": "clarify", "draft_source_event_ids": []})
                self.backend.enqueue(bad, bad)
                result = await self.runtime.lead_turn(self.session_id, conversation_id, "Try an unoffered reference.")
                self.assertIs(result.next_action, c.CollaborationNextAction.CONTINUE_WITH_LEAD)
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
        self.assertIs(refused.next_action, c.CollaborationNextAction.CONTINUE_WITH_LEAD)
        self.assertEqual(self.projection(rename_conversation).artifact_objectives, ())
        self.backend.enqueue(outcome([source, destination]))
        prepared = await self.runtime.lead_turn(self.session_id, rename_conversation, f"Prepare the complete rename of ${{{source}}} to {destination}.")
        self.assertIs(prepared.kind, c.LeadOutcomeKind.PROPOSAL)
        self.assertEqual(self.objective(rename_conversation).facts.target_paths, (source, destination))
        self.assertEqual(self.snapshots(), before)

    def current_prepare_response(self, intent, *, task_objective_id=None, mutate=None):
        from tests import test_artifact_proposal_orchestration as artifact
        base = artifact._artifact_outcome(
            operations=lambda call, _material: [{"kind": "replace", "evidence_id": artifact._status_id(call, objectives.OUTLINE), "new_text": objectives.DRAFT}],
            evidence_status="empty", evidence_ids=lambda call, _material: [artifact._status_id(call, objectives.OUTLINE)],
            objective_intent=intent)
        def respond(call, provider):
            envelope = json.loads(base(call, provider))
            value = envelope["action"]["result"]
            value["authoring_scope"] = lead._compact_scope(
                self.scope(
                    call,
                    intent={"objective_id": task_objective_id or intent.get("objective_id")},
                )
            )
            if mutate is not None:
                mutate(value)
            return json.dumps(envelope)
        return respond

    async def test_clarification_requires_current_draft_before_prepare_review_apply(self):
        conversation, _ = await self.clarify()
        objective = self.objective(conversation)
        source = self.projection(conversation).offered_draft_sources[-1]
        intent = {"action": "continue", "objective_id": objective.facts.objective_id,
                  "next_step": "prepare", "draft_source_event_ids": [source.event_id]}
        before = self.snapshots()
        invalid = self.current_prepare_response(intent)
        self.backend.enqueue(invalid, invalid)
        refused = await self.runtime.lead_turn(self.session_id, conversation, "Prepare it before drafting.")
        self.assertIs(refused.next_action, c.CollaborationNextAction.CONTINUE_WITH_LEAD)
        self.assertEqual(self.objective(conversation), objective)
        self.assertFalse(any(isinstance(e.payload, c.ArtifactReviewCandidatePayload) for e in self.history(conversation)))
        self.assertEqual(self.snapshots(), before)
        await self.start(conversation, objective_id=objective.facts.objective_id)
        prepared = await self.prepare(conversation)
        self.assertEqual(prepared.proposal.authoring_scope.task_id, self.objective_tasks[objective.facts.objective_id])
        self.assertEqual(prepared.proposal.participants, (PLOT_ARCHITECT,))
        self.assertTrue(all(a.mode.value == "review" for a in prepared.proposal.assignments))
        reviewed = await self.review(prepared)
        self.assertIs(self.objective(conversation).state, c.ArtifactObjectiveState.OPEN)
        self.assertEqual(self.snapshots(), before)
        await self.apply(reviewed)
        self.assertIs(self.objective(conversation).state, c.ArtifactObjectiveState.SATISFIED)
        self.assertEqual(self.workspace.path(objectives.OUTLINE).read_text(), objectives.DRAFT)
        self.assertEqual(self.snapshots()[1:], before[1:])

    async def test_objective_prepare_rejects_other_task_source_and_wrong_revision(self):
        conversation, _ = await self.start()
        first = self.objective(conversation)
        await self.start(conversation)
        second = self.objective(conversation)
        other_source = self.projection(conversation).offered_draft_sources[-1]
        first_source = next(s for s in self.projection(conversation).offered_draft_sources
                            if s.event_id in {e.event_id for e in self.history(conversation)
                                if isinstance(e.payload, c.CollaborationLeadSynthesisPayload)
                                and e.payload.authoring_scope.task_id == self.objective_tasks[first.facts.objective_id]})
        base_intent = {"action": "continue", "objective_id": first.facts.objective_id, "next_step": "prepare"}
        cases = (
            (
                self.current_prepare_response(
                    dict(base_intent, draft_source_event_ids=[other_source.event_id]),
                    task_objective_id=second.facts.objective_id,
                ),
                c.CollaborationNextAction.CONTINUE_WITH_LEAD,
            ),
            (
                self.current_prepare_response(
                    dict(base_intent, draft_source_event_ids=[first_source.event_id]),
                    mutate=lambda value: value["authoring_scope"].update(
                        task_revision=value["authoring_scope"]["task_revision"] + 1
                    ),
                ),
                c.CollaborationNextAction.RETRY_LEAD_TURN,
            ),
        )
        before = self.snapshots()
        objectives_before = self.projection(conversation).artifact_objectives
        for invalid, expected_next_action in cases:
            self.backend.enqueue(invalid, invalid)
            refused = await self.runtime.lead_turn(self.session_id, conversation, "Prepare the first objective using that selection.")
            self.assertIs(refused.next_action, expected_next_action)
            self.assertIsNone(refused.artifact_proposal)
            self.assertEqual(self.projection(conversation).artifact_objectives, objectives_before)
            self.assertEqual(self.snapshots(), before)

    async def test_supplied_finished_candidate_has_exact_review_without_fabricated_draft(self):
        conversation = self.conversation()
        intent = {"action": "start", "request": "Review this finished user candidate.",
                  "target_paths": [objectives.OUTLINE], "next_step": "prepare", "draft_source_event_ids": []}
        self.backend.enqueue(self.current_prepare_response(intent))
        before = self.snapshots()
        prepared = await self.runtime.lead_turn(self.session_id, conversation, "Review this finished outline candidate: " + objectives.DRAFT)
        self.assertIs(prepared.kind, c.LeadOutcomeKind.PROPOSAL, prepared)
        self.assertEqual(prepared.proposal.participants, (PLOT_ARCHITECT,))
        self.assertTrue(all(a.mode.value == "review" for a in prepared.proposal.assignments))
        self.assertFalse(any(isinstance(e.payload, c.CollaborationSpecialistReplyPayload) for e in self.history(conversation)))
        self.assertIsNone(prepared.artifact_application_request)
        self.assertEqual(self.snapshots(), before)
        reviewed = await self.review(prepared)
        self.assertIs(self.objective(conversation).state, c.ArtifactObjectiveState.OPEN)
        self.assertEqual(self.snapshots(), before)
        await self.apply(reviewed)
        self.assertIs(self.objective(conversation).state, c.ArtifactObjectiveState.SATISFIED)

    async def test_minimal_live_variant_preserves_internal_positional_contract(self):
        from dataclasses import fields
        from story_writing_agents.specialist_routing import AuthoringScope, AuthoringActivity
        objective = c.LeadObjectiveIntent(c.LeadObjectiveAction.START, request="Clarify the outline.",
            target_paths=(objectives.OUTLINE,), next_step=c.ArtifactObjectiveNextStep.CLARIFY)
        scope = AuthoringScope(AuthoringActivity.DRAFT, (objectives.OUTLINE,), (), "Clarify the synthetic outline.", "task_fixture", 1)
        outcome = c.LeadProviderOutcome(c.LeadOutcomeKind.QUESTION, "Which ending?", c.LeadOutcomeScope.STORY_SPECIFIC,
            c.LeadEvidenceStatus.INCOMPLETE, (), None, None, objective, None, authoring_scope=scope)
        self.assertIs(outcome.objective_intent, objective)
        self.assertIsNone(outcome.recommended_action)
        self.assertIs(outcome.authoring_scope, scope)
        self.assertEqual(tuple((f.name, f.kw_only) for f in fields(outcome))[-4:],
            (("objective_intent", False), ("recommended_action", False),
             ("authoring_scope", True), ("trace", True)))
        observed = []
        original = self.question(intent={"action": "start", "request": "Clarify the outline.", "target_paths": [objectives.OUTLINE],
            "next_step": "clarify", "draft_source_event_ids": []}, message="Which ending?")
        def capture(call, provider):
            material = lead._workflow_material(call.payload)
            self.assertEqual(material["contract_version"], "lead-outcome-v5")
            envelope = json.loads(original(call, provider))
            observed.append(envelope["action"]["result"])
            return json.dumps(envelope)
        self.backend.enqueue(capture)
        result = await self.runtime.lead_turn(self.session_id, self.conversation(), "Clarify the ending.")
        self.assertIs(result.kind, c.LeadOutcomeKind.QUESTION)
        self.assertEqual(set(observed[0]), {"authoring_scope", "kind", "message", "objective_intent"})
        for unscoped in (
            {"kind": "question", "message": "Which ending?"},
            {"message": "Visible."},
        ):
            with self.assertRaises(lead.BackendProtocolError):
                StoryRuntime._decode_lead_outcome(json.dumps(unscoped))
        scoped_question = dict(observed[0])
        scoped_question.pop("objective_intent")
        decoded_scoped_question = StoryRuntime._decode_lead_outcome(
            json.dumps(scoped_question)
        )
        self.assertIs(decoded_scoped_question.kind, c.LeadOutcomeKind.QUESTION)
        self.assertIsNone(decoded_scoped_question.objective_intent)
        self.assertIsNotNone(decoded_scoped_question.authoring_scope)
        for key in ("authoring_scope", "kind", "message"):
            malformed = dict(observed[0]); malformed.pop(key)
            with self.assertRaises(lead.BackendProtocolError):
                StoryRuntime._decode_lead_outcome(json.dumps(malformed))
