"""Real-repository coverage for exact internal-ref checkpoints."""

from __future__ import annotations

import hashlib
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from story_writing_agents.git import session_checkpoints as checkpoints_module
from story_writing_agents.git import story_commits as story_commits_module
from story_writing_agents.git.session_checkpoint_contracts import (
    SessionCheckpointError,
    SessionCheckpointFailureCode,
    SessionCheckpointOwner,
    SessionCheckpointPathOperation,
    SessionCheckpointRecoveryAction,
    SessionCheckpointRequest,
    SessionCheckpointSnapshot,
    SessionCheckpointState,
)
from story_writing_agents.git.session_checkpoints import SessionCheckpointService
from story_writing_agents.git.process import GitProcessProfile, GitProcessRunner
from story_writing_agents.git.session_history import (
    GitSessionHistory,
    open_git_session_history,
)
from story_writing_agents.git.story_commit_contracts import (
    StoryCommitConfirmation,
    StoryCommitDecision,
    StoryCommitError,
    StoryCommitFailureCode,
)
from story_writing_agents.git.story_commits import StoryCommitService
from story_writing_agents.workspace.initialization import provision_session_resources
from story_writing_agents.workspace.schema import canonical_initializer_scaffold

from tests.test_git_story_commit_candidates import StoryCommitGitTestCase, manifest


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class SessionCheckpointGitTests(StoryCommitGitTestCase):
    def ready(self, name: str, *, object_format: str = "sha1"):
        root = self.initialize_workspace(name, object_format=object_format)
        resource = provision_session_resources(root, "novel")
        return (
            root,
            resource.identity.worktree_root,
            SessionCheckpointService(root, manifest(), "novel"),
        )

    @staticmethod
    def request(snapshot: SessionCheckpointSnapshot | None, suffix: str = "1"):
        return SessionCheckpointRequest(
            "novel",
            f"correlation:{suffix}",
            () if snapshot is None else (snapshot,),
        )

    @staticmethod
    def snapshot(operation, *, before=None, content=None):
        return SessionCheckpointSnapshot(
            SessionCheckpointOwner.WORKSPACE,
            "agent_states/orchestrator_state/sessions/novel.json",
            operation,
            before,
            None if content is None else digest(content),
            content,
        )

    def invariant_snapshot(self, root: Path, session_root: Path):
        return (
            self.run_git(root, "symbolic-ref", "HEAD").stdout,
            self.run_git(root, "rev-parse", "HEAD").stdout,
            self.run_git(
                root, "status", "--porcelain=v2", "-z", "--untracked-files=all"
            ).stdout,
            (root / ".git" / "index").read_bytes(),
            self.run_git(session_root, "symbolic-ref", "HEAD").stdout,
            self.run_git(session_root, "rev-parse", "HEAD").stdout,
            self.run_git(session_root, "ls-files", "--stage", "-z").stdout,
            tuple(
                (item.path, (session_root / item.path).read_bytes())
                for item in canonical_initializer_scaffold()
                if item.path.startswith(("core_documents/", "manuscript/"))
            ),
        )

    def test_sha1_create_update_delete_have_exact_metadata_and_preserve_every_nonref_invariant(
        self,
    ) -> None:
        root, session_root, service = self.ready("checkpoint root 한글")
        source = session_root / "agent_states/orchestrator_state/sessions/novel.json"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"owner source remains untouched\n")
        unrelated = session_root / "unrelated bytes.txt"
        unrelated.write_bytes(b"unrelated bytes\n")
        before = self.invariant_snapshot(root, session_root)
        first = b'{"state":"one"}\n'
        second = '{"state":"둘 🌙"}\n'.encode()
        with (
            mock.patch.object(checkpoints_module.time, "time", return_value=123456789),
            mock.patch.object(
                checkpoints_module.uuid, "uuid4", return_value=mock.Mock(hex="a" * 32)
            ),
        ):
            created = service.checkpoint(
                self.request(
                    self.snapshot(SessionCheckpointPathOperation.CREATE, content=first),
                    "create",
                )
            )
        self.assertIs(created.state, SessionCheckpointState.COMMITTED)
        payload = self.run_git(
            root, "cat-file", "commit", created.checkpoint_id or ""
        ).stdout
        headers, message = payload.split(b"\n\n", 1)
        self.assertEqual(
            len([line for line in headers.splitlines() if line.startswith(b"parent ")]),
            1,
        )
        self.assertIn(
            b"author Session Author <session-author@example.invalid> 123456789 +0000",
            headers,
        )
        self.assertIn(
            b"committer Session Committer <session-committer@example.invalid> 123456789 +0000",
            headers,
        )
        self.assertEqual(
            message,
            f"Checkpoint story session runtime: novel\n\nCorrelation: correlation:create\nOperation: {'a' * 32}\n".encode(),
        )
        updated = service.checkpoint(
            self.request(
                self.snapshot(
                    SessionCheckpointPathOperation.UPDATE,
                    before=digest(first),
                    content=second,
                ),
                "update",
            )
        )
        deleted = service.checkpoint(
            self.request(
                self.snapshot(
                    SessionCheckpointPathOperation.DELETE, before=digest(second)
                ),
                "delete",
            )
        )
        self.assertTrue(
            all(
                result.state is SessionCheckpointState.COMMITTED
                for result in (updated, deleted)
            )
        )
        self.assertEqual(self.invariant_snapshot(root, session_root), before)
        self.assertEqual(source.read_bytes(), b"owner source remains untouched\n")
        self.assertEqual(unrelated.read_bytes(), b"unrelated bytes\n")

    def test_empty_and_same_tree_requests_are_unchanged_without_history_or_git_mutation(
        self,
    ) -> None:
        root, session_root, service = self.ready("unchanged")
        before = (
            self.repository_snapshot(root),
            self.invariant_snapshot(root, session_root),
        )
        result = service.checkpoint(self.request(None, "empty"))
        self.assertIs(result.state, SessionCheckpointState.UNCHANGED)
        self.assertIs(result.recovery_action, SessionCheckpointRecoveryAction.NONE)
        self.assertIsNone(result.operation_id)
        self.assertEqual(
            (
                self.repository_snapshot(root),
                self.invariant_snapshot(root, session_root),
            ),
            before,
        )
        history_root = root / ".git/story-writing-agents/runtime/session-history/novel"
        self.assertFalse((history_root / "checkpoint-operations").exists())
        self.assertFalse((history_root / "checkpoint-inputs").exists())

    def test_sha256_unicode_content_and_spaced_root_commit_exact_snapshot(self) -> None:
        root, _session_root, service = self.ready(
            "sha256 space 한글", object_format="sha256"
        )
        content = "유니코드 상태 🌙\n".encode()
        result = service.checkpoint(
            self.request(
                self.snapshot(SessionCheckpointPathOperation.CREATE, content=content),
                "sha256",
            )
        )
        self.assertIs(result.state, SessionCheckpointState.COMMITTED)
        self.assertEqual(len(result.checkpoint_id or ""), 64)
        tree = self.run_git(
            root, "ls-tree", "-r", "--name-only", result.checkpoint_id or ""
        ).stdout
        self.assertEqual(tree, b"agent_states/orchestrator_state/sessions/novel.json\n")
        blob = self.run_git(
            root,
            "show",
            f"{result.checkpoint_id}:agent_states/orchestrator_state/sessions/novel.json",
        ).stdout
        self.assertEqual(blob, content)

    def test_pre_cas_and_post_cas_faults_restart_conservatively_without_replay(
        self,
    ) -> None:
        for point in ("commit", "verify"):
            with self.subTest(point=point):
                root, _session_root, service = self.ready(f"fault-{point}")
                request = self.request(
                    self.snapshot(
                        SessionCheckpointPathOperation.CREATE, content=b"state\n"
                    ),
                    point,
                )
                target = "_commit" if point == "commit" else "_verify_invariants"
                with (
                    mock.patch.object(
                        SessionCheckpointService,
                        target,
                        side_effect=SessionCheckpointError(
                            SessionCheckpointFailureCode.GIT_FAILED,
                            SessionCheckpointRecoveryAction.NONE,
                        ),
                    ),
                    self.assertRaises(SessionCheckpointError) as caught,
                ):
                    service.checkpoint(request)
                self.assertIs(
                    caught.exception.code, SessionCheckpointFailureCode.OUTCOME_UNKNOWN
                )
                with open_git_session_history(root, manifest(), "novel") as history:
                    operation_id = history.inspect().checkpoint_operations[0][0]
                recovered = SessionCheckpointService(root, manifest(), "novel").recover(
                    operation_id
                )
                expected = (
                    SessionCheckpointState.RECOVERY_REQUIRED
                    if point == "commit"
                    else SessionCheckpointState.COMMITTED
                )
                self.assertIs(recovered.state, expected)

    def test_malformed_existing_internal_tree_and_snapshot_drift_fail_closed(
        self,
    ) -> None:
        root, _session_root, service = self.ready("malformed")
        internal_ref = "refs/story-writing-agents/runtime/sessions/novel"
        parent = self.run_git(root, "rev-parse", internal_ref).stdout.strip()
        blob = self.run_git(
            root, "hash-object", "-w", "--stdin", input_bytes=b"excluded\n"
        ).stdout.strip()
        excluded_tree = self.run_git(
            root,
            "mktree",
            input_bytes=b"100644 blob " + blob + b"\tprivate.jsonl\n",
        ).stdout.strip()
        tree = self.run_git(
            root,
            "mktree",
            input_bytes=b"040000 tree " + excluded_tree + b"\tdebug_logs\n",
        ).stdout.strip()
        commit = self.run_git(
            root,
            "commit-tree",
            tree.decode(),
            "-p",
            parent.decode(),
            input_bytes=b"malformed\n",
            environment=self.environment,
        ).stdout.strip()
        self.run_git(root, "update-ref", internal_ref, commit.decode(), parent.decode())
        with self.assertRaises(SessionCheckpointError) as caught:
            service.checkpoint(self.request(None, "malformed"))
        self.assertIs(
            caught.exception.code, SessionCheckpointFailureCode.RECOVERY_REQUIRED
        )

    def test_checkpoint_serializes_with_story_commit_and_stale_candidate_fails_before_mutation(
        self,
    ) -> None:
        root, session_root, evidence = self.applied_replace("interleave")
        story = StoryCommitService(root, manifest(), "novel")
        candidate = story.prepare(evidence, "Commit story", ("impact",))
        checkpoint = SessionCheckpointService(root, manifest(), "novel")
        request = self.request(
            self.snapshot(
                SessionCheckpointPathOperation.CREATE, content=b"runtime state\n"
            ),
            "interleave",
        )
        entered = threading.Event()
        release = threading.Event()
        attempted = threading.Event()
        original_prepare = SessionCheckpointService._prepare  # noqa: SLF001
        original_open = story_commits_module.open_git_session_history

        def paused_prepare(service, value):
            entered.set()
            if not release.wait(timeout=10):
                raise AssertionError("checkpoint release barrier timed out")
            return original_prepare(service, value)

        def observed_open(*args, **kwargs):
            attempted.set()
            return original_open(*args, **kwargs)

        confirmation = StoryCommitConfirmation(
            "confirmation:interleave",
            candidate.candidate_id,
            candidate.proposal_id,
            candidate.correlation_id,
            StoryCommitDecision.COMMIT,
        )

        def authorize_story():
            try:
                return story.authorize(candidate, confirmation)
            except StoryCommitError as error:
                return error

        branch_before = self.run_git(session_root, "rev-parse", "HEAD").stdout
        with (
            mock.patch.object(SessionCheckpointService, "_prepare", paused_prepare),
            mock.patch.object(
                story_commits_module, "open_git_session_history", observed_open
            ),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            checkpoint_future = executor.submit(checkpoint.checkpoint, request)
            self.assertTrue(entered.wait(timeout=10))
            story_future = executor.submit(authorize_story)
            self.assertTrue(attempted.wait(timeout=10))
            self.assertFalse(story_future.done())
            release.set()
            checkpoint_result = checkpoint_future.result(timeout=20)
            story_result = story_future.result(timeout=20)
        self.assertIs(checkpoint_result.state, SessionCheckpointState.COMMITTED)
        self.assertIsInstance(story_result, StoryCommitError)
        self.assertIs(story_result.code, StoryCommitFailureCode.CANDIDATE_DRIFT)
        self.assertEqual(
            self.run_git(session_root, "rev-parse", "HEAD").stdout, branch_before
        )

    def test_running_and_committed_evidence_bracket_only_exact_alternate_index_commands(
        self,
    ) -> None:
        root, _session_root, service = self.ready("command-order")
        events: list[tuple[str, object]] = []
        original_run = GitProcessRunner.run
        original_write = GitSessionHistory.write_checkpoint_operation
        original_remove = GitSessionHistory.remove_completed_checkpoint_index

        def observed_run(runner, arguments, **kwargs):
            events.append(
                (
                    "git",
                    (
                        arguments,
                        kwargs.get("profile"),
                        kwargs.get("alternate_index"),
                        kwargs.get("commit_timestamps"),
                    ),
                )
            )
            return original_run(runner, arguments, **kwargs)

        def observed_write(history, record):
            events.append(("history", record.state.value))
            return original_write(history, record)

        def observed_remove(history, alternate):
            events.append(("cleanup", alternate.operation_id))
            return original_remove(history, alternate)

        with (
            mock.patch.object(GitProcessRunner, "run", observed_run),
            mock.patch.object(
                GitSessionHistory, "write_checkpoint_operation", observed_write
            ),
            mock.patch.object(
                GitSessionHistory,
                "remove_completed_checkpoint_index",
                observed_remove,
            ),
        ):
            result = service.checkpoint(
                self.request(
                    self.snapshot(
                        SessionCheckpointPathOperation.CREATE,
                        content=b"runtime state\n",
                    ),
                    "commands",
                )
            )
        self.assertIs(result.state, SessionCheckpointState.COMMITTED)
        running = next(
            i for i, event in enumerate(events) if event == ("history", "running")
        )
        committed = next(
            i for i, event in enumerate(events) if event == ("history", "committed")
        )
        cleanup = next(i for i, event in enumerate(events) if event[0] == "cleanup")
        mutating = [
            (i, value)
            for i, (kind, value) in enumerate(events)
            if kind == "git" and (value[2] is not None or value[0][0] == "update-ref")
        ]
        self.assertLess(running, mutating[0][0])
        self.assertLess(committed, cleanup)
        for _index, (arguments, profile, alternate, timestamps) in mutating:
            if arguments[0] == "update-ref":
                self.assertEqual(
                    arguments[1],
                    "refs/story-writing-agents/runtime/sessions/novel",
                )
                self.assertIs(profile, GitProcessProfile.MUTATION)
                self.assertIsNone(alternate)
            else:
                self.assertIs(profile, GitProcessProfile.INTERNAL_CHECKPOINT)
                self.assertIsNotNone(alternate)
            if arguments[0] == "commit-tree":
                self.assertIsNotNone(timestamps)
                self.assertEqual(arguments[2], "-p")
            else:
                self.assertIsNone(timestamps)
        commands = [value[0] for _index, value in mutating]
        self.assertIn(("read-tree", commands[0][1]), commands)
        self.assertTrue(
            any(
                command[:4] == ("hash-object", "-w", "--no-filters", "--stdin")
                for command in commands
            )
        )
        self.assertTrue(
            any(
                command[:3] == ("update-index", "--add", "--cacheinfo")
                for command in commands
            )
        )
        self.assertNotIn("refs/heads/story/session/novel", repr(commands))

    def test_stale_cas_preserves_foreign_internal_tip_and_restart_does_not_guess(
        self,
    ) -> None:
        root, _session_root, service = self.ready("stale-cas")
        internal_ref = "refs/story-writing-agents/runtime/sessions/novel"
        original_cas = SessionCheckpointService._cas  # noqa: SLF001
        foreign: list[str] = []

        def race(checkpoint_service, operation, checkpoint_id):
            tree = self.run_git(
                root, "rev-parse", f"{operation.expected_internal_tip}^{{tree}}"
            ).stdout.strip()
            commit = (
                self.run_git(
                    root,
                    "commit-tree",
                    tree.decode(),
                    "-p",
                    operation.expected_internal_tip,
                    input_bytes=b"foreign checkpoint\n",
                    environment=self.environment,
                )
                .stdout.strip()
                .decode()
            )
            self.run_git(
                root,
                "update-ref",
                internal_ref,
                commit,
                operation.expected_internal_tip,
            )
            foreign.append(commit)
            return original_cas(checkpoint_service, operation, checkpoint_id)

        with (
            mock.patch.object(SessionCheckpointService, "_cas", race),
            self.assertRaises(SessionCheckpointError) as caught,
        ):
            service.checkpoint(
                self.request(
                    self.snapshot(
                        SessionCheckpointPathOperation.CREATE, content=b"state\n"
                    ),
                    "stale",
                )
            )
        self.assertIs(
            caught.exception.code, SessionCheckpointFailureCode.OUTCOME_UNKNOWN
        )
        self.assertEqual(
            self.run_git(root, "rev-parse", internal_ref).stdout.strip().decode(),
            foreign[0],
        )
        with open_git_session_history(root, manifest(), "novel") as history:
            operation_id = history.inspect().checkpoint_operations[0][0]
        recovered = SessionCheckpointService(root, manifest(), "novel").recover(
            operation_id
        )
        self.assertIs(recovered.state, SessionCheckpointState.OUTCOME_UNKNOWN)


if __name__ == "__main__":
    unittest.main()
