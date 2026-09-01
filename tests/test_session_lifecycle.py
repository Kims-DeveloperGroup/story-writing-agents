"""Strict clean-break provider-session lifecycle tests."""

from __future__ import annotations

import ast
import errno
import inspect
import json
import multiprocessing
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError, fields
from enum import StrEnum
from pathlib import Path
from typing import Any
from unittest import mock

import story_writing_agents
from story_writing_agents import (
    AgentBackend,
    BackendProtocolError,
    SessionAccessBlockedError,
    SessionAccessState,
    SessionDurableState,
    SessionExistsError,
    SessionLifecycleState,
    SessionNotFoundError,
    SessionOwnershipUnknownError,
    WorkspaceFormatError,
    WorkspaceVersionUnsupportedError,
    provider_work_is_ready,
)
from story_writing_agents import backend as backend_module
from story_writing_agents import errors as errors_module
from story_writing_agents import models as models_module
from story_writing_agents import session_lifecycle as lifecycle_module
from story_writing_agents.debug_log import (
    DEBUG_LOG_RELATIVE_PATH,
    WorkspaceDebugLog,
)

from tests.helpers import initialized_workspace


_SESSION_DIRECTORY = "agent_states/orchestrator_state/provider_sessions"
_ACCESS_GUARD = f"{_SESSION_DIRECTORY}/.access-claims.lock"
_CREATED = "2026-01-01T00:00:00+00:00"
_UPDATED = "2026-01-02T00:00:00+00:00"
_NEWEST = "2026-01-03T00:00:00+00:00"


def _manifest_relative(session_id: str) -> str:
    """Return the canonical V2 manifest path for ``session_id``."""

    return f"{_SESSION_DIRECTORY}/{session_id}.json"


def _access_relative(session_id: str) -> str:
    """Return the canonical V2 access-claim path for ``session_id``."""

    return f"{_SESSION_DIRECTORY}/{session_id}.access.json"


def _thread(*, updated_at: str = _UPDATED) -> dict[str, Any]:
    """Build one strict persisted provider-thread record."""

    return {
        "backend": "codex",
        "agent_id": "plot_architect",
        "fingerprint": "f" * 64,
        "thread_id": "thread-retained",
        "tainted": False,
        "updated_at": updated_at,
    }


