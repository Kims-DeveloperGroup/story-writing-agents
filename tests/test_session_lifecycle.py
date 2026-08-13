"""Strict provider-session lifecycle, lease, and compatibility tests."""

from __future__ import annotations

import inspect
import errno
import multiprocessing
import os
import socket
import stat
import sys
import unittest
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, get_type_hints
from unittest import mock

import story_writing_agents
from story_writing_agents import (
    AgentBackend,
    BackendProtocolError,
    SessionExistsError,
    SessionIdError,
    SessionInUseError,
    SessionLeaseState,
    SessionNotFoundError,
    SessionSummary,
    StoryWritingAgentsError,
    WorkspaceFormatError,
)
from story_writing_agents import backend as backend_module
from story_writing_agents.cli_context import select_session

from tests.helpers import initialized_workspace


_SESSION_DIRECTORY = "agent_states/orchestrator_state/provider_sessions"
_LEASE_CLAIM_GUARD = f"{_SESSION_DIRECTORY}/.lease-claims.lock"
_CLI_CONTEXT = "agent_states/orchestrator_state/cli_context.json"
_CREATED = "2026-01-01T00:00:00+00:00"
_UPDATED = "2026-01-02T00:00:00+00:00"
_NEWEST = "2026-01-03T00:00:00+00:00"


def _manifest_relative(session_id: str) -> str:
    return f"{_SESSION_DIRECTORY}/{session_id}.json"


def _lease_relative(session_id: str) -> str:
    return f"{_SESSION_DIRECTORY}/{session_id}.lease.json"


def _thread(*, updated_at: str = _UPDATED) -> dict[str, Any]:
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
    active: bool = True,
    created_at: str = _CREATED,
    updated_at: str = _UPDATED,
    threads: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "session_id": session_id,
        "active": active,
        "created_at": created_at,
        "updated_at": updated_at,
        "threads": dict(threads or {}),
    }


def _lease(
    session_id: str,
    *,
    hostname: str | None = None,
    pid: int | None = None,
    token: str = "foreign-token",
    acquired_at: str = _UPDATED,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "session_id": session_id,
        "hostname": socket.gethostname() if hostname is None else hostname,
        "pid": os.getpid() if pid is None else pid,
        "token": token,
        "acquired_at": acquired_at,
    }


def _artifact_snapshot(path: Path) -> tuple[bool, bytes | None, int | None]:
    if not path.exists():
        return False, None, None
    return True, path.read_bytes(), path.stat().st_mtime_ns


