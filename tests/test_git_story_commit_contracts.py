"""Pure contract coverage for exact user-confirmed story commits."""

from __future__ import annotations

import copy
import pickle
import unittest
from dataclasses import FrozenInstanceError, fields, is_dataclass

import story_writing_agents
import story_writing_agents.git as git_facade
from story_writing_agents.git import story_commit_contracts as contracts
from story_writing_agents.git.story_commit_contracts import (
    StoryCommitCandidate,
    StoryCommitConfirmation,
    StoryCommitDecision,
    StoryCommitError,
    StoryCommitFailureCode,
    StoryCommitIdentitySource,
    StoryCommitPath,
    StoryCommitPathOperation,
    StoryCommitRecoveryAction,
    StoryCommitResult,
    StoryCommitState,
    project_story_commit_candidate,
)


_DIGEST = "a" * 64
_TIP = "b" * 40
_TREE = "c" * 40


def candidate() -> StoryCommitCandidate:
    return StoryCommitCandidate(
        candidate_id=_DIGEST,
        session_id="novel",
        branch="story/session/novel",
        branch_tip=_TIP,
        expected_tree=_TREE,
        worktree_identity_sha256="d" * 64,
        application_id="application:1",
        proposal_id="proposal:1",
        correlation_id="correlation:1",
        paths=(
            StoryCommitPath(
                "manuscript/part-01/opening.md",
                StoryCommitPathOperation.CREATE,
                None,
                "e" * 64,
            ),
        ),
        proposed_message="Commit 장면 🌙",
        impact_boundary=("reader sees the revised opening",),
        author_identity_source=StoryCommitIdentitySource.ENVIRONMENT,
        committer_identity_source=StoryCommitIdentitySource.REPOSITORY,
    )


class StoryCommitContractTests(unittest.TestCase):
    def test_exports_enums_immutable_shapes_and_facade_absence_are_exact(self) -> None:
        self.assertEqual(
            tuple(contracts.__all__),
            (
                "STORY_COMMIT_MAX_IMPACT_ITEMS",
                "STORY_COMMIT_MAX_MESSAGE_BYTES",
                "StoryCommitApplicationEvidence",
                "StoryCommitCandidate",
                "StoryCommitConfirmation",
                "StoryCommitDecision",
                "StoryCommitError",
                "StoryCommitFailureCode",
                "StoryCommitIdentitySource",
                "StoryCommitPath",
                "StoryCommitPathOperation",
                "StoryCommitProjection",
                "StoryCommitRecoveryAction",
                "StoryCommitResult",
                "StoryCommitState",
                "project_story_commit_candidate",
            ),
        )
        self.assertEqual(
            tuple(item.value for item in StoryCommitDecision),
            ("commit", "reject", "revise"),
        )
        self.assertEqual(
            tuple(item.value for item in StoryCommitPathOperation),
            ("create", "update", "delete"),
        )
        self.assertEqual(
            tuple(item.value for item in StoryCommitState),
            (
                "prepared",
                "running",
                "staged",
                "committed",
                "rejected",
                "outcome_unknown",
                "recovery_required",
            ),
        )
        value = candidate()
        self.assertTrue(is_dataclass(value))
        self.assertTrue(hasattr(value, "__slots__"))
        self.assertEqual(len(fields(value)), 14)
        with self.assertRaises(FrozenInstanceError):
            value.branch = "other"  # type: ignore[misc]
        for name in contracts.__all__:
            self.assertFalse(hasattr(git_facade, name), name)
            self.assertFalse(hasattr(story_writing_agents, name), name)

    def test_candidate_requires_canonical_unique_paths_and_impact_order(self) -> None:
        first = StoryCommitPath(
            "core_documents/global_outline.md",
            StoryCommitPathOperation.UPDATE,
            "1" * 64,
            "2" * 64,
        )
        second = candidate().paths[0]
        for paths, impacts in (
            ((second, first), ("a",)),
            ((first, first), ("a",)),
            ((first,), ("z", "a")),
            ((first,), ("a", "a")),
        ):
            with self.subTest(paths=paths, impacts=impacts):
                with self.assertRaises(StoryCommitError) as caught:
                    StoryCommitCandidate(
                        **{
                            **{
                                field.name: getattr(candidate(), field.name)
                                for field in fields(candidate())
                            },
                            "paths": paths,
                            "impact_boundary": impacts,
                        }
                    )
                self.assertIs(
                    caught.exception.code, StoryCommitFailureCode.INVALID_REQUEST
                )

    def test_projection_is_content_free_defensive_and_omits_runtime_binding(
        self,
    ) -> None:
        value = candidate()
        projection = project_story_commit_candidate(value)
        self.assertEqual(projection.paths, value.paths)
        self.assertFalse(hasattr(projection, "worktree_identity_sha256"))
        rendered = repr(projection)
        self.assertNotIn("story bytes", rendered)
        object.__setattr__(value, "branch", "refs/heads/forged")
        with self.assertRaises(StoryCommitError) as caught:
            project_story_commit_candidate(value)
        self.assertIs(caught.exception.code, StoryCommitFailureCode.INVALID_REQUEST)

    def test_confirmation_result_and_failures_are_closed_and_content_free(self) -> None:
        value = candidate()
        confirmation = StoryCommitConfirmation(
            "confirmation:1",
            value.candidate_id,
            value.proposal_id,
            value.correlation_id,
            StoryCommitDecision.COMMIT,
        )
        self.assertIs(confirmation.decision, StoryCommitDecision.COMMIT)
        result = StoryCommitResult(
            "novel",
            value.candidate_id,
            "f" * 32,
            value.correlation_id,
            StoryCommitState.COMMITTED,
            StoryCommitRecoveryAction.NONE,
            value.branch,
            "9" * 40,
            "9" * 40,
        )
        self.assertEqual(result.commit_id, result.branch_tip)
        with self.assertRaises(StoryCommitError):
            StoryCommitResult(
                "novel",
                value.candidate_id,
                "f" * 32,
                value.correlation_id,
                StoryCommitState.RUNNING,
                StoryCommitRecoveryAction.INSPECT,
                value.branch,
                value.branch_tip,
                "9" * 40,
            )
        error = StoryCommitError(StoryCommitFailureCode.CANDIDATE_DRIFT)
        self.assertEqual(
            str(error), "Story commit candidate no longer matches the session."
        )
        self.assertNotIn(value.proposed_message, repr(error))
        self.assertIs(error.recovery_action, StoryCommitRecoveryAction.REPREPARE)

    def test_runtime_authority_is_not_exposed_by_contracts_or_serializable_values(
        self,
    ) -> None:
        value = candidate()
        self.assertFalse(hasattr(contracts, "StoryCommitAuthority"))
        self.assertEqual(copy.copy(value), value)
        self.assertEqual(pickle.loads(pickle.dumps(value)), value)


if __name__ == "__main__":
    unittest.main()
