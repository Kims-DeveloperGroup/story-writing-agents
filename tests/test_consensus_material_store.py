"""Security and concurrency tests for unpublished consensus material storage."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import errno
import hashlib
import multiprocessing
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest import mock

from story_writing_agents.consensus import material_store as material_store_module
from story_writing_agents.consensus.contracts import ConsensusMaterialHandle
from story_writing_agents.consensus.material_store import (
    CONSENSUS_MATERIAL_DIRECTORY,
    MAX_CONSENSUS_MATERIAL_BYTES,
    ConsensusMaterialStore,
    ConsensusMaterialStoreError,
    ConsensusMaterialStoreSecurityError,
)


def _put_from_spawned_process(
    root: str,
    content: str,
    barrier: object,
    results: object,
) -> None:
    """Perform one synchronized first put in a fresh interpreter process."""

    try:
        barrier.wait(timeout=10)  # type: ignore[attr-defined]
        handle = ConsensusMaterialStore(root).put_text(content)
    except BaseException as exc:  # pragma: no cover - returned to the parent.
        results.put(("error", type(exc).__name__, str(exc)))  # type: ignore[attr-defined]
    else:
        results.put(("ok", handle.locator, handle.digest))  # type: ignore[attr-defined]


class ConsensusMaterialStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "workspace"
        self.root.mkdir()
        self.store = ConsensusMaterialStore(self.root)

    def material_path(self, handle: ConsensusMaterialHandle) -> Path:
        digest = handle.digest
        return (
            self.root
            / CONSENSUS_MATERIAL_DIRECTORY
            / digest[:2]
            / f"{digest[2:]}.utf8"
        )

    def install_raw(self, raw: bytes) -> ConsensusMaterialHandle:
        digest = hashlib.sha256(raw).hexdigest()
        handle = ConsensusMaterialHandle(f"candidate:{digest}", digest)
        target = self.material_path(handle)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return handle

    @staticmethod
    def paths_for_content(root: Path, content: str) -> tuple[Path, Path]:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        candidate = (
            root
            / CONSENSUS_MATERIAL_DIRECTORY
            / digest[:2]
            / f"{digest[2:]}.utf8"
        )
        return candidate, candidate.parent / ".lock"

    def assert_single_candidate_and_lock(self, root: Path) -> None:
        storage = root / CONSENSUS_MATERIAL_DIRECTORY
        candidates = tuple(storage.rglob("*.utf8"))
        locks = tuple(storage.rglob(".lock"))
        self.assertEqual(len(candidates), 1)
        self.assertEqual(len(locks), 1)
        lock = locks[0]
        observed = lock.lstat()
        self.assertEqual(lock.name, ".lock")
        self.assertTrue(stat.S_ISREG(observed.st_mode))
        self.assertEqual(observed.st_nlink, 1)

    def test_lifecycle_is_content_addressed_and_readable_by_fresh_instance(self) -> None:
        content = "Opening revision 한글\r\nÉcho.  \r\n"
        handle = self.store.put_text(content)
        expected_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self.assertEqual(
            (handle.locator, handle.digest),
            (f"candidate:{expected_digest}", expected_digest),
        )
        self.assertEqual(
            self.store.inspect(handle).utf8_bytes,
            len(content.encode("utf-8")),
        )
        self.assertEqual(ConsensusMaterialStore(self.root).read_text(handle), content)
        self.assertTrue(ConsensusMaterialStore(self.root).delete(handle))
        self.assertFalse(self.store.delete(handle))
        with self.assertRaisesRegex(ConsensusMaterialStoreError, "does not exist"):
            self.store.read_text(handle)

    def test_candidate_locator_and_cleanup_never_touch_canonical_artifacts(self) -> None:
        protected = {
            "core_documents/global_outline.md": b"outline",
            "manuscript/chapter-01.md": b"chapter",
            "config/workspace.json": b"{}",
        }
        for relative, content in protected.items():
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

        handle = self.store.put_text("unpublished candidate")
        self.assertTrue(handle.locator.startswith("candidate:"))
        self.assertNotIn("/", handle.locator)
        candidate_path = self.material_path(handle)
        self.assertTrue(candidate_path.is_file())
        self.assertTrue(
            candidate_path.is_relative_to(self.root / CONSENSUS_MATERIAL_DIRECTORY)
        )
        self.assertTrue(self.store.delete(handle))
        self.assertFalse(candidate_path.exists())
        for relative, content in protected.items():
            self.assertEqual((self.root / relative).read_bytes(), content)

    def test_identical_put_is_inode_and_mtime_idempotent(self) -> None:
        first = self.store.put_text("same candidate")
        target = self.material_path(first)
        before = target.stat()
        second = ConsensusMaterialStore(self.root).put_text("same candidate")
        after = target.stat()
        self.assertEqual(second, first)
        self.assertEqual((after.st_dev, after.st_ino), (before.st_dev, before.st_ino))
        self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(
            len(tuple((self.root / CONSENSUS_MATERIAL_DIRECTORY).rglob("*.utf8"))),
            1,
        )

    def test_concurrent_identical_create_converges_to_one_verified_file(self) -> None:
        workers = 8
        barrier = threading.Barrier(workers)

        def put(_ordinal: int) -> ConsensusMaterialHandle:
            barrier.wait(timeout=5)
            return ConsensusMaterialStore(self.root).put_text("concurrent candidate")

        with ThreadPoolExecutor(max_workers=workers) as executor:
            handles = tuple(executor.map(put, range(workers)))
        self.assertEqual(len(set(handles)), 1)
        self.assertEqual(self.store.read_text(handles[0]), "concurrent candidate")
        self.assert_single_candidate_and_lock(self.root)

    def test_lock_bootstrap_opens_the_exclusive_create_winner(self) -> None:
        content = "bootstrap contention"
        candidate, lock = self.paths_for_content(self.root, content)
        lock.parent.mkdir(parents=True)
        real_open = os.open
        lock_calls: list[int] = []

        def open_with_bootstrap_winner(
            path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            if path == ".lock":
                lock_calls.append(flags)
                if len(lock_calls) == 2:
                    winner = real_open(
                        ".lock",
                        os.O_RDWR | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=dir_fd,
                    )
                    os.close(winner)
                    raise FileExistsError(
                        errno.EEXIST,
                        "simulated exclusive-create winner",
                        ".lock",
                    )
            return real_open(path, flags, mode, dir_fd=dir_fd)

        shard = os.open(
            lock.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        opened_lock = -1
        try:
            with mock.patch(
                "story_writing_agents.consensus.material_store.os.open",
                side_effect=open_with_bootstrap_winner,
            ):
                opened_lock = ConsensusMaterialStore._open_lock(shard)
        finally:
            if opened_lock >= 0:
                os.close(opened_lock)
            os.close(shard)

        open_existing = os.O_RDWR | os.O_NOFOLLOW
        self.assertEqual(
            lock_calls,
            [
                open_existing,
                open_existing | os.O_CREAT | os.O_EXCL,
                open_existing,
            ],
        )
        self.assertTrue(lock.is_file())

        handle = self.store.put_text(content)
        self.assertEqual(self.material_path(handle), candidate)
        self.assertEqual(candidate.read_text(), content)
        self.assert_single_candidate_and_lock(self.root)

    def test_unsafe_named_lock_types_fail_closed_without_touching_targets(self) -> None:
        for kind in ("symlink", "directory", "hardlink"):
            with self.subTest(kind=kind):
                root = self.root / kind
                root.mkdir()
                content = f"unsafe {kind} lock"
                candidate, lock = self.paths_for_content(root, content)
                lock.parent.mkdir(parents=True)
                outside = self.root.parent / f"{kind}-lock-target"
                if kind == "symlink":
                    outside.write_bytes(b"symlink target")
                    lock.symlink_to(outside)
                elif kind == "directory":
                    lock.mkdir()
                else:
                    outside.write_bytes(b"hardlink target")
                    os.link(outside, lock)

                with self.assertRaisesRegex(
                    ConsensusMaterialStoreSecurityError,
                    "lock",
                ):
                    ConsensusMaterialStore(root).put_text(content)

                self.assertFalse(candidate.exists())
                if kind == "directory":
                    self.assertTrue(lock.is_dir())
                else:
                    expected = (
                        b"symlink target" if kind == "symlink" else b"hardlink target"
                    )
                    self.assertEqual(outside.read_bytes(), expected)
                    self.assertEqual(lock.read_bytes(), expected)

    def test_named_lock_inode_replacement_fails_closed(self) -> None:
        content = "replace named lock"
        candidate, lock = self.paths_for_content(self.root, content)
        lock.parent.mkdir(parents=True)
        lock.write_bytes(b"original lock")
        original_identity = (lock.stat().st_dev, lock.stat().st_ino)
        assert material_store_module.fcntl is not None
        real_flock = material_store_module.fcntl.flock

        def flock_and_replace(descriptor: int, operation: int) -> None:
            real_flock(descriptor, operation)
            lock.unlink()
            lock.write_bytes(b"replacement lock")

        with mock.patch(
            "story_writing_agents.consensus.material_store.fcntl.flock",
            side_effect=flock_and_replace,
        ):
            with self.assertRaisesRegex(
                ConsensusMaterialStoreSecurityError,
                "lock identity",
            ):
                self.store.put_text(content)

        self.assertFalse(candidate.exists())
        self.assertEqual(lock.read_bytes(), b"replacement lock")
        self.assertNotEqual(
            (lock.stat().st_dev, lock.stat().st_ino),
            original_identity,
        )

    def test_independent_processes_converge_on_first_identical_put(self) -> None:
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(2)
        results = context.Queue()
        content = "independent process candidate"
        processes = [
            context.Process(
                target=_put_from_spawned_process,
                args=(str(self.root), content, barrier, results),
            )
            for _index in range(2)
        ]
        try:
            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=15)
            self.assertFalse([process for process in processes if process.is_alive()])
            self.assertEqual([process.exitcode for process in processes], [0, 0])
            observed = [results.get(timeout=5) for _process in processes]
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=5)
            results.close()
            results.join_thread()

        self.assertEqual({row[0] for row in observed}, {"ok"})
        self.assertEqual(len(set(observed)), 1)
        handle = ConsensusMaterialHandle(observed[0][1], observed[0][2])
        self.assertEqual(self.store.read_text(handle), content)
        self.assert_single_candidate_and_lock(self.root)

    def test_digest_corruption_collision_and_unverified_cleanup_fail_closed(self) -> None:
        handle = self.store.put_text("trusted candidate")
        target = self.material_path(handle)
        original = target.read_bytes()
        target.write_bytes(b"corrupted")
        with self.assertRaisesRegex(ConsensusMaterialStoreError, "digest mismatch"):
            self.store.read_text(handle)
        with self.assertRaisesRegex(ConsensusMaterialStoreError, "digest mismatch"):
            self.store.delete(handle)
        self.assertEqual(target.read_bytes(), b"corrupted")
        target.write_bytes(original)
        self.assertTrue(self.store.delete(handle))

        raw = b"expected bytes"
        digest = "a" * 64
        collision = ConsensusMaterialHandle(f"candidate:{digest}", digest)
        collision_path = self.material_path(collision)
        collision_path.parent.mkdir(parents=True, exist_ok=True)
        collision_path.write_bytes(b"different bytes")
        fake_hash = mock.Mock()
        fake_hash.hexdigest.return_value = digest
        with mock.patch(
            "story_writing_agents.consensus.material_store.hashlib.sha256",
            return_value=fake_hash,
        ):
            with self.assertRaisesRegex(ConsensusMaterialStoreError, "collision"):
                self.store.put_text(raw.decode())

    def test_text_and_stored_byte_validation_is_strict(self) -> None:
        for invalid in (None, b"bytes", "", "has\x00nul"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex((TypeError, ValueError), "nonempty NUL-free"):
                    self.store.put_text(invalid)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "byte limit"):
            self.store.put_text("x" * (MAX_CONSENSUS_MATERIAL_BYTES + 1))

        invalid_utf8 = self.install_raw(b"\xff")
        with self.assertRaisesRegex(ConsensusMaterialStoreError, "strict UTF-8"):
            self.store.read_text(invalid_utf8)
        stored_nul = self.install_raw(b"stored\x00nul")
        with self.assertRaisesRegex(ConsensusMaterialStoreError, "contains NUL"):
            self.store.read_text(stored_nul)

        oversized_digest = "b" * 64
        oversized = ConsensusMaterialHandle(
            f"candidate:{oversized_digest}", oversized_digest
        )
        oversized_path = self.material_path(oversized)
        oversized_path.parent.mkdir(parents=True, exist_ok=True)
        with oversized_path.open("wb") as stream:
            stream.truncate(MAX_CONSENSUS_MATERIAL_BYTES + 1)
        with self.assertRaisesRegex(ConsensusMaterialStoreSecurityError, "identity"):
            self.store.read_text(oversized)

    def test_symlinked_root_ancestry_shard_entry_and_forged_locator_are_rejected(self) -> None:
        outside = self.root.parent / "outside"
        outside.mkdir()
        root_link = self.root.parent / "workspace-link"
        root_link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ConsensusMaterialStoreSecurityError, "securely open"):
            ConsensusMaterialStore(root_link).put_text("candidate")

        ancestry_root = self.root.parent / "unsafe-ancestry"
        ancestry_root.mkdir()
        (ancestry_root / "agent_states").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ConsensusMaterialStoreSecurityError):
            ConsensusMaterialStore(ancestry_root).put_text("candidate")

        self.store.put_text("create store")
        occupied_prefixes = {
            path.name
            for path in (self.root / CONSENSUS_MATERIAL_DIRECTORY).iterdir()
            if path.is_dir()
        }
        shard_content = next(
            f"unsafe shard {index}"
            for index in range(256)
            if hashlib.sha256(f"unsafe shard {index}".encode()).hexdigest()[:2]
            not in occupied_prefixes
        )
        shard_digest = hashlib.sha256(shard_content.encode()).hexdigest()
        shard = self.root / CONSENSUS_MATERIAL_DIRECTORY / shard_digest[:2]
        shard.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ConsensusMaterialStoreSecurityError, "shard"):
            self.store.put_text(shard_content)

        raw = b"unsafe entry"
        entry_digest = hashlib.sha256(raw).hexdigest()
        entry_handle = ConsensusMaterialHandle(
            f"candidate:{entry_digest}", entry_digest
        )
        entry = self.material_path(entry_handle)
        entry.parent.mkdir(parents=True, exist_ok=True)
        outside_file = outside / "outside.txt"
        outside_file.write_bytes(raw)
        entry.symlink_to(outside_file)
        with self.assertRaisesRegex(ConsensusMaterialStoreSecurityError, "entry"):
            self.store.read_text(entry_handle)

        valid = self.store.put_text("locator target")
        with self.assertRaisesRegex(ValueError, "locator is invalid"):
            ConsensusMaterialHandle(f"candidate:{'c' * 64}", valid.digest)
        self.assertEqual(self.store.read_text(valid), "locator target")


if __name__ == "__main__":
    unittest.main()
