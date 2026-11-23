"""Optional RAG import-direction, pin, and artifact boundary verification."""
from __future__ import annotations
from tests.module_graph_fixture import module_inventory, internal_edges
import ast
import configparser
from collections.abc import Callable, Iterator
from contextlib import nullcontext
import email.parser
import importlib.util
import io
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import tomllib
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, get_type_hints
from inspect import Parameter, signature
from story_writing_agents.lead_preparation import (
    premise_preparation,
    lead_task_control,
    specialist_advisory_values,
    artifact_review_planning,
    artifact_objective_planning,
    lead_consultation_planning,
    lead_terminal_planning,
    collaboration_response_contracts,
    collaboration_response_decoding,
    lead_material_inputs,
    outcome_decoding,
    call_rule_factory,
    lead_answer_authority,
    lead_document_nodes,
    lead_evidence_receipts,
    lead_front_values,
    authoring_source_history,
    reply_contract_values,
)
import story_writing_agents
from story_writing_agents import conversations
from story_writing_agents.conversations import artifact_revision_sources
from story_writing_agents.consensus import (
    provider_reply_mapping,
    provider_request_policy,
    authoring_qualification,
)
from story_writing_agents import authoring_admission
from story_writing_agents import backend as backend_module, backend_reply_decoding, conversation_retrieval, models
from story_writing_agents import provider_session_claim_guard, provider_session_timestamps, provider_thread_records
from story_writing_agents import provider_session_access_records, provider_session_access_transactions, provider_session_artifacts
from story_writing_agents import bound_agent_logical_invocation, provider_conversation_retrieval_loop
from story_writing_agents import provider_direct_structured_calls, provider_invocation_routing
from story_writing_agents import provider_context_invocation, provider_reference_read_lifecycle, provider_structured_invocation
from story_writing_agents import provider_context_thread_store, provider_manifest_thread_store, provider_session_lifecycle_queries
from story_writing_agents import provider_configuration_activation, provider_configuration_candidate
from story_writing_agents import backend_shutdown, provider_binding_admission
from story_writing_agents import bound_agent_request_validation, provider_legacy_thread_compatibility
from story_writing_agents import provider_session_lifecycle_operations, provider_startup_options
from story_writing_agents import provider_workspace_authority_lifecycle
from story_writing_agents import conversation_retrieval_wire, provider_direct_call_wire, provider_execution_profiles, provider_session_transitions
from story_writing_agents.errors import (
    BackendProtocolError, SessionNotFoundError, WorkspaceFormatError, WorkspaceSecurityError, WorkspaceVersionUnsupportedError,
)
from story_writing_agents.session_lifecycle import SessionAccessState, SessionDurableState, SessionLifecycleState
from story_writing_agents.workspace import Workspace
from story_writing_agents import (
    antigravity_adapter, antigravity_agent_definition, antigravity_native_output_schema,
    antigravity_native_outcome,
    antigravity_native_recovery,
    antigravity_recovery_matching, antigravity_response_decoding, antigravity_rules,
    antigravity_state_directories,
)
from story_writing_agents import codex_adapter, codex_command_preflight, codex_definition_sources, codex_invocation_files, codex_stream_observation
from story_writing_agents import codex_native_outcome
from story_writing_agents.agent_context import ContextComponentBytes
from story_writing_agents.agent_context.progress import ProviderStreamSink
from story_writing_agents.agent_context.manifest import ContextScope
from story_writing_agents.config import ConfigurationSnapshot
from story_writing_agents.provider_adapter import AgentExecutionProfile, ProviderInvocation
from story_writing_agents.lead_preparation import recommendation_preparation, recommendation_requests
from story_writing_agents.lead_preparation import recommendation_decisions
from story_writing_agents.lead_task_transitions import LeadTaskRef
from story_writing_agents import conversation_rag, conversation_rag_cli, conversation_rag_composition, conversation_rag_local, conversation_rag_runtime, conversation_rag_store




_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


_SOURCE_ROOT = _REPOSITORY_ROOT / "src" / "story_writing_agents"


_GENERIC_RAG_PIN = "c77456321c614518e420e45c033e3f71dc5363b8"


_GENERIC_RAG_REPOSITORY = "https://github.com/Kims-DeveloperGroup/generic-rag.git"


_GENERIC_RAG_REQUIREMENT = (
    f"generic-rag @ git+{_GENERIC_RAG_REPOSITORY}@{_GENERIC_RAG_PIN}")


_OLD_JSONL_VIEWER_PIN = "a38a975eda4123df11c1fa4f796fd49ab88efcee"


_JSONL_VIEWER_PIN = "8b6ac65ad3c85bf8d331f6b94567e607ba0da1e4"


_JSONL_VIEWER_VERSION = "0.9.0"


_JSONL_VIEWER_REPOSITORY = "https://github.com/Kims-DeveloperGroup/jsonl-viewer.git"


_JSONL_VIEWER_REQUIREMENT = (
    f"jsonl-viewer @ git+{_JSONL_VIEWER_REPOSITORY}@{_JSONL_VIEWER_PIN}")


_GITLINK_PATHS = {'vendor/generic-rag': _GENERIC_RAG_PIN, 'vendor/jsonl-viewer': _JSONL_VIEWER_PIN}


_VIEWER_SOURCE_ASSIGNMENT = '                source = b\'{"timestamp":"2026-08-30T00:00:00Z","request_type":"request","content":"ok"}\\n\'\n'


def _viewer_host_source(size, source_line=""):
    return f"""                class Host:
                    def __init__(self): self.frames = []; self.closed = 0
                    def terminal_size(self): return {size!r}
                    def color_enabled(self): return False
                    def present(self, frame): self.frames.append(frame)
                    def read_event(self): return "close"
                    def close_view(self): self.closed += 1
{source_line}                host = Host()
                import io, jsonl_viewer
                from story_writing_agents.interactive_terminal._external_view import ExternalTerminalIO
                active_host = host; actual_view = jsonl_viewer.view_jsonl
                def owned(source, spec, host=None, *, terminal=None, transient_only=False):
                    assert host is None and transient_only is True and isinstance(terminal, jsonl_viewer.ViewerTerminal); actual_view(source, spec, active_host)
                jsonl_viewer.view_jsonl = owned; transport = ExternalTerminalIO(io.StringIO(), io.StringIO(), lambda before_read: "", True)"""


def _locked_runtime_wheels(directory: Path) -> Path:
    script = textwrap.dedent("""
        import sys, tomllib
        from pathlib import Path
        directory = Path(sys.argv[1])
        from importlib.metadata import distribution
        from setuptools.command.bdist_wheel import WheelFile
        directory.mkdir()
        packages = tomllib.loads(Path(sys.argv[2]).read_text())["package"]
        for name in ("markdown-it-py", "mdurl"):
            installed = distribution(name)
            assert installed.version == next(item["version"] for item in packages if item["name"] == name)
            metadata = installed.read_text("WHEEL") or ""
            assert "Root-Is-Purelib: true" in metadata and "Tag: py3-none-any" in metadata
            info = next(path.parent for path in installed.files or () if path.name == "METADATA")
            with WheelFile(directory / f"{info.name.removesuffix('.dist-info')}-py3-none-any.whl", "w") as archive:
                for path in installed.files or ():
                    if ".." in path.parts or path.suffix == ".pyc" or path.name in {"RECORD", "RECORD.jws", "RECORD.p7s", "INSTALLER", "REQUESTED", "direct_url.json"}:
                        continue
                    archive.write(installed.locate_file(path), str(path))
    """)
    completed = subprocess.run(
        [sys.executable, "-W", "error", "-c", script, str(directory), str(_REPOSITORY_ROOT / "uv.lock")],
        text=True, capture_output=True, check=False,
    )
    if completed.returncode:
        raise AssertionError(completed.stderr)
    return directory


def _extract_wheels(target: Path, wheels) -> None:
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(target)


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
            destination / "vendor" / "generic-rag",)
        _export_gitlink_project(
            "vendor/jsonl-viewer",
            destination / "vendor" / "jsonl-viewer",)


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
    for metadata in (*site_packages.glob('setuptools-*.dist-info'), *site_packages.glob('setuptools-*.egg-info')):
        if metadata.is_dir():
            shutil.copytree(metadata, destination / metadata.name)


