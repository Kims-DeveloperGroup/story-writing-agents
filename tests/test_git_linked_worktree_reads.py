"""Focused status/log acceptance coverage for secure linked worktrees."""

from __future__ import annotations

import ast
import hashlib
import inspect
import os
from pathlib import Path
import stat
import unittest
from unittest import mock

import story_writing_agents
import story_writing_agents.git as git_facade
from story_writing_agents.git import (
    GitBaselineManifest,
    GitChangeKind,
    GitExternalOperationInspection,
    GitExternalOperationKind,
    GitFailureCode,
    GitLogMetadata,
    GitManifestEntry,
    GitOperationError,
    GitOperationKind,
    GitRepositoryAdapter,
    GitRepositoryState,
    GitRepositoryTopology,
    GitStatus,
    GitTopologyKind,
)
from story_writing_agents.git import adapter as adapter_module

from tests.git_contract_fixture import DisposableGitTestCase, exception_graph


_ROOT = Path(__file__).resolve().parents[1]
_ADAPTER_SOURCE = _ROOT / "src" / "story_writing_agents" / "git" / "adapter.py"
_IDENTITY = (17, 29)
_HEAD = "1" * 40
_OPENING = GitExternalOperationInspection(
    GitExternalOperationKind.NONE,
    "a" * 64,
    "b" * 64,
)
_DRIFTED = GitExternalOperationInspection(
    GitExternalOperationKind.MERGE,
    "a" * 64,
    "b" * 64,
)


def _administrative_directory(root: Path) -> Path:
    marker = root / ".git"
    if marker.is_dir():
        return marker
    payload = marker.read_bytes()
    prefix = b"gitdir: "
    if not payload.startswith(prefix) or not payload.endswith(b"\n"):
        raise AssertionError("fixture has malformed linked-worktree metadata")
    return Path(payload[len(prefix) : -1].decode("utf-8"))


def _tree_snapshot(
    root: Path,
) -> tuple[tuple[str, int, int, int, int, bytes | None], ...]:
    values = []
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        observed = path.lstat()
        content = path.read_bytes() if stat.S_ISREG(observed.st_mode) else None
        values.append(
            (
                path.relative_to(root).as_posix(),
                observed.st_mode,
                observed.st_ino,
                observed.st_size,
                observed.st_mtime_ns,
                content,
            )
        )
    return tuple(values)


