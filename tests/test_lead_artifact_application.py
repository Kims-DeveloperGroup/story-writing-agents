"""Focused public-runtime coverage for exact reviewed artifact application."""

from __future__ import annotations
from story_writing_agents import editorial_scope as scopes
from tests.test_collaboration_events import editorial, editorial_request

from story_writing_agents import lead_exploration as exploration

import os
import subprocess
import unittest
from contextlib import contextmanager
from dataclasses import replace
from unittest import mock

from story_writing_agents import StoryRuntime
from story_writing_agents import orchestrator as orchestrator_module
from story_writing_agents.git.story_commits import StoryCommitService
from story_writing_agents.conversations import (
    ArtifactApplicationDecision, ArtifactApplicationDecisionKind,
    ArtifactApplicationNextAction, ArtifactApplicationOutcomeKind,
    ArtifactApplicationResultPayload, CollaborationNextAction,
    CollaborationSideEffect, StoryCommitDecisionRequest, StoryCommitNextAction,
    StoryCommitOutcomeKind, StoryCommitRequestPayload, project_collaboration_history,
)
from story_writing_agents.git.story_commit_contracts import (
    StoryCommitDecision, StoryCommitError, StoryCommitFailureCode,
)
from story_writing_agents.writing_artifacts import (
    ArtifactApplicationError,
    ArtifactApplicationErrorCode,
    ArtifactApplicationState,
)
from story_writing_agents.writing_artifacts._storage import ArtifactRootSecurityError
from story_writing_agents.writing_artifacts.repository import WritingArtifactRepository
from story_writing_agents.workspace import initialization as initialization_module
from story_writing_agents.workspace.initialization import (
    SessionArtifactAccess,
    SessionArtifactBinding,
    SessionArtifactBindingError,
    SessionArtifactGenerationPin,
    provision_session_resources,
)

from tests.test_artifact_proposal_orchestration import _INDEX, _RULES, _SECOND, _THIRD
from tests.collaboration_contract_fixture import seed_reviewed_artifact
from tests.test_lead_orchestration import LeadOrchestrationTestCase, _LeadBackend


def _application_decision(
    turn: object,
    kind: ArtifactApplicationDecisionKind,
    *,
    revision_request: str | None = None,
) -> ArtifactApplicationDecision:
    request = turn.artifact_application_request  # type: ignore[attr-defined]
    assert request is not None
    reviewed = request.reviewed_proposal
    application = request.application_request
    return ArtifactApplicationDecision(
        session_id=turn.session_id,  # type: ignore[attr-defined]
        conversation_id=turn.conversation_id,  # type: ignore[attr-defined]
        step_id=turn.step_id,  # type: ignore[attr-defined]
        confirmation_request_id=request.confirmation_request_id,
        reviewed_proposal_id=reviewed.proposal_id,
        reviewed_proposal_revision=reviewed.proposal_revision,
        reviewed_proposal_digest=reviewed.proposal_digest,
        candidate_id=application.candidate_id,
        candidate_revision=application.candidate_revision,
        candidate_digest=application.candidate_digest,
        application_id=application.application_id,
        application_request_digest=request.application_request_digest,
        kind=kind,
        revision_request=revision_request,
    )


