"""Invocation-local state machine for bounded agent-directed context."""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from dataclasses import FrozenInstanceError, fields, replace
from unittest import mock

import story_writing_agents
from story_writing_agents import agent_context as agent_context_package
from story_writing_agents.agent_context import service as service_module
from story_writing_agents.agent_context.accounting import canonical_provider_json
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


def _search_matches(
    manifest: ContextManifest,
    snapshots: dict[str, ContextSourceSnapshot],
) -> tuple[ContextSourceSearchMatch, ...]:
    return tuple(
        ContextSourceSearchMatch(
            1,
            snapshots[entry.source_id].descriptor,
            snapshots[entry.source_id].records[0],
            "needle",
            len(b"needle"),
        )
        for entry in manifest.entries
        if entry.source_id in snapshots
    )


def _mint_handles(
    session: ContextInvocationSession,
    registry: ContextSourceRegistry,
    snapshots: dict[str, ContextSourceSnapshot],
) -> tuple[tuple[ContextManifestEntry, ContextSourceHandle], ...]:
    matches = _search_matches(session.binding.manifest, snapshots)
    registry.search = mock.Mock(
        return_value=(
            matches,
            False,
            sum(match.descriptor.utf8_bytes for match in matches),
        )
    )
    observation = session.search(
        ContextSearchAction(
            _correlation(session.binding, session.next_action_index),
            (
                ContextSearchQuery(
                    "needle",
                    (ContextSourceKind.WRITING_ARTIFACT,),
                ),
            ),
        )
    )
    by_id = {entry.source_id: entry for entry in session.binding.manifest.entries}
    return tuple((by_id[hit.source_id], hit.handle) for hit in observation.hits)


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
    source_aliases: dict[str, str] | None = None,
    allowed_search_source_kinds: tuple[ContextSourceKind, ...] = (
        service_module._SEARCH_SOURCE_KIND_ORDER
    ),
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
            source_aliases=source_aliases,
            allowed_search_source_kinds=allowed_search_source_kinds,
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
                "ContextExecutionEvent",
                "ContextExecutionEventKind",
                "ContextExecutionEventSink",
                "ContextInvocationBinding",
                "ContextInvocationRequest",
                "ContextInvocationResult",
                "ContextInvocationSession",
                "ContextInvocationState",
                "ContextProvider",
                "ContextProviderCallFinalizer",
                "ContextProviderCallRequest",
                "ContextProviderCallResult",
                "ContextProviderFailureFacts",
                "ContextProviderTerminalCode",
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
            tuple(field.name for field in fields(service_module.ContextExecutionEvent)),
            (
                "kind",
                "scope",
                "operation_id",
                "phase",
                "round_number",
                "action_index",
                "selected_source_count",
                "selected_source_utf8_bytes",
                "omitted_searchable_source_count",
                "omitted_searchable_source_utf8_bytes",
                "search_hit_count",
                "retrieved_source_count",
                "retrieved_source_utf8_bytes",
                "mandatory_reference_count",
                "mandatory_reference_utf8_bytes",
                "provider_input_tokens",
                "transcript_turn_count",
                "transcript_event_count",
                "observation_utf8_bytes",
                "truncated",
            ),
        )
        progress = service_module.ContextExecutionEvent(
            service_module.ContextExecutionEventKind.BOOTSTRAP,
            _SCOPE,
            "operation",
            selected_source_count=2,
            selected_source_utf8_bytes=128,
            omitted_searchable_source_count=1,
            omitted_searchable_source_utf8_bytes=64,
            mandatory_reference_count=0,
            mandatory_reference_utf8_bytes=0,
        )
        self.assertFalse(hasattr(progress, "__dict__"))
        for private in (_PRIVATE_ANSWER, _PRIVATE_BODY_A, _PRIVATE_BODY_B):
            self.assertNotIn(private, repr(progress))
        with self.assertRaises((FrozenInstanceError, TypeError)):
            progress.operation_id = "changed"  # type: ignore[misc]
        self.assertEqual(
            tuple(item.value for item in ContextInvocationState),
            ("open", "searching", "reading", "answer_authorized", "failed"),
        )
        self.assertEqual(
            tuple(item.value for item in service_module.ContextProviderTerminalCode),
            (
                "malformed_structured_response",
                "response_correlation_mismatch",
                "response_semantic_rejected",
                "action_exhausted",
                "answer_validation_failed",
                "reference_runtime_failed",
                "context_runtime_failed",
            ),
        )
        for facade in (story_writing_agents, agent_context_package):
            self.assertFalse(hasattr(facade, "ContextProviderCallFinalizer"))
            self.assertFalse(hasattr(facade, "ContextProviderTerminalCode"))
        self.assertEqual(
            tuple(
                field.name
                for field in fields(service_module.ContextProviderCallResult)
            ),
            ("response_text", "thread_id", "accounting", "finalizer"),
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


class CompactWireDecoderTests(unittest.TestCase):
    """Verify the complete strict wire-v5 response contract in isolation."""

    def setUp(self) -> None:
        manifest, _snapshots = _manifest_fixture()
        self.correlation = _correlation(_binding(manifest), 1)
        self.token = service_module._correlation_token(self.correlation)

    @staticmethod
    def _resolve_handle(value: object) -> ContextSourceHandle:
        if type(value) is not str or not value.startswith("h"):
            return ContextSourceHandle(value)  # type: ignore[arg-type]
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return ContextSourceHandle(f"ctxh_v1_{digest}")

    def decode(
        self,
        action: object,
        *,
        allowed: tuple[str, ...] = ("search", "read", "answer"),
        contract: object = "text",
        source_kinds: tuple[ContextSourceKind, ...] = (
            service_module._SEARCH_SOURCE_KIND_ORDER
        ),
    ) -> ContextSearchAction | ContextReadAction | ContextAnswerAction:
        response = (
            action
            if type(action) is str
            else json.dumps(action, ensure_ascii=False, separators=(",", ":"))
        )
        assert isinstance(response, str)
        return service_module._decode_context_action(
            response,
            correlation=self.correlation,
            allowed_actions=allowed,
            result_contract=contract,
            allowed_search_source_kinds=source_kinds,
            resolve_handle=self._resolve_handle,
        )

    def assert_malformed(
        self,
        action: object,
        *,
        allowed: tuple[str, ...] = ("search", "read", "answer"),
        contract: object = "text",
        source_kinds: tuple[ContextSourceKind, ...] = (
            service_module._SEARCH_SOURCE_KIND_ORDER
        ),
    ) -> None:
        with self.assertRaises(service_module.ContextExecutionError) as caught:
            self.decode(
                action,
                allowed=allowed,
                contract=contract,
                source_kinds=source_kinds,
            )
        self.assertIs(
            caught.exception.code,
            service_module.ContextExecutionErrorCode.MALFORMED_ACTION,
        )
        self.assertIsNone(caught.exception.__cause__)

    def test_schema_and_full_correlation_token_are_exact(self) -> None:
        schema = service_module._context_action_schema()
        self.assertEqual(
            schema,
            {
                "additionalProperties": False,
                "properties": {
                    "a": {"enum": ["a", "r", "s"]},
                    "c": {"type": "string"},
                    "d": {"items": {}, "type": "array"},
                    "u": {"items": {"type": "string"}, "type": "array"},
                },
                "required": ["a", "c", "d", "u"],
                "type": "object",
            },
        )
        self.assertEqual(len(canonical_provider_json(schema)), 218)
        self.assertRegex(self.token, r"\A[A-Za-z0-9_-]{43}\Z")
        variants = (
            replace(
                self.correlation,
                scope=replace(self.correlation.scope, session_id="other"),
            ),
            replace(self.correlation, operation_id="other"),
            replace(self.correlation, result_correlation_id="other"),
            replace(self.correlation, action_index=2),
        )
        tokens = (self.token,) + tuple(
            service_module._correlation_token(value) for value in variants
        )
        self.assertEqual(len(set(tokens)), len(tokens))
        compare = service_module.hmac.compare_digest
        with mock.patch.object(
            service_module.hmac,
            "compare_digest",
            wraps=compare,
        ) as checked:
            self.assertIsInstance(
                self.decode(
                    {"a": "a", "c": self.token, "d": ["answer"], "u": []},
                    allowed=("answer",),
                ),
                ContextAnswerAction,
            )
        checked.assert_called_once()

    def test_valid_search_read_and_natural_result_shapes_decode_exactly(self) -> None:
        search = self.decode(
            {
                "a": "s",
                "c": self.token,
                "d": [["Needle", ["w", "c", "d", "o"]]],
                "u": [],
            },
            allowed=("search", "answer"),
        )
        self.assertIsInstance(search, ContextSearchAction)
        assert isinstance(search, ContextSearchAction)
        self.assertEqual(search.queries[0].normalized_text, "needle")
        self.assertEqual(
            search.queries[0].source_kinds,
            service_module._SEARCH_SOURCE_KIND_ORDER,
        )

        read = self.decode(
            {"a": "r", "c": self.token, "d": ["h1", "h2"], "u": []},
            allowed=("read", "answer"),
        )
        self.assertIsInstance(read, ContextReadAction)
        assert isinstance(read, ContextReadAction)
        answer_handle = read.handles[0]

        cases = (
            ("text", "visible text", "visible text"),
            (
                ["retrieval", "text", "citations"],
                ["visible retrieval", [1, 2]],
                '["visible retrieval",[1,2]]',
            ),
            (["audit", "approve-or-conflict"], "a", '"a"'),
            (
                ["audit", "approve-or-conflict"],
                ["c", "reason"],
                '["c","reason"]',
            ),
            (
                ["finalize", "path-and-replacement-list"],
                [["global_outline.md", "replacement"], ["rules.md", ""]],
                '[["global_outline.md","replacement"],["rules.md",""]]',
            ),
            (["finalize", "path-and-replacement-list"], [], "[]"),
        )
        for contract, result, expected in cases:
            with self.subTest(contract=contract, result=result):
                decoded = self.decode(
                    {
                        "a": "a",
                        "c": self.token,
                        "d": [result],
                        "u": ["h1"] if contract == "text" else [],
                    },
                    allowed=("answer",),
                    contract=contract,
                )
                self.assertIsInstance(decoded, ContextAnswerAction)
                assert isinstance(decoded, ContextAnswerAction)
                self.assertEqual(decoded.answer, expected)
                self.assertEqual(
                    decoded.used_handles,
                    (answer_handle,) if contract == "text" else (),
                )

    def test_search_wire_supports_shared_artifact_prefix_and_kind_matrix(self) -> None:
        unprefixed = self.decode(
            {
                "a": "s",
                "c": self.token,
                "d": [
                    ["First", ["w"]],
                    ["Second", ["c", "d", "o"]],
                ],
                "u": [],
            },
            allowed=("search",),
        )
        self.assertIsInstance(unprefixed, ContextSearchAction)
        assert isinstance(unprefixed, ContextSearchAction)
        self.assertEqual(
            tuple(
                (
                    query.normalized_text,
                    query.source_kinds,
                    query.artifact_path_prefix,
                )
                for query in unprefixed.queries
            ),
            (
                ("first", (ContextSourceKind.WRITING_ARTIFACT,), None),
                (
                    "second",
                    (
                        ContextSourceKind.CONVERSATION,
                        ContextSourceKind.ACTIVE_DEBATE,
                        ContextSourceKind.OWN_AGENT_STATE,
                    ),
                    None,
                ),
            ),
        )

        prefix = "manuscript/part-01/"
        prefixed = self.decode(
            {
                "a": "s",
                "c": self.token,
                "d": [prefix, ["Alpha", ["w"]], ["Beta", ["w"]]],
                "u": [],
            },
            allowed=("search",),
        )
        self.assertIsInstance(prefixed, ContextSearchAction)
        assert isinstance(prefixed, ContextSearchAction)
        self.assertEqual(
            tuple(query.artifact_path_prefix for query in prefixed.queries),
            (prefix, prefix),
        )
        self.assertEqual(
            tuple(query.normalized_text for query in prefixed.queries),
            ("alpha", "beta"),
        )

        for code, kind in zip(
            ("w", "c", "d", "o"),
            service_module._SEARCH_SOURCE_KIND_ORDER,
            strict=True,
        ):
            with self.subTest(code=code):
                action = self.decode(
                    {
                        "a": "s",
                        "c": self.token,
                        "d": [["needle", [code]]],
                        "u": [],
                    },
                    allowed=("search",),
                    source_kinds=(kind,),
                )
                assert isinstance(action, ContextSearchAction)
                self.assertEqual(action.queries[0].source_kinds, (kind,))

    def test_artifact_prefix_validation_is_exact_and_one_scope_per_search(self) -> None:
        valid = (
            "core_documents/",
            "manuscript/",
            "manuscript/part-01/",
            "manuscript/part-01/scene.md/",
            "manuscript/space dir/",
            "manuscript/.hidden%25/",
            "manuscript/private-\ue000/",
            "manuscript/unassigned-\u0378/",
        )
        for prefix in valid:
            with self.subTest(valid=prefix):
                query = ContextSearchQuery(
                    "needle",
                    (ContextSourceKind.WRITING_ARTIFACT,),
                    prefix,
                )
                self.assertEqual(query.artifact_path_prefix, prefix)

        invalid = (
            "",
            "/manuscript/",
            "manuscript",
            "manuscript\\part-01/",
            "manuscript//part-01/",
            "manuscript/./",
            "manuscript/../",
            "discussion_logs/",
            "core_documents/nested/",
            "manuscript/part-01/chapter.md",
            "manuscript/}/",
            "manuscript/\u2028/",
            "manuscript/\u2029/",
            "manuscript/new\nline/",
            "manuscript/joiner-\u200d/",
            "manuscript/surrogate-\ud800/",
        )
        for prefix in invalid:
            with self.subTest(invalid=repr(prefix)), self.assertRaises(
                ContextActionError
            ):
                ContextSearchQuery(
                    "needle",
                    (ContextSourceKind.WRITING_ARTIFACT,),
                    prefix,
                )

        with self.assertRaises(ContextActionError):
            ContextSearchQuery(
                "needle",
                (ContextSourceKind.CONVERSATION,),
                "manuscript/",
            )
        with self.assertRaises(ContextActionError):
            ContextSearchAction(
                self.correlation,
                (
                    ContextSearchQuery(
                        "one",
                        (ContextSourceKind.WRITING_ARTIFACT,),
                        "manuscript/part-01/",
                    ),
                    ContextSearchQuery(
                        "two",
                        (ContextSourceKind.WRITING_ARTIFACT,),
                        "manuscript/part-02/",
                    ),
                ),
            )

    def test_malformed_exact_shape_types_routes_duplicates_and_limits_reject(self) -> None:
        base = {"a": "a", "c": self.token, "d": ["answer"], "u": []}
        malformed = (
            {},
            {**base, "extra": 1},
            {key: value for key, value in base.items() if key != "u"},
            {**base, "a": "unknown"},
            {**base, "c": "A" * 43},
            {**base, "c": True},
            {**base, "d": True},
            {**base, "u": True},
            {**base, "d": []},
            {**base, "d": ["one", "two"]},
            {**base, "u": ["h1", "h1"]},
            '{"a":"a","a":"a","c":"%s","d":["answer"],"u":[]}'
            % self.token,
        )
        for value in malformed:
            with self.subTest(value=value):
                self.assert_malformed(value)

        self.assert_malformed(base, allowed=("search",))
        self.assert_malformed(
            {"a": "s", "c": self.token, "d": [["x", ["c"]]], "u": []},
            source_kinds=(ContextSourceKind.WRITING_ARTIFACT,),
        )
        self.assert_malformed(
            {
                "a": "s",
                "c": self.token,
                "d": [["x", ["w"]], ["x", ["w"]]],
                "u": [],
            }
        )
        for data in (
            [],
            ["manuscript/"],
            [["x", ["w"], "manuscript/"]],
            ["manuscript/", ["x", ["w"], "legacy"]],
            [["x", ["w"]], "manuscript/"],
            ["manuscript/", ["x", ["w"]], ["x", ["w"]]],
            ["manuscript/part-01/chapter.md", ["x", ["w"]]],
            ["manuscript/", ["x", ["c"]]],
        ):
            with self.subTest(search_data=data):
                self.assert_malformed(
                    {"a": "s", "c": self.token, "d": data, "u": []}
                )
        self.assert_malformed(
            {
                "a": "s",
                "c": self.token,
                "d": ["manuscript/", ["x", ["w"]]],
                "u": [],
            },
            source_kinds=(ContextSourceKind.CONVERSATION,),
        )
        self.assert_malformed(
            {"a": "r", "c": self.token, "d": ["h1", "h1"], "u": []}
        )
        self.assert_malformed(
            {"a": "r", "c": self.token, "d": ["h1"], "u": ["h1"]}
        )
        self.assert_malformed(
            {
                "a": "s",
                "c": self.token,
                "d": [
                    [f"q{index}", ["w"]]
                    for index in range(CONTEXT_ACTION_POLICY.max_queries_per_search + 1)
                ],
                "u": [],
            }
        )
        self.assert_malformed(
            {
                "a": "r",
                "c": self.token,
                "d": [
                    f"h{index}"
                    for index in range(CONTEXT_ACTION_POLICY.max_handles_per_read + 1)
                ],
                "u": [],
            }
        )

    def test_double_encoded_or_invalid_structured_results_reject(self) -> None:
        cases = (
            (
                ["retrieval", "text", "citations"],
                json.dumps(["reply", [1]]),
            ),
            (["audit", "approve-or-conflict"], json.dumps("a")),
            (
                ["finalize", "path-and-replacement-list"],
                json.dumps([["path", "replacement"]]),
            ),
            (["audit", "approve-or-conflict"], ["c", ""]),
            (
                ["finalize", "path-and-replacement-list"],
                [["path", "one"], ["path", "two"]],
            ),
            ("text", ""),
        )
        for contract, result in cases:
            with self.subTest(contract=contract, result=result):
                self.assert_malformed(
                    {"a": "a", "c": self.token, "d": [result], "u": []},
                    allowed=("answer",),
                    contract=contract,
                )


class HandleAndReadLifecycleTests(unittest.TestCase):
    """Verify opaque handles and atomic ordered READ behavior."""

    def setUp(self) -> None:
        self.manifest, self.snapshots = _manifest_fixture()
        self.binding = _binding(self.manifest)

    def test_initial_read_is_impossible_and_search_handles_are_canonical_opaque(self) -> None:
        registry = _registry(_SCOPE, self.snapshots)
        session = _session(self.binding, registry)
        with self.assertRaises(ContextActionError) as initial_read:
            session.read(
                ContextReadAction(
                    _correlation(self.binding, 1),
                    (ContextSourceHandle("ctxh_v1_" + "0" * 64),),
                )
            )
        self.assertEqual(
            initial_read.exception.code,
            ContextActionErrorCode.HANDLE_INVALID,
        )
        self.assertEqual(session.state, ContextInvocationState.FAILED)

        registry = _registry(_SCOPE, self.snapshots)
        session = _session(self.binding, registry)
        pairs = _mint_handles(session, registry, self.snapshots)
        self.assertEqual(tuple(entry for entry, _ in pairs), self.manifest.entries)
        self.assertEqual(len({handle.value for _, handle in pairs}), len(pairs))
        for entry, handle in pairs:
            self.assertRegex(handle.value, r"\Actxh_v1_[0-9a-f]{64}\Z")
            self.assertNotIn(handle.value, repr(handle))
            self.assertNotIn(entry.provenance.locator, repr(handle))

    def test_handles_are_invocation_bound_unforgeable_and_terminal_on_reuse(self) -> None:
        first_registry = _registry(_SCOPE, self.snapshots)
        second_registry = _registry(_SCOPE, self.snapshots)
        first = _session(self.binding, first_registry, secret=b"a" * 32)
        second = _session(self.binding, second_registry, secret=b"b" * 32)
        first_handle = _mint_handles(first, first_registry, self.snapshots)[0][1]
        second_handle = _mint_handles(second, second_registry, self.snapshots)[0][1]
        self.assertNotEqual(first_handle, second_handle)
        with self.assertRaises(ContextActionError) as crossed:
            second.read(ContextReadAction(_correlation(self.binding, 2), (first_handle,)))
        self.assertEqual(crossed.exception.code, ContextActionErrorCode.HANDLE_INVALID)
        self.assertEqual(second.state, ContextInvocationState.FAILED)
        forged = ContextSourceHandle(
            first_handle.value[:-1] + ("0" if first_handle.value[-1] != "0" else "1")
        )
        third_registry = _registry(_SCOPE, self.snapshots)
        third = _session(self.binding, third_registry, secret=b"a" * 32)
        _mint_handles(third, third_registry, self.snapshots)
        with self.assertRaises(ContextActionError):
            third.read(ContextReadAction(_correlation(self.binding, 2), (forged,)))
        fourth_registry = _registry(_SCOPE, self.snapshots)
        fourth = _session(self.binding, fourth_registry)
        handle = _mint_handles(fourth, fourth_registry, self.snapshots)[0][1]
        _, receipt = fourth.read(ContextReadAction(_correlation(self.binding, 2), (handle,)))
        fourth.complete_read(receipt)
        with self.assertRaises(ContextActionError) as reused:
            fourth.read(ContextReadAction(_correlation(self.binding, 3), (handle,)))
        self.assertEqual(reused.exception.code, ContextActionErrorCode.HANDLE_INVALID)

    def test_release_handle_matrix_preserves_failure_order_and_privacy(self) -> None:
        private_values = (
            _PRIVATE_BODY_A,
            _PRIVATE_BODY_B,
            "core_documents/global_outline.md",
        )

        def assert_failure(
            session: ContextInvocationSession,
            action: ContextReadAction,
            expected: ContextActionErrorCode,
            *,
            next_action_index: int,
            extra_private: tuple[str, ...] = (),
        ) -> ContextActionError:
            with self.assertRaises(ContextActionError) as caught:
                session.read(action)
            self.assertEqual(caught.exception.code, expected)
            self.assertEqual(session.state, ContextInvocationState.FAILED)
            self.assertEqual(session.next_action_index, next_action_index)
            encoded = repr(caught.exception)
            for value in (*private_values, *extra_private):
                self.assertNotIn(value, encoded)
            return caught.exception

        fresh_registry = _registry(_SCOPE, self.snapshots)
        fresh = _session(self.binding, fresh_registry)
        fresh_handle = _mint_handles(
            fresh,
            fresh_registry,
            self.snapshots,
        )[0][1]
        _observation, receipt = fresh.read(
            ContextReadAction(
                _correlation(self.binding, 2),
                (fresh_handle,),
            )
        )
        fresh.complete_read(receipt)
        authorization = fresh.authorize_answer(
            ContextAnswerAction(
                _correlation(self.binding, 3),
                _PRIVATE_ANSWER,
                (fresh_handle,),
            )
        )
        self.assertEqual(
            tuple(item.handle for item in authorization.used_evidence),
            (fresh_handle,),
        )
        self.assertEqual(fresh.state, ContextInvocationState.ANSWER_AUTHORIZED)

        guessed = ContextSourceHandle("ctxh_v1_" + "0" * 64)
        guessed_session = _session(
            self.binding,
            _registry(_SCOPE, self.snapshots),
        )
        assert_failure(
            guessed_session,
            ContextReadAction(
                _correlation(self.binding, 1),
                (guessed,),
            ),
            ContextActionErrorCode.HANDLE_INVALID,
            next_action_index=1,
            extra_private=(guessed.value,),
        )

        foreign_scopes = (
            ("stale_operation", _SCOPE, "stale_operation", "stale_result"),
            (
                "cross_scope",
                replace(_SCOPE, session_id="other_session"),
                "operation",
                "result",
            ),
            (
                "cross_agent",
                replace(_SCOPE, agent_id="plot_architect"),
                "operation",
                "result",
            ),
        )
        for label, foreign_scope, operation_id, result_id in foreign_scopes:
            with self.subTest(handle_origin=label):
                foreign_manifest, foreign_snapshots = _manifest_fixture(
                    foreign_scope
                )
                foreign_binding = _binding(
                    foreign_manifest,
                    operation_id=operation_id,
                    result_id=result_id,
                )
                foreign_registry = _registry(
                    foreign_scope,
                    foreign_snapshots,
                )
                foreign = _session(
                    foreign_binding,
                    foreign_registry,
                    secret=b"z" * 32,
                )
                foreign_handle = _mint_handles(
                    foreign,
                    foreign_registry,
                    foreign_snapshots,
                )[0][1]
                current = _session(
                    self.binding,
                    _registry(_SCOPE, self.snapshots),
                    secret=b"z" * 32,
                )
                assert_failure(
                    current,
                    ContextReadAction(
                        _correlation(self.binding, 1),
                        (foreign_handle,),
                    ),
                    ContextActionErrorCode.HANDLE_INVALID,
                    next_action_index=1,
                    extra_private=(foreign_handle.value,),
                )

        for label, correlation in (
            (
                "cross_scope_correlation",
                replace(
                    _correlation(self.binding, 2),
                    scope=replace(_SCOPE, session_id="other_session"),
                ),
            ),
            (
                "cross_agent_correlation",
                replace(
                    _correlation(self.binding, 2),
                    scope=replace(_SCOPE, agent_id="plot_architect"),
                ),
            ),
        ):
            with self.subTest(failure_order=label):
                registry = _registry(_SCOPE, self.snapshots)
                current = _session(self.binding, registry)
                valid_handle = _mint_handles(
                    current,
                    registry,
                    self.snapshots,
                )[0][1]
                assert_failure(
                    current,
                    ContextReadAction(correlation, (valid_handle, guessed)),
                    ContextActionErrorCode.CORRELATION_MISMATCH,
                    next_action_index=2,
                    extra_private=(valid_handle.value, guessed.value),
                )
                registry.capture.assert_not_called()

        consumed_registry = _registry(_SCOPE, self.snapshots)
        consumed = _session(self.binding, consumed_registry)
        consumed_handle = _mint_handles(
            consumed,
            consumed_registry,
            self.snapshots,
        )[0][1]
        _observation, receipt = consumed.read(
            ContextReadAction(
                _correlation(self.binding, 2),
                (consumed_handle,),
            )
        )
        consumed.complete_read(receipt)
        assert_failure(
            consumed,
            ContextReadAction(
                _correlation(self.binding, 3),
                (consumed_handle,),
            ),
            ContextActionErrorCode.HANDLE_INVALID,
            next_action_index=3,
            extra_private=(consumed_handle.value,),
        )

        drift_entry = self.manifest.entries[0]
        stable = self.snapshots[drift_entry.source_id]
        drifted = _snapshot(
            _SCOPE,
            drift_entry.provenance.locator,
            "changed private body needle\n",
            revision_seed=b"changed-release-revision",
        )
        drift_registry = _registry(_SCOPE, self.snapshots)
        drift = _session(self.binding, drift_registry)
        drift_handle = _mint_handles(
            drift,
            drift_registry,
            self.snapshots,
        )[0][1]
        drift_registry.capture = mock.Mock(side_effect=(stable, drifted))
        assert_failure(
            drift,
            ContextReadAction(
                _correlation(self.binding, 2),
                (drift_handle,),
            ),
            ContextActionErrorCode.HANDLE_INVALID,
            next_action_index=3,
            extra_private=(drift_handle.value, "changed private body"),
        )

        terminal = _session(
            self.binding,
            _registry(_SCOPE, self.snapshots),
        )
        terminal.authorize_answer(
            ContextAnswerAction(
                _correlation(self.binding, 1),
                _PRIVATE_ANSWER,
            )
        )
        assert_failure(
            terminal,
            ContextReadAction(
                _correlation(self.binding, 2),
                (guessed,),
            ),
            ContextActionErrorCode.INVALID_TRANSITION,
            next_action_index=2,
            extra_private=(guessed.value,),
        )

    def test_read_captures_canonical_sources_twice_but_returns_requested_order(self) -> None:
        registry = _registry(_SCOPE, self.snapshots)
        seen: list[str] = []
        def capture(_manifest: ContextManifest, entry: ContextManifestEntry, _policy: ContextSourcePolicy) -> ContextSourceSnapshot:
            seen.append(entry.source_id)
            return self.snapshots[entry.source_id]
        registry.capture = mock.Mock(side_effect=capture)
        session = _session(self.binding, registry)
        handles = {
            entry.source_id: handle
            for entry, handle in _mint_handles(session, registry, self.snapshots)
        }
        requested_ids = tuple(entry.source_id for entry in reversed(self.manifest.entries))
        observation, receipt = session.read(
            ContextReadAction(
                _correlation(self.binding, 2),
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
        handles = tuple(
            handle
            for _, handle in reversed(
                _mint_handles(session, registry, self.snapshots)
            )
        )
        with self.assertRaises(ContextSourceError) as caught:
            session.read(ContextReadAction(_correlation(self.binding, 2), handles))
        self.assertEqual(caught.exception.code, ContextSourceErrorCode.SOURCE_MISSING)
        self.assertEqual(session.state, ContextInvocationState.FAILED)
        self.assertEqual(session.next_action_index, 3)

    def test_preflight_failure_is_not_charged_but_executed_byte_failure_is_charged(self) -> None:
        preflight_policy = replace(
            CONTEXT_ACTION_POLICY,
            max_handles_per_read=1,
        )
        registry = _registry(_SCOPE, self.snapshots)
        session = _session(self.binding, registry, action_policy=preflight_policy)
        handles = tuple(
            handle for _, handle in _mint_handles(session, registry, self.snapshots)
        )
        with self.assertRaises(ContextActionError) as caught:
            session.read(
                ContextReadAction(
                    _correlation(self.binding, 2),
                    handles,
                )
            )
        self.assertEqual(caught.exception.code, ContextActionErrorCode.READ_LIMIT_EXCEEDED)
        self.assertEqual(session.next_action_index, 2)
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
        handles = tuple(
            handle for _, handle in _mint_handles(session, registry, self.snapshots)
        )
        with self.assertRaises(ContextActionError) as executed:
            session.read(
                ContextReadAction(
                    _correlation(self.binding, 2),
                    handles,
                )
            )
        self.assertEqual(executed.exception.code, ContextActionErrorCode.READ_LIMIT_EXCEEDED)
        self.assertEqual(session.next_action_index, 3)
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
        handle = dict(
            _mint_handles(session, registry, self.snapshots)
        )[entry]
        with self.assertRaises(ContextActionError) as caught:
            session.read(ContextReadAction(_correlation(self.binding, 2), (handle,)))
        self.assertEqual(caught.exception.code, ContextActionErrorCode.HANDLE_INVALID)
        self.assertEqual(session.state, ContextInvocationState.FAILED)
        registry = _registry(_SCOPE, self.snapshots)
        session = _session(self.binding, registry)
        handle = _mint_handles(session, registry, self.snapshots)[0][1]
        def reentrant(*_args: object) -> ContextSourceSnapshot:
            session.fail()
            return self.snapshots[session.binding.manifest.entries[0].source_id]
        registry.capture = mock.Mock(side_effect=reentrant)
        with self.assertRaises(ContextActionError) as reentered:
            session.read(ContextReadAction(_correlation(self.binding, 2), (handle,)))
        self.assertEqual(reentered.exception.code, ContextActionErrorCode.INVALID_TRANSITION)
        self.assertEqual(session.state, ContextInvocationState.FAILED)

    def test_outstanding_read_requires_exact_receipt_and_blocks_answer(self) -> None:
        registry = _registry(_SCOPE, self.snapshots)
        session = _session(self.binding, registry)
        handle = _mint_handles(session, registry, self.snapshots)[0][1]
        _, receipt = session.read(ContextReadAction(_correlation(self.binding, 2), (handle,)))
        with self.assertRaises(ContextActionError) as blocked:
            session.authorize_answer(ContextAnswerAction(_correlation(self.binding, 3), _PRIVATE_ANSWER, (handle,)))
        self.assertEqual(blocked.exception.code, ContextActionErrorCode.OUTSTANDING_READ)
        self.assertEqual(session.state, ContextInvocationState.FAILED)
        registry = _registry(_SCOPE, self.snapshots)
        session = _session(self.binding, registry)
        handle = _mint_handles(session, registry, self.snapshots)[0][1]
        _, receipt = session.read(ContextReadAction(_correlation(self.binding, 2), (handle,)))
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

    def test_prefixed_search_filters_before_one_capture_revalidation_pair(self) -> None:
        scoped_body = "needle" + "x" * 979 + "\n"
        self.assertEqual(len(scoped_body.encode("utf-8")), 986)
        snapshots_tuple = (
            _snapshot(
                _SCOPE,
                "manuscript/part_01/chapter.md",
                scoped_body,
            ),
            _snapshot(
                _SCOPE,
                "manuscript/part_010/chapter.md",
                "needle outside exact part prefix\n",
            ),
            _snapshot(
                _SCOPE,
                "core_documents/global_outline.md",
                "needle outside manuscript prefix\n",
            ),
        )
        manifest = ContextManifest(
            _SCOPE,
            tuple(
                _entry(snapshot, index)
                for index, snapshot in enumerate(snapshots_tuple)
            ),
        )
        snapshots = {
            snapshot.descriptor.source_id: snapshot
            for snapshot in snapshots_tuple
        }
        registry = _registry(_SCOPE, snapshots)
        registry.search = ContextSourceRegistry.search.__get__(registry)
        session = _session(_binding(manifest), registry)
        query = ContextSearchQuery(
            "needle",
            (ContextSourceKind.WRITING_ARTIFACT,),
            "manuscript/part_01/",
        )

        observation = session.search(
            ContextSearchAction(
                _correlation(session.binding, 1),
                (query,),
            )
        )

        self.assertEqual(len(observation.hits), 1)
        self.assertEqual(observation.hits[0].query_index, 1)
        self.assertEqual(
            observation.hits[0].source_id,
            snapshots_tuple[0].descriptor.source_id,
        )
        self.assertEqual(registry.capture.call_count, 2)
        captured_manifests = tuple(
            call.args[0] for call in registry.capture.call_args_list
        )
        self.assertTrue(
            all(
                current.entries == (manifest.entries[0],)
                for current in captured_manifests
            )
        )
        self.assertEqual(
            sum(
                snapshots[entry.source_id].descriptor.utf8_bytes
                for entry in captured_manifests[0].entries
            ),
            986,
        )
        self.assertEqual(session._scan_utf8_bytes, 986)

    def test_part_prefix_matrix_scans_nested_before_hit_limits_and_excludes_decoys(
        self,
    ) -> None:
        prefix = "manuscript/part_01/"
        eligible_paths = (
            "manuscript/part_01/chapter_01.md",
            "manuscript/part_01/scenes/chapter_02.md",
            "manuscript/part_01/scenes/memories/chapter_03.md",
        )
        decoy_paths = (
            "manuscript/part_02/chapter_07.md",
            "manuscript/part_010/chapter_99.md",
            "core_documents/narrative_rules.md",
        )
        internal_path = "discussion_logs/private_context.md"
        snapshots_tuple = tuple(
            _snapshot(
                _SCOPE,
                path,
                (
                    "part one release needle\n"
                    if path in eligible_paths
                    else "part one release needle " * 20 + "decoy\n"
                ),
            )
            for path in (*eligible_paths, *decoy_paths)
        )
        manifest = ContextManifest(
            _SCOPE,
            tuple(
                _entry(snapshot, index)
                for index, snapshot in enumerate(snapshots_tuple)
            ),
        )
        snapshots = {
            snapshot.descriptor.source_id: snapshot
            for snapshot in snapshots_tuple
        }
        registry = _registry(_SCOPE, snapshots)
        registry.search = ContextSourceRegistry.search.__get__(registry)
        policy = replace(
            CONTEXT_ACTION_POLICY,
            max_hits_per_search=1,
            max_hits_per_query=1,
            max_hits_per_source_query=1,
        )
        session = _session(
            _binding(manifest),
            registry,
            action_policy=policy,
        )
        query = ContextSearchQuery(
            "part one release needle",
            (ContextSourceKind.WRITING_ARTIFACT,),
            prefix,
        )

        observation = session.search(
            ContextSearchAction(
                _correlation(session.binding, 1),
                (query,),
            )
        )

        self.assertEqual(len(observation.hits), 1)
        self.assertTrue(observation.truncated)
        captured_entries = tuple(
            call.args[1] for call in registry.capture.call_args_list
        )
        captured_paths = tuple(
            entry.provenance.locator for entry in captured_entries
        )
        self.assertEqual(
            set(captured_paths),
            set(eligible_paths),
        )
        self.assertEqual(
            {path: captured_paths.count(path) for path in eligible_paths},
            {path: 2 for path in eligible_paths},
        )
        self.assertTrue(
            all(
                call.args[0].entries
                == tuple(
                    entry
                    for entry in manifest.entries
                    if entry.provenance.locator in eligible_paths
                )
                for call in registry.capture.call_args_list
            )
        )
        self.assertEqual(
            session._scan_utf8_bytes,
            sum(
                snapshots[_artifact_id(path)].descriptor.utf8_bytes
                for path in eligible_paths
            ),
        )
        self.assertTrue(
            all(path not in captured_paths for path in decoy_paths)
        )
        self.assertNotIn(
            internal_path,
            tuple(entry.provenance.locator for entry in manifest.entries),
        )
        self.assertNotIn(internal_path, captured_paths)
        self.assertIn(
            observation.hits[0].source_id,
            {_artifact_id(path) for path in eligible_paths},
        )

        invalid_matrix = (
            (
                "unsafe_parent",
                lambda: ContextSearchQuery(
                    "needle",
                    (ContextSourceKind.WRITING_ARTIFACT,),
                    "manuscript/part_01/../",
                ),
            ),
            (
                "lookalike_without_separator",
                lambda: ContextSearchQuery(
                    "needle",
                    (ContextSourceKind.WRITING_ARTIFACT,),
                    "manuscript/part_01",
                ),
            ),
            (
                "incompatible_source",
                lambda: ContextSearchQuery(
                    "needle",
                    (ContextSourceKind.CONVERSATION,),
                    prefix,
                ),
            ),
            (
                "mixed_prefixes",
                lambda: ContextSearchAction(
                    _correlation(self.binding, 1),
                    (
                        ContextSearchQuery(
                            "one",
                            (ContextSourceKind.WRITING_ARTIFACT,),
                            prefix,
                        ),
                        ContextSearchQuery(
                            "two",
                            (ContextSourceKind.WRITING_ARTIFACT,),
                            "manuscript/part_02/",
                        ),
                    ),
                ),
            ),
        )
        for label, construct in invalid_matrix:
            with self.subTest(rejected=label), self.assertRaises(
                ContextActionError
            ):
                construct()

    def test_compact_search_read_evidence_retains_all_authoritative_facts(self) -> None:
        scope = replace(
            _SCOPE,
            subject_kind=ContextSubjectKind.DEBATE,
            subject_id="debate-one",
        )
        cases = (
            (
                ContextSourceKind.WRITING_ARTIFACT,
                ContextAuthorityKind.WRITING_ARTIFACT_REPOSITORY,
                "manuscript/part-01/chapter.md",
                ContextSourceBoundary.artifact_lines(1, 1),
                "artifact needle\n",
            ),
            (
                ContextSourceKind.CONVERSATION,
                ContextAuthorityKind.CONVERSATION_STORAGE,
                "conversation/conversation-one",
                ContextSourceBoundary.conversation_turns(
                    1,
                    2,
                    "turn-one",
                    "turn-one",
                ),
                "conversation needle",
            ),
            (
                ContextSourceKind.ACTIVE_DEBATE,
                ContextAuthorityKind.ACTIVE_DEBATE,
                "debate/debate-one",
                ContextSourceBoundary.records(1, 1),
                "active debate needle",
            ),
            (
                ContextSourceKind.OWN_AGENT_STATE,
                ContextAuthorityKind.OWN_AGENT_STATE,
                "agent-state/lead_orchestrator",
                ContextSourceBoundary.records(1, 1),
                '{"debate_id":"debate-one","note":"own state needle"}',
            ),
        )
        snapshots_list: list[ContextSourceSnapshot] = []
        for index, (kind, authority, locator, boundary, content) in enumerate(
            cases,
            start=1,
        ):
            encoded = content.encode("utf-8")
            record = ContextSourceRecord(
                f"record-{index}",
                boundary,
                content,
                content,
                _digest(encoded),
                len(encoded),
            )
            source_id = {
                ContextSourceKind.WRITING_ARTIFACT: _artifact_id(locator),
                ContextSourceKind.CONVERSATION: "conversation:conversation-one",
                ContextSourceKind.ACTIVE_DEBATE: "active_debate:debate-one",
                ContextSourceKind.OWN_AGENT_STATE: (
                    "own_agent_state:lead_orchestrator"
                ),
            }[kind]
            content_digest = _digest(encoded)
            descriptor = ContextSourceDescriptor(
                scope,
                source_id,
                kind,
                (
                    _digest(f"revision-{index}".encode())
                    if kind is ContextSourceKind.WRITING_ARTIFACT
                    else content_digest
                ),
                ContextProvenance(authority, locator),
                content_digest,
                len(encoded),
                1,
            )
            snapshot = object.__new__(ContextSourceSnapshot)
            object.__setattr__(snapshot, "descriptor", descriptor)
            object.__setattr__(snapshot, "records", (record,))
            object.__setattr__(snapshot, "content", encoded)
            snapshots_list.append(snapshot)
        manifest = ContextManifest(
            scope,
            tuple(
                _entry(snapshot, index)
                for index, snapshot in enumerate(snapshots_list)
            ),
        )
        snapshots = {
            snapshot.descriptor.source_id: snapshot
            for snapshot in snapshots_list
        }
        registry = _registry(scope, snapshots)
        registry.search = ContextSourceRegistry.search.__get__(registry)
        aliases = {
            entry.source_id: f"s{index}"
            for index, entry in enumerate(manifest.entries, start=1)
        }
        session = _session(
            _binding(manifest),
            registry,
            source_aliases=aliases,
        )
        search = session.search(
            ContextSearchAction(
                _correlation(session.binding, 1),
                (
                    ContextSearchQuery(
                        "needle",
                        service_module._SEARCH_SOURCE_KIND_ORDER,
                    ),
                ),
            )
        )
        search_serialized = session.serialize_observation(search)
        search_wire = json.loads(search_serialized)
        self.assertEqual(search_wire[:2], ["s", False])
        self.assertEqual(len(search_wire[2]), 4)
        for expected_index, (hit, expected) in enumerate(
            zip(search_wire[2], cases, strict=True),
            start=1,
        ):
            kind, authority, locator, boundary, content = expected
            snapshot = snapshots_list[expected_index - 1]
            source = hit[1]
            boundary_facts = hit[2]
            provenance = hit[3]
            digest_facts = (
                [
                    "revision",
                    snapshot.descriptor.revision,
                    "source_digest",
                    snapshot.descriptor.content_digest,
                ]
                if kind is ContextSourceKind.WRITING_ARTIFACT
                else [
                    "revision=source_digest",
                    snapshot.descriptor.revision,
                ]
            )
            self.assertEqual(hit[0], 1)
            self.assertEqual(
                source,
                [
                    "source",
                    expected_index,
                    snapshot.descriptor.source_id,
                    kind.value,
                    digest_facts,
                    snapshot.descriptor.utf8_bytes,
                ],
            )
            self.assertEqual(
                boundary_facts,
                [
                    "boundary",
                    boundary.kind.value,
                    boundary.start,
                    boundary.end,
                    boundary.first_id,
                    boundary.last_id,
                    _digest(content.encode("utf-8")),
                    len(content.encode("utf-8")),
                ],
            )
            safe_locator = (
                f"{locator}/debate-one"
                if kind is ContextSourceKind.OWN_AGENT_STATE
                else locator
            )
            self.assertEqual(provenance, ["provenance", authority.value, safe_locator])
            self.assertEqual(
                hit[4],
                {
                    ContextSourceKind.WRITING_ARTIFACT: "w",
                    ContextSourceKind.CONVERSATION: "c",
                    ContextSourceKind.ACTIVE_DEBATE: "d",
                    ContextSourceKind.OWN_AGENT_STATE: "o",
                }[kind],
            )
            self.assertRegex(hit[5], r"\Ah[A-Za-z0-9_-]{16}\Z")
            self.assertEqual(hit[6][0], "snippet")
            self.assertEqual(
                hit[6][2],
                len(hit[6][1].encode("utf-8")),
            )

        handles = tuple(hit.handle for hit in search.hits)
        read, receipt = session.read(
            ContextReadAction(_correlation(session.binding, 2), handles)
        )
        read_serialized = session.serialize_observation(read)
        read_wire = json.loads(read_serialized)
        self.assertEqual(read_wire[0], "r")
        self.assertEqual(len(read_wire[1]), 4)
        for search_hit, evidence, expected in zip(
            search_wire[2],
            read_wire[1],
            cases,
            strict=True,
        ):
            self.assertEqual(evidence[:5], search_hit[1:6])
            self.assertEqual(evidence[5], expected[4])
        session.complete_read(receipt)
        authorization = session.authorize_answer(
            ContextAnswerAction(
                _correlation(session.binding, 3),
                _PRIVATE_ANSWER,
                handles,
            )
        )
        self.assertEqual(
            tuple(item.handle for item in authorization.used_evidence),
            handles,
        )
        framed = json.dumps(
            [search_wire, read_wire],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self.assertNotIn("/Users/", framed)
        self.assertNotIn("character_custodian", framed)
        self.assertLessEqual(
            len(search_serialized.encode("utf-8"))
            + len(read_serialized.encode("utf-8")),
            CONTEXT_ACTION_POLICY.max_observation_utf8_bytes,
        )

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
        pairs = tuple(
            reversed(_mint_handles(session, registry, self.snapshots))
        )
        observation, receipt = session.read(
            ContextReadAction(_correlation(self.binding, 2), tuple(handle for _, handle in pairs))
        )
        session.complete_read(receipt)
        used = (pairs[0][1],)
        authorization = session.authorize_answer(
            ContextAnswerAction(_correlation(self.binding, 3), _PRIVATE_ANSWER, used)
        )
        requested_ids = tuple(entry.source_id for entry, _ in pairs)
        self.assertEqual(tuple(item.source_id for item in authorization.completed_evidence), requested_ids)
        self.assertEqual(tuple(item.handle for item in authorization.used_evidence), used)
        canonical_ids = [entry.source_id for entry in self.manifest.entries]
        self.assertEqual(seen, canonical_ids + canonical_ids + canonical_ids)
        self.assertEqual(tuple(item.receipt.source_id for item in observation.evidence), requested_ids)

    def test_unused_or_substituted_handle_cannot_authorize_and_baseexception_cleans(self) -> None:
        registry = _registry(_SCOPE, self.snapshots)
        session = _session(self.binding, registry)
        unused = _mint_handles(session, registry, self.snapshots)[0][1]
        with self.assertRaises(ContextActionError) as unauthorized:
            session.authorize_answer(ContextAnswerAction(_correlation(self.binding, 2), _PRIVATE_ANSWER, (unused,)))
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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
