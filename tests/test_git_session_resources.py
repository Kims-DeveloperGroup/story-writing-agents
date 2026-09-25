"""Acceptance coverage for exact local Git session resources."""

from __future__ import annotations

import os
import hashlib
import stat
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from story_writing_agents import Workspace
from story_writing_agents.git import GitBaselineManifest, GitManifestEntry
from story_writing_agents.git.process import GitProcessResult, GitProcessRunner
from story_writing_agents.git.session_contracts import (
    GIT_SESSION_PROVISIONING_DIRECTORY,
    GIT_SESSION_SEED_MESSAGE_PREFIX,
    GitSessionIdentity,
    GitSessionFailureCode,
    GitSessionOperationKind,
    GitSessionRecoveryAction,
    GitSessionResourceError,
    GitSessionResourceState,
)
from story_writing_agents.git import session_resources as resources_module
from story_writing_agents.git.session_resources import _Repository
from story_writing_agents.workspace.initialization import (
    inspect_session_resources,
    list_session_resources,
    provision_session_resources,
    require_ready_session_resources,
)
from story_writing_agents.workspace.schema import (
    WORKSPACE_INITIALIZATION_JOURNAL_PATH,
    WorkspaceInitializationJournal,
    canonical_initializer_scaffold,
    encode_initialization_journal,
)

from tests.git_contract_fixture import DisposableGitTestCase, exception_graph
from tests.helpers import materialize_pristine_initialized_workspace




class GitSessionResourceGitTestCase(DisposableGitTestCase):
    """Build isolated schema-v2 repositories in both supported object formats."""

    def session_environment(self) -> dict[str, str]:
        return self.git_environment(
            GIT_AUTHOR_NAME="Session Author",
            GIT_AUTHOR_EMAIL="session-author@example.invalid",
            GIT_COMMITTER_NAME="Session Committer",
            GIT_COMMITTER_EMAIL="session-committer@example.invalid",
        )

    def initialize_workspace(self, name: str, *, object_format: str = "sha1") -> Path:
        root = self.test_root / name
        if object_format == "sha1":
            materialize_pristine_initialized_workspace(root)
            return root
        root.mkdir()
        scaffold = canonical_initializer_scaffold()
        manifest = GitBaselineManifest(
            tuple(
                GitManifestEntry(entry.path, hashlib.sha256(entry.content).hexdigest())
                for entry in scaffold
            )
        )
        for entry in scaffold:
            target = root / entry.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(entry.content)
        (root / WORKSPACE_INITIALIZATION_JOURNAL_PATH).write_bytes(
            encode_initialization_journal(
                WorkspaceInitializationJournal(manifest.manifest_digest)
            )
        )
        initialized = self.run_git(
            root,
            "init",
            "--quiet",
            "--initial-branch=main",
            "--template=",
            "--object-format=sha256",
            check=False,
        )
        if initialized.returncode != 0:
            self.skipTest("installed Git does not support SHA-256 repositories")
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            Workspace(root).initialize()
        return root

    def repository_snapshot(self, root: Path) -> tuple[bytes, ...]:
        return (
            self.run_git(
                root,
                "for-each-ref",
                "--format=%(refname)%00%(objectname)",
            ).stdout,
            self.run_git(root, "worktree", "list", "--porcelain", "-z").stdout,
            self.run_git(root, "symbolic-ref", "HEAD").stdout,
            self.run_git(root, "rev-parse", "HEAD^{tree}").stdout,
            self.run_git(root, "status", "--porcelain=v2", "--").stdout,
            (root / ".git" / "index").read_bytes(),
            self.run_git(root, "count-objects", "-v").stdout,
        )




