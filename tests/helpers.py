"""Shared deterministic fixtures for the standard-library test suite."""

from __future__ import annotations

import asyncio
import atexit
import hashlib
import inspect
import os
import shutil
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias
from unittest import TestCase
from unittest import mock

from story_writing_agents import (
    AgentDefinition,
    AgentReply,
    AgentRequest,
    Phase,
    SPECIALIST_IDS,
    SessionAccessState,
    SessionDurableState,
    SessionLifecycleState,
    Workspace,
)
from story_writing_agents.agents import CORE_DOCUMENTS
from story_writing_agents.git.session_contracts import (
    GIT_SESSION_WORKTREE_SIBLING_SUFFIX,
)
from story_writing_agents.workspace.initialization import (
    WorkspaceRootInspection,
    WorkspaceRootRole,
    WorkspaceRootState,
)


HandlerResult: TypeAlias = AgentReply | BaseException | Awaitable[AgentReply]
RequestHandler: TypeAlias = Callable[[AgentRequest], HandlerResult]


SUPPORTED_ARRAY_ITEMS_SCHEMA_VALIDATOR_SOURCE = r"""def schema_uses_supported_array_items(specification):
    if isinstance(specification, bool):
        return True
    if not isinstance(specification, dict):
        return False
    if specification.get("type") == "array":
        items = specification.get("items")
        if not isinstance(items, (dict, bool)):
            return False
        if "additionalItems" in specification or "prefixItems" in specification:
            return False
    for key, value in specification.items():
        if key == "properties":
            if not isinstance(value, dict) or not all(
                schema_uses_supported_array_items(child)
                for child in value.values()
            ):
                return False
        elif key == "items" and not schema_uses_supported_array_items(value):
            return False
        elif key == "anyOf":
            if not isinstance(value, list) or not value or not all(
                schema_uses_supported_array_items(child) for child in value
            ):
                return False
    return True
"""


_TEST_GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "Story Test Author",
    "GIT_AUTHOR_EMAIL": "story-author@example.invalid",
    "GIT_COMMITTER_NAME": "Story Test Committer",
    "GIT_COMMITTER_EMAIL": "story-committer@example.invalid",
}


@contextmanager
def workspace_git_identity():
    """Supply deterministic effective identity only during test initialization."""

    with mock.patch.dict(os.environ, _TEST_GIT_IDENTITY, clear=False):
        yield


def initialize_test_workspace(root: str | os.PathLike[str]) -> Workspace:
    """REAL tier: run exact v2 initialization without developer Git config."""

    with workspace_git_identity():
        return Workspace(root).initialize()


@dataclass(frozen=True, slots=True)
class WorkspaceFixtureTiming:
    """One process-local fixture phase measurement for diagnostic tests."""

    materialization_id: int
    trust_tier: str
    phase: str
    seconds: float


@dataclass(slots=True)
class _WorkspaceTemplate:
    container: Path
    root: Path
    digest: str
    process_id: int
    first_copy_validated: bool = False


_WORKSPACE_TEMPLATE: _WorkspaceTemplate | None = None
_WORKSPACE_TEMPLATE_LOCK = threading.RLock()
_WORKSPACE_FIXTURE_TIMINGS: list[WorkspaceFixtureTiming] = []
_WORKSPACE_MATERIALIZATION_SEQUENCE = 0
_RUNTIME_STATE_ROOTS = (
    "debug_logs",
    "discussion_logs",
    "manuscript",
    "agent_states/orchestrator_state/sessions",
    "agent_states/orchestrator_state/provider_sessions",
)


def _record_workspace_fixture_timing(
    materialization_id: int,
    trust_tier: str,
    phase: str,
    started_at: float,
) -> None:
    _WORKSPACE_FIXTURE_TIMINGS.append(
        WorkspaceFixtureTiming(
            materialization_id,
            trust_tier,
            phase,
            max(0.0, time.perf_counter() - started_at),
        )
    )


