"""Invocation-local state machine for bounded agent-directed context."""

from __future__ import annotations

import hashlib
import re
import unittest
from dataclasses import FrozenInstanceError, fields, replace
from unittest import mock

from story_writing_agents.agent_context import service as service_module
from story_writing_agents.agent_context.actions import (
    CONTEXT_ACTION_POLICY,
    ContextActionCorrelation,
    ContextActionError,
    ContextActionErrorCode,
    ContextActionPolicy,
    ContextAnswerAction,
    ContextReadAction,
    ContextSearchAction,
    ContextSearchQuery,
    ContextSourceBoundary,
    ContextSourceHandle,
)
from story_writing_agents.agent_context.manifest import (
    ContextAuthorityKind,
    ContextManifest,
    ContextManifestEntry,
    ContextPriority,
    ContextProvenance,
    ContextScope,
    ContextSemanticChannel,
    ContextSourceKind,
    ContextSubjectKind,
)
from story_writing_agents.agent_context.planning import (
    MandatoryReferenceReservation,
)
from story_writing_agents.agent_context.service import (
    ContextInvocationBinding,
    ContextInvocationSession,
    ContextInvocationState,
    ContextReadReceipt,
)
from story_writing_agents.agent_context.sources import (
    CONTEXT_SOURCE_POLICY,
    ContextSourceDescriptor,
    ContextSourceError,
    ContextSourceErrorCode,
    ContextSourcePolicy,
    ContextSourceRecord,
    ContextSourceRegistry,
    ContextSourceSearchMatch,
    ContextSourceSnapshot,
)


_PRIVATE_ANSWER = "PRIVATE_FINAL_ANSWER_한🙂"
_PRIVATE_BODY_A = "PRIVATE_SOURCE_A needle 한🙂\n"
_PRIVATE_BODY_B = "PRIVATE_SOURCE_B needle\n"
_SCOPE = ContextScope(
    "workspace",
    "session",
    ContextSubjectKind.CONVERSATION,
    "conversation",
    "lead_orchestrator",
)


def _digest(body: bytes) -> str:
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def _artifact_id(path: str) -> str:
    return "writing_artifact:sha256:" + hashlib.sha256(path.encode()).hexdigest()


def _snapshot(
    scope: ContextScope,
    path: str,
    body: str,
    *,
    revision_seed: bytes | None = None,
) -> ContextSourceSnapshot:
    encoded = body.encode("utf-8")
    record = ContextSourceRecord(
        "line_1",
        ContextSourceBoundary.artifact_lines(1, 1),
        body,
        body,
        _digest(encoded),
        len(encoded),
    )
    descriptor = ContextSourceDescriptor(
        scope,
        _artifact_id(path),
        ContextSourceKind.WRITING_ARTIFACT,
        _digest(revision_seed or (path + "-revision").encode()),
        ContextProvenance(
            ContextAuthorityKind.WRITING_ARTIFACT_REPOSITORY,
            path,
        ),
        _digest(encoded),
        len(encoded),
        1,
    )
    return ContextSourceSnapshot(descriptor, (record,), encoded)


def _entry(snapshot: ContextSourceSnapshot, ordinal: int) -> ContextManifestEntry:
    descriptor = snapshot.descriptor
    return ContextManifestEntry(
        descriptor.source_id,
        descriptor.source_kind,
        ContextSemanticChannel.FOCUSED_EVIDENCE,
        ContextPriority.P2_WORKING,
        ordinal,
        descriptor.revision,
        descriptor.content_digest,
        descriptor.provenance,
        descriptor.utf8_bytes,
        searchable=True,
    )


def _manifest_fixture(
    scope: ContextScope = _SCOPE,
) -> tuple[ContextManifest, dict[str, ContextSourceSnapshot]]:
    snapshots = (
        _snapshot(scope, "core_documents/global_outline.md", _PRIVATE_BODY_A),
        _snapshot(scope, "core_documents/character_sheets.md", _PRIVATE_BODY_B),
    )
    manifest = ContextManifest(
        scope,
        tuple(_entry(snapshot, index) for index, snapshot in enumerate(snapshots)),
    )
    return manifest, {
        snapshot.descriptor.source_id: snapshot for snapshot in snapshots
    }


