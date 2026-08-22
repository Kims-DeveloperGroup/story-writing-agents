"""Canonical writing-artifact grammar, provenance, and storage tests."""

from __future__ import annotations

import hashlib
import json
import os
import errno
import socket
import stat
import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from story_writing_agents import Workspace
from story_writing_agents.agents import (
    CHARACTER_CUSTODIAN,
    CORE_DOCUMENTS,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
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
from story_writing_agents.writing_artifacts._storage import (
    ArtifactEntrySecurityError,
    ArtifactRootSecurityError,
    SecureWorkspaceStorage,
)
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
        candidate_author_agent_id: str | None = None,
    ) -> tuple[str, tuple[str, ...]]:
        return self.repository.commit_consensus(
            output_path=output_path,
            candidate=candidate,
            core_updates={} if core_updates is None else core_updates,
            session_id="session_01",
            debate_id=debate_id,
            revision_id=revision_id,
            candidate_author_agent_id=(
                EXECUTION_SPECIALIST
                if candidate_author_agent_id is None and revision_id == "r0"
                else LEAD_ORCHESTRATOR
                if candidate_author_agent_id is None
                else candidate_author_agent_id
            ),
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
        self.assertEqual(
            manuscript_artifact_path("manuscript/chapter.md"),
            "manuscript/manuscript/chapter.md",
        )
        rejected = (
            "chapter.md",
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
        for invalid_schema in (True, 1.0, "1", None):
            with self.subTest(invalid_schema=invalid_schema):
                with self.assertRaises(ValueError):
                    ArtifactCatalog.from_dict(
                        {
                            **catalog.to_dict(),
                            "schema_version": invalid_schema,
                        }
                    )

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

    def test_selective_reads_touch_only_requested_artifacts_not_manuscripts(self) -> None:
        self.commit(
            core_updates={"global_outline.md": "verified outline"},
        )
        for index in range(8):
            self.commit(
                f"part-{index + 2:02d}/large.md",
                candidate=(f"manuscript-{index}\n" * 4096),
                debate_id=f"selective_{index}",
            )

        read_bytes = self.repository._storage.read_bytes
        observed_artifacts: list[str] = []

        def record(relative: str, *args, **kwargs):
            if canonical_artifact_kind(relative) is not None:
                observed_artifacts.append(relative)
            return read_bytes(relative, *args, **kwargs)

        requested = ("core_documents/global_outline.md",)
        with mock.patch.object(
            self.repository._storage,
            "read_bytes",
            side_effect=record,
        ):
            snapshots = self.repository.read_current(requested)

        self.assertEqual(tuple(snapshot.path for snapshot in snapshots), requested)
        self.assertEqual(observed_artifacts, list(requested))
        self.assertEqual(snapshots[0].read_text(), "verified outline")

    def test_catalog_omits_entry_local_failures_but_propagates_root_failures(self) -> None:
        manuscript_path, _ = self.commit()
        read_bytes = self.repository._storage.read_bytes

        def fail_entry(relative: str, *args, **kwargs):
            if relative == manuscript_path:
                raise ArtifactEntrySecurityError("entry changed")
            return read_bytes(relative, *args, **kwargs)

        with mock.patch.object(
            self.repository._storage,
            "read_bytes",
            side_effect=fail_entry,
        ):
            self.assertEqual(self.repository.list_paths(), ())

        def fail_root(relative: str, *args, **kwargs):
            if relative == manuscript_path:
                raise ArtifactRootSecurityError("root changed")
            return read_bytes(relative, *args, **kwargs)

        with mock.patch.object(
            self.repository._storage,
            "read_bytes",
            side_effect=fail_root,
        ):
            with self.assertRaisesRegex(ArtifactRootSecurityError, "root changed"):
                self.repository.catalog()
        self.assertEqual(self.repository.list_paths(), (manuscript_path,))

    def test_candidate_author_matches_revision_and_manuscript_is_a_part_id(self) -> None:
        rejected = (
            ("r0", LEAD_ORCHESTRATOR),
            ("r0", PLOT_ARCHITECT),
            ("r0", CHARACTER_CUSTODIAN),
            ("r1", EXECUTION_SPECIALIST),
        )
        for index, (revision_id, author) in enumerate(rejected):
            with self.subTest(revision_id=revision_id, author=author):
                with self.assertRaisesRegex(
                    WorkspaceSecurityError,
                    "candidate author",
                ):
                    self.commit(
                        f"rejected-{index}/chapter.md",
                        debate_id=f"rejected_{index}",
                        revision_id=revision_id,
                        candidate_author_agent_id=author,
                    )

        path, _ = self.commit(
            "manuscript/chapter.md",
            revision_id="r1",
            candidate_author_agent_id=LEAD_ORCHESTRATOR,
        )
        self.assertEqual(path, "manuscript/manuscript/chapter.md")
        self.assertEqual(
            self.repository.catalog().entries[path].author_agent_id,
            LEAD_ORCHESTRATOR,
        )

    def test_malformed_recovered_author_provenance_is_rejected(self) -> None:
        manuscript_path, _ = self.commit()
        catalog = self.repository.catalog()
        commit_id = catalog.entries[manuscript_path].commit_id
        commit_path = self.root.joinpath(
            "agent_states/orchestrator_state/writing_artifacts/commits",
            f"{commit_id}.json",
        )
        commit = json.loads(commit_path.read_text(encoding="utf-8"))
        commit["artifacts"][0]["author_agent_id"] = PLOT_ARCHITECT
        commit_path.write_text(json.dumps(commit), encoding="utf-8")

        with self.assertRaisesRegex(
            WorkspaceFormatError,
            "commit author is invalid",
        ):
            WritingArtifactRepository(self.root).catalog()


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

    def test_unsupported_safe_primitives_fail_on_first_artifact_operation(self) -> None:
        with mock.patch.object(storage_module, "fcntl", None):
            storage = SecureWorkspaceStorage(self.root)
            repository = WritingArtifactRepository(self.root)
            self.workspace.write_text("discussion_logs/still-available.txt", "ok")
            self.assertEqual(
                self.workspace.read_text("discussion_logs/still-available.txt"),
                "ok",
            )
            with self.assertRaisesRegex(
                WorkspaceSecurityError,
                "locking or listing unavailable",
            ):
                storage.read_bytes("core_documents/global_outline.md")
            with self.assertRaisesRegex(
                WorkspaceSecurityError,
                "locking or listing unavailable",
            ):
                repository.list_paths()
        with mock.patch.object(storage_module.os, "supports_dir_fd", set()):
            storage = SecureWorkspaceStorage(self.root)
            with self.assertRaisesRegex(
                WorkspaceSecurityError,
                "descriptor-relative",
            ):
                storage.read_bytes("core_documents/global_outline.md")

    def test_nonregular_reads_reject_before_opening_and_never_block(self) -> None:
        parent = self.root / "nonregular"
        parent.mkdir()
        fifo = parent / "fifo"
        os.mkfifo(fifo)
        unix_socket = parent / "socket"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(str(unix_socket))
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, directory)

        for name in (fifo.name, unix_socket.name):
            with self.subTest(name=name):
                with (
                    mock.patch.object(
                        storage_module.os,
                        "open",
                        side_effect=AssertionError("nonregular file was opened"),
                    ),
                    self.assertRaisesRegex(
                        ArtifactEntrySecurityError,
                        "unique regular file",
                    ),
                ):
                    SecureWorkspaceStorage._read_file(directory, name)

        device_stat = SimpleNamespace(st_mode=stat.S_IFCHR, st_nlink=1)
        with (
            mock.patch.object(storage_module.os, "stat", return_value=device_stat),
            mock.patch.object(
                storage_module.os,
                "open",
                side_effect=AssertionError("device was opened"),
            ),
            self.assertRaisesRegex(
                ArtifactEntrySecurityError,
                "unique regular file",
            ),
        ):
            SecureWorkspaceStorage._read_file(directory, "device")

    def test_detached_artifact_ancestor_fails_before_publication(self) -> None:
        manuscript = self.root / "manuscript"
        manuscript.mkdir(exist_ok=True)
        storage = SecureWorkspaceStorage(self.root)
        relative_lock = (
            "agent_states/orchestrator_state/writing_artifacts/.ancestor.lock"
        )
        real_verify = SecureWorkspaceStorage._verify_directory_chain
        detached = self.root / "manuscript-detached"

        def detach_then_verify(chain):
            if any(name == "manuscript" for _, name, _, _ in chain):
                manuscript.rename(detached)
                manuscript.mkdir()
            real_verify(chain)

        with storage.exclusive_lock(relative_lock):
            with (
                mock.patch.object(
                    SecureWorkspaceStorage,
                    "_verify_directory_chain",
                    side_effect=detach_then_verify,
                ),
                self.assertRaisesRegex(
                    ArtifactRootSecurityError,
                    "ancestry changed",
                ),
            ):
                storage.publish_bytes(
                    "manuscript/part-01/chapter.md",
                    b"must not publish",
                    expected_sha256=None,
                    expected_exists=False,
                    create_only=True,
                )

        self.assertFalse(self.root.joinpath("manuscript/part-01/chapter.md").exists())
        self.assertFalse(detached.joinpath("part-01/chapter.md").exists())

    def test_detachment_immediately_after_publication_retires_new_and_preserves_old(
        self,
    ) -> None:
        core = self.root / "core_documents"
        target = core / "global_outline.md"
        target.write_bytes(b"prior outline")
        detached_core = self.root / "core_documents-detached"
        rename_noreplace = SecureWorkspaceStorage._rename_noreplace
        detached = False

        def detach_after_target_publication(
            source_directory: int,
            source: str,
            destination_directory: int,
            destination: str,
        ) -> None:
            nonlocal detached
            rename_noreplace(
                source_directory,
                source,
                destination_directory,
                destination,
            )
            if (
                not detached
                and source.endswith(".tmp")
                and destination == "global_outline.md"
            ):
                detached = True
                core.rename(detached_core)
                core.mkdir()

        with (
            mock.patch.object(
                SecureWorkspaceStorage,
                "_rename_noreplace",
                side_effect=detach_after_target_publication,
            ),
            self.assertRaisesRegex(
                ArtifactRootSecurityError,
                "ancestry changed",
            ),
        ):
            self.commit(
                core_updates={"global_outline.md": "new outline"},
            )

        self.assertTrue(detached)
        self.assertFalse(target.exists())
        self.assertFalse(detached_core.joinpath("global_outline.md").exists())
        state = self.root / "agent_states/orchestrator_state/writing_artifacts"
        staging = state / "staging"
        held = tuple(staging.glob("*.held"))
        retired = tuple(staging.glob("*.retired"))
        self.assertEqual([path.read_bytes() for path in held], [b"prior outline"])
        self.assertEqual([path.read_bytes() for path in retired], [b"new outline"])
        self.assertFalse(state.joinpath("catalog.json").exists())
        self.assertEqual(len(tuple(state.joinpath("transactions").glob("*.json"))), 1)

    def test_native_no_replace_selection_and_deferred_unavailability(self) -> None:
        class NativeFunction:
            def __init__(self, error_number: int) -> None:
                self.error_number = error_number
                self.calls: list[tuple[object, ...]] = []

            def __call__(self, *args):
                self.calls.append(args)
                storage_module.ctypes.set_errno(self.error_number)
                return -1

        for platform, symbol, expected_flag in (
            ("darwin", "renameatx_np", 0x00000004),
            ("linux", "renameat2", 0x00000001),
        ):
            with self.subTest(platform=platform):
                native = NativeFunction(errno.EBADF)
                library = SimpleNamespace(**{symbol: native})
                with (
                    mock.patch.object(storage_module.sys, "platform", platform),
                    mock.patch.object(
                        storage_module.ctypes,
                        "CDLL",
                        return_value=library,
                    ),
                ):
                    selected, flag = SecureWorkspaceStorage._native_rename_function()
                self.assertIs(selected, native)
                self.assertEqual(flag, expected_flag)
                self.assertEqual(
                    native.calls,
                    [(-1, b".", -1, b".", expected_flag)],
                )

        unavailable = NativeFunction(errno.ENOSYS)
        with (
            mock.patch.object(storage_module.sys, "platform", "linux"),
            mock.patch.object(
                storage_module.ctypes,
                "CDLL",
                return_value=SimpleNamespace(renameat2=unavailable),
            ),
            self.assertRaisesRegex(
                WorkspaceSecurityError,
                "no-replace artifact rename unavailable",
            ),
        ):
            SecureWorkspaceStorage._native_rename_function()

        with mock.patch.object(
            SecureWorkspaceStorage,
            "_native_rename_function",
            side_effect=WorkspaceSecurityError(
                "atomic no-replace artifact rename unavailable"
            ),
        ):
            storage = SecureWorkspaceStorage(self.root)
            repository = WritingArtifactRepository(self.root)
            self.workspace.write_text("discussion_logs/native-unavailable.txt", "ok")
            self.assertEqual(
                self.workspace.read_text("discussion_logs/native-unavailable.txt"),
                "ok",
            )
            with self.assertRaisesRegex(
                WorkspaceSecurityError,
                "no-replace artifact rename unavailable",
            ):
                storage.read_bytes("core_documents/global_outline.md")
            with self.assertRaisesRegex(
                WorkspaceSecurityError,
                "no-replace artifact rename unavailable",
            ):
                repository.list_paths()

    def test_held_name_collision_survives_and_next_exclusive_name_is_used(self) -> None:
        target = self.root / "core_documents/global_outline.md"
        target.write_bytes(b"original")
        storage = SecureWorkspaceStorage(self.root)
        staging = self.root / "staging"
        staging.mkdir()
        temporary_hex = "1" * 32
        collision_hex = "2" * 32
        selected_hex = "3" * 32
        collision = staging / f".artifacts-{collision_hex}-0.held"
        collision.write_bytes(b"pre-existing held collision")

        with storage.exclusive_lock(
            "agent_states/orchestrator_state/writing_artifacts/.replace.lock"
        ):
            with mock.patch.object(
                storage_module,
                "uuid4",
                side_effect=(
                    SimpleNamespace(hex=temporary_hex),
                    SimpleNamespace(hex=collision_hex),
                    SimpleNamespace(hex=selected_hex),
                ),
            ):
                storage.publish_bytes(
                    "core_documents/global_outline.md",
                    b"replacement",
                    expected_sha256=hashlib.sha256(b"original").hexdigest(),
                    expected_exists=True,
                    create_only=False,
                    staging_directory="staging",
                )

        self.assertEqual(target.read_bytes(), b"replacement")
        self.assertEqual(collision.read_bytes(), b"pre-existing held collision")
        selected = staging / f".artifacts-{selected_hex}-1.held"
        self.assertEqual(selected.read_bytes(), b"original")

    def test_generated_temp_collision_and_failed_owned_temp_are_both_retained(self) -> None:
        storage = SecureWorkspaceStorage(self.root)
        staging = self.root / "staging"
        staging.mkdir()
        collision_hex = "a" * 32
        collision = staging / f".artifacts-{collision_hex}.tmp"
        collision.write_bytes(b"pre-existing")
        lock = "agent_states/orchestrator_state/writing_artifacts/.temp.lock"

        with storage.exclusive_lock(lock):
            with (
                mock.patch.object(
                    storage_module,
                    "uuid4",
                    return_value=SimpleNamespace(hex=collision_hex),
                ),
                self.assertRaises(FileExistsError),
            ):
                storage.publish_bytes(
                    "manuscript/part-01/collision.md",
                    b"new",
                    expected_sha256=None,
                    expected_exists=False,
                    create_only=True,
                    staging_directory="staging",
                )

        self.assertEqual(collision.read_bytes(), b"pre-existing")
        self.assertFalse(self.root.joinpath("manuscript/part-01/collision.md").exists())

        owned_hex = "b" * 32
        with storage.exclusive_lock(lock):
            with (
                mock.patch.object(
                    storage_module,
                    "uuid4",
                    return_value=SimpleNamespace(hex=owned_hex),
                ),
                mock.patch.object(
                    SecureWorkspaceStorage,
                    "_rename_noreplace",
                    side_effect=OSError("publication stopped"),
                ),
                self.assertRaisesRegex(OSError, "publication stopped"),
            ):
                storage.publish_bytes(
                    "manuscript/part-01/cleanup.md",
                    b"new",
                    expected_sha256=None,
                    expected_exists=False,
                    create_only=True,
                    staging_directory="staging",
                )
        owned = staging.joinpath(f".artifacts-{owned_hex}.tmp")
        self.assertEqual(owned.read_bytes(), b"new")
        self.assertEqual(collision.read_bytes(), b"pre-existing")

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
                    _anchor: str,
                    descriptor: int,
                ) -> None:
                    nonlocal verification_calls
                    verification_calls += 1
                    if verification_calls == replacement_call:
                        replacement = lock_path.with_suffix(".replacement")
                        replacement.write_bytes(b"unsafe replacement")
                        lock_path.unlink()
                        replacement.rename(lock_path)
                    real_verify(directory, name, _anchor, descriptor)

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
                self.assertIsNone(storage._active_lease)
                with self.assertRaisesRegex(
                    WorkspaceSecurityError,
                    "artifact lock",
                ):
                    with storage.exclusive_lock(relative):
                        body_entered = True
                self.assertFalse(body_entered)

    def test_replacing_either_paired_lock_name_is_rejected(self) -> None:
        relative_parent = "agent_states/orchestrator_state/writing_artifacts"
        real_verify = SecureWorkspaceStorage._verify_lock_identity

        for replaced_name in ("lock", "anchor"):
            with self.subTest(replaced_name=replaced_name):
                relative = f"{relative_parent}/.{replaced_name}-pair.lock"
                lock_path = self.root / relative
                anchor_path = lock_path.with_name(f"{lock_path.name}.anchor")
                body_entered = False
                calls = 0

                def replace_after_flock(directory, name, anchor, descriptor):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        selected = lock_path if replaced_name == "lock" else anchor_path
                        selected.unlink()
                        selected.write_bytes(b"replacement")
                    real_verify(directory, name, anchor, descriptor)

                storage = SecureWorkspaceStorage(self.root)
                with (
                    mock.patch.object(
                        SecureWorkspaceStorage,
                        "_verify_lock_identity",
                        side_effect=replace_after_flock,
                    ),
                    self.assertRaisesRegex(
                        WorkspaceSecurityError,
                        "artifact lock",
                    ),
                ):
                    with storage.exclusive_lock(relative):
                        body_entered = True
                self.assertFalse(body_entered)
                self.assertIsNone(storage._active_lease)


