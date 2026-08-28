"""Pure contracts for bounded provider-neutral agent context actions."""

from __future__ import annotations

import hashlib
import inspect
import json
import unittest
from dataclasses import FrozenInstanceError, fields, replace

from story_writing_agents.agent_context import actions as actions_module
from story_writing_agents.agent_context.actions import (
    CONTEXT_ACTION_POLICY,
    ContextActionCorrelation,
    ContextActionError,
    ContextActionErrorCode,
    ContextActionKind,
    ContextActionPolicy,
    ContextAnswerAction,
    ContextAnswerAuthorization,
    ContextEvidenceReceipt,
    ContextReadAction,
    ContextReadObservation,
    ContextSearchAction,
    ContextSearchHit,
    ContextSearchObservation,
    ContextSearchQuery,
    ContextSourceBoundary,
    ContextSourceBoundaryKind,
    ContextSourceEvidence,
    ContextSourceHandle,
    serialize_context_action_observation,
)
from story_writing_agents.agent_context.manifest import (
    ContextAuthorityKind,
    ContextProvenance,
    ContextScope,
    ContextSourceKind,
    ContextSubjectKind,
)
from story_writing_agents.agent_context.planning import (
    MandatoryReferenceReservation,
)


_PRIVATE_ANSWER = "PRIVATE_ANSWER_한🙂"
_PRIVATE_EXCERPT = "PRIVATE_EXCERPT_한🙂"
_PRIVATE_HANDLE = "ctxh_v1_" + ("a" * 64)
_DIGEST = "sha256:" + ("1" * 64)
_REVISION = "sha256:" + ("2" * 64)
_SCOPE = ContextScope(
    "workspace",
    "session",
    ContextSubjectKind.CONVERSATION,
    "conversation",
    "lead_orchestrator",
)


def _digest(body: bytes) -> str:
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def _correlation(index: int = 1, **overrides: object) -> ContextActionCorrelation:
    values: dict[str, object] = {
        "scope": _SCOPE,
        "operation_id": "operation",
        "result_correlation_id": "result",
        "action_index": index,
    }
    values.update(overrides)
    return ContextActionCorrelation(**values)  # type: ignore[arg-type]


def _source_facts(
    kind: ContextSourceKind = ContextSourceKind.WRITING_ARTIFACT,
) -> tuple[ContextProvenance, ContextSourceBoundary]:
    if kind is ContextSourceKind.WRITING_ARTIFACT:
        return (
            ContextProvenance(
                ContextAuthorityKind.WRITING_ARTIFACT_REPOSITORY,
                "core_documents/outline.md",
            ),
            ContextSourceBoundary.artifact_lines(1, 1),
        )
    if kind is ContextSourceKind.CONVERSATION:
        return (
            ContextProvenance(
                ContextAuthorityKind.CONVERSATION_STORAGE,
                "conversations/session/conversation",
            ),
            ContextSourceBoundary.conversation_turns(1, 2, "turn", "turn"),
        )
    if kind is ContextSourceKind.ACTIVE_DEBATE:
        return (
            ContextProvenance(
                ContextAuthorityKind.ACTIVE_DEBATE,
                "debates/session/debate",
            ),
            ContextSourceBoundary.records(1, 1),
        )
    if kind is ContextSourceKind.OWN_AGENT_STATE:
        return (
            ContextProvenance(
                ContextAuthorityKind.OWN_AGENT_STATE,
                "agent-state/lead_orchestrator/session",
            ),
            ContextSourceBoundary.records(1, 1),
        )
    raise AssertionError(kind)


def _receipt(
    *,
    handle: str = _PRIVATE_HANDLE,
    content: str = _PRIVATE_EXCERPT,
    kind: ContextSourceKind = ContextSourceKind.WRITING_ARTIFACT,
    whole: bool = False,
) -> ContextEvidenceReceipt:
    provenance, boundary = _source_facts(kind)
    body = content.encode("utf-8")
    return ContextEvidenceReceipt(
        ContextSourceHandle(handle),
        f"source_{kind.value}",
        kind,
        _REVISION,
        provenance,
        _digest(body),
        len(body),
        ContextSourceBoundary.whole() if whole else boundary,
        _digest(body),
        len(body),
    )


