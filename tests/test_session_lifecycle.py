"""Strict clean-break provider-session lifecycle tests."""

from __future__ import annotations

import ast
import errno
import inspect
import json
import multiprocessing
import os
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
    SessionAccessState,
    SessionDurableState,
    SessionLifecycleState,
    Workspace,
    provider_work_is_ready,
)
from story_writing_agents import backend as backend_module, provider_session_artifacts, provider_session_claim_guard
from story_writing_agents import errors as errors_module
from story_writing_agents import models as models_module
from story_writing_agents import session_lifecycle as lifecycle_module
from story_writing_agents.session_recovery import SessionRecoveryFailureCode
from story_writing_agents.session_recovery.transition_assessment import (
    SessionTransitionAssessment,
    SessionTransitionAssessmentState,
    SessionTransitionNextAction,
    SessionTransitionOperation,
    SessionTransitionScope,
)

from tests.backend_lifecycle_diagnostic_fixture import _fresh_literal_object_side_effect
from tests.helpers import initialized_workspace
from tests.provider_session_lifecycle_artifacts_fixture import (
    _SESSION_DIRECTORY,
    _UPDATED,
    _access_relative,
    _artifact_snapshot,
    _manifest_relative,
)
from tests.test_backend_diagnostic_events import BackendDiagnosticEventCases
from tests.test_backend_shutdown_resources import BackendShutdownResourceCases
from tests.test_provider_session_lifecycle_operations import ProviderSessionLifecycleOperationCases


_ACCESS_GUARD = f"{_SESSION_DIRECTORY}/.access-claims.lock"
_CREATED = "2026-01-01T00:00:00+00:00"
_NEWEST = "2026-01-03T00:00:00+00:00"

def _fresh_literal_object_return_value(target, attribute, value):
    return mock.patch.object(target, attribute, return_value=value)

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