class GitSessionResourceLifecycleTests(GitSessionResourceGitTestCase):
    """Prove real correlated resources and immutable baseline/seed anchors."""

    def test_create_reopen_and_inventory_are_exact_and_root_invariant(self) -> None:
        root = self.initialize_workspace("Story Space 한글")
        baseline = self.run_git(root, "rev-parse", "HEAD").stdout.decode().strip()
        root_before = self.repository_snapshot(root)

        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            created = provision_session_resources(root, "Novel_01")
        self.assertIs(created.state, GitSessionResourceState.READY)
        self.assertEqual(created.baseline_commit_id, baseline)
        self.assertEqual(created.initial_branch_tip, baseline)
        identity = created.identity
        self.assertTrue(identity.worktree_root.is_dir())
        self.assertEqual(
            self.run_git(root, "rev-parse", f"refs/heads/{identity.branch}").stdout,
            (baseline + "\n").encode(),
        )
        self.assertEqual(
            self.run_git(identity.worktree_root, "symbolic-ref", "HEAD").stdout,
            f"refs/heads/{identity.branch}\n".encode(),
        )
        self.assertEqual(
            self.run_git(root, "status", "--porcelain=v2", "--").stdout,
            b"",
        )
        self.assertEqual(
            self.run_git(root, "symbolic-ref", "HEAD").stdout,
            root_before[2],
        )
        self.assertEqual(
            self.run_git(root, "rev-parse", "HEAD^{tree}").stdout, root_before[3]
        )
        self.assertEqual((root / ".git" / "index").read_bytes(), root_before[5])

        seed = created.seed_commit_id
        assert seed is not None
        payload = self.run_git(root, "cat-file", "commit", seed).stdout
        header, message = payload.split(b"\n\n", 1)
        self.assertIn(b"author Session Author <session-author@example.invalid>", header)
        self.assertIn(
            b"committer Session Committer <session-committer@example.invalid>", header
        )
        self.assertNotIn(b"parent ", header)
        self.assertEqual(
            message, f"{GIT_SESSION_SEED_MESSAGE_PREFIX}Novel_01\n".encode()
        )
        empty_tree = self.run_git(
            root, "hash-object", "-t", "tree", "--stdin", input_bytes=b""
        ).stdout.strip()
        self.assertIn(b"tree " + empty_tree, header)
        self.assertEqual(
            self.run_git(root, "rev-parse", identity.internal_ref).stdout,
            (seed + "\n").encode(),
        )

        after_create = self.repository_snapshot(root)
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            repeated = provision_session_resources(root, "Novel_01")
            inspected = inspect_session_resources(root, "Novel_01")
            reopened = require_ready_session_resources(
                identity.worktree_root, "Novel_01"
            )
            inventory = list_session_resources(root)
        self.assertEqual(repeated, created)
        self.assertEqual(inspected, created)
        self.assertEqual(reopened, created)
        self.assertEqual(inventory.baseline_commit_id, baseline)
        self.assertEqual(inventory.sessions, (created,))
        self.assertEqual(self.repository_snapshot(root), after_create)








class GitSessionResourceRestartTests(GitSessionResourceGitTestCase):
    """Prove durable evidence controls every interrupted provisioning replay."""





    def test_internal_ref_prefix_resumes_branch_and_worktree_in_order(self) -> None:
        root = self.initialize_workspace("internal-prefix")
        real_cas = _Repository._cas_ref

        def stop_before_branch(repository: _Repository, ref: str, target: str) -> None:
            if ref.startswith("refs/heads/"):
                raise OSError
            real_cas(repository, ref, target)

        with (
            mock.patch.dict(os.environ, self.session_environment(), clear=True),
            mock.patch.object(_Repository, "_cas_ref", new=stop_before_branch),
        ):
            interrupted = provision_session_resources(root, "novel")
        self.assertIs(interrupted.state, GitSessionResourceState.PROVISIONING)
        self.assertIsNotNone(interrupted.seed_commit_id)
        self.assertIsNone(interrupted.current_branch_tip)
        self.assertIsNotNone(interrupted.current_internal_tip)
        self.assertFalse(interrupted.identity.worktree_root.exists())

        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            recovered = provision_session_resources(root, "novel")
        self.assertIs(recovered.state, GitSessionResourceState.READY)
        self.assertEqual(recovered.seed_commit_id, interrupted.seed_commit_id)






class GitSessionResourceSafetyTests(GitSessionResourceGitTestCase):
    """Prove repository serialization and no-follow collision behavior."""



    def test_preexisting_file_and_symlink_siblings_are_preserved_before_mutation(
        self,
    ) -> None:
        for kind in ("file", "symlink"):
            with self.subTest(kind=kind):
                root = self.initialize_workspace(f"sibling-{kind}")
                identity = GitSessionIdentity(root, "novel")
                identity.worktree_root.parent.mkdir()
                outside = self.test_root / f"outside-{kind}"
                outside.write_bytes(b"private target bytes\n")
                if kind == "file":
                    identity.worktree_root.write_bytes(b"private sibling bytes\n")
                else:
                    identity.worktree_root.symlink_to(outside)
                before = self.repository_snapshot(root)
                with mock.patch.dict(
                    os.environ, self.session_environment(), clear=True
                ):
                    result = provision_session_resources(root, "novel")
                self.assertIs(result.state, GitSessionResourceState.RECOVERY_REQUIRED)
                self.assertEqual(self.repository_snapshot(root), before)
                self.assertEqual(outside.read_bytes(), b"private target bytes\n")
                if kind == "file":
                    self.assertEqual(
                        identity.worktree_root.read_bytes(),
                        b"private sibling bytes\n",
                    )
                else:
                    self.assertTrue(identity.worktree_root.is_symlink())













if __name__ == "__main__":
    unittest.main()
