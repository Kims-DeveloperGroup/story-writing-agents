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









if __name__ == "__main__":
    unittest.main()
