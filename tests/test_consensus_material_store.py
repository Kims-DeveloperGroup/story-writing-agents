"""Security and concurrency tests for unpublished consensus material storage."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from story_writing_agents.consensus.contracts import ConsensusMaterialHandle
from story_writing_agents.consensus.material_store import (
    CONSENSUS_MATERIAL_DIRECTORY,
    MAX_CONSENSUS_MATERIAL_BYTES,
    ConsensusMaterialStore,
    ConsensusMaterialStoreError,
    ConsensusMaterialStoreSecurityError,
)


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

    def test_lifecycle_is_content_addressed_and_readable_by_fresh_instance(self) -> None:
        content = "Opening revision 한글\n"
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
        self.assertEqual(
            len(tuple((self.root / CONSENSUS_MATERIAL_DIRECTORY).rglob("*.utf8"))),
            1,
        )

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
        forged = ConsensusMaterialHandle(f"candidate:{'c' * 64}", valid.digest)
        with self.assertRaisesRegex(ConsensusMaterialStoreError, "handle is invalid"):
            self.store.read_text(forged)
        self.assertEqual(self.store.read_text(valid), "locator target")


if __name__ == "__main__":
    unittest.main()
