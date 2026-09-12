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

    def test_exports_are_closed_and_authority_is_one_use_owner_thread_bound_nonserializable(
        self,
    ) -> None:
        self.assertEqual(
            tuple(commits_module.__all__),
            ("StoryCommitAuthority", "StoryCommitService"),
        )
        for name in commits_module.__all__:
            self.assertFalse(hasattr(git_facade, name), name)
            self.assertFalse(hasattr(story_writing_agents, name), name)
        root, _session_root, service, candidate = self.prepared("authority")
        authority = service.authorize(candidate, self.confirmation(candidate))
        self.assertEqual(repr(authority), "StoryCommitAuthority(<process-local>)")
        for action in (copy.copy, copy.deepcopy, pickle.dumps):
            with self.subTest(action=action.__name__):
                with self.assertRaises(StoryCommitError) as caught:
                    action(authority)
                self.assertIs(
                    caught.exception.code, StoryCommitFailureCode.AUTHORITY_INVALID
                )
        original_pid = authority._pid  # noqa: SLF001
        authority._pid = -1  # noqa: SLF001
        with self.assertRaises(StoryCommitError) as caught:
            service.execute(authority)
        self.assertIs(caught.exception.code, StoryCommitFailureCode.AUTHORITY_INVALID)
        authority._pid = os.getpid()  # noqa: SLF001
        self.assertEqual(original_pid, authority._pid)  # noqa: SLF001
        foreign_service = StoryCommitService(root, manifest(), "novel")
        with self.assertRaises(StoryCommitError) as caught:
            foreign_service.execute(authority)
        self.assertIs(caught.exception.code, StoryCommitFailureCode.AUTHORITY_INVALID)
        with ThreadPoolExecutor(max_workers=1) as executor:
            error = executor.submit(self._execute_error, service, authority).result()
        self.assertIs(error.code, StoryCommitFailureCode.AUTHORITY_INVALID)
        result = service.execute(authority)
        self.assertIs(result.state, StoryCommitState.COMMITTED)
        with self.assertRaises(StoryCommitError) as caught:
            service.execute(authority)
        self.assertIs(caught.exception.code, StoryCommitFailureCode.AUTHORITY_CONSUMED)

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

    def test_apply_identity_blocks_pre_running_replacement_and_marks_post_running_unknown(
        self,
    ) -> None:
        def prepared_bound(name):
            root, session_root, evidence = self.applied_replace(name)
            observed = os.stat(session_root)
            service = commits_module.create_identity_bound_story_commit_service(
                root,
                manifest(),
                "novel",
                (observed.st_dev, observed.st_ino),
            )
            candidate = service.prepare(evidence, "Commit exact change", ("impact",))
            authority = service.authorize(candidate, self.confirmation(candidate))
            return root, session_root, service, authority

        _root, session_root, service, authority = prepared_bound(
            "pre-running-root-replacement"
        )
        before_head = self.run_git(session_root, "rev-parse", "HEAD").stdout
        before_index = self.run_git(
            session_root,
            "diff",
            "--cached",
            "--name-only",
        ).stdout
        with self.replace_session_root(session_root), self.assertRaises(
            StoryCommitError
        ) as pre_running:
            service.execute(authority)
        self.assertIs(
            pre_running.exception.code,
            StoryCommitFailureCode.CANDIDATE_DRIFT,
        )
        self.assertEqual(self.run_git(session_root, "rev-parse", "HEAD").stdout, before_head)
        self.assertEqual(
            self.run_git(session_root, "diff", "--cached", "--name-only").stdout,
            before_index,
        )

        _root, session_root, service, authority = prepared_bound(
            "post-running-root-replacement"
        )
        before_head = self.run_git(session_root, "rev-parse", "HEAD").stdout
        before_index = self.run_git(
            session_root,
            "diff",
            "--cached",
            "--name-only",
        ).stdout
        original_stage = service._stage_git

        def replace_after_running(revalidation):
            with self.replace_session_root(session_root):
                return original_stage(revalidation)

        with mock.patch.object(
            StoryCommitService,
            "_stage_git",
            side_effect=replace_after_running,
        ), self.assertRaises(StoryCommitError) as post_running:
            service.execute(authority)
        self.assertIs(
            post_running.exception.code,
            StoryCommitFailureCode.OUTCOME_UNKNOWN,
        )
        self.assertEqual(self.run_git(session_root, "rev-parse", "HEAD").stdout, before_head)
        self.assertEqual(
            self.run_git(session_root, "diff", "--cached", "--name-only").stdout,
            before_index,
        )
    def test_reject_and_revise_persist_result_without_git_or_artifact_mutation(
        self,
    ) -> None:
        for decision in (StoryCommitDecision.REJECT, StoryCommitDecision.REVISE):
            with self.subTest(decision=decision):
                root, session_root, service, candidate = self.prepared(
                    f"decision-{decision.value}"
                )
                before = self.repository_snapshot(root)
                staged_before = self.run_git(
                    session_root, "diff", "--cached", "--name-only"
                ).stdout
                artifact_before = (
                    session_root / "core_documents/global_outline.md"
                ).read_bytes()
                action = (
                    service.reject
                    if decision is StoryCommitDecision.REJECT
                    else service.revise
                )
                result = action(
                    candidate, self.confirmation(candidate, decision, decision.value)
                )
                self.assertIs(result.state, StoryCommitState.REJECTED)
                self.assertIs(result.recovery_action, StoryCommitRecoveryAction.NONE)
                self.assertEqual(self.repository_snapshot(root), before)
                self.assertEqual(
                    self.run_git(
                        session_root, "diff", "--cached", "--name-only"
                    ).stdout,
                    staged_before,
                )
                self.assertEqual(
                    (session_root / "core_documents/global_outline.md").read_bytes(),
                    artifact_before,
                )
                self.assertEqual(service.inspect(result.operation_id), result)

    def test_real_sha1_and_sha256_commits_have_exact_tree_parent_identity_and_invariants(
        self,
    ) -> None:
        for algorithm in ("sha1", "sha256"):
            with self.subTest(algorithm=algorithm):
                root, session_root, service, candidate = self.prepared(
                    f"{algorithm} root with spaces 한글", object_format=algorithm
                )
                main_before = self.repository_snapshot(root)
                main_head_before = self.run_git(root, "rev-parse", "HEAD").stdout
                internal_before = self.run_git(
                    root,
                    "rev-parse",
                    "refs/story-writing-agents/runtime/sessions/novel",
                ).stdout
                unrelated = session_root / "unrelated private bytes.txt"
                unrelated.write_bytes(b"preserve unrelated private bytes\n")
                result = service.execute(
                    service.authorize(
                        candidate, self.confirmation(candidate, suffix=algorithm)
                    )
                )
                self.assertIs(result.state, StoryCommitState.COMMITTED)
                self.assertIsNotNone(result.commit_id)
                self.assertEqual(
                    len(result.commit_id or ""), 40 if algorithm == "sha1" else 64
                )
                commit = self.run_git(
                    session_root, "cat-file", "commit", result.commit_id or ""
                ).stdout
                headers, message = commit.split(b"\n\n", 1)
                self.assertIn(
                    f"tree {candidate.expected_tree}\n".encode(), headers + b"\n"
                )
                self.assertEqual(
                    [
                        line
                        for line in headers.splitlines()
                        if line.startswith(b"parent ")
                    ],
                    [f"parent {candidate.branch_tip}".encode()],
                )
                self.assertIn(
                    b"author Session Author <session-author@example.invalid>", headers
                )
                self.assertIn(
                    b"committer Session Committer <session-committer@example.invalid>",
                    headers,
                )
                self.assertEqual(message, "Commit exact 장면 🌙\n".encode())
                self.assertEqual(
                    self.run_git(session_root, "rev-parse", "HEAD^{tree}")
                    .stdout.strip()
                    .decode(),
                    candidate.expected_tree,
                )
                self.assertEqual(
                    self.run_git(root, "rev-parse", "HEAD").stdout, main_head_before
                )
                self.assertEqual(
                    self.run_git(
                        root,
                        "status",
                        "--porcelain=v2",
                        "-z",
                        "--untracked-files=all",
                    ).stdout,
                    main_before[4],
                )
                self.assertEqual(
                    self.run_git(
                        root,
                        "rev-parse",
                        "refs/story-writing-agents/runtime/sessions/novel",
                    ).stdout,
                    internal_before,
                )
                self.assertEqual(
                    unrelated.read_bytes(), b"preserve unrelated private bytes\n"
                )
                self.assertEqual((root / ".git" / "index").read_bytes(), main_before[5])

    def test_restart_recovers_correlated_commit_after_branch_cas(self) -> None:
        root, _session_root, service, candidate = self.prepared("restart")
        authority = service.authorize(
            candidate, self.confirmation(candidate, suffix="restart")
        )
        with (
            mock.patch.object(
                StoryCommitService,
                "_verify_invariants",
                side_effect=StoryCommitError(StoryCommitFailureCode.OUTCOME_UNKNOWN),
            ),
            self.assertRaises(StoryCommitError) as caught,
        ):
            service.execute(authority)
        self.assertIs(caught.exception.code, StoryCommitFailureCode.OUTCOME_UNKNOWN)
        with open_git_session_history(root, manifest(), "novel") as history:
            operation_id = history.inspect().commit_operations[0][0]
        restarted = StoryCommitService(root, manifest(), "novel")
        recovered = restarted.recover(operation_id)
        self.assertIs(recovered.state, StoryCommitState.COMMITTED)
        self.assertIsNotNone(recovered.commit_id)
        self.assertEqual(restarted.inspect(operation_id), recovered)


if __name__ == "__main__":
    unittest.main()
