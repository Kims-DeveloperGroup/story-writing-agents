"""Focused public-runtime coverage for exact reviewed artifact application."""

from __future__ import annotations

import subprocess
import unittest
from dataclasses import replace
from unittest import mock

from story_writing_agents import StoryRuntime
from story_writing_agents.agents import LEAD_ORCHESTRATOR
from story_writing_agents.conversations import (
    ArtifactApplicationDecision,
    ArtifactApplicationDecisionKind,
    ArtifactApplicationNextAction,
    ArtifactApplicationOutcomeKind,
    CollaborationConfirmationDecision,
    CollaborationNextAction,
    CollaborationSideEffect,
    project_collaboration_history,
)
from story_writing_agents.writing_artifacts import (
    ArtifactApplicationError,
    ArtifactApplicationErrorCode,
    ArtifactApplicationState,
)
from story_writing_agents.writing_artifacts._storage import ArtifactRootSecurityError
from story_writing_agents.writing_artifacts.repository import WritingArtifactRepository
from story_writing_agents.workspace.initialization import provision_session_resources

from tests.test_artifact_proposal_orchestration import (
    _INDEX,
    _RULES,
    _SECOND,
    _THIRD,
    _artifact_outcome,
    _artifact_synthesis_outcome,
    _reference_ids,
    _status_id,
)
from tests.test_lead_orchestration import (
    LeadOrchestrationTestCase,
    _LeadBackend,
    _decision,
    _specialist_outcome,
    _visible_outcome,
)


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

    async def reviewed_create(self, subject: str = "Reviewed application"):
        conversation_id = self.conversation(subject)
        self.workspace.path(_RULES).unlink(missing_ok=True)
        self.backend.enqueue(
            _artifact_outcome(
                operations=[
                    {
                        "kind": "create",
                        "new_text": "Use restrained close-third narration.\n",
                        "suggested_path": _RULES,
                    }
                ],
                evidence_status="missing",
                evidence_ids=lambda call, _material: [_status_id(call, _RULES)],
            )
        )
        proposed = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Prepare exact narrative rules for review.",
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

    async def reviewed_rename(self):
        self.workspace.path(_INDEX).write_text("[next](second.md)\n", encoding="utf-8")
        self.workspace.path(_SECOND).write_text("Second scene.\n", encoding="utf-8")
        conversation_id = self.conversation("Atomic manuscript application")
        self.backend.enqueue(
            _artifact_outcome(
                operations=lambda _call, material: [
                    {
                        "evidence_id": _reference_ids(material)[0],
                        "kind": "replace",
                        "new_text": "[next](third.md)\n",
                    },
                    {
                        "evidence_id": _reference_ids(material)[1],
                        "kind": "rename",
                        "suggested_destination": _THIRD,
                    },
                ]
            )
        )
        proposed = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            f"Repair ${{{_INDEX}}} and rename ${{{_SECOND}}} atomically.",
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
        )
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
                turn = await self.reviewed_create(code.value)
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

        turn = await self.reviewed_create("Result persistence failure")
        original_append = self.runtime._append_drafts
        calls = 0

        def fail_result_append(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("result persistence unavailable")
            return original_append(*args, **kwargs)

        git_before = self.git_snapshot()
        with mock.patch.object(
            self.runtime, "_append_drafts", side_effect=fail_result_append
        ):
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

    async def test_ready_binding_loss_after_confirmation_is_durable_and_single_use(
        self,
    ) -> None:
        turn = await self.reviewed_create("Binding loss")
        before = self.artifact_snapshot()
        session_git_before = self.git_snapshot()
        control_before = self.control_git_snapshot()
        subprocess.run(
            [
                "git",
                "update-ref",
                "-d",
                self.session_resources.identity.internal_ref,
            ],
            cwd=self.control_workspace.root,
            check=True,
        )

        with mock.patch.object(
            WritingArtifactRepository,
            "apply_artifact_changes",
            side_effect=AssertionError("artifact write was attempted"),
        ) as apply_changes:
            result = await self.runtime.decide_artifact_application(
                _application_decision(turn, ArtifactApplicationDecisionKind.APPLY)
            )

        self.assertIs(
            result.outcome.outcome,
            ArtifactApplicationOutcomeKind.RECOVERY_REQUIRED,
        )
        self.assertIs(
            result.outcome.next_action,
            ArtifactApplicationNextAction.OPERATOR_RECOVERY,
        )
        self.assertIsNone(result.outcome.result)
        self.assertIsNone(result.commit_request)
        apply_changes.assert_not_called()
        self.assertEqual(self.artifact_snapshot(), before)
        self.assertEqual(self.git_snapshot(), session_git_before)
        self.assertEqual(self.control_git_snapshot(), control_before)
        projection = project_collaboration_history(self.history(turn.conversation_id))
        self.assertEqual(
            projection.current_artifact_application_result,
            result.outcome,
        )
        with self.assertRaisesRegex(ValueError, "authoritative request tail"):
            await self.runtime.decide_artifact_application(
                _application_decision(turn, ArtifactApplicationDecisionKind.APPLY)
            )

    async def test_physical_root_loss_before_repository_entry_is_durable_zero_write(
        self,
    ) -> None:
        turn = await self.reviewed_create("Physical root loss")
        sibling = provision_session_resources(
            self.control_workspace.root,
            "sibling_session",
        )
        sibling_source = sibling.identity.worktree_root / _RULES
        sibling_source.write_text("Sibling rule.\n", encoding="utf-8")
        before = self.artifact_snapshot()
        control_before = self.control_git_snapshot()
        sibling_before = sibling_source.read_bytes()

        with self.replace_session_worktree() as (moved, replacement):
            replacement_source = replacement / _RULES
            replacement_source.parent.mkdir(parents=True)
            replacement_source.write_text(
                "Replacement must remain untouched.\n",
                encoding="utf-8",
            )
            result = await self.runtime.decide_artifact_application(
                _application_decision(turn, ArtifactApplicationDecisionKind.APPLY)
            )

            self.assertEqual(
                replacement_source.read_text(encoding="utf-8"),
                "Replacement must remain untouched.\n",
            )
            self.assertFalse((moved / _RULES).exists())
            self.assertEqual(sibling_source.read_bytes(), sibling_before)
            self.assertEqual(self.control_git_snapshot(), control_before)

        self.assertIs(
            result.outcome.outcome,
            ArtifactApplicationOutcomeKind.RECOVERY_REQUIRED,
        )
        self.assertIs(
            result.outcome.next_action,
            ArtifactApplicationNextAction.OPERATOR_RECOVERY,
        )
        self.assertIsNone(result.outcome.result)
        self.assertIsNone(result.commit_request)
        self.assertEqual(self.artifact_snapshot(), before)
        projection = project_collaboration_history(self.history(turn.conversation_id))
        self.assertEqual(projection.current_artifact_application_result, result.outcome)
        with self.assertRaisesRegex(ValueError, "authoritative request tail"):
            await self.runtime.decide_artifact_application(
                _application_decision(turn, ArtifactApplicationDecisionKind.APPLY)
            )

    async def test_root_loss_after_repository_entry_remains_outcome_unknown(self) -> None:
        turn = await self.reviewed_create("Post-entry root loss")
        before = self.artifact_snapshot()
        git_before = self.git_snapshot()

        with mock.patch.object(
            WritingArtifactRepository,
            "apply_artifact_changes",
            side_effect=ArtifactRootSecurityError("workspace root identity changed"),
        ):
            result = await self.runtime.decide_artifact_application(
                _application_decision(turn, ArtifactApplicationDecisionKind.APPLY)
            )

        self.assertIs(
            result.outcome.outcome,
            ArtifactApplicationOutcomeKind.OUTCOME_UNKNOWN,
        )
        self.assertIs(
            result.outcome.next_action,
            ArtifactApplicationNextAction.INSPECT_AND_RESOLVE_OUTCOME,
        )
        self.assertIsNone(result.outcome.result)
        self.assertIsNone(result.commit_request)
        self.assertEqual(self.artifact_snapshot(), before)
        self.assertEqual(self.git_snapshot(), git_before)


if __name__ == "__main__":
    unittest.main()
