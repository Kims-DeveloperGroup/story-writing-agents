"""Pure deterministic local embedding and ranking for conversation RAG."""

from __future__ import annotations

import hashlib
import math
import unicodedata
from collections.abc import Iterable

from .conversation_literal_search import normalize_conversation_literal_text
from .conversation_rag import (
    CONVERSATION_RAG_POLICY,
    ConversationRagEmbeddingIdentity,
    ConversationRagEmbeddingVector,
    ConversationRagProviderCandidate,
    ConversationRagRetrievalRequest,
    ConversationRagSourceReference,
    ConversationRagVectorRecord,
)

LOCAL_CONVERSATION_RAG_PROVIDER_ID = "local"
LOCAL_CONVERSATION_RAG_MODEL_ID = "story-conversation-rag-local-hash-v1"
LOCAL_CONVERSATION_RAG_DIMENSIONS = 256

_FEATURE_DOMAIN = "story-conversation-rag-local-feature-v1"
_TOKEN_KIND = "token"
_CODEPOINT_KINDS = ("cp1", "cp2", "cp3")
_LEFT_BOUNDARY = "\x02"
_RIGHT_BOUNDARY = "\x03"
# Unicode assignments after the frozen 3.2 database vary across supported
# interpreters. Replacing them before the shared normalizer pins V1 feature
# classification without shipping a second Unicode table.
_UNASSIGNED_REPLACEMENT = "\ufffd"
_IDENTITY = ConversationRagEmbeddingIdentity(
    LOCAL_CONVERSATION_RAG_MODEL_ID,
    LOCAL_CONVERSATION_RAG_DIMENSIONS,
)
_ZERO_VECTOR = ConversationRagEmbeddingVector(
    (0.0,) * LOCAL_CONVERSATION_RAG_DIMENSIONS
)


def _strict_bounded_text(value: object) -> str:
    if type(value) is not str:
        raise TypeError("local embedding text must be a string")
    assert isinstance(value, str)
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise ValueError("local embedding text must be valid UTF-8") from None
    if len(value) > CONVERSATION_RAG_POLICY.max_fragment_codepoints:
        raise ValueError("local embedding text exceeds the fragment budget")
    return value


def _feature_digest(kind: str, value: str) -> bytes:
    digest = hashlib.sha256()
    for field in (_FEATURE_DOMAIN, kind, value):
        encoded = field.encode("utf-8", errors="strict")
        digest.update(len(encoded).to_bytes(8, "big", signed=False))
        digest.update(encoded)
    return digest.digest()


def _accumulate_feature(accumulator: list[int], kind: str, value: str) -> None:
    digest = _feature_digest(kind, value)
    bucket = int.from_bytes(digest[:8], "big", signed=False) % len(accumulator)
    accumulator[bucket] += -1 if digest[8] & 1 else 1


def _embed_one(value: str) -> ConversationRagEmbeddingVector:
    source = _strict_bounded_text(value)
    pinned = "".join(
        _UNASSIGNED_REPLACEMENT
        if unicodedata.ucd_3_2_0.category(character) == "Cn"
        else character
        for character in source
    )
    normalized = normalize_conversation_literal_text(pinned)
    if not normalized:
        return _ZERO_VECTOR

    accumulator = [0] * LOCAL_CONVERSATION_RAG_DIMENSIONS
    for token in normalized.split(" "):
        _accumulate_feature(accumulator, _TOKEN_KIND, token)
        padded = _LEFT_BOUNDARY + token + _RIGHT_BOUNDARY
        for width, kind in enumerate(_CODEPOINT_KINDS, start=1):
            for start in range(len(padded) - width + 1):
                _accumulate_feature(
                    accumulator,
                    kind,
                    padded[start : start + width],
                )

    norm = math.sqrt(math.fsum(float(value * value) for value in accumulator))
    if norm == 0.0:
        return _ZERO_VECTOR
    return ConversationRagEmbeddingVector(
        tuple(float(value) / norm if value else 0.0 for value in accumulator)
    )