def _registry(
    scope: ContextScope,
    snapshots: dict[str, ContextSourceSnapshot],
) -> ContextSourceRegistry:
    registry = object.__new__(ContextSourceRegistry)
    registry.scope = scope
    registry.capture = mock.Mock(
        side_effect=lambda _manifest, entry, _policy: snapshots[entry.source_id]
    )
    registry.search = mock.Mock(return_value=((), False, 0))
    return registry


def _binding(
    manifest: ContextManifest,
    *,
    operation_id: str = "operation",
    result_id: str = "result",
    reference_operation_id: str | None = None,
    references: tuple[MandatoryReferenceReservation, ...] = (),
) -> ContextInvocationBinding:
    return ContextInvocationBinding(
        manifest.scope,
        operation_id,
        result_id,
        manifest,
        reference_operation_id,
        references,
    )


def _correlation(
    binding: ContextInvocationBinding,
    index: int,
    **overrides: object,
) -> ContextActionCorrelation:
    values: dict[str, object] = {
        "scope": binding.scope,
        "operation_id": binding.operation_id,
        "result_correlation_id": binding.result_correlation_id,
        "action_index": index,
    }
    values.update(overrides)
    return ContextActionCorrelation(**values)  # type: ignore[arg-type]


def _reference(
    ordinal: int,
    *,
    reference_id: str | None = None,
    source_id: str = "same-artifact",
    locator: str = "core_documents/global_outline.md",
    digest_character: str = "4",
) -> MandatoryReferenceReservation:
    return MandatoryReferenceReservation(
        ordinal,
        reference_id or f"range-{ordinal}",
        source_id,
        "revision-1",
        ContextProvenance(ContextAuthorityKind.REFERENCE_AUTHORIZATION, locator),
        "sha256:" + digest_character * 64,
        ordinal * 7,
    )


def _session(
    binding: ContextInvocationBinding,
    registry: ContextSourceRegistry,
    *,
    secret: bytes = b"k" * 32,
    action_policy: ContextActionPolicy = CONTEXT_ACTION_POLICY,
    source_policy: ContextSourcePolicy = CONTEXT_SOURCE_POLICY,
) -> ContextInvocationSession:
    with mock.patch(
        "story_writing_agents.agent_context.service.secrets.token_bytes",
        return_value=secret,
    ):
        return ContextInvocationSession(
            binding,
            registry,
            action_policy=action_policy,
            source_policy=source_policy,
        )


