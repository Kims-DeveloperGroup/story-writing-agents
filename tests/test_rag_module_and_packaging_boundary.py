"""Optional RAG import-direction, pin, and artifact boundary verification."""

from __future__ import annotations

import ast
import configparser
import importlib.util
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
    edges: set[tuple[str, str]] = set()
    for module, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        context = (
            module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
        )
        for node in ast.walk(tree):
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
        shutil.copytree(
            source / "vendor" / "generic-rag",
            destination / "vendor" / "generic-rag",
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
        )


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
        self.assertEqual(len(modules), 88)
        self.assertEqual(len(edges), 356)
        self.assertIsNone(_cycle_witness(modules, edges))
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
                "story_writing_agents.errors",
                "story_writing_agents.models",
                "story_writing_agents.workspace",
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
                "story_writing_agents.agent_context.ledger",
                "story_writing_agents.agent_context.manifest",
                "story_writing_agents.agent_context.planning",
                "story_writing_agents.agent_context.sources",
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
                and source.startswith(
                    "story_writing_agents.interactive_controller."
                )
            },
            {"story_writing_agents.interactive_controller._runtime_policy"},
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
        self.assertEqual(len(story_writing_agents.__all__), 120)
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
    def test_optional_metadata_lock_and_gitlink_share_one_exact_pin(self) -> None:
        with (_REPOSITORY_ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)["project"]
        self.assertEqual(project["dependencies"], [])
        self.assertEqual(
            project["optional-dependencies"],
            {"rag": [_GENERIC_RAG_REQUIREMENT]},
        )

        with (_REPOSITORY_ROOT / "uv.lock").open("rb") as handle:
            lock = tomllib.load(handle)
        packages = {package["name"]: package for package in lock["package"]}
        generic = packages["generic-rag"]
        self.assertEqual(generic["version"], "0.1.0")
        self.assertEqual(
            generic["source"]["git"],
            f"{_GENERIC_RAG_REPOSITORY}?rev={_GENERIC_RAG_PIN}#{_GENERIC_RAG_PIN}",
        )
        story = packages["story-writing-agents"]
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
                }
            ],
        )
        self.assertEqual(story["metadata"]["provides-extras"], ["rag"])

        submodules = configparser.ConfigParser()
        submodules.read(_REPOSITORY_ROOT / ".gitmodules", encoding="utf-8")
        section = 'submodule "vendor/generic-rag"'
        self.assertEqual(submodules.sections(), [section])
        self.assertEqual(submodules[section]["path"], "vendor/generic-rag")
        self.assertEqual(submodules[section]["url"], _GENERIC_RAG_REPOSITORY)

        gitlink = subprocess.run(
            ["git", "ls-files", "--stage", "--", "vendor/generic-rag"],
            cwd=_REPOSITORY_ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        self.assertEqual(
            gitlink,
            f"160000 {_GENERIC_RAG_PIN} 0\tvendor/generic-rag",
        )
        submodule_head = subprocess.run(
            ["git", "-C", "vendor/generic-rag", "rev-parse", "HEAD"],
            cwd=_REPOSITORY_ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        self.assertEqual(submodule_head, _GENERIC_RAG_PIN)
        submodule_status = subprocess.run(
            [
                "git",
                "-C",
                "vendor/generic-rag",
                "status",
                "--porcelain",
                "--untracked-files=no",
            ],
            cwd=_REPOSITORY_ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        self.assertEqual(submodule_status, "")

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
            _copy_build_input(_REPOSITORY_ROOT, story_project, vendor=True)
            _copy_build_input(
                _REPOSITORY_ROOT / "vendor" / "generic-rag",
                generic_project,
                vendor=False,
            )
            story_distribution = root / "story-dist"
            generic_distribution = root / "generic-dist"
            story_distribution.mkdir()
            generic_distribution.mkdir()
            story_wheel, story_sdist = _build_local_artifacts(
                story_project,
                story_distribution,
            )
            generic_wheel, _generic_sdist = _build_local_artifacts(
                generic_project,
                generic_distribution,
            )

            with zipfile.ZipFile(story_wheel) as archive:
                wheel_names = tuple(archive.namelist())
            with tarfile.open(story_sdist, "r:gz") as archive:
                sdist_names = tuple(archive.getnames())
            for names in (wheel_names, sdist_names):
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

            base_target = root / "base-install"
            base_target.mkdir()
            with zipfile.ZipFile(story_wheel) as archive:
                archive.extractall(base_target)
            base_script = textwrap.dedent(
                f"""
                import sys
                sys.path.insert(0, {str(base_target)!r})
                import story_writing_agents
                import story_writing_agents.conversation_rag_cli as rag_cli
                import story_writing_agents.conversation_rag_composition as composition
                import story_writing_agents.conversation_rag_local as local
                import story_writing_agents.conversation_rag_runtime as runtime
                import story_writing_agents.conversation_rag_store as store
                assert len(story_writing_agents.__all__) == 120
                assert len(rag_cli.__all__) == 7
                assert len(composition.__all__) == 10
                assert len(local.__all__) == 5
                assert len(runtime.__all__) == 10
                assert len(store.__all__) == 8
                assert runtime.CONVERSATION_RAG_RUNTIME_SCHEMA_ID == "story-conversation-rag-visible-turn-v1"
                assert local.LocalConversationRagEmbedder().identity.dimensions == 256
                assert store.CONVERSATION_RAG_STORE_SCHEMA_ID == "story-conversation-rag-store-v1"
                assert not any(
                    name == "generic_rag" or name.startswith("generic_rag.")
                    for name in sys.modules
                )
                """
            )
            base = subprocess.run(
                [sys.executable, "-I", "-W", "error", "-c", base_script],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                base.returncode,
                0,
                f"stdout:\n{base.stdout}\nstderr:\n{base.stderr}",
            )

            optional_target = root / "optional-install"
            optional_target.mkdir()
            for wheel in (story_wheel, generic_wheel):
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
                [sys.executable, "-I", "-W", "error", "-c", optional_script],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                optional.returncode,
                0,
                f"stdout:\n{optional.stdout}\nstderr:\n{optional.stderr}",
            )


if __name__ == "__main__":
    unittest.main()
