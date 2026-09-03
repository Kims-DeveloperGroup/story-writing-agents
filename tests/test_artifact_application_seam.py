"""Focused repository-seam and stale-evidence tests for artifact application."""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
import unittest
from unittest import mock

from story_writing_agents.writing_artifacts import (
    ArtifactApplicationError,
    ArtifactApplicationErrorCode,
    ArtifactApplicationRequest,
    ArtifactApplicationState,
    ArtifactCreateOperation,
    ArtifactDeleteOperation,
    ArtifactKind,
    ArtifactPatchOperation,
    ArtifactReference,
    ArtifactReferenceBatch,
    ArtifactRenameOperation,
    ArtifactReplaceOperation,
    ArtifactTextHunk,
    ReferenceAuthorizationError,
    ReferenceAuthorizationErrorCode,
    ReferenceOperationState,
)
from story_writing_agents.writing_artifacts import (
    _application_store as application_store_module,
)
from story_writing_agents.writing_artifacts.relationship_policy import (
    validate_artifact_relationships,
)
from story_writing_agents.writing_artifacts.repository import (
    WritingArtifactRepository,
)
from story_writing_agents.writing_artifacts import repository as repository_module

from tests.test_artifact_application_store_lifecycle import (
    _ApplicationStoreTestCase,
    _CORE_PATCH,
    _apply,
    _current_state,
    _five_operation_request,
    _request,
    _seed_five_operation_state,
    _sha256,
)


