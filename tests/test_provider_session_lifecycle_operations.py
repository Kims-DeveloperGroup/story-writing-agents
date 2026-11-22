"""Provider session lifecycle operations collected by the concrete Backend tests."""

from __future__ import annotations

import os
import socket
from unittest import mock

from story_writing_agents import (
    BackendProtocolError,
    SessionAccessBlockedError,
    SessionAccessState,
    SessionDurableState,
    SessionExistsError,
    SessionNotFoundError,
    SessionOwnershipUnknownError,
    WorkspaceFormatError,
    WorkspaceVersionUnsupportedError,
    provider_session_access_records,
    provider_session_artifacts,
)
from story_writing_agents.debug_log import DEBUG_LOG_RELATIVE_PATH

from tests.backend_lifecycle_diagnostic_fixture import (
    _debug_records,
    _fresh_literal_object_side_effect,
)
from tests.provider_session_lifecycle_artifacts_fixture import (
    _SESSION_DIRECTORY,
    _access_relative,
    _artifact_snapshot,
    _manifest_relative,
    _thread,
)


class ProviderSessionLifecycleOperationCases:
    """Existing lifecycle operations attached to AgentBackendLifecycleTests."""

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
            [],
        )

        mark = len(_debug_records(self.workspace))
        created = backend.create_session_with_access("record_created")
        self.assertEqual(created.access_state, SessionAccessState.HELD_HERE)
        self.assertEqual(
            lifecycle_rows_since(mark),
            [],
        )

        mark = len(_debug_records(self.workspace))
        released = backend.release_session_access("record_created")
        self.assertEqual(released.access_state, SessionAccessState.CLAIMABLE)
        self.assertEqual(
            lifecycle_rows_since(mark),
            [],
        )

        self.write_manifest("record_reopen", durable_state="paused")
        mark = len(_debug_records(self.workspace))
        reopened = backend.reopen_session_with_access("record_reopen")
        self.assertEqual(reopened.durable_state, SessionDurableState.READY)
        self.assertEqual(
            lifecycle_rows_since(mark),
            [],
        )
        backend.release_session_access("record_reopen")

        self.write_manifest("record_pause")
        mark = len(_debug_records(self.workspace))
        paused = backend.pause_session_and_release_access("record_pause")
        self.assertEqual(paused.durable_state, SessionDurableState.PAUSED)
        self.assertEqual(
            lifecycle_rows_since(mark),
            [],
        )

        self.write_manifest("record_acquire")
        mark = len(_debug_records(self.workspace))
        acquired = backend.acquire_session_access("record_acquire")
        self.assertEqual(acquired.access_state, SessionAccessState.HELD_HERE)
        self.assertEqual(
            lifecycle_rows_since(mark),
            [],
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
        for label, operation, exception_type, session_id, _observes in failures:
            with self.subTest(label=label):
                mark = len(_debug_records(self.workspace))
                with self.assertRaises(exception_type):
                    operation()
                self.assertEqual(lifecycle_rows_since(mark), [])

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
            [],
        )
        self.assertEqual(_artifact_snapshot(claim_path), claim_before)

        self.write_access("record_orphan")
        mark = len(_debug_records(self.workspace))
        with self.assertRaises(WorkspaceFormatError):
            backend.list_session_lifecycles()
        self.assertEqual(
            lifecycle_rows_since(mark),
            [],
        )
        debug_path = self.workspace.path(DEBUG_LOG_RELATIVE_PATH)
        raw_debug_log = (
            debug_path.read_text(encoding="utf-8") if debug_path.exists() else ""
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
        with _fresh_literal_object_side_effect(provider_session_access_records.os, "kill", ProcessLookupError):
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
            with mock.patch.object(provider_session_access_records.os, "kill", side_effect=error):
                self.assertEqual(
                    backend.get_session_lifecycle(session).access_state,
                    SessionAccessState.OWNERSHIP_UNKNOWN,
                )
                with self.assertRaises(SessionOwnershipUnknownError):
                    backend.acquire_session_access(session)
            self.assertEqual(_artifact_snapshot(path), before)

    def test_write_failures_compensate_only_newly_acquired_access(self) -> None:
        backend = self.backend()
        with _fresh_literal_object_side_effect(provider_session_artifacts, 'write_manifest', OSError('create write')):
            with self.assertRaisesRegex(OSError, "create write"):
                backend.create_session_with_access("create_failure")
        self.assertFalse(
            self.workspace.path(_manifest_relative("create_failure")).exists()
        )
        self.assertFalse(
            self.workspace.path(_access_relative("create_failure")).exists()
        )

        self.write_manifest("new_claim", durable_state="paused")
        with _fresh_literal_object_side_effect(provider_session_artifacts, 'write_manifest', OSError('reopen write')):
            with self.assertRaisesRegex(OSError, "reopen write"):
                backend.reopen_session_with_access("new_claim")
        self.assertFalse(self.workspace.path(_access_relative("new_claim")).exists())

        self.write_manifest("preowned", durable_state="paused")
        backend.acquire_session_access("preowned")
        claim_before = _artifact_snapshot(
            self.workspace.path(_access_relative("preowned"))
        )
        with _fresh_literal_object_side_effect(provider_session_artifacts, 'write_manifest', OSError('owned write')):
            with self.assertRaisesRegex(OSError, "owned write"):
                backend.reopen_session_with_access("preowned")
        self.assertEqual(
            _artifact_snapshot(self.workspace.path(_access_relative("preowned"))),
            claim_before,
        )
        self.assertIn("preowned", backend._access_claim_tokens)
