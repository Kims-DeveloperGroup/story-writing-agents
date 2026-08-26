"""Provider-neutral conversation RAG contract verification."""

from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError, fields

from story_writing_agents import conversation_rag as rag

_EXPECTED_EXPORTS = {
    "CONVERSATION_RAG_POLICY",
    "ConversationRagCandidate",
    "ConversationRagCollaborators",
    "ConversationRagContractError",
    "ConversationRagDocumentIdentity",
    "ConversationRagDocumentKey",
    "ConversationRagEmbedder",
    "ConversationRagEmbeddingIdentity",
    "ConversationRagEmbeddingVector",
    "ConversationRagError",
    "ConversationRagLexicalRetriever",
    "ConversationRagOperationError",
    "ConversationRagOutcome",
    "ConversationRagPolicy",
    "ConversationRagProjectionCheckpoint",
    "ConversationRagProjectionCheckpointEntry",
    "ConversationRagProjectionFailureStage",
    "ConversationRagProjectionOutcome",
    "ConversationRagProjectionRequest",
    "ConversationRagProjectionResult",
    "ConversationRagProjectionState",
    "ConversationRagProjectionStateAvailability",
    "ConversationRagProjectionStatus",
    "ConversationRagProviderCandidate",
    "ConversationRagReason",
    "ConversationRagRetrievalRequest",
    "ConversationRagRetrievalResult",
    "ConversationRagService",
    "ConversationRagSourceDocument",
    "ConversationRagSourceReference",
    "ConversationRagStateError",
    "ConversationRagVectorIndexReader",
    "ConversationRagVectorIndexResetter",
    "ConversationRagVectorIndexWriter",
    "ConversationRagVectorRecord",
}


def _document_identity(
    document_id: str = "document-a",
    *,
    session_id: str = "session-a",
    conversation_id: str = "conversation-a",
    start_sequence: int = 1,
    end_sequence: int = 1,
    revision_id: str = "revision-a",
) -> rag.ConversationRagDocumentIdentity:
    return rag.ConversationRagDocumentIdentity(
        rag.ConversationRagDocumentKey(session_id, document_id),
        conversation_id,
        f"turn-{start_sequence}",
        f"turn-{end_sequence}",
        start_sequence,
        end_sequence,
        revision_id,
    )


def _source_reference(
    document: rag.ConversationRagDocumentIdentity | None = None,
    *,
    fragment_id: str = "fragment-a",
    text: str = "secret",
) -> rag.ConversationRagSourceReference:
    return rag.ConversationRagSourceReference(
        _document_identity() if document is None else document,
        fragment_id,
        0,
        len(text),
    )


def _checkpoint(
    document: rag.ConversationRagDocumentIdentity | None = None,
    *,
    schema_id: str = "schema-a",
    model_id: str = "model-a",
) -> rag.ConversationRagProjectionCheckpoint:
    identity = _document_identity() if document is None else document
    return rag.ConversationRagProjectionCheckpoint(
        identity.key.session_id,
        schema_id,
        rag.ConversationRagEmbeddingIdentity(model_id, 2),
        800,
        80,
        (
            rag.ConversationRagProjectionCheckpointEntry(
                identity,
                "sha256:" + ("0" * 64),
                1,
            ),
        ),
        "checkpoint-a",
    )


