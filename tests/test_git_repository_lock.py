"""Focused safety tests for the shared Git repository lease."""

from __future__ import annotations

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
