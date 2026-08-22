"""Canonical writing-artifact grammar, provenance, and storage tests."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from story_writing_agents import Workspace
from story_writing_agents.agents import (
    CORE_DOCUMENTS,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
)
from story_writing_agents.errors import (
    ManuscriptExistsError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from story_writing_agents.writing_artifacts import (
    CORE_ARTIFACT_PATHS,
    ArtifactCatalog,
    ArtifactCatalogEntry,
    ArtifactKind,
    ArtifactLineRange,
    ArtifactReference,
    canonical_artifact_kind,
    manuscript_artifact_path,
)
from story_writing_agents.writing_artifacts import _storage as storage_module
from story_writing_agents.writing_artifacts._storage import SecureWorkspaceStorage
from story_writing_agents.writing_artifacts.repository import (
    WritingArtifactRepository,
)


class WritingArtifactTestCase(unittest.TestCase):
    """Own one initialized workspace without generated writing documents."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = Workspace(self.root).initialize()
        self.repository = WritingArtifactRepository(self.root)

    def commit(
        self,
        output_path: str = "part-01/chapter-01.md",
        *,
        candidate: str = "agent-authored manuscript\n",
        core_updates: dict[str, str] | None = None,
        debate_id: str = "debate_01",
        revision_id: str = "r0",
    ) -> tuple[str, tuple[str, ...]]:
        return self.repository.commit_consensus(
            output_path=output_path,
            candidate=candidate,
            core_updates={} if core_updates is None else core_updates,
            session_id="session_01",
            debate_id=debate_id,
            revision_id=revision_id,
            candidate_author_agent_id=EXECUTION_SPECIALIST,
        )


class ArtifactGrammarTests(unittest.TestCase):
    def test_only_four_core_paths_and_part_manuscripts_are_canonical(self) -> None:
        self.assertEqual(
            CORE_ARTIFACT_PATHS,
            tuple(f"core_documents/{name}" for name in CORE_DOCUMENTS),
        )
        for path in CORE_ARTIFACT_PATHS:
            with self.subTest(path=path):
                self.assertIs(
                    canonical_artifact_kind(path),
                    ArtifactKind.CORE_DOCUMENT,
                )

        accepted = (
            "manuscript/part-01/chapter.md",
            "manuscript/Part_A/scenes/opening.v2.md",
        )
        for path in accepted:
            with self.subTest(path=path):
                self.assertIs(
                    canonical_artifact_kind(path),
                    ArtifactKind.MANUSCRIPT,
                )

        excluded = (
            "core_documents/extra.md",
            "transcripts/session.md",
            "discussion_logs/debate.md",
            "agent_states/orchestrator_state/state.md",
            "manuscript/root.md",
            "manuscript/part-01/chapter.txt",
            "manuscript/part-01/.md",
            "manuscript/bad part/chapter.md",
        )
        for path in excluded:
            with self.subTest(path=path):
                self.assertIsNone(canonical_artifact_kind(path))

    def test_unsafe_non_normalized_and_non_text_paths_fail_before_io(self) -> None:
        invalid = (
            "../core_documents/global_outline.md",
            "/core_documents/global_outline.md",
            "core_documents//global_outline.md",
            "core_documents/./global_outline.md",
            "core_documents\\global_outline.md",
            "core_documents/global_outline.md\x00",
        )
        for path in invalid:
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    canonical_artifact_kind(path)
        with self.assertRaises(TypeError):
            canonical_artifact_kind(Path("core_documents/global_outline.md"))

    def test_required_output_grammar_has_no_root_or_compatibility_form(self) -> None:
        self.assertEqual(
            manuscript_artifact_path("part-02/revisions/chapter-03.md"),
            "manuscript/part-02/revisions/chapter-03.md",
        )
        rejected = (
            "chapter.md",
            "manuscript/part-02/chapter.md",
            "part-02/chapter.txt",
            "part 02/chapter.md",
            "part-02/../chapter.md",
        )
        for value in rejected:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    manuscript_artifact_path(value)

    def test_catalog_and_reference_values_enforce_exact_schema(self) -> None:
        entry = ArtifactCatalogEntry(
            path=CORE_ARTIFACT_PATHS[0],
            kind=ArtifactKind.CORE_DOCUMENT,
            sha256="a" * 64,
            commit_id="b" * 32,
            committed_at="2026-08-22T00:00:00+00:00",
            author_agent_id=LEAD_ORCHESTRATOR,
            session_id="session_01",
            debate_id="debate_01",
            revision_id="r0",
        )
        catalog = ArtifactCatalog({entry.path: entry})
        self.assertEqual(ArtifactCatalog.from_dict(catalog.to_dict()), catalog)
        with self.assertRaises(ValueError):
            ArtifactCatalog.from_dict(
                {**catalog.to_dict(), "unexpected": True}
            )
        malformed = entry.to_dict()
        malformed["unexpected"] = "field"
        with self.assertRaises(ValueError):
            ArtifactCatalogEntry.from_dict(malformed)
        with self.assertRaises(ValueError):
            ArtifactCatalog({"wrong": entry})

        selected = ArtifactReference(
            "manuscript/part-01/chapter.md",
            ArtifactLineRange(7, 9),
        )
        self.assertEqual(
            selected.token,
            "${manuscript/part-01/chapter.md}#L7-L9",
        )
        with self.assertRaises(ValueError):
            ArtifactLineRange(0, 1)
        with self.assertRaises(ValueError):
            ArtifactReference("discussion_logs/private.md")


