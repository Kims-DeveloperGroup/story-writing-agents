"""Canonical writing-artifact grammar, provenance, and storage tests."""

from __future__ import annotations

import hashlib
import json
import os
import errno
import base64
import socket
import stat
import tempfile
import threading
import unittest
from copy import deepcopy
from datetime import UTC, datetime
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
    MAX_ARTIFACT_PATH_BYTES,
    MAX_VIEWER_UTF8_BYTES,
    ArtifactCatalog,
    ArtifactCatalogEntry,
    ArtifactKind,
    ArtifactLineRange,
    ArtifactReference,
    canonical_artifact_kind,
    manuscript_artifact_path,
)
from story_writing_agents.writing_artifacts import _storage as storage_module
from story_writing_agents.writing_artifacts import (
    filesystem_sources as filesystem_sources_module,
)
from story_writing_agents.writing_artifacts import repository as repository_module
from story_writing_agents.writing_artifacts._storage import (
    ArtifactEntrySecurityError,
    ArtifactLeaseSecurityError,
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
        with self.assertRaisesRegex(ValueError, "too long"):
            canonical_artifact_kind("a" * (MAX_ARTIFACT_PATH_BYTES + 1))

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
            "part-02\u00a0/chapter.md",
            "part-02\u2003/chapter.md",
            "part-02\u3000/chapter.md",
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
    def test_metadata_only_discovery_lists_every_safe_canonical_file(self) -> None:
        safe = {
            **{
                path: f"manual::{path}".encode()
                for path in CORE_ARTIFACT_PATHS
            },
            "manuscript/part-01/manual.md": b"manual",
            "manuscript/part-01/empty.md": b"",
            "manuscript/part-01/init.md": b"initialized placeholder",
            "manuscript/part-01/pre-provenance.md": b"pre-provenance",
            "manuscript/Part_A/scenes/opening.v2.md": b"nested",
            "manuscript/part-01/invalid-utf8.md": b"\xff",
        }
        for path, content in safe.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

        excluded = (
            "core_documents/extra.md",
            "discussion_logs/private.md",
            "agent_states/private.md",
            "manuscript/root.md",
            "manuscript/part-01/not-markdown.txt",
            "manuscript/part-01/.gitkeep",
            "manuscript/bad part/chapter.md",
        )
        for path in excluded:
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"excluded")

        alias = self.root / "manuscript/part-01/alias.md"
        alias.write_bytes(b"alias")
        alias.with_name("ａｌｉａｓ.md").write_bytes(b"normalized alias")

        hardlink = self.root / "manuscript/part-01/hardlink.md"
        hardlink.write_bytes(b"hardlink")
        os.link(hardlink, hardlink.with_name("hardlink-copy.md"))

        if os.name == "posix":
            outside = tempfile.TemporaryDirectory()
            self.addCleanup(outside.cleanup)
            outside_target = Path(outside.name) / "outside.md"
            outside_target.write_bytes(b"outside workspace")
            symlink = self.root / "manuscript/part-01/symlink.md"
            symlink.symlink_to(outside_target)
            fifo = self.root / "manuscript/part-01/fifo.md"
            os.mkfifo(fifo)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(listener.close)
            listener.bind(str(self.root / "manuscript/part-01/socket.md"))

        with mock.patch.object(
            storage_module.os,
            "read",
            side_effect=AssertionError("source listing decoded file content"),
        ):
            self.assertEqual(
                self.repository.list_paths(),
                tuple(sorted(safe)),
            )
        bounded = self.repository._storage.list_regular_files(
            "manuscript",
            max_path_bytes=len("manuscript/part-01/manual.md") - 1,
        )
        self.assertNotIn(
            "manuscript/part-01/manual.md",
            tuple(path for path, _identity in bounded),
        )

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

    def test_source_reads_ignore_drift_and_missing_catalog_provenance_but_publication_is_strict(
        self,
    ) -> None:
        manuscript_path, _ = self.commit()
        (self.root / manuscript_path).write_text("manual drift", encoding="utf-8")
        self.assertEqual(self.repository.list_paths(), (manuscript_path,))
        self.assertEqual(self.repository.read_text(manuscript_path), "manual drift")

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

        with (
            mock.patch.object(
                other,
                "_load_catalog",
                side_effect=AssertionError("source operation loaded catalog"),
            ) as load_catalog,
            mock.patch.object(
                other,
                "_recover",
                side_effect=AssertionError("source operation ran recovery"),
            ) as recover,
        ):
            self.assertEqual(other.list_paths(), (manuscript_path,))
            self.assertEqual(other.read_text(manuscript_path), "manual drift")
        load_catalog.assert_not_called()
        recover.assert_not_called()

        with self.assertRaisesRegex(
            WorkspaceFormatError,
            "path set does not match commit ledger",
        ):
            other.catalog()
        with self.assertRaisesRegex(
            WorkspaceFormatError,
            "path set does not match commit ledger",
        ):
            other.commit_consensus(
                output_path="part-02/blocked.md",
                candidate="blocked",
                core_updates={},
                session_id="session_02",
                debate_id="strict_publication",
                revision_id="r0",
                candidate_author_agent_id=EXECUTION_SPECIALIST,
            )

    def test_source_adapter_delegates_full_content_validation_to_snapshot(
        self,
    ) -> None:
        manuscript_path = "manuscript/part-01/manual.md"
        content = b"\xffbefore\x00after"
        target = self.root / manuscript_path
        target.parent.mkdir(parents=True)
        target.write_bytes(content)
        sentinel = object()

        with mock.patch.object(
            filesystem_sources_module,
            "ArtifactContentSnapshot",
            return_value=sentinel,
        ) as snapshot_type:
            self.assertEqual(
                self.repository.read_current((manuscript_path,)),
                (sentinel,),
            )

        snapshot_type.assert_called_once()
        source, observed_content = snapshot_type.call_args.args
        self.assertEqual(source.path, manuscript_path)
        self.assertEqual(source.source_utf8_bytes, len(content))
        self.assertEqual(observed_content, content)

    def test_read_enforces_viewer_size_strict_utf8_and_nul_on_current_bytes(
        self,
    ) -> None:
        manuscript_path = "manuscript/part-01/manual.md"
        target = self.root / manuscript_path
        target.parent.mkdir(parents=True)
        target.write_text("é", encoding="utf-8")
        with self.assertRaisesRegex(WorkspaceFormatError, "permitted size"):
            self.repository.read_text(manuscript_path, max_bytes=1)
        with mock.patch.object(
            self.repository._storage,
            "read_snapshot",
            side_effect=AssertionError("invalid limit reached storage"),
        ) as read_snapshot:
            for invalid_limit in (0, -1, True, "8"):
                with self.subTest(max_bytes=invalid_limit):
                    with self.assertRaisesRegex(
                        ValueError,
                        "positive integer",
                    ):
                        self.repository.read_text(
                            manuscript_path,
                            max_bytes=invalid_limit,
                        )
        read_snapshot.assert_not_called()

        target.write_bytes(b"x" * MAX_VIEWER_UTF8_BYTES)
        self.assertEqual(
            len(self.repository.read_text(manuscript_path)),
            MAX_VIEWER_UTF8_BYTES,
        )
        self.assertEqual(
            len(
                self.repository.read_text(
                    manuscript_path,
                    max_bytes=MAX_VIEWER_UTF8_BYTES * 2,
                )
            ),
            MAX_VIEWER_UTF8_BYTES,
        )
        current = self.repository.read_current((manuscript_path,))
        self.assertEqual(len(current[0].content), MAX_VIEWER_UTF8_BYTES)
        del current

        with target.open("ab") as stream:
            stream.write(b"x")
        with self.assertRaisesRegex(WorkspaceFormatError, "permitted size"):
            self.repository.read_text(manuscript_path)
        with self.assertRaisesRegex(WorkspaceFormatError, "permitted size"):
            self.repository.read_text(
                manuscript_path,
                max_bytes=MAX_VIEWER_UTF8_BYTES * 2,
            )
        with self.assertRaisesRegex(WorkspaceFormatError, "permitted size"):
            self.repository.read_current((manuscript_path,))

        target.write_bytes(b"\xff")
        with self.assertRaisesRegex(WorkspaceFormatError, "valid UTF-8"):
            self.repository.read_text(manuscript_path)
        target.write_bytes(b"before\x00after")
        with self.assertRaisesRegex(WorkspaceFormatError, "contains NUL"):
            self.repository.read_text(manuscript_path)

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

    def test_deleted_cataloged_manuscript_identity_cannot_be_reused(self) -> None:
        manuscript_path, _ = self.commit()
        target = self.root / manuscript_path
        target.unlink()
        state = self.root / "agent_states/orchestrator_state/writing_artifacts"
        commits_before = tuple(state.joinpath("commits").iterdir())
        catalog_before = state.joinpath("catalog.json").read_bytes()

        with self.assertRaisesRegex(ManuscriptExistsError, "cataloged path"):
            self.commit(candidate="replacement bytes")

        self.assertFalse(target.exists())
        self.assertEqual(tuple(state.joinpath("commits").iterdir()), commits_before)
        self.assertEqual(state.joinpath("catalog.json").read_bytes(), catalog_before)

    def test_repository_rejects_case_and_normalization_aliases_without_mutation(
        self,
    ) -> None:
        real_listdir = storage_module.os.listdir
        cases = (
            ("Core_Documents", {"global_outline.md": "outline"}),
            ("ｍａｎｕｓｃｒｉｐｔ", {}),
        )
        state = self.root / "agent_states/orchestrator_state/writing_artifacts"
        root_identity = (self.root.stat().st_dev, self.root.stat().st_ino)
        for alias, updates in cases:
            with self.subTest(alias=alias):
                def aliased_listdir(directory):
                    names = list(real_listdir(directory))
                    metadata = os.fstat(directory)
                    if (metadata.st_dev, metadata.st_ino) == root_identity:
                        names.append(alias)
                    return names

                with (
                    mock.patch.object(
                        SecureWorkspaceStorage,
                        "_require_primitives",
                    ),
                    mock.patch.object(
                        storage_module.os,
                        "listdir",
                        side_effect=aliased_listdir,
                    ),
                    self.assertRaises(
                        (ArtifactRootSecurityError, ArtifactEntrySecurityError)
                    ),
                ):
                    self.commit(
                        output_path=f"alias-{len(updates)}/chapter.md",
                        core_updates=updates,
                    )

                self.assertFalse(state.joinpath("catalog.json").exists())
                self.assertFalse(tuple(state.joinpath("transactions").glob("*.json")))
                self.assertFalse(self.root.joinpath(f"manuscript/alias-{len(updates)}/chapter.md").exists())

        manuscript_path, _ = self.commit(
            output_path="read-alias/chapter.md",
            core_updates={"global_outline.md": "outline"},
        )
        for alias, path in (
            ("Core_Documents", "core_documents/global_outline.md"),
            ("ｍａｎｕｓｃｒｉｐｔ", manuscript_path),
        ):
            with self.subTest(read_alias=alias):
                before = (self.root / path).read_bytes()

                def aliased_listdir(directory):
                    names = list(real_listdir(directory))
                    metadata = os.fstat(directory)
                    if (metadata.st_dev, metadata.st_ino) == root_identity:
                        names.append(alias)
                    return names

                with (
                    mock.patch.object(
                        SecureWorkspaceStorage,
                        "_require_primitives",
                    ),
                    mock.patch.object(
                        storage_module.os,
                        "listdir",
                        side_effect=aliased_listdir,
                    ),
                    self.assertRaises(
                        (ArtifactRootSecurityError, ArtifactEntrySecurityError)
                    ),
                ):
                    self.repository.read_text(path)
                self.assertEqual((self.root / path).read_bytes(), before)

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

        read_snapshot = self.repository._storage.read_snapshot
        observed_artifacts: list[str] = []

        def record(relative: str, *args, **kwargs):
            if canonical_artifact_kind(relative) is not None:
                observed_artifacts.append(relative)
            return read_snapshot(relative, *args, **kwargs)

        requested = ("core_documents/global_outline.md",)
        with mock.patch.object(
            self.repository._storage,
            "read_snapshot",
            side_effect=record,
        ):
            snapshots = self.repository.read_current(requested)

        self.assertEqual(tuple(snapshot.path for snapshot in snapshots), requested)
        self.assertEqual(observed_artifacts, list(requested))
        self.assertEqual(snapshots[0].read_text(), "verified outline")
        original_source = snapshots[0].source
        replacement = self.root / "core_documents/replacement.md"
        replacement.write_text("verified outline", encoding="utf-8")
        os.replace(replacement, self.root / requested[0])
        replaced = self.repository.read_current(requested)[0]
        self.assertEqual(replaced.content, snapshots[0].content)
        self.assertNotEqual(replaced.source, original_source)

    def test_source_listing_is_content_free_while_catalog_remains_strict(self) -> None:
        manuscript_path, _ = self.commit()
        read_bytes = self.repository._storage.read_bytes

        with mock.patch.object(
            self.repository._storage,
            "read_bytes",
            side_effect=AssertionError("source listing read content"),
        ):
            self.assertEqual(self.repository.list_paths(), (manuscript_path,))

        def fail_entry(relative: str, *args, **kwargs):
            if relative == manuscript_path:
                raise ArtifactEntrySecurityError("entry changed")
            return read_bytes(relative, *args, **kwargs)

        with mock.patch.object(
            self.repository._storage,
            "read_bytes",
            side_effect=fail_entry,
        ):
            self.assertEqual(tuple(self.repository.catalog().entries), ())

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
    def test_relative_repository_root_is_lexical_and_survives_cwd_change(self) -> None:
        initial = self.root / "initial"
        later = self.root / "later"
        workspace_root = initial / "workspace"
        workspace_root.mkdir(parents=True)
        later.mkdir()
        original_cwd = Path.cwd()
        try:
            os.chdir(initial)
            lexical_root = Path(os.path.abspath("workspace"))
            with (
                mock.patch.object(
                    Path,
                    "resolve",
                    side_effect=AssertionError("constructor resolved root"),
                ) as resolve,
                mock.patch.object(
                    storage_module.os,
                    "open",
                    side_effect=AssertionError("constructor opened filesystem"),
                ) as open_file,
                mock.patch.object(
                    storage_module.os,
                    "stat",
                    side_effect=AssertionError("constructor stated filesystem"),
                ) as stat_file,
                mock.patch.object(
                    storage_module.os,
                    "listdir",
                    side_effect=AssertionError("constructor listed filesystem"),
                ) as list_directory,
                mock.patch.object(
                    SecureWorkspaceStorage,
                    "_require_primitives",
                    side_effect=AssertionError("constructor probed primitives"),
                ) as primitives,
            ):
                repository = WritingArtifactRepository("workspace")
            resolve.assert_not_called()
            open_file.assert_not_called()
            stat_file.assert_not_called()
            list_directory.assert_not_called()
            primitives.assert_not_called()
            self.assertEqual(repository._storage._root, lexical_root)
            self.assertTrue(repository._storage._root.is_absolute())

            os.chdir(later)
            self.assertEqual(repository.list_paths(), ())
            self.assertTrue(
                workspace_root.joinpath(
                    "agent_states/orchestrator_state/writing_artifacts"
                ).is_dir()
            )
            self.assertFalse(later.joinpath("workspace").exists())
        finally:
            os.chdir(original_cwd)

    def test_relative_repository_root_preserves_symlink_spelling(self) -> None:
        initial = self.root / "symlink-root"
        actual = initial / "actual"
        alias = initial / "alias"
        later = self.root / "symlink-later"
        actual.mkdir(parents=True)
        alias.symlink_to(actual, target_is_directory=True)
        later.mkdir()
        original_cwd = Path.cwd()
        try:
            os.chdir(initial)
            lexical_alias = Path(os.path.abspath("alias"))
            repository = WritingArtifactRepository("alias")
            self.assertEqual(repository._storage._root, lexical_alias)
            self.assertNotEqual(repository._storage._root, actual)
            os.chdir(later)
            with self.assertRaisesRegex(
                ArtifactRootSecurityError,
                "workspace root",
            ):
                repository.list_paths()
            self.assertFalse(actual.joinpath("agent_states").exists())
        finally:
            os.chdir(original_cwd)

    def test_open_directory_closes_child_once_for_every_post_open_failure(
        self,
    ) -> None:
        self.root.joinpath("existing").mkdir()
        for failure in (
            "fstat",
            "type",
            "identity",
            "exact name",
            "chain",
        ):
            with self.subTest(failure=failure):
                storage = SecureWorkspaceStorage(self.root)
                real_open = storage_module.os.open
                real_close = storage_module.os.close
                real_fstat = storage_module.os.fstat
                real_require = SecureWorkspaceStorage._require_exact_name
                real_chain = SecureWorkspaceStorage._verify_directory_chain
                child_descriptor = -1
                closes = 0
                exact_name_checks = 0

                def record_open(path, *args, **kwargs):
                    nonlocal child_descriptor
                    descriptor = real_open(path, *args, **kwargs)
                    if path == "existing":
                        child_descriptor = descriptor
                    return descriptor

                def record_close(descriptor):
                    nonlocal closes
                    if descriptor == child_descriptor:
                        closes += 1
                    return real_close(descriptor)

                def fail_fstat(descriptor):
                    metadata = real_fstat(descriptor)
                    if descriptor != child_descriptor:
                        return metadata
                    if failure == "fstat":
                        raise OSError("post-open fstat failure")
                    if failure == "type":
                        return SimpleNamespace(
                            st_mode=stat.S_IFREG | 0o600,
                            st_dev=metadata.st_dev,
                            st_ino=metadata.st_ino,
                        )
                    if failure == "identity":
                        return SimpleNamespace(
                            st_mode=metadata.st_mode,
                            st_dev=metadata.st_dev,
                            st_ino=metadata.st_ino + 1,
                        )
                    return metadata

                def fail_exact_name(
                    directory,
                    name,
                    *,
                    directory_entry,
                    missing_ok=False,
                ):
                    nonlocal exact_name_checks
                    if name == "existing":
                        exact_name_checks += 1
                        if failure == "exact name" and exact_name_checks == 2:
                            raise ArtifactRootSecurityError(
                                "post-open spelling failure"
                            )
                    return real_require(
                        directory,
                        name,
                        directory_entry=directory_entry,
                        missing_ok=missing_ok,
                    )

                def fail_chain(chain):
                    if failure == "chain" and chain:
                        raise ArtifactRootSecurityError(
                            "post-open chain failure"
                        )
                    return real_chain(chain)

                with (
                    mock.patch.object(SecureWorkspaceStorage, "_require_primitives"),
                    mock.patch.object(storage_module.os, "open", side_effect=record_open),
                    mock.patch.object(storage_module.os, "close", side_effect=record_close),
                    mock.patch.object(storage_module.os, "fstat", side_effect=fail_fstat),
                    mock.patch.object(
                        SecureWorkspaceStorage,
                        "_require_exact_name",
                        side_effect=fail_exact_name,
                    ),
                    mock.patch.object(
                        SecureWorkspaceStorage,
                        "_verify_directory_chain",
                        side_effect=fail_chain,
                    ),
                    self.assertRaises((OSError, WorkspaceSecurityError)),
                ):
                    with storage._open_root() as root_descriptor:
                        with storage._open_directory(
                            root_descriptor,
                            ("existing",),
                            create=False,
                        ):
                            self.fail("post-open failure entered directory body")

                self.assertGreaterEqual(child_descriptor, 0)
                self.assertEqual(closes, 1)

    def test_file_inspection_and_recursive_walk_close_descriptors_on_races(
        self,
    ) -> None:
        parent = self.root / "manuscript/part-01"
        nested = parent / "nested"
        nested.mkdir(parents=True)
        inspected = parent / "inspected.md"
        inspected.write_bytes(b"inspected")
        nested.joinpath("hidden.md").write_bytes(b"hidden")
        storage = SecureWorkspaceStorage(self.root)

        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, directory)
        real_open = storage_module.os.open
        real_close = storage_module.os.close
        real_require = SecureWorkspaceStorage._require_exact_name
        inspected_descriptor = -1
        inspected_closes = 0
        exact_checks = 0

        def record_inspected_open(path, *args, **kwargs):
            nonlocal inspected_descriptor
            descriptor = real_open(path, *args, **kwargs)
            if path == inspected.name:
                inspected_descriptor = descriptor
            return descriptor

        def record_inspected_close(descriptor):
            nonlocal inspected_closes
            if descriptor == inspected_descriptor:
                inspected_closes += 1
            return real_close(descriptor)

        def replace_after_open(
            directory_descriptor,
            name,
            *,
            directory_entry,
            missing_ok=False,
        ):
            nonlocal exact_checks
            if name == inspected.name:
                exact_checks += 1
                if exact_checks == 2:
                    raise ArtifactEntrySecurityError(
                        "injected post-open replacement"
                    )
            return real_require(
                directory_descriptor,
                name,
                directory_entry=directory_entry,
                missing_ok=missing_ok,
            )

        with (
            mock.patch.object(SecureWorkspaceStorage, "_require_primitives"),
            mock.patch.object(
                storage_module.os,
                "open",
                side_effect=record_inspected_open,
            ),
            mock.patch.object(
                storage_module.os,
                "close",
                side_effect=record_inspected_close,
            ),
            mock.patch.object(
                SecureWorkspaceStorage,
                "_require_exact_name",
                side_effect=replace_after_open,
            ),
            self.assertRaisesRegex(
                ArtifactEntrySecurityError,
                "post-open replacement",
            ),
        ):
            SecureWorkspaceStorage._inspect_file(
                directory,
                inspected.name,
            )
        self.assertGreaterEqual(inspected_descriptor, 0)
        self.assertEqual(inspected_closes, 1)

        real_verify = SecureWorkspaceStorage._verify_directory_chain
        nested_descriptor = -1
        nested_closes = 0

        def record_nested_open(path, *args, **kwargs):
            nonlocal nested_descriptor
            descriptor = real_open(path, *args, **kwargs)
            if path == nested.name:
                nested_descriptor = descriptor
            return descriptor

        def record_nested_close(descriptor):
            nonlocal nested_closes
            if descriptor == nested_descriptor:
                nested_closes += 1
            return real_close(descriptor)

        def reject_detached_nested(chain):
            if chain and chain[-1][1] == nested.name:
                raise ArtifactRootSecurityError("injected detached directory")
            return real_verify(chain)

        with (
            mock.patch.object(SecureWorkspaceStorage, "_require_primitives"),
            mock.patch.object(
                storage_module.os,
                "open",
                side_effect=record_nested_open,
            ),
            mock.patch.object(
                storage_module.os,
                "close",
                side_effect=record_nested_close,
            ),
            mock.patch.object(
                SecureWorkspaceStorage,
                "_verify_directory_chain",
                side_effect=reject_detached_nested,
            ),
        ):
            listed = storage.list_regular_files(
                "manuscript",
                max_path_bytes=MAX_ARTIFACT_PATH_BYTES,
            )
        self.assertGreaterEqual(nested_descriptor, 0)
        self.assertEqual(nested_closes, 1)
        self.assertNotIn(
            "manuscript/part-01/nested/hidden.md",
            tuple(path for path, _identity in listed),
        )

    def test_recursive_listing_safely_omits_a_file_deleted_during_inspection(
        self,
    ) -> None:
        target = self.root / "manuscript/part-01/race.md"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"race")
        inspect_file = SecureWorkspaceStorage._inspect_file

        def delete_before_inspection(directory, name):
            if name == target.name:
                os.unlink(name, dir_fd=directory)
                raise FileNotFoundError(name)
            return inspect_file(directory, name)

        with mock.patch.object(
            SecureWorkspaceStorage,
            "_inspect_file",
            side_effect=delete_before_inspection,
        ):
            self.assertEqual(self.repository.list_paths(), ())
        self.assertFalse(target.exists())

    def test_final_name_aliases_reject_before_stat_staging_or_mutation(self) -> None:
        parent = self.root / "manuscript/part-alias"
        parent.mkdir(parents=True)
        parent_identity = (parent.stat().st_dev, parent.stat().st_ino)
        real_listdir = storage_module.os.listdir
        real_stat = storage_module.os.stat

        for alias in ("Chapter.md", "ｃｈａｐｔｅｒ.md"):
            with self.subTest(alias=alias):
                def aliased_listdir(directory):
                    names = list(real_listdir(directory))
                    metadata = os.fstat(directory)
                    if (metadata.st_dev, metadata.st_ino) == parent_identity:
                        names.append(alias)
                    return names

                def reject_final_stat(path, *args, **kwargs):
                    if path == "chapter.md" and kwargs.get("dir_fd") is not None:
                        directory = os.fstat(kwargs["dir_fd"])
                        if (directory.st_dev, directory.st_ino) == parent_identity:
                            raise AssertionError("final alias reached stat")
                    return real_stat(path, *args, **kwargs)

                with (
                    mock.patch.object(
                        SecureWorkspaceStorage,
                        "_require_primitives",
                    ),
                    mock.patch.object(
                        storage_module.os,
                        "listdir",
                        side_effect=aliased_listdir,
                    ),
                    mock.patch.object(
                        storage_module.os,
                        "stat",
                        side_effect=reject_final_stat,
                    ),
                    mock.patch.object(
                        self.repository._storage,
                        "publish_bytes",
                        side_effect=AssertionError("alias reached staging"),
                    ) as publish,
                    self.assertRaisesRegex(
                        ArtifactEntrySecurityError,
                        "spelling is not exact",
                    ),
                ):
                    self.commit(output_path="part-alias/chapter.md")

                publish.assert_not_called()
                self.assertFalse(parent.joinpath("chapter.md").exists())

    def test_lock_aliases_fail_as_lease_errors_before_creation_or_flock(self) -> None:
        relative = "agent_states/orchestrator_state/writing_artifacts/.artifacts.lock"
        parent = self.root / "agent_states/orchestrator_state/writing_artifacts"
        storage = SecureWorkspaceStorage(self.root)
        storage.ensure_directory(parent.relative_to(self.root).as_posix())
        parent_identity = (parent.stat().st_dev, parent.stat().st_ino)
        real_listdir = storage_module.os.listdir
        real_open = storage_module.os.open
        real_flock = storage_module.fcntl.flock

        for alias in (
            ".Artifacts.lock",
            ".artifacts.lock.ａｎｃｈｏｒ",
            ".artifacts.lock.ＲＥＡＤＹ",
        ):
            with self.subTest(alias=alias):
                body_entered = False
                flock_targets: list[str] = []

                def aliased_listdir(directory):
                    names = list(real_listdir(directory))
                    metadata = os.fstat(directory)
                    if (metadata.st_dev, metadata.st_ino) == parent_identity:
                        names.append(alias)
                    return names

                def reject_lock_creation(path, *args, **kwargs):
                    if path == ".artifacts.lock":
                        raise AssertionError("alias reached lock creation")
                    return real_open(path, *args, **kwargs)

                def record_flock(descriptor, operation):
                    flock_targets.append(
                        "directory"
                        if stat.S_ISDIR(os.fstat(descriptor).st_mode)
                        else "primary"
                    )
                    return real_flock(descriptor, operation)

                with (
                    mock.patch.object(
                        SecureWorkspaceStorage,
                        "_require_primitives",
                    ),
                    mock.patch.object(
                        storage_module.os,
                        "listdir",
                        side_effect=aliased_listdir,
                    ),
                    mock.patch.object(
                        storage_module.os,
                        "open",
                        side_effect=reject_lock_creation,
                    ),
                    mock.patch.object(
                        storage_module.fcntl,
                        "flock",
                        side_effect=record_flock,
                    ),
                    self.assertRaisesRegex(
                        ArtifactLeaseSecurityError,
                        "spelling changed",
                    ),
                ):
                    with storage.exclusive_lock(relative):
                        body_entered = True

                self.assertFalse(body_entered)
                self.assertEqual(flock_targets, ["directory", "directory"])
                self.assertIsNone(storage._active_lease)
                self.assertFalse(parent.joinpath(".artifacts.lock").exists())
                self.assertFalse(parent.joinpath(".artifacts.lock.anchor").exists())
                self.assertFalse(parent.joinpath(".artifacts.lock.ready").exists())

    def test_new_lock_creation_order_and_existing_triple_open_flags_are_exact(self) -> None:
        relative = "agent_states/orchestrator_state/writing_artifacts/.artifacts.lock"
        parent = self.root / "agent_states/orchestrator_state/writing_artifacts"
        storage = SecureWorkspaceStorage(self.root)
        storage.ensure_directory(parent.relative_to(self.root).as_posix())
        parent_identity = (parent.stat().st_dev, parent.stat().st_ino)
        events: list[str] = []
        real_open = storage_module.os.open
        real_fsync = storage_module.os.fsync
        real_link = storage_module.os.link
        real_verify = SecureWorkspaceStorage._verify_lock_identity
        real_flock = storage_module.fcntl.flock

        def record_open(path, flags, *args, **kwargs):
            if path == ".artifacts.lock":
                self.assertTrue(flags & os.O_EXCL)
                self.assertTrue(flags & os.O_CREAT)
                events.append("open_exclusive")
            return real_open(path, flags, *args, **kwargs)

        def record_fsync(descriptor):
            metadata = os.fstat(descriptor)
            if stat.S_ISDIR(metadata.st_mode):
                if (metadata.st_dev, metadata.st_ino) == parent_identity:
                    events.append("fsync_parent")
            else:
                events.append("fsync_lock")
            return real_fsync(descriptor)

        def record_link(source, destination, *args, **kwargs):
            events.append(
                "link_anchor"
                if destination.endswith(".anchor")
                else "link_ready"
            )
            return real_link(source, destination, *args, **kwargs)

        def record_verify(*args, **kwargs):
            events.append("verify")
            return real_verify(*args, **kwargs)

        def record_flock(descriptor, operation):
            metadata = os.fstat(descriptor)
            if stat.S_ISDIR(metadata.st_mode):
                events.append(
                    "bootstrap_lock"
                    if operation == storage_module.fcntl.LOCK_EX
                    else "bootstrap_unlock"
                )
            elif operation == storage_module.fcntl.LOCK_EX:
                events.append("primary_flock")
            return real_flock(descriptor, operation)

        with (
            mock.patch.object(SecureWorkspaceStorage, "_require_primitives"),
            mock.patch.object(storage_module.os, "open", side_effect=record_open),
            mock.patch.object(storage_module.os, "fsync", side_effect=record_fsync),
            mock.patch.object(storage_module.os, "link", side_effect=record_link),
            mock.patch.object(
                SecureWorkspaceStorage,
                "_verify_lock_identity",
                side_effect=record_verify,
            ),
            mock.patch.object(storage_module.fcntl, "flock", side_effect=record_flock),
        ):
            with storage.exclusive_lock(relative):
                events.append("body")

        self.assertEqual(
            tuple(events[:14]),
            (
                "bootstrap_lock",
                "fsync_parent",
                "open_exclusive",
                "fsync_lock",
                "fsync_parent",
                "link_anchor",
                "fsync_parent",
                "link_ready",
                "fsync_parent",
                "verify",
                "primary_flock",
                "verify",
                "bootstrap_unlock",
                "body",
            ),
        )

        existing_flags: list[int] = []

        def record_existing_open(path, flags, *args, **kwargs):
            if path == ".artifacts.lock":
                existing_flags.append(flags)
            return real_open(path, flags, *args, **kwargs)

        with (
            mock.patch.object(SecureWorkspaceStorage, "_require_primitives"),
            mock.patch.object(
                storage_module.os,
                "open",
                side_effect=record_existing_open,
            ),
        ):
            with storage.exclusive_lock(relative):
                pass
        self.assertEqual(len(existing_flags), 1)
        self.assertFalse(existing_flags[0] & os.O_CREAT)
        self.assertFalse(existing_flags[0] & os.O_EXCL)

    def test_lock_creation_durability_failures_never_enter_and_retry_exactly(
        self,
    ) -> None:
        relative = "agent_states/orchestrator_state/writing_artifacts/.artifacts.lock"
        for failure in (
            "file fsync",
            "anchor flush",
            "ready link",
            "ready flush",
        ):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                Workspace(root).initialize()
                storage = SecureWorkspaceStorage(root)
                parent = root / "agent_states/orchestrator_state/writing_artifacts"
                storage.ensure_directory(parent.relative_to(root).as_posix())
                real_fsync = storage_module.os.fsync
                real_link = storage_module.os.link
                body_entered = False
                parent_fsyncs = 0

                def fail_selected_fsync(descriptor):
                    nonlocal parent_fsyncs
                    metadata = os.fstat(descriptor)
                    if stat.S_ISDIR(metadata.st_mode) and (
                        metadata.st_dev,
                        metadata.st_ino,
                    ) == (parent.stat().st_dev, parent.stat().st_ino):
                        parent_fsyncs += 1
                    selected = (failure == "file fsync" and not stat.S_ISDIR(metadata.st_mode)) or (
                        failure == "anchor flush" and parent_fsyncs == 3
                    ) or (
                        failure == "ready flush" and parent_fsyncs == 4
                    )
                    if selected:
                        raise OSError(failure)
                    return real_fsync(descriptor)

                def fail_link(*args, **kwargs):
                    if failure == "ready link" and args[1].endswith(".ready"):
                        raise OSError(failure)
                    return real_link(*args, **kwargs)

                with (
                    mock.patch.object(
                        SecureWorkspaceStorage,
                        "_require_primitives",
                    ),
                    mock.patch.object(
                        storage_module.os,
                        "fsync",
                        side_effect=fail_selected_fsync,
                    ),
                    mock.patch.object(
                        storage_module.os,
                        "link",
                        side_effect=fail_link,
                    ),
                    self.assertRaisesRegex(
                        ArtifactLeaseSecurityError,
                        "cannot securely acquire",
                    ),
                ):
                    with storage.exclusive_lock(relative):
                        body_entered = True

                self.assertFalse(body_entered)
                self.assertIsNone(storage._active_lease)
                lock_path = parent / ".artifacts.lock"
                anchor_path = parent / ".artifacts.lock.anchor"
                ready_path = parent / ".artifacts.lock.ready"
                if failure == "file fsync":
                    self.assertTrue(lock_path.is_file())
                    self.assertFalse(anchor_path.exists())
                    self.assertFalse(ready_path.exists())
                elif failure in {"anchor flush", "ready link"}:
                    self.assertTrue(lock_path.is_file())
                    self.assertTrue(anchor_path.is_file())
                    self.assertFalse(ready_path.exists())
                else:
                    self.assertTrue(lock_path.is_file())
                    self.assertTrue(anchor_path.is_file())
                    self.assertTrue(ready_path.is_file())
                if failure == "ready flush":
                    with storage.exclusive_lock(relative):
                        pass
                else:
                    with self.assertRaisesRegex(
                        ArtifactLeaseSecurityError,
                        "triple is incomplete",
                    ):
                        with storage.exclusive_lock(relative):
                            self.fail("incomplete lock triple entered protected body")

    def test_every_partial_lock_triple_rejects_without_reconstruction(self) -> None:
        relative = "agent_states/orchestrator_state/writing_artifacts/.artifacts.lock"
        names = (
            ".artifacts.lock",
            ".artifacts.lock.anchor",
            ".artifacts.lock.ready",
        )
        subsets = (
            (names[0],),
            (names[1],),
            (names[2],),
            (names[0], names[1]),
            (names[0], names[2]),
            (names[1], names[2]),
        )
        for present in subsets:
            with self.subTest(present=present), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                Workspace(root).initialize()
                parent = root / "agent_states/orchestrator_state/writing_artifacts"
                parent.mkdir(parents=True)
                first = parent / present[0]
                first.write_bytes(b"partial lock")
                for name in present[1:]:
                    os.link(first, parent / name)
                storage = SecureWorkspaceStorage(root)
                body_entered = False

                with self.assertRaisesRegex(
                    ArtifactLeaseSecurityError,
                    "triple is incomplete",
                ):
                    with storage.exclusive_lock(relative):
                        body_entered = True

                self.assertFalse(body_entered)
                self.assertEqual(
                    {name for name in names if parent.joinpath(name).exists()},
                    set(present),
                )
                self.assertEqual(first.stat().st_nlink, len(present))

    def test_complete_lock_triple_rejects_wrong_inode_and_link_count(self) -> None:
        relative = "agent_states/orchestrator_state/writing_artifacts/.artifacts.lock"
        for corruption in ("wrong ready inode", "extra hardlink"):
            with (
                self.subTest(corruption=corruption),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                Workspace(root).initialize()
                parent = root / "agent_states/orchestrator_state/writing_artifacts"
                parent.mkdir(parents=True)
                lock = parent / ".artifacts.lock"
                anchor = parent / ".artifacts.lock.anchor"
                ready = parent / ".artifacts.lock.ready"
                lock.write_bytes(b"lock")
                os.link(lock, anchor)
                if corruption == "wrong ready inode":
                    ready.write_bytes(b"wrong inode")
                else:
                    os.link(lock, ready)
                    os.link(lock, parent / ".extra-lock-link")
                storage = SecureWorkspaceStorage(root)
                body_entered = False

                with self.assertRaisesRegex(
                    ArtifactLeaseSecurityError,
                    "stable linked regular file",
                ):
                    with storage.exclusive_lock(relative):
                        body_entered = True
                self.assertFalse(body_entered)
                self.assertIsNone(storage._active_lease)

    def test_bootstrap_flock_and_ancestry_durability_failures_prevent_body(
        self,
    ) -> None:
        relative = "agent_states/orchestrator_state/writing_artifacts/.artifacts.lock"
        for failure in ("bootstrap flock", "ancestry durability"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                Workspace(root).initialize()
                parent = root / "agent_states/orchestrator_state/writing_artifacts"
                storage = SecureWorkspaceStorage(root)
                storage.ensure_directory(parent.relative_to(root).as_posix())
                real_flock = storage_module.fcntl.flock
                body_entered = False

                def fail_bootstrap(descriptor, operation):
                    if (
                        failure == "bootstrap flock"
                        and operation == storage_module.fcntl.LOCK_EX
                        and stat.S_ISDIR(os.fstat(descriptor).st_mode)
                    ):
                        raise OSError(failure)
                    return real_flock(descriptor, operation)

                ancestry_effect = (
                    OSError(failure)
                    if failure == "ancestry durability"
                    else None
                )
                ancestry_patch = (
                    mock.patch.object(
                        SecureWorkspaceStorage,
                        "_fsync_directory_ancestry",
                        side_effect=ancestry_effect,
                    )
                    if ancestry_effect is not None
                    else mock.patch.object(
                        SecureWorkspaceStorage,
                        "_fsync_directory_ancestry",
                        wraps=SecureWorkspaceStorage._fsync_directory_ancestry,
                    )
                )
                with (
                    mock.patch.object(
                        storage_module.fcntl,
                        "flock",
                        side_effect=fail_bootstrap,
                    ),
                    ancestry_patch,
                    self.assertRaisesRegex(
                        ArtifactLeaseSecurityError,
                        "cannot securely acquire",
                    ),
                ):
                    with storage.exclusive_lock(relative):
                        body_entered = True

                self.assertFalse(body_entered)
                self.assertIsNone(storage._active_lease)
                self.assertFalse(parent.joinpath(".artifacts.lock").exists())
                self.assertFalse(parent.joinpath(".artifacts.lock.anchor").exists())
                self.assertFalse(parent.joinpath(".artifacts.lock.ready").exists())

    def test_two_simultaneous_first_lock_writers_serialize_without_deadlock(self) -> None:
        relative = "agent_states/orchestrator_state/writing_artifacts/.first.lock"
        first = SecureWorkspaceStorage(self.root)
        second = SecureWorkspaceStorage(self.root)
        start = threading.Barrier(3)
        first_body = threading.Event()
        release = threading.Event()
        results: list[str] = []
        results_lock = threading.Lock()

        def contender(label: str, storage: SecureWorkspaceStorage) -> None:
            start.wait(timeout=1)
            with storage.exclusive_lock(relative):
                with results_lock:
                    results.append(label)
                    position = len(results)
                if position == 1:
                    first_body.set()
                    release.wait(timeout=1)

        threads = (
            threading.Thread(target=contender, args=("first", first), daemon=True),
            threading.Thread(target=contender, args=("second", second), daemon=True),
        )
        for thread in threads:
            thread.start()
        start.wait(timeout=1)
        self.assertTrue(first_body.wait(timeout=1))
        with results_lock:
            self.assertEqual(len(results), 1)
        release.set()
        for thread in threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(results, ("first", "second"))

    def test_active_lease_detects_ready_name_replacement(self) -> None:
        relative = "agent_states/orchestrator_state/writing_artifacts/.active.lock"
        ready = self.root / f"{relative}.ready"
        storage = SecureWorkspaceStorage(self.root)
        body_entered = False

        with self.assertRaisesRegex(
            ArtifactLeaseSecurityError,
            "artifact lock",
        ):
            with storage.exclusive_lock(relative):
                body_entered = True
                ready.unlink()
                ready.write_bytes(b"replacement")
                storage._require_active_lease()

        self.assertTrue(body_entered)
        self.assertIsNone(storage._active_lease)

    def test_read_traversal_does_not_fsync_but_child_creation_fsyncs_parent_once(
        self,
    ) -> None:
        existing = self.root / "manuscript/part-08/scenes"
        existing.mkdir(parents=True)
        existing.joinpath("chapter.md").write_bytes(b"existing bytes")
        storage = SecureWorkspaceStorage(self.root)
        real_fsync = storage_module.os.fsync
        fsynced_directories: list[tuple[int, int]] = []

        def record(descriptor: int) -> None:
            metadata = os.fstat(descriptor)
            if stat.S_ISDIR(metadata.st_mode):
                fsynced_directories.append((metadata.st_dev, metadata.st_ino))
            real_fsync(descriptor)

        with mock.patch.object(storage_module.os, "fsync", side_effect=record):
            self.assertEqual(
                storage.read_bytes("manuscript/part-08/scenes/chapter.md"),
                b"existing bytes",
            )
            self.assertEqual(fsynced_directories, [])

            storage.ensure_directory("manuscript/part-08/scenes/revisions")

        self.assertEqual(
            fsynced_directories,
            [(existing.stat().st_dev, existing.stat().st_ino)],
        )

    def test_each_created_nested_directory_parent_is_fsynced(self) -> None:
        storage = SecureWorkspaceStorage(self.root)
        real_fsync = storage_module.os.fsync
        fsynced_directories: set[tuple[int, int]] = set()

        def record(descriptor: int) -> None:
            metadata = os.fstat(descriptor)
            if stat.S_ISDIR(metadata.st_mode):
                fsynced_directories.add((metadata.st_dev, metadata.st_ino))
            real_fsync(descriptor)

        with mock.patch.object(storage_module.os, "fsync", side_effect=record):
            storage.ensure_directory("manuscript/part-09/scenes/revisions")

        expected_parents = (
            self.root / "manuscript",
            self.root / "manuscript/part-09",
            self.root / "manuscript/part-09/scenes",
        )
        self.assertTrue(all(path.is_dir() for path in expected_parents))
        self.assertTrue(
            {
                (path.stat().st_dev, path.stat().st_ino)
                for path in expected_parents
            }.issubset(fsynced_directories)
        )

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
                    anchor: str,
                    ready: str,
                    descriptor: int,
                ) -> None:
                    nonlocal verification_calls
                    verification_calls += 1
                    if verification_calls == replacement_call:
                        replacement = lock_path.with_suffix(".replacement")
                        replacement.write_bytes(b"unsafe replacement")
                        lock_path.unlink()
                        replacement.rename(lock_path)
                    real_verify(directory, name, anchor, ready, descriptor)

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

    def test_replacing_any_triple_lock_name_after_primary_flock_is_rejected(self) -> None:
        relative_parent = "agent_states/orchestrator_state/writing_artifacts"
        real_verify = SecureWorkspaceStorage._verify_lock_identity

        for replaced_name in ("lock", "anchor", "ready"):
            with self.subTest(replaced_name=replaced_name):
                relative = f"{relative_parent}/.{replaced_name}-pair.lock"
                lock_path = self.root / relative
                anchor_path = lock_path.with_name(f"{lock_path.name}.anchor")
                ready_path = lock_path.with_name(f"{lock_path.name}.ready")
                body_entered = False
                calls = 0

                def replace_after_flock(
                    directory,
                    name,
                    anchor,
                    ready,
                    descriptor,
                ):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        selected = {
                            "lock": lock_path,
                            "anchor": anchor_path,
                            "ready": ready_path,
                        }[replaced_name]
                        selected.unlink()
                        selected.write_bytes(b"replacement")
                    real_verify(directory, name, anchor, ready, descriptor)

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
    @staticmethod
    def commit_repository(
        repository: WritingArtifactRepository,
        *,
        output_path: str = "part-01/chapter-01.md",
        core_updates: dict[str, str] | None = None,
    ) -> tuple[str, tuple[str, ...]]:
        return repository.commit_consensus(
            output_path=output_path,
            candidate="agent-authored manuscript\n",
            core_updates={} if core_updates is None else core_updates,
            session_id="session_01",
            debate_id="debate_01",
            revision_id="r0",
            candidate_author_agent_id=EXECUTION_SPECIALIST,
        )

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

    @staticmethod
    def rewrite_commit_timestamps(
        root: Path,
        repository: WritingArtifactRepository,
        timestamps_by_manuscript: dict[str, str],
    ) -> dict[str, dict[str, object]]:
        state = root / "agent_states/orchestrator_state/writing_artifacts"
        commits: dict[str, dict[str, object]] = {}
        for commit_path in state.joinpath("commits").glob("*.json"):
            commit = json.loads(commit_path.read_text(encoding="utf-8"))
            manuscript = next(
                artifact
                for artifact in commit["artifacts"]
                if artifact["kind"] == ArtifactKind.MANUSCRIPT.value
            )
            timestamp = timestamps_by_manuscript.get(manuscript["path"])
            if timestamp is not None:
                commit["committed_at"] = timestamp
            commits[commit["commit_id"]] = commit
            commit_path.write_bytes(repository._json_bytes(commit))

        catalog_path = state / "catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        for entry in catalog["entries"]:
            entry["committed_at"] = commits[entry["commit_id"]]["committed_at"]
        catalog_path.write_bytes(repository._json_bytes(catalog))
        return commits

    @staticmethod
    def point_catalog_core_to_commit(
        root: Path,
        repository: WritingArtifactRepository,
        commit: dict[str, object],
        path: str,
    ) -> None:
        state = root / "agent_states/orchestrator_state/writing_artifacts"
        catalog_path = state / "catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        artifact = next(
            item for item in commit["artifacts"] if item["path"] == path
        )
        entry = next(item for item in catalog["entries"] if item["path"] == path)
        entry.update(
            {
                "author_agent_id": artifact["author_agent_id"],
                "commit_id": commit["commit_id"],
                "committed_at": commit["committed_at"],
                "debate_id": commit["debate_id"],
                "kind": artifact["kind"],
                "revision_id": commit["revision_id"],
                "session_id": commit["session_id"],
                "sha256": artifact["sha256"],
            }
        )
        catalog_path.write_bytes(repository._json_bytes(catalog))

    @staticmethod
    def set_transaction_timestamp(
        transaction: dict[str, object],
        timestamp: str,
    ) -> None:
        commit = transaction["commit"]
        assert isinstance(commit, dict)
        commit["committed_at"] = timestamp
        catalog_after = transaction["catalog_after"]
        assert isinstance(catalog_after, dict)
        for entry in catalog_after["entries"]:
            if entry["commit_id"] == commit["commit_id"]:
                entry["committed_at"] = timestamp

    def test_commit_ledger_requires_canonical_json_without_rewrite(self) -> None:
        manuscript_path, _ = self.commit()
        state = self.root / "agent_states/orchestrator_state/writing_artifacts"
        commit_path = next(state.joinpath("commits").glob("*.json"))
        parsed = json.loads(commit_path.read_text(encoding="utf-8"))
        noncanonical = json.dumps(
            parsed,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertNotEqual(noncanonical, self.repository._json_bytes(parsed))
        commit_path.write_bytes(noncanonical)
        catalog_before = state.joinpath("catalog.json").read_bytes()

        for operation in (
            lambda: self.repository.catalog(),
            lambda: self.commit(output_path="part-02/new.md"),
        ):
            with self.assertRaisesRegex(
                WorkspaceFormatError,
                "ledger record is not canonical",
            ):
                operation()

        self.assertEqual(commit_path.read_bytes(), noncanonical)
        self.assertEqual(state.joinpath("catalog.json").read_bytes(), catalog_before)
        self.assertTrue(self.root.joinpath(manuscript_path).is_file())
        self.assertFalse(self.root.joinpath("manuscript/part-02/new.md").exists())
        self.assertFalse(tuple(state.joinpath("transactions").glob("*.json")))

    def test_repeated_core_history_selects_unique_greatest_timestamp_instant(
        self,
    ) -> None:
        first, _ = self.commit(
            output_path="part-01/first.md",
            core_updates={"global_outline.md": "older outline"},
        )
        second, _ = self.commit(
            output_path="part-02/second.md",
            core_updates={"global_outline.md": "newer outline"},
            debate_id="debate_02",
        )
        self.rewrite_commit_timestamps(
            self.root,
            self.repository,
            {
                first: "2026-01-01T01:00:00+01:00",
                second: "2025-12-31T20:00:01-04:00",
            },
        )

        self.assertEqual(
            self.repository.read_text("core_documents/global_outline.md"),
            "newer outline",
        )
        self.assertEqual(
            self.repository.list_paths(),
            (
                "core_documents/global_outline.md",
                first,
                second,
            ),
        )

    def test_repeated_core_history_rejects_stale_catalog_and_greatest_tie(
        self,
    ) -> None:
        for state_kind in ("stale", "tie"):
            with (
                self.subTest(state_kind=state_kind),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                Workspace(root).initialize()
                repository = WritingArtifactRepository(root)
                first, _ = self.commit_repository(
                    repository,
                    output_path="part-01/first.md",
                    core_updates={"global_outline.md": "older outline"},
                )
                second, _ = self.commit_repository(
                    repository,
                    output_path="part-02/second.md",
                    core_updates={"global_outline.md": "newer outline"},
                )
                commits = self.rewrite_commit_timestamps(
                    root,
                    repository,
                    {
                        first: "2026-01-01T00:00:00+00:00",
                        second: (
                            "2025-12-31T19:00:00-05:00"
                            if state_kind == "tie"
                            else "2026-01-01T00:00:01+00:00"
                        ),
                    },
                )
                if state_kind == "stale":
                    older = next(
                        commit
                        for commit in commits.values()
                        if any(
                            artifact["path"] == first
                            for artifact in commit["artifacts"]
                        )
                    )
                    self.point_catalog_core_to_commit(
                        root,
                        repository,
                        older,
                        "core_documents/global_outline.md",
                    )

                expected = (
                    "not the newest commit"
                    if state_kind == "stale"
                    else "no unique newest commit"
                )
                with self.assertRaisesRegex(WorkspaceFormatError, expected):
                    repository.catalog()

    def test_clock_regression_advances_beyond_entire_commit_ledger(self) -> None:
        first, _ = self.commit(
            output_path="part-01/first.md",
            core_updates={"global_outline.md": "outline"},
        )
        second, _ = self.commit(
            output_path="part-02/future-manuscript.md",
            debate_id="debate_02",
        )
        ledger_maximum = "2099-01-02T00:00:00+00:00"
        self.rewrite_commit_timestamps(
            self.root,
            self.repository,
            {
                first: "2099-01-01T00:00:00+00:00",
                second: ledger_maximum,
            },
        )
        regressed_clock = mock.Mock(wraps=datetime)
        regressed_clock.now.return_value = datetime(2000, 1, 1, tzinfo=UTC)

        with mock.patch.object(
            repository_module,
            "datetime",
            regressed_clock,
        ):
            third, _ = self.commit(
                output_path="part-03/after-regression.md",
                debate_id="debate_03",
            )

        state = self.root / "agent_states/orchestrator_state/writing_artifacts"
        third_commit = next(
            json.loads(path.read_text(encoding="utf-8"))
            for path in state.joinpath("commits").glob("*.json")
            if any(
                artifact["path"] == third
                for artifact in json.loads(path.read_text(encoding="utf-8"))[
                    "artifacts"
                ]
            )
        )
        self.assertGreater(
            datetime.fromisoformat(third_commit["committed_at"]),
            datetime.fromisoformat(ledger_maximum),
        )

    def test_maximum_ledger_timestamp_blocks_before_any_publication(self) -> None:
        first, _ = self.commit(output_path="part-01/first.md")
        maximum = datetime.max.replace(tzinfo=UTC).isoformat()
        self.rewrite_commit_timestamps(
            self.root,
            self.repository,
            {first: maximum},
        )
        state = self.root / "agent_states/orchestrator_state/writing_artifacts"
        catalog_before = state.joinpath("catalog.json").read_bytes()
        commits_before = {
            path.name: path.read_bytes()
            for path in state.joinpath("commits").glob("*.json")
        }

        with (
            mock.patch.object(
                self.repository._storage,
                "publish_bytes",
                side_effect=AssertionError("overflow reached publication"),
            ) as publish,
            self.assertRaisesRegex(
                WorkspaceFormatError,
                "timestamp cannot advance",
            ),
        ):
            self.commit(output_path="part-02/overflow.md")

        publish.assert_not_called()
        self.assertFalse(self.root.joinpath("manuscript/part-02/overflow.md").exists())
        self.assertFalse(tuple(state.joinpath("transactions").glob("*.json")))
        self.assertEqual(state.joinpath("catalog.json").read_bytes(), catalog_before)
        self.assertEqual(
            {
                path.name: path.read_bytes()
                for path in state.joinpath("commits").glob("*.json")
            },
            commits_before,
        )

    def test_empty_ledger_allows_first_commit_and_valid_history_reserves_paths(
        self,
    ) -> None:
        state = self.root / "agent_states/orchestrator_state/writing_artifacts"
        self.assertFalse(state.joinpath("catalog.json").exists())
        self.assertFalse(state.joinpath("commits").exists())

        first, _ = self.commit(
            output_path="part-01/first.md",
            core_updates={"global_outline.md": "outline one"},
        )
        second, _ = self.commit(
            output_path="part-02/second.md",
            candidate="second manuscript",
            core_updates={"global_outline.md": "outline two"},
            debate_id="debate_02",
        )

        self.assertEqual(
            self.repository.list_paths(),
            tuple(
                sorted(
                    (
                        "core_documents/global_outline.md",
                        first,
                        second,
                    )
                )
            ),
        )
        self.assertEqual(
            self.repository.read_text("core_documents/global_outline.md"),
            "outline two",
        )
        self.assertEqual(len(tuple(state.joinpath("commits").glob("*.json"))), 2)

    def test_omitted_catalog_row_does_not_block_sources_but_blocks_publication(
        self,
    ) -> None:
        manuscript_path, _ = self.commit(
            core_updates={"global_outline.md": "committed outline"},
        )
        state = self.root / "agent_states/orchestrator_state/writing_artifacts"
        catalog_path = state / "catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["entries"] = [
            entry
            for entry in catalog["entries"]
            if entry["path"] != "core_documents/global_outline.md"
        ]
        catalog_path.write_bytes(self.repository._json_bytes(catalog))

        self.assertEqual(
            self.repository.list_paths(),
            (
                "core_documents/global_outline.md",
                manuscript_path,
            ),
        )
        self.assertEqual(
            self.repository.read_text("core_documents/global_outline.md"),
            "committed outline",
        )
        for operation in (
            lambda: self.repository.catalog(),
            lambda: self.commit(output_path="part-02/new.md"),
        ):
            with self.assertRaisesRegex(
                WorkspaceFormatError,
                "path set does not match commit ledger",
            ):
                operation()
        self.assertTrue(self.root.joinpath(manuscript_path).is_file())
        self.assertFalse(self.root.joinpath("manuscript/part-02/new.md").exists())
        self.assertFalse(tuple(state.joinpath("transactions").glob("*.json")))

    def test_pending_first_core_introduction_contributes_no_before_paths(self) -> None:
        with (
            mock.patch.object(
                self.repository,
                "_complete_transaction",
                side_effect=OSError("prepared"),
            ),
            self.assertRaisesRegex(OSError, "prepared"),
        ):
            self.commit(core_updates={"global_outline.md": "first outline"})
        state = self.root / "agent_states/orchestrator_state/writing_artifacts"
        journal = next(state.joinpath("transactions").glob("*.json"))
        transaction = json.loads(journal.read_text(encoding="utf-8"))
        self.set_transaction_timestamp(
            transaction,
            "2099-01-01T00:00:00+00:00",
        )
        journal.write_bytes(self.repository._json_bytes(transaction))
        self.assertEqual(transaction["catalog_before"]["image"]["entries"], [])
        self.assertEqual(
            {entry["path"] for entry in transaction["catalog_after"]["entries"]},
            {
                "core_documents/global_outline.md",
                "manuscript/part-01/chapter-01.md",
            },
        )
        commit = transaction["commit"]
        state.joinpath("commits", f"{commit['commit_id']}.json").write_bytes(
            self.repository._json_bytes(commit)
        )

        recovered = WritingArtifactRepository(self.root)
        self.assertEqual(recovered.list_paths(), ())
        recovered.catalog()
        self.assertEqual(
            recovered.list_paths(),
            (
                "core_documents/global_outline.md",
                "manuscript/part-01/chapter-01.md",
            ),
        )

    def test_pending_core_update_requires_established_current_row(self) -> None:
        for remove_current in (False, True):
            with (
                self.subTest(remove_current=remove_current),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                Workspace(root).initialize()
                repository = WritingArtifactRepository(root)
                self.commit_repository(
                    repository,
                    output_path="part-01/first.md",
                    core_updates={"global_outline.md": "older outline"},
                )
                with (
                    mock.patch.object(
                        repository,
                        "_complete_transaction",
                        side_effect=OSError("prepared"),
                    ),
                    self.assertRaisesRegex(OSError, "prepared"),
                ):
                    self.commit_repository(
                        repository,
                        output_path="part-02/second.md",
                        core_updates={"global_outline.md": "new outline"},
                    )
                state = root / "agent_states/orchestrator_state/writing_artifacts"
                journal = next(state.joinpath("transactions").glob("*.json"))
                transaction = json.loads(journal.read_text(encoding="utf-8"))
                self.set_transaction_timestamp(
                    transaction,
                    "2099-01-01T00:00:00+00:00",
                )
                journal.write_bytes(repository._json_bytes(transaction))
                before_entries = transaction["catalog_before"]["image"]["entries"]
                self.assertIn(
                    "core_documents/global_outline.md",
                    {entry["path"] for entry in before_entries},
                )
                commit = transaction["commit"]
                state.joinpath("commits", f"{commit['commit_id']}.json").write_bytes(
                    repository._json_bytes(commit)
                )
                if remove_current:
                    catalog_path = state / "catalog.json"
                    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
                    catalog["entries"] = [
                        entry
                        for entry in catalog["entries"]
                        if entry["path"] != "core_documents/global_outline.md"
                    ]
                    catalog_path.write_bytes(repository._json_bytes(catalog))

                recovered = WritingArtifactRepository(root)
                if remove_current:
                    self.assertEqual(
                        recovered.read_text("core_documents/global_outline.md"),
                        "older outline",
                    )
                    self.assertEqual(
                        recovered.list_paths(),
                        (
                            "core_documents/global_outline.md",
                            "manuscript/part-01/first.md",
                        ),
                    )
                    with self.assertRaisesRegex(
                        WorkspaceFormatError,
                        "recovery is blocked",
                    ):
                        recovered.catalog()
                    self.assertFalse(root.joinpath("manuscript/part-02/second.md").exists())
                else:
                    self.assertEqual(
                        recovered.read_text("core_documents/global_outline.md"),
                        "older outline",
                    )
                    self.assertEqual(
                        recovered.list_paths(),
                        (
                            "core_documents/global_outline.md",
                            "manuscript/part-01/first.md",
                        ),
                    )
                    recovered.catalog()
                    self.assertEqual(
                        recovered.read_text("core_documents/global_outline.md"),
                        "new outline",
                    )
                    self.assertEqual(
                        recovered.list_paths(),
                        (
                            "core_documents/global_outline.md",
                            "manuscript/part-01/first.md",
                            "manuscript/part-02/second.md",
                        ),
                    )

    def test_pending_after_includes_newest_core_commit(self) -> None:
        self.commit(
            output_path="part-01/first.md",
            core_updates={"global_outline.md": "older outline"},
        )
        with (
            mock.patch.object(
                self.repository,
                "_complete_transaction",
                side_effect=OSError("prepared"),
            ),
            self.assertRaisesRegex(OSError, "prepared"),
        ):
            self.commit(
                output_path="part-02/second.md",
                core_updates={"global_outline.md": "newest outline"},
                debate_id="debate_02",
            )
        state = self.root / "agent_states/orchestrator_state/writing_artifacts"
        journal = next(state.joinpath("transactions").glob("*.json"))
        transaction = json.loads(journal.read_text(encoding="utf-8"))
        self.set_transaction_timestamp(
            transaction,
            "2099-01-01T00:00:00+00:00",
        )
        journal.write_bytes(self.repository._json_bytes(transaction))
        for target in transaction["targets"]:
            destination = self.root / target["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(base64.b64decode(target["new_content_base64"]))
        commit = transaction["commit"]
        state.joinpath("commits", f"{commit['commit_id']}.json").write_bytes(
            self.repository._json_bytes(commit)
        )
        state.joinpath("catalog.json").write_bytes(
            self.repository._json_bytes(transaction["catalog_after"])
        )

        recovered = WritingArtifactRepository(self.root)
        self.assertEqual(
            recovered.read_text("core_documents/global_outline.md"),
            "newest outline",
        )
        self.assertEqual(
            recovered.list_paths(),
            (
                "core_documents/global_outline.md",
                "manuscript/part-01/first.md",
                "manuscript/part-02/second.md",
            ),
        )
        self.assertTrue(journal.is_file())
        recovered.catalog()
        self.assertFalse(journal.exists())

    def test_missing_empty_or_subset_catalog_with_commits_blocks_before_mutation(
        self,
    ) -> None:
        for catalog_state in ("missing", "empty", "subset"):
            with (
                self.subTest(catalog_state=catalog_state),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                Workspace(root).initialize()
                repository = WritingArtifactRepository(root)
                first, _ = self.commit_repository(
                    repository,
                    output_path="part-01/first.md",
                )
                second, _ = self.commit_repository(
                    repository,
                    output_path="part-02/second.md",
                )
                state = root / "agent_states/orchestrator_state/writing_artifacts"
                catalog_path = state / "catalog.json"
                if catalog_state == "missing":
                    catalog_path.unlink()
                elif catalog_state == "empty":
                    catalog_path.write_bytes(
                        repository._json_bytes(ArtifactCatalog.empty().to_dict())
                    )
                else:
                    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
                    catalog["entries"] = [
                        entry
                        for entry in catalog["entries"]
                        if entry["path"] != second
                    ]
                    catalog_path.write_bytes(repository._json_bytes(catalog))

                with self.assertRaises(WorkspaceFormatError):
                    self.commit_repository(
                        repository,
                        output_path="part-03/new.md",
                    )
                self.assertFalse(root.joinpath("manuscript/part-03/new.md").exists())
                self.assertFalse(tuple(state.joinpath("transactions").glob("*.json")))
                self.assertTrue(root.joinpath(first).is_file())
                self.assertTrue(root.joinpath(second).is_file())

    def test_malformed_unexpected_mismatched_and_extra_commit_records_block(
        self,
    ) -> None:
        for corruption in ("malformed", "unexpected", "mismatched", "extra"):
            with (
                self.subTest(corruption=corruption),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                Workspace(root).initialize()
                repository = WritingArtifactRepository(root)
                self.commit_repository(repository)
                state = root / "agent_states/orchestrator_state/writing_artifacts"
                commit_path = next(state.joinpath("commits").glob("*.json"))
                commit = json.loads(commit_path.read_text(encoding="utf-8"))
                replacement_id = "f" * 32
                if commit["commit_id"] == replacement_id:
                    replacement_id = "e" * 32

                if corruption == "malformed":
                    commit_path.write_bytes(b"{not json")
                elif corruption == "unexpected":
                    state.joinpath("commits/unexpected.txt").write_bytes(b"unexpected")
                elif corruption == "mismatched":
                    commit_path.rename(state.joinpath("commits", f"{replacement_id}.json"))
                else:
                    commit["commit_id"] = replacement_id
                    for artifact in commit["artifacts"]:
                        if artifact["kind"] == ArtifactKind.MANUSCRIPT.value:
                            artifact["path"] = "manuscript/part-extra/extra.md"
                    state.joinpath("commits", f"{replacement_id}.json").write_bytes(
                        repository._json_bytes(commit)
                    )

                with self.assertRaises(WorkspaceFormatError):
                    repository.catalog()
                self.assertFalse(root.joinpath("manuscript/part-new/new.md").exists())

    def test_duplicate_historical_manuscript_reservation_blocks_path_reuse(self) -> None:
        manuscript_path, _ = self.commit()
        state = self.root / "agent_states/orchestrator_state/writing_artifacts"
        commit_path = next(state.joinpath("commits").glob("*.json"))
        duplicate = json.loads(commit_path.read_text(encoding="utf-8"))
        duplicate_id = "f" * 32
        if duplicate["commit_id"] == duplicate_id:
            duplicate_id = "e" * 32
        duplicate["commit_id"] = duplicate_id
        state.joinpath("commits", f"{duplicate_id}.json").write_bytes(
            self.repository._json_bytes(duplicate)
        )

        with self.assertRaisesRegex(
            WorkspaceFormatError,
            "reuses a manuscript path",
        ):
            self.commit(output_path="part-02/new.md")
        self.assertTrue(self.root.joinpath(manuscript_path).is_file())
        self.assertFalse(self.root.joinpath("manuscript/part-02/new.md").exists())
        self.assertFalse(tuple(state.joinpath("transactions").glob("*.json")))

    def test_exact_pending_commit_is_exempt_but_unrelated_commit_is_not(self) -> None:
        for unrelated in (False, True):
            with self.subTest(unrelated=unrelated), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                Workspace(root).initialize()
                repository = WritingArtifactRepository(root)
                with (
                    mock.patch.object(
                        repository,
                        "_complete_transaction",
                        side_effect=OSError("prepared"),
                    ),
                    self.assertRaisesRegex(OSError, "prepared"),
                ):
                    self.commit_repository(repository)
                state = root / "agent_states/orchestrator_state/writing_artifacts"
                journal = next(state.joinpath("transactions").glob("*.json"))
                transaction = json.loads(journal.read_text(encoding="utf-8"))
                commit = transaction["commit"]
                state.joinpath("commits", f"{commit['commit_id']}.json").write_bytes(
                    repository._json_bytes(commit)
                )
                if unrelated:
                    unrelated_commit = deepcopy(commit)
                    unrelated_id = "f" * 32
                    if unrelated_id == commit["commit_id"]:
                        unrelated_id = "e" * 32
                    unrelated_commit["commit_id"] = unrelated_id
                    for artifact in unrelated_commit["artifacts"]:
                        if artifact["kind"] == ArtifactKind.MANUSCRIPT.value:
                            artifact["path"] = "manuscript/unrelated/other.md"
                    state.joinpath("commits", f"{unrelated_id}.json").write_bytes(
                        repository._json_bytes(unrelated_commit)
                    )

                recovered = WritingArtifactRepository(root)
                self.assertEqual(recovered.list_paths(), ())
                if unrelated:
                    with self.assertRaisesRegex(
                        WorkspaceFormatError,
                        "recovery is blocked",
                    ):
                        recovered.catalog()
                    self.assertFalse(
                        root.joinpath("manuscript/part-01/chapter-01.md").exists()
                    )
                    self.assertTrue(state.joinpath("recovery_blocked.json").is_file())
                else:
                    recovered.catalog()
                    self.assertEqual(
                        recovered.list_paths(),
                        ("manuscript/part-01/chapter-01.md",),
                    )

    def test_catalog_after_requires_exact_commit_and_target_state(self) -> None:
        _, transaction = self.prepared_transaction()
        state = self.root / "agent_states/orchestrator_state/writing_artifacts"
        state.joinpath("catalog.json").write_bytes(
            self.repository._json_bytes(transaction["catalog_after"])
        )

        recovered = WritingArtifactRepository(self.root)
        self.assertEqual(recovered.list_paths(), ())
        with self.assertRaisesRegex(WorkspaceFormatError, "recovery is blocked"):
            recovered.catalog()
        self.assertFalse(
            self.root.joinpath("manuscript/part-01/chapter-01.md").exists()
        )
        self.assertTrue(state.joinpath("recovery_blocked.json").is_file())

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

    def test_prepared_journal_is_immutable_and_retired_with_exact_bytes(self) -> None:
        transaction_publications: list[tuple[str, bytes, dict[str, object]]] = []
        publish = self.repository._storage.publish_bytes

        def record(relative: str, content: bytes, **kwargs):
            if "/transactions/" in relative:
                transaction_publications.append((relative, content, kwargs))
            return publish(relative, content, **kwargs)

        with mock.patch.object(
            self.repository._storage,
            "publish_bytes",
            side_effect=record,
        ):
            self.commit(core_updates={"global_outline.md": "agent outline"})

        self.assertEqual(len(transaction_publications), 1)
        relative, prepared_bytes, arguments = transaction_publications[0]
        self.assertTrue(relative.endswith(".json"))
        self.assertIsNone(arguments["expected_sha256"])
        self.assertFalse(arguments["expected_exists"])
        self.assertTrue(arguments["create_only"])
        state = self.root / "agent_states/orchestrator_state/writing_artifacts"
        completed = tuple(state.joinpath("staging").glob("*.completed"))
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].read_bytes(), prepared_bytes)

    def test_each_publication_boundary_lists_safe_sources_then_rolls_forward_catalog(
        self,
    ) -> None:
        stages = (
            "after target 1",
            "after target 2",
            "after commit",
            "after catalog",
            "before retirement",
            "after retirement",
        )
        catalog_relative = (
            "agent_states/orchestrator_state/writing_artifacts/catalog.json"
        )
        for stage in stages:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                Workspace(root).initialize()
                repository = WritingArtifactRepository(root)
                publish = repository._storage.publish_bytes
                write_immutable = repository._write_immutable_json
                retire = repository._storage.unlink
                target_count = 0

                def interrupt_publish(relative: str, *args, **kwargs):
                    nonlocal target_count
                    result = publish(relative, *args, **kwargs)
                    if canonical_artifact_kind(relative) is not None:
                        target_count += 1
                        if stage == f"after target {target_count}":
                            raise OSError(stage)
                    if relative == catalog_relative and stage == "after catalog":
                        raise OSError(stage)
                    return result

                def interrupt_commit(relative: str, value: object) -> None:
                    write_immutable(relative, value)
                    if stage == "after commit":
                        raise OSError(stage)

                def interrupt_retirement(*args, **kwargs) -> None:
                    if stage == "before retirement":
                        raise OSError(stage)
                    retire(*args, **kwargs)
                    if stage == "after retirement":
                        raise OSError(stage)

                with (
                    mock.patch.object(
                        repository._storage,
                        "publish_bytes",
                        side_effect=interrupt_publish,
                    ),
                    mock.patch.object(
                        repository,
                        "_write_immutable_json",
                        side_effect=interrupt_commit,
                    ),
                    mock.patch.object(
                        repository._storage,
                        "unlink",
                        side_effect=interrupt_retirement,
                    ),
                    self.assertRaisesRegex(OSError, stage),
                ):
                    self.commit_repository(
                        repository,
                        core_updates={"global_outline.md": "agent outline"},
                    )

                state = root / "agent_states/orchestrator_state/writing_artifacts"
                if stage in {"after target 1", "after target 2", "after commit"}:
                    self.assertFalse(state.joinpath("catalog.json").exists())
                recovered = WritingArtifactRepository(root)
                physical_before = tuple(
                    path
                    for path in (
                        "core_documents/global_outline.md",
                        "manuscript/part-01/chapter-01.md",
                    )
                    if root.joinpath(path).is_file()
                )
                self.assertEqual(
                    recovered.list_paths(),
                    physical_before,
                )
                recovered.catalog()
                self.assertEqual(
                    recovered.list_paths(),
                    (
                        "core_documents/global_outline.md",
                        "manuscript/part-01/chapter-01.md",
                    ),
                )
                self.assertFalse(state.joinpath("recovery_blocked.json").exists())

    def test_repository_decoder_rejects_duplicate_keys_at_every_nesting_level(
        self,
    ) -> None:
        cases = {
            "journal": b'{"schema_version":2,"schema_version":2}',
            "catalog": b'{"catalog_after":{"entries":[],"entries":[]}}',
            "commit": b'{"commit":{"schema_version":1,"schema_version":1}}',
            "nested entry": b'{"entries":[{"path":"a","path":"a"}]}',
        }
        for label, raw in cases.items():
            with self.subTest(label=label), self.assertRaisesRegex(
                WorkspaceFormatError,
                "duplicate keys",
            ):
                self.repository._decode_json(raw)

    def test_missing_or_replaced_live_journal_authority_blocks_durably(self) -> None:
        for disposition in ("missing", "replaced"):
            with (
                self.subTest(disposition=disposition),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                Workspace(root).initialize()
                repository = WritingArtifactRepository(root)
                publish = repository._storage.publish_bytes
                changed = False

                def invalidate_after_first_target(relative: str, *args, **kwargs):
                    nonlocal changed
                    result = publish(relative, *args, **kwargs)
                    if canonical_artifact_kind(relative) is not None and not changed:
                        changed = True
                        state = root / "agent_states/orchestrator_state/writing_artifacts"
                        journal = next(state.joinpath("transactions").glob("*.json"))
                        journal.unlink()
                        if disposition == "replaced":
                            journal.write_bytes(b'{"replacement":true}\n')
                    return result

                with (
                    mock.patch.object(
                        repository._storage,
                        "publish_bytes",
                        side_effect=invalidate_after_first_target,
                    ),
                    self.assertRaisesRegex(
                        WorkspaceFormatError,
                        "recovery is blocked",
                    ),
                ):
                    self.commit_repository(repository)

                state = root / "agent_states/orchestrator_state/writing_artifacts"
                self.assertTrue(state.joinpath("recovery_blocked.json").is_file())
                self.assertFalse(state.joinpath("catalog.json").exists())
                source_paths = tuple(
                    path
                    for path in ("manuscript/part-01/chapter-01.md",)
                    if root.joinpath(path).is_file()
                )
                self.assertEqual(
                    WritingArtifactRepository(root).list_paths(),
                    source_paths,
                )
                with self.assertRaisesRegex(
                    WorkspaceFormatError,
                    "recovery is blocked",
                ):
                    WritingArtifactRepository(root).catalog()

    def test_catalog_before_after_and_neither_are_handled_exactly(self) -> None:
        for position in ("before", "after", "neither"):
            with self.subTest(position=position), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                Workspace(root).initialize()
                repository = WritingArtifactRepository(root)
                with (
                    mock.patch.object(
                        repository,
                        "_complete_transaction",
                        side_effect=OSError("prepared"),
                    ),
                    self.assertRaisesRegex(OSError, "prepared"),
                ):
                    self.commit_repository(
                        repository,
                        core_updates={"global_outline.md": "agent outline"},
                    )
                state = root / "agent_states/orchestrator_state/writing_artifacts"
                journal = next(state.joinpath("transactions").glob("*.json"))
                transaction = json.loads(journal.read_text(encoding="utf-8"))

                if position == "after":
                    for target in transaction["targets"]:
                        destination = root / target["path"]
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(
                            base64.b64decode(target["new_content_base64"])
                        )
                    commit = transaction["commit"]
                    state.joinpath("commits", f"{commit['commit_id']}.json").write_bytes(
                        repository._json_bytes(commit)
                    )
                    state.joinpath("catalog.json").write_bytes(
                        repository._json_bytes(transaction["catalog_after"])
                    )
                elif position == "neither":
                    state.joinpath("catalog.json").write_bytes(
                        repository._json_bytes(ArtifactCatalog.empty().to_dict())
                    )

                recovered = WritingArtifactRepository(root)
                physical_before = (
                    (
                        "core_documents/global_outline.md",
                        "manuscript/part-01/chapter-01.md",
                    )
                    if position == "after"
                    else ()
                )
                self.assertEqual(recovered.list_paths(), physical_before)
                if position == "neither":
                    with self.assertRaisesRegex(
                        WorkspaceFormatError,
                        "recovery is blocked",
                    ):
                        recovered.catalog()
                    self.assertFalse(
                        root.joinpath("manuscript/part-01/chapter-01.md").exists()
                    )
                    self.assertTrue(state.joinpath("recovery_blocked.json").is_file())
                else:
                    recovered.catalog()
                    self.assertEqual(
                        recovered.list_paths(),
                        (
                            "core_documents/global_outline.md",
                            "manuscript/part-01/chapter-01.md",
                        ),
                    )
                    self.assertFalse(tuple(state.joinpath("transactions").glob("*.json")))

    def test_unrelated_catalog_changes_block_before_target_mutation(self) -> None:
        for mutation in ("deletion", "substitution", "addition"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                Workspace(root).initialize()
                repository = WritingArtifactRepository(root)
                prior_path, _ = self.commit_repository(
                    repository,
                    output_path="part-00/prior.md",
                    core_updates={"global_outline.md": "prior outline"},
                )
                prior_bytes = (root / prior_path).read_bytes()
                with (
                    mock.patch.object(
                        repository,
                        "_complete_transaction",
                        side_effect=OSError("prepared"),
                    ),
                    self.assertRaisesRegex(OSError, "prepared"),
                ):
                    self.commit_repository(
                        repository,
                        output_path="part-01/new.md",
                    )
                state = root / "agent_states/orchestrator_state/writing_artifacts"
                catalog_path = state / "catalog.json"
                catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
                entries = catalog["entries"]
                if mutation == "deletion":
                    entries.pop(0)
                elif mutation == "substitution":
                    entries[0]["sha256"] = "f" * 64
                else:
                    extra = deepcopy(entries[0])
                    extra["path"] = "manuscript/part-00/unrelated.md"
                    entries.append(extra)
                catalog_path.write_bytes(repository._json_bytes(catalog))

                expected_sources = (
                    "core_documents/global_outline.md",
                    prior_path,
                )
                self.assertEqual(
                    WritingArtifactRepository(root).list_paths(),
                    expected_sources,
                )
                with self.assertRaisesRegex(
                    WorkspaceFormatError,
                    "recovery is blocked",
                ):
                    WritingArtifactRepository(root).catalog()
                self.assertFalse(root.joinpath("manuscript/part-01/new.md").exists())
                self.assertEqual((root / prior_path).read_bytes(), prior_bytes)
                self.assertTrue(state.joinpath("recovery_blocked.json").is_file())

    def test_interrupted_publication_lists_physical_progress_then_rolls_forward(
        self,
    ) -> None:
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
            ("manuscript/part-01/chapter-01.md",),
        )
        recovered.catalog()
        self.assertEqual(
            recovered.list_paths(),
            (
                "core_documents/global_outline.md",
                "manuscript/part-01/chapter-01.md",
            ),
        )
        self.assertFalse(tuple(state.joinpath("transactions").iterdir()))

    def test_unrecoverable_journal_blocks_publication_but_not_source_access(
        self,
    ) -> None:
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
        self.assertEqual(recovered.list_paths(), ())
        self.assertFalse(state.joinpath("recovery_blocked.json").exists())
        with self.assertRaisesRegex(WorkspaceFormatError, "recovery is blocked"):
            recovered.catalog()
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
        self.assertEqual(recovered.list_paths(), ())
        with self.assertRaisesRegex(WorkspaceFormatError, "recovery is blocked"):
            recovered.catalog()

        self.assertEqual(commit_path.read_bytes(), conflicting_bytes)
        self.assertTrue(state.joinpath("recovery_blocked.json").is_file())
        self.assertTrue(transaction.is_file())
        self.assertFalse(state.joinpath("catalog.json").exists())
        self.assertFalse(
            self.root.joinpath("manuscript/part-01/chapter-01.md").exists()
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
                WorkspaceFormatError,
                "recovery is blocked",
            ),
        ):
            self.commit(core_updates={"global_outline.md": "agent outline"})

        self.assertTrue(replaced)
        transaction_path = next(state.joinpath("transactions").glob("*.json"))
        self.assertEqual(transaction_path.read_bytes(), replacement)
        recovered = WritingArtifactRepository(self.root)
        self.assertEqual(
            recovered.list_paths(),
            (
                "core_documents/global_outline.md",
                "manuscript/part-01/chapter-01.md",
            ),
        )
        with self.assertRaisesRegex(WorkspaceFormatError, "recovery is blocked"):
            recovered.catalog()
        self.assertEqual(transaction_path.read_bytes(), replacement)
        self.assertTrue(state.joinpath("recovery_blocked.json").is_file())

    def test_orphan_staging_is_ignored_but_unexpected_transaction_state_blocks(self) -> None:
        self.assertEqual(self.repository.list_paths(), ())
        state = self.root / "agent_states/orchestrator_state/writing_artifacts"
        state.joinpath("staging").mkdir(parents=True, exist_ok=True)
        state.joinpath("transactions").mkdir(parents=True, exist_ok=True)
        orphan = state / "staging/orphan.tmp"
        orphan.write_bytes(b"unowned staging residue")

        self.assertEqual(WritingArtifactRepository(self.root).list_paths(), ())
        self.assertFalse(state.joinpath("recovery_blocked.json").exists())
        self.assertEqual(orphan.read_bytes(), b"unowned staging residue")

        unexpected = state / "transactions/unexpected.tmp"
        unexpected.write_bytes(b"authoritative ambiguity")
        self.assertEqual(WritingArtifactRepository(self.root).list_paths(), ())
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

        for invalid in (True, 1.0, "1", None, 3):
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
