"""Persisted conversation RAG generation values, formats, and errors."""

from __future__ import annotations

import errno
import hashlib
import json
import math
import re
from collections.abc import Generator
from dataclasses import dataclass
from enum import StrEnum
from typing import NoReturn

from .conversation_rag import (
    CONVERSATION_RAG_POLICY,
    ConversationRagDocumentIdentity,
    ConversationRagDocumentKey,
    ConversationRagEmbeddingIdentity,
    ConversationRagEmbeddingVector,
    ConversationRagError,
    ConversationRagProjectionCheckpoint,
    ConversationRagProjectionCheckpointEntry,
    ConversationRagProjectionRequest,
    ConversationRagProjectionState,
    ConversationRagProjectionStateAvailability,
    ConversationRagProviderCandidate,
    ConversationRagSourceReference,
    ConversationRagVectorRecord,
)


CONVERSATION_RAG_STORE_SCHEMA_ID = "story-conversation-rag-store-v1"


STORE_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


GENERATION_NAME_PATTERN = re.compile(r"gen-[0-9a-f]{64}")


SHA256_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")


MAX_GENERATION_RECORDS = 65_536


MAX_GENERATION_SOURCE_UTF8_BYTES = 32 * 1024 * 1024


MAX_GENERATION_BYTES = 512 * 1024 * 1024


_SOURCE_DIGEST_DOMAIN = "generic-rag:projection-source:v1"


_FRAGMENT_ID_DOMAIN = "generic-rag:fragment-id:v1"


_CHECKPOINT_DOMAIN = "generic-rag:projection-checkpoint:v1"


_GENERATION_DIGEST_DOMAIN = "story-conversation-rag-generation-v1"


class ConversationRagStoreStatus(StrEnum):
    """Content-free observed state of one disposable session projection."""

    MISSING = "missing"
    READY = "ready"
    STALE = "stale"
    CORRUPT = "corrupt"
    INCOMPATIBLE = "incompatible"
    PROVIDER_FAILED = "provider_failed"


class ConversationRagStoreFailure(StrEnum):
    """Fixed failure categories for generation-store operations."""

    UNSUPPORTED = "unsupported"
    BUSY = "busy"
    UNSAFE = "unsafe"
    CORRUPT = "corrupt"
    INCOMPATIBLE = "incompatible"
    PROVIDER_FAILED = "provider_failed"
    CONFIRMATION_REQUIRED = "confirmation_required"
    OPERATION_FAILED = "operation_failed"


_FAILURE_MESSAGES = {
    ConversationRagStoreFailure.UNSUPPORTED: (
        "conversation RAG storage is unsupported on this filesystem"
    ),
    ConversationRagStoreFailure.BUSY: "conversation RAG storage is busy",
    ConversationRagStoreFailure.UNSAFE: "conversation RAG storage is unsafe",
    ConversationRagStoreFailure.CORRUPT: "conversation RAG storage is corrupt",
    ConversationRagStoreFailure.INCOMPATIBLE: (
        "conversation RAG storage is incompatible"
    ),
    ConversationRagStoreFailure.PROVIDER_FAILED: (
        "conversation RAG provider operation failed"
    ),
    ConversationRagStoreFailure.CONFIRMATION_REQUIRED: (
        "conversation RAG purge requires explicit confirmation"
    ),
    ConversationRagStoreFailure.OPERATION_FAILED: (
        "conversation RAG storage operation failed"
    ),
}


def _exact_nonnegative_integer(label: str, value: object) -> int:
    if type(value) is not int:
        raise TypeError(f"{label} must be an integer")
    assert isinstance(value, int)
    if value < 0:
        raise ValueError(f"{label} must be nonnegative")
    return value


def validate_store_identifier(label: str, value: object) -> str:
    if type(value) is not str:
        raise TypeError(f"{label} must be a string")
    assert isinstance(value, str)
    if STORE_IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def validate_expected_projection_schema_id(value: object) -> str:
    if type(value) is not str:
        raise TypeError("expected projection schema ID must be a string")
    assert isinstance(value, str)
    if not value or value.isspace():
        raise ValueError(
            "expected projection schema ID must not be empty or whitespace-only"
        )
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("expected projection schema ID must be valid UTF-8") from exc
    return value


