"""Focused safety tests for the shared Git repository lease."""

from __future__ import annotations

import multiprocessing
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import story_writing_agents
import story_writing_agents.git as git_facade
from story_writing_agents.git import repository_lock as lock_module
from story_writing_agents.git.repository_lock import (
    GitRepositoryLease,
    GitRepositoryLockFailure,
    GitRepositoryLockFailureCode,
    lock_git_repository,
)

from tests.git_contract_fixture import DisposableGitTestCase, exception_graph


class GitRepositoryLockContractTests(unittest.TestCase):
    def test_exports_codes_and_facade_absence_are_exact(self) -> None:
        self.assertEqual(
            tuple(lock_module.__all__),
            (
                "GitRepositoryLease",
                "GitRepositoryLockFailure",
                "GitRepositoryLockFailureCode",
                "lock_git_repository",
            ),
        )
        self.assertEqual(
            tuple(item.value for item in GitRepositoryLockFailureCode),
            ("unsafe_root", "lock_unavailable"),
        )
        for name in lock_module.__all__:
            self.assertFalse(hasattr(git_facade, name), name)
            self.assertFalse(hasattr(story_writing_agents, name), name)

    def test_invalid_roots_and_missing_lock_support_fail_content_free(self) -> None:
        private = "private-root-name"
        invalid = (Path("relative"), Path(f"/missing/{private}"), f"/{private}")
        for root in invalid:
            with self.subTest(root=repr(root)):
                with self.assertRaises(GitRepositoryLockFailure) as caught:
                    with lock_git_repository(root):  # type: ignore[arg-type]
                        self.fail("invalid root acquired a repository lease")
                self.assertIs(
                    caught.exception.code,
                    GitRepositoryLockFailureCode.UNSAFE_ROOT,
                )
                self.assertEqual(exception_graph(caught.exception), (caught.exception,))
                self.assertNotIn(private, repr(caught.exception))
        with (
            mock.patch.object(lock_module, "_fcntl", None),
            self.assertRaises(GitRepositoryLockFailure) as caught,
        ):
            with lock_git_repository(Path("/not-inspected")):
                self.fail("missing flock support acquired a lease")
        self.assertIs(
            caught.exception.code,
            GitRepositoryLockFailureCode.LOCK_UNAVAILABLE,
        )


