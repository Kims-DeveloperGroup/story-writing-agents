"""Durable, content-free evidence contracts for authorized artifact reads."""

from __future__ import annotations

import json
import shutil
import threading
import unittest
from pathlib import Path
from unittest import mock

from story_writing_agents.writing_artifacts import (
    ArtifactReference,
    ArtifactReferenceBatch,
    ReferenceAuthorizationError,
    ReferenceAuthorizationErrorCode,
    ReferenceInvocationState,
    ReferenceOperationState,
)
from story_writing_agents.writing_artifacts._storage import (
    ArtifactRootSecurityError,
)

from tests.test_artifact_reference_authorization import (
    _MANUSCRIPT_PATH,
    _SNAPSHOTS,
    _expect_error,
    ReferenceRepositoryTestCase,
)


_FORBIDDEN_EVIDENCE_KEYS = frozenset(
    {
        "author_agent_id",
        "artifact_bytes",
        "bytes",
        "catalog_entry",
        "commit_id",
        "committed_at",
        "content",
        "debate_id",
        "prompt",
        "provider_text",
        "revision_id",
        "session_id",
        "source_text",
    }
)


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _walk_json(value: object):
    yield value
    if type(value) is dict:
        for key, item in value.items():
            yield key
            yield from _walk_json(item)
    elif type(value) is list:
        for item in value:
            yield from _walk_json(item)


class ReferenceEvidenceTestCase(ReferenceRepositoryTestCase):
    def evidence_root(self, operation_id: str) -> Path:
        return self.root / _SNAPSHOTS / operation_id

    def complete_invocation(
        self,
        authorization,
        invocation_id: str,
        ordinal: int,
    ):
        capability = self.authorize(authorization, invocation_id, ordinal)
        delivery = self.repository.deliver_authorized_references(
            capability,
            authorization.manifest.references,
        )
        delivery.close()
        self.repository.record_reference_result_received(capability)
        self.repository.accept_reference_invocation(capability)
        return capability

    def assert_corrupt(self, operation_id: str) -> None:
        with _expect_error(self, ReferenceAuthorizationErrorCode.EVIDENCE_CORRUPT):
            self.repository.inspect_reference_operation(operation_id)