def _manifest(
    session_id: str,
    *,
    durable_state: str = "ready",
    created_at: str = _CREATED,
    updated_at: str = _UPDATED,
    provider_threads: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one exact V2 lifecycle manifest."""

    return {
        "schema_version": 2,
        "session_id": session_id,
        "durable_state": durable_state,
        "created_at": created_at,
        "updated_at": updated_at,
        "provider_threads": dict(provider_threads or {}),
    }


def _access(
    session_id: str,
    *,
    hostname: str | None = None,
    pid: int | None = None,
    token: str = "foreign-token",
    claimed_at: str = _UPDATED,
) -> dict[str, Any]:
    """Build one exact V2 access claim."""

    return {
        "schema_version": 2,
        "session_id": session_id,
        "owner_hostname": socket.gethostname() if hostname is None else hostname,
        "owner_pid": os.getpid() if pid is None else pid,
        "claim_token": token,
        "claimed_at": claimed_at,
    }


def _state(**changes: Any) -> SessionLifecycleState:
    """Build one canonical ready and claimable lifecycle value."""

    values: dict[str, Any] = {
        "session_id": "session",
        "durable_state": SessionDurableState.READY,
        "access_state": SessionAccessState.CLAIMABLE,
        "owner_hostname": None,
        "owner_pid": None,
        "created_at": _CREATED,
        "updated_at": _UPDATED,
    }
    values.update(changes)
    return SessionLifecycleState(**values)


def _artifact_snapshot(path: Path) -> tuple[bool, bytes | None, int | None]:
    """Capture existence, bytes, and modification time for one artifact."""

    if not path.exists():
        return False, None, None
    return True, path.read_bytes(), path.stat().st_mtime_ns


def _tree_snapshot(directory: Path) -> dict[str, tuple[bytes, int]]:
    """Capture every non-diagnostic regular file below ``directory``."""

    return {
        str(path.relative_to(directory)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(directory.rglob("*"))
        if path.is_file() and "debug_logs" not in path.relative_to(directory).parts
    }


def _debug_records(workspace: Any) -> list[dict[str, Any]]:
    """Return every currently persisted diagnostic record."""

    path = workspace.path(DEBUG_LOG_RELATIVE_PATH)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _cleanup_rows_since(
    workspace: Any,
    mark: int,
) -> list[tuple[object, ...]]:
    """Project backend cleanup records after one exact log position."""

    return [
        (
            record["level"],
            record["phase"],
            record.get("outcome"),
            record.get("session_id"),
        )
        for record in _debug_records(workspace)[mark:]
        if record["component"] == "backend" and record["operation"] == "cleanup"
    ]


def _race_for_dead_claim(
    workspace_root: str,
    ready: Any,
    start: Any,
    release: Any,
    results: Any,
) -> None:
    """Contend for a dead claim while keeping the winning process live."""

    backend = AgentBackend(workspace_root, executable=sys.executable)
    ready.put(True)
    if not start.wait(10):
        results.put(("timeout",))
        return
    try:
        state = backend.acquire_session_access("stale_race")
        token = backend._access_claim_tokens["stale_race"]
        results.put(("held", token, state.access_state.value))
        release.wait(10)
    except Exception as exc:
        results.put(("error", type(exc).__name__, str(exc)))
    finally:
        backend.close()


class SessionLifecycleContractTests(unittest.TestCase):
    """Verify the pure canonical lifecycle and public boundary."""

    def test_exact_enums_dataclass_exports_and_backend_protocol(self) -> None:
        self.assertTrue(issubclass(SessionDurableState, StrEnum))
        self.assertTrue(issubclass(SessionAccessState, StrEnum))
        self.assertEqual(
            [(item.name, item.value) for item in SessionDurableState],
            [("ABSENT", "absent"), ("READY", "ready"), ("PAUSED", "paused")],
        )
        self.assertEqual(
            [(item.name, item.value) for item in SessionAccessState],
            [
                ("CLAIMABLE", "claimable"),
                ("HELD_HERE", "held_here"),
                ("BLOCKED_BY_LIVE_LOCAL_OWNER", "blocked_by_live_local_owner"),
                ("OWNERSHIP_UNKNOWN", "ownership_unknown"),
            ],
        )
        names = [field.name for field in fields(SessionLifecycleState)]
        self.assertEqual(
            names,
            [
                "session_id",
                "durable_state",
                "access_state",
                "owner_hostname",
                "owner_pid",
                "created_at",
                "updated_at",
            ],
        )
        signature = inspect.signature(SessionLifecycleState)
        self.assertTrue(
            all(
                item.kind is inspect.Parameter.KEYWORD_ONLY
                for item in signature.parameters.values()
            )
        )
        self.assertEqual(SessionLifecycleState.__match_args__, ())
        self.assertEqual(
            lifecycle_module.__all__,
            [
                "SessionAccessState",
                "SessionDurableState",
                "SessionLifecycleState",
                "provider_work_is_ready",
            ],
        )
        self.assertIs(story_writing_agents.SessionLifecycleState, SessionLifecycleState)
        self.assertEqual(len(story_writing_agents.__all__), 104)

        expected = {
            "get_session_lifecycle": ("self", "session_id"),
            "list_session_lifecycles": ("self",),
            "validate_provider_configuration": ("self", "session_id"),
            "acquire_session_access": ("self", "session_id"),
            "release_session_access": ("self", "session_id"),
            "create_session_with_access": ("self", "session_id"),
            "reopen_session_with_access": ("self", "session_id"),
            "pause_session_and_release_access": ("self", "session_id"),
        }
        for method_name, parameters in expected.items():
            with self.subTest(method=method_name):
                method = getattr(AgentBackend, method_name)
                self.assertEqual(
                    tuple(inspect.signature(method).parameters), parameters
                )

    def test_old_lifecycle_surface_is_absent_without_aliases(self) -> None:
        old_names = (
            "Session" + "LeaseState",
            "Session" + "Summary",
            "Session" + "InUseError",
            "session_summary_from_" + "lifecycle",
            "lifecycle_from_session_" + "summary",
        )
        for owner in (
            story_writing_agents,
            lifecycle_module,
            models_module,
            errors_module,
        ):
            for name in old_names:
                with self.subTest(owner=owner.__name__, name=name):
                    self.assertFalse(hasattr(owner, name))
        old_methods = (
            "get_session_" + "summary",
            "list_" + "sessions",
            "prepare_" + "session",
            "attach_" + "session",
            "detach_" + "session",
            "start_" + "session",
            "resume_" + "session",
            "end_" + "session",
        )
        for name in old_methods:
            self.assertFalse(hasattr(AgentBackend, name), name)

    def test_valid_state_matrix_is_immutable_and_hashable(self) -> None:
        persisted = (SessionDurableState.READY, SessionDurableState.PAUSED)
        access_states = tuple(SessionAccessState)
        rows = [
            _state(
                durable_state=SessionDurableState.ABSENT,
                created_at=None,
                updated_at=None,
            )
        ]
        for durable in persisted:
            for access in access_states:
                owner_required = access is not SessionAccessState.CLAIMABLE
                rows.append(
                    _state(
                        durable_state=durable,
                        access_state=access,
                        owner_hostname="owner.example" if owner_required else None,
                        owner_pid=101 if owner_required else None,
                    )
                )
            rows.append(
                _state(
                    durable_state=durable,
                    owner_hostname="dead-owner.example",
                    owner_pid=102,
                )
            )
        self.assertEqual(len(rows), 11)
        self.assertEqual(len(set(rows)), 11)
        with self.assertRaises(FrozenInstanceError):
            rows[0].session_id = "changed"  # type: ignore[misc]

    def test_invalid_state_facts_fail_exactly(self) -> None:
        invalid_rows = (
            ({"session_id": ""}, ValueError),
            ({"session_id": 1}, TypeError),
            ({"durable_state": "ready"}, TypeError),
            ({"access_state": "claimable"}, TypeError),
            ({"owner_hostname": "owner"}, ValueError),
            ({"owner_hostname": "", "owner_pid": 1}, ValueError),
            ({"owner_hostname": "owner", "owner_pid": True}, TypeError),
            ({"owner_hostname": "owner", "owner_pid": 0}, ValueError),
            ({"access_state": SessionAccessState.HELD_HERE}, ValueError),
            ({"created_at": None}, ValueError),
            ({"created_at": "2026-01-01T00:00:00"}, ValueError),
            ({"updated_at": "2025-01-01T00:00:00+00:00"}, ValueError),
            (
                {
                    "durable_state": SessionDurableState.ABSENT,
                    "created_at": _CREATED,
                    "updated_at": _UPDATED,
                },
                ValueError,
            ),
        )
        for changes, exception in invalid_rows:
            with self.subTest(changes=changes), self.assertRaises(exception):
                _state(**changes)

    def test_readiness_truth_table_is_exact_and_pure(self) -> None:
        before = _state(
            access_state=SessionAccessState.HELD_HERE,
            owner_hostname="owner.example",
            owner_pid=123,
        )
        with (
            mock.patch("builtins.open", side_effect=AssertionError("I/O")),
            mock.patch.object(os, "kill", side_effect=AssertionError("probe")),
        ):
            self.assertTrue(
                provider_work_is_ready(before, provider_configuration_ready=True)
            )
            self.assertFalse(
                provider_work_is_ready(before, provider_configuration_ready=False)
            )
            for durable in SessionDurableState:
                for access in SessionAccessState:
                    if (
                        durable is SessionDurableState.ABSENT
                        and access is not SessionAccessState.CLAIMABLE
                    ):
                        continue
                    owner = access is not SessionAccessState.CLAIMABLE
                    state = _state(
                        durable_state=durable,
                        access_state=access,
                        owner_hostname="owner" if owner else None,
                        owner_pid=1 if owner else None,
                        created_at=None
                        if durable is SessionDurableState.ABSENT
                        else _CREATED,
                        updated_at=None
                        if durable is SessionDurableState.ABSENT
                        else _UPDATED,
                    )
                    expected = (
                        durable is SessionDurableState.READY
                        and access is SessionAccessState.HELD_HERE
                    )
                    self.assertEqual(
                        provider_work_is_ready(
                            state, provider_configuration_ready=True
                        ),
                        expected,
                    )
        self.assertEqual(
            before,
            _state(
                access_state=SessionAccessState.HELD_HERE,
                owner_hostname="owner.example",
                owner_pid=123,
            ),
        )
        for value in (1, "yes", None):
            with self.subTest(value=value), self.assertRaises(TypeError):
                provider_work_is_ready(before, provider_configuration_ready=value)  # type: ignore[arg-type]

    def test_lifecycle_module_is_leaf_and_clean_imports_work(self) -> None:
        source_root = Path(__file__).parents[1] / "src" / "story_writing_agents"
        tree = ast.parse(
            (source_root / "session_lifecycle.py").read_text(encoding="utf-8")
        )
        self.assertFalse(
            [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.level
            ]
        )
        models_tree = ast.parse((source_root / "models.py").read_text(encoding="utf-8"))
        self.assertFalse(
            [
                node
                for node in ast.walk(models_tree)
                if isinstance(node, ast.ImportFrom)
                and node.module == "session_lifecycle"
            ]
        )
        source_files = tuple(source_root.rglob("*.py"))
        self.assertEqual(len(source_files), 97)
        self.assertTrue(
            {
                source_root / "conversations" / "__init__.py",
                source_root / "conversations" / "catalog.py",
                source_root / "conversations" / "context.py",
                source_root / "conversations" / "events.py",
                source_root / "workspace" / "__init__.py",
                source_root / "workspace" / "core.py",
                source_root / "conversation_storage" / "archive.py",
                source_root / "conversation_storage" / "filesystem.py",
                source_root / "conversation_storage" / "filesystem_archive.py",
                source_root / "conversation_storage" / "migration.py",
                source_root / "conversation_storage" / "provisioning.py",
                source_root / "conversation_storage" / "selection.py",
                source_root / "conversation_storage" / "sqlite_database.py",
                source_root / "conversation_storage" / "sqlite.py",
                source_root / "conversation_storage" / "sqlite_search_index.py",
                source_root / "conversation_literal_search.py",
                source_root / "conversation_rag_cli.py",
                source_root / "conversation_rag_runtime.py",
                source_root / "interactive_controller" / "_exchange_policy.py",
                source_root / "interactive_controller" / "_exchange_viewer.py",
                source_root / "interactive_terminal" / "_external_view.py",
            }
            <= set(source_files)
        )
        self.assertNotIn(source_root / "conversations.py", source_files)
        self.assertNotIn(source_root / "workspace.py", source_files)
        index = (source_root.parents[1] / "PYTHON_MODULE_INDEX.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Importable production units indexed: 97.", index)
        self.assertIn("Direct internal dependency edges indexed: 362.", index)
        self.assertIn("Directed internal dependency cycles indexed: 0.", index)
        headings = tuple(re.findall(r"^### `([^`]+)`$", index, flags=re.MULTILINE))
        expected_modules: set[str] = set()
        for path in source_files:
            relative = path.relative_to(source_root).with_suffix("")
            parts = relative.parts
            if parts[-1] == "__init__":
                parts = parts[:-1]
            expected_modules.add(".".join(("story_writing_agents", *parts)))
        self.assertEqual(len(headings), 97)
        self.assertEqual(len(set(headings)), 97)
        self.assertEqual(set(headings), expected_modules)
        self.assertIn("Exactly 81 names in `__all__`", index)
        self.assertIn("Exactly 20 owning-module names in `__all__`", index)
        self.assertIn("Exactly 54 owning-module names in `__all__`", index)
        self.assertIn(
            "project-root facade retains only its immutable conversation-",
            index,
        )
        self.assertIn("exports no schema-v2 collaboration name", index)
        for module_name in (
            "story_writing_agents.conversations",
            "story_writing_agents.conversations.catalog",
            "story_writing_agents.conversations.context",
            "story_writing_agents.conversations.events",
            "story_writing_agents.conversation_storage.filesystem",
            "story_writing_agents.workspace",
            "story_writing_agents.workspace.core",
        ):
            self.assertIn(f"### `{module_name}`", index)
        environment = dict(os.environ, PYTHONPATH=str(source_root.parent))
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from story_writing_agents.session_lifecycle import SessionLifecycleState; "
                "from story_writing_agents import SessionLifecycleState as Public; "
                "assert SessionLifecycleState is Public",
            ],
            cwd=source_root.parents[1],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class AgentBackendLifecycleTests(unittest.TestCase):
    """Verify strict V2 storage, transitions, ownership, and cleanup."""

    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)

    def backend(self) -> AgentBackend:
        """Create a concrete backend and register deterministic cleanup."""

        backend = AgentBackend(self.workspace, executable=sys.executable)
        self.addCleanup(backend.close)
        return backend

    def prepare_close_state(
        self,
        backend: AgentBackend,
        label: str,
        tokens: dict[str, str],
    ) -> Path:
        """Populate all close-owned resource collections for one case."""

        backend._access_claim_tokens.update(tokens)
        backend._thread_lock(f"{label}_lock", "codex", "plot_architect")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        staging = Path(temporary.name) / f"PRIVATE_{label.upper()}_STAGING"
        staging.mkdir()
        backend._staging_directories.add(staging)
        return staging

    def assert_close_resources_cleared(
        self,
        backend: AgentBackend,
        staging: Path,
    ) -> None:
        """Require the original complete-cleanup state after close returns."""

        self.assertTrue(backend._closed)
        self.assertEqual(backend._access_claim_tokens, {})
        self.assertEqual(backend._thread_locks, {})
        self.assertEqual(backend._staging_directories, set())
        self.assertFalse(staging.exists())

    def write_manifest(self, path_session_id: str, **changes: Any) -> dict[str, Any]:
        """Persist one valid V2 manifest with optional field replacements."""

        value = _manifest(path_session_id)
        value.update(changes)
        self.workspace.write_json(_manifest_relative(path_session_id), value)
        return value

    def write_access(self, path_session_id: str, **changes: Any) -> dict[str, Any]:
        """Persist one valid V2 access claim with optional replacements."""

        value = _access(path_session_id)
        value.update(changes)
        self.workspace.write_json(_access_relative(path_session_id), value)
        return value

    def test_absence_listing_order_and_configuration_validation_are_read_only(
        self,
    ) -> None:
        backend = self.backend()
        directory = self.workspace.path(_SESSION_DIRECTORY)
        before = _tree_snapshot(self.workspace.root)
        absent = backend.get_session_lifecycle("missing")
        self.assertEqual(
            absent,
            SessionLifecycleState(
                session_id="missing",
                durable_state=SessionDurableState.ABSENT,
                access_state=SessionAccessState.CLAIMABLE,
                owner_hostname=None,
                owner_pid=None,
                created_at=None,
                updated_at=None,
            ),
        )
        self.assertEqual(backend.list_session_lifecycles(), ())
        with (
            mock.patch.object(
                backend, "acquire_session_access", side_effect=AssertionError
            ),
            mock.patch.object(backend, "_write_manifest", side_effect=AssertionError),
        ):
            self.assertIs(backend.validate_provider_configuration("missing"), True)
        self.assertEqual(_tree_snapshot(self.workspace.root), before)
        self.assertTrue(directory.is_dir())

        self.write_manifest("alpha", updated_at=_UPDATED)
        self.write_manifest("zeta", updated_at=_NEWEST)
        self.write_manifest("beta", updated_at=_NEWEST)
        before = _tree_snapshot(directory)
        self.assertEqual(
            [state.session_id for state in backend.list_session_lifecycles()],
            ["beta", "zeta", "alpha"],
        )
        self.assertEqual(_tree_snapshot(directory), before)

    def test_lifecycle_operation_records_cover_success_rejection_and_cleanup(
        self,
    ) -> None:
        """Map every public lifecycle path to fixed records and original failures."""

        backend = self.backend()

        def lifecycle_rows_since(mark: int) -> list[tuple[object, ...]]:
            return [
                (
                    record["level"],
                    record["phase"],
                    record.get("outcome"),
                    record.get("session_id"),
                )
                for record in _debug_records(self.workspace)[mark:]
                if record["component"] == "backend"
                and record["operation"] == "session_lifecycle"
            ]

        mark = len(_debug_records(self.workspace))
        self.assertEqual(backend.list_session_lifecycles(), ())
        self.assertEqual(
            lifecycle_rows_since(mark),
            [("trace", "observed", "completed", None)],
        )

        mark = len(_debug_records(self.workspace))
        created = backend.create_session_with_access("record_created")
        self.assertEqual(created.access_state, SessionAccessState.HELD_HERE)
        self.assertEqual(
            lifecycle_rows_since(mark),
            [
                ("trace", "started", None, "record_created"),
                ("trace", "observed", "claimable", "record_created"),
                ("trace", "succeeded", "ready", "record_created"),
            ],
        )

        mark = len(_debug_records(self.workspace))
        released = backend.release_session_access("record_created")
        self.assertEqual(released.access_state, SessionAccessState.CLAIMABLE)
        self.assertEqual(
            lifecycle_rows_since(mark),
            [
                ("trace", "started", None, "record_created"),
                ("trace", "observed", "claimable", "record_created"),
                ("trace", "succeeded", "claimable", "record_created"),
            ],
        )

        self.write_manifest("record_reopen", durable_state="paused")
        mark = len(_debug_records(self.workspace))
        reopened = backend.reopen_session_with_access("record_reopen")
        self.assertEqual(reopened.durable_state, SessionDurableState.READY)
        self.assertEqual(
            lifecycle_rows_since(mark),
            [
                ("trace", "started", None, "record_reopen"),
                ("trace", "observed", "claimable", "record_reopen"),
                ("trace", "succeeded", "ready", "record_reopen"),
            ],
        )
        backend.release_session_access("record_reopen")

        self.write_manifest("record_pause")
        mark = len(_debug_records(self.workspace))
        paused = backend.pause_session_and_release_access("record_pause")
        self.assertEqual(paused.durable_state, SessionDurableState.PAUSED)
        self.assertEqual(
            lifecycle_rows_since(mark),
            [
                ("trace", "started", None, "record_pause"),
                ("trace", "observed", "claimable", "record_pause"),
                ("trace", "observed", "claimable", "record_pause"),
                ("trace", "succeeded", "paused", "record_pause"),
            ],
        )

        self.write_manifest("record_acquire")
        mark = len(_debug_records(self.workspace))
        acquired = backend.acquire_session_access("record_acquire")
        self.assertEqual(acquired.access_state, SessionAccessState.HELD_HERE)
        self.assertEqual(
            lifecycle_rows_since(mark),
            [
                ("trace", "started", None, "record_acquire"),
                ("trace", "observed", "claimable", "record_acquire"),
                ("trace", "succeeded", "held_here", "record_acquire"),
            ],
        )

        failures = (
            (
                "duplicate_create",
                lambda: backend.create_session_with_access("record_created"),
                SessionExistsError,
                "record_created",
                True,
            ),
            (
                "missing_acquire",
                lambda: backend.acquire_session_access("record_missing_acquire"),
                SessionNotFoundError,
                "record_missing_acquire",
                True,
            ),
            (
                "missing_reopen",
                lambda: backend.reopen_session_with_access("record_missing_reopen"),
                SessionNotFoundError,
                "record_missing_reopen",
                True,
            ),
            (
                "missing_pause",
                lambda: backend.pause_session_and_release_access(
                    "record_missing_pause"
                ),
                SessionNotFoundError,
                "record_missing_pause",
                True,
            ),
        )
        for label, operation, exception_type, session_id, observes in failures:
            with self.subTest(label=label):
                mark = len(_debug_records(self.workspace))
                with self.assertRaises(exception_type):
                    operation()
                expected = [("trace", "started", None, session_id)]
                if observes:
                    expected.append(("trace", "observed", "claimable", session_id))
                expected.append(("error", "failed", "failed", session_id))
                self.assertEqual(lifecycle_rows_since(mark), expected)

        claim_path = self.workspace.path(_access_relative("record_acquire"))
        claim = self.workspace.read_json(_access_relative("record_acquire"))
        claim["claim_token"] = "PRIVATE_FOREIGN_CLAIM_TOKEN"
        self.workspace.write_json(_access_relative("record_acquire"), claim)
        claim_before = _artifact_snapshot(claim_path)
        mark = len(_debug_records(self.workspace))
        with self.assertRaises(BackendProtocolError):
            backend.release_session_access("record_acquire")
        self.assertEqual(
            lifecycle_rows_since(mark),
            [
                ("trace", "started", None, "record_acquire"),
                ("error", "failed", "failed", "record_acquire"),
            ],
        )
        self.assertEqual(_artifact_snapshot(claim_path), claim_before)

        self.write_access("record_orphan")
        mark = len(_debug_records(self.workspace))
        with self.assertRaises(WorkspaceFormatError):
            backend.list_session_lifecycles()
        self.assertEqual(
            lifecycle_rows_since(mark),
            [("error", "failed", "failed", None)],
        )
        raw_debug_log = self.workspace.path(DEBUG_LOG_RELATIVE_PATH).read_text(
            encoding="utf-8"
        )
        self.assertNotIn("PRIVATE_FOREIGN_CLAIM_TOKEN", raw_debug_log)

    def test_create_release_and_duplicate_create_use_exact_artifacts(self) -> None:
        backend = self.backend()
        state = backend.create_session_with_access("created")
        self.assertEqual(state.durable_state, SessionDurableState.READY)
        self.assertEqual(state.access_state, SessionAccessState.HELD_HERE)
        self.assertEqual(state.owner_hostname, socket.gethostname())
        self.assertEqual(state.owner_pid, os.getpid())
        manifest = self.workspace.read_json(_manifest_relative("created"))
        claim = self.workspace.read_json(_access_relative("created"))
        self.assertEqual(
            set(manifest),
            {
                "schema_version",
                "session_id",
                "durable_state",
                "created_at",
                "updated_at",
                "provider_threads",
            },
        )
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["durable_state"], "ready")
        self.assertEqual(manifest["provider_threads"], {})
        self.assertEqual(manifest["created_at"], manifest["updated_at"])
        self.assertEqual(
            set(claim),
            {
                "schema_version",
                "session_id",
                "owner_hostname",
                "owner_pid",
                "claim_token",
                "claimed_at",
            },
        )
        self.assertEqual(claim["schema_version"], 2)
        self.assertNotIn(claim["claim_token"], repr(state))
        manifest_before = _artifact_snapshot(
            self.workspace.path(_manifest_relative("created"))
        )
        released = backend.release_session_access("created")
        self.assertEqual(released.access_state, SessionAccessState.CLAIMABLE)
        self.assertFalse(self.workspace.path(_access_relative("created")).exists())
        self.assertEqual(
            _artifact_snapshot(self.workspace.path(_manifest_relative("created"))),
            manifest_before,
        )
        with self.assertRaises(SessionExistsError):
            backend.create_session_with_access("created")
        self.assertEqual(
            _artifact_snapshot(self.workspace.path(_manifest_relative("created"))),
            manifest_before,
        )

    def test_manifest_schema_is_strict_and_v1_is_rejected_unchanged(self) -> None:
        backend = self.backend()
        invalid_rows = (
            ("bool_schema", {"schema_version": True}, WorkspaceFormatError),
            ("old_schema", {"schema_version": 1}, WorkspaceVersionUnsupportedError),
            ("extra", {"extra": "field"}, WorkspaceFormatError),
            ("mismatch", {"session_id": "other"}, WorkspaceFormatError),
            ("absent", {"durable_state": "absent"}, WorkspaceFormatError),
            ("naive", {"updated_at": "2026-01-02T00:00:00"}, WorkspaceFormatError),
            ("threads", {"provider_threads": []}, WorkspaceFormatError),
        )
        for session, changes, exception in invalid_rows:
            value = self.write_manifest(session, **changes)
            path = self.workspace.path(_manifest_relative(session))
            before = _artifact_snapshot(path)
            with self.subTest(session=session), self.assertRaises(exception):
                backend.get_session_lifecycle(session)
            self.assertEqual(_artifact_snapshot(path), before)
            self.assertEqual(
                self.workspace.read_json(_manifest_relative(session)), value
            )

    def test_access_schema_duplicate_json_and_orphans_fail_closed(self) -> None:
        backend = self.backend()
        invalid_rows = (
            ("old_access", {"schema_version": 1}, WorkspaceVersionUnsupportedError),
            ("bool_pid", {"owner_pid": True}, WorkspaceFormatError),
            ("extra_access", {"extra": "field"}, WorkspaceFormatError),
            ("mismatch_access", {"session_id": "other"}, WorkspaceFormatError),
            (
                "naive_access",
                {"claimed_at": "2026-01-01T00:00:00"},
                WorkspaceFormatError,
            ),
        )
        for session, changes, exception in invalid_rows:
            self.write_manifest(session)
            value = self.write_access(session, **changes)
            path = self.workspace.path(_access_relative(session))
            before = _artifact_snapshot(path)
            with self.subTest(session=session), self.assertRaises(exception):
                backend.get_session_lifecycle(session)
            self.assertEqual(_artifact_snapshot(path), before)
            self.assertEqual(self.workspace.read_json(_access_relative(session)), value)

        duplicate = "duplicate"
        self.workspace.write_text(
            _manifest_relative(duplicate),
            '{"schema_version":2,"schema_version":2,"session_id":"duplicate",'
            '"durable_state":"ready","created_at":"2026-01-01T00:00:00+00:00",'
            '"updated_at":"2026-01-02T00:00:00+00:00","provider_threads":{}}',
        )
        before = _artifact_snapshot(self.workspace.path(_manifest_relative(duplicate)))
        with self.assertRaises(WorkspaceFormatError):
            backend.get_session_lifecycle(duplicate)
        self.assertEqual(
            _artifact_snapshot(self.workspace.path(_manifest_relative(duplicate))),
            before,
        )

        orphan = "orphan"
        self.write_access(orphan, owner_hostname="remote.example", owner_pid=999)
        before = _artifact_snapshot(self.workspace.path(_access_relative(orphan)))
        with self.assertRaises(WorkspaceFormatError):
            backend.get_session_lifecycle(orphan)
        with self.assertRaises(WorkspaceFormatError):
            backend.list_session_lifecycles()
        self.assertEqual(
            _artifact_snapshot(self.workspace.path(_access_relative(orphan))), before
        )

    def test_v1_sidecar_and_guard_are_typed_rejections_without_migration(self) -> None:
        backend = self.backend()
        self.write_manifest("legacy_sidecar")
        sidecar = self.workspace.write_json(
            f"{_SESSION_DIRECTORY}/legacy_sidecar.lease.json",
            {"schema_version": 1},
        )
        before = _artifact_snapshot(sidecar)
        with self.assertRaises(WorkspaceVersionUnsupportedError):
            backend.get_session_lifecycle("legacy_sidecar")
        self.assertEqual(_artifact_snapshot(sidecar), before)
        sidecar.unlink()

        guard = self.workspace.write_text(
            f"{_SESSION_DIRECTORY}/.lease-claims.lock", "v1"
        )
        before = _artifact_snapshot(guard)
        with self.assertRaises(WorkspaceVersionUnsupportedError):
            backend.list_session_lifecycles()
        self.assertEqual(_artifact_snapshot(guard), before)

    def test_reopen_and_pause_transitions_preserve_provider_state_and_noops(
        self,
    ) -> None:
        backend = self.backend()
        threads = {"codex:plot_architect": _thread()}
        self.write_manifest("paused", durable_state="paused", provider_threads=threads)
        before = self.workspace.read_json(_manifest_relative("paused"))
        reopened = backend.reopen_session_with_access("paused")
        after = self.workspace.read_json(_manifest_relative("paused"))
        self.assertEqual(
            (reopened.durable_state, reopened.access_state),
            (SessionDurableState.READY, SessionAccessState.HELD_HERE),
        )
        self.assertEqual(after["created_at"], before["created_at"])
        self.assertEqual(after["provider_threads"], threads)
        self.assertGreater(after["updated_at"], before["updated_at"])

        ready_before = _artifact_snapshot(
            self.workspace.path(_manifest_relative("paused"))
        )
        self.assertEqual(
            backend.reopen_session_with_access("paused").access_state,
            SessionAccessState.HELD_HERE,
        )
        self.assertEqual(
            _artifact_snapshot(self.workspace.path(_manifest_relative("paused"))),
            ready_before,
        )

        paused = backend.pause_session_and_release_access("paused")
        paused_manifest = self.workspace.read_json(_manifest_relative("paused"))
        self.assertEqual(
            (paused.durable_state, paused.access_state),
            (SessionDurableState.PAUSED, SessionAccessState.CLAIMABLE),
        )
        self.assertEqual(paused_manifest["provider_threads"], threads)
        self.assertFalse(self.workspace.path(_access_relative("paused")).exists())
        noop_before = _artifact_snapshot(
            self.workspace.path(_manifest_relative("paused"))
        )
        backend.pause_session_and_release_access("paused")
        self.assertEqual(
            _artifact_snapshot(self.workspace.path(_manifest_relative("paused"))),
            noop_before,
        )

        for operation in (
            backend.acquire_session_access,
            backend.reopen_session_with_access,
            backend.pause_session_and_release_access,
        ):
            with (
                self.subTest(operation=operation.__name__),
                self.assertRaises(SessionNotFoundError),
            ):
                operation("missing")

    def test_access_liveness_is_conservative_and_dead_local_claim_is_reclaimed(
        self,
    ) -> None:
        backend = self.backend()
        self.write_manifest("live")
        live = self.write_access("live", claim_token="live-secret")
        live_path = self.workspace.path(_access_relative("live"))
        before = _artifact_snapshot(live_path)
        self.assertEqual(
            backend.get_session_lifecycle("live").access_state,
            SessionAccessState.BLOCKED_BY_LIVE_LOCAL_OWNER,
        )
        with self.assertRaises(SessionAccessBlockedError) as caught:
            backend.acquire_session_access("live")
        self.assertNotIn(live["claim_token"], str(caught.exception))
        self.assertEqual(_artifact_snapshot(live_path), before)

        self.write_manifest("remote")
        self.write_access("remote", owner_hostname="remote.example", owner_pid=4321)
        self.assertEqual(
            backend.get_session_lifecycle("remote").access_state,
            SessionAccessState.OWNERSHIP_UNKNOWN,
        )
        with self.assertRaises(SessionOwnershipUnknownError):
            backend.acquire_session_access("remote")

        self.write_manifest("dead")
        original = self.write_access(
            "dead",
            owner_pid=2_147_483_647,
            claim_token="dead-secret",
        )
        dead_path = self.workspace.path(_access_relative("dead"))
        before = _artifact_snapshot(dead_path)
        with mock.patch.object(
            backend_module.os, "kill", side_effect=ProcessLookupError
        ):
            observed = backend.get_session_lifecycle("dead")
            self.assertEqual(observed.access_state, SessionAccessState.CLAIMABLE)
            self.assertEqual(
                (observed.owner_hostname, observed.owner_pid),
                (original["owner_hostname"], original["owner_pid"]),
            )
            self.assertEqual(_artifact_snapshot(dead_path), before)
            held = backend.acquire_session_access("dead")
        replacement = self.workspace.read_json(_access_relative("dead"))
        self.assertEqual(held.access_state, SessionAccessState.HELD_HERE)
        self.assertNotEqual(replacement["claim_token"], original["claim_token"])

    def test_unknown_liveness_errors_never_replace_claim(self) -> None:
        backend = self.backend()
        for session, error in (
            ("permission", PermissionError()),
            ("operating", OSError("ambiguous")),
            ("overflow", OverflowError()),
        ):
            self.write_manifest(session)
            self.write_access(session, owner_pid=999)
            path = self.workspace.path(_access_relative(session))
            before = _artifact_snapshot(path)
            with mock.patch.object(backend_module.os, "kill", side_effect=error):
                self.assertEqual(
                    backend.get_session_lifecycle(session).access_state,
                    SessionAccessState.OWNERSHIP_UNKNOWN,
                )
                with self.assertRaises(SessionOwnershipUnknownError):
                    backend.acquire_session_access(session)
            self.assertEqual(_artifact_snapshot(path), before)

    def test_release_token_comparison_and_close_preserve_foreign_claims(self) -> None:
        backend = self.backend()
        backend.create_session_with_access("changed")
        claim = self.workspace.read_json(_access_relative("changed"))
        claim["claim_token"] = "replacement-token"
        self.workspace.write_json(_access_relative("changed"), claim)
        changed_path = self.workspace.path(_access_relative("changed"))
        before = _artifact_snapshot(changed_path)
        with self.assertRaises(BackendProtocolError):
            backend.release_session_access("changed")
        self.assertEqual(_artifact_snapshot(changed_path), before)

        backend.create_session_with_access("owned")
        backend.close()
        self.assertFalse(self.workspace.path(_access_relative("owned")).exists())
        self.assertEqual(_artifact_snapshot(changed_path), before)
        self.assertEqual(backend._access_claim_tokens, {})

    def test_close_success_records_terminal_and_is_idempotent(self) -> None:
        backend = self.backend()
        tokens = {"close_success": "PRIVATE_CLOSE_SUCCESS_TOKEN"}
        staging = self.prepare_close_state(backend, "close_success", tokens)
        attempts: list[tuple[str, str]] = []
        close_order: list[str] = []

        def release(session_id: str, token: str) -> None:
            attempts.append((session_id, token))

        codex_close = backend._codex_adapter.close
        antigravity_close = backend._antigravity_adapter.close
        trace_debug = backend._trace_debug

        def close_codex() -> None:
            close_order.append("codex")
            codex_close()

        def close_antigravity() -> None:
            close_order.append("antigravity")
            antigravity_close()

        def trace(operation: object, phase: object, **fields: object) -> None:
            if operation == "cleanup" and phase in {"failed", "succeeded"}:
                close_order.append("terminal")
            trace_debug(operation, phase, **fields)  # type: ignore[arg-type]

        mark = len(_debug_records(self.workspace))
        with (
            mock.patch.object(
                backend,
                "_release_access_claim",
                side_effect=release,
            ),
            mock.patch.object(
                backend._codex_adapter,
                "close",
                side_effect=close_codex,
            ),
            mock.patch.object(
                backend._antigravity_adapter,
                "close",
                side_effect=close_antigravity,
            ),
            mock.patch.object(backend, "_trace_debug", side_effect=trace),
        ):
            self.assertIsNone(backend.close())

        self.assertEqual(attempts, list(tokens.items()))
        self.assertEqual(close_order, ["codex", "antigravity", "terminal"])
        self.assert_close_resources_cleared(backend, staging)
        self.assertEqual(
            _cleanup_rows_since(self.workspace, mark),
            [
                ("trace", "started", None, None),
                ("trace", "succeeded", "completed", None),
            ],
        )
        raw = self.workspace.path(DEBUG_LOG_RELATIVE_PATH).read_text(encoding="utf-8")
        for private in (*tokens.values(), str(staging), str(self.workspace.root)):
            self.assertNotIn(private, raw)

        repeat_mark = len(_debug_records(self.workspace))
        self.assertIsNone(backend.close())
        self.assertEqual(len(_debug_records(self.workspace)), repeat_mark)

    def test_close_ordinary_release_failures_record_failed_terminal(self) -> None:
        cases = (
            ("one", ("close_beta",)),
            ("multiple", ("close_alpha", "close_gamma")),
        )
        for label, failed_sessions in cases:
            with self.subTest(label=label):
                backend = self.backend()
                tokens = {
                    "close_alpha": "PRIVATE_CLOSE_ALPHA_TOKEN",
                    "close_beta": "PRIVATE_CLOSE_BETA_TOKEN",
                    "close_gamma": "PRIVATE_CLOSE_GAMMA_TOKEN",
                }
                staging = self.prepare_close_state(
                    backend,
                    f"ordinary_{label}",
                    tokens,
                )
                failures = {
                    session_id: OSError(f"PRIVATE_{session_id.upper()}_FAILURE")
                    for session_id in failed_sessions
                }
                failure_messages = {
                    session_id: str(failure) for session_id, failure in failures.items()
                }
                attempts: list[tuple[str, str]] = []

                def release(session_id: str, token: str) -> None:
                    attempts.append((session_id, token))
                    failure = failures.get(session_id)
                    if failure is not None:
                        raise failure

                mark = len(_debug_records(self.workspace))
                with mock.patch.object(
                    backend,
                    "_release_access_claim",
                    side_effect=release,
                ):
                    self.assertIsNone(backend.close())

                self.assertEqual(attempts, list(tokens.items()))
                self.assert_close_resources_cleared(backend, staging)
                expected = [("trace", "started", None, None)]
                expected.extend(
                    ("error", "failed", "failed", session_id)
                    for session_id in tokens
                    if session_id in failures
                )
                expected.append(("trace", "failed", "failed", None))
                self.assertEqual(
                    _cleanup_rows_since(self.workspace, mark),
                    expected,
                )
                for failure in failures.values():
                    self.assertIsNone(failure.__cause__)
                    self.assertIsNone(failure.__context__)
                raw = self.workspace.path(DEBUG_LOG_RELATIVE_PATH).read_text(
                    encoding="utf-8"
                )
                for private in (
                    *tokens.values(),
                    *failure_messages.values(),
                    str(staging),
                    str(self.workspace.root),
                ):
                    self.assertNotIn(private, raw)

    def test_close_diagnostic_control_flow_does_not_change_cleanup(self) -> None:
        error_backend = self.backend()
        error_tokens = {
            "error_sink_alpha": "PRIVATE_ERROR_SINK_ALPHA_TOKEN",
            "error_sink_beta": "PRIVATE_ERROR_SINK_BETA_TOKEN",
        }
        error_staging = self.prepare_close_state(
            error_backend,
            "error_sink",
            error_tokens,
        )
        error_failure = OSError("PRIVATE_ERROR_SINK_RELEASE_FAILURE")
        error_attempts: list[tuple[str, str]] = []

        def release_for_error_sink(session_id: str, token: str) -> None:
            error_attempts.append((session_id, token))
            if session_id == "error_sink_alpha":
                raise error_failure

        error_mark = len(_debug_records(self.workspace))
        with (
            mock.patch.object(
                error_backend,
                "_release_access_claim",
                side_effect=release_for_error_sink,
            ),
            mock.patch.object(
                WorkspaceDebugLog,
                "error",
                side_effect=KeyboardInterrupt("diagnostic interrupt"),
            ) as debug_error,
        ):
            self.assertIsNone(error_backend.close())

        self.assertEqual(error_attempts, list(error_tokens.items()))
        debug_error.assert_called_once()
        self.assertIs(debug_error.call_args.args[2], error_failure)
        self.assert_close_resources_cleared(error_backend, error_staging)
        self.assertEqual(
            _cleanup_rows_since(self.workspace, error_mark),
            [
                ("trace", "started", None, None),
                ("trace", "failed", "failed", None),
            ],
        )

        trace_backend = self.backend()
        trace_tokens = {
            "trace_sink_alpha": "PRIVATE_TRACE_SINK_ALPHA_TOKEN",
            "trace_sink_beta": "PRIVATE_TRACE_SINK_BETA_TOKEN",
        }
        trace_staging = self.prepare_close_state(
            trace_backend,
            "trace_sink",
            trace_tokens,
        )
        trace_failure = OSError("PRIVATE_TRACE_SINK_RELEASE_FAILURE")
        trace_attempts: list[tuple[str, str]] = []

        def release_for_trace_sink(session_id: str, token: str) -> None:
            trace_attempts.append((session_id, token))
            if session_id == "trace_sink_alpha":
                raise trace_failure

        trace_mark = len(_debug_records(self.workspace))
        with (
            mock.patch.object(
                trace_backend,
                "_release_access_claim",
                side_effect=release_for_trace_sink,
            ),
            mock.patch.object(
                WorkspaceDebugLog,
                "trace",
                side_effect=SystemExit("diagnostic exit"),
            ) as debug_trace,
        ):
            self.assertIsNone(trace_backend.close())

        self.assertEqual(trace_attempts, list(trace_tokens.items()))
        self.assert_close_resources_cleared(trace_backend, trace_staging)
        self.assertEqual(debug_trace.call_count, 2)
        self.assertEqual(
            [call.kwargs["phase"].value for call in debug_trace.call_args_list],
            ["started", "failed"],
        )
        self.assertEqual(
            [
                None
                if call.kwargs.get("outcome") is None
                else call.kwargs["outcome"].value
                for call in debug_trace.call_args_list
            ],
            [None, "failed"],
        )
        self.assertEqual(
            _cleanup_rows_since(self.workspace, trace_mark),
            [("error", "failed", "failed", "trace_sink_alpha")],
        )
        raw = self.workspace.path(DEBUG_LOG_RELATIVE_PATH).read_text(encoding="utf-8")
        for private in (
            *error_tokens.values(),
            *trace_tokens.values(),
            str(error_failure),
            str(trace_failure),
            str(error_staging),
            str(trace_staging),
            str(self.workspace.root),
        ):
            self.assertNotIn(private, raw)

    def test_close_release_base_exception_preserves_interrupted_state(self) -> None:
        backend = self.backend()
        tokens = {
            "interrupt_alpha": "PRIVATE_INTERRUPT_ALPHA_TOKEN",
            "interrupt_beta": "PRIVATE_INTERRUPT_BETA_TOKEN",
        }
        staging = self.prepare_close_state(backend, "interrupt", tokens)
        failure = KeyboardInterrupt("PRIVATE_APPLICATION_RELEASE_INTERRUPT")
        failure_message = str(failure)
        attempts: list[tuple[str, str]] = []

        def release(session_id: str, token: str) -> None:
            attempts.append((session_id, token))
            raise failure

        mark = len(_debug_records(self.workspace))
        with (
            mock.patch.object(
                backend,
                "_release_access_claim",
                side_effect=release,
            ),
            self.assertRaises(KeyboardInterrupt) as raised,
        ):
            backend.close()

        self.assertIs(raised.exception, failure)
        self.assertEqual(str(raised.exception), failure_message)
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)
        self.assertEqual(attempts, [next(iter(tokens.items()))])
        self.assertTrue(backend._closed)
        self.assertEqual(backend._access_claim_tokens, tokens)
        self.assertTrue(backend._thread_locks)
        self.assertEqual(backend._staging_directories, {staging})
        self.assertTrue(staging.is_dir())
        self.assertEqual(
            _cleanup_rows_since(self.workspace, mark),
            [("trace", "started", None, None)],
        )
        raw = self.workspace.path(DEBUG_LOG_RELATIVE_PATH).read_text(encoding="utf-8")
        for private in (
            *tokens.values(),
            failure_message,
            str(staging),
            str(self.workspace.root),
        ):
            self.assertNotIn(private, raw)

        repeat_mark = len(_debug_records(self.workspace))
        self.assertIsNone(backend.close())
        self.assertEqual(len(_debug_records(self.workspace)), repeat_mark)
        self.assertEqual(backend._access_claim_tokens, tokens)
        self.assertEqual(backend._staging_directories, {staging})

    def test_write_failures_compensate_only_newly_acquired_access(self) -> None:
        backend = self.backend()
        with mock.patch.object(
            backend, "_write_manifest", side_effect=OSError("create write")
        ):
            with self.assertRaisesRegex(OSError, "create write"):
                backend.create_session_with_access("create_failure")
        self.assertFalse(
            self.workspace.path(_manifest_relative("create_failure")).exists()
        )
        self.assertFalse(
            self.workspace.path(_access_relative("create_failure")).exists()
        )

        self.write_manifest("new_claim", durable_state="paused")
        with mock.patch.object(
            backend, "_write_manifest", side_effect=OSError("reopen write")
        ):
            with self.assertRaisesRegex(OSError, "reopen write"):
                backend.reopen_session_with_access("new_claim")
        self.assertFalse(self.workspace.path(_access_relative("new_claim")).exists())

        self.write_manifest("preowned", durable_state="paused")
        backend.acquire_session_access("preowned")
        claim_before = _artifact_snapshot(
            self.workspace.path(_access_relative("preowned"))
        )
        with mock.patch.object(
            backend, "_write_manifest", side_effect=OSError("owned write")
        ):
            with self.assertRaisesRegex(OSError, "owned write"):
                backend.reopen_session_with_access("preowned")
        self.assertEqual(
            _artifact_snapshot(self.workspace.path(_access_relative("preowned"))),
            claim_before,
        )
        self.assertIn("preowned", backend._access_claim_tokens)

    def test_guard_is_lazy_persistent_secure_and_ignored_by_listing(self) -> None:
        backend = self.backend()
        guard = self.workspace.path(_ACCESS_GUARD)
        self.assertFalse(guard.exists())
        self.write_manifest("guarded")
        backend.acquire_session_access("guarded")
        self.assertTrue(guard.is_file())
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(guard.stat().st_mode), 0o600)
        backend.release_session_access("guarded")
        self.assertTrue(guard.is_file())
        self.assertEqual(
            [state.session_id for state in backend.list_session_lifecycles()],
            ["guarded"],
        )

    def test_windows_guard_branch_initializes_retries_and_unlocks(self) -> None:
        backend = self.backend()
        fake_msvcrt = mock.Mock()
        fake_msvcrt.LK_NBLCK = 1
        fake_msvcrt.LK_UNLCK = 2
        calls = 0

        def locking(_descriptor: int, mode: int, length: int) -> None:
            """Simulate one Windows contention before successful acquisition."""

            nonlocal calls
            self.assertEqual(length, 1)
            calls += 1
            if mode == fake_msvcrt.LK_NBLCK and calls == 1:
                raise OSError(errno.EACCES, "contended")

        fake_msvcrt.locking.side_effect = locking
        with (
            mock.patch.object(backend_module, "_fcntl", None),
            mock.patch.object(backend_module, "_msvcrt", fake_msvcrt),
            mock.patch.object(backend_module.time, "sleep") as sleep,
            backend._access_claim_guard(),
        ):
            pass
        self.assertEqual(self.workspace.path(_ACCESS_GUARD).read_bytes(), b"\0")
        self.assertEqual(fake_msvcrt.locking.call_count, 3)
        sleep.assert_called_once_with(backend_module._WINDOWS_LOCK_RETRY_SECONDS)

    @unittest.skipUnless(os.name == "posix", "requires POSIX process semantics")
    def test_concurrent_dead_reclaim_has_exactly_one_owner(self) -> None:
        self.write_manifest("stale_race")
        self.write_access(
            "stale_race",
            owner_pid=2_147_483_647,
            claim_token="dead-owner",
        )
        context = multiprocessing.get_context("fork")
        ready = context.Queue()
        results = context.Queue()
        start = context.Event()
        release = context.Event()
        processes = [
            context.Process(
                target=_race_for_dead_claim,
                args=(str(self.workspace.root), ready, start, release, results),
            )
            for _ in range(2)
        ]
        try:
            for process in processes:
                process.start()
            for _ in processes:
                self.assertTrue(ready.get(timeout=10))
            start.set()
            outcomes = [results.get(timeout=10) for _ in processes]
            held = [outcome for outcome in outcomes if outcome[0] == "held"]
            errors = [outcome for outcome in outcomes if outcome[0] == "error"]
            self.assertEqual(len(held), 1, outcomes)
            self.assertEqual(len(errors), 1, outcomes)
            self.assertEqual(held[0][2], SessionAccessState.HELD_HERE.value)
            self.assertEqual(errors[0][1], "SessionAccessBlockedError")
            self.assertNotIn(held[0][1], errors[0][2])
            persisted = self.workspace.read_json(_access_relative("stale_race"))
            self.assertEqual(persisted["claim_token"], held[0][1])
        finally:
            release.set()
            for process in processes:
                process.join(timeout=10)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
            for process in processes:
                self.assertEqual(process.exitcode, 0)
            for queue in (ready, results):
                queue.close()
                queue.join_thread()
        self.assertFalse(self.workspace.path(_access_relative("stale_race")).exists())


if __name__ == "__main__":
    unittest.main()