class LocalConversationRagEmbedder:
    """Embed bounded text with the fixed dependency-free local model."""

    __slots__ = ()

    @property
    def identity(self) -> ConversationRagEmbeddingIdentity:
        """Return the immutable local model identity."""

        return _IDENTITY

    def embed(
        self,
        texts: tuple[str, ...],
        /,
    ) -> tuple[ConversationRagEmbeddingVector, ...]:
        """Return one deterministic same-order vector per bounded text."""

        if type(texts) is not tuple:
            raise TypeError("local embedding input must be a tuple")
        if len(texts) > CONVERSATION_RAG_POLICY.max_embedding_batch_size:
            raise ValueError("local embedding input exceeds the batch budget")
        return tuple(_embed_one(text) for text in texts)


def _source_key(source: ConversationRagSourceReference) -> tuple[object, ...]:
    document = source.document
    return (
        document.key.session_id,
        document.key.document_id,
        document.revision_id,
        document.conversation_id,
        document.start_sequence,
        document.end_sequence,
        document.start_turn_id,
        document.end_turn_id,
        source.fragment_id,
        source.start_codepoint,
        source.end_codepoint,
    )


def _validated_vector(
    label: str,
    value: object,
) -> ConversationRagEmbeddingVector:
    if type(value) is not ConversationRagEmbeddingVector:
        raise TypeError(f"{label} must use the exact embedding vector")
    assert isinstance(value, ConversationRagEmbeddingVector)
    if len(value.values) != LOCAL_CONVERSATION_RAG_DIMENSIONS:
        raise ValueError(f"{label} has incompatible dimensions")
    return value


class LocalConversationRagVectorRanker:
    """Rank one bounded stream by deterministic positive cosine similarity."""

    __slots__ = ()

    def rank(
        self,
        request: ConversationRagRetrievalRequest,
        embedding: ConversationRagEmbeddingVector,
        records: Iterable[ConversationRagVectorRecord],
        /,
    ) -> tuple[ConversationRagProviderCandidate, ...]:
        """Return best-first unique candidates without exposing raw scores."""

        if type(request) is not ConversationRagRetrievalRequest:
            raise TypeError("local rank request must use the exact RAG request")
        query = _validated_vector("local rank query", embedding)

        first_by_source: dict[
            ConversationRagSourceReference,
            ConversationRagVectorRecord,
        ] = {}
        scored: list[
            tuple[float, tuple[object, ...], ConversationRagProviderCandidate]
        ] = []
        count = 0
        try:
            iterator = iter(records)
        except TypeError:
            raise TypeError("local rank records must be iterable") from None

        for record in iterator:
            count += 1
            if count > 65_536:
                raise ValueError("local rank input exceeds the record budget")
            if type(record) is not ConversationRagVectorRecord:
                raise TypeError("local rank records must use exact vector records")
            vector = _validated_vector("local rank record", record.embedding)
            if record.candidate.source.document.key.session_id != request.session_id:
                raise ValueError("local rank record belongs to another session")

            source = record.candidate.source
            previous = first_by_source.get(source)
            if previous is not None:
                if previous != record:
                    raise ValueError("local rank input has conflicting duplicates")
                continue
            first_by_source[source] = record

            score = math.fsum(
                query_coordinate * record_coordinate
                for query_coordinate, record_coordinate in zip(
                    query.values,
                    vector.values,
                    strict=True,
                )
            )
            if not math.isfinite(score):
                raise ValueError("local rank score is not finite")
            if score > 0.0:
                scored.append((score, _source_key(source), record.candidate))

        scored.sort(key=lambda item: (-item[0], item[1]))
        return tuple(item[2] for item in scored[: request.hit_limit])


__all__ = [
    "LOCAL_CONVERSATION_RAG_DIMENSIONS",
    "LOCAL_CONVERSATION_RAG_MODEL_ID",
    "LOCAL_CONVERSATION_RAG_PROVIDER_ID",
    "LocalConversationRagEmbedder",
    "LocalConversationRagVectorRanker",
]
