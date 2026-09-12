"""Provider-free contracts for explicit writing-artifact authorization."""

from __future__ import annotations

import hashlib
import os
import pickle
import tempfile
import threading
import unittest
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from unittest import mock

import story_writing_agents
from story_writing_agents.agents import EXECUTION_SPECIALIST
from story_writing_agents.writing_artifacts import (
    MAX_REFERENCE_TOTAL_BYTES,
    MAX_REFERENCE_UTF8_BYTES,
    MAX_VIEWER_UTF8_BYTES,
    ArtifactContentSnapshot,
    ArtifactKind,
    ArtifactLineRange,
    ArtifactReference,
    ArtifactReferenceBatch,
    ArtifactSourceIdentity,
    AuthorizedArtifactReference,
    ReferenceAuthorizationError,
    ReferenceAuthorizationErrorCode,
    ReferenceEvidence,
    ReferenceInvocationCorrelation,
    ReferenceInvocationState,
    ReferenceOperationHistory,
    ReferenceOperationManifest,
    ReferenceOperationState,
)
from story_writing_agents import writing_artifacts as artifact_facade
from story_writing_agents.writing_artifacts._storage import SecureWorkspaceStorage
from story_writing_agents.writing_artifacts.authorization import (
    ReadCapability,
    ReferenceAuthorization,
    build_authorized_references,
    select_reference_bytes,
)
from story_writing_agents.writing_artifacts.repository import (
    WritingArtifactRepository,
)

from tests.helpers import initialize_test_workspace


_CORE_PATH = "core_documents/global_outline.md"
_MANUSCRIPT_PATH = "manuscript/part-01/seed.md"
_STATE = Path("agent_states/orchestrator_state/writing_artifacts")
_SNAPSHOTS = _STATE / "input_snapshots"
_ERROR_MESSAGES = {
    ReferenceAuthorizationErrorCode.INVALID_IDENTITY: "reference authorization identity is invalid",
    ReferenceAuthorizationErrorCode.NOT_FOUND: "referenced artifact is unavailable",
    ReferenceAuthorizationErrorCode.INELIGIBLE: "referenced artifact is not eligible",
    ReferenceAuthorizationErrorCode.DRIFT: "referenced artifact changed during the operation",
    ReferenceAuthorizationErrorCode.INVALID_UTF8: "referenced artifact is not strict UTF-8",
    ReferenceAuthorizationErrorCode.NUL_CONTENT: "referenced artifact contains NUL",
    ReferenceAuthorizationErrorCode.RANGE_OUT_OF_BOUNDS: "reference range is outside the artifact",
    ReferenceAuthorizationErrorCode.READ_LIMIT_EXCEEDED: "one reference exceeds the read limit",
    ReferenceAuthorizationErrorCode.TOTAL_LIMIT_EXCEEDED: "reference batch exceeds the read limit",
    ReferenceAuthorizationErrorCode.CAPABILITY_CONSUMED: "reference read capability was already consumed",
    ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH: "reference invocation correlation does not match",
    ReferenceAuthorizationErrorCode.INVALID_TRANSITION: "reference authorization transition is invalid",
    ReferenceAuthorizationErrorCode.EVIDENCE_CORRUPT: "reference authorization evidence is corrupt",
    ReferenceAuthorizationErrorCode.CLOSED: "reference authorization is closed",
    ReferenceAuthorizationErrorCode.CANCELLED: "reference operation was cancelled",
    ReferenceAuthorizationErrorCode.PROVIDER_FAILURE: "reference provider invocation failed",
    ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE: "reference workflow failed",
}


@contextmanager
def _expect_error(
    case: unittest.TestCase,
    code: ReferenceAuthorizationErrorCode,
):
    with case.assertRaises(ReferenceAuthorizationError) as raised:
        yield
    case.assertIs(raised.exception.code, code)
    case.assertEqual(str(raised.exception), _ERROR_MESSAGES[code])


def _reference_batch(
    *references: ArtifactReference,
    source_text: str = "reference request",
) -> ArtifactReferenceBatch:
    return ArtifactReferenceBatch(source_text, source_text, references)


def _correlation(
    operation_id: str,
    invocation_id: str,
    ordinal: int,
    *,
    agent_id: str = "lead",
    phase: str = "draft",
    round_number: int | None = None,
    attempt: int = 1,
) -> ReferenceInvocationCorrelation:
    return ReferenceInvocationCorrelation(
        operation_id=operation_id,
        invocation_id=invocation_id,
        invocation_ordinal=ordinal,
        agent_id=agent_id,
        phase=phase,
        round_number=round_number,
        attempt=attempt,
    )


def _snapshot(
    path: str,
    content: bytes,
    *,
    identity: int = 1,
) -> ArtifactContentSnapshot:
    source = ArtifactSourceIdentity(
        path=path,
        kind=(
            ArtifactKind.CORE_DOCUMENT
            if path.startswith("core_documents/")
            else ArtifactKind.MANUSCRIPT
        ),
        source_sha256=hashlib.sha256(content).hexdigest(),
        source_utf8_bytes=len(content),
        device_id=1,
        inode=identity,
        modified_ns=identity,
        changed_ns=identity,
    )
    return ArtifactContentSnapshot(source, content)


class ReferenceRepositoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        initialize_test_workspace(self.root)
        self.repository = WritingArtifactRepository(self.root)
        self.manuscript_bytes = b"alpha\r\nbeta\ngamma\rfour\xe2\x80\xa8five"
        self.core_bytes = b"core-one\ncore-two\n"
        self.repository.commit_consensus(
            output_path="part-01/seed.md",
            candidate=self.manuscript_bytes.decode("utf-8"),
            core_updates={"global_outline.md": self.core_bytes.decode("utf-8")},
            session_id="session",
            debate_id="seed",
            revision_id="r0",
            candidate_author_agent_id=EXECUTION_SPECIALIST,
        )

    def capture(
        self,
        operation_id: str,
        references: tuple[ArtifactReference, ...] | None = None,
    ) -> ReferenceAuthorization:
        selected = (
            (ArtifactReference(_MANUSCRIPT_PATH),) if references is None else references
        )
        authorization = self.repository.capture_reference_authorization(
            operation_id=operation_id,
            batch=_reference_batch(*selected),
        )
        self.assertIsInstance(authorization, ReferenceAuthorization)
        assert authorization is not None
        return authorization

    def authorize(
        self,
        authorization: ReferenceAuthorization,
        invocation_id: str,
        ordinal: int,
        *,
        attempt: int = 1,
    ) -> ReadCapability:
        return self.repository.authorize_reference_invocation(
            authorization,
            _correlation(
                authorization.manifest.operation_id,
                invocation_id,
                ordinal,
                attempt=attempt,
            ),
        )

    def accept_invocation(
        self,
        capability: ReadCapability,
        requested: tuple[ArtifactReference, ...],
    ) -> tuple[bytes, ...]:
        delivery = self.repository.deliver_authorized_references(
            capability,
            requested,
        )
        contents = delivery.contents
        delivery.close()
        self.repository.record_reference_result_received(capability)
        self.repository.accept_reference_invocation(capability)
        return contents


class ReferenceAuthorizationValueTests(unittest.TestCase):
    def test_error_codes_messages_and_facade_are_exact_and_content_free(self) -> None:
        self.assertEqual(
            tuple(code.value for code in ReferenceAuthorizationErrorCode),
            tuple(code.value for code in _ERROR_MESSAGES),
        )
        for code, message in _ERROR_MESSAGES.items():
            with self.subTest(code=code):
                error = ReferenceAuthorizationError(code)
                self.assertIs(error.code, code)
                self.assertEqual(str(error), message)
                self.assertNotIn("artifact bytes", str(error))
        with self.assertRaisesRegex(
            TypeError,
            "code must be a ReferenceAuthorizationErrorCode",
        ):
            ReferenceAuthorizationError("drift")  # type: ignore[arg-type]

        supported = {
            "ArtifactContentSnapshot",
            "ArtifactSourceIdentity",
            "AuthorizedArtifactReference",
            "ReferenceAuthorizationError",
            "ReferenceAuthorizationErrorCode",
            "ReferenceEvidence",
            "ReferenceInvocationCorrelation",
            "ReferenceInvocationState",
            "ReferenceOperationHistory",
            "ReferenceOperationManifest",
            "ReferenceOperationState",
        }
        transient = {
            "ArtifactReferenceDelivery",
            "ReadCapability",
            "ReferenceAuthorization",
        }
        self.assertTrue(supported.issubset(artifact_facade.__all__))
        self.assertTrue(transient.isdisjoint(artifact_facade.__all__))
        self.assertNotIn("CanonicalArtifactFilesystem", artifact_facade.__all__)
        self.assertEqual(len(artifact_facade.__all__), 78)
        self.assertTrue(
            (supported | transient).isdisjoint(story_writing_agents.__all__)
        )
        self.assertEqual(len(story_writing_agents.__all__), 104)

    def test_correlation_requires_safe_ids_positive_ordinals_and_attempts(self) -> None:
        valid = _correlation(
            "o" * 128,
            "invocation_1",
            1,
            agent_id="agent-1",
            phase="audit_phase",
            round_number=0,
            attempt=2,
        )
        self.assertEqual(valid.round_number, 0)
        self.assertEqual(valid.attempt, 2)
        for field_name in ("operation_id", "invocation_id", "agent_id", "phase"):
            for invalid in ("", "-bad", "bad/path", "a" * 129, True):
                with self.subTest(field=field_name, invalid=invalid):
                    values = {
                        "operation_id": "operation",
                        "invocation_id": "invocation",
                        "invocation_ordinal": 1,
                        "agent_id": "agent",
                        "phase": "phase",
                        "round_number": None,
                        "attempt": 1,
                    }
                    values[field_name] = invalid
                    with _expect_error(
                        self,
                        ReferenceAuthorizationErrorCode.INVALID_IDENTITY,
                    ):
                        ReferenceInvocationCorrelation(**values)  # type: ignore[arg-type]
        for name, value, message in (
            ("invocation_ordinal", 0, "invocation_ordinal must be a positive integer"),
            (
                "invocation_ordinal",
                True,
                "invocation_ordinal must be a positive integer",
            ),
            ("attempt", 0, "attempt must be a positive integer"),
            ("attempt", True, "attempt must be a positive integer"),
            ("round_number", -1, "round_number must be None or a nonnegative integer"),
            (
                "round_number",
                True,
                "round_number must be None or a nonnegative integer",
            ),
        ):
            with self.subTest(name=name, value=value):
                values = {
                    "operation_id": "operation",
                    "invocation_id": "invocation",
                    "invocation_ordinal": 1,
                    "agent_id": "agent",
                    "phase": "phase",
                    "round_number": None,
                    "attempt": 1,
                }
                values[name] = value
                with self.assertRaisesRegex(ValueError, message):
                    ReferenceInvocationCorrelation(**values)  # type: ignore[arg-type]

    def test_public_values_are_frozen_slotted_hash_safe_and_content_free(self) -> None:
        content = b"line\n"
        reference = ArtifactReference(_CORE_PATH, ArtifactLineRange(1, 1))
        snapshot = _snapshot(_CORE_PATH, content)
        authorized = AuthorizedArtifactReference(
            ordinal=1,
            reference=reference,
            source=snapshot.source,
            selected_sha256=hashlib.sha256(content).hexdigest(),
            selected_utf8_bytes=len(content),
            source_line_count=1,
        )
        manifest = ReferenceOperationManifest("operation", (reference,))
        correlation = _correlation("operation", "invocation", 1)
        evidence = ReferenceEvidence(
            sequence=1,
            operation_id="operation",
            operation_state=ReferenceOperationState.CAPTURED,
            invocation_state=None,
            correlation=None,
            references=(authorized,),
        )
        history = ReferenceOperationHistory(manifest, (evidence,), ())
        for value in (
            snapshot.source,
            authorized,
            manifest,
            correlation,
            evidence,
            history,
        ):
            with self.subTest(value=type(value).__name__):
                hash(value)
                self.assertFalse(hasattr(value, "__dict__"))
                self.assertNotIn(content.decode(), repr(value))
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, fields(value)[0].name, None)
        self.assertEqual(authorized.ordinal, 1)
        self.assertEqual(
            tuple(field.name for field in fields(ArtifactSourceIdentity)),
            (
                "path",
                "kind",
                "source_sha256",
                "source_utf8_bytes",
                "device_id",
                "inode",
                "modified_ns",
                "changed_ns",
            ),
        )
        self.assertEqual(
            tuple(field.name for field in fields(AuthorizedArtifactReference)),
            (
                "ordinal",
                "reference",
                "source",
                "selected_sha256",
                "selected_utf8_bytes",
                "source_line_count",
            ),
        )
        self.assertFalse(hasattr(authorized, "catalog_entry"))
        self.assertEqual(
            tuple(field.name for field in fields(ReferenceInvocationCorrelation)),
            (
                "operation_id",
                "invocation_id",
                "invocation_ordinal",
                "agent_id",
                "phase",
                "round_number",
                "attempt",
            ),
        )
        self.assertNotIn("capability_id", {field.name for field in fields(correlation)})
        self.assertNotIn("capability_id", {field.name for field in fields(evidence)})


