"""End-to-end authority, decision, commit, and recovery coverage."""

from __future__ import annotations

import copy
import os
import pickle
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import story_writing_agents
import story_writing_agents.git as git_facade
from story_writing_agents.git import story_commits as commits_module
from story_writing_agents.git.session_history import open_git_session_history
from story_writing_agents.git.story_commit_contracts import (
    StoryCommitConfirmation,
    StoryCommitDecision,
    StoryCommitError,
    StoryCommitFailureCode,
    StoryCommitRecoveryAction,
    StoryCommitState,
)
from story_writing_agents.git.story_commits import StoryCommitService

from tests.test_git_story_commit_candidates import StoryCommitGitTestCase, manifest


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


    @staticmethod
    def _execute_error(service, authority):
        try:
            service.execute(authority)
        except StoryCommitError as error:
            return error
        raise AssertionError("cross-thread authority unexpectedly executed")

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
        service.authorize(candidate, confirmation)
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
