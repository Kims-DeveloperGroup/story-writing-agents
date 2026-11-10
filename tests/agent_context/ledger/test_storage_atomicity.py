'Existing LedgerStorageSecurityAndAtomicityTests scenarios, moved without new cases.'
from __future__ import annotations
import os
import stat
from unittest import mock
from story_writing_agents.writing_artifacts.repository import WritingArtifactRepository
from tests.helpers import pristine_initialized_workspace
from story_writing_agents.agent_context.ledger import atomic_files as owner_atomic_files
from story_writing_agents.agent_context.ledger import checkpoint_ledger as owner_checkpoint_ledger
from story_writing_agents.agent_context.ledger import record_validation as owner_record_validation
from story_writing_agents.agent_context.ledger import record_values as owner_record_values
from tests.agent_context.ledger import ledger_fixture as fixture_ledger_fixture


class LedgerStorageSecurityAndAtomicityTests(fixture_ledger_fixture.LedgerTestCase):
    def test_real_ledger_is_excluded_from_canonical_artifact_discovery(self) -> None:
        self.ledger.invalidate(self.key, expected_generation=0)
        paths = WritingArtifactRepository(self.workspace.root).list_paths()
        relative_record = self.record_path().relative_to(self.workspace.root).as_posix()
        relative_lock = self.lock_path().relative_to(self.workspace.root).as_posix()
        self.assertTrue(self.record_path().is_file())
        self.assertTrue(self.lock_path().is_file())
        self.assertNotIn(relative_record, paths)
        self.assertNotIn(relative_lock, paths)
        self.assertFalse(any("context_ledger" in path for path in paths))

    def test_symlink_hardlink_nonregular_and_unsafe_modes_fail_without_outside_mutation(self,
    ) -> None:
        for kind in ("record_symlink",
            "record_hardlink", "record_directory",
            "record_mode", "lock_symlink",
            "lock_directory", "lock_mode",
        ):
            with self.subTest(kind=kind):
                workspace = pristine_initialized_workspace(self)
                ledger = owner_checkpoint_ledger.ContextLedger(workspace, clock=fixture_ledger_fixture.LedgerClock())
                key = owner_record_values.ContextLedgerKey(self.scope, "provider")
                ledger.invalidate(key, expected_generation=0)
                digest = owner_record_values.context_ledger_scope_key(key)[len("sha256:") :]
                directory = (
                    workspace.root / "agent_states/orchestrator_state/context_ledger/v2")
                record = directory / f"{digest}.json"
                lock = directory / f"{digest}.lock"
                outside = workspace.root.parent / "outside"
                outside.write_bytes(b"outside-safe")
                os.chmod(outside, 0o600)
                target = record if kind.startswith("record") else lock
                target.unlink()
                if kind.endswith("symlink"):
                    target.symlink_to(outside)
                elif kind.endswith("hardlink"):
                    os.link(outside, target)
                elif kind.endswith("directory"):
                    target.mkdir(mode=0o700)
                else:
                    target.write_bytes(b"unsafe-mode")
                    os.chmod(target, 0o644)
                if kind.startswith("record"):

                    def callable_operation():
                        return ledger.inspect(key)
                else:

                    def callable_operation():
                        return ledger.invalidate(key,
                            expected_generation=1,)

                with self.assertRaises(owner_record_validation.ContextLedgerError) as caught:
                    callable_operation()
                self.assertIs(
                    caught.exception.code, owner_record_validation.ContextLedgerErrorCode.STORAGE_UNSAFE)
                self.assertEqual(outside.read_bytes(), b"outside-safe")

    def test_workspace_root_identity_replacement_is_rejected_without_new_state(self,
    ) -> None:
        workspace = pristine_initialized_workspace(self)
        ledger = owner_checkpoint_ledger.ContextLedger(workspace, clock=fixture_ledger_fixture.LedgerClock())
        original = workspace.root.with_name("identity-workspace-original")
        workspace.root.rename(original)
        workspace.root.mkdir()
        try:
            with self.assertRaises(owner_record_validation.ContextLedgerError) as caught:
                ledger.inspect(self.key)
            self.assertIs(caught.exception.code, owner_record_validation.ContextLedgerErrorCode.STORAGE_UNSAFE)
            self.assertEqual(list(workspace.root.iterdir()), [])
        finally:
            workspace.root.rmdir()
            original.rename(workspace.root)

    def test_precommit_write_fsync_and_replace_failures_preserve_old_record_and_retry(self,
    ) -> None:
        injections = ("partial_write", "fsync", "replace")
        for index, injection in enumerate(injections, start=1):
            with self.subTest(injection=injection):
                scope = fixture_ledger_fixture.make_scope(subject_id=f"atomic-{index}")
                key = owner_record_values.ContextLedgerKey(scope, "provider")
                projection = fixture_ledger_fixture.make_projection(scope)
                plan = fixture_ledger_fixture.make_plan(
                    projection, attempt_id=f"attempt-atomic-{index}",
                    operation_id=f"operation-atomic-{index}",
                    result_correlation_id=f"result-atomic-{index}",)
                stale = self.ledger.invalidate(key, expected_generation=0)
                path = self.record_path(key)
                before = path.read_bytes()
                real_write = owner_atomic_files.os.write

                def partial_write(descriptor: int, payload: object) -> int:
                    value = bytes(payload)  # type: ignore[arg-type]
                    real_write(descriptor, value[:7])
                    raise OSError("PRIVATE partial write")

                patcher = (mock.patch.object(
                        owner_atomic_files.os, "write", side_effect=partial_write)
                    if injection == "partial_write" else mock.patch.object(
                        owner_atomic_files.os, injection,
                        side_effect=OSError(f"PRIVATE {injection}"),)
                )
                with patcher:
                    self.assert_ledger_error(lambda: self.ledger.reserve_attempt(
                            key, expected_generation=stale.generation,
                            plan=plan,),
                        owner_record_validation.ContextLedgerErrorCode.STORAGE_FAILURE,)
                self.assertEqual(path.read_bytes(), before)
                self.assertFalse(
                    any(item.name.startswith(".tmp-") for item in path.parent.iterdir()))
                recovered = self.ledger.reserve_attempt(
                    key,
                    expected_generation=stale.generation,
                    plan=plan,)
                self.assertIs(
                    recovered.record.state, owner_record_values.ContextLedgerRecordState.INCOMPLETE)

    def test_post_replace_durability_failure_leaves_complete_idempotent_record(self,
    ) -> None:
        stale = self.ledger.invalidate(self.key, expected_generation=0)
        real_fsync = owner_atomic_files.os.fsync
        failed = False

        def fail_directory_fsync(descriptor: int) -> None:
            nonlocal failed
            if stat.S_ISDIR(os.fstat(descriptor).st_mode) and not failed:
                failed = True
                raise OSError("PRIVATE directory fsync")
            real_fsync(descriptor)

        with mock.patch.object(
            owner_atomic_files.os, "fsync",
            side_effect=fail_directory_fsync,):
            self.assert_ledger_error(
                lambda: self.ledger.reserve_attempt(
                    self.key,
                    expected_generation=stale.generation,
                    plan=self.plan,),
                owner_record_validation.ContextLedgerErrorCode.STORAGE_FAILURE,)
        observed = self.ledger.inspect(self.key)
        self.assertEqual((observed.generation, observed.record.state),
            (2, owner_record_values.ContextLedgerRecordState.INCOMPLETE),)
        self.assertEqual(
            self.ledger.reserve_attempt(
                self.key, expected_generation=stale.generation,
                plan=self.plan,),
            observed,)

    def test_control_exceptions_cleanup_temporary_and_writer_lock_for_retry(self,
    ) -> None:
        for index, control in enumerate((KeyboardInterrupt("PRIVATE"), SystemExit(7)), start=1
        ):
            with self.subTest(control=type(control).__name__):
                scope = fixture_ledger_fixture.make_scope(subject_id=f"control-{index}")
                key = owner_record_values.ContextLedgerKey(scope, "provider")
                projection = fixture_ledger_fixture.make_projection(scope)
                plan = fixture_ledger_fixture.make_plan(
                    projection, attempt_id=f"attempt-control-{index}",
                    operation_id=f"operation-control-{index}",
                    result_correlation_id=f"result-control-{index}",)
                stale = self.ledger.invalidate(key, expected_generation=0)
                before = self.record_path(key).read_bytes()
                with (mock.patch.object(owner_atomic_files.os, "replace", side_effect=control),
                    self.assertRaises(type(control)) as caught,):
                    self.ledger.reserve_attempt(key,
                        expected_generation=stale.generation,
                        plan=plan,)
                self.assertIs(caught.exception, control)
                self.assertEqual(self.record_path(key).read_bytes(), before)
                self.assertFalse(
                    any(item.name.startswith(".tmp-")
                        for item in self.record_path(key).parent.iterdir())
                )
                recovered = self.ledger.reserve_attempt(
                    key,
                    expected_generation=stale.generation,
                    plan=plan,)
                self.assertIs(
                    recovered.record.state, owner_record_values.ContextLedgerRecordState.INCOMPLETE)

    def test_unsupported_storage_primitives_fail_before_mutation(self) -> None:
        state_root = (
            self.workspace.root / "agent_states/orchestrator_state/context_ledger")
        with mock.patch.object(owner_atomic_files, '_fcntl', None):
            self.assert_ledger_error(
                lambda: self.ledger.inspect(self.key),
                owner_record_validation.ContextLedgerErrorCode.STORAGE_UNSUPPORTED,)
        self.assertFalse(state_root.exists())