def _gitlink_git_directory(relative: str) -> Path:
    if relative not in _GITLINK_PATHS:
        raise ValueError("unsupported gitlink path")
    child = _REPOSITORY_ROOT / relative
    if (child / ".git").exists():
        return Path(subprocess.run(
            ["git", "-C", str(child), "rev-parse", "--absolute-git-dir"],
            text=True, capture_output=True, check=True).stdout.strip())
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
        """)
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script, str(distribution)],
        cwd=project,
        text=True,
        capture_output=True,
        check=False,)
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
        modules = module_inventory(_SOURCE_ROOT)
        edges = internal_edges(modules)
        ledger_owner_modules = {name for name in modules if name == "story_writing_agents.agent_context.ledger" or name.startswith("story_writing_agents.agent_context.ledger.")}
        source_owner_modules = {name for name in modules if name == "story_writing_agents.agent_context.sources" or name.startswith("story_writing_agents.agent_context.sources.")}
        invocation_owner_modules = {
            "story_writing_agents.agent_context.invocation.context_execution",
            "story_writing_agents.agent_context.invocation.invocation_session"}
        self.assertEqual(len(modules), 371)
        self.assertEqual(len(edges), 2255)
        self.assertIn(('story_writing_agents.agent_context.invocation.context_execution', 'story_writing_agents.provider_adapter'), edges)
        self.assertIn(('story_writing_agents.agent_context.invocation.provider', 'story_writing_agents.provider_adapter'), edges)
        self.assertIn(('story_writing_agents.interactive_terminal._advisory_progress', 'story_writing_agents.agent_context.contracts.events'), edges)
        self.assertIn(('story_writing_agents.interactive_terminal._core', 'story_writing_agents.agent_context.contracts.events'), edges)
        self.assertIsNone(_cycle_witness(modules, edges))
        structured_owner = "story_writing_agents.provider_structured_invocation"
        logical_invocation_owner = "story_writing_agents.bound_agent_logical_invocation"
        conversation_retrieval_owner = "story_writing_agents.provider_conversation_retrieval_loop"
        direct_structured_owner = "story_writing_agents.provider_direct_structured_calls"
        activation_owner = "story_writing_agents.provider_configuration_activation"
        request_validation_owner = "story_writing_agents.bound_agent_request_validation"
        provider_dependencies = {
            "story_writing_agents.codex_native_outcome": (
                {
                    "story_writing_agents.agent_context",
                    "story_writing_agents.codex_event_decoding",
                    "story_writing_agents.codex_invocation_files",
                    "story_writing_agents.codex_native_context",
                    "story_writing_agents.errors",
                    "story_writing_agents.provider_adapter",
                    "story_writing_agents.provider_execution_workspace",
                    "story_writing_agents.provider_process",
                },
                {"story_writing_agents.codex_adapter"},
            ),
            "story_writing_agents.antigravity_native_outcome": (
                {
                    "story_writing_agents.agent_context",
                    "story_writing_agents.errors",
                    "story_writing_agents.provider_adapter",
                    "story_writing_agents.provider_process",
                },
                {"story_writing_agents.antigravity_adapter"},
            ),
            "story_writing_agents.provider_workspace_authority_lifecycle": (
                {
                    "story_writing_agents.agent_context.invocation.provider",
                    "story_writing_agents.antigravity_adapter",
                    "story_writing_agents.errors",
                    "story_writing_agents.provider_adapter",
                    "story_writing_agents.provider_adapter_generations",
                    "story_writing_agents.provider_execution_workspace",
                    "story_writing_agents.workspace.session_artifact_binding",
                    "story_writing_agents.writing_artifacts.read_actions",
                },
                {"story_writing_agents.backend"},
            ),
            "story_writing_agents.provider_startup_options": (
                {"story_writing_agents.config", "story_writing_agents.errors"},
                {"story_writing_agents.backend"},
            ),
            "story_writing_agents.provider_legacy_thread_compatibility": (
                {
                    "story_writing_agents.errors",
                    "story_writing_agents.models",
                    "story_writing_agents.provider_adapter_generations",
                    "story_writing_agents.provider_execution_workspace",
                    "story_writing_agents.provider_message_catalog",
                },
                {"story_writing_agents.backend"},
            ),
            request_validation_owner: (
                {
                    "story_writing_agents.agents",
                    "story_writing_agents.conversation_retrieval",
                    "story_writing_agents.errors",
                    "story_writing_agents.models",
                    "story_writing_agents.provider_thread_records",
                },
                {"story_writing_agents.backend"},
            ),
            "story_writing_agents.backend_shutdown": (
                {"story_writing_agents.provider_adapter"}, {"story_writing_agents.backend"},
            ),
            "story_writing_agents.provider_binding_admission": (
                {
                    "story_writing_agents.errors",
                    "story_writing_agents.models",
                    "story_writing_agents.provider_adapter",
                    "story_writing_agents.provider_adapter_generations",
                },
                {"story_writing_agents.backend"},
            ),
            activation_owner: (
                {
                    "story_writing_agents.agents",
                    "story_writing_agents.debug_log",
                    "story_writing_agents.errors",
                    "story_writing_agents.provider_adapter",
                    "story_writing_agents.provider_adapter_generations",
                    "story_writing_agents.provider_execution_profile_observations",
                },
                {"story_writing_agents.backend"},
            ),
            "story_writing_agents.provider_configuration_candidate": (
                {
                    "story_writing_agents.agents",
                    "story_writing_agents.antigravity_adapter",
                    "story_writing_agents.config",
                    "story_writing_agents.errors",
                    "story_writing_agents.interactive_settings",
                    "story_writing_agents.provider_adapter",
                    "story_writing_agents.provider_adapter_generations",
                },
                {"story_writing_agents.backend"},
            ),
            "story_writing_agents.backend_diagnostic_events": (
                {"story_writing_agents.debug_log", "story_writing_agents.provider_adapter"},
                {"story_writing_agents.backend"},
            ),
            "story_writing_agents.antigravity_native_output_schema": (
                {"story_writing_agents.agent_context.accounting"},
                {"story_writing_agents.antigravity_adapter"},
            ),
            "story_writing_agents.provider_invocation_routing": (
                {
                    "story_writing_agents.conversation_retrieval",
                    "story_writing_agents.errors",
                    "story_writing_agents.models",
                    "story_writing_agents.provider_adapter",
                    "story_writing_agents.provider_execution_workspace",
                    "story_writing_agents.writing_artifacts.read_actions",
                },
                {"story_writing_agents.backend"},
            ),
            direct_structured_owner: (
                {
                    "story_writing_agents.agent_context",
                    "story_writing_agents.backend_reply_decoding",
                    "story_writing_agents.errors",
                    "story_writing_agents.models",
                    "story_writing_agents.provider_direct_call_wire",
                    "story_writing_agents.provider_execution_workspace",
                    "story_writing_agents.writing_artifacts.read_actions",
                },
                {"story_writing_agents.backend"},
            ),
            conversation_retrieval_owner: (
                {
                    "story_writing_agents.agent_context",
                    "story_writing_agents.agent_context.invocation.provider",
                    "story_writing_agents.backend_reply_decoding",
                    "story_writing_agents.conversation_retrieval",
                    "story_writing_agents.conversation_retrieval_wire",
                    "story_writing_agents.errors",
                    "story_writing_agents.models",
                    "story_writing_agents.provider_execution_workspace",
                    "story_writing_agents.provider_manifest_thread_store",
                    "story_writing_agents.provider_message_catalog",
                    "story_writing_agents.provider_reference_read_lifecycle",
                    "story_writing_agents.provider_request_mapping",
                    "story_writing_agents.provider_retrieval_handler_outcomes",
                    "story_writing_agents.reply_trace",
                    "story_writing_agents.workspace",
                    "story_writing_agents.writing_artifacts.read_actions",
                },
                {"story_writing_agents.backend"},
            ),
            logical_invocation_owner: (
                {
                    "story_writing_agents.agents",
                    "story_writing_agents.conversation_retrieval",
                    "story_writing_agents.debug_log",
                    "story_writing_agents.errors",
                    "story_writing_agents.models",
                    "story_writing_agents.provider_execution_workspace",
                    "story_writing_agents.provider_structured_invocation",
                    "story_writing_agents.provider_thread_records",
                    "story_writing_agents.workspace",
                    "story_writing_agents.workspace.session_artifact_binding",
                    "story_writing_agents.writing_artifacts.read_actions",
                },
                {"story_writing_agents.backend"},
            ),
            structured_owner: (
                {
                    "story_writing_agents.agent_context",
                    "story_writing_agents.agent_context.contracts.errors",
                    "story_writing_agents.agent_exchanges",
                    "story_writing_agents.agent_exchanges.capture",
                    "story_writing_agents.agent_exchanges.provider_finalization",
                    "story_writing_agents.agents",
                    "story_writing_agents.antigravity_adapter",
                    "story_writing_agents.backend_reply_decoding",
                    "story_writing_agents.conversation_retrieval",
                    "story_writing_agents.conversations.session_context",
                    "story_writing_agents.errors",
                    "story_writing_agents.models",
                    "story_writing_agents.provider_adapter",
                    "story_writing_agents.provider_adapter_generations",
                    "story_writing_agents.provider_contract_guidance",
                    "story_writing_agents.provider_error_artifacts",
                    "story_writing_agents.provider_execution_workspace",
                    "story_writing_agents.provider_invocation_accounting",
                    "story_writing_agents.provider_manifest_thread_store",
                    "story_writing_agents.provider_message_catalog",
                    "story_writing_agents.provider_schema_projection",
                    "story_writing_agents.reply_trace",
                    "story_writing_agents.structured_output_validation",
                    "story_writing_agents.workspace",
                    "story_writing_agents.writing_artifacts.read_actions",
                },
                {"story_writing_agents.backend", logical_invocation_owner},
            ),
            "story_writing_agents.provider_reference_read_lifecycle": (
                {
                    "story_writing_agents.agent_context",
                    "story_writing_agents.agent_context.invocation.provider",
                    "story_writing_agents.errors",
                    "story_writing_agents.models",
                    "story_writing_agents.provider_execution_workspace",
                    "story_writing_agents.provider_manifest_thread_store",
                    "story_writing_agents.provider_request_mapping",
                    "story_writing_agents.workspace",
                    "story_writing_agents.writing_artifacts.read_actions",
                },
                {"story_writing_agents.backend", conversation_retrieval_owner},
            ),
            "story_writing_agents.provider_context_invocation": (
                {
                    "story_writing_agents.agent_context",
                    "story_writing_agents.agent_context.contracts.errors",
                    "story_writing_agents.agent_context.invocation.provider",
                    "story_writing_agents.agent_context.manifest",
                    "story_writing_agents.agent_exchanges",
                    "story_writing_agents.agent_exchanges.capture",
                    "story_writing_agents.agent_exchanges.provider_finalization",
                    "story_writing_agents.agents",
                    "story_writing_agents.antigravity_adapter",
                    "story_writing_agents.config",
                    "story_writing_agents.conversations.session_context",
                    "story_writing_agents.errors",
                    "story_writing_agents.interactive_settings",
                    "story_writing_agents.models",
                    "story_writing_agents.provider_adapter",
                    "story_writing_agents.provider_adapter_generations",
                    "story_writing_agents.provider_auth_recovery",
                    "story_writing_agents.provider_context_thread_store",
                    "story_writing_agents.provider_execution_workspace",
                    "story_writing_agents.provider_invocation_accounting",
                    "story_writing_agents.provider_message_catalog",
                    "story_writing_agents.provider_schema_projection",
                    "story_writing_agents.provider_thread_records",
                    "story_writing_agents.workspace",
                },
                {"story_writing_agents.backend"},
            ),
            "story_writing_agents.provider_bound_context": (
                {
                    "story_writing_agents.agent_context.contracts.errors",
                    "story_writing_agents.agent_context.invocation.provider",
                    "story_writing_agents.agent_context.manifest",
                    "story_writing_agents.errors",
                    "story_writing_agents.models",
                    "story_writing_agents.provider_context_thread_store",
                    "story_writing_agents.provider_execution_workspace",
                    "story_writing_agents.workspace",
                    "story_writing_agents.workspace.session_artifact_binding",
                    "story_writing_agents.writing_artifacts.read_actions",
                },
                {"story_writing_agents.backend"},
            ),
            'story_writing_agents.provider_contract_guidance': (
                {
                    'story_writing_agents.provider_message_catalog',
                    'story_writing_agents.provider_schema_selection',
                    'story_writing_agents.structured_output_validation',
                },
                {
                    'story_writing_agents.agent_context.invocation.context_execution',
                    'story_writing_agents.agent_context.invocation.group_payload',
                    'story_writing_agents.agent_context.payload.actions',
                    'story_writing_agents.agent_context.payload.context',
                    'story_writing_agents.antigravity_agent_definition',
                    structured_owner,
                    'story_writing_agents.codex_definition_sources',
                    'story_writing_agents.lead_provider_contracts',
                    'story_writing_agents.prompts',
                    'story_writing_agents.provider_request_mapping',
                    'story_writing_agents.provider_schema_projection',
                    "story_writing_agents.backend_reply_decoding",
                },
            ),
            'story_writing_agents.provider_message_catalog': (
                set(),
                {
                    'story_writing_agents.provider_legacy_thread_compatibility',
                    'story_writing_agents.agent_context.actions',
                    'story_writing_agents.agent_context.contracts.context_values',
                    'story_writing_agents.agent_context.invocation.context_execution',
                    'story_writing_agents.agent_context.payload.actions',
                    'story_writing_agents.backend',
                    structured_owner,
                    'story_writing_agents.provider_context_invocation',
                    conversation_retrieval_owner,
                    'story_writing_agents.lead_provider_contracts',
                    'story_writing_agents.prompts',
                    'story_writing_agents.provider_contract_guidance',
                    'story_writing_agents.provider_request_mapping',
                    'story_writing_agents.provider_response_mapping',
                    'story_writing_agents.provider_schema_projection',
                    "story_writing_agents.backend_reply_decoding",
                },
            ),
            'story_writing_agents.provider_request_mapping': (
                {
                    'story_writing_agents.agent_task_projection',
                    'story_writing_agents.provider_contract_guidance',
                    'story_writing_agents.provider_message_catalog',
                    'story_writing_agents.provider_schema_selection',
                },
                {
                    'story_writing_agents.agent_context.actions',
                    'story_writing_agents.agent_context.invocation.group_payload',
                    'story_writing_agents.agent_context.payload.context',
                    conversation_retrieval_owner,
                    'story_writing_agents.conversation_retrieval_wire',
                    'story_writing_agents.provider_direct_call_wire',
                    'story_writing_agents.provider_reference_read_lifecycle',
                },
            ),
            'story_writing_agents.provider_response_mapping': (
                {
                    'story_writing_agents.provider_message_catalog',
                },
                {
                    'story_writing_agents.agent_context.contracts.bound_call',
                    'story_writing_agents.agent_context.payload.actions',
                    'story_writing_agents.backend',
                    'story_writing_agents.lead_provider_contracts',
                    'story_writing_agents.orchestrator',
                    'story_writing_agents.runtime_resources.provider_call_accounting',
                    "story_writing_agents.backend_reply_decoding",
                },
            ),
            'story_writing_agents.provider_schema_projection': (
                {
                    'story_writing_agents.provider_contract_guidance',
                    'story_writing_agents.provider_message_catalog',
                    'story_writing_agents.structured_output_validation',
                },
                {
                    'story_writing_agents.agent_context.contracts.action_schema',
                    'story_writing_agents.agent_context.contracts.bound_call',
                    'story_writing_agents.agent_context.invocation.context_execution',
                    structured_owner,
                    'story_writing_agents.provider_context_invocation',
                    'story_writing_agents.lead_preparation.reply_contract_values',
                    'story_writing_agents.lead_provider_contracts',
                    'story_writing_agents.orchestrator',
                    'story_writing_agents.conversation_retrieval_wire',
                    'story_writing_agents.provider_direct_call_wire',
                },
            ),
        }
        for owner, model, sources in (
            (conversation_retrieval_wire, "conversation_retrieval",
             {"story_writing_agents.backend", conversation_retrieval_owner}),
            (provider_direct_call_wire, "models", {"story_writing_agents.backend", direct_structured_owner}),
        ):
            self.assertEqual(
                {target for source, target in edges if source == owner.__name__},
                {f"story_writing_agents.{model}", "story_writing_agents.provider_request_mapping",
                 "story_writing_agents.provider_schema_projection"},
            )
            self.assertEqual({source for source, target in edges if target == owner.__name__},
                             sources)
        for name, (outgoing, incoming) in provider_dependencies.items():
            self.assertEqual({target for origin, target in edges if origin == name}, outgoing)
            self.assertEqual({origin for origin, target in edges if target == name}, incoming)
        self.assertNotIn("story_writing_agents.provider_wire", modules)

        conversation_dependencies = {
            "story_writing_agents.conversations": {
                "story_writing_agents.conversations.event_vocabulary",
                "story_writing_agents.document_plan",
                "story_writing_agents.editorial_scope",
                "story_writing_agents.editorial_state",
                "story_writing_agents.conversations.catalog",
                "story_writing_agents.conversations.context",
                "story_writing_agents.conversations.events",
                "story_writing_agents.git.story_commit_contracts",
                "story_writing_agents.review_contracts",
                "story_writing_agents.specialist_routing",
            },
            "story_writing_agents.conversations.catalog": {
                "story_writing_agents.errors",
            },
            "story_writing_agents.conversations.events": {
                "story_writing_agents.conversations.event_vocabulary",
                "story_writing_agents.document_plan",
                "story_writing_agents.review_contracts",
                "story_writing_agents.agent_context",
                "story_writing_agents.lead_task_transitions",
                "story_writing_agents.lead_exploration",
                "story_writing_agents.lead_premise_decisions",
            "story_writing_agents.editorial_state",
                "story_writing_agents.reply_trace",
                "story_writing_agents.specialist_routing",
                "story_writing_agents.structured_output_validation",
                "story_writing_agents.agents",
                "story_writing_agents.artifact_proposals",
                "story_writing_agents.conversations.event_value_validation",
                "story_writing_agents.git.story_commit_contracts",
                "story_writing_agents.writing_artifacts", "story_writing_agents.authoring_admission",
                "story_writing_agents.core_document_readiness",
                'story_writing_agents.agent_context.accounting',
                'story_writing_agents.agent_context.group_observation',
            },
            "story_writing_agents.conversations.context": {
                "story_writing_agents.conversations.event_vocabulary",
                "story_writing_agents.document_plan",
                "story_writing_agents.authoring_task_lifecycle",
                "story_writing_agents.review_contracts",
                "story_writing_agents.lead_task_transitions",
                "story_writing_agents.lead_exploration",
                "story_writing_agents.lead_premise_decisions",
            "story_writing_agents.editorial_state",
                "story_writing_agents.reply_trace",
                "story_writing_agents.specialist_routing",
                "story_writing_agents.structured_output_validation",
                "story_writing_agents.conversations.events",
                "story_writing_agents.errors",
                "story_writing_agents.git.story_commit_contracts",
                "story_writing_agents.writing_artifacts", "story_writing_agents.authoring_admission",
                "story_writing_agents.core_document_readiness",
                'story_writing_agents.agent_context.group_observation',
                "story_writing_agents.conversations.artifact_revision_sources",
            },
        }
        for source, expected in conversation_dependencies.items():
            with self.subTest(conversation_source=source):
                self.assertEqual(
                    {target for edge_source, target in edges if edge_source == source},
                    expected,
                )
        publication = "story_writing_agents.lead_execution.collaboration_result_publication"
        self.assertEqual({target for source, target in edges if source == publication}, {
            "story_writing_agents.collaboration_execution_results",
            "story_writing_agents.conversation_storage.contracts",
            "story_writing_agents.conversations",
            "story_writing_agents.errors",
            "story_writing_agents.lead_control_events",
            "story_writing_agents.lead_preparation",
            "story_writing_agents.lead_preparation.authoring_authority",
            "story_writing_agents.lead_preparation.reply_contract_values",
            "story_writing_agents.runtime_resources",
            "story_writing_agents.runtime_resources.collaboration_event_journal",
            "story_writing_agents.runtime_resources.generation_pin_cleanup",
        })
        self.assertEqual({source for source, target in edges if target == publication}, {"story_writing_agents.orchestrator"})
        workspace_dependencies = {
            "story_writing_agents.workspace": {
                "story_writing_agents.conversation_storage.filesystem",
                "story_writing_agents.workspace.core",
            },
            "story_writing_agents.workspace.core": {
                "story_writing_agents.conversations",
                "story_writing_agents.errors",
                "story_writing_agents.models",
                "story_writing_agents.workspace.discovery",
                "story_writing_agents.workspace.initialization",
                "story_writing_agents.workspace.root_contracts",
                "story_writing_agents.workspace.root_filesystem",
                "story_writing_agents.workspace.schema",
                "story_writing_agents.workspace.session_records",
                "story_writing_agents.writing_artifacts",
            },
            "story_writing_agents.workspace.initialization": {
                "story_writing_agents.agent_exchange_paths",
                "story_writing_agents.errors",
                "story_writing_agents.git",
                "story_writing_agents.git.session_contracts",
                "story_writing_agents.git.session_resources",
                "story_writing_agents.runtime_state_paths",
                "story_writing_agents.workspace.policy_scaffold",
                "story_writing_agents.workspace.root_contracts",
                "story_writing_agents.workspace.root_filesystem",
                "story_writing_agents.workspace.schema",
            },
            "story_writing_agents.workspace.schema": {
                "story_writing_agents.agents",
                "story_writing_agents.errors",
                "story_writing_agents.git.session_contracts",
                "story_writing_agents.workspace.policy_material",
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
        self.assertEqual({target for source, target in edges if source == "story_writing_agents.workspace.session_source_workspace"}, {
            "story_writing_agents.workspace.core",
            "story_writing_agents.workspace.session_artifact_binding",
            "story_writing_agents.workspace.session_record_validation",
            "story_writing_agents.agents",
            "story_writing_agents.errors",
            "story_writing_agents.models",
        })
        extracted_workspace_dependencies = {
            "workspace.debate_context_codec": {"conversations", "errors", "models"},
            "workspace.discovery": {
                "errors", "workspace.initialization", "workspace.root_contracts",
                "workspace.root_filesystem", "workspace.schema",
            },
            "workspace.policy_material": {"agents"},
            "workspace.policy_scaffold": {
                "config", "errors", "git", "workspace._policy_baselines_editorial",
                "workspace._policy_baselines_guidance", "workspace._policy_baselines_legacy",
                "workspace.root_filesystem", "workspace.schema",
            },
            "workspace._policy_baselines_editorial": set(),
            "workspace._policy_baselines_guidance": set(),
            "workspace._policy_baselines_legacy": set(),
            "workspace.root_contracts": {
                "errors", "git.session_contracts", "workspace.root_filesystem", "workspace.schema",
            },
            "workspace.root_filesystem": {"errors"},
            "workspace.session_artifact_binding": {
                "errors", "git", "git.repository_lock", "git.session_contracts",
                "git.session_generation", "git.session_resources", "git.story_commits", "provider_execution_workspace",
                "workspace.policy_scaffold", "workspace.root_contracts", "workspace.root_filesystem",
                "workspace.session_role_projection", "writing_artifacts.repository",
            },
            "workspace.session_records": {
                "agents", "conversations", "errors", "models",
                "workspace.debate_context_codec", "workspace.session_record_validation",
            },
            "workspace.session_resources": {
                "git.session_checkpoints", "git.session_contracts", "git.session_history",
                "git.session_generation", "git.session_resources", "git.story_commit_recovery", "workspace.policy_scaffold",
                "workspace.root_contracts",
            },
        }
        for owner, expected in extracted_workspace_dependencies.items():
            self.assertEqual(
                {
                    target.removeprefix("story_writing_agents.")
                    for source, target in edges
                    if source == f"story_writing_agents.{owner}"
                },
                expected,
                owner,
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
                "story_writing_agents.git.adapter_contracts",
                "story_writing_agents.git.adapter_execution",
                "story_writing_agents.git.contracts",
                "story_writing_agents.git.external_operation_inspection",
                "story_writing_agents.git.repository_baseline",
                "story_writing_agents.git.repository_metadata",
                "story_writing_agents.git.repository_topology",
            },
            "story_writing_agents.git.session_contracts": {
                "story_writing_agents.git.contracts",
            },
            "story_writing_agents.git.session_resources": {
                "story_writing_agents.git.contracts",
                "story_writing_agents.git.repository_lock",
                "story_writing_agents.git.session_contracts",
                "story_writing_agents.git.session_generation",
                "story_writing_agents.git.session_provisioning",
                "story_writing_agents.git.session_resource_files",
                "story_writing_agents.git.session_resource_inspection",
                "story_writing_agents.git.session_resource_repository",
                "story_writing_agents.git.session_resource_values",
            },
            "story_writing_agents.git.story_commit_candidates": {
                "story_writing_agents.git.contracts",
                "story_writing_agents.git.process",
                "story_writing_agents.git.session_contracts",
                "story_writing_agents.git.session_generation",
                "story_writing_agents.git.session_history",
                "story_writing_agents.git.session_history_contracts",
                "story_writing_agents.git.session_resources",
                "story_writing_agents.git.story_commit_contracts",
                "story_writing_agents.writing_artifacts",
                "story_writing_agents.writing_artifacts.repository",
            },
            "story_writing_agents.git.story_commits": {
                "story_writing_agents.git.contracts",
                "story_writing_agents.git.process",
                "story_writing_agents.git.repository_lock",
                "story_writing_agents.git.session_contracts",
                "story_writing_agents.git.session_generation",
                "story_writing_agents.git.session_history",
                "story_writing_agents.git.session_history_contracts",
                "story_writing_agents.git.story_commit_candidates",
                "story_writing_agents.git.story_commit_contracts",
                "story_writing_agents.git.story_commit_git",
                "story_writing_agents.git.story_commit_recovery",
                "story_writing_agents.git.story_commit_session",
            },
        }
        for source, expected in git_dependencies.items():
            context = nullcontext() if source.endswith((".story_commit_candidates", ".story_commits")) else self.subTest(git_source=source)
            with context:
                self.assertEqual({target for edge_source, target in edges if edge_source == source}, expected, source)
        extracted_git_dependencies = {
            "git.adapter_contracts": {
                "git.contracts",
            },
            "git.adapter_execution": {
                "git.adapter_contracts",
                "git.contracts",
                "git.process",
            },
            "git.external_operation_inspection": {
                "git.adapter_contracts",
                "git.contracts",
            },
            "git.repository_baseline": {
                "git.adapter_contracts",
                "git.adapter_execution",
                "git.contracts",
                "git.repository_topology",
            },
            "git.repository_metadata": {
                "git.adapter_contracts",
                "git.adapter_execution",
                "git.contracts",
                "git.external_operation_inspection",
                "git.repository_topology",
            },
            "git.repository_topology": {
                "git.adapter_contracts",
                "git.adapter_execution",
                "git.contracts",
            },
            "git.session_generation": {
                "git.process",
                "git.repository_lock",
                "git.session_contracts",
                "git.session_resource_files",
                "git.session_resource_values",
            },
            "git.session_inspection_data": {
                "git.contracts",
                "git.inspection_objects",
                "git.process",
                "git.session_contracts",
                "git.session_resource_files",
                "git.session_resource_repository",
                "git.session_resource_values",
            },
            "git.session_provisioning": {
                "git.contracts",
                "git.session_contracts",
                "git.session_resource_files",
                "git.session_resource_inspection",
                "git.session_resource_repository",
                "git.session_resource_values",
            },
            "git.session_resource_files": {
                "git.session_contracts",
                "git.session_resource_values",
            },
            "git.session_resource_inspection": {
                "git.contracts",
                "git.process",
                "git.session_contracts",
                "git.session_inspection_data",
                "git.session_resource_files",
                "git.session_resource_repository",
                "git.session_resource_values",
            },
            "git.session_resource_repository": {
                "git.contracts",
                "git.process",
                "git.repository_lock",
                "git.session_contracts",
                "git.session_resource_files",
                "git.session_resource_values",
            },
            "git.session_resource_values": {
                "git.contracts",
                "git.session_contracts",
            },
            "git.story_commit_git": {
                "git.adapter",
                "git.contracts",
                "git.process",
                "git.session_contracts",
                "git.session_history_contracts",
                "git.story_commit_candidates",
                "git.story_commit_contracts",
                "git.story_commit_session",
            },
            "git.story_commit_recovery": {
                "git.contracts",
                "git.process",
                "git.session_contracts",
                "git.session_generation",
                "git.session_history",
                "git.session_history_contracts",
                "git.session_resources",
                "git.story_commit_candidates",
                "git.story_commit_contracts",
                "git.story_commit_git",
                "git.story_commit_session",
            },
            "git.story_commit_session": {
                "git.session_contracts",
                "git.session_generation",
                "git.session_history",
                "git.session_history_contracts",
                "git.story_commit_contracts",
            },
        }
        for owner, expected in extracted_git_dependencies.items():
            actual = {
                target.removeprefix("story_writing_agents.")
                for source, target in edges if source == f"story_writing_agents.{owner}"
            }
            self.assertEqual(actual, expected, owner)
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
                "story_writing_agents.workspace.session_source_workspace",
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
            ('story_writing_agents.writing_artifacts', 'story_writing_agents.writing_artifacts.change_sets'),
            edges,
        )
        self.assertIn(
            ('story_writing_agents.writing_artifacts', 'story_writing_agents.writing_artifacts.relationship_policy'),
            edges,
        )
        self.assertEqual(
            {
                target
                for source, target in edges
                if source == "story_writing_agents.conversation_rag_local"
            },
            {'story_writing_agents.conversation_literal_search', 'story_writing_agents.conversation_rag'},
        )
        self.assertEqual(
            {
                target
                for source, target in edges
                if source == "story_writing_agents.agent_context"
            },
            {'story_writing_agents.agent_context.accounting', 'story_writing_agents.agent_context.progress'},
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
                if source in ledger_owner_modules and target not in ledger_owner_modules
            },
            {
                'story_writing_agents.agent_context.accounting',
                'story_writing_agents.agent_context.contracts.checkpoints',
                'story_writing_agents.agent_context.contracts.planning_inputs',
                'story_writing_agents.agent_context.contracts.projection',
                'story_writing_agents.agent_context.contracts.projection_accounting',
                'story_writing_agents.agent_context.manifest',
                'story_writing_agents.runtime_state_paths',
                'story_writing_agents.workspace',
            },
        )
        self.assertEqual(
            {
                target
                for source, target in edges
                if source == "story_writing_agents.agent_context.actions"
            },
            {
                'story_writing_agents.agent_context.contracts.planning_inputs',
                'story_writing_agents.agent_context.manifest',
                'story_writing_agents.provider_message_catalog',
                'story_writing_agents.provider_request_mapping',
                'story_writing_agents.provider_schema_patterns',
                'story_writing_agents.structured_output_validation',
            },
        )
        self.assertEqual(
            {
                target
                for source, target in edges
                if source in source_owner_modules and target not in source_owner_modules
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
                if source in invocation_owner_modules and target not in invocation_owner_modules
            },
            {
                'story_writing_agents.agent_context.accounting',
                'story_writing_agents.agent_context.actions',
                'story_writing_agents.agent_context.continuation',
                'story_writing_agents.agent_context.contracts.action_schema',
                'story_writing_agents.agent_context.contracts.bound_call',
                'story_writing_agents.agent_context.contracts.call_offers',
                'story_writing_agents.agent_context.contracts.checkpoints',
                'story_writing_agents.agent_context.contracts.context_values',
                'story_writing_agents.agent_context.contracts.errors',
                'story_writing_agents.agent_context.contracts.events',
                'story_writing_agents.agent_context.contracts.planning_inputs',
                'story_writing_agents.agent_context.contracts.session',
                'story_writing_agents.agent_context.group_observation',
                'story_writing_agents.agent_context.invocation.delivery',
                'story_writing_agents.agent_context.invocation.group_payload',
                'story_writing_agents.agent_context.invocation.provider',
                'story_writing_agents.agent_context.invocation.requests',
                'story_writing_agents.agent_context.ledger.checkpoint_ledger',
                'story_writing_agents.agent_context.ledger.checkpoint_wire',
                'story_writing_agents.agent_context.ledger.record_validation',
                'story_writing_agents.agent_context.ledger.record_values',
                'story_writing_agents.agent_context.manifest',
                'story_writing_agents.agent_context.payload.actions',
                'story_writing_agents.agent_context.payload.context',
                'story_writing_agents.agent_context.payload.history',
                'story_writing_agents.agent_context.planning',
                'story_writing_agents.agent_context.progress',
                'story_writing_agents.agent_context.sources.active_debate',
                'story_writing_agents.agent_context.sources.capture_values',
                'story_writing_agents.agent_context.sources.focused_conversation',
                'story_writing_agents.agent_context.sources.own_agent_state',
                'story_writing_agents.agent_context.sources.registry',
                'story_writing_agents.agent_context.sources.writing_artifact',
                'story_writing_agents.agents',
                'story_writing_agents.conversations.session_context',
                'story_writing_agents.errors',
                'story_writing_agents.lead_schema_groups',
                'story_writing_agents.provider_adapter',
                'story_writing_agents.provider_auth_recovery',
                'story_writing_agents.provider_contract_guidance',
                'story_writing_agents.provider_message_catalog',
                'story_writing_agents.provider_schema_projection',
                'story_writing_agents.structured_output_validation',
                'story_writing_agents.workspace',
            },
        )
        self.assertIn(('story_writing_agents.backend', 'story_writing_agents.agent_context.invocation.provider'), edges)
        self.assertIn(('story_writing_agents.backend', 'story_writing_agents.agent_context.contracts.errors'), edges)
        self.assertEqual(
            {
                source
                for source, target in edges
                if target in invocation_owner_modules and source not in invocation_owner_modules
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
            {'story_writing_agents.conversation_rag', 'story_writing_agents.workspace'},
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
                "story_writing_agents.reply_trace",
                "story_writing_agents.structured_output_validation",
            },
        )

        self.assertEqual(
            {target for source, target in edges
             if source == "story_writing_agents.agent_context.group_observation"},
            {"story_writing_agents.agent_context.accounting", "story_writing_agents.lead_schema_groups"},
        )
        self.assertEqual(
            {target for source, target in edges
             if source == "story_writing_agents.agent_context.group_reporting"},
            {"story_writing_agents.agent_context.accounting",
             "story_writing_agents.agent_context.group_observation", "story_writing_agents.provider_adapter"},
        )
        self.assertEqual(
            {source for source, target in edges
             if target == "story_writing_agents.agent_context.group_observation"},
            {"story_writing_agents.agent_context.group_reporting", 'story_writing_agents.agent_context.invocation.context_execution',
             "story_writing_agents.conversations.context", "story_writing_agents.conversations.events",
             "story_writing_agents.orchestrator",
             "story_writing_agents.runtime_resources.provider_call_accounting"},
        )
        self.assertEqual(
            {source for source, target in edges
             if target == "story_writing_agents.agent_context.group_reporting"},
            {"story_writing_agents.orchestrator"},
        )

        self.assertIn("story_writing_agents.review_contracts", modules)
        self.assertEqual({target for source, target in edges if source == "story_writing_agents.review_contracts"}, set())
        self.assertEqual({source for source, target in edges if target == "story_writing_agents.review_contracts"}, {
            "story_writing_agents.authoring_task_lifecycle", "story_writing_agents.conversations", "story_writing_agents.conversations.context",
            "story_writing_agents.conversations.events", "story_writing_agents.interactive_terminal._collaboration_view",
            "story_writing_agents.artifact_transactions.artifact_application_authority", "story_writing_agents.lead_preparation.outcome_decoding",
            "story_writing_agents.lead_execution.known_result_finalization",
            'story_writing_agents.lead_preparation.lead_answer_authority',
            "story_writing_agents.lead_preparation.call_rule_factory", "story_writing_agents.lead_provider_contracts", "story_writing_agents.orchestrator"})
        self.assertIn("story_writing_agents.document_plan", modules)
        self.assertEqual({target for source, target in edges if source == "story_writing_agents.document_plan"}, {
            "story_writing_agents.core_document_readiness", "story_writing_agents.editorial_scope",
            "story_writing_agents.lead_task_transitions", "story_writing_agents.specialist_routing"})
        self.assertEqual({source for source, target in edges if target == "story_writing_agents.document_plan"}, {
            'story_writing_agents.conversations',
            'story_writing_agents.conversations.context',
            'story_writing_agents.conversations.events',
            'story_writing_agents.interactive_controller._lead_policy',
            'story_writing_agents.interactive_terminal._collaboration_view',
            'story_writing_agents.lead_preparation.document_plan_selection',
            'story_writing_agents.lead_preparation.document_plan_views',
            'story_writing_agents.lead_preparation.lead_document_nodes',
            'story_writing_agents.lead_preparation.proposal_basis_values',
            'story_writing_agents.lead_preparation.response_rules',
            'story_writing_agents.orchestrator',
            'story_writing_agents.runtime_resources.collaboration_event_journal',
            'story_writing_agents.runtime_resources.provider_call_accounting',
        'story_writing_agents.lead_preparation.lead_task_control',
            'story_writing_agents.lead_preparation.artifact_objective_planning',
            'story_writing_agents.lead_preparation.lead_consultation_planning',
            'story_writing_agents.lead_preparation.call_rule_factory',
            'story_writing_agents.lead_preparation.lead_front_values',
            'story_writing_agents.lead_preparation.lead_material_inputs',
                                                                                                                 })
        self.assertIn("story_writing_agents.blocking_work", modules)
        self.assertEqual({target for source, target in edges if source == "story_writing_agents.blocking_work"}, set())
        self.assertEqual({source for source, target in edges if target == "story_writing_agents.blocking_work"},
                         {"story_writing_agents.codex_native_context", "story_writing_agents.orchestrator",
                          "story_writing_agents.lead_preparation.call_rule_factory",
                        "story_writing_agents.lead_execution.lead_step_decisions"})

        known_result_owner = "story_writing_agents.lead_execution.known_result_finalization"
        self.assertEqual({target for source, target in edges if source == known_result_owner}, {
            "story_writing_agents.agent_context.invocation.context_execution",
            "story_writing_agents.agents",
            "story_writing_agents.artifact_proposals",
            "story_writing_agents.artifact_transactions.artifact_application_authority",
            "story_writing_agents.authoring_admission",
            "story_writing_agents.conversation_storage.contracts",
            "story_writing_agents.conversations",
            "story_writing_agents.core_document_readiness",
            "story_writing_agents.errors",
            "story_writing_agents.lead_control_events",
            "story_writing_agents.lead_preparation",
            "story_writing_agents.lead_preparation.authoring_authority",
            "story_writing_agents.lead_preparation.reply_contract_values",
            "story_writing_agents.models",
            "story_writing_agents.review_contracts",
            "story_writing_agents.runtime_resources",
            "story_writing_agents.runtime_resources.collaboration_event_journal",
            "story_writing_agents.runtime_resources.provider_call_accounting",
            "story_writing_agents.specialist_routing",
            "story_writing_agents.workspace.session_artifact_binding",
        })
        self.assertEqual({source for source, target in edges if target == known_result_owner}, {"story_writing_agents.orchestrator"})

        lead_material_inputs = "story_writing_agents.lead_preparation.lead_material_inputs"
        self.assertEqual({target for source, target in edges if source == lead_material_inputs}, {
            "story_writing_agents.agent_context.actions",
            "story_writing_agents.agent_context.contracts.planning_inputs",
            "story_writing_agents.agent_context.manifest",
            "story_writing_agents.agent_context.sources.capture_values",
            "story_writing_agents.authoring_admission",
            "story_writing_agents.conversations",
            "story_writing_agents.conversations.session_context",
            "story_writing_agents.lead_preparation",
            "story_writing_agents.lead_preparation.recommendation_preparation",
            "story_writing_agents.lead_preparation.reply_contract_values",
            "story_writing_agents.lead_task_transitions",
            "story_writing_agents.specialist_routing",
            "story_writing_agents.writing_artifacts",
            "story_writing_agents.writing_artifacts.runtime_references",
            'story_writing_agents.artifact_proposals',
            'story_writing_agents.conversations.context',
            'story_writing_agents.conversations.event_vocabulary',
            'story_writing_agents.conversations.events',
            'story_writing_agents.document_plan',
            'story_writing_agents.errors',
            'story_writing_agents.lead_premise_decisions',
            'story_writing_agents.lead_preparation.authoring_authority',
            'story_writing_agents.lead_provider_contracts',
            'story_writing_agents.runtime_resources',
            'story_writing_agents.runtime_resources.collaboration_event_journal',
        })
        self.assertEqual({source for source, target in edges if target == lead_material_inputs}, {
            'story_writing_agents.lead_preparation.current_lead_evidence',
            'story_writing_agents.lead_preparation.current_lead_inputs',
            'story_writing_agents.lead_preparation.lead_source_qualification',
            'story_writing_agents.lead_preparation.review_material_validation',
            'story_writing_agents.lead_preparation.lead_front_values',
            'story_writing_agents.orchestrator',
        })
        lead_evidence_receipts = "story_writing_agents.lead_preparation.lead_evidence_receipts"
        self.assertEqual({target for source, target in edges if source == lead_evidence_receipts}, {
            "story_writing_agents.agent_context.actions",
            "story_writing_agents.agent_context.manifest",
            "story_writing_agents.conversations",
            "story_writing_agents.lead_preparation",
            "story_writing_agents.lead_preparation.reply_contract_values",
            "story_writing_agents.specialist_routing",
            'story_writing_agents.lead_preparation.authoring_authority',
            "story_writing_agents.structured_output_validation",
        })
        self.assertEqual({source for source, target in edges if target == lead_evidence_receipts}, {
            "story_writing_agents.orchestrator",
            "story_writing_agents.lead_preparation.current_lead_evidence",
        })
        lead_proposal_inputs = "story_writing_agents.lead_preparation.lead_proposal_inputs"
        self.assertEqual({target for source, target in edges if source == lead_proposal_inputs}, {
            "story_writing_agents.agents",
            "story_writing_agents.conversations",
            "story_writing_agents.lead_preparation",
            "story_writing_agents.lead_preparation.authoring_authority",
            "story_writing_agents.lead_preparation.reply_contract_values",
            "story_writing_agents.specialist_routing",
            "story_writing_agents.structured_output_validation",
        })
        self.assertEqual({source for source, target in edges if target == lead_proposal_inputs}, {
            "story_writing_agents.lead_preparation.recommendation_requests",
            "story_writing_agents.orchestrator",
            "story_writing_agents.lead_preparation.artifact_review_planning",
        })
        recommendation_requests_owner = "story_writing_agents.lead_preparation.recommendation_requests"
        self.assertEqual({target for source, target in edges if source == recommendation_requests_owner}, {
            "story_writing_agents.authoring_admission",
            "story_writing_agents.authoring_task_lifecycle",
            "story_writing_agents.conversations",
            "story_writing_agents.conversations.events",
            "story_writing_agents.core_document_readiness",
            "story_writing_agents.editorial_state",
            "story_writing_agents.lead_preparation",
            "story_writing_agents.lead_preparation.authoring_authority",
            "story_writing_agents.lead_preparation.authoring_source_history",
            "story_writing_agents.lead_preparation.lead_proposal_inputs",
            "story_writing_agents.lead_preparation.proposal_basis_values",
            "story_writing_agents.lead_preparation.reply_contract_values",
            "story_writing_agents.lead_task_transitions",
            "story_writing_agents.runtime_resources",
            "story_writing_agents.runtime_resources.collaboration_event_journal",
            "story_writing_agents.specialist_routing",
            "story_writing_agents.structured_output_validation",
        })
        self.assertEqual({source for source, target in edges if target == recommendation_requests_owner}, {"story_writing_agents.orchestrator"})
        recommendation_decisions_owner = "story_writing_agents.lead_preparation.recommendation_decisions"
        self.assertEqual({target for source, target in edges if source == recommendation_decisions_owner}, {
            "story_writing_agents.agent_context.contracts.call_offers",
            "story_writing_agents.agents",
            "story_writing_agents.conversations",
            "story_writing_agents.errors",
            "story_writing_agents.lead_preparation",
            "story_writing_agents.lead_preparation.recommendation_preparation",
            "story_writing_agents.lead_task_transitions",
            "story_writing_agents.runtime_resources",
            "story_writing_agents.runtime_resources.collaboration_event_journal",
            "story_writing_agents.writing_artifacts.authorization",
        })
        self.assertEqual({source for source, target in edges if target == recommendation_decisions_owner}, {"story_writing_agents.orchestrator"})
        recommendation_owner = "story_writing_agents.lead_preparation.recommendation_preparation"
        self.assertEqual({target for source, target in edges if source == recommendation_owner}, {
            "story_writing_agents.conversations",
            "story_writing_agents.errors",
            "story_writing_agents.lead_preparation",
            "story_writing_agents.lead_preparation.reply_contract_values",
            "story_writing_agents.lead_provider_contracts",
            "story_writing_agents.runtime_resources",
            "story_writing_agents.runtime_resources.collaboration_event_journal",
            "story_writing_agents.specialist_routing",
            "story_writing_agents.structured_output_validation",
        })
        self.assertEqual({source for source, target in edges if target == recommendation_owner}, {
            "story_writing_agents.lead_preparation.lead_material_inputs",
            "story_writing_agents.lead_preparation.recommendation_decisions",
            "story_writing_agents.orchestrator",
            "story_writing_agents.lead_preparation.artifact_objective_planning",
            "story_writing_agents.lead_preparation.artifact_review_planning",
            'story_writing_agents.lead_preparation.lead_front_values',
            "story_writing_agents.lead_preparation.lead_consultation_planning",
        })
        proposal_values_owner = "story_writing_agents.lead_preparation.proposal_basis_values"
        self.assertEqual({target for source, target in edges if source == proposal_values_owner}, {
            "story_writing_agents.agent_task_projection",
            "story_writing_agents.conversations",
            "story_writing_agents.conversations.events",
            "story_writing_agents.conversations.session_context",
            "story_writing_agents.core_document_readiness",
            "story_writing_agents.document_plan",
            "story_writing_agents.editorial_scope",
            "story_writing_agents.editorial_state",
            "story_writing_agents.errors",
            "story_writing_agents.lead_premise_decisions",
            "story_writing_agents.lead_preparation",
            "story_writing_agents.lead_preparation.authoring_authority",
            "story_writing_agents.lead_preparation.reply_contract_values",
            "story_writing_agents.runtime_resources",
            "story_writing_agents.runtime_resources.collaboration_event_journal",
            "story_writing_agents.specialist_routing",
            "story_writing_agents.writing_artifacts",
        })
        self.assertEqual({source for source, target in edges if target == proposal_values_owner}, {
            "story_writing_agents.lead_preparation.recommendation_requests",
            "story_writing_agents.orchestrator",
            "story_writing_agents.lead_preparation.artifact_objective_planning",
            "story_writing_agents.lead_preparation.artifact_review_planning",
            "story_writing_agents.lead_preparation.lead_consultation_planning",
            "story_writing_agents.lead_preparation.collaboration_response_decoding",
        })

        lead_document_nodes = "story_writing_agents.lead_preparation.lead_document_nodes"
        self.assertEqual({target for source, target in edges if source == lead_document_nodes}, {
            'story_writing_agents.conversations.events',
            'story_writing_agents.conversations.session_context',
            'story_writing_agents.core_document_readiness',
            'story_writing_agents.document_plan',
            'story_writing_agents.lead_preparation',
            'story_writing_agents.lead_preparation.authoring_authority',
            'story_writing_agents.specialist_routing',
            'story_writing_agents.lead_preparation.reply_contract_values',
            'story_writing_agents.conversations',
            'story_writing_agents.errors',
            'story_writing_agents.lead_task_transitions',
            'story_writing_agents.runtime_resources',
            'story_writing_agents.runtime_resources.collaboration_event_journal',
            'story_writing_agents.writing_artifacts',
        })
        self.assertEqual({source for source, target in edges if target == lead_document_nodes}, {"story_writing_agents.orchestrator"})

        current_evidence_owner = "story_writing_agents.lead_preparation.current_lead_evidence"
        self.assertEqual({target for source, target in edges if source == current_evidence_owner}, {
            "story_writing_agents.agent_context.invocation.context_execution",
            "story_writing_agents.agent_context.manifest",
            "story_writing_agents.agent_context.sources.capture_values",
            "story_writing_agents.agent_context.sources.focused_conversation",
            "story_writing_agents.agent_context.sources.own_agent_state",
            "story_writing_agents.agent_context.sources.writing_artifact",
            "story_writing_agents.agents",
            "story_writing_agents.artifact_proposals",
            "story_writing_agents.conversation_storage.contracts",
            "story_writing_agents.conversations",
            "story_writing_agents.conversations.events",
            "story_writing_agents.lead_preparation",
            "story_writing_agents.lead_preparation.lead_evidence_receipts",
            "story_writing_agents.lead_preparation.lead_material_inputs",
            "story_writing_agents.lead_preparation.reply_contract_values",
            "story_writing_agents.lead_provider_contracts",
            "story_writing_agents.runtime_resources",
            "story_writing_agents.runtime_resources.session_worktree_access",
            "story_writing_agents.structured_output_validation",
            "story_writing_agents.workspace.session_artifact_binding",
            "story_writing_agents.workspace.session_source_workspace",
            "story_writing_agents.writing_artifacts",
        })
        self.assertEqual({source for source, target in edges if target == current_evidence_owner}, {"story_writing_agents.orchestrator"})

        authoring_source_history_owner = 'story_writing_agents.lead_preparation.authoring_source_history'
        self.assertEqual({target for source, target in edges if source == authoring_source_history_owner}, {
            'story_writing_agents.authoring_admission',
            'story_writing_agents.conversations',
            'story_writing_agents.conversations.session_context',
            'story_writing_agents.errors',
            'story_writing_agents.lead_preparation',
            'story_writing_agents.lead_preparation.authoring_authority',
            'story_writing_agents.lead_task_transitions',
            'story_writing_agents.runtime_resources',
            'story_writing_agents.runtime_resources.collaboration_event_journal',
            'story_writing_agents.authoring_task_lifecycle',
            'story_writing_agents.lead_preparation.reply_contract_values',
            'story_writing_agents.specialist_routing',
        })
        self.assertEqual({source for source, target in edges if target == authoring_source_history_owner}, {
            "story_writing_agents.lead_preparation.recommendation_requests",
            "story_writing_agents.orchestrator",
        })

        current_lead_inputs_owner = 'story_writing_agents.lead_preparation.current_lead_inputs'
        self.assertEqual({target for source, target in edges if source == current_lead_inputs_owner}, {
            'story_writing_agents.conversations',
            'story_writing_agents.conversations.events',
            'story_writing_agents.conversations.session_context',
            'story_writing_agents.errors',
            'story_writing_agents.lead_exploration',
            'story_writing_agents.lead_premise_decisions',
            'story_writing_agents.lead_preparation',
            'story_writing_agents.lead_preparation.lead_material_inputs',
            'story_writing_agents.lead_preparation.reply_contract_values',
            'story_writing_agents.runtime_resources',
            'story_writing_agents.runtime_resources.collaboration_event_journal',
            'story_writing_agents.workspace.session_artifact_binding',
            'story_writing_agents.writing_artifacts',
        })
        self.assertEqual({source for source, target in edges if target == current_lead_inputs_owner}, {"story_writing_agents.orchestrator"})

        current_proposal_authority_owner = 'story_writing_agents.lead_preparation.current_proposal_authority'
        self.assertEqual({target for source, target in edges if source == current_proposal_authority_owner}, {
            'story_writing_agents.agent_context.invocation.context_execution',
            'story_writing_agents.agents',
            'story_writing_agents.authoring_admission',
            'story_writing_agents.conversations',
            'story_writing_agents.core_document_readiness',
            'story_writing_agents.lead_preparation',
            'story_writing_agents.lead_preparation.authoring_authority',
            'story_writing_agents.lead_preparation.reply_contract_values',
            'story_writing_agents.specialist_routing',
            'story_writing_agents.workspace.session_artifact_binding',
        })
        self.assertEqual({source for source, target in edges if target == current_proposal_authority_owner}, {"story_writing_agents.orchestrator"})

        editorial_capture_owner = 'story_writing_agents.lead_preparation.editorial_capture'
        self.assertEqual({target for source, target in edges if source == editorial_capture_owner}, {
            'story_writing_agents.conversations',
            'story_writing_agents.conversations.session_context',
            'story_writing_agents.editorial_scope',
            'story_writing_agents.editorial_state',
            'story_writing_agents.specialist_routing',
        })
        self.assertEqual({source for source, target in edges if target == editorial_capture_owner}, {"story_writing_agents.orchestrator"})

        lead_source_qualification_owner = 'story_writing_agents.lead_preparation.lead_source_qualification'
        self.assertEqual({target for source, target in edges if source == lead_source_qualification_owner}, {
            'story_writing_agents.authoring_admission',
            'story_writing_agents.conversations',
            'story_writing_agents.conversations.events',
            'story_writing_agents.conversations.session_context',
            'story_writing_agents.core_document_readiness',
            'story_writing_agents.lead_preparation',
            'story_writing_agents.lead_preparation.lead_material_inputs',
            'story_writing_agents.lead_preparation.reply_contract_values',
            'story_writing_agents.lead_task_transitions',
            'story_writing_agents.runtime_resources',
            'story_writing_agents.runtime_resources.collaboration_event_journal',
            'story_writing_agents.specialist_routing',
        })
        self.assertEqual({source for source, target in edges if target == lead_source_qualification_owner}, {"story_writing_agents.orchestrator"})

        review_material_validation_owner = 'story_writing_agents.lead_preparation.review_material_validation'
        self.assertEqual({target for source, target in edges if source == review_material_validation_owner}, {
            'story_writing_agents.artifact_proposals',
            'story_writing_agents.conversations',
            'story_writing_agents.conversations.events',
            'story_writing_agents.errors',
            'story_writing_agents.lead_exploration',
            'story_writing_agents.lead_preparation',
            'story_writing_agents.lead_preparation.authoring_authority',
            'story_writing_agents.lead_preparation.lead_material_inputs',
            'story_writing_agents.lead_preparation.reply_contract_values',
            'story_writing_agents.runtime_resources',
            'story_writing_agents.runtime_resources.collaboration_event_journal',
            'story_writing_agents.specialist_routing',
            'story_writing_agents.structured_output_validation',
            'story_writing_agents.workspace.session_artifact_binding',
        })
        self.assertEqual({source for source, target in edges if target == review_material_validation_owner}, {"story_writing_agents.orchestrator"})

        document_plan_selection_owner = 'story_writing_agents.lead_preparation.document_plan_selection'
        self.assertEqual({target for source, target in edges if source == document_plan_selection_owner}, {
            'story_writing_agents.conversations',
            'story_writing_agents.core_document_readiness',
            'story_writing_agents.document_plan',
            'story_writing_agents.lead_task_transitions',
            'story_writing_agents.runtime_resources',
            'story_writing_agents.runtime_resources.collaboration_event_journal',
            'story_writing_agents.specialist_routing',
        })
        self.assertEqual({source for source, target in edges if target == document_plan_selection_owner}, {"story_writing_agents.orchestrator", "story_writing_agents.lead_preparation.lead_consultation_planning"})

        document_plan_views_owner = 'story_writing_agents.lead_preparation.document_plan_views'
        self.assertEqual({target for source, target in edges if source == document_plan_views_owner}, {
            'story_writing_agents.authoring_admission',
            'story_writing_agents.conversations',
            'story_writing_agents.conversations.context',
            'story_writing_agents.conversations.session_context',
            'story_writing_agents.core_document_readiness',
            'story_writing_agents.document_plan',
            'story_writing_agents.specialist_routing',
        })
        self.assertEqual({source for source, target in edges if target == document_plan_views_owner}, {"story_writing_agents.orchestrator"})

        premise_preparation_owner = 'story_writing_agents.lead_preparation.premise_preparation'
        self.assertEqual({target for source, target in edges if source == premise_preparation_owner}, {
            'story_writing_agents.conversations',
            'story_writing_agents.conversations.events',
            'story_writing_agents.errors',
            'story_writing_agents.lead_premise_decisions',
            'story_writing_agents.lead_preparation',
            'story_writing_agents.lead_preparation.authoring_authority',
            'story_writing_agents.runtime_resources',
            'story_writing_agents.runtime_resources.collaboration_event_journal',
            'story_writing_agents.specialist_routing',
            'story_writing_agents.collaboration_execution_results',
            'story_writing_agents.lead_preparation.collaboration_response_decoding',
        })
        self.assertEqual({source for source, target in edges if target == premise_preparation_owner}, {"story_writing_agents.orchestrator"})
        lead_task_control_owner = 'story_writing_agents.lead_preparation.lead_task_control'
        self.assertEqual({target for source, target in edges if source == lead_task_control_owner}, {
            'story_writing_agents.conversations',
            'story_writing_agents.conversations.events',
            'story_writing_agents.document_plan',
            'story_writing_agents.lead_exploration',
            'story_writing_agents.lead_premise_decisions',
            'story_writing_agents.lead_task_transitions',
            'story_writing_agents.runtime_resources',
            'story_writing_agents.runtime_resources.collaboration_event_journal',
        })
        self.assertEqual({source for source, target in edges if target == lead_task_control_owner}, {"story_writing_agents.orchestrator"})
        specialist_advisory_values_owner = 'story_writing_agents.lead_preparation.specialist_advisory_values'
        self.assertEqual({target for source, target in edges if source == specialist_advisory_values_owner}, {
            'story_writing_agents.conversations',
            'story_writing_agents.lead_preparation',
            'story_writing_agents.lead_preparation.authoring_authority',
            'story_writing_agents.runtime_resources',
            'story_writing_agents.runtime_resources.collaboration_event_journal',
            'story_writing_agents.specialist_routing',
            'story_writing_agents.writing_artifacts',
        })
        self.assertEqual({source for source, target in edges if target == specialist_advisory_values_owner}, {"story_writing_agents.orchestrator"})

        planned_owners = (
            (
                artifact_review_planning,
                {
                    'validate_artifact_review_origin',
                    'validate_artifact_review_change_set',
                    'artifact_review_inputs',
                    'qualified_review_authoring_values',
                    'artifact_review_consultation_intent',
                    'artifact_review_candidate_records',
                    'artifact_review_publication_tail',
                },
                {
                    'story_writing_agents.artifact_proposals',
                    'story_writing_agents.authoring_admission',
                    'story_writing_agents.conversations',
                    'story_writing_agents.conversations.events',
                    'story_writing_agents.lead_preparation',
                    'story_writing_agents.lead_preparation.authoring_authority',
                    'story_writing_agents.lead_preparation.lead_proposal_inputs',
                    'story_writing_agents.lead_preparation.proposal_basis_values',
                    'story_writing_agents.lead_preparation.recommendation_preparation',
                    'story_writing_agents.lead_preparation.reply_contract_values',
                    'story_writing_agents.runtime_resources',
                    'story_writing_agents.runtime_resources.collaboration_event_journal',
                    'story_writing_agents.specialist_routing',
                    'story_writing_agents.structured_output_validation',
                    'story_writing_agents.writing_artifacts',
                },
            ),
            (
                artifact_objective_planning,
                {
                    'objective_goal_exists',
                    'prepared_document_objective',
                    'ordinary_objective_outcome',
                    'objective_facts',
                    'objective_capture_inputs',
                    'objective_document_window',
                    'clarify_unprepared_authoring_proposal',
                    'objective_drafts_for_values',
                },
                {
                    'story_writing_agents.artifact_proposals',
                    'story_writing_agents.conversations',
                    'story_writing_agents.document_plan',
                    'story_writing_agents.lead_preparation',
                    'story_writing_agents.lead_preparation.authoring_authority',
                    'story_writing_agents.lead_preparation.proposal_basis_values',
                    'story_writing_agents.lead_preparation.recommendation_preparation',
                    'story_writing_agents.lead_preparation.reply_contract_values',
                    'story_writing_agents.runtime_resources',
                    'story_writing_agents.runtime_resources.collaboration_event_journal',
                    'story_writing_agents.specialist_routing',
                    'story_writing_agents.structured_output_validation',
                    'story_writing_agents.writing_artifacts',
                },
            ),
            (
                lead_consultation_planning,
                {
                    'consultation_document_selection',
                    'consultation_document_binding',
                    'consultation_candidate_inputs',
                    'inherit_final_review_inputs',
                    'validate_consultation_plan',
                    'prepare_consultation_assignments',
                    'build_consultation_proposal',
                },
                {
                    'story_writing_agents.agents',
                    'story_writing_agents.conversations',
                    'story_writing_agents.conversations.events',
                    'story_writing_agents.document_plan',
                    'story_writing_agents.errors',
                    'story_writing_agents.lead_premise_decisions',
                    'story_writing_agents.lead_preparation',
                    'story_writing_agents.lead_preparation.authoring_authority',
                    'story_writing_agents.lead_preparation.document_plan_selection',
                    'story_writing_agents.lead_preparation.proposal_basis_values',
                    'story_writing_agents.lead_preparation.recommendation_preparation',
                    'story_writing_agents.lead_task_transitions',
                    'story_writing_agents.runtime_resources',
                    'story_writing_agents.runtime_resources.collaboration_event_journal',
                    'story_writing_agents.runtime_resources.provider_call_accounting',
                    'story_writing_agents.specialist_routing',
                },
            ),
            (
                lead_terminal_planning,
                {
                    'lead_failure_drafts',
                    'discussion_control',
                    'structured_fallback_values',
                    'structured_fallback_draft',
                },
                {
                    'story_writing_agents.agents',
                    'story_writing_agents.conversations',
                    'story_writing_agents.lead_preparation',
                    'story_writing_agents.lead_preparation.reply_contract_values',
                    'story_writing_agents.lead_task_transitions',
                    'story_writing_agents.reply_trace',
                    'story_writing_agents.runtime_resources',
                    'story_writing_agents.runtime_resources.collaboration_event_journal',
                },
            ),
            (
                provider_reply_mapping,
                {'map_consensus_provider_reply'},
                {
                    'story_writing_agents.agents',
                    'story_writing_agents.consensus.contracts',
                    'story_writing_agents.consensus.execution',
                    'story_writing_agents.models',
                    'story_writing_agents.reply_trace',
                    'story_writing_agents.specialist_routing',
                },
            ),
            (
                provider_request_policy,
                {'require_running_consensus_request', 'consensus_provider_phase', 'review_only_audit_event', 'consensus_continuation_context', 'consensus_expected_reviewed_proposal', 'consensus_agent_request', 'consensus_authored_material_required', 'consensus_report_material_required'},
                {
                    'story_writing_agents.agents',
                    'story_writing_agents.consensus.contracts',
                    'story_writing_agents.consensus.execution',
                    'story_writing_agents.consensus.projection',
                    'story_writing_agents.consensus.serialization',
                    'story_writing_agents.conversations',
                    'story_writing_agents.models',
                    'story_writing_agents.reply_trace',
                    'story_writing_agents.specialist_routing',
                },
            ),
            (
                authoring_qualification,
                {'consensus_lineage_changed', 'record_consensus_lineage_hop', 'consensus_admission_inputs', 'manuscript_admission_source', 'consensus_admission_source_fact', 'qualify_consensus_authoring_sources'},
                {
                    'story_writing_agents.authoring_admission',
                    'story_writing_agents.consensus.contracts',
                    'story_writing_agents.consensus.serialization',
                    'story_writing_agents.core_document_readiness',
                    'story_writing_agents.writing_artifacts.references',
                },
            ),
        )
        for owner, public_names, peer_names in planned_owners:
            self.assertEqual({target for source, target in edges if source == owner.__name__}, peer_names)
            self.assertEqual({source for source, target in edges if target == owner.__name__}, {"story_writing_agents.orchestrator"})
            actual_public = {name for name, value in vars(owner).items()
                             if not name.startswith("_") and callable(value)
                             and getattr(value, "__module__", None) == owner.__name__}
            self.assertEqual(actual_public, public_names)
            for name in sorted(public_names):
                parameters = signature(getattr(owner, name)).parameters
                self.assertNotIn("runtime", parameters)
                self.assertFalse(any(value.kind in (Parameter.VAR_POSITIONAL, Parameter.VAR_KEYWORD)
                                     for value in parameters.values()))

        for owner, expected_targets, expected_sources in (
            (collaboration_response_contracts, {
                'story_writing_agents.agent_context.contracts.call_offers',
                'story_writing_agents.agent_context.invocation.provider',
                'story_writing_agents.agents',
                'story_writing_agents.conversations.event_vocabulary',
                'story_writing_agents.conversations.events',
                'story_writing_agents.errors',
                'story_writing_agents.lead_preparation',
                'story_writing_agents.lead_preparation.reply_contract_values',
                'story_writing_agents.lead_provider_contracts',
                'story_writing_agents.lead_exploration',
                'story_writing_agents.lead_preparation.response_rules',
                'story_writing_agents.lead_task_transitions',
                'story_writing_agents.conversations',
                'story_writing_agents.specialist_routing',
            }, {'story_writing_agents.orchestrator'}),
            (collaboration_response_decoding, {
                'story_writing_agents.authoring_task_lifecycle',
                'story_writing_agents.agents',
                'story_writing_agents.collaboration_execution_results',
                'story_writing_agents.conversations.event_vocabulary',
                'story_writing_agents.conversations.events',
                'story_writing_agents.errors',
                'story_writing_agents.lead_premise_decisions',
                'story_writing_agents.lead_preparation',
                'story_writing_agents.lead_preparation.outcome_decoding',
                'story_writing_agents.lead_preparation.proposal_basis_values',
                'story_writing_agents.lead_preparation.reply_contract_values',
                'story_writing_agents.lead_provider_contracts',
                'story_writing_agents.specialist_routing',
                'story_writing_agents.structured_output_validation',
            }, {'story_writing_agents.orchestrator', 'story_writing_agents.lead_preparation.premise_preparation'}),
            (artifact_revision_sources, {
                'story_writing_agents.conversations.event_vocabulary',
                'story_writing_agents.conversations.events',
                'story_writing_agents.lead_task_transitions',
                'story_writing_agents.specialist_routing',
            }, {'story_writing_agents.conversations.context', 'story_writing_agents.orchestrator'}),
            (lead_answer_authority, {
                'story_writing_agents.agent_context.actions',
                'story_writing_agents.agent_context.manifest',
                'story_writing_agents.artifact_proposals',
                'story_writing_agents.authoring_admission',
                'story_writing_agents.conversations.context',
                'story_writing_agents.conversations.event_vocabulary',
                'story_writing_agents.conversations.events',
                'story_writing_agents.lead_exploration',
                'story_writing_agents.lead_premise_decisions',
                'story_writing_agents.lead_preparation',
                'story_writing_agents.lead_preparation.authoring_authority',
                'story_writing_agents.lead_preparation.reply_contract_values',
                'story_writing_agents.lead_provider_contracts',
                'story_writing_agents.lead_task_transitions',
                'story_writing_agents.review_contracts',
                'story_writing_agents.specialist_routing',
                'story_writing_agents.structured_output_validation',
            }, {'story_writing_agents.orchestrator'}),
            (lead_front_values, {
                'story_writing_agents.agent_task_projection',
                'story_writing_agents.authoring_admission',
                'story_writing_agents.conversations.context',
                'story_writing_agents.conversations.event_vocabulary',
                'story_writing_agents.conversations.events',
                'story_writing_agents.document_plan',
                'story_writing_agents.lead_preparation',
                'story_writing_agents.lead_preparation.lead_material_inputs',
                'story_writing_agents.lead_preparation.reply_contract_values',
                'story_writing_agents.lead_provider_contracts',
                'story_writing_agents.agents',
                'story_writing_agents.conversations',
                'story_writing_agents.lead_exploration',
                'story_writing_agents.lead_premise_decisions',
                'story_writing_agents.lead_preparation.recommendation_preparation',
                'story_writing_agents.lead_task_transitions',
                'story_writing_agents.specialist_routing',
            }, {'story_writing_agents.orchestrator'}),
        ):
            self.assertEqual({target for source, target in edges if source == owner.__name__}, expected_targets)
            self.assertEqual({source for source, target in edges if target == owner.__name__}, expected_sources)
        antigravity_rules_owner = "story_writing_agents.antigravity_rules"
        self.assertEqual({target for source, target in edges if source == antigravity_rules_owner}, set())
        definition_owner = antigravity_agent_definition.__name__
        directory_owner = antigravity_state_directories.__name__
        adapter_owner = antigravity_adapter.__name__
        for owner, dependencies in (
            ("story_writing_agents.antigravity_command", {
                "story_writing_agents.agent_context",
                "story_writing_agents.provider_adapter",
                "story_writing_agents.provider_execution_workspace",
            }),
            ("story_writing_agents.antigravity_invocation_options", {"story_writing_agents.provider_adapter"}),
            ("story_writing_agents.antigravity_stream_observation", {
                "story_writing_agents.agent_context.progress",
                "story_writing_agents.antigravity_response_decoding",
            }),
        ):
            self.assertEqual({target for source, target in edges if source == owner}, dependencies)
            self.assertEqual({source for source, target in edges if target == owner}, {adapter_owner})
        self.assertEqual({source for source, target in edges if target == antigravity_rules_owner}, {definition_owner})
        self.assertEqual({source for source, target in edges if target == definition_owner}, {adapter_owner})
        self.assertEqual({target for source, target in edges if source == definition_owner}, {
            "story_writing_agents.agent_context", antigravity_rules_owner, directory_owner,
            "story_writing_agents.models", "story_writing_agents.provider_adapter",
            "story_writing_agents.provider_contract_guidance", "story_writing_agents.workspace.session_role_projection",
        })
        self.assertEqual({target for source, target in edges if source == directory_owner}, set())
        self.assertEqual({source for source, target in edges if target == directory_owner}, {adapter_owner, definition_owner})
        generation_owner = "story_writing_agents.provider_generation_adapters"
        self.assertEqual({source for source, target in edges if target == generation_owner}, {"story_writing_agents.backend"})
        self.assertEqual({target for source, target in edges if source == generation_owner}, {
            "story_writing_agents.agents",
            "story_writing_agents.antigravity_adapter",
            "story_writing_agents.codex_adapter",
            "story_writing_agents.config",
            "story_writing_agents.provider_adapter",
        })
        profile_observations = "story_writing_agents.provider_execution_profile_observations"
        self.assertEqual(
            {source for source, target in edges if target == profile_observations},
            {"story_writing_agents.backend", activation_owner},
        )
        self.assertEqual(
            {target for source, target in edges if source == profile_observations},
            {
                "story_writing_agents.agents",
                "story_writing_agents.debug_log",
                "story_writing_agents.errors",
                "story_writing_agents.provider_adapter",
                "story_writing_agents.provider_adapter_generations",
            },
        )
        adapter_generations = "story_writing_agents.provider_adapter_generations"
        self.assertEqual(
            {source for source, target in edges if target == adapter_generations},
            {
                profile_observations,
                "story_writing_agents.provider_workspace_authority_lifecycle",
                "story_writing_agents.provider_legacy_thread_compatibility",
                "story_writing_agents.backend",
                activation_owner,
                "story_writing_agents.provider_binding_admission",
                "story_writing_agents.provider_configuration_candidate",
                "story_writing_agents.provider_context_invocation",
                structured_owner,
            },
        )
        self.assertEqual(
            {target for source, target in edges if source == adapter_generations},
            {
                "story_writing_agents.config",
                "story_writing_agents.errors",
                "story_writing_agents.interactive_settings",
                "story_writing_agents.provider_adapter",
            },
        )
        binding_lifetime = "story_writing_agents.provider_binding_call_lifetime"
        self.assertEqual(
            {source for source, target in edges if target == binding_lifetime},
            {"story_writing_agents.backend"},
        )
        self.assertEqual(
            {target for source, target in edges if source == binding_lifetime},
            {"story_writing_agents.errors"},
        )
        accounting_owner = "story_writing_agents.provider_invocation_accounting"
        self.assertEqual(
            {source for source, target in edges if target == accounting_owner},
            {"story_writing_agents.provider_context_invocation", structured_owner},
        )
        self.assertEqual(
            {target for source, target in edges if source == accounting_owner},
            {"story_writing_agents.agent_context", "story_writing_agents.provider_adapter"},
        )
        retrieval_owner = "story_writing_agents.provider_retrieval_handler_outcomes"
        self.assertEqual({source for source, target in edges if target == retrieval_owner}, {conversation_retrieval_owner})
        self.assertEqual({target for source, target in edges if source == retrieval_owner}, {"story_writing_agents.conversation_retrieval"})
        backend_reply_owner = "story_writing_agents.backend_reply_decoding"
        self.assertEqual(
            {target for source, target in edges if source == backend_reply_owner},
            {
                "story_writing_agents.conversation_retrieval",
                "story_writing_agents.errors",
                "story_writing_agents.models",
                "story_writing_agents.provider_contract_guidance",
                "story_writing_agents.provider_message_catalog",
                "story_writing_agents.provider_response_mapping",
                "story_writing_agents.structured_output_validation",
            },
        )
        self.assertEqual({source for source, target in edges if target == backend_reply_owner}, {"story_writing_agents.backend", structured_owner, conversation_retrieval_owner, direct_structured_owner})
        codex_stream_owner = codex_stream_observation.__name__
        self.assertEqual(
            {target for source, target in edges if source == codex_stream_owner},
            {"story_writing_agents.agent_context.progress", "story_writing_agents.codex_event_decoding"},
        )
        self.assertEqual(
            {source for source, target in edges if target == codex_stream_owner},
            {"story_writing_agents.codex_adapter"},
        )
        preflight_owner = codex_command_preflight.__name__
        self.assertEqual(
            {target for source, target in edges if source == preflight_owner},
            {
                "story_writing_agents.agents",
                "story_writing_agents.config",
                "story_writing_agents.errors",
                "story_writing_agents.git.session_contracts",
                "story_writing_agents.provider_adapter",
                "story_writing_agents.provider_execution_workspace",
            },
        )
        self.assertEqual({source for source, target in edges if target == preflight_owner},
                         {"story_writing_agents.codex_adapter"})
        exchange_finalization_owner = "story_writing_agents.agent_exchanges.provider_finalization"
        self.assertEqual(
            {target for source, target in edges if source == exchange_finalization_owner},
            {
                "story_writing_agents.agent_context.invocation.provider",
                "story_writing_agents.agent_exchanges.capture",
                "story_writing_agents.provider_adapter",
            },
        )
        self.assertEqual({source for source, target in edges if target == exchange_finalization_owner},
                         {"story_writing_agents.provider_context_invocation", structured_owner})
        codex_definition_owner = "story_writing_agents.codex_definition_sources"
        invocation_files_owner = codex_invocation_files.__name__
        self.assertEqual({target for source, target in edges if source == invocation_files_owner},
                         {"story_writing_agents.agent_context", "story_writing_agents.provider_adapter"})
        self.assertEqual({source for source, target in edges if target == invocation_files_owner},
                         {"story_writing_agents.codex_adapter", "story_writing_agents.codex_native_outcome"})
        self.assertEqual(
            {target for source, target in edges if source == codex_definition_owner},
            {
                "story_writing_agents.agent_context",
                "story_writing_agents.models",
                "story_writing_agents.provider_contract_guidance",
            },
        )
        self.assertEqual(
            {source for source, target in edges if target == codex_definition_owner},
            {"story_writing_agents.codex_adapter"},
        )
        antigravity_native_owner = antigravity_native_recovery.__name__
        self.assertEqual(
            {target for source, target in edges if source == antigravity_native_owner},
            {"story_writing_agents.antigravity_recovery_matching", "story_writing_agents.provider_adapter"},
        )
        self.assertEqual(
            {source for source, target in edges if target == antigravity_native_owner},
            {"story_writing_agents.antigravity_adapter"},
        )
        antigravity_matching_owner = "story_writing_agents.antigravity_recovery_matching"
        self.assertEqual(
            {target for source, target in edges if source == antigravity_matching_owner},
            {"story_writing_agents.antigravity_response_decoding"},
        )
        self.assertEqual(
            {source for source, target in edges if target == antigravity_matching_owner},
            {"story_writing_agents.antigravity_adapter", antigravity_native_owner},
        )
        records_owner = provider_thread_records.__name__
        stamps_owner = provider_session_timestamps.__name__
        access_records_owner = provider_session_access_records.__name__
        artifacts_owner = provider_session_artifacts.__name__
        operations_owner = provider_session_lifecycle_operations.__name__
        queries_owner = provider_session_lifecycle_queries.__name__
        transactions_owner = provider_session_access_transactions.__name__
        context_store_owner = provider_context_thread_store.__name__
        manifest_store_owner = provider_manifest_thread_store.__name__
        transitions_owner = provider_session_transitions.__name__
        execution_profiles_owner = provider_execution_profiles.__name__
        self.assertEqual(
            {target for source, target in edges if source == records_owner},
            {
                "story_writing_agents.agent_context.contracts.errors",
                "story_writing_agents.agent_context.manifest",
                "story_writing_agents.errors",
                "story_writing_agents.models",
                stamps_owner,
                "story_writing_agents.runtime_state_paths",
                "story_writing_agents.session_lifecycle",
            },
        )
        self.assertEqual({source for source, target in edges if target == records_owner},
                         {"story_writing_agents.backend", "story_writing_agents.provider_context_invocation",
                          artifacts_owner, context_store_owner, manifest_store_owner, transitions_owner, logical_invocation_owner,
                          request_validation_owner})
        self.assertEqual({target for source, target in edges if source == stamps_owner},
                         {"story_writing_agents.errors"})
        self.assertEqual({source for source, target in edges if target == stamps_owner},
                         {records_owner, access_records_owner, queries_owner, transitions_owner})
        self.assertNotIn(("story_writing_agents.backend", "story_writing_agents.runtime_state_paths"), edges)
        guard_owner = provider_session_claim_guard.__name__
        self.assertEqual({target for source, target in edges if source == guard_owner},
                         {"story_writing_agents.errors", "story_writing_agents.workspace"})
        self.assertEqual({source for source, target in edges if target == guard_owner},
                         {transactions_owner, transitions_owner})
        self.assertEqual(
            {target for source, target in edges if source == access_records_owner},
            {"story_writing_agents.errors", stamps_owner, "story_writing_agents.session_lifecycle"},
        )
        self.assertEqual(
            {source for source, target in edges if target == access_records_owner},
            {artifacts_owner, transactions_owner, transitions_owner},
        )
        self.assertEqual(
            {target for source, target in edges if source == artifacts_owner},
            {"story_writing_agents.errors", access_records_owner, records_owner, "story_writing_agents.workspace"},
        )
        self.assertEqual({source for source, target in edges if target == artifacts_owner},
                         {"story_writing_agents.backend", queries_owner, transactions_owner, manifest_store_owner, transitions_owner})
        self.assertEqual(
            {target for source, target in edges if source == queries_owner},
            {"story_writing_agents.errors", artifacts_owner, stamps_owner, "story_writing_agents.session_lifecycle",
             "story_writing_agents.workspace"},
        )
        self.assertEqual({source for source, target in edges if target == queries_owner},
                         {operations_owner, transactions_owner, transitions_owner})
        self.assertEqual(
            {target for source, target in edges if source == operations_owner},
            {"story_writing_agents.debug_log", "story_writing_agents.errors", queries_owner,
             "story_writing_agents.session_lifecycle"},
        )
        self.assertEqual({source for source, target in edges if target == operations_owner},
                         {"story_writing_agents.backend"})
        self.assertEqual(
            {target for source, target in edges if source == transactions_owner},
            {"story_writing_agents.errors", access_records_owner, artifacts_owner, guard_owner, queries_owner,
             "story_writing_agents.session_lifecycle", "story_writing_agents.workspace"},
        )
        self.assertEqual({source for source, target in edges if target == transactions_owner},
                         {"story_writing_agents.backend", transitions_owner})
        self.assertEqual(
            {target for source, target in edges if source == context_store_owner},
            {"story_writing_agents.agent_context.contracts.errors", "story_writing_agents.agent_context.manifest",
             "story_writing_agents.errors", records_owner, "story_writing_agents.workspace"},
        )
        self.assertEqual({source for source, target in edges if target == context_store_owner},
                         {"story_writing_agents.provider_bound_context", "story_writing_agents.provider_context_invocation"})
        self.assertEqual(
            {target for source, target in edges if source == manifest_store_owner},
            {"story_writing_agents.errors", "story_writing_agents.models", artifacts_owner, records_owner,
             "story_writing_agents.workspace"},
        )
        self.assertEqual({source for source, target in edges if target == manifest_store_owner},
                         {"story_writing_agents.backend", "story_writing_agents.provider_reference_read_lifecycle",
                          structured_owner, conversation_retrieval_owner})
        self.assertEqual(
            {target for source, target in edges if source == transitions_owner},
            {"story_writing_agents.errors", access_records_owner, transactions_owner, artifacts_owner, guard_owner,
             queries_owner, stamps_owner, records_owner, "story_writing_agents.session_lifecycle",
             "story_writing_agents.workspace"},
        )
        self.assertEqual({source for source, target in edges if target == transitions_owner},
                         {"story_writing_agents.backend"})
        self.assertEqual(
            {target for source, target in edges if source == execution_profiles_owner},
            {"story_writing_agents.agents", "story_writing_agents.config",
             "story_writing_agents.provider_adapter", "story_writing_agents.workspace"},
        )
        self.assertEqual({source for source, target in edges if target == execution_profiles_owner},
                         {"story_writing_agents.backend"})


    def test_supported_module_surfaces_do_not_expand_the_root_facade(self) -> None:

        self.assertEqual(len(story_writing_agents.__all__), 106)
        rag_names = set().union(
            conversation_rag.__all__,
            conversation_rag_cli.__all__,
            conversation_rag_composition.__all__,
            conversation_rag_local.__all__,
            conversation_rag_runtime.__all__,
            conversation_rag_store.__all__,)
        self.assertTrue(rag_names.isdisjoint(story_writing_agents.__all__))
        self.assertFalse(hasattr(story_writing_agents, "compose_conversation_rag"))
        self.assertFalse(
            hasattr(story_writing_agents, "compose_local_conversation_rag"))
        self.assertFalse(hasattr(story_writing_agents, "ConversationRagCandidate"))
        self.assertFalse(hasattr(story_writing_agents, "LocalConversationRagEmbedder"))
        self.assertFalse(
            hasattr(story_writing_agents, "ConversationRagGenerationStore"))
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

        self.assertEqual(tuple(recommendation_requests.__all__), (
            "RecommendationRequestPolicyResult",
            "prepare_recommendation_request_policy",
            "recommendation_basis",
        ))
        self.assertEqual(tuple(recommendation_preparation.__all__), (
            "RecommendationCapability",
            "active_recommendation_origin",
            "bind_recommendation_preparation",
            "validate_recommendation_preparation",
            "recommendation_closure_draft",
        ))
        self.assertEqual(tuple(recommendation_decisions.__all__), (
            "PreparedRecommendationDecision",
            "PreparedRecommendationRevision",
            "RecommendationDecisionClosure",
            "RecommendationFailureMatch",
            "prepare_recommendation_decision",
            "build_recommendation_decision_outcome",
            "match_recommendation_failure",
            "terminal_recommendation_failure_is_accounted",
            "build_recommendation_failure_closure",
        ))
        self.assertTrue(all(getattr(recommendation_requests, name).__module__ == recommendation_requests.__name__
                            for name in recommendation_requests.__all__))
        self.assertTrue(all(getattr(recommendation_preparation, name).__module__ == recommendation_preparation.__name__
                            for name in recommendation_preparation.__all__))
        self.assertTrue(all(getattr(recommendation_decisions, name).__module__ == recommendation_decisions.__name__
                            for name in recommendation_decisions.__all__))
        self.assertIs(get_type_hints(recommendation_requests.prepare_recommendation_request_policy)["return"],
                      recommendation_requests.RecommendationRequestPolicyResult)
        self.assertIs(get_type_hints(recommendation_requests.recommendation_basis)["return"],
                      conversations.CollaborationProposalBasis)
        self.assertEqual(get_type_hints(recommendation_decisions.prepare_recommendation_decision)["return"],
                         recommendation_decisions.PreparedRecommendationDecision
                         | recommendation_decisions.PreparedRecommendationRevision)
        self.assertEqual(get_type_hints(recommendation_decisions.build_recommendation_decision_outcome)["return"],
                         recommendation_decisions.RecommendationDecisionClosure | conversations.CollaborationEventDraft)
        self.assertEqual(get_type_hints(recommendation_decisions.match_recommendation_failure)["return"],
                         recommendation_decisions.RecommendationFailureMatch | None)
        self.assertIs(get_type_hints(recommendation_decisions.terminal_recommendation_failure_is_accounted)["return"], bool)
        self.assertEqual(get_type_hints(recommendation_decisions.build_recommendation_failure_closure)["return"],
                         tuple[conversations.CollaborationEventDraft, tuple[str, ...]])
        self.assertIs(get_type_hints(recommendation_requests.RecommendationRequestPolicyResult)["current"],
                      conversations.ArtifactDraftSource)
        self.assertIs(get_type_hints(recommendation_decisions.PreparedRecommendationDecision)["request_event"],
                      conversations.CollaborationEvent)
        self.assertEqual(get_type_hints(recommendation_decisions.PreparedRecommendationRevision)["source"],
                         LeadTaskRef | None)
        self.assertIs(get_type_hints(recommendation_decisions.RecommendationDecisionClosure)["draft"],
                      conversations.CollaborationEventDraft)
        self.assertIs(get_type_hints(recommendation_decisions.RecommendationFailureMatch)["lead_result"],
                      conversations.CollaborationEvent)

        self.assertEqual(tuple(premise_preparation.__all__), (
            'capture_shared_premises',
            'prepare_premise_admission',
            'validate_premise_control',
            'premise_synthesis_provenance',
            'premise_synthesis_result',
        ))
        self.assertEqual(tuple(lead_task_control.__all__), (
            'prepare_exploration_adoption',
            'prepare_exploration_request',
            'prepare_lead_turn_origin',
            'prepare_premise_selection',
            'prepare_task_transition',
            'superseding_turn_draft',
        ))
        self.assertEqual(tuple(specialist_advisory_values.__all__), (
            'ADVISORY_USER_IMPACT',
            'advisory_confirmation_batch',
            'prepare_advisory_origin',
            'prepare_specialist_advisory_intent',
            'specialist_advisory_assignment',
            'specialist_advisory_budget',
        ))
        policy_contracts = (
            (premise_preparation, premise_preparation.capture_shared_premises,
             ('session_id', 'intent', 'inputs', 'read_session_authoring_events'), ('read_session_authoring_events',)),
            (premise_preparation, premise_preparation.validate_premise_control,
             ('session_id', 'conversation_id', 'selection', 'events', 'projection', 'read_session_authoring_events'), ('read_session_authoring_events',)),
            (premise_preparation, premise_preparation.prepare_premise_admission,
             ('session_id', 'conversation_id', 'turn_id', 'step_id', 'invocation_id', 'question_id', 'offered_tasks', 'events', 'projection', 'premise_capture', 'read_session_authoring_events'), ('premise_capture', 'read_session_authoring_events')),
            (lead_task_control, lead_task_control.prepare_task_transition,
             ('session_id', 'conversation_id', 'decision', 'document_selection', 'events', 'projection', 'materials'), ()),
            (lead_task_control, lead_task_control.prepare_lead_turn_origin,
             ('session_id', 'conversation_id', 'text', 'projection'), ()),
            (lead_task_control, lead_task_control.prepare_premise_selection,
             ('session_id', 'conversation_id', 'turn_id', 'provider_step_id', 'control', 'selection', 'report', 'option'), ()),
            (lead_task_control, lead_task_control.prepare_exploration_request,
             ('session_id', 'conversation_id', 'turn_id', 'provider_step_id', 'user_event'), ()),
            (lead_task_control, lead_task_control.prepare_exploration_adoption,
             ('session_id', 'conversation_id', 'turn_id', 'provider_step_id', 'user_event', 'adoption', 'selected_text', 'adoption_source'), ()),
            (lead_task_control, lead_task_control.superseding_turn_draft,
             ('session_id', 'conversation_id', 'turn_id', 'provider_step_id', 'user_event', 'pending_proposal', 'projection'), ()),
            (specialist_advisory_values, specialist_advisory_values.prepare_specialist_advisory_intent,
             ('authoring_scope', 'specialist_id', 'text', 'inputs', 'review_basis'), ()),
            (specialist_advisory_values, specialist_advisory_values.specialist_advisory_assignment,
             ('specialist_id', 'text', 'projection', 'inputs', 'call_limits', 'mode', 'review_basis'), ()),
            (specialist_advisory_values, specialist_advisory_values.specialist_advisory_budget,
             ('projection', 'required_calls'), ()),
            (specialist_advisory_values, specialist_advisory_values.prepare_advisory_origin,
             ('session_id', 'conversation_id', 'turn_id', 'step_id', 'specialist_id', 'text', 'proposal'), ()),
            (specialist_advisory_values, specialist_advisory_values.advisory_confirmation_batch,
             ('session_id', 'conversation_id', 'turn_id', 'step_id', 'user_event', 'message', 'proposal_event', 'proposal', 'request_id'), ()),
        )
        for owner, function, parameter_names, keyword_only in policy_contracts:
            self.assertEqual(function.__module__, owner.__name__)
            parameters = signature(function).parameters
            self.assertEqual(tuple(parameters), parameter_names)
            self.assertEqual(tuple(name for name, value in parameters.items() if value.kind is Parameter.KEYWORD_ONLY), keyword_only)
            self.assertEqual({name: value.default for name, value in parameters.items() if value.default is not Parameter.empty},
                             {"premise_capture": None} if function is premise_preparation.prepare_premise_admission else {})
            self.assertEqual(set(function.__annotations__), {*parameter_names, "return"})
            self.assertFalse(any(isinstance(value, str) for value in function.__annotations__.values()))
            self.assertEqual(get_type_hints(function), {
                name: type(None) if value is None else value for name, value in function.__annotations__.items()
            })
        self.assertIsInstance(specialist_advisory_values.ADVISORY_USER_IMPACT, str)
        for owner, exported in (
            (collaboration_response_decoding, ('decode_artifact_synthesis', 'decode_specialist_reply', 'decode_lead_synthesis', 'required_unresolved_input_ids', 'decode_continuation_intent', 'normalize_locked_collaboration_answer')),
            (collaboration_response_contracts, ('specialist_result_contract', 'lead_synthesis_result_contract', 'LEAD_CORRECTABLE_RESPONSE_REJECTION_CODES', 'LEAD_RESPONSE_CORRECTION_INSTRUCTION', 'prepare_lead_rule_seed', 'task_discussion_constraints')),
            (artifact_revision_sources, ('artifact_revision_source',)),
            (authoring_source_history, (
                'ordinary_front_call_cap',
                'historical_authoring_users',
                'current_previous_scope',
                'authoring_task_offers',
                'recommendation_task_scope',
                'recommendation_task_offers',
                'review_source_revision',
                'local_binding_scopes',
            )),
            (lead_answer_authority, (
                'discussion_report',
                'exploration_answer_content',
                'authority_free_response',
                'validate_premise_reviewers',
                'superseding_recommendation',
                'validate_selected_preparation',
                'offered_authoring_scope',
                'transition_source_request',
                'retained_transition_source',
                'validate_current_scope',
                'scope_interpretation',
                'objective_scope',
                'validate_preliminary_targets',
                'validate_nonworkflow_answer',
                'resolved_proposal_intent',
                'validate_user_task_selection',
                'validate_adoption_answer',
                'adoption_review_assignments',
                'validate_premise_answer',
                'validate_authoring_admission_inputs',
                'validate_proposal_relation',
                'validate_artifact_request',
                'validate_premise_question',
            )),
            (lead_front_values, (
                'lead_front_material_prefix',
                'lead_front_material_suffix',
                'recommendation_preparation_material',
                'document_lineage_front_cap',
                'front_budget_limits',
                'adopted_idea_input',
                'selected_premise_input',
                'document_candidate_material_inputs',
                'front_submitted_call_count',
            )),
            (lead_document_nodes, (
                'document_plan_views',
                'validate_document_node',
                'automatic_document_node',
                'document_candidate_inputs',
                'validate_document_target',
                'document_selection_from_transition',
                'retained_document_node',
                'implicit_document_goal',
                'validate_document_task_origin',
            )),
            (provider_reply_mapping, ('map_consensus_provider_reply',)),
            (provider_request_policy, ('require_running_consensus_request', 'consensus_provider_phase', 'review_only_audit_event', 'consensus_continuation_context', 'consensus_expected_reviewed_proposal', 'consensus_agent_request', 'consensus_authored_material_required', 'consensus_report_material_required')),
            (authoring_qualification, ('consensus_lineage_changed', 'record_consensus_lineage_hop', 'consensus_admission_inputs', 'manuscript_admission_source', 'consensus_admission_source_fact', 'qualify_consensus_authoring_sources')),
            (antigravity_rules, ("read_rules_source", "install_rules")),
            (antigravity_native_recovery, ("NativeRecoveryBaseline", "SAFE_CONVERSATION_ID",
                                          "has_current_finish", "native_recovery_baseline")),
            (antigravity_recovery_matching, ("MALFORMED_FUNCTION_CALL", "NativeEvidenceUnavailable",
                                            "matches_current_finish", "native_steps")),
            (codex_command_preflight, ("command", "executable_available", "execution_command",
                                       "invocation_settings", "model", "native_state_directory", "reasoning_effort")),
            (codex_definition_sources, ("read_definition_source",)),
            (codex_stream_observation, ("make_stream_line_handler",)),
            (codex_invocation_files, ("create_invocation_files", "write_schema", "regular_output_exists",
                                      "read_regular_output", "remove_invocation_directory")),
            (provider_thread_records, (
                "PROVIDER_FINGERPRINT_PATTERN", "PROVIDER_SESSION_SCHEMA_VERSION", "PROVIDER_THREAD_ID_PATTERN",
                "context_thread_id", "context_thread_key_payload", "context_thread_record", "context_thread_relative",
                "select_thread_record", "store_thread_record", "taint_thread_record", "tainted_context_thread_record",
                "thread_key", "validate_context_thread_key", "validate_session_manifest", "validate_thread_record",
            )),
            (provider_session_timestamps, ("advance_record_timestamp", "parse_record_timestamp", "record_timestamp")),
            (provider_session_claim_guard, ("access_claim_guard",)),
            (provider_session_access_transactions, ("ProviderSessionAccessTransactions",)),
            (provider_session_transitions, ("ProviderSessionTransitions",)),
            (conversation_retrieval_wire, ("allowed_actions", "continuation_payload", "initial_payload", "retrieval_action_schema")),
            (provider_direct_call_wire, ("conversation_output_schema", "conversation_request_payload", "phase_output_schema", "phase_request_payload")),
            (provider_execution_profiles, ("execution_profile_fingerprint", "resolve_execution_profiles")),
            (provider_context_thread_store, ("load_context_thread", "store_context_thread", "taint_context_thread")),
            (provider_manifest_thread_store, ("load_manifest_thread", "store_manifest_thread", "taint_manifest_thread")),
            (provider_session_lifecycle_queries,
             ("get_session_lifecycle", "list_session_lifecycles", "session_lifecycle_from_manifest")),
            (provider_session_artifacts, (
                "PROVIDER_SESSION_DIRECTORY", "access_claim_relative", "manifest_exists", "manifest_relative",
                "read_access_claim", "read_access_claim_if_present", "read_manifest",
                "reject_unsupported_access_artifacts", "write_manifest",
            )),
            (provider_session_access_records,
             ("access_hostname_diagnostic", "new_access_claim", "unowned_access_state", "validate_access_claim")),
            (backend_reply_decoding, ("decode_conversation_retrieval_action", "decode_conversation_reply", "decode_reply", "PhaseReplyConstraintError")),
        ):
            self.assertEqual(set(owner.__all__), set(exported))
            actual = {name for name, value in vars(owner).items()
                if not name.startswith("_") and callable(value) and getattr(value, "__module__", None) == owner.__name__}
            self.assertEqual(actual, {name for name in exported if callable(getattr(owner, name))})
        self.assertIs(provider_request_policy.ArtifactProposalState, conversations.ArtifactProposalState)
        self.assertIs(authoring_qualification.admission_digest, authoring_admission.admission_digest)
        for owner, function_name, parameter_names, keyword_names, defaults, postponed in (
            (collaboration_response_decoding, 'decode_artifact_synthesis', ('answer', 'proposal', 'attempt_id', 'reply_event_ids', 'replies', 'candidate'), ('proposal', 'attempt_id', 'reply_event_ids', 'replies', 'candidate'), {}, False),
            (collaboration_response_decoding, 'decode_specialist_reply', ('answer', 'proposal', 'attempt_id', 'assignment', 'dispatch_event_id', 'premise_dependencies'), ('proposal', 'attempt_id', 'assignment', 'dispatch_event_id', 'premise_dependencies'), {'premise_dependencies': ()}, False),
            (collaboration_response_decoding, 'decode_lead_synthesis', ('answer', 'proposal', 'attempt_id', 'reply_event_ids', 'replies', 'review_candidate_ids'), ('proposal', 'attempt_id', 'reply_event_ids', 'replies', 'review_candidate_ids'), {}, False),
            (collaboration_response_decoding, 'required_unresolved_input_ids', ('replies',), (), {}, False),
            (collaboration_response_decoding, 'decode_continuation_intent', ('value', 'authoring_scope', 'review_candidate_ids', 'shared_premises'), ('authoring_scope', 'review_candidate_ids', 'shared_premises'), {'shared_premises': ()}, False),
            (collaboration_response_contracts, 'specialist_result_contract', ('assignment', 'premise_dependencies'), ('premise_dependencies',), {'premise_dependencies': ()}, False),
            (collaboration_response_contracts, 'lead_synthesis_result_contract', ('evidence', 'input_ids', 'reply_event_ids', 'allow_next_intent', 'artifact_review', 'authoring_scope', 'constraints'), ('evidence', 'input_ids', 'reply_event_ids', 'allow_next_intent', 'artifact_review', 'authoring_scope', 'constraints'), {'constraints': None}, False),
            (artifact_revision_sources, 'artifact_revision_source', ('receipt', 'original', 'history'), (), {}, False),
            (lead_material_inputs, 'specialist_assignment_material', ('proposal', 'assignment', 'input_events', 'events'), (), {}, True),
            (lead_material_inputs, 'synthesis_constraints', ('proposal', 'replies', 'reply_event_ids', 'current_projection', 'conversation_id'), (), {}, True),
            (lead_material_inputs, 'synthesis_material', ('proposal', 'replies', 'events', 'input_events', 'conversation_id', 'reply_event_ids', 'constraints', 'artifact_candidate'), (), {}, True),
            (lead_material_inputs, 'premise_assignment_material', ('proposal', 'assignment', 'events'), (), {}, True),
            (lead_material_inputs, 'premise_synthesis_values', ('proposal', 'attempt_id', 'replies', 'events'), (), {}, True),
            (premise_preparation, 'premise_synthesis_provenance', ('current', 'proposal', 'attempt_id', 'conversation_id'), (), {}, False),
            (premise_preparation, 'premise_synthesis_result', ('proposal', 'attempt_id', 'ids', 'replies', 'summary', 'report'), (), {}, False),
            (outcome_decoding, 'diagnose_collaboration_fallback', ('answer',), (), {}, True),
            (collaboration_response_decoding, 'normalize_locked_collaboration_answer', ('answer', 'constraints'), (), {}, False),
            (lead_answer_authority, 'discussion_report', ('value', 'authorization', 'task_transition', 'task_receipt'), (), {}, False),
            (lead_answer_authority, 'exploration_answer_content', ('value', 'request_event_id', 'artifact_reads'), (), {}, False),
            (lead_answer_authority, 'authority_free_response', ('value', 'spec', 'binding_pinned'), (), {}, False),
            (lead_answer_authority, 'validate_premise_reviewers', ('intent', 'materialized'), (), {}, False),
            (lead_answer_authority, 'superseding_recommendation', ('decoded', 'request', 'interpretation', 'inputs'), (), {}, False),
            (lead_answer_authority, 'validate_selected_preparation', ('decoded', 'prepare_selected'), (), {}, False),
            (lead_answer_authority, 'offered_authoring_scope', ('decoded', 'admission', 'offered_tasks'), (), {}, False),
            (lead_answer_authority, 'transition_source_request', ('receipt', 'selected_prior', 'scope', 'session_id', 'step_id', 'conversation_id'), (), {}, False),
            (lead_answer_authority, 'retained_transition_source', ('projection', 'failure', 'receipt', 'selected_prior'), (), {}, False),
            (lead_answer_authority, 'validate_current_scope', ('selected_prior', 'selected_current', 'retained_transition_source'), (), {}, False),
            (lead_answer_authority, 'scope_interpretation', ('scope', 'target_prior', 'provisional_brief_refinement'), (), {}, False),
            (lead_answer_authority, 'objective_scope', ('decoded', 'scope', 'projection'), (), {}, False),
            (lead_answer_authority, 'validate_preliminary_targets', ('review', 'scope', 'targets'), (), {}, False),
            (lead_answer_authority, 'validate_nonworkflow_answer', ('decoded', 'scope', 'evidence'), (), {}, False),
            (lead_answer_authority, 'resolved_proposal_intent', ('intent', 'declared_scope', 'resolved_scope'), (), {}, False),
            (lead_document_nodes, 'validate_document_target', ('scope', 'target_path'), (), {}, True),
            (lead_answer_authority, 'validate_user_task_selection', ('decoded', 'receipt', 'source_review_revision', 'source_artifact_revision', 'selected_document_review_path', 'task_material_events'), (), {}, False),
            (lead_answer_authority, 'validate_adoption_answer', ('decoded', 'scope', 'capture'), (), {}, False),
            (lead_answer_authority, 'adoption_review_assignments', ('intent', 'inputs'), (), {}, False),
            (lead_answer_authority, 'validate_premise_answer', ('decoded', 'scope', 'capture'), (), {}, False),
            (lead_answer_authority, 'validate_authoring_admission_inputs', ('decoded', 'historical_user_sources', 'source_scope'), (), {}, False),
            (lead_answer_authority, 'validate_proposal_relation', ('intent', 'expected_relation'), (), {}, False),
            (lead_answer_authority, 'validate_artifact_request', ('decoded', 'scope', 'expected_relation'), (), {}, False),
            (lead_evidence_receipts, 'read_reviewer_inputs', ('receipts',), (), {}, True),
            (lead_answer_authority, 'validate_premise_question', ('intent', 'premise_question_id'), (), {}, False),
            (collaboration_response_contracts, 'prepare_lead_rule_seed', ('discussion_only', 'discussion_constraints', 'expected_relation', 'inputs', 'offered_tasks', 'constraints', 'successor_only', 'task_receipt', 'revision_source_scope', 'source_artifact_revision', 'selected_document_review_path', 'interaction_mode'), ('discussion_only', 'discussion_constraints', 'expected_relation', 'inputs', 'offered_tasks', 'constraints', 'successor_only', 'task_receipt', 'revision_source_scope', 'source_artifact_revision', 'selected_document_review_path', 'interaction_mode'), {}, False),
            (outcome_decoding, 'diagnose_lead_answer', ('answer', 'discussion_constraints', 'discussion_only', 'constraints', 'read_ids', 'read_reviewers', 'interaction_mode'), ('discussion_constraints', 'discussion_only', 'constraints', 'read_ids', 'read_reviewers', 'interaction_mode'), {}, True),
            (call_rule_factory, 'front_constraints', ('inputs', 'task_material_events', 'historical_user_sources', 'readiness_pin', 'offered_tasks', 'successor_only', 'objective_projection', 'conversation_id', 'preparation_request', 'is_revision', 'preparation_present', 'premise_question_id', 'adoption_capture', 'document_selection', 'document_node', 'selected_path'), ('inputs', 'task_material_events', 'historical_user_sources', 'readiness_pin', 'offered_tasks', 'successor_only', 'objective_projection', 'conversation_id', 'preparation_request', 'is_revision', 'preparation_present', 'premise_question_id', 'adoption_capture', 'document_selection', 'document_node', 'selected_path'), {}, True),
            (lead_front_values, 'lead_front_material_prefix', ('document_selection', 'document_node', 'candidate_material', 'conversation_id', 'constraints', 'historical_user_sources', 'objective_projection'), (), {}, False),
            (lead_front_values, 'lead_front_material_suffix', ('objective_projection', 'objective_events', 'binding_prior', 'ordinary_front_cap', 'successor_only', 'inputs'), (), {}, False),
            (lead_front_values, 'recommendation_preparation_material', ('request_event', 'decision_event', 'start_event', 'inputs', 'candidate_material_by_input', 'objective_events'), (), {}, False),
            (lead_document_nodes, 'document_selection_from_transition', ('controls', 'transition_event_id'), (), {}, True),
            (lead_document_nodes, 'retained_document_node', ('projection', 'retained_scope', 'retained_node'), (), {}, True),
            (collaboration_response_contracts, 'task_discussion_constraints', ('transition', 'receipt', 'discussion_only'), (), {}, False),
            (lead_document_nodes, 'implicit_document_goal', ('projection', 'history', 'current_goal', 'current_budget_task_id'), (), {}, True),
            (lead_front_values, 'document_lineage_front_cap', ('ordinary_front_cap', 'inherited_calls', 'inherited_rounds'), (), {}, False),
            (lead_front_values, 'front_budget_limits', ('ordinary_front_cap', 'task_receipt', 'document_goal', 'projection', 'document_node', 'preparation', 'previous_proposal', 'expected_relation'), (), {}, False),
            (lead_material_inputs, 'selected_task_input', ('event', 'ordinal', 'capture'), (), {}, True),
            (authoring_source_history, 'current_previous_scope', ('previous_scope', 'available_tasks'), (), {}, True),
            (authoring_source_history, 'authoring_task_offers', ('previous_scope', 'available_tasks', 'events', 'conversation_id', 'step_id', 'successor_only'), (), {}, True),
            (authoring_source_history, 'recommendation_task_scope', ('request', 'available_tasks'), (), {}, True),
            (authoring_source_history, 'recommendation_task_offers', ('request_scope', 'current_scope', 'decision'), (), {}, True),
            (lead_front_values, 'adopted_idea_input', ('capture', 'event_id'), (), {}, False),
            (lead_front_values, 'selected_premise_input', ('capture', 'event_id'), (), {}, False),
            (lead_document_nodes, 'validate_document_task_origin', ('origin', 'receipt', 'session_id', 'conversation_id', 'step_id', 'selection', 'offered_tasks', 'selected_path'), ('offered_tasks', 'selected_path'), {}, True),
            (lead_material_inputs, 'document_candidate_input_material', ('inputs',), (), {}, True),
            (authoring_source_history, 'review_source_revision', ('receipt', 'expected_relation', 'previous_proposal', 'previous_scope', 'events', 'task_material_events'), (), {}, True),
            (authoring_source_history, 'local_binding_scopes', ('available_tasks', 'events', 'conversation_id'), (), {}, True),
            (lead_front_values, 'document_candidate_material_inputs', ('intents', 'inputs'), (), {}, False),
            (lead_material_inputs, 'document_work_plan_material', ('view', 'selection'), (), {}, True),
            (lead_front_values, 'front_submitted_call_count', ('events', 'step_id'), (), {}, False),
            (reply_contract_values, 'lead_rejection_failure_code', ('source_owner_rejection', 'prepare_selected', 'declared_artifact_intent', 'decoded'), (), {}, True),
            (artifact_objective_planning, 'clarify_unprepared_authoring_proposal', ('outcome', 'document_selection', 'document_node'), ('document_selection', 'document_node'), {}, False),
            (provider_reply_mapping, 'map_consensus_provider_reply', ('invocation', 'reply', 'authored_material', 'report_material'), ('authored_material', 'report_material'), {'authored_material': None, 'report_material': None}, False),
            (provider_request_policy, 'require_running_consensus_request', ('invocation', 'stored'), (), {}, False),
            (provider_request_policy, 'consensus_provider_phase', ('invocation',), (), {}, False),
            (provider_request_policy, 'review_only_audit_event', ('invocation', 'stored'), (), {}, False),
            (provider_request_policy, 'consensus_continuation_context', ('input_text', 'ruling', 'conflicts'), (), {}, False),
            (provider_request_policy, 'consensus_expected_reviewed_proposal', ('reviewed_projection',), (), {}, False),
            (provider_request_policy, 'consensus_agent_request', ('invocation', 'phase', 'input_text', 'candidate_text', 'expected_reviewed_proposal', 'review_messages'), (), {}, False),
            (provider_request_policy, 'consensus_authored_material_required', ('invocation', 'reply'), (), {}, False),
            (provider_request_policy, 'consensus_report_material_required', ('invocation', 'reply'), (), {}, False),
            (authoring_qualification, 'consensus_lineage_changed', ('original', 'material', 'direction_material'), (), {}, False),
            (authoring_qualification, 'record_consensus_lineage_hop', ('original', 'seen'), (), {}, False),
            (authoring_qualification, 'consensus_admission_inputs', ('input_kind', 'material', 'direction_material', 'ruling_material'), (), {}, False),
            (authoring_qualification, 'manuscript_admission_source', ('handle', 'snapshots', 'facts'), (), {}, False),
            (authoring_qualification, 'consensus_admission_source_fact', ('alias', 'kind', 'handle', 'text', 'revision_digest'), (), {}, False),
            (authoring_qualification, 'qualify_consensus_authoring_sources', ('identity', 'workflow_id', 'material', 'assessment', 'input_kind', 'direction_material', 'ruling_material', 'source_revision_id', 'authoring_target', 'facts'), ('input_kind', 'direction_material', 'ruling_material', 'source_revision_id', 'authoring_target', 'facts'), {}, False),
            (antigravity_rules, "read_rules_source", ("source",), (), {}, True),
            (antigravity_rules, "install_rules", ("target", "content"), (), {}, True),
            (antigravity_recovery_matching, "native_steps", ("content",), (), {}, True),
            (antigravity_recovery_matching, "matches_current_finish",
             ("steps", "prompt", "structured_output", "native_error"), (), {}, True),
            (codex_command_preflight, "command",
             ("executable", "staging_directory", "schema_path", "output_path", "thread_id", "agent_id", "invocation_settings"),
             ("invocation_settings",), {}, True),
            (codex_command_preflight, "executable_available", ("which", "get_executable"), (), {}, True),
            (codex_command_preflight, "execution_command",
             ("staging_directory", "schema_path", "output_path", "thread_id", "agent_id", "execution_workspace",
              "which", "get_executable", "get_control_root", "get_native_state_directory", "invocation_settings"),
             ("which", "get_executable", "get_control_root", "get_native_state_directory", "invocation_settings"), {}, True),
            (codex_command_preflight, "invocation_settings", ("agent_id", "execution_profiles", "explicit_model", "configuration"),
             ("execution_profiles", "explicit_model", "configuration"), {}, True),
            (codex_command_preflight, "model", ("explicit_model", "configuration"), (), {}, True),
            (codex_command_preflight, "native_state_directory", ("session_root", "session_container", "staging", "output", "get_control_root"),
             ("session_root", "session_container", "staging", "output", "get_control_root"), {}, True),
            (codex_command_preflight, "reasoning_effort", ("configuration",), (), {}, True),
            (codex_definition_sources, "read_definition_source", ("definition",), (), {}, True),
            (codex_stream_observation, "make_stream_line_handler", ("agent_id", "stream_sink"), (), {}, True),
            (codex_invocation_files, "create_invocation_files", ("make_temporary_directory",), (), {}, True),
            (codex_invocation_files, "write_schema", ("schema_path", "invocation"), (), {}, True),
            (codex_invocation_files, "regular_output_exists", ("path",), (), {}, True),
            (codex_invocation_files, "read_regular_output", ("path",), (), {}, True),
            (codex_invocation_files, "remove_invocation_directory", ("temporary_directory",), (), {}, True),
            (provider_thread_records, "validate_thread_record", ("value",), (), {}, True),
            (provider_session_timestamps, "record_timestamp", (), (), {}, True),
            (provider_session_claim_guard, "access_claim_guard",
             ("workspace", "session_directory", "guard_relative"), (), {}, True),
            (provider_session_access_records, "new_access_claim", ("session_id", "token"), (), {}, True),
            (provider_session_access_records, "unowned_access_state", ("value",), (), {}, True),
            (provider_session_access_records, "access_hostname_diagnostic", ("hostname",), (), {}, True),
            (provider_session_access_records, "validate_access_claim", ("value", "session_id"), (), {}, True),
            (provider_session_artifacts, "access_claim_relative", ("session_id",), (), {}, True),
            (provider_session_artifacts, "manifest_exists", ("workspace", "session_id"), (), {}, True),
            (provider_session_artifacts, "manifest_relative", ("session_id",), (), {}, True),
            (provider_session_artifacts, "read_access_claim", ("workspace", "session_id"), (), {}, True),
            (provider_session_artifacts, "read_access_claim_if_present", ("workspace", "session_id"), (), {}, True),
            (provider_session_artifacts, "read_manifest", ("workspace", "session_id", "missing_ok"),
             ("missing_ok",), {}, True),
            (provider_session_artifacts, "reject_unsupported_access_artifacts", ("workspace", "session_id"),
             (), {}, True),
            (provider_session_artifacts, "write_manifest", ("workspace", "session_id", "value", "overwrite"),
             ("overwrite",), {"overwrite": True}, True),
            (conversation_retrieval_wire, "allowed_actions", ("action_index", "has_observation", "after_read", "final_provider_call"),
             ("action_index", "has_observation", "after_read", "final_provider_call"), {"final_provider_call": False}, True),
            (conversation_retrieval_wire, "continuation_payload", ("request", "action_index", "allowed_actions", "observation"),
             ("action_index", "allowed_actions", "observation"), {}, True),
            (conversation_retrieval_wire, "initial_payload", ("request", "allowed_actions"), ("allowed_actions",), {}, True),
            (conversation_retrieval_wire, "retrieval_action_schema", ("allowed_actions",), ("allowed_actions",), {"allowed_actions": None}, True),
            (provider_direct_call_wire, "conversation_output_schema", (), (), {}, True),
            (provider_direct_call_wire, "conversation_request_payload", ("request",), (), {}, True),
            (provider_direct_call_wire, "phase_request_payload", ("request",), (), {}, True),
            (provider_direct_call_wire, "phase_output_schema", ("phase", "expected_reviewed_proposal", "review_only"),
             ("expected_reviewed_proposal", "review_only"), {"expected_reviewed_proposal": None, "review_only": False}, True),
            (provider_execution_profiles, "execution_profile_fingerprint", ("profile",), (), {}, True),
            (provider_execution_profiles, "resolve_execution_profiles",
             ("workspace", "configuration", "backend_overrides", "explicit_model",
              "explicit_antigravity_model", "explicit_antigravity_effort"),
             ("backend_overrides", "explicit_model", "explicit_antigravity_model", "explicit_antigravity_effort"), {}, True),
            (provider_context_thread_store, "load_context_thread",
             ("workspace", "scope", "provider_id", "definition_fingerprint", "policy_fingerprint", "require_ready"),
             ("require_ready",), {}, True),
            (provider_context_thread_store, "store_context_thread",
             ("workspace", "scope", "provider_id", "definition_fingerprint", "policy_fingerprint", "thread_id",
              "tainted", "require_owned_access"), ("tainted", "require_owned_access"), {}, True),
            (provider_context_thread_store, "taint_context_thread",
             ("workspace", "scope", "provider_id", "definition_fingerprint", "policy_fingerprint", "require_owned_access"),
             ("require_owned_access",), {}, True),
            (provider_manifest_thread_store, "load_manifest_thread",
             ("workspace", "session_id", "definition", "fingerprint"), ("fingerprint",), {"fingerprint": None}, True),
            (provider_manifest_thread_store, "store_manifest_thread",
             ("workspace", "session_id", "definition", "thread_id", "tainted", "fingerprint", "require_owned_access"),
             ("tainted", "fingerprint", "require_owned_access"), {"fingerprint": None}, True),
            (provider_manifest_thread_store, "taint_manifest_thread",
             ("workspace", "session_id", "definition", "fingerprint"), ("fingerprint",), {"fingerprint": None}, True),
            (provider_session_lifecycle_queries, "get_session_lifecycle",
             ("workspace", "session_id", "classify_access_state"), (), {}, True),
            (provider_session_lifecycle_queries, "list_session_lifecycles",
             ("workspace", "classify_access_state"), (), {}, True),
            (provider_session_lifecycle_queries, "session_lifecycle_from_manifest",
             ("workspace", "manifest", "classify_access_state"), (), {}, True),
            (provider_session_timestamps, "parse_record_timestamp", ("value", "label"), ("label",), {}, True),
            (provider_session_timestamps, "advance_record_timestamp", ("previous", "candidate"),
             ("candidate",), {"candidate": None}, True),
            (backend_reply_decoding, "decode_conversation_retrieval_action", ("text", "request", "action_index", "provider_name", "allowed_actions"), ("request", "action_index", "provider_name", "allowed_actions"), {"provider_name": "Codex", "allowed_actions": None}, True),
            (backend_reply_decoding, "decode_conversation_reply", ("request", "text", "provider_name"), ("provider_name",), {"provider_name": "Codex"}, True),
            (backend_reply_decoding, "decode_reply", ("phase", "text", "revision_id", "provider_name"), ("revision_id", "provider_name"), {"provider_name": "Codex"}, True),
        ):
            function = getattr(owner, function_name)
            self.assertEqual(function.__qualname__, function_name)
            self.assertEqual(function.__module__, owner.__name__)
            parameters = signature(function).parameters
            self.assertEqual(tuple(parameters), parameter_names)
            self.assertEqual(tuple(name for name, value in parameters.items() if value.kind is Parameter.KEYWORD_ONLY), keyword_names)
            self.assertEqual({name: value.default for name, value in parameters.items() if value.default is not Parameter.empty}, defaults)
            self.assertFalse(any(value.kind in (Parameter.VAR_POSITIONAL, Parameter.VAR_KEYWORD) for value in parameters.values()))
            self.assertEqual(set(function.__annotations__), {*parameter_names, "return"})
            if postponed:
                self.assertTrue(all(isinstance(value, str) for value in function.__annotations__.values()))
            else:
                self.assertFalse(any(isinstance(value, str) for value in function.__annotations__.values()))
            resolved = get_type_hints(function)
            self.assertEqual(set(resolved), {*parameter_names, "return"})
            self.assertFalse(any(isinstance(value, str) for value in resolved.values()))
            if not postponed:
                self.assertEqual(get_type_hints(function), {name: type(None) if value is None else value for name, value in function.__annotations__.items()})
        self.assertIsInstance(collaboration_response_contracts.LEAD_CORRECTABLE_RESPONSE_REJECTION_CODES, frozenset)
        self.assertIsInstance(collaboration_response_contracts.LEAD_RESPONSE_CORRECTION_INSTRUCTION, str)
        for module_name in ("group_observation", "group_reporting"):
            spec = importlib.util.find_spec("story_writing_agents.agent_context." + module_name)
            self.assertIsNotNone(spec)
            self.assertEqual(Path(spec.origin), _SOURCE_ROOT / "agent_context" / (module_name + ".py"))
        for name in ("LeadGroupObservation", "LeadGroupObservationKind", "LeadGroupObservationSink",
                     "LeadGroupTrace", "LeadGroupRequestMeasurement", "group_request_size_report",
                     "group_trace_report",
                     "prepare_recommendation_request_policy",
                     "recommendation_basis",
                     "RecommendationRequestPolicyResult",
                     "prepare_recommendation_decision",
                     "build_recommendation_decision_outcome",
                     "match_recommendation_failure",
                     "terminal_recommendation_failure_is_accounted",
                     "build_recommendation_failure_closure",
                     "PreparedRecommendationDecision",
                     "PreparedRecommendationRevision",
                     "RecommendationDecisionClosure",
                     "RecommendationFailureMatch",
                     'capture_shared_premises',
                     'prepare_premise_admission',
                     'validate_premise_control',
                     'prepare_exploration_adoption',
                     'prepare_exploration_request',
                     'prepare_lead_turn_origin',
                     'prepare_premise_selection',
                     'prepare_task_transition',
                     'superseding_turn_draft',
                     'ADVISORY_USER_IMPACT',
                     'advisory_confirmation_batch',
                     'prepare_advisory_origin',
                     'prepare_specialist_advisory_intent',
                     'specialist_advisory_assignment',
                     'specialist_advisory_budget', 'decode_artifact_synthesis', 'decode_specialist_reply', 'decode_lead_synthesis', 'required_unresolved_input_ids', 'decode_continuation_intent', 'specialist_result_contract', 'lead_synthesis_result_contract', 'artifact_revision_source', 'LEAD_CORRECTABLE_RESPONSE_REJECTION_CODES', 'LEAD_RESPONSE_CORRECTION_INSTRUCTION',
                    'map_consensus_provider_reply',
            'require_running_consensus_request',
            'consensus_provider_phase',
            'review_only_audit_event',
            'consensus_continuation_context',
            'consensus_expected_reviewed_proposal',
            'consensus_agent_request',
            'consensus_authored_material_required',
            'consensus_report_material_required',
            'consensus_lineage_changed',
            'record_consensus_lineage_hop',
            'consensus_admission_inputs',
            'manuscript_admission_source',
            'consensus_admission_source_fact',
            'qualify_consensus_authoring_sources',
        ):
            self.assertNotIn(name, story_writing_agents.__all__)
            self.assertFalse(hasattr(story_writing_agents, name))
        self.assertIs(get_type_hints(antigravity_rules.read_rules_source)["source"], Path)
        self.assertIs(get_type_hints(antigravity_rules.read_rules_source)["return"], bytes)
        self.assertIs(get_type_hints(antigravity_rules.install_rules)["target"], Path)
        self.assertIs(get_type_hints(antigravity_rules.install_rules)["content"], bytes)
        self.assertIs(get_type_hints(antigravity_rules.install_rules)["return"], type(None))
        self.assertIs(antigravity_rules.os, antigravity_adapter.os)
        self.assertIs(antigravity_state_directories.os, antigravity_rules.os)
        self.assertFalse(hasattr(antigravity_adapter.AntigravityAdapter, "_read_rules_source"))
        self.assertFalse(hasattr(antigravity_adapter.AntigravityAdapter, "_install_rules"))
        self.assertNotIn("read_rules_source", story_writing_agents.__all__)
        self.assertNotIn("install_rules", story_writing_agents.__all__)
        schema_descriptor = vars(antigravity_adapter.AntigravityAdapter)["native_output_schema"]
        self.assertIs(type(schema_descriptor), staticmethod)
        self.assertIs(schema_descriptor.__func__, antigravity_native_output_schema.render_native_output_schema)
        self.assertFalse(hasattr(story_writing_agents, "render_native_output_schema"))
        for name in ("classify_native_authentication_failure", "complete_native_outcome"):
            self.assertIs(
                getattr(antigravity_adapter, f"_{name}"),
                getattr(antigravity_native_outcome, name),
            )
            self.assertFalse(hasattr(story_writing_agents, name))
        auth_classifier = vars(antigravity_adapter.AntigravityAdapter)["_authentication_failure"]
        self.assertIs(type(auth_classifier), classmethod)
        self.assertIs(antigravity_adapter.antigravity_native_recovery, antigravity_native_recovery)
        self.assertIs(antigravity_native_recovery.antigravity_recovery_matching, antigravity_recovery_matching)
        self.assertEqual(antigravity_native_recovery.NativeRecoveryBaseline.__module__, antigravity_native_recovery.__name__)
        matching = antigravity_recovery_matching
        self.assertIs(antigravity_adapter.antigravity_recovery_matching, matching)
        self.assertIs(matching.parse_result, antigravity_response_decoding.parse_result)
        self.assertEqual(get_type_hints(matching.native_steps), {"content": bytes, "return": list[dict[str, Any]]})
        self.assertEqual(
            get_type_hints(matching.matches_current_finish),
            {"steps": list[dict[str, Any]], "prompt": str, "structured_output": dict[str, Any],
             "native_error": str, "return": bool},
        )
        self.assertEqual(matching.NativeEvidenceUnavailable.__bases__, (ValueError,))
        self.assertEqual(matching.NativeEvidenceUnavailable.__module__, matching.__name__)
        for name in matching.__all__:
            self.assertNotIn(name, story_writing_agents.__all__)
            self.assertFalse(hasattr(story_writing_agents, name))
        self.assertFalse(hasattr(antigravity_adapter, "_NativeEvidenceUnavailable"))
        self.assertFalse(hasattr(antigravity_adapter, "_MALFORMED_FUNCTION_CALL"))
        self.assertEqual(
            get_type_hints(codex_definition_sources.read_definition_source),
            {"definition": models.AgentDefinition, "return": tuple[bytes, tuple[ContextComponentBytes, ...]]},
        )
        self.assertEqual(
            get_type_hints(codex_stream_observation.make_stream_line_handler),
            {"agent_id": str, "stream_sink": ProviderStreamSink | None, "return": Callable[[bytes], None]},
        )
        self.assertIs(codex_adapter.codex_stream_observation, codex_stream_observation)
        self.assertIs(codex_adapter.codex_command_preflight, codex_command_preflight)
        self.assertIs(codex_adapter.codex_invocation_files, codex_invocation_files)
        self.assertIs(codex_native_outcome.codex_invocation_files, codex_invocation_files)
        self.assertIs(codex_native_outcome.codex_native_context, codex_adapter.codex_native_context)
        for name in ("classify_native_authentication_failure", "interpret_completed_native_outcome"):
            self.assertIs(
                getattr(codex_adapter, f"_{name}"),
                getattr(codex_native_outcome, name),
            )
            self.assertFalse(hasattr(story_writing_agents, name))
        codex_auth_classifier = vars(codex_adapter.CodexAdapter)["_authentication_failure"]
        self.assertIs(type(codex_auth_classifier), classmethod)
        self.assertEqual(get_type_hints(codex_invocation_files.create_invocation_files),
                         {"make_temporary_directory": Callable[[], Callable[..., str]], "return": tuple[Path, Path, Path]})
        self.assertEqual(get_type_hints(codex_invocation_files.write_schema),
                         {"schema_path": Path, "invocation": ProviderInvocation, "return": type(None)})
        self.assertNotIn("make_stream_line_handler", story_writing_agents.__all__)
        self.assertFalse(hasattr(story_writing_agents, "make_stream_line_handler"))
        self.assertIs(codex_adapter.codex_definition_sources, codex_definition_sources)
        self.assertFalse(hasattr(codex_adapter.CodexAdapter, "_definition_file"))
        self.assertNotIn("read_definition_source", story_writing_agents.__all__)
        self.assertFalse(hasattr(story_writing_agents, "read_definition_source"))
        self.assertIs(
            backend_module._stage_provider_configuration,
            provider_configuration_candidate.stage_provider_configuration,
        )
        self.assertFalse(hasattr(story_writing_agents, "stage_provider_configuration"))
        for name in (
            "activate_provider_configuration", "validate_provider_configuration_candidate",
            "provider_configuration_candidate_is_active", "discard_provider_configuration",
            "record_provider_configuration_recovery_required",
        ):
            self.assertIs(getattr(backend_module, f"_{name}"), getattr(provider_configuration_activation, name))
            self.assertFalse(hasattr(story_writing_agents, name))
        self.assertIs(backend_module._initial_legacy_thread_disposition, provider_legacy_thread_compatibility.initial_legacy_thread_disposition)
        self.assertIs(backend_module._build_legacy_continuation_fingerprint, provider_legacy_thread_compatibility.legacy_continuation_fingerprint)
        self.assertIs(
            backend_module._validate_provider_startup_options,
            provider_startup_options.validate_provider_startup_options,
        )
        self.assertFalse(hasattr(story_writing_agents, "validate_provider_startup_options"))
        for name in ("open_bound_provider_execution_workspace", "prepare_context_reference_handoff"):
            self.assertIs(
                getattr(backend_module, f"_{name}"),
                getattr(provider_workspace_authority_lifecycle, name),
            )
            self.assertFalse(hasattr(story_writing_agents, name))
        self.assertIs(backend_module.provider_manifest_thread_store, provider_manifest_thread_store)
        self.assertIs(backend_module.provider_thread_records, provider_thread_records)
        self.assertIs(backend_module._prepare_provider_binding_locked, provider_binding_admission.prepare_provider_binding_locked)
        self.assertIs(backend_module._release_backend_resources, backend_shutdown.release_backend_resources)
        self.assertIs(backend_module._validate_conversation_request, bound_agent_request_validation.validate_conversation_request)
        self.assertIs(backend_module._validate_conversation_retrieval_request, bound_agent_request_validation.validate_conversation_retrieval_request)
        transitions = provider_session_transitions
        self.assertIs(backend_module.provider_session_transitions, transitions)
        self.assertIs(transitions.provider_session_timestamps, provider_session_timestamps)
        self.assertIs(transitions.provider_session_claim_guard, provider_session_claim_guard)
        self.assertEqual(get_type_hints(provider_session_claim_guard.access_claim_guard.__wrapped__),
                         {"workspace": Workspace, "session_directory": str, "guard_relative": str,
                          "return": Iterator[None]})
        self.assertIs(provider_session_claim_guard.BackendProtocolError, BackendProtocolError)
        self.assertIs(provider_session_claim_guard.WorkspaceFormatError, WorkspaceFormatError)
        self.assertIs(provider_session_claim_guard.WorkspaceSecurityError, WorkspaceSecurityError)
        self.assertFalse(hasattr(backend_module.AgentBackend, "_access_claim_guard"))
        self.assertNotIn("access_claim_guard", story_writing_agents.__all__)
        self.assertFalse(hasattr(story_writing_agents, "access_claim_guard"))
        access_records = provider_session_access_records
        self.assertIs(transitions.provider_session_access_records, access_records)
        artifacts = provider_session_artifacts
        self.assertIs(backend_module.provider_session_artifacts, artifacts)
        self.assertIs(transitions.provider_session_artifacts, artifacts)
        queries = provider_session_lifecycle_queries
        operations = provider_session_lifecycle_operations
        self.assertIs(operations.provider_session_lifecycle_queries, queries)
        for name in (
            "get_session_lifecycle", "list_session_lifecycles",
            "acquire_session_access", "release_session_access",
            "create_session_with_access", "reopen_session_with_access",
            "pause_session_and_release_access",
        ):
            self.assertIs(getattr(backend_module.AgentBackend, name), getattr(operations, name))
            self.assertFalse(hasattr(story_writing_agents, name))
        self.assertIs(backend_module.AgentBackend._assert_provider_work_ready, operations.assert_provider_work_ready)
        self.assertFalse(hasattr(story_writing_agents, "assert_provider_work_ready"))
        self.assertIs(transitions.provider_session_lifecycle_queries, queries)
        transactions = provider_session_access_transactions
        self.assertIs(backend_module.provider_session_access_transactions, transactions)
        self.assertIs(transactions.access_claim_guard, provider_session_claim_guard.access_claim_guard)
        self.assertIs(transactions.session_lifecycle_from_manifest, queries.session_lifecycle_from_manifest)
        transaction_type = transactions.ProviderSessionAccessTransactions
        for method, parameters in (
            (transaction_type.__init__, ("access_claim_tokens", "guard_relative")),
            (transaction_type.acquire_access_with_reuse, ("workspace", "session_id", "get_session_lifecycle")),
            (transaction_type.release_owned_access_claim, ("workspace", "session_id")),
            (transaction_type.release_access_claim, ("workspace", "session_id", "token")),
            (transaction_type.access_state, ("session_id", "value")),
            (transaction_type.discard_access_claim_after_failed_create, ("workspace", "session_id", "token", "failure")),
            (transaction_type.assert_owned_access, ("workspace", "session_id")),
        ):
            args = signature(method).parameters
            self.assertEqual(tuple(args), ("self", *parameters))
            self.assertTrue(all(arg.kind is Parameter.POSITIONAL_OR_KEYWORD and arg.default is Parameter.empty
                                for arg in args.values()))
        self.assertEqual(get_type_hints(transaction_type.release_access_claim),
                         {"workspace": Workspace, "session_id": str, "token": str, "return": type(None)})
        self.assertEqual(get_type_hints(transaction_type.acquire_access_with_reuse)["get_session_lifecycle"],
                         Callable[[str], SessionLifecycleState])
        self.assertNotIn("ProviderSessionAccessTransactions", story_writing_agents.__all__)
        self.assertFalse(hasattr(story_writing_agents, "ProviderSessionAccessTransactions"))
        transition_type = transitions.ProviderSessionTransitions
        constructor = signature(transition_type).parameters
        self.assertEqual(tuple(constructor), ("access_transactions", "access_claim_tokens", "guard_relative"))
        self.assertTrue(all(arg.kind is Parameter.POSITIONAL_OR_KEYWORD and arg.default is Parameter.empty
                            for arg in constructor.values()))
        self.assertEqual(get_type_hints(transition_type.__init__),
                         {"access_transactions": transaction_type, "access_claim_tokens": dict[str, str],
                          "guard_relative": str, "return": type(None)})
        for method in (
            transition_type.create_session_with_access,
            transition_type.reopen_session_with_access,
            transition_type.pause_session_and_release_access,
        ):
            args = signature(method).parameters
            self.assertEqual(tuple(args), ("self", "workspace", "session", "get_session_lifecycle"))
            self.assertTrue(all(arg.kind is Parameter.POSITIONAL_OR_KEYWORD and arg.default is Parameter.empty
                                for arg in args.values()))
            self.assertEqual(get_type_hints(method),
                             {"workspace": Workspace, "session": str, "get_session_lifecycle": Callable[[str], SessionLifecycleState],
                              "return": SessionLifecycleState})
        self.assertNotIn("ProviderSessionTransitions", story_writing_agents.__all__)
        self.assertFalse(hasattr(story_writing_agents, "ProviderSessionTransitions"))
        self.assertIs(backend_module.conversation_retrieval_wire, conversation_retrieval_wire)
        self.assertIs(backend_module.provider_direct_call_wire, provider_direct_call_wire)
        action_kinds = tuple[conversation_retrieval.ConversationRetrievalActionKind, ...]
        payload = dict[str, Any]
        for function, expected in (
            (conversation_retrieval_wire.allowed_actions, {"action_index": int, "has_observation": bool,
             "after_read": bool, "final_provider_call": bool, "return": action_kinds}),
            (conversation_retrieval_wire.continuation_payload, {"request": conversation_retrieval.ConversationRetrievalRequest,
             "action_index": int, "allowed_actions": action_kinds, "observation": str, "return": payload}),
            (conversation_retrieval_wire.initial_payload, {"request": conversation_retrieval.ConversationRetrievalRequest,
             "allowed_actions": action_kinds, "return": payload}),
            (conversation_retrieval_wire.retrieval_action_schema, {"allowed_actions": action_kinds | None, "return": payload}),
            (provider_direct_call_wire.conversation_output_schema, {"return": payload}),
            (provider_direct_call_wire.conversation_request_payload, {"request": models.ConversationRequest, "return": payload}),
            (provider_direct_call_wire.phase_request_payload, {"request": models.AgentRequest, "return": payload}),
            (provider_direct_call_wire.phase_output_schema, {"phase": models.Phase, "expected_reviewed_proposal": tuple[str, str] | None,
             "review_only": bool, "return": payload}),
        ):
            self.assertEqual(get_type_hints(function), expected)
        for module in (conversation_retrieval_wire, provider_direct_call_wire):
            self.assertTrue(set(module.__all__).isdisjoint(story_writing_agents.__all__))
            self.assertTrue(all(not hasattr(story_writing_agents, name) for name in module.__all__))
        profiles = provider_execution_profiles
        self.assertIs(backend_module.provider_execution_profiles, profiles)
        self.assertEqual(get_type_hints(profiles.execution_profile_fingerprint), {"profile": AgentExecutionProfile, "return": str})
        self.assertEqual(
            get_type_hints(profiles.resolve_execution_profiles),
            {"workspace": Workspace, "configuration": ConfigurationSnapshot, "backend_overrides": Mapping[str, str],
             "explicit_model": str | None, "explicit_antigravity_model": str | None,
             "explicit_antigravity_effort": str | None, "return": Mapping[str, AgentExecutionProfile]},
        )
        for name in profiles.__all__:
            self.assertNotIn(name, story_writing_agents.__all__)
            self.assertFalse(hasattr(story_writing_agents, name))
        from story_writing_agents.provider_bound_context import BoundContextProvider
        self.assertIs(backend_module._BoundContextProvider, BoundContextProvider)
        self.assertFalse(hasattr(story_writing_agents, "BoundContextProvider"))
        self.assertFalse(hasattr(story_writing_agents, "_BoundContextProvider"))
        context_store = provider_context_thread_store
        self.assertIs(provider_context_invocation.provider_context_thread_store, context_store)
        manifest_store = provider_manifest_thread_store
        self.assertIs(backend_module.provider_manifest_thread_store, manifest_store)
        self.assertIs(provider_reference_read_lifecycle.provider_manifest_thread_store, manifest_store)
        self.assertIs(provider_conversation_retrieval_loop.provider_manifest_thread_store, manifest_store)
        self.assertIs(backend_module.AgentBackend._converse_with_retrieval_codex,
                      provider_conversation_retrieval_loop.converse_with_retrieval)
        self.assertIs(backend_module.AgentBackend._invoke_codex, provider_direct_structured_calls.invoke_direct_structured)
        self.assertIs(backend_module.AgentBackend._converse_codex, provider_direct_structured_calls.converse_direct_structured)
        for route in (
            "invoke_provider", "invoke_antigravity", "converse_provider", "converse_antigravity",
            "converse_with_retrieval_provider", "converse_with_retrieval_antigravity",
        ):
            self.assertIs(getattr(backend_module.AgentBackend, f"_{route}"),
                          getattr(provider_invocation_routing, route))
        self.assertIs(backend_module.AgentBackend._taint_retrieval_thread_without_replacing_failure,
                      provider_conversation_retrieval_loop.taint_retrieval_thread_without_replacing_failure)
        self.assertIs(backend_module.AgentBackend._load_authorized_reference_context,
                      provider_reference_read_lifecycle.load_authorized_reference_context)
        self.assertIs(backend_module._DEFERRED_DIRECT_CODEX_ACCEPTANCES,
                      provider_structured_invocation.DEFERRED_DIRECT_CODEX_ACCEPTANCES)
        self.assertIs(bound_agent_logical_invocation._DEFERRED_DIRECT_CODEX_ACCEPTANCES,
                      provider_structured_invocation.DEFERRED_DIRECT_CODEX_ACCEPTANCES)
        self.assertIs(backend_module.BoundAgent._direct_execution_workspace,
                      bound_agent_logical_invocation.direct_execution_workspace)
        self.assertIs(backend_module.BoundAgent._invoke_logical, bound_agent_logical_invocation.invoke_logical)
        self.assertIs(backend_module.AgentBackend._invoke_provider_structured,
                      provider_structured_invocation.invoke_provider_structured)
        self.assertIs(backend_module.AgentBackend._begin_legacy_agent_exchange,
                      provider_structured_invocation.begin_legacy_agent_exchange)
        self.assertIs(manifest_store.provider_session_artifacts, artifacts)
        self.assertIs(manifest_store.provider_thread_records, provider_thread_records)
        self.assertIs(manifest_store.Workspace, Workspace)
        self.assertIs(manifest_store.AgentDefinition, models.AgentDefinition)
        self.assertIs(manifest_store.BackendProtocolError, BackendProtocolError)
        self.assertEqual(get_type_hints(manifest_store.load_manifest_thread)["return"], dict[str, Any] | None)
        self.assertEqual(get_type_hints(manifest_store.store_manifest_thread)["require_owned_access"],
                         Callable[[Workspace, str], None])
        for name in manifest_store.__all__:
            self.assertNotIn(name, story_writing_agents.__all__)
            self.assertFalse(hasattr(story_writing_agents, name))
        self.assertIs(context_store.Workspace, Workspace)
        self.assertIs(context_store.ContextScope, ContextScope)
        self.assertIs(context_store.context_thread_relative, provider_thread_records.context_thread_relative)
        self.assertIs(context_store.tainted_context_thread_record, provider_thread_records.tainted_context_thread_record)
        self.assertEqual(get_type_hints(context_store.load_context_thread)["require_ready"], Callable[[str], None])
        self.assertEqual(get_type_hints(context_store.store_context_thread)["require_owned_access"],
                         Callable[[Workspace, str], None])
        self.assertEqual(get_type_hints(context_store.taint_context_thread)["require_owned_access"],
                         Callable[[Workspace, str], None])
        for name in context_store.__all__:
            self.assertNotIn(name, story_writing_agents.__all__)
            self.assertFalse(hasattr(story_writing_agents, name))
        self.assertIs(queries.Workspace, Workspace)
        self.assertIs(queries.WorkspaceFormatError, WorkspaceFormatError)
        self.assertIs(queries.WorkspaceVersionUnsupportedError, WorkspaceVersionUnsupportedError)
        self.assertIs(queries.SessionAccessState, SessionAccessState)
        self.assertIs(queries.SessionDurableState, SessionDurableState)
        self.assertIs(queries.SessionLifecycleState, SessionLifecycleState)
        self.assertIs(queries.PROVIDER_SESSION_DIRECTORY, artifacts.PROVIDER_SESSION_DIRECTORY)
        self.assertIs(queries.read_access_claim, artifacts.read_access_claim)
        self.assertIs(queries.read_access_claim_if_present, artifacts.read_access_claim_if_present)
        self.assertIs(queries.read_manifest, artifacts.read_manifest)
        self.assertIs(queries.reject_unsupported_access_artifacts, artifacts.reject_unsupported_access_artifacts)
        self.assertIs(queries.parse_record_timestamp, provider_session_timestamps.parse_record_timestamp)
        classify = Callable[[str, Mapping[str, Any]], SessionAccessState]
        self.assertEqual(get_type_hints(queries.get_session_lifecycle),
                         {"workspace": Workspace, "session_id": str, "classify_access_state": classify,
                          "return": SessionLifecycleState})
        self.assertEqual(get_type_hints(queries.list_session_lifecycles),
                         {"workspace": Workspace, "classify_access_state": classify,
                          "return": tuple[SessionLifecycleState, ...]})
        self.assertEqual(get_type_hints(queries.session_lifecycle_from_manifest),
                         {"workspace": Workspace, "manifest": Mapping[str, Any], "classify_access_state": classify,
                          "return": SessionLifecycleState})
        for name in queries.__all__:
            self.assertNotIn(name, story_writing_agents.__all__)
            self.assertFalse(hasattr(story_writing_agents, name))
        self.assertIs(artifacts.Workspace, Workspace)
        self.assertIs(artifacts.SessionNotFoundError, SessionNotFoundError)
        self.assertIs(artifacts.WorkspaceFormatError, WorkspaceFormatError)
        self.assertIs(artifacts.WorkspaceVersionUnsupportedError, WorkspaceVersionUnsupportedError)
        self.assertIs(artifacts.validate_access_claim, access_records.validate_access_claim)
        self.assertIs(artifacts.validate_session_manifest, provider_thread_records.validate_session_manifest)
        self.assertEqual(get_type_hints(artifacts.write_manifest),
                         {"workspace": Workspace, "session_id": str, "value": Mapping[str, Any],
                          "overwrite": bool, "return": type(None)})
        for name in artifacts.__all__:
            self.assertNotIn(name, story_writing_agents.__all__)
            self.assertFalse(hasattr(story_writing_agents, name))
        self.assertIs(access_records.os, os)
        self.assertIs(access_records.socket, socket)
        self.assertIs(access_records.WorkspaceFormatError, WorkspaceFormatError)
        self.assertIs(access_records.WorkspaceVersionUnsupportedError, WorkspaceVersionUnsupportedError)
        self.assertIs(access_records.SessionAccessState, SessionAccessState)
        self.assertIs(access_records.parse_record_timestamp, provider_session_timestamps.parse_record_timestamp)
        self.assertIs(access_records.record_timestamp, provider_session_timestamps.record_timestamp)
        self.assertEqual(get_type_hints(access_records.new_access_claim),
                         {"session_id": str, "token": str, "return": dict[str, Any]})
        self.assertEqual(get_type_hints(access_records.unowned_access_state),
                         {"value": Mapping[str, Any], "return": SessionAccessState})
        self.assertEqual(get_type_hints(access_records.access_hostname_diagnostic),
                         {"hostname": str, "return": str})
        self.assertEqual(get_type_hints(access_records.validate_access_claim),
                         {"value": object, "session_id": str, "return": dict[str, Any]})
        for name in access_records.__all__:
            self.assertNotIn(name, story_writing_agents.__all__)
            self.assertFalse(hasattr(story_writing_agents, name))
        self.assertIs(provider_thread_records.parse_record_timestamp, provider_session_timestamps.parse_record_timestamp)
        self.assertEqual(get_type_hints(provider_thread_records.validate_thread_record),
                         {"value": Any, "return": dict[str, Any]})
        self.assertEqual(get_type_hints(provider_session_timestamps.record_timestamp), {"return": str})
        self.assertEqual(get_type_hints(provider_session_timestamps.parse_record_timestamp),
                         {"value": Any, "label": str, "return": datetime})
        self.assertEqual(get_type_hints(provider_session_timestamps.advance_record_timestamp),
                         {"previous": str, "candidate": str | None, "return": str})
        self.assertIs(provider_thread_records.WorkspaceFormatError, WorkspaceFormatError)
        self.assertIs(provider_session_timestamps.WorkspaceFormatError, WorkspaceFormatError)
        self.assertFalse(hasattr(backend_module.AgentBackend, "_validate_thread"))
        for name in (*provider_thread_records.__all__, *provider_session_timestamps.__all__):
            self.assertNotIn(name, story_writing_agents.__all__)
            self.assertFalse(hasattr(story_writing_agents, name))
        self.assertIs(get_type_hints(backend_reply_decoding.decode_reply)["phase"], models.Phase)
        self.assertIs(get_type_hints(backend_reply_decoding.decode_reply)["return"], models.AgentReply)
        self.assertIs(get_type_hints(backend_reply_decoding.decode_conversation_reply)["request"], models.ConversationRequest)
        self.assertIs(get_type_hints(backend_reply_decoding.decode_conversation_reply)["return"], models.ConversationReply)
        self.assertIs(get_type_hints(backend_reply_decoding.decode_conversation_retrieval_action)["request"], conversation_retrieval.ConversationRetrievalRequest)
        self.assertEqual(get_type_hints(backend_reply_decoding.decode_conversation_retrieval_action)["return"], conversation_retrieval.ConversationRetrievalAction)
        self.assertIs(get_type_hints(backend_reply_decoding.decode_conversation_retrieval_action)["action_index"], int)
        self.assertIs(backend_reply_decoding.BackendProtocolError, BackendProtocolError)
        self.assertIs(backend_module.backend_reply_decoding, backend_reply_decoding)
        phase_error = backend_reply_decoding.PhaseReplyConstraintError
        self.assertEqual((phase_error.__module__, phase_error.__qualname__), (backend_reply_decoding.__name__, "PhaseReplyConstraintError"))
        self.assertEqual(phase_error.__bases__, (BackendProtocolError,))
        self.assertEqual(tuple(signature(phase_error).parameters), ("rule",))
        self.assertIs(signature(phase_error).parameters["rule"].default, Parameter.empty)
        self.assertIs(signature(phase_error).parameters["rule"].kind, Parameter.POSITIONAL_OR_KEYWORD)
        self.assertEqual(get_type_hints(phase_error.__init__), {"rule": str, "return": type(None)})
        self.assertFalse(hasattr(backend_module, "_PhaseReplyConstraintError"))
        for name in ("_decode_reply", "_decode_conversation_reply", "_decode_conversation_retrieval_action"):
            self.assertFalse(hasattr(backend_module.AgentBackend, name))
        for name in ("decode_reply", "decode_conversation_reply", "decode_conversation_retrieval_action", "PhaseReplyConstraintError"):
            self.assertNotIn(name, story_writing_agents.__all__)
            self.assertFalse(hasattr(story_writing_agents, name))


class RagPackagingBoundaryTests(unittest.TestCase):
    def _assert_process_succeeded(self, result: subprocess.CompletedProcess[str], stage: str) -> None:
        self.assertEqual(
            result.returncode,
            0,
            f"{stage}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def test_required_metadata_lock_and_gitlinks_share_exact_pins(self) -> None:
        with (_REPOSITORY_ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)["project"]
        self.assertEqual(project["version"], "0.1.0")
        self.assertEqual(project["dependencies"], ["markdown-it-py>=4.2,<5", _JSONL_VIEWER_REQUIREMENT])
        self.assertEqual(
            project["optional-dependencies"],
            {'rag': [_GENERIC_RAG_REQUIREMENT], 'viewer': []},
        )

        with (_REPOSITORY_ROOT / "uv.lock").open("rb") as handle:
            lock = tomllib.load(handle)
        packages = {package["name"]: package for package in lock["package"]}
        generic = packages["generic-rag"]
        viewer = packages["jsonl-viewer"]
        self.assertEqual(generic["version"], "0.1.0")
        self.assertEqual(
            generic["source"]["git"],
            f"{_GENERIC_RAG_REPOSITORY}?rev={_GENERIC_RAG_PIN}#{_GENERIC_RAG_PIN}",)
        self.assertEqual(viewer["version"], _JSONL_VIEWER_VERSION)
        self.assertEqual(
            viewer["source"]["git"],
            f"{_JSONL_VIEWER_REPOSITORY}?rev={_JSONL_VIEWER_PIN}#{_JSONL_VIEWER_PIN}",)
        story = packages["story-writing-agents"]
        self.assertEqual(story["version"], "0.1.0")
        self.assertEqual(story["dependencies"], [{"name": "jsonl-viewer"}, {"name": "markdown-it-py"}])
        self.assertEqual(
            story["optional-dependencies"],
            {"rag": [{"name": "generic-rag"}]},)
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
                {"name": "markdown-it-py", "specifier": ">=4.2,<5"},
            ],)
        self.assertEqual(story["metadata"]["provides-extras"], ["rag", "viewer"])
        self.assertEqual((packages["markdown-it-py"]["version"], packages["markdown-it-py"]["dependencies"]), ("4.2.0", [{"name": "mdurl"}]))

        submodules = configparser.ConfigParser()
        submodules.read(_REPOSITORY_ROOT / ".gitmodules", encoding="utf-8")
        generic_section = 'submodule "vendor/generic-rag"'
        viewer_section = 'submodule "vendor/jsonl-viewer"'
        self.assertEqual(submodules.sections(), [generic_section, viewer_section])
        self.assertEqual(
            submodules[generic_section]["path"],
            "vendor/generic-rag",)
        self.assertEqual(
            submodules[generic_section]["url"],
            _GENERIC_RAG_REPOSITORY,)
        self.assertEqual(
            submodules[viewer_section]["path"],
            "vendor/jsonl-viewer",)
        self.assertEqual(
            submodules[viewer_section]["url"],
            _JSONL_VIEWER_REPOSITORY,)
        self.assertEqual(
            (_REPOSITORY_ROOT / "MANIFEST.in").read_text(encoding="utf-8"),
            "prune vendor/jsonl-viewer\n",)
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
                generic_project,)
            _export_gitlink_project(
                "vendor/jsonl-viewer",
                viewer_project,)
            story_distribution = root / "story-dist"
            generic_distribution = root / "generic-dist"
            viewer_distribution = root / "viewer-dist"
            story_distribution.mkdir()
            generic_distribution.mkdir()
            viewer_distribution.mkdir()
            story_wheel, story_sdist = _build_local_artifacts(
                story_project,
                story_distribution,)
            generic_wheel, _generic_sdist = _build_local_artifacts(
                generic_project,
                generic_distribution,)
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
            self.assertEqual(len(requires), 3)
            self.assertIn("markdown-it-py<5,>=4.2", requires)
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
            group_package_paths = (
            "story_writing_agents/codex_event_decoding.py",
            "story_writing_agents/provider_schema_selection.py",
            "story_writing_agents/provider_schema_patterns.py",
                "story_writing_agents/agent_context/group_observation.py",
                "story_writing_agents/agent_context/group_reporting.py",
                "story_writing_agents/lead_preparation/authoring_authority.py",
            "story_writing_agents/artifact_transactions/artifact_application_authority.py",
            "story_writing_agents/artifact_transactions/pending_story_commits.py",
            "story_writing_agents/artifact_transactions/prepared_story_commit.py",
            "story_writing_agents/artifact_transactions/story_commit_decisions.py",
            "story_writing_agents/collaboration_execution_results.py",
            "story_writing_agents/lead_control_events.py",
            "story_writing_agents/runtime_resources/generation_pin_cleanup.py",
                "story_writing_agents/antigravity_agent_definition.py",
            "story_writing_agents/antigravity_command.py",
            "story_writing_agents/antigravity_invocation_options.py",
            "story_writing_agents/antigravity_stream_observation.py",
            "story_writing_agents/provider_generation_adapters.py",
            "story_writing_agents/provider_configuration_candidate.py",
            "story_writing_agents/provider_configuration_activation.py",
            "story_writing_agents/provider_binding_admission.py",
            "story_writing_agents/backend_shutdown.py",
            "story_writing_agents/bound_agent_request_validation.py",
            "story_writing_agents/provider_legacy_thread_compatibility.py",
            "story_writing_agents/provider_startup_options.py",
            "story_writing_agents/provider_workspace_authority_lifecycle.py",
            "story_writing_agents/provider_invocation_accounting.py",
            "story_writing_agents/provider_binding_call_lifetime.py",
            "story_writing_agents/provider_bound_context.py",
            "story_writing_agents/provider_context_invocation.py",
            "story_writing_agents/provider_reference_read_lifecycle.py",
            "story_writing_agents/provider_structured_invocation.py",
            "story_writing_agents/bound_agent_logical_invocation.py",
            "story_writing_agents/provider_conversation_retrieval_loop.py",
            "story_writing_agents/provider_direct_structured_calls.py",
            "story_writing_agents/backend_diagnostic_events.py",
            "story_writing_agents/provider_invocation_routing.py",
            "story_writing_agents/provider_retrieval_handler_outcomes.py",
            "story_writing_agents/antigravity_native_output_schema.py",
            "story_writing_agents/antigravity_native_outcome.py",
            "story_writing_agents/antigravity_native_recovery.py",
            "story_writing_agents/antigravity_state_directories.py",
            "story_writing_agents/antigravity_recovery_matching.py",
                "story_writing_agents/antigravity_response_decoding.py",
            "story_writing_agents/lead_execution/confirmed_step_execution.py",
            "story_writing_agents/lead_execution/lead_step_decisions.py",
            "story_writing_agents/lead_execution/collaboration_result_publication.py",
            "story_writing_agents/lead_execution/known_result_finalization.py",
            "story_writing_agents/workspace/session_record_validation.py",
            "story_writing_agents/workspace/session_source_workspace.py",
            'story_writing_agents/provider_contract_guidance.py',
            'story_writing_agents/provider_message_catalog.py',
            'story_writing_agents/provider_request_mapping.py',
            'story_writing_agents/provider_response_mapping.py',
            'story_writing_agents/provider_schema_projection.py',
            "story_writing_agents/lead_preparation/recommendation_preparation.py",
            "story_writing_agents/lead_preparation/recommendation_requests.py",
            "story_writing_agents/lead_preparation/recommendation_decisions.py",
            "story_writing_agents/lead_preparation/premise_preparation.py",
            "story_writing_agents/lead_preparation/lead_task_control.py",
            "story_writing_agents/lead_preparation/specialist_advisory_values.py",
            "story_writing_agents/lead_preparation/proposal_basis_values.py",
            "story_writing_agents/lead_preparation/lead_material_inputs.py",
            "story_writing_agents/lead_preparation/lead_evidence_receipts.py",
            "story_writing_agents/lead_preparation/lead_proposal_inputs.py",
            "story_writing_agents/lead_preparation/lead_document_nodes.py",
            "story_writing_agents/lead_preparation/current_lead_evidence.py",
            "story_writing_agents/lead_preparation/document_plan_selection.py",
            "story_writing_agents/lead_preparation/document_plan_views.py",
            "story_writing_agents/lead_preparation/authoring_source_history.py",
            "story_writing_agents/lead_preparation/current_lead_inputs.py",
            "story_writing_agents/lead_preparation/current_proposal_authority.py",
            "story_writing_agents/lead_preparation/editorial_capture.py",
            "story_writing_agents/lead_preparation/lead_source_qualification.py",
            "story_writing_agents/lead_preparation/review_material_validation.py",
            "story_writing_agents/lead_preparation/collaboration_response_contracts.py",
            "story_writing_agents/lead_preparation/collaboration_response_decoding.py",
            "story_writing_agents/lead_preparation/lead_answer_authority.py",
            "story_writing_agents/lead_preparation/lead_front_values.py",
            "story_writing_agents/conversations/artifact_revision_sources.py",
            "story_writing_agents/consensus/provider_reply_mapping.py",
            "story_writing_agents/consensus/provider_request_policy.py",
            "story_writing_agents/consensus/authoring_qualification.py",
            "story_writing_agents/antigravity_rules.py",
            "story_writing_agents/backend_reply_decoding.py",
            "story_writing_agents/codex_command_preflight.py",
            "story_writing_agents/codex_definition_sources.py",
            "story_writing_agents/codex_stream_observation.py",
            "story_writing_agents/codex_invocation_files.py",
            "story_writing_agents/codex_native_outcome.py",
            "story_writing_agents/agent_exchanges/provider_finalization.py",
            "story_writing_agents/provider_session_access_records.py",
            "story_writing_agents/provider_session_access_transactions.py",
            "story_writing_agents/provider_context_thread_store.py",
            "story_writing_agents/conversation_retrieval_wire.py",
            "story_writing_agents/provider_direct_call_wire.py",
            "story_writing_agents/provider_execution_profiles.py",
            "story_writing_agents/provider_manifest_thread_store.py",
            "story_writing_agents/provider_session_artifacts.py",
            "story_writing_agents/provider_session_claim_guard.py",
            "story_writing_agents/provider_session_lifecycle_operations.py",
            "story_writing_agents/provider_session_lifecycle_queries.py",
            "story_writing_agents/provider_session_timestamps.py",
            "story_writing_agents/provider_session_transitions.py",
            "story_writing_agents/provider_thread_records.py",
            )
            with zipfile.ZipFile(story_wheel) as archive:
                for package_path in group_package_paths:
                    self.assertEqual(archive.read(package_path),
                        (_REPOSITORY_ROOT / "src" / package_path).read_bytes())
            with tarfile.open(story_sdist, "r:gz") as archive:
                for package_path in group_package_paths:
                    members = tuple(member for member in archive.getmembers()
                                    if member.name.endswith("/" + package_path))
                    self.assertEqual(len(members), 1)
                    with archive.extractfile(members[0]) as handle:
                        self.assertEqual(handle.read(),
                            (_REPOSITORY_ROOT / "src" / package_path).read_bytes())
            conversation_package_paths = (
                "story_writing_agents/artifact_transactions/__init__.py",
                "story_writing_agents/conversations/__init__.py",
                "story_writing_agents/conversations/catalog.py",
                "story_writing_agents/conversations/context.py",
                "story_writing_agents/conversations/events.py",
                "story_writing_agents/conversations/event_vocabulary.py",
                "story_writing_agents/conversations/event_value_validation.py",
                "story_writing_agents/lead_execution/__init__.py",
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

            runtime_wheels = tuple(_locked_runtime_wheels(root / "runtime-wheels").glob("*.whl"))
            damaged_target = root / "damaged-install"
            damaged_target.mkdir()
            _extract_wheels(damaged_target, (story_wheel, *runtime_wheels))
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
                assert len(story_writing_agents.__all__) == 106
                assert len(conversations.__all__) == 235
                assert len(conversation_events.__all__) == 97
                assert len(conversation_context.__all__) == 37
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
            self._assert_process_succeeded(damaged, 'damaged')

            optional_target = root / "optional-install"
            optional_target.mkdir()
            _extract_wheels(optional_target, (story_wheel, generic_wheel, viewer_wheel, *runtime_wheels))
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
            self._assert_process_succeeded(optional, 'optional')

            viewer_target = root / "viewer-install"
            viewer_target.mkdir()
            _extract_wheels(viewer_target, (story_wheel, viewer_wheel, *runtime_wheels))
            viewer_script = textwrap.dedent(
                f"""
                import sys
                sys.path.insert(0, {str(viewer_target)!r})
                from story_writing_agents.agent_exchanges import AgentExchangeScopeKind
                from story_writing_agents.interactive_controller._exchange_viewer import prepare_agent_exchange_view
                import jsonl_viewer
                assert jsonl_viewer.__all__ == ["ViewerHost", "ViewerSpec", "ViewerTerminal", "view_jsonl"]
                assert not any(
                    name == "generic_rag" or name.startswith("generic_rag.")
                    for name in sys.modules
                )
{_viewer_host_source((80, 24))}
                operation = prepare_agent_exchange_view(
                    b"",
                    session_id="session_1",
                    scope_kind=AgentExchangeScopeKind.CONVERSATION,
                    scope_id="conversation_1",
                    scope_subject="Subject",
                    agent_id="lead_orchestrator",
                )
                operation(transport)
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
            self._assert_process_succeeded(viewer, 'viewer')

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
            # _copy_build_input already exported the exact pin before any URL rewrite.
            mirror_environment = os.environ.copy()
            for name in ("GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG_PARAMETERS"):
                mirror_environment.pop(name, None)
            mirror_environment.update({"GIT_ALLOW_PROTOCOL": "file", "GIT_CONFIG_COUNT": "0"})
            viewer_source = root / "viewer-upstream.git"
            viewer_git_directory = root / "viewer-pinned.git"
            def viewer_git(directory, *arguments, input=None):
                prepared = subprocess.run(["git", "--git-dir", str(directory), *arguments],
                    input=input, env=mirror_environment,
                    text=True, capture_output=True, check=False)
                self._assert_process_succeeded(prepared, f"offline source {arguments!r}")
                return prepared.stdout.strip()
            for directory, source in ((viewer_source, _gitlink_git_directory("vendor/jsonl-viewer")),
                                      (viewer_git_directory, viewer_source)):
                viewer_git(directory, "init", "--bare", "--template=")
                viewer_git(directory, "fetch", "--depth=1", "--update-shallow", "--no-tags",
                           source.resolve().as_uri(), _JSONL_VIEWER_PIN)
                if directory == viewer_source:
                    tree = viewer_git(directory, "rev-parse", f"{_JSONL_VIEWER_PIN}^{{tree}}")
                    upstream = viewer_git(directory, "hash-object", "-t", "commit", "-w", "--stdin",
                        input=f"tree {tree}\nparent {_JSONL_VIEWER_PIN}\n"
                        "author Fixture <fixture@example.test> 0 +0000\n"
                        "committer Fixture <fixture@example.test> 0 +0000\n\nUpstream fixture\n")
                    viewer_git(directory, "update-ref", "refs/heads/upstream", upstream)
                    viewer_git(directory, "update-ref", "--no-deref", "HEAD", _JSONL_VIEWER_PIN)
                    (directory / "shallow").write_text(f"{_JSONL_VIEWER_PIN}\n{upstream}\n")
                    self.assertEqual(viewer_git(directory, "rev-parse", "HEAD"), _JSONL_VIEWER_PIN)
                    self.assertNotEqual(viewer_git(directory, "rev-parse", "refs/heads/upstream"), _JSONL_VIEWER_PIN)
                else:
                    viewer_git(directory, "update-ref", "refs/heads/pinned", _JSONL_VIEWER_PIN)
                    viewer_git(directory, "symbolic-ref", "HEAD", "refs/heads/pinned")
                    viewer_git(directory, "fsck", "--full", "--strict", "--no-dangling")
                    self.assertEqual(viewer_git(directory, "rev-parse", "HEAD"), _JSONL_VIEWER_PIN)
                    self.assertEqual((directory / "shallow").read_text().splitlines(), [_JSONL_VIEWER_PIN])
                    self.assertEqual(viewer_git(directory, "for-each-ref", "--format=%(objectname)"), _JSONL_VIEWER_PIN)
            self.assertFalse(any((viewer_git_directory / "objects" / "info" / name).exists()
                for name in ("alternates", "http-alternates")))
            self.assertEqual(tuple((viewer_git_directory / "objects" / "pack").glob("*.promisor")), ())
            mirror_config = (viewer_git_directory / "config").read_text(encoding="utf-8").lower()
            self.assertNotIn("promisor", mirror_config)
            self.assertNotIn("partialclone", mirror_config)
            build_support = root / "build-support"
            _copy_setuptools_build_support(build_support)
            install_environment = os.environ.copy()
            for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "GIT_OBJECT_DIRECTORY",
                         "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG_PARAMETERS"):
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
                    "PIP_FIND_LINKS": str(_locked_runtime_wheels(root / "runtime-wheels")),
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
                assert len(story_writing_agents.__all__) == 106
                assert len(conversations.__all__) == 235
                assert len(events.__all__) == 97
                assert len(context.__all__) == 37
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
                assert len(requirements) == 3
                assert "markdown-it-py<5,>=4.2" in requirements
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
{_viewer_host_source((80, 24), _VIEWER_SOURCE_ASSIGNMENT)}
                operation = prepare_agent_exchange_view(
                    source,
                    session_id="session_1",
                    scope_kind=AgentExchangeScopeKind.CONVERSATION,
                    scope_id="conversation_1",
                    scope_subject="Subject",
                    agent_id="lead_orchestrator",
                )
                operation(transport)
                assert host.closed == 1
                assert host.frames and "READ ONLY" in host.frames[0]
                assert source.endswith(b"\\n")
                import jsonl_viewer
                assert jsonl_viewer.__all__ == ["ViewerHost", "ViewerSpec", "ViewerTerminal", "view_jsonl"]
                """
            )
            modes = (
                ("wheel", [str(story_wheel)]),
                ("editable-base", ["-e", str(story_project)]),
                ("editable-viewer-extra", ["-e", f"{story_project}[viewer]"]),
            )
            for mode, target in modes:
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
                self._assert_process_succeeded(created, 'created')
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
                self._assert_process_succeeded(installed, 'installed')
                checked = subprocess.run(
                    [str(python), "-m", "pip", "check"],
                    env=install_environment,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self._assert_process_succeeded(checked, 'checked')
                verified = subprocess.run(
                    [str(python), "-I", "-W", "error", "-c", probe, mode],
                    env=install_environment,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self._assert_process_succeeded(verified, 'verified')

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
            self._assert_process_succeeded(created, 'created')
            python = environment / "bin" / "python"
            build_support = root / "build-support"
            _copy_setuptools_build_support(build_support)
            viewer_git_directory = root / "viewer-upgrade.git"
            source_commands = (
                ["git", "clone", "--bare", _JSONL_VIEWER_REPOSITORY, str(viewer_git_directory)],
                ["git", "--git-dir", str(viewer_git_directory), "cat-file", "-e", _OLD_JSONL_VIEWER_PIN + "^{commit}"],
                ["git", "--git-dir", str(viewer_git_directory), "cat-file", "-e", _JSONL_VIEWER_PIN + "^{commit}"],
                ["git", "--git-dir", str(viewer_git_directory), "fsck", "--full", "--no-dangling"],
            )
            for command in source_commands:
                prepared = subprocess.run(command, text=True, capture_output=True, check=False)
                self.assertEqual(prepared.returncode, 0, f"{command!r}\n{prepared.stdout}\n{prepared.stderr}")
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
                    "PIP_FIND_LINKS": str(_locked_runtime_wheels(root / "runtime-wheels")),
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
                next(
                    requirement
                    for requirement in tomllib.loads((story_project / "pyproject.toml").read_text())["project"]["dependencies"]
                    if requirement.startswith("markdown-it-py")
                ),
            ]
            seeded = subprocess.run(
                seed_command,
                env=install_environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self._assert_process_succeeded(seeded, 'seeded')
            old_probe = textwrap.dedent(
                f"""
                import importlib.metadata
                import json
                viewer = importlib.metadata.distribution("jsonl-viewer")
                assert viewer.version == "0.7.0"
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
                from story_writing_agents.interactive_controller._exchange_viewer import prepare_agent_exchange_view
                prepared = prepare_agent_exchange_view(
                    b"", session_id="s", scope_kind=AgentExchangeScopeKind.CONVERSATION,
                    scope_id="c", scope_subject="Subject", agent_id="lead_orchestrator",
                )
                assert callable(prepared)
                """
            )
            old_verified = subprocess.run(
                [str(python), "-I", "-W", "error", "-c", old_probe],
                env=install_environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self._assert_process_succeeded(old_verified, 'old_verified')

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
            self._assert_process_succeeded(upgraded, 'upgraded')
            checked = subprocess.run(
                [str(python), "-m", "pip", "check"],
                env=install_environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self._assert_process_succeeded(checked, 'checked')
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
{_viewer_host_source((240, 40))}
                operation = prepare_agent_exchange_view(
                    source,
                    session_id="session_1",
                    scope_kind=AgentExchangeScopeKind.CONVERSATION,
                    scope_id="conversation_1",
                    scope_subject="Upgrade subject",
                    agent_id="character_custodian",
                )
                operation(transport)
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
                assert frame.count('[expanded JSON string ×1] {{') == 2, frame
                assert '[JSON expansion skipped:' not in frame, frame
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
                searched_host = SearchHost(); active_host = searched_host
                prepare_agent_exchange_view(
                    searched_source, session_id="session_1",
                    scope_kind=AgentExchangeScopeKind.CONVERSATION,
                    scope_id="conversation_1", scope_subject="Upgrade subject",
                    agent_id="lead_orchestrator",
                )(transport)
                assert searched_host.closed == 1
                assert "Occurrence 1/14" in searched_host.frames[-1] and "Search: '개요'" in searched_host.frames[-1] and "In: All text" in searched_host.frames[-1]
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
            self._assert_process_succeeded(verified, 'verified')


if __name__ == "__main__":
    unittest.main()
