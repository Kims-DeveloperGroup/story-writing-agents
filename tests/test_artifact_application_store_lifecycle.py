"""Focused lifecycle and persisted-truth tests for artifact application."""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from unittest import mock

from story_writing_agents.agents import EXECUTION_SPECIALIST
from story_writing_agents.errors import WorkspaceFormatError
from story_writing_agents.writing_artifacts import (ARTIFACT_APPLICATION_SCHEMA_VERSION,
    ArtifactApplicationError,
    ArtifactApplicationProvenance,
    ArtifactApplicationRequest,
    ArtifactApplicationState,
    ArtifactChangeSetError,
    ArtifactCreateOperation,
    ArtifactDeleteOperation,
    ArtifactKind,
    ArtifactPatchOperation,
    ArtifactRenameOperation,
    ArtifactReplaceOperation,
    ArtifactTextHunk,
    build_artifact_change_set,
)
from story_writing_agents.writing_artifacts import (_application_store as application_store_module,
)
from story_writing_agents.writing_artifacts.relationship_policy import (ArtifactRelationshipError,
    validate_artifact_relationships,
)
from story_writing_agents.writing_artifacts.repository import (
    WritingArtifactRepository,
)

from tests.helpers import pristine_initialized_workspace


_CORE_CREATE = "core_documents/character_sheets.md"
_CORE_PATCH = "core_documents/global_outline.md"
_MOVE = "manuscript/part-01/move.md"
_MOVED = "manuscript/part-01/moved.md"
_REPLACE = "manuscript/part-01/opening.md"
_DELETE = "manuscript/part-01/retired.md"
_CREATE = "manuscript/part-01/created.md"


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _current_state(repository: WritingArtifactRepository) -> dict[str, bytes]:
    with repository._locked_repository():
        return repository._applications.current_state()


def _request(repository: WritingArtifactRepository,
    operation_factory,
    *,
    application_id: str,
    candidate_revision: int = 1,
    attached_core_paths: tuple[str, ...] = (),
) -> ArtifactApplicationRequest:
    base = _current_state(repository)
    change_set = build_artifact_change_set(base, tuple(operation_factory(base)))
    validation = validate_artifact_relationships(base,
        change_set,
        attached_core_paths=attached_core_paths,
    )
    provenance = ArtifactApplicationProvenance(application_id=application_id,
        candidate_id=f"candidate-{application_id}",
        candidate_revision=candidate_revision,
        candidate_digest="sha256:" + "a" * 64,
        change_set_digest=change_set.change_set_digest,
        relationship_validation_digest=(application_store_module._relationship_digest(validation)
        ),
    )
    return ArtifactApplicationRequest(ARTIFACT_APPLICATION_SCHEMA_VERSION,
        provenance,
        change_set,
        attached_core_paths,
    )


def _apply(repository: WritingArtifactRepository,
    request: ArtifactApplicationRequest,
):
    return repository.apply_artifact_changes(request)


def _seed_five_operation_state(root: Path) -> None:
    root.joinpath(_CORE_CREATE).unlink()
    for path, content in ((_CORE_PATCH, b"outline-before"),
        (_MOVE, b"move-content"),
        (_REPLACE, b"opening-before"),
        (_DELETE, b"retired-content"),
    ):
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)


def _five_operation_request(repository: WritingArtifactRepository,
    *,
    application_id: str = "application-five",
) -> ArtifactApplicationRequest:
    def operations(base: dict[str, bytes]):
        core_before = base[_CORE_PATCH]
        move = base[_MOVE]
        replace_before = base[_REPLACE]
        deleted = base[_DELETE]
        created = b"character-created"
        core_after = b"outline-after"
        replaced = b"opening-after"
        core_digest = _sha256(core_before)
        return (ArtifactCreateOperation(
                _CORE_CREATE,
                ArtifactKind.CORE_DOCUMENT,
                created,
                _sha256(created),
            ),
            ArtifactPatchOperation(_CORE_PATCH,
                ArtifactKind.CORE_DOCUMENT,
                core_digest,
                (ArtifactTextHunk(
                        core_digest,
                        0,
                        len(core_before),
                        core_before,
                        core_after,
                    ),
                ),
                _sha256(core_after),
            ),
            ArtifactRenameOperation(_MOVE,
                _MOVED,
                ArtifactKind.MANUSCRIPT,
                _sha256(move),
                _sha256(move),
            ),
            ArtifactReplaceOperation(_REPLACE,
                ArtifactKind.MANUSCRIPT,
                _sha256(replace_before),
                replaced,
                _sha256(replaced),
            ),
            ArtifactDeleteOperation(_DELETE,
                ArtifactKind.MANUSCRIPT,
                _sha256(deleted),
            ),
        )

    return _request(repository,
        operations,
        application_id=application_id,
    )