def _hit(
    *,
    handle: str = _PRIVATE_HANDLE,
    query_index: int = 1,
    kind: ContextSourceKind = ContextSourceKind.WRITING_ARTIFACT,
    source_id: str | None = None,
) -> ContextSearchHit:
    provenance, boundary = _source_facts(kind)
    snippet = "needle 한🙂"
    body = snippet.encode("utf-8")
    return ContextSearchHit(
        query_index,
        ContextSourceHandle(handle),
        source_id or f"source_{kind.value}",
        kind,
        _REVISION,
        provenance,
        _digest(body),
        len(body),
        boundary,
        _digest(body),
        len(body),
        snippet,
        len(body),
    )


def _receipt_value(receipt: ContextEvidenceReceipt) -> dict[str, object]:
    boundary = receipt.boundary
    return {
        "boundary": {
            "end": boundary.end,
            "first_id": boundary.first_id,
            "kind": boundary.kind.value,
            "last_id": boundary.last_id,
            "start": boundary.start,
        },
        "boundary_digest": receipt.boundary_digest,
        "boundary_utf8_bytes": receipt.boundary_utf8_bytes,
        "handle": receipt.handle.value,
        "provenance": {
            "authority_kind": receipt.provenance.authority_kind.value,
            "locator": receipt.provenance.locator,
        },
        "revision": receipt.revision,
        "source_digest": receipt.source_digest,
        "source_id": receipt.source_id,
        "source_kind": receipt.source_kind.value,
        "source_utf8_bytes": receipt.source_utf8_bytes,
    }


