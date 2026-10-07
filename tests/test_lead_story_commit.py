"""Focused public-runtime coverage for separately confirmed local story commits."""

from __future__ import annotations
import story_writing_agents.lead_task_transitions as task
from tests.test_lead_orchestration import _discussion_reply, _SubmittedFailure, _submitted_calls

from story_writing_agents import lead_exploration as exploration

import asyncio
import os
import subprocess
import unittest
from contextlib import contextmanager
from dataclasses import replace
from unittest import mock

from story_writing_agents import StoryRuntime, Workspace
from story_writing_agents import orchestrator as orchestrator_module
from story_writing_agents.conversations import context as context_module
from story_writing_agents.agents import LEAD_ORCHESTRATOR
from story_writing_agents.conversations import (
    ArtifactApplicationDecision,
    ArtifactApplicationDecisionKind,
    ArtifactApplicationResultPayload,
    CollaborationConfirmationDecision,
    CollaborationNextAction,
    CollaborationSideEffect,
    StoryCommitDecisionRequest,
    StoryCommitNextAction,
    StoryCommitOutcomeKind,
    StoryCommitRequestPayload,
    StoryCommitResultPayload,
    decode_collaboration_event,
    encode_collaboration_event,
    project_collaboration_history,
)
from story_writing_agents.git.process import GitProcessRunner
from story_writing_agents.git.story_commit_contracts import (
    StoryCommitDecision,
    StoryCommitError,
    StoryCommitFailureCode,
    StoryCommitRecoveryAction,
    StoryCommitState,
)
from story_writing_agents.workspace.initialization import provision_session_resources

