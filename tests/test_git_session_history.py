"""Filesystem safety coverage for durable Git session history."""

from __future__ import annotations

import hashlib
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest import mock

from story_writing_agents.git import GitBaselineManifest, GitManifestEntry
from story_writing_agents.git import repository_lock as lock_module
from story_writing_agents.git import session_history as history_module
from story_writing_agents.git.session_contracts import (
    GitSessionFailureCode,
    GitSessionOperationKind,
    GitSessionRecoveryAction,
    GitSessionResourceError,
    GitSessionResourceState,
)
from story_writing_agents.git.session_history import (
    open_git_session_history,
    open_story_commit_recovery_history,
)
from story_writing_agents.git.session_history_contracts import (
    GIT_SESSION_HISTORY_DIRECTORY,
    GitSessionCandidatePath,
    GitSessionCandidatePathOperation,
    GitSessionCheckpointOperationRecord,
    GitSessionCommitOperationRecord,
    GitSessionHistoryError,
    GitSessionHistoryFailureCode,
    GitSessionHistoryRecoveryAction,
    GitSessionHistoryState,
    GitSessionHistorySnapshot,
    GitSessionIdentitySource,
    advance_checkpoint_operation,
    advance_commit_operation,
    build_candidate_record,
)
from story_writing_agents.workspace.initialization import provision_session_resources
from story_writing_agents.workspace.schema import canonical_initializer_scaffold

from tests.git_contract_fixture import exception_graph
from tests.test_git_session_resources import GitSessionResourceGitTestCase


_OPERATION_ID = "a" * 32
_CANDIDATE_ID = "b" * 64


def _manifest() -> GitBaselineManifest:
    return GitBaselineManifest(
        tuple(
            GitManifestEntry(
                item.path,
                hashlib.sha256(item.content).hexdigest(),
            )
            for item in canonical_initializer_scaffold()
        )
    )


def _running(tip: str) -> GitSessionCommitOperationRecord:
    return GitSessionCommitOperationRecord(
        session_id="novel",
        operation_id=_OPERATION_ID,
        candidate_id=_CANDIDATE_ID,
        correlation_id="correlation:1",
        state=GitSessionHistoryState.RUNNING,
        sequence=0,
        expected_branch_tip=tip,
    )


def _checkpoint_running(tip: str) -> GitSessionCheckpointOperationRecord:
    return GitSessionCheckpointOperationRecord(
        session_id="novel",
        operation_id="c" * 32,
        correlation_id="correlation:checkpoint",
        state=GitSessionHistoryState.RUNNING,
        sequence=0,
        expected_internal_tip=tip,
        expected_tree="5" * len(tip),
        commit_intent_sha256="6" * 64,
    )


def _candidate(tip: str, tree: str, *, suffix: str = "01"):
    return build_candidate_record(
        session_id="novel",
        branch="story/session/novel",
        branch_tip=tip,
        expected_tree=tree,
        worktree_identity_sha256="d" * 64,
        application_id=f"application:{suffix}",
        proposal_id=f"proposal:{suffix}",
        correlation_id=f"correlation:{suffix}",
        paths=(
            GitSessionCandidatePath(
                f"manuscript/part-01/chapter {suffix} 한글.md",
                GitSessionCandidatePathOperation.CREATE,
                None,
                "e" * 64,
            ),
        ),
        proposed_message=f"Update chapter {suffix}",
        impact_boundary=("plot",),
        author_identity_source=GitSessionIdentitySource.REPOSITORY,
        committer_identity_source=GitSessionIdentitySource.REPOSITORY,
    )