class RepositoryRecoveryTests(WritingArtifactTestCase):
    def prepared_transaction(self) -> tuple[Path, dict[str, object]]:
        with (
            mock.patch.object(
                self.repository,
                "_complete_transaction",
                side_effect=OSError("leave prepared transaction"),
            ),
            self.assertRaisesRegex(OSError, "leave prepared transaction"),
        ):
            self.commit(core_updates={"global_outline.md": "agent outline"})
        state = self.root / "agent_states/orchestrator_state/writing_artifacts"
        transaction_path = next(state.joinpath("transactions").glob("*.json"))
        return transaction_path, json.loads(
            transaction_path.read_text(encoding="utf-8")
        )

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

    def test_replacement_before_completed_retirement_survives_and_blocks(self) -> None:
        move_to_retirement = SecureWorkspaceStorage._move_to_retirement
        state = self.root / "agent_states/orchestrator_state/writing_artifacts"
        replacement = b'{"newer_journal":true}\n'
        replaced = False

        def replace_before_retirement(
            source_directory: int,
            source: str,
            retirement_directory: int,
            *,
            suffix: str,
        ):
            nonlocal replaced
            if suffix == "completed" and not replaced:
                replaced = True
                os.unlink(source, dir_fd=source_directory)
                descriptor = os.open(
                    source,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=source_directory,
                )
                try:
                    os.write(descriptor, replacement)
                finally:
                    os.close(descriptor)
            return move_to_retirement(
                source_directory,
                source,
                retirement_directory,
                suffix=suffix,
            )

        with (
            mock.patch.object(
                SecureWorkspaceStorage,
                "_move_to_retirement",
                side_effect=replace_before_retirement,
            ),
            self.assertRaisesRegex(
                ArtifactEntrySecurityError,
                "refusing to retire changed",
            ),
        ):
            self.commit(core_updates={"global_outline.md": "agent outline"})

        self.assertTrue(replaced)
        transaction_path = next(state.joinpath("transactions").glob("*.json"))
        self.assertEqual(transaction_path.read_bytes(), replacement)
        with self.assertRaisesRegex(WorkspaceFormatError, "recovery is blocked"):
            WritingArtifactRepository(self.root).list_paths()
        self.assertEqual(transaction_path.read_bytes(), replacement)
        self.assertTrue(state.joinpath("recovery_blocked.json").is_file())

    def test_orphan_staging_is_ignored_but_unexpected_transaction_state_blocks(self) -> None:
        self.assertEqual(self.repository.list_paths(), ())
        state = self.root / "agent_states/orchestrator_state/writing_artifacts"
        orphan = state / "staging/orphan.tmp"
        orphan.write_bytes(b"unowned staging residue")

        self.assertEqual(WritingArtifactRepository(self.root).list_paths(), ())
        self.assertFalse(state.joinpath("recovery_blocked.json").exists())
        self.assertEqual(orphan.read_bytes(), b"unowned staging residue")

        unexpected = state / "transactions/unexpected.tmp"
        unexpected.write_bytes(b"authoritative ambiguity")
        with self.assertRaisesRegex(WorkspaceFormatError, "recovery is blocked"):
            WritingArtifactRepository(self.root).catalog()
        self.assertTrue(state.joinpath("recovery_blocked.json").is_file())
        self.assertEqual(orphan.read_bytes(), b"unowned staging residue")

    def test_all_retained_residue_stays_in_staging_and_cleanup_never_replaces(self) -> None:
        core_path = self.root / "core_documents/global_outline.md"
        core_path.write_bytes(b"prior core")
        state = self.root / "agent_states/orchestrator_state/writing_artifacts"
        staging = state / "staging"
        rename_noreplace = SecureWorkspaceStorage._rename_noreplace

        with (
            mock.patch.object(
                storage_module.os,
                "unlink",
                side_effect=AssertionError("storage invoked os.unlink"),
            ) as unlink,
            mock.patch.object(
                storage_module.os,
                "rename",
                side_effect=AssertionError("storage invoked replacing rename"),
            ) as replacing_rename,
        ):
            manuscript_path, _ = self.commit(
                core_updates={"global_outline.md": "current core"},
            )

            with self.repository._locked_repository():
                def stop_one_staged_publication(
                    source_directory: int,
                    source: str,
                    destination_directory: int,
                    destination: str,
                ) -> None:
                    if destination == "tmp-failure.md":
                        raise OSError("retain staged temporary")
                    rename_noreplace(
                        source_directory,
                        source,
                        destination_directory,
                        destination,
                    )

                with (
                    mock.patch.object(
                        storage_module,
                        "uuid4",
                        return_value=SimpleNamespace(hex="4" * 32),
                    ),
                    mock.patch.object(
                        SecureWorkspaceStorage,
                        "_rename_noreplace",
                        side_effect=stop_one_staged_publication,
                    ),
                    self.assertRaisesRegex(OSError, "retain staged temporary"),
                ):
                    self.repository._storage.publish_bytes(
                        "manuscript/part-02/tmp-failure.md",
                        b"retained temp",
                        expected_sha256=None,
                        expected_exists=False,
                        create_only=True,
                        staging_directory=(
                            "agent_states/orchestrator_state/writing_artifacts/staging"
                        ),
                    )

                with (
                    mock.patch.object(
                        storage_module,
                        "uuid4",
                        side_effect=(
                            SimpleNamespace(hex="5" * 32),
                            SimpleNamespace(hex="6" * 32),
                            SimpleNamespace(hex="7" * 32),
                        ),
                    ),
                    mock.patch.object(
                        self.repository._storage,
                        "_verify_root_identity",
                        side_effect=ArtifactRootSecurityError(
                            "post-publication verification failed"
                        ),
                    ),
                    self.assertRaisesRegex(
                        ArtifactRootSecurityError,
                        "post-publication verification failed",
                    ),
                ):
                    self.repository._storage.publish_bytes(
                        "core_documents/global_outline.md",
                        b"failed replacement",
                        expected_sha256=hashlib.sha256(b"current core").hexdigest(),
                        expected_exists=True,
                        create_only=False,
                        staging_directory=(
                            "agent_states/orchestrator_state/writing_artifacts/staging"
                        ),
                    )

            unlink.assert_not_called()
            replacing_rename.assert_not_called()

        self.assertEqual(core_path.read_bytes(), b"current core")
        suffixes = {path.suffix for path in staging.glob(".artifacts-*")}
        self.assertTrue({".tmp", ".held", ".retired", ".completed"} <= suffixes)
        residue = tuple(self.root.rglob(".artifacts-*"))
        self.assertTrue(residue)
        self.assertTrue(all(path.parent == staging for path in residue))
        self.assertEqual(
            WritingArtifactRepository(self.root).list_paths(),
            tuple(sorted(("core_documents/global_outline.md", manuscript_path))),
        )
        self.assertFalse(state.joinpath("recovery_blocked.json").exists())

    def test_repository_json_scalars_and_boolean_fields_are_exact(self) -> None:
        _, transaction = self.prepared_transaction()
        transaction_id = transaction["transaction_id"]
        assert isinstance(transaction_id, str)

        for invalid in (True, 1.0, "1", None, 2):
            with self.subTest(record="transaction", invalid=invalid):
                malformed = deepcopy(transaction)
                malformed["schema_version"] = invalid
                with self.assertRaises(WorkspaceFormatError):
                    self.repository._validate_transaction(
                        malformed,
                        expected_id=transaction_id,
                    )

            with self.subTest(record="commit", invalid=invalid):
                malformed_commit = deepcopy(transaction["commit"])
                assert isinstance(malformed_commit, dict)
                malformed_commit["schema_version"] = invalid
                with self.assertRaises(WorkspaceFormatError):
                    self.repository._validate_commit(
                        malformed_commit,
                        transaction_id=transaction_id,
                    )

        for field in ("create_only", "old_exists"):
            for invalid in (1, 0.0, "false", None):
                with self.subTest(field=field, invalid=invalid):
                    malformed = deepcopy(transaction)
                    targets = malformed["targets"]
                    assert isinstance(targets, list)
                    targets[0][field] = invalid
                    with self.assertRaisesRegex(
                        WorkspaceFormatError,
                        "boolean field",
                    ):
                        self.repository._validate_transaction(
                            malformed,
                            expected_id=transaction_id,
                        )

    def test_transaction_targets_commit_and_catalog_must_match_exactly(self) -> None:
        _, transaction = self.prepared_transaction()
        transaction_id = transaction["transaction_id"]
        assert isinstance(transaction_id, str)
        cases: list[tuple[str, dict[str, object]]] = []

        duplicate_target = deepcopy(transaction)
        duplicate_targets = duplicate_target["targets"]
        assert isinstance(duplicate_targets, list)
        duplicate_targets.append(deepcopy(duplicate_targets[0]))
        cases.append(("duplicate target", duplicate_target))

        missing_commit = deepcopy(transaction)
        missing_commit_record = missing_commit["commit"]
        assert isinstance(missing_commit_record, dict)
        missing_commit_artifacts = missing_commit_record["artifacts"]
        assert isinstance(missing_commit_artifacts, list)
        missing_commit_artifacts.pop()
        cases.append(("missing commit artifact", missing_commit))

        mismatched_catalog = deepcopy(transaction)
        catalog_after = mismatched_catalog["catalog_after"]
        assert isinstance(catalog_after, dict)
        catalog_entries = catalog_after["entries"]
        assert isinstance(catalog_entries, list)
        catalog_entries[0]["sha256"] = "f" * 64
        cases.append(("catalog metadata", mismatched_catalog))

        extra_catalog = deepcopy(transaction)
        extra_catalog_after = extra_catalog["catalog_after"]
        assert isinstance(extra_catalog_after, dict)
        extra_entries = extra_catalog_after["entries"]
        assert isinstance(extra_entries, list)
        extra_entry = deepcopy(
            next(
                entry
                for entry in extra_entries
                if entry["kind"] == ArtifactKind.MANUSCRIPT.value
            )
        )
        extra_entry["path"] = "manuscript/extra/extra.md"
        extra_entries.append(extra_entry)
        cases.append(("extra current-commit catalog entry", extra_catalog))

        with mock.patch.object(
            self.repository._storage,
            "read_bytes",
            side_effect=AssertionError("validation consulted provenance I/O"),
        ) as read_bytes:
            for label, malformed in cases:
                with self.subTest(label=label):
                    with self.assertRaises(WorkspaceFormatError):
                        self.repository._validate_transaction(
                            malformed,
                            expected_id=transaction_id,
                        )
            read_bytes.assert_not_called()


if __name__ == "__main__":
    unittest.main()