def _workspace_fixture_timing_snapshot() -> tuple[WorkspaceFixtureTiming, ...]:
    """Return timings without exposing the sealed template or its path."""

    with _WORKSPACE_TEMPLATE_LOCK:
        return tuple(_WORKSPACE_FIXTURE_TIMINGS)


def _workspace_template_digest() -> str:
    """Return only the sealed digest, never the template location."""

    with _WORKSPACE_TEMPLATE_LOCK:
        return _workspace_template().digest


def _tree_digest(root: Path) -> str:
    """Digest every relative name, lstat type/mode, and file/link content."""

    digest = hashlib.sha256()

    def add(value: bytes) -> None:
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)

    def visit(path: Path, relative: str) -> None:
        observed = path.lstat()
        file_type = stat.S_IFMT(observed.st_mode)
        add(relative.encode("utf-8"))
        add(str(file_type).encode("ascii"))
        add(str(stat.S_IMODE(observed.st_mode)).encode("ascii"))
        if stat.S_ISREG(observed.st_mode):
            add(path.read_bytes())
        elif stat.S_ISLNK(observed.st_mode):
            add(os.readlink(path).encode("utf-8"))
        elif stat.S_ISDIR(observed.st_mode):
            for child in sorted(path.iterdir(), key=lambda item: item.name):
                child_relative = child.name if relative == "." else f"{relative}/{child.name}"
                visit(child, child_relative)
        else:
            raise AssertionError(f"workspace fixture contains an unsafe entry: {relative}")

    visit(root, ".")
    return "sha256:" + digest.hexdigest()


def _require_clean_workspace_template(root: Path) -> None:
    """Reject session, lock, SQLite, and application artifacts before sealing."""

    session_root = root.parent / f"{root.name}{GIT_SESSION_WORKTREE_SIBLING_SUFFIX}"
    if session_root.exists() or (root / ".git/worktrees").exists():
        raise AssertionError("workspace fixture template contains session worktrees")
    for path in root.rglob("*"):
        name = path.name
        if name.endswith((".lock", "-wal", "-shm", "-journal")):
            raise AssertionError(f"workspace fixture template contains runtime state: {name}")
    for relative in _RUNTIME_STATE_ROOTS:
        state_root = root / relative
        if not state_root.exists():
            continue
        unexpected = [
            path for path in state_root.rglob("*")
            if path.is_file() and path.name != ".gitkeep"
        ]
        if unexpected:
            raise AssertionError(
                f"workspace fixture template contains application state: {relative}"
            )


def _workspace_template() -> _WorkspaceTemplate:
    global _WORKSPACE_TEMPLATE
    if (
        _WORKSPACE_TEMPLATE is not None
        and _WORKSPACE_TEMPLATE.process_id != os.getpid()
    ):
        _WORKSPACE_TEMPLATE = None
    if _WORKSPACE_TEMPLATE is not None:
        if _tree_digest(_WORKSPACE_TEMPLATE.root) != _WORKSPACE_TEMPLATE.digest:
            raise AssertionError("sealed workspace fixture template changed")
        return _WORKSPACE_TEMPLATE
    started_at = time.perf_counter()
    container = Path(tempfile.mkdtemp(prefix="story-workspace-template-"))
    root = Path(os.path.realpath(container)) / "workspace"
    try:
        initialize_test_workspace(root)
        _require_clean_workspace_template(root)
        template = _WorkspaceTemplate(
            container,
            root,
            _tree_digest(root),
            os.getpid(),
        )
    except BaseException:
        shutil.rmtree(container, ignore_errors=True)
        raise
    _WORKSPACE_TEMPLATE = template
    atexit.register(_cleanup_workspace_template, template.process_id, container)
    _record_workspace_fixture_timing(0, "real", "template_prepare", started_at)
    return template


def _cleanup_workspace_template(process_id: int, container: Path) -> None:
    """Remove only the template created by this exact process."""

    if os.getpid() == process_id:
        shutil.rmtree(container, ignore_errors=True)