class UniversalNewlineAndLimitTests(unittest.TestCase):
    def test_universal_newlines_preserve_terminators_and_unicode_is_content(
        self,
    ) -> None:
        source = b"one\r\ntwo\nthree\rfour\xe2\x80\xa8five"
        expected = (
            (ArtifactLineRange(1, 1), b"one\r\n"),
            (ArtifactLineRange(2, 2), b"two\n"),
            (ArtifactLineRange(3, 3), b"three\r"),
            (ArtifactLineRange(4, 4), b"four\xe2\x80\xa8five"),
            (ArtifactLineRange(2, 4), b"two\nthree\rfour\xe2\x80\xa8five"),
        )
        whole, line_count = select_reference_bytes(source, None)
        self.assertEqual((whole, line_count), (source, 4))
        for line_range, selected in expected:
            with self.subTest(line_range=line_range):
                self.assertEqual(
                    select_reference_bytes(source, line_range),
                    (selected, 4),
                )

    def test_empty_trailing_terminator_final_fragment_and_bounds_are_exact(
        self,
    ) -> None:
        self.assertEqual(select_reference_bytes(b"", None), (b"", 0))
        with _expect_error(self, ReferenceAuthorizationErrorCode.RANGE_OUT_OF_BOUNDS):
            select_reference_bytes(b"", ArtifactLineRange(1, 1))
        self.assertEqual(
            select_reference_bytes(b"one\n", ArtifactLineRange(1, 1)),
            (b"one\n", 1),
        )
        with _expect_error(self, ReferenceAuthorizationErrorCode.RANGE_OUT_OF_BOUNDS):
            select_reference_bytes(b"one\n", ArtifactLineRange(2, 2))
        self.assertEqual(
            select_reference_bytes(b"one\ntwo", ArtifactLineRange(2, 2)),
            (b"two", 2),
        )
        with _expect_error(self, ReferenceAuthorizationErrorCode.RANGE_OUT_OF_BOUNDS):
            select_reference_bytes(b"one\ntwo", ArtifactLineRange(2, 3))

    def test_full_source_utf8_and_nul_validation_precedes_range_selection(self) -> None:
        for source, code in (
            (b"safe\ninvalid\xff", ReferenceAuthorizationErrorCode.INVALID_UTF8),
            (b"safe\ncontains\x00nul", ReferenceAuthorizationErrorCode.NUL_CONTENT),
        ):
            with self.subTest(code=code), _expect_error(self, code):
                select_reference_bytes(source, ArtifactLineRange(1, 1))
        with self.assertRaisesRegex(TypeError, "reference source must be bytes"):
            select_reference_bytes("text", None)  # type: ignore[arg-type]

    def test_per_read_and_aggregate_limits_are_inclusive_and_exact(self) -> None:
        exact = b"x" * MAX_REFERENCE_UTF8_BYTES
        self.assertEqual(select_reference_bytes(exact, None), (exact, 1))
        with _expect_error(
            self,
            ReferenceAuthorizationErrorCode.READ_LIMIT_EXCEEDED,
        ):
            select_reference_bytes(exact + b"x", None)

        large_source = b"selected\n" + b"z" * (MAX_REFERENCE_UTF8_BYTES + 1)
        self.assertEqual(
            select_reference_bytes(large_source, ArtifactLineRange(1, 1))[0],
            b"selected\n",
        )

        paths = (
            "manuscript/part-01/a.md",
            "manuscript/part-01/b.md",
            "manuscript/part-01/c.md",
        )
        contents = (exact, exact, b"z")
        references = tuple(ArtifactReference(path) for path in paths)
        snapshots = tuple(
            _snapshot(path, content, identity=index)
            for index, (path, content) in enumerate(
                zip(paths, contents),
                start=1,
            )
        )
        proofs, captured = build_authorized_references(
            _reference_batch(*references[:2]),
            snapshots[:2],
        )
        self.assertEqual(
            sum(item.selected_utf8_bytes for item in proofs), MAX_REFERENCE_TOTAL_BYTES
        )
        self.assertEqual(len(captured), 2)
        with _expect_error(
            self,
            ReferenceAuthorizationErrorCode.TOTAL_LIMIT_EXCEEDED,
        ):
            build_authorized_references(
                _reference_batch(*references),
                snapshots,
            )


