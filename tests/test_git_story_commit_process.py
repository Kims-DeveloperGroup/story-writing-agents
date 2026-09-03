"""Git process ordering and closed-command coverage for story commits."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from story_writing_agents.git.process import GitProcessProfile, GitProcessRunner
from story_writing_agents.git.session_history import GitSessionHistory
from story_writing_agents.git.story_commit_contracts import (
    StoryCommitConfirmation,
    StoryCommitDecision,
    StoryCommitError,
    StoryCommitFailureCode,
    StoryCommitState,
)
from story_writing_agents.git.story_commits import StoryCommitService

from tests.test_git_story_commit_candidates import StoryCommitGitTestCase, manifest


class StoryCommitProcessTests(StoryCommitGitTestCase):
    def service_candidate(self):
        root, session_root, evidence = self.applied_replace("process story 한글")
        service = StoryCommitService(root, manifest(), "novel")
        candidate = service.prepare(
            evidence, "Commit exact 장면 🌙", ("reader impact",)
        )
        confirmation = StoryCommitConfirmation(
            "confirmation:process",
            candidate.candidate_id,
            candidate.proposal_id,
            candidate.correlation_id,
            StoryCommitDecision.COMMIT,
        )
        return root, session_root, service, candidate, confirmation

    def test_durable_running_and_staged_precede_exact_literal_mutation_commands(
        self,
    ) -> None:
        _root, _session_root, service, candidate, confirmation = (
            self.service_candidate()
        )
        authority = service.authorize(candidate, confirmation)
        events: list[tuple[str, object]] = []
        original_run = GitProcessRunner.run
        original_write = GitSessionHistory.write_commit_operation

        def observed_run(runner, arguments, **kwargs):
            events.append(
                ("git", (arguments, kwargs.get("profile"), kwargs.get("stdin", b"")))
            )
            return original_run(runner, arguments, **kwargs)

        def observed_write(history, record):
            events.append(("history", record.state.value))
            return original_write(history, record)

        with (
            mock.patch.object(GitProcessRunner, "run", observed_run),
            mock.patch.object(
                GitSessionHistory, "write_commit_operation", observed_write
            ),
        ):
            result = service.execute(authority)
        self.assertIs(result.state, StoryCommitState.COMMITTED)
        history_events = [
            (index, value)
            for index, (kind, value) in enumerate(events)
            if kind == "history"
        ]
        git_events = [
            (index, value)
            for index, (kind, value) in enumerate(events)
            if kind == "git"
        ]
        running = next(index for index, value in history_events if value == "running")
        staged = next(index for index, value in history_events if value == "staged")
        committed = next(
            index for index, value in history_events if value == "committed"
        )
        mutating = [
            (index, value)
            for index, value in git_events
            if value[1]
            in {GitProcessProfile.MUTATION, GitProcessProfile.IDENTITY_MUTATION}
        ]
        commit_tree = next(
            index for index, value in git_events if value[0][0] == "commit-tree"
        )
        self.assertLess(running, mutating[0][0])
        self.assertLess(staged, commit_tree)
        self.assertLess(commit_tree, committed)
        literal = [(value[0], value[1], value[2]) for _index, value in mutating]
        self.assertEqual(
            literal[0][0], ("hash-object", "-w", "--no-filters", "--stdin")
        )
        self.assertEqual(literal[0][1], GitProcessProfile.MUTATION)
        self.assertEqual(literal[0][2], "새 장면 🌙\n".encode())
        blob = next(
            value[0][3].split(",")[1]
            for value in literal
            if value[0][0] == "update-index"
        )
        self.assertIn(
            (
                (
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"100644,{blob},core_documents/global_outline.md",
                ),
                GitProcessProfile.MUTATION,
                b"",
            ),
            literal,
        )
        self.assertIn((("write-tree",), GitProcessProfile.MUTATION, b""), literal)
        self.assertIn(
            (
                ("commit-tree", candidate.expected_tree, "-p", candidate.branch_tip),
                GitProcessProfile.IDENTITY_MUTATION,
                "Commit exact 장면 🌙\n".encode(),
            ),
            literal,
        )
        self.assertIn(
            (
                (
                    "update-ref",
                    f"refs/heads/{candidate.branch}",
                    result.commit_id,
                    candidate.branch_tip,
                ),
                GitProcessProfile.MUTATION,
                b"",
            ),
            literal,
        )
        forbidden = {
            "add",
            "checkout",
            "clean",
            "fetch",
            "merge",
            "pull",
            "push",
            "remote",
            "reset",
        }
        self.assertTrue(
            forbidden.isdisjoint(
                {arguments[0] for arguments, _profile, _stdin in literal}
            )
        )

    def test_hostile_hooks_alias_filters_pagers_editors_and_replacements_do_not_run(
        self,
    ) -> None:
        root, _session_root, service, candidate, confirmation = self.service_candidate()
        marker = self.test_root / "hostile-marker"
        hook = root / ".git" / "hooks" / "post-commit"
        hook.parent.mkdir()
        hook.write_text(f"#!/bin/sh\necho hostile > '{marker}'\n", encoding="utf-8")
        hook.chmod(0o700)
        for key, value in (
            ("alias.commit-tree", f"!echo hostile > {marker}"),
            ("commit.gpgSign", "true"),
            ("filter.story.clean", f"echo hostile > {marker}"),
            ("core.pager", f"echo hostile > {marker}"),
        ):
            self.run_git(root, "config", key, value)
        with mock.patch.dict(
            os.environ,
            {
                **self.environment,
                "GIT_EDITOR": f"echo hostile > {marker}",
                "GIT_PAGER": f"echo hostile > {marker}",
                "GIT_REPLACE_REF_BASE": "refs/replace/hostile",
            },
            clear=True,
        ):
            result = service.execute(service.authorize(candidate, confirmation))
        self.assertIs(result.state, StoryCommitState.COMMITTED)
        self.assertFalse(marker.exists())

    def test_failure_after_staging_is_durable_content_free_and_preserves_index(
        self,
    ) -> None:
        _root, session_root, service, candidate, confirmation = self.service_candidate()
        authority = service.authorize(candidate, confirmation)
        with (
            mock.patch.object(
                StoryCommitService,
                "_create_commit",
                side_effect=StoryCommitError(StoryCommitFailureCode.GIT_FAILED),
            ),
            self.assertRaises(StoryCommitError) as caught,
        ):
            service.execute(authority)
        self.assertIs(caught.exception.code, StoryCommitFailureCode.OUTCOME_UNKNOWN)
        self.assertNotIn("새 장면", repr(caught.exception))
        staged = self.run_git(session_root, "diff", "--cached", "--name-only").stdout
        self.assertEqual(staged, b"core_documents/global_outline.md\n")


if __name__ == "__main__":
    unittest.main()