def _bind_guarded_ready_workspace(root: Path, expected_digest: str) -> Workspace:
    """Bind one exact copied baseline to fresh root identity without rediscovery."""

    if _tree_digest(root) != expected_digest:
        raise AssertionError("copied workspace fixture differs from its sealed template")
    observed = root.lstat()
    if not stat.S_ISDIR(observed.st_mode) or observed.st_dev <= 0 or observed.st_ino <= 0:
        raise AssertionError("copied workspace fixture root is not a safe directory")
    identity = (observed.st_dev, observed.st_ino)
    inspection = WorkspaceRootInspection(
        state=WorkspaceRootState.READY,
        identity=identity,
        role=WorkspaceRootRole.MAIN,
        session_id=None,
        main_root=root,
    )
    workspace = Workspace.__new__(Workspace)
    workspace.root = root
    workspace._root_inspection = inspection
    workspace._root_identity = identity
    workspace._root_role = WorkspaceRootRole.MAIN
    workspace._session_id = None
    workspace._main_root = root
    workspace._ready = True
    workspace._initialization_failed = False
    return workspace


def _require_equivalent_ready_shape(
    validated: Workspace,
    guarded: Workspace,
) -> None:
    attributes = (
        "root",
        "_root_inspection",
        "_root_identity",
        "_root_role",
        "_session_id",
        "_main_root",
        "_ready",
        "_initialization_failed",
    )
    if any(getattr(validated, name) != getattr(guarded, name) for name in attributes):
        raise AssertionError("guarded workspace fixture binding differs from real reopening")


def _copied_test_workspace(
    test_case: TestCase,
    *,
    trust_tier: str,
) -> tuple[Workspace, int]:
    """Materialize an independent baseline through validated or guarded trust."""

    global _WORKSPACE_MATERIALIZATION_SEQUENCE
    if trust_tier not in {"validated", "guarded"}:
        raise ValueError("workspace fixture trust tier is invalid")
    with _WORKSPACE_TEMPLATE_LOCK:
        template = _workspace_template()
        overall_started = time.perf_counter()
        _WORKSPACE_MATERIALIZATION_SEQUENCE += 1
        materialization_id = _WORKSPACE_MATERIALIZATION_SEQUENCE
        integrity_started = time.perf_counter()
        if _tree_digest(template.root) != template.digest:
            raise AssertionError("sealed workspace fixture template changed before copying")
        _record_workspace_fixture_timing(
            materialization_id, trust_tier, "integrity_before", integrity_started
        )
        owner = tempfile.TemporaryDirectory(prefix="story-workspace-copy-")
        target = Path(os.path.realpath(owner.name)) / "workspace"
        try:
            copy_started = time.perf_counter()
            shutil.copytree(template.root, target, copy_function=shutil.copy2, symlinks=True)
            _record_workspace_fixture_timing(materialization_id, trust_tier, "copy", copy_started)
            bind_started = time.perf_counter()
            guarded = _bind_guarded_ready_workspace(target, template.digest)
            _record_workspace_fixture_timing(materialization_id, trust_tier, "bind", bind_started)
            validated: Workspace | None = None
            if trust_tier == "validated" or not template.first_copy_validated:
                validation_started = time.perf_counter()
                validated = Workspace(target).require_ready()
                _record_workspace_fixture_timing(
                    materialization_id, trust_tier, "normal_validation", validation_started
                )
                _require_equivalent_ready_shape(validated, guarded)
                template.first_copy_validated = True
            integrity_started = time.perf_counter()
            if _tree_digest(template.root) != template.digest:
                raise AssertionError("sealed workspace fixture template changed after copying")
            _record_workspace_fixture_timing(
                materialization_id, trust_tier, "integrity_after", integrity_started
            )
        except BaseException:
            owner.cleanup()
            raise

    def cleanup() -> None:
        cleanup_started = time.perf_counter()
        owner.cleanup()
        with _WORKSPACE_TEMPLATE_LOCK:
            _record_workspace_fixture_timing(
                materialization_id, trust_tier, "cleanup", cleanup_started
            )

    test_case.addCleanup(cleanup)
    with _WORKSPACE_TEMPLATE_LOCK:
        _record_workspace_fixture_timing(
            materialization_id, trust_tier, "materialize", overall_started
        )
    workspace = validated if validated is not None and trust_tier == "validated" else guarded
    return workspace, materialization_id