class ReferenceCaptureAndEligibilityTests(ReferenceRepositoryTestCase):
    def test_no_reference_returns_before_lock_storage_or_history(self) -> None:
        empty = ArtifactReferenceBatch("plain", "plain", ())
        with (
            mock.patch.object(
                self.repository,
                "_locked_repository",
                side_effect=AssertionError("lock must not run"),
            ) as locked,
            mock.patch.object(
                self.repository._reference_evidence,
                "begin_operation",
                side_effect=AssertionError("history must not run"),
            ) as began,
            mock.patch.object(
                self.repository._storage,
                "read_snapshot",
                side_effect=AssertionError("storage must not run"),
            ) as read,
        ):
            self.assertIsNone(
                self.repository.capture_reference_authorization(
                    operation_id="unused",
                    batch=empty,
                )
            )
        locked.assert_not_called()
        began.assert_not_called()
        read.assert_not_called()

    def test_selective_baseline_reads_never_create_or_satisfy_evidence(self) -> None:
        snapshots = self.repository.read_current((_CORE_PATH,))
        self.assertEqual(tuple(item.path for item in snapshots), (_CORE_PATH,))
        self.assertIsNone(self.repository.inspect_reference_operation("baseline"))
        snapshot_root = self.root / _SNAPSHOTS
        self.assertEqual(tuple(snapshot_root.iterdir()), ())

        authorization = self.capture(
            "explicit",
            (ArtifactReference(_CORE_PATH),),
        )
        self.assertIsNotNone(self.repository.inspect_reference_operation("explicit"))
        authorization.close()

    def test_same_path_multiple_ranges_read_source_once_and_keep_request_order(
        self,
    ) -> None:
        references = (
            ArtifactReference(_MANUSCRIPT_PATH, ArtifactLineRange(3, 3)),
            ArtifactReference(_MANUSCRIPT_PATH, ArtifactLineRange(1, 1)),
            ArtifactReference(_MANUSCRIPT_PATH, ArtifactLineRange(4, 4)),
        )
        calls = 0
        original = SecureWorkspaceStorage.read_snapshot

        def observed(storage, path, *args, **kwargs):
            nonlocal calls
            if storage is self.repository._storage and path == _MANUSCRIPT_PATH:
                calls += 1
            return original(storage, path, *args, **kwargs)

        with mock.patch.object(
            SecureWorkspaceStorage,
            "read_snapshot",
            new=observed,
        ):
            authorization = self.capture("ranges", references)
        self.assertEqual(calls, 1)
        self.assertEqual(
            tuple(item.reference for item in authorization.authorized_references),
            references,
        )
        self.assertEqual(
            tuple(item.ordinal for item in authorization.authorized_references),
            (1, 2, 3),
        )
        authorization.close()

    def test_manual_empty_missing_invalid_and_nul_sources_are_coded_exactly(
        self,
    ) -> None:
        manual_path = "manuscript/manual older 世界.md"
        manual_target = self.root / manual_path
        manual_target.parent.mkdir(parents=True, exist_ok=True)
        manual_target.write_bytes(b"manual")
        manual = self.repository.capture_reference_authorization(
            operation_id="manual",
            batch=_reference_batch(ArtifactReference(manual_path)),
        )
        assert manual is not None
        self.assertEqual(
            manual.delivery_snapshot(self.repository._reference_authorization_owner)[1],
            (b"manual",),
        )
        manual_history = self.repository.inspect_reference_operation("manual")
        assert manual_history is not None
        self.assertEqual(
            tuple(event.operation_state for event in manual_history.operation_events),
            (ReferenceOperationState.REQUESTED, ReferenceOperationState.CAPTURED),
        )
        manual.close()

        empty_path = "manuscript/empty.md"
        (self.root / empty_path).write_bytes(b"")
        empty = self.repository.capture_reference_authorization(
            operation_id="empty",
            batch=_reference_batch(ArtifactReference(empty_path)),
        )
        assert empty is not None
        proof = empty.authorized_references[0]
        self.assertEqual(
            (proof.selected_utf8_bytes, proof.source_line_count),
            (0, 0),
        )
        empty.close()
        with _expect_error(
            self,
            ReferenceAuthorizationErrorCode.RANGE_OUT_OF_BOUNDS,
        ):
            self.repository.capture_reference_authorization(
                operation_id="empty_range",
                batch=_reference_batch(
                    ArtifactReference(empty_path, ArtifactLineRange(1, 1))
                ),
            )

        missing_path = "manuscript/missing draft.md"
        with _expect_error(self, ReferenceAuthorizationErrorCode.NOT_FOUND):
            self.repository.capture_reference_authorization(
                operation_id="missing",
                batch=_reference_batch(ArtifactReference(missing_path)),
            )

        for label, content, code in (
            (
                "invalid",
                b"safe\ninvalid\xff",
                ReferenceAuthorizationErrorCode.INVALID_UTF8,
            ),
            ("nul", b"safe\x00nul", ReferenceAuthorizationErrorCode.NUL_CONTENT),
        ):
            with self.subTest(label=label):
                path = f"manuscript/{label} older.md"
                (self.root / path).write_bytes(content)
                with _expect_error(self, code):
                    self.repository.capture_reference_authorization(
                        operation_id=label,
                        batch=_reference_batch(ArtifactReference(path)),
                    )

    def test_symlink_hardlink_and_fifo_fail_without_content_in_errors(self) -> None:
        scenarios: list[tuple[str, callable]] = []
        target = self.root / _MANUSCRIPT_PATH
        original = target.read_bytes()
        outside = self.root / "outside.md"
        outside.write_bytes(original)

        def symlink() -> None:
            target.unlink()
            target.symlink_to(outside)

        extra = self.root / "extra-link.md"

        def hardlink() -> None:
            os.link(target, extra)

        def fifo() -> None:
            target.unlink()
            os.mkfifo(target)

        scenarios.extend(
            (
                ("symlink", symlink),
                ("hardlink", hardlink),
                ("fifo", fifo),
            )
        )
        for label, mutate in scenarios:
            with self.subTest(label=label):
                if target.exists() or target.is_symlink():
                    target.unlink()
                target.write_bytes(original)
                if extra.exists():
                    extra.unlink()
                mutate()
                with _expect_error(
                    self,
                    ReferenceAuthorizationErrorCode.INELIGIBLE,
                ):
                    self.repository.capture_reference_authorization(
                        operation_id=f"unsafe_{label}",
                        batch=_reference_batch(ArtifactReference(_MANUSCRIPT_PATH)),
                    )


