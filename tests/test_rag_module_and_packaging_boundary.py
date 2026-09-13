"""Optional RAG import-direction, pin, and artifact boundary verification."""

from __future__ import annotations

import ast
import configparser
import email.parser
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import tomllib
import unittest
import zipfile
from pathlib import Path

import story_writing_agents
from story_writing_agents import (
    conversation_rag,
    conversation_rag_cli,
    conversation_rag_composition,
    conversation_rag_local,
    conversation_rag_runtime,
    conversation_rag_store,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_ROOT = _REPOSITORY_ROOT / "src" / "story_writing_agents"
_GENERIC_RAG_PIN = "c77456321c614518e420e45c033e3f71dc5363b8"
_GENERIC_RAG_REPOSITORY = "https://github.com/Kims-DeveloperGroup/generic-rag.git"
_GENERIC_RAG_REQUIREMENT = (
    f"generic-rag @ git+{_GENERIC_RAG_REPOSITORY}@{_GENERIC_RAG_PIN}"
)
_OLD_JSONL_VIEWER_PIN = "1d4f5f86ccb4ebd2f458f046df7a0b99b9fbeaec"
_JSONL_VIEWER_PIN = "ce57f560f3ca1366ea69dfe15cb1c598513e2c82"
_JSONL_VIEWER_VERSION = "0.3.0"
_JSONL_VIEWER_REPOSITORY = "https://github.com/Kims-DeveloperGroup/jsonl-viewer.git"
_JSONL_VIEWER_REQUIREMENT = (
    f"jsonl-viewer @ git+{_JSONL_VIEWER_REPOSITORY}@{_JSONL_VIEWER_PIN}"
)
_GITLINK_PATHS = {
    "vendor/generic-rag": _GENERIC_RAG_PIN,
    "vendor/jsonl-viewer": _JSONL_VIEWER_PIN,
}


def _module_inventory() -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for path in _SOURCE_ROOT.rglob("*.py"):
        relative = path.relative_to(_SOURCE_ROOT)
        if relative.name == "__init__.py":
            parts = ("story_writing_agents", *relative.parts[:-1])
        else:
            parts = ("story_writing_agents", *relative.with_suffix("").parts)
        modules[".".join(parts)] = path
    return modules


def _resolve_internal_module(
    target: str,
    modules: dict[str, Path],
) -> str | None:
    parts = target.split(".")
    while parts and ".".join(parts) not in modules:
        parts.pop()
    resolved = ".".join(parts)
    return resolved if resolved in modules else None


def _internal_edges(modules: dict[str, Path]) -> set[tuple[str, str]]:
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
        context = (
            module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
        )
        for node in runtime_nodes(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    retained = len(context) - (node.level - 1)
                    base = context[:retained]
                    if node.module:
                        base.extend(node.module.split("."))
                    target = ".".join(base)
                else:
                    target = node.module or ""
                targets.append(target)
                for alias in node.names:
                    child = f"{target}.{alias.name}" if target else alias.name
                    if child in modules:
                        targets.append(child)
            for target in targets:
                resolved = _resolve_internal_module(target, modules)
                if resolved is not None and resolved != module:
                    edges.add((module, resolved))
    return edges


def _cycle_witness(
    modules: dict[str, Path],
    edges: set[tuple[str, str]],
) -> tuple[str, ...] | None:
    successors: dict[str, set[str]] = {module: set() for module in modules}
    for source, target in edges:
        successors[source].add(target)
    visiting: list[str] = []
    active: set[str] = set()
    complete: set[str] = set()

    def visit(module: str) -> tuple[str, ...] | None:
        if module in complete:
            return None
        if module in active:
            start = visiting.index(module)
            return (*visiting[start:], module)
        active.add(module)
        visiting.append(module)
        for target in sorted(successors[module]):
            witness = visit(target)
            if witness is not None:
                return witness
        visiting.pop()
        active.remove(module)
        complete.add(module)
        return None

    for module in sorted(modules):
        witness = visit(module)
        if witness is not None:
            return witness
    return None


def _generic_importers(modules: dict[str, Path]) -> set[str]:
    importers: set[str] = set()
    for module, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                targets = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                targets = (node.module or "",)
            else:
                continue
            if any(
                target == "generic_rag" or target.startswith("generic_rag.")
                for target in targets
            ):
                importers.add(module)
    return importers


def _copy_build_input(source: Path, destination: Path, *, vendor: bool) -> None:
    destination.mkdir(parents=True)
    for filename in ("pyproject.toml", "README.md"):
        shutil.copy2(source / filename, destination / filename)
    shutil.copytree(
        source / "src",
        destination / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    if vendor:
        _export_gitlink_project(
            "vendor/generic-rag",
            destination / "vendor" / "generic-rag",
        )
        _export_gitlink_project(
            "vendor/jsonl-viewer",
            destination / "vendor" / "jsonl-viewer",
        )


def _copy_setuptools_build_support(destination: Path) -> None:
    specification = importlib.util.find_spec("setuptools")
    if specification is None or specification.origin is None:
        raise AssertionError("setuptools build support is unavailable")
    source = Path(specification.origin).parent
    site_packages = source.parent
    destination.mkdir()
    for package_name in ("setuptools", "_distutils_hack"):
        package = site_packages / package_name
        if package.is_dir():
            shutil.copytree(
                package,
                destination / package_name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
    for metadata in (
        *site_packages.glob("setuptools-*.dist-info"),
        *site_packages.glob("setuptools-*.egg-info"),
    ):
        if metadata.is_dir():
            shutil.copytree(metadata, destination / metadata.name)


def _gitlink_git_directory(relative: str) -> Path:
    if relative not in _GITLINK_PATHS:
        raise ValueError("unsupported gitlink path")
    resolved = subprocess.run(
        ["git", "rev-parse", "--git-path", f"modules/{relative}"],
        cwd=_REPOSITORY_ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    directory = Path(resolved)
    if not directory.is_absolute():
        directory = _REPOSITORY_ROOT / directory
    if not directory.is_dir():
        common = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=_REPOSITORY_ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        common_directory = Path(common)
        if not common_directory.is_absolute():
            common_directory = _REPOSITORY_ROOT / common_directory
        directory = common_directory / "modules" / relative
    if not directory.is_dir():
        raise AssertionError(f"gitlink object store is unavailable: {relative}")
    return directory.resolve()


def _gitlink_index_entry(relative: str) -> tuple[str, str]:
    entry = subprocess.run(
        ["git", "ls-files", "--stage", "--", relative],
        cwd=_REPOSITORY_ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    prefix, observed_path = entry.split("\t", 1)
    mode, object_id, stage = prefix.split()
    if stage != "0" or observed_path != relative:
        raise AssertionError(f"invalid gitlink index entry: {entry!r}")
    return mode, object_id


def _assert_effective_gitlink(test: unittest.TestCase, relative: str) -> None:
    expected = _GITLINK_PATHS[relative]
    mode, indexed = _gitlink_index_entry(relative)
    test.assertEqual(mode, "160000")
    head = subprocess.run(
        [
            "git",
            f"--git-dir={_gitlink_git_directory(relative)}",
            "rev-parse",
            "HEAD",
        ],
        cwd=_REPOSITORY_ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    test.assertEqual(head, expected)
    if indexed != expected:
        submodule_diff = subprocess.run(
            ["git", "diff", "--submodule=short", "--", relative],
            cwd=_REPOSITORY_ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        test.assertIn(f"-Subproject commit {indexed}", submodule_diff)
        test.assertIn(f"+Subproject commit {expected}", submodule_diff)


def _export_gitlink_project(relative: str, destination: Path) -> None:
    expected = _GITLINK_PATHS[relative]
    git_directory = _gitlink_git_directory(relative)
    archive = subprocess.run(
        [
            "git",
            f"--git-dir={git_directory}",
            "archive",
            "--format=zip",
            expected,
        ],
        cwd=_REPOSITORY_ROOT,
        capture_output=True,
        check=True,
    ).stdout
    destination.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(archive)) as packaged:
        packaged.extractall(destination)


def _build_local_artifacts(project: Path, distribution: Path) -> tuple[Path, Path]:
    script = textwrap.dedent(
        """
        import pathlib
        from setuptools import build_meta

        destination = pathlib.Path(__import__("sys").argv[1]).resolve()
        wheel = build_meta.build_wheel(str(destination))
        sdist = build_meta.build_sdist(str(destination))
        print(wheel)
        print(sdist)
        """
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script, str(distribution)],
        cwd=project,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "local artifact build failed\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    wheel = next(distribution.glob("*.whl"))
    sdist = next(distribution.glob("*.tar.gz"))
    return wheel, sdist


class RagModuleBoundaryTests(unittest.TestCase):
    def test_inventory_edges_cycles_and_sole_dual_importer_match_the_brief(
        self,
    ) -> None:
        modules = _module_inventory()
        edges = _internal_edges(modules)
        self.assertEqual(len(modules), 140)
        self.assertEqual(len(edges), 555)
        self.assertIsNone(_cycle_witness(modules, edges))
        conversation_dependencies = {
            "story_writing_agents.conversations": {
                "story_writing_agents.conversations.catalog",
                "story_writing_agents.conversations.context",
                "story_writing_agents.conversations.events",
                "story_writing_agents.git.story_commit_contracts",
            },
            "story_writing_agents.conversations.catalog": {
                "story_writing_agents.errors",
            },
            "story_writing_agents.conversations.events": {
                "story_writing_agents.specialist_routing",
                "story_writing_agents.agents",
                "story_writing_agents.artifact_proposals",
                "story_writing_agents.errors",
                "story_writing_agents.git.story_commit_contracts",
                "story_writing_agents.writing_artifacts",
            },
            "story_writing_agents.conversations.context": {
                "story_writing_agents.specialist_routing",
                "story_writing_agents.conversations.events",
                "story_writing_agents.errors",
                "story_writing_agents.git.story_commit_contracts",
                "story_writing_agents.writing_artifacts",
            },
        }
        for source, expected in conversation_dependencies.items():
            with self.subTest(conversation_source=source):
                self.assertEqual(
                    {target for edge_source, target in edges if edge_source == source},
                    expected,
                )
        workspace_dependencies = {
            "story_writing_agents.workspace": {
                "story_writing_agents.conversation_storage.filesystem",
                "story_writing_agents.workspace.core",
            },
            "story_writing_agents.workspace.core": {
                "story_writing_agents.agents",
                "story_writing_agents.conversations",
                "story_writing_agents.errors",
                "story_writing_agents.models",
                "story_writing_agents.workspace.initialization",
                "story_writing_agents.workspace.schema",
                "story_writing_agents.writing_artifacts",
            },
            "story_writing_agents.workspace.initialization": {
                "story_writing_agents.agent_exchange_paths",
                "story_writing_agents.config",
                "story_writing_agents.errors",
                "story_writing_agents.git",
                "story_writing_agents.git.session_contracts",
                "story_writing_agents.git.session_checkpoints",
                "story_writing_agents.git.session_history",
                "story_writing_agents.git.session_resources",
                "story_writing_agents.git.story_commits",
                "story_writing_agents.runtime_state_paths",
                "story_writing_agents.workspace._policy_baselines",
                "story_writing_agents.workspace.schema",
            },
            "story_writing_agents.workspace.schema": {
                "story_writing_agents.agents",
                "story_writing_agents.errors",
                "story_writing_agents.git.session_contracts",
            },
            "story_writing_agents.conversation_storage.filesystem": {
                "story_writing_agents.conversations",
                "story_writing_agents.conversations.events",
                "story_writing_agents.errors",
                "story_writing_agents.workspace.core",
            },
        }
        for source, expected in workspace_dependencies.items():
            with self.subTest(workspace_source=source):
                self.assertEqual(
                    {target for edge_source, target in edges if edge_source == source},
                    expected,
                )
        git_dependencies = {
            "story_writing_agents.git": {
                "story_writing_agents.git.adapter",
                "story_writing_agents.git.contracts",
            },
            "story_writing_agents.git.contracts": set(),
            "story_writing_agents.git.process": {
                "story_writing_agents.git.contracts",
            },
            "story_writing_agents.git.adapter": {
                "story_writing_agents.git.contracts",
                "story_writing_agents.git.process",
            },
            "story_writing_agents.git.session_contracts": {
                "story_writing_agents.git.contracts",
            },
            "story_writing_agents.git.session_resources": {
                "story_writing_agents.git.contracts",
                "story_writing_agents.git.process",
                "story_writing_agents.git.repository_lock",
                "story_writing_agents.git.session_contracts",
            },
        }
        for source, expected in git_dependencies.items():
            with self.subTest(git_source=source):
                self.assertEqual(
                    {target for edge_source, target in edges if edge_source == source},
                    expected,
                )
        changed_storage_dependencies = {
            "story_writing_agents.conversation_storage.composition": {
                "story_writing_agents.conversation_search",
                "story_writing_agents.conversation_storage.contracts",
                "story_writing_agents.conversation_storage.filesystem",
                "story_writing_agents.workspace.core",
            },
            "story_writing_agents.conversation_storage.filesystem_archive": {
                "story_writing_agents.conversation_storage.archive",
                "story_writing_agents.conversation_storage.contracts",
                "story_writing_agents.conversation_storage.filesystem",
                "story_writing_agents.conversations",
                "story_writing_agents.errors",
                "story_writing_agents.workspace.core",
            },
            "story_writing_agents.conversation_storage.migration": {
                "story_writing_agents.conversation_storage.archive",
                "story_writing_agents.conversation_storage.composition",
                "story_writing_agents.conversation_storage.contracts",
                "story_writing_agents.conversation_storage.filesystem_archive",
                "story_writing_agents.conversation_storage.sqlite",
                "story_writing_agents.errors",
                "story_writing_agents.workspace.core",
            },
            "story_writing_agents.conversation_storage.provisioning": {
                "story_writing_agents.conversation_storage.contracts",
                "story_writing_agents.conversation_storage.sqlite",
                "story_writing_agents.workspace.core",
            },
            "story_writing_agents.conversation_storage.selection": {
                "story_writing_agents.config",
                "story_writing_agents.conversation_storage.archive",
                "story_writing_agents.conversation_storage.composition",
                "story_writing_agents.conversation_storage.contracts",
                "story_writing_agents.conversation_storage.filesystem_archive",
                "story_writing_agents.conversation_storage.provisioning",
                "story_writing_agents.conversation_storage.sqlite",
                "story_writing_agents.workspace.core",
            },
            "story_writing_agents.conversation_storage.sqlite_database": {
                "story_writing_agents.workspace.core",
            },
            "story_writing_agents.conversation_storage.sqlite": {
                "story_writing_agents.conversation_literal_search",
                "story_writing_agents.conversation_search",
                "story_writing_agents.conversation_storage.archive",
                "story_writing_agents.conversation_storage.composition",
                "story_writing_agents.conversation_storage.contracts",
                "story_writing_agents.conversation_storage.sqlite_database",
                "story_writing_agents.conversation_storage.sqlite_search_index",
                "story_writing_agents.conversations",
                "story_writing_agents.conversations.events",
                "story_writing_agents.errors",
                "story_writing_agents.workspace.core",
            },
        }
        for source, expected in changed_storage_dependencies.items():
            with self.subTest(storage_source=source):
                self.assertEqual(
                    {target for edge_source, target in edges if edge_source == source},
                    expected,
                )
        self.assertEqual(
            {
                source
                for source, target in edges
                if target == "story_writing_agents.workspace.core"
            },
            {
                "story_writing_agents.conversation_storage.composition",
                "story_writing_agents.conversation_storage.filesystem",
                "story_writing_agents.conversation_storage.filesystem_archive",
                "story_writing_agents.conversation_storage.migration",
                "story_writing_agents.conversation_storage.provisioning",
                "story_writing_agents.conversation_storage.selection",
                "story_writing_agents.conversation_storage.sqlite",
                "story_writing_agents.conversation_storage.sqlite_database",
                "story_writing_agents.workspace",
            },
        )
        self.assertEqual(
            {
                source
                for source, target in edges
                if target == "story_writing_agents.conversation_storage.filesystem"
            },
            {
                "story_writing_agents.conversation_storage.composition",
                "story_writing_agents.conversation_storage.filesystem_archive",
                "story_writing_agents.workspace",
            },
        )
        self.assertFalse(
            {
                source
                for source, target in edges
                if source.startswith("story_writing_agents.conversation_storage.")
                and target == "story_writing_agents.workspace"
            }
        )
        self.assertEqual(
            _generic_importers(modules),
            {"story_writing_agents.generic_rag_adapter"},
        )
        self.assertEqual(
            {
                target
                for source, target in edges
                if source == "story_writing_agents.conversation_rag"
            },
            set(),
        )
        self.assertEqual(
            {
                target
                for source, target in edges
                if source == "story_writing_agents.generic_rag_adapter"
            },
            {"story_writing_agents.conversation_rag"},
        )
        self.assertEqual(
            {
                target
                for source, target in edges
                if source == "story_writing_agents.writing_artifacts.change_sets"
            },
            {"story_writing_agents.writing_artifacts.catalog"},
        )
        self.assertEqual(
            {
                target
                for source, target in edges
                if source
                == "story_writing_agents.writing_artifacts.relationship_policy"
            },
            {
                "story_writing_agents.specialist_routing",
                "story_writing_agents.writing_artifacts.catalog",
                "story_writing_agents.writing_artifacts.change_sets",
            },
        )
        self.assertIn(
            (
                "story_writing_agents.writing_artifacts",
                "story_writing_agents.writing_artifacts.change_sets",
            ),
            edges,
        )
        self.assertIn(
            (
                "story_writing_agents.writing_artifacts",
                "story_writing_agents.writing_artifacts.relationship_policy",
            ),
            edges,
        )
        self.assertEqual(
            {
                target
                for source, target in edges
                if source == "story_writing_agents.conversation_rag_local"
            },
            {
                "story_writing_agents.conversation_literal_search",
                "story_writing_agents.conversation_rag",
            },
        )
        self.assertEqual(
            {
                target
                for source, target in edges
                if source == "story_writing_agents.agent_context"
            },
            {"story_writing_agents.agent_context.accounting"},
        )
        self.assertEqual(
            {
                target
                for source, target in edges
                if source == "story_writing_agents.agent_context.accounting"
            },
            set(),
        )
        self.assertEqual(
            {
                target
                for source, target in edges
                if source == "story_writing_agents.agent_context.ledger"
            },
            {
                "story_writing_agents.agent_context.accounting",
                "story_writing_agents.agent_context.manifest",
                "story_writing_agents.agent_context.planning",
                "story_writing_agents.runtime_state_paths",
                "story_writing_agents.workspace",
            },
        )
        self.assertEqual(
            {
                target
                for source, target in edges
                if source == "story_writing_agents.agent_context.actions"
            },
            {
                "story_writing_agents.agent_context.manifest",
                "story_writing_agents.agent_context.planning",
            },
        )
        self.assertEqual(
            {
                target
                for source, target in edges
                if source == "story_writing_agents.agent_context.sources"
            },
            {
                "story_writing_agents.agent_context.actions",
                "story_writing_agents.agent_context.manifest",
                "story_writing_agents.conversations",
                "story_writing_agents.conversations.session_context",
                "story_writing_agents.errors",
                "story_writing_agents.models",
                "story_writing_agents.workspace",
                "story_writing_agents.writing_artifacts.catalog",
                "story_writing_agents.writing_artifacts.filesystem_sources",
                "story_writing_agents.writing_artifacts.references",
                "story_writing_agents.writing_artifacts.repository",
            },
        )
        self.assertEqual(
            {
                target
                for source, target in edges
                if source == "story_writing_agents.agent_context.service"
            },
            {
                "story_writing_agents.agent_context.accounting",
                "story_writing_agents.agent_context.actions",
                "story_writing_agents.agent_context.continuation",
                "story_writing_agents.agent_context.ledger",
                "story_writing_agents.agent_context.manifest",
                "story_writing_agents.agent_context.planning",
                "story_writing_agents.agent_context.sources",
                "story_writing_agents.conversations.session_context",
                "story_writing_agents.errors",
                "story_writing_agents.provider_auth_recovery",
                "story_writing_agents.workspace",
            },
        )
        self.assertIn(
            (
                "story_writing_agents.backend",
                "story_writing_agents.agent_context.service",
            ),
            edges,
        )
        self.assertEqual(
            {
                source
                for source, target in edges
                if target == "story_writing_agents.agent_context.service"
                and source.startswith("story_writing_agents.interactive_controller.")
            },
            set(),
        )
        self.assertEqual(
            {
                target
                for source, target in edges
                if source == "story_writing_agents.conversation_rag_store"
            },
            {
                "story_writing_agents.conversation_rag",
                "story_writing_agents.workspace",
            },
        )
        self.assertEqual(
            {
                target
                for source, target in edges
                if source == "story_writing_agents.conversation_rag_composition"
            },
            {
                "story_writing_agents.conversation_rag",
                "story_writing_agents.conversation_rag_local",
                "story_writing_agents.conversation_rag_runtime",
                "story_writing_agents.conversation_rag_store",
                "story_writing_agents.generic_rag_adapter",
                "story_writing_agents.workspace",
            },
        )
        self.assertEqual(
            {
                target
                for source, target in edges
                if source == "story_writing_agents.conversation_rag_cli"
            },
            {
                "story_writing_agents.config",
                "story_writing_agents.conversation_rag",
                "story_writing_agents.conversation_rag_composition",
                "story_writing_agents.conversation_rag_local",
                "story_writing_agents.conversation_rag_runtime",
                "story_writing_agents.conversation_rag_store",
                "story_writing_agents.conversation_retrieval",
                "story_writing_agents.conversation_search",
                "story_writing_agents.conversation_storage.contracts",
                "story_writing_agents.conversation_storage.selection",
                "story_writing_agents.conversations",
                "story_writing_agents.errors",
                "story_writing_agents.workspace",
            },
        )
        self.assertEqual(
            {
                target
                for source, target in edges
                if source == "story_writing_agents.conversation_rag_runtime"
            },
            {
                "story_writing_agents.conversation_literal_search",
                "story_writing_agents.conversation_rag",
                "story_writing_agents.conversation_retrieval",
                "story_writing_agents.conversation_search",
                "story_writing_agents.conversation_storage.contracts",
                "story_writing_agents.conversations",
            },
        )
        self.assertEqual(
            {
                target
                for source, target in edges
                if source == "story_writing_agents.conversation_retrieval"
            },
            {
                "story_writing_agents.agents",
                "story_writing_agents.conversation_rag",
                "story_writing_agents.conversation_search",
                "story_writing_agents.conversations",
                "story_writing_agents.errors",
                "story_writing_agents.models",
            },
        )

    def test_supported_module_surfaces_do_not_expand_the_root_facade(self) -> None:
        self.assertEqual(len(story_writing_agents.__all__), 106)
        rag_names = set().union(
            conversation_rag.__all__,
            conversation_rag_cli.__all__,
            conversation_rag_composition.__all__,
            conversation_rag_local.__all__,
            conversation_rag_runtime.__all__,
            conversation_rag_store.__all__,
        )
        self.assertTrue(rag_names.isdisjoint(story_writing_agents.__all__))
        self.assertFalse(hasattr(story_writing_agents, "compose_conversation_rag"))
        self.assertFalse(
            hasattr(story_writing_agents, "compose_local_conversation_rag")
        )
        self.assertFalse(hasattr(story_writing_agents, "ConversationRagCandidate"))
        self.assertFalse(hasattr(story_writing_agents, "LocalConversationRagEmbedder"))
        self.assertFalse(
            hasattr(story_writing_agents, "ConversationRagGenerationStore")
        )
        self.assertFalse(hasattr(story_writing_agents, "ConversationRagRuntimeService"))
        self.assertEqual(
            tuple(conversation_rag_runtime.__all__),
            (
                "CONVERSATION_RAG_RUNTIME_POLICY",
                "CONVERSATION_RAG_RUNTIME_SCHEMA_ID",
                "ConversationRagRuntimeInspection",
                "ConversationRagRuntimeInspectionStatus",
                "ConversationRagRuntimeError",
                "ConversationRagRuntimePolicy",
                "ConversationRagRuntimeService",
                "ConversationRagRuntimeState",
                "ConversationRagRuntimeStateSnapshot",
                "inspect_authorized_conversation_rag",
            ),
        )


class RagPackagingBoundaryTests(unittest.TestCase):
    def test_required_metadata_lock_and_gitlinks_share_exact_pins(self) -> None:
        with (_REPOSITORY_ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)["project"]
        self.assertEqual(project["version"], "0.1.0")
        self.assertEqual(project["dependencies"], [_JSONL_VIEWER_REQUIREMENT])
        self.assertEqual(
            project["optional-dependencies"],
            {
                "rag": [_GENERIC_RAG_REQUIREMENT],
                "viewer": [],
            },
        )

        with (_REPOSITORY_ROOT / "uv.lock").open("rb") as handle:
            lock = tomllib.load(handle)
        packages = {package["name"]: package for package in lock["package"]}
        generic = packages["generic-rag"]
        viewer = packages["jsonl-viewer"]
        self.assertEqual(generic["version"], "0.1.0")
        self.assertEqual(
            generic["source"]["git"],
            f"{_GENERIC_RAG_REPOSITORY}?rev={_GENERIC_RAG_PIN}#{_GENERIC_RAG_PIN}",
        )
        self.assertEqual(viewer["version"], _JSONL_VIEWER_VERSION)
        self.assertEqual(
            viewer["source"]["git"],
            f"{_JSONL_VIEWER_REPOSITORY}?rev={_JSONL_VIEWER_PIN}#{_JSONL_VIEWER_PIN}",
        )
        story = packages["story-writing-agents"]
        self.assertEqual(story["version"], "0.1.0")
        self.assertEqual(story["dependencies"], [{"name": "jsonl-viewer"}])
        self.assertEqual(
            story["optional-dependencies"],
            {"rag": [{"name": "generic-rag"}]},
        )
        self.assertEqual(
            story["metadata"]["requires-dist"],
            [
                {
                    "name": "generic-rag",
                    "marker": "extra == 'rag'",
                    "git": f"{_GENERIC_RAG_REPOSITORY}?rev={_GENERIC_RAG_PIN}",
                },
                {
                    "name": "jsonl-viewer",
                    "git": f"{_JSONL_VIEWER_REPOSITORY}?rev={_JSONL_VIEWER_PIN}",
                },
            ],
        )
        self.assertEqual(story["metadata"]["provides-extras"], ["rag", "viewer"])

        submodules = configparser.ConfigParser()
        submodules.read(_REPOSITORY_ROOT / ".gitmodules", encoding="utf-8")
        generic_section = 'submodule "vendor/generic-rag"'
        viewer_section = 'submodule "vendor/jsonl-viewer"'
        self.assertEqual(submodules.sections(), [generic_section, viewer_section])
        self.assertEqual(
            submodules[generic_section]["path"],
            "vendor/generic-rag",
        )
        self.assertEqual(
            submodules[generic_section]["url"],
            _GENERIC_RAG_REPOSITORY,
        )
        self.assertEqual(
            submodules[viewer_section]["path"],
            "vendor/jsonl-viewer",
        )
        self.assertEqual(
            submodules[viewer_section]["url"],
            _JSONL_VIEWER_REPOSITORY,
        )
        self.assertEqual(
            (_REPOSITORY_ROOT / "MANIFEST.in").read_text(encoding="utf-8"),
            "prune vendor/jsonl-viewer\n",
        )
        _assert_effective_gitlink(self, "vendor/generic-rag")
        _assert_effective_gitlink(self, "vendor/jsonl-viewer")

    @unittest.skipUnless(
        importlib.util.find_spec("setuptools") is not None,
        "local artifact verification requires setuptools",
    )
    def test_temporary_base_and_optional_artifacts_exclude_vendored_source(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            story_project = root / "story-project"
            generic_project = root / "generic-project"
            viewer_project = root / "viewer-project"
            _copy_build_input(_REPOSITORY_ROOT, story_project, vendor=True)
            _export_gitlink_project(
                "vendor/generic-rag",
                generic_project,
            )
            _export_gitlink_project(
                "vendor/jsonl-viewer",
                viewer_project,
            )
            story_distribution = root / "story-dist"
            generic_distribution = root / "generic-dist"
            viewer_distribution = root / "viewer-dist"
            story_distribution.mkdir()
            generic_distribution.mkdir()
            viewer_distribution.mkdir()
            story_wheel, story_sdist = _build_local_artifacts(
                story_project,
                story_distribution,
            )
            generic_wheel, _generic_sdist = _build_local_artifacts(
                generic_project,
                generic_distribution,
            )
            viewer_wheel, _viewer_sdist = _build_local_artifacts(
                viewer_project,
                viewer_distribution,
            )

            with (viewer_project / "pyproject.toml").open("rb") as handle:
                viewer_project_metadata = tomllib.load(handle)["project"]
            self.assertEqual(viewer_project_metadata["version"], _JSONL_VIEWER_VERSION)
            self.assertEqual(viewer_project_metadata["license"], "MIT")
            self.assertEqual(viewer_project_metadata["requires-python"], ">=3.11")
            self.assertEqual(viewer_project_metadata["dependencies"], [])
            self.assertEqual(
                (viewer_project / "LICENSE")
                .read_text(encoding="utf-8")
                .splitlines()[0],
                "MIT License",
            )
            external_imports: set[str] = set()
            for source_path in (viewer_project / "src" / "jsonl_viewer").glob("*.py"):
                tree = ast.parse(
                    source_path.read_text(encoding="utf-8"),
                    filename=str(source_path),
                )
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        external_imports.update(
                            alias.name.partition(".")[0] for alias in node.names
                        )
                    elif isinstance(node, ast.ImportFrom) and node.level == 0:
                        external_imports.add((node.module or "").partition(".")[0])
            self.assertTrue(external_imports <= sys.stdlib_module_names)
            self.assertNotIn("story_writing_agents", external_imports)

            with zipfile.ZipFile(viewer_wheel) as archive:
                viewer_metadata_name = next(
                    name
                    for name in archive.namelist()
                    if name.endswith(".dist-info/METADATA")
                )
                viewer_metadata = email.parser.BytesParser().parsebytes(
                    archive.read(viewer_metadata_name)
                )
            self.assertEqual(viewer_metadata["License-Expression"], "MIT")
            self.assertEqual(viewer_metadata["Version"], _JSONL_VIEWER_VERSION)
            self.assertEqual(viewer_metadata["Requires-Python"], ">=3.11")
            self.assertIsNone(viewer_metadata["Requires-Dist"])

            with zipfile.ZipFile(story_wheel) as archive:
                wheel_names = tuple(archive.namelist())
                metadata_name = next(
                    name for name in wheel_names if name.endswith(".dist-info/METADATA")
                )
                metadata = email.parser.BytesParser().parsebytes(
                    archive.read(metadata_name)
                )
            with tarfile.open(story_sdist, "r:gz") as archive:
                sdist_names = tuple(archive.getnames())
            self.assertEqual(
                metadata.get_all("Provides-Extra"),
                ["rag", "viewer"],
            )
            requires = metadata.get_all("Requires-Dist") or []
            self.assertEqual(len(requires), 2)
            normalized_requires = []
            for requirement in requires:
                name, separator, reference = requirement.partition("@")
                normalized_requires.append(
                    requirement
                    if not separator
                    else f"{name.rstrip()}@ {reference.lstrip()}"
                )
            self.assertTrue(
                any(
                    requirement.startswith(
                        f"jsonl-viewer@ git+{_JSONL_VIEWER_REPOSITORY}@{_JSONL_VIEWER_PIN}"
                    )
                    and ";" not in requirement
                    for requirement in normalized_requires
                ),
                requires,
            )
            self.assertTrue(
                any(
                    requirement.startswith(
                        f"generic-rag@ git+{_GENERIC_RAG_REPOSITORY}@{_GENERIC_RAG_PIN}"
                    )
                    and 'extra == "rag"' in requirement
                    for requirement in normalized_requires
                ),
                requires,
            )
            conversation_package_paths = (
                "story_writing_agents/conversations/__init__.py",
                "story_writing_agents/conversations/catalog.py",
                "story_writing_agents/conversations/context.py",
                "story_writing_agents/conversations/events.py",
            )
            workspace_package_paths = (
                "story_writing_agents/workspace/__init__.py",
                "story_writing_agents/workspace/core.py",
            )
            filesystem_owner_path = (
                "story_writing_agents/conversation_storage/filesystem.py"
            )
            artifact_proposals_path = "story_writing_agents/artifact_proposals.py"
            for names in (wheel_names, sdist_names):
                for package_path in conversation_package_paths:
                    self.assertTrue(
                        any(name.endswith(package_path) for name in names),
                        package_path,
                    )
                for package_path in workspace_package_paths:
                    self.assertTrue(
                        any(name.endswith(package_path) for name in names),
                        package_path,
                    )
                self.assertTrue(
                    any(name.endswith(filesystem_owner_path) for name in names),
                    filesystem_owner_path,
                )
                self.assertTrue(
                    any(name.endswith(artifact_proposals_path) for name in names),
                    artifact_proposals_path,
                )
                self.assertFalse(
                    any(
                        name.endswith("story_writing_agents/conversations.py")
                        for name in names
                    )
                )
                self.assertFalse(
                    any(
                        name.endswith("story_writing_agents/workspace.py")
                        for name in names
                    )
                )
                self.assertTrue(
                    any(
                        name.endswith("story_writing_agents/conversation_rag.py")
                        for name in names
                    )
                )
                self.assertTrue(
                    any(
                        name.endswith("story_writing_agents/conversation_rag_cli.py")
                        for name in names
                    )
                )
                self.assertTrue(
                    any(
                        name.endswith("story_writing_agents/generic_rag_adapter.py")
                        for name in names
                    )
                )
                self.assertTrue(
                    any(
                        name.endswith("story_writing_agents/conversation_rag_local.py")
                        for name in names
                    )
                )
                self.assertTrue(
                    any(
                        name.endswith("story_writing_agents/conversation_rag_store.py")
                        for name in names
                    )
                )
                self.assertTrue(
                    any(
                        name.endswith(
                            "story_writing_agents/conversation_rag_runtime.py"
                        )
                        for name in names
                    )
                )
                self.assertFalse(any("/vendor/" in f"/{name}/" for name in names))
                self.assertFalse(any("/generic_rag/" in f"/{name}/" for name in names))
                self.assertFalse(any("/jsonl_viewer/" in f"/{name}/" for name in names))

            damaged_target = root / "damaged-install"
            damaged_target.mkdir()
            with zipfile.ZipFile(story_wheel) as archive:
                archive.extractall(damaged_target)
            damaged_script = textwrap.dedent(
                f"""
                import sys
                sys.path.insert(0, {str(damaged_target)!r})
                import story_writing_agents
                import story_writing_agents.conversation_rag_cli as rag_cli
                import story_writing_agents.conversation_rag_composition as composition
                import story_writing_agents.conversation_rag_local as local
                import story_writing_agents.conversation_rag_runtime as runtime
                import story_writing_agents.conversation_rag_store as store
                import story_writing_agents.interactive_controller._exchange_policy as exchange_policy
                import story_writing_agents.interactive_controller._exchange_viewer as exchange_viewer
                import story_writing_agents.interactive_terminal._external_view as external_view
                import story_writing_agents.conversation_storage as conversation_storage
                import story_writing_agents.conversation_storage.filesystem as filesystem
                import story_writing_agents.conversations as conversations
                import story_writing_agents.conversations.context as conversation_context
                import story_writing_agents.conversations.events as conversation_events
                import story_writing_agents.workspace as workspace
                import story_writing_agents.workspace.core as workspace_core
                from story_writing_agents.agent_exchanges import AgentExchangeScopeKind
                assert len(story_writing_agents.__all__) == 104
                assert len(conversations.__all__) == 158
                assert len(conversation_events.__all__) == 120
                assert len(conversation_context.__all__) == 30
                assert conversations.CollaborationProposal is conversation_events.CollaborationProposal
                assert conversations.CollaborationEventStore is conversation_context.CollaborationEventStore
                assert conversations.CollaborationProposal.__module__ == "story_writing_agents.conversations"
                assert conversations.project_collaboration_history.__module__ == "story_writing_agents.conversations"
                assert not hasattr(story_writing_agents, "CollaborationProposal")
                assert not hasattr(story_writing_agents, "CollaborationEventStore")
                assert "__getattr__" not in vars(conversations)
                assert workspace.__all__ == [
                    "Workspace",
                    "initialize_workspace",
                    "FilesystemConversationCatalog",
                ]
                assert workspace_core.__all__ == ["Workspace", "initialize_workspace"]
                assert filesystem.__all__ == [
                    "FilesystemConversationCatalog",
                    "FilesystemCollaborationEventStore",
                ]
                for name in workspace_core.__all__:
                    value = getattr(workspace, name)
                    assert value is getattr(workspace_core, name)
                    assert value is getattr(story_writing_agents, name)
                    assert value.__module__ == "story_writing_agents.workspace"
                for name in ("FilesystemConversationCatalog",):
                    value = getattr(workspace, name)
                    assert value is getattr(filesystem, name)
                    assert value is getattr(story_writing_agents, name)
                    assert value.__module__ == filesystem.__name__
                    assert not hasattr(workspace_core, name)
                    assert name not in conversation_storage.__all__
                    assert not hasattr(conversation_storage, name)
                direct_only_name = "FilesystemCollaborationEventStore"
                direct_only_value = getattr(filesystem, direct_only_name)
                assert direct_only_value.__module__ == filesystem.__name__
                for boundary in (
                    story_writing_agents,
                    workspace,
                    workspace_core,
                    conversation_storage,
                ):
                    assert direct_only_name not in boundary.__all__
                    assert not hasattr(boundary, direct_only_name)
                for name in (
                    "open_workspace_root_directory",
                    "verify_workspace_root_directory",
                    "WORKSPACE_DIRECTORIES",
                ):
                    assert getattr(workspace, name) is getattr(workspace_core, name)
                    assert name not in workspace.__all__
                    assert name not in story_writing_agents.__all__
                    assert not hasattr(story_writing_agents, name)
                assert not hasattr(workspace, "os")
                assert "__getattr__" not in vars(workspace)
                assert "__getattr__" not in vars(filesystem)
                assert filesystem.FilesystemConversationCatalog.__init__.__globals__ is filesystem.__dict__
                assert filesystem.FilesystemCollaborationEventStore.__init__.__globals__ is filesystem.__dict__
                assert len(rag_cli.__all__) == 7
                assert len(composition.__all__) == 10
                assert len(local.__all__) == 5
                assert len(runtime.__all__) == 10
                assert len(store.__all__) == 8
                assert runtime.CONVERSATION_RAG_RUNTIME_SCHEMA_ID == "story-collaboration-rag-readable-event-v2"
                assert local.LocalConversationRagEmbedder().identity.dimensions == 256
                assert store.CONVERSATION_RAG_STORE_SCHEMA_ID == "story-conversation-rag-store-v1"
                assert not any(
                    name == "generic_rag" or name.startswith("generic_rag.")
                    for name in sys.modules
                )
                assert not any(
                    name == "jsonl_viewer" or name.startswith("jsonl_viewer.")
                    for name in sys.modules
                )
                assert len(exchange_policy.__all__) == 7
                assert exchange_viewer.__all__ == ["AgentExchangeViewerError", "prepare_agent_exchange_view"]
                assert external_view.__all__ == ["ExternalViewOperation", "ExternalViewerHost"]
                try:
                    exchange_viewer.prepare_agent_exchange_view(
                        b"",
                        session_id="session_1",
                        scope_kind=AgentExchangeScopeKind.CONVERSATION,
                        scope_id="conversation_1",
                        scope_subject="Subject",
                        agent_id="lead_orchestrator",
                    )
                except exchange_viewer.AgentExchangeViewerError as failure:
                    assert failure.code == "installation_required"
                    assert str(failure) == (
                        "Required agent exchange viewer is unavailable. Repair or "
                        "reinstall Story and retry /exchanges."
                    )
                else:
                    raise AssertionError("damaged install unexpectedly loaded jsonl_viewer")
                """
            )
            damaged = subprocess.run(
                [sys.executable, "-I", "-S", "-W", "error", "-c", damaged_script],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                damaged.returncode,
                0,
                f"stdout:\n{damaged.stdout}\nstderr:\n{damaged.stderr}",
            )

            optional_target = root / "optional-install"
            optional_target.mkdir()
            for wheel in (story_wheel, generic_wheel, viewer_wheel):
                with zipfile.ZipFile(wheel) as archive:
                    archive.extractall(optional_target)
            optional_script = textwrap.dedent(
                f"""
                import sys
                sys.path.insert(0, {str(optional_target)!r})
                from story_writing_agents import conversation_rag as rag
                from story_writing_agents import conversation_rag_composition as composition
                from story_writing_agents import conversation_rag_local as local
                from story_writing_agents import conversation_rag_store as store
                service = composition.compose_conversation_rag(
                    "local",
                    rag.ConversationRagCollaborators(
                        object(), object(), object(), object(), object()
                    ),
                )
                assert type(service).__name__ == "GenericRagConversationService"
                assert type(service).__module__ == "story_writing_agents.generic_rag_adapter"
                assert local.LocalConversationRagEmbedder().identity.dimensions == 256
                assert len(store.__all__) == 8
                """
            )
            optional = subprocess.run(
                [sys.executable, "-I", "-S", "-W", "error", "-c", optional_script],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                optional.returncode,
                0,
                f"stdout:\n{optional.stdout}\nstderr:\n{optional.stderr}",
            )

            viewer_target = root / "viewer-install"
            viewer_target.mkdir()
            for wheel in (story_wheel, viewer_wheel):
                with zipfile.ZipFile(wheel) as archive:
                    archive.extractall(viewer_target)
            viewer_script = textwrap.dedent(
                f"""
                import sys
                sys.path.insert(0, {str(viewer_target)!r})
                from story_writing_agents.agent_exchanges import AgentExchangeScopeKind
                from story_writing_agents.interactive_controller._exchange_viewer import prepare_agent_exchange_view
                import jsonl_viewer
                assert jsonl_viewer.__all__ == ["ViewerHost", "ViewerSpec", "view_jsonl"]
                assert not any(
                    name == "generic_rag" or name.startswith("generic_rag.")
                    for name in sys.modules
                )
                class Host:
                    def __init__(self): self.frames = []; self.closed = 0
                    def terminal_size(self): return (80, 24)
                    def color_enabled(self): return False
                    def present(self, frame): self.frames.append(frame)
                    def read_event(self): return "close"
                    def close_view(self): self.closed += 1
                host = Host()
                operation = prepare_agent_exchange_view(
                    b"",
                    session_id="session_1",
                    scope_kind=AgentExchangeScopeKind.CONVERSATION,
                    scope_id="conversation_1",
                    scope_subject="Subject",
                    agent_id="lead_orchestrator",
                )
                operation(host)
                assert host.closed == 1
                assert host.frames and "READ ONLY" in host.frames[0]
                """
            )
            viewer = subprocess.run(
                [sys.executable, "-I", "-S", "-W", "error", "-c", viewer_script],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                viewer.returncode,
                0,
                f"stdout:\n{viewer.stdout}\nstderr:\n{viewer.stderr}",
            )

    @unittest.skipUnless(
        importlib.util.find_spec("setuptools") is not None,
        "isolated install verification requires setuptools",
    )
    def test_clean_base_wheel_editable_and_compatibility_extra_installs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            story_project = root / "story-project"
            _copy_build_input(_REPOSITORY_ROOT, story_project, vendor=True)
            distribution = root / "dist"
            distribution.mkdir()
            story_wheel, _story_sdist = _build_local_artifacts(
                story_project,
                distribution,
            )
            viewer_git_directory = _gitlink_git_directory("vendor/jsonl-viewer")
            build_support = root / "build-support"
            _copy_setuptools_build_support(build_support)
            install_environment = os.environ.copy()
            for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
                install_environment.pop(name, None)
            install_environment.update(
                {
                    "GIT_ALLOW_PROTOCOL": "file",
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": (
                        f"url.file://{viewer_git_directory.as_posix()}/.insteadOf"
                    ),
                    "GIT_CONFIG_VALUE_0": _JSONL_VIEWER_REPOSITORY,
                    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                    "PIP_NO_INDEX": "1",
                    "PYTHONPATH": str(build_support),
                    "PYTHONNOUSERSITE": "1",
                }
            )
            probe = textwrap.dedent(
                f"""
                import importlib.metadata
                import json
                import sys
                from typing import get_type_hints

                mode = sys.argv[1]
                import story_writing_agents
                import story_writing_agents.conversations as conversations
                import story_writing_agents.conversations.catalog as catalog
                import story_writing_agents.conversations.context as context
                import story_writing_agents.conversations.events as events
                assert len(story_writing_agents.__all__) == 104
                assert len(conversations.__all__) == 158
                assert len(events.__all__) == 120
                assert len(context.__all__) == 30
                assert catalog.ConversationCatalog is conversations.ConversationCatalog
                assert events.ConversationEvent is conversations.ConversationEvent
                assert not hasattr(context, "ConversationEventStore")
                assert not hasattr(conversations, "ConversationEventStore")
                assert events.CollaborationProposal is conversations.CollaborationProposal
                assert context.CollaborationEventStore is conversations.CollaborationEventStore
                assert context.CollaborationHistoryProjection is conversations.CollaborationHistoryProjection
                assert not hasattr(story_writing_agents, "CollaborationProposal")
                assert not hasattr(story_writing_agents, "CollaborationEventStore")
                for name in (
                    "CollaborationProposal",
                    "CollaborationEvent",
                    "CollaborationEventBatch",
                    "CollaborationEventStore",
                    "CollaborationHistoryProjection",
                    "project_collaboration_history",
                    "validate_collaboration_candidate_batch",
                ):
                    assert getattr(conversations, name).__module__ == (
                        "story_writing_agents.conversations"
                    )
                for name in (
                    "ConversationCatalog",
                    "ConversationContextPolicy",
                    "ConversationContextWindow",
                    "ConversationEvent",
                    "ConversationEventKind",
                    "ConversationMetadata",
                    "ConversationStatus",
                    "normalize_conversation_subject",
                    "select_bounded_conversation_context",
                    "validate_conversation_context_window",
                ):
                    assert getattr(conversations, name).__module__ == (
                        "story_writing_agents.conversations"
                    )
                for name in (
                    "ConversationCatalog",
                    "ConversationContextPolicy",
                    "ConversationContextWindow",
                    "ConversationEvent",
                    "ConversationEventKind",
                    "ConversationMetadata",
                    "ConversationStatus",
                    "normalize_conversation_subject",
                ):
                    assert getattr(story_writing_agents, name) is getattr(
                        conversations,
                        name,
                    )
                assert (
                    get_type_hints(context.CollaborationEventStore.read_context)[
                        "return"
                    ]
                    is conversations.CollaborationHistoryProjection
                )
                assert not any(
                    name == "jsonl_viewer" or name.startswith("jsonl_viewer.")
                    for name in sys.modules
                )
                from story_writing_agents.interactive_commands import ActionKind, parse_interactive_line
                action = parse_interactive_line("/exchanges")
                assert action.kind is ActionKind.EXCHANGES
                assert not any(
                    name == "jsonl_viewer" or name.startswith("jsonl_viewer.")
                    for name in sys.modules
                )

                viewer_distribution = importlib.metadata.distribution("jsonl-viewer")
                direct_url = json.loads(viewer_distribution.read_text("direct_url.json"))
                assert direct_url == {{
                    "url": {_JSONL_VIEWER_REPOSITORY!r},
                    "vcs_info": {{
                        "commit_id": {_JSONL_VIEWER_PIN!r},
                        "requested_revision": {_JSONL_VIEWER_PIN!r},
                        "vcs": "git",
                    }},
                }}, direct_url
                assert viewer_distribution.version == {_JSONL_VIEWER_VERSION!r}
                assert viewer_distribution.requires in (None, [])
                assert sum(
                    distribution.metadata["Name"].lower() == "jsonl-viewer"
                    for distribution in importlib.metadata.distributions()
                ) == 1
                try:
                    importlib.metadata.distribution("generic-rag")
                except importlib.metadata.PackageNotFoundError:
                    pass
                else:
                    raise AssertionError("base viewer install unexpectedly includes RAG")

                story_distribution = importlib.metadata.distribution(
                    "story-writing-agents"
                )
                assert sorted(story_distribution.metadata.get_all("Provides-Extra")) == [
                    "rag",
                    "viewer",
                ]
                requirements = story_distribution.metadata.get_all("Requires-Dist")
                assert len(requirements) == 2
                viewer_requirements = [
                    requirement
                    for requirement in requirements
                    if requirement.lower().startswith("jsonl-viewer")
                ]
                assert len(viewer_requirements) == 1
                assert ";" not in viewer_requirements[0]
                if mode.startswith("editable"):
                    story_direct_url = json.loads(
                        story_distribution.read_text("direct_url.json")
                    )
                    assert story_direct_url["dir_info"] == {{"editable": True}}
                    assert story_direct_url["url"].startswith("file:")

                from story_writing_agents.agent_exchanges import AgentExchangeScopeKind
                from story_writing_agents.interactive_controller._exchange_viewer import prepare_agent_exchange_view
                class Host:
                    def __init__(self): self.frames = []; self.closed = 0
                    def terminal_size(self): return (80, 24)
                    def color_enabled(self): return False
                    def present(self, frame): self.frames.append(frame)
                    def read_event(self): return "close"
                    def close_view(self): self.closed += 1
                source = b'{{"timestamp":"2026-08-30T00:00:00Z","request_type":"request","content":"ok"}}\\n'
                host = Host()
                operation = prepare_agent_exchange_view(
                    source,
                    session_id="session_1",
                    scope_kind=AgentExchangeScopeKind.CONVERSATION,
                    scope_id="conversation_1",
                    scope_subject="Subject",
                    agent_id="lead_orchestrator",
                )
                operation(host)
                assert host.closed == 1
                assert host.frames and "READ ONLY" in host.frames[0]
                assert source.endswith(b"\\n")
                import jsonl_viewer
                assert jsonl_viewer.__all__ == ["ViewerHost", "ViewerSpec", "view_jsonl"]
                """
            )
            modes = (
                ("wheel", [str(story_wheel)]),
                ("editable-base", ["-e", str(story_project)]),
                ("editable-viewer-extra", ["-e", f"{story_project}[viewer]"]),
            )
            for mode, target in modes:
                with self.subTest(mode=mode):
                    environment = root / f"venv-{mode}"
                    created = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "venv",
                            str(environment),
                        ],
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(
                        created.returncode,
                        0,
                        f"stdout:\n{created.stdout}\nstderr:\n{created.stderr}",
                    )
                    python = environment / "bin" / "python"
                    installed = subprocess.run(
                        [
                            str(python),
                            "-m",
                            "pip",
                            "install",
                            "--ignore-installed",
                            "--no-build-isolation",
                            *target,
                        ],
                        env=install_environment,
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(
                        installed.returncode,
                        0,
                        f"stdout:\n{installed.stdout}\nstderr:\n{installed.stderr}",
                    )
                    checked = subprocess.run(
                        [str(python), "-m", "pip", "check"],
                        env=install_environment,
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(
                        checked.returncode,
                        0,
                        f"stdout:\n{checked.stdout}\nstderr:\n{checked.stderr}",
                    )
                    verified = subprocess.run(
                        [str(python), "-I", "-W", "error", "-c", probe, mode],
                        env=install_environment,
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(
                        verified.returncode,
                        0,
                        f"stdout:\n{verified.stdout}\nstderr:\n{verified.stderr}",
                    )

    @unittest.skipUnless(
        importlib.util.find_spec("setuptools") is not None,
        "isolated upgrade verification requires setuptools",
    )
    def test_normal_story_install_upgrades_exact_old_viewer_and_renders_nested_json(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            story_project = root / "story-project"
            _copy_build_input(_REPOSITORY_ROOT, story_project, vendor=True)
            environment = root / "venv-upgrade"
            created = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "venv",
                    str(environment),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                created.returncode,
                0,
                f"stdout:\n{created.stdout}\nstderr:\n{created.stderr}",
            )
            python = environment / "bin" / "python"
            build_support = root / "build-support"
            _copy_setuptools_build_support(build_support)
            viewer_git_directory = _gitlink_git_directory("vendor/jsonl-viewer")
            install_environment = os.environ.copy()
            for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
                install_environment.pop(name, None)
            install_environment.update(
                {
                    "GIT_ALLOW_PROTOCOL": "file",
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": (
                        f"url.file://{viewer_git_directory.as_posix()}/.insteadOf"
                    ),
                    "GIT_CONFIG_VALUE_0": _JSONL_VIEWER_REPOSITORY,
                    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                    "PIP_NO_INDEX": "1",
                    "PYTHONPATH": str(build_support),
                    "PYTHONNOUSERSITE": "1",
                }
            )
            old_requirement = (
                f"jsonl-viewer @ git+{_JSONL_VIEWER_REPOSITORY}@{_OLD_JSONL_VIEWER_PIN}"
            )
            seed_command = [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-build-isolation",
                old_requirement,
            ]
            seeded = subprocess.run(
                seed_command,
                env=install_environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                seeded.returncode,
                0,
                f"stdout:\n{seeded.stdout}\nstderr:\n{seeded.stderr}",
            )
            old_probe = textwrap.dedent(
                f"""
                import importlib.metadata
                import json
                viewer = importlib.metadata.distribution("jsonl-viewer")
                assert viewer.version == "0.1.1"
                direct_url = json.loads(viewer.read_text("direct_url.json"))
                assert direct_url == {{
                    "url": {_JSONL_VIEWER_REPOSITORY!r},
                    "vcs_info": {{
                        "commit_id": {_OLD_JSONL_VIEWER_PIN!r},
                        "requested_revision": {_OLD_JSONL_VIEWER_PIN!r},
                        "vcs": "git",
                    }},
                }}, direct_url
                """
            )
            old_probe += textwrap.dedent(
                f"""
                import sys
                sys.path.insert(0, {str(story_project / "src")!r})
                from story_writing_agents.agent_exchanges import AgentExchangeScopeKind
                from story_writing_agents.interactive_controller._exchange_viewer import AgentExchangeViewerError, prepare_agent_exchange_view
                try:
                    prepare_agent_exchange_view(
                        b"", session_id="s", scope_kind=AgentExchangeScopeKind.CONVERSATION,
                        scope_id="c", scope_subject="Subject", agent_id="lead_orchestrator",
                    )
                except AgentExchangeViewerError as failure:
                    assert failure.code == "incompatible"
                else:
                    raise AssertionError("old viewer admitted before terminal operation")
                """
            )
            old_verified = subprocess.run(
                [str(python), "-I", "-W", "error", "-c", old_probe],
                env=install_environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                old_verified.returncode,
                0,
                f"stdout:\n{old_verified.stdout}\nstderr:\n{old_verified.stderr}",
            )

            upgrade_command = [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-build-isolation",
                "-e",
                str(story_project),
            ]
            self.assertNotIn("--ignore-installed", upgrade_command)
            self.assertNotIn("--force-reinstall", upgrade_command)
            self.assertNotIn("uninstall", upgrade_command)
            upgraded = subprocess.run(
                upgrade_command,
                env=install_environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                upgraded.returncode,
                0,
                f"stdout:\n{upgraded.stdout}\nstderr:\n{upgraded.stderr}",
            )
            checked = subprocess.run(
                [str(python), "-m", "pip", "check"],
                env=install_environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                checked.returncode,
                0,
                f"stdout:\n{checked.stdout}\nstderr:\n{checked.stderr}",
            )
            upgraded_probe = textwrap.dedent(
                f"""
                import importlib.metadata
                import json

                viewer_distribution = importlib.metadata.distribution("jsonl-viewer")
                assert viewer_distribution.version == {_JSONL_VIEWER_VERSION!r}
                assert json.loads(viewer_distribution.read_text("direct_url.json")) == {{
                    "url": {_JSONL_VIEWER_REPOSITORY!r},
                    "vcs_info": {{
                        "commit_id": {_JSONL_VIEWER_PIN!r},
                        "requested_revision": {_JSONL_VIEWER_PIN!r},
                        "vcs": "git",
                    }},
                }}
                assert sum(
                    distribution.metadata["Name"].lower() == "jsonl-viewer"
                    for distribution in importlib.metadata.distributions()
                ) == 1

                from story_writing_agents.agent_exchanges import AgentExchangeScopeKind
                from story_writing_agents.interactive_controller._exchange_viewer import prepare_agent_exchange_view
                nested = {{
                    "payload": {{
                        "complete_key": "complete value",
                        "long_leaf": "x" * 5_000,
                    }},
                    "items": [{{"item_key": "item value"}}],
                }}
                content = json.dumps(
                    {{
                        "kind": "provider_response",
                        "response_text": json.dumps(
                            nested,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    }},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                source = (
                    json.dumps(
                        {{
                            "timestamp": "2026-08-30T00:00:00Z",
                            "request_type": "response",
                            "content": content,
                        }},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\\n"
                ).encode("utf-8")
                original = bytes(source)
                class Host:
                    def __init__(self): self.frames = []; self.closed = 0
                    def terminal_size(self): return (240, 40)
                    def color_enabled(self): return False
                    def present(self, frame): self.frames.append(frame)
                    def read_event(self): return "close"
                    def close_view(self): self.closed += 1
                host = Host()
                operation = prepare_agent_exchange_view(
                    source,
                    session_id="session_1",
                    scope_kind=AgentExchangeScopeKind.CONVERSATION,
                    scope_id="conversation_1",
                    scope_subject="Upgrade subject",
                    agent_id="character_custodian",
                )
                operation(host)
                assert host.closed == 1
                assert source == original
                assert host.frames
                frame = host.frames[-1]
                assert "\\x1b" not in frame
                assert '"content": [expanded JSON string ×1] {{' in frame
                assert '"response_text": [expanded JSON string ×1] {{' in frame
                assert '"payload": {{' in frame
                assert '"complete_key": "complete value"' in frame
                assert '"items": [' in frame
                assert '"item_key": "item value"' in frame
                assert r'\\"response_text\\"' not in frame
                assert r'\\"payload\\"' not in frame
                truncated_lines = [
                    line for line in frame.splitlines() if "[truncated" in line
                ]
                assert len(truncated_lines) == 1, truncated_lines
                assert (
                    '"long_leaf": [truncated 4096/5000 UTF-8 bytes] "'
                    in truncated_lines[0]
                )
                assert "JSON display 2 expanded, 0 skipped, 1 truncated" in frame
                key_lines = [
                    next(
                        index
                        for index, line in enumerate(frame.splitlines())
                        if key in line
                    )
                    for key in (
                        '"content"',
                        '"response_text"',
                        '"payload"',
                        '"complete_key"',
                        '"long_leaf"',
                        '"items"',
                        '"item_key"',
                    )
                ]
                assert len(set(key_lines)) == len(key_lines)
                """
            )
            upgraded_probe += textwrap.dedent(
                """
                searched_source = b"".join(
                    (json.dumps({"content": "개요 개요 " + str(index)}, ensure_ascii=False) + "\\n").encode()
                    for index in range(7)
                )
                class SearchHost(Host):
                    def __init__(self):
                        super().__init__()
                        self.events = iter(("text\\t/", "text\\twrong", "key\\tctrl_u", "text\\t개요", "key\\tenter", "next_match", "previous_match", "close"))
                    def read_event(self): return next(self.events)
                searched_host = SearchHost()
                prepare_agent_exchange_view(
                    searched_source, session_id="session_1",
                    scope_kind=AgentExchangeScopeKind.CONVERSATION,
                    scope_id="conversation_1", scope_subject="Upgrade subject",
                    agent_id="lead_orchestrator",
                )(searched_host)
                assert searched_host.closed == 1
                assert "1/14 occurrences • Search all text='개요'" in searched_host.frames[-1]
                assert '"content": "개요 ⟦개요⟧ 0"' in searched_host.frames[-2]
                assert '"content": "⟦개요⟧ 개요 0"' in searched_host.frames[-1]
                assert "U0015" not in searched_host.frames[-1]
                assert "wrong" not in searched_host.frames[-1]
                """
            )
            verified = subprocess.run(
                [str(python), "-I", "-W", "error", "-c", upgraded_probe],
                env=install_environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                verified.returncode,
                0,
                f"stdout:\n{verified.stdout}\nstderr:\n{verified.stderr}",
            )


if __name__ == "__main__":
    unittest.main()