from tests import test_lead_artifact_application as lead_artifact_application_tests
from tests.collaboration_contract_fixture import seed_reviewed_artifact
from tests.helpers import pristine_initialized_workspace
from tests.test_artifact_proposal_orchestration import (
    _RULES,
    _artifact_outcome,
    _artifact_synthesis_outcome,
    _reference_ids,
)
from tests.test_lead_artifact_application import _application_decision
from tests.test_lead_orchestration import (
    LeadOrchestrationTestCase,
    _LeadBackend,
    _decision,
    _specialist_outcome,
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
        session_id=reviewed.session_id,
        conversation_id=reviewed.conversation_id,
        step_id=reviewed.step_id,
        confirmation_request_id=request.confirmation_request_id,
        application_result_event_id=request.application_result_event_id,
        candidate_id=request.candidate.candidate_id,
        request_digest=request.request_digest,
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
        self.backend = _LeadBackend(vendor="fixture")
        self.runtime = StoryRuntime(self.workspace, self.backend)  # type: ignore[arg-type]

    async def reviewed_create(
        self, subject: str = "Reviewed update", *,
        new_text: str = "Use restrained close-third narration.\n",
    ):
        conversation_id = self.conversation(subject)
        self.backend.enqueue(
            _artifact_outcome(
                operations=lambda _call, material: [
                    {
                        "evidence_id": _reference_ids(material)[0],
                        "kind": "replace",
                        "new_text": new_text,
                    }
                ],
                evidence_status="empty",
            )
        )
        proposed = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            f"Update ${{{_RULES}}} with an exact narrative rule.",
        )
        for assignment in proposed.proposal.assignments:
            self.backend.enqueue_for(
                assignment.specialist_id,
                _specialist_outcome("supports"),
            )
        self.backend.enqueue_for(
            LEAD_ORCHESTRATOR,
            _artifact_synthesis_outcome("propose"),
        )
        reviewed = await self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
        )
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
        self, subject: str = "Prepared local commit", *, seeded=False,
        new_text: str = "Use restrained close-third narration.\n",
    ):
        turn = (
            await seed_reviewed_artifact(
                self, self.conversation(subject), (("replace", _RULES, new_text),),
            ) if seeded else await self.reviewed_create(subject, new_text=new_text)
        )
        applied = await self.runtime.decide_artifact_application(
            _application_decision(turn, ArtifactApplicationDecisionKind.APPLY)
        )
        self.assertIsNotNone(applied.commit_request)
        return applied

    def pending_commit(self, applied):
        reviewed = applied.request.reviewed_proposal
        key = (reviewed.session_id, reviewed.conversation_id, reviewed.step_id)
        pending = self.runtime._pending_story_commits[key]
        pin = pending._generation_pin
        self.assertIsNotNone(pin)
        descriptors = pin._pin._descriptors
        self.assertEqual(len(descriptors), 6)
        for descriptor in descriptors:
            os.fstat(descriptor)
        return key, pending, pin, descriptors

    def assert_pin_closed(self, pin, descriptors) -> None:
        self.assertIsNone(pin._pin)
        for descriptor in descriptors:
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    @contextmanager
    def track_pin_closes(self, *pins):
        calls = []
        original_close = type(pins[0]).close

        def tracked_close(current):
            if current in pins:
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
        applied = await self.applied_create("Reject commit", seeded=True)
        key, _pending, pin, descriptors = self.pending_commit(applied)
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
                for event in (application_results[0], commit_requests[0])
            ),
            ((0, 2), (1, 2)),
        )
        before_files = self.artifact_snapshot()
        before_git = self.session_git_snapshot()
        restarted_backend = _LeadBackend()
        restarted = StoryRuntime(self.workspace, restarted_backend)  # type: ignore[arg-type]
        redisclosed = await restarted.lead_turn(
            self.session_id, applied.request.reviewed_proposal.conversation_id,
            "Commit these changes now.",
         interaction_mode=exploration.LeadInteractionMode.EXPLORATION)
        self.assertIs(redisclosed.next_action, CollaborationNextAction.SUBMIT_COMMIT_DECISION)
        self.assertEqual(redisclosed.story_commit_request, request)
        self.assertEqual(restarted_backend.calls, [])
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
            tuple(item.path for item in request.candidate.paths), (_RULES,)
        )
        self.assertEqual(self.session_git_snapshot(), before_git)

        conversation = applied.request.reviewed_proposal.conversation_id
        self.backend.enqueue(_discussion_reply("Discussion preserves this exact Commit request."))
        await self.runtime.decide_lead_task(task.LeadTaskDecision.for_conversation(
            self.session_id, conversation, task.LeadTaskAction.DISCUSS, text="Explain the pending Commit.",
        ))
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
        close_calls = []
        original_close = type(pin).close

        def tracked_close(current):
            if current is pin:
                close_calls.append(current)
            return original_close(current)

        with mock.patch.object(type(pin), "close", new=tracked_close):
            rejected = await self.runtime.decide_story_commit(
                _commit_decision(applied, StoryCommitDecision.REJECT)
            )
        self.assertEqual(close_calls, [pin])
        self.assertNotIn(key, self.runtime._pending_story_commits)
        self.assert_pin_closed(pin, descriptors)
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
        pending = await uncertain.applied_create("Unknown discussion with pending Commit")
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
        applied = await self.applied_create("Exact commit")
        key, _pending, pin, descriptors = self.pending_commit(applied)
        request = applied.commit_request
        assert request is not None
        before_head = self.git("rev-parse", "HEAD").strip()
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
        self.assertNotIn(key, self.runtime._pending_story_commits)
        self.assert_pin_closed(pin, descriptors)

        self.assertIs(committed.outcome.outcome, StoryCommitOutcomeKind.COMMITTED)
        result = committed.outcome.result
        assert result is not None
        self.assertIs(result.state, StoryCommitState.COMMITTED)
        after_head = self.git("rev-parse", "HEAD").strip()
        self.assertNotEqual(after_head, before_head)
        self.assertEqual(result.commit_id.encode("ascii"), after_head)
        self.assertEqual(
            self.git(
                "diff-tree", "--no-commit-id", "--name-only", "-r", after_head.decode()
            ).splitlines(),
            [_RULES.encode()],
        )
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
        fresh_request = await self.runtime.reprepare_story_commit(preparation)
        self.assertNotEqual(fresh_request.candidate.candidate_id, old_request.candidate.candidate_id)
        self.assertEqual(fresh_request.application_result_event_id, old_request.application_result_event_id)
        with mock.patch.object(GitProcessRunner, "run", tracked_run):
            revised_commit = await self.runtime.decide_story_commit(
                _commit_decision(
                    replace(revised_apply, commit_request=fresh_request),
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
            [_RULES.encode()],
        )
        self.assertEqual(self.git("diff", "--cached", "--binary"), b"")
        self.assertFalse(any(command[0] in forbidden for command in commands))









if __name__ == "__main__":
    unittest.main()