class LeadArtifactApplicationTests(LeadOrchestrationTestCase):

    def artifact_snapshot(self) -> tuple[tuple[str, bytes], ...]:
        return tuple(
            (item.path, item.content)
            for item in WritingArtifactRepository(
                self.workspace.root
            ).capture_complete_state()
        )

    def git_snapshot(self) -> tuple[bytes, bytes]:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=self.workspace.root
            ),
            subprocess.check_output(
                ["git", "diff", "--cached", "--binary"], cwd=self.workspace.root
            ),
        )

    def control_git_snapshot(self) -> tuple[bytes, bytes, bytes]:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=self.control_workspace.root
            ),
            subprocess.check_output(
                ["git", "status", "--porcelain=v1", "-z"],
                cwd=self.control_workspace.root,
            ),
            self.control_workspace.path(
                "core_documents/global_outline.md"
            ).read_bytes(),
        )

    async def reviewed_create(self, subject: str = "Reviewed application", *, kind="create",
                              editorial_refs=(), editorial_context_digest=None):
        conversation_id = self.conversation(subject)
        if kind == "create":
            self.workspace.path(_RULES).unlink(missing_ok=True)
        return await seed_reviewed_artifact(self, conversation_id, (
            (kind, _RULES, "Use restrained close-third narration.\n"),
        ),
            editorial_refs=editorial_refs, editorial_context_digest=editorial_context_digest,
        )

    async def reviewed_rename(self):
        self.workspace.path(_INDEX).write_text("[next](second.md)\n", encoding="utf-8")
        self.workspace.path(_SECOND).write_text("Second scene.\n", encoding="utf-8")
        return await seed_reviewed_artifact(self, self.conversation("Atomic manuscript application"), (
            ("replace", _INDEX, "[next](third.md)\n"), ("rename", _SECOND, _THIRD),
        ), extra_core_evidence=("core_documents/character_sheets.md", "core_documents/global_outline.md", "core_documents/narrative_rules.md", "core_documents/part_outlines.md"))

    async def test_request_is_exact_visible_and_free_form_text_has_no_authority(
        self,
    ) -> None:
        turn = await self.reviewed_create("Free-form authority")
        request = turn.artifact_application_request
        assert request is not None
        before = self.artifact_snapshot()
        git_before = self.git_snapshot()

        self.assertIs(
            turn.next_action, CollaborationNextAction.SUBMIT_APPLICATION_DECISION
        )
        self.assertEqual(
            request.choices,
            (
                ArtifactApplicationDecisionKind.APPLY,
                ArtifactApplicationDecisionKind.REJECT,
                ArtifactApplicationDecisionKind.REVISE,
            ),
        )
        self.assertEqual(
            request.side_effects, (CollaborationSideEffect.ARTIFACT_MUTATION,)
        )
        self.assertEqual(
            request.prohibited_side_effects,
            (
                CollaborationSideEffect.GIT_INDEX_MUTATION,
                CollaborationSideEffect.GIT_COMMIT,
                CollaborationSideEffect.GIT_REMOTE_MUTATION,
                CollaborationSideEffect.GIT_MERGE,
            ),
        )
        self.assertEqual(
            request.application_request.change_set,
            request.reviewed_proposal.candidate.change_set,
        )

        calls = len(self.backend.calls)
        history = self.history(turn.conversation_id)
        redisclosed = await self.runtime.lead_turn(
            self.session_id, turn.conversation_id, "Apply the reviewed change now."
        , interaction_mode=exploration.LeadInteractionMode.EXPLORATION)
        self.assertEqual(redisclosed.artifact_application_request, request)
        self.assertEqual(len(self.backend.calls), calls)
        self.assertEqual(self.history(turn.conversation_id), history)
        self.assertEqual(self.artifact_snapshot(), before)
        self.assertEqual(self.git_snapshot(), git_before)

    async def test_reject_and_revise_are_structured_zero_write_terminals(self) -> None:
        for kind, expected, revision in (
            (
                ArtifactApplicationDecisionKind.REJECT,
                ArtifactApplicationOutcomeKind.REJECTED,
                None,
            ),
            (
                ArtifactApplicationDecisionKind.REVISE,
                ArtifactApplicationOutcomeKind.SUPERSEDED,
                "Preserve the current tense.",
            ),
        ):
            with self.subTest(kind=kind):
                turn = await self.reviewed_create(kind.value)
                before = self.artifact_snapshot()
                git_before = self.git_snapshot()
                result = await self.runtime.decide_artifact_application(
                    _application_decision(turn, kind, revision_request=revision)
                )
                self.assertIs(result.outcome.outcome, expected)
                self.assertIsNone(result.outcome.result)
                self.assertEqual(self.artifact_snapshot(), before)
                self.assertEqual(self.git_snapshot(), git_before)
                projection = project_collaboration_history(
                    self.history(turn.conversation_id)
                )
                self.assertEqual(
                    projection.current_artifact_application_result, result.outcome
                )
                with self.assertRaisesRegex(ValueError, "authoritative request tail"):
                    await self.runtime.decide_artifact_application(
                        _application_decision(turn, kind, revision_request=revision)
                    )

    async def test_atomic_multi_file_apply_has_exact_provenance_and_no_git_effect(
        self,
    ) -> None:
        other = provision_session_resources(
            self.control_workspace.root,
            "other_session",
        )
        other_index = other.identity.worktree_root / _INDEX
        other_second = other.identity.worktree_root / _SECOND
        other_index.parent.mkdir(parents=True, exist_ok=True)
        other_index.write_text("[other](second.md)\n", encoding="utf-8")
        other_second.write_text("Other session scene.\n", encoding="utf-8")
        other_before = (other_index.read_bytes(), other_second.read_bytes())
        turn = await self.reviewed_rename()
        request = turn.artifact_application_request
        assert request is not None
        git_before = self.git_snapshot()
        control_before = self.control_git_snapshot()
        result = await self.runtime.decide_artifact_application(
            _application_decision(turn, ArtifactApplicationDecisionKind.APPLY)
        )

        self.assertIs(result.outcome.outcome, ArtifactApplicationOutcomeKind.APPLIED)
        applied = result.outcome.result
        assert applied is not None
        self.assertIs(applied.state, ArtifactApplicationState.APPLIED)
        self.assertEqual(applied.provenance, request.application_request.provenance)
        self.assertEqual(applied.affected_paths, (_INDEX, _SECOND, _THIRD))
        self.assertEqual(
            tuple(item.path for item in applied.final_artifacts), (_INDEX, _THIRD)
        )
        self.assertEqual(
            tuple(
                (item.source_path, item.destination_path)
                for item in applied.rename_lineage
            ),
            ((_SECOND, _THIRD),),
        )
        self.assertEqual(
            self.workspace.path(_INDEX).read_text(encoding="utf-8"),
            "[next](third.md)\n",
        )
        self.assertFalse(self.workspace.path(_SECOND).exists())
        self.assertEqual(
            self.workspace.path(_THIRD).read_text(encoding="utf-8"), "Second scene.\n"
        )
        self.assertEqual(self.git_snapshot(), git_before)
        self.assertEqual(self.control_git_snapshot(), control_before)
        self.assertEqual(
            (other_index.read_bytes(), other_second.read_bytes()),
            other_before,
        )
        self.assertFalse((other.identity.worktree_root / _THIRD).exists())
        with self.assertRaisesRegex(ValueError, "authoritative request tail"):
            await self.runtime.decide_artifact_application(
                _application_decision(turn, ArtifactApplicationDecisionKind.APPLY)
            )

    async def test_restart_cannot_reconstruct_apply_authority(self) -> None:
        turn = await self.reviewed_create("Restart authority")
        before = self.artifact_snapshot()
        git_before = self.git_snapshot()
        restarted = StoryRuntime(self.control_workspace, _LeadBackend())  # type: ignore[arg-type]
        result = await restarted.decide_artifact_application(
            _application_decision(turn, ArtifactApplicationDecisionKind.APPLY)
        )
        self.assertIs(result.outcome.outcome, ArtifactApplicationOutcomeKind.STALE)
        self.assertIs(
            result.outcome.next_action,
            ArtifactApplicationNextAction.REGENERATE_AND_REVIEW,
        )
        self.assertIsNone(result.outcome.result)
        self.assertEqual(self.artifact_snapshot(), before)
        self.assertEqual(self.git_snapshot(), git_before)

    async def test_prewrite_identity_base_and_relationship_drift_are_zero_write(
        self,
    ) -> None:
        identity_turn = await self.reviewed_create("Reviewer identity drift")
        identity_before = self.artifact_snapshot()
        identity_git = self.git_snapshot()
        bad = replace(
            _application_decision(identity_turn, ArtifactApplicationDecisionKind.APPLY),
            reviewed_proposal_digest="sha256:" + "0" * 64,
        )
        with self.assertRaisesRegex(ValueError, "does not match tail"):
            await self.runtime.decide_artifact_application(bad)
        self.assertEqual(self.artifact_snapshot(), identity_before)
        self.assertEqual(self.git_snapshot(), identity_git)

        base_turn = await self.reviewed_create("Base drift")
        self.workspace.path(_RULES).write_text("External rule.\n", encoding="utf-8")
        base_git = self.git_snapshot()
        stale = await self.runtime.decide_artifact_application(
            _application_decision(base_turn, ArtifactApplicationDecisionKind.APPLY)
        )
        self.assertIs(stale.outcome.outcome, ArtifactApplicationOutcomeKind.STALE)
        self.assertEqual(
            self.workspace.path(_RULES).read_text(encoding="utf-8"), "External rule.\n"
        )
        self.assertEqual(self.git_snapshot(), base_git)

        relationship_turn = await self.reviewed_create("Relationship drift")
        relationship_before = self.artifact_snapshot()
        relationship_git = self.git_snapshot()
        with mock.patch(
            "story_writing_agents.orchestrator.validate_artifact_relationships",
            return_value=mock.sentinel.drifted_relationship,
        ):
            drifted = await self.runtime.decide_artifact_application(
                _application_decision(
                    relationship_turn,
                    ArtifactApplicationDecisionKind.APPLY,
                )
            )
        self.assertIs(drifted.outcome.outcome, ArtifactApplicationOutcomeKind.STALE)
        self.assertEqual(self.artifact_snapshot(), relationship_before)
        self.assertEqual(self.git_snapshot(), relationship_git)

    async def test_application_and_result_persistence_failures_map_safely(self) -> None:
        self.workspace.path(_RULES).unlink(missing_ok=True)
        subprocess.run(
            ["git", "add", "-A", "--", "core_documents", "manuscript"],
            cwd=self.workspace.root, check=True,
        )
        subprocess.run(
            ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "commit", "-m", "Seed Apply failure baseline"],
            cwd=self.workspace.root, check=True, stdout=subprocess.DEVNULL,
        )
        memory_conversation = self.conversation("Scoped failure direction")
        draft = replace(editorial_request("failure_scope").draft, scope=scopes.EditorialScope.whole(_RULES))
        memory = await self.runtime.record_editorial_decision(self.session_id, memory_conversation,
            editorial.EditorialRecordRequest.directive(draft,
                transition_reason="Retain the actual target direction."))
        for code, expected, next_action in (
            (
                ArtifactApplicationErrorCode.RECOVERY_REQUIRED,
                ArtifactApplicationOutcomeKind.RECOVERY_REQUIRED,
                ArtifactApplicationNextAction.OPERATOR_RECOVERY,
            ),
            (
                ArtifactApplicationErrorCode.INVALID_SCHEMA,
                ArtifactApplicationOutcomeKind.OUTCOME_UNKNOWN,
                ArtifactApplicationNextAction.INSPECT_AND_RESOLVE_OUTCOME,
            ),
        ):
            with self.subTest(code=code):
                turn = await self.reviewed_create(code.value, editorial_refs=(memory.record_ref,),
                    editorial_context_digest=self.runtime.inspect_editorial_state(self.session_id)
                        .context_digest((memory.record_ref,)))
                before = self.artifact_snapshot()
                git_before = self.git_snapshot()
                with mock.patch.object(
                    WritingArtifactRepository,
                    "apply_artifact_changes",
                    side_effect=ArtifactApplicationError(code),
                ):
                    result = await self.runtime.decide_artifact_application(
                        _application_decision(
                            turn, ArtifactApplicationDecisionKind.APPLY
                        )
                    )
                self.assertIs(result.outcome.outcome, expected)
                self.assertIs(result.outcome.next_action, next_action)
                self.assertEqual(self.artifact_snapshot(), before)
                self.assertEqual(self.git_snapshot(), git_before)
                state = self.runtime.inspect_editorial_state(self.session_id)
                attempts = tuple(
                    item for item in state.application_attempts if item.record_ref == memory.record_ref)
                self.assertEqual(attempts[-1].phase, "recovery_required" if expected is
                                 ArtifactApplicationOutcomeKind.RECOVERY_REQUIRED else "uncertain")
                self.assertEqual(attempts[-1].target_paths, (_RULES,))
                self.assertEqual(state.applied_facts, ())

        turn = await self.reviewed_create("Commit preparation failure")
        git_before = self.git_snapshot()
        with mock.patch.object(
            StoryCommitService, "prepare",
            side_effect=StoryCommitError(StoryCommitFailureCode.CANDIDATE_DRIFT),
        ):
            prepared = await self.runtime.decide_artifact_application(
                _application_decision(turn, ArtifactApplicationDecisionKind.APPLY)
            )
        self.assertIs(prepared.outcome.outcome, ArtifactApplicationOutcomeKind.APPLIED)
        self.assertIs(
            prepared.outcome.next_action, ArtifactApplicationNextAction.REPREPARE_COMMIT_REQUEST,
        )
        self.assertIsNone(prepared.commit_request)
        self.assertEqual(self.git_snapshot(), git_before)
        prepared_events = self.history(turn.conversation_id)
        self.assertIs(type(prepared_events[-1].payload), ArtifactApplicationResultPayload)
        self.assertFalse(
            any(type(event.payload) is StoryCommitRequestPayload for event in prepared_events)
        )
        restarted_backend = _LeadBackend()
        restarted = StoryRuntime(self.control_workspace, restarted_backend)  # type: ignore[arg-type]
        redisclosed = await restarted.lead_turn(
            self.session_id, turn.conversation_id, "Commit it now.",
        )
        self.assertIs(redisclosed.next_action, CollaborationNextAction.REPREPARE_COMMIT_REQUEST)
        reprepare = redisclosed.story_commit_preparation_request
        assert reprepare is not None
        self.assertEqual(
            (reprepare.application_result_event_id, reprepare.tail_event_id),
            (prepared_events[-1].event_id,) * 2,
        )
        self.assertEqual(
            (reprepare.application_id, reprepare.application_request_digest),
            (prepared.outcome.application_id, prepared.outcome.application_request_digest),
        )
        self.assertEqual(restarted_backend.calls, [])
        self.assertEqual(self.git_snapshot(), git_before)
        renewed = await restarted.reprepare_story_commit(reprepare)
        self.assertEqual(renewed.application_result_event_id, prepared_events[-1].event_id)
        self.assertIs(type(self.history(turn.conversation_id)[-1].payload), StoryCommitRequestPayload)
        with self.assertRaises(ValueError):
            await restarted.reprepare_story_commit(reprepare)
        rejected = await restarted.decide_story_commit(
            StoryCommitDecisionRequest(
                session_id=self.session_id, conversation_id=turn.conversation_id,
                step_id=turn.step_id,
                confirmation_request_id=renewed.confirmation_request_id,
                application_result_event_id=renewed.application_result_event_id,
                candidate_id=renewed.candidate.candidate_id,
                request_digest=renewed.request_digest,
                confirmation_id="reprepared_commit_reject",
                kind=StoryCommitDecision.REJECT,
            )
        )
        self.assertIs(rejected.outcome.outcome, StoryCommitOutcomeKind.REJECTED)
        self.assertEqual(self.git_snapshot(), git_before)

        turn = await self.reviewed_create("Published pair readback")
        store = self.runtime.conversation_storage_services.event_store
        original_batch = type(store).append_batch
        appended_pair = False

        def append_then_raise(current, batch):
            nonlocal appended_pair
            receipt = original_batch(current, batch)
            if current is store and any(
                type(event.payload) is StoryCommitRequestPayload for event in batch.events
            ):
                appended_pair = True
                raise OSError("commit pair acknowledgement unavailable")
            return receipt

        git_before = self.git_snapshot()
        with mock.patch.object(type(store), "append_batch", new=append_then_raise):
            recovered = await self.runtime.decide_artifact_application(
                _application_decision(turn, ArtifactApplicationDecisionKind.APPLY)
            )
        self.assertTrue(appended_pair)
        self.assertIs(recovered.outcome.outcome, ArtifactApplicationOutcomeKind.APPLIED)
        self.assertIs(
            recovered.outcome.next_action,
            ArtifactApplicationNextAction.REQUEST_COMMIT_CONFIRMATION,
        )
        self.assertIsNotNone(recovered.commit_request)
        self.assertEqual(
            sum(type(event.payload) is StoryCommitRequestPayload for event in self.history(turn.conversation_id)),
            1,
        )
        self.assertEqual(self.git_snapshot(), git_before)

        turn = await self.reviewed_create("Commit publication failure")
        failed_pair = False

        def fail_pair(current, batch):
            nonlocal failed_pair
            if current is store and any(
                type(event.payload) is StoryCommitRequestPayload for event in batch.events
            ):
                failed_pair = True
                raise OSError("commit request publication unavailable")
            return original_batch(current, batch)
        git_before = self.git_snapshot()
        with mock.patch.object(type(store), "append_batch", new=fail_pair):
            absent = await self.runtime.decide_artifact_application(
                _application_decision(turn, ArtifactApplicationDecisionKind.APPLY)
            )
        self.assertTrue(failed_pair)
        self.assertIs(absent.outcome.outcome, ArtifactApplicationOutcomeKind.APPLIED)
        self.assertIs(
            absent.outcome.next_action, ArtifactApplicationNextAction.REPREPARE_COMMIT_REQUEST,
        )
        self.assertIsNone(absent.commit_request)
        self.assertEqual(self.git_snapshot(), git_before)
        absent_events = self.history(turn.conversation_id)
        self.assertIs(type(absent_events[-1].payload), ArtifactApplicationResultPayload)
        self.assertFalse(
            any(type(event.payload) is StoryCommitRequestPayload for event in absent_events)
        )

        turn = await self.reviewed_create("Result persistence failure")
        original_read = type(store).read
        pair_failed = False

        def fail_ambiguous_pair(current, batch):
            nonlocal pair_failed
            if current is store and any(
                type(event.payload) is StoryCommitRequestPayload for event in batch.events
            ):
                pair_failed = True
                raise OSError("result publication unavailable")
            return original_batch(current, batch)

        def fail_readback(current, *args):
            if current is store and pair_failed:
                raise OSError("result readback unavailable")
            return original_read(current, *args)

        git_before = self.git_snapshot()
        with mock.patch.object(
            type(store), "append_batch", new=fail_ambiguous_pair,
        ), mock.patch.object(type(store), "read", new=fail_readback):
            uncertain = await self.runtime.decide_artifact_application(
                _application_decision(turn, ArtifactApplicationDecisionKind.APPLY)
            )
        self.assertIs(
            uncertain.outcome.outcome, ArtifactApplicationOutcomeKind.OUTCOME_UNKNOWN
        )
        self.assertIs(
            uncertain.outcome.next_action,
            ArtifactApplicationNextAction.INSPECT_AND_RESOLVE_OUTCOME,
        )
        self.assertIsNone(uncertain.outcome.result)
        self.assertTrue(self.workspace.path(_RULES).exists())
        self.assertEqual(self.git_snapshot(), git_before)















if __name__ == "__main__":
    unittest.main()
