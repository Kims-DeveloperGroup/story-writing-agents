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


class GitSessionResourceFailureTests(unittest.TestCase):
    """Prove malformed bounded Git facts remain closed and content-free."""

    def test_malformed_ref_row_has_closed_error_graph_and_no_private_bytes(
        self,
    ) -> None:
        private_row = b"private malformed ref row"
        with tempfile.TemporaryDirectory(prefix="git-session-row-") as temporary:
            root = Path(temporary).resolve()
            identity = (root.stat().st_dev, root.stat().st_ino)
            repository = _Repository(root, root / ".git", -1, identity)
            repository.runner = mock.Mock()
            repository.runner.run.return_value = GitProcessResult(private_row, 0)

            with self.assertRaises(GitSessionResourceError) as caught:
                repository._facts()  # noqa: SLF001

        error = caught.exception
        self.assertIs(error.code, GitSessionFailureCode.RECOVERY_REQUIRED)
        self.assertIs(error.operation, GitSessionOperationKind.INSPECT)
        self.assertIs(error.state, GitSessionResourceState.RECOVERY_REQUIRED)
        self.assertIs(
            error.recovery_action,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
        self.assertEqual(exception_graph(error), (error,))
        self.assertNotIn(private_row.decode(), str(error))
        self.assertNotIn(private_row.decode(), repr(error))

    def test_allowed_configuration_status_is_exact_unstaged_subset_only(self) -> None:
        system = "config/system_defaults.json"
        agents = "config/agent_configs.json"
        accepts = (
            (b"", (system, agents)),
            (f" M {system}\0".encode(), (system,)),
            (f" M {agents}\0".encode(), (agents,)),
            (f" M {system}\0 M {agents}\0".encode(), (system, agents)),
        )
        for content, allowed in accepts:
            with self.subTest(accepts=content):
                self.assertTrue(
                    resources_module._status_matches_allowed_worktree_changes(
                        content,
                        allowed,
                    )
                )

        rejects = (
            (f"M  {system}\0".encode(), (system,)),
            (f"MM {system}\0".encode(), (system,)),
            (f"R  {system}\0config/old.json\0".encode(), (system,)),
            (f"?? {system}.new\0".encode(), (system,)),
            (f" M {system}\0 M {system}\0".encode(), (system,)),
            (b" M \xff\0", (system,)),
            (b" M other.txt\0", (system,)),
            (f" M {system}\0".encode(), (agents,)),
            (f" M {system}\0".encode(), (system, "other.txt")),
        )
        for content, allowed in rejects:
            with self.subTest(rejects=content, allowed=allowed):
                self.assertFalse(
                    resources_module._status_matches_allowed_worktree_changes(
                        content,
                        allowed,
                    )
                )


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
            with mock.patch.dict(os.environ, self.session_environment(), clear=True):
                Workspace(root).initialize()
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


class GitSessionResourceCollisionTests(GitSessionResourceGitTestCase):
    """Prove pre-existing sibling identities block every session mutation."""

    def test_unregistered_sibling_collision_precedes_all_resource_mutation(
        self,
    ) -> None:
        root = self.test_root / "story"
        identity_environment = self.session_environment()
        with mock.patch.dict(os.environ, identity_environment, clear=True):
            Workspace(root).initialize()

        identity = GitSessionIdentity(root, "novel")
        identity.worktree_root.mkdir(parents=True)
        private = identity.worktree_root / "private.txt"
        private.write_bytes(b"preserve private collision bytes\n")

        before = self.repository_snapshot(root)
        with mock.patch.dict(os.environ, identity_environment, clear=True):
            result = provision_session_resources(root, "novel")

        self.assertIs(result.state, GitSessionResourceState.RECOVERY_REQUIRED)
        self.assertEqual(self.repository_snapshot(root), before)
        self.assertEqual(private.read_bytes(), b"preserve private collision bytes\n")
        journal = root / ".git" / GIT_SESSION_PROVISIONING_DIRECTORY / "novel.json"
        self.assertFalse(journal.exists())


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

    def test_sha256_seed_and_resource_ids_are_complete_and_retry_stable(self) -> None:
        root = self.initialize_workspace("sha256-story", object_format="sha256")
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            created = provision_session_resources(root, "novel")
        self.assertIs(created.state, GitSessionResourceState.READY)
        for object_id in (
            created.baseline_commit_id,
            created.seed_commit_id,
            created.current_branch_tip,
            created.current_internal_tip,
        ):
            self.assertIsNotNone(object_id)
            self.assertEqual(len(object_id or ""), 64)
        before = self.repository_snapshot(root)
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            repeated = provision_session_resources(root, "novel")
        self.assertEqual(repeated, created)
        self.assertEqual(self.repository_snapshot(root), before)

    def test_absent_inspect_and_require_ready_are_read_only(self) -> None:
        root = self.initialize_workspace("absent")
        before = self.repository_snapshot(root)
        inspected = inspect_session_resources(root, "novel")
        self.assertIs(inspected.state, GitSessionResourceState.ABSENT)
        self.assertIs(inspected.recovery_action, GitSessionRecoveryAction.CREATE)
        with self.assertRaises(GitSessionResourceError) as caught:
            require_ready_session_resources(root, "novel")
        self.assertIs(caught.exception.operation, GitSessionOperationKind.REQUIRE_READY)
        self.assertIs(caught.exception.state, GitSessionResourceState.ABSENT)
        self.assertEqual(self.repository_snapshot(root), before)
        self.assertFalse((root / ".git" / GIT_SESSION_PROVISIONING_DIRECTORY).exists())

    def test_packed_refs_preserve_ready_identity_and_preexisting_collision(
        self,
    ) -> None:
        ready_root = self.initialize_workspace("packed-ready")
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            created = provision_session_resources(ready_root, "novel")
        self.run_git(ready_root, "pack-refs", "--all", "--prune")
        packed_before = self.repository_snapshot(ready_root)
        self.assertEqual(inspect_session_resources(ready_root, "novel"), created)
        self.assertEqual(list_session_resources(ready_root).sessions, (created,))
        self.assertEqual(self.repository_snapshot(ready_root), packed_before)

        collision_root = self.initialize_workspace("packed-collision")
        self.run_git(
            collision_root,
            "branch",
            "story/session/collision",
            "HEAD",
        )
        self.run_git(collision_root, "pack-refs", "--all", "--prune")
        collision_before = self.repository_snapshot(collision_root)
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            result = provision_session_resources(collision_root, "collision")
        self.assertIs(result.state, GitSessionResourceState.RECOVERY_REQUIRED)
        self.assertEqual(self.repository_snapshot(collision_root), collision_before)

    def test_no_journal_partial_resource_is_preserved_and_never_completed(self) -> None:
        root = self.initialize_workspace("partial")
        self.run_git(root, "branch", "story/session/novel", "HEAD")
        before = self.repository_snapshot(root)
        inspected = inspect_session_resources(root, "novel")
        self.assertIs(inspected.state, GitSessionResourceState.RECOVERY_REQUIRED)
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            result = provision_session_resources(root, "novel")
        self.assertIs(result.state, GitSessionResourceState.RECOVERY_REQUIRED)
        self.assertEqual(self.repository_snapshot(root), before)
        self.assertFalse(
            (root / ".git" / GIT_SESSION_PROVISIONING_DIRECTORY / "novel.json").exists()
        )

    def test_exhaustive_inventory_rejects_unknown_ref_and_foreign_worktree(
        self,
    ) -> None:
        ref_root = self.initialize_workspace("unknown-ref")
        self.run_git(ref_root, "update-ref", "refs/tags/private", "HEAD")
        ref_before = self.repository_snapshot(ref_root)
        with self.assertRaises(GitSessionResourceError) as caught:
            list_session_resources(ref_root)
        self.assertIs(caught.exception.code, GitSessionFailureCode.RESOURCE_COLLISION)
        self.assertEqual(self.repository_snapshot(ref_root), ref_before)

        worktree_root = self.initialize_workspace("foreign-worktree")
        foreign = self.test_root / "foreign"
        self.run_git(
            worktree_root,
            "worktree",
            "add",
            "--detach",
            os.fspath(foreign),
            "HEAD",
        )
        worktree_before = self.repository_snapshot(worktree_root)
        with self.assertRaises(GitSessionResourceError) as caught:
            list_session_resources(worktree_root)
        self.assertIs(caught.exception.code, GitSessionFailureCode.RECOVERY_REQUIRED)
        self.assertIs(caught.exception.state, GitSessionResourceState.RECOVERY_REQUIRED)
        self.assertEqual(self.repository_snapshot(worktree_root), worktree_before)

    def test_inspection_reports_current_tips_but_keeps_immutable_roots(self) -> None:
        root = self.initialize_workspace("advanced")
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            created = provision_session_resources(root, "novel")
        identity = created.identity
        outline = identity.worktree_root / "core_documents/global_outline.md"
        outline.write_bytes(b"later canonical bytes\n")
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            self.run_git(
                identity.worktree_root,
                "add",
                "--",
                "core_documents/global_outline.md",
                environment=self.session_environment(),
            )
            self.run_git(
                identity.worktree_root,
                "commit",
                "--quiet",
                "--no-gpg-sign",
                "-m",
                "later canonical commit",
                environment=self.session_environment(),
            )
        branch_tip = (
            self.run_git(identity.worktree_root, "rev-parse", "HEAD")
            .stdout.decode()
            .strip()
        )

        seed = created.seed_commit_id
        assert seed is not None
        empty_tree = self.run_git(
            root, "show", "-s", "--format=%T", seed
        ).stdout.strip()
        internal_tip = (
            self.run_git(
                root,
                "commit-tree",
                empty_tree.decode(),
                "-p",
                seed,
                "-m",
                "later internal checkpoint",
                environment=self.session_environment(),
            )
            .stdout.decode()
            .strip()
        )
        self.run_git(root, "update-ref", identity.internal_ref, internal_tip, seed)

        inspected = inspect_session_resources(root, "novel")
        self.assertIs(inspected.state, GitSessionResourceState.READY)
        self.assertEqual(inspected.initial_branch_tip, created.baseline_commit_id)
        self.assertEqual(inspected.current_branch_tip, branch_tip)
        self.assertEqual(inspected.seed_commit_id, seed)
        self.assertEqual(inspected.initial_internal_tip, seed)
        self.assertEqual(inspected.current_internal_tip, internal_tip)
        self.assertEqual(
            self.run_git(root, "status", "--porcelain=v2", "--").stdout, b""
        )


class GitSessionResourceRestartTests(GitSessionResourceGitTestCase):
    """Prove durable evidence controls every interrupted provisioning replay."""

    def test_journal_unlink_failure_is_not_ready_and_fresh_create_finishes(
        self,
    ) -> None:
        root = self.initialize_workspace("remove-journal")
        real_unlink = os.unlink

        def fail_journal_unlink(
            path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
            *args: object,
            **kwargs: object,
        ) -> None:
            if path == "novel.json":
                raise OSError
            real_unlink(path, *args, **kwargs)  # type: ignore[arg-type]

        with (
            mock.patch.dict(os.environ, self.session_environment(), clear=True),
            mock.patch.object(
                resources_module.os,
                "unlink",
                side_effect=fail_journal_unlink,
            ),
        ):
            interrupted = provision_session_resources(root, "novel")

        identity = interrupted.identity
        journal = root / ".git" / GIT_SESSION_PROVISIONING_DIRECTORY / "novel.json"
        self.assertIs(interrupted.state, GitSessionResourceState.PROVISIONING)
        self.assertIs(
            interrupted.recovery_action,
            GitSessionRecoveryAction.RETRY_CREATE,
        )
        self.assertTrue(journal.is_file())
        self.assertTrue(identity.worktree_root.is_dir())
        before_retry = self.repository_snapshot(root)

        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            recovered = provision_session_resources(root, "novel")
        self.assertIs(recovered.state, GitSessionResourceState.READY)
        self.assertFalse(journal.exists())
        self.assertEqual(recovered.seed_commit_id, interrupted.seed_commit_id)
        self.assertEqual(
            self.repository_snapshot(root)[-1],
            before_retry[-1],
        )

    def test_journal_directory_fsync_failure_retries_same_seed_and_finishes(
        self,
    ) -> None:
        root = self.initialize_workspace("fsync-remove-journal")
        real_unlink = os.unlink
        real_fsync = os.fsync
        journal_unlinked = False
        failed_directory_fsync = False

        def observe_journal_unlink(
            path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
            *args: object,
            **kwargs: object,
        ) -> None:
            nonlocal journal_unlinked
            real_unlink(path, *args, **kwargs)  # type: ignore[arg-type]
            if path == "novel.json":
                journal_unlinked = True

        def fail_post_unlink_directory_fsync(descriptor: int) -> None:
            nonlocal failed_directory_fsync
            if (
                journal_unlinked
                and not failed_directory_fsync
                and stat.S_ISDIR(os.fstat(descriptor).st_mode)
            ):
                failed_directory_fsync = True
                raise OSError
            real_fsync(descriptor)

        with (
            mock.patch.dict(os.environ, self.session_environment(), clear=True),
            mock.patch.object(
                resources_module.os,
                "unlink",
                side_effect=observe_journal_unlink,
            ),
            mock.patch.object(
                resources_module.os,
                "fsync",
                side_effect=fail_post_unlink_directory_fsync,
            ),
        ):
            interrupted = provision_session_resources(root, "novel")

        identity = interrupted.identity
        journal = root / ".git" / GIT_SESSION_PROVISIONING_DIRECTORY / "novel.json"
        self.assertTrue(journal_unlinked)
        self.assertTrue(failed_directory_fsync)
        self.assertIs(interrupted.state, GitSessionResourceState.PROVISIONING)
        self.assertIs(
            interrupted.recovery_action,
            GitSessionRecoveryAction.RETRY_CREATE,
        )
        self.assertFalse(journal.exists())
        self.assertTrue(identity.worktree_root.is_dir())
        before_retry = self.repository_snapshot(root)

        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            recovered = provision_session_resources(root, "novel")
        self.assertIs(recovered.state, GitSessionResourceState.READY)
        self.assertFalse(journal.exists())
        self.assertEqual(recovered.seed_commit_id, interrupted.seed_commit_id)
        self.assertEqual(self.repository_snapshot(root)[-1], before_retry[-1])

    def test_journal_only_prefix_resumes_without_early_git_mutation(self) -> None:
        root = self.initialize_workspace("journal-prefix")
        before = self.repository_snapshot(root)
        with (
            mock.patch.dict(os.environ, self.session_environment(), clear=True),
            mock.patch.object(_Repository, "_create_seed", side_effect=OSError),
        ):
            interrupted = provision_session_resources(root, "novel")
        self.assertIs(interrupted.state, GitSessionResourceState.PROVISIONING)
        self.assertEqual(self.repository_snapshot(root), before)
        journal = root / ".git" / GIT_SESSION_PROVISIONING_DIRECTORY / "novel.json"
        self.assertTrue(journal.is_file())

        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            recovered = provision_session_resources(root, "novel")
        self.assertIs(recovered.state, GitSessionResourceState.READY)
        self.assertFalse(journal.exists())

    def test_unreachable_seed_prefix_reuses_object_before_internal_ref(self) -> None:
        root = self.initialize_workspace("seed-prefix")
        with (
            mock.patch.dict(os.environ, self.session_environment(), clear=True),
            mock.patch.object(_Repository, "_cas_ref", side_effect=OSError),
        ):
            interrupted = provision_session_resources(root, "novel")
        self.assertIs(interrupted.state, GitSessionResourceState.PROVISIONING)
        refs = self.run_git(root, "for-each-ref", "--format=%(refname)").stdout
        self.assertEqual(refs, b"refs/heads/main\n")
        objects_after_seed = self.run_git(root, "count-objects", "-v").stdout

        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            recovered = provision_session_resources(root, "novel")
        self.assertIs(recovered.state, GitSessionResourceState.READY)
        self.assertEqual(
            self.run_git(root, "count-objects", "-v").stdout,
            objects_after_seed,
        )

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

    def test_branch_prefix_resumes_worktree_without_destructive_cleanup(self) -> None:
        root = self.initialize_workspace("branch-prefix")
        real_required = _Repository._required
        commands: list[tuple[str, ...]] = []

        def stop_before_worktree(
            repository: _Repository,
            arguments: tuple[str, ...],
            **kwargs: object,
        ) -> GitProcessResult:
            commands.append(arguments)
            if arguments[:2] == ("worktree", "add"):
                raise OSError
            return real_required(repository, arguments, **kwargs)  # type: ignore[arg-type]

        with (
            mock.patch.dict(os.environ, self.session_environment(), clear=True),
            mock.patch.object(_Repository, "_required", new=stop_before_worktree),
        ):
            interrupted = provision_session_resources(root, "novel")
        self.assertIs(interrupted.state, GitSessionResourceState.PROVISIONING)
        self.assertIsNotNone(interrupted.current_branch_tip)
        self.assertIsNotNone(interrupted.current_internal_tip)
        self.assertFalse(interrupted.identity.worktree_root.exists())
        forbidden = {
            ("worktree", "remove"),
            ("worktree", "prune"),
            ("reset",),
            ("clean",),
            ("gc",),
            ("prune",),
            ("rebase",),
            ("merge",),
        }
        self.assertFalse(
            any(
                command[: len(prefix)] == prefix
                for command in commands
                for prefix in forbidden
            )
        )

        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            recovered = provision_session_resources(root, "novel")
        self.assertIs(recovered.state, GitSessionResourceState.READY)
        self.assertEqual(recovered.seed_commit_id, interrupted.seed_commit_id)

    def test_unprovable_post_mutation_inspection_returns_outcome_unknown(self) -> None:
        root = self.initialize_workspace("unknown")
        real_facts = _Repository._facts
        mutation_attempted = False

        def fail_mutation(repository: _Repository, ref: str, target: str) -> None:
            del repository, ref, target
            nonlocal mutation_attempted
            mutation_attempted = True
            raise OSError

        def fail_recovery_inspection(repository: _Repository) -> object:
            if mutation_attempted:
                raise OSError
            return real_facts(repository)

        with (
            mock.patch.dict(os.environ, self.session_environment(), clear=True),
            mock.patch.object(_Repository, "_cas_ref", new=fail_mutation),
            mock.patch.object(_Repository, "_facts", new=fail_recovery_inspection),
        ):
            interrupted = provision_session_resources(root, "novel")
        self.assertIs(interrupted.state, GitSessionResourceState.OUTCOME_UNKNOWN)
        self.assertIs(
            interrupted.recovery_action,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
        fresh = inspect_session_resources(root, "novel")
        self.assertIs(fresh.state, GitSessionResourceState.PROVISIONING)

    def test_completed_worktree_mutation_with_failed_return_resumes_in_place(
        self,
    ) -> None:
        root = self.initialize_workspace("worktree-outcome")
        real_required = _Repository._required

        def fail_after_worktree(
            repository: _Repository,
            arguments: tuple[str, ...],
            **kwargs: object,
        ) -> GitProcessResult:
            result = real_required(repository, arguments, **kwargs)  # type: ignore[arg-type]
            if arguments[:2] == ("worktree", "add"):
                raise OSError
            return result

        with (
            mock.patch.dict(os.environ, self.session_environment(), clear=True),
            mock.patch.object(_Repository, "_required", new=fail_after_worktree),
        ):
            interrupted = provision_session_resources(root, "novel")
        self.assertIs(interrupted.state, GitSessionResourceState.PROVISIONING)
        self.assertTrue(interrupted.identity.worktree_root.is_dir())
        before_retry = self.repository_snapshot(root)

        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            recovered = provision_session_resources(root, "novel")
        self.assertIs(recovered.state, GitSessionResourceState.READY)
        self.assertEqual(recovered.seed_commit_id, interrupted.seed_commit_id)
        self.assertEqual(self.repository_snapshot(root)[-1], before_retry[-1])

    def test_provisioning_inspect_and_require_ready_never_continue_mutation(
        self,
    ) -> None:
        root = self.initialize_workspace("read-only-provisioning")
        with (
            mock.patch.dict(os.environ, self.session_environment(), clear=True),
            mock.patch.object(_Repository, "_create_seed", side_effect=OSError),
        ):
            interrupted = provision_session_resources(root, "novel")
        self.assertIs(interrupted.state, GitSessionResourceState.PROVISIONING)
        journal = root / ".git" / GIT_SESSION_PROVISIONING_DIRECTORY / "novel.json"
        journal_before = (journal.read_bytes(), journal.stat().st_mtime_ns)
        repository_before = self.repository_snapshot(root)

        inspected = inspect_session_resources(root, "novel")
        self.assertIs(inspected.state, GitSessionResourceState.PROVISIONING)
        with self.assertRaises(GitSessionResourceError) as caught:
            require_ready_session_resources(root, "novel")
        self.assertIs(caught.exception.operation, GitSessionOperationKind.REQUIRE_READY)
        self.assertIs(caught.exception.state, GitSessionResourceState.PROVISIONING)
        self.assertEqual(self.repository_snapshot(root), repository_before)
        self.assertEqual(
            (journal.read_bytes(), journal.stat().st_mtime_ns),
            journal_before,
        )


class GitSessionResourceSafetyTests(GitSessionResourceGitTestCase):
    """Prove repository serialization and no-follow collision behavior."""

    def test_same_and_different_session_concurrent_creates_converge(self) -> None:
        same_root = self.initialize_workspace("same-concurrent")

        def provision(root: Path, session_id: str) -> object:
            return provision_session_resources(root, session_id)

        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            with ThreadPoolExecutor(max_workers=2) as executor:
                same = tuple(
                    executor.map(
                        lambda _index: provision(same_root, "novel"),
                        range(2),
                    )
                )
        self.assertEqual(same[0], same[1])
        self.assertIs(same[0].state, GitSessionResourceState.READY)  # type: ignore[attr-defined]
        self.assertEqual(len(list_session_resources(same_root).sessions), 1)

        different_root = self.initialize_workspace("different-concurrent")
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            with ThreadPoolExecutor(max_workers=2) as executor:
                different = tuple(
                    executor.map(
                        lambda session: provision(different_root, session),
                        ("alpha", "beta"),
                    )
                )
        self.assertTrue(
            all(value.state is GitSessionResourceState.READY for value in different)  # type: ignore[attr-defined]
        )
        self.assertEqual(
            tuple(
                value.identity.session_id
                for value in list_session_resources(different_root).sessions
            ),
            ("alpha", "beta"),
        )

    def test_case_distinct_ids_follow_filesystem_alias_truth_without_folding(
        self,
    ) -> None:
        root = self.initialize_workspace("case-alias")
        probe = self.test_root / "case-probe"
        probe.mkdir()
        upper = probe / "Novel"
        upper.mkdir()
        aliases = (probe / "novel").exists()

        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            first = provision_session_resources(root, "Novel")
            second = provision_session_resources(root, "novel")
        self.assertIs(first.state, GitSessionResourceState.READY)
        if aliases:
            self.assertIs(second.state, GitSessionResourceState.RECOVERY_REQUIRED)
            self.assertEqual(len(list_session_resources(root).sessions), 1)
        else:
            self.assertIs(second.state, GitSessionResourceState.READY)
            self.assertEqual(
                tuple(
                    item.identity.session_id
                    for item in list_session_resources(root).sessions
                ),
                ("Novel", "novel"),
            )

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

    def test_symlinked_journal_and_lock_are_content_free_and_nonmutating(self) -> None:
        for name in ("novel.json", ".lock"):
            with self.subTest(name=name):
                root = self.initialize_workspace(f"admin-{name.replace('.', 'x')}")
                directory = root / ".git" / GIT_SESSION_PROVISIONING_DIRECTORY
                directory.mkdir(parents=True)
                outside = self.test_root / f"outside-{name.replace('.', 'x')}"
                outside.write_bytes(b"private administrative target\n")
                (directory / name).symlink_to(outside)
                before = self.repository_snapshot(root)
                with mock.patch.dict(
                    os.environ, self.session_environment(), clear=True
                ):
                    with self.assertRaises(GitSessionResourceError) as caught:
                        provision_session_resources(root, "novel")
                self.assertIs(
                    caught.exception.state, GitSessionResourceState.RECOVERY_REQUIRED
                )
                self.assertEqual(exception_graph(caught.exception), (caught.exception,))
                self.assertNotIn(
                    "private administrative target", repr(caught.exception)
                )
                self.assertEqual(self.repository_snapshot(root), before)
                self.assertEqual(
                    outside.read_bytes(), b"private administrative target\n"
                )
                self.assertTrue((directory / name).is_symlink())

    def test_journal_and_lock_are_private_and_file_directory_syncs_are_observed(
        self,
    ) -> None:
        root = self.initialize_workspace("durability")
        sync_modes: list[int] = []
        real_fsync = os.fsync

        def record_fsync(descriptor: int) -> None:
            sync_modes.append(os.fstat(descriptor).st_mode)
            real_fsync(descriptor)

        with (
            mock.patch.dict(os.environ, self.session_environment(), clear=True),
            mock.patch.object(os, "fsync", side_effect=record_fsync),
            mock.patch.object(_Repository, "_create_seed", side_effect=OSError),
        ):
            interrupted = provision_session_resources(root, "novel")
        self.assertIs(interrupted.state, GitSessionResourceState.PROVISIONING)
        directory = root / ".git" / GIT_SESSION_PROVISIONING_DIRECTORY
        journal = directory / "novel.json"
        lock = directory / ".lock"
        self.assertEqual(journal.stat().st_mode & 0o777, 0o600)
        self.assertEqual(lock.stat().st_mode & 0o777, 0o600)
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertTrue(any(stat.S_ISREG(mode) for mode in sync_modes))
        self.assertTrue(any(stat.S_ISDIR(mode) for mode in sync_modes))

    def test_success_uses_only_exact_fixed_mutation_argv(self) -> None:
        root = self.initialize_workspace("argv")
        calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
        real_run = GitProcessRunner.run

        def record_run(
            runner: GitProcessRunner,
            arguments: tuple[str, ...],
            **kwargs: object,
        ) -> GitProcessResult:
            calls.append((arguments, dict(kwargs)))
            return real_run(runner, arguments, **kwargs)  # type: ignore[arg-type]

        with (
            mock.patch.dict(os.environ, self.session_environment(), clear=True),
            mock.patch.object(GitProcessRunner, "run", new=record_run),
        ):
            created = provision_session_resources(root, "novel")
        baseline = created.baseline_commit_id
        seed = created.seed_commit_id
        assert baseline is not None and seed is not None
        mutations = [
            arguments
            for arguments, options in calls
            if options.get("read_only") is not True
        ]
        self.assertEqual(
            mutations,
            [
                ("hash-object", "-w", "-t", "tree", "--stdin"),
                ("hash-object", "-w", "-t", "commit", "--stdin"),
                ("update-ref", created.identity.internal_ref, seed, "0" * len(seed)),
                (
                    "update-ref",
                    f"refs/heads/{created.identity.branch}",
                    baseline,
                    "0" * len(baseline),
                ),
                (
                    "worktree",
                    "add",
                    "--no-guess-remote",
                    os.fspath(created.identity.worktree_root),
                    created.identity.branch,
                ),
            ],
        )
        self.assertTrue(all(type(arguments) is tuple for arguments, _options in calls))
        readiness_status = (
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=no",
        )
        self.assertTrue(
            any(
                arguments == readiness_status and options.get("read_only") is True
                for arguments, options in calls
            )
        )
        staged_session_status = (
            "diff-index",
            "--cached",
            "--quiet",
            "HEAD",
            "--",
        )
        self.assertTrue(
            any(
                arguments == staged_session_status
                and options.get("read_only") is True
                for arguments, options in calls
            )
        )
        self.assertFalse(
            any(
                arguments[:3] == ("status", "--porcelain=v1", "-z")
                and "--untracked-files=all" in arguments
                for arguments, _options in calls
            )
        )
        self.assertFalse(
            any(
                arguments[:1]
                in {
                    ("reset",),
                    ("clean",),
                    ("gc",),
                    ("prune",),
                    ("merge",),
                    ("rebase",),
                }
                or arguments[:2] in {("worktree", "remove"), ("worktree", "prune")}
                for arguments, _options in calls
            )
        )

    def test_malformed_linked_worktree_indirection_is_preserved_and_rejected(
        self,
    ) -> None:
        root = self.initialize_workspace("malformed-indirection")
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            ready = provision_session_resources(root, "novel")
        administrative = ready.identity.worktree_root / ".git"
        administrative.write_bytes(b"private malformed administrative bytes\n")
        before = self.repository_snapshot(root)
        inspected = inspect_session_resources(root, "novel")
        self.assertIs(inspected.state, GitSessionResourceState.RECOVERY_REQUIRED)
        self.assertEqual(self.repository_snapshot(root), before)
        self.assertEqual(
            administrative.read_bytes(),
            b"private malformed administrative bytes\n",
        )

    def test_symlinked_linked_worktree_git_file_is_not_followed(self) -> None:
        root = self.initialize_workspace("symlinked-git-file")
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            ready = provision_session_resources(root, "novel")
        self.assertIs(ready.state, GitSessionResourceState.READY)
        administrative = ready.identity.worktree_root / ".git"
        legitimate_payload = administrative.read_bytes()
        external = self.test_root / "external-git-file"
        external.write_bytes(legitimate_payload)
        administrative.unlink()
        administrative.symlink_to(external)
        before = self.repository_snapshot(root)

        inspected = inspect_session_resources(root, "novel")

        self.assertIs(inspected.state, GitSessionResourceState.RECOVERY_REQUIRED)
        self.assertIs(
            inspected.recovery_action,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
        with self.assertRaises(GitSessionResourceError) as caught:
            require_ready_session_resources(root, "novel")
        self.assertIs(caught.exception.code, GitSessionFailureCode.RECOVERY_REQUIRED)
        self.assertIs(
            caught.exception.operation,
            GitSessionOperationKind.REQUIRE_READY,
        )
        self.assertEqual(exception_graph(caught.exception), (caught.exception,))
        self.assertEqual(self.repository_snapshot(root), before)
        self.assertTrue(administrative.is_symlink())
        self.assertEqual(external.read_bytes(), legitimate_payload)

    def test_symlinked_linked_worktree_commondir_is_not_followed(self) -> None:
        root = self.initialize_workspace("symlinked-commondir")
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            ready = provision_session_resources(root, "novel")
        self.assertIs(ready.state, GitSessionResourceState.READY)
        administrative_payload = (ready.identity.worktree_root / ".git").read_text()
        administrative_root = Path(
            administrative_payload.removeprefix("gitdir: ").strip()
        )
        commondir = administrative_root / "commondir"
        legitimate_payload = commondir.read_bytes()
        external = self.test_root / "external-commondir"
        external.write_bytes(legitimate_payload)
        commondir.unlink()
        commondir.symlink_to(external)
        before = self.repository_snapshot(root)

        inspected = inspect_session_resources(root, "novel")

        self.assertIs(inspected.state, GitSessionResourceState.RECOVERY_REQUIRED)
        self.assertIs(
            inspected.recovery_action,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
        with self.assertRaises(GitSessionResourceError) as caught:
            require_ready_session_resources(root, "novel")
        self.assertIs(caught.exception.code, GitSessionFailureCode.RECOVERY_REQUIRED)
        self.assertIs(
            caught.exception.operation,
            GitSessionOperationKind.REQUIRE_READY,
        )
        self.assertEqual(exception_graph(caught.exception), (caught.exception,))
        self.assertEqual(self.repository_snapshot(root), before)
        self.assertTrue(commondir.is_symlink())
        self.assertEqual(external.read_bytes(), legitimate_payload)

    def test_oversized_linked_worktree_git_file_is_bounded_and_preserved(
        self,
    ) -> None:
        root = self.initialize_workspace("oversized-git-file")
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            ready = provision_session_resources(root, "novel")
        self.assertIs(ready.state, GitSessionResourceState.READY)
        administrative = ready.identity.worktree_root / ".git"
        oversized = b"private administrative bytes" * 2_521
        self.assertGreater(len(oversized), 64 * 1024)
        administrative.write_bytes(oversized)
        before = self.repository_snapshot(root)

        inspected = inspect_session_resources(root, "novel")

        self.assertIs(inspected.state, GitSessionResourceState.RECOVERY_REQUIRED)
        self.assertIs(
            inspected.recovery_action,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
        with self.assertRaises(GitSessionResourceError) as caught:
            require_ready_session_resources(root, "novel")
        self.assertIs(caught.exception.code, GitSessionFailureCode.RECOVERY_REQUIRED)
        self.assertIs(
            caught.exception.operation,
            GitSessionOperationKind.REQUIRE_READY,
        )
        self.assertEqual(exception_graph(caught.exception), (caught.exception,))
        self.assertNotIn("private administrative bytes", repr(caught.exception))
        self.assertEqual(self.repository_snapshot(root), before)
        self.assertEqual(administrative.read_bytes(), oversized)

    def test_oversized_linked_worktree_commondir_is_bounded_and_preserved(
        self,
    ) -> None:
        root = self.initialize_workspace("oversized-commondir")
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            ready = provision_session_resources(root, "novel")
        self.assertIs(ready.state, GitSessionResourceState.READY)
        administrative_payload = (ready.identity.worktree_root / ".git").read_text()
        administrative_root = Path(
            administrative_payload.removeprefix("gitdir: ").strip()
        )
        commondir = administrative_root / "commondir"
        oversized = b"private administrative bytes" * 2_521
        self.assertGreater(len(oversized), 64 * 1024)
        before = (
            self.run_git(
                root,
                "for-each-ref",
                "--format=%(refname)%00%(objectname)",
            ).stdout,
            self.run_git(root, "symbolic-ref", "HEAD").stdout,
            self.run_git(root, "rev-parse", "HEAD^{tree}").stdout,
            self.run_git(root, "status", "--porcelain=v2", "--").stdout,
            (root / ".git" / "index").read_bytes(),
            self.run_git(root, "count-objects", "-v").stdout,
        )
        commondir.write_bytes(oversized)

        with self.assertRaises(GitSessionResourceError) as caught:
            inspect_session_resources(root, "novel")

        error = caught.exception
        self.assertIs(error.code, GitSessionFailureCode.RECOVERY_REQUIRED)
        self.assertIs(error.operation, GitSessionOperationKind.INSPECT)
        self.assertIs(error.state, GitSessionResourceState.RECOVERY_REQUIRED)
        self.assertIs(
            error.recovery_action,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
        self.assertEqual(exception_graph(error), (error,))
        self.assertNotIn("private administrative bytes", repr(error))
        after = (
            self.run_git(
                root,
                "for-each-ref",
                "--format=%(refname)%00%(objectname)",
            ).stdout,
            self.run_git(root, "symbolic-ref", "HEAD").stdout,
            self.run_git(root, "rev-parse", "HEAD^{tree}").stdout,
            self.run_git(root, "status", "--porcelain=v2", "--").stdout,
            (root / ".git" / "index").read_bytes(),
            self.run_git(root, "count-objects", "-v").stdout,
        )
        self.assertEqual(after, before)
        self.assertEqual(commondir.read_bytes(), oversized)

    def test_read_only_process_nonzero_is_content_free_recovery_without_mutation(
        self,
    ) -> None:
        root = self.initialize_workspace("read-only-nonzero")
        before = self.repository_snapshot(root)
        private = b"private process output"
        calls: list[dict[str, object]] = []

        def fail_read(
            _runner: GitProcessRunner,
            _arguments: tuple[str, ...],
            **kwargs: object,
        ) -> GitProcessResult:
            calls.append(dict(kwargs))
            return GitProcessResult(private, 7)

        with (
            mock.patch.object(GitProcessRunner, "run", new=fail_read),
            self.assertRaises(GitSessionResourceError) as caught,
        ):
            inspect_session_resources(root, "novel")

        error = caught.exception
        self.assertIs(error.code, GitSessionFailureCode.RECOVERY_REQUIRED)
        self.assertIs(error.operation, GitSessionOperationKind.INSPECT)
        self.assertIs(error.state, GitSessionResourceState.RECOVERY_REQUIRED)
        self.assertIs(
            error.recovery_action,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
        self.assertEqual(exception_graph(error), (error,))
        self.assertNotIn(private.decode(), str(error))
        self.assertNotIn(private.decode(), repr(error))
        self.assertEqual(
            calls,
            [
                {
                    "expected_identity": mock.ANY,
                    "stdin": b"",
                    "read_only": True,
                    "identity_environment": None,
                }
            ],
        )
        self.assertEqual(self.repository_snapshot(root), before)
        self.assertFalse((root / ".git" / GIT_SESSION_PROVISIONING_DIRECTORY).exists())

    def test_missing_lock_capability_rejects_before_any_resource_mutation(self) -> None:
        root = self.initialize_workspace("no-lock")
        before = self.repository_snapshot(root)
        with (
            mock.patch.object(resources_module, "_fcntl", None),
            mock.patch.dict(os.environ, self.session_environment(), clear=True),
            self.assertRaises(GitSessionResourceError) as caught,
        ):
            provision_session_resources(root, "novel")
        self.assertIs(caught.exception.code, GitSessionFailureCode.LOCK_UNAVAILABLE)
        self.assertEqual(exception_graph(caught.exception), (caught.exception,))
        self.assertEqual(self.repository_snapshot(root), before)
        self.assertFalse((root / ".git" / GIT_SESSION_PROVISIONING_DIRECTORY).exists())

    def test_malformed_journal_bytes_are_preserved_and_content_free(self) -> None:
        root = self.initialize_workspace("malformed-journal")
        directory = root / ".git" / GIT_SESSION_PROVISIONING_DIRECTORY
        directory.mkdir(parents=True)
        journal = directory / "novel.json"
        private = b'{"private":"caller journal bytes"}\n'
        journal.write_bytes(private)
        before = self.repository_snapshot(root)
        with self.assertRaises(GitSessionResourceError) as caught:
            inspect_session_resources(root, "novel")
        self.assertIs(caught.exception.code, GitSessionFailureCode.JOURNAL_INVALID)
        self.assertEqual(exception_graph(caught.exception), (caught.exception,))
        self.assertNotIn("caller journal bytes", repr(caught.exception))
        self.assertEqual(self.repository_snapshot(root), before)
        self.assertEqual(journal.read_bytes(), private)


if __name__ == "__main__":
    unittest.main()
