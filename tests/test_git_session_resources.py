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

from story_writing_agents import PLOT_ARCHITECT, Workspace
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
    SessionArtifactBindingError,
    compose_session_artifact_binding,
    inspect_session_resources,
    list_session_resources,
    provision_session_resources,
    require_ready_session_resources,
)
from story_writing_agents.workspace.session_role_projection import (
    prepare_session_role_projection,
)
from story_writing_agents.workspace import session_role_projection as projection_module
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
        row = b"refs/heads/main\0" + baseline.encode() + b"\0*\n"
        repository = object.__new__(_Repository)
        query = ("for-each-ref", "--format=%(refname)%00%(objectname)%00%(HEAD)")
        with mock.patch.object(_Repository, "_required", return_value=GitProcessResult(row, 0)) as required:
            self.assertEqual(repository._read_refs().rows, (("refs/heads/main", baseline, True),))
        required.assert_called_once_with(query, read_only=True)
        invalid = (
            row[:-1], row + row, row + row.replace(b"main", b"other"),
            row.replace(b"\0*", b""), row.replace(b"\0*", b"\0?"), row.replace(baseline.encode(), b"bad"),
        )
        for output in invalid:
            with mock.patch.object(
                _Repository, "_required", return_value=GitProcessResult(output, 0)
            ):
                with self.assertRaises(GitSessionResourceError) as caught:
                    repository._read_refs()
            self.assertIs(caught.exception.code, GitSessionFailureCode.RECOVERY_REQUIRED)
        root_before = self.repository_snapshot(root)

        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            created = provision_session_resources(root, "Novel_01")
        self.assertIs(created.state, GitSessionResourceState.READY)
        self.assertEqual(created.baseline_commit_id, baseline)
        self.assertEqual(created.initial_branch_tip, baseline)
        identity = created.identity
        self.assertTrue(identity.worktree_root.is_dir())
        self.assertEqual(self.run_git(root, "rev-parse", f"refs/heads/{identity.branch}").stdout,
                         (baseline + "\n").encode())
        self.assertEqual(self.run_git(identity.worktree_root, "symbolic-ref", "HEAD").stdout,
                         f"refs/heads/{identity.branch}\n".encode())
        self.assertEqual(self.run_git(root, "status", "--porcelain=v2", "--").stdout, b"")
        self.assertEqual(self.run_git(root, "symbolic-ref", "HEAD").stdout, root_before[2])
        self.assertEqual(self.run_git(root, "rev-parse", "HEAD^{tree}").stdout, root_before[3])
        self.assertEqual((root / ".git" / "index").read_bytes(), root_before[5])

        seed = created.seed_commit_id
        assert seed is not None
        payload = self.run_git(root, "cat-file", "commit", seed).stdout
        header, message = payload.split(b"\n\n", 1)
        self.assertIn(b"author Session Author <session-author@example.invalid>", header)
        self.assertIn(b"committer Session Committer <session-committer@example.invalid>", header)
        self.assertNotIn(b"parent ", header)
        self.assertEqual(message, f"{GIT_SESSION_SEED_MESSAGE_PREFIX}Novel_01\n".encode())
        empty_tree = self.run_git(
            root, "hash-object", "-t", "tree", "--stdin", input_bytes=b""
        ).stdout.strip()
        self.assertIn(b"tree " + empty_tree, header)
        self.assertEqual(self.run_git(root, "rev-parse", identity.internal_ref).stdout,
                         (seed + "\n").encode())

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
        blobs = (b"", b"binary\n\x00\xff")
        ids = (b"a" * 40, b"b" * 40)
        tree = b"".join(b"100644 blob " + oid + b" " + str(len(blob)).rjust(7).encode() +
                        b"\tfixture/" + str(index).encode() + b"\0"
                        for index, (oid, blob) in enumerate(zip(ids, blobs)))
        frames = b"".join(oid + b" blob " + str(len(blob)).encode() + b"\n" + blob + b"\n"
                          for oid, blob in zip(ids, blobs))
        repository = object.__new__(_Repository)
        with mock.patch.object(_Repository, "_required", side_effect=(
            GitProcessResult(tree, 0), GitProcessResult(frames, 0),
        )) as required:
            self.assertEqual(repository._tree_digests("0" * 40), {
                f"fixture/{index}": hashlib.sha256(blob).hexdigest()
                for index, blob in enumerate(blobs)
            })
            self.assertEqual(required.call_args_list, [
                mock.call(("ls-tree", "-r", "-l", "-z", "0" * 40), read_only=True),
                mock.call(("cat-file", "--batch"), stdin=b"".join(oid + b"\n" for oid in ids), read_only=True),
            ])
        with mock.patch.object(_Repository, "_required", return_value=GitProcessResult(b"", 0)) as required:
            self.assertEqual(repository._tree_digests("0" * 40), {})
            required.assert_called_once_with(("ls-tree", "-r", "-l", "-z", "0" * 40), read_only=True)
        (self.test_root / "sha256-probe").mkdir()
        if self.run_git(self.test_root / "sha256-probe", "init", "--quiet", "--initial-branch=main", "--template=", "--object-format=sha256", check=False).returncode == 0:
            sha_root = self.initialize_workspace("SHA-256 Story", object_format="sha256")
            with mock.patch.dict(os.environ, self.session_environment(), clear=True):
                self.assertIs(provision_session_resources(sha_root, "Novel_02").state, GitSessionResourceState.READY)
                self.assertIs(require_ready_session_resources(sha_root, "Novel_02").state, GitSessionResourceState.READY)








        role = prepare_session_role_projection(
            PLOT_ARCHITECT, b'---\nname: "plot_architect"\nexact role bytes\n'
        )
        role_root = self.initialize_workspace("role-unrelated-tracked")
        role_ready = provision_session_resources(role_root, "novel")
        role_session = role_ready.identity.worktree_root
        self.configure_identity(role_root)
        unrelated = (
            ".agents-backup/private.bin",
            "agents/private.bin",
            "nested/.agents/private.bin",
        )
        self.commit_fixture_files(
            role_session,
            {path: b"unrelated tracked bytes\n" for path in unrelated},
            "Track unrelated names",
        )
        role_binding = compose_session_artifact_binding(role_root, "novel")
        with role_binding.open_ready_resources() as access:
            access.install_provider_role(role)
        role_path = role_session / ".agents/agents" / role.native_agent_id / "agent.md"
        self.assertEqual(role_path.read_bytes(), role.content)
        self.assertEqual(self.run_git(role_session, "check-ignore", "-q", "--", str(role_path), check=False).returncode, 0)
        for path in unrelated:
            self.assertEqual((role_session / path).read_bytes(), b"unrelated tracked bytes\n")

        for source, initial in (
            ("exclude", b"prefix\n/.agents/\n!/.agents/\n"),
            ("gitignore", b"!/.agents/\n"),
        ):
            excluded_root = self.initialize_workspace(f"role-{source}-negation")
            excluded = provision_session_resources(excluded_root, "novel")
            session = excluded.identity.worktree_root
            if source == "exclude":
                exclusion = excluded_root / ".git/info/exclude"
                exclusion.parent.mkdir(parents=True, exist_ok=True)
                exclusion.write_bytes(initial)
            else:
                self.configure_identity(excluded_root)
                self.commit_fixture_files(session, {".gitignore": initial}, "Unignore roles")
            binding = compose_session_artifact_binding(excluded_root, "novel")
            if source == "exclude":
                with binding.open_ready_resources() as access:
                    access.install_provider_role(role)
                self.assertEqual(exclusion.read_bytes(), initial + b"/.agents/\n")
            else:
                with binding.open_ready_resources() as access, self.assertRaises(
                    SessionArtifactBindingError
                ) as caught:
                    access.install_provider_role(role)
                self.assertEqual(str(caught.exception), "session artifact resources are unavailable")
                self.assertFalse((session / ".agents").exists())
                self.assertEqual(
                    self.run_git(session, "check-ignore", "-q", "--no-index", "--", ".agents/", check=False).returncode,
                    1,
                )



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
            inspected = inspect_session_resources(root, "novel")
        self.assertIs(recovered.state, GitSessionResourceState.READY)
        self.assertEqual(inspected, recovered)
        self.assertEqual(recovered.seed_commit_id, interrupted.seed_commit_id)
        baseline = recovered.baseline_commit_id
        advanced = self.run_git(
            root, "commit-tree", f"{baseline}^{{tree}}", "-p", baseline,
            input_bytes=b"Concurrent main advance\n", environment=self.session_environment(),
        ).stdout.decode().strip()
        for hook, ref, target in (
            ("_verify_main", "refs/heads/main", advanced),
            ("_classify", recovered.identity.internal_ref, baseline),
        ):
            previous = self.run_git(root, "rev-parse", ref).stdout.decode().strip()
            original = getattr(_Repository, hook)

            def drift(repository, *args, **kwargs):
                self.run_git(root, "update-ref", ref, target, previous)
                return original(repository, *args, **kwargs)

            with mock.patch.dict(os.environ, self.session_environment(), clear=True):
                with mock.patch.object(_Repository, hook, drift):
                    with self.assertRaises(GitSessionResourceError) as caught:
                        inspect_session_resources(root, "novel")
            self.assertIs(caught.exception.code, GitSessionFailureCode.RECOVERY_REQUIRED)
            self.assertIs(caught.exception.operation, GitSessionOperationKind.INSPECT)
            self.assertIs(caught.exception.state, GitSessionResourceState.RECOVERY_REQUIRED)
            self.assertIs(caught.exception.recovery_action, GitSessionRecoveryAction.OPERATOR_RECOVERY)
            self.assertEqual(self.run_git(root, "rev-parse", ref).stdout.decode().strip(), target)
            self.run_git(root, "update-ref", ref, previous, target)






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
                    self.assertEqual(identity.worktree_root.read_bytes(), b"private sibling bytes\n")
                else:
                    self.assertTrue(identity.worktree_root.is_symlink())













        role = prepare_session_role_projection(
            PLOT_ARCHITECT, b'---\nname: "plot_architect"\nexact role bytes\n'
        )
        real_install = projection_module._install_immutable_regular
        for timing in ("before", "after"):
            root = self.initialize_workspace(f"role-index-stage-{timing}")
            ready = provision_session_resources(root, "novel")
            session = ready.identity.worktree_root
            binding = compose_session_artifact_binding(root, "novel")
            selected = session / ".agents/agents" / role.native_agent_id / "agent.md"
            staged = False

            def stage_at_publication(*args, **kwargs):
                nonlocal staged
                if timing == "before":
                    selected.write_bytes(role.content)
                    selected.chmod(0o400)
                else:
                    real_install(*args, **kwargs)
                self.run_git(session, "add", "-f", "--", selected.relative_to(session).as_posix())
                staged = True
                if timing == "before":
                    real_install(*args, **kwargs)

            with self.assertRaises(SessionArtifactBindingError) as caught:
                with binding.open_ready_resources() as access, mock.patch.object(
                    projection_module, "_install_immutable_regular", new=stage_at_publication
                ):
                    access.install_provider_role(role)
            self.assertTrue(staged)
            self.assertEqual(str(caught.exception), "session artifact resources are unavailable")
            self.assertEqual(selected.read_bytes(), role.content)
            self.assertIn(selected.relative_to(session).as_posix().encode() + b"\0",
                          self.run_git(session, "ls-files", "-z").stdout)

        real_write = os.write
        root = self.initialize_workspace("role-exclude-short-write")
        ready = provision_session_resources(root, "novel")
        session = ready.identity.worktree_root
        exclude = root / ".git/info/exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_bytes(b"preexisting\n")
        exclude_inode = exclude.stat().st_ino
        writes: list[int] = []

        def write_prefix_then_rest(descriptor: int, content: bytes) -> int:
            if os.fstat(descriptor).st_ino != exclude_inode:
                return real_write(descriptor, content)
            self.assertFalse((session / ".agents").exists())
            count = real_write(descriptor, content[:3] if not writes else content)
            writes.append(count)
            return count

        binding = compose_session_artifact_binding(root, "novel")
        with binding.open_ready_resources() as access, mock.patch.object(
            projection_module.os, "write", side_effect=write_prefix_then_rest
        ):
            access.install_provider_role(role)
        self.assertEqual(writes, [3, len(b"/.agents/\n") - 3])
        self.assertEqual(exclude.read_bytes(), b"preexisting\n/.agents/\n")
        self.assertEqual((session / ".agents/agents" / role.native_agent_id / "agent.md").read_bytes(), role.content)

        for failure in ("zero", "oversized", "bool", "partial-error"):
            failed_root = self.initialize_workspace(f"role-exclude-write-{failure}")
            failed_ready = provision_session_resources(failed_root, "novel")
            failed_session = failed_ready.identity.worktree_root
            failed_exclude = failed_root / ".git/info/exclude"
            failed_exclude.parent.mkdir(parents=True, exist_ok=True)
            failed_exclude.write_bytes(b"preexisting\n")
            failed_inode = failed_exclude.stat().st_ino
            attempts = 0

            def fail_append(descriptor: int, content: bytes) -> int:
                nonlocal attempts
                if os.fstat(descriptor).st_ino != failed_inode:
                    return real_write(descriptor, content)
                attempts += 1
                if failure == "partial-error":
                    if attempts == 1:
                        return real_write(descriptor, content[:3])
                    raise OSError("PRIVATE_SHORT_WRITE_FAILURE")
                if failure == "zero":
                    return 0
                return len(content) + 1 if failure == "oversized" else True

            failed_binding = compose_session_artifact_binding(failed_root, "novel")
            with self.assertRaises(SessionArtifactBindingError) as caught:
                with failed_binding.open_ready_resources() as access, mock.patch.object(
                    projection_module.os, "write", side_effect=fail_append
                ):
                    access.install_provider_role(role)
            self.assertEqual(attempts, 2 if failure == "partial-error" else 1)
            self.assertEqual(failed_exclude.read_bytes(),
                             b"preexisting\n" + (b"/.a" if failure == "partial-error" else b""))
            self.assertFalse((failed_session / ".agents").exists())
            self.assertEqual(str(caught.exception), "session artifact resources are unavailable")
            self.assertNotIn("PRIVATE", repr(caught.exception))

if __name__ == "__main__":
    unittest.main()
