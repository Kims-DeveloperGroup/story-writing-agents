"""Filesystem safety coverage for durable Git session history."""

from __future__ import annotations

import hashlib
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from story_writing_agents.git import GitBaselineManifest, GitManifestEntry
from story_writing_agents.git import session_history as history_module
from story_writing_agents.git.session_history import open_git_session_history
from story_writing_agents.git.session_history_contracts import (
    GIT_SESSION_HISTORY_DIRECTORY,
    GitSessionCandidatePath,
    GitSessionCandidatePathOperation,
    GitSessionCheckpointOperationRecord,
    GitSessionCommitOperationRecord,
    GitSessionHistoryError,
    GitSessionHistoryFailureCode,
    GitSessionHistoryState,
    GitSessionHistorySnapshot,
    GitSessionIdentitySource,
    advance_checkpoint_operation,
    advance_commit_operation,
    build_candidate_record,
)
from story_writing_agents.workspace.initialization import provision_session_resources
from story_writing_agents.workspace.schema import canonical_initializer_scaffold

from tests.test_git_session_resources import GitSessionResourceGitTestCase


_OPERATION_ID = "a" * 32
_CANDIDATE_ID = "b" * 64


def _manifest() -> GitBaselineManifest:
    return GitBaselineManifest(
        tuple(
            GitManifestEntry(
                item.path,
                hashlib.sha256(item.content).hexdigest(),
            )
            for item in canonical_initializer_scaffold()
        )
    )


def _running(tip: str) -> GitSessionCommitOperationRecord:
    return GitSessionCommitOperationRecord(
        session_id="novel",
        operation_id=_OPERATION_ID,
        candidate_id=_CANDIDATE_ID,
        correlation_id="correlation:1",
        state=GitSessionHistoryState.RUNNING,
        sequence=0,
        expected_branch_tip=tip,
    )


def _checkpoint_running(tip: str) -> GitSessionCheckpointOperationRecord:
    return GitSessionCheckpointOperationRecord(
        session_id="novel",
        operation_id="c" * 32,
        correlation_id="correlation:checkpoint",
        state=GitSessionHistoryState.RUNNING,
        sequence=0,
        expected_internal_tip=tip,
        expected_tree="5" * len(tip),
        commit_intent_sha256="6" * 64,
    )


def _candidate(tip: str, tree: str, *, suffix: str = "01"):
    return build_candidate_record(
        session_id="novel",
        branch="story/session/novel",
        branch_tip=tip,
        expected_tree=tree,
        worktree_identity_sha256="d" * 64,
        application_id=f"application:{suffix}",
        proposal_id=f"proposal:{suffix}",
        correlation_id=f"correlation:{suffix}",
        paths=(
            GitSessionCandidatePath(
                f"manuscript/part-01/chapter {suffix} 한글.md",
                GitSessionCandidatePathOperation.CREATE,
                None,
                "e" * 64,
            ),
        ),
        proposed_message=f"Update chapter {suffix}",
        impact_boundary=("plot",),
        author_identity_source=GitSessionIdentitySource.REPOSITORY,
        committer_identity_source=GitSessionIdentitySource.REPOSITORY,
    )


class GitSessionHistorySafetyTests(GitSessionResourceGitTestCase):
    """Reject aliased identifiers and substituted durable records."""

    def _ready_root(self, name: str = "History Space 한글") -> tuple[Path, str]:
        root = self.initialize_workspace(name)
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            ready = provision_session_resources(root, "novel")
        assert ready.current_branch_tip is not None
        return root, ready.current_branch_tip

    def _tree(self, root: Path) -> str:
        return self.run_git(root, "rev-parse", "HEAD^{tree}").stdout.decode().strip()


    def test_snapshot_fails_closed_on_drift_corruption_and_preserves_lease(self) -> None:
        root, tip = self._ready_root("snapshot-fail-closed")
        candidate = _candidate(tip, self._tree(root))
        with open_git_session_history(
            root, _manifest(), "novel", create=True
        ) as history:
            history.write_candidate(candidate)
            repository_before = self.repository_snapshot(root)
            divergent = GitSessionHistorySnapshot(
                history.inspect(),
                (candidate,),
                (),
                (),
            ).inspection
            with mock.patch.object(
                history_module.GitSessionHistory,
                "inspect",
                return_value=type(divergent)("novel", (), (), (), ()),
            ):
                with self.assertRaises(GitSessionHistoryError) as drift:
                    history.snapshot()
            self.assertIs(
                drift.exception.code,
                GitSessionHistoryFailureCode.RECOVERY_REQUIRED,
            )
            history.require_active()
            self.assertEqual(self.repository_snapshot(root), repository_before)

            record = (
                root
                / ".git"
                / GIT_SESSION_HISTORY_DIRECTORY
                / "novel"
                / "candidates"
                / f"{candidate.candidate_id}.json"
            )
            record.write_bytes(b"not canonical history bytes")
            with self.assertRaises(GitSessionHistoryError) as corrupt:
                history.snapshot()
            self.assertIs(
                corrupt.exception.code,
                GitSessionHistoryFailureCode.RECORD_INVALID,
            )
            self.assertIs(
                corrupt.exception.recovery_action,
                history_module.GitSessionHistoryRecoveryAction.NONE,
            )
            self.assertNotIn("canonical history", repr(corrupt.exception))
            history.require_active()


    def test_commit_checkpoint_transitions_restart_and_inventory_are_exact(
        self,
    ) -> None:
        root, tip = self._ready_root("transitions")
        with open_git_session_history(
            root, _manifest(), "novel", create=True
        ) as history:
            commit = _running(tip)
            history.write_commit_operation(commit)
            staged = advance_commit_operation(commit, GitSessionHistoryState.STAGED)
            history.write_commit_operation(staged)
            committed = advance_commit_operation(
                staged,
                GitSessionHistoryState.COMMITTED,
                commit_id="3" * len(tip),
            )
            history.write_commit_operation(committed)

            checkpoint = _checkpoint_running("4" * len(tip))
            history.write_checkpoint_operation(checkpoint)
            unknown = advance_checkpoint_operation(
                checkpoint,
                GitSessionHistoryState.OUTCOME_UNKNOWN,
                alternate_index_preserved=True,
            )
            history.write_checkpoint_operation(unknown)
            with self.assertRaises(GitSessionHistoryError) as caught:
                history.write_checkpoint_operation(checkpoint)
            self.assertIs(
                caught.exception.code,
                GitSessionHistoryFailureCode.TRANSITION_INVALID,
            )
        with open_git_session_history(root, _manifest(), "novel") as restarted:
            self.assertEqual(restarted.read_commit_operation(_OPERATION_ID), committed)
            self.assertEqual(restarted.read_checkpoint_operation("c" * 32), unknown)
            inspection = restarted.inspect()
        self.assertEqual(
            inspection.commit_operations,
            ((_OPERATION_ID, GitSessionHistoryState.COMMITTED),),
        )
        self.assertEqual(
            inspection.checkpoint_operations,
            (("c" * 32, GitSessionHistoryState.OUTCOME_UNKNOWN),),
        )








if __name__ == "__main__":
    unittest.main()
