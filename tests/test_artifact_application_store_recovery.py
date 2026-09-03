"""Focused recovery and boundary tests for artifact application."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from story_writing_agents.errors import WorkspaceFormatError, WorkspaceSecurityError
from story_writing_agents.writing_artifacts import (
    ArtifactApplicationState,
    ArtifactCreateOperation,
    ArtifactKind,
)
from story_writing_agents.writing_artifacts import (
    _application_store as application_store_module,
)
from story_writing_agents.writing_artifacts._storage import (
    ArtifactLeaseSecurityError,
)
from story_writing_agents.writing_artifacts.repository import (
    WritingArtifactRepository,
)

from tests.helpers import initialize_test_workspace
from tests.test_artifact_application_store_lifecycle import (
    _ApplicationStoreTestCase,
    _apply,
    _five_operation_request,
    _request,
    _seed_five_operation_state,
    _sha256,
)


_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_ROOT = _ROOT / "src" / "story_writing_agents"
_CREATE = "manuscript/part-01/security.md"
_EXPECTED_FAULT_POINTS = (
    "after_revalidation",
    "after_journal",
    "after_target",
    "after_targets",
    "after_record",
    "after_catalog",
    "before_journal_retirement",
    "after_journal_retirement",
)
_FAULT_PARTITIONS = {
    "prejournal": frozenset({"after_revalidation"}),
    "journal": frozenset({"after_journal"}),
    "targets": frozenset({"after_target"}),
    "metadata_tail": frozenset(
        {
            "after_targets",
            "after_record",
            "after_catalog",
            "before_journal_retirement",
            "after_journal_retirement",
        }
    ),
}


class _InjectedFault(OSError):
    pass


def _inject_once(point: str, *, occurrence: int = 1):
    observed = 0

    def inject(actual: str) -> None:
        nonlocal observed
        if actual == point:
            observed += 1
            if observed == occurrence:
                raise _InjectedFault(f"injected {point} {occurrence}")

    return inject


def _state_root(root: Path) -> Path:
    return root / application_store_module.ARTIFACT_STATE_DIRECTORY


def _assert_recovered_and_idempotent(
    test_case: unittest.TestCase,
    root: Path,
    request,
):
    recovered = WritingArtifactRepository(root)
    catalog = recovered.catalog()
    replay = _apply(recovered, request)
    test_case.assertIs(replay.state, ArtifactApplicationState.UNCHANGED)
    test_case.assertEqual(
        tuple(catalog.entries),
        tuple(item.path for item in replay.final_artifacts),
    )
    test_case.assertFalse(
        tuple(_state_root(root).joinpath("transactions").glob("*.json"))
    )
    return replay


class ArtifactApplicationRecoveryTests(_ApplicationStoreTestCase):
    def test_after_revalidation_keeps_old_authority_and_clean_retry_applies_once(
        self,
    ) -> None:
        root, repository = self.new_repository()
        _seed_five_operation_state(root)
        request = _five_operation_request(repository)
        before = {
            path: path.read_bytes()
            for path in root.rglob("*.md")
            if path.is_file()
        }
        repository._applications._fault_injector = _inject_once(
            "after_revalidation"
        )
        with self.assertRaisesRegex(_InjectedFault, "after_revalidation"):
            _apply(repository, request)
        after = {
            path: path.read_bytes()
            for path in root.rglob("*.md")
            if path.is_file()
        }
        self.assertEqual(after, before)
        self.assert_no_application_authority(root)

        retry_repository = WritingArtifactRepository(root)
        applied = _apply(retry_repository, request)
        self.assertIs(applied.state, ArtifactApplicationState.APPLIED)
        replay = _apply(WritingArtifactRepository(root), request)
        self.assertIs(replay.state, ArtifactApplicationState.UNCHANGED)

    def test_after_journal_recovers_before_authoritative_reads_and_retry_is_unchanged(
        self,
    ) -> None:
        root, repository = self.new_repository()
        _seed_five_operation_state(root)
        request = _five_operation_request(repository)
        repository._applications._fault_injector = _inject_once("after_journal")
        with self.assertRaisesRegex(_InjectedFault, "after_journal"):
            _apply(repository, request)
        journals = tuple(_state_root(root).joinpath("transactions").glob("*.json"))
        self.assertEqual(len(journals), 1)
        self.assertEqual(len(json.loads(journals[0].read_bytes())["targets"]), 6)

        replay = _assert_recovered_and_idempotent(self, root, request)
        self.assertEqual(len(replay.affected_paths), 6)

    def test_each_after_target_ordinal_recovers_five_operation_topology_once(
        self,
    ) -> None:
        for occurrence in range(1, 7):
            with self.subTest(occurrence=occurrence):
                root, repository = self.new_repository()
                _seed_five_operation_state(root)
                request = _five_operation_request(
                    repository,
                    application_id=f"application-target-{occurrence}",
                )
                repository._applications._fault_injector = _inject_once(
                    "after_target",
                    occurrence=occurrence,
                )
                with self.assertRaisesRegex(_InjectedFault, "after_target"):
                    _apply(repository, request)
                replay = _assert_recovered_and_idempotent(self, root, request)
                self.assertEqual(len(replay.affected_paths), 6)
                self.assertEqual(len(replay.rename_lineage), 1)
                self.assertEqual(len(replay.deletion_provenance), 1)

    def test_metadata_tail_faults_recover_new_authority_and_completed_lookup_is_idempotent(
        self,
    ) -> None:
        for point in tuple(_FAULT_PARTITIONS["metadata_tail"]):
            with self.subTest(point=point):
                root, repository = self.new_repository()
                _seed_five_operation_state(root)
                request = _five_operation_request(
                    repository,
                    application_id=f"application-{point}",
                )
                repository._applications._fault_injector = _inject_once(point)
                with self.assertRaisesRegex(_InjectedFault, point):
                    _apply(repository, request)
                record = _state_root(root) / "applications" / f"{request.application_id}.json"
                record_before = record.read_bytes() if record.exists() else None
                self.assertEqual(record_before is not None, point != "after_targets")
                replay = _assert_recovered_and_idempotent(self, root, request)
                if record_before is not None:
                    self.assertEqual(record.read_bytes(), record_before)
                else:
                    self.assertTrue(record.is_file())
                self.assertIs(replay.state, ArtifactApplicationState.UNCHANGED)


class ArtifactApplicationSecurityTests(_ApplicationStoreTestCase):
    def test_application_targets_and_authority_fail_closed_on_symlink_hardlink_alias_and_root_replacement(
        self,
    ) -> None:
        with self.subTest(case="target_symlink"):
            root, repository = self.new_repository()
            request = _request(
                repository,
                lambda _base: (
                    ArtifactCreateOperation(
                        _CREATE,
                        ArtifactKind.MANUSCRIPT,
                        b"new-content",
                        _sha256(b"new-content"),
                    ),
                ),
                application_id="application-symlink",
            )
            outside = root / "outside-sentinel"
            outside.write_bytes(b"outside")
            (root / _CREATE).parent.mkdir(parents=True, exist_ok=True)
            (root / _CREATE).symlink_to(outside)
            with self.assertRaises(WorkspaceSecurityError):
                _apply(repository, request)
            self.assertEqual(outside.read_bytes(), b"outside")

        with self.subTest(case="journal_hardlink"):
            root, repository = self.new_repository()
            _seed_five_operation_state(root)
            request = _five_operation_request(
                repository,
                application_id="application-hardlink",
            )
            repository._applications._fault_injector = _inject_once("after_journal")
            with self.assertRaises(_InjectedFault):
                _apply(repository, request)
            state = _state_root(root)
            journal = next(state.joinpath("transactions").glob("*.json"))
            alias = state / "staging/journal-hardlink"
            os.link(journal, alias)
            with self.assertRaises(WorkspaceFormatError):
                WritingArtifactRepository(root).catalog()
            self.assertEqual(os.stat(journal).st_nlink, 2)

        with self.subTest(case="catalog_alias"):
            root, repository = self.new_repository()
            _seed_five_operation_state(root)
            request = _five_operation_request(
                repository,
                application_id="application-alias",
            )
            state = _state_root(root)
            state.mkdir(parents=True, exist_ok=True)
            state.joinpath("Catalog.json").write_bytes(b"alias")
            with self.assertRaises(WorkspaceSecurityError):
                _apply(repository, request)

        with self.subTest(case="root_replacement"):
            root, repository = self.new_repository()
            _seed_five_operation_state(root)
            request = _five_operation_request(
                repository,
                application_id="application-root-replacement",
            )
            detached = root.with_name(root.name + "-detached")
            root.rename(detached)
            root.mkdir()
            try:
                with self.assertRaises(WorkspaceSecurityError):
                    _apply(repository, request)
                self.assertFalse(tuple(root.iterdir()))
            finally:
                root.rmdir()
                detached.rename(root)


class ArtifactApplicationLeaseTests(_ApplicationStoreTestCase):
    def test_one_repository_lease_spans_every_application_fault_point(self) -> None:
        root, repository = self.new_repository()
        _seed_five_operation_state(root)
        request = _five_operation_request(repository)
        observed: list[str] = []

        def observe(point: str) -> None:
            repository._storage._require_active_lease()
            observed.append(point)

        repository._applications._fault_injector = observe
        lock = repository._storage.exclusive_lock
        with mock.patch.object(
            repository._storage,
            "exclusive_lock",
            wraps=lock,
        ) as acquire:
            result = _apply(repository, request)
        self.assertIs(result.state, ArtifactApplicationState.APPLIED)
        self.assertEqual(acquire.call_count, 1)
        self.assertEqual(set(observed), set(_EXPECTED_FAULT_POINTS))
        self.assertEqual(observed.count("after_target"), 6)
        with self.assertRaises(ArtifactLeaseSecurityError):
            repository._storage._require_active_lease()


class ArtifactApplicationGitBoundaryTests(_ApplicationStoreTestCase):
    def test_application_changes_no_git_index_refs_or_staging_and_imports_no_git_code(
        self,
    ) -> None:
        root, repository = self.new_repository()
        _seed_five_operation_state(root)
        request = _five_operation_request(repository)

        def git(*arguments: str) -> bytes:
            return subprocess.run(
                ("git", *arguments),
                cwd=root,
                check=True,
                capture_output=True,
            ).stdout

        before = {
            "head": git("rev-parse", "HEAD"),
            "refs": git("show-ref", "--head"),
            "index": git("write-tree"),
            "staged": git("diff", "--cached", "--binary"),
        }
        _apply(repository, request)
        after = {
            "head": git("rev-parse", "HEAD"),
            "refs": git("show-ref", "--head"),
            "index": git("write-tree"),
            "staged": git("diff", "--cached", "--binary"),
        }
        self.assertEqual(after, before)
        tree = ast.parse(
            Path(application_store_module.__file__).read_text(encoding="utf-8")
        )
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        self.assertFalse(any(name == "git" or name.endswith(".git") for name in imported))


class ArtifactApplicationBoundaryTests(_ApplicationStoreTestCase):
    def test_store_is_sole_persistence_interpreter_and_module_index_matches_boundary(
        self,
    ) -> None:
        combined = set().union(*_FAULT_PARTITIONS.values())
        self.assertEqual(tuple(application_store_module.APPLICATION_FAULT_POINTS), _EXPECTED_FAULT_POINTS)
        self.assertEqual(combined, set(_EXPECTED_FAULT_POINTS))
        self.assertEqual(
            sum(len(value) for value in _FAULT_PARTITIONS.values()),
            len(combined),
        )
        repository_path = _SOURCE_ROOT / "writing_artifacts/repository.py"
        repository_source = repository_path.read_text(encoding="utf-8")
        for private_owner in (
            "_complete_transaction",
            "_validate_transaction",
            "_load_application_records",
            "ARTIFACT_TRANSACTIONS_DIRECTORY",
        ):
            self.assertNotIn(private_owner, repository_source)

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

        edges: set[tuple[str, str]] = set()
        for module, path in modules.items():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            context = module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
            for node in ast.walk(tree):
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
        self.assertEqual(len(modules), 117)
        self.assertEqual(len(edges), 424)
        store = "story_writing_agents.writing_artifacts._application_store"
        repository = "story_writing_agents.writing_artifacts.repository"
        self.assertEqual(
            {target for source, target in edges if source == store},
            {
                "story_writing_agents.errors",
                "story_writing_agents.writing_artifacts._storage",
                "story_writing_agents.writing_artifacts.application_contracts",
                "story_writing_agents.writing_artifacts.catalog",
                "story_writing_agents.writing_artifacts.change_sets",
                "story_writing_agents.writing_artifacts.relationship_policy",
            },
        )
        self.assertIn((repository, store), edges)
        self.assertIn(
            (
                repository,
                "story_writing_agents.writing_artifacts.application_contracts",
            ),
            edges,
        )
        self.assertNotIn((store, repository), edges)
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
        index = (_ROOT / "PYTHON_MODULE_INDEX.md").read_text(encoding="utf-8")
        self.assertIn("Importable production units indexed: 117.", index)
        self.assertIn("Direct internal dependency edges indexed: 424.", index)
        self.assertIn("Directed internal dependency cycles indexed: 0.", index)
        self.assertIn(
            "### `story_writing_agents.writing_artifacts._application_store`",
            index,
        )


if __name__ == "__main__":
    unittest.main()
