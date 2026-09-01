"""Authorized schema-v2 collaboration-RAG runtime tests."""

from __future__ import annotations

import inspect
import unittest
from dataclasses import FrozenInstanceError, fields, replace

from story_writing_agents.conversation_rag import (
    ConversationRagEmbeddingIdentity,
    ConversationRagOutcome,
    ConversationRagProjectionState,
    ConversationRagProjectionStateAvailability,
    ConversationRagReason,
)
from story_writing_agents.conversation_rag_runtime import (
    CONVERSATION_RAG_RUNTIME_POLICY,
    CONVERSATION_RAG_RUNTIME_SCHEMA_ID,
    ConversationRagRuntimeError,
    ConversationRagRuntimeInspection,
    ConversationRagRuntimeInspectionStatus,
    ConversationRagRuntimePolicy,
    ConversationRagRuntimeService,
    ConversationRagRuntimeState,
    ConversationRagRuntimeStateSnapshot,
    inspect_authorized_conversation_rag,
)
from story_writing_agents.conversation_storage import (
    compose_filesystem_conversation_storage,
)
from story_writing_agents.conversations import CollaborationEventBatch

from tests.collaboration_contract_fixture import contract_batches
from tests.helpers import initialized_workspace


SESSION_ID = "rag_runtime_session"


class _StatePort:
    def __init__(self, workspace_key: str) -> None:
        self.workspace_key = workspace_key
        self.embedding = ConversationRagEmbeddingIdentity(
            model_id="test-embedding-v2",
            dimensions=3,
        )
        self.snapshot = ConversationRagRuntimeStateSnapshot(
            session_id=SESSION_ID,
            state=ConversationRagProjectionState(
                availability=ConversationRagProjectionStateAvailability.MISSING,
                checkpoint=None,
            ),
            reason=ConversationRagReason.UNAVAILABLE,
        )
        self.inspections: list[str] = []
        self.stale_calls: list[str] = []

    def inspect_projection_state(
        self,
        session_id: str,
        /,
    ) -> ConversationRagRuntimeStateSnapshot:
        self.inspections.append(session_id)
        return self.snapshot

    def mark_projection_stale(self, session_id: str, /) -> None:
        self.stale_calls.append(session_id)


class _UnusedLowLevelService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def project_documents(self, request, state):  # type: ignore[no-untyped-def]
        self.calls.append("project")
        raise AssertionError("projection was not requested")

    def rebuild_projection(self, request, state):  # type: ignore[no-untyped-def]
        self.calls.append("rebuild")
        raise AssertionError("projection was not requested")

    def retrieve_semantic(self, request, state):  # type: ignore[no-untyped-def]
        self.calls.append("semantic")
        raise AssertionError("missing state must not run semantic retrieval")

    def retrieve_hybrid(self, request, state, lexical):  # type: ignore[no-untyped-def]
        self.calls.append("hybrid")
        raise AssertionError("hybrid retrieval was not requested")


def _services(test_case: unittest.TestCase):  # type: ignore[no-untyped-def]
    workspace = initialized_workspace(test_case)
    services = compose_filesystem_conversation_storage(workspace)
    metadata = services.catalog.create(SESSION_ID, "Subject-only search decoy")
    batch = contract_batches(metadata.conversation_id)["proposal"]
    batch = CollaborationEventBatch(
        batch_id=batch.batch_id,
        expected_last_sequence=batch.expected_last_sequence,
        events=tuple(replace(event, session_id=SESSION_ID) for event in batch.events),
    )
    services.event_store.append_batch(batch)
    return workspace, services, metadata