def validated_initialized_workspace(test_case: TestCase) -> Workspace:
    """VALIDATED tier: copy the baseline and reopen through normal validation."""

    workspace, _materialization_id = _copied_test_workspace(
        test_case, trust_tier="validated"
    )
    return workspace


def initialized_workspace(test_case: TestCase) -> Workspace:
    """GUARDED tier: copy an already-ready workspace and add explicit core inputs.

    Workspace initialization deliberately does not author core writing
    artifacts. Tests that exercise story-agent work supply those inputs as
    fixtures instead of relying on legacy generated placeholders.
    """

    workspace, materialization_id = _copied_test_workspace(
        test_case, trust_tier="guarded"
    )
    mutation_started = time.perf_counter()
    for filename in CORE_DOCUMENTS:
        # These are deliberately unprovenanced fixture inputs. Runtime code
        # cannot create canonical writing artifacts through generic Workspace
        # writes; repository-focused tests publish their own verified bytes.
        workspace.path(f"core_documents/{filename}").write_text(
            f"test input for {filename}\n",
            encoding="utf-8",
        )
    with _WORKSPACE_TEMPLATE_LOCK:
        _record_workspace_fixture_timing(
            materialization_id,
            "guarded",
            "fixture_mutation",
            mutation_started,
        )
    return workspace


def pristine_initialized_workspace(test_case: TestCase) -> Workspace:
    """GUARDED tier: copy one clean baseline for already-ready preconditions."""

    workspace, _materialization_id = _copied_test_workspace(
        test_case, trust_tier="guarded"
    )
    return workspace


def core_snapshot(workspace: Workspace) -> dict[str, str]:
    """Return raw fixture-only bytes for the full allowlisted core state."""

    return {
        filename: workspace.path(f"core_documents/{filename}").read_text(
            encoding="utf-8"
        )
        for filename in CORE_DOCUMENTS
    }


class _DelegatingBoundAgent:
    """Bind one fake backend to exactly one runtime agent definition."""

    def __init__(
        self,
        backend: BindingBackendMixin,
        definition: AgentDefinition,
    ) -> None:
        self.backend = backend
        self.definition = definition

    async def invoke(self, request: AgentRequest) -> AgentReply:
        if request.agent_id != self.definition.agent_id:
            raise AssertionError(
                "request was routed through the wrong bound agent: "
                f"{request.agent_id} != {self.definition.agent_id}"
            )
        self.backend.bound_invocations.append((self.definition, request))
        return await self.backend.invoke(request)


class BindingBackendMixin:
    """Give deterministic test backends binding and lifecycle contracts."""

    definitions: list[AgentDefinition]
    bound_invocations: list[tuple[AgentDefinition, AgentRequest]]

    async def bind(self, definition: AgentDefinition) -> _DelegatingBoundAgent:
        if not hasattr(self, "definitions"):
            self.definitions = []
        if not hasattr(self, "bound_invocations"):
            self.bound_invocations = []
        self.definitions.append(definition)
        return _DelegatingBoundAgent(self, definition)

    async def invoke(self, request: AgentRequest) -> AgentReply:
        raise NotImplementedError

    def validate_provider_configuration(self, _session_id: str) -> bool:
        """Return the exact provider-ready fact used by neutral test doubles."""

        return True

    def get_session_lifecycle(self, session_id: str) -> SessionLifecycleState:
        """Return deterministic ready access already held by this test double."""

        return self._test_lifecycle(session_id, held=True)

    def acquire_session_access(self, session_id: str) -> SessionLifecycleState:
        """Return deterministic ready access held by this test double."""

        return self._test_lifecycle(session_id, held=True)

    def release_session_access(self, session_id: str) -> SessionLifecycleState:
        """Return deterministic ready state after releasing test-double access."""

        return self._test_lifecycle(session_id, held=False)

    @staticmethod
    def _test_lifecycle(
        session_id: str,
        *,
        held: bool,
    ) -> SessionLifecycleState:
        """Build one exact immutable lifecycle result without external work."""

        return SessionLifecycleState(
            session_id=session_id,
            durable_state=SessionDurableState.READY,
            access_state=(
                SessionAccessState.HELD_HERE if held else SessionAccessState.CLAIMABLE
            ),
            owner_hostname="test-runtime" if held else None,
            owner_pid=1 if held else None,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )


class ScriptedBackend(BindingBackendMixin):
    """Record requests and answer them with a deterministic handler."""

    def __init__(self, handler: RequestHandler) -> None:
        self.handler = handler
        self.definitions: list[AgentDefinition] = []
        self.bound_invocations: list[tuple[AgentDefinition, AgentRequest]] = []
        self.requests: list[AgentRequest] = []

    async def invoke(self, request: AgentRequest) -> AgentReply:
        self.requests.append(request)
        try:
            result = self.handler(request)
        except AssertionError as exc:
            neutral_unhandled_phase = str(exc) in {
                str(request.phase),
                f"unexpected phase: {request.phase}",
            }
            if request.phase is not Phase.DRAFT or not neutral_unhandled_phase:
                raise
            # Supplied drafts are no longer treated as already-authored
            # candidates. Most workflow tests are concerned with a later
            # phase, so their neutral backend still performs the required
            # execution-specialist authoring hop without duplicating it in
            # every handler.
            result = AgentReply.candidate(
                request.revision_id,
                request.user_payload or "agent-authored candidate",
            )
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, BaseException):
            raise result
        return result

    def close(self) -> None:
        """Match the concrete backend lifecycle for CLI substitution tests."""


class ReverseCompletionApprovalBackend(BindingBackendMixin):
    """Prove audits overlap while completing them opposite roster order."""

    def __init__(self) -> None:
        self.definitions: list[AgentDefinition] = []
        self.bound_invocations: list[tuple[AgentDefinition, AgentRequest]] = []
        self.requests: list[AgentRequest] = []
        self.arrivals: list[str] = []
        self.completions: list[str] = []
        self._all_arrived = asyncio.Event()
        self._execution_done = asyncio.Event()
        self._character_done = asyncio.Event()

    async def invoke(self, request: AgentRequest) -> AgentReply:
        self.requests.append(request)
        if request.phase is Phase.DRAFT:
            return AgentReply.candidate(request.revision_id, request.user_payload)
        if request.phase is Phase.AUDIT:
            self.arrivals.append(request.agent_id)
            if len(self.arrivals) == len(SPECIALIST_IDS):
                self._all_arrived.set()
            await asyncio.wait_for(self._all_arrived.wait(), timeout=1)

            if request.agent_id == SPECIALIST_IDS[2]:
                self.completions.append(request.agent_id)
                self._execution_done.set()
            elif request.agent_id == SPECIALIST_IDS[1]:
                await asyncio.wait_for(self._execution_done.wait(), timeout=1)
                self.completions.append(request.agent_id)
                self._character_done.set()
            else:
                await asyncio.wait_for(self._character_done.wait(), timeout=1)
                self.completions.append(request.agent_id)
            return AgentReply.approval(request.revision_id)

        if request.phase is Phase.FINALIZE:
            return AgentReply.finalization(request.revision_id)
        raise AssertionError(f"unexpected phase: {request.phase}")


def phase_requests(
    backend: ScriptedBackend | ReverseCompletionApprovalBackend,
    phase: Phase,
) -> list[AgentRequest]:
    """Select recorded requests for one phase."""

    return [request for request in backend.requests if request.phase is phase]


def all_manuscripts(workspace: Workspace) -> list[Path]:
    """List committed manuscript files, excluding workspace sentinels."""

    return sorted(
        path
        for path in workspace.path("manuscript").rglob("*")
        if path.is_file() and path.name != ".gitkeep"
    )
