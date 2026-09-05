"""Focused public-runtime coverage for separately confirmed local story commits."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from dataclasses import replace
from unittest import mock

from story_writing_agents import StoryRuntime, Workspace
from story_writing_agents.agents import LEAD_ORCHESTRATOR
from story_writing_agents.conversations import (
    ArtifactApplicationDecision,
    ArtifactApplicationDecisionKind,
    CollaborationConfirmationDecision,
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

from tests.test_artifact_proposal_orchestration import (
    _RULES,
    _artifact_outcome,
    _artifact_synthesis_outcome,
    _reference_ids,
)
from tests.test_lead_artifact_application import (
    LeadArtifactApplicationTests,
    _application_decision,
)
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
    artifact_snapshot = LeadArtifactApplicationTests.artifact_snapshot

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="lead-story-commit-")
        self.temporary = temporary
        self.addCleanup(temporary.cleanup)
        main = Workspace(temporary.name).initialize()
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
        self.backend = _LeadBackend()
        self.runtime = StoryRuntime(self.workspace, self.backend)  # type: ignore[arg-type]

    async def reviewed_create(self, subject: str = "Reviewed update"):
        conversation_id = self.conversation(subject)
        self.backend.enqueue(
            _artifact_outcome(
                operations=lambda _call, material: [
                    {
                        "evidence_id": _reference_ids(material)[0],
                        "kind": "replace",
                        "new_text": "Use restrained close-third narration.\n",
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

    async def applied_create(self, subject: str = "Prepared local commit"):
        turn = await self.reviewed_create(subject)
        applied = await self.runtime.decide_artifact_application(
            _application_decision(turn, ArtifactApplicationDecisionKind.APPLY)
        )
        self.assertIsNotNone(applied.commit_request)
        return applied

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
        applied = await self.applied_create("Reject commit")
        request = applied.commit_request
        assert request is not None
        before_files = self.artifact_snapshot()
        before_git = self.session_git_snapshot()

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

        rejected = await self.runtime.decide_story_commit(
            _commit_decision(applied, StoryCommitDecision.REJECT)
        )
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

    async def test_revise_preserves_applied_files_and_requires_reprepare(self) -> None:
        applied = await self.applied_create("Revise commit")
        before_files = self.artifact_snapshot()
        before_git = self.session_git_snapshot()
        revised = await self.runtime.decide_story_commit(
            _commit_decision(
                applied,
                StoryCommitDecision.REVISE,
                revision_request="Use a narrower commit message.",
            )
        )
        self.assertIs(revised.outcome.outcome, StoryCommitOutcomeKind.SUPERSEDED)
        self.assertIs(revised.outcome.next_action, StoryCommitNextAction.REPREPARE)
        self.assertEqual(self.artifact_snapshot(), before_files)
        self.assertEqual(self.session_git_snapshot(), before_git)

    async def test_exact_commit_uses_user_identity_and_no_remote_or_destructive_git(
        self,
    ) -> None:
        applied = await self.applied_create("Exact commit")
        request = applied.commit_request
        assert request is not None
        before_head = self.git("rev-parse", "HEAD").strip()
        commands: list[tuple[str, ...]] = []
        original_run = GitProcessRunner.run

        def tracked_run(runner, arguments, **kwargs):
            commands.append(arguments)
            return original_run(runner, arguments, **kwargs)

        with mock.patch.object(GitProcessRunner, "run", tracked_run):
            committed = await self.runtime.decide_story_commit(
                _commit_decision(applied, StoryCommitDecision.COMMIT)
            )

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

    async def test_restart_cannot_reconstruct_commit_authority(self) -> None:
        applied = await self.applied_create("Restarted commit")
        before_files = self.artifact_snapshot()
        before_git = self.session_git_snapshot()
        restarted = StoryRuntime(self.workspace, _LeadBackend())  # type: ignore[arg-type]
        stale = await restarted.decide_story_commit(
            _commit_decision(applied, StoryCommitDecision.COMMIT)
        )
        self.assertIs(stale.outcome.outcome, StoryCommitOutcomeKind.STALE)
        self.assertIs(stale.outcome.next_action, StoryCommitNextAction.REPREPARE)
        self.assertIsNone(stale.outcome.result)
        self.assertEqual(self.artifact_snapshot(), before_files)
        self.assertEqual(self.session_git_snapshot(), before_git)

    async def test_mismatch_and_prestaged_drift_fail_before_commit(self) -> None:
        applied = await self.applied_create("Decision mismatch")
        before_files = self.artifact_snapshot()
        before_git = self.session_git_snapshot()
        mismatched = replace(
            _commit_decision(applied, StoryCommitDecision.COMMIT),
            request_digest="sha256:" + "0" * 64,
        )
        with self.assertRaisesRegex(ValueError, "does not match tail"):
            await self.runtime.decide_story_commit(mismatched)
        self.assertEqual(self.artifact_snapshot(), before_files)
        self.assertEqual(self.session_git_snapshot(), before_git)

        self.workspace.path("core_documents/global_outline.md").write_text(
            "Externally staged outline.\n",
            encoding="utf-8",
        )
        self.git("add", "--", "core_documents/global_outline.md")
        drifted_files = self.artifact_snapshot()
        drifted_git = self.session_git_snapshot()
        stale = await self.runtime.decide_story_commit(
            _commit_decision(applied, StoryCommitDecision.COMMIT)
        )
        self.assertIs(stale.outcome.outcome, StoryCommitOutcomeKind.STALE)
        self.assertIs(stale.outcome.next_action, StoryCommitNextAction.REPREPARE)
        self.assertEqual(self.artifact_snapshot(), drifted_files)
        self.assertEqual(self.session_git_snapshot(), drifted_git)

    async def test_service_and_result_persistence_failures_are_inspect_only(
        self,
    ) -> None:
        applied = await self.applied_create("Service failure")
        key = (
            applied.request.reviewed_proposal.session_id,
            applied.request.reviewed_proposal.conversation_id,
            applied.request.reviewed_proposal.step_id,
        )
        service = self.runtime._story_commit_services[key]
        before_git = self.session_git_snapshot()
        with mock.patch.object(
            type(service),
            "execute",
            side_effect=StoryCommitError(
                StoryCommitFailureCode.OUTCOME_UNKNOWN,
                StoryCommitRecoveryAction.INSPECT,
            ),
        ):
            uncertain = await self.runtime.decide_story_commit(
                _commit_decision(applied, StoryCommitDecision.COMMIT)
            )
        self.assertIs(uncertain.outcome.outcome, StoryCommitOutcomeKind.OUTCOME_UNKNOWN)
        self.assertIs(uncertain.outcome.next_action, StoryCommitNextAction.INSPECT)
        self.assertEqual(self.session_git_snapshot(), before_git)

    async def test_result_persistence_failure_is_inspect_only(self) -> None:
        applied = await self.applied_create("Result persistence failure")
        original_append = self.runtime._append_drafts
        calls = 0

        def fail_result_append(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("commit result persistence unavailable")
            return original_append(*args, **kwargs)

        before_head = self.git("rev-parse", "HEAD")
        with mock.patch.object(
            self.runtime,
            "_append_drafts",
            side_effect=fail_result_append,
        ):
            missing = await self.runtime.decide_story_commit(
                _commit_decision(applied, StoryCommitDecision.COMMIT)
            )
        self.assertIs(missing.outcome.outcome, StoryCommitOutcomeKind.OUTCOME_UNKNOWN)
        self.assertIs(missing.outcome.next_action, StoryCommitNextAction.INSPECT)
        self.assertIsNone(missing.outcome.result)
        self.assertNotEqual(self.git("rev-parse", "HEAD"), before_head)
        with self.assertRaisesRegex(ValueError, "authoritative request tail"):
            await self.runtime.decide_story_commit(
                _commit_decision(applied, StoryCommitDecision.COMMIT)
            )


if __name__ == "__main__":
    unittest.main()