class ConversationRagContractTests(unittest.TestCase):
    def test_supported_direct_exports_and_fixed_policy_are_exact_and_immutable(
        self,
    ) -> None:
        self.assertEqual(len(rag.__all__), 35)
        self.assertEqual(set(rag.__all__), _EXPECTED_EXPORTS)
        self.assertEqual(len(rag.__all__), len(set(rag.__all__)))
        for name in rag.__all__:
            self.assertTrue(hasattr(rag, name), name)

        policy = rag.CONVERSATION_RAG_POLICY
        self.assertEqual(
            (
                policy.max_candidates,
                policy.max_conversations,
                policy.max_events,
                policy.max_fragment_codepoints,
                policy.fragment_overlap_codepoints,
                policy.max_embedding_batch_size,
            ),
            (20, 256, 10_000, 800, 80, 32),
        )
        with self.assertRaises(FrozenInstanceError):
            policy.max_candidates = 21  # type: ignore[misc]
        with self.assertRaisesRegex(ValueError, "policy is fixed"):
            rag.ConversationRagPolicy(max_candidates=19)
        with self.assertRaisesRegex(TypeError, "must be integers"):
            rag.ConversationRagPolicy(max_candidates=True)  # type: ignore[arg-type]

    def test_stable_enum_values_cover_results_state_reasons_and_failure_stages(
        self,
    ) -> None:
        self.assertEqual(
            tuple(member.value for member in rag.ConversationRagOutcome),
            ("complete", "partial", "literal_fallback"),
        )
        self.assertEqual(
            tuple(member.value for member in rag.ConversationRagReason),
            (
                "none",
                "partial",
                "disabled",
                "unavailable",
                "stale",
                "corrupt",
                "incompatible",
                "failed",
            ),
        )
        self.assertEqual(
            tuple(member.value for member in rag.ConversationRagProjectionStatus),
            (
                "missing",
                "current",
                "stale",
                "corrupt",
                "schema_mismatch",
                "embedding_mismatch",
            ),
        )
        self.assertEqual(
            tuple(member.value for member in rag.ConversationRagProjectionFailureStage),
            (
                "embedder_identity",
                "embedding",
                "replacement",
                "deletion",
                "reset",
                "adapter",
            ),
        )

    def test_values_are_exact_frozen_and_hide_non_authoritative_text(self) -> None:
        identity = _document_identity()
        source = rag.ConversationRagSourceDocument(identity, "source secret")
        provider_text = "provider payload secret"
        reference = _source_reference(identity, text=provider_text)
        provider = rag.ConversationRagProviderCandidate(reference, provider_text)
        request = rag.ConversationRagRetrievalRequest(
            "session-a",
            "query secret",
            1,
        )
        candidate = rag.ConversationRagCandidate(reference, 1)
        result = rag.ConversationRagRetrievalResult(
            request,
            rag.ConversationRagOutcome.COMPLETE,
            rag.ConversationRagReason.NONE,
            (candidate,),
            False,
        )

        self.assertNotIn("source secret", repr(source))
        self.assertNotIn(provider_text, repr(provider))
        self.assertNotIn("query secret", repr(request))
        self.assertNotIn(provider_text, repr(result))
        self.assertEqual(
            tuple(field.name for field in fields(rag.ConversationRagCandidate)),
            ("source", "rank"),
        )
        self.assertFalse(hasattr(candidate, "text"))
        with self.assertRaises(FrozenInstanceError):
            candidate.rank = 2  # type: ignore[misc]

    def test_exact_type_validation_rejects_subclasses_and_bool_integers(self) -> None:
        class StringSubclass(str):
            pass

        class TupleSubclass(tuple):
            pass

        class KeySubclass(rag.ConversationRagDocumentKey):
            pass

        with self.assertRaisesRegex(TypeError, "session ID must be exactly str"):
            rag.ConversationRagDocumentKey(StringSubclass("session-a"), "document-a")
        with self.assertRaisesRegex(
            TypeError, "embedding values must be exactly tuple"
        ):
            rag.ConversationRagEmbeddingVector(TupleSubclass((1.0,)))  # type: ignore[arg-type]
        with self.assertRaisesRegex(
            TypeError, "embedding dimensions must be exactly int"
        ):
            rag.ConversationRagEmbeddingIdentity("model-a", True)  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "RAG document key"):
            rag.ConversationRagDocumentIdentity(
                KeySubclass("session-a", "document-a"),
                "conversation-a",
                "turn-1",
                "turn-1",
                1,
                1,
                "revision-a",
            )

    def test_projection_request_enforces_scope_order_uniqueness_and_budgets(
        self,
    ) -> None:
        embedding = rag.ConversationRagEmbeddingIdentity("model-a", 2)
        first = rag.ConversationRagSourceDocument(_document_identity("document-a"), "a")
        second = rag.ConversationRagSourceDocument(
            _document_identity("document-b"), "b"
        )

        valid = rag.ConversationRagProjectionRequest(
            "session-a",
            "schema-a",
            embedding,
            (first, second),
        )
        self.assertEqual(valid.documents, (first, second))
        with self.assertRaisesRegex(ValueError, "canonical order"):
            rag.ConversationRagProjectionRequest(
                "session-a",
                "schema-a",
                embedding,
                (second, first),
            )
        with self.assertRaisesRegex(ValueError, "keys must be unique"):
            rag.ConversationRagProjectionRequest(
                "session-a",
                "schema-a",
                embedding,
                (first, first),
            )
        foreign = rag.ConversationRagSourceDocument(
            _document_identity("document-c", session_id="session-b"),
            "c",
        )
        with self.assertRaisesRegex(ValueError, "must match the session"):
            rag.ConversationRagProjectionRequest(
                "session-a",
                "schema-a",
                embedding,
                (foreign,),
            )

        too_many_conversations = tuple(
            rag.ConversationRagSourceDocument(
                _document_identity(
                    f"document-{index:03}",
                    conversation_id=f"conversation-{index:03}",
                ),
                "x",
            )
            for index in range(257)
        )
        with self.assertRaisesRegex(ValueError, "conversation budget"):
            rag.ConversationRagProjectionRequest(
                "session-a",
                "schema-a",
                embedding,
                too_many_conversations,
            )
        too_many_events = rag.ConversationRagSourceDocument(
            _document_identity("document-z", end_sequence=10_001),
            "z",
        )
        with self.assertRaisesRegex(ValueError, "event budget"):
            rag.ConversationRagProjectionRequest(
                "session-a",
                "schema-a",
                embedding,
                (too_many_events,),
            )

    def test_checkpoint_and_projection_results_enforce_canonical_success(self) -> None:
        document = _document_identity()
        checkpoint = _checkpoint(document)
        state = rag.ConversationRagProjectionState(
            rag.ConversationRagProjectionStateAvailability.PRESENT,
            checkpoint,
        )
        self.assertIs(state.checkpoint, checkpoint)
        with self.assertRaisesRegex(ValueError, "has no checkpoint"):
            rag.ConversationRagProjectionState(
                rag.ConversationRagProjectionStateAvailability.CORRUPT,
                checkpoint,
            )
        with self.assertRaisesRegex(ValueError, "chunking policy is fixed"):
            rag.ConversationRagProjectionCheckpoint(
                "session-a",
                "schema-a",
                rag.ConversationRagEmbeddingIdentity("model-a", 2),
                801,
                80,
                (),
                "checkpoint-a",
            )
        with self.assertRaisesRegex(ValueError, "counts must match"):
            rag.ConversationRagProjectionResult(
                rag.ConversationRagProjectionStatus.STALE,
                rag.ConversationRagProjectionOutcome.COMPLETED,
                2,
                1,
                checkpoint,
            )
        with self.assertRaisesRegex(ValueError, "zero attempts"):
            rag.ConversationRagProjectionResult(
                rag.ConversationRagProjectionStatus.CURRENT,
                rag.ConversationRagProjectionOutcome.UNCHANGED,
                1,
                1,
                checkpoint,
            )

    def test_retrieval_result_invariants_keep_fallbacks_content_free(self) -> None:
        request = rag.ConversationRagRetrievalRequest("session-a", "query", 2)
        first = rag.ConversationRagCandidate(_source_reference(), 1)
        second = rag.ConversationRagCandidate(
            _source_reference(fragment_id="fragment-b"),
            2,
        )
        partial = rag.ConversationRagRetrievalResult(
            request,
            rag.ConversationRagOutcome.PARTIAL,
            rag.ConversationRagReason.PARTIAL,
            (first, second),
            True,
        )
        self.assertEqual(partial.candidates, (first, second))
        fallback = rag.ConversationRagRetrievalResult(
            request,
            rag.ConversationRagOutcome.LITERAL_FALLBACK,
            rag.ConversationRagReason.STALE,
            (),
            False,
        )
        self.assertEqual(fallback.candidates, ())
        with self.assertRaisesRegex(ValueError, "ranks must be contiguous"):
            rag.ConversationRagRetrievalResult(
                request,
                rag.ConversationRagOutcome.COMPLETE,
                rag.ConversationRagReason.NONE,
                (second,),
                False,
            )
        with self.assertRaisesRegex(ValueError, "sources must be unique"):
            rag.ConversationRagRetrievalResult(
                request,
                rag.ConversationRagOutcome.COMPLETE,
                rag.ConversationRagReason.NONE,
                (
                    first,
                    rag.ConversationRagCandidate(first.source, 2),
                ),
                False,
            )
        with self.assertRaisesRegex(ValueError, "cannot contain"):
            rag.ConversationRagRetrievalResult(
                request,
                rag.ConversationRagOutcome.LITERAL_FALLBACK,
                rag.ConversationRagReason.FAILED,
                (first,),
                False,
            )

    def test_boundary_errors_have_fixed_messages_and_content_free_attributes(
        self,
    ) -> None:
        contract = rag.ConversationRagContractError()
        state = rag.ConversationRagStateError(
            rag.ConversationRagProjectionStatus.SCHEMA_MISMATCH
        )
        operation = rag.ConversationRagOperationError(
            rag.ConversationRagProjectionFailureStage.REPLACEMENT,
            rag.ConversationRagDocumentKey("session-a", "document-a"),
            3,
            2,
        )
        self.assertEqual(str(contract), "conversation RAG contract validation failed")
        self.assertEqual(
            str(state),
            "conversation RAG projection state is incompatible",
        )
        self.assertEqual(str(operation), "conversation RAG collaborator failed")
        self.assertEqual(
            state.status, rag.ConversationRagProjectionStatus.SCHEMA_MISMATCH
        )
        self.assertEqual(
            (
                operation.stage,
                operation.affected_document,
                operation.attempted_documents,
                operation.completed_documents,
            ),
            (
                rag.ConversationRagProjectionFailureStage.REPLACEMENT,
                rag.ConversationRagDocumentKey("session-a", "document-a"),
                3,
                2,
            ),
        )
        for error in (contract, state, operation):
            with self.subTest(error=type(error).__name__):
                self.assertNotIn("secret", str(error))
                self.assertIsNone(error.__cause__)
                self.assertIsNone(error.__context__)


if __name__ == "__main__":
    unittest.main()
