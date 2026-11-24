"""Focused public-runtime coverage for separately confirmed local story commits."""

from __future__ import annotations

from story_writing_agents.errors import BackendProtocolError
import story_writing_agents.lead_task_transitions as task
from tests.lead_fixtures.preparation_data import (
    discussion_reply as _discussion_reply,
    submitted_calls as _submitted_calls,
)
from tests.lead_fixtures.provider_responses import SubmittedFailure as _SubmittedFailure

from story_writing_agents import lead_exploration as exploration

import asyncio
import os
import subprocess
import unittest
from contextlib import contextmanager
from dataclasses import asdict, replace
from unittest import mock

from story_writing_agents import StoryRuntime, Workspace
from story_writing_agents.conversations import context as context_module
from story_writing_agents.artifact_transactions.prepared_story_commit import PreparedStoryCommit
from story_writing_agents.agents import LEAD_ORCHESTRATOR
from story_writing_agents.conversations import (
    ArtifactApplicationDecision,
    ArtifactApplicationDecisionKind,
    ArtifactApplicationOutcomeKind,
    ArtifactApplicationResultPayload,
    CollaborationConfirmationDecision,
    CollaborationNextAction,
    CollaborationSideEffect,
    StoryCommitDecisionRequest,
    StoryCommitNextAction,
    StoryCommitOutcomeKind,
    StoryCommitRequestPayload,
    StoryCommitResultPayload,
    SynchronizationPayload,
    decode_collaboration_event,
    encode_collaboration_event,
    project_collaboration_history,
    synchronization_event_ref,
)
from story_writing_agents.conversations.views.decision_results import StoryCommitQualificationBlocked
from story_writing_agents.git.process import GitProcessRunner
from story_writing_agents.git.story_commit_contracts import (
    StoryCommitDecision,
    StoryCommitState,
)
from story_writing_agents.workspace.session_resources import provision_session_resources

from tests import test_lead_artifact_application_authority as lead_artifact_application_tests
from tests.known_result_finalization_fixture import seed_reviewed_artifact
from tests.helpers import pristine_initialized_workspace
from tests.provider_wire_fixture import selection_plan, lead_fixture_result, lead_fixture_response
from tests.lead_fixtures.artifact_review import (
    ARTIFACT_FIXTURE_RULES_PATH,
    artifact_review_outcome,
    artifact_synthesis_with_current_reads,
    _artifact_synthesis_outcome,
    _reference_ids,
)
from tests.lead_fixtures.runtime_setup import LeadOrchestrationTestCase
from tests.lead_fixtures.provider_responses import LeadBackend as _LeadBackend
from tests.lead_fixtures.preparation_data import (
    lead_confirmation_decision,
    lead_specialist_outcome,
    lead_fixture_authoring_scope,
)


def _commit_decision(
    applied: object,
    kind: StoryCommitDecision,
    *,
    confirmation_id: str = "commit_confirmation",
    revision_request: str | None = None,
) -> StoryCommitDecisionRequest:
    request = applied.commit_request  # type: ignore[attr-defined]
    assert request is not None
    reviewed = applied.request.reviewed_proposal  # type: ignore[attr-defined]
    return StoryCommitDecisionRequest(
        session_id=reviewed.session_id, conversation_id=reviewed.conversation_id,
        step_id=reviewed.step_id,
        confirmation_request_id=request.confirmation_request_id,
        application_result_event_id=request.application_result_event_id,
        candidate_id=request.candidate.candidate_id, request_digest=request.request_digest,
        confirmation_id=confirmation_id,
        kind=kind,
        revision_request=revision_request,
    )


def _application_replay(applied: object) -> ArtifactApplicationDecision:
    request = applied.request  # type: ignore[attr-defined]
    reviewed = request.reviewed_proposal
    application = request.application_request
    return ArtifactApplicationDecision(
        session_id=reviewed.session_id,
        conversation_id=reviewed.conversation_id,
        step_id=reviewed.step_id,
        confirmation_request_id=request.confirmation_request_id,
        reviewed_proposal_id=reviewed.proposal_id,
        reviewed_proposal_revision=reviewed.proposal_revision,
        reviewed_proposal_digest=reviewed.proposal_digest,
        candidate_id=application.candidate_id,
        candidate_revision=application.candidate_revision,
        candidate_digest=application.candidate_digest,
        application_id=application.application_id,
        application_request_digest=request.application_request_digest,
        kind=ArtifactApplicationDecisionKind.APPLY,
    )


