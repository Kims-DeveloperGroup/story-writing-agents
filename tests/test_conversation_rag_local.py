"""Deterministic dependency-free local conversation RAG verification."""

from __future__ import annotations

import hashlib
import itertools
import math
import os
import struct
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from story_writing_agents import conversation_rag as rag
from story_writing_agents import conversation_rag_local as local

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_ROOT = _REPOSITORY_ROOT / "src"
_VECTOR_GOLDENS = {
    "Alpha beta": "b6a3a62f6c0847c627c4ecea8c8daa643ba3e339c39b3810eeaf49389ec963d1",
    "ＡLPHA  Straße\u200d\n한글 🌙": (
        "c4ed68144fb263d964c31369110b8e58a91716f610f556d940ece2965096f870"
    ),
    "\U00011f02": "50a58d7c5c230bc4ac4ccf52f9d0b152efb40eb962f81814362dab024ae1302d",
    "\U00011380": "50a58d7c5c230bc4ac4ccf52f9d0b152efb40eb962f81814362dab024ae1302d",
    "�": "50a58d7c5c230bc4ac4ccf52f9d0b152efb40eb962f81814362dab024ae1302d",
}


def _vector_digest(vector: rag.ConversationRagEmbeddingVector) -> str:
    return hashlib.sha256(struct.pack(">256d", *vector.values)).hexdigest()


def _document(
    document_id: str,
    *,
    session_id: str = "session-a",
    conversation_id: str | None = None,
    revision_id: str = "revision-a",
) -> rag.ConversationRagDocumentIdentity:
    return rag.ConversationRagDocumentIdentity(
        rag.ConversationRagDocumentKey(session_id, document_id),
        conversation_id or f"conversation-{document_id}",
        "turn-a",
        "turn-a",
        1,
        1,
        revision_id,
    )


def _record(
    document_id: str,
    score: float,
    *,
    session_id: str = "session-a",
    revision_id: str = "revision-a",
    text: str = "alpha",
) -> rag.ConversationRagVectorRecord:
    source = rag.ConversationRagSourceReference(
        _document(
            document_id,
            session_id=session_id,
            revision_id=revision_id,
        ),
        f"fragment-{document_id}-{revision_id}",
        0,
        len(text),
    )
    values = (score, *(0.0 for _ in range(local.LOCAL_CONVERSATION_RAG_DIMENSIONS - 1)))
    return rag.ConversationRagVectorRecord(
        rag.ConversationRagProviderCandidate(source, text),
        rag.ConversationRagEmbeddingVector(tuple(values)),
    )


def _query_vector(value: float = 1.0) -> rag.ConversationRagEmbeddingVector:
    return rag.ConversationRagEmbeddingVector(
        (value, *(0.0 for _ in range(local.LOCAL_CONVERSATION_RAG_DIMENSIONS - 1)))
    )


