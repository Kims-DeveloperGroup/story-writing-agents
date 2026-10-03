"""Focused pure artifact-application contract verification for Issue #518."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import subprocess
import sys
import unittest
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import story_writing_agents
from story_writing_agents import writing_artifacts
from story_writing_agents.writing_artifacts import (
    ARTIFACT_APPLICATION_SCHEMA_VERSION,
    ArtifactApplicationError,
    ArtifactApplicationErrorCode,
    ArtifactApplicationFinalArtifact,
    ArtifactApplicationProvenance,
    ArtifactApplicationRequest,
    ArtifactApplicationResult,
    ArtifactApplicationState,
    ArtifactCreateOperation,
    ArtifactDeleteOperation,
    ArtifactDeletionProvenance,
    ArtifactKind,
    ArtifactPatchOperation,
    ArtifactRenameLineage,
    ArtifactRenameOperation,
    ArtifactReplaceOperation,
    ArtifactTextHunk,
    build_artifact_change_set,
    deserialize_artifact_application_request,
    deserialize_artifact_application_result,
    serialize_artifact_application_request,
    serialize_artifact_application_result,
)
from story_writing_agents.writing_artifacts import (
    application_contracts as application_module,
)
from story_writing_agents.writing_artifacts import serialize_artifact_change_set


_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_ROOT = _ROOT / "src" / "story_writing_agents"
_CORE_CREATE = "core_documents/character_sheets.md"
_CORE_PATCH = "core_documents/global_outline.md"
_MANUSCRIPT_MOVE = "manuscript/part-01/move.md"
_MANUSCRIPT_MOVED = "manuscript/part-01/moved.md"
_MANUSCRIPT_REPLACE = "manuscript/part-01/opening.md"
_MANUSCRIPT_DELETE = "manuscript/part-01/retired.md"
_CANDIDATE_DIGEST = "sha256:" + "a" * 64
_RELATIONSHIP_DIGEST = "sha256:" + "b" * 64
_ERROR_MESSAGES = {
    ArtifactApplicationErrorCode.INVALID_SCHEMA: (
        "artifact application schema is invalid"
    ),
    ArtifactApplicationErrorCode.INVALID_SERIALIZATION: (
        "artifact application serialization is noncanonical or invalid"
    ),
    ArtifactApplicationErrorCode.INVALID_UTF8: (
        "artifact application serialization is not strict UTF-8"
    ),
    ArtifactApplicationErrorCode.INVALID_IDENTIFIER: (
        "artifact application identity is invalid"
    ),
    ArtifactApplicationErrorCode.INVALID_DIGEST: (
        "artifact application digest is invalid"
    ),
    ArtifactApplicationErrorCode.INVALID_PATH: (
        "artifact application path is invalid"
    ),
    ArtifactApplicationErrorCode.CORRELATION_MISMATCH: (
        "artifact application correlation does not match"
    ),
    ArtifactApplicationErrorCode.INVALID_RESULT: (
        "artifact application result is invalid"
    ),
    ArtifactApplicationErrorCode.DRIFT: (
        "artifact application authority has drifted"
    ),
    ArtifactApplicationErrorCode.CORRUPTION: (
        "artifact application state is corrupt"
    ),
    ArtifactApplicationErrorCode.RECOVERY_REQUIRED: (
        "artifact application recovery is required"
    ),
}
_MODULE_EXPORTS = (
    "ARTIFACT_APPLICATION_SCHEMA_VERSION",
    "ArtifactApplicationError",
    "ArtifactApplicationErrorCode",
    "ArtifactApplicationFinalArtifact",
    "ArtifactApplicationProvenance",
    "ArtifactApplicationRequest",
    "ArtifactApplicationResult",
    "ArtifactApplicationState",
    "artifact_relationship_validation_digest",
    "build_artifact_application_request",
    "ArtifactDeletionProvenance",
    "ArtifactRenameLineage",
    "deserialize_artifact_application_request",
    "deserialize_artifact_application_result",
    "serialize_artifact_application_request",
    "serialize_artifact_application_result",
)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _fixture():
    core_before = b"outline-before"
    core_after = b"outline-after"
    replace_before = b"opening-before"
    replace_after = b"opening-after"
    moved_content = b"move-content"
    deleted_content = b"retired-content"
    created_content = b"character-content"
    base = {
        _CORE_PATCH: core_before,
        _MANUSCRIPT_MOVE: moved_content,
        _MANUSCRIPT_REPLACE: replace_before,
        _MANUSCRIPT_DELETE: deleted_content,
    }
    core_digest = _sha256(core_before)
    operations = (
        ArtifactCreateOperation(
            _CORE_CREATE,
            ArtifactKind.CORE_DOCUMENT,
            created_content,
            _sha256(created_content),
        ),
        ArtifactPatchOperation(
            _CORE_PATCH,
            ArtifactKind.CORE_DOCUMENT,
            core_digest,
            (
                ArtifactTextHunk(
                    core_digest,
                    0,
                    len(core_before),
                    core_before,
                    core_after,
                ),
            ),
            _sha256(core_after),
        ),
        ArtifactRenameOperation(
            _MANUSCRIPT_MOVE,
            _MANUSCRIPT_MOVED,
            ArtifactKind.MANUSCRIPT,
            _sha256(moved_content),
            _sha256(moved_content),
        ),
        ArtifactReplaceOperation(
            _MANUSCRIPT_REPLACE,
            ArtifactKind.MANUSCRIPT,
            _sha256(replace_before),
            replace_after,
            _sha256(replace_after),
        ),
        ArtifactDeleteOperation(
            _MANUSCRIPT_DELETE,
            ArtifactKind.MANUSCRIPT,
            _sha256(deleted_content),
        ),
    )
    change_set = build_artifact_change_set(base, operations)
    provenance = ArtifactApplicationProvenance(
        application_id="application-1",
        candidate_id="candidate-1",
        candidate_revision=2,
        candidate_digest=_CANDIDATE_DIGEST,
        change_set_digest=change_set.change_set_digest,
        relationship_validation_digest=_RELATIONSHIP_DIGEST,
    )
    request = ArtifactApplicationRequest(
        ARTIFACT_APPLICATION_SCHEMA_VERSION,
        provenance,
        change_set,
        (_CORE_PATCH,),
    )
    affected = tuple(
        sorted(
            (
                _CORE_CREATE,
                _CORE_PATCH,
                _MANUSCRIPT_MOVE,
                _MANUSCRIPT_MOVED,
                _MANUSCRIPT_REPLACE,
                _MANUSCRIPT_DELETE,
            ),
            key=lambda path: path.encode("utf-8"),
        )
    )
    finals = (
        ArtifactApplicationFinalArtifact(
            _CORE_CREATE,
            ArtifactKind.CORE_DOCUMENT,
            _sha256(created_content),
        ),
        ArtifactApplicationFinalArtifact(
            _CORE_PATCH,
            ArtifactKind.CORE_DOCUMENT,
            _sha256(core_after),
        ),
        ArtifactApplicationFinalArtifact(
            _MANUSCRIPT_MOVED,
            ArtifactKind.MANUSCRIPT,
            _sha256(moved_content),
        ),
        ArtifactApplicationFinalArtifact(
            _MANUSCRIPT_REPLACE,
            ArtifactKind.MANUSCRIPT,
            _sha256(replace_after),
        ),
    )
    lineage = (
        ArtifactRenameLineage(
            _MANUSCRIPT_MOVE,
            _MANUSCRIPT_MOVED,
            _sha256(moved_content),
        ),
    )
    deletion = (
        ArtifactDeletionProvenance(
            _MANUSCRIPT_DELETE,
            _sha256(deleted_content),
        ),
    )
    result = ArtifactApplicationResult(
        ARTIFACT_APPLICATION_SCHEMA_VERSION,
        provenance,
        ArtifactApplicationState.APPLIED,
        affected,
        finals,
        lineage,
        deletion,
    )
    return request, result


class ApplicationContractTestCase(unittest.TestCase):
    def assert_application_error(
        self,
        code: ArtifactApplicationErrorCode,
        callback,
    ) -> ArtifactApplicationError:
        with self.assertRaises(ArtifactApplicationError) as raised:
            callback()
        error = raised.exception
        self.assertIs(error.code, code)
        self.assertEqual(str(error), _ERROR_MESSAGES[code])
        self.assertEqual(error.args, (_ERROR_MESSAGES[code],))
        return error


class ArtifactApplicationSurfaceTests(ApplicationContractTestCase):
    def test_exact_public_surface_schemas_enums_signatures_and_facade(self) -> None:
        self.assertEqual(ARTIFACT_APPLICATION_SCHEMA_VERSION, 1)
        self.assertEqual(tuple(application_module.__all__), _MODULE_EXPORTS)
        self.assertEqual(len(writing_artifacts.__all__), 78)
        self.assertEqual(len(set(writing_artifacts.__all__)), 78)
        for name in _MODULE_EXPORTS:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(writing_artifacts, name),
                    getattr(application_module, name),
                )
                self.assertEqual(writing_artifacts.__all__.count(name), 1)
                self.assertNotIn(name, story_writing_agents.__all__)
                self.assertFalse(hasattr(story_writing_agents, name))
        self.assertEqual(len(story_writing_agents.__all__), 106)
        self.assertEqual(
            tuple(state.value for state in ArtifactApplicationState),
            ("applied", "unchanged"),
        )
        self.assertEqual(
            tuple(code.value for code in ArtifactApplicationErrorCode),
            tuple(code.value for code in _ERROR_MESSAGES),
        )
        expected_fields = {
            ArtifactApplicationProvenance: (
                "application_id",
                "candidate_id",
                "candidate_revision",
                "candidate_digest",
                "change_set_digest",
                "relationship_validation_digest",
            ),
            ArtifactApplicationRequest: (
                "schema_version",
                "provenance",
                "change_set",
                "attached_core_paths",
            ),
            ArtifactApplicationFinalArtifact: (
                "path",
                "artifact_kind",
                "sha256",
            ),
            ArtifactRenameLineage: (
                "source_path",
                "destination_path",
                "sha256",
            ),
            ArtifactDeletionProvenance: ("path", "prior_sha256"),
            ArtifactApplicationResult: (
                "schema_version",
                "provenance",
                "state",
                "affected_paths",
                "final_artifacts",
                "rename_lineage",
                "deletion_provenance",
            ),
        }
        request, result = _fixture()
        values = (
            request.provenance,
            request,
            *result.final_artifacts,
            *result.rename_lineage,
            *result.deletion_provenance,
            result,
        )
        for value_type, expected in expected_fields.items():
            with self.subTest(value_type=value_type.__name__):
                self.assertEqual(tuple(field.name for field in fields(value_type)), expected)
                self.assertEqual(tuple(inspect.signature(value_type).parameters), expected)
                self.assertTrue(value_type.__dataclass_params__.frozen)
                self.assertIn("__slots__", value_type.__dict__)
                value = next(item for item in values if type(item) is value_type)
                self.assertFalse(hasattr(value, "__dict__"))
                with self.assertRaises((FrozenInstanceError, AttributeError, TypeError)):
                    setattr(value, expected[0], getattr(value, expected[0]))
        signatures = {
            serialize_artifact_application_request: ("request",),
            deserialize_artifact_application_request: ("data",),
            serialize_artifact_application_result: ("result",),
            deserialize_artifact_application_result: ("data",),
        }
        for callable_, expected in signatures.items():
            self.assertEqual(tuple(inspect.signature(callable_).parameters), expected)


class ArtifactApplicationRequestTests(ApplicationContractTestCase):
    def test_request_represents_all_five_operations_and_exact_review_correlation(
        self,
    ) -> None:
        request, _ = _fixture()
        self.assertEqual(
            tuple(operation.operation_kind.value for operation in request.change_set.operations),
            ("create", "patch", "rename", "replace", "delete"),
        )
        self.assertEqual(request.application_id, "application-1")
        self.assertEqual(request.candidate_id, "candidate-1")
        self.assertEqual(request.candidate_revision, 2)
        self.assertEqual(request.candidate_digest, _CANDIDATE_DIGEST)
        self.assertEqual(
            request.change_set_digest,
            request.change_set.change_set_digest,
        )
        self.assertEqual(
            request.relationship_validation_digest,
            _RELATIONSHIP_DIGEST,
        )
        self.assertEqual(request.attached_core_paths, (_CORE_PATCH,))

    def test_request_rejects_invalid_values_and_change_set_correlation_drift(
        self,
    ) -> None:
        request, _ = _fixture()
        values = {
            "application_id": "application-1",
            "candidate_id": "candidate-1",
            "candidate_revision": 2,
            "candidate_digest": _CANDIDATE_DIGEST,
            "change_set_digest": request.change_set.change_set_digest,
            "relationship_validation_digest": _RELATIONSHIP_DIGEST,
        }
        invalid = (
            ("application_id", "", ArtifactApplicationErrorCode.INVALID_IDENTIFIER),
            ("candidate_id", "bad id", ArtifactApplicationErrorCode.INVALID_IDENTIFIER),
            ("candidate_revision", 0, ArtifactApplicationErrorCode.INVALID_IDENTIFIER),
            ("candidate_revision", True, ArtifactApplicationErrorCode.INVALID_IDENTIFIER),
            ("candidate_digest", "a" * 64, ArtifactApplicationErrorCode.INVALID_DIGEST),
            ("change_set_digest", "A" * 64, ArtifactApplicationErrorCode.INVALID_DIGEST),
            (
                "relationship_validation_digest",
                "sha256:" + "B" * 64,
                ArtifactApplicationErrorCode.INVALID_DIGEST,
            ),
        )
        for name, value, code in invalid:
            changed = dict(values)
            changed[name] = value
            with self.subTest(name=name, value=value):
                self.assert_application_error(
                    code,
                    lambda changed=changed: ArtifactApplicationProvenance(**changed),
                )
        drift = ArtifactApplicationProvenance(
            **{**values, "change_set_digest": "0" * 64}
        )
        self.assert_application_error(
            ArtifactApplicationErrorCode.CORRELATION_MISMATCH,
            lambda: ArtifactApplicationRequest(
                1,
                drift,
                request.change_set,
                request.attached_core_paths,
            ),
        )
        self.assert_application_error(
            ArtifactApplicationErrorCode.INVALID_SCHEMA,
            lambda: ArtifactApplicationRequest(
                True,
                request.provenance,
                request.change_set,
                request.attached_core_paths,
            ),
        )
        invalid_attached_paths = (
            ([_CORE_PATCH], ArtifactApplicationErrorCode.INVALID_SCHEMA),
            ((_MANUSCRIPT_REPLACE,), ArtifactApplicationErrorCode.INVALID_PATH),
            ((_CORE_PATCH, _CORE_PATCH), ArtifactApplicationErrorCode.INVALID_PATH),
            (
                ("core_documents/narrative_rules.md", _CORE_PATCH),
                ArtifactApplicationErrorCode.INVALID_PATH,
            ),
        )
        for attached_paths, code in invalid_attached_paths:
            with self.subTest(attached_paths=attached_paths):
                self.assert_application_error(
                    code,
                    lambda attached_paths=attached_paths: ArtifactApplicationRequest(
                        1,
                        request.provenance,
                        request.change_set,
                        attached_paths,
                    ),
                )


class ArtifactApplicationResultTests(ApplicationContractTestCase):
    def test_applied_result_reports_exact_final_identities_rename_lineage_and_delete_provenance(
        self,
    ) -> None:
        _, result = _fixture()
        self.assertIs(result.state, ArtifactApplicationState.APPLIED)
        self.assertEqual(result.applied_paths, result.affected_paths)
        self.assertEqual(result.unchanged_paths, ())
        self.assertEqual(
            tuple(item.path for item in result.final_artifacts),
            (_CORE_CREATE, _CORE_PATCH, _MANUSCRIPT_MOVED, _MANUSCRIPT_REPLACE),
        )
        self.assertEqual(
            result.rename_lineage,
            (
                ArtifactRenameLineage(
                    _MANUSCRIPT_MOVE,
                    _MANUSCRIPT_MOVED,
                    result.final_artifacts[2].sha256,
                ),
            ),
        )
        self.assertEqual(result.deletion_provenance[0].path, _MANUSCRIPT_DELETE)
        self.assertRegex(result.deletion_provenance[0].prior_sha256, r"[0-9a-f]{64}")

    def test_unchanged_result_is_explicit_and_rejects_inconsistent_mutation_facts(
        self,
    ) -> None:
        _, applied = _fixture()
        unchanged = ArtifactApplicationResult(
            applied.schema_version,
            applied.provenance,
            ArtifactApplicationState.UNCHANGED,
            applied.affected_paths,
            applied.final_artifacts,
            applied.rename_lineage,
            applied.deletion_provenance,
        )
        self.assertEqual(unchanged.applied_paths, ())
        self.assertEqual(unchanged.unchanged_paths, unchanged.affected_paths)
        self.assertEqual(unchanged.final_artifacts, applied.final_artifacts)
        self.assertEqual(unchanged.rename_lineage, applied.rename_lineage)
        self.assertEqual(unchanged.deletion_provenance, applied.deletion_provenance)
        self.assert_application_error(
            ArtifactApplicationErrorCode.INVALID_RESULT,
            lambda: ArtifactApplicationResult(
                unchanged.schema_version,
                unchanged.provenance,
                unchanged.state,
                unchanged.affected_paths[:-1],
                unchanged.final_artifacts,
                unchanged.rename_lineage,
                unchanged.deletion_provenance,
            ),
        )
        self.assert_application_error(
            ArtifactApplicationErrorCode.INVALID_RESULT,
            lambda: ArtifactRenameLineage(
                "core_documents/global_outline.md",
                "core_documents/narrative_rules.md",
                "c" * 64,
            ),
        )


class ArtifactApplicationCodecTests(ApplicationContractTestCase):
    def test_request_and_result_have_exact_golden_canonical_byte_round_trips(
        self,
    ) -> None:
        request, result = _fixture()
        expected_request = _canonical_json(
            {
                "attached_core_paths": list(request.attached_core_paths),
                "change_set": json.loads(serialize_artifact_change_set(request.change_set)),
                "provenance": {
                    "application_id": request.application_id,
                    "candidate_digest": request.candidate_digest,
                    "candidate_id": request.candidate_id,
                    "candidate_revision": request.candidate_revision,
                    "change_set_digest": request.change_set_digest,
                    "relationship_validation_digest": (
                        request.relationship_validation_digest
                    ),
                },
                "schema_version": 1,
            }
        )
        expected_result = _canonical_json(
            {
                "affected_paths": list(result.affected_paths),
                "deletion_provenance": [
                    {
                        "path": item.path,
                        "prior_sha256": item.prior_sha256,
                    }
                    for item in result.deletion_provenance
                ],
                "final_artifacts": [
                    {
                        "artifact_kind": item.artifact_kind.value,
                        "path": item.path,
                        "sha256": item.sha256,
                    }
                    for item in result.final_artifacts
                ],
                "provenance": {
                    "application_id": result.application_id,
                    "candidate_digest": result.candidate_digest,
                    "candidate_id": result.candidate_id,
                    "candidate_revision": result.candidate_revision,
                    "change_set_digest": result.change_set_digest,
                    "relationship_validation_digest": (
                        result.relationship_validation_digest
                    ),
                },
                "rename_lineage": [
                    {
                        "destination_path": item.destination_path,
                        "sha256": item.sha256,
                        "source_path": item.source_path,
                    }
                    for item in result.rename_lineage
                ],
                "schema_version": 1,
                "state": "applied",
            }
        )
        encoded_request = serialize_artifact_application_request(request)
        encoded_result = serialize_artifact_application_result(result)
        self.assertEqual(encoded_request, expected_request)
        self.assertEqual(encoded_result, expected_result)
        self.assertFalse(encoded_request.endswith(b"\n"))
        self.assertFalse(encoded_result.endswith(b"\n"))
        self.assertEqual(deserialize_artifact_application_request(encoded_request), request)
        self.assertEqual(deserialize_artifact_application_result(encoded_result), result)
        self.assertEqual(
            serialize_artifact_application_request(
                deserialize_artifact_application_request(encoded_request)
            ),
            encoded_request,
        )
        self.assertEqual(
            serialize_artifact_application_result(
                deserialize_artifact_application_result(encoded_result)
            ),
            encoded_result,
        )

    def test_decoders_reject_unknown_missing_duplicate_and_noncanonical_input(
        self,
    ) -> None:
        request, result = _fixture()
        request_bytes = serialize_artifact_application_request(request)
        result_bytes = serialize_artifact_application_result(result)
        for decoder, canonical in (
            (deserialize_artifact_application_request, request_bytes),
            (deserialize_artifact_application_result, result_bytes),
        ):
            for value in (canonical.decode(), bytearray(canonical), memoryview(canonical)):
                with self.subTest(decoder=decoder.__name__, value=type(value).__name__):
                    self.assert_application_error(
                        ArtifactApplicationErrorCode.INVALID_SERIALIZATION,
                        lambda decoder=decoder, value=value: decoder(value),
                    )
            alternates = (
                b" " + canonical,
                canonical + b"\n",
                json.dumps(json.loads(canonical), sort_keys=True).encode("utf-8"),
            )
            for value in alternates:
                with self.subTest(decoder=decoder.__name__, value=value[:16]):
                    self.assert_application_error(
                        ArtifactApplicationErrorCode.INVALID_SERIALIZATION,
                        lambda decoder=decoder, value=value: decoder(value),
                    )
            raw = json.loads(canonical)
            unknown = dict(raw)
            unknown["unknown"] = 1
            missing = dict(raw)
            del missing[next(iter(missing))]
            for value in (unknown, missing):
                self.assert_application_error(
                    ArtifactApplicationErrorCode.INVALID_SERIALIZATION,
                    lambda decoder=decoder, value=value: decoder(_canonical_json(value)),
                )
        duplicates = (
            b'{"schema_version":1,"schema_version":1}',
            request_bytes.replace(
                b'"candidate_id":"candidate-1"',
                b'"candidate_id":"candidate-1","candidate_id":"candidate-1"',
                1,
            ),
        )
        for value in duplicates:
            self.assert_application_error(
                ArtifactApplicationErrorCode.INVALID_SERIALIZATION,
                lambda value=value: deserialize_artifact_application_request(value),
            )
        legacy_request = json.loads(request_bytes)
        del legacy_request["attached_core_paths"]
        self.assert_application_error(
            ArtifactApplicationErrorCode.INVALID_SERIALIZATION,
            lambda: deserialize_artifact_application_request(
                _canonical_json(legacy_request)
            ),
        )
        self.assert_application_error(
            ArtifactApplicationErrorCode.INVALID_UTF8,
            lambda: deserialize_artifact_application_request(b"\xff"),
        )
        for value in (b"not-json", b'{"x":NaN}', b'{"x":Infinity}'):
            self.assert_application_error(
                ArtifactApplicationErrorCode.INVALID_SERIALIZATION,
                lambda value=value: deserialize_artifact_application_result(value),
            )

    def test_decoders_reject_embedded_correlation_and_payload_digest_drift(
        self,
    ) -> None:
        request, result = _fixture()
        raw_request = json.loads(serialize_artifact_application_request(request))
        raw_request["provenance"]["change_set_digest"] = "0" * 64
        self.assert_application_error(
            ArtifactApplicationErrorCode.CORRELATION_MISMATCH,
            lambda: deserialize_artifact_application_request(
                _canonical_json(raw_request)
            ),
        )
        raw_request = json.loads(serialize_artifact_application_request(request))
        raw_request["change_set"]["change_set_digest"] = "0" * 64
        self.assert_application_error(
            ArtifactApplicationErrorCode.INVALID_SERIALIZATION,
            lambda: deserialize_artifact_application_request(
                _canonical_json(raw_request)
            ),
        )
        raw_request = json.loads(serialize_artifact_application_request(request))
        raw_request["provenance"]["candidate_digest"] = "sha256:" + "A" * 64
        self.assert_application_error(
            ArtifactApplicationErrorCode.INVALID_DIGEST,
            lambda: deserialize_artifact_application_request(
                _canonical_json(raw_request)
            ),
        )
        raw_result = json.loads(serialize_artifact_application_result(result))
        raw_result["final_artifacts"][2]["sha256"] = "0" * 64
        self.assert_application_error(
            ArtifactApplicationErrorCode.INVALID_RESULT,
            lambda: deserialize_artifact_application_result(_canonical_json(raw_result)),
        )
        raw_result = json.loads(serialize_artifact_application_result(result))
        raw_result["deletion_provenance"][0]["prior_sha256"] = "A" * 64
        self.assert_application_error(
            ArtifactApplicationErrorCode.INVALID_DIGEST,
            lambda: deserialize_artifact_application_result(_canonical_json(raw_result)),
        )


class ArtifactApplicationPrivacyTests(ApplicationContractTestCase):
    def test_repr_errors_exception_graph_and_logs_are_content_free(self) -> None:
        secret = "UNIQUE-APPLICATION-STORY-SECRET"
        content = secret.encode()
        operation = ArtifactCreateOperation(
            _MANUSCRIPT_REPLACE,
            ArtifactKind.MANUSCRIPT,
            content,
            _sha256(content),
        )
        change_set = build_artifact_change_set({}, (operation,))
        provenance = ArtifactApplicationProvenance(
            "application-secret",
            "candidate-secret",
            1,
            _CANDIDATE_DIGEST,
            change_set.change_set_digest,
            _RELATIONSHIP_DIGEST,
        )
        request = ArtifactApplicationRequest(1, provenance, change_set, ())
        self.assertNotIn(secret, repr(request))
        self.assertIn(content, serialize_artifact_application_request(request))
        with self.assertNoLogs():
            restored = deserialize_artifact_application_request(
                serialize_artifact_application_request(request)
            )
        self.assertEqual(restored, request)
        with self.assertRaises(ArtifactApplicationError) as raised:
            deserialize_artifact_application_request(secret.encode())
        pending = [raised.exception]
        seen: set[int] = set()
        rendered: list[str] = []
        while pending:
            error = pending.pop()
            if id(error) in seen:
                continue
            seen.add(id(error))
            rendered.extend((str(error), repr(error), repr(error.args)))
            if hasattr(error, "__dict__"):
                rendered.append(repr(vars(error)))
            if error.__cause__ is not None:
                pending.append(error.__cause__)
            if error.__context__ is not None:
                pending.append(error.__context__)
        self.assertNotIn(secret, "\n".join(rendered))


class ArtifactApplicationBoundaryTests(ApplicationContractTestCase):
    def test_module_imports_only_catalog_change_sets_and_standard_library(self) -> None:
        source_path = Path(application_module.__file__)
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
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
        self.assertEqual(
            relative_imports,
            {"catalog", "change_sets", "relationship_policy"},
        )
        self.assertEqual(
            absolute_roots,
            {
                "__future__",
                "dataclasses",
                "enum",
                "hashlib",
                "json",
                "re",
                "typing",
            },
        )
        forbidden = {
            "agents",
            "authorization",
            "backend",
            "conversation_retrieval",
            "git",
            "orchestrator",
            "provider_adapter",
            "repository",
            "runtime_references",
            "workspace",
        }
        self.assertTrue(relative_imports.isdisjoint(forbidden))

    def test_clean_direct_and_facade_imports_need_no_optional_collaborator(
        self,
    ) -> None:
        source_root = Path(application_module.__file__).parents[2]
        script = """
