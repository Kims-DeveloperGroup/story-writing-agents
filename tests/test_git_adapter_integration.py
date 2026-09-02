"""Isolated real-Git acceptance coverage for the safe repository adapter."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import unittest
from unittest import mock

from story_writing_agents.git import (
    GIT_BASELINE_BRANCH,
    GIT_BASELINE_COMMIT_MESSAGE,
    GitChangeKind,
    GitFailureCode,
    GitIdentity,
    GitOperationError,
    GitRepositoryAdapter,
    GitRepositoryState,
    GitTopologyKind,
)
from story_writing_agents.git import adapter as adapter_module

from tests.git_contract_fixture import (
    DisposableGitTestCase,
    exception_graph,
    write_manifest,
)


_IDENTITY_ENVIRONMENT = {
    "GIT_AUTHOR_NAME": "Baseline Author",
    "GIT_AUTHOR_EMAIL": "author@example.invalid",
    "GIT_COMMITTER_NAME": "Baseline Committer",
    "GIT_COMMITTER_EMAIL": "committer@example.invalid",
}


class GitAdapterIntegrationTests(DisposableGitTestCase):
    """Prove exact baseline and bounded projections against disposable Git."""

    def _adapter_environment(self, **updates: str) -> dict[str, str]:
        environment = self.git_environment(**_IDENTITY_ENVIRONMENT)
        environment.update(updates)
        return environment

    def _baseline_files(self) -> dict[str, bytes]:
        return {
            "core/outline.md": "# Outline\nA → B\n".encode(),
            "manuscript/part-01/chapter-01.md": b"Chapter one.\n",
        }

    def _assert_inspection_preserves_invalidated_index(
        self,
        *,
        object_format: str,
    ) -> None:
        root = self.repository(f"index-{object_format}")
        if object_format == "sha256":
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
        else:
            self.initialize(root)
        manifest = write_manifest(root, self._baseline_files())
        adapter = GitRepositoryAdapter(root)
        with mock.patch.dict(os.environ, self._adapter_environment(), clear=True):
            created = adapter.create_baseline(manifest)

        entries_before = self.run_git(root, "ls-files", "--stage", "-z", "--").stdout
        self.run_git(
            root,
            "update-index",
            "-z",
            "--index-info",
            input_bytes=entries_before,
        )
        self.assertEqual(
            self.run_git(root, "ls-files", "--stage", "-z", "--").stdout,
            entries_before,
        )

        index_path = root / ".git" / "index"
        fixed_time_ns = 1_600_000_000_123_456_789
        os.utime(index_path, ns=(fixed_time_ns, fixed_time_ns))
        index_before = index_path.read_bytes()
        stat_before = index_path.stat()
        cache_tree = index_before.find(b"TREE")
        self.assertNotEqual(cache_tree, -1)
        cache_tree_size = int.from_bytes(
            index_before[cache_tree + 4 : cache_tree + 8],
            byteorder="big",
        )
        cache_tree_before = index_before[cache_tree : cache_tree + 8 + cache_tree_size]
        self.assertIn(b"\x00-1 ", cache_tree_before)

        with mock.patch.dict(os.environ, self._adapter_environment(), clear=True):
            inspected = adapter.inspect_baseline(manifest)

        index_after = index_path.read_bytes()
        stat_after = index_path.stat()
        self.assertIs(inspected.state, GitRepositoryState.BASELINE_COMMITTED)
        self.assertEqual(inspected.commit_id, created.commit_id)
        self.assertEqual(inspected.tree_id, created.tree_id)
        self.assertEqual(index_after, index_before)
        self.assertEqual(stat_after.st_size, stat_before.st_size)
        self.assertEqual(stat_after.st_mtime_ns, stat_before.st_mtime_ns)
        self.assertEqual(
            index_after[cache_tree : cache_tree + len(cache_tree_before)],
            cache_tree_before,
        )

    def test_create_baseline_is_exact_root_commit_and_idempotent(self) -> None:
        root = self.repository()
        files = self._baseline_files()
        manifest = write_manifest(root, files)
        undeclared = root / "notes.tmp"
        undeclared.write_bytes(b"must remain untracked\n")
        before = {path: (root / path).read_bytes() for path in files}
        adapter = GitRepositoryAdapter(root)

        with mock.patch.dict(os.environ, self._adapter_environment(), clear=True):
            absent = adapter.inspect_baseline(manifest)
            created = adapter.create_baseline(manifest)
            repeated = adapter.create_baseline(manifest)
            status = adapter.status()
            diff = adapter.diff_metadata()
            log = adapter.log_metadata()

        self.assertIs(absent.topology.kind, GitTopologyKind.ABSENT)
        self.assertIs(absent.state, GitRepositoryState.ABSENT)
        self.assertIs(created.topology.kind, GitTopologyKind.STANDALONE)
        self.assertIs(created.state, GitRepositoryState.BASELINE_COMMITTED)
        self.assertEqual(created.manifest_digest, manifest.manifest_digest)
        self.assertEqual(repeated, created)
        self.assertEqual(
            created.effective_identity.author,
            GitIdentity("Baseline Author", "author@example.invalid"),
        )
        self.assertEqual(
            created.effective_identity.committer,
            GitIdentity("Baseline Committer", "committer@example.invalid"),
        )

        refs = self.run_git(root, "for-each-ref", "--format=%(refname)").stdout
        self.assertEqual(refs, b"refs/heads/main\n")
        self.assertEqual(
            self.run_git(root, "symbolic-ref", "HEAD").stdout,
            b"refs/heads/main\n",
        )
        self.assertEqual(
            self.run_git(root, "rev-list", "--count", "HEAD").stdout, b"1\n"
        )
        commit_and_parents = self.run_git(
            root, "rev-list", "--parents", "-1", "HEAD"
        ).stdout
        self.assertEqual(len(commit_and_parents.split()), 1)
        self.assertEqual(
            self.run_git(root, "write-tree").stdout.strip().decode(), created.tree_id
        )
        self.assertEqual(
            self.run_git(root, "rev-parse", "HEAD").stdout.strip().decode(),
            created.commit_id,
        )

        commit_payload = self.run_git(root, "cat-file", "commit", "HEAD").stdout
        _, message = commit_payload.split(b"\n\n", 1)
        self.assertEqual(message, GIT_BASELINE_COMMIT_MESSAGE.encode() + b"\n")
        self.assertEqual(
            self.run_git(
                root, "ls-tree", "-r", "--name-only", "-z", "HEAD", "--"
            ).stdout,
            b"core/outline.md\x00manuscript/part-01/chapter-01.md\x00",
        )
        self.assertEqual(
            self.run_git(root, "ls-files", "-z", "--").stdout,
            b"core/outline.md\x00manuscript/part-01/chapter-01.md\x00",
        )
        for relative, content in files.items():
            with self.subTest(relative=relative):
                self.assertEqual(
                    self.run_git(root, "show", f"HEAD:{relative}").stdout, content
                )
                self.assertEqual((root / relative).read_bytes(), before[relative])
        self.assertEqual(undeclared.read_bytes(), b"must remain untracked\n")
        self.assertTrue(status.entries)
        self.assertEqual(status.entries[0].path, "notes.tmp")
        self.assertIs(status.entries[0].worktree_change, GitChangeKind.UNTRACKED)
        self.assertEqual(diff.entries, ())
        self.assertEqual(len(log.commits), 1)
        self.assertEqual(log.commits[0].commit_id, created.commit_id)
        self.assertEqual(log.commits[0].parent_ids, ())
        self.assertEqual(
            log.commits[0].message_digest,
            hashlib.sha256(GIT_BASELINE_COMMIT_MESSAGE.encode() + b"\n").hexdigest(),
        )
        self.assertEqual(
            log.commits[0].message_utf8_bytes,
            len(GIT_BASELINE_COMMIT_MESSAGE.encode() + b"\n"),
        )
        self.assertFalse(hasattr(log.commits[0], "message"))

    def test_bare_repository_is_root_mismatch_and_create_is_nonmutating(self) -> None:
        root = self.repository("bare")
        self.assertEqual(
            self.run_git(
                root,
                "init",
                "--bare",
                "--quiet",
                "--initial-branch=main",
                "--template=",
                check=False,
            ).returncode,
            0,
        )
        manifest = write_manifest(root, {"outline.md": b"outline\n"})

        def snapshot() -> dict[str, tuple[int, int, int, bytes | str | None]]:
            observed: dict[str, tuple[int, int, int, bytes | str | None]] = {}
            for path in sorted(root.rglob("*")):
                value = path.lstat()
                content: bytes | str | None = None
                if stat.S_ISREG(value.st_mode):
                    content = path.read_bytes()
                elif stat.S_ISLNK(value.st_mode):
                    content = os.readlink(path)
                observed[path.relative_to(root).as_posix()] = (
                    stat.S_IFMT(value.st_mode) | stat.S_IMODE(value.st_mode),
                    value.st_size,
                    value.st_mtime_ns,
                    content,
                )
            return observed

        before = snapshot()
        adapter = GitRepositoryAdapter(root)
        topology = adapter.inspect_topology()
        with self.assertRaises(GitOperationError) as caught:
            adapter.create_baseline(manifest)

        self.assertIs(topology.kind, GitTopologyKind.ROOT_MISMATCH)
        self.assertIs(caught.exception.code, GitFailureCode.RECOVERY_REQUIRED)
        self.assertIs(
            caught.exception.repository_state,
            GitRepositoryState.RECOVERY_REQUIRED,
        )
        self.assertEqual(snapshot(), before)
        self.assertFalse((root / ".git").exists())

    def test_forged_manifest_is_rejected_before_read_or_repository_mutation(
        self,
    ) -> None:
        root = self.repository("forged-manifest")
        inside = b"inside\n"
        outside = b"outside private bytes\n"
        (self.test_root / "outside-secret.md").write_bytes(outside)
        manifest = write_manifest(root, {"outline.md": inside})
        entry = manifest.entries[0]
        object.__setattr__(entry, "path", "../outside-secret.md")
        object.__setattr__(entry, "content_digest", hashlib.sha256(outside).hexdigest())
        payload = json.dumps(
            [
                {
                    "content_digest": entry.content_digest,
                    "path": entry.path,
                }
            ],
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        object.__setattr__(
            manifest,
            "manifest_digest",
            hashlib.sha256(payload).hexdigest(),
        )

        with (
            mock.patch.object(adapter_module.os, "open", wraps=os.open) as open_file,
            mock.patch.object(adapter_module.subprocess, "Popen") as popen,
        ):
            for operation in ("inspect_baseline", "create_baseline"):
                with self.subTest(operation=operation):
                    with self.assertRaises(GitOperationError) as caught:
                        getattr(GitRepositoryAdapter(root), operation)(manifest)
                    self.assertIs(
                        caught.exception.code,
                        GitFailureCode.INVALID_MANIFEST,
                    )

        open_file.assert_not_called()
        popen.assert_not_called()
        self.assertEqual((root / "outline.md").read_bytes(), inside)
        self.assertEqual((self.test_root / "outside-secret.md").read_bytes(), outside)
        self.assertFalse((root / ".git").exists())

    def test_inspect_baseline_preserves_invalidated_sha1_index(self) -> None:
        self._assert_inspection_preserves_invalidated_index(object_format="sha1")

    def test_inspect_baseline_preserves_invalidated_sha256_index(self) -> None:
        self._assert_inspection_preserves_invalidated_index(object_format="sha256")

    def test_manifest_drift_symlink_and_missing_identity_fail_before_init(self) -> None:
        cases = ("drift", "symlink", "missing_identity", "invalid_identity")
        for case in cases:
            with self.subTest(case=case):
                root = self.repository(case)
                manifest = write_manifest(root, {"outline.md": b"original\n"})
                environment = self._adapter_environment()
                expected = GitFailureCode.IDENTITY_INVALID
                if case == "drift":
                    (root / "outline.md").write_bytes(b"changed-secret\n")
                    expected = GitFailureCode.MANIFEST_DRIFT
                elif case == "symlink":
                    target = root / "target.md"
                    target.write_bytes(b"original\n")
                    (root / "outline.md").unlink()
                    (root / "outline.md").symlink_to(target)
                    expected = GitFailureCode.MANIFEST_DRIFT
                elif case == "missing_identity":
                    environment = self.git_environment()
                else:
                    environment["GIT_AUTHOR_NAME"] = "bad\nsecret"
                with mock.patch.dict(os.environ, environment, clear=True):
                    with self.assertRaises(GitOperationError) as caught:
                        GitRepositoryAdapter(root).create_baseline(manifest)
                self.assertIs(caught.exception.code, expected)
                self.assertFalse((root / ".git").exists())
                self.assertEqual(exception_graph(caught.exception), (caught.exception,))
                self.assertNotIn("changed-secret", repr(caught.exception))

    def test_existing_wrong_baseline_and_post_commit_drift_require_recovery(
        self,
    ) -> None:
        wrong = self.repository("wrong")
        (wrong / "outline.md").write_bytes(b"wrong\n")
        self.initialize(wrong)
        self.configure_identity(wrong)
        self.commit_all(wrong, "not the fixed baseline")
        manifest = write_manifest(wrong, {"outline.md": b"wrong\n"})
        adapter = GitRepositoryAdapter(wrong)
        with mock.patch.dict(os.environ, self._adapter_environment(), clear=True):
            inspection = adapter.inspect_baseline(manifest)
            with self.assertRaises(GitOperationError) as caught:
                adapter.create_baseline(manifest)
        self.assertIs(inspection.state, GitRepositoryState.RECOVERY_REQUIRED)
        self.assertIs(caught.exception.code, GitFailureCode.RECOVERY_REQUIRED)
        self.assertIs(
            caught.exception.repository_state, GitRepositoryState.RECOVERY_REQUIRED
        )
        self.assertEqual(
            self.run_git(wrong, "rev-list", "--count", "HEAD").stdout, b"1\n"
        )

        root = self.repository("drift-after")
        current = write_manifest(root, {"outline.md": b"original\n"})
        with mock.patch.dict(os.environ, self._adapter_environment(), clear=True):
            baseline = GitRepositoryAdapter(root).create_baseline(current)
            (root / "outline.md").write_bytes(b"changed\n")
            changed = GitRepositoryAdapter(root).inspect_baseline(current)
        self.assertIs(baseline.state, GitRepositoryState.BASELINE_COMMITTED)
        self.assertIs(changed.state, GitRepositoryState.RECOVERY_REQUIRED)

    def test_initialized_repository_uses_effective_local_identity_without_mutating_config(
        self,
    ) -> None:
        root = self.repository()
        self.initialize(root)
        self.configure_identity(
            root,
            name="Repository Local",
            email="repository@example.invalid",
        )
        manifest = write_manifest(root, {"outline.md": b"outline\n"})
        config_before = (root / ".git" / "config").read_bytes()
        with mock.patch.dict(os.environ, self.git_environment(), clear=True):
            created = GitRepositoryAdapter(root).create_baseline(manifest)
        expected = GitIdentity("Repository Local", "repository@example.invalid")
        self.assertEqual(created.effective_identity.author, expected)
        self.assertEqual(created.effective_identity.committer, expected)
        self.assertEqual((root / ".git" / "config").read_bytes(), config_before)

    def test_status_and_diff_are_bounded_content_free_literal_metadata(self) -> None:
        root = self.repository()
        files = self._baseline_files()
        manifest = write_manifest(root, files)
        adapter = GitRepositoryAdapter(root)
        with mock.patch.dict(os.environ, self._adapter_environment(), clear=True):
            baseline = adapter.create_baseline(manifest)
        (root / "core/outline.md").write_bytes(b"changed prose must not be returned\n")
        (root / "manuscript/part-01/chapter-01.md").unlink()
        (root / "staged.md").write_bytes(b"staged secret\n")
        self.run_git(root, "add", "--", "staged.md")
        (root / "--literal.md").write_bytes(b"untracked option-like path\n")

        with mock.patch.dict(os.environ, self._adapter_environment(), clear=True):
            status = adapter.status()
            diff = adapter.diff_metadata()

        observed_status = {
            entry.path: (entry.index_change, entry.worktree_change)
            for entry in status.entries
        }
        self.assertEqual(status.branch, GIT_BASELINE_BRANCH)
        self.assertEqual(status.head_id, baseline.commit_id)
        self.assertEqual(
            observed_status,
            {
                "--literal.md": (None, GitChangeKind.UNTRACKED),
                "core/outline.md": (None, GitChangeKind.MODIFIED),
                "manuscript/part-01/chapter-01.md": (None, GitChangeKind.DELETED),
                "staged.md": (GitChangeKind.ADDED, None),
            },
        )
        observed_diff = {
            (entry.path, entry.staged): entry.change for entry in diff.entries
        }
        self.assertEqual(
            observed_diff,
            {
                ("core/outline.md", False): GitChangeKind.MODIFIED,
                ("manuscript/part-01/chapter-01.md", False): GitChangeKind.DELETED,
                ("staged.md", True): GitChangeKind.ADDED,
            },
        )
        self.assertFalse(hasattr(diff.entries[0], "content"))
        projection = repr((status, diff))
        for secret in ("changed prose", "staged secret", "untracked option-like"):
            self.assertNotIn(secret, projection)

    def test_hostile_git_configuration_cannot_execute_extensions(self) -> None:
        root = self.repository()
        markers = self.test_root / "markers"
        markers.mkdir()
        sentinel = self.test_root / "sentinel.sh"
        sentinel.write_text(
            '#!/bin/sh\nmkdir -p "$SAFE_GIT_MARKERS"\ntouch "$SAFE_GIT_MARKERS/$1"\nexit 99\n',
            encoding="utf-8",
        )
        sentinel.chmod(sentinel.stat().st_mode | stat.S_IXUSR)
        hooks = self.test_root / "hostile-hooks"
        hooks.mkdir()
        for hook in ("pre-commit", "commit-msg", "post-commit"):
            target = hooks / hook
            target.write_text(
                f'#!/bin/sh\n"{sentinel}" hook-{hook}\n', encoding="utf-8"
            )
            target.chmod(target.stat().st_mode | stat.S_IXUSR)
        template = self.test_root / "hostile-template"
        (template / "hooks").mkdir(parents=True)
        template_hook = template / "hooks" / "post-commit"
        template_hook.write_text(
            f'#!/bin/sh\n"{sentinel}" template\n', encoding="utf-8"
        )
        template_hook.chmod(template_hook.stat().st_mode | stat.S_IXUSR)
        global_config = self.test_root / "home" / ".gitconfig"
        global_config.write_text(
            "\n".join(
                (
                    "[user]",
                    "\tname = Hostile Global",
                    "\temail = hostile@example.invalid",
                    "[core]",
                    f"\thooksPath = {hooks}",
                    f"\tfsmonitor = {sentinel} fsmonitor",
                    f"\tpager = {sentinel} pager",
                    "[init]",
                    f"\ttemplateDir = {template}",
                    "[commit]",
                    "\tgpgSign = true",
                    "[gpg]",
                    f"\tprogram = {sentinel} signing",
                    "[diff]",
                    f"\texternal = {sentinel} external-diff",
                    '[diff "evil"]',
                    f"\tcommand = {sentinel} textconv",
                    '[filter "evil"]',
                    f"\tclean = {sentinel} clean",
                    f"\tsmudge = {sentinel} smudge",
                    "\trequired = true",
                    "[credential]",
                    f"\thelper = !{sentinel} credential",
                    "[alias]",
                    f"\tstatus = !{sentinel} alias",
                    "[pager]",
                    f"\tstatus = {sentinel} pager-status",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        global_config_before = global_config.read_bytes()
        files = {
            ".gitattributes": b"*.md filter=evil diff=evil\n",
            "outline.md": b"raw bytes\n",
        }
        manifest = write_manifest(root, files)
        environment = self._adapter_environment(SAFE_GIT_MARKERS=os.fspath(markers))

        with mock.patch.dict(os.environ, environment, clear=True):
            adapter = GitRepositoryAdapter(root)
            created = adapter.create_baseline(manifest)
            self.assertTrue(adapter.status().clean)
            self.assertEqual(adapter.diff_metadata().entries, ())
            self.assertEqual(len(adapter.log_metadata().commits), 1)

        self.assertIs(created.state, GitRepositoryState.BASELINE_COMMITTED)
        self.assertEqual(tuple(markers.iterdir()), ())
        self.assertEqual(global_config.read_bytes(), global_config_before)
        self.assertFalse((root / ".git" / "hooks" / "post-commit").exists())
        self.assertEqual(
            self.run_git(root, "show", "HEAD:outline.md").stdout, b"raw bytes\n"
        )

    def test_topology_matrix_rejects_parent_worktree_submodule_sparse_and_malformed(
        self,
    ) -> None:
        absent = self.repository("absent")
        self.assertIs(
            GitRepositoryAdapter(absent).inspect_topology().kind,
            GitTopologyKind.ABSENT,
        )

        standalone = self.repository("standalone")
        self.initialize(standalone)
        self.assertIs(
            GitRepositoryAdapter(standalone).inspect_topology().kind,
            GitTopologyKind.STANDALONE,
        )

        parent = self.repository("parent")
        self.initialize(parent)
        child = parent / "child"
        child.mkdir()
        self.assertIs(
            GitRepositoryAdapter(child.resolve()).inspect_topology().kind,
            GitTopologyKind.PARENT_REPOSITORY,
        )

        sparse = self.repository("sparse")
        self.initialize(sparse)
        self.run_git(sparse, "config", "core.sparseCheckout", "true")
        self.assertIs(
            GitRepositoryAdapter(sparse).inspect_topology().kind,
            GitTopologyKind.SPARSE_CHECKOUT,
        )

        symlinked = self.repository("symlinked")
        admin_target = self.test_root / "admin-target"
        admin_target.mkdir()
        (symlinked / ".git").symlink_to(admin_target, target_is_directory=True)
        self.assertIs(
            GitRepositoryAdapter(symlinked).inspect_topology().kind,
            GitTopologyKind.SYMLINKED_ADMINISTRATIVE_PATH,
        )

        nested_symlink = self.repository("nested-symlink")
        self.initialize(nested_symlink)
        (nested_symlink / ".git" / "unsafe-link").symlink_to(self.test_root / "outside")
        self.assertIs(
            GitRepositoryAdapter(nested_symlink).inspect_topology().kind,
            GitTopologyKind.SYMLINKED_ADMINISTRATIVE_PATH,
        )

        malformed = self.repository("malformed")
        (malformed / ".git").write_bytes(b"not a gitdir\n")
        self.assertIs(
            GitRepositoryAdapter(malformed).inspect_topology().kind,
            GitTopologyKind.LINKED_WORKTREE,
        )
        malformed_manifest = write_manifest(malformed, {"outline.md": b"outline\n"})
        with self.assertRaises(GitOperationError) as caught:
            GitRepositoryAdapter(malformed).create_baseline(malformed_manifest)
        self.assertIs(caught.exception.code, GitFailureCode.RECOVERY_REQUIRED)
        self.assertEqual((malformed / ".git").read_bytes(), b"not a gitdir\n")
        malformed_directory = self.repository("malformed-directory")
        (malformed_directory / ".git").mkdir()
        self.assertIs(
            GitRepositoryAdapter(malformed_directory).inspect_topology().kind,
            GitTopologyKind.MALFORMED_ADMINISTRATIVE_PATH,
        )

        alternates = self.repository("alternates")
        self.initialize(alternates)
        info = alternates / ".git" / "objects" / "info"
        info.mkdir(parents=True, exist_ok=True)
        (info / "alternates").write_text("/outside\n", encoding="utf-8")
        self.assertIs(
            GitRepositoryAdapter(alternates).inspect_topology().kind,
            GitTopologyKind.MALFORMED_ADMINISTRATIVE_PATH,
        )

        mismatch = self.repository("root-mismatch")
        other_worktree = self.repository("different-worktree")
        self.initialize(mismatch)
        self.run_git(mismatch, "config", "core.worktree", os.fspath(other_worktree))
        self.assertIs(
            GitRepositoryAdapter(mismatch).inspect_topology().kind,
            GitTopologyKind.ROOT_MISMATCH,
        )

        linked_source = self.repository("linked-source")
        self.initialize(linked_source)
        self.configure_identity(linked_source)
        (linked_source / "tracked").write_bytes(b"x")
        self.commit_all(linked_source)
        linked = self.test_root / "linked"
        self.run_git(
            linked_source,
            "worktree",
            "add",
            "--quiet",
            "--detach",
            os.fspath(linked),
            "HEAD",
        )
        self.assertIs(
            GitRepositoryAdapter(linked.resolve()).inspect_topology().kind,
            GitTopologyKind.LINKED_WORKTREE,
        )

        child_repository = self.repository("submodule-source")
        self.initialize(child_repository)
        self.configure_identity(child_repository)
        (child_repository / "tracked").write_bytes(b"x")
        self.commit_all(child_repository)
        superproject = self.repository("superproject")
        self.initialize(superproject)
        self.configure_identity(superproject)
        (superproject / "seed").write_bytes(b"x")
        self.commit_all(superproject)
        self.run_git(
            superproject,
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "--quiet",
            os.fspath(child_repository),
            "module",
        )
        self.assertIs(
            GitRepositoryAdapter((superproject / "module").resolve())
            .inspect_topology()
            .kind,
            GitTopologyKind.SUBMODULE,
        )

    def test_symlinked_root_is_a_content_free_unsafe_root_failure(self) -> None:
        target = self.repository("target")
        link = self.test_root / "root-link"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(GitOperationError) as caught:
            GitRepositoryAdapter(link).inspect_topology()
        self.assertIs(caught.exception.code, GitFailureCode.UNSAFE_ROOT)
        self.assertEqual(exception_graph(caught.exception), (caught.exception,))
        self.assertNotIn(os.fspath(target), repr(caught.exception))

    def test_interruption_at_mutation_stages_reports_state_and_retry_converges(
        self,
    ) -> None:
        scenarios = (
            (
                "before-init",
                "init",
                False,
                KeyboardInterrupt,
                GitRepositoryState.ABSENT,
            ),
            (
                "after-init",
                "init",
                True,
                SystemExit,
                GitRepositoryState.INITIALIZED_WITHOUT_BASELINE,
            ),
            (
                "after-commit-tree",
                "commit-tree",
                True,
                KeyboardInterrupt,
                GitRepositoryState.INITIALIZED_WITHOUT_BASELINE,
            ),
            (
                "after-update-ref",
                "update-ref",
                True,
                SystemExit,
                GitRepositoryState.BASELINE_COMMITTED,
            ),
        )
        original = GitRepositoryAdapter._required_git  # noqa: SLF001
        for name, command, after, signal, expected in scenarios:
            with self.subTest(stage=name):
                root = self.repository(name)
                manifest = write_manifest(root, {"outline.md": b"outline\n"})
                adapter = GitRepositoryAdapter(root)
                interrupted = False

                def interrupt_stage(
                    instance: GitRepositoryAdapter,
                    arguments: tuple[str, ...],
                    **kwargs: object,
                ) -> object:
                    nonlocal interrupted
                    selected = arguments[0] == command and not interrupted
                    if selected and not after:
                        interrupted = True
                        raise signal()
                    result = original(instance, arguments, **kwargs)
                    if selected:
                        interrupted = True
                        raise signal()
                    return result

                with (
                    mock.patch.dict(
                        os.environ, self._adapter_environment(), clear=True
                    ),
                    mock.patch.object(
                        GitRepositoryAdapter, "_required_git", interrupt_stage
                    ),
                ):
                    with self.assertRaises(GitOperationError) as caught:
                        adapter.create_baseline(manifest)
                self.assertTrue(interrupted)
                self.assertIs(caught.exception.code, GitFailureCode.CANCELLED)
                self.assertIs(caught.exception.repository_state, expected)
                self.assertEqual(exception_graph(caught.exception), (caught.exception,))
                with mock.patch.dict(
                    os.environ, self._adapter_environment(), clear=True
                ):
                    recovered = adapter.create_baseline(manifest)
                    retried = adapter.create_baseline(manifest)
                self.assertIs(recovered.state, GitRepositoryState.BASELINE_COMMITTED)
                self.assertEqual(retried.commit_id, recovered.commit_id)
                self.assertEqual(
                    self.run_git(root, "rev-list", "--count", "HEAD").stdout, b"1\n"
                )


if __name__ == "__main__":
    unittest.main()