class GitSessionHistorySafetyTests(GitSessionResourceGitTestCase):
    """Reject aliased identifiers and substituted durable records."""

    def _ready_root(self, name: str = "History Space 한글") -> tuple[Path, str]:
        root = self.initialize_workspace(name)
        with mock.patch.dict(os.environ, self.session_environment(), clear=True):
            ready = provision_session_resources(root, "novel")
        assert ready.current_branch_tip is not None
        return root, ready.current_branch_tip

    def _tree(self, root: Path) -> str:
        return self.run_git(root, "rev-parse", "HEAD^{tree}").stdout.decode().strip()

    def test_semantic_ready_revalidates_identity_branch_index_and_internal_tip(
        self,
    ) -> None:
        root, tip = self._ready_root("semantic-ready")
        with open_git_session_history(root, _manifest(), "novel") as history:
            ready = history.require_semantic_ready()
            self.assertEqual(ready.current_branch_tip, tip)
            history.require_semantic_ready(
                expected_branch_tip=ready.current_branch_tip,
                expected_internal_tip=ready.current_internal_tip,
            )
            variants = (
                replace(
                    ready,
                    state=GitSessionResourceState.RECOVERY_REQUIRED,
                    recovery_action=(
                        type(ready.recovery_action).OPERATOR_RECOVERY
                    ),
                ),
                replace(
                    ready,
                    identity=type(ready.identity)(
                        ready.identity.main_root,
                        "foreign",
                    ),
                ),
                replace(ready, current_branch_tip="0" * len(tip)),
                replace(ready, current_internal_tip="1" * len(tip)),
            )
            for confirmed in variants:
                with self.subTest(confirmed=confirmed), mock.patch.object(
                    history_module,
                    "require_ready_locked_git_session_resources",
                    return_value=confirmed,
                ), self.assertRaises(GitSessionHistoryError) as caught:
                    history.require_semantic_ready(
                        expected_branch_tip=ready.current_branch_tip,
                        expected_internal_tip=ready.current_internal_tip,
                    )
                self.assertIs(
                    caught.exception.code,
                    GitSessionHistoryFailureCode.RECOVERY_REQUIRED,
                )

    def test_recovery_topology_translates_resource_failure_without_cause(
        self,
    ) -> None:
        root, _tip = self._ready_root("recovery-topology-error")
        resource_failure = GitSessionResourceError(
            GitSessionFailureCode.RECOVERY_REQUIRED,
            operation=GitSessionOperationKind.REQUIRE_READY,
            state=GitSessionResourceState.RECOVERY_REQUIRED,
            recovery_action=GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
        with open_story_commit_recovery_history(
            root,
            _manifest(),
            "novel",
        ) as recovery, mock.patch.object(
            history_module,
            "require_story_commit_recovery_locked_git_session_resources",
            side_effect=resource_failure,
        ), self.assertRaises(GitSessionHistoryError) as caught:
            recovery.require_recovery_topology()

        self.assertIs(
            caught.exception.code,
            GitSessionHistoryFailureCode.RECOVERY_REQUIRED,
        )
        self.assertIs(
            caught.exception.recovery_action,
            GitSessionHistoryRecoveryAction.OPERATOR_RECOVERY,
        )
        self.assertIsNone(caught.exception.__cause__)

    def test_foreign_thread_history_surface_fails_without_mutation_and_exit_cleans_up(
        self,
    ) -> None:
        root, tip = self._ready_root("history-thread-affinity")
        manager = open_git_session_history(root, _manifest(), "novel", create=True)
        history = manager.__enter__()
        tree = self._tree(root)
        candidate = history.write_candidate(_candidate(tip, tree))
        commit = history.write_commit_operation(
            replace(_running(tip), candidate_id=candidate.candidate_id)
        )
        checkpoint = history.write_checkpoint_operation(_checkpoint_running(tip))
        before = history.snapshot()
        operations = (
            lambda: history.session_id,
            history.require_active,
            history.require_semantic_ready,
            history.close,
            lambda: history.write_candidate(candidate),
            lambda: history.read_candidate(candidate.candidate_id),
            lambda: history.write_commit_operation(commit),
            lambda: history.read_commit_operation(commit.operation_id),
            lambda: history.write_checkpoint_operation(checkpoint),
            lambda: history.read_checkpoint_operation(checkpoint.operation_id),
            history.inspect,
            history.snapshot,
            history.capture_story_commit_records,
        )

        def foreign_calls():
            failures = []
            for operation in operations:
                try:
                    operation()
                except GitSessionHistoryError as exc:
                    failures.append(exc)
            return tuple(failures)

        with ThreadPoolExecutor(max_workers=1) as executor:
            failures = executor.submit(foreign_calls).result(timeout=10)
        self.assertEqual(len(failures), len(operations))
        self.assertTrue(
            all(
                failure.code
                in {
                    GitSessionHistoryFailureCode.LOCK_UNAVAILABLE,
                    GitSessionHistoryFailureCode.UNSAFE_ROOT,
                }
                for failure in failures
            )
        )
        self.assertEqual(history.snapshot(), before)

        body_failure = RuntimeError("preserve history caller failure")
        with ThreadPoolExecutor(max_workers=1) as executor:
            suppresses = executor.submit(
                manager.__exit__,
                RuntimeError,
                body_failure,
                None,
            ).result(timeout=10)
        self.assertFalse(suppresses)
        with self.assertRaises(GitSessionHistoryError):
            history.require_active()
        with open_git_session_history(root, _manifest(), "novel") as reopened:
            self.assertEqual(reopened.snapshot(), before)

    def test_recycled_numeric_ident_cannot_reuse_regular_or_recovery_history(
        self,
    ) -> None:
        root, tip = self._ready_root("recycled-history-token")
        tree = self._tree(root)
        manager = open_git_session_history(
            root,
            _manifest(),
            "novel",
            create=True,
        )
        history = manager.__enter__()
        candidate = history.write_candidate(_candidate(tip, tree))
        operation = history.write_commit_operation(
            replace(_running(tip), candidate_id=candidate.candidate_id)
        )
        checkpoint = history.write_checkpoint_operation(_checkpoint_running(tip))
        before = history.snapshot()
        descriptor = history._descriptor
        assert descriptor is not None
        creator_token = lock_module.current_thread_token()
        numeric_ident = threading.get_ident()
        successor_token = object()
        lock_module._THREAD_STATE.token = successor_token
        try:
            self.assertEqual(threading.get_ident(), numeric_ident)
            operations = (
                lambda: history.session_id,
                history.require_active,
                history.require_semantic_ready,
                history.close,
                lambda: history.write_candidate(candidate),
                lambda: history.read_candidate(candidate.candidate_id),
                lambda: history.write_commit_operation(operation),
                lambda: history.read_commit_operation(operation.operation_id),
                lambda: history.write_checkpoint_operation(checkpoint),
                lambda: history.read_checkpoint_operation(checkpoint.operation_id),
                history.inspect,
                history.snapshot,
                history.capture_story_commit_records,
            )
            for call in operations:
                with self.subTest(history="regular", call=call), self.assertRaises(
                    GitSessionHistoryError
                ):
                    call()
            with self.assertRaises(GitSessionHistoryError):
                manager.__exit__(None, None, None)
        finally:
            lock_module._THREAD_STATE.token = creator_token
        with self.assertRaises(OSError):
            os.fstat(descriptor)
        with open_git_session_history(root, _manifest(), "novel") as reopened:
            self.assertEqual(reopened.snapshot(), before)

        recovery_manager = open_story_commit_recovery_history(
            root,
            _manifest(),
            "novel",
        )
        recovery = recovery_manager.__enter__()
        recovery_descriptor = recovery._history._descriptor
        assert recovery_descriptor is not None
        creator_token = lock_module.current_thread_token()
        successor_token = object()
        lock_module._THREAD_STATE.token = successor_token
        try:
            self.assertEqual(threading.get_ident(), numeric_ident)
            operations = (
                lambda: recovery.session_id,
                lambda: recovery.topology,
                recovery.require_active,
                recovery.require_recovery_topology,
                lambda: recovery.read_candidate(candidate.candidate_id),
                lambda: recovery.read_commit_operation(operation.operation_id),
                recovery.capture_story_commit_records,
                lambda: recovery.write_commit_operation(operation),
                recovery.close,
            )
            for call in operations:
                with self.subTest(history="recovery", call=call), self.assertRaises(
                    GitSessionHistoryError
                ) as caught:
                    call()
                self.assertIs(
                    caught.exception.code,
                    GitSessionHistoryFailureCode.UNSAFE_ROOT,
                )
                self.assertEqual(exception_graph(caught.exception), (caught.exception,))
            with self.assertRaises(GitSessionHistoryError) as teardown:
                recovery_manager.__exit__(None, None, None)
            self.assertIs(
                teardown.exception.code,
                GitSessionHistoryFailureCode.UNSAFE_ROOT,
            )
            self.assertEqual(
                exception_graph(teardown.exception),
                (teardown.exception,),
            )
        finally:
            lock_module._THREAD_STATE.token = creator_token
        with self.assertRaises(OSError):
            os.fstat(recovery_descriptor)
        with open_git_session_history(root, _manifest(), "novel") as reopened:
            self.assertEqual(reopened.snapshot(), before)

    def test_snapshot_empty_create_false_and_complete_records_are_exact(self) -> None:
        root, tip = self._ready_root("snapshot-positive")
        history_root = root / ".git" / GIT_SESSION_HISTORY_DIRECTORY / "novel"
        with open_git_session_history(
            root, _manifest(), "novel", create=False
        ) as empty_history:
            empty = empty_history.snapshot()
            self.assertEqual(
                empty,
                GitSessionHistorySnapshot(
                    empty.inspection,
                    (),
                    (),
                    (),
                ),
            )
            self.assertEqual(empty.inspection.candidate_ids, ())
        self.assertFalse(history_root.exists())

        candidate = _candidate(tip, self._tree(root))
        commit = GitSessionCommitOperationRecord(
            session_id="novel",
            operation_id=_OPERATION_ID,
            candidate_id=candidate.candidate_id,
            correlation_id=candidate.correlation_id,
            state=GitSessionHistoryState.RUNNING,
            sequence=0,
            expected_branch_tip=tip,
        )
        checkpoint = _checkpoint_running("4" * len(tip))
        with open_git_session_history(
            root, _manifest(), "novel", create=True
        ) as history:
            history.write_candidate(candidate)
            history.write_commit_operation(commit)
            history.write_checkpoint_operation(checkpoint)
            repository_before = self.repository_snapshot(root)
            with (
                mock.patch.object(
                    history_module,
                    "decode_candidate_record",
                    wraps=history_module.decode_candidate_record,
                ) as candidate_decoder,
                mock.patch.object(
                    history_module,
                    "decode_commit_operation",
                    wraps=history_module.decode_commit_operation,
                ) as commit_decoder,
                mock.patch.object(
                    history_module,
                    "decode_checkpoint_operation",
                    wraps=history_module.decode_checkpoint_operation,
                ) as checkpoint_decoder,
            ):
                snapshot = history.snapshot()
            self.assertEqual(candidate_decoder.call_count, 2)
            self.assertEqual(commit_decoder.call_count, 2)
            self.assertEqual(checkpoint_decoder.call_count, 2)
            self.assertEqual(snapshot.candidates, (candidate,))
            self.assertEqual(snapshot.commit_operations, (commit,))
            self.assertEqual(snapshot.checkpoint_operations, (checkpoint,))
            self.assertEqual(snapshot.inspection, history.inspect())
            self.assertEqual(history.read_candidate(candidate.candidate_id), candidate)
            history.require_active()
            self.assertEqual(self.repository_snapshot(root), repository_before)

    def test_snapshot_fails_closed_on_drift_corruption_and_preserves_lease(self) -> None:
        root, tip = self._ready_root("snapshot-fail-closed")
        candidate = _candidate(tip, self._tree(root))
        with open_git_session_history(
            root, _manifest(), "novel", create=True
        ) as history:
            history.write_candidate(candidate)
            repository_before = self.repository_snapshot(root)
            divergent = GitSessionHistorySnapshot(
                history.inspect(),
                (candidate,),
                (),
                (),
            ).inspection
            with mock.patch.object(
                history_module.GitSessionHistory,
                "inspect",
                return_value=type(divergent)("novel", (), (), (), ()),
            ):
                with self.assertRaises(GitSessionHistoryError) as drift:
                    history.snapshot()
            self.assertIs(
                drift.exception.code,
                GitSessionHistoryFailureCode.RECOVERY_REQUIRED,
            )
            history.require_active()
            self.assertEqual(self.repository_snapshot(root), repository_before)

            record = (
                root
                / ".git"
                / GIT_SESSION_HISTORY_DIRECTORY
                / "novel"
                / "candidates"
                / f"{candidate.candidate_id}.json"
            )
            record.write_bytes(b"not canonical history bytes")
            with self.assertRaises(GitSessionHistoryError) as corrupt:
                history.snapshot()
            self.assertIs(
                corrupt.exception.code,
                GitSessionHistoryFailureCode.RECORD_INVALID,
            )
            self.assertIs(
                corrupt.exception.recovery_action,
                history_module.GitSessionHistoryRecoveryAction.NONE,
            )
            self.assertNotIn("canonical history", repr(corrupt.exception))
            history.require_active()

    def test_candidate_is_durable_immutable_idempotent_and_restart_readable(
        self,
    ) -> None:
        root, tip = self._ready_root()
        candidate = _candidate(tip, self._tree(root))
        repository_before = self.repository_snapshot(root)
        with open_git_session_history(
            root, _manifest(), "novel", create=True
        ) as history:
            self.assertEqual(history.write_candidate(candidate), candidate)
            self.assertEqual(history.write_candidate(candidate), candidate)
            self.assertEqual(history.read_candidate(candidate.candidate_id), candidate)
            self.assertEqual(
                history.inspect().candidate_ids,
                (candidate.candidate_id,),
            )
        history_root = root / ".git" / GIT_SESSION_HISTORY_DIRECTORY / "novel"
        candidate_path = history_root / "candidates" / f"{candidate.candidate_id}.json"
        self.assertEqual(candidate_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(history_root.stat().st_mode & 0o777, 0o700)
        with open_git_session_history(root, _manifest(), "novel") as restarted:
            self.assertEqual(
                restarted.read_candidate(candidate.candidate_id), candidate
            )
        self.assertEqual(self.repository_snapshot(root), repository_before)

    def test_commit_checkpoint_transitions_restart_and_inventory_are_exact(
        self,
    ) -> None:
        root, tip = self._ready_root("transitions")
        with open_git_session_history(
            root, _manifest(), "novel", create=True
        ) as history:
            commit = _running(tip)
            history.write_commit_operation(commit)
            staged = advance_commit_operation(commit, GitSessionHistoryState.STAGED)
            history.write_commit_operation(staged)
            committed = advance_commit_operation(
                staged,
                GitSessionHistoryState.COMMITTED,
                commit_id="3" * len(tip),
            )
            history.write_commit_operation(committed)

            checkpoint = _checkpoint_running("4" * len(tip))
            history.write_checkpoint_operation(checkpoint)
            unknown = advance_checkpoint_operation(
                checkpoint,
                GitSessionHistoryState.OUTCOME_UNKNOWN,
                alternate_index_preserved=True,
            )
            history.write_checkpoint_operation(unknown)
            with self.assertRaises(GitSessionHistoryError) as caught:
                history.write_checkpoint_operation(checkpoint)
            self.assertIs(
                caught.exception.code,
                GitSessionHistoryFailureCode.TRANSITION_INVALID,
            )
        with open_git_session_history(root, _manifest(), "novel") as restarted:
            self.assertEqual(restarted.read_commit_operation(_OPERATION_ID), committed)
            self.assertEqual(restarted.read_checkpoint_operation("c" * 32), unknown)
            inspection = restarted.inspect()
        self.assertEqual(
            inspection.commit_operations,
            ((_OPERATION_ID, GitSessionHistoryState.COMMITTED),),
        )
        self.assertEqual(
            inspection.checkpoint_operations,
            (("c" * 32, GitSessionHistoryState.OUTCOME_UNKNOWN),),
        )

    def test_checkpoint_index_is_owned_preserved_then_narrowly_removed(self) -> None:
        root, tip = self._ready_root("checkpoint-index")
        repository_before = self.repository_snapshot(root)
        outside = self.test_root / "preserve-user-file.txt"
        outside.write_bytes(b"preserve user bytes\n")
        with open_git_session_history(
            root, _manifest(), "novel", create=True
        ) as history:
            running = _checkpoint_running("4" * len(tip))
            history.write_checkpoint_operation(running)
            alternate = history.allocate_checkpoint_index(running.operation_id)
            self.assertEqual(alternate.session_id, "novel")
            self.assertEqual(alternate.operation_id, running.operation_id)
            alternate.path.write_bytes(b"operation-owned index bytes")
            unknown = advance_checkpoint_operation(
                running,
                GitSessionHistoryState.OUTCOME_UNKNOWN,
                alternate_index_preserved=True,
            )
            history.write_checkpoint_operation(unknown)
            self.assertEqual(
                history.inspect().preserved_checkpoint_indices,
                (running.operation_id,),
            )
            with self.assertRaises(GitSessionHistoryError) as caught:
                history.remove_completed_checkpoint_index(alternate)
            self.assertIs(
                caught.exception.code,
                GitSessionHistoryFailureCode.TRANSITION_INVALID,
            )
            self.assertTrue(alternate.path.exists())

            committed = advance_checkpoint_operation(
                unknown,
                GitSessionHistoryState.COMMITTED,
                checkpoint_id="5" * len(tip),
            )
            history.write_checkpoint_operation(committed)
            history.remove_completed_checkpoint_index(alternate)
            self.assertFalse(alternate.path.exists())
            self.assertEqual(history.inspect().preserved_checkpoint_indices, ())
        self.assertEqual(outside.read_bytes(), b"preserve user bytes\n")
        repository_after = self.repository_snapshot(root)
        self.assertEqual(repository_after[:5], repository_before[:5])
        self.assertEqual(repository_after[5:], repository_before[5:])

    def test_symlinked_record_and_fsync_failure_are_preserved_content_free(
        self,
    ) -> None:
        symlink_root, symlink_tip = self._ready_root("symlinked-record")
        candidate = _candidate(symlink_tip, self._tree(symlink_root))
        history_root = symlink_root / ".git" / GIT_SESSION_HISTORY_DIRECTORY / "novel"
        with open_git_session_history(
            symlink_root,
            _manifest(),
            "novel",
            create=True,
        ):
            pass
        candidate_directory = history_root / "candidates"
        candidate_directory.mkdir()
        outside = self.test_root / "private-story-bytes"
        outside.write_bytes(b"private story prose\n")
        candidate_path = candidate_directory / f"{candidate.candidate_id}.json"
        candidate_path.symlink_to(outside)
        directory_descriptor = os.open(candidate_directory, os.O_RDONLY)
        try:
            with self.assertRaises(GitSessionHistoryError) as caught:
                history_module._read_regular(  # noqa: SLF001
                    directory_descriptor,
                    candidate_path.name,
                )
            self.assertIs(
                caught.exception.code,
                GitSessionHistoryFailureCode.RECORD_INVALID,
            )
            self.assertTrue(candidate_path.is_symlink())
            self.assertEqual(outside.read_bytes(), b"private story prose\n")
        finally:
            os.close(directory_descriptor)

        fsync_root, fsync_tip = self._ready_root("fsync-failure")
        with open_git_session_history(
            fsync_root,
            _manifest(),
            "novel",
            create=True,
        ) as history:
            with (
                mock.patch.object(history_module.os, "fsync", side_effect=OSError),
                self.assertRaises(GitSessionHistoryError) as caught,
            ):
                history.write_commit_operation(_running(fsync_tip))
            self.assertIs(
                caught.exception.code,
                GitSessionHistoryFailureCode.PERSISTENCE_FAILED,
            )
            self.assertNotIn("private story prose", repr(caught.exception))

    def test_sha1_sha256_unicode_and_spaces_share_exact_storage_semantics(self) -> None:
        for object_format in ("sha1", "sha256"):
            with self.subTest(object_format=object_format):
                root = self.initialize_workspace(
                    f"History {object_format} 한글",
                    object_format=object_format,
                )
                with mock.patch.dict(
                    os.environ,
                    self.session_environment(),
                    clear=True,
                ):
                    ready = provision_session_resources(root, "novel")
                tip = ready.current_branch_tip
                assert tip is not None
                candidate = _candidate(tip, self._tree(root), suffix=object_format)
                with open_git_session_history(
                    root,
                    _manifest(),
                    "novel",
                    create=True,
                ) as history:
                    self.assertEqual(history.write_candidate(candidate), candidate)
                    self.assertEqual(
                        history.read_candidate(candidate.candidate_id), candidate
                    )

    def test_history_lease_serializes_session_resource_provisioning(self) -> None:
        root, _tip = self._ready_root("shared-lock")
        started = mock.Mock()

        def provision() -> object:
            started()
            return provision_session_resources(root, "novel")

        with ThreadPoolExecutor(max_workers=1) as executor:
            with open_git_session_history(root, _manifest(), "novel", create=True):
                future = executor.submit(provision)
                for _index in range(100):
                    if started.called:
                        break
                self.assertTrue(started.called)
                self.assertFalse(future.done())
            self.assertEqual(
                future.result(timeout=10).current_branch_tip is not None, True
            )

    def test_non_ascii_hex_record_identifiers_are_invalid_requests(self) -> None:
        root, _tip = self._ready_root("unicode-identifiers")
        non_ascii_ids = (
            "٠" * 32,
            "１" * 32,
            "a" * 31 + "١",
            "٠" * 64,
            "１" * 64,
            "b" * 63 + "١",
        )
        with open_git_session_history(
            root, _manifest(), "novel", create=True
        ) as history:
            for value in non_ascii_ids:
                with self.subTest(value=value[:2], length=len(value)):
                    operation = (
                        history.read_commit_operation
                        if len(value) == 32
                        else history.read_candidate
                    )
                    with self.assertRaises(GitSessionHistoryError) as caught:
                        operation(value)
                    self.assertIs(
                        caught.exception.code,
                        GitSessionHistoryFailureCode.INVALID_REQUEST,
                    )

    def test_operation_update_does_not_overwrite_a_substituted_current_record(
        self,
    ) -> None:
        root, tip = self._ready_root("record-substitution")
        outside = self.test_root / "foreign-operation.json"
        outside.write_bytes(b"foreign durable bytes\n")
        real_identity = history_module._regular_identity  # noqa: SLF001
        identity_calls = 0

        with open_git_session_history(
            root, _manifest(), "novel", create=True
        ) as history:
            running = _running(tip)
            history.write_commit_operation(running)
            staged = advance_commit_operation(running, GitSessionHistoryState.STAGED)

            def substitute_before_final_identity_check(
                parent: int,
                name: str,
            ) -> tuple[int, int, int, int, int, int, int]:
                nonlocal identity_calls
                identity_calls += 1
                if identity_calls == 2:
                    os.unlink(name, dir_fd=parent)
                    os.symlink(outside, name, dir_fd=parent)
                return real_identity(parent, name)

            with (
                mock.patch.object(
                    history_module,
                    "_regular_identity",
                    side_effect=substitute_before_final_identity_check,
                ),
                self.assertRaises(GitSessionHistoryError) as caught,
            ):
                history.write_commit_operation(staged)

            self.assertIs(
                caught.exception.code,
                GitSessionHistoryFailureCode.RECOVERY_REQUIRED,
            )
            operation_path = (
                root
                / ".git"
                / "story-writing-agents/runtime/session-history/novel/commit-operations"
                / f"{_OPERATION_ID}.json"
            )
            self.assertTrue(operation_path.is_symlink())
            self.assertEqual(operation_path.readlink(), outside)
            self.assertEqual(outside.read_bytes(), b"foreign durable bytes\n")


if __name__ == "__main__":
    unittest.main()
