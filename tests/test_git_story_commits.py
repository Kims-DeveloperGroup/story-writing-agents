"""End-to-end authority, decision, commit, and recovery coverage."""

from __future__ import annotations

import copy
import multiprocessing
import os
import pickle
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest import mock

import story_writing_agents
import story_writing_agents.git as git_facade
from story_writing_agents.git import story_commits as commits_module
from story_writing_agents.git import repository_lock as lock_module
from story_writing_agents.git.session_history import (
    GitSessionHistory,
    GitSessionRecoveryHistory,
    open_git_session_history,
)
from story_writing_agents.git.session_history_contracts import (
    GIT_SESSION_HISTORY_DIRECTORY,
    GitSessionCommitOperationRecord,
    GitSessionHistoryError,
    GitSessionHistoryFailureCode,
    GitSessionHistoryRecoveryAction,
    GitSessionHistoryState,
    advance_commit_operation,
    encode_commit_operation,
)
from story_writing_agents.git.story_commit_contracts import (
    StoryCommitApplicationEvidence,
    StoryCommitConfirmation,
    StoryCommitDecision,
    StoryCommitError,
    StoryCommitFailureCode,
    StoryCommitRecoveryAction,
    StoryCommitState,
)
from story_writing_agents.git.story_commits import StoryCommitService
from story_writing_agents.workspace.initialization import (
    SessionArtifactBindingError,
    compose_session_artifact_binding,
)
from story_writing_agents.workspace import initialization as initialization_module
from story_writing_agents.workspace.schema import canonical_initializer_scaffold
from story_writing_agents.writing_artifacts import (
    ArtifactKind,
    ArtifactReplaceOperation,
    build_artifact_change_set,
    canonical_artifact_kind,
)
from story_writing_agents.writing_artifacts.repository import WritingArtifactRepository

from tests.test_git_story_commit_candidates import (
    StoryCommitGitTestCase,
    manifest,
    sha,
)


class StoryCommitServiceTests(StoryCommitGitTestCase):
    def prepared(self, name: str = "commit story 한글", *, object_format: str = "sha1"):
        root, session_root, evidence = self.applied_replace(
            name, object_format=object_format
        )
        service = StoryCommitService(root, manifest(), "novel")
        candidate = service.prepare(
            evidence, "Commit exact 장면 🌙", ("reader impact",)
        )
        return root, session_root, service, candidate




    @staticmethod
    def confirmation(candidate, decision=StoryCommitDecision.COMMIT, suffix="1"):
        return StoryCommitConfirmation(
            f"confirmation:{suffix}",
            candidate.candidate_id,
            candidate.proposal_id,
            candidate.correlation_id,
            decision,
        )






    def test_only_exact_commit_decision_authorizes_and_confirmation_cannot_replay(
        self,
    ) -> None:
        _root, _session_root, service, candidate = self.prepared("decision")
        for decision in (StoryCommitDecision.REJECT, StoryCommitDecision.REVISE):
            with self.subTest(decision=decision):
                with self.assertRaises(StoryCommitError) as caught:
                    service.authorize(
                        candidate,
                        self.confirmation(candidate, decision, decision.value),
                    )
                self.assertIs(
                    caught.exception.code, StoryCommitFailureCode.AUTHORITY_INVALID
                )
        confirmation = self.confirmation(candidate, suffix="exact")
        authority = service.authorize(candidate, confirmation)
        self.assertEqual(
            authority._operation_id,
            commits_module._candidate_operation_id("commit", candidate.candidate_id),
        )
        with self.assertRaises(StoryCommitError) as caught:
            service.authorize(candidate, confirmation)
        self.assertIs(caught.exception.code, StoryCommitFailureCode.AUTHORITY_INVALID)
        mismatched = StoryCommitConfirmation(
            "confirmation:mismatch",
            candidate.candidate_id,
            "proposal:other",
            candidate.correlation_id,
            StoryCommitDecision.COMMIT,
        )
        with self.assertRaises(StoryCommitError) as caught:
            service.authorize(candidate, mismatched)
        self.assertIs(caught.exception.code, StoryCommitFailureCode.AUTHORITY_INVALID)


























if __name__ == "__main__":
    unittest.main()
