"""Story-to-generic-rag translation and failure-boundary verification."""

from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path
from unittest import mock

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_GENERIC_RAG_SOURCE = _REPOSITORY_ROOT / "vendor" / "generic-rag" / "src"
if str(_GENERIC_RAG_SOURCE) not in sys.path:
    sys.path.insert(0, str(_GENERIC_RAG_SOURCE))

generic_contracts = importlib.import_module("generic_rag.contracts")
generic_errors = importlib.import_module("generic_rag.errors")
rag = importlib.import_module("story_writing_agents.conversation_rag")
adapter = importlib.import_module("story_writing_agents.generic_rag_adapter")


def _document(
    document_id: str,
    text: str,
    *,
    revision_id: str = "revision-a",
) -> rag.ConversationRagSourceDocument:
    return rag.ConversationRagSourceDocument(
        rag.ConversationRagDocumentIdentity(
            rag.ConversationRagDocumentKey("session-a", document_id),
            f"conversation-{document_id}",
            f"turn-{document_id}-start",
            f"turn-{document_id}-end",
            1,
            1,
            revision_id,
        ),
        text,
    )


def _projection_request(
    documents: tuple[rag.ConversationRagSourceDocument, ...],
    *,
    schema_id: str = "schema-a",
) -> rag.ConversationRagProjectionRequest:
    return rag.ConversationRagProjectionRequest(
        "session-a",
        schema_id,
        rag.ConversationRagEmbeddingIdentity("model-a", 2),
        documents,
    )


def _missing_state() -> rag.ConversationRagProjectionState:
    return rag.ConversationRagProjectionState(
        rag.ConversationRagProjectionStateAvailability.MISSING,
        None,
    )


def _present_state(
    checkpoint: rag.ConversationRagProjectionCheckpoint,
) -> rag.ConversationRagProjectionState:
    return rag.ConversationRagProjectionState(
        rag.ConversationRagProjectionStateAvailability.PRESENT,
        checkpoint,
    )


class _LifecycleWitness:
    def __init__(self) -> None:
        self.lifecycle_calls: list[str] = []

    def __enter__(self):  # type: ignore[no-untyped-def]
        self.lifecycle_calls.append("enter")
        return self

    def __exit__(self, *args: object) -> bool:
        self.lifecycle_calls.append("exit")
        return False

    def close(self) -> None:
        self.lifecycle_calls.append("close")


class _Embedder(_LifecycleWitness):
    def __init__(self) -> None:
        super().__init__()
        self.current_identity = rag.ConversationRagEmbeddingIdentity("model-a", 2)
        self.identity_failure: BaseException | None = None
        self.embedding_failure: BaseException | None = None
        self.calls: list[tuple[str, ...]] = []

    @property
    def identity(self) -> rag.ConversationRagEmbeddingIdentity:
        if self.identity_failure is not None:
            raise self.identity_failure
        return self.current_identity

    def embed(
        self,
        texts: tuple[str, ...],
        /,
    ) -> tuple[rag.ConversationRagEmbeddingVector, ...]:
        self.calls.append(texts)
        if self.embedding_failure is not None:
            raise self.embedding_failure
        return tuple(
            rag.ConversationRagEmbeddingVector((float(len(text)), 0.5))
            for text in texts
        )


class _VectorWriter(_LifecycleWitness):
    def __init__(self) -> None:
        super().__init__()
        self.replacements: list[
            tuple[
                rag.ConversationRagDocumentIdentity,
                tuple[rag.ConversationRagVectorRecord, ...],
            ]
        ] = []
        self.deletions: list[rag.ConversationRagDocumentKey] = []
        self.fail_document_id: str | None = None

    def replace_document(
        self,
        document: rag.ConversationRagDocumentIdentity,
        records: tuple[rag.ConversationRagVectorRecord, ...],
        /,
    ) -> None:
        if document.key.document_id == self.fail_document_id:
            raise RuntimeError("secret writer failure")
        self.replacements.append((document, records))

    def delete_document(self, document: rag.ConversationRagDocumentKey, /) -> None:
        if document.document_id == self.fail_document_id:
            raise RuntimeError("secret writer failure")
        self.deletions.append(document)