def _completed_digest(receipts: tuple[ContextEvidenceReceipt, ...]) -> str:
    body = json.dumps(
        {
            "evidence": [_receipt_value(receipt) for receipt in receipts],
            "kind": "completed_context_reads_v1",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _digest(body)


def _reference(ordinal: int, locator: str) -> MandatoryReferenceReservation:
    return MandatoryReferenceReservation(
        ordinal,
        f"reference-{ordinal}",
        f"artifact-{ordinal}",
        "revision-1",
        ContextProvenance(
            ContextAuthorityKind.REFERENCE_AUTHORIZATION,
            locator,
        ),
        _DIGEST,
        7,
    )


class ActionSurfaceAndPolicyTests(unittest.TestCase):
    """Verify the closed action vocabulary and every finite hard cap."""

    def test_exact_surface_enums_fields_and_signatures(self) -> None:
        self.assertEqual(
            tuple(actions_module.__all__),
            (
                "CONTEXT_ACTION_POLICY",
                "ContextAction",
                "ContextActionCorrelation",
                "ContextActionError",
                "ContextActionErrorCode",
                "ContextActionKind",
                "ContextActionObservation",
                "ContextActionPolicy",
                "ContextAnswerAction",
                "ContextAnswerAuthorization",
                "ContextEvidenceReceipt",
                "ContextReadAction",
                "ContextReadObservation",
                "ContextSearchAction",
                "ContextSearchHit",
                "ContextSearchObservation",
                "ContextSearchQuery",
                "ContextSourceBoundary",
                "ContextSourceBoundaryKind",
                "ContextSourceEvidence",
                "ContextSourceHandle",
                "serialize_context_action_observation",
            ),
        )
        self.assertEqual(tuple(item.value for item in ContextActionKind), ("search", "read", "answer"))
        self.assertEqual(
            tuple(item.value for item in ContextSourceBoundaryKind),
            ("whole", "artifact_lines", "conversation_turns", "records"),
        )
        self.assertEqual(
            tuple(item.value for item in ContextActionErrorCode),
            (
                "invalid_value",
                "correlation_mismatch",
                "invalid_transition",
                "action_limit_exceeded",
                "search_limit_exceeded",
                "read_limit_exceeded",
                "observation_limit_exceeded",
                "handle_invalid",
                "outstanding_read",
                "answer_not_authorized",
            ),
        )
        self.assertEqual(
            tuple(field.name for field in fields(ContextAnswerAction)),
            ("correlation", "answer", "used_handles"),
        )
        self.assertIn("answer", inspect.signature(ContextAnswerAction).parameters)
        self.assertEqual(len(fields(ContextEvidenceReceipt)), 10)
        self.assertEqual(len(fields(ContextAnswerAuthorization)), 9)

    def test_default_policy_is_exact_and_each_independent_cap_rejects_n_plus_one(self) -> None:
        expected = {
            "max_nonterminal_actions": 4,
            "max_provider_calls": 5,
            "max_search_actions": 2,
            "max_read_actions": 2,
            "max_queries_per_search": 4,
            "max_cumulative_queries": 8,
            "max_handles_per_read": 8,
            "max_cumulative_handles": 16,
            "max_hits_per_query": 10,
            "max_hits_per_search": 20,
            "max_cumulative_hits": 20,
            "max_hits_per_source_query": 3,
            "max_query_characters": 200,
            "max_snippet_characters": 240,
            "max_snippet_utf8_bytes": 1024,
            "max_excerpt_utf8_bytes": 16384,
            "max_source_read_utf8_bytes": 16384,
            "max_read_utf8_bytes": 24576,
            "max_observation_utf8_bytes": 32768,
        }
        self.assertEqual(
            {field.name: getattr(CONTEXT_ACTION_POLICY, field.name) for field in fields(ContextActionPolicy)},
            expected,
        )
        for name, value in expected.items():
            with self.subTest(name=name):
                with self.assertRaises(ContextActionError):
                    ContextActionPolicy(**{**expected, name: value + 1})
        for invalid in (0, -1, True, 1.0, "1"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ContextActionError):
                    ContextActionPolicy(**{**expected, "max_query_characters": invalid})  # type: ignore[arg-type]

    def test_custom_lower_caps_require_exact_call_equation_and_all_relationships(self) -> None:
        valid = ContextActionPolicy(
            max_nonterminal_actions=2,
            max_provider_calls=3,
            max_search_actions=1,
            max_read_actions=1,
            max_queries_per_search=1,
            max_cumulative_queries=2,
            max_handles_per_read=1,
            max_cumulative_handles=2,
            max_hits_per_query=2,
            max_hits_per_search=3,
            max_cumulative_hits=4,
            max_hits_per_source_query=1,
            max_query_characters=20,
            max_snippet_characters=20,
            max_snippet_utf8_bytes=20,
            max_excerpt_utf8_bytes=30,
            max_source_read_utf8_bytes=40,
            max_read_utf8_bytes=50,
            max_observation_utf8_bytes=500,
        )
        self.assertEqual(valid.max_provider_calls, valid.max_nonterminal_actions + 1)
        invalid = (
            {"max_provider_calls": 4},
            {"max_search_actions": 3},
            {"max_read_actions": 3},
            {"max_queries_per_search": 3},
            {"max_handles_per_read": 3},
            {"max_hits_per_source_query": 3},
            {"max_hits_per_query": 4},
            {"max_hits_per_search": 5},
            {"max_snippet_utf8_bytes": 31},
            {"max_excerpt_utf8_bytes": 41},
            {"max_source_read_utf8_bytes": 51},
            {"max_read_utf8_bytes": 501},
        )
        for override in invalid:
            with self.subTest(override=override):
                with self.assertRaises(ContextActionError):
                    replace(valid, **override)

    def test_errors_are_fixed_content_free_and_chainless(self) -> None:
        expected = {
            "invalid_value": "context action value is invalid",
            "correlation_mismatch": "context action correlation does not match",
            "invalid_transition": "context action transition is invalid",
            "action_limit_exceeded": "context action limit exceeded",
            "search_limit_exceeded": "context search limit exceeded",
            "read_limit_exceeded": "context read limit exceeded",
            "observation_limit_exceeded": "context observation limit exceeded",
            "handle_invalid": "context source handle is invalid",
            "outstanding_read": "context read delivery remains outstanding",
            "answer_not_authorized": "context answer is not authorized",
        }
        for code in ContextActionErrorCode:
            with self.subTest(code=code):
                error = ContextActionError(code)
                self.assertEqual(str(error), expected[code.value])
                self.assertNotIn("PRIVATE", repr(error))
                self.assertIsNone(error.__cause__)
                self.assertIsNone(error.__context__)


class ActionValueTests(unittest.TestCase):
    """Verify strict correlations, queries, source facts, and privacy."""

    def test_correlation_boundary_and_handle_are_exact_frozen_slots(self) -> None:
        correlation = _correlation()
        self.assertFalse(hasattr(correlation, "__dict__"))
        with self.assertRaises((FrozenInstanceError, TypeError)):
            correlation.action_index = 2  # type: ignore[misc]
        for override in ({"action_index": 0}, {"action_index": True}, {"operation_id": ""}, {"scope": object()}):
            with self.subTest(override=override):
                with self.assertRaises(ContextActionError):
                    _correlation(**override)
        self.assertEqual(ContextSourceBoundary.whole().kind, ContextSourceBoundaryKind.WHOLE)
        self.assertEqual(ContextSourceBoundary.artifact_lines(2, 4).start, 2)
        self.assertEqual(ContextSourceBoundary.records(3, 3).end, 3)
        self.assertEqual(
            ContextSourceBoundary.conversation_turns(1, 2, "turn", "turn").last_id,
            "turn",
        )
        for boundary in (
            lambda: ContextSourceBoundary.artifact_lines(0, 1),
            lambda: ContextSourceBoundary.records(2, 1),
            lambda: ContextSourceBoundary(ContextSourceBoundaryKind.WHOLE, 1),
            lambda: ContextSourceBoundary(ContextSourceBoundaryKind.ARTIFACT_LINES, 1, 1, "id", None),
        ):
            with self.assertRaises(ContextActionError):
                boundary()
        handle = ContextSourceHandle(_PRIVATE_HANDLE)
        self.assertNotIn(_PRIVATE_HANDLE, repr(handle))
        for invalid in ("", "ctxh_v1_" + "A" * 64, "ctxh_v1_" + "a" * 63, 1):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ContextActionError):
                    ContextSourceHandle(invalid)  # type: ignore[arg-type]

    def test_query_normalization_kind_order_and_duplicate_filter_rejection(self) -> None:
        query = ContextSearchQuery(
            "  ＮＥＥＤＬＥ 한  ",
            (ContextSourceKind.WRITING_ARTIFACT, ContextSourceKind.CONVERSATION),
        )
        self.assertEqual(query.normalized_text, "needle 한")
        self.assertNotIn(query.text, repr(query))
        for invalid in ("bad\x00query", "bad\u200bquery", "bad\ud800query", " ", "x" * 201):
            with self.subTest(invalid=repr(invalid)):
                with self.assertRaises(ContextActionError):
                    ContextSearchQuery(invalid, (ContextSourceKind.WRITING_ARTIFACT,))
        invalid_kinds = (
            (),
            (ContextSourceKind.CONVERSATION, ContextSourceKind.WRITING_ARTIFACT),
            (ContextSourceKind.WRITING_ARTIFACT, ContextSourceKind.WRITING_ARTIFACT),
            ("writing_artifact",),
            (ContextSourceKind.RUNTIME_INPUT,),
        )
        for kinds in invalid_kinds:
            with self.subTest(kinds=kinds):
                with self.assertRaises(ContextActionError):
                    ContextSearchQuery("needle", kinds)  # type: ignore[arg-type]
        first = ContextSearchQuery("Needle", (ContextSourceKind.WRITING_ARTIFACT,))
        duplicate = ContextSearchQuery("ＮＥＥＤＬＥ", (ContextSourceKind.WRITING_ARTIFACT,))
        with self.assertRaises(ContextActionError):
            ContextSearchAction(_correlation(), (first, duplicate))
        distinct_filter = ContextSearchQuery("needle", (ContextSourceKind.CONVERSATION,))
        self.assertEqual(len(ContextSearchAction(_correlation(), (first, distinct_filter)).queries), 2)

    def test_source_authority_boundary_matrix_and_exact_digest_bytes(self) -> None:
        for index, kind in enumerate(
            (
                ContextSourceKind.WRITING_ARTIFACT,
                ContextSourceKind.CONVERSATION,
                ContextSourceKind.ACTIVE_DEBATE,
                ContextSourceKind.OWN_AGENT_STATE,
            )
        ):
            with self.subTest(kind=kind):
                hit = _hit(handle="ctxh_v1_" + format(index + 1, "064x"), kind=kind)
                self.assertEqual(hit.boundary_utf8_bytes, len("needle 한🙂".encode("utf-8")))
                self.assertEqual(_receipt(kind=kind).source_kind, kind)
                wrong_provenance, _ = _source_facts(ContextSourceKind.WRITING_ARTIFACT if kind is not ContextSourceKind.WRITING_ARTIFACT else ContextSourceKind.CONVERSATION)
                with self.assertRaises(ContextActionError):
                    replace(hit, provenance=wrong_provenance)
                wrong_boundary = ContextSourceBoundary.records(1, 1) if kind in {ContextSourceKind.WRITING_ARTIFACT, ContextSourceKind.CONVERSATION} else ContextSourceBoundary.artifact_lines(1, 1)
                with self.assertRaises(ContextActionError):
                    replace(hit, boundary=wrong_boundary)
        receipt = _receipt()
        for override in (
            {"source_utf8_bytes": receipt.boundary_utf8_bytes - 1},
            {"boundary_utf8_bytes": 0 - 1},
            {"source_digest": "sha256:" + "A" * 64},
            {"revision": "revision"},
        ):
            with self.subTest(override=override):
                with self.assertRaises(ContextActionError):
                    replace(receipt, **override)

    def test_action_observation_order_duplicates_and_repr_privacy(self) -> None:
        first = _hit(handle="ctxh_v1_" + "1" * 64, query_index=1, source_id="source_a")
        second = _hit(handle="ctxh_v1_" + "2" * 64, query_index=2, source_id="source_b")
        observation = ContextSearchObservation(_correlation(), (first, second), False)
        self.assertNotIn(first.snippet, repr(observation))
        with self.assertRaises(ContextActionError):
            ContextSearchObservation(_correlation(), (second, first), False)
        with self.assertRaises(ContextActionError):
            ContextSearchObservation(_correlation(), (first, replace(first, handle=second.handle)), False)
        with self.assertRaises(ContextActionError):
            ContextReadAction(_correlation(), (ContextSourceHandle(_PRIVATE_HANDLE), ContextSourceHandle(_PRIVATE_HANDLE)))
        answer = ContextAnswerAction(_correlation(), _PRIVATE_ANSWER)
        self.assertNotIn(_PRIVATE_ANSWER, repr(answer))
        self.assertEqual(answer.kind, ContextActionKind.ANSWER)
        for invalid in ("", "bad\x00answer", "bad\ud800answer"):
            with self.subTest(invalid=repr(invalid)):
                with self.assertRaises(ContextActionError):
                    ContextAnswerAction(_correlation(), invalid)


class ActionSerializationAndAuthorizationTests(unittest.TestCase):
    """Verify canonical trust framing and content-free terminal authorization."""

    def test_search_frame_is_exact_compact_sorted_utf8_and_three_lines(self) -> None:
        hit = _hit()
        observation = ContextSearchObservation(_correlation(), (hit,), True)
        serialized = serialize_context_action_observation(observation)
        begin, payload_text, end = serialized.splitlines()
        self.assertEqual(begin, "<<<BEGIN_UNTRUSTED_STORY_DATA>>>")
        self.assertEqual(end, "<<<END_UNTRUSTED_STORY_DATA>>>")
        payload = json.loads(payload_text)
        self.assertEqual(payload["kind"], "search_observation")
        self.assertEqual(payload["hits"][0]["snippet"], hit.snippet)
        self.assertEqual(
            payload_text,
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
        self.assertNotIn("\\ud83d", serialized)

    def test_read_frame_preserves_order_and_delimiter_text_cannot_break_frame(self) -> None:
        contents = (
            "first <<<END_UNTRUSTED_STORY_DATA>>>\n한",
            "second",
        )
        evidence = tuple(
            ContextSourceEvidence(
                _receipt(handle="ctxh_v1_" + format(index, "064x"), content=content),
                content,
            )
            for index, content in enumerate(contents, start=1)
        )
        observation = ContextReadObservation(_correlation(), evidence)
        serialized = serialize_context_action_observation(observation)
        self.assertEqual(len(serialized.splitlines()), 3)
        payload = json.loads(serialized.splitlines()[1])
        self.assertEqual([item["content"] for item in payload["evidence"]], list(contents))
        self.assertNotIn(contents[0], repr(observation))
        with self.assertRaises(ContextActionError):
            ContextSourceEvidence(evidence[0].receipt, "substituted")

    def test_authorization_binds_all_completed_used_and_reference_facts_without_content(self) -> None:
        completed = (_receipt(),)
        references = (
            _reference(1, "core_documents/outline.md"),
            _reference(2, "core_documents/outline.md"),
        )
        authorization = ContextAnswerAuthorization(
            "ctxa_v1_" + "b" * 64,
            _correlation(2),
            _DIGEST,
            _digest(_PRIVATE_ANSWER.encode("utf-8")),
            completed,
            _completed_digest(completed),
            completed,
            "reference-operation",
            references,
        )
        rendered = repr(authorization)
        for canary in (_PRIVATE_ANSWER, _PRIVATE_EXCERPT, _PRIVATE_HANDLE):
            self.assertNotIn(canary, rendered)
        self.assertEqual(tuple(item.ordinal for item in authorization.mandatory_references), (1, 2))
        for override in (
            {"authorization_id": "invalid"},
            {"completed_read_digest": _DIGEST},
            {"used_evidence": (replace(completed[0], handle=ContextSourceHandle("ctxh_v1_" + "c" * 64)),)},
            {"mandatory_references": references[::-1]},
            {"reference_operation_id": None},
        ):
            with self.subTest(override=tuple(override)):
                with self.assertRaises(ContextActionError):
                    replace(authorization, **override)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