class EvidenceSchemaAndPrivacyTests(ReferenceEvidenceTestCase):
    def test_absent_is_none_but_each_partial_authoritative_shape_is_corrupt(self) -> None:
        self.assertIsNone(self.repository.inspect_reference_operation("absent"))

        for name, relative in (
            ("empty", None),
            ("manifest-only", "manifest.json"),
            ("events-only", "operation_events/00000001.json"),
        ):
            with self.subTest(name=name):
                root = self.evidence_root(name)
                root.mkdir(parents=True)
                if relative is not None:
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"{}\n")
                self.assert_corrupt(name)

    def test_healthy_records_are_schema_v2_canonical_create_only_and_content_free(self) -> None:
        authorization = self.capture("canonical")
        capability = self.complete_invocation(authorization, "invoke", 1)
        self.repository.finish_reference_operation(authorization, outcome=None)

        root = self.evidence_root("canonical")
        raw_files = tuple(sorted(root.rglob("*.json")))
        self.assertEqual(len(raw_files), 9)
        for path in raw_files:
            raw = path.read_bytes()
            value = json.loads(raw.decode("utf-8"))
            self.assertEqual(raw, _canonical_json(value))
            for item in _walk_json(value):
                if type(item) is str:
                    self.assertNotIn(item, _FORBIDDEN_EVIDENCE_KEYS)
                    self.assertNotIn("alpha\r\nbeta", item)
                    self.assertNotIn("reference request", item)

        manifest = json.loads((root / "manifest.json").read_text())
        invocation = json.loads(
            (root / "invocations/invoke/manifest.json").read_text()
        )
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(invocation["schema_version"], 2)
        source = invocation["references"][0]["source"]
        self.assertEqual(
            set(source),
            {
                "changed_ns",
                "device_id",
                "inode",
                "kind",
                "modified_ns",
                "path",
                "source_sha256",
                "source_utf8_bytes",
            },
        )
        self.assertNotIn("catalog_entry", invocation["references"][0])
        self.assertRegex(invocation["capability_id"], r"^[0-9a-f]{32}$")
        self.assertNotIn(invocation["capability_id"], repr(capability.correlation))

        publish = self.repository._storage.publish_bytes
        with mock.patch.object(
            self.repository._storage,
            "publish_bytes",
            wraps=publish,
        ) as observed:
            other = self.capture("flags")
        self.addCleanup(other.close)
        self.assertGreaterEqual(observed.call_count, 2)
        for call in observed.call_args_list:
            self.assertIs(call.kwargs["create_only"], True)
            self.assertIs(call.kwargs["expected_exists"], False)
            self.assertIsNone(call.kwargs["expected_sha256"])

    def test_noncanonical_changed_and_duplicate_json_reject_without_rewrite(self) -> None:
        corruptions = {
            "noncanonical": lambda raw: json.dumps(json.loads(raw), sort_keys=True).encode(),
            "changed": lambda raw: _canonical_json(
                {**json.loads(raw), "operation_id": "different"}
            ),
            "duplicate": lambda raw: raw.replace(
                b'  "operation_id": "duplicate",\n',
                b'  "operation_id": "duplicate",\n  "operation_id": "duplicate",\n',
                1,
            ),
            "extra": lambda raw: _canonical_json(
                {**json.loads(raw), "unexpected": "field"}
            ),
            "missing": lambda raw: _canonical_json(
                {
                    key: value
                    for key, value in json.loads(raw).items()
                    if key != "schema_version"
                }
            ),
            "schema": lambda raw: _canonical_json(
                {**json.loads(raw), "schema_version": 1}
            ),
            "unknown-enum": lambda raw: _canonical_json(
                {**json.loads(raw), "operation_state": "unknown"}
            ),
        }
        for operation_id, mutate in corruptions.items():
            with self.subTest(operation_id=operation_id):
                authorization = self.capture(operation_id)
                self.addCleanup(authorization.close)
                target = (
                    self.evidence_root(operation_id) / "manifest.json"
                    if operation_id != "unknown-enum"
                    else self.evidence_root(operation_id)
                    / "operation_events/00000001.json"
                )
                original = target.read_bytes()
                altered = mutate(original)
                self.assertNotEqual(altered, original)
                target.write_bytes(altered)
                self.assert_corrupt(operation_id)
                self.assertEqual(target.read_bytes(), altered)

    def test_schema_v1_evidence_is_not_reused_migrated_or_globally_blocking(self) -> None:
        legacy = self.capture("legacy")
        legacy.close()
        manifest = self.evidence_root("legacy") / "manifest.json"
        value = json.loads(manifest.read_text())
        value["schema_version"] = 1
        legacy_bytes = _canonical_json(value)
        manifest.write_bytes(legacy_bytes)

        self.assert_corrupt("legacy")
        with _expect_error(
            self,
            ReferenceAuthorizationErrorCode.EVIDENCE_CORRUPT,
        ):
            self.capture("legacy")
        self.assertEqual(manifest.read_bytes(), legacy_bytes)

        fresh = self.capture("fresh-v2")
        self.assertEqual(fresh.manifest.schema_version, 2)
        fresh.close()

    def test_nested_duplicates_forbidden_keys_and_values_fail_content_free(self) -> None:
        for operation_id in ("nested-duplicate", "forbidden-key"):
            with self.subTest(operation_id=operation_id):
                authorization = self.capture(operation_id)
                self.addCleanup(authorization.close)
                target = (
                    self.evidence_root(operation_id)
                    / "operation_events/00000002.json"
                )
                raw = target.read_bytes()
                if operation_id == "nested-duplicate":
                    altered = raw.replace(
                        b'        "line_end": null,\n',
                        b'        "line_end": null,\n'
                        b'        "line_end": null,\n',
                        1,
                    )
                else:
                    value = json.loads(raw)
                    value["references"][0]["reference"]["content"] = "hidden"
                    altered = _canonical_json(value)
                self.assertNotEqual(altered, raw)
                target.write_bytes(altered)
                with _expect_error(
                    self,
                    ReferenceAuthorizationErrorCode.EVIDENCE_CORRUPT,
                ):
                    self.repository.inspect_reference_operation(operation_id)
                self.assertNotIn("hidden", str(self.repository._reference_evidence))