class ReferenceInvocationLifecycleTests(ReferenceRepositoryTestCase):
    def test_complete_lifecycle_records_exact_states_and_closes_authorization(
        self,
    ) -> None:
        requested = (ArtifactReference(_MANUSCRIPT_PATH),)
        authorization = self.capture("complete", requested)
        capability = self.authorize(authorization, "invoke", 1)
        self.assertEqual(
            self.accept_invocation(capability, requested), (self.manuscript_bytes,)
        )
        self.repository.revalidate_reference_operation(
            authorization,
            checkpoint="before_finish",
        )
        self.repository.finish_reference_operation(authorization, outcome=None)

        history = self.repository.inspect_reference_operation("complete")
        assert history is not None
        self.assertEqual(
            tuple(event.operation_state for event in history.operation_events),
            (
                ReferenceOperationState.REQUESTED,
                ReferenceOperationState.CAPTURED,
                ReferenceOperationState.REVALIDATED,
                ReferenceOperationState.ACCEPTED,
            ),
        )
        self.assertEqual(
            tuple(event.invocation_state for event in history.invocation_events[0]),
            (
                ReferenceInvocationState.AUTHORIZED,
                ReferenceInvocationState.DELIVERED,
                ReferenceInvocationState.RESULT_RECEIVED,
                ReferenceInvocationState.ACCEPTED,
            ),
        )
        with _expect_error(self, ReferenceAuthorizationErrorCode.CLOSED):
            authorization.authorized_references

    def test_exact_request_is_one_use_even_when_delivery_validation_fails(self) -> None:
        references = (
            ArtifactReference(_MANUSCRIPT_PATH, ArtifactLineRange(1, 1)),
            ArtifactReference(_CORE_PATH),
        )
        invalid_requests = (
            tuple(reversed(references)),
            references[:1],
            references + (references[-1],),
            references
            + (ArtifactReference(_MANUSCRIPT_PATH, ArtifactLineRange(2, 2)),),
        )
        authorization = self.capture("request_shapes", references)
        for ordinal, requested in enumerate(invalid_requests, start=1):
            capability = self.authorize(
                authorization,
                f"bad_{ordinal}",
                ordinal,
            )
            with _expect_error(
                self,
                ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH,
            ):
                self.repository.deliver_authorized_references(capability, requested)
            with _expect_error(
                self,
                ReferenceAuthorizationErrorCode.CAPABILITY_CONSUMED,
            ):
                self.repository.deliver_authorized_references(capability, references)
        history = self.repository.inspect_reference_operation("request_shapes")
        assert history is not None
        self.assertEqual(
            tuple(
                tuple(event.invocation_state for event in events)
                for events in history.invocation_events
            ),
            (
                (
                    ReferenceInvocationState.AUTHORIZED,
                    ReferenceInvocationState.TERMINATED,
                ),
            )
            * len(invalid_requests),
        )
        self.assertTrue(
            all(
                events[-1].outcome
                is ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH
                for events in history.invocation_events
            )
        )
        self.repository.finish_reference_operation(
            authorization,
            outcome=ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
        )

    def test_cross_operation_repository_forged_and_closed_handles_fail_coded(
        self,
    ) -> None:
        requested = (ArtifactReference(_MANUSCRIPT_PATH),)
        authorization = self.capture("owners", requested)
        other_repository = WritingArtifactRepository(self.root)
        with _expect_error(
            self,
            ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH,
        ):
            other_repository.authorize_reference_invocation(
                authorization,
                _correlation("owners", "other", 1),
            )
        with _expect_error(
            self,
            ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH,
        ):
            self.repository.authorize_reference_invocation(
                authorization,
                _correlation("different", "wrong_operation", 1),
            )

        forged = ReadCapability(
            authorization,
            _correlation("owners", "forged", 1),
            capability_id="f" * 32,
            authorized_references=authorization.authorized_references,
            owner=object(),
        )
        with _expect_error(
            self,
            ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH,
        ):
            self.repository.deliver_authorized_references(forged, requested)

        capability = self.authorize(authorization, "closed", 1)
        authorization.close()
        with _expect_error(self, ReferenceAuthorizationErrorCode.CLOSED):
            self.repository.deliver_authorized_references(capability, requested)

    def test_retry_uses_new_capability_and_same_captured_snapshot(self) -> None:
        requested = (ArtifactReference(_MANUSCRIPT_PATH),)
        authorization = self.capture("retry", requested)
        first = self.authorize(authorization, "z_retry", 1, attempt=1)
        first_delivery = self.repository.deliver_authorized_references(first, requested)
        first_contents = first_delivery.contents
        first_delivery.close()
        self.repository.terminate_reference_invocation(
            first,
            ReferenceAuthorizationErrorCode.PROVIDER_FAILURE,
        )

        second = self.authorize(authorization, "a_retry", 2, attempt=2)
        second_contents = self.accept_invocation(second, requested)
        self.assertNotEqual(first.capability_id, second.capability_id)
        self.assertRegex(first.capability_id, r"[0-9a-f]{32}")
        self.assertRegex(second.capability_id, r"[0-9a-f]{32}")
        self.assertEqual(first_contents, second_contents)
        self.repository.finish_reference_operation(authorization, outcome=None)

        history = self.repository.inspect_reference_operation("retry")
        assert history is not None
        self.assertEqual(
            tuple(
                events[0].correlation.invocation_id
                for events in history.invocation_events
                if events[0].correlation is not None
            ),
            ("z_retry", "a_retry"),
        )
        self.assertNotIn(first.capability_id, repr(history))
        self.assertNotIn(second.capability_id, repr(history))

    def test_gap_duplicate_and_illegal_transitions_fail_without_acceptance(
        self,
    ) -> None:
        requested = (ArtifactReference(_MANUSCRIPT_PATH),)
        premature = self.capture("premature", requested)
        with _expect_error(self, ReferenceAuthorizationErrorCode.INVALID_TRANSITION):
            self.repository.finish_reference_operation(premature, outcome=None)
        premature_history = self.repository.inspect_reference_operation("premature")
        assert premature_history is not None
        self.assertIs(
            premature_history.operation_events[-1].outcome,
            ReferenceAuthorizationErrorCode.INVALID_TRANSITION,
        )
        with _expect_error(self, ReferenceAuthorizationErrorCode.CLOSED):
            premature.authorized_references

        authorization = self.capture("illegal", requested)
        first = self.authorize(authorization, "first", 1)
        with _expect_error(self, ReferenceAuthorizationErrorCode.INVALID_TRANSITION):
            self.authorize(authorization, "gap", 3)
        with _expect_error(self, ReferenceAuthorizationErrorCode.INVALID_TRANSITION):
            self.repository.record_reference_result_received(first)
        self.repository.terminate_reference_invocation(
            first,
            ReferenceAuthorizationErrorCode.CANCELLED,
        )
        with _expect_error(self, ReferenceAuthorizationErrorCode.INVALID_TRANSITION):
            self.repository.terminate_reference_invocation(
                first,
                ReferenceAuthorizationErrorCode.CANCELLED,
            )
        with _expect_error(self, ReferenceAuthorizationErrorCode.EVIDENCE_CORRUPT):
            self.authorize(authorization, "first", 2)
        self.repository.finish_reference_operation(
            authorization,
            outcome=ReferenceAuthorizationErrorCode.CANCELLED,
        )