class GitLinkedWorktreeReadContractTests(unittest.TestCase):
    def test_status_and_log_keep_exact_signatures_results_exports_and_dependencies(
        self,
    ) -> None:
        self.assertEqual(
            str(inspect.signature(GitRepositoryAdapter.status)),
            "(self) -> 'GitStatus'",
        )
        self.assertEqual(
            str(inspect.signature(GitRepositoryAdapter.log_metadata)),
            "(self) -> 'GitLogMetadata'",
        )
        self.assertIn("GitStatus", git_facade.__all__)
        self.assertIn("GitLogMetadata", git_facade.__all__)
        self.assertNotIn("GitStatus", story_writing_agents.__all__)
        self.assertNotIn("GitLogMetadata", story_writing_agents.__all__)
        self.assertEqual(len(git_facade.__all__), 24)
        self.assertEqual(len(story_writing_agents.__all__), 106)
        self.assertEqual(len(tuple(GitOperationKind)), 7)

        tree = ast.parse(_ADAPTER_SOURCE.read_text())
        dependencies = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 1
        }
        self.assertEqual(dependencies, {"contracts", "process"})
        index = (_ROOT / "PYTHON_MODULE_INDEX.md").read_text()
        self.assertIn("Importable production units indexed: 143.", index)
        self.assertIn("Direct internal dependency edges indexed: 576.", index)
        self.assertIn("Directed internal dependency cycles indexed: 0.", index)

    def test_status_and_log_guard_external_identity_before_body_and_after_success(
        self,
    ) -> None:
        adapter = GitRepositoryAdapter(Path("/tmp/linked-contract"))
        for operation in ("status", "log_metadata"):
            trace: list[str] = []

            def inspect_external(_self):
                trace.append("external")
                return _OPENING

            def head(_self, _identity, *, allow_absent=False):
                del allow_absent
                trace.append("body")
                return None

            with (
                mock.patch.object(
                    GitRepositoryAdapter,
                    "_inspect_topology",
                    return_value=(
                        GitRepositoryTopology(GitTopologyKind.LINKED_WORKTREE),
                        _IDENTITY,
                    ),
                ),
                mock.patch.object(
                    GitRepositoryAdapter,
                    "_inspect_external_operation",
                    autospec=True,
                    side_effect=inspect_external,
                ),
                mock.patch.object(
                    GitRepositoryAdapter,
                    "_symbolic_head",
                    return_value=None,
                ),
                mock.patch.object(
                    GitRepositoryAdapter,
                    "_head_id",
                    autospec=True,
                    side_effect=head,
                ),
                mock.patch.object(
                    GitRepositoryAdapter,
                    "_repository_changes",
                    return_value=((), ()),
                ),
                mock.patch.object(
                    GitRepositoryAdapter,
                    "_bind_root",
                    return_value=_IDENTITY,
                ),
            ):
                result = getattr(adapter, operation)()
            with self.subTest(operation=operation):
                self.assertEqual(trace, ["external", "body", "external"])
                expected_type = GitStatus if operation == "status" else GitLogMetadata
                self.assertIs(type(result), expected_type)

    def test_final_guard_runs_after_failure_and_interruption_with_drift_precedence(
        self,
    ) -> None:
        adapter = GitRepositoryAdapter(Path("/tmp/linked-contract-failure"))
        scenarios = (
            (
                "ordinary",
                adapter_module._AdapterFailure(
                    GitFailureCode.COMMAND_FAILED,
                    exit_code=7,
                ),
                (_OPENING, _OPENING),
                GitFailureCode.COMMAND_FAILED,
                7,
            ),
            (
                "interruption",
                KeyboardInterrupt(),
                (_OPENING, _OPENING),
                GitFailureCode.CANCELLED,
                None,
            ),
            (
                "drift-precedence",
                adapter_module._AdapterFailure(
                    GitFailureCode.COMMAND_FAILED,
                    exit_code=7,
                ),
                (_OPENING, _DRIFTED),
                GitFailureCode.RECOVERY_REQUIRED,
                None,
            ),
        )
        for operation in ("status", "log_metadata"):
            for name, body_failure, guards, code, exit_code in scenarios:
                with self.subTest(operation=operation, scenario=name):
                    with (
                        mock.patch.object(
                            GitRepositoryAdapter,
                            "_inspect_topology",
                            return_value=(
                                GitRepositoryTopology(
                                    GitTopologyKind.LINKED_WORKTREE
                                ),
                                _IDENTITY,
                            ),
                        ),
                        mock.patch.object(
                            GitRepositoryAdapter,
                            "_inspect_external_operation",
                            side_effect=guards,
                        ) as external,
                        mock.patch.object(
                            GitRepositoryAdapter,
                            "_symbolic_head",
                            return_value=None,
                        ),
                        mock.patch.object(
                            GitRepositoryAdapter,
                            "_head_id",
                            side_effect=body_failure,
                        ),
                        mock.patch.object(
                            GitRepositoryAdapter,
                            "_bind_root",
                            return_value=_IDENTITY,
                        ),
                        self.assertRaises(GitOperationError) as raised,
                    ):
                        getattr(adapter, operation)()
                    error = raised.exception
                    self.assertIs(error.code, code)
                    self.assertEqual(error.exit_code, exit_code)
                    self.assertIs(
                        error.operation,
                        GitOperationKind.STATUS
                        if operation == "status"
                        else GitOperationKind.LOG_METADATA,
                    )
                    self.assertEqual(external.call_count, 2)
                    self.assertEqual(exception_graph(error), (error,))
                    self.assertNotIn("linked-contract-failure", repr(error))