class RepositoryEligibilityTests(WritingArtifactTestCase):
    def test_manual_placeholders_and_internal_files_are_never_eligible(self) -> None:
        for path in (
            *CORE_ARTIFACT_PATHS,
            "manuscript/part-01/manual.md",
            "discussion_logs/private.md",
            "agent_states/private.md",
        ):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("manual or initialized bytes", encoding="utf-8")

        self.assertEqual(self.repository.list_paths(), ())
        for path in (*CORE_ARTIFACT_PATHS, "manuscript/part-01/manual.md"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(
                    WorkspaceSecurityError,
                    "no repository provenance",
                ):
                    self.repository.read_text(path)

    def test_consensus_persists_exact_agent_bytes_and_all_four_updates(self) -> None:
        updates = {
            filename: f"agent-returned::{filename}\n"
            for filename in reversed(CORE_DOCUMENTS)
        }
        manuscript_path, ordered_updates = self.commit(
            candidate="agent-returned::manuscript\n",
            core_updates=updates,
        )

        self.assertEqual(manuscript_path, "manuscript/part-01/chapter-01.md")
        self.assertEqual(ordered_updates, CORE_DOCUMENTS)
        expected_paths = tuple(sorted((*CORE_ARTIFACT_PATHS, manuscript_path)))
        self.assertEqual(self.repository.list_paths(), expected_paths)
        self.assertEqual(
            self.repository.read_text(manuscript_path),
            "agent-returned::manuscript\n",
        )
        for filename, content in updates.items():
            with self.subTest(filename=filename):
                path = f"core_documents/{filename}"
                self.assertEqual(self.repository.read_text(path), content)
                self.assertEqual((self.root / path).read_text(), content)

        catalog = self.repository.catalog()
        commit_ids = {entry.commit_id for entry in catalog.entries.values()}
        self.assertEqual(len(commit_ids), 1)
        self.assertEqual(
            catalog.entries[manuscript_path].author_agent_id,
            EXECUTION_SPECIALIST,
        )
        for path in CORE_ARTIFACT_PATHS:
            self.assertEqual(
                catalog.entries[path].author_agent_id,
                LEAD_ORCHESTRATOR,
            )

    def test_hash_drift_and_missing_commit_provenance_remove_eligibility(self) -> None:
        manuscript_path, _ = self.commit()
        (self.root / manuscript_path).write_text("manual drift", encoding="utf-8")
        self.assertEqual(self.repository.list_paths(), ())
        with self.assertRaisesRegex(WorkspaceSecurityError, "does not match"):
            self.repository.read_text(manuscript_path)

        (self.root / manuscript_path).write_text(
            "agent-authored manuscript\n",
            encoding="utf-8",
        )
        other = WritingArtifactRepository(self.root)
        self.assertEqual(other.list_paths(), (manuscript_path,))
        catalog_data = json.loads(
            (
                self.root
                / "agent_states/orchestrator_state/writing_artifacts/catalog.json"
            ).read_text()
        )
        commit_id = catalog_data["entries"][0]["commit_id"]
        (
            self.root
            / "agent_states/orchestrator_state/writing_artifacts/commits"
            / f"{commit_id}.json"
        ).unlink()
        self.assertEqual(other.list_paths(), ())

    def test_read_rejects_oversize_and_non_utf8_current_bytes(self) -> None:
        manuscript_path, _ = self.commit(candidate="é")
        with self.assertRaisesRegex(WorkspaceFormatError, "permitted size"):
            self.repository.read_text(manuscript_path, max_bytes=1)

        path = self.root / manuscript_path
        invalid = b"\xff"
        path.write_bytes(invalid)
        catalog_path = (
            self.root
            / "agent_states/orchestrator_state/writing_artifacts/catalog.json"
        )
        catalog = json.loads(catalog_path.read_text())
        catalog["entries"][0]["sha256"] = hashlib.sha256(invalid).hexdigest()
        commit_id = catalog["entries"][0]["commit_id"]
        commit_path = (
            self.root
            / "agent_states/orchestrator_state/writing_artifacts/commits"
            / f"{commit_id}.json"
        )
        commit = json.loads(commit_path.read_text())
        commit["artifacts"][0]["sha256"] = hashlib.sha256(invalid).hexdigest()
        commit_path.write_text(json.dumps(commit), encoding="utf-8")
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        with self.assertRaisesRegex(WorkspaceFormatError, "valid UTF-8"):
            WritingArtifactRepository(self.root).read_text(manuscript_path)

    def test_manuscripts_are_create_only_but_distinct_revisions_are_allowed(self) -> None:
        self.commit(candidate="revision one")
        with self.assertRaisesRegex(ManuscriptExistsError, "refusing to overwrite"):
            self.commit(candidate="overwrite", debate_id="debate_02")
        second, _ = self.commit(
            "part-01/revisions/chapter-01-r2.md",
            candidate="revision two",
            debate_id="debate_02",
            revision_id="r1",
        )
        self.assertEqual(
            self.repository.read_text(second),
            "revision two",
        )

    def test_manual_target_collision_and_unknown_core_update_fail_before_publish(self) -> None:
        target = self.root / "manuscript/part-01/collision.md"
        target.parent.mkdir(parents=True)
        target.write_text("manual", encoding="utf-8")
        with self.assertRaises(ManuscriptExistsError):
            self.commit("part-01/collision.md")
        with self.assertRaisesRegex(WorkspaceSecurityError, "not allowlisted"):
            self.commit(
                "part-01/unknown-core.md",
                core_updates={"private.md": "must not publish"},
            )
        self.assertFalse(self.root.joinpath("manuscript/part-01/unknown-core.md").exists())


@unittest.skipUnless(os.name == "posix", "secure descriptor tests require POSIX")
class SecureStorageTests(WritingArtifactTestCase):
    def test_storage_rejects_traversal_symlink_hardlink_and_nonregular_files(self) -> None:
        storage = SecureWorkspaceStorage(self.root)
        for path in ("../outside", "/absolute", "a//b", "a\\b"):
            with self.subTest(path=path):
                with self.assertRaises(WorkspaceSecurityError):
                    storage.read_bytes(path)

        cases = ("symlink", "hardlink", "directory")
        for index, kind in enumerate(cases):
            with self.subTest(kind=kind):
                output = f"part-{index + 1:02d}/chapter.md"
                artifact_path, _ = self.commit(
                    output,
                    debate_id=f"secure_{index}",
                )
                target = self.root / artifact_path
                original = target.read_bytes()
                target.unlink()
                if kind == "symlink":
                    outside = self.root / f"outside-{index}.md"
                    outside.write_bytes(original)
                    target.symlink_to(outside)
                elif kind == "hardlink":
                    outside = self.root / f"outside-{index}.md"
                    outside.write_bytes(original)
                    os.link(outside, target)
                else:
                    target.mkdir()
                self.assertNotIn(artifact_path, self.repository.list_paths())
                with self.assertRaises(WorkspaceSecurityError):
                    self.repository.read_text(artifact_path)

    def test_root_replacement_is_detected_by_a_live_repository(self) -> None:
        self.assertEqual(self.repository.list_paths(), ())
        original = self.root.with_name(f"{self.root.name}-original")
        self.root.rename(original)
        self.root.mkdir()
        with self.assertRaisesRegex(WorkspaceSecurityError, "identity changed"):
            self.repository.list_paths()

    def test_unsupported_safe_primitives_fail_at_construction(self) -> None:
        with mock.patch.object(storage_module, "fcntl", None):
            with self.assertRaisesRegex(
                WorkspaceSecurityError,
                "locking or listing unavailable",
            ):
                SecureWorkspaceStorage(self.root)
        with mock.patch.object(storage_module.os, "supports_dir_fd", set()):
            with self.assertRaisesRegex(
                WorkspaceSecurityError,
                "descriptor-relative",
            ):
                SecureWorkspaceStorage(self.root)

    def test_workspace_lock_serializes_independent_repository_instances(self) -> None:
        first = SecureWorkspaceStorage(self.root)
        second = SecureWorkspaceStorage(self.root)
        acquired = threading.Event()
        finished = threading.Event()

        def contender() -> None:
            with second.exclusive_lock(
                "agent_states/orchestrator_state/writing_artifacts/.lock-test"
            ):
                acquired.set()
            finished.set()

        with first.exclusive_lock(
            "agent_states/orchestrator_state/writing_artifacts/.lock-test"
        ):
            thread = threading.Thread(target=contender, daemon=True)
            thread.start()
            self.assertFalse(acquired.wait(timeout=0.1))
        self.assertTrue(finished.wait(timeout=1))
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())

    def test_lock_name_replacement_never_enters_protected_body(self) -> None:
        relative_parent = (
            "agent_states/orchestrator_state/writing_artifacts"
        )
        real_verify = SecureWorkspaceStorage._verify_lock_identity

        for replacement_call in (1, 2):
            with self.subTest(replacement_call=replacement_call):
                relative = f"{relative_parent}/.replacement-{replacement_call}.lock"
                lock_path = self.root / relative
                storage = SecureWorkspaceStorage(self.root)
                verification_calls = 0
                body_entered = False

                def replace_then_verify(
                    directory: int,
                    name: str,
                    descriptor: int,
                ) -> None:
                    nonlocal verification_calls
                    verification_calls += 1
                    if verification_calls == replacement_call:
                        replacement = lock_path.with_suffix(".replacement")
                        replacement.write_bytes(b"unsafe replacement")
                        lock_path.unlink()
                        replacement.rename(lock_path)
                    real_verify(directory, name, descriptor)

                with (
                    mock.patch.object(
                        SecureWorkspaceStorage,
                        "_verify_lock_identity",
                        side_effect=replace_then_verify,
                    ),
                    self.assertRaisesRegex(
                        WorkspaceSecurityError,
                        "artifact lock",
                    ),
                ):
                    with storage.exclusive_lock(relative):
                        body_entered = True

                self.assertFalse(body_entered)
                self.assertEqual(verification_calls, replacement_call)
                with storage.exclusive_lock(relative):
                    body_entered = True
                self.assertTrue(body_entered)