class _ApplicationStoreTestCase(unittest.TestCase):
    def new_repository(self) -> tuple[Path, WritingArtifactRepository]:
        root = pristine_initialized_workspace(self).root
        return root, WritingArtifactRepository(root)

    @staticmethod
    def state_root(root: Path) -> Path:
        return root / application_store_module.ARTIFACT_STATE_DIRECTORY

    def assert_no_application_authority(self, root: Path) -> None:
        state = self.state_root(root)
        for name in ("applications", "transactions"):
            directory = state / name
            self.assertFalse(directory.exists() and tuple(directory.iterdir()))


class ArtifactApplicationLifecycleTests(_ApplicationStoreTestCase):
    def test_multifile_request_applies_all_five_operations_and_returns_exact_applied_result(self,
    ) -> None:
        root, repository = self.new_repository()
        _seed_five_operation_state(root)
        request = _five_operation_request(repository)

        result = _apply(repository, request)

        self.assertIs(result.state, ArtifactApplicationState.APPLIED)
        self.assertEqual(result.application_id, request.application_id)
        self.assertEqual(result.affected_paths,
            tuple(sorted(
                    (_CORE_CREATE, _CORE_PATCH, _MOVE, _MOVED, _REPLACE, _DELETE),
                    key=lambda value: value.encode("utf-8"),
                )
            ),
        )
        self.assertEqual((root / _CORE_CREATE).read_bytes(), b"character-created")
        self.assertEqual((root / _CORE_PATCH).read_bytes(), b"outline-after")
        self.assertFalse((root / _MOVE).exists())
        self.assertEqual((root / _MOVED).read_bytes(), b"move-content")
        self.assertEqual((root / _REPLACE).read_bytes(), b"opening-after")
        self.assertFalse((root / _DELETE).exists())
        catalog = repository.catalog()
        self.assertEqual(tuple(catalog.entries),
            (_CORE_CREATE, _CORE_PATCH, _MOVED, _REPLACE),
        )
        self.assertEqual(tuple((item.path, item.sha256) for item in result.final_artifacts),
            tuple((path, catalog.entries[path].sha256)
                for path in (_CORE_CREATE, _CORE_PATCH, _MOVED, _REPLACE)
            ),
        )
        self.assertEqual(
            tuple((item.source_path, item.destination_path) for item in result.rename_lineage),
            ((_MOVE, _MOVED),),
        )
        self.assertEqual(tuple(item.path for item in result.deletion_provenance),
            (_DELETE,),
        )

    def test_each_operation_enforces_presence_absence_and_source_digest_before_journal(self,
    ) -> None:
        cases = (("create", "present"),) + tuple(
            (operation, state)
            for operation in ("patch", "replace", "rename", "delete")
            for state in ("absent", "drift")
        )
        for operation, mutation in cases:
            with self.subTest(operation=operation, mutation=mutation):
                root, repository = self.new_repository()
                path = _CREATE if operation == "create" else _REPLACE
                if operation != "create":
                    (root / path).parent.mkdir(parents=True, exist_ok=True)
                    (root / path).write_bytes(b"before")

                def operations(base: dict[str, bytes]):
                    if operation == "create":
                        return (
                            ArtifactCreateOperation(
                                path,
                                ArtifactKind.MANUSCRIPT,
                                b"created",
                                _sha256(b"created"),
                            ),
                        )
                    before = base[path]
                    digest = _sha256(before)
                    if operation == "patch":
                        return (
                            ArtifactPatchOperation(
                                path,
                                ArtifactKind.MANUSCRIPT,
                                digest,
                                (ArtifactTextHunk(digest, 0, len(before), before, b"after"),),
                                _sha256(b"after"),
                            ),
                        )
                    if operation == "replace":
                        return (ArtifactReplaceOperation(
                                path,
                                ArtifactKind.MANUSCRIPT,
                                digest,
                                b"after",
                                _sha256(b"after"),
                            ),
                        )
                    if operation == "rename":
                        return (ArtifactRenameOperation(
                                path,
                                _MOVED,
                                ArtifactKind.MANUSCRIPT,
                                digest,
                                digest,
                            ),
                        )
                    return (
                        ArtifactDeleteOperation(path, ArtifactKind.MANUSCRIPT, digest),
                    )

                request = _request(repository,
                    operations,
                    application_id=f"application-{operation}-{mutation}",
                )
                if mutation == "absent":
                    (root / path).unlink()
                else:
                    (root / path).parent.mkdir(parents=True, exist_ok=True)
                    (root / path).write_bytes(b"external-drift")
                with self.assertRaises(
                    (ArtifactChangeSetError, ArtifactApplicationError, WorkspaceFormatError)
                ):
                    _apply(repository, request)
                self.assert_no_application_authority(root)

    def test_core_create_patch_replace_succeed_and_core_rename_delete_fail_before_publication(self,
    ) -> None:
        for operation in ("create", "patch", "replace"):
            with self.subTest(operation=operation):
                root, repository = self.new_repository()
                path = _CORE_CREATE if operation == "create" else _CORE_PATCH
                if operation == "create":
                    (root / path).unlink()
                else:
                    (root / path).write_bytes(b"before")

                def operations(base: dict[str, bytes]):
                    if operation == "create":
                        return (
                            ArtifactCreateOperation(
                                path,
                                ArtifactKind.CORE_DOCUMENT,
                                b"after",
                                _sha256(b"after"),
                            ),
                        )
                    before = base[path]
                    digest = _sha256(before)
                    if operation == "patch":
                        return (
                            ArtifactPatchOperation(
                                path,
                                ArtifactKind.CORE_DOCUMENT,
                                digest,
                                (ArtifactTextHunk(digest, 0, len(before), before, b"after"),),
                                _sha256(b"after"),
                            ),
                        )
                    return (
                        ArtifactReplaceOperation(path,
                            ArtifactKind.CORE_DOCUMENT,
                            digest,
                            b"after",
                            _sha256(b"after"),
                        ),
                    )

                result = _apply(repository,
                    _request(repository,
                        operations,
                        application_id=f"application-core-{operation}",
                    ),
                )
                self.assertIs(result.state, ArtifactApplicationState.APPLIED)
                self.assertEqual((root / path).read_bytes(), b"after")

        for operation in ("rename", "delete"):
            with self.subTest(operation=operation):
                root, repository = self.new_repository()
                path = _CORE_PATCH
                (root / path).write_bytes(b"before")
                destination = "core_documents/narrative_rules.md"
                if operation == "rename":
                    (root / destination).unlink()
                base = _current_state(repository)
                digest = _sha256(base[path])
                operations = ((
                        ArtifactRenameOperation(path,
                            destination,
                            ArtifactKind.CORE_DOCUMENT,
                            digest,
                            digest,
                        )
                    )
                    if operation == "rename"
                    else ArtifactDeleteOperation(path,
                        ArtifactKind.CORE_DOCUMENT,
                        digest,
                    ),
                )
                change_set = build_artifact_change_set(base, operations)
                provenance = ArtifactApplicationProvenance(
                    application_id=f"application-core-{operation}",
                    candidate_id=f"candidate-core-{operation}",
                    candidate_revision=1,
                    candidate_digest="sha256:" + "a" * 64,
                    change_set_digest=change_set.change_set_digest,
                    relationship_validation_digest="sha256:" + "b" * 64,
                )
                request = ArtifactApplicationRequest(1, provenance, change_set, ())
                with self.assertRaises(ArtifactRelationshipError):
                    _apply(repository, request)
                self.assertEqual((root / path).read_bytes(), b"before")
                self.assert_no_application_authority(root)

    def test_catalog_tracks_only_current_membership_while_history_preserves_rename_and_delete_facts(
        self,
    ) -> None:
        root, repository = self.new_repository()
        _seed_five_operation_state(root)
        result = _apply(repository, _five_operation_request(repository))
        state = self.state_root(root)
        record_path = state / "applications/application-five.json"
        record_bytes = record_path.read_bytes()
        record = json.loads(record_bytes)

        self.assertNotIn(_MOVE, repository.catalog().entries)
        self.assertNotIn(_DELETE, repository.catalog().entries)
        self.assertIn(_MOVED, repository.catalog().entries)
        self.assertEqual(record["result"]["rename_lineage"][0]["source_path"], _MOVE)
        self.assertEqual(record["result"]["deletion_provenance"][0]["path"], _DELETE)
        self.assertNotIn(b"move-content", record_bytes)
        self.assertNotIn(b"retired-content", record_bytes)
        self.assertEqual(result.rename_lineage[0].sha256, _sha256(b"move-content"))

    def test_later_applications_preserve_prior_history_and_audit_exact_current_projection(self,
    ) -> None:
        root, repository = self.new_repository()
        _seed_five_operation_state(root)
        _apply(repository, _five_operation_request(repository))
        first_path = self.state_root(root) / "applications/application-five.json"
        first_bytes = first_path.read_bytes()

        def operations(base: dict[str, bytes]):
            before = base[_MOVED]
            after = b"move-content-revised"
            return (
                ArtifactReplaceOperation(_MOVED,
                    ArtifactKind.MANUSCRIPT,
                    _sha256(before),
                    after,
                    _sha256(after),
                ),
            )

        second = _request(repository,
            operations,
            application_id="application-second",
            candidate_revision=2,
        )
        _apply(repository, second)

        self.assertEqual(first_path.read_bytes(), first_bytes)
        self.assertEqual((root / _MOVED).read_bytes(), b"move-content-revised")
        catalog = repository.catalog()
        self.assertEqual(catalog.entries[_MOVED].application_id, "application-second")
        self.assertEqual(catalog.entries[_MOVED].application_sequence, 2)
        self.assertEqual(tuple(path.name for path in sorted(first_path.parent.glob("*.json"))),
            ("application-five.json", "application-second.json"),
        )

    def test_prior_create_only_catalog_transaction_and_commit_shapes_fail_closed_without_rewrite(
        self,
    ) -> None:
        cases = {"catalog": (
                "catalog.json",
                _canonical_json({"entries": [], "schema_version": 1}),
            ),
            "transaction": ("transactions/" + "1" * 32 + ".json",
                _canonical_json({
                        "catalog_after": {"entries": [], "schema_version": 1},
                        "catalog_before": {"exists": False,
                            "image": {"entries": [], "schema_version": 1},
                            "sha256": None,
                        },
                        "commit": {},
                        "schema_version": 2,
                        "targets": [],
                        "transaction_id": "1" * 32,
                    }
                ),
            ),
            "commit": ("commits/" + "2" * 32 + ".json",
                _canonical_json({"schema_version": 1}),
            ),
        }
        for label, (relative, payload) in cases.items():
            with self.subTest(label=label):
                root, repository = self.new_repository()
                state = self.state_root(root)
                destination = state / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload)
                with self.assertRaises(WorkspaceFormatError):
                    repository.catalog()
                self.assertEqual(destination.read_bytes(), payload)
                self.assertFalse(tuple(state.joinpath("applications").glob("*.json")))

    def test_temporary_commit_consensus_delegates_to_new_engine_and_persists_only_new_schemas(self,
    ) -> None:
        root, repository = self.new_repository()
        original = repository._applications.apply_consensus
        with mock.patch.object(repository._applications,
            "apply_consensus",
            wraps=original,
        ) as delegated:
            path, updates = repository.commit_consensus(output_path="part-01/consensus.md",
                candidate="consensus text\n",
                core_updates={"global_outline.md": "outline\n"},
                session_id="session-1",
                debate_id="debate-1",
                revision_id="r0",
                candidate_author_agent_id=EXECUTION_SPECIALIST,
            )
        self.assertEqual(path, "manuscript/part-01/consensus.md")
        self.assertEqual(updates, ("global_outline.md",))
        delegated.assert_called_once()
        state = self.state_root(root)
        records = tuple(state.joinpath("applications").glob("*.json"))
        self.assertEqual(len(records), 1)
        self.assertEqual(json.loads(records[0].read_bytes())["schema_version"], 1)
        self.assertEqual(json.loads(state.joinpath("catalog.json").read_bytes())["schema_version"], 2)
        self.assertFalse(state.joinpath("commits").exists())
        self.assertFalse(tuple(state.joinpath("transactions").glob("*.json")))


if __name__ == "__main__":
    unittest.main()