class LocalConversationRagTests(unittest.TestCase):
    def test_exact_surface_identity_and_fixed_values(self) -> None:
        self.assertEqual(
            local.__all__,
            [
                "LOCAL_CONVERSATION_RAG_DIMENSIONS",
                "LOCAL_CONVERSATION_RAG_MODEL_ID",
                "LOCAL_CONVERSATION_RAG_PROVIDER_ID",
                "LocalConversationRagEmbedder",
                "LocalConversationRagVectorRanker",
            ],
        )
        self.assertEqual(local.LOCAL_CONVERSATION_RAG_PROVIDER_ID, "local")
        self.assertEqual(
            local.LOCAL_CONVERSATION_RAG_MODEL_ID,
            "story-conversation-rag-local-hash-v1",
        )
        self.assertEqual(local.LOCAL_CONVERSATION_RAG_DIMENSIONS, 256)
        identity = local.LocalConversationRagEmbedder().identity
        self.assertEqual(
            identity,
            rag.ConversationRagEmbeddingIdentity(
                "story-conversation-rag-local-hash-v1",
                256,
            ),
        )
        self.assertIs(identity, local.LocalConversationRagEmbedder().identity)

    def test_embedding_goldens_pin_unicode_normalization_and_binary_floats(
        self,
    ) -> None:
        embedder = local.LocalConversationRagEmbedder()
        samples = tuple(_VECTOR_GOLDENS)
        vectors = embedder.embed(samples)
        self.assertEqual(len(vectors), len(samples))
        for sample, vector in zip(samples, vectors, strict=True):
            with self.subTest(sample=sample.encode("unicode_escape")):
                self.assertEqual(len(vector.values), 256)
                self.assertTrue(all(math.isfinite(value) for value in vector.values))
                self.assertEqual(_vector_digest(vector), _VECTOR_GOLDENS[sample])
                self.assertAlmostEqual(
                    math.fsum(value * value for value in vector.values),
                    1.0,
                    places=15,
                )

        replacement = embedder.embed(("�",))[0]
        self.assertEqual(embedder.embed(("\U00011f02",))[0], replacement)
        self.assertEqual(embedder.embed(("\U00011380",))[0], replacement)
        for left, right in (
            ("Ａｌｐｈａ", "alpha"),
            ("Straße", "strasse"),
            ("e\u0301", "é"),
            ("alpha\u200d\nbeta", "alpha beta"),
        ):
            with self.subTest(left=left, right=right):
                self.assertEqual(embedder.embed((left,)), embedder.embed((right,)))

        zero = (0.0,) * 256
        self.assertEqual(
            embedder.embed(("", " \n\t", "\x00")),
            tuple(rag.ConversationRagEmbeddingVector(zero) for _ in range(3)),
        )

    def test_embedding_contract_rejects_wrong_shapes_before_partial_results(
        self,
    ) -> None:
        embedder = local.LocalConversationRagEmbedder()
        with self.assertRaisesRegex(TypeError, "must be a tuple"):
            embedder.embed(["alpha"])  # type: ignore[arg-type]
        self.assertEqual(len(embedder.embed(("a",) * 32)), 32)
        with self.assertRaisesRegex(ValueError, "batch budget"):
            embedder.embed(("a",) * 33)
        self.assertEqual(len(embedder.embed(("a" * 800,))[0].values), 256)
        with self.assertRaisesRegex(ValueError, "fragment budget"):
            embedder.embed(("a" * 801,))
        with self.assertRaisesRegex(ValueError, "valid UTF-8"):
            embedder.embed(("bad\ud800",))
        with self.assertRaisesRegex(TypeError, "must be a string"):
            embedder.embed((1,))  # type: ignore[arg-type]

    def test_hash_seed_locale_and_timezone_do_not_change_the_golden(self) -> None:
        script = textwrap.dedent(
            f"""
            import hashlib
            import struct
            import sys
            sys.path.insert(0, {str(_SOURCE_ROOT)!r})
            from story_writing_agents.conversation_rag_local import LocalConversationRagEmbedder
            value = LocalConversationRagEmbedder().embed(("ＡLPHA  Straße\\u200d\\n한글 🌙",))[0]
            print(hashlib.sha256(struct.pack(">256d", *value.values)).hexdigest())
            """
        )
        observed: list[str] = []
        for seed, locale_name, timezone in (
            ("1", "C", "UTC0"),
            ("8675309", "C", "KST-9"),
            ("random", "C", "GMT+11"),
        ):
            environment = os.environ.copy()
            environment.update(
                {
                    "LC_ALL": locale_name,
                    "LANG": locale_name,
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONHASHSEED": seed,
                    "TZ": timezone,
                }
            )
            completed = subprocess.run(
                [sys.executable, "-B", "-W", "error", "-c", script],
                cwd=_REPOSITORY_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            observed.append(completed.stdout.strip())
        self.assertEqual(
            observed,
            [_VECTOR_GOLDENS["ＡLPHA  Straße\u200d\n한글 🌙"]] * 3,
        )

    def test_rank_orders_scores_then_full_source_key_and_drops_nonpositive(
        self,
    ) -> None:
        ranker = local.LocalConversationRagVectorRanker()
        request = rag.ConversationRagRetrievalRequest("session-a", "query", 3)
        high = _record("document-z", 1.0)
        tied_b = _record("document-b", 0.5)
        tied_a = _record("document-a", 0.5)
        zero = _record("document-zero", 0.0)
        negative = _record("document-negative", -1.0)
        ranked = ranker.rank(
            request,
            _query_vector(),
            (zero, tied_b, negative, high, tied_a),
        )
        self.assertEqual(
            tuple(candidate.source.document.key.document_id for candidate in ranked),
            ("document-z", "document-a", "document-b"),
        )
        self.assertEqual(
            ranked,
            ranker.rank(
                request,
                _query_vector(),
                (tied_a, high, tied_b, negative, zero),
            ),
        )

        revision_b = _record("document-a", 0.25, revision_id="revision-b")
        revision_a = _record("document-a", 0.25, revision_id="revision-a")
        self.assertEqual(
            ranker.rank(request, _query_vector(), (revision_b, revision_a)),
            (revision_a.candidate, revision_b.candidate),
        )

    def test_rank_deduplicates_exact_records_and_rejects_conflicts(self) -> None:
        ranker = local.LocalConversationRagVectorRanker()
        request = rag.ConversationRagRetrievalRequest("session-a", "query", 20)
        record = _record("document-a", 1.0)
        self.assertEqual(
            ranker.rank(request, _query_vector(), (record, record, record)),
            (record.candidate,),
        )
        conflicting = rag.ConversationRagVectorRecord(
            rag.ConversationRagProviderCandidate(record.candidate.source, "bravo"),
            record.embedding,
        )
        with self.assertRaisesRegex(ValueError, "conflicting duplicates"):
            ranker.rank(request, _query_vector(), (record, conflicting))

        with self.assertRaisesRegex(ValueError, "another session"):
            ranker.rank(
                request, _query_vector(), (_record("x", 1.0, session_id="other"),)
            )
        with self.assertRaisesRegex(ValueError, "incompatible dimensions"):
            ranker.rank(
                request,
                rag.ConversationRagEmbeddingVector((1.0,)),
                (record,),
            )
        with self.assertRaisesRegex(TypeError, "exact vector records"):
            ranker.rank(request, _query_vector(), (object(),))  # type: ignore[arg-type]

    def test_rank_record_budget_and_process_controls_are_exact(self) -> None:
        ranker = local.LocalConversationRagVectorRanker()
        request = rag.ConversationRagRetrievalRequest("session-a", "query", 1)
        record = _record("document-a", 1.0)
        self.assertEqual(
            ranker.rank(
                request,
                _query_vector(),
                itertools.repeat(record, 65_536),
            ),
            (record.candidate,),
        )
        with self.assertRaisesRegex(ValueError, "record budget"):
            ranker.rank(
                request,
                _query_vector(),
                itertools.repeat(record, 65_537),
            )

        class ControlledIterable:
            def __init__(self, control: BaseException) -> None:
                self.control = control

            def __iter__(self):  # type: ignore[no-untyped-def]
                raise self.control

        for control in (KeyboardInterrupt("stop"), SystemExit(7)):
            with self.subTest(control=type(control).__name__):
                with self.assertRaises(type(control)) as caught:
                    ranker.rank(request, _query_vector(), ControlledIterable(control))
                self.assertIs(caught.exception, control)


if __name__ == "__main__":
    unittest.main()