@dataclass(frozen=True, slots=True)
class ConversationRagStoreCounts:
    """Safe bounded counts for one inspected derived-state subtree."""

    conversations: int = 0
    events: int = 0
    documents: int = 0
    fragments: int = 0
    source_map_records: int = 0
    vector_records: int = 0
    generations: int = 0
    orphan_generations: int = 0
    source_utf8_bytes: int = 0
    payload_bytes: int = 0
    generation_bytes: int = 0

    def __post_init__(self) -> None:
        for label, value in (
            ("conversation count", self.conversations),
            ("event count", self.events),
            ("document count", self.documents),
            ("fragment count", self.fragments),
            ("source-map record count", self.source_map_records),
            ("vector record count", self.vector_records),
            ("generation count", self.generations),
            ("orphan generation count", self.orphan_generations),
            ("source UTF-8 byte count", self.source_utf8_bytes),
            ("payload byte count", self.payload_bytes),
            ("generation byte count", self.generation_bytes),
        ):
            _exact_nonnegative_integer(label, value)
        if self.conversations > CONVERSATION_RAG_POLICY.max_conversations:
            raise ValueError("conversation count exceeds the RAG budget")
        if self.events > CONVERSATION_RAG_POLICY.max_events:
            raise ValueError("event count exceeds the RAG budget")
        if self.documents > CONVERSATION_RAG_POLICY.max_events:
            raise ValueError("document count exceeds the RAG budget")
        if self.fragments > MAX_GENERATION_RECORDS:
            raise ValueError("fragment count exceeds the RAG budget")
        if (
            self.source_map_records != self.fragments
            or self.vector_records != self.fragments
        ):
            raise ValueError("stored record counts must match fragment count")
        if self.source_utf8_bytes > MAX_GENERATION_SOURCE_UTF8_BYTES:
            raise ValueError("source byte count exceeds the RAG budget")
        if self.payload_bytes > MAX_GENERATION_BYTES:
            raise ValueError("payload byte count exceeds the RAG budget")
        if self.generation_bytes > MAX_GENERATION_BYTES:
            raise ValueError("generation byte count exceeds the RAG budget")


@dataclass(frozen=True, slots=True)
class ConversationRagStoreSnapshot:
    """One content-free active-generation inspection result."""

    session_id: str
    status: ConversationRagStoreStatus
    state: ConversationRagProjectionState
    provider_id: str | None
    generation_id: str | None
    counts: ConversationRagStoreCounts

    def __post_init__(self) -> None:
        validate_store_identifier("session ID", self.session_id)
        if type(self.status) is not ConversationRagStoreStatus:
            raise TypeError("store status must use the exact enum")
        if type(self.state) is not ConversationRagProjectionState:
            raise TypeError("store state must use the exact projection state")
        if self.provider_id is not None:
            validate_store_identifier("RAG provider ID", self.provider_id)
        if self.generation_id is not None and (
            type(self.generation_id) is not str
            or GENERATION_NAME_PATTERN.fullmatch(self.generation_id) is None
        ):
            raise ValueError("generation ID is invalid")
        if type(self.counts) is not ConversationRagStoreCounts:
            raise TypeError("store counts must use the exact value")
        if (
            self.state.availability
            is ConversationRagProjectionStateAvailability.PRESENT
        ):
            if self.provider_id is None or self.generation_id is None:
                raise ValueError("present store state requires active identities")
        elif self.generation_id is not None:
            raise ValueError("non-present store state cannot name a generation")


class ConversationRagStoreError(ConversationRagError):
    """Sanitized actionable failure from the disposable generation store."""

    failure: ConversationRagStoreFailure

    def __init__(self, failure: ConversationRagStoreFailure) -> None:
        if type(failure) is not ConversationRagStoreFailure:
            raise TypeError("store failure must use the exact enum")
        self.failure = failure
        Exception.__init__(self, _FAILURE_MESSAGES[failure])


class ConversationRagStoreFault(ConversationRagStoreError):
    __slots__ = ()

    def __init__(self, failure: ConversationRagStoreFailure) -> None:
        ConversationRagStoreError.__init__(self, failure)


def raise_store_fault(failure: ConversationRagStoreFailure) -> NoReturn:
    raise ConversationRagStoreFault(failure)


def raise_store_error(failure: ConversationRagStoreFailure) -> NoReturn:
    raise ConversationRagStoreError(failure)


