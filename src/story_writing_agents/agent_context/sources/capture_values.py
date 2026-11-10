"""Capture values."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from ..actions import CONTEXT_ACTION_POLICY, ContextSourceBoundary, ContextSourceBoundaryKind
from ..manifest import (
    ContextAuthorityKind,
    ContextManifestError,
    ContextProvenance,
    ContextScope,
    ContextSourceKind,
    validate_context_digest,
    validate_context_identity,
)
from ...conversations import (
    COLLABORATION_EVENT_SCHEMA_VERSION,
    CollaborationReadableField,
    CollaborationReadableFieldKey,
)
from ...writing_artifacts.filesystem_sources import (
    MAX_ARTIFACT_SOURCE_INVENTORY,
    MAX_ARTIFACT_SOURCE_TRAVERSAL_ENTRIES,
)
from ...writing_artifacts.references import reference_source_kind


_MEBIBYTE = 1024 * 1024

_SOURCE_KIND_ORDER = (
    ContextSourceKind.WRITING_ARTIFACT,
    ContextSourceKind.CONVERSATION,
    ContextSourceKind.ACTIVE_DEBATE,
    ContextSourceKind.OWN_AGENT_STATE,
)

class ContextSourceErrorCode(StrEnum):
    """Stable content-free failures from authorized context sources."""

    INVALID_VALUE = "invalid_value"
    SCOPE_MISMATCH = "scope_mismatch"
    AUTHORITY_MISMATCH = "authority_mismatch"
    INVENTORY_LIMIT_EXCEEDED = "inventory_limit_exceeded"
    SOURCE_LIMIT_EXCEEDED = "source_limit_exceeded"
    SOURCE_MISSING = "source_missing"
    SOURCE_UNSAFE = "source_unsafe"
    SOURCE_INVALID_UTF8 = "source_invalid_utf8"
    SOURCE_NUL = "source_nul"
    SOURCE_DRIFT = "source_drift"
    SOURCE_UNAVAILABLE = "source_unavailable"
    ADAPTER_CONTRACT_VIOLATION = "adapter_contract_violation"

_SOURCE_ERROR_MESSAGES = {
    ContextSourceErrorCode.INVALID_VALUE: "context source value is invalid",
    ContextSourceErrorCode.SCOPE_MISMATCH: "context source scope does not match",
    ContextSourceErrorCode.AUTHORITY_MISMATCH: (
        "context source authority does not match"
    ),
    ContextSourceErrorCode.INVENTORY_LIMIT_EXCEEDED: (
        "context source inventory exceeds its limit"
    ),
    ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED: ("context source exceeds its limit"),
    ContextSourceErrorCode.SOURCE_MISSING: "context source is missing",
    ContextSourceErrorCode.SOURCE_UNSAFE: "context source is unsafe",
    ContextSourceErrorCode.SOURCE_INVALID_UTF8: ("context source is not valid UTF-8"),
    ContextSourceErrorCode.SOURCE_NUL: "context source contains NUL",
    ContextSourceErrorCode.SOURCE_DRIFT: "context source changed during use",
    ContextSourceErrorCode.SOURCE_UNAVAILABLE: "context source is unavailable",
    ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION: (
        "context source adapter returned an invalid value"
    ),
}

class ContextSourceError(ValueError):
    """Report one fixed content-free source failure."""

    def __init__(self, code: ContextSourceErrorCode) -> None:
        if type(code) is not ContextSourceErrorCode:
            raise TypeError("source error code must be a ContextSourceErrorCode")
        self.code = code
        super().__init__(_SOURCE_ERROR_MESSAGES[code])

def source_error(code: ContextSourceErrorCode) -> ContextSourceError:
    return ContextSourceError(code)

def _positive(value: object) -> int:
    if type(value) is not int or value < 1:
        raise source_error(ContextSourceErrorCode.INVALID_VALUE)
    assert isinstance(value, int)
    return value

def source_nonnegative(value: object) -> int:
    if type(value) is not int or value < 0:
        raise source_error(ContextSourceErrorCode.INVALID_VALUE)
    assert isinstance(value, int)
    return value

def require_source_policy(value: object) -> ContextSourcePolicy:
    """Return one exact source policy or fail with fixed content-free text."""

    if type(value) is not ContextSourcePolicy:
        raise source_error(ContextSourceErrorCode.INVALID_VALUE)
    assert isinstance(value, ContextSourcePolicy)
    return value

def source_safe_identity(value: object) -> str:
    validated: str | None = None
    try:
        validated = validate_context_identity(value)
    except ContextManifestError:
        pass
    if validated is None:
        raise source_error(ContextSourceErrorCode.INVALID_VALUE)
    return validated

def source_safe_digest(value: object) -> str:
    validated: str | None = None
    try:
        validated = validate_context_digest(value)
    except ContextManifestError:
        pass
    if validated is None:
        raise source_error(ContextSourceErrorCode.INVALID_VALUE)
    return validated

def _strict_text(value: object, *, allow_empty: bool = False) -> str:
    if type(value) is not str or (not value and not allow_empty):
        raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
    assert isinstance(value, str)
    valid = True
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        valid = False
    if not valid:
        raise source_error(ContextSourceErrorCode.SOURCE_INVALID_UTF8)
    if "\x00" in value:
        raise source_error(ContextSourceErrorCode.SOURCE_NUL)
    return value

def source_canonical_json_bytes(value: object) -> bytes:
    if _contains_nul(value):
        raise source_error(ContextSourceErrorCode.SOURCE_NUL)
    serialized: str | None = None
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        encoded = serialized.encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError):
        serialized = None
        encoded = b""
    if serialized is None:
        raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
    if b"\x00" in encoded:
        raise source_error(ContextSourceErrorCode.SOURCE_NUL)
    return encoded

def _contains_nul(value: object) -> bool:
    if type(value) is str:
        return "\x00" in value
    if type(value) in {tuple, list}:
        return any(_contains_nul(item) for item in value)
    if type(value) is dict:
        return any(
            _contains_nul(key) or _contains_nul(item) for key, item in value.items()
        )
    return False

def source_digest(body: bytes) -> str:
    return f"sha256:{hashlib.sha256(body).hexdigest()}"

def source_identity_digest(value: object) -> str:
    return source_digest(source_canonical_json_bytes(value))

def source_structured_source_id(prefix: str, identity: str) -> str:
    return f"{prefix}:{identity}"

def writing_artifact_source_id(path: str) -> str:
    """Return the canonical content-free source identity for an artifact path."""

    if not artifact_locator_is_lexically_safe(path):
        raise source_error(ContextSourceErrorCode.SOURCE_UNSAFE)
    digest = hashlib.sha256(path.encode("utf-8", errors="strict")).hexdigest()
    return f"writing_artifact:sha256:{digest}"

def artifact_locator_is_lexically_safe(value: object) -> bool:
    if type(value) is not str or not value or "\\" in value or "\x00" in value:
        return False
    assert isinstance(value, str)
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return False
    if (len(value) >= 3 and value[0].isalpha() and value[1:3] == ":/") or any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs"}
        or character in {"\u2028", "\u2029"}
        for character in value
    ):
        return False
    path = PurePosixPath(value)
    return bool(
        len(encoded) <= 1_024
        and not path.is_absolute()
        and path.as_posix() == value
        and all(part not in {"", ".", ".."} for part in path.parts)
    )

@dataclass(frozen=True, slots=True)
class ContextSourcePolicy:
    """Hard upper bounds for inventory, capture, and literal source scans."""

    max_inventory_sources: int = 4_096
    max_traversal_entries: int = 8_192
    max_records_per_source: int = 10_000
    max_source_utf8_bytes: int = 8 * _MEBIBYTE
    max_inventory_body_utf8_bytes: int = 64 * _MEBIBYTE
    max_source_scan_utf8_bytes: int = 8 * _MEBIBYTE
    max_cumulative_scan_utf8_bytes: int = 32 * _MEBIBYTE

    def __post_init__(self) -> None:
        for name in self.__slots__:
            _positive(getattr(self, name))
        if self.max_inventory_sources > MAX_ARTIFACT_SOURCE_INVENTORY:
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        if self.max_traversal_entries > MAX_ARTIFACT_SOURCE_TRAVERSAL_ENTRIES:
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        if self.max_source_utf8_bytes > 8 * _MEBIBYTE:
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        if self.max_records_per_source > 10_000:
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        if self.max_inventory_body_utf8_bytes > 64 * _MEBIBYTE:
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        if self.max_cumulative_scan_utf8_bytes > 32 * _MEBIBYTE:
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        if self.max_source_scan_utf8_bytes > self.max_source_utf8_bytes:
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        if self.max_cumulative_scan_utf8_bytes < self.max_source_scan_utf8_bytes:
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        if self.max_inventory_body_utf8_bytes < self.max_source_utf8_bytes:
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)

CONTEXT_SOURCE_POLICY = ContextSourcePolicy()

_EXPECTED_AUTHORITY = {
    ContextSourceKind.WRITING_ARTIFACT: (
        ContextAuthorityKind.WRITING_ARTIFACT_REPOSITORY
    ),
    ContextSourceKind.CONVERSATION: ContextAuthorityKind.CONVERSATION_STORAGE,
    ContextSourceKind.ACTIVE_DEBATE: ContextAuthorityKind.ACTIVE_DEBATE,
    ContextSourceKind.OWN_AGENT_STATE: ContextAuthorityKind.OWN_AGENT_STATE,
}

@dataclass(frozen=True, slots=True)
class ContextSourceDescriptor:
    """Content-free identity of one current authorized source snapshot."""

    scope: ContextScope
    source_id: str
    source_kind: ContextSourceKind
    revision: str
    provenance: ContextProvenance
    content_digest: str
    utf8_bytes: int
    record_count: int

    def __post_init__(self) -> None:
        if type(self.scope) is not ContextScope:
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        source_safe_identity(self.source_id)
        if (
            type(self.source_kind) is not ContextSourceKind
            or self.source_kind not in _SOURCE_KIND_ORDER
        ):
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        source_safe_digest(self.revision)
        if type(self.provenance) is not ContextProvenance:
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        if self.provenance.authority_kind is not _EXPECTED_AUTHORITY[self.source_kind]:
            raise source_error(ContextSourceErrorCode.AUTHORITY_MISMATCH)
        source_safe_digest(self.content_digest)
        locator = self.provenance.locator
        if self.source_kind is ContextSourceKind.WRITING_ARTIFACT:
            eligible = False
            try:
                eligible = reference_source_kind(locator) is not None
            except (TypeError, ValueError):
                pass
            exact_identity = self.source_id == writing_artifact_source_id(locator)
        elif self.source_kind is ContextSourceKind.CONVERSATION:
            identity = self.source_id.removeprefix("conversation:")
            eligible = self.source_id == source_structured_source_id(
                "conversation",
                identity,
            )
            exact_identity = locator == f"conversation/{identity}"
        elif self.source_kind is ContextSourceKind.ACTIVE_DEBATE:
            identity = self.source_id.removeprefix("active_debate:")
            eligible = (
                self.scope.subject_kind.value == "debate"
                and self.scope.subject_id == identity
                and self.source_id == source_structured_source_id("active_debate", identity)
            )
            exact_identity = locator == f"debate/{identity}"
        else:
            identity = self.source_id.removeprefix("own_agent_state:")
            eligible = (
                identity == self.scope.agent_id
                and self.source_id == source_structured_source_id("own_agent_state", identity)
            )
            exact_identity = locator == f"agent-state/{identity}"
        if not eligible or not exact_identity:
            raise source_error(ContextSourceErrorCode.AUTHORITY_MISMATCH)
        if (
            self.source_kind is not ContextSourceKind.WRITING_ARTIFACT
            and self.revision != self.content_digest
        ):
            raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
        if source_nonnegative(self.utf8_bytes) > CONTEXT_SOURCE_POLICY.max_source_utf8_bytes:
            raise source_error(ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)
        if source_nonnegative(self.record_count) > (
            CONTEXT_SOURCE_POLICY.max_records_per_source
        ):
            raise source_error(ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)


def source_snapshot_order_key(item):
    return (
        _SOURCE_KIND_ORDER.index(item.source_kind),
        item.source_id.encode("utf-8"),
    )

@dataclass(frozen=True, slots=True)
class ContextSourceRecord:
    """One atomic searchable and readable record within a source snapshot."""

    record_id: str
    boundary: ContextSourceBoundary
    content: str = field(repr=False)
    search_text: str = field(repr=False)
    content_digest: str = ""
    utf8_bytes: int = 0

    def __post_init__(self) -> None:
        source_safe_identity(self.record_id)
        if type(self.boundary) is not ContextSourceBoundary:
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        content = _strict_text(self.content, allow_empty=True)
        _strict_text(self.search_text, allow_empty=True)
        encoded = content.encode("utf-8")
        if self.utf8_bytes != len(encoded) or self.content_digest != source_digest(encoded):
            raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)

@dataclass(frozen=True, slots=True)
class ContextSourceSnapshot:
    """One immutable exact source capture with bounded atomic records."""

    descriptor: ContextSourceDescriptor
    records: tuple[ContextSourceRecord, ...]
    content: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.descriptor) is not ContextSourceDescriptor:
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        if type(self.records) is not tuple or any(
            type(record) is not ContextSourceRecord for record in self.records
        ):
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        if len(self.records) != self.descriptor.record_count:
            raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
        if len({record.record_id for record in self.records}) != len(self.records):
            raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
        if type(self.content) is not bytes:
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        decoded: str | None = None
        try:
            decoded = self.content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            pass
        if decoded is None:
            raise source_error(ContextSourceErrorCode.SOURCE_INVALID_UTF8)
        if b"\x00" in self.content:
            raise source_error(ContextSourceErrorCode.SOURCE_NUL)
        if (
            len(self.content) != self.descriptor.utf8_bytes
            or source_digest(self.content) != self.descriptor.content_digest
        ):
            raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
        if self.descriptor.source_kind is ContextSourceKind.WRITING_ARTIFACT:
            expected = tuple(
                ContextSourceBoundary.artifact_lines(index, index)
                for index in range(1, len(self.records) + 1)
            )
            reconstructed = b"".join(
                record.content.encode("utf-8") for record in self.records
            )
            if (
                self.descriptor.source_id
                != writing_artifact_source_id(self.descriptor.provenance.locator)
                or tuple(record.boundary for record in self.records) != expected
                or any(record.search_text != record.content for record in self.records)
                or reconstructed != self.content
            ):
                raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
            return
        parsed: object | None = None
        try:
            parsed = json.loads(decoded)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        if parsed is None or source_canonical_json_bytes(parsed) != self.content:
            raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
        if self.descriptor.revision != self.descriptor.content_digest:
            raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
        if self.descriptor.source_kind is ContextSourceKind.CONVERSATION:
            if type(parsed) is not dict or set(parsed) != {"bootstrap", "records"}:
                raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
            bootstrap = parsed.get("bootstrap")
            values = parsed.get("records")
            prefix = "conversation/"
            locator = self.descriptor.provenance.locator
            conversation_id = (
                locator[len(prefix) :] if locator.startswith(prefix) else None
            )
            if (
                type(bootstrap) is not dict
                or set(bootstrap)
                != {
                    "conversation_id",
                    "last_sequence",
                    "omitted_records",
                    "requested_records",
                    "retained_records",
                    "retained_utf8_bytes",
                    "schema_version",
                    "session_id",
                    "tail_batch_digest",
                    "total_records",
                    "truncated",
                }
                or bootstrap.get("schema_version") != COLLABORATION_EVENT_SCHEMA_VERSION
                or bootstrap.get("session_id") != self.descriptor.scope.session_id
                or bootstrap.get("conversation_id") != conversation_id
                or type(conversation_id) is not str
                or self.descriptor.source_id
                != source_structured_source_id("conversation", conversation_id)
                or self.descriptor.provenance.locator
                != f"conversation/{conversation_id}"
                or type(values) is not list
                or len(values) != len(self.records)
                or bootstrap.get("retained_records") != len(values)
                or any(
                    type(bootstrap.get(name)) is not int or bootstrap[name] < 0
                    for name in (
                        "total_records", "requested_records", "retained_records",
                        "omitted_records", "retained_utf8_bytes",
                    )
                )
                or bootstrap["requested_records"] > bootstrap["total_records"]
                or bootstrap["requested_records"] != (
                    bootstrap["retained_records"] + bootstrap["omitted_records"]
                )
                or type(bootstrap.get("truncated")) is not bool
                or bootstrap["truncated"] is not (bootstrap["omitted_records"] > 0)
            ):
                raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
            previous_end = 0
            retained_utf8_bytes = 0
            record_keys = {
                "batch_digest",
                "batch_id",
                "batch_ordinal",
                "batch_size",
                "conversation_id",
                "direction",
                "event_id",
                "event_revision",
                "fields",
                "kind",
                "recipient",
                "schema_version",
                "sender",
                "sequence",
                "session_id",
                "step_id",
                "timestamp",
                "turn_id",
            }
            field_keys = {"assignment_ordinal", "input_ordinal", "key", "text"}
            for value, record in zip(values, self.records, strict=True):
                if (
                    type(value) is not dict
                    or set(value) != record_keys
                    or type(value.get("fields")) is not list
                ):
                    raise source_error(
                        ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION
                    )
                fields = value["fields"]
                valid_fields = True
                search_texts: list[str] = []
                for item in fields:
                    try:
                        if type(item) is not dict or set(item) != field_keys:
                            raise TypeError
                        field = CollaborationReadableField(
                            key=CollaborationReadableFieldKey(item["key"]),
                            text=item["text"],
                            assignment_ordinal=item["assignment_ordinal"],
                            input_ordinal=item["input_ordinal"],
                        )
                    except (KeyError, TypeError, ValueError):
                        valid_fields = False
                        break
                    search_texts.append(field.text)
                    retained_utf8_bytes += len(field.text.encode("utf-8"))
                boundary = record.boundary
                if (
                    not valid_fields
                    or value.get("session_id") != self.descriptor.scope.session_id
                    or value.get("conversation_id") != conversation_id
                    or boundary.kind is not ContextSourceBoundaryKind.RECORDS
                    or boundary.start != value.get("sequence")
                    or boundary.end != value.get("sequence")
                    or boundary.start is None
                    or boundary.start <= previous_end
                    or record.record_id != value.get("event_id")
                    or record.content != source_canonical_json_bytes(value).decode("utf-8")
                    or record.search_text != "\n".join(search_texts)
                ):
                    raise source_error(
                        ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION
                    )
                assert boundary.end is not None
                previous_end = boundary.end
            if bootstrap["retained_utf8_bytes"] != retained_utf8_bytes:
                raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
            return
        if self.descriptor.source_kind is ContextSourceKind.ACTIVE_DEBATE:
            if type(parsed) is not dict or set(parsed) != {"messages", "state"}:
                raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
            state = parsed.get("state")
            values = parsed.get("messages")
            debate_id = self.descriptor.source_id.removeprefix("active_debate:")
            if (
                type(state) is not dict
                or "owner_token" in state
                or state.get("session_id") != self.descriptor.scope.session_id
                or state.get("debate_id") != debate_id
                or state.get("status") != "active"
                or state.get("terminal") is not False
                or self.descriptor.source_id
                != source_structured_source_id("active_debate", debate_id)
                or self.descriptor.provenance.locator != f"debate/{debate_id}"
            ):
                raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
            if type(values) is not list or len(self.records) != len(values) + 1:
                raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
            state_record = self.records[0]
            state_content = source_canonical_json_bytes(state).decode("utf-8")
            if (
                state_record.record_id != "state"
                or state_record.boundary != ContextSourceBoundary.records(1, 1)
                or state_record.content != state_content
                or state_record.search_text != state_content
            ):
                raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
            message_keys = {
                "content",
                "kind",
                "phase",
                "revision",
                "round",
                "sender",
                "timestamp",
            }
            for index, (value, record) in enumerate(
                zip(values, self.records[1:]),
                start=1,
            ):
                if (
                    type(value) is not dict
                    or set(value) != message_keys
                    or record.record_id != f"message_{index}"
                    or record.boundary
                    != ContextSourceBoundary.records(index + 1, index + 1)
                    or record.content != source_canonical_json_bytes(value).decode("utf-8")
                    or record.search_text != value.get("content")
                ):
                    raise source_error(
                        ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION
                    )
            return
        values = parsed
        if (
            self.descriptor.source_id
            != source_structured_source_id(
                "own_agent_state",
                self.descriptor.scope.agent_id,
            )
            or self.descriptor.provenance.locator
            != f"agent-state/{self.descriptor.scope.agent_id}"
            or type(values) is not list
            or len(values) != len(self.records)
        ):
            raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
        entry_keys = {
            "agent_id",
            "content",
            "debate_id",
            "kind",
            "phase",
            "revision",
            "round",
            "session_id",
            "timestamp",
        }
        for index, (value, record) in enumerate(zip(values, self.records), start=1):
            if (
                type(value) is not dict
                or set(value) != entry_keys
                or record.record_id != f"entry_{index}"
                or record.boundary != ContextSourceBoundary.records(index, index)
                or record.content != source_canonical_json_bytes(value).decode("utf-8")
                or record.search_text != value.get("content")
                or value.get("agent_id") != self.descriptor.scope.agent_id
                or value.get("session_id") != self.descriptor.scope.session_id
            ):
                raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)

@dataclass(frozen=True, slots=True)
class ContextSourceSearchMatch:
    """One canonically ordered readable match before handle minting."""

    query_index: int
    descriptor: ContextSourceDescriptor
    record: ContextSourceRecord
    snippet: str = field(repr=False)
    snippet_utf8_bytes: int = 0

    def __post_init__(self) -> None:
        if _positive(self.query_index) > CONTEXT_ACTION_POLICY.max_queries_per_search:
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        if type(self.descriptor) is not ContextSourceDescriptor:
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        if type(self.record) is not ContextSourceRecord:
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        expected_boundary = {
            ContextSourceKind.WRITING_ARTIFACT: (
                ContextSourceBoundaryKind.ARTIFACT_LINES
            ),
            ContextSourceKind.CONVERSATION: (ContextSourceBoundaryKind.RECORDS),
            ContextSourceKind.ACTIVE_DEBATE: ContextSourceBoundaryKind.RECORDS,
            ContextSourceKind.OWN_AGENT_STATE: ContextSourceBoundaryKind.RECORDS,
        }[self.descriptor.source_kind]
        if (
            self.record.boundary.kind is not expected_boundary
            or self.record.utf8_bytes > CONTEXT_ACTION_POLICY.max_excerpt_utf8_bytes
        ):
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        snippet = _strict_text(self.snippet, allow_empty=True)
        if self.snippet_utf8_bytes != len(snippet.encode("utf-8")):
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        if (
            len(snippet) > CONTEXT_ACTION_POLICY.max_snippet_characters
            or self.snippet_utf8_bytes > CONTEXT_ACTION_POLICY.max_snippet_utf8_bytes
        ):
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)

def source_record(
    record_id: str,
    boundary: ContextSourceBoundary,
    content: str,
    search_text: str,
) -> ContextSourceRecord:
    content = _strict_text(content, allow_empty=True)
    search_text = _strict_text(search_text, allow_empty=True)
    body = content.encode("utf-8", errors="strict")
    return ContextSourceRecord(
        record_id=record_id,
        boundary=boundary,
        content=content,
        search_text=search_text,
        content_digest=source_digest(body),
        utf8_bytes=len(body),
    )

def source_descriptor(
    *,
    scope: ContextScope,
    source_id: str,
    source_kind: ContextSourceKind,
    revision: str,
    provenance: ContextProvenance,
    content: bytes,
    record_count: int,
) -> ContextSourceDescriptor:
    return ContextSourceDescriptor(
        scope=scope,
        source_id=source_id,
        source_kind=source_kind,
        revision=revision,
        provenance=provenance,
        content_digest=source_digest(content),
        utf8_bytes=len(content),
        record_count=record_count,
    )