class ConversationRagRuntimeTests(unittest.TestCase):
    def test_contract_exports_policy_signatures_and_state_port_are_exact(self) -> None:
        import story_writing_agents.conversation_rag_runtime as runtime

        self.assertEqual(
            tuple(runtime.__all__),
            (
                "CONVERSATION_RAG_RUNTIME_POLICY",
                "CONVERSATION_RAG_RUNTIME_SCHEMA_ID",
                "ConversationRagRuntimeInspection",
                "ConversationRagRuntimeInspectionStatus",
                "ConversationRagRuntimeError",
                "ConversationRagRuntimePolicy",
                "ConversationRagRuntimeService",
                "ConversationRagRuntimeState",
                "ConversationRagRuntimeStateSnapshot",
                "inspect_authorized_conversation_rag",
            ),
        )
        self.assertEqual(
            CONVERSATION_RAG_RUNTIME_SCHEMA_ID,
            "story-collaboration-rag-readable-event-v2",
        )
        self.assertEqual(
            CONVERSATION_RAG_RUNTIME_POLICY,
            ConversationRagRuntimePolicy(
                200, 20, 20, 20, 800, 32_768, 32 * 1_024 * 1_024
            ),
        )
        self.assertTrue(getattr(ConversationRagRuntimeState, "_is_protocol", False))
        self.assertEqual(
            tuple(inspect.signature(inspect_authorized_conversation_rag).parameters),
            ("conversation_storage_services", "state_port", "session_id"),
        )

    def test_inspection_counts_one_document_per_readable_event_without_mutation(
        self,
    ) -> None:
        workspace, services, metadata = _services(self)
        state = _StatePort(services.provenance.workspace_key)
        before = {
            path.relative_to(workspace.root).as_posix(): path.read_bytes()
            for path in workspace.root.rglob("*")
            if path.is_file()
        }
        first = inspect_authorized_conversation_rag(services, state, SESSION_ID)
        second = inspect_authorized_conversation_rag(services, state, SESSION_ID)
        self.assertEqual(first, second)
        self.assertIs(first.status, ConversationRagRuntimeInspectionStatus.MISSING)
        self.assertEqual(first.authoritative_conversations, 1)
        history = services.event_store.read(SESSION_ID, metadata.conversation_id)
        self.assertEqual(first.authoritative_events, len(history))
        self.assertEqual(first.authoritative_documents, len(history))
        self.assertGreater(first.authoritative_source_utf8_bytes, 0)
        self.assertEqual(first.indexed_documents, 0)
        self.assertTrue(first.rebuild_required)
        after = {
            path.relative_to(workspace.root).as_posix(): path.read_bytes()
            for path in workspace.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)

    def test_literal_preflight_returns_exact_event_field_citation_provider_free(
        self,
    ) -> None:
        _workspace, services, metadata = _services(self)
        state = _StatePort(services.provenance.workspace_key)
        low_level = _UnusedLowLevelService()
        runtime = ConversationRagRuntimeService(low_level, state)
        context = runtime.search_authorized(
            services,
            SESSION_ID,
            "Consult specialists",
        )
        self.assertIs(context.outcome.outcome, ConversationRagOutcome.LITERAL_FALLBACK)
        self.assertIs(context.outcome.reason, ConversationRagReason.UNAVAILABLE)
        self.assertEqual(len(context.excerpts), 1)
        excerpt = context.excerpts[0]
        citation = excerpt.source.citation
        self.assertEqual(citation.conversation_id, metadata.conversation_id)
        self.assertEqual(citation.event_id, "proposal_event")
        self.assertEqual(excerpt.reference.document.start_sequence, citation.sequence)
        self.assertEqual(excerpt.reference.document.end_sequence, citation.sequence)
        self.assertEqual(excerpt.content, "Consult specialists")
        self.assertEqual(low_level.calls, [])

    def test_disabled_automatic_preflight_is_io_free_and_returns_none(self) -> None:
        _workspace, services, _metadata = _services(self)
        state = _StatePort(services.provenance.workspace_key)
        low_level = _UnusedLowLevelService()
        runtime = ConversationRagRuntimeService(low_level, state)
        self.assertFalse(runtime.automatic_enabled)
        self.assertIsNone(runtime.preflight(services, SESSION_ID, "turn text"))
        self.assertEqual(state.inspections, [])
        self.assertEqual(low_level.calls, [])

    def test_invalid_or_failing_state_is_sanitized_without_content(self) -> None:
        _workspace, services, _metadata = _services(self)
        state = _StatePort(services.provenance.workspace_key)

        def fail(_session: str):
            raise RuntimeError("PRIVATE_STATE_FAILURE")

        state.inspect_projection_state = fail  # type: ignore[method-assign]
        with self.assertRaises(ConversationRagRuntimeError) as caught:
            inspect_authorized_conversation_rag(services, state, SESSION_ID)
        self.assertEqual(
            str(caught.exception), "conversation RAG runtime operation failed"
        )
        self.assertNotIn("PRIVATE", repr(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def test_inspection_values_are_frozen_slotted_and_strict(self) -> None:
        value = ConversationRagRuntimeInspection(
            session_id=SESSION_ID,
            status=ConversationRagRuntimeInspectionStatus.MISSING,
            authoritative_checkpoint="sha256:" + "1" * 64,
            indexed_checkpoint=None,
            authoritative_conversations=0,
            authoritative_events=0,
            authoritative_documents=0,
            authoritative_source_utf8_bytes=0,
            indexed_documents=0,
            rebuild_required=True,
        )
        self.assertEqual(
            tuple(field.name for field in fields(value)),
            (
                "session_id",
                "status",
                "authoritative_checkpoint",
                "indexed_checkpoint",
                "authoritative_conversations",
                "authoritative_events",
                "authoritative_documents",
                "authoritative_source_utf8_bytes",
                "indexed_documents",
                "rebuild_required",
            ),
        )
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            value.status = ConversationRagRuntimeInspectionStatus.READY  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