class _VectorResetter(_LifecycleWitness):
    def __init__(self) -> None:
        super().__init__()
        self.sessions: list[str] = []
        self.failure: BaseException | None = None

    def reset_session(self, session_id: str, /) -> None:
        if self.failure is not None:
            raise self.failure
        self.sessions.append(session_id)


class _VectorReader(_LifecycleWitness):
    def __init__(self) -> None:
        super().__init__()
        self.output: tuple[rag.ConversationRagProviderCandidate, ...] = ()
        self.failure: BaseException | None = None
        self.calls: list[
            tuple[
                rag.ConversationRagRetrievalRequest, rag.ConversationRagEmbeddingVector
            ]
        ] = []

    def search(
        self,
        request: rag.ConversationRagRetrievalRequest,
        embedding: rag.ConversationRagEmbeddingVector,
        /,
    ) -> tuple[rag.ConversationRagProviderCandidate, ...]:
        self.calls.append((request, embedding))
        if self.failure is not None:
            raise self.failure
        return self.output


class _LexicalRetriever(_LifecycleWitness):
    def __init__(self) -> None:
        super().__init__()
        self.output: tuple[rag.ConversationRagProviderCandidate, ...] = ()
        self.failure: BaseException | None = None
        self.calls: list[rag.ConversationRagRetrievalRequest] = []

    def search(
        self,
        request: rag.ConversationRagRetrievalRequest,
        /,
    ) -> tuple[rag.ConversationRagProviderCandidate, ...]:
        self.calls.append(request)
        if self.failure is not None:
            raise self.failure
        return self.output


class _Fixture:
    def __init__(self) -> None:
        self.embedder = _Embedder()
        self.writer = _VectorWriter()
        self.resetter = _VectorResetter()
        self.reader = _VectorReader()
        self.lexical = _LexicalRetriever()
        self.collaborators = rag.ConversationRagCollaborators(
            self.embedder,
            self.writer,
            self.resetter,
            self.reader,
            self.lexical,
        )
        self.service = adapter.GenericRagConversationService(self.collaborators)

    @property
    def resources(self) -> tuple[_LifecycleWitness, ...]:
        return (
            self.embedder,
            self.writer,
            self.resetter,
            self.reader,
            self.lexical,
        )