_ERROR_MESSAGES = {
    ArtifactApplicationErrorCode.INVALID_SCHEMA: (
        "artifact application schema is invalid"
    ),
    ArtifactApplicationErrorCode.CORRELATION_MISMATCH: (
        "artifact application correlation does not match"
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


class _RequestSubclass(ArtifactApplicationRequest):
    pass


class ArtifactApplicationSeamTests(_ApplicationStoreTestCase):
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
        return error

    def test_only_exact_revalidated_request_authority_reaches_the_repository_lock(
        self,
    ) -> None:
        root, repository = self.new_repository()
        _seed_five_operation_state(root)
        valid = _five_operation_request(repository)
        subclass = _RequestSubclass(
            valid.schema_version,
            valid.provenance,
            valid.change_set,
            valid.attached_core_paths,
        )
        tampered = _five_operation_request(
            repository,
            application_id="application-tampered",
        )
        object.__setattr__(tampered, "attached_core_paths", [_CORE_PATCH])

        with (
            mock.patch.object(
                repository,
                "_locked_repository",
                wraps=repository._locked_repository,
            ) as locked,
            mock.patch.object(
                repository._applications,
                "apply",
                wraps=repository._applications.apply,
            ) as delegated,
        ):
            for authority in (
                "replace the opening",
                {"request": "replace the opening"},
                [valid],
                subclass,
                tampered,
            ):
                with self.subTest(authority_type=type(authority).__name__):
                    self.assert_application_error(
                        ArtifactApplicationErrorCode.INVALID_SCHEMA,
                        lambda authority=authority: repository.apply_artifact_changes(
                            authority
                        ),
                    )
            self.assertEqual(locked.call_count, 0)
            self.assertEqual(delegated.call_count, 0)

    def test_one_lock_and_one_delegation_return_applied_then_unchanged(self) -> None:
        root, repository = self.new_repository()
        _seed_five_operation_state(root)
        request = _five_operation_request(repository)
        lock = repository._storage.exclusive_lock
        apply = repository._applications.apply

        with (
            mock.patch.object(
                repository._storage,
                "exclusive_lock",
                wraps=lock,
            ) as acquire,
            mock.patch.object(
                repository._applications,
                "apply",
                wraps=apply,
            ) as delegate,
        ):
            applied = repository.apply_artifact_changes(request)
            self.assertIs(applied.state, ArtifactApplicationState.APPLIED)
            self.assertEqual(acquire.call_count, 1)
            delegate.assert_called_once_with(request)

            acquire.reset_mock()
            delegate.reset_mock()
            unchanged = repository.apply_artifact_changes(request)
            self.assertIs(unchanged.state, ArtifactApplicationState.UNCHANGED)
            self.assertEqual(acquire.call_count, 1)
            delegate.assert_called_once_with(request)

        self.assertEqual(unchanged.affected_paths, applied.affected_paths)
        self.assertEqual(unchanged.final_artifacts, applied.final_artifacts)
        self.assertEqual(unchanged.rename_lineage, applied.rename_lineage)
        self.assertEqual(unchanged.deletion_provenance, applied.deletion_provenance)

    def test_attached_core_paths_change_relationship_digest_and_mismatch_fails_closed(
        self,
    ) -> None:
        root, repository = self.new_repository()
        path = "manuscript/part-01/attached.md"
        correct = _request(
            repository,
            lambda _base: (
                ArtifactCreateOperation(
                    path,
                    ArtifactKind.MANUSCRIPT,
                    b"attached review",
                    _sha256(b"attached review"),
                ),
            ),
            application_id="application-attached",
            attached_core_paths=(_CORE_PATCH,),
        )
        base = _current_state(repository)
        without_attached = validate_artifact_relationships(
            base,
            correct.change_set,
            attached_core_paths=(),
        )
        without_digest = application_store_module._relationship_digest(
            without_attached
        )
        self.assertNotEqual(
            without_digest,
            correct.relationship_validation_digest,
        )
        mismatched = ArtifactApplicationRequest(
            correct.schema_version,
            replace(
                correct.provenance,
                relationship_validation_digest=without_digest,
            ),
            correct.change_set,
            correct.attached_core_paths,
        )

        self.assert_application_error(
            ArtifactApplicationErrorCode.CORRELATION_MISMATCH,
            lambda: repository.apply_artifact_changes(mismatched),
        )
        self.assertFalse((root / path).exists())
        applied = repository.apply_artifact_changes(correct)
        self.assertIs(applied.state, ArtifactApplicationState.APPLIED)

    def test_application_changes_replace_rename_and_delete_captured_sources_as_drift(
        self,
    ) -> None:
        cases = ("change", "replace", "rename", "delete")
        for label in cases:
            with self.subTest(label=label):
                root, repository = self.new_repository()
                path = f"manuscript/part-01/{label}.md"
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                before = f"before-{label}".encode()
                target.write_bytes(before)
                reference = ArtifactReference(path)
                authorization = repository.capture_reference_authorization(
                    operation_id=f"reference-{label}",
                    batch=ArtifactReferenceBatch(
                        "review exact source",
                        "review exact source",
                        (reference,),
                    ),
                )
                assert authorization is not None
                captured = repository.inspect_reference_operation(
                    f"reference-{label}"
                )
                assert captured is not None

                def operations(base: dict[str, bytes]):
                    digest = _sha256(base[path])
                    if label == "change":
                        after = b"changed"
                        return (
                            ArtifactPatchOperation(
                                path,
                                ArtifactKind.MANUSCRIPT,
                                digest,
                                (
                                    ArtifactTextHunk(
                                        digest,
                                        0,
                                        len(before),
                                        before,
                                        after,
                                    ),
                                ),
                                _sha256(after),
                            ),
                        )
                    if label == "replace":
                        after = b"replaced"
                        return (
                            ArtifactReplaceOperation(
                                path,
                                ArtifactKind.MANUSCRIPT,
                                digest,
                                after,
                                _sha256(after),
                            ),
                        )
                    if label == "rename":
                        return (
                            ArtifactRenameOperation(
                                path,
                                "manuscript/part-01/renamed.md",
                                ArtifactKind.MANUSCRIPT,
                                digest,
                                digest,
                            ),
                        )
                    return (
                        ArtifactDeleteOperation(
                            path,
                            ArtifactKind.MANUSCRIPT,
                            digest,
                        ),
                    )

                result = _apply(
                    repository,
                    _request(
                        repository,
                        operations,
                        application_id=f"application-{label}",
                    ),
                )
                self.assertIs(result.state, ArtifactApplicationState.APPLIED)
                self.assertEqual(
                    repository.inspect_reference_operation(f"reference-{label}"),
                    captured,
                )
                with self.assertRaises(ReferenceAuthorizationError) as raised:
                    repository.revalidate_reference_operation(
                        authorization,
                        checkpoint="after_application",
                    )
                self.assertIs(
                    raised.exception.code,
                    ReferenceAuthorizationErrorCode.DRIFT,
                )
                retained = repository.inspect_reference_operation(
                    f"reference-{label}"
                )
                assert retained is not None
                self.assertEqual(
                    retained.operation_events[:-1],
                    captured.operation_events,
                )
                self.assertEqual(retained.invocation_events, captured.invocation_events)
                self.assertIs(
                    retained.operation_events[-1].operation_state,
                    ReferenceOperationState.TERMINATED,
                )
                self.assertIs(
                    retained.operation_events[-1].outcome,
                    ReferenceAuthorizationErrorCode.DRIFT,
                )

    def test_public_operational_failures_are_coded_content_safe_and_context_free(
        self,
    ) -> None:
        secret = "UNIQUE-APPLICATION-SEAM-SECRET"
        cases = (
            ArtifactApplicationErrorCode.DRIFT,
            ArtifactApplicationErrorCode.CORRUPTION,
            ArtifactApplicationErrorCode.RECOVERY_REQUIRED,
        )
        for code in cases:
            with self.subTest(code=code.value):
                root, repository = self.new_repository()
                path = "manuscript/part-01/failure.md"
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"before")
                request = _request(
                    repository,
                    lambda base: (
                        ArtifactReplaceOperation(
                            path,
                            ArtifactKind.MANUSCRIPT,
                            _sha256(base[path]),
                            b"after",
                            _sha256(b"after"),
                        ),
                    ),
                    application_id=f"application-{code.value}",
                )
                if code is ArtifactApplicationErrorCode.DRIFT:
                    target.write_bytes(b"outside change")
                elif code is ArtifactApplicationErrorCode.CORRUPTION:
                    state = self.state_root(root)
                    state.mkdir(parents=True, exist_ok=True)
                    state.joinpath("catalog.json").write_text(secret)
                else:
                    blocked = root / application_store_module.ARTIFACT_RECOVERY_BLOCK_PATH
                    blocked.parent.mkdir(parents=True, exist_ok=True)
                    blocked.write_text(secret)

                error = self.assert_application_error(
                    code,
                    lambda: repository.apply_artifact_changes(request),
                )
                self.assertIsNone(error.__cause__)
                self.assertIsNone(error.__context__)
                rendered = "\n".join((str(error), repr(error), repr(error.args), repr(vars(error))))
                self.assertNotIn(secret, rendered)


class ArtifactApplicationBoundaryTests(unittest.TestCase):
    def test_repository_has_one_application_seam_and_no_orchestrator_context_provider_confirmation_or_git_path(
        self,
    ) -> None:
        source_path = Path(repository_module.__file__)
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        repository_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "WritingArtifactRepository"
        )
        seams = [
            node
            for node in repository_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "apply_artifact_changes"
        ]
        self.assertEqual(len(seams), 1)
        seam = seams[0]
        delegated = [
            node
            for node in ast.walk(seam)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "apply"
        ]
        self.assertEqual(len(delegated), 1)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        forbidden = (
            "agent_context",
            "provider",
            "proposal",
            "confirmation",
            "lead_agent",
            "specialist",
            "session_checkpoint",
            ".git",
        )
        self.assertFalse(
            any(marker in name for name in imported for marker in forbidden)
        )
        self.assertFalse(
            any(
                isinstance(node, ast.FunctionDef)
                and ("reject" in node.name or "revise" in node.name)
                for node in repository_class.body
            )
        )


if __name__ == "__main__":
    unittest.main()