class EvidenceCorrelationAndIsolationTests(ReferenceEvidenceTestCase):
    def test_invocations_are_ordinal_sorted_not_lexically_sorted(self) -> None:
        authorization = self.capture("sort")
        self.complete_invocation(authorization, "z-last-lexically", 1)
        self.complete_invocation(authorization, "a-first-lexically", 2)
        history = self.repository.inspect_reference_operation("sort")
        assert history is not None
        self.assertEqual(
            tuple(events[0].correlation.invocation_id for events in history.invocation_events),
            ("z-last-lexically", "a-first-lexically"),
        )
        self.repository.finish_reference_operation(authorization, outcome=None)

    def test_event_name_collisions_gaps_and_illegal_persisted_transitions_corrupt(self) -> None:
        for operation_id, mutate in (
            (
                "collision",
                lambda root: (root / "operation_events/unexpected.json").write_bytes(b"{}\n"),
            ),
            (
                "gap",
                lambda root: (root / "operation_events/00000002.json").rename(
                    root / "operation_events/00000003.json"
                ),
            ),
            (
                "transition",
                lambda root: self._replace_json(
                    root / "operation_events/00000002.json",
                    operation_state="requested",
                ),
            ),
        ):
            with self.subTest(operation_id=operation_id):
                authorization = self.capture(operation_id)
                self.addCleanup(authorization.close)
                mutate(self.evidence_root(operation_id))
                self.assert_corrupt(operation_id)

    def test_capability_correlation_ordinal_and_duplicate_capabilities_corrupt(self) -> None:
        cases = (
            "capability",
            "correlation",
            "ordinal",
            "duplicate-capability",
        )
        for operation_id in cases:
            with self.subTest(operation_id=operation_id):
                authorization = self.capture(operation_id)
                self.authorize(authorization, "z-invoke", 1)
                self.authorize(authorization, "a-invoke", 2)
                root = self.evidence_root(operation_id) / "invocations"
                target = root / "a-invoke/manifest.json"
                value = json.loads(target.read_text())
                if operation_id == "capability":
                    value["capability_id"] = "not-a-capability"
                elif operation_id == "correlation":
                    value["correlation"]["invocation_id"] = "other-invocation"
                elif operation_id == "ordinal":
                    value["correlation"]["invocation_ordinal"] = 3
                else:
                    first_value = json.loads((root / "z-invoke/manifest.json").read_text())
                    value["capability_id"] = first_value["capability_id"]
                target.write_bytes(_canonical_json(value))
                self.assert_corrupt(operation_id)
                authorization.close()

    def test_corruption_is_isolated_from_other_evidence_catalog_reads_and_commits(self) -> None:
        broken = self.capture("broken")
        healthy = self.capture("healthy")
        target = self.evidence_root("broken") / "manifest.json"
        target.write_bytes(b"{}\n")
        self.assert_corrupt("broken")

        history = self.repository.inspect_reference_operation("healthy")
        self.assertIsNotNone(history)
        self.assertEqual(
            self.repository.read_text(_MANUSCRIPT_PATH),
            self.manuscript_bytes.decode("utf-8"),
        )
        result = self.repository.commit_consensus(
            output_path="part-02/isolated.md",
            candidate="independent",
            core_updates={},
            session_id="session",
            debate_id="isolation",
            revision_id="r0",
            candidate_author_agent_id="execution_specialist",
        )
        self.assertIn("manuscript/part-02/isolated.md", result)
        self.assertIsNotNone(self.repository.inspect_reference_operation("healthy"))
        broken.close()
        healthy.close()

    def test_terminal_replay_rejects_deleted_and_nonterminal_invocation_histories(self) -> None:
        accepted = self.capture("deleted-invocation")
        self.complete_invocation(accepted, "invoke", 1)
        self.repository.finish_reference_operation(accepted, outcome=None)
        shutil.rmtree(
            self.evidence_root("deleted-invocation") / "invocations/invoke"
        )
        self.assert_corrupt("deleted-invocation")

        contradictory = self.capture("nonterminal-invocation")
        self.authorize(contradictory, "invoke", 1)
        operation_root = self.evidence_root("nonterminal-invocation")
        terminal = json.loads(
            (operation_root / "operation_events/00000002.json").read_text()
        )
        terminal.update(
            sequence=3,
            operation_state="terminated",
            outcome="workflow_failure",
        )
        (operation_root / "operation_events/00000003.json").write_bytes(
            _canonical_json(terminal)
        )
        self.assert_corrupt("nonterminal-invocation")
        contradictory.close()

    def test_precapture_terminal_event_with_forged_proofs_is_corrupt(self) -> None:
        source = self.capture("proof-source")
        source_event = json.loads(
            (
                self.evidence_root("proof-source")
                / "operation_events/00000002.json"
            ).read_text()
        )

        missing_path = "manuscript/part-01/not-cataloged.md"
        with _expect_error(self, ReferenceAuthorizationErrorCode.NOT_FOUND):
            self.repository.capture_reference_authorization(
                operation_id="precapture-terminal",
                batch=ArtifactReferenceBatch(
                    "reference request",
                    "reference request",
                    (ArtifactReference(missing_path),),
                ),
            )
        target = (
            self.evidence_root("precapture-terminal")
            / "operation_events/00000002.json"
        )
        forged = json.loads(target.read_text())
        self.assertEqual(forged["references"], [])
        forged["references"] = source_event["references"]
        target.write_bytes(_canonical_json(forged))
        self.assert_corrupt("precapture-terminal")
        source.close()

    def test_source_root_safety_is_coded_but_catalog_state_is_not_consulted(self) -> None:
        original_read = self.repository._storage.read_snapshot
        root_failure = ArtifactRootSecurityError("root changed")

        def root_read(path, *args, **kwargs):
            if path == _MANUSCRIPT_PATH:
                raise root_failure
            return original_read(path, *args, **kwargs)

        with mock.patch.object(
            self.repository._storage,
            "read_snapshot",
            side_effect=root_read,
        ):
            with _expect_error(
                self,
                ReferenceAuthorizationErrorCode.INELIGIBLE,
            ):
                self.capture("root-failure")
        root_history = self.repository.inspect_reference_operation("root-failure")
        assert root_history is not None
        self.assertEqual(
            tuple(event.operation_state for event in root_history.operation_events),
            (ReferenceOperationState.REQUESTED, ReferenceOperationState.TERMINATED),
        )
        self.assertIs(
            root_history.operation_events[-1].outcome,
            ReferenceAuthorizationErrorCode.INELIGIBLE,
        )

        with (
            mock.patch.object(
                self.repository,
                "_load_catalog",
                side_effect=AssertionError("source capture loaded catalog"),
            ) as load_catalog,
            mock.patch.object(
                self.repository,
                "_recover",
                side_effect=AssertionError("source capture ran recovery"),
            ) as recover,
        ):
            catalog_independent = self.capture("catalog-failure")
        load_catalog.assert_not_called()
        recover.assert_not_called()
        catalog_history = self.repository.inspect_reference_operation(
            "catalog-failure"
        )
        assert catalog_history is not None
        self.assertEqual(
            tuple(event.operation_state for event in catalog_history.operation_events),
            (ReferenceOperationState.REQUESTED, ReferenceOperationState.CAPTURED),
        )
        catalog_independent.close()

        inspected = self.capture("inspect-root-failure")
        original_list = self.repository._storage.list_names
        inspect_failure = ArtifactRootSecurityError("snapshot root changed")

        def root_list(path, *args, **kwargs):
            if path == str(_SNAPSHOTS):
                raise inspect_failure
            return original_list(path, *args, **kwargs)

        with mock.patch.object(
            self.repository._storage,
            "list_names",
            side_effect=root_list,
        ):
            with self.assertRaises(ArtifactRootSecurityError) as raised:
                self.repository.inspect_reference_operation(
                    "inspect-root-failure"
                )
        self.assertIs(raised.exception, inspect_failure)
        self.assertIsNotNone(
            self.repository.inspect_reference_operation("inspect-root-failure")
        )
        inspected.close()

    @staticmethod
    def _replace_json(path: Path, **updates: object) -> None:
        value = json.loads(path.read_text())
        value.update(updates)
        path.write_bytes(_canonical_json(value))


