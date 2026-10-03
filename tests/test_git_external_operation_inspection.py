"""Focused external Git-operation inspection acceptance coverage."""

from __future__ import annotations

import ast
import os
from pathlib import Path
import re
import stat
import unittest
from unittest import mock

import story_writing_agents
import story_writing_agents.git as git_facade
from story_writing_agents.git import (
    GitExternalOperationInspection,
    GitExternalOperationKind,
    GitFailureCode,
    GitOperationError,
    GitOperationKind,
    GitRecoveryAction,
    GitRepositoryAdapter,
    GitRepositoryState,
)

from tests.git_contract_fixture import DisposableGitTestCase, exception_graph


_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_ROOT = _ROOT / "src" / "story_writing_agents"
_DIRECTORY_MARKERS = frozenset({"rebase-merge", "rebase-apply", "sequencer"})
_IDENTITY = re.compile(r"[0-9a-f]{64}")


def _administrative_directory(root: Path) -> Path:
    marker = root / ".git"
    if marker.is_dir():
        return marker
    payload = marker.read_bytes()
    prefix = b"gitdir: "
    if not payload.startswith(prefix) or not payload.endswith(b"\n"):
        raise AssertionError("oracle fixture has an invalid linked-worktree marker")
    return Path(payload[len(prefix) : -1].decode("utf-8"))


def _install_markers(root: Path, names: tuple[str, ...], *, secret: bytes) -> None:
    administrative = _administrative_directory(root)
    for name in names:
        target = administrative / name
        if name in _DIRECTORY_MARKERS:
            target.mkdir()
        else:
            target.write_bytes(secret)


def _tree_snapshot(root: Path) -> tuple[tuple[str, int, int, int, int, int, bytes | None], ...]:
    result = []
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        observed = path.lstat()
        content = path.read_bytes() if stat.S_ISREG(observed.st_mode) else None
        result.append(
            (
                path.relative_to(root).as_posix(),
                observed.st_mode,
                observed.st_ino,
                observed.st_size,
                observed.st_mtime_ns,
                observed.st_ctime_ns,
                content,
            )
        )
    return tuple(result)