class ServiceSurfaceAndBindingTests(unittest.TestCase):
    """Verify the direct surface, immutable binding, and construction boundary."""

    def test_exact_surface_states_fields_and_no_content_in_repr(self) -> None:
        self.assertEqual(
            tuple(service_module.__all__),
            (
                "AgentContextService",
                "ContextExecutionError",
                "ContextExecutionErrorCode",
                "ContextInvocationBinding",
                "ContextInvocationRequest",
                "ContextInvocationResult",
                "ContextInvocationSession",
                "ContextInvocationState",
                "ContextProvider",
                "ContextProviderCallRequest",
                "ContextProviderCallResult",
                "ContextProviderFailureFacts",
                "ContextReferencePreludeRequest",
                "ContextReferencePreludeResult",
                "ContextReadReceipt",
                "ContextScope",
                "ContextSourceClassification",
                "context_provider_failure_facts",
                "record_context_provider_failure",
            ),
        )
        self.assertEqual(
            tuple(item.value for item in ContextInvocationState),
            ("open", "searching", "reading", "answer_authorized", "failed"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(ContextInvocationBinding)),
            (
                "scope",
                "operation_id",
                "result_correlation_id",
                "manifest",
                "reference_operation_id",
                "mandatory_references",
            ),
        )
        manifest, snapshots = _manifest_fixture()
        binding = _binding(manifest)
        self.assertFalse(hasattr(binding, "__dict__"))
        self.assertNotIn(_PRIVATE_BODY_A, repr(binding))
        with self.assertRaises((FrozenInstanceError, TypeError)):
            binding.operation_id = "changed"  # type: ignore[misc]
        session = _session(binding, _registry(_SCOPE, snapshots))
        self.assertEqual(session.state, ContextInvocationState.OPEN)
        self.assertEqual(session.next_action_index, 1)

    def test_binding_rejects_scope_identity_reference_order_and_unpaired_facts(self) -> None:
        manifest, _ = _manifest_fixture()
        references = (_reference(1), _reference(2, digest_character="5"))
        valid = _binding(
            manifest,
            reference_operation_id="reference-operation",
            references=references,
        )
        self.assertEqual(valid.mandatory_references, references)
        other_scope = replace(_SCOPE, session_id="other")
        cases = (
            lambda: ContextInvocationBinding(other_scope, "operation", "result", manifest),
            lambda: ContextInvocationBinding(_SCOPE, "", "result", manifest),
            lambda: _binding(manifest, reference_operation_id="reference-operation"),
            lambda: _binding(manifest, references=references),
            lambda: _binding(manifest, reference_operation_id="reference-operation", references=references[::-1]),
        )
        for case in cases:
            with self.assertRaises(ContextActionError):
                case()

    def test_entropy_failure_and_registry_scope_mismatch_are_content_free(self) -> None:
        manifest, snapshots = _manifest_fixture()
        binding = _binding(manifest)
        registry = _registry(_SCOPE, snapshots)
        registry.scope = replace(_SCOPE, session_id="other")
        with self.assertRaises(ContextActionError):
            _session(binding, registry)
        registry.scope = _SCOPE
        for entropy in (b"short", None):
            with self.subTest(entropy=entropy):
                with mock.patch(
                    "story_writing_agents.agent_context.service.secrets.token_bytes",
                    return_value=entropy,
                ):
                    with self.assertRaises(ContextActionError) as caught:
                        ContextInvocationSession(binding, registry)
                self.assertNotIn("PRIVATE", repr(caught.exception))