def _tree_snapshot(directory: Path) -> dict[str, tuple[bytes, int]]:
    """Capture every non-diagnostic regular file below ``directory``."""

    return {
        str(path.relative_to(directory)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(directory.rglob("*"))
        if path.is_file() and "debug_logs" not in path.relative_to(directory).parts
    }


def _module_name_from_source_path(source_path: str) -> str:
    """Map one indexed ``src`` Python path to its canonical module name."""

    relative = Path(source_path).relative_to("src").with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_import_from_base(
    source_module: str,
    source_path: str,
    node: ast.ImportFrom,
) -> str:
    """Resolve one ``from`` import's base with normal package semantics."""

    if node.level == 0:
        return node.module or ""
    package = (
        source_module
        if Path(source_path).name == "__init__.py"
        else source_module.rpartition(".")[0]
    )
    package_parts = package.split(".")
    parent_count = node.level - 1
    if parent_count > len(package_parts):
        return ""
    base_parts = package_parts[: len(package_parts) - parent_count]
    if node.module:
        base_parts.extend(node.module.split("."))
    return ".".join(base_parts)


def _race_for_dead_claim(
    workspace: Workspace,
    ready: Any,
    start: Any,
    release: Any,
    results: Any,
) -> None:
    """Contend for a dead claim while keeping the winning process live."""

    backend = AgentBackend(workspace, executable=sys.executable)
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
        self.assertEqual(len(story_writing_agents.__all__), 106)

        expected = {
            "get_session_lifecycle": ("self", "session_id"),
            "list_session_lifecycles": ("self",),
            "validate_provider_configuration": ("self", "session_id"),
            "acquire_session_access": ("self", "session_id"),
            "release_session_access": ("self", "session_id"),
            "create_session_with_access": ("self", "session_id"),
            "reopen_session_with_access": ("self", "session_id"),
            "pause_session_and_release_access": ("self", "session_id"),
            "provision_session_resources": ("self", "session_id"),
            "assess_session_transition": (
                "self",
                "lifecycle_state",
                "requested_operation",
                "conversation_id",
                "collaboration_store",
                "reviewed_proposal",
                "artifact_application",
            ),
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
        self.assertEqual(len(source_files), 445)
        self.assertTrue(
            {
                source_root / "artifact_proposals.py",
                source_root / "conversations" / "__init__.py",
                source_root / "conversations" / "catalog.py",
                source_root / "conversations" / "context.py",
                source_root / "conversations" / "events.py",
                source_root / "workspace" / "__init__.py",
                source_root / "workspace" / "core.py",
                source_root / "workspace" / "initialization.py",
                source_root / "workspace" / "schema.py",
                source_root / "conversation_storage" / "archive.py",
                source_root / "conversation_storage" / "filesystem.py",
                source_root / "conversation_storage" / "filesystem_archive.py",
                source_root / "conversation_storage" / "migration.py",
                source_root / "conversation_storage" / "provisioning.py",
                source_root / "conversation_storage" / "selection.py",
                source_root / "conversation_storage" / "sqlite_database.py",
                source_root / "conversation_storage" / "sqlite.py",
                source_root / "conversation_storage" / "sqlite_search_index.py",
                source_root / "provider_session_lifecycle_operations.py",
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

    def test_provision_and_assessment_delegate_once_preserving_exact_identity(
        self,
    ) -> None:
        backend = self.backend()
        resource = object()
        lifecycle = SessionLifecycleState(
            session_id="session",
            durable_state=SessionDurableState.READY,
            access_state=SessionAccessState.HELD_HERE,
            owner_hostname="localhost",
            owner_pid=42,
            created_at=_CREATED,
            updated_at=_UPDATED,
        )
        assessment = SessionTransitionAssessment(
            session_id="session",
            conversation_id=None,
            scope=SessionTransitionScope.IDLE,
            state=SessionTransitionAssessmentState.IDLE_READY,
            next_action=SessionTransitionNextAction.BEGIN_WITH_LEAD,
            owner=None,
            failure_code=SessionRecoveryFailureCode.NONE,
            allowed_operations=tuple(SessionTransitionOperation),
            recovery_result=None,
        )
        assessor = mock.Mock()
        assessor.inspect.return_value = assessment
        with (
            _fresh_literal_object_return_value(backend_module, '_provision_session_resources', resource) as provision,
            _fresh_literal_object_return_value(backend_module, '_require_ready_session_resources', resource) as verify,
            _fresh_literal_object_return_value(backend_module, 'SessionTransitionAssessor', assessor) as assessor_type,
        ):
            self.assertIs(backend.provision_session_resources("session"), resource)
            observed = backend.assess_session_transition(
                lifecycle_state=lifecycle,
                requested_operation=SessionTransitionOperation.PAUSE_SESSION,
            )

        provision.assert_called_once_with(self.workspace.root, "session")
        verify.assert_called_once_with(self.workspace.root, "session")
        assessor_type.assert_called_once_with(self.workspace.root)
        assessor.inspect.assert_called_once_with(
            lifecycle_state=lifecycle,
            conversation_id=None,
            collaboration_store=None,
            reviewed_proposal=None,
            artifact_application=None,
        )
        self.assertIs(observed, assessment)

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
            _fresh_literal_object_side_effect(backend, 'acquire_session_access', AssertionError),
            mock.patch.object(provider_session_artifacts, "write_manifest", side_effect=AssertionError),
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

    test_lifecycle_operation_records_cover_success_rejection_and_cleanup = (
        ProviderSessionLifecycleOperationCases.test_lifecycle_operation_records_cover_success_rejection_and_cleanup
    )

    test_create_release_and_duplicate_create_use_exact_artifacts = (
        ProviderSessionLifecycleOperationCases.test_create_release_and_duplicate_create_use_exact_artifacts
    )

    test_manifest_schema_is_strict_and_v1_is_rejected_unchanged = (
        ProviderSessionLifecycleOperationCases.test_manifest_schema_is_strict_and_v1_is_rejected_unchanged
    )

    test_access_schema_duplicate_json_and_orphans_fail_closed = (
        ProviderSessionLifecycleOperationCases.test_access_schema_duplicate_json_and_orphans_fail_closed
    )

    test_v1_sidecar_and_guard_are_typed_rejections_without_migration = (
        ProviderSessionLifecycleOperationCases.test_v1_sidecar_and_guard_are_typed_rejections_without_migration
    )

    test_reopen_and_pause_transitions_preserve_provider_state_and_noops = (
        ProviderSessionLifecycleOperationCases.test_reopen_and_pause_transitions_preserve_provider_state_and_noops
    )

    test_access_liveness_is_conservative_and_dead_local_claim_is_reclaimed = (
        ProviderSessionLifecycleOperationCases.test_access_liveness_is_conservative_and_dead_local_claim_is_reclaimed
    )

    test_unknown_liveness_errors_never_replace_claim = (
        ProviderSessionLifecycleOperationCases.test_unknown_liveness_errors_never_replace_claim
    )

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

    test_close_success_records_terminal_and_is_idempotent = (
        BackendShutdownResourceCases.test_close_success_records_terminal_and_is_idempotent
    )

    test_close_ordinary_release_failures_record_failed_terminal = (
        BackendShutdownResourceCases.test_close_ordinary_release_failures_record_failed_terminal
    )

    test_close_diagnostic_control_flow_does_not_change_cleanup = (
        BackendDiagnosticEventCases.test_close_diagnostic_control_flow_does_not_change_cleanup
    )

    test_close_release_base_exception_preserves_interrupted_state = (
        BackendShutdownResourceCases.test_close_release_base_exception_preserves_interrupted_state
    )

    test_write_failures_compensate_only_newly_acquired_access = (
        ProviderSessionLifecycleOperationCases.test_write_failures_compensate_only_newly_acquired_access
    )

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
            mock.patch.object(provider_session_claim_guard, "_fcntl", None),
            mock.patch.object(provider_session_claim_guard, "_msvcrt", fake_msvcrt),
            mock.patch.object(provider_session_claim_guard.time, "sleep") as sleep,
            provider_session_claim_guard.access_claim_guard(
                backend.workspace, provider_session_artifacts.PROVIDER_SESSION_DIRECTORY,
                backend_module._ACCESS_CLAIM_GUARD,
            ),
        ):
            pass
        self.assertEqual(self.workspace.path(_ACCESS_GUARD).read_bytes(), b"\0")
        self.assertEqual(fake_msvcrt.locking.call_count, 3)
        sleep.assert_called_once_with(provider_session_claim_guard._WINDOWS_LOCK_RETRY_SECONDS)

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
                args=(self.workspace, ready, start, release, results),
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
