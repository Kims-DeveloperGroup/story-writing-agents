"""Exact pure change-set contracts for canonical writing artifacts."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from types import MappingProxyType

import story_writing_agents
from story_writing_agents import writing_artifacts
from story_writing_agents.writing_artifacts import (
    ARTIFACT_CHANGE_SET_SCHEMA_VERSION,
    ArtifactChangeOperation,
    ArtifactChangeOperationKind,
    ArtifactChangeSet,
    ArtifactChangeSetError,
    ArtifactChangeSetErrorCode,
    ArtifactCreateOperation,
    ArtifactDeleteOperation,
    ArtifactKind,
    ArtifactPatchOperation,
    ArtifactRenameOperation,
    ArtifactReplaceOperation,
    ArtifactTextHunk,
    build_artifact_change_set,
    deserialize_artifact_change_set,
    project_artifact_change_set,
    serialize_artifact_change_set,
)
from story_writing_agents.writing_artifacts import change_sets as change_sets_module


_CORE = "core_documents/global_outline.md"
_CORE_DESTINATION = "core_documents/narrative_rules.md"
_MANUSCRIPT = "manuscript/part-01/opening.md"
_ERROR_MESSAGES = {
    ArtifactChangeSetErrorCode.INVALID_SCHEMA: "artifact change-set schema is invalid",
    ArtifactChangeSetErrorCode.INVALID_SERIALIZATION: "artifact change-set serialization is noncanonical or invalid",
    ArtifactChangeSetErrorCode.INVALID_UTF8: "artifact change text must be strict UTF-8",
    ArtifactChangeSetErrorCode.INVALID_PATH: "artifact change path is not canonical",
    ArtifactChangeSetErrorCode.INVALID_KIND: "artifact change kind does not match its canonical path",
    ArtifactChangeSetErrorCode.INVALID_DIGEST: "artifact change digest is invalid",
    ArtifactChangeSetErrorCode.INVALID_HUNK: "artifact text hunk is invalid",
    ArtifactChangeSetErrorCode.INVALID_BYTE_BOUNDARY: "artifact text hunk is not on UTF-8 byte boundaries",
    ArtifactChangeSetErrorCode.NO_OP: "artifact change must alter the projected state",
    ArtifactChangeSetErrorCode.HUNK_ORDER: "artifact text hunks are not in canonical order",
    ArtifactChangeSetErrorCode.HUNK_OVERLAP: "artifact text hunks overlap",
    ArtifactChangeSetErrorCode.DUPLICATE_INSERTION: "artifact text hunks contain a duplicate insertion anchor",
    ArtifactChangeSetErrorCode.OPERATION_ORDER: "artifact operations are not in canonical order",
    ArtifactChangeSetErrorCode.BASE_STATE_MISMATCH: "artifact base state does not match the change set",
    ArtifactChangeSetErrorCode.BASE_ARTIFACT_MISMATCH: "artifact base content does not match the operation",
    ArtifactChangeSetErrorCode.PROJECTED_DIGEST_MISMATCH: "artifact projected content digest does not match",
    ArtifactChangeSetErrorCode.PATH_COLLISION: "artifact change-set paths collide",
    ArtifactChangeSetErrorCode.RENAME_CONFLICT: "artifact rename topology is invalid",
    ArtifactChangeSetErrorCode.CHANGE_SET_DIGEST_MISMATCH: "artifact change-set digest does not match",
}
_MODULE_EXPORTS = (
    "ARTIFACT_CHANGE_SET_SCHEMA_VERSION",
    "ArtifactChangeOperation",
    "ArtifactChangeOperationKind",
    "ArtifactChangeSet",
    "ArtifactChangeSetError",
    "ArtifactChangeSetErrorCode",
    "ArtifactCreateOperation",
    "ArtifactDeleteOperation",
    "ArtifactPatchOperation",
    "ArtifactRenameOperation",
    "ArtifactReplaceOperation",
    "ArtifactTextHunk",
    "build_artifact_change_set",
    "deserialize_artifact_change_set",
    "project_artifact_change_set",
    "serialize_artifact_change_set",
)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _independent_state_digest(state: dict[str, bytes]) -> str:
    manifest = {
        "artifacts": [
            {
                "content_digest": _sha256(content),
                "kind": (
                    ArtifactKind.CORE_DOCUMENT.value
                    if path.startswith("core_documents/")
                    else ArtifactKind.MANUSCRIPT.value
                ),
                "path": path,
                "utf8_bytes": len(content),
            }
            for path, content in sorted(
                state.items(), key=lambda item: item[0].encode("utf-8")
            )
        ],
        "schema_version": 1,
    }
    return _sha256(_canonical_json(manifest))


def _replace_operation(
    path: str,
    before: bytes,
    after: bytes,
    *,
    kind: ArtifactKind = ArtifactKind.MANUSCRIPT,
) -> ArtifactReplaceOperation:
    return ArtifactReplaceOperation(
        path=path,
        artifact_kind=kind,
        base_digest=_sha256(before),
        content=after,
        projected_digest=_sha256(after),
    )


def _patch_operation(
    path: str,
    before: bytes,
    after: bytes,
    hunks: tuple[ArtifactTextHunk, ...],
    *,
    kind: ArtifactKind = ArtifactKind.MANUSCRIPT,
) -> ArtifactPatchOperation:
    return ArtifactPatchOperation(
        path=path,
        artifact_kind=kind,
        base_digest=_sha256(before),
        hunks=hunks,
        projected_digest=_sha256(after),
    )


def _golden_fixture() -> tuple[
    dict[str, bytes],
    ArtifactChangeSet,
    dict[str, bytes],
    dict[str, object],
]:
    before_outline = "A\r\n한글🌙\x00end".encode()
    after_outline = "\ufeffB\n바다☾\x00end\n".encode()
    outline_digest = _sha256(before_outline)
    base = {
        _CORE: before_outline,
        "manuscript/part-01/move.md": b"Move me\n",
        _MANUSCRIPT: b"Old\r\n",
        "manuscript/part-01/retired.md": b"Retire\n",
    }
    hunks = (
        ArtifactTextHunk(outline_digest, 0, 0, b"", "\ufeff".encode()),
        ArtifactTextHunk(outline_digest, 0, 1, b"A", b"B"),
        ArtifactTextHunk(outline_digest, 1, 3, b"\r\n", b"\n"),
        ArtifactTextHunk(outline_digest, 3, 9, "한글".encode(), b""),
        ArtifactTextHunk(outline_digest, 9, 9, b"", "바다".encode()),
        ArtifactTextHunk(outline_digest, 9, 13, "🌙".encode(), "☾".encode()),
        ArtifactTextHunk(outline_digest, 17, 17, b"", b"\n"),
    )
    operations = (
        ArtifactCreateOperation(
            "core_documents/character_sheets.md",
            ArtifactKind.CORE_DOCUMENT,
            "이름: 민\n".encode(),
            _sha256("이름: 민\n".encode()),
        ),
        ArtifactPatchOperation(
            _CORE,
            ArtifactKind.CORE_DOCUMENT,
            outline_digest,
            hunks,
            _sha256(after_outline),
        ),
        ArtifactRenameOperation(
            "manuscript/part-01/move.md",
            "manuscript/part-01/moved.md",
            ArtifactKind.MANUSCRIPT,
            _sha256(b"Move me\n"),
            _sha256(b"Move me\n"),
        ),
        _replace_operation(_MANUSCRIPT, b"Old\r\n", "새 장면\n".encode()),
        ArtifactDeleteOperation(
            "manuscript/part-01/retired.md",
            ArtifactKind.MANUSCRIPT,
            _sha256(b"Retire\n"),
        ),
    )
    change_set = build_artifact_change_set(base, operations)
    projected = {
        "core_documents/character_sheets.md": "이름: 민\n".encode(),
        _CORE: after_outline,
        "manuscript/part-01/moved.md": b"Move me\n",
        _MANUSCRIPT: "새 장면\n".encode(),
    }
    serialized_object = {
        "base_state_digest": change_set.base_state_digest,
        "change_set_digest": change_set.change_set_digest,
        "operations": [
            {
                "content": "이름: 민\n",
                "kind": "core_document",
                "operation": "create",
                "path": "core_documents/character_sheets.md",
                "projected_digest": _sha256("이름: 민\n".encode()),
            },
            {
                "base_digest": outline_digest,
                "hunks": [
                    {
                        "base_digest": hunk.base_digest,
                        "end_utf8_byte": hunk.end_utf8_byte,
                        "new_text": hunk.new_text.decode(),
                        "old_text": hunk.old_text.decode(),
                        "start_utf8_byte": hunk.start_utf8_byte,
                    }
                    for hunk in hunks
                ],
                "kind": "core_document",
                "operation": "patch",
                "path": _CORE,
                "projected_digest": _sha256(after_outline),
            },
            {
                "base_digest": _sha256(b"Move me\n"),
                "destination_path": "manuscript/part-01/moved.md",
                "kind": "manuscript",
                "operation": "rename",
                "projected_digest": _sha256(b"Move me\n"),
                "source_path": "manuscript/part-01/move.md",
            },
            {
                "base_digest": _sha256(b"Old\r\n"),
                "content": "새 장면\n",
                "kind": "manuscript",
                "operation": "replace",
                "path": _MANUSCRIPT,
                "projected_digest": _sha256("새 장면\n".encode()),
            },
            {
                "base_digest": _sha256(b"Retire\n"),
                "kind": "manuscript",
                "operation": "delete",
                "path": "manuscript/part-01/retired.md",
            },
        ],
        "projected_state_digest": change_set.projected_state_digest,
        "schema_version": 1,
    }
    return base, change_set, projected, serialized_object


class ChangeSetTestCase(unittest.TestCase):
    def assert_change_error(
        self,
        code: ArtifactChangeSetErrorCode,
        callback,
        *,
        operation_index: int | None = None,
        hunk_index: int | None = None,
    ) -> ArtifactChangeSetError:
        with self.assertRaises(ArtifactChangeSetError) as raised:
            callback()
        error = raised.exception
        self.assertIs(error.code, code)
        self.assertEqual(str(error), _ERROR_MESSAGES[code])
        self.assertEqual(error.args, (_ERROR_MESSAGES[code],))
        self.assertEqual(error.operation_index, operation_index)
        self.assertEqual(error.hunk_index, hunk_index)
        return error


class ArtifactChangeSetSurfaceTests(ChangeSetTestCase):
    def test_exact_public_surface_enums_signatures_and_facades(self) -> None:
        self.assertEqual(ARTIFACT_CHANGE_SET_SCHEMA_VERSION, 1)
        self.assertEqual(tuple(change_sets_module.__all__), _MODULE_EXPORTS)
        self.assertEqual(len(writing_artifacts.__all__), 62)
        for name in _MODULE_EXPORTS:
            self.assertIs(
                getattr(writing_artifacts, name),
                getattr(change_sets_module, name),
            )
            self.assertNotIn(name, story_writing_agents.__all__)
            self.assertFalse(hasattr(story_writing_agents, name))
        self.assertEqual(len(story_writing_agents.__all__), 104)
        self.assertEqual(
            tuple(item.value for item in ArtifactChangeOperationKind),
            ("create", "patch", "replace", "rename", "delete"),
        )
        self.assertEqual(
            tuple(item.value for item in ArtifactChangeSetErrorCode),
            tuple(code.value for code in _ERROR_MESSAGES),
        )
        self.assertEqual(
            tuple(inspect.signature(build_artifact_change_set).parameters),
            ("base_state", "operations"),
        )
        self.assertEqual(
            tuple(inspect.signature(project_artifact_change_set).parameters),
            ("base_state", "change_set"),
        )
        self.assertEqual(
            tuple(inspect.signature(serialize_artifact_change_set).parameters),
            ("change_set",),
        )
        self.assertEqual(
            tuple(inspect.signature(deserialize_artifact_change_set).parameters),
            ("data",),
        )
        self.assertIsNotNone(ArtifactChangeOperation)

    def test_value_schemas_are_exact_frozen_slotted_and_content_free_in_repr(
        self,
    ) -> None:
        expected_fields = {
            ArtifactTextHunk: (
                "base_digest",
                "start_utf8_byte",
                "end_utf8_byte",
                "old_text",
                "new_text",
            ),
            ArtifactCreateOperation: (
                "path",
                "artifact_kind",
                "content",
                "projected_digest",
            ),
            ArtifactPatchOperation: (
                "path",
                "artifact_kind",
                "base_digest",
                "hunks",
                "projected_digest",
            ),
            ArtifactReplaceOperation: (
                "path",
                "artifact_kind",
                "base_digest",
                "content",
                "projected_digest",
            ),
            ArtifactRenameOperation: (
                "source_path",
                "destination_path",
                "artifact_kind",
                "base_digest",
                "projected_digest",
            ),
            ArtifactDeleteOperation: ("path", "artifact_kind", "base_digest"),
            ArtifactChangeSet: (
                "schema_version",
                "base_state_digest",
                "operations",
                "projected_state_digest",
                "change_set_digest",
            ),
        }
        base, change_set, _, _ = _golden_fixture()
        values = (
            change_set.operations[1].hunks[0],
            *change_set.operations,
            change_set,
        )
        for value_type, field_names in expected_fields.items():
            with self.subTest(value_type=value_type.__name__):
                self.assertEqual(
                    tuple(field.name for field in fields(value_type)), field_names
                )
                self.assertEqual(
                    tuple(inspect.signature(value_type).parameters), field_names
                )
                self.assertTrue(value_type.__dataclass_params__.frozen)
                self.assertIn("__slots__", value_type.__dict__)
        for value in values:
            with self.subTest(value=type(value).__name__):
                self.assertFalse(hasattr(value, "__dict__"))
                field_name = fields(type(value))[0].name
                with self.assertRaises(
                    (FrozenInstanceError, AttributeError, TypeError)
                ):
                    setattr(value, field_name, None)
        review_text = base[_CORE]
        self.assertNotIn(repr(review_text), repr(change_set))
        self.assertNotIn("한글", repr(change_set))
        self.assertNotIn("새 장면", repr(change_set))
        self.assertEqual(
            tuple(operation.operation_kind for operation in change_set.operations),
            (
                ArtifactChangeOperationKind.CREATE,
                ArtifactChangeOperationKind.PATCH,
                ArtifactChangeOperationKind.RENAME,
                ArtifactChangeOperationKind.REPLACE,
                ArtifactChangeOperationKind.DELETE,
            ),
        )

    def test_error_constructor_is_closed_and_content_free(self) -> None:
        for code, message in _ERROR_MESSAGES.items():
            with self.subTest(code=code):
                error = ArtifactChangeSetError(code, operation_index=1, hunk_index=2)
                self.assertEqual(str(error), message)
                self.assertEqual(error.args, (message,))
                self.assertNotIn("story-secret", repr(error))
        for value in ("invalid_schema", 1, None):
            with self.subTest(value=value), self.assertRaises(TypeError):
                ArtifactChangeSetError(value)  # type: ignore[arg-type]
        for name in ("operation_index", "hunk_index"):
            for value in (-1, True, "1"):
                with self.subTest(name=name, value=value), self.assertRaises(TypeError):
                    ArtifactChangeSetError(
                        ArtifactChangeSetErrorCode.INVALID_SCHEMA,
                        **{name: value},
                    )


class ArtifactTextHunkTests(ChangeSetTestCase):
    def test_known_vector_preserves_utf8_crlf_bom_nul_and_no_final_newline(
        self,
    ) -> None:
        base, change_set, projected, _ = _golden_fixture()
        result = project_artifact_change_set(base, change_set)
        self.assertEqual(dict(result), projected)
        self.assertEqual(
            result[_CORE],
            "\ufeffB\n바다☾\x00end\n".encode(),
        )
        self.assertIn(b"\x00", result[_CORE])
        self.assertNotIn(b"\r\n", result[_CORE])
        self.assertEqual(base[_CORE], "A\r\n한글🌙\x00end".encode())
        self.assertFalse(base[_CORE].endswith(b"\n"))
        self.assertEqual(
            _sha256(base[_CORE]),
            "2176a12019c7890402c37cf15a5afa6f78155664c8cb058de6100aeefa440471",
        )
        self.assertEqual(
            _sha256(result[_CORE]),
            "6fec5ab59fa33a9f1d2b5c37e91352d2134a2a8df8d3b94d64ba76e94b96bbac",
        )

    def test_codepoint_boundary_insert_replace_delete_property_vectors(self) -> None:
        texts = (
            "",
            "ascii",
            "line\n",
            "crlf\r\nline",
            "끝없음",
            "🌙x",
            "\ufeffbom",
            "nul\x00byte",
            "é",
            "e\u0301",
        )
        for text in texts:
            before = text.encode()
            boundaries = [len(text[:index].encode()) for index in range(len(text) + 1)]
            for offset in boundaries:
                with self.subTest(text=text, operation="insert", offset=offset):
                    after = before[:offset] + "§".encode() + before[offset:]
                    hunk = ArtifactTextHunk(
                        _sha256(before), offset, offset, b"", "§".encode()
                    )
                    change_set = build_artifact_change_set(
                        {_MANUSCRIPT: before},
                        (_patch_operation(_MANUSCRIPT, before, after, (hunk,)),),
                    )
                    self.assertEqual(
                        project_artifact_change_set({_MANUSCRIPT: before}, change_set)[
                            _MANUSCRIPT
                        ],
                        after,
                    )
            for index, character in enumerate(text):
                start = len(text[:index].encode())
                end = len(text[: index + 1].encode())
                old = character.encode()
                for replacement in (b"", b"Z"):
                    with self.subTest(
                        text=text,
                        operation="delete" if not replacement else "replace",
                        index=index,
                    ):
                        after = before[:start] + replacement + before[end:]
                        hunk = ArtifactTextHunk(
                            _sha256(before), start, end, old, replacement
                        )
                        change_set = build_artifact_change_set(
                            {_MANUSCRIPT: before},
                            (_patch_operation(_MANUSCRIPT, before, after, (hunk,)),),
                        )
                        self.assertEqual(
                            project_artifact_change_set(
                                {_MANUSCRIPT: before}, change_set
                            )[_MANUSCRIPT],
                            after,
                        )

    def test_insertion_before_consuming_hunk_and_adjacent_hunks_are_canonical(
        self,
    ) -> None:
        before = b"ab"
        digest = _sha256(before)
        vectors = (
            (
                (
                    ArtifactTextHunk(digest, 0, 0, b"", b"X"),
                    ArtifactTextHunk(digest, 0, 1, b"a", b"Y"),
                ),
                b"XYb",
            ),
            (
                (
                    ArtifactTextHunk(digest, 0, 1, b"a", b"Y"),
                    ArtifactTextHunk(digest, 1, 1, b"", b"X"),
                ),
                b"YXb",
            ),
        )
        for hunks, after in vectors:
            with self.subTest(after=after):
                change_set = build_artifact_change_set(
                    {_MANUSCRIPT: before},
                    (_patch_operation(_MANUSCRIPT, before, after, hunks),),
                )
                self.assertEqual(
                    project_artifact_change_set({_MANUSCRIPT: before}, change_set)[
                        _MANUSCRIPT
                    ],
                    after,
                )

    def test_hunk_construction_and_sequence_reject_invalid_values(self) -> None:
        digest = _sha256(b"ab")
        bad_hunk_factories = (
            (
                ArtifactChangeSetErrorCode.INVALID_DIGEST,
                lambda: ArtifactTextHunk("A" * 64, 0, 1, b"a", b"x"),
            ),
            (
                ArtifactChangeSetErrorCode.INVALID_HUNK,
                lambda: ArtifactTextHunk(digest, True, 1, b"a", b"x"),
            ),
            (
                ArtifactChangeSetErrorCode.INVALID_HUNK,
                lambda: ArtifactTextHunk(digest, 1, 0, b"", b"x"),
            ),
            (
                ArtifactChangeSetErrorCode.INVALID_HUNK,
                lambda: ArtifactTextHunk(digest, 0, 2, b"a", b"x"),
            ),
            (
                ArtifactChangeSetErrorCode.INVALID_UTF8,
                lambda: ArtifactTextHunk(digest, 0, 1, b"\xff", b"x"),
            ),
            (
                ArtifactChangeSetErrorCode.INVALID_UTF8,
                lambda: ArtifactTextHunk(digest, 0, 1, b"a", b"\xff"),
            ),
            (
                ArtifactChangeSetErrorCode.NO_OP,
                lambda: ArtifactTextHunk(digest, 0, 1, b"a", b"a"),
            ),
            (
                ArtifactChangeSetErrorCode.NO_OP,
                lambda: ArtifactTextHunk(digest, 0, 0, b"", b""),
            ),
        )
        for code, factory in bad_hunk_factories:
            with self.subTest(code=code):
                self.assert_change_error(code, factory)

        insertion = ArtifactTextHunk(digest, 0, 0, b"", b"X")
        consuming = ArtifactTextHunk(digest, 0, 1, b"a", b"Y")
        overlap = ArtifactTextHunk(digest, 0, 2, b"ab", b"Z")
        cases = (
            (
                ArtifactChangeSetErrorCode.HUNK_ORDER,
                (consuming, insertion),
                1,
            ),
            (
                ArtifactChangeSetErrorCode.DUPLICATE_INSERTION,
                (insertion, ArtifactTextHunk(digest, 0, 0, b"", b"Q")),
                1,
            ),
            (
                ArtifactChangeSetErrorCode.HUNK_OVERLAP,
                (overlap, ArtifactTextHunk(digest, 1, 1, b"", b"Q")),
                1,
            ),
            (
                ArtifactChangeSetErrorCode.INVALID_HUNK,
                (insertion, ArtifactTextHunk(_sha256(b"other"), 1, 1, b"", b"Q")),
                1,
            ),
        )
        for code, hunks, index in cases:
            with self.subTest(code=code):
                self.assert_change_error(
                    code,
                    lambda hunks=hunks: ArtifactPatchOperation(
                        _MANUSCRIPT,
                        ArtifactKind.MANUSCRIPT,
                        digest,
                        hunks,
                        _sha256(b"changed"),
                    ),
                    hunk_index=index,
                )
        self.assert_change_error(
            ArtifactChangeSetErrorCode.INVALID_HUNK,
            lambda: ArtifactPatchOperation(
                _MANUSCRIPT,
                ArtifactKind.MANUSCRIPT,
                digest,
                (),
                _sha256(b"changed"),
            ),
        )
        self.assert_change_error(
            ArtifactChangeSetErrorCode.INVALID_HUNK,
            lambda: ArtifactPatchOperation(
                _MANUSCRIPT,
                ArtifactKind.MANUSCRIPT,
                digest,
                [insertion],
                _sha256(b"changed"),
            ),
        )

    def test_projection_rejects_invalid_boundaries_old_text_and_projected_digest(
        self,
    ) -> None:
        before = "é".encode()
        digest = _sha256(before)
        cases = (
            (
                ArtifactChangeSetErrorCode.INVALID_BYTE_BOUNDARY,
                ArtifactTextHunk(digest, 1, 2, b"x", b"y"),
                _sha256(b"projected"),
            ),
            (
                ArtifactChangeSetErrorCode.INVALID_BYTE_BOUNDARY,
                ArtifactTextHunk(digest, 3, 3, b"", b"y"),
                _sha256(b"projected"),
            ),
            (
                ArtifactChangeSetErrorCode.BASE_ARTIFACT_MISMATCH,
                ArtifactTextHunk(digest, 0, 2, b"xx", b"y"),
                _sha256(b"projected"),
            ),
            (
                ArtifactChangeSetErrorCode.PROJECTED_DIGEST_MISMATCH,
                ArtifactTextHunk(digest, 0, 2, before, b"y"),
                _sha256(b"wrong"),
            ),
        )
        for code, hunk, projected_digest in cases:
            operation = ArtifactPatchOperation(
                _MANUSCRIPT,
                ArtifactKind.MANUSCRIPT,
                digest,
                (hunk,),
                projected_digest,
            )
            with self.subTest(code=code):
                self.assert_change_error(
                    code,
                    lambda operation=operation: build_artifact_change_set(
                        {_MANUSCRIPT: before}, (operation,)
                    ),
                    operation_index=0,
                    hunk_index=0
                    if code is not ArtifactChangeSetErrorCode.PROJECTED_DIGEST_MISMATCH
                    else None,
                )


class ArtifactChangeProjectionTests(ChangeSetTestCase):
    def test_golden_state_and_change_set_digests_are_independent_and_stable(
        self,
    ) -> None:
        base, change_set, projected, _ = _golden_fixture()
        self.assertEqual(
            change_set.base_state_digest,
            "7fcb6365e4f063d0f677c9954eb079e4e68cc2de355f9e09903322e19440af1d",
        )
        self.assertEqual(
            change_set.projected_state_digest,
            "3e543d458bad17096582b62f3a14940cca6f79b994ba959dac13ce8648e41fe8",
        )
        self.assertEqual(
            change_set.change_set_digest,
            "eb4727f3b77ea30a04a6b43ccb687eb413a606822fe7fbec86e8a71cd232f6dd",
        )
        _, _, _, serialized_object = _golden_fixture()
        digest_object = dict(serialized_object)
        del digest_object["change_set_digest"]
        self.assertEqual(
            change_set.change_set_digest,
            _sha256(_canonical_json(digest_object)),
        )
        self.assertEqual(change_set.base_state_digest, _independent_state_digest(base))
        self.assertEqual(
            change_set.projected_state_digest,
            _independent_state_digest(projected),
        )
        permuted = dict(reversed(tuple(base.items())))
        rebuilt = build_artifact_change_set(permuted, change_set.operations)
        self.assertEqual(rebuilt, change_set)
        self.assertEqual(permuted, dict(reversed(tuple(base.items()))))

    def test_all_five_operations_project_core_and_manuscript_workflow(self) -> None:
        base, change_set, expected, _ = _golden_fixture()
        projected = project_artifact_change_set(base, change_set)
        self.assertIsInstance(projected, MappingProxyType)
        self.assertEqual(dict(projected), expected)
        self.assertEqual(
            tuple(projected),
            tuple(sorted(expected, key=lambda path: path.encode("utf-8"))),
        )
        with self.assertRaises(TypeError):
            projected[_CORE] = b"mutate"
        self.assertEqual(base["manuscript/part-01/move.md"], b"Move me\n")
        self.assertIn("manuscript/part-01/retired.md", base)

    def test_core_rename_and_delete_are_representable_without_policy_rejection(
        self,
    ) -> None:
        content = b"core\n"
        rename = ArtifactRenameOperation(
            _CORE,
            _CORE_DESTINATION,
            ArtifactKind.CORE_DOCUMENT,
            _sha256(content),
            _sha256(content),
        )
        renamed = build_artifact_change_set({_CORE: content}, (rename,))
        self.assertEqual(
            dict(project_artifact_change_set({_CORE: content}, renamed)),
            {_CORE_DESTINATION: content},
        )
        deletion = ArtifactDeleteOperation(
            _CORE, ArtifactKind.CORE_DOCUMENT, _sha256(content)
        )
        deleted = build_artifact_change_set({_CORE: content}, (deletion,))
        self.assertEqual(
            dict(project_artifact_change_set({_CORE: content}, deleted)), {}
        )

    def test_exact_base_absence_presence_and_digest_are_enforced_for_each_operation(
        self,
    ) -> None:
        before = b"before"
        after = b"after"
        digest = _sha256(before)
        create = ArtifactCreateOperation(
            _MANUSCRIPT,
            ArtifactKind.MANUSCRIPT,
            after,
            _sha256(after),
        )
        patch = _patch_operation(
            _MANUSCRIPT,
            before,
            after,
            (ArtifactTextHunk(digest, 0, len(before), before, after),),
        )
        replace = _replace_operation(_MANUSCRIPT, before, after)
        rename = ArtifactRenameOperation(
            _MANUSCRIPT,
            "manuscript/part-01/renamed.md",
            ArtifactKind.MANUSCRIPT,
            digest,
            digest,
        )
        delete = ArtifactDeleteOperation(_MANUSCRIPT, ArtifactKind.MANUSCRIPT, digest)
        self.assert_change_error(
            ArtifactChangeSetErrorCode.BASE_ARTIFACT_MISMATCH,
            lambda: build_artifact_change_set({_MANUSCRIPT: before}, (create,)),
            operation_index=0,
        )
        for operation in (patch, replace, rename, delete):
            with self.subTest(operation=type(operation).__name__, reason="absent"):
                self.assert_change_error(
                    ArtifactChangeSetErrorCode.BASE_ARTIFACT_MISMATCH,
                    lambda operation=operation: build_artifact_change_set(
                        {}, (operation,)
                    ),
                    operation_index=0,
                )
            with self.subTest(operation=type(operation).__name__, reason="digest"):
                self.assert_change_error(
                    ArtifactChangeSetErrorCode.BASE_ARTIFACT_MISMATCH,
                    lambda operation=operation: build_artifact_change_set(
                        {_MANUSCRIPT: b"drift"}, (operation,)
                    ),
                    operation_index=0,
                )

    def test_complete_state_digest_rejects_unrelated_add_remove_and_content_drift(
        self,
    ) -> None:
        base, change_set, _, _ = _golden_fixture()
        variants = (
            {**base, "manuscript/part-02/extra.md": b"extra"},
            {path: content for path, content in base.items() if path != _CORE},
            {**base, _CORE: b"different"},
        )
        for variant in variants:
            with self.subTest(paths=tuple(variant)):
                self.assert_change_error(
                    ArtifactChangeSetErrorCode.BASE_STATE_MISMATCH,
                    lambda variant=variant: project_artifact_change_set(
                        variant, change_set
                    ),
                )

    def test_state_and_operation_inputs_are_closed_exact_types(self) -> None:
        before = b"before"
        operation = _replace_operation(_MANUSCRIPT, before, b"after")
        invalid_states = (
            [(_MANUSCRIPT, before)],
            {1: before},
            {_MANUSCRIPT: bytearray(before)},
            {_MANUSCRIPT: b"\xff"},
            {"notes/private.md": before},
            {"manuscript/part-01/../opening.md": before},
        )
        expected = (
            ArtifactChangeSetErrorCode.INVALID_SCHEMA,
            ArtifactChangeSetErrorCode.INVALID_PATH,
            ArtifactChangeSetErrorCode.INVALID_UTF8,
            ArtifactChangeSetErrorCode.INVALID_UTF8,
            ArtifactChangeSetErrorCode.INVALID_PATH,
            ArtifactChangeSetErrorCode.INVALID_PATH,
        )
        for state, code in zip(invalid_states, expected, strict=True):
            with self.subTest(code=code):
                self.assert_change_error(
                    code,
                    lambda state=state: build_artifact_change_set(state, (operation,)),
                )
        self.assert_change_error(
            ArtifactChangeSetErrorCode.INVALID_SCHEMA,
            lambda: build_artifact_change_set({_MANUSCRIPT: before}, [operation]),
        )

    def test_empty_and_unchanged_change_sets_are_rejected(self) -> None:
        self.assert_change_error(
            ArtifactChangeSetErrorCode.NO_OP,
            lambda: build_artifact_change_set({_MANUSCRIPT: b"base"}, ()),
        )
        self.assert_change_error(
            ArtifactChangeSetErrorCode.NO_OP,
            lambda: build_artifact_change_set(
                {_MANUSCRIPT: b"base"},
                (_replace_operation(_MANUSCRIPT, b"base", b"base"),),
            ),
            operation_index=0,
        )
        before = b"ab"
        digest = _sha256(before)
        unchanged_hunks = (
            ArtifactTextHunk(digest, 0, 0, b"", b"a"),
            ArtifactTextHunk(digest, 0, 1, b"a", b""),
        )
        self.assert_change_error(
            ArtifactChangeSetErrorCode.NO_OP,
            lambda: build_artifact_change_set(
                {_MANUSCRIPT: before},
                (_patch_operation(_MANUSCRIPT, before, before, unchanged_hunks),),
            ),
            operation_index=0,
        )


class ArtifactOperationTopologyTests(ChangeSetTestCase):
    def test_paths_kinds_projected_digests_and_operation_order_are_exact(self) -> None:
        content = b"content"
        invalid_factories = (
            (
                ArtifactChangeSetErrorCode.INVALID_PATH,
                lambda: ArtifactCreateOperation(
                    "notes/private.md",
                    ArtifactKind.MANUSCRIPT,
                    content,
                    _sha256(content),
                ),
            ),
            (
                ArtifactChangeSetErrorCode.INVALID_KIND,
                lambda: ArtifactCreateOperation(
                    _CORE,
                    ArtifactKind.MANUSCRIPT,
                    content,
                    _sha256(content),
                ),
            ),
            (
                ArtifactChangeSetErrorCode.PROJECTED_DIGEST_MISMATCH,
                lambda: ArtifactCreateOperation(
                    _MANUSCRIPT,
                    ArtifactKind.MANUSCRIPT,
                    content,
                    _sha256(b"wrong"),
                ),
            ),
            (
                ArtifactChangeSetErrorCode.INVALID_UTF8,
                lambda: ArtifactCreateOperation(
                    _MANUSCRIPT,
                    ArtifactKind.MANUSCRIPT,
                    b"\xff",
                    _sha256(b"\xff"),
                ),
            ),
            (
                ArtifactChangeSetErrorCode.PROJECTED_DIGEST_MISMATCH,
                lambda: ArtifactRenameOperation(
                    _MANUSCRIPT,
                    "manuscript/part-01/renamed.md",
                    ArtifactKind.MANUSCRIPT,
                    _sha256(content),
                    _sha256(b"wrong"),
                ),
            ),
            (
                ArtifactChangeSetErrorCode.RENAME_CONFLICT,
                lambda: ArtifactRenameOperation(
                    _MANUSCRIPT,
                    _MANUSCRIPT,
                    ArtifactKind.MANUSCRIPT,
                    _sha256(content),
                    _sha256(content),
                ),
            ),
        )
        for code, factory in invalid_factories:
            with self.subTest(code=code):
                self.assert_change_error(code, factory)

        first = ArtifactCreateOperation(
            "manuscript/part-01/a.md",
            ArtifactKind.MANUSCRIPT,
            b"a",
            _sha256(b"a"),
        )
        second = ArtifactCreateOperation(
            "manuscript/part-01/z.md",
            ArtifactKind.MANUSCRIPT,
            b"z",
            _sha256(b"z"),
        )
        self.assert_change_error(
            ArtifactChangeSetErrorCode.OPERATION_ORDER,
            lambda: build_artifact_change_set({}, (second, first)),
            operation_index=1,
        )

    def test_path_participation_is_conservative_across_edit_create_and_rename(
        self,
    ) -> None:
        content = b"base"
        digest = _sha256(content)
        delete = ArtifactDeleteOperation(_MANUSCRIPT, ArtifactKind.MANUSCRIPT, digest)
        replace = _replace_operation(_MANUSCRIPT, content, b"changed")
        cases = (
            (delete, replace),
            (
                ArtifactRenameOperation(
                    _MANUSCRIPT,
                    "manuscript/part-01/z.md",
                    ArtifactKind.MANUSCRIPT,
                    digest,
                    digest,
                ),
                ArtifactCreateOperation(
                    "manuscript/part-01/z.md",
                    ArtifactKind.MANUSCRIPT,
                    b"new",
                    _sha256(b"new"),
                ),
            ),
        )
        for operations in cases:
            with self.subTest(
                operations=tuple(type(item).__name__ for item in operations)
            ):
                self.assert_change_error(
                    ArtifactChangeSetErrorCode.PATH_COLLISION,
                    lambda operations=operations: build_artifact_change_set(
                        {_MANUSCRIPT: content}, operations
                    ),
                    operation_index=1,
                )

    def test_rename_destination_must_be_absent_and_topologies_reject_chains_swaps_cycles(
        self,
    ) -> None:
        paths = tuple(f"manuscript/part-01/{letter}.md" for letter in "abc")
        content = b"base"
        digest = _sha256(content)

        def rename(source: str, destination: str) -> ArtifactRenameOperation:
            return ArtifactRenameOperation(
                source,
                destination,
                ArtifactKind.MANUSCRIPT,
                digest,
                digest,
            )

        occupied = rename(paths[0], paths[1])
        self.assert_change_error(
            ArtifactChangeSetErrorCode.RENAME_CONFLICT,
            lambda: build_artifact_change_set(
                {paths[0]: content, paths[1]: content}, (occupied,)
            ),
            operation_index=0,
        )
        topologies = (
            (rename(paths[0], paths[1]), rename(paths[1], paths[2])),
            (rename(paths[0], paths[1]), rename(paths[1], paths[0])),
            (
                rename(paths[0], paths[1]),
                rename(paths[1], paths[2]),
                rename(paths[2], paths[0]),
            ),
        )
        for operations in topologies:
            with self.subTest(size=len(operations)):
                self.assert_change_error(
                    ArtifactChangeSetErrorCode.RENAME_CONFLICT,
                    lambda operations=operations: build_artifact_change_set(
                        {path: content for path in paths}, operations
                    ),
                )
        self.assert_change_error(
            ArtifactChangeSetErrorCode.INVALID_KIND,
            lambda: ArtifactRenameOperation(
                _CORE,
                _MANUSCRIPT,
                ArtifactKind.CORE_DOCUMENT,
                digest,
                digest,
            ),
        )


class ArtifactChangeSetCodecTests(ChangeSetTestCase):
    def test_exact_canonical_utf8_golden_serialization_and_round_trip(self) -> None:
        _, change_set, _, expected_object = _golden_fixture()
        expected = _canonical_json(expected_object)
        encoded = serialize_artifact_change_set(change_set)
        self.assertEqual(encoded, expected)
        self.assertFalse(encoded.endswith(b"\n"))
        self.assertIn("이름: 민".encode(), encoded)
        self.assertIn("\ufeff".encode(), encoded)
        self.assertNotIn(b"\\ud83c", encoded)
        self.assertEqual(deserialize_artifact_change_set(encoded), change_set)
        self.assertEqual(
            serialize_artifact_change_set(deserialize_artifact_change_set(encoded)),
            encoded,
        )

    def test_serializer_preserves_distinct_unicode_forms_and_all_text_bytes(
        self,
    ) -> None:
        for text in ("é\x00", "e\u0301\x00", "\ufeff\r\nno-final-line"):
            content = text.encode()
            operation = ArtifactCreateOperation(
                _MANUSCRIPT,
                ArtifactKind.MANUSCRIPT,
                content,
                _sha256(content),
            )
            change_set = build_artifact_change_set({}, (operation,))
            encoded = serialize_artifact_change_set(change_set)
            restored = deserialize_artifact_change_set(encoded)
            with self.subTest(text=text):
                self.assertEqual(restored.operations[0].content, content)
                self.assertEqual(json.loads(encoded)["operations"][0]["content"], text)
        composed = build_artifact_change_set(
            {},
            (
                ArtifactCreateOperation(
                    _MANUSCRIPT,
                    ArtifactKind.MANUSCRIPT,
                    "é".encode(),
                    _sha256("é".encode()),
                ),
            ),
        )
        decomposed = build_artifact_change_set(
            {},
            (
                ArtifactCreateOperation(
                    _MANUSCRIPT,
                    ArtifactKind.MANUSCRIPT,
                    "e\u0301".encode(),
                    _sha256("e\u0301".encode()),
                ),
            ),
        )
        self.assertNotEqual(composed.change_set_digest, decomposed.change_set_digest)
        self.assertNotEqual(
            serialize_artifact_change_set(composed),
            serialize_artifact_change_set(decomposed),
        )

    def test_deserializer_accepts_bytes_only_and_rejects_noncanonical_encodings(
        self,
    ) -> None:
        _, change_set, _, _ = _golden_fixture()
        canonical = serialize_artifact_change_set(change_set)
        raw = json.loads(canonical)
        alternates = (
            b" " + canonical,
            canonical + b"\n",
            json.dumps(raw, ensure_ascii=False, sort_keys=True).encode(),
            json.dumps(
                raw,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode(),
            b'{"schema_version":1,"operations":[]}',
        )
        for value in (canonical.decode(), bytearray(canonical), memoryview(canonical)):
            with self.subTest(type=type(value).__name__):
                self.assert_change_error(
                    ArtifactChangeSetErrorCode.INVALID_SERIALIZATION,
                    lambda value=value: deserialize_artifact_change_set(value),
                )
        for alternate in alternates:
            with self.subTest(alternate=alternate[:24]):
                self.assert_change_error(
                    ArtifactChangeSetErrorCode.INVALID_SERIALIZATION,
                    lambda alternate=alternate: deserialize_artifact_change_set(
                        alternate
                    ),
                )

    def test_recursive_duplicate_keys_are_rejected_before_schema_use(self) -> None:
        _, change_set, _, _ = _golden_fixture()
        canonical = serialize_artifact_change_set(change_set)
        duplicates = (
            b'{"base_state_digest":"x","base_state_digest":"x"}',
            canonical.replace(
                b'"path":"core_documents/character_sheets.md"',
                b'"path":"core_documents/character_sheets.md",'
                b'"path":"core_documents/character_sheets.md"',
                1,
            ),
            canonical.replace(
                b'"old_text":""',
                b'"old_text":"","old_text":""',
                1,
            ),
        )
        for value in duplicates:
            with self.subTest(value=value[:48]):
                self.assert_change_error(
                    ArtifactChangeSetErrorCode.INVALID_SERIALIZATION,
                    lambda value=value: deserialize_artifact_change_set(value),
                )

    def test_missing_and_unknown_fields_reject_at_top_operation_and_hunk_depths(
        self,
    ) -> None:
        _, change_set, _, _ = _golden_fixture()
        base = json.loads(serialize_artifact_change_set(change_set))
        variants: list[
            tuple[
                dict[str, object],
                ArtifactChangeSetErrorCode,
                int | None,
                int | None,
            ]
        ] = []
        for missing in ("base_state_digest", "operations"):
            value = json.loads(json.dumps(base))
            del value[missing]
            variants.append(
                (value, ArtifactChangeSetErrorCode.INVALID_SCHEMA, None, None)
            )
        value = json.loads(json.dumps(base))
        value["unknown"] = 1
        variants.append((value, ArtifactChangeSetErrorCode.INVALID_SCHEMA, None, None))
        for operation_index in (0, 1):
            value = json.loads(json.dumps(base))
            value["operations"][operation_index]["unknown"] = 1
            variants.append(
                (
                    value,
                    ArtifactChangeSetErrorCode.INVALID_SCHEMA,
                    operation_index,
                    None,
                )
            )
            value = json.loads(json.dumps(base))
            del value["operations"][operation_index]["kind"]
            variants.append(
                (
                    value,
                    ArtifactChangeSetErrorCode.INVALID_KIND,
                    operation_index,
                    None,
                )
            )
        value = json.loads(json.dumps(base))
        value["operations"][1]["hunks"][0]["unknown"] = 1
        variants.append((value, ArtifactChangeSetErrorCode.INVALID_SCHEMA, 1, 0))
        value = json.loads(json.dumps(base))
        del value["operations"][1]["hunks"][0]["old_text"]
        variants.append((value, ArtifactChangeSetErrorCode.INVALID_SCHEMA, 1, 0))
        for variant, code, operation_index, hunk_index in variants:
            with self.subTest(keys=tuple(variant)):
                self.assert_change_error(
                    code,
                    lambda variant=variant: deserialize_artifact_change_set(
                        _canonical_json(variant)
                    ),
                    operation_index=operation_index,
                    hunk_index=hunk_index,
                )

    def test_malformed_utf8_json_constants_and_digest_tampering_fail_closed(
        self,
    ) -> None:
        _, change_set, _, _ = _golden_fixture()
        self.assert_change_error(
            ArtifactChangeSetErrorCode.INVALID_UTF8,
            lambda: deserialize_artifact_change_set(b"\xff"),
        )
        for value in (b'{"x":NaN}', b'{"x":Infinity}', b"not-json"):
            with self.subTest(value=value):
                self.assert_change_error(
                    ArtifactChangeSetErrorCode.INVALID_SERIALIZATION,
                    lambda value=value: deserialize_artifact_change_set(value),
                )
        raw = json.loads(serialize_artifact_change_set(change_set))
        raw["change_set_digest"] = "0" * 64
        self.assert_change_error(
            ArtifactChangeSetErrorCode.CHANGE_SET_DIGEST_MISMATCH,
            lambda: deserialize_artifact_change_set(_canonical_json(raw)),
        )


class ArtifactChangeSetPrivacyAndBoundaryTests(ChangeSetTestCase):
    def assert_content_free_graph(
        self,
        error: BaseException,
        secrets: tuple[str, ...],
    ) -> None:
        pending = [error]
        seen: set[int] = set()
        while pending:
            current = pending.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            rendered = " ".join((str(current), repr(current), repr(current.args)))
            for secret in secrets:
                self.assertNotIn(secret, rendered)
            if current.__cause__ is not None:
                pending.append(current.__cause__)
            if current.__context__ is not None:
                pending.append(current.__context__)

    def test_public_failures_are_content_free_without_hidden_exception_graph_leaks(
        self,
    ) -> None:
        secret = "UNIQUE-STORY-SECRET-DO-NOT-LEAK"
        bad_path = f"notes/{secret}.md"
        factories = (
            lambda: ArtifactCreateOperation(
                bad_path,
                ArtifactKind.MANUSCRIPT,
                secret.encode(),
                _sha256(secret.encode()),
            ),
            lambda: build_artifact_change_set(
                {_MANUSCRIPT: b"base"},
                (
                    _patch_operation(
                        _MANUSCRIPT,
                        b"base",
                        b"replacement",
                        (
                            ArtifactTextHunk(
                                _sha256(b"base"),
                                0,
                                len(secret),
                                secret.encode(),
                                b"replacement",
                            ),
                        ),
                    ),
                ),
            ),
            lambda: deserialize_artifact_change_set(secret.encode()),
        )
        for factory in factories:
            with self.subTest(factory=factory):
                with self.assertRaises(ArtifactChangeSetError) as raised:
                    factory()
                self.assert_content_free_graph(raised.exception, (secret, bad_path))
                self.assertIsNone(raised.exception.__cause__)
                self.assertIsNone(raised.exception.__context__)

    def test_corrupted_frozen_instances_fail_with_typed_content_free_errors(
        self,
    ) -> None:
        secret = b"CORRUPTED-STORY-BYTES"
        before = b"base"
        hunk = ArtifactTextHunk(_sha256(before), 0, 4, before, b"after")
        patch = _patch_operation(_MANUSCRIPT, before, b"after", (hunk,))
        change_set = build_artifact_change_set({_MANUSCRIPT: before}, (patch,))
        corruptions = (
            (hunk, "old_text", secret.decode()),
            (patch, "path", f"notes/{secret.decode()}.md"),
            (change_set, "operations", [secret]),
        )
        for target, attribute, value in corruptions:
            fresh_hunk = ArtifactTextHunk(_sha256(before), 0, 4, before, b"after")
            fresh_patch = _patch_operation(_MANUSCRIPT, before, b"after", (fresh_hunk,))
            fresh_change_set = build_artifact_change_set(
                {_MANUSCRIPT: before}, (fresh_patch,)
            )
            selected = {
                ArtifactTextHunk: fresh_hunk,
                ArtifactPatchOperation: fresh_patch,
                ArtifactChangeSet: fresh_change_set,
            }[type(target)]
            object.__setattr__(selected, attribute, value)
            with self.subTest(target=type(target).__name__, attribute=attribute):
                with self.assertRaises(ArtifactChangeSetError) as raised:
                    serialize_artifact_change_set(fresh_change_set)
                self.assert_content_free_graph(
                    raised.exception,
                    (secret.decode(), f"notes/{secret.decode()}.md"),
                )
                self.assertIsNone(raised.exception.__cause__)
                self.assertIsNone(raised.exception.__context__)

    def test_review_serializer_explicitly_contains_text_while_repr_does_not(
        self,
    ) -> None:
        review_text = "VISIBLE-ONLY-IN-EXPLICIT-REVIEW-JSON"
        content = review_text.encode()
        operation = ArtifactCreateOperation(
            _MANUSCRIPT,
            ArtifactKind.MANUSCRIPT,
            content,
            _sha256(content),
        )
        change_set = build_artifact_change_set({}, (operation,))
        self.assertIn(review_text.encode(), serialize_artifact_change_set(change_set))
        self.assertNotIn(review_text, repr(operation))
        self.assertNotIn(review_text, repr(change_set))

    def test_contract_has_no_authority_filesystem_provider_or_git_capability(
        self,
    ) -> None:
        forbidden_fragments = (
            "agent",
            "authority",
            "authorization",
            "capability",
            "commit",
            "conversation",
            "filesystem",
            "git",
            "provider",
            "relationship",
            "repository",
            "session",
            "workspace",
        )
        public_types = (
            ArtifactTextHunk,
            ArtifactCreateOperation,
            ArtifactPatchOperation,
            ArtifactReplaceOperation,
            ArtifactRenameOperation,
            ArtifactDeleteOperation,
            ArtifactChangeSet,
        )
        for value_type in public_types:
            names = tuple(field.name.lower() for field in fields(value_type))
            for fragment in forbidden_fragments:
                self.assertFalse(
                    any(fragment in name for name in names),
                    (value_type.__name__, fragment, names),
                )
        source_path = Path(change_sets_module.__file__)
        tree = ast.parse(
            source_path.read_text(encoding="utf-8"), filename=str(source_path)
        )
        relative_imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level
        }
        absolute_roots = {
            alias.name.split(".", maxsplit=1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            (node.module or "").split(".", maxsplit=1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and not node.level
        }
        self.assertEqual(relative_imports, {"catalog"})
        self.assertEqual(
            absolute_roots,
            {
                "__future__",
                "collections",
                "dataclasses",
                "enum",
                "hashlib",
                "json",
                "re",
                "types",
                "typing",
            },
        )

    def test_clean_direct_and_facade_imports_require_no_optional_collaborator(
        self,
    ) -> None:
        source_root = Path(change_sets_module.__file__).parents[2]
        script = """
import sys
sys.path.insert(0, sys.argv[1])
import story_writing_agents
from story_writing_agents import writing_artifacts
from story_writing_agents.writing_artifacts import change_sets
assert writing_artifacts.ArtifactChangeSet is change_sets.ArtifactChangeSet
assert not hasattr(story_writing_agents, "ArtifactChangeSet")
assert tuple(change_sets.__all__) == tuple(sys.argv[2].split(","))
"""
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                script,
                str(source_root),
                ",".join(_MODULE_EXPORTS),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_pure_projection_does_not_mutate_inputs_or_workspace_files(self) -> None:
        base, change_set, _, _ = _golden_fixture()
        base_snapshot = dict(base)
        operations_snapshot = tuple(change_set.operations)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "core_documents" / "global_outline.md"
            artifact.parent.mkdir()
            artifact.write_bytes(b"filesystem sentinel")
            before_files = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            project_artifact_change_set(base, change_set)
            serialize_artifact_change_set(change_set)
            deserialize_artifact_change_set(serialize_artifact_change_set(change_set))
            after_files = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
        self.assertEqual(base, base_snapshot)
        self.assertEqual(change_set.operations, operations_snapshot)
        self.assertEqual(after_files, before_files)


if __name__ == "__main__":
    unittest.main()