class GitRepositoryLockSafetyTests(DisposableGitTestCase):
    def _repository(self, name: str) -> Path:
        root = self.repository(name)
        self.initialize(root)
        return root

    def test_lease_revalidates_names_and_expires_after_every_exit(self) -> None:
        root = self._repository("lease")
        lease: GitRepositoryLease | None = None
        with lock_git_repository(root) as current:
            lease = current
            self.assertEqual(current.main_root, root)
            self.assertEqual(current.common_git, root / ".git")
            self.assertGreater(current.common_descriptor, 0)
            current.require_active()
        assert lease is not None
        for access in (lambda: lease.main_root, lease.require_active):
            with self.assertRaises(GitRepositoryLockFailure) as caught:
                access()
            self.assertIs(
                caught.exception.code,
                GitRepositoryLockFailureCode.LOCK_UNAVAILABLE,
            )
        with self.assertRaises(OSError):
            os.fstat(lease._common_descriptor)  # noqa: SLF001

        root = self._repository("substitution")
        with self.assertRaises(GitRepositoryLockFailure) as caught:
            with lock_git_repository(root) as current:
                administrative = root / ".git"
                moved = root / "original.git"
                administrative.rename(moved)
                administrative.mkdir()
                current.require_active()
        self.assertIs(
            caught.exception.code,
            GitRepositoryLockFailureCode.UNSAFE_ROOT,
        )

    def test_foreign_thread_cannot_observe_or_close_lease_and_exit_cleans_up(
        self,
    ) -> None:
        root = self._repository("thread-affinity")
        manager = lock_git_repository(root)
        lease = manager.__enter__()
        descriptor = lease.common_descriptor

        operations = (
            lambda: lease.main_root,
            lambda: lease.common_git,
            lambda: lease.root_identity,
            lambda: lease.common_identity,
            lambda: lease.common_descriptor,
            lease.require_active,
        )

        def foreign_calls():
            failures = []
            for operation in operations:
                try:
                    operation()
                except GitRepositoryLockFailure as exc:
                    failures.append(exc)
            return tuple(failures)

        with ThreadPoolExecutor(max_workers=1) as executor:
            failures = executor.submit(foreign_calls).result(timeout=5)
        self.assertEqual(len(failures), len(operations))
        self.assertTrue(
            all(
                failure.code is GitRepositoryLockFailureCode.LOCK_UNAVAILABLE
                for failure in failures
            )
        )
        os.fstat(descriptor)
        lease.require_active()

        body_failure = RuntimeError("preserve caller failure")
        with ThreadPoolExecutor(max_workers=1) as executor:
            suppresses = executor.submit(
                manager.__exit__,
                RuntimeError,
                body_failure,
                None,
            ).result(timeout=5)
        self.assertFalse(suppresses)
        with self.assertRaises(OSError):
            os.fstat(descriptor)
        with lock_git_repository(root) as replacement:
            replacement.require_active()

    def test_forked_context_exit_cannot_unlock_parent_lease(self) -> None:
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("requires POSIX fork semantics")
        if lock_module._fcntl is None:
            self.skipTest("requires flock support")
        root = self._repository("forked-context-exit")
        manager = lock_git_repository(root)
        lease = manager.__enter__()
        descriptor = lease.common_descriptor
        context = multiprocessing.get_context("fork")
        child_results = context.Queue()

        def exit_in_child() -> None:
            failure_code = None
            try:
                manager.__exit__(None, None, None)
            except GitRepositoryLockFailure as failure:
                failure_code = failure.code.value
            try:
                os.fstat(descriptor)
                descriptor_closed = False
            except OSError:
                descriptor_closed = True
            child_results.put((failure_code, descriptor_closed))

        def contend(result_queue) -> None:
            contender = os.open(root / ".git", os.O_RDONLY)
            acquired = False
            try:
                try:
                    lock_module._fcntl.flock(
                        contender,
                        lock_module._fcntl.LOCK_EX | lock_module._fcntl.LOCK_NB,
                    )
                    acquired = True
                except OSError:
                    acquired = False
                if acquired:
                    lock_module._fcntl.flock(
                        contender,
                        lock_module._fcntl.LOCK_UN,
                    )
            finally:
                os.close(contender)
            result_queue.put(acquired)

        child = context.Process(target=exit_in_child)
        blocked = None
        try:
            child.start()
            child.join(timeout=10)
            self.assertFalse(child.is_alive())
            self.assertEqual(child.exitcode, 0)
            self.assertEqual(
                child_results.get(timeout=3),
                (GitRepositoryLockFailureCode.LOCK_UNAVAILABLE.value, True),
            )
            lease.require_active()
            os.fstat(descriptor)

            blocked_results = context.Queue()
            blocked = context.Process(target=contend, args=(blocked_results,))
            blocked.start()
            blocked.join(timeout=10)
            self.assertFalse(blocked.is_alive())
            self.assertEqual(blocked.exitcode, 0)
            self.assertFalse(blocked_results.get(timeout=3))
        finally:
            if child.is_alive():
                child.terminate()
                child.join(timeout=5)
            if blocked is not None and blocked.is_alive():
                blocked.terminate()
                blocked.join(timeout=5)
            manager.__exit__(None, None, None)
        with self.assertRaises(OSError):
            os.fstat(descriptor)

        released_results = context.Queue()
        released = context.Process(target=contend, args=(released_results,))
        try:
            released.start()
            released.join(timeout=10)
            self.assertFalse(released.is_alive())
            self.assertEqual(released.exitcode, 0)
            self.assertTrue(released_results.get(timeout=3))
        finally:
            if released.is_alive():
                released.terminate()
                released.join(timeout=5)
        with lock_git_repository(root) as replacement:
            replacement.require_active()

    def test_recycled_numeric_ident_cannot_reuse_lease_token_and_teardown_unlocks(
        self,
    ) -> None:
        root = self._repository("recycled-thread-token")
        manager = lock_git_repository(root)
        lease = manager.__enter__()
        descriptor = lease.common_descriptor
        creator_token = lock_module.current_thread_token()
        numeric_ident = threading.get_ident()
        successor_token = object()
        lock_module._THREAD_STATE.token = successor_token
        try:
            self.assertEqual(threading.get_ident(), numeric_ident)
            self.assertIs(lock_module.current_thread_token(), successor_token)
            self.assertIsNot(successor_token, creator_token)
            operations = (
                lambda: lease.main_root,
                lambda: lease.common_git,
                lambda: lease.root_identity,
                lambda: lease.common_identity,
                lambda: lease.common_descriptor,
                lease.require_active,
            )
            for operation in operations:
                with self.subTest(operation=operation), self.assertRaises(
                    GitRepositoryLockFailure
                ) as caught:
                    operation()
                self.assertIs(
                    caught.exception.code,
                    GitRepositoryLockFailureCode.LOCK_UNAVAILABLE,
                )
            os.fstat(descriptor)
            with self.assertRaises(GitRepositoryLockFailure):
                manager.__exit__(None, None, None)
        finally:
            lock_module._THREAD_STATE.token = creator_token

        with self.assertRaises(OSError):
            os.fstat(descriptor)
        with lock_git_repository(root) as replacement:
            replacement.require_active()

    def test_symlinked_root_or_common_directory_is_never_followed(self) -> None:
        root = self._repository("real")
        alias = self.test_root / "alias"
        alias.symlink_to(root, target_is_directory=True)
        with self.assertRaises(GitRepositoryLockFailure):
            with lock_git_repository(alias):
                self.fail("symlinked root acquired a lease")

        outside = self.test_root / "outside.git"
        outside.mkdir()
        administrative = root / ".git"
        preserved = root / "preserved.git"
        administrative.rename(preserved)
        administrative.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(GitRepositoryLockFailure):
            with lock_git_repository(root):
                self.fail("symlinked common directory acquired a lease")
        self.assertTrue(administrative.is_symlink())

    def test_same_repository_serializes_while_distinct_repository_progresses(
        self,
    ) -> None:
        first_root = self._repository("first")
        second_root = self._repository("second")
        entered = threading.Event()
        release = threading.Event()
        same_entered = threading.Event()

        def hold_first() -> None:
            with lock_git_repository(first_root):
                entered.set()
                if not release.wait(5):
                    raise AssertionError("test did not release first lease")

        def acquire_same() -> None:
            with lock_git_repository(first_root):
                same_entered.set()

        with ThreadPoolExecutor(max_workers=2) as executor:
            holding = executor.submit(hold_first)
            self.assertTrue(entered.wait(5))
            waiting = executor.submit(acquire_same)
            self.assertFalse(same_entered.wait(0.1))
            with lock_git_repository(second_root):
                self.assertFalse(same_entered.is_set())
            release.set()
            holding.result(timeout=5)
            waiting.result(timeout=5)
        self.assertTrue(same_entered.is_set())


if __name__ == "__main__":
    unittest.main()