class GitLinkedWorktreeReadIntegrationTests(DisposableGitTestCase):
    def _linked_repository(self, name: str) -> tuple[Path, Path, bytes]:
        root = self.repository(f"{name}-source")
        self.initialize(root)
        self.configure_identity(root)
        (root / "tracked.md").write_bytes(b"first\n")
        self.commit_all(root, message="first")
        (root / "tracked.md").write_bytes(b"second\n")
        self.commit_all(root, message="second")
        head = self.run_git(root, "rev-parse", "HEAD").stdout.strip()
        linked = self.test_root / f"{name}-linked"
        self.run_git(
            root,
            "worktree",
            "add",
            "--quiet",
            "-b",
            f"story-{name}",
            os.fspath(linked),
            "HEAD",
        )
        return root, linked.resolve(), head

    def _assert_recovery(
        self,
        callback,
        operation: GitOperationKind,
    ) -> GitOperationError:
        with self.assertRaises(GitOperationError) as raised:
            callback()
        error = raised.exception
        self.assertIs(error.code, GitFailureCode.RECOVERY_REQUIRED)
        self.assertIs(error.operation, operation)
        self.assertEqual(exception_graph(error), (error,))
        self.assertNotIn(os.fspath(self.test_root), repr(error))
        return error

    def test_status_and_log_return_exact_linked_worktree_metadata_with_stable_markers(
        self,
    ) -> None:
        root, linked, head = self._linked_repository("positive")
        (linked / "tracked.md").write_bytes(b"private changed prose\n")
        (linked / "staged.md").write_bytes(b"private staged prose\n")
        self.run_git(linked, "add", "--", "staged.md")
        (linked / "untracked.md").write_bytes(b"private untracked prose\n")
        marker = _administrative_directory(linked) / "MERGE_HEAD"
        marker.write_bytes(head + b"\n")

        adapter = GitRepositoryAdapter(linked)
        status = adapter.status()
        log = adapter.log_metadata()
        observed = {
            entry.path: (entry.index_change, entry.worktree_change)
            for entry in status.entries
        }
        self.assertEqual(status.branch, "story-positive")
        self.assertEqual(status.head_id, head.decode())
        self.assertEqual(
            observed,
            {
                "staged.md": (GitChangeKind.ADDED, None),
                "tracked.md": (None, GitChangeKind.MODIFIED),
                "untracked.md": (None, GitChangeKind.UNTRACKED),
            },
        )
        self.assertEqual(log.commits[0].commit_id, head.decode())
        self.assertEqual(
            tuple(commit.commit_id for commit in log.commits),
            tuple(
                self.run_git(root, "rev-list", "HEAD")
                .stdout.decode()
                .splitlines()
            ),
        )
        rendered = repr((status, log))
        for secret in ("private changed", "private staged", "private untracked"):
            self.assertNotIn(secret, rendered)
        self.assertEqual(marker.read_bytes(), head + b"\n")
        self.assertTrue(GitRepositoryAdapter(root).status().clean)
        self.assertEqual(
            GitRepositoryAdapter(root).log_metadata().commits[0].commit_id,
            head.decode(),
        )

    def test_linked_reads_preserve_markers_refs_index_head_and_worktree(self) -> None:
        root, linked, head = self._linked_repository("preserve")
        marker = _administrative_directory(linked) / "MERGE_HEAD"
        marker.write_bytes(head + b"\nprivate marker bytes")
        (linked / "tracked.md").write_bytes(b"private worktree bytes\n")
        administrative = _administrative_directory(linked)
        environment = self.git_environment(GIT_OPTIONAL_LOCKS="0")

        def git_facts() -> tuple[bytes, ...]:
            return (
                self.run_git(root, "show-ref", "--head", environment=environment).stdout,
                self.run_git(linked, "rev-parse", "HEAD", environment=environment).stdout,
                self.run_git(
                    linked,
                    "status",
                    "--porcelain=v2",
                    "-z",
                    environment=environment,
                ).stdout,
                (administrative / "index").read_bytes(),
            )

        before = (_tree_snapshot(linked), _tree_snapshot(administrative), git_facts())
        with mock.patch.dict(os.environ, environment, clear=True):
            adapter = GitRepositoryAdapter(linked)
            adapter.status()
            adapter.log_metadata()
        after = (_tree_snapshot(linked), _tree_snapshot(administrative), git_facts())
        self.assertEqual(after, before)

    def test_linked_root_external_kind_and_admin_identity_drift_fail_closed(self) -> None:
        _root, linked, _head = self._linked_repository("marker-drift")
        marker = _administrative_directory(linked) / "MERGE_HEAD"
        marker.write_bytes(b"stable marker")
        original_head = GitRepositoryAdapter._head_id

        def remove_marker(instance, identity, *, allow_absent=False):
            marker.unlink()
            return original_head(instance, identity, allow_absent=allow_absent)

        with mock.patch.object(
            GitRepositoryAdapter,
            "_head_id",
            autospec=True,
            side_effect=remove_marker,
        ):
            self._assert_recovery(
                GitRepositoryAdapter(linked).status,
                GitOperationKind.STATUS,
            )
        self.assertFalse(marker.exists())

        _root, admin_linked, _head = self._linked_repository("admin-drift")
        administrative = _administrative_directory(admin_linked)
        displaced_admin = administrative.with_name(administrative.name + "-preserved")
        original_changes = GitRepositoryAdapter._repository_changes

        def replace_admin(instance, identity, head):
            result = original_changes(instance, identity, head)
            administrative.rename(displaced_admin)
            return result

        with mock.patch.object(
            GitRepositoryAdapter,
            "_repository_changes",
            autospec=True,
            side_effect=replace_admin,
        ):
            self._assert_recovery(
                GitRepositoryAdapter(admin_linked).status,
                GitOperationKind.STATUS,
            )
        self.assertTrue(displaced_admin.is_dir())

        _root, root_linked, _head = self._linked_repository("root-drift")
        displaced_root = root_linked.with_name(root_linked.name + "-preserved")
        original_symbolic = GitRepositoryAdapter._symbolic_head

        def replace_root(instance, identity, *, allow_detached=False):
            result = original_symbolic(
                instance,
                identity,
                allow_detached=allow_detached,
            )
            root_linked.rename(displaced_root)
            root_linked.mkdir()
            return result

        with mock.patch.object(
            GitRepositoryAdapter,
            "_symbolic_head",
            autospec=True,
            side_effect=replace_root,
        ):
            self._assert_recovery(
                GitRepositoryAdapter(root_linked).status,
                GitOperationKind.STATUS,
            )
        self.assertTrue(displaced_root.joinpath(".git").is_file())
        self.assertEqual(tuple(root_linked.iterdir()), ())

    def test_diff_and_baseline_remain_rejected_for_linked_worktrees(self) -> None:
        _root, linked, _head = self._linked_repository("excluded")
        manifest = GitBaselineManifest(
            (
                GitManifestEntry(
                    "tracked.md",
                    hashlib.sha256((linked / "tracked.md").read_bytes()).hexdigest(),
                ),
            )
        )
        adapter = GitRepositoryAdapter(linked)
        with self.assertRaises(GitOperationError) as diff:
            adapter.diff_metadata()
        self.assertIs(diff.exception.code, GitFailureCode.UNSUPPORTED_TOPOLOGY)
        self.assertIs(diff.exception.operation, GitOperationKind.DIFF_METADATA)
        inspected = adapter.inspect_baseline(manifest)
        self.assertIs(inspected.state, GitRepositoryState.RECOVERY_REQUIRED)
        self.assertIs(inspected.topology.kind, GitTopologyKind.LINKED_WORKTREE)
        with self.assertRaises(GitOperationError) as created:
            adapter.create_baseline(manifest)
        self.assertIs(created.exception.code, GitFailureCode.RECOVERY_REQUIRED)
        self.assertIs(created.exception.operation, GitOperationKind.CREATE_BASELINE)


if __name__ == "__main__":
    unittest.main()