class EvidenceWriteFailureTests(ReferenceEvidenceTestCase):
    def _fail_publish_once(self, suffix: str):
        original = self.repository._storage.publish_bytes
        failed = False

        def publish(path, *args, **kwargs):
            nonlocal failed
            if not failed and path.endswith(suffix):
                failed = True
                raise OSError("injected evidence write failure")
            return original(path, *args, **kwargs)

        return mock.patch.object(self.repository._storage, "publish_bytes", side_effect=publish)

    def test_begin_failure_returns_no_authorization_and_existing_collision_is_preserved(self) -> None:
        operation_root = self.evidence_root("collision")
        operation_root.mkdir(parents=True)
        sentinel = operation_root / "sentinel"
        sentinel.write_bytes(b"preserve")
        with _expect_error(self, ReferenceAuthorizationErrorCode.EVIDENCE_CORRUPT):
            self.capture("collision")
        self.assertEqual(sentinel.read_bytes(), b"preserve")

        with self._fail_publish_once("/manifest.json"):
            with _expect_error(self, ReferenceAuthorizationErrorCode.EVIDENCE_CORRUPT):
                self.capture("begin-write")
        self.assert_corrupt("begin-write")

    def test_captured_write_failure_terminates_without_returning_authorization(self) -> None:
        with self._fail_publish_once("/operation_events/00000002.json"):
            with _expect_error(self, ReferenceAuthorizationErrorCode.EVIDENCE_CORRUPT):
                self.capture("captured-write")
        history = self.repository.inspect_reference_operation("captured-write")
        assert history is not None
        self.assertEqual(
            tuple(event.operation_state for event in history.operation_events),
            (ReferenceOperationState.REQUESTED, ReferenceOperationState.TERMINATED),
        )
        self.assertIs(
            history.operation_events[-1].outcome,
            ReferenceAuthorizationErrorCode.EVIDENCE_CORRUPT,
        )

    def test_delivered_write_failure_returns_no_content_and_records_terminal_failure(self) -> None:
        authorization = self.capture("delivery-write")
        capability = self.authorize(authorization, "invoke", 1)
        with self._fail_publish_once("/events/00000002.json"):
            with _expect_error(self, ReferenceAuthorizationErrorCode.EVIDENCE_CORRUPT):
                self.repository.deliver_authorized_references(
                    capability,
                    authorization.manifest.references,
                )
        history = self.repository.inspect_reference_operation("delivery-write")
        assert history is not None
        events = history.invocation_events[0]
        self.assertEqual(
            tuple(event.invocation_state for event in events),
            (ReferenceInvocationState.AUTHORIZED, ReferenceInvocationState.TERMINATED),
        )
        self.assertIs(events[-1].outcome, ReferenceAuthorizationErrorCode.EVIDENCE_CORRUPT)
        authorization.close()

    def test_acceptance_and_operation_terminal_write_failures_never_accept(self) -> None:
        authorization = self.capture("accept-write")
        capability = self.authorize(authorization, "invoke", 1)
        delivery = self.repository.deliver_authorized_references(
            capability,
            authorization.manifest.references,
        )
        delivery.close()
        self.repository.record_reference_result_received(capability)
        with self._fail_publish_once("/events/00000004.json"):
            with _expect_error(self, ReferenceAuthorizationErrorCode.EVIDENCE_CORRUPT):
                self.repository.accept_reference_invocation(capability)
        history = self.repository.inspect_reference_operation("accept-write")
        assert history is not None
        self.assertEqual(
            history.invocation_events[0][-1].invocation_state,
            ReferenceInvocationState.TERMINATED,
        )
        self.repository.finish_reference_operation(
            authorization,
            outcome=ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE,
        )

        final = self.capture("operation-write")
        self.complete_invocation(final, "invoke", 1)
        with self._fail_publish_once("/operation_events/00000003.json"):
            with _expect_error(self, ReferenceAuthorizationErrorCode.EVIDENCE_CORRUPT):
                self.repository.finish_reference_operation(final, outcome=None)
        final_history = self.repository.inspect_reference_operation("operation-write")
        assert final_history is not None
        self.assertEqual(
            final_history.operation_events[-1].operation_state,
            ReferenceOperationState.TERMINATED,
        )
        self.assertIs(
            final_history.operation_events[-1].outcome,
            ReferenceAuthorizationErrorCode.EVIDENCE_CORRUPT,
        )

    def test_duplicate_operation_race_has_one_complete_authority_and_one_coded_failure(self) -> None:
        barrier = threading.Barrier(2)
        outcomes: list[object] = []

        def capture() -> None:
            barrier.wait()
            try:
                outcomes.append(self.capture("raced"))
            except ReferenceAuthorizationError as exc:
                outcomes.append(exc.code)

        threads = [threading.Thread(target=capture) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())

        authorizations = [item for item in outcomes if not isinstance(item, ReferenceAuthorizationErrorCode)]
        self.assertEqual(len(authorizations), 1)
        self.assertEqual(
            [item for item in outcomes if isinstance(item, ReferenceAuthorizationErrorCode)],
            [ReferenceAuthorizationErrorCode.EVIDENCE_CORRUPT],
        )
        history = self.repository.inspect_reference_operation("raced")
        assert history is not None
        self.assertEqual(history.operation_events[-1].operation_state, ReferenceOperationState.CAPTURED)
        authorizations[0].close()


if __name__ == "__main__":
    unittest.main()