class LeadStoryCommitTests(LeadOrchestrationTestCase):
    artifact_snapshot = lead_artifact_application_tests.LeadArtifactApplicationTests.artifact_snapshot

    def setUp(self) -> None:
        main = pristine_initialized_workspace(self)
        self.session_id = "story_session"
        ready = provision_session_resources(main.root, self.session_id)
        self.workspace = Workspace(ready.identity.worktree_root).require_ready()
        self.workspace.path("core_documents/global_outline.md").write_text(
            "Outline first line.\nOutline second line.\n",
            encoding="utf-8",
        )
        self.workspace.path("core_documents/character_sheets.md").write_text(
            "Mara wants safety.\nMara fears exposure.\n",
            encoding="utf-8",
        )
        part = self.workspace.path("manuscript/part-01")
        part.mkdir(parents=True, exist_ok=True)
        (part / "opening.md").write_text(
            "Opening one.\nOpening two.\nOpening three.\n",
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "add", "--", "core_documents", "manuscript"],
            cwd=self.workspace.root,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Seed lead commit test fixtures"],
            cwd=self.workspace.root,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        self.backend = _LeadBackend(vendor="fixture", test_case=self)
        self.runtime = StoryRuntime(self.workspace, self.backend)  # type: ignore[arg-type]

    async def reviewed_create(
        self, subject: str = "Reviewed update", *,
        new_text: str = "Use restrained close-third narration.\n", user_brief: str | None = None, readiness=False,
    ):
        from tests.recommendation_fixture import task_assessment
        if readiness:
            await self.record_book_readiness(seed_before_capture=True)
        conversation_id = self.conversation(subject)
        self.backend.enqueue(
            artifact_review_outcome(
                operations=lambda _call, material, *, input_payload: [
                    {"evidence_id": _reference_ids(material)[0], "kind": "replace", "new_text": new_text}
                ],
                evidence_status="empty",
                authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments', 'separate proposal preparation'))),
                authoring_scope=lambda call: lead_fixture_authoring_scope(call, activity='review', domains=('tone_execution',), paths=(ARTIFACT_FIXTURE_RULES_PATH,)),
            ),
                             control_plan=selection_plan("artifact.changes"))
        user_material = "\nSupplied replacement:\n" + new_text if user_brief is None else "\nSupplied narrative-style brief:\n" + user_brief
        proposed = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            ((f"Update ${{{ARTIFACT_FIXTURE_RULES_PATH}}} with an exact narrative rule." + user_material + '\nYou may give bounded artifact review judgments and prepare a separate exact replacement proposal; preserve supplied facts and require separate execution confirmation.')),
        )
        self.assertIsNotNone(proposed.proposal, (
            proposed.kind, proposed.next_action, proposed.state, proposed.validation_details,
            proposed.artifact_proposal is not None,
        ))
        for assignment in proposed.proposal.assignments:
            self.backend.enqueue_for(
                assignment.specialist_id,
                lead_specialist_outcome("supports"),
            )
        related_reads = (
            ("core_documents/global_outline.md", "blocked bridge"),
            ("core_documents/part_outlines.md", "tests ownership"),
            ("core_documents/character_sheets.md", "role is courier"),
            ("manuscript/part-01/opening.md", "Opening one"),
        )
        responses = (artifact_synthesis_with_current_reads(related_reads) if readiness
                     else (_artifact_synthesis_outcome("propose"),))
        if readiness:
            self.assertGreaterEqual(proposed.proposal.budget.synthesis_max_physical_calls, len(responses))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, *responses)
        reviewed = await self.runtime.decide_lead_step(
            lead_confirmation_decision(proposed, CollaborationConfirmationDecision.CONFIRM)
        )
        if readiness:
            history = self.runtime.inspect_semantic_synchronization(self.session_id)
            assessment = history.assessments[-1][1]
            self.assertTrue(assessment.basis.projected)
            self.assertTrue(assessment.ready, assessment.blockers)
            self.assertEqual({finding.target_path for finding in assessment.findings},
                             {path for path, _ in related_reads})
            self.assertEqual({read.document.path for finding in assessment.findings for read in finding.reads
                              if read.receipt_id.startswith("read_")}, {path for path, _ in related_reads})
        return reviewed.turn

    def session_root(self):
        return self.workspace.root

    def git(self, *arguments: str, root=None) -> bytes:
        return subprocess.check_output(
            ["git", *arguments],
            cwd=self.session_root() if root is None else root,
        )

    def session_git_snapshot(self) -> tuple[bytes, bytes, bytes]:
        return (
            self.git("rev-parse", "HEAD"),
            self.git("diff", "--cached", "--binary"),
            self.git("status", "--porcelain=v1", "-z"),
        )

    async def applied_create(
        self, subject: str = "Prepared local commit", *, seeded=False, readiness=False,
        new_text: str = "Use restrained close-third narration.\n",
    ):
        turn = (
            await seed_reviewed_artifact(
                self, self.conversation(subject), (("replace", ARTIFACT_FIXTURE_RULES_PATH, new_text),),
            ) if seeded else await self.reviewed_create(subject, new_text=new_text, readiness=readiness)
        )
        applied = await self.runtime.decide_artifact_application(
            lead_artifact_application_tests._application_decision(turn, ArtifactApplicationDecisionKind.APPLY)
        )
        self.assertIsNotNone(applied.commit_request,
                             (applied.outcome.outcome, applied.outcome.next_action, applied.message))
        return applied

    def pending_commit(self, applied, captured):
        reviewed = applied.request.reviewed_proposal
        key = (reviewed.session_id, reviewed.conversation_id, reviewed.step_id)
        pending = self.runtime.pending_story_commits.observe_for_commit(key, applied.commit_request)[0]
        pin = next(call.args[3] for call in captured.call_args_list if call.args[0] is pending)
        captured.reset_mock()
        self.assertIsNotNone(pin)
        descriptors = pin._pin._descriptors
        self.assertEqual(len(descriptors), 6)
        for descriptor in descriptors:
            os.fstat(descriptor)
        return key, pending, pin, descriptors

    def assert_retired_commit(self, key, request, pin, descriptors) -> None:
        self.assertIsNone(self.runtime.pending_story_commits.observe_for_commit(key, request)[0])
        self.assertIsNone(pin._pin)
        for descriptor in descriptors:
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    @contextmanager
    def track_pin_closes(self, *pins):
        calls = []
        original_close = type(pins[0]).close

        def tracked_close(current):
            if any(current is pin for pin in pins):
                calls.append(current)
            return original_close(current)

        with mock.patch.object(type(pins[0]), "close", new=tracked_close):
            yield calls

    def assert_commit_events_round_trip(self, conversation_id: str) -> None:
        commit_types = (StoryCommitRequestPayload, StoryCommitResultPayload)
        events = tuple(
            event
            for event in self.history(conversation_id)
            if type(event.payload) in commit_types
            or type(event.payload).__name__ == "StoryCommitDecisionPayload"
        )
        self.assertTrue(events)
        for event in events:
            with self.subTest(payload=type(event.payload).__name__):
                self.assertEqual(
                    decode_collaboration_event(encode_collaboration_event(event)),
                    event,
                )

    async def test_request_is_inspectable_and_reject_is_zero_git_mutation(self) -> None:
        with mock.patch.object(PreparedStoryCommit, "__init__", autospec=True, side_effect=PreparedStoryCommit.__init__) as captured:
            self.addCleanup(captured.reset_mock)
            applied = await self.applied_create("Reject commit", seeded=True)
            key, _pending, pin, descriptors = self.pending_commit(applied, captured)
        request = applied.commit_request
        assert request is not None
        events = self.history(applied.request.reviewed_proposal.conversation_id)
        application_results = [
            event for event in events if type(event.payload) is ArtifactApplicationResultPayload
        ]
        commit_requests = [
            event for event in events if type(event.payload) is StoryCommitRequestPayload
        ]
        self.assertEqual((len(application_results), len(commit_requests)), (1, 1))
        application_result = application_results[0]
        self.assertIs(application_result.payload.outcome, ArtifactApplicationOutcomeKind.APPLIED)
        applied_assessment, = (
            event for event in events
            if type(event.payload) is SynchronizationPayload and event.payload.assessment is not None
            and event.payload.assessment.basis.application is not None
        )
        synchronization = applied_assessment.payload.assessment
        self.assertEqual(synchronization.basis.application.result, synchronization_event_ref(application_result))
        self.assertEqual(synchronization.basis.application.application_id, application_result.payload.application_id)
        self.assertEqual(synchronization.basis.candidate.candidate_digest, applied.request.reviewed_proposal.candidate.candidate_digest)
        self.assertEqual(applied_assessment.batch_id, application_result.batch_id)
        self.assertEqual(
            (application_results[0].batch_id, application_results[0].event_id),
            (
                commit_requests[0].batch_id,
                commit_requests[0].payload.application_result_event_id,
            ),
        )
        self.assertEqual(
            tuple(
                (event.batch_ordinal, event.batch_size)
                for event in (application_result, commit_requests[0], applied_assessment)
            ),
            ((0, 3), (1, 3), (2, 3)),
        )
        before_files = self.artifact_snapshot()
        before_git = self.session_git_snapshot()
        restarted_backend = _LeadBackend(test_case=self)
        restarted = StoryRuntime(self.workspace, restarted_backend)  # type: ignore[arg-type]
        redisclosed = await restarted.lead_turn(
            self.session_id, applied.request.reviewed_proposal.conversation_id,
            "Commit these changes now.",
         interaction_mode=exploration.LeadInteractionMode.EXPLORATION)
        self.assertIs(redisclosed.next_action, CollaborationNextAction.SUBMIT_COMMIT_DECISION)
        self.assertEqual(redisclosed.story_commit_request, request)
        self.assertEqual(restarted_backend.calls, [])
        other_owner = self.conversation("Different exploration owner")
        with self.assertRaisesRegex(BackendProtocolError, applied.request.reviewed_proposal.conversation_id):
            await restarted.lead_turn(self.session_id, other_owner, "Explore while control is unresolved.",
                                           interaction_mode=exploration.LeadInteractionMode.EXPLORATION)
        self.assertEqual(self.history(other_owner), ())
        self.assertEqual(self.session_git_snapshot(), before_git)

        self.assertEqual(
            request.choices,
            (
                StoryCommitDecision.COMMIT,
                StoryCommitDecision.REJECT,
                StoryCommitDecision.REVISE,
            ),
        )
        self.assertEqual(
            request.side_effects,
            (
                CollaborationSideEffect.GIT_INDEX_MUTATION,
                CollaborationSideEffect.GIT_COMMIT,
            ),
        )
        self.assertEqual(
            request.prohibited_side_effects,
            (
                CollaborationSideEffect.ARTIFACT_MUTATION,
                CollaborationSideEffect.GIT_REMOTE_MUTATION,
                CollaborationSideEffect.GIT_MERGE,
            ),
        )
        self.assertEqual(
            tuple(item.path for item in request.candidate.paths), (ARTIFACT_FIXTURE_RULES_PATH,)
        )
        self.assertEqual(self.session_git_snapshot(), before_git)

        conversation = applied.request.reviewed_proposal.conversation_id
        def pending_discussion(call, _provider):
            self.assertIsNone(call.contract.schema_group_phase)
            return lead_fixture_response(call, lead_fixture_result({"response_type": "answer",
                "response_text": "Discussion preserves this exact Commit request.", "used_evidence_ids": {}}), family=0)
        self.backend.enqueue(pending_discussion)
        discussion = await self.runtime.decide_lead_task(task.LeadTaskDecision.for_conversation(
            self.session_id, conversation, task.LeadTaskAction.DISCUSS, text="Explain the pending Commit.",
        ))
        self.assertEqual((discussion.turn.next_action, discussion.turn.story_commit_request),
                         (CollaborationNextAction.SUBMIT_COMMIT_DECISION, request))
        for failure in (_SubmittedFailure(RuntimeError("known Commit discussion failure")),
                        _SubmittedFailure(asyncio.CancelledError("known Commit discussion cancellation"))):
            prefix = self.history(conversation)
            self.backend.enqueue(failure)
            decision = task.LeadTaskDecision.for_conversation(
                self.session_id, conversation, task.LeadTaskAction.DISCUSS, text="Read-only Commit discussion.",
            )
            if isinstance(failure.failure, asyncio.CancelledError):
                with self.assertRaises(asyncio.CancelledError):
                    await self.runtime.decide_lead_task(decision)
            else:
                await self.runtime.decide_lead_task(decision)
            projection = StoryRuntime(self.workspace, self.backend).inspect_lead_collaboration(
                self.session_id, conversation,
            )
            self.assertEqual(projection.current_story_commit_request, request)
            self.assertEqual(len(_submitted_calls(self.history(conversation)[len(prefix):])), 1)
            self.assertEqual(self.history(conversation)[:len(prefix)], prefix)
            self.assertEqual(self.artifact_snapshot(), before_files)
            self.assertEqual(self.session_git_snapshot(), before_git)
        with self.track_pin_closes(pin) as close_calls:
            rejected = await self.runtime.decide_story_commit(
                _commit_decision(applied, StoryCommitDecision.REJECT)
            )
        self.assertEqual(close_calls, [pin])
        self.assert_retired_commit(key, applied.commit_request, pin, descriptors)
        self.assertIs(rejected.outcome.outcome, StoryCommitOutcomeKind.REJECTED)
        self.assertIs(rejected.outcome.result.state, StoryCommitState.REJECTED)
        self.assertEqual(self.artifact_snapshot(), before_files)
        self.assertEqual(self.session_git_snapshot(), before_git)
        projection = project_collaboration_history(
            self.history(applied.request.reviewed_proposal.conversation_id)
        )
        self.assertEqual(projection.current_story_commit_request, request)
        self.assertEqual(projection.current_story_commit_result, rejected.outcome)
        self.assert_commit_events_round_trip(
            applied.request.reviewed_proposal.conversation_id
        )

        uncertain = LeadStoryCommitTests()
        uncertain.setUp()
        self.addCleanup(uncertain.doCleanups)
        pending = await uncertain.applied_create("Unknown discussion with pending Commit", readiness=True)
        conversation = pending.request.reviewed_proposal.conversation_id
        files_before, git_before = uncertain.artifact_snapshot(), uncertain.session_git_snapshot()
        uncertain.backend.enqueue(_SubmittedFailure(RuntimeError("uncertain pending discussion"), submission_uncertain=True))
        with self.assertRaisesRegex(RuntimeError, "^uncertain pending discussion$"):
            await uncertain.runtime.decide_lead_task(task.LeadTaskDecision.for_conversation(
                uncertain.session_id, conversation, task.LeadTaskAction.DISCUSS, text="Explain without committing.",
            ))
        prefix, spent = uncertain.history(conversation), len(uncertain.backend.calls)
        restarted = StoryRuntime(uncertain.workspace, uncertain.backend)
        blocked = restarted.inspect_lead_collaboration(uncertain.session_id, conversation)
        self.assertEqual(blocked.current_story_commit_request, pending.commit_request)
        self.assertIs(blocked.next_safe_action, CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME)
        with self.assertRaises(ValueError):
            await restarted.decide_lead_task(task.LeadTaskDecision.for_conversation(
                uncertain.session_id, conversation, task.LeadTaskAction.DISCUSS, text="Do not replay unknown work.",
            ))
        self.assertEqual(uncertain.history(conversation), prefix)
        self.assertEqual(len(uncertain.backend.calls), spent)
        self.assertEqual(uncertain.artifact_snapshot(), files_before)
        self.assertEqual(uncertain.session_git_snapshot(), git_before)


    async def test_exact_commit_uses_user_identity_and_no_remote_or_destructive_git(
        self,
    ) -> None:
        with mock.patch.object(PreparedStoryCommit, "__init__", autospec=True, side_effect=PreparedStoryCommit.__init__) as captured:
            self.addCleanup(captured.reset_mock)
            first = await self.applied_create("First reviewed change", readiness=True)
            first_head, first_index, _ = self.session_git_snapshot()
            rejected = await self.runtime.decide_story_commit(
                _commit_decision(first, StoryCommitDecision.REJECT, confirmation_id="first_commit_reject")
            )
            self.assertIs(rejected.outcome.outcome, StoryCommitOutcomeKind.REJECTED)
            self.assertEqual(self.session_git_snapshot()[:2], (first_head, first_index))
            opening = "manuscript/part-01/opening.md"
            rules_text = "Use restrained close-third narration throughout the scene.\n"
            opening_text = "Opening revised with the reviewed narrative voice.\n"
            first_review = first.request.reviewed_proposal.final_review_evidence
            turn = await seed_reviewed_artifact(
                self, self.conversation("Second reviewed change"),
                (("replace", ARTIFACT_FIXTURE_RULES_PATH, rules_text), ("replace", opening, opening_text)),
                review_task=(first_review.task_id, first_review.task_revision + 1),
            )
            applied = await self.runtime.decide_artifact_application(
                lead_artifact_application_tests._application_decision(turn, ArtifactApplicationDecisionKind.APPLY)
            )
            self.assertIs(applied.outcome.outcome, ArtifactApplicationOutcomeKind.APPLIED)
            self.assertEqual(self.session_git_snapshot()[:2], (first_head, first_index))
            self.assertIsNotNone(applied.commit_request, applied.message)
            key, _pending, pin, descriptors = self.pending_commit(applied, captured)
        request = applied.commit_request
        assert request is not None
        self.assertNotEqual(request.application_result_event_id, first.commit_request.application_result_event_id)
        self.assertEqual(request.candidate.application_ids, (first.outcome.application_id, applied.outcome.application_id))
        self.assertEqual(request.candidate.branch_tip, first_head.strip().decode())
        self.assertIsNone(request.candidate.bundle_base_branch_tip)
        self.assertEqual(tuple(item.path for item in request.candidate.paths), (ARTIFACT_FIXTURE_RULES_PATH, opening))
        commands: list[tuple[str, ...]] = []
        original_run = GitProcessRunner.run

        def tracked_run(runner, arguments, **kwargs):
            commands.append(arguments)
            return original_run(runner, arguments, **kwargs)

        with mock.patch.object(GitProcessRunner, "run", tracked_run), \
                self.track_pin_closes(pin) as close_calls:
            committed = await self.runtime.decide_story_commit(
                _commit_decision(applied, StoryCommitDecision.COMMIT)
            )

        self.assertEqual(close_calls, [pin])
        self.assert_retired_commit(key, applied.commit_request, pin, descriptors)

        self.assertIs(committed.outcome.outcome, StoryCommitOutcomeKind.COMMITTED)
        result = committed.outcome.result
        assert result is not None
        self.assertIs(result.state, StoryCommitState.COMMITTED)
        after_head = self.git("rev-parse", "HEAD").strip()
        self.assertNotEqual(after_head, first_head.strip())
        self.assertEqual(result.commit_id.encode("ascii"), after_head)
        self.assertEqual(
            self.git(
                "diff-tree", "--no-commit-id", "--name-only", "-r", after_head.decode()
            ).splitlines(),
            [ARTIFACT_FIXTURE_RULES_PATH.encode(), opening.encode()],
        )
        self.assertEqual(self.git("rev-parse", "HEAD^{tree}").strip().decode(), request.candidate.expected_tree)
        self.assertEqual(self.git("show", f"HEAD:{ARTIFACT_FIXTURE_RULES_PATH}"), rules_text.encode())
        self.assertEqual(self.git("show", f"HEAD:{opening}"), opening_text.encode())
        self.assertEqual(self.git("diff", "--cached", "--binary"), b"")
        identity = (
            self.git(
                "show", "-s", "--format=%an%x00%ae%x00%cn%x00%ce", after_head.decode()
            )
            .strip()
            .split(b"\0")
        )
        configured = (
            self.git("config", "--get", "user.name").strip(),
            self.git("config", "--get", "user.email").strip(),
        )
        self.assertEqual(tuple(identity), configured + configured)
        forbidden = {
            "push",
            "pull",
            "fetch",
            "merge",
            "rebase",
            "reset",
            "clean",
            "checkout",
            "remote",
        }
        self.assertTrue(commands)
        self.assertFalse(any(command[0] in forbidden for command in commands))

        with self.assertRaises(TypeError):
            await self.runtime.decide_story_commit(
                _application_replay(applied)  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(ValueError, "authoritative request tail"):
            await self.runtime.decide_story_commit(
                _commit_decision(applied, StoryCommitDecision.COMMIT)
            )

        revised_apply = await self.applied_create(
            "Revised exact commit", seeded=True,
            new_text="Use a measured close-third voice.\n",
        )
        old_request = revised_apply.commit_request
        assert old_request is not None
        revised_head = self.git("rev-parse", "HEAD").strip()
        revised = await self.runtime.decide_story_commit(
            _commit_decision(
                revised_apply, StoryCommitDecision.REVISE,
                revision_request="Refine the local commit message.",
            )
        )
        self.assertIs(revised.outcome.outcome, StoryCommitOutcomeKind.SUPERSEDED)
        self.assertIs(revised.outcome.next_action, StoryCommitNextAction.REPREPARE)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), revised_head)
        history = self.history(revised_apply.request.reviewed_proposal.conversation_id)
        self.assertIs(type(history[-1].payload), StoryCommitResultPayload)
        projection = project_collaboration_history(history)
        for outcome, action in (
            (StoryCommitOutcomeKind.OUTCOME_UNKNOWN, CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME),
            (StoryCommitOutcomeKind.RECOVERY_REQUIRED, CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY),
        ):
            terminal = replace(revised.outcome, outcome=outcome, result=None)
            self.assertIs(
                context_module._artifact_operation_next_action(
                    replace(history[-1], payload=terminal),
                    projection.current_artifact_application_request,
                    projection.current_artifact_application_result,
                    projection.current_story_commit_request,
                    terminal,
                ),
                action,
            )
        rejected_reprepare = replace(
            revised.outcome, outcome=StoryCommitOutcomeKind.REJECTED, result=None
        )
        self.assertIsNot(
            context_module._artifact_operation_next_action(
                replace(history[-1], payload=rejected_reprepare),
                projection.current_artifact_application_request,
                projection.current_artifact_application_result,
                projection.current_story_commit_request,
                rejected_reprepare,
            ),
            CollaborationNextAction.REPREPARE_COMMIT_REQUEST,
        )
        redisclosed = await self.runtime.lead_turn(
            self.session_id, revised_apply.request.reviewed_proposal.conversation_id,
            "Commit the revision now.",
        )
        self.assertIs(redisclosed.next_action, CollaborationNextAction.REPREPARE_COMMIT_REQUEST)
        preparation = redisclosed.story_commit_preparation_request
        assert preparation is not None
        self.assertEqual(preparation.application_result_event_id, old_request.application_result_event_id)
        prior_opening = self.git("show", f"HEAD:{opening}")
        later_text = "A separately reviewed revision of the opening.\n"
        revised_review = revised_apply.request.reviewed_proposal.final_review_evidence
        later_turn = await seed_reviewed_artifact(
            self, self.conversation("Separately reviewed opening"), (("replace", opening, later_text),),
            review_task=(revised_review.task_id, revised_review.task_revision + 1),
            retained_review_paths=(ARTIFACT_FIXTURE_RULES_PATH,),
        )
        later_apply = await self.runtime.decide_artifact_application(
            lead_artifact_application_tests._application_decision(later_turn, ArtifactApplicationDecisionKind.APPLY)
        )
        self.assertIs(later_apply.outcome.outcome, ArtifactApplicationOutcomeKind.APPLIED)
        later_request = later_apply.commit_request
        self.assertIsNotNone(later_request, later_apply.message)
        before_selection = self.session_git_snapshot()
        restarted = StoryRuntime(self.workspace, _LeadBackend(test_case=self))
        stale = await restarted.decide_story_commit(
            _commit_decision(later_apply, StoryCommitDecision.COMMIT, confirmation_id="old_runtime_commit"),
        )
        self.assertIs(stale.outcome.outcome, StoryCommitOutcomeKind.STALE)
        self.assertIs(stale.outcome.next_action, StoryCommitNextAction.REPREPARE)
        self.assertEqual(self.session_git_snapshot(), before_selection)
        later_focus = await self.runtime.lead_turn(self.session_id, later_turn.conversation_id, "Prepare a Commit request.")
        later_preparation = later_focus.story_commit_preparation_request
        assert later_preparation is not None
        with self.assertRaises(StoryCommitQualificationBlocked) as blocked:
            await self.runtime.reprepare_story_commit(replace(later_preparation, selected_paths=(opening,)))
        self.assertTrue(any(ARTIFACT_FIXTURE_RULES_PATH in reason for reason in blocked.exception.blockers))
        self.assertEqual(self.session_git_snapshot(), before_selection)
        self.assertEqual(self.workspace.path(opening).read_bytes(), later_text.encode())
        fresh_request = await self.runtime.reprepare_story_commit(
            replace(later_preparation, selected_paths=(ARTIFACT_FIXTURE_RULES_PATH,)),
        )
        self.assertNotEqual(fresh_request.candidate.candidate_id, later_request.candidate.candidate_id)
        self.assertEqual(fresh_request.application_result_event_id, later_request.application_result_event_id)
        self.assertEqual(tuple(item.path for item in fresh_request.candidate.paths), (ARTIFACT_FIXTURE_RULES_PATH,))
        self.assertEqual(tuple(item.path for item in fresh_request.candidate.remaining_paths), (opening,))
        self.assertEqual(
            fresh_request.candidate.application_ids,
            (revised_apply.outcome.application_id, later_apply.outcome.application_id),
        )
        opening_file = self.workspace.path(opening)
        drifted_text = "Externally changed opening after Commit preparation.\n"
        opening_file.write_text(drifted_text, encoding="utf-8")
        before_drifted_commit = self.session_git_snapshot()
        drifted_commit = await self.runtime.decide_story_commit(_commit_decision(
            replace(later_apply, commit_request=fresh_request), StoryCommitDecision.COMMIT,
            confirmation_id="worktree_drift_commit",
        ))
        self.assertIs(drifted_commit.outcome.outcome, StoryCommitOutcomeKind.STALE)
        self.assertEqual(self.session_git_snapshot(), before_drifted_commit)
        self.assertEqual(opening_file.read_text(encoding="utf-8"), drifted_text)
        opening_file.write_text(later_text, encoding="utf-8")
        retry = await self.runtime.lead_turn(self.session_id, later_turn.conversation_id, "Prepare a Commit request.")
        retry_preparation = retry.story_commit_preparation_request
        assert retry_preparation is not None
        retry_request = await self.runtime.reprepare_story_commit(
            replace(retry_preparation, selected_paths=(ARTIFACT_FIXTURE_RULES_PATH,)),
        )
        self.assertNotEqual(retry_request.confirmation_request_id, fresh_request.confirmation_request_id)
        fresh_request = retry_request
        with mock.patch.object(GitProcessRunner, "run", tracked_run):
            revised_commit = await self.runtime.decide_story_commit(
                _commit_decision(
                    replace(later_apply, commit_request=fresh_request),
                    StoryCommitDecision.COMMIT, confirmation_id="revised_commit_confirmation",
                )
            )
        self.assertIs(revised_commit.outcome.outcome, StoryCommitOutcomeKind.COMMITTED)
        revised_result = revised_commit.outcome.result
        assert revised_result is not None
        final_head = self.git("rev-parse", "HEAD").strip()
        self.assertNotEqual(final_head, revised_head)
        self.assertEqual(revised_result.commit_id.encode("ascii"), final_head)
        self.assertEqual(
            self.git("diff-tree", "--no-commit-id", "--name-only", "-r", final_head.decode()).splitlines(),
            [ARTIFACT_FIXTURE_RULES_PATH.encode()],
        )
        self.assertEqual(self.git("diff", "--cached", "--binary"), b"")
        self.assertEqual(self.git("rev-parse", "HEAD^{tree}").strip().decode(), fresh_request.candidate.expected_tree)
        self.assertEqual(self.git("show", f"HEAD:{opening}"), prior_opening)
        self.assertEqual(self.workspace.path(opening).read_bytes(), later_text.encode())
        self.assertEqual(self.git("diff", "--name-only"), opening.encode() + b"\n")
        self.assertFalse(any(command[0] in forbidden for command in commands))
        self.assertIs(revised_commit.outcome.next_action, StoryCommitNextAction.REPREPARE)
        resumed = await self.runtime.lead_turn(self.session_id, later_turn.conversation_id, "Prepare a Commit request.")
        self.assertIs(resumed.next_action, CollaborationNextAction.REPREPARE_COMMIT_REQUEST)
        remaining_preparation = resumed.story_commit_preparation_request
        assert remaining_preparation is not None
        before_remainder = self.session_git_snapshot()
        remainder = await self.runtime.reprepare_story_commit(remaining_preparation)
        self.assertNotEqual(remainder.confirmation_request_id, fresh_request.confirmation_request_id)
        self.assertEqual(remainder.application_result_event_id, fresh_request.application_result_event_id)
        self.assertEqual(remainder.candidate.branch_tip, final_head.decode())
        self.assertEqual(tuple(item.path for item in remainder.candidate.paths), (opening,))
        self.assertEqual(remainder.candidate.remaining_paths, ())
        rejected_remainder = await self.runtime.decide_story_commit(_commit_decision(
            replace(later_apply, commit_request=remainder), StoryCommitDecision.REJECT,
            confirmation_id="remaining_paths_reject",
        ))
        self.assertIs(rejected_remainder.outcome.outcome, StoryCommitOutcomeKind.REJECTED)
        self.assertEqual(self.session_git_snapshot(), before_remainder)









if __name__ == "__main__":
    unittest.main()
