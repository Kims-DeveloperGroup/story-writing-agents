"""Existing persisted generation format scenarios for the concrete store test case."""

from __future__ import annotations

import inspect
import json
from dataclasses import FrozenInstanceError, fields

from story_writing_agents import conversation_rag as rag
from story_writing_agents import conversation_rag_store as store_module
from story_writing_agents.conversation_rag_local import (
    LocalConversationRagEmbedder,
    LocalConversationRagVectorRanker,
)
from story_writing_agents.conversation_rag_store import (
    CONVERSATION_RAG_STORE_SCHEMA_ID,
    ConversationRagGenerationStore,
    ConversationRagStoreCounts,
    ConversationRagStoreError,
    ConversationRagStoreFailure,
    ConversationRagStoreSnapshot,
    ConversationRagStoreStatus,
)
from tests.conversation_rag.store_fixture import (
    _canonical_json,
    _persist_oversized_float_exponent,
    _publish,
    _request,
)


class GenerationFormatContractMixin:
    def test_exact_surface_values_counts_snapshots_and_errors(self) -> None:
        self.assertEqual(
            store_module.__all__,
            [
                "CONVERSATION_RAG_STORE_SCHEMA_ID",
                "ConversationRagStoreStatus",
                "ConversationRagStoreFailure",
                "ConversationRagStoreCounts",
                "ConversationRagStoreSnapshot",
                "ConversationRagGenerationBuilder",
                "ConversationRagGenerationStore",
                "ConversationRagStoreError",
            ],
        )
        self.assertEqual(
            CONVERSATION_RAG_STORE_SCHEMA_ID,
            "story-conversation-rag-store-v1",
        )
        inspection = inspect.signature(ConversationRagGenerationStore.inspect)
        self.assertEqual(
            tuple(
                (parameter.name, parameter.kind, parameter.default)
                for parameter in inspection.parameters.values()
            ),
            (
                ("self", inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.empty),
                (
                    "session_id",
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.empty,
                ),
                (
                    "provider_id",
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.empty,
                ),
                (
                    "embedding",
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.empty,
                ),
                (
                    "expected_projection_schema_id",
                    inspect.Parameter.KEYWORD_ONLY,
                    CONVERSATION_RAG_STORE_SCHEMA_ID,
                ),
            ),
        )
        self.assertEqual(
            tuple(item.value for item in ConversationRagStoreStatus),
            ("missing", "ready", "stale", "corrupt", "incompatible", "provider_failed"),
        )
        self.assertEqual(
            tuple(item.value for item in ConversationRagStoreFailure),
            (
                "unsupported",
                "busy",
                "unsafe",
                "corrupt",
                "incompatible",
                "provider_failed",
                "confirmation_required",
                "operation_failed",
            ),
        )
        self.assertEqual(
            tuple(field.name for field in fields(ConversationRagStoreCounts)),
            (
                "conversations",
                "events",
                "documents",
                "fragments",
                "source_map_records",
                "vector_records",
                "generations",
                "orphan_generations",
                "source_utf8_bytes",
                "payload_bytes",
                "generation_bytes",
            ),
        )
        counts = ConversationRagStoreCounts()
        with self.assertRaises(FrozenInstanceError):
            counts.documents = 1  # type: ignore[misc]
        with self.assertRaisesRegex(ValueError, "record counts"):
            ConversationRagStoreCounts(fragments=1)
        with self.assertRaisesRegex(TypeError, "must be an integer"):
            ConversationRagStoreCounts(events=True)  # type: ignore[arg-type]
        for failure in ConversationRagStoreFailure:
            with self.subTest(failure=failure):
                self.assertStoreFailure(ConversationRagStoreError(failure), failure)

    def test_canonical_corruption_size_and_generation_parity_fail_closed(self) -> None:
        cases = ("bom", "pretty", "metadata_count", "jsonl_blank", "oversized")
        for case in cases:
            with self.subTest(case=case):
                workspace, store = self.new_store()
                _result, snapshot = _publish(store, _request())
                session = self.session_root(workspace)
                generation = self.generation_root(workspace, snapshot)
                if case == "bom":
                    active = session / "active.json"
                    active.write_bytes(b"\xef\xbb\xbf" + active.read_bytes())
                elif case == "pretty":
                    active = session / "active.json"
                    active.write_text(
                        json.dumps(json.loads(active.read_bytes()), indent=2),
                        encoding="utf-8",
                    )
                elif case == "metadata_count":
                    metadata_path = generation / "generation.json"
                    metadata = json.loads(metadata_path.read_bytes())
                    metadata["fragment_count"] = 2
                    metadata_path.write_bytes(_canonical_json(metadata))
                elif case == "jsonl_blank":
                    path = generation / "vectors.jsonl"
                    path.write_bytes(path.read_bytes() + b"\n")
                else:
                    active = session / "active.json"
                    with active.open("r+b") as handle:
                        handle.truncate(store_module._MAX_POINTER_BYTES + 1)
                corrupt = store.inspect(
                    "session-a",
                    "local",
                    LocalConversationRagEmbedder().identity,
                )
                self.assertIs(corrupt.status, ConversationRagStoreStatus.CORRUPT)
                self.assertIs(
                    corrupt.state.availability,
                    rag.ConversationRagProjectionStateAvailability.CORRUPT,
                )
                self.assertIsNone(corrupt.generation_id)

    def test_bounded_hostile_json_and_float_exponents_are_fixed_corruption(
        self,
    ) -> None:
        cases: tuple[tuple[str, bytes | None], ...] = (
            ("deep_nesting", b"[" * 2_048 + b"0" + b"]" * 2_048 + b"\n"),
            ("nan", b"NaN\n"),
            ("infinity", b"Infinity\n"),
            ("overflow_decimal", b"1e400\n"),
            ("oversized_float_hex", None),
        )
        for case, active_payload in cases:
            with self.subTest(case=case):
                workspace, store = self.new_store()
                _result, ready = _publish(store, _request())
                session = self.session_root(workspace)
                generation = self.generation_root(workspace, ready)
                if active_payload is None:
                    _persist_oversized_float_exponent(session, generation)
                    active = json.loads((session / "active.json").read_bytes())
                    mutated = session / "generations" / active["generation_id"]
                    self.assertLess(
                        (mutated / "vectors.jsonl").stat().st_size,
                        store_module._MAX_JSONL_RECORD_BYTES,
                    )
                else:
                    self.assertLess(
                        len(active_payload),
                        store_module._MAX_POINTER_BYTES,
                    )
                    (session / "active.json").write_bytes(active_payload)

                observed = store.inspect(
                    "session-a",
                    "local",
                    LocalConversationRagEmbedder().identity,
                )
                self.assertIs(observed.status, ConversationRagStoreStatus.CORRUPT)
                with self.assertRaises(ConversationRagStoreError) as caught:
                    store.open_active_reader(ready, LocalConversationRagVectorRanker())
                self.assertStoreFailure(
                    caught.exception,
                    ConversationRagStoreFailure.CORRUPT,
                )