def classify_store_os_failure(value: OSError) -> ConversationRagStoreFailure:
    if value.errno in {
        getattr(errno, "ENOTSUP", errno.ENOSYS),
        errno.ENOSYS,
    }:
        return ConversationRagStoreFailure.UNSUPPORTED
    if value.errno in {
        errno.EACCES,
        errno.EAGAIN,
        getattr(errno, "EWOULDBLOCK", errno.EAGAIN),
        getattr(errno, "EDEADLK", errno.EAGAIN),
    }:
        return ConversationRagStoreFailure.BUSY
    if value.errno in {
        errno.ELOOP,
        errno.ENOTDIR,
        errno.EPERM,
    }:
        return ConversationRagStoreFailure.UNSAFE
    return ConversationRagStoreFailure.OPERATION_FAILED


def encode_generation_json(
    value: object,
    *,
    failure: ConversationRagStoreFailure = (
        ConversationRagStoreFailure.OPERATION_FAILED
    ),
) -> bytes:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return (rendered + "\n").encode("utf-8", errors="strict")
    except (
        TypeError,
        ValueError,
        UnicodeEncodeError,
        OverflowError,
        RecursionError,
    ):
        raise_store_fault(failure)
    raise AssertionError("unreachable")


def _reject_duplicate_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> NoReturn:
    raise ValueError("non-finite JSON number")


def parse_generation_json(raw: bytes) -> object:
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_fields,
            parse_constant=_reject_json_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        OverflowError,
        RecursionError,
    ):
        raise_store_fault(ConversationRagStoreFailure.CORRUPT)
    if encode_generation_json(value, failure=ConversationRagStoreFailure.CORRUPT) != raw:
        raise_store_fault(ConversationRagStoreFailure.CORRUPT)
    return value