class GitExternalOperationInspectionTests(DisposableGitTestCase):
    def initialized_repository(self, name: str) -> Path:
        root = self.repository(name)
        self.initialize(root)
        return root

    def assert_recovery_required(
        self,
        callback,
        *,
        forbidden: tuple[str, ...] = (),
    ) -> GitOperationError:
        with self.assertRaises(GitOperationError) as raised:
            callback()
        error = raised.exception
        self.assertIs(error.code, GitFailureCode.RECOVERY_REQUIRED)
        self.assertIs(error.operation, GitOperationKind.INSPECT_EXTERNAL_OPERATION)
        self.assertIs(error.repository_state, GitRepositoryState.RECOVERY_REQUIRED)
        self.assertIs(error.recovery_action, GitRecoveryAction.INSPECT_REPOSITORY)
        self.assertEqual(str(error), "Git repository requires recovery.")
        self.assertEqual(exception_graph(error), (error,))
        rendered = "\n".join((str(error), repr(error), repr(error.args), repr(vars(error))))
        for value in forbidden:
            self.assertNotIn(value, rendered)
        return error

    def test_none_and_each_unambiguous_marker_shape_classify_exactly(self) -> None:
        cases = (
            ((), GitExternalOperationKind.NONE),
            (("MERGE_HEAD",), GitExternalOperationKind.MERGE),
            (("rebase-merge",), GitExternalOperationKind.REBASE),
            (("rebase-apply",), GitExternalOperationKind.REBASE),
            (("CHERRY_PICK_HEAD",), GitExternalOperationKind.CHERRY_PICK),
            (
                ("CHERRY_PICK_HEAD", "sequencer"),
                GitExternalOperationKind.CHERRY_PICK,
            ),
            (("REVERT_HEAD",), GitExternalOperationKind.REVERT),
            (("REVERT_HEAD", "sequencer"), GitExternalOperationKind.REVERT),
            (("sequencer",), GitExternalOperationKind.SEQUENCER),
        )
        secret = b"PRIVATE EXTERNAL OPERATION PAYLOAD"
        for index, (markers, expected) in enumerate(cases):
            with self.subTest(markers=markers):
                root = self.initialized_repository(f"positive-{index}")
                _install_markers(root, markers, secret=secret)
                result = GitRepositoryAdapter(root).inspect_external_operation()
                self.assertIs(result.kind, expected)
                self.assertNotIn(secret.decode(), repr(result))

    def test_identity_digests_are_fresh_opaque_stable_and_worktree_specific(
        self,
    ) -> None:
        root = self.initialized_repository("identity-main")
        self.configure_identity(root)
        (root / "tracked").write_bytes(b"tracked")
        self.commit_all(root)
        linked = self.test_root / "identity-linked"
        self.run_git(
            root,
            "worktree",
            "add",
            "--quiet",
            "--detach",
            os.fspath(linked),
            "HEAD",
        )
        other = self.initialized_repository("identity-other")

        first = GitRepositoryAdapter(root).inspect_external_operation()
        repeated = GitRepositoryAdapter(root).inspect_external_operation()
        linked_result = GitRepositoryAdapter(linked.resolve()).inspect_external_operation()
        other_result = GitRepositoryAdapter(other).inspect_external_operation()

        self.assertEqual(repeated, first)
        self.assertEqual(
            linked_result.repository_identity_digest,
            first.repository_identity_digest,
        )
        self.assertNotEqual(
            linked_result.worktree_identity_digest,
            first.worktree_identity_digest,
        )
        self.assertNotEqual(
            other_result.repository_identity_digest,
            first.repository_identity_digest,
        )
        for result in (first, linked_result, other_result):
            self.assertIsInstance(result, GitExternalOperationInspection)
            self.assertRegex(result.repository_identity_digest, _IDENTITY)
            self.assertRegex(result.worktree_identity_digest, _IDENTITY)
            for private in (root.name, linked.name, other.name, os.fspath(self.test_root)):
                self.assertNotIn(private, repr(result))

    def test_marker_observation_uses_only_direct_read_only_metadata(self) -> None:
        root = self.initialized_repository("process-free")
        _install_markers(root, ("MERGE_HEAD",), secret=b"private")
        adapter = GitRepositoryAdapter(root)
        with mock.patch.object(
            GitRepositoryAdapter,
            "_run_git",
            side_effect=AssertionError("external operation inspection must not run Git"),
        ) as run_git:
            result = adapter.inspect_external_operation()
        self.assertIs(result.kind, GitExternalOperationKind.MERGE)
        self.assertEqual(run_git.call_count, 0)

    def test_mixed_dual_and_wrong_type_markers_require_content_safe_recovery(
        self,
    ) -> None:
        mixed = (
            ("MERGE_HEAD", "rebase-merge"),
            ("rebase-merge", "rebase-apply"),
            ("CHERRY_PICK_HEAD", "REVERT_HEAD"),
            ("MERGE_HEAD", "sequencer"),
            ("rebase-apply", "sequencer"),
        )
        secret = "PRIVATE-CONTRADICTORY-MARKER"
        for index, markers in enumerate(mixed):
            with self.subTest(markers=markers):
                root = self.initialized_repository(f"mixed-{index}")
                _install_markers(root, markers, secret=secret.encode())
                self.assert_recovery_required(
                    lambda root=root: GitRepositoryAdapter(
                        root
                    ).inspect_external_operation(),
                    forbidden=(secret,),
                )

        wrong_types = (
            ("MERGE_HEAD", "directory"),
            ("rebase-merge", "file"),
            ("sequencer", "file"),
        )
        for index, (name, wrong_type) in enumerate(wrong_types):
            with self.subTest(name=name, wrong_type=wrong_type):
                root = self.initialized_repository(f"wrong-type-{index}")
                target = _administrative_directory(root) / name
                if wrong_type == "directory":
                    target.mkdir()
                else:
                    target.write_bytes(secret.encode())
                self.assert_recovery_required(
                    lambda root=root: GitRepositoryAdapter(
                        root
                    ).inspect_external_operation(),
                    forbidden=(secret,),
                )

    def test_symlink_unreadable_replaced_and_racing_roots_fail_closed(
        self,
    ) -> None:
        secret = "PRIVATE-OUTSIDE-MARKER"
        for index, name in enumerate(("MERGE_HEAD", "sequencer")):
            with self.subTest(kind="symlink", name=name):
                root = self.initialized_repository(f"symlink-{index}")
                outside = self.test_root / f"outside-{index}"
                if name in _DIRECTORY_MARKERS:
                    outside.mkdir()
                else:
                    outside.write_text(secret, encoding="utf-8")
                (_administrative_directory(root) / name).symlink_to(
                    outside,
                    target_is_directory=name in _DIRECTORY_MARKERS,
                )
                outside_before = outside.stat() if outside.is_dir() else outside.read_bytes()
                self.assert_recovery_required(
                    lambda root=root: GitRepositoryAdapter(
                        root
                    ).inspect_external_operation(),
                    forbidden=(secret, os.fspath(outside)),
                )
                outside_after = outside.stat() if outside.is_dir() else outside.read_bytes()
                self.assertEqual(outside_after, outside_before)

        for index, name in enumerate(("MERGE_HEAD", "rebase-merge")):
            with self.subTest(kind="unreadable", name=name):
                root = self.initialized_repository(f"unreadable-{index}")
                _install_markers(root, (name,), secret=secret.encode())
                target = _administrative_directory(root) / name
                target.chmod(0)
                self.addCleanup(target.chmod, 0o700 if target.is_dir() else 0o600)
                self.assert_recovery_required(
                    lambda root=root: GitRepositoryAdapter(
                        root
                    ).inspect_external_operation(),
                    forbidden=(secret,),
                )

        replaced = self.initialized_repository("replaced-root")
        adapter = GitRepositoryAdapter(replaced)
        detached = self.test_root / "replaced-root-detached"
        replaced.rename(detached)
        replaced.mkdir()
        self.assert_recovery_required(adapter.inspect_external_operation)
        self.assertTrue(detached.joinpath(".git").is_dir())
        self.assertEqual(tuple(replaced.iterdir()), ())

        racing = self.initialized_repository("racing-root")
        race_adapter = GitRepositoryAdapter(racing)
        original = GitRepositoryAdapter._bind_root
        observations = 0

        def changed_identity(instance):
            nonlocal observations
            self.assertIs(instance, race_adapter)
            observations += 1
            identity = original(instance)
            return identity if observations == 1 else (identity[0], identity[1] + 1)

        with mock.patch.object(
            GitRepositoryAdapter,
            "_bind_root",
            autospec=True,
            side_effect=changed_identity,
        ):
            self.assert_recovery_required(race_adapter.inspect_external_operation)

    def test_inspection_preserves_marker_bytes_stats_refs_index_head_and_worktree(
        self,
    ) -> None:
        root = self.initialized_repository("preservation")
        self.configure_identity(root)
        (root / "tracked").write_bytes(b"tracked")
        self.commit_all(root)
        marker_secret = b"PRIVATE MERGE PAYLOAD"
        _install_markers(root, ("MERGE_HEAD",), secret=marker_secret)
        environment = self.git_environment(GIT_OPTIONAL_LOCKS="0")

        git_before = {
            "head": self.run_git(root, "rev-parse", "HEAD", environment=environment).stdout,
            "refs": self.run_git(root, "show-ref", "--head", environment=environment).stdout,
            "index": (root / ".git" / "index").read_bytes(),
            "tree": self.run_git(root, "write-tree", environment=environment).stdout,
            "status": self.run_git(
                root,
                "status",
                "--porcelain=v2",
                "-z",
                environment=environment,
            ).stdout,
        }
        filesystem_before = _tree_snapshot(root)
        adapter = GitRepositoryAdapter(root)
        with mock.patch.object(
            GitRepositoryAdapter,
            "_run_git",
            side_effect=AssertionError("inspection must not run Git"),
        ) as run_git:
            result = adapter.inspect_external_operation()
        filesystem_after = _tree_snapshot(root)
        git_after = {
            "head": self.run_git(root, "rev-parse", "HEAD", environment=environment).stdout,
            "refs": self.run_git(root, "show-ref", "--head", environment=environment).stdout,
            "index": (root / ".git" / "index").read_bytes(),
            "tree": self.run_git(root, "write-tree", environment=environment).stdout,
            "status": self.run_git(
                root,
                "status",
                "--porcelain=v2",
                "-z",
                environment=environment,
            ).stdout,
        }
        self.assertIs(result.kind, GitExternalOperationKind.MERGE)
        self.assertEqual(run_git.call_count, 0)
        self.assertEqual(filesystem_after, filesystem_before)
        self.assertEqual(git_after, git_before)
        self.assertEqual(
            (_administrative_directory(root) / "MERGE_HEAD").read_bytes(),
            marker_secret,
        )

    def test_contract_adapter_exports_and_dependencies_are_exact(self) -> None:
        for name in ("GitExternalOperationKind", "GitExternalOperationInspection"):
            self.assertIn(name, git_facade.__all__)
            self.assertNotIn(name, story_writing_agents.__all__)
            self.assertFalse(hasattr(story_writing_agents, name))
        public = {
            name
            for name in dir(GitRepositoryAdapter)
            if not name.startswith("_") and callable(getattr(GitRepositoryAdapter, name))
        }
        self.assertFalse(
            public & {"abort", "continue_operation", "reset", "clean", "merge"}
        )

        modules: dict[str, Path] = {}
        for path in _SOURCE_ROOT.rglob("*.py"):
            relative = path.relative_to(_SOURCE_ROOT)
            parts = (
                ("story_writing_agents", *relative.parts[:-1])
                if relative.name == "__init__.py"
                else ("story_writing_agents", *relative.with_suffix("").parts)
            )
            modules[".".join(parts)] = path

        def resolve(target: str) -> str | None:
            parts = target.split(".")
            while parts and ".".join(parts) not in modules:
                parts.pop()
            candidate = ".".join(parts)
            return candidate if candidate in modules else None

        def runtime_nodes(node: ast.AST):
            yield node
            if isinstance(node, ast.If) and (
                isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
                or isinstance(node.test, ast.Attribute) and node.test.attr == "TYPE_CHECKING"
            ):
                for child in node.orelse:
                    yield from runtime_nodes(child)
                return
            for child in ast.iter_child_nodes(node):
                yield from runtime_nodes(child)

        edges: set[tuple[str, str]] = set()
        for module, path in modules.items():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            context = module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
            for node in runtime_nodes(tree):
                targets: list[str] = []
                if isinstance(node, ast.Import):
                    targets.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        base = context[: len(context) - (node.level - 1)]
                        if node.module:
                            base.extend(node.module.split("."))
                        target = ".".join(base)
                    else:
                        target = node.module or ""
                    targets.append(target)
                    targets.extend(
                        f"{target}.{alias.name}" if target else alias.name
                        for alias in node.names
                    )
                for target in targets:
                    resolved = resolve(target)
                    if resolved is not None and resolved != module:
                        edges.add((module, resolved))
        self.assertEqual(len(modules), 149)
        self.assertEqual(len(edges), 628)
        git_contracts = "story_writing_agents.git.contracts"
        git_adapter = "story_writing_agents.git.adapter"
        self.assertEqual(
            {target for source, target in edges if source == git_adapter},
            {git_contracts, "story_writing_agents.git.process"},
        )
        self.assertFalse({target for source, target in edges if source == git_contracts})
        successors = {module: set() for module in modules}
        for source, target in edges:
            successors[source].add(target)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(module: str) -> None:
            if module in visiting:
                self.fail(f"internal import cycle reaches {module}")
            if module in visited:
                return
            visiting.add(module)
            for dependency in successors[module]:
                visit(dependency)
            visiting.remove(module)
            visited.add(module)

        for module in modules:
            visit(module)


if __name__ == "__main__":
    unittest.main()