class HandleAndReadLifecycleTests(unittest.TestCase):
    """Verify opaque handles and atomic ordered READ behavior."""

    def setUp(self) -> None:
        self.manifest, self.snapshots = _manifest_fixture()
        self.binding = _binding(self.manifest)

    def test_manifest_handles_are_canonical_opaque_and_exclude_oversized_sources(self) -> None:
        registry = _registry(_SCOPE, self.snapshots)
        session = _session(self.binding, registry)
        pairs = session.manifest_handles
        self.assertEqual(tuple(entry for entry, _ in pairs), self.manifest.entries)
        self.assertEqual(len({handle.value for _, handle in pairs}), len(pairs))
        for entry, handle in pairs:
            self.assertRegex(handle.value, r"\Actxh_v1_[0-9a-f]{64}\Z")
            self.assertNotIn(handle.value, repr(handle))
            self.assertNotIn(entry.provenance.locator, repr(handle))
        oversized = replace(
            self.manifest.entries[0],
            source_id=_artifact_id("core_documents/narrative_rules.md"),
            provenance=ContextProvenance(
                ContextAuthorityKind.WRITING_ARTIFACT_REPOSITORY,
                "core_documents/narrative_rules.md",
            ),
            utf8_bytes=CONTEXT_ACTION_POLICY.max_excerpt_utf8_bytes + 1,
            content_digest="sha256:" + "9" * 64,
            revision="sha256:" + "8" * 64,
        )
        manifest = ContextManifest(_SCOPE, (*self.manifest.entries, oversized))
        session = _session(_binding(manifest), _registry(_SCOPE, self.snapshots))
        self.assertNotIn(oversized, tuple(entry for entry, _ in session.manifest_handles))

    def test_handles_are_invocation_bound_unforgeable_and_terminal_on_reuse(self) -> None:
        first = _session(self.binding, _registry(_SCOPE, self.snapshots), secret=b"a" * 32)
        second = _session(self.binding, _registry(_SCOPE, self.snapshots), secret=b"b" * 32)
        first_handle = first.manifest_handles[0][1]
        second_handle = second.manifest_handles[0][1]
        self.assertNotEqual(first_handle, second_handle)
        with self.assertRaises(ContextActionError) as crossed:
            second.read(ContextReadAction(_correlation(self.binding, 1), (first_handle,)))
        self.assertEqual(crossed.exception.code, ContextActionErrorCode.HANDLE_INVALID)
        self.assertEqual(second.state, ContextInvocationState.FAILED)
        forged = ContextSourceHandle(
            first_handle.value[:-1] + ("0" if first_handle.value[-1] != "0" else "1")
        )
        third = _session(self.binding, _registry(_SCOPE, self.snapshots), secret=b"a" * 32)
        with self.assertRaises(ContextActionError):
            third.read(ContextReadAction(_correlation(self.binding, 1), (forged,)))
        fourth = _session(self.binding, _registry(_SCOPE, self.snapshots))
        handle = fourth.manifest_handles[0][1]
        _, receipt = fourth.read(ContextReadAction(_correlation(self.binding, 1), (handle,)))
        fourth.complete_read(receipt)
        with self.assertRaises(ContextActionError) as reused:
            fourth.read(ContextReadAction(_correlation(self.binding, 2), (handle,)))
        self.assertEqual(reused.exception.code, ContextActionErrorCode.HANDLE_INVALID)

    def test_read_captures_canonical_sources_twice_but_returns_requested_order(self) -> None:
        registry = _registry(_SCOPE, self.snapshots)
        seen: list[str] = []
        def capture(_manifest: ContextManifest, entry: ContextManifestEntry, _policy: ContextSourcePolicy) -> ContextSourceSnapshot:
            seen.append(entry.source_id)
            return self.snapshots[entry.source_id]
        registry.capture = mock.Mock(side_effect=capture)
        session = _session(self.binding, registry)
        handles = {entry.source_id: handle for entry, handle in session.manifest_handles}
        requested_ids = tuple(entry.source_id for entry in reversed(self.manifest.entries))
        observation, receipt = session.read(
            ContextReadAction(
                _correlation(self.binding, 1),
                tuple(handles[source_id] for source_id in requested_ids),
            )
        )
        canonical = [entry.source_id for entry in self.manifest.entries]
        self.assertEqual(seen, canonical + canonical)
        self.assertEqual(tuple(item.receipt.source_id for item in observation.evidence), requested_ids)
        self.assertEqual(tuple(item.source_id for item in receipt.evidence), requested_ids)
        self.assertEqual(session.state, ContextInvocationState.READING)
        self.assertNotIn(_PRIVATE_BODY_A, repr(receipt))
        session.complete_read(receipt)
        self.assertEqual(session.state, ContextInvocationState.OPEN)

    def test_second_source_failure_is_atomic_executed_and_clears_every_handle(self) -> None:
        registry = _registry(_SCOPE, self.snapshots)
        canonical = tuple(self.manifest.entries)
        registry.capture = mock.Mock(
            side_effect=(
                self.snapshots[canonical[0].source_id],
                ContextSourceError(ContextSourceErrorCode.SOURCE_MISSING),
            )
        )
        session = _session(self.binding, registry)
        handles = tuple(handle for _, handle in reversed(session.manifest_handles))
        with self.assertRaises(ContextSourceError) as caught:
            session.read(ContextReadAction(_correlation(self.binding, 1), handles))
        self.assertEqual(caught.exception.code, ContextSourceErrorCode.SOURCE_MISSING)
        self.assertEqual(session.state, ContextInvocationState.FAILED)
        self.assertEqual(session.next_action_index, 2)
        self.assertEqual(session.manifest_handles, ())

    def test_preflight_failure_is_not_charged_but_executed_byte_failure_is_charged(self) -> None:
        preflight_policy = replace(
            CONTEXT_ACTION_POLICY,
            max_handles_per_read=1,
        )
        registry = _registry(_SCOPE, self.snapshots)
        session = _session(self.binding, registry, action_policy=preflight_policy)
        with self.assertRaises(ContextActionError) as caught:
            session.read(
                ContextReadAction(
                    _correlation(self.binding, 1),
                    tuple(handle for _, handle in session.manifest_handles),
                )
            )
        self.assertEqual(caught.exception.code, ContextActionErrorCode.READ_LIMIT_EXCEEDED)
        self.assertEqual(session.next_action_index, 1)
        registry.capture.assert_not_called()
        byte_policy = replace(
            CONTEXT_ACTION_POLICY,
            max_snippet_utf8_bytes=10,
            max_excerpt_utf8_bytes=40,
            max_source_read_utf8_bytes=40,
            max_read_utf8_bytes=40,
        )
        registry = _registry(_SCOPE, self.snapshots)
        session = _session(self.binding, registry, action_policy=byte_policy)
        with self.assertRaises(ContextActionError) as executed:
            session.read(
                ContextReadAction(
                    _correlation(self.binding, 1),
                    tuple(handle for _, handle in session.manifest_handles),
                )
            )
        self.assertEqual(executed.exception.code, ContextActionErrorCode.READ_LIMIT_EXCEEDED)
        self.assertEqual(session.next_action_index, 2)
        self.assertGreater(registry.capture.call_count, 0)

    def test_post_selection_drift_and_reentrant_failure_cannot_resurrect_session(self) -> None:
        entry = self.manifest.entries[0]
        stable = self.snapshots[entry.source_id]
        drifted = _snapshot(
            _SCOPE,
            entry.provenance.locator,
            "changed needle\n",
            revision_seed=b"changed",
        )
        registry = _registry(_SCOPE, self.snapshots)
        registry.capture = mock.Mock(side_effect=(stable, drifted))
        session = _session(self.binding, registry)
        handle = dict((item.source_id, value) for item, value in session.manifest_handles)[entry.source_id]
        with self.assertRaises(ContextActionError) as caught:
            session.read(ContextReadAction(_correlation(self.binding, 1), (handle,)))
        self.assertEqual(caught.exception.code, ContextActionErrorCode.HANDLE_INVALID)
        self.assertEqual(session.state, ContextInvocationState.FAILED)
        registry = _registry(_SCOPE, self.snapshots)
        session = _session(self.binding, registry)
        handle = session.manifest_handles[0][1]
        def reentrant(*_args: object) -> ContextSourceSnapshot:
            session.fail()
            return self.snapshots[session.binding.manifest.entries[0].source_id]
        registry.capture = mock.Mock(side_effect=reentrant)
        with self.assertRaises(ContextActionError) as reentered:
            session.read(ContextReadAction(_correlation(self.binding, 1), (handle,)))
        self.assertEqual(reentered.exception.code, ContextActionErrorCode.INVALID_TRANSITION)
        self.assertEqual(session.state, ContextInvocationState.FAILED)

    def test_outstanding_read_requires_exact_receipt_and_blocks_answer(self) -> None:
        registry = _registry(_SCOPE, self.snapshots)
        session = _session(self.binding, registry)
        handle = session.manifest_handles[0][1]
        _, receipt = session.read(ContextReadAction(_correlation(self.binding, 1), (handle,)))
        with self.assertRaises(ContextActionError) as blocked:
            session.authorize_answer(ContextAnswerAction(_correlation(self.binding, 2), _PRIVATE_ANSWER, (handle,)))
        self.assertEqual(blocked.exception.code, ContextActionErrorCode.OUTSTANDING_READ)
        self.assertEqual(session.state, ContextInvocationState.FAILED)
        session = _session(self.binding, _registry(_SCOPE, self.snapshots))
        handle = session.manifest_handles[0][1]
        _, receipt = session.read(ContextReadAction(_correlation(self.binding, 1), (handle,)))
        wrong = replace(receipt, observation_digest="sha256:" + "f" * 64)
        with self.assertRaises(ContextActionError) as mismatch:
            session.complete_read(wrong)
        self.assertEqual(mismatch.exception.code, ContextActionErrorCode.OUTSTANDING_READ)
        self.assertEqual(session.state, ContextInvocationState.FAILED)