def require_generation_fields(value: object, fields: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict or frozenset(value) != fields:
        raise_store_fault(ConversationRagStoreFailure.CORRUPT)
    assert isinstance(value, dict)
    return value


def require_generation_string(value: object) -> str:
    if type(value) is not str:
        raise_store_fault(ConversationRagStoreFailure.CORRUPT)
    assert isinstance(value, str)
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise_store_fault(ConversationRagStoreFailure.CORRUPT)
    return value


def require_generation_integer(value: object) -> int:
    if type(value) is not int:
        raise_store_fault(ConversationRagStoreFailure.CORRUPT)
    assert isinstance(value, int)
    return value


def _document_value(value: ConversationRagDocumentIdentity) -> dict[str, object]:
    return {
        "conversation_id": value.conversation_id,
        "document_id": value.key.document_id,
        "end_sequence": value.end_sequence,
        "end_turn_id": value.end_turn_id,
        "revision_id": value.revision_id,
        "session_id": value.key.session_id,
        "start_sequence": value.start_sequence,
        "start_turn_id": value.start_turn_id,
    }


_DOCUMENT_FIELDS = frozenset(
    {
        "session_id",
        "document_id",
        "conversation_id",
        "start_turn_id",
        "end_turn_id",
        "start_sequence",
        "end_sequence",
        "revision_id",
    }
)


def _document_from(value: object) -> ConversationRagDocumentIdentity:
    row = require_generation_fields(value, _DOCUMENT_FIELDS)
    try:
        return ConversationRagDocumentIdentity(
            ConversationRagDocumentKey(
                require_generation_string(row["session_id"]),
                require_generation_string(row["document_id"]),
            ),
            require_generation_string(row["conversation_id"]),
            require_generation_string(row["start_turn_id"]),
            require_generation_string(row["end_turn_id"]),
            require_generation_integer(row["start_sequence"]),
            require_generation_integer(row["end_sequence"]),
            require_generation_string(row["revision_id"]),
        )
    except (TypeError, ValueError):
        raise_store_fault(ConversationRagStoreFailure.CORRUPT)
    raise AssertionError("unreachable")


def _sha256_fields(fields: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for field in fields:
        encoded = field.encode("utf-8", errors="strict")
        digest.update(len(encoded).to_bytes(8, "big", signed=False))
        digest.update(encoded)
    return f"sha256:{digest.hexdigest()}"


def _source_digest(text: str) -> str:
    return _sha256_fields(
        (
            _SOURCE_DIGEST_DOMAIN,
            "text",
            text,
            "attributes_count",
            "0",
        )
    )


def generation_fragment_id(
    document: ConversationRagDocumentIdentity,
    start: int,
    end: int,
) -> str:
    return _sha256_fields(
        (
            _FRAGMENT_ID_DOMAIN,
            "corpus_id",
            document.key.session_id,
            "document_id",
            document.key.document_id,
            "revision_id",
            document.revision_id,
            "start",
            str(start),
            "end",
            str(end),
        )
    )


def _checkpoint_token(
    session_id: str,
    schema_id: str,
    embedding: ConversationRagEmbeddingIdentity,
    entries: tuple[ConversationRagProjectionCheckpointEntry, ...],
) -> str:
    fields = [
        _CHECKPOINT_DOMAIN,
        "corpus_id",
        session_id,
        "schema_id",
        schema_id,
        "embedding_model_id",
        embedding.model_id,
        "embedding_dimensions",
        str(embedding.dimensions),
        "max_fragment_codepoints",
        str(CONVERSATION_RAG_POLICY.max_fragment_codepoints),
        "overlap_codepoints",
        str(CONVERSATION_RAG_POLICY.fragment_overlap_codepoints),
        "entry_count",
        str(len(entries)),
    ]
    for entry in entries:
        fields.extend(
            (
                "document_id",
                entry.document.key.document_id,
                "revision_id",
                entry.document.revision_id,
                "source_digest",
                entry.source_digest,
                "fragment_count",
                str(entry.fragment_count),
            )
        )
    return _sha256_fields(tuple(fields))


def generation_digest(
    manifest_digest: str,
    source_map_digest: str,
    vectors_digest: str,
) -> str:
    for value in (manifest_digest, source_map_digest, vectors_digest):
        if SHA256_DIGEST_PATTERN.fullmatch(value) is None:
            raise_store_fault(ConversationRagStoreFailure.CORRUPT)
    return _sha256_fields(
        (
            _GENERATION_DIGEST_DOMAIN,
            "generic_manifest_digest",
            manifest_digest,
            "source_map_digest",
            source_map_digest,
            "vectors_digest",
            vectors_digest,
        )
    )


def generation_fragment_count(text: str) -> int:
    if not text:
        return 0
    width = CONVERSATION_RAG_POLICY.max_fragment_codepoints
    step = width - CONVERSATION_RAG_POLICY.fragment_overlap_codepoints
    if len(text) <= width:
        return 1
    return 1 + math.ceil((len(text) - width) / step)


def target_generation_checkpoint(
    request: ConversationRagProjectionRequest,
) -> ConversationRagProjectionCheckpoint:
    entries = tuple(
        ConversationRagProjectionCheckpointEntry(
            document.identity,
            _source_digest(document.text),
            generation_fragment_count(document.text),
        )
        for document in request.documents
    )
    token = _checkpoint_token(
        request.session_id,
        request.schema_id,
        request.embedding,
        entries,
    )
    return ConversationRagProjectionCheckpoint(
        request.session_id,
        request.schema_id,
        request.embedding,
        CONVERSATION_RAG_POLICY.max_fragment_codepoints,
        CONVERSATION_RAG_POLICY.fragment_overlap_codepoints,
        entries,
        token,
    )


def generation_manifest_value(
    checkpoint: ConversationRagProjectionCheckpoint,
) -> dict[str, object]:
    return {
        "checkpoint_token": checkpoint.token,
        "chunking": {
            "max_fragment_codepoints": checkpoint.max_fragment_codepoints,
            "overlap_codepoints": checkpoint.fragment_overlap_codepoints,
        },
        "embedding": {
            "dimensions": checkpoint.embedding.dimensions,
            "model_id": checkpoint.embedding.model_id,
        },
        "entries": [
            {
                "document": _document_value(entry.document),
                "fragment_count": entry.fragment_count,
                "source_digest": entry.source_digest,
            }
            for entry in checkpoint.entries
        ],
        "projection_schema_id": checkpoint.schema_id,
        "schema_id": CONVERSATION_RAG_STORE_SCHEMA_ID,
        "session_id": checkpoint.session_id,
    }


_MANIFEST_FIELDS = frozenset(
    {
        "schema_id",
        "session_id",
        "projection_schema_id",
        "embedding",
        "chunking",
        "entries",
        "checkpoint_token",
    }
)


def parse_generation_manifest(value: object) -> ConversationRagProjectionCheckpoint:
    row = require_generation_fields(value, _MANIFEST_FIELDS)
    if require_generation_string(row["schema_id"]) != CONVERSATION_RAG_STORE_SCHEMA_ID:
        raise_store_fault(ConversationRagStoreFailure.INCOMPATIBLE)
    embedding_row = require_generation_fields(
        row["embedding"],
        frozenset({"model_id", "dimensions"}),
    )
    chunking = require_generation_fields(
        row["chunking"],
        frozenset({"max_fragment_codepoints", "overlap_codepoints"}),
    )
    entries_value = row["entries"]
    if type(entries_value) is not list:
        raise_store_fault(ConversationRagStoreFailure.CORRUPT)
    assert isinstance(entries_value, list)
    entries: list[ConversationRagProjectionCheckpointEntry] = []
    try:
        embedding = ConversationRagEmbeddingIdentity(
            require_generation_string(embedding_row["model_id"]),
            require_generation_integer(embedding_row["dimensions"]),
        )
        for item in entries_value:
            entry = require_generation_fields(
                item,
                frozenset({"document", "source_digest", "fragment_count"}),
            )
            entries.append(
                ConversationRagProjectionCheckpointEntry(
                    _document_from(entry["document"]),
                    require_generation_string(entry["source_digest"]),
                    require_generation_integer(entry["fragment_count"]),
                )
            )
        checkpoint = ConversationRagProjectionCheckpoint(
            require_generation_string(row["session_id"]),
            require_generation_string(row["projection_schema_id"]),
            embedding,
            require_generation_integer(chunking["max_fragment_codepoints"]),
            require_generation_integer(chunking["overlap_codepoints"]),
            tuple(entries),
            require_generation_string(row["checkpoint_token"]),
        )
    except (TypeError, ValueError):
        raise_store_fault(ConversationRagStoreFailure.CORRUPT)
    if checkpoint.token != _checkpoint_token(
        checkpoint.session_id,
        checkpoint.schema_id,
        checkpoint.embedding,
        checkpoint.entries,
    ):
        raise_store_fault(ConversationRagStoreFailure.CORRUPT)
    return checkpoint


_SOURCE_ROW_FIELDS = frozenset(
    {"document", "fragment_id", "start_codepoint", "end_codepoint", "text"}
)


_VECTOR_ROW_FIELDS = frozenset({"document_id", "revision_id", "fragment_id", "values"})


def generation_record_values(
    record: ConversationRagVectorRecord,
) -> tuple[dict[str, object], dict[str, object]]:
    source = record.candidate.source
    source_value: dict[str, object] = {
        "document": _document_value(source.document),
        "end_codepoint": source.end_codepoint,
        "fragment_id": source.fragment_id,
        "start_codepoint": source.start_codepoint,
        "text": record.candidate.text,
    }
    vector_value: dict[str, object] = {
        "document_id": source.document.key.document_id,
        "fragment_id": source.fragment_id,
        "revision_id": source.document.revision_id,
        "values": [float.hex(value) for value in record.embedding.values],
    }
    return source_value, vector_value


def parse_generation_record(
    source_value: object,
    vector_value: object,
) -> ConversationRagVectorRecord:
    source_row = require_generation_fields(source_value, _SOURCE_ROW_FIELDS)
    vector_row = require_generation_fields(vector_value, _VECTOR_ROW_FIELDS)
    document = _document_from(source_row["document"])
    fragment_id = require_generation_string(source_row["fragment_id"])
    start = require_generation_integer(source_row["start_codepoint"])
    end = require_generation_integer(source_row["end_codepoint"])
    if (
        require_generation_string(vector_row["document_id"]) != document.key.document_id
        or require_generation_string(vector_row["revision_id"]) != document.revision_id
        or require_generation_string(vector_row["fragment_id"]) != fragment_id
    ):
        raise_store_fault(ConversationRagStoreFailure.CORRUPT)
    values = vector_row["values"]
    if type(values) is not list:
        raise_store_fault(ConversationRagStoreFailure.CORRUPT)
    assert isinstance(values, list)
    coordinates: list[float] = []
    for encoded in values:
        text = require_generation_string(encoded)
        try:
            coordinate = float.fromhex(text)
        except (ValueError, OverflowError):
            raise_store_fault(ConversationRagStoreFailure.CORRUPT)
        if not math.isfinite(coordinate) or float.hex(coordinate) != text:
            raise_store_fault(ConversationRagStoreFailure.CORRUPT)
        if coordinate == 0.0 and math.copysign(1.0, coordinate) < 0.0:
            raise_store_fault(ConversationRagStoreFailure.CORRUPT)
        coordinates.append(coordinate)
    try:
        source = ConversationRagSourceReference(
            document,
            fragment_id,
            start,
            end,
        )
        return ConversationRagVectorRecord(
            ConversationRagProviderCandidate(
                source,
                require_generation_string(source_row["text"]),
            ),
            ConversationRagEmbeddingVector(tuple(coordinates)),
        )
    except (TypeError, ValueError):
        raise_store_fault(ConversationRagStoreFailure.CORRUPT)
    raise AssertionError("unreachable")


def validate_generation_record_stream(
    iterator: Generator[ConversationRagVectorRecord, None, None],
    checkpoint: ConversationRagProjectionCheckpoint,
) -> tuple[int, int]:
    current = next(iterator, None)
    fragment_total = 0
    source_bytes = 0
    for entry in checkpoint.entries:
        document = entry.document
        assembled = ""
        prior_end = 0
        prior_width = 0
        count = 0
        while (
            current is not None
            and current.candidate.source.document.key == document.key
        ):
            source = current.candidate.source
            if source.document != document:
                raise_store_fault(ConversationRagStoreFailure.CORRUPT)
            if len(current.embedding.values) != checkpoint.embedding.dimensions:
                raise_store_fault(ConversationRagStoreFailure.CORRUPT)
            width = source.end_codepoint - source.start_codepoint
            if width > CONVERSATION_RAG_POLICY.max_fragment_codepoints:
                raise_store_fault(ConversationRagStoreFailure.CORRUPT)
            if source.fragment_id != generation_fragment_id(
                document, source.start_codepoint, source.end_codepoint
            ):
                raise_store_fault(ConversationRagStoreFailure.CORRUPT)
            if count == 0:
                if source.start_codepoint != 0:
                    raise_store_fault(ConversationRagStoreFailure.CORRUPT)
                assembled = current.candidate.text
            else:
                if prior_width != CONVERSATION_RAG_POLICY.max_fragment_codepoints:
                    raise_store_fault(ConversationRagStoreFailure.CORRUPT)
                expected_start = (
                    prior_end - CONVERSATION_RAG_POLICY.fragment_overlap_codepoints
                )
                if source.start_codepoint != expected_start:
                    raise_store_fault(ConversationRagStoreFailure.CORRUPT)
                overlap = prior_end - source.start_codepoint
                if (
                    assembled[source.start_codepoint : prior_end]
                    != current.candidate.text[:overlap]
                ):
                    raise_store_fault(ConversationRagStoreFailure.CORRUPT)
                assembled += current.candidate.text[overlap:]
            prior_end = source.end_codepoint
            prior_width = width
            count += 1
            fragment_total += 1
            if fragment_total > MAX_GENERATION_RECORDS:
                raise_store_fault(ConversationRagStoreFailure.CORRUPT)
            current = next(iterator, None)
        if (
            count != entry.fragment_count
            or _source_digest(assembled) != entry.source_digest
        ):
            raise_store_fault(ConversationRagStoreFailure.CORRUPT)
        source_bytes += len(assembled.encode("utf-8", errors="strict"))
        if source_bytes > MAX_GENERATION_SOURCE_UTF8_BYTES:
            raise_store_fault(ConversationRagStoreFailure.CORRUPT)
    if current is not None:
        raise_store_fault(ConversationRagStoreFailure.CORRUPT)
    return fragment_total, source_bytes


__all__ = [
    "CONVERSATION_RAG_STORE_SCHEMA_ID",
    "ConversationRagStoreCounts",
    "ConversationRagStoreError",
    "ConversationRagStoreFailure",
    "ConversationRagStoreFault",
    "ConversationRagStoreSnapshot",
    "ConversationRagStoreStatus",
    "GENERATION_NAME_PATTERN",
    "MAX_GENERATION_BYTES",
    "MAX_GENERATION_RECORDS",
    "MAX_GENERATION_SOURCE_UTF8_BYTES",
    "SHA256_DIGEST_PATTERN",
    "STORE_IDENTIFIER_PATTERN",
    "classify_store_os_failure",
    "encode_generation_json",
    "generation_digest",
    "generation_fragment_count",
    "generation_fragment_id",
    "generation_manifest_value",
    "generation_record_values",
    "parse_generation_json",
    "parse_generation_manifest",
    "parse_generation_record",
    "raise_store_error",
    "raise_store_fault",
    "require_generation_fields",
    "require_generation_integer",
    "require_generation_string",
    "target_generation_checkpoint",
    "validate_expected_projection_schema_id",
    "validate_generation_record_stream",
    "validate_store_identifier",
]