class GenericRagAdapterTests(unittest.TestCase):
    def assertSanitized(self, error: BaseException) -> None:
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        self.assertNotIn("secret", str(error))
        self.assertNotIn("secret", repr(error))

    def test_capability_inventory_and_exact_public_exports_are_content_free(
        self,
    ) -> None:
        self.assertEqual(
            adapter.__all__,
            [
                "GenericRagCapabilitySnapshot",
                "GenericRagConversationService",
                "inspect_generic_rag_capabilities",
            ],
        )
        snapshot = adapter.inspect_generic_rag_capabilities()
        self.assertEqual(
            tuple(module for module, _symbols in snapshot.modules),
            (
                "generic_rag.contracts",
                "generic_rag.errors",
                "generic_rag.ports",
                "generic_rag.projection",
                "generic_rag.retrieval",
            ),
        )
        self.assertTrue(
            snapshot.supports(
                (
                    ("generic_rag.contracts", ("Document", "RetrievalResult")),
                    ("generic_rag.ports", ("Borrowed",)),
                )
            )
        )
        self.assertFalse(
            snapshot.supports((("generic_rag.contracts", ("MissingSymbol",)),))
        )
        self.assertNotIn("/", repr(snapshot))

    def test_rebuild_incremental_projection_and_current_mapping_are_exact(self) -> None:
        fixture = _Fixture()
        original = _projection_request(
            (
                _document("document-a", "a" * 900),
                _document("document-b", "bravo"),
            )
        )
        rebuilt = fixture.service.rebuild_projection(original, _missing_state())

        self.assertEqual(
            (
                rebuilt.status_before,
                rebuilt.outcome,
                rebuilt.attempted_documents,
                rebuilt.completed_documents,
            ),
            (
                rag.ConversationRagProjectionStatus.MISSING,
                rag.ConversationRagProjectionOutcome.COMPLETED,
                2,
                2,
            ),
        )
        self.assertEqual(fixture.resetter.sessions, ["session-a"])
        self.assertEqual(
            [
                document.key.document_id
                for document, _records in fixture.writer.replacements
            ],
            ["document-a", "document-b"],
        )
        first_records = fixture.writer.replacements[0][1]
        self.assertEqual(
            tuple(
                (
                    record.candidate.source.start_codepoint,
                    record.candidate.source.end_codepoint,
                )
                for record in first_records
            ),
            ((0, 800), (720, 900)),
        )
        self.assertEqual(
            tuple(entry.fragment_count for entry in rebuilt.checkpoint.entries),
            (2, 1),
        )

        effect_counts = (
            len(fixture.embedder.calls),
            len(fixture.writer.replacements),
            len(fixture.writer.deletions),
            len(fixture.resetter.sessions),
        )
        unchanged = fixture.service.project_documents(
            original,
            _present_state(rebuilt.checkpoint),
        )
        self.assertEqual(
            (
                unchanged.status_before,
                unchanged.outcome,
                unchanged.attempted_documents,
            ),
            (
                rag.ConversationRagProjectionStatus.CURRENT,
                rag.ConversationRagProjectionOutcome.UNCHANGED,
                0,
            ),
        )
        self.assertEqual(
            (
                len(fixture.embedder.calls),
                len(fixture.writer.replacements),
                len(fixture.writer.deletions),
                len(fixture.resetter.sessions),
            ),
            effect_counts,
        )

        updated = _projection_request(
            (_document("document-a", "updated", revision_id="revision-b"),)
        )
        incremental = fixture.service.project_documents(
            updated,
            _present_state(rebuilt.checkpoint),
        )
        self.assertEqual(
            incremental.status_before, rag.ConversationRagProjectionStatus.STALE
        )
        self.assertEqual(incremental.attempted_documents, 2)
        self.assertEqual(
            fixture.writer.replacements[-1][0].revision_id,
            "revision-b",
        )
        self.assertEqual(
            fixture.writer.deletions,
            [rag.ConversationRagDocumentKey("session-a", "document-b")],
        )
        for resource in fixture.resources:
            self.assertEqual(resource.lifecycle_calls, [])

    def test_incompatible_incremental_state_is_sanitized_before_effects(self) -> None:
        fixture = _Fixture()
        request = _projection_request((_document("document-a", "alpha"),))
        with self.assertRaises(rag.ConversationRagStateError) as missing_caught:
            fixture.service.project_documents(request, _missing_state())
        self.assertEqual(
            missing_caught.exception.status,
            rag.ConversationRagProjectionStatus.MISSING,
        )
        self.assertSanitized(missing_caught.exception)
        self.assertEqual(fixture.writer.replacements, [])

        checkpoint = fixture.service.rebuild_projection(
            request,
            _missing_state(),
        ).checkpoint
        fixture.writer.replacements.clear()
        fixture.resetter.sessions.clear()
        with self.assertRaises(rag.ConversationRagStateError) as schema_caught:
            fixture.service.project_documents(
                _projection_request(request.documents, schema_id="schema-b"),
                _present_state(checkpoint),
            )
        self.assertEqual(
            schema_caught.exception.status,
            rag.ConversationRagProjectionStatus.SCHEMA_MISMATCH,
        )
        self.assertSanitized(schema_caught.exception)
        self.assertEqual(fixture.writer.replacements, [])
        self.assertEqual(fixture.resetter.sessions, [])

        incompatible_cases = (
            (
                request,
                rag.ConversationRagProjectionState(
                    rag.ConversationRagProjectionStateAvailability.CORRUPT,
                    None,
                ),
                rag.ConversationRagProjectionStatus.CORRUPT,
            ),
            (
                rag.ConversationRagProjectionRequest(
                    "session-a",
                    "schema-a",
                    rag.ConversationRagEmbeddingIdentity("model-b", 2),
                    request.documents,
                ),
                _present_state(checkpoint),
                rag.ConversationRagProjectionStatus.EMBEDDING_MISMATCH,
            ),
        )
        for target, state, status in incompatible_cases:
            with self.subTest(status=status):
                with self.assertRaises(rag.ConversationRagStateError) as caught:
                    fixture.service.project_documents(target, state)
                self.assertEqual(caught.exception.status, status)
                self.assertSanitized(caught.exception)
        self.assertEqual(fixture.writer.replacements, [])
        self.assertEqual(fixture.resetter.sessions, [])

    def test_projection_failures_preserve_stage_counts_and_drop_provider_details(
        self,
    ) -> None:
        fixture = _Fixture()
        empty = fixture.service.rebuild_projection(
            _projection_request(()),
            _missing_state(),
        )
        fixture.writer.fail_document_id = "document-b"
        request = _projection_request(
            (
                _document("document-a", "alpha"),
                _document("document-b", "bravo"),
            )
        )
        with self.assertRaises(rag.ConversationRagOperationError) as caught:
            fixture.service.project_documents(
                request,
                _present_state(empty.checkpoint),
            )
        error = caught.exception
        self.assertEqual(
            (
                error.stage,
                error.affected_document,
                error.attempted_documents,
                error.completed_documents,
            ),
            (
                rag.ConversationRagProjectionFailureStage.REPLACEMENT,
                rag.ConversationRagDocumentKey("session-a", "document-b"),
                2,
                1,
            ),
        )
        self.assertSanitized(error)

        reset_fixture = _Fixture()
        reset_fixture.resetter.failure = RuntimeError("secret reset failure")
        with self.assertRaises(rag.ConversationRagOperationError) as reset_caught:
            reset_fixture.service.rebuild_projection(
                _projection_request((_document("document-a", "alpha"),)),
                _missing_state(),
            )
        reset_error = reset_caught.exception
        self.assertEqual(
            (
                reset_error.stage,
                reset_error.affected_document,
                reset_error.attempted_documents,
                reset_error.completed_documents,
            ),
            (rag.ConversationRagProjectionFailureStage.RESET, None, 1, 0),
        )
        self.assertSanitized(reset_error)

    def test_remaining_projection_failure_stages_are_mapped_without_details(
        self,
    ) -> None:
        request = _projection_request((_document("document-a", "alpha"),))

        identity_fixture = _Fixture()
        identity_fixture.embedder.current_identity = (
            rag.ConversationRagEmbeddingIdentity("model-b", 2)
        )
        with self.assertRaises(rag.ConversationRagOperationError) as identity_caught:
            identity_fixture.service.rebuild_projection(request, _missing_state())
        self.assertEqual(
            (
                identity_caught.exception.stage,
                identity_caught.exception.affected_document,
                identity_caught.exception.attempted_documents,
                identity_caught.exception.completed_documents,
            ),
            (rag.ConversationRagProjectionFailureStage.EMBEDDER_IDENTITY, None, 1, 0),
        )
        self.assertSanitized(identity_caught.exception)

        embedding_fixture = _Fixture()
        embedding_fixture.embedder.embedding_failure = RuntimeError(
            "secret embedding failure"
        )
        with self.assertRaises(rag.ConversationRagOperationError) as embedding_caught:
            embedding_fixture.service.rebuild_projection(request, _missing_state())
        self.assertEqual(
            (
                embedding_caught.exception.stage,
                embedding_caught.exception.affected_document,
                embedding_caught.exception.attempted_documents,
                embedding_caught.exception.completed_documents,
            ),
            (
                rag.ConversationRagProjectionFailureStage.EMBEDDING,
                rag.ConversationRagDocumentKey("session-a", "document-a"),
                1,
                0,
            ),
        )
        self.assertSanitized(embedding_caught.exception)

        deletion_fixture = _Fixture()
        checkpoint = deletion_fixture.service.rebuild_projection(
            request,
            _missing_state(),
        ).checkpoint
        deletion_fixture.writer.fail_document_id = "document-a"
        with self.assertRaises(rag.ConversationRagOperationError) as deletion_caught:
            deletion_fixture.service.project_documents(
                _projection_request(()),
                _present_state(checkpoint),
            )
        self.assertEqual(
            (
                deletion_caught.exception.stage,
                deletion_caught.exception.affected_document,
                deletion_caught.exception.attempted_documents,
                deletion_caught.exception.completed_documents,
            ),
            (
                rag.ConversationRagProjectionFailureStage.DELETION,
                rag.ConversationRagDocumentKey("session-a", "document-a"),
                1,
                0,
            ),
        )
        self.assertSanitized(deletion_caught.exception)

    def test_exact_generic_errors_and_result_classes_are_enforced(self) -> None:
        fixture = _Fixture()
        request = _projection_request(())
        with (
            mock.patch.object(
                adapter._projection,
                "rebuild_projection",
                side_effect=generic_errors.ContractValidationError(
                    "secret generic detail"
                ),
            ),
            self.assertRaises(rag.ConversationRagContractError) as caught,
        ):
            fixture.service.rebuild_projection(request, _missing_state())
        self.assertSanitized(caught.exception)

        class DerivedContractError(generic_errors.ContractValidationError):
            pass

        with (
            mock.patch.object(
                adapter._projection,
                "rebuild_projection",
                side_effect=DerivedContractError("secret subclass detail"),
            ),
            self.assertRaises(rag.ConversationRagOperationError) as derived_caught,
        ):
            fixture.service.rebuild_projection(request, _missing_state())
        self.assertEqual(
            derived_caught.exception.stage,
            rag.ConversationRagProjectionFailureStage.ADAPTER,
        )
        self.assertSanitized(derived_caught.exception)

        class DerivedRetrievalResult(generic_contracts.RetrievalResult):
            pass

        generic_query = generic_contracts.RetrievalQuery(
            "session-a",
            "query",
            1,
            20,
        )
        derived_result = DerivedRetrievalResult(
            generic_query,
            generic_contracts.RetrievalOutcome.UNAVAILABLE,
            (),
            False,
        )
        with (
            mock.patch.object(
                adapter._retrieval,
                "retrieve_semantic",
                return_value=derived_result,
            ),
            self.assertRaises(rag.ConversationRagOperationError) as result_caught,
        ):
            fixture.service.retrieve_semantic(
                rag.ConversationRagRetrievalRequest("session-a", "query", 1),
                _missing_state(),
            )
        self.assertEqual(
            result_caught.exception.stage,
            rag.ConversationRagProjectionFailureStage.ADAPTER,
        )
        self.assertSanitized(result_caught.exception)

    def test_semantic_and_hybrid_results_are_ranked_bounded_and_text_free(self) -> None:
        fixture = _Fixture()
        projected = fixture.service.rebuild_projection(
            _projection_request((_document("document-a", "a" * 900),)),
            _missing_state(),
        )
        records = fixture.writer.replacements[0][1]
        provider_candidates = tuple(record.candidate for record in records)
        fixture.reader.output = provider_candidates
        fixture.lexical.output = tuple(reversed(provider_candidates))
        state = _present_state(projected.checkpoint)

        semantic_request = rag.ConversationRagRetrievalRequest(
            "session-a",
            "find alpha",
            1,
        )
        semantic = fixture.service.retrieve_semantic(semantic_request, state)
        self.assertEqual(
            (
                semantic.outcome,
                semantic.reason,
                len(semantic.candidates),
                semantic.truncated,
            ),
            (
                rag.ConversationRagOutcome.COMPLETE,
                rag.ConversationRagReason.NONE,
                1,
                True,
            ),
        )
        self.assertFalse(hasattr(semantic.candidates[0], "text"))
        for provider in provider_candidates:
            self.assertNotIn(provider.text, repr(semantic))

        hybrid = fixture.service.retrieve_hybrid(
            rag.ConversationRagRetrievalRequest("session-a", "find alpha", 2),
            state,
        )
        self.assertEqual(hybrid.outcome, rag.ConversationRagOutcome.COMPLETE)
        self.assertEqual(
            tuple(candidate.rank for candidate in hybrid.candidates),
            (1, 2),
        )
        self.assertEqual(
            len({candidate.source for candidate in hybrid.candidates}),
            2,
        )
        for resource in fixture.resources:
            self.assertEqual(resource.lifecycle_calls, [])

    def test_partial_stale_unknown_and_missing_retrieval_paths_fail_closed(
        self,
    ) -> None:
        fixture = _Fixture()
        projected = fixture.service.rebuild_projection(
            _projection_request((_document("document-a", "alpha"),)),
            _missing_state(),
        )
        state = _present_state(projected.checkpoint)
        candidate = fixture.writer.replacements[0][1][0].candidate
        fixture.reader.output = (candidate,)
        fixture.lexical.failure = RuntimeError("secret lexical failure")
        request = rag.ConversationRagRetrievalRequest("session-a", "query", 2)

        partial = fixture.service.retrieve_hybrid(request, state)
        self.assertEqual(
            (partial.outcome, partial.reason, len(partial.candidates)),
            (rag.ConversationRagOutcome.PARTIAL, rag.ConversationRagReason.PARTIAL, 1),
        )

        fixture.embedder.current_identity = rag.ConversationRagEmbeddingIdentity(
            "model-stale",
            2,
        )
        stale = fixture.service.retrieve_semantic(request, state)
        self.assertEqual(
            (stale.outcome, stale.reason, stale.candidates),
            (
                rag.ConversationRagOutcome.LITERAL_FALLBACK,
                rag.ConversationRagReason.STALE,
                (),
            ),
        )
        fixture.embedder.current_identity = rag.ConversationRagEmbeddingIdentity(
            "model-a",
            2,
        )

        for unknown_document in (
            _document("document-unknown", "other").identity,
            _document(
                "document-a",
                "other",
                revision_id="revision-stale",
            ).identity,
        ):
            source = rag.ConversationRagSourceReference(
                unknown_document,
                "unknown-fragment",
                0,
                5,
            )
            fixture.reader.output = (
                rag.ConversationRagProviderCandidate(source, "other"),
            )
            with self.subTest(document=unknown_document):
                failed = fixture.service.retrieve_semantic(request, state)
                self.assertEqual(
                    (failed.outcome, failed.reason, failed.candidates),
                    (
                        rag.ConversationRagOutcome.LITERAL_FALLBACK,
                        rag.ConversationRagReason.FAILED,
                        (),
                    ),
                )

        calls_before = (
            len(fixture.embedder.calls),
            len(fixture.reader.calls),
            len(fixture.lexical.calls),
        )
        unavailable = fixture.service.retrieve_hybrid(request, _missing_state())
        self.assertEqual(
            (unavailable.outcome, unavailable.reason),
            (
                rag.ConversationRagOutcome.LITERAL_FALLBACK,
                rag.ConversationRagReason.UNAVAILABLE,
            ),
        )
        self.assertEqual(
            (
                len(fixture.embedder.calls),
                len(fixture.reader.calls),
                len(fixture.lexical.calls),
            ),
            calls_before,
        )

    def test_cross_provider_payload_conflict_drops_every_candidate(self) -> None:
        fixture = _Fixture()
        projected = fixture.service.rebuild_projection(
            _projection_request((_document("document-a", "alpha"),)),
            _missing_state(),
        )
        candidate = fixture.writer.replacements[0][1][0].candidate
        fixture.reader.output = (candidate,)
        fixture.lexical.output = (
            rag.ConversationRagProviderCandidate(
                candidate.source,
                "x" * len(candidate.text),
            ),
        )
        result = fixture.service.retrieve_hybrid(
            rag.ConversationRagRetrievalRequest("session-a", "query", 2),
            _present_state(projected.checkpoint),
        )
        self.assertEqual(
            (result.outcome, result.reason, result.candidates, result.truncated),
            (
                rag.ConversationRagOutcome.LITERAL_FALLBACK,
                rag.ConversationRagReason.FAILED,
                (),
                False,
            ),
        )

    def test_process_controls_propagate_and_caller_lifecycle_remains_untouched(
        self,
    ) -> None:
        request = _projection_request((_document("document-a", "alpha"),))
        for control in (KeyboardInterrupt("stop"), SystemExit(7)):
            with self.subTest(control=type(control).__name__):
                fixture = _Fixture()
                fixture.embedder.identity_failure = control
                with self.assertRaises(type(control)) as caught:
                    fixture.service.rebuild_projection(request, _missing_state())
                self.assertIs(caught.exception, control)
                for resource in fixture.resources:
                    self.assertEqual(resource.lifecycle_calls, [])

        for control in (KeyboardInterrupt("stop"), SystemExit(9)):
            with self.subTest(retrieval_control=type(control).__name__):
                fixture = _Fixture()
                projected = fixture.service.rebuild_projection(
                    request,
                    _missing_state(),
                )
                fixture.reader.failure = control
                with self.assertRaises(type(control)) as caught:
                    fixture.service.retrieve_semantic(
                        rag.ConversationRagRetrievalRequest(
                            "session-a",
                            "query",
                            1,
                        ),
                        _present_state(projected.checkpoint),
                    )
                self.assertIs(caught.exception, control)
                for resource in fixture.resources:
                    self.assertEqual(resource.lifecycle_calls, [])


if __name__ == "__main__":
    unittest.main()