class SearchAndAnswerLifecycleTests(unittest.TestCase):
    """Verify bounded SEARCH and terminal answer authorization facts."""

    def setUp(self) -> None:
        self.manifest, self.snapshots = _manifest_fixture()
        self.binding = _binding(self.manifest)

    def test_search_preserves_query_match_order_mints_handles_and_rejects_reuse(self) -> None:
        registry = _registry(_SCOPE, self.snapshots)
        entries = tuple(self.manifest.entries)
        matches = tuple(
            ContextSourceSearchMatch(
                index,
                self.snapshots[entry.source_id].descriptor,
                self.snapshots[entry.source_id].records[0],
                f"needle {index}",
                len(f"needle {index}".encode()),
            )
            for index, entry in enumerate(reversed(entries), start=1)
        )
        registry.search = mock.Mock(return_value=(matches, False, sum(item.descriptor.utf8_bytes for item in matches)))
        session = _session(self.binding, registry)
        queries = (
            ContextSearchQuery("needle", (ContextSourceKind.WRITING_ARTIFACT,)),
            ContextSearchQuery("private", (ContextSourceKind.WRITING_ARTIFACT,)),
        )
        observation = session.search(ContextSearchAction(_correlation(self.binding, 1), queries))
        self.assertEqual(tuple(hit.query_index for hit in observation.hits), (1, 2))
        self.assertEqual(tuple(hit.source_id for hit in observation.hits), tuple(item.descriptor.source_id for item in matches))
        self.assertTrue(all(re.fullmatch(r"ctxh_v1_[0-9a-f]{64}", hit.handle.value) for hit in observation.hits))
        self.assertEqual(session.next_action_index, 2)
        registry.search.assert_called_once()

    def test_search_preflight_and_executed_failures_charge_differently(self) -> None:
        registry = _registry(_SCOPE, self.snapshots)
        session = _session(self.binding, registry)
        wrong = replace(_correlation(self.binding, 1), result_correlation_id="wrong")
        with self.assertRaises(ContextActionError) as preflight:
            session.search(ContextSearchAction(wrong, (ContextSearchQuery("needle", (ContextSourceKind.WRITING_ARTIFACT,)),)))
        self.assertEqual(preflight.exception.code, ContextActionErrorCode.CORRELATION_MISMATCH)
        self.assertEqual(session.next_action_index, 1)
        registry.search.assert_not_called()
        registry = _registry(_SCOPE, self.snapshots)
        registry.search = mock.Mock(side_effect=ContextSourceError(ContextSourceErrorCode.SOURCE_DRIFT))
        session = _session(self.binding, registry)
        with self.assertRaises(ContextSourceError):
            session.search(ContextSearchAction(_correlation(self.binding, 1), (ContextSearchQuery("needle", (ContextSourceKind.WRITING_ARTIFACT,)),)))
        self.assertEqual(session.next_action_index, 2)
        self.assertEqual(session.state, ContextInvocationState.FAILED)

    def test_every_correlation_dimension_is_exact_and_preflight_noncharging(self) -> None:
        other_scope = replace(_SCOPE, session_id="other")
        invalid = (
            replace(_correlation(self.binding, 1), scope=other_scope),
            replace(_correlation(self.binding, 1), operation_id="other"),
            replace(_correlation(self.binding, 1), result_correlation_id="other"),
            replace(_correlation(self.binding, 1), action_index=2),
        )
        query = ContextSearchQuery(
            "needle",
            (ContextSourceKind.WRITING_ARTIFACT,),
        )
        for correlation in invalid:
            with self.subTest(correlation=correlation):
                registry = _registry(_SCOPE, self.snapshots)
                session = _session(self.binding, registry)
                with self.assertRaises(ContextActionError) as caught:
                    session.search(ContextSearchAction(correlation, (query,)))
                self.assertEqual(
                    caught.exception.code,
                    ContextActionErrorCode.CORRELATION_MISMATCH,
                )
                self.assertEqual(session.next_action_index, 1)
                registry.search.assert_not_called()

    def test_nonterminal_and_provider_call_caps_leave_the_terminal_slot_exact(self) -> None:
        policy = replace(
            CONTEXT_ACTION_POLICY,
            max_nonterminal_actions=2,
            max_provider_calls=3,
            max_search_actions=2,
        )
        registry = _registry(_SCOPE, self.snapshots)
        session = _session(self.binding, registry, action_policy=policy)
        query = ContextSearchQuery(
            "absent",
            (ContextSourceKind.WRITING_ARTIFACT,),
        )
        for index in (1, 2):
            session.search(
                ContextSearchAction(_correlation(self.binding, index), (query,))
            )
        self.assertEqual(session.next_action_index, 3)
        authorization = session.authorize_answer(
            ContextAnswerAction(_correlation(self.binding, 3), _PRIVATE_ANSWER)
        )
        self.assertEqual(
            authorization.correlation.action_index,
            policy.max_provider_calls,
        )

    def test_search_trims_tail_to_frame_budget_and_empty_frame_failure_is_terminal(self) -> None:
        entry = self.manifest.entries[0]
        snapshot = self.snapshots[entry.source_id]
        matches = tuple(
            ContextSourceSearchMatch(1, snapshot.descriptor, snapshot.records[0], "needle", 6)
            for _ in range(3)
        )
        # Give each logical hit a distinct record identity accepted by the observation.
        matches = tuple(
            replace(
                match,
                record=replace(
                    match.record,
                    record_id=f"line_{index}",
                    boundary=ContextSourceBoundary.artifact_lines(index, index),
                ),
            )
            for index, match in enumerate(matches, start=1)
        )
        registry = _registry(_SCOPE, self.snapshots)
        registry.search = mock.Mock(return_value=(matches, False, snapshot.descriptor.utf8_bytes))
        policy = replace(
            CONTEXT_ACTION_POLICY,
            max_snippet_utf8_bytes=100,
            max_excerpt_utf8_bytes=200,
            max_source_read_utf8_bytes=300,
            max_read_utf8_bytes=400,
            max_observation_utf8_bytes=900,
        )
        session = _session(self.binding, registry, action_policy=policy)
        observation = session.search(ContextSearchAction(_correlation(self.binding, 1), (ContextSearchQuery("needle", (ContextSourceKind.WRITING_ARTIFACT,)),)))
        self.assertTrue(observation.truncated)
        self.assertLess(len(observation.hits), len(matches))
        tiny = replace(
            CONTEXT_ACTION_POLICY,
            max_snippet_characters=1,
            max_snippet_utf8_bytes=1,
            max_excerpt_utf8_bytes=1,
            max_source_read_utf8_bytes=1,
            max_read_utf8_bytes=1,
            max_observation_utf8_bytes=1,
        )
        registry = _registry(_SCOPE, self.snapshots)
        session = _session(self.binding, registry, action_policy=tiny)
        with self.assertRaises(ContextActionError) as caught:
            session.search(ContextSearchAction(_correlation(self.binding, 1), (ContextSearchQuery("x", (ContextSourceKind.WRITING_ARTIFACT,)),)))
        self.assertEqual(caught.exception.code, ContextActionErrorCode.OBSERVATION_LIMIT_EXCEEDED)
        self.assertEqual(session.next_action_index, 2)

    def test_immediate_answer_has_fresh_bound_content_free_authorization_and_cleans_session(self) -> None:
        references = (
            _reference(1, reference_id="whole", digest_character="4"),
            _reference(2, reference_id="range-2-4", digest_character="5"),
            _reference(3, reference_id="range-8-9", digest_character="6"),
        )
        binding = _binding(
            self.manifest,
            reference_operation_id="reference-operation",
            references=references,
        )
        registry = _registry(_SCOPE, self.snapshots)
        session = _session(binding, registry)
        authorization = session.authorize_answer(
            ContextAnswerAction(_correlation(binding, 1), _PRIVATE_ANSWER)
        )
        self.assertRegex(authorization.authorization_id, r"\Actxa_v1_[0-9a-f]{64}\Z")
        self.assertEqual(authorization.answer_digest, _digest(_PRIVATE_ANSWER.encode("utf-8")))
        self.assertRegex(authorization.manifest_digest, r"\Asha256:[0-9a-f]{64}\Z")
        self.assertRegex(authorization.completed_read_digest, r"\Asha256:[0-9a-f]{64}\Z")
        self.assertEqual(authorization.completed_evidence, ())
        self.assertEqual(authorization.used_evidence, ())
        self.assertIs(authorization.mandatory_references, references)
        self.assertEqual(tuple(item.ordinal for item in authorization.mandatory_references), (1, 2, 3))
        self.assertEqual(session.state, ContextInvocationState.ANSWER_AUTHORIZED)
        self.assertEqual(session.manifest_handles, ())
        registry.capture.assert_not_called()
        rendered = repr(authorization)
        for canary in (_PRIVATE_ANSWER, _PRIVATE_BODY_A, "ctxh_v1_"):
            self.assertNotIn(canary, rendered)
        alternate = _session(binding, _registry(_SCOPE, self.snapshots))
        different = alternate.authorize_answer(
            ContextAnswerAction(_correlation(binding, 1), _PRIVATE_ANSWER + " changed")
        )
        self.assertNotEqual(different.authorization_id, authorization.authorization_id)

    def test_read_backed_answer_binds_all_completed_used_subset_and_final_canonical_revalidation(self) -> None:
        registry = _registry(_SCOPE, self.snapshots)
        seen: list[str] = []
        def capture(_manifest: ContextManifest, entry: ContextManifestEntry, _policy: ContextSourcePolicy) -> ContextSourceSnapshot:
            seen.append(entry.source_id)
            return self.snapshots[entry.source_id]
        registry.capture = mock.Mock(side_effect=capture)
        session = _session(self.binding, registry)
        pairs = tuple(reversed(session.manifest_handles))
        observation, receipt = session.read(
            ContextReadAction(_correlation(self.binding, 1), tuple(handle for _, handle in pairs))
        )
        session.complete_read(receipt)
        used = (pairs[0][1],)
        authorization = session.authorize_answer(
            ContextAnswerAction(_correlation(self.binding, 2), _PRIVATE_ANSWER, used)
        )
        requested_ids = tuple(entry.source_id for entry, _ in pairs)
        self.assertEqual(tuple(item.source_id for item in authorization.completed_evidence), requested_ids)
        self.assertEqual(tuple(item.handle for item in authorization.used_evidence), used)
        canonical_ids = [entry.source_id for entry in self.manifest.entries]
        self.assertEqual(seen, canonical_ids + canonical_ids + canonical_ids)
        self.assertEqual(session.manifest_handles, ())
        self.assertEqual(tuple(item.receipt.source_id for item in observation.evidence), requested_ids)

    def test_unused_or_substituted_handle_cannot_authorize_and_baseexception_cleans(self) -> None:
        registry = _registry(_SCOPE, self.snapshots)
        session = _session(self.binding, registry)
        unused = session.manifest_handles[0][1]
        with self.assertRaises(ContextActionError) as unauthorized:
            session.authorize_answer(ContextAnswerAction(_correlation(self.binding, 1), _PRIVATE_ANSWER, (unused,)))
        self.assertEqual(unauthorized.exception.code, ContextActionErrorCode.ANSWER_NOT_AUTHORIZED)
        self.assertEqual(session.state, ContextInvocationState.FAILED)
        for failure in (
            KeyboardInterrupt("PRIVATE_INTERRUPT"),
            SystemExit("PRIVATE_EXIT"),
        ):
            with self.subTest(failure=type(failure).__name__):
                registry = _registry(_SCOPE, self.snapshots)
                registry.search = mock.Mock(side_effect=failure)
                session = _session(self.binding, registry)
                with self.assertRaises(type(failure)):
                    session.search(ContextSearchAction(_correlation(self.binding, 1), (ContextSearchQuery("needle", (ContextSourceKind.WRITING_ARTIFACT,)),)))
                self.assertEqual(session.state, ContextInvocationState.FAILED)
                self.assertEqual(session.next_action_index, 2)
                self.assertEqual(session.manifest_handles, ())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