class ReferenceDriftAndLifetimeTests(ReferenceRepositoryTestCase):
    def test_source_only_hash_replacement_and_deletion_drift_fail_closed(
        self,
    ) -> None:
        path = "manuscript/older draft 世界.md"
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        original = b"source-only marker\n"
        target.write_bytes(original)
        requested = (ArtifactReference(path),)

        changed = self.capture("source_only_hash_drift", requested)
        changed_capability = self.authorize(changed, "invoke", 1)
        target.write_bytes(b"changed source-only marker\n")
        with _expect_error(self, ReferenceAuthorizationErrorCode.DRIFT):
            self.repository.deliver_authorized_references(
                changed_capability,
                requested,
            )
        self.repository.finish_reference_operation(
            changed,
            outcome=ReferenceAuthorizationErrorCode.DRIFT,
        )

        target.write_bytes(original)
        replaced = self.capture("source_only_replacement_drift", requested)
        replaced_capability = self.authorize(replaced, "invoke", 1)
        replacement = target.with_name("replacement.md")
        replacement.write_bytes(original)
        os.replace(replacement, target)
        with _expect_error(self, ReferenceAuthorizationErrorCode.DRIFT):
            self.repository.deliver_authorized_references(
                replaced_capability,
                requested,
            )
        self.repository.finish_reference_operation(
            replaced,
            outcome=ReferenceAuthorizationErrorCode.DRIFT,
        )

        deleted = self.capture("source_only_deletion_drift", requested)
        target.unlink()
        with _expect_error(self, ReferenceAuthorizationErrorCode.DRIFT):
            self.repository.revalidate_reference_operation(
                deleted,
                checkpoint="before_provider",
            )

    def test_drift_before_delivery_terminates_invocation_without_refresh(self) -> None:
        requested = (ArtifactReference(_MANUSCRIPT_PATH),)
        authorization = self.capture("delivery_drift", requested)
        capability = self.authorize(authorization, "invoke", 1)
        (self.root / _MANUSCRIPT_PATH).write_bytes(b"changed")
        with _expect_error(self, ReferenceAuthorizationErrorCode.DRIFT):
            self.repository.deliver_authorized_references(capability, requested)
        history = self.repository.inspect_reference_operation("delivery_drift")
        assert history is not None
        self.assertEqual(
            history.invocation_events[0][-1].invocation_state,
            ReferenceInvocationState.TERMINATED,
        )
        self.assertIs(
            history.invocation_events[0][-1].outcome,
            ReferenceAuthorizationErrorCode.DRIFT,
        )

    def test_same_bytes_physical_replacement_and_hardlink_are_drift(self) -> None:
        requested = (ArtifactReference(_CORE_PATH),)
        authorization = self.capture("replacement_drift", requested)
        capability = self.authorize(authorization, "invoke", 1)
        target = self.root / _CORE_PATH
        replacement = target.with_name("replacement.md")
        replacement.write_bytes(self.core_bytes)
        os.replace(replacement, target)
        with _expect_error(self, ReferenceAuthorizationErrorCode.DRIFT):
            self.repository.deliver_authorized_references(capability, requested)

        hardlink_authorization = self.capture("hardlink_drift", requested)
        hardlink_capability = self.authorize(
            hardlink_authorization,
            "invoke",
            1,
        )
        os.link(target, target.with_name("additional-link.md"))
        with _expect_error(self, ReferenceAuthorizationErrorCode.DRIFT):
            self.repository.deliver_authorized_references(
                hardlink_capability,
                requested,
            )

    def test_drift_at_invocation_acceptance_and_final_acceptance_fails_closed(
        self,
    ) -> None:
        requested = (ArtifactReference(_MANUSCRIPT_PATH),)
        authorization = self.capture("accept_drift", requested)
        capability = self.authorize(authorization, "invoke", 1)
        delivery = self.repository.deliver_authorized_references(capability, requested)
        self.assertEqual(delivery.contents, (self.manuscript_bytes,))
        delivery.close()
        self.repository.record_reference_result_received(capability)
        (self.root / _MANUSCRIPT_PATH).write_bytes(b"changed")
        with _expect_error(self, ReferenceAuthorizationErrorCode.DRIFT):
            self.repository.accept_reference_invocation(capability)
        self.repository.finish_reference_operation(
            authorization,
            outcome=ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
        )
        history = self.repository.inspect_reference_operation("accept_drift")
        assert history is not None
        self.assertIs(
            history.operation_events[-1].operation_state,
            ReferenceOperationState.TERMINATED,
        )

        second_temporary = tempfile.TemporaryDirectory()
        self.addCleanup(second_temporary.cleanup)
        second = Path(second_temporary.name)
        initialize_test_workspace(second)
        repository = WritingArtifactRepository(second)
        repository.commit_consensus(
            output_path="part-01/seed.md",
            candidate="stable",
            core_updates={},
            session_id="session",
            debate_id="seed",
            revision_id="r0",
            candidate_author_agent_id=EXECUTION_SPECIALIST,
        )
        ref = ArtifactReference(_MANUSCRIPT_PATH)
        auth = repository.capture_reference_authorization(
            operation_id="final_drift",
            batch=_reference_batch(ref),
        )
        assert auth is not None
        cap = repository.authorize_reference_invocation(
            auth,
            _correlation("final_drift", "invoke", 1),
        )
        delivered = repository.deliver_authorized_references(cap, (ref,))
        delivered.close()
        repository.record_reference_result_received(cap)
        repository.accept_reference_invocation(cap)
        (second / _MANUSCRIPT_PATH).write_bytes(b"changed")
        with _expect_error(self, ReferenceAuthorizationErrorCode.DRIFT):
            repository.finish_reference_operation(auth, outcome=None)
        final_history = repository.inspect_reference_operation("final_drift")
        assert final_history is not None
        self.assertIs(
            final_history.operation_events[-1].outcome,
            ReferenceAuthorizationErrorCode.DRIFT,
        )
        with _expect_error(self, ReferenceAuthorizationErrorCode.CLOSED):
            auth.authorized_references

    def test_operation_checkpoint_drift_terminates_and_closes(self) -> None:
        authorization = self.capture("checkpoint")
        (self.root / _MANUSCRIPT_PATH).unlink()
        with _expect_error(self, ReferenceAuthorizationErrorCode.DRIFT):
            self.repository.revalidate_reference_operation(
                authorization,
                checkpoint="before_provider",
            )
        history = self.repository.inspect_reference_operation("checkpoint")
        assert history is not None
        self.assertIs(
            history.operation_events[-1].outcome,
            ReferenceAuthorizationErrorCode.DRIFT,
        )
        with _expect_error(self, ReferenceAuthorizationErrorCode.CLOSED):
            authorization.authorized_references

    def test_growth_past_default_limit_is_drift_and_new_capture_is_coded(
        self,
    ) -> None:
        requested = (ArtifactReference(_MANUSCRIPT_PATH),)
        authorization = self.capture("oversize_growth", requested)
        os.truncate(
            self.root / _MANUSCRIPT_PATH,
            MAX_VIEWER_UTF8_BYTES + 1,
        )

        with _expect_error(self, ReferenceAuthorizationErrorCode.DRIFT):
            self.repository.revalidate_reference_operation(
                authorization,
                checkpoint="before_provider",
            )
        growth_history = self.repository.inspect_reference_operation("oversize_growth")
        assert growth_history is not None
        self.assertEqual(
            tuple(event.operation_state for event in growth_history.operation_events),
            (
                ReferenceOperationState.REQUESTED,
                ReferenceOperationState.CAPTURED,
                ReferenceOperationState.TERMINATED,
            ),
        )
        self.assertIs(
            growth_history.operation_events[-1].outcome,
            ReferenceAuthorizationErrorCode.DRIFT,
        )

        with _expect_error(
            self,
            ReferenceAuthorizationErrorCode.READ_LIMIT_EXCEEDED,
        ):
            self.repository.capture_reference_authorization(
                operation_id="oversize_capture",
                batch=_reference_batch(*requested),
            )
        capture_history = self.repository.inspect_reference_operation(
            "oversize_capture"
        )
        assert capture_history is not None
        self.assertEqual(
            tuple(event.operation_state for event in capture_history.operation_events),
            (
                ReferenceOperationState.REQUESTED,
                ReferenceOperationState.TERMINATED,
            ),
        )
        self.assertIs(
            capture_history.operation_events[-1].outcome,
            ReferenceAuthorizationErrorCode.READ_LIMIT_EXCEEDED,
        )

    def test_delivery_and_authorization_hide_serialize_close_and_race_atomically(
        self,
    ) -> None:
        requested = (ArtifactReference(_MANUSCRIPT_PATH),)
        authorization = self.capture("lifetime", requested)
        capability = self.authorize(authorization, "invoke", 1)
        delivery = self.repository.deliver_authorized_references(capability, requested)
        self.assertEqual(
            repr(authorization), "ReferenceAuthorization(<content hidden>)"
        )
        self.assertEqual(repr(capability), "ReadCapability(<content hidden>)")
        self.assertEqual(repr(delivery), "ArtifactReferenceDelivery(<content hidden>)")
        for value, message in (
            (authorization, "ReferenceAuthorization cannot be serialized"),
            (capability, "ReadCapability cannot be serialized"),
            (delivery, "ArtifactReferenceDelivery cannot be serialized"),
        ):
            with self.subTest(value=type(value).__name__):
                with self.assertRaisesRegex(TypeError, message):
                    pickle.dumps(value)
        with delivery:
            self.assertEqual(delivery.contents, (self.manuscript_bytes,))
        delivery.close()
        with _expect_error(self, ReferenceAuthorizationErrorCode.CLOSED):
            delivery.contents

        outcomes: list[object] = []
        for index in range(24):
            auth = self.capture(f"race_{index}")
            barrier = threading.Barrier(2)

            def read_snapshot() -> None:
                barrier.wait()
                try:
                    outcomes.append(
                        auth.delivery_snapshot(
                            self.repository._reference_authorization_owner
                        )[1]
                    )
                except ReferenceAuthorizationError as exc:
                    outcomes.append(exc.code)

            thread = threading.Thread(target=read_snapshot)
            thread.start()
            barrier.wait()
            auth.close()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        self.assertTrue(outcomes)
        self.assertTrue(
            all(
                outcome == (self.manuscript_bytes,)
                or outcome is ReferenceAuthorizationErrorCode.CLOSED
                for outcome in outcomes
            )
        )