class RepositoryRecoveryTests(WritingArtifactTestCase):
    def test_publication_is_catalog_last(self) -> None:
        observed: list[str] = []
        publish = self.repository._storage.publish_bytes

        def record(relative: str, *args, **kwargs):
            observed.append(relative)
            return publish(relative, *args, **kwargs)

        with mock.patch.object(
            self.repository._storage,
            "publish_bytes",
            side_effect=record,
        ):
            self.commit(
                core_updates={"global_outline.md": "agent outline"},
            )

        catalog_path = "agent_states/orchestrator_state/writing_artifacts/catalog.json"
        manuscript_index = observed.index("manuscript/part-01/chapter-01.md")
        core_index = observed.index("core_documents/global_outline.md")
        catalog_index = max(
            index for index, path in enumerate(observed) if path == catalog_path
        )
        self.assertLess(manuscript_index, catalog_index)
        self.assertLess(core_index, catalog_index)

    def test_interrupted_publication_has_no_partial_eligibility_and_rolls_forward(self) -> None:
        publish = self.repository._storage.publish_bytes
        artifact_publications = 0

        def interrupt(relative: str, *args, **kwargs):
            nonlocal artifact_publications
            if canonical_artifact_kind(relative) is not None:
                artifact_publications += 1
                if artifact_publications == 2:
                    raise OSError("simulated interruption")
            return publish(relative, *args, **kwargs)

        with (
            mock.patch.object(
                self.repository._storage,
                "publish_bytes",
                side_effect=interrupt,
            ),
            self.assertRaisesRegex(OSError, "simulated interruption"),
        ):
            self.commit(core_updates={"global_outline.md": "agent outline"})

        state = self.root / "agent_states/orchestrator_state/writing_artifacts"
        self.assertTrue(self.root.joinpath("manuscript/part-01/chapter-01.md").is_file())
        self.assertFalse(state.joinpath("catalog.json").exists())
        self.assertEqual(len(tuple(state.joinpath("transactions").glob("*.json"))), 1)

        recovered = WritingArtifactRepository(self.root)
        self.assertEqual(
            recovered.list_paths(),
            (
                "core_documents/global_outline.md",
                "manuscript/part-01/chapter-01.md",
            ),
        )
        self.assertFalse(tuple(state.joinpath("transactions").iterdir()))

    def test_unrecoverable_journal_blocks_all_later_repository_access(self) -> None:
        publish = self.repository._storage.publish_bytes

        def interrupt(relative: str, *args, **kwargs):
            if relative == "manuscript/part-01/chapter-01.md":
                raise OSError("simulated interruption")
            return publish(relative, *args, **kwargs)

        with (
            mock.patch.object(
                self.repository._storage,
                "publish_bytes",
                side_effect=interrupt,
            ),
            self.assertRaisesRegex(OSError, "simulated interruption"),
        ):
            self.commit()

        state = self.root / "agent_states/orchestrator_state/writing_artifacts"
        transaction = next(state.joinpath("transactions").glob("*.json"))
        transaction.write_text("{}", encoding="utf-8")
        recovered = WritingArtifactRepository(self.root)
        with self.assertRaisesRegex(WorkspaceFormatError, "recovery is blocked"):
            recovered.list_paths()
        marker = state / "recovery_blocked.json"
        self.assertTrue(marker.is_file())
        with self.assertRaisesRegex(WorkspaceFormatError, "recovery is blocked"):
            WritingArtifactRepository(self.root).catalog()

    def test_recovery_preserves_conflicting_immutable_commit_and_blocks(self) -> None:
        publish = self.repository._storage.publish_bytes

        def interrupt(relative: str, *args, **kwargs):
            if relative == "manuscript/part-01/chapter-01.md":
                raise OSError("simulated interruption before artifact publication")
            return publish(relative, *args, **kwargs)

        with (
            mock.patch.object(
                self.repository._storage,
                "publish_bytes",
                side_effect=interrupt,
            ),
            self.assertRaisesRegex(OSError, "simulated interruption"),
        ):
            self.commit()

        state = self.root / "agent_states/orchestrator_state/writing_artifacts"
        transaction = next(state.joinpath("transactions").glob("*.json"))
        commit_id = json.loads(transaction.read_text())["transaction_id"]
        commit_path = state / "commits" / f"{commit_id}.json"
        conflicting_bytes = b'{"preserved":"conflicting commit"}\n'
        commit_path.write_bytes(conflicting_bytes)

        recovered = WritingArtifactRepository(self.root)
        with self.assertRaisesRegex(WorkspaceFormatError, "recovery is blocked"):
            recovered.list_paths()

        self.assertEqual(commit_path.read_bytes(), conflicting_bytes)
        self.assertTrue(state.joinpath("recovery_blocked.json").is_file())
        self.assertTrue(transaction.is_file())
        self.assertFalse(state.joinpath("catalog.json").exists())
        self.assertTrue(
            self.root.joinpath("manuscript/part-01/chapter-01.md").is_file()
        )
        with self.assertRaisesRegex(WorkspaceFormatError, "recovery is blocked"):
            WritingArtifactRepository(self.root).catalog()


if __name__ == "__main__":
    unittest.main()