def _tree_snapshot(directory: Path) -> dict[str, tuple[bool, bytes | None, int | None]]:
    return {
        str(path.relative_to(directory)): _artifact_snapshot(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _race_for_stale_lease(
    workspace_root: str,
    ready: Any,
    start: Any,
    release: Any,
    results: Any,
) -> None:
    """Contend for one dead lease while keeping the winner process alive."""

    backend = AgentBackend(workspace_root, executable=sys.executable)
    ready.put(True)
    if not start.wait(10):
        results.put(("timeout", None))
        return
    try:
        summary = backend.attach_session("stale_race")
        token = backend._lease_tokens["stale_race"]
        results.put(("owned", token, summary.lease_state.value))
        release.wait(10)
        backend.detach_session("stale_race")
    except Exception as exc:
        results.put(("error", type(exc).__name__, str(exc)))
    finally:
        backend.close()


class SessionLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)

    def backend(self, *, executable: str = sys.executable) -> AgentBackend:
        backend = AgentBackend(self.workspace, executable=executable)
        self.addCleanup(backend.close)
        return backend

    def write_manifest(
        self,
        session_id: str,
        value: Any | None = None,
        **overrides: Any,
    ) -> dict[str, Any] | Any:
        manifest = (
            _manifest(session_id, **overrides)
            if value is None
            else value
        )
        self.workspace.write_json(_manifest_relative(session_id), manifest)
        return manifest

    def write_lease(
        self,
        session_id: str,
        value: Any | None = None,
        **overrides: Any,
    ) -> dict[str, Any] | Any:
        lease = _lease(session_id, **overrides) if value is None else value
        self.workspace.write_json(_lease_relative(session_id), lease)
        return lease

    def assert_no_lease(self, session_id: str) -> None:
        self.assertFalse(self.workspace.path(_lease_relative(session_id)).exists())

    def test_public_contracts_are_exact_frozen_slotted_exports(self) -> None:
        self.assertTrue(issubclass(SessionLeaseState, StrEnum))
        self.assertEqual(
            [(member.name, member.value) for member in SessionLeaseState],
            [
                ("AVAILABLE", "available"),
                ("OWNED", "owned"),
                ("IN_USE", "in_use"),
                ("UNVERIFIED", "unverified"),
            ],
        )
        self.assertEqual(
            [field.name for field in fields(SessionSummary)],
            [
                "session_id",
                "active",
                "selected",
                "lease_state",
                "lease_hostname",
                "lease_pid",
                "created_at",
                "updated_at",
            ],
        )
        self.assertEqual(
            get_type_hints(SessionSummary),
            {
                "session_id": str,
                "active": bool,
                "selected": bool,
                "lease_state": SessionLeaseState,
                "lease_hostname": str | None,
                "lease_pid": int | None,
                "created_at": str,
                "updated_at": str,
            },
        )
        summary = SessionSummary(
            "session",
            True,
            False,
            SessionLeaseState.AVAILABLE,
            None,
            None,
            _CREATED,
            _UPDATED,
        )
        self.assertFalse(hasattr(summary, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            summary.active = False  # type: ignore[misc]

        exported = (
            SessionLeaseState,
            SessionSummary,
            SessionExistsError,
            SessionNotFoundError,
            SessionInUseError,
        )
        for public_value in exported:
            with self.subTest(export=public_value.__name__):
                self.assertIn(public_value.__name__, story_writing_agents.__all__)
                self.assertIs(
                    getattr(story_writing_agents, public_value.__name__),
                    public_value,
                )
        for error_type in (
            SessionExistsError,
            SessionNotFoundError,
            SessionInUseError,
        ):
            with self.subTest(error=error_type.__name__):
                self.assertTrue(issubclass(error_type, BackendProtocolError))
                self.assertTrue(issubclass(error_type, StoryWritingAgentsError))

    def test_public_lifecycle_signatures_keep_selection_caller_owned(self) -> None:
        positional = inspect.Parameter.POSITIONAL_OR_KEYWORD
        keyword_only = inspect.Parameter.KEYWORD_ONLY
        methods = {
            "get_session_summary": (
                ["self", "session_id", "selected_session"],
                {
                    "self": positional,
                    "session_id": positional,
                    "selected_session": keyword_only,
                },
                SessionSummary,
            ),
            "list_sessions": (
                ["self", "selected_session"],
                {"self": positional, "selected_session": keyword_only},
                tuple[SessionSummary, ...],
            ),
            "create_session": (
                ["self", "session_id"],
                {"self": positional, "session_id": positional},
                SessionSummary,
            ),
            "resume_session": (
                ["self", "session_id"],
                {"self": positional, "session_id": positional},
                SessionSummary,
            ),
            "attach_session": (
                ["self", "session_id"],
                {"self": positional, "session_id": positional},
                SessionSummary,
            ),
            "detach_session": (
                ["self", "session_id"],
                {"self": positional, "session_id": positional},
                SessionSummary,
            ),
        }
        for name, (parameter_names, parameter_kinds, return_type) in methods.items():
            with self.subTest(method=name):
                method = getattr(AgentBackend, name)
                signature = inspect.signature(method)
                self.assertEqual(list(signature.parameters), parameter_names)
                self.assertEqual(
                    {
                        parameter: signature.parameters[parameter].kind
                        for parameter in parameter_names
                    },
                    parameter_kinds,
                )
                self.assertIsNone(
                    signature.parameters.get(
                        "selected_session",
                        inspect.Parameter("unused", positional),
                    ).default
                    if "selected_session" in signature.parameters
                    else None
                )
                self.assertEqual(get_type_hints(method)["return"], return_type)

    def test_read_apis_are_provider_free_ordered_selected_and_read_only(self) -> None:
        self.write_manifest("beta", updated_at=_UPDATED)
        self.write_manifest("alpha", updated_at=_UPDATED)
        self.write_manifest("newest", updated_at=_NEWEST)
        self.write_lease("orphan", hostname="remote.example", pid=321)
        self.workspace.write_text(f"{_SESSION_DIRECTORY}/notes.txt", "ignored")
        select_session(self.workspace, "alpha")

        session_directory = self.workspace.path(_SESSION_DIRECTORY)
        context_path = self.workspace.path(_CLI_CONTEXT)
        before_tree = _tree_snapshot(session_directory)
        before_context = _artifact_snapshot(context_path)
        backend = self.backend(executable="/definitely/missing/codex")
        with mock.patch.object(
            backend_module.shutil,
            "which",
            side_effect=AssertionError("read API performed provider preflight"),
        ):
            summaries = backend.list_sessions(selected_session="alpha")
            alpha = backend.get_session_summary(
                "alpha",
                selected_session="alpha",
            )
            unselected = backend.list_sessions(selected_session="not_created")

        self.assertIsInstance(summaries, tuple)
        self.assertEqual(
            [summary.session_id for summary in summaries],
            ["newest", "alpha", "beta"],
        )
        self.assertEqual(
            [summary.selected for summary in summaries],
            [False, True, False],
        )
        self.assertTrue(alpha.selected)
        self.assertEqual(alpha.lease_state, SessionLeaseState.AVAILABLE)
        self.assertIsNone(alpha.lease_hostname)
        self.assertIsNone(alpha.lease_pid)
        self.assertFalse(any(summary.selected for summary in unselected))
        self.assertEqual(_tree_snapshot(session_directory), before_tree)
        self.assertEqual(_artifact_snapshot(context_path), before_context)

        with self.assertRaises(SessionIdError):
            backend.list_sessions(selected_session="../unsafe")
        with self.assertRaises(SessionIdError):
            backend.get_session_summary("alpha", selected_session="../unsafe")
        with self.assertRaises(SessionNotFoundError):
            backend.get_session_summary("missing")

    def test_empty_listing_validates_selection_without_mutation(self) -> None:
        backend = self.backend(executable="/definitely/missing/codex")
        directory = self.workspace.path(_SESSION_DIRECTORY)
        before = _tree_snapshot(directory)
        self.assertEqual(backend.list_sessions(selected_session="future_session"), ())
        with self.assertRaises(SessionIdError):
            backend.list_sessions(selected_session="")
        self.assertEqual(_tree_snapshot(directory), before)

    def test_public_reads_strictly_validate_manifests_and_threads(self) -> None:
        session = "strict_manifest"
        valid = _manifest(session)
        invalid_thread = _thread(updated_at="2026-01-02T00:00:00")
        cases: tuple[tuple[str, Any], ...] = (
            ("non_object", []),
            ("extra_field", {**valid, "extra": True}),
            ("boolean_schema", {**valid, "schema_version": True}),
            ("path_mismatch", {**valid, "session_id": "other"}),
            ("non_boolean_active", {**valid, "active": 1}),
            ("naive_created_at", {**valid, "created_at": "2026-01-01T00:00:00"}),
            (
                "updated_precedes_created",
                {**valid, "created_at": _UPDATED, "updated_at": _CREATED},
            ),
            ("threads_not_object", {**valid, "threads": []}),
            (
                "thread_key_mismatch",
                {**valid, "threads": {"codex:wrong": _thread()}},
            ),
            (
                "thread_naive_timestamp",
                {**valid, "threads": {"codex:plot_architect": invalid_thread}},
            ),
            (
                "thread_unsafe_id",
                {
                    **valid,
                    "threads": {
                        "codex:plot_architect": {
                            **_thread(),
                            "thread_id": "--unsafe-option",
                        }
                    },
                },
            ),
        )
        backend = self.backend()
        for label, value in cases:
            with self.subTest(case=label):
                self.write_manifest(session, deepcopy(value))
                path = self.workspace.path(_manifest_relative(session))
                before = _artifact_snapshot(path)
                with self.assertRaises(WorkspaceFormatError):
                    backend.get_session_summary(session)
                with self.assertRaises(WorkspaceFormatError):
                    backend.list_sessions()
                self.assertEqual(_artifact_snapshot(path), before)

    def test_public_reads_strictly_validate_leases(self) -> None:
        session = "strict_lease"
        self.write_manifest(session)
        valid = _lease(session, hostname="remote.example", pid=123)
        cases: tuple[tuple[str, Any], ...] = (
            ("non_object", []),
            ("extra_field", {**valid, "extra": True}),
            ("boolean_schema", {**valid, "schema_version": True}),
            ("path_mismatch", {**valid, "session_id": "other"}),
            ("boolean_pid", {**valid, "pid": True}),
            ("zero_pid", {**valid, "pid": 0}),
            (
                "naive_acquired_at",
                {**valid, "acquired_at": "2026-01-02T00:00:00"},
            ),
        )
        backend = self.backend()
        for label, value in cases:
            with self.subTest(case=label):
                self.write_lease(session, deepcopy(value))
                path = self.workspace.path(_lease_relative(session))
                before = _artifact_snapshot(path)
                with self.assertRaises(WorkspaceFormatError):
                    backend.get_session_summary(session)
                with self.assertRaises(WorkspaceFormatError):
                    backend.list_sessions()
                self.assertEqual(_artifact_snapshot(path), before)

    def test_lifecycle_preflight_happens_before_any_mutation(self) -> None:
        missing_executable = str(self.workspace.root / "missing-codex")
        backend = self.backend(executable=missing_executable)
        self.write_manifest("existing_create")
        self.write_manifest("existing_resume", active=False)
        self.write_manifest("existing_attach")
        select_session(self.workspace, "existing_create")

        operations = (
            (backend.create_session, "new_create"),
            (backend.create_session, "existing_create"),
            (backend.resume_session, "missing_resume"),
            (backend.resume_session, "existing_resume"),
            (backend.attach_session, "existing_attach"),
        )
        before_tree = _tree_snapshot(self.workspace.path(_SESSION_DIRECTORY))
        before_context = _artifact_snapshot(self.workspace.path(_CLI_CONTEXT))
        for operation, session_id in operations:
            with self.subTest(operation=operation.__name__, session=session_id):
                with self.assertRaisesRegex(BackendProtocolError, "was not found"):
                    operation(session_id)
        self.assertEqual(
            _tree_snapshot(self.workspace.path(_SESSION_DIRECTORY)),
            before_tree,
        )
        self.assertEqual(
            _artifact_snapshot(self.workspace.path(_CLI_CONTEXT)),
            before_context,
        )

    def test_create_is_create_only_retains_lease_and_detaches_without_end(self) -> None:
        backend = self.backend()
        with mock.patch.object(backend_module, "_now", return_value=_CREATED):
            summary = backend.create_session("created_session")

        expected_manifest = _manifest(
            "created_session",
            created_at=_CREATED,
            updated_at=_CREATED,
        )
        self.assertEqual(
            self.workspace.read_json(_manifest_relative("created_session")),
            expected_manifest,
        )
        self.assertEqual(
            summary,
            SessionSummary(
                "created_session",
                True,
                False,
                SessionLeaseState.OWNED,
                socket.gethostname(),
                os.getpid(),
                _CREATED,
                _CREATED,
            ),
        )
        lease_path = self.workspace.path(_lease_relative("created_session"))
        self.assertTrue(lease_path.is_file())
        manifest_before_detach = _artifact_snapshot(
            self.workspace.path(_manifest_relative("created_session"))
        )
        detached = backend.detach_session("created_session")
        self.assertEqual(detached.lease_state, SessionLeaseState.AVAILABLE)
        self.assertTrue(detached.active)
        self.assertFalse(detached.selected)
        self.assertIsNone(detached.lease_hostname)
        self.assertIsNone(detached.lease_pid)
        self.assert_no_lease("created_session")
        self.assertEqual(
            _artifact_snapshot(
                self.workspace.path(_manifest_relative("created_session"))
            ),
            manifest_before_detach,
        )

        select_session(self.workspace, "selected_session")
        context_before = _artifact_snapshot(self.workspace.path(_CLI_CONTEXT))
        for session_id, active in (("active_exists", True), ("inactive_exists", False)):
            existing = self.write_manifest(session_id, active=active)
            manifest_path = self.workspace.path(_manifest_relative(session_id))
            before = _artifact_snapshot(manifest_path)
            with self.subTest(session=session_id):
                with self.assertRaises(SessionExistsError):
                    backend.create_session(session_id)
                self.assertEqual(
                    self.workspace.read_json(_manifest_relative(session_id)),
                    existing,
                )
                self.assertEqual(_artifact_snapshot(manifest_path), before)
                self.assert_no_lease(session_id)
        self.assertEqual(
            _artifact_snapshot(self.workspace.path(_CLI_CONTEXT)),
            context_before,
        )

    def test_create_failures_and_post_claim_race_leave_no_partial_state(self) -> None:
        backend = self.backend()
        selection = select_session(self.workspace, "selected_session")
        self.assertEqual(selection.selected_session, "selected_session")
        context_before = _artifact_snapshot(self.workspace.path(_CLI_CONTEXT))

        with mock.patch.object(
            backend,
            "_write_manifest",
            side_effect=OSError("simulated manifest write failure"),
        ):
            with self.assertRaisesRegex(OSError, "manifest write"):
                backend.create_session("write_failure")
        self.assertFalse(
            self.workspace.path(_manifest_relative("write_failure")).exists()
        )
        self.assert_no_lease("write_failure")

        competing = _manifest("raced_create", active=False)
        original_claim = backend._claim_lease

        def claim_then_publish(session_id: str) -> str:
            token = original_claim(session_id)
            self.workspace.write_json(_manifest_relative(session_id), competing)
            return token

        with mock.patch.object(
            backend,
            "_claim_lease",
            side_effect=claim_then_publish,
        ):
            with self.assertRaises(SessionExistsError):
                backend.create_session("raced_create")
        self.assertEqual(
            self.workspace.read_json(_manifest_relative("raced_create")),
            competing,
        )
        self.assert_no_lease("raced_create")
        self.assertEqual(
            _artifact_snapshot(self.workspace.path(_CLI_CONTEXT)),
            context_before,
        )

    def test_resume_and_attach_enforce_existing_active_contracts(self) -> None:
        backend = self.backend()
        threads = {"codex:plot_architect": _thread()}
        self.write_manifest("active_resume", threads=threads)
        active_path = self.workspace.path(_manifest_relative("active_resume"))
        active_before = _artifact_snapshot(active_path)
        resumed_active = backend.resume_session("active_resume")
        self.assertEqual(resumed_active.lease_state, SessionLeaseState.OWNED)
        self.assertFalse(resumed_active.selected)
        self.assertEqual(_artifact_snapshot(active_path), active_before)
        backend.detach_session("active_resume")

        self.write_manifest("active_attach", threads=threads)
        attach_path = self.workspace.path(_manifest_relative("active_attach"))
        attach_before = _artifact_snapshot(attach_path)
        attached = backend.attach_session("active_attach")
        self.assertEqual(attached.lease_state, SessionLeaseState.OWNED)
        self.assertFalse(attached.selected)
        self.assertEqual(_artifact_snapshot(attach_path), attach_before)
        backend.detach_session("active_attach")

        inactive = self.write_manifest("inactive_attach", active=False, threads=threads)
        inactive_path = self.workspace.path(_manifest_relative("inactive_attach"))
        inactive_before = _artifact_snapshot(inactive_path)
        with self.assertRaisesRegex(BackendProtocolError, "not active"):
            backend.attach_session("inactive_attach")
        self.assertEqual(
            self.workspace.read_json(_manifest_relative("inactive_attach")),
            inactive,
        )
        self.assertEqual(_artifact_snapshot(inactive_path), inactive_before)
        self.assert_no_lease("inactive_attach")

        with self.assertRaises(SessionNotFoundError):
            backend.resume_session("missing_resume")
        with self.assertRaises(SessionNotFoundError):
            backend.attach_session("missing_attach")
        self.assert_no_lease("missing_resume")
        self.assert_no_lease("missing_attach")

    def test_inactive_attach_rejects_every_lease_state_before_claim(self) -> None:
        local_hostname = "local.test"
        cases = (
            ("inactive_absent", None, None),
            (
                "inactive_live",
                _lease("inactive_live", hostname=local_hostname, pid=401),
                SessionLeaseState.IN_USE,
            ),
            (
                "inactive_remote",
                _lease("inactive_remote", hostname="remote.test", pid=402),
                SessionLeaseState.UNVERIFIED,
            ),
            (
                "inactive_dead",
                _lease("inactive_dead", hostname=local_hostname, pid=403),
                SessionLeaseState.AVAILABLE,
            ),
        )
        backend = self.backend()
        guard = self.workspace.path(_LEASE_CLAIM_GUARD)

        for session, lease, expected_state in cases:
            with self.subTest(session=session):
                self.write_manifest(session, active=False)
                if lease is not None:
                    self.write_lease(session, lease)
                manifest_path = self.workspace.path(_manifest_relative(session))
                lease_path = self.workspace.path(_lease_relative(session))
                manifest_before = _artifact_snapshot(manifest_path)
                lease_before = _artifact_snapshot(lease_path)
                fake_os = mock.Mock(wraps=os)
                fake_os.name = "posix"
                fake_os.kill = mock.Mock(
                    side_effect=(
                        ProcessLookupError()
                        if expected_state is SessionLeaseState.AVAILABLE
                        else None
                    )
                )
                fake_socket = mock.Mock(wraps=socket)
                fake_socket.gethostname.return_value = local_hostname

                with (
                    mock.patch.object(backend_module, "os", fake_os),
                    mock.patch.object(backend_module, "socket", fake_socket),
                    mock.patch.object(
                        backend,
                        "_claim_lease",
                        side_effect=AssertionError(
                            "inactive attach attempted to claim a lease"
                        ),
                    ) as claim,
                ):
                    if lease is not None:
                        self.assertEqual(
                            backend._unowned_lease_state(lease),
                            expected_state,
                        )
                    fake_os.kill.reset_mock()
                    with self.assertRaisesRegex(
                        BackendProtocolError,
                        rf"^provider session is not active: {session}$",
                    ):
                        backend.attach_session(session)

                claim.assert_not_called()
                fake_os.kill.assert_not_called()
                self.assertEqual(_artifact_snapshot(manifest_path), manifest_before)
                self.assertEqual(_artifact_snapshot(lease_path), lease_before)
                self.assertFalse(guard.exists())

    def test_inactive_resume_preserves_threads_created_data_and_selection(self) -> None:
        session = "inactive_resume"
        threads = {"codex:plot_architect": _thread()}
        self.write_manifest(session, active=False, threads=threads)
        self.workspace.save_session_overrides(
            session,
            {"language": "EN", "persona": None, "agents": {}},
        )
        retained_path = self.workspace.write_text(
            f"discussion_logs/{session}/retained.txt",
            "retained session data",
        )
        select_session(self.workspace, session)
        override_path = self.workspace.path(
            f"agent_states/orchestrator_state/sessions/{session}.json"
        )
        context_path = self.workspace.path(_CLI_CONTEXT)
        retained_before = _artifact_snapshot(retained_path)
        override_before = _artifact_snapshot(override_path)
        context_before = _artifact_snapshot(context_path)

        backend = self.backend()
        with mock.patch.object(backend_module, "_now", return_value=_CREATED):
            summary = backend.resume_session(session)

        persisted = self.workspace.read_json(_manifest_relative(session))
        self.assertTrue(persisted["active"])
        self.assertEqual(persisted["schema_version"], 1)
        self.assertEqual(persisted["created_at"], _CREATED)
        self.assertEqual(persisted["threads"], threads)
        self.assertGreater(
            datetime.fromisoformat(persisted["updated_at"]),
            datetime.fromisoformat(_UPDATED),
        )
        self.assertEqual(summary.lease_state, SessionLeaseState.OWNED)
        self.assertFalse(summary.selected)
        self.assertEqual(summary.created_at, _CREATED)
        self.assertEqual(summary.updated_at, persisted["updated_at"])
        self.assertEqual(_artifact_snapshot(retained_path), retained_before)
        self.assertEqual(_artifact_snapshot(override_path), override_before)
        self.assertEqual(_artifact_snapshot(context_path), context_before)

    def test_lifecycle_operations_recheck_after_claim_and_cleanup(self) -> None:
        backend = self.backend()
        self.write_manifest("resume_deleted", active=False)
        original_claim = backend._claim_lease

        def claim_then_delete(session_id: str) -> str:
            token = original_claim(session_id)
            self.workspace.path(_manifest_relative(session_id)).unlink()
            return token

        with mock.patch.object(
            backend,
            "_claim_lease",
            side_effect=claim_then_delete,
        ):
            with self.assertRaises(SessionNotFoundError):
                backend.resume_session("resume_deleted")
        self.assert_no_lease("resume_deleted")

        active = self.write_manifest("attach_deactivated")

        def claim_then_deactivate(session_id: str) -> str:
            token = original_claim(session_id)
            raced = {**active, "active": False, "updated_at": _NEWEST}
            self.workspace.write_json(_manifest_relative(session_id), raced)
            return token

        with mock.patch.object(
            backend,
            "_claim_lease",
            side_effect=claim_then_deactivate,
        ):
            with self.assertRaisesRegex(BackendProtocolError, "not active"):
                backend.attach_session("attach_deactivated")
        self.assertEqual(
            self.workspace.read_json(_manifest_relative("attach_deactivated")),
            {**active, "active": False, "updated_at": _NEWEST},
        )
        self.assert_no_lease("attach_deactivated")

    def test_summary_classifies_all_lease_liveness_states(self) -> None:
        backend = self.backend()
        for session in ("absent", "owned", "live", "dead", "remote", "ambiguous"):
            self.write_manifest(session)
        owned = backend.attach_session("owned")
        self.write_lease("live", pid=101)
        self.write_lease("dead", pid=102)
        self.write_lease("remote", hostname="remote.example", pid=103)
        self.write_lease("ambiguous", pid=104)
        dead_path = self.workspace.path(_lease_relative("dead"))
        dead_before = _artifact_snapshot(dead_path)

        def liveness(pid: int, signal: int) -> None:
            self.assertEqual(signal, 0)
            if pid == 101:
                return
            if pid == 102:
                raise ProcessLookupError
            if pid == 104:
                raise PermissionError
            raise AssertionError(f"unexpected liveness probe: {pid}")

        with mock.patch.object(backend_module.os, "kill", side_effect=liveness):
            summaries = {
                session: backend.get_session_summary(session)
                for session in (
                    "absent",
                    "owned",
                    "live",
                    "dead",
                    "remote",
                    "ambiguous",
                )
            }
            listed = {
                summary.session_id: summary
                for summary in backend.list_sessions()
            }

        self.assertEqual(owned.lease_state, SessionLeaseState.OWNED)
        self.assertEqual(summaries["absent"].lease_state, SessionLeaseState.AVAILABLE)
        self.assertIsNone(summaries["absent"].lease_hostname)
        self.assertIsNone(summaries["absent"].lease_pid)
        self.assertEqual(summaries["owned"].lease_state, SessionLeaseState.OWNED)
        self.assertEqual(summaries["live"].lease_state, SessionLeaseState.IN_USE)
        self.assertEqual(summaries["dead"].lease_state, SessionLeaseState.AVAILABLE)
        self.assertEqual(summaries["remote"].lease_state, SessionLeaseState.UNVERIFIED)
        self.assertEqual(
            summaries["ambiguous"].lease_state,
            SessionLeaseState.UNVERIFIED,
        )
        self.assertEqual(summaries["dead"].lease_hostname, socket.gethostname())
        self.assertEqual(summaries["dead"].lease_pid, 102)
        self.assertEqual(_artifact_snapshot(dead_path), dead_before)
        self.assertEqual(
            {session: summary.lease_state for session, summary in listed.items()},
            {session: summary.lease_state for session, summary in summaries.items()},
        )

    def test_non_posix_same_host_lease_is_unverified_and_never_reclaimed(
        self,
    ) -> None:
        session = "non_posix_unverified"
        local_hostname = "local.test"
        self.write_manifest(session)
        self.write_lease(session, hostname=local_hostname, pid=501)
        manifest_path = self.workspace.path(_manifest_relative(session))
        lease_path = self.workspace.path(_lease_relative(session))
        manifest_before = _artifact_snapshot(manifest_path)
        lease_before = _artifact_snapshot(lease_path)
        guard = self.workspace.path(_LEASE_CLAIM_GUARD)
        backend = self.backend()
        fake_os = mock.Mock(wraps=os)
        fake_os.name = "nt"
        fake_os.kill = mock.Mock(
            side_effect=AssertionError("non-POSIX liveness probe attempted")
        )
        fake_socket = mock.Mock(wraps=socket)
        fake_socket.gethostname.return_value = local_hostname

        with (
            mock.patch.object(backend_module, "os", fake_os),
            mock.patch.object(backend_module, "socket", fake_socket),
            mock.patch.object(
                backend,
                "_lease_claim_guard",
                return_value=nullcontext(),
            ),
        ):
            summary = backend.get_session_summary(session)
            self.assertEqual(summary.lease_state, SessionLeaseState.UNVERIFIED)
            self.assertEqual(_artifact_snapshot(lease_path), lease_before)
            with self.assertRaises(SessionInUseError):
                backend.attach_session(session)

        fake_os.kill.assert_not_called()
        self.assertEqual(_artifact_snapshot(manifest_path), manifest_before)
        self.assertEqual(_artifact_snapshot(lease_path), lease_before)
        self.assertFalse(guard.exists())

    def test_contention_is_typed_actionable_token_safe_and_non_mutating(self) -> None:
        backend = self.backend()
        cases = (
            ("live_contended", socket.gethostname(), 201, None),
            ("remote_contended", "remote.example", 202, None),
            ("ambiguous_contended", socket.gethostname(), 203, PermissionError()),
        )
        for session, hostname, pid, liveness_error in cases:
            with self.subTest(session=session):
                self.write_manifest(session)
                token = f"secret-{session}"
                self.write_lease(session, hostname=hostname, pid=pid, token=token)
                manifest_path = self.workspace.path(_manifest_relative(session))
                lease_path = self.workspace.path(_lease_relative(session))
                before_manifest = _artifact_snapshot(manifest_path)
                before_lease = _artifact_snapshot(lease_path)
                liveness = mock.patch.object(
                    backend_module.os,
                    "kill",
                    side_effect=liveness_error,
                )
                with liveness:
                    with self.assertRaises(SessionInUseError) as caught:
                        backend.attach_session(session)
                message = str(caught.exception)
                self.assertIn(session, message)
                self.assertIn(hostname, message)
                self.assertIn(str(pid), message)
                self.assertNotIn(token, message)
                self.assertEqual(_artifact_snapshot(manifest_path), before_manifest)
                self.assertEqual(_artifact_snapshot(lease_path), before_lease)

    def test_dead_local_lease_is_read_only_then_reclaimed_on_attach(self) -> None:
        session = "dead_reclaim"
        self.write_manifest(session)
        original = self.write_lease(session, pid=301, token="dead-token")
        manifest_path = self.workspace.path(_manifest_relative(session))
        lease_path = self.workspace.path(_lease_relative(session))
        manifest_before = _artifact_snapshot(manifest_path)
        lease_before = _artifact_snapshot(lease_path)
        backend = self.backend()
        with mock.patch.object(
            backend_module.os,
            "kill",
            side_effect=ProcessLookupError,
        ):
            available = backend.get_session_summary(session)
            self.assertEqual(available.lease_state, SessionLeaseState.AVAILABLE)
            self.assertEqual(_artifact_snapshot(lease_path), lease_before)
            attached = backend.attach_session(session)

        self.assertEqual(attached.lease_state, SessionLeaseState.OWNED)
        self.assertEqual(_artifact_snapshot(manifest_path), manifest_before)
        replacement = self.workspace.read_json(_lease_relative(session))
        self.assertNotEqual(replacement["token"], original["token"])
        self.assertEqual(replacement["hostname"], socket.gethostname())
        self.assertEqual(replacement["pid"], os.getpid())

    @unittest.skipUnless(os.name == "posix", "requires POSIX process semantics")
    def test_concurrent_stale_reclaim_has_exactly_one_owner(self) -> None:
        self.write_manifest("stale_race")
        self.write_lease(
            "stale_race",
            pid=2_147_483_647,
            token="dead-owner",
        )
        context = multiprocessing.get_context("fork")
        ready = context.Queue()
        results = context.Queue()
        start = context.Event()
        release = context.Event()
        processes = [
            context.Process(
                target=_race_for_stale_lease,
                args=(
                    str(self.workspace.root),
                    ready,
                    start,
                    release,
                    results,
                ),
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
            owned = [outcome for outcome in outcomes if outcome[0] == "owned"]
            errors = [outcome for outcome in outcomes if outcome[0] == "error"]
            self.assertEqual(len(owned), 1, outcomes)
            self.assertEqual(len(errors), 1, outcomes)
            self.assertEqual(errors[0][1], "SessionInUseError")
            self.assertIn("stale_race", errors[0][2])
            self.assertEqual(owned[0][2], SessionLeaseState.OWNED.value)
            persisted = self.workspace.read_json(_lease_relative("stale_race"))
            self.assertEqual(persisted["token"], owned[0][1])
        finally:
            release.set()
            for process in processes:
                process.join(timeout=10)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
            for process in processes:
                self.assertEqual(process.exitcode, 0)
        self.assert_no_lease("stale_race")

    def test_claim_guard_is_lazy_persistent_secure_and_not_a_session(self) -> None:
        guard = self.workspace.path(_LEASE_CLAIM_GUARD)
        backend = self.backend()
        self.assertEqual(backend.list_sessions(), ())
        self.assertFalse(guard.exists())

        self.write_manifest("guarded")
        backend.attach_session("guarded")
        self.assertTrue(guard.is_file())
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(guard.stat().st_mode), 0o600)
        backend.detach_session("guarded")
        self.assertTrue(guard.is_file())
        self.assertEqual(
            [summary.session_id for summary in backend.list_sessions()],
            ["guarded"],
        )

        guard.unlink()
        symlink_target = self.workspace.write_text(
            f"{_SESSION_DIRECTORY}/guard-target",
            "unchanged",
        )
        try:
            guard.symlink_to(symlink_target)
        except (NotImplementedError, OSError):
            self.skipTest("symbolic links are unavailable")
        self.write_manifest("guard_symlink")
        with self.assertRaises(WorkspaceFormatError):
            backend.attach_session("guard_symlink")
        self.assert_no_lease("guard_symlink")
        self.assertEqual(symlink_target.read_text(encoding="utf-8"), "unchanged")

    def test_guard_cleanup_errors_never_orphan_a_committed_lease(self) -> None:
        self.write_manifest("unlock_failure")
        backend = self.backend()
        with mock.patch.object(
            backend,
            "_unlock_lease_claim_guard",
            side_effect=OSError("unlock failed"),
        ):
            summary = backend.attach_session("unlock_failure")
        self.assertEqual(summary.lease_state, SessionLeaseState.OWNED)
        self.assertIn("unlock_failure", backend._lease_tokens)
        backend.detach_session("unlock_failure")
        self.assert_no_lease("unlock_failure")

        self.write_manifest("primary_error")
        existing = self.write_lease(
            "primary_error",
            pid=os.getpid(),
            token="foreign-token",
        )
        with (
            mock.patch.object(
                backend,
                "_unlock_lease_claim_guard",
                side_effect=OSError("unlock failed"),
            ),
            self.assertRaises(SessionInUseError) as caught,
        ):
            backend.attach_session("primary_error")
        self.assertTrue(
            any("could not be unlocked" in note for note in caught.exception.__notes__)
        )
        self.assertEqual(
            self.workspace.read_json(_lease_relative("primary_error")),
            existing,
        )

    def test_windows_guard_branch_initializes_retries_and_unlocks(self) -> None:
        backend = self.backend()
        fake_msvcrt = mock.Mock()
        fake_msvcrt.LK_NBLCK = 1
        fake_msvcrt.LK_UNLCK = 2
        calls = 0

        def locking(_descriptor: int, mode: int, length: int) -> None:
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
            backend._lease_claim_guard(),
        ):
            pass
        guard = self.workspace.path(_LEASE_CLAIM_GUARD)
        self.assertEqual(guard.read_bytes(), b"\0")
        self.assertEqual(fake_msvcrt.locking.call_count, 3)
        sleep.assert_called_once_with(backend_module._WINDOWS_LOCK_RETRY_SECONDS)

    def test_failed_destination_attach_preserves_source_and_cli_selection(self) -> None:
        backend = self.backend()
        self.write_manifest("source")
        self.write_manifest("destination")
        source = backend.attach_session("source")
        self.assertEqual(source.lease_state, SessionLeaseState.OWNED)
        self.write_lease("destination", pid=os.getpid(), token="destination-secret")
        select_session(self.workspace, "source")

        paths = (
            self.workspace.path(_manifest_relative("source")),
            self.workspace.path(_lease_relative("source")),
            self.workspace.path(_manifest_relative("destination")),
            self.workspace.path(_lease_relative("destination")),
            self.workspace.path(_CLI_CONTEXT),
        )
        before = {path: _artifact_snapshot(path) for path in paths}
        with self.assertRaises(SessionInUseError):
            backend.attach_session("destination")
        self.assertEqual(
            backend.get_session_summary(
                "source",
                selected_session="source",
            ).lease_state,
            SessionLeaseState.OWNED,
        )
        self.assertTrue(
            backend.get_session_summary("source", selected_session="source").selected
        )
        self.assertEqual({path: _artifact_snapshot(path) for path in paths}, before)

    def test_owned_end_writes_inactive_then_releases_and_retains_state(self) -> None:
        session = "owned_end"
        threads = {"codex:plot_architect": _thread()}
        self.write_manifest(session, threads=threads)
        self.workspace.save_session_overrides(
            session,
            {"language": "EN", "persona": None, "agents": {}},
        )
        retained_path = self.workspace.write_text(
            f"discussion_logs/{session}/retained.txt",
            "retained",
        )
        select_session(self.workspace, session)
        backend = self.backend()
        backend.attach_session(session)

        override_path = self.workspace.path(
            f"agent_states/orchestrator_state/sessions/{session}.json"
        )
        context_path = self.workspace.path(_CLI_CONTEXT)
        retained_before = _artifact_snapshot(retained_path)
        override_before = _artifact_snapshot(override_path)
        context_before = _artifact_snapshot(context_path)
        events: list[str] = []
        original_write = backend._write_manifest
        original_release = backend._release_lease

        def record_write(session_id: str, value: Any) -> None:
            events.append("write")
            original_write(session_id, value)

        def record_release(session_id: str, token: str) -> None:
            events.append("release")
            original_release(session_id, token)

        with (
            mock.patch.object(
                backend,
                "_claim_lease",
                side_effect=AssertionError("owned end reacquired its lease"),
            ),
            mock.patch.object(backend, "_write_manifest", side_effect=record_write),
            mock.patch.object(backend, "_release_lease", side_effect=record_release),
            mock.patch.object(backend_module, "_now", return_value=_CREATED),
        ):
            ended = backend.end_session(session)

        self.assertIsInstance(ended, dict)
        self.assertEqual(events, ["write", "release"])
        self.assertFalse(ended["active"])
        self.assertEqual(ended["created_at"], _CREATED)
        self.assertEqual(ended["threads"], threads)
        self.assertGreater(
            datetime.fromisoformat(ended["updated_at"]),
            datetime.fromisoformat(_UPDATED),
        )
        self.assertEqual(
            self.workspace.read_json(_manifest_relative(session)),
            ended,
        )
        self.assert_no_lease(session)
        self.assertEqual(_artifact_snapshot(retained_path), retained_before)
        self.assertEqual(_artifact_snapshot(override_path), override_before)
        self.assertEqual(_artifact_snapshot(context_path), context_before)

    def test_owned_end_failures_retain_existing_attachment(self) -> None:
        for failure in ("read", "write"):
            with self.subTest(failure=failure):
                session = f"owned_end_{failure}"
                self.write_manifest(
                    session,
                    threads={"codex:plot_architect": _thread()},
                )
                backend = self.backend()
                backend.attach_session(session)
                manifest_path = self.workspace.path(_manifest_relative(session))
                lease_path = self.workspace.path(_lease_relative(session))
                manifest_before = _artifact_snapshot(manifest_path)
                lease_before = _artifact_snapshot(lease_path)
                patch = (
                    mock.patch.object(
                        backend,
                        "_read_manifest",
                        side_effect=WorkspaceFormatError("simulated read failure"),
                    )
                    if failure == "read"
                    else mock.patch.object(
                        backend,
                        "_write_manifest",
                        side_effect=OSError("simulated write failure"),
                    )
                )
                error_type = WorkspaceFormatError if failure == "read" else OSError
                with patch:
                    with self.assertRaises(error_type):
                        backend.end_session(session)
                self.assertEqual(_artifact_snapshot(manifest_path), manifest_before)
                self.assertEqual(_artifact_snapshot(lease_path), lease_before)
                self.assertEqual(
                    backend.get_session_summary(session).lease_state,
                    SessionLeaseState.OWNED,
                )

    def test_transient_end_failures_release_transient_lease(self) -> None:
        malformed_session = "transient_read_failure"
        malformed = {**_manifest(malformed_session), "schema_version": True}
        self.write_manifest(malformed_session, malformed)
        malformed_path = self.workspace.path(_manifest_relative(malformed_session))
        malformed_before = _artifact_snapshot(malformed_path)
        backend = self.backend()
        with self.assertRaises(WorkspaceFormatError):
            backend.end_session(malformed_session)
        self.assertEqual(_artifact_snapshot(malformed_path), malformed_before)
        self.assert_no_lease(malformed_session)

        write_session = "transient_write_failure"
        self.write_manifest(write_session)
        write_path = self.workspace.path(_manifest_relative(write_session))
        write_before = _artifact_snapshot(write_path)
        with mock.patch.object(
            backend,
            "_write_manifest",
            side_effect=OSError("simulated write failure"),
        ):
            with self.assertRaises(OSError):
                backend.end_session(write_session)
        self.assertEqual(_artifact_snapshot(write_path), write_before)
        self.assert_no_lease(write_session)

    def test_foreign_token_is_never_deleted_by_detach_or_close(self) -> None:
        session = "foreign_token"
        self.write_manifest(session)
        backend = self.backend()
        backend.attach_session(session)
        foreign = _lease(session, token="replacement-owner")
        self.workspace.write_json(_lease_relative(session), foreign)
        lease_path = self.workspace.path(_lease_relative(session))
        before = _artifact_snapshot(lease_path)

        with self.assertRaises(BackendProtocolError):
            backend.detach_session(session)
        self.assertEqual(_artifact_snapshot(lease_path), before)
        backend.close()
        self.assertEqual(_artifact_snapshot(lease_path), before)

    def test_close_releases_all_leases_without_deactivating_sessions(self) -> None:
        backend = self.backend()
        sessions = ("close_one", "close_two")
        for session in sessions:
            self.write_manifest(session)
            backend.attach_session(session)
        manifests_before = {
            session: _artifact_snapshot(
                self.workspace.path(_manifest_relative(session))
            )
            for session in sessions
        }

        backend.close()
        backend.close()
        for session in sessions:
            with self.subTest(session=session):
                self.assert_no_lease(session)
                self.assertEqual(
                    _artifact_snapshot(
                        self.workspace.path(_manifest_relative(session))
                    ),
                    manifests_before[session],
                )
                self.assertTrue(
                    self.workspace.read_json(_manifest_relative(session))["active"]
                )

    def test_start_and_prepare_session_remain_compatible(self) -> None:
        missing_executable = str(self.workspace.root / "missing-codex")
        legacy = self.backend(executable=missing_executable)
        with mock.patch.object(backend_module, "_now", return_value=_CREATED):
            created = legacy.start_session("legacy_session")
        self.assertIsInstance(created, dict)
        self.assertEqual(created["schema_version"], 1)
        self.assertTrue(created["active"])
        self.assert_no_lease("legacy_session")

        threads = {"codex:plot_architect": _thread()}
        inactive = {
            **created,
            "active": False,
            "threads": threads,
            "updated_at": _UPDATED,
        }
        self.workspace.write_json(_manifest_relative("legacy_session"), inactive)
        with mock.patch.object(backend_module, "_now", return_value=_CREATED):
            restarted = legacy.start_session("legacy_session")
        self.assertTrue(restarted["active"])
        self.assertEqual(restarted["created_at"], _CREATED)
        self.assertEqual(restarted["threads"], threads)
        self.assertGreater(
            datetime.fromisoformat(restarted["updated_at"]),
            datetime.fromisoformat(_UPDATED),
        )
        self.assert_no_lease("legacy_session")

        valid = self.backend()
        valid.prepare_session("legacy_session")
        self.assertEqual(
            valid.get_session_summary("legacy_session").lease_state,
            SessionLeaseState.OWNED,
        )
        contender = self.backend()
        with self.assertRaises(SessionInUseError):
            contender.prepare_session("legacy_session")
        valid.close()
        contender.prepare_session("legacy_session")
        self.assertEqual(
            contender.get_session_summary("legacy_session").lease_state,
            SessionLeaseState.OWNED,
        )
        contender.close()
        self.assert_no_lease("legacy_session")

        self.write_manifest("inactive_prepare", active=False)
        with self.assertRaisesRegex(BackendProtocolError, "not active"):
            self.backend().prepare_session("inactive_prepare")
        self.assert_no_lease("inactive_prepare")

        invalid = self.backend(executable=missing_executable)
        with self.assertRaisesRegex(BackendProtocolError, "was not found"):
            invalid.prepare_session("legacy_session")
        self.assert_no_lease("legacy_session")


if __name__ == "__main__":
    unittest.main()