class ReferenceConcurrencyTests(ReferenceRepositoryTestCase):
    def test_duplicate_operation_and_invocation_serialize_without_deadlock(
        self,
    ) -> None:
        batch = _reference_batch(ArtifactReference(_MANUSCRIPT_PATH))
        repositories = (
            WritingArtifactRepository(self.root),
            WritingArtifactRepository(self.root),
        )
        barrier = threading.Barrier(2)
        results: list[object] = []

        def capture(repository: WritingArtifactRepository) -> None:
            barrier.wait()
            try:
                results.append(
                    repository.capture_reference_authorization(
                        operation_id="concurrent",
                        batch=batch,
                    )
                )
            except ReferenceAuthorizationError as exc:
                results.append(exc.code)

        threads = [
            threading.Thread(target=capture, args=(repo,)) for repo in repositories
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(
            sum(type(result) is ReferenceAuthorization for result in results),
            1,
        )
        self.assertEqual(
            results.count(ReferenceAuthorizationErrorCode.EVIDENCE_CORRUPT),
            1,
        )
        for result in results:
            if type(result) is ReferenceAuthorization:
                result.close()

        authorization = self.capture("invocation_race")
        correlation = _correlation("invocation_race", "same", 1)
        barrier = threading.Barrier(2)
        invocation_results: list[object] = []

        def authorize() -> None:
            barrier.wait()
            try:
                invocation_results.append(
                    self.repository.authorize_reference_invocation(
                        authorization,
                        correlation,
                    )
                )
            except ReferenceAuthorizationError as exc:
                invocation_results.append(exc.code)

        threads = [threading.Thread(target=authorize) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(
            sum(type(result) is ReadCapability for result in invocation_results),
            1,
        )
        self.assertEqual(
            invocation_results.count(
                ReferenceAuthorizationErrorCode.INVALID_TRANSITION
            ),
            1,
        )
        for result in invocation_results:
            if type(result) is ReadCapability:
                self.repository.terminate_reference_invocation(
                    result,
                    ReferenceAuthorizationErrorCode.CANCELLED,
                )
        self.repository.finish_reference_operation(
            authorization,
            outcome=ReferenceAuthorizationErrorCode.CANCELLED,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