import sys
sys.path.insert(0, sys.argv[1])
for name in ('generic_rag', 'jsonl_viewer', 'openai', 'anthropic'):
    sys.modules[name] = None
import story_writing_agents
from story_writing_agents import writing_artifacts
from story_writing_agents.writing_artifacts import application_contracts
assert writing_artifacts.ArtifactApplicationRequest is application_contracts.ArtifactApplicationRequest
assert not hasattr(story_writing_agents, 'ArtifactApplicationRequest')
assert tuple(application_contracts.__all__) == tuple(sys.argv[2].split(','))
assert len(writing_artifacts.__all__) == len(set(writing_artifacts.__all__)) == 78
"""
        completed = subprocess.run(
            (
                sys.executable,
                "-I",
                "-c",
                script,
                str(source_root),
                ",".join(_MODULE_EXPORTS),
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_module_graph_matches_accepted_delta(self) -> None:
        modules: dict[str, Path] = {}
        for path in _SOURCE_ROOT.rglob("*.py"):
            relative = path.relative_to(_SOURCE_ROOT)
            parts = (
                ("story_writing_agents", *relative.parts[:-1])
                if relative.name == "__init__.py"
                else ("story_writing_agents", *relative.with_suffix("").parts)
            )
            modules[".".join(parts)] = path

        def resolve(target: str) -> str | None:
            parts = target.split(".")
            while parts and ".".join(parts) not in modules:
                parts.pop()
            candidate = ".".join(parts)
            return candidate if candidate in modules else None

        def runtime_nodes(node: ast.AST):
            yield node
            if isinstance(node, ast.If) and (
                isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
                or isinstance(node.test, ast.Attribute) and node.test.attr == "TYPE_CHECKING"
            ):
                for child in node.orelse:
                    yield from runtime_nodes(child)
                return
            for child in ast.iter_child_nodes(node):
                yield from runtime_nodes(child)

        edges: set[tuple[str, str]] = set()
        for module, path in modules.items():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            context = (
                module.split(".")
                if path.name == "__init__.py"
                else module.split(".")[:-1]
            )
            for node in runtime_nodes(tree):
                targets: list[str] = []
                if isinstance(node, ast.Import):
                    targets.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        base = context[: len(context) - (node.level - 1)]
                        if node.module:
                            base.extend(node.module.split("."))
                        target = ".".join(base)
                    else:
                        target = node.module or ""
                    targets.append(target)
                    targets.extend(
                        f"{target}.{alias.name}" if target else alias.name
                        for alias in node.names
                    )
                for target in targets:
                    resolved = resolve(target)
                    if resolved is not None and resolved != module:
                        edges.add((module, resolved))
        self.assertEqual(len(modules), 149)
        self.assertEqual(len(edges), 628)
        application_name = (
            "story_writing_agents.writing_artifacts.application_contracts"
        )
        facade_name = "story_writing_agents.writing_artifacts"
        self.assertEqual(
            {target for source, target in edges if source == application_name},
            {
                "story_writing_agents.writing_artifacts.catalog",
                "story_writing_agents.writing_artifacts.change_sets",
                "story_writing_agents.writing_artifacts.relationship_policy",
            },
        )
        self.assertIn((facade_name, application_name), edges)
        successors = {module: set() for module in modules}
        for source, target in edges:
            successors[source].add(target)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(module: str) -> None:
            if module in visiting:
                self.fail(f"internal import cycle reaches {module}")
            if module in visited:
                return
            visiting.add(module)
            for dependency in successors[module]:
                visit(dependency)
            visiting.remove(module)
            visited.add(module)

        for module in modules:
            visit(module)


if __name__ == "__main__":
    unittest.main()
