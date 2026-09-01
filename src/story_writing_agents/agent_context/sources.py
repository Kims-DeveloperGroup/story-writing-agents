"""Authorized bounded source adapters for provider-neutral agent context."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath

from ..conversations import (
    COLLABORATION_EVENT_SCHEMA_VERSION,
    CollaborationEvent,
    CollaborationEventStore,
    CollaborationReadableContext,
    CollaborationReadableField,
    CollaborationReadableFieldKey,
    CollaborationReadableRecord,
    ConversationCatalog,
    ConversationMetadata,
    ConversationStatus,
    encode_collaboration_event,
    select_bounded_collaboration_context,
)
from ..errors import (
    ConversationNotFoundError,
    StoryWritingAgentsError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from ..models import AgentStateEntry, DebateMessage
from ..workspace import Workspace
from ..writing_artifacts.filesystem_sources import (
    MAX_ARTIFACT_SOURCE_INVENTORY,
    MAX_ARTIFACT_SOURCE_TRAVERSAL_ENTRIES,
    ArtifactSourceInvalidUtf8Error,
    ArtifactSourceInventoryLimitError,
    ArtifactSourceNulError,
)
from ..writing_artifacts.references import (
    ArtifactContentSnapshot,
    reference_source_kind,
)
from ..writing_artifacts.repository import WritingArtifactRepository
from .actions import (
    CONTEXT_ACTION_POLICY,
    ContextActionPolicy,
    ContextSearchQuery,
    ContextSourceBoundary,
    ContextSourceBoundaryKind,
)
from .manifest import (
    ContextAuthorityKind,
    ContextManifest,
    ContextManifestEntry,
    ContextManifestError,
    ContextProvenance,
    ContextScope,
    ContextSourceKind,
    context_manifest_entry_key,
    validate_context_digest,
    validate_context_identity,
)

__all__ = [
    "CONTEXT_SOURCE_POLICY",
    "ActiveDebateSourceAdapter",
    "ContextSourceDescriptor",
    "ContextSourceError",
    "ContextSourceErrorCode",
    "ContextSourcePolicy",
    "ContextSourceRecord",
    "ContextSourceRegistry",
    "ContextSourceSearchMatch",
    "ContextSourceSnapshot",
    "FocusedConversationSourceAdapter",
    "OwnAgentStateSourceAdapter",
    "WritingArtifactSourceAdapter",
]

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


def _source_error(code: ContextSourceErrorCode) -> ContextSourceError:
    return ContextSourceError(code)


def _positive(value: object) -> int:
    if type(value) is not int or value < 1:
        raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
    assert isinstance(value, int)
    return value


def _nonnegative(value: object) -> int:
    if type(value) is not int or value < 0:
        raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
    assert isinstance(value, int)
    return value


def _require_source_policy(value: object) -> ContextSourcePolicy:
    """Return one exact source policy or fail with fixed content-free text."""

    if type(value) is not ContextSourcePolicy:
        raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
    assert isinstance(value, ContextSourcePolicy)
    return value


def _safe_identity(value: object) -> str:
    validated: str | None = None
    try:
        validated = validate_context_identity(value)
    except ContextManifestError:
        pass
    if validated is None:
        raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
    return validated


def _safe_digest(value: object) -> str:
    validated: str | None = None
    try:
        validated = validate_context_digest(value)
    except ContextManifestError:
        pass
    if validated is None:
        raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
    return validated


def _strict_text(value: object, *, allow_empty: bool = False) -> str:
    if type(value) is not str or (not value and not allow_empty):
        raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
    assert isinstance(value, str)
    valid = True
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        valid = False
    if not valid:
        raise _source_error(ContextSourceErrorCode.SOURCE_INVALID_UTF8)
    if "\x00" in value:
        raise _source_error(ContextSourceErrorCode.SOURCE_NUL)
    return value


def _canonical_json_bytes(value: object) -> bytes:
    if _contains_nul(value):
        raise _source_error(ContextSourceErrorCode.SOURCE_NUL)
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
        raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
    if b"\x00" in encoded:
        raise _source_error(ContextSourceErrorCode.SOURCE_NUL)
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


def _digest(body: bytes) -> str:
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def _identity_digest(value: object) -> str:
    return _digest(_canonical_json_bytes(value))


def _structured_source_id(prefix: str, identity: str) -> str:
    return f"{prefix}:{identity}"


def writing_artifact_source_id(path: str) -> str:
    """Return the canonical content-free source identity for an artifact path."""

    if not _artifact_locator_is_lexically_safe(path):
        raise _source_error(ContextSourceErrorCode.SOURCE_UNSAFE)
    digest = hashlib.sha256(path.encode("utf-8", errors="strict")).hexdigest()
    return f"writing_artifact:sha256:{digest}"


def _physical_lines(text: str) -> tuple[str, ...]:
    """Split only CRLF, LF, and lone CR while preserving exact bytes."""

    lines: list[str] = []
    start = 0
    index = 0
    while index < len(text):
        character = text[index]
        if character == "\r":
            index += 1
            if index < len(text) and text[index] == "\n":
                index += 1
            lines.append(text[start:index])
            start = index
            continue
        if character == "\n":
            index += 1
            lines.append(text[start:index])
            start = index
            continue
        index += 1
    if start < len(text):
        lines.append(text[start:])
    return tuple(lines)


def _artifact_locator_is_lexically_safe(value: object) -> bool:
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
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        if self.max_traversal_entries > MAX_ARTIFACT_SOURCE_TRAVERSAL_ENTRIES:
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        if self.max_source_utf8_bytes > 8 * _MEBIBYTE:
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        if self.max_records_per_source > 10_000:
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        if self.max_inventory_body_utf8_bytes > 64 * _MEBIBYTE:
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        if self.max_cumulative_scan_utf8_bytes > 32 * _MEBIBYTE:
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        if self.max_source_scan_utf8_bytes > self.max_source_utf8_bytes:
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        if self.max_cumulative_scan_utf8_bytes < self.max_source_scan_utf8_bytes:
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        if self.max_inventory_body_utf8_bytes < self.max_source_utf8_bytes:
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)


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
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        _safe_identity(self.source_id)
        if (
            type(self.source_kind) is not ContextSourceKind
            or self.source_kind not in _SOURCE_KIND_ORDER
        ):
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        _safe_digest(self.revision)
        if type(self.provenance) is not ContextProvenance:
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        if self.provenance.authority_kind is not _EXPECTED_AUTHORITY[self.source_kind]:
            raise _source_error(ContextSourceErrorCode.AUTHORITY_MISMATCH)
        _safe_digest(self.content_digest)
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
            eligible = self.source_id == _structured_source_id(
                "conversation",
                identity,
            )
            exact_identity = locator == f"conversation/{identity}"
        elif self.source_kind is ContextSourceKind.ACTIVE_DEBATE:
            identity = self.source_id.removeprefix("active_debate:")
            eligible = (
                self.scope.subject_kind.value == "debate"
                and self.scope.subject_id == identity
                and self.source_id == _structured_source_id("active_debate", identity)
            )
            exact_identity = locator == f"debate/{identity}"
        else:
            identity = self.source_id.removeprefix("own_agent_state:")
            eligible = (
                identity == self.scope.agent_id
                and self.source_id == _structured_source_id("own_agent_state", identity)
            )
            exact_identity = locator == f"agent-state/{identity}"
        if not eligible or not exact_identity:
            raise _source_error(ContextSourceErrorCode.AUTHORITY_MISMATCH)
        if (
            self.source_kind is not ContextSourceKind.WRITING_ARTIFACT
            and self.revision != self.content_digest
        ):
            raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
        if _nonnegative(self.utf8_bytes) > CONTEXT_SOURCE_POLICY.max_source_utf8_bytes:
            raise _source_error(ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)
        if _nonnegative(self.record_count) > (
            CONTEXT_SOURCE_POLICY.max_records_per_source
        ):
            raise _source_error(ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)


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
        _safe_identity(self.record_id)
        if type(self.boundary) is not ContextSourceBoundary:
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        content = _strict_text(self.content, allow_empty=True)
        _strict_text(self.search_text, allow_empty=True)
        encoded = content.encode("utf-8")
        if self.utf8_bytes != len(encoded) or self.content_digest != _digest(encoded):
            raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)


@dataclass(frozen=True, slots=True)
class ContextSourceSnapshot:
    """One immutable exact source capture with bounded atomic records."""

    descriptor: ContextSourceDescriptor
    records: tuple[ContextSourceRecord, ...]
    content: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.descriptor) is not ContextSourceDescriptor:
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        if type(self.records) is not tuple or any(
            type(record) is not ContextSourceRecord for record in self.records
        ):
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        if len(self.records) != self.descriptor.record_count:
            raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
        if len({record.record_id for record in self.records}) != len(self.records):
            raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
        if type(self.content) is not bytes:
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        decoded: str | None = None
        try:
            decoded = self.content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            pass
        if decoded is None:
            raise _source_error(ContextSourceErrorCode.SOURCE_INVALID_UTF8)
        if b"\x00" in self.content:
            raise _source_error(ContextSourceErrorCode.SOURCE_NUL)
        if (
            len(self.content) != self.descriptor.utf8_bytes
            or _digest(self.content) != self.descriptor.content_digest
        ):
            raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
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
                raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
            return
        parsed: object | None = None
        try:
            parsed = json.loads(decoded)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        if parsed is None or _canonical_json_bytes(parsed) != self.content:
            raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
        if self.descriptor.revision != self.descriptor.content_digest:
            raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
        if self.descriptor.source_kind is ContextSourceKind.CONVERSATION:
            if type(parsed) is not dict or set(parsed) != {"bootstrap", "records"}:
                raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
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
                != _structured_source_id("conversation", conversation_id)
                or self.descriptor.provenance.locator
                != f"conversation/{conversation_id}"
                or type(values) is not list
                or len(values) != len(self.records)
                or bootstrap.get("retained_records") != len(values)
            ):
                raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
            previous_end = 0
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
                    raise _source_error(
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
                    or record.content != _canonical_json_bytes(value).decode("utf-8")
                    or record.search_text != "\n".join(search_texts)
                ):
                    raise _source_error(
                        ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION
                    )
                assert boundary.end is not None
                previous_end = boundary.end
            return
        if self.descriptor.source_kind is ContextSourceKind.ACTIVE_DEBATE:
            if type(parsed) is not dict or set(parsed) != {"messages", "state"}:
                raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
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
                != _structured_source_id("active_debate", debate_id)
                or self.descriptor.provenance.locator != f"debate/{debate_id}"
            ):
                raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
            if type(values) is not list or len(self.records) != len(values) + 1:
                raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
            state_record = self.records[0]
            state_content = _canonical_json_bytes(state).decode("utf-8")
            if (
                state_record.record_id != "state"
                or state_record.boundary != ContextSourceBoundary.records(1, 1)
                or state_record.content != state_content
                or state_record.search_text != state_content
            ):
                raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
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
                    or record.content != _canonical_json_bytes(value).decode("utf-8")
                    or record.search_text != value.get("content")
                ):
                    raise _source_error(
                        ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION
                    )
            return
        values = parsed
        if (
            self.descriptor.source_id
            != _structured_source_id(
                "own_agent_state",
                self.descriptor.scope.agent_id,
            )
            or self.descriptor.provenance.locator
            != f"agent-state/{self.descriptor.scope.agent_id}"
            or type(values) is not list
            or len(values) != len(self.records)
        ):
            raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
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
                or record.content != _canonical_json_bytes(value).decode("utf-8")
                or record.search_text != value.get("content")
                or value.get("agent_id") != self.descriptor.scope.agent_id
                or value.get("session_id") != self.descriptor.scope.session_id
            ):
                raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)


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
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        if type(self.descriptor) is not ContextSourceDescriptor:
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        if type(self.record) is not ContextSourceRecord:
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
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
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        snippet = _strict_text(self.snippet, allow_empty=True)
        if self.snippet_utf8_bytes != len(snippet.encode("utf-8")):
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        if (
            len(snippet) > CONTEXT_ACTION_POLICY.max_snippet_characters
            or self.snippet_utf8_bytes > CONTEXT_ACTION_POLICY.max_snippet_utf8_bytes
        ):
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)


def _record(
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
        content_digest=_digest(body),
        utf8_bytes=len(body),
    )


def _descriptor(
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
        content_digest=_digest(content),
        utf8_bytes=len(content),
        record_count=record_count,
    )


class WritingArtifactSourceAdapter:
    """Capture canonical writing artifacts through their repository owner."""

    source_kind = ContextSourceKind.WRITING_ARTIFACT

    def __init__(
        self,
        repository: WritingArtifactRepository,
        scope: ContextScope,
        allowed_paths: frozenset[str] | None = None,
    ) -> None:
        if not isinstance(repository, WritingArtifactRepository):
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        if type(scope) is not ContextScope:
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        if allowed_paths is not None:
            if type(allowed_paths) is not frozenset:
                raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
            try:
                validated_paths = frozenset(
                    self._validate_locator(path) for path in allowed_paths
                )
            except ContextSourceError:
                raise
            if validated_paths != allowed_paths:
                raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        self._repository = repository
        self.scope = scope
        self._allowed_paths = allowed_paths

    def inventory(
        self,
        policy: ContextSourcePolicy,
    ) -> tuple[ContextSourceDescriptor, ...]:
        policy = _require_source_policy(policy)
        for attempt in range(2):
            paths = self._list_paths(policy)
            descriptors: list[ContextSourceDescriptor] = []
            inventory_bytes = 0
            retry = False
            for path in paths:
                try:
                    descriptor = self.capture(path, policy).descriptor
                except ContextSourceError as error:
                    if attempt == 0 and error.code in {
                        ContextSourceErrorCode.SOURCE_DRIFT,
                        ContextSourceErrorCode.SOURCE_MISSING,
                    }:
                        retry = True
                        break
                    raise
                inventory_bytes += descriptor.utf8_bytes
                if inventory_bytes > policy.max_inventory_body_utf8_bytes:
                    raise _source_error(ContextSourceErrorCode.INVENTORY_LIMIT_EXCEEDED)
                descriptors.append(descriptor)
            if retry:
                continue
            if paths == self._list_paths(policy):
                return tuple(descriptors)
        raise _source_error(ContextSourceErrorCode.SOURCE_DRIFT)

    def _list_paths(self, policy: ContextSourcePolicy) -> tuple[str, ...]:
        paths: tuple[str, ...] | None = None
        try:
            paths = self._repository.list_paths_bounded(
                max_visited_entries=policy.max_traversal_entries,
                max_eligible_sources=policy.max_inventory_sources,
            )
        except ArtifactSourceInventoryLimitError:
            pass
        if paths is None:
            raise _source_error(ContextSourceErrorCode.INVENTORY_LIMIT_EXCEEDED)
        if self._allowed_paths is None:
            return paths
        missing = self._allowed_paths.difference(paths)
        if missing:
            raise _source_error(ContextSourceErrorCode.SOURCE_MISSING)
        return tuple(path for path in paths if path in self._allowed_paths)

    @staticmethod
    def _validate_locator(locator: object) -> str:
        if not _artifact_locator_is_lexically_safe(locator):
            raise _source_error(ContextSourceErrorCode.SOURCE_UNSAFE)
        assert isinstance(locator, str)
        eligible = False
        try:
            eligible = reference_source_kind(locator) is not None
        except (TypeError, ValueError):
            pass
        if not eligible:
            raise _source_error(ContextSourceErrorCode.AUTHORITY_MISMATCH)
        return locator

    def _capture_once(
        self,
        locator: str,
        policy: ContextSourcePolicy,
    ) -> ContextSourceSnapshot:
        snapshots: tuple[ArtifactContentSnapshot, ...] | None = None
        capture_error: ContextSourceErrorCode | None = None
        try:
            snapshots = self._repository.read_current((locator,))
        except FileNotFoundError:
            capture_error = ContextSourceErrorCode.SOURCE_MISSING
        except ArtifactSourceInvalidUtf8Error:
            capture_error = ContextSourceErrorCode.SOURCE_INVALID_UTF8
        except ArtifactSourceNulError:
            capture_error = ContextSourceErrorCode.SOURCE_NUL
        except WorkspaceSecurityError:
            capture_error = ContextSourceErrorCode.SOURCE_UNSAFE
        except WorkspaceFormatError:
            capture_error = ContextSourceErrorCode.SOURCE_UNAVAILABLE
        if capture_error is not None:
            raise _source_error(capture_error)
        if snapshots == ():
            raise _source_error(ContextSourceErrorCode.SOURCE_MISSING)
        if snapshots is None or len(snapshots) != 1:
            raise _source_error(ContextSourceErrorCode.SOURCE_DRIFT)
        captured = snapshots[0]
        if captured.path != locator:
            raise _source_error(ContextSourceErrorCode.SOURCE_DRIFT)
        if len(captured.content) > policy.max_source_utf8_bytes:
            raise _source_error(ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)
        text: str | None = None
        try:
            text = captured.content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            pass
        if text is None:
            raise _source_error(ContextSourceErrorCode.SOURCE_INVALID_UTF8)
        if "\x00" in text:
            raise _source_error(ContextSourceErrorCode.SOURCE_NUL)
        lines = _physical_lines(text)
        if len(lines) > policy.max_records_per_source:
            raise _source_error(ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)
        records = tuple(
            _record(
                f"line_{index}",
                ContextSourceBoundary.artifact_lines(index, index),
                line,
                line,
            )
            for index, line in enumerate(lines, start=1)
        )
        source = captured.source
        provenance = ContextProvenance(
            ContextAuthorityKind.WRITING_ARTIFACT_REPOSITORY,
            locator,
        )
        revision = _identity_digest(
            {
                "changed_ns": source.changed_ns,
                "device_id": source.device_id,
                "digest": source.source_sha256,
                "inode": source.inode,
                "kind": source.kind.value,
                "modified_ns": source.modified_ns,
                "path": source.path,
                "utf8_bytes": source.source_utf8_bytes,
            }
        )
        descriptor = _descriptor(
            scope=self.scope,
            source_id=writing_artifact_source_id(locator),
            source_kind=self.source_kind,
            revision=revision,
            provenance=provenance,
            content=captured.content,
            record_count=len(records),
        )
        return ContextSourceSnapshot(descriptor, records, captured.content)

    def capture(
        self,
        locator: str,
        policy: ContextSourcePolicy,
    ) -> ContextSourceSnapshot:
        policy = _require_source_policy(policy)
        locator = self._validate_locator(locator)
        if self._allowed_paths is not None and locator not in self._allowed_paths:
            raise _source_error(ContextSourceErrorCode.AUTHORITY_MISMATCH)
        for _attempt in range(2):
            first = self._capture_once(locator, policy)
            second = self._capture_once(locator, policy)
            if first == second:
                return second
        raise _source_error(ContextSourceErrorCode.SOURCE_DRIFT)


def _readable_record_value(record: CollaborationReadableRecord) -> dict[str, object]:
    event = CollaborationEvent(
        schema_version=record.schema_version,
        event_id=record.event_id,
        session_id=record.session_id,
        conversation_id=record.conversation_id,
        turn_id=record.turn_id,
        step_id=record.step_id,
        sequence=record.sequence,
        timestamp=record.timestamp,
        batch_id=record.batch_id,
        batch_ordinal=record.batch_ordinal,
        batch_size=record.batch_size,
        batch_digest=record.batch_digest,
        kind=record.kind,
        sender=record.sender,
        recipient=record.recipient,
        payload=record.payload,
    )
    revision = _digest(
        b"story-agent-context-collaboration-event-v2\0"
        + encode_collaboration_event(event)
        + b"\0"
        + record.batch_digest.encode("ascii")
    )
    return {
        "schema_version": record.schema_version,
        "session_id": record.session_id,
        "conversation_id": record.conversation_id,
        "turn_id": record.turn_id,
        "step_id": record.step_id,
        "event_id": record.event_id,
        "batch_id": record.batch_id,
        "batch_ordinal": record.batch_ordinal,
        "batch_size": record.batch_size,
        "batch_digest": record.batch_digest,
        "sequence": record.sequence,
        "timestamp": record.timestamp,
        "sender": {
            "kind": record.sender.kind.value,
            "agent_id": record.sender.agent_id,
        },
        "recipient": {
            "kind": record.recipient.kind.value,
            "agent_id": record.recipient.agent_id,
        },
        "direction": record.direction.value,
        "kind": record.kind.value,
        "event_revision": revision,
        "fields": [
            {
                "key": field.key.value,
                "text": field.text,
                "assignment_ordinal": field.assignment_ordinal,
                "input_ordinal": field.input_ordinal,
            }
            for field in record.fields
        ],
    }


def _readable_context_bootstrap(
    context: CollaborationReadableContext,
    *,
    session_id: str,
    conversation_id: str,
) -> dict[str, object]:
    bootstrap = context.bootstrap
    return {
        "schema_version": bootstrap.schema_version,
        "session_id": bootstrap.session_id or session_id,
        "conversation_id": bootstrap.conversation_id or conversation_id,
        "total_records": bootstrap.total_records,
        "last_sequence": bootstrap.last_sequence,
        "tail_batch_digest": bootstrap.tail_batch_digest,
        "requested_records": context.requested_records,
        "retained_records": context.retained_records,
        "omitted_records": context.omitted_records,
        "retained_utf8_bytes": context.retained_utf8_bytes,
        "truncated": context.truncated,
    }


class FocusedConversationSourceAdapter:
    """Expose explicitly selected readable collaboration records for context."""

    source_kind = ContextSourceKind.CONVERSATION

    def __init__(
        self,
        catalog: ConversationCatalog,
        event_store: CollaborationEventStore,
        scope: ContextScope,
        conversation_id: str | None,
        *,
        include_same_session_active: bool = False,
        include_searchable_history: bool = False,
        captured_context: CollaborationReadableContext | None = None,
    ) -> None:
        if type(scope) is not ContextScope:
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        if conversation_id is not None:
            _safe_identity(conversation_id)
            if scope.subject_kind.value == "conversation" and (
                scope.subject_id != conversation_id
            ):
                raise _source_error(ContextSourceErrorCode.SCOPE_MISMATCH)
        if (
            type(include_same_session_active) is not bool
            or type(include_searchable_history) is not bool
        ):
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        if include_same_session_active and conversation_id is None:
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        if captured_context is not None:
            if (
                type(captured_context) is not CollaborationReadableContext
                or conversation_id is None
                or include_same_session_active
                or include_searchable_history
            ):
                raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
            bootstrap = captured_context.bootstrap
            if (
                bootstrap.session_id != scope.session_id
                or bootstrap.conversation_id != conversation_id
            ):
                raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        if not callable(getattr(catalog, "get", None)) or any(
            not callable(getattr(event_store, name, None))
            for name in ("read", "read_context")
        ):
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        if include_same_session_active and not callable(getattr(catalog, "list", None)):
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        self._catalog = catalog
        self._event_store = event_store
        self.scope = scope
        self._conversation_id = conversation_id
        self._include_same_session_active = include_same_session_active
        self._include_searchable_history = include_searchable_history
        self._captured_context = captured_context
        self._snapshot_conversation_ids: tuple[str, ...] | None = None
        self._snapshots: dict[
            tuple[str, ContextSourcePolicy], ContextSourceSnapshot
        ] = {}

    def inventory(
        self, policy: ContextSourcePolicy
    ) -> tuple[ContextSourceDescriptor, ...]:
        policy = _require_source_policy(policy)
        return tuple(
            self.capture(self._locator(conversation_id), policy).descriptor
            for conversation_id in self._active_conversation_ids()
        )

    @staticmethod
    def _locator(conversation_id: str) -> str:
        return f"conversation/{conversation_id}"

    def _active_conversation_ids(self) -> tuple[str, ...]:
        if self._include_searchable_history and (
            self._snapshot_conversation_ids is not None
        ):
            return self._snapshot_conversation_ids
        if self._captured_context is not None or not self._include_same_session_active:
            identifiers = (
                () if self._conversation_id is None else (self._conversation_id,)
            )
            if self._include_searchable_history:
                self._snapshot_conversation_ids = identifiers
            return identifiers
        try:
            raw = self._catalog.list(self.scope.session_id, include_archived=False)
        except (StoryWritingAgentsError, OSError, TypeError, ValueError):
            raise _source_error(ContextSourceErrorCode.SOURCE_UNAVAILABLE) from None
        if type(raw) is not tuple:
            raise _source_error(ContextSourceErrorCode.SOURCE_UNAVAILABLE)
        identifiers: list[str] = []
        for metadata in raw:
            if type(metadata) is not ConversationMetadata:
                raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
            if metadata.session_id != self.scope.session_id:
                raise _source_error(ContextSourceErrorCode.SCOPE_MISMATCH)
            if metadata.status is not ConversationStatus.ACTIVE:
                raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
            identifiers.append(metadata.conversation_id)
        if len(set(identifiers)) != len(identifiers):
            raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
        if self._conversation_id not in identifiers:
            raise _source_error(ContextSourceErrorCode.SOURCE_MISSING)
        captured = tuple(identifiers)
        if self._include_searchable_history:
            self._snapshot_conversation_ids = captured
        return captured

    def _authorized_conversation_id(self, locator: str) -> str:
        prefix = "conversation/"
        if not locator.startswith(prefix):
            raise _source_error(ContextSourceErrorCode.AUTHORITY_MISMATCH)
        conversation_id = locator[len(prefix) :]
        try:
            _safe_identity(conversation_id)
        except ContextSourceError:
            raise _source_error(ContextSourceErrorCode.AUTHORITY_MISMATCH) from None
        if conversation_id not in self._active_conversation_ids():
            raise _source_error(ContextSourceErrorCode.AUTHORITY_MISMATCH)
        return conversation_id

    def _capture_once(
        self,
        locator: str,
        policy: ContextSourcePolicy,
    ) -> ContextSourceSnapshot:
        policy = _require_source_policy(policy)
        conversation_id = self._authorized_conversation_id(locator)
        try:
            metadata = self._catalog.get(self.scope.session_id, conversation_id)
            if self._captured_context is None:
                events = self._event_store.read(self.scope.session_id, conversation_id)
                context = select_bounded_collaboration_context(
                    events,
                    event_ids=(
                        tuple(event.event_id for event in events)
                        if self._include_searchable_history
                        else ()
                    ),
                )
            else:
                context = self._captured_context
        except ConversationNotFoundError:
            raise _source_error(ContextSourceErrorCode.SOURCE_MISSING) from None
        except (StoryWritingAgentsError, OSError, TypeError, ValueError):
            raise _source_error(ContextSourceErrorCode.SOURCE_UNAVAILABLE) from None
        if (
            type(metadata) is not ConversationMetadata
            or metadata.session_id != self.scope.session_id
            or metadata.conversation_id != conversation_id
        ):
            raise _source_error(ContextSourceErrorCode.SCOPE_MISMATCH)
        if metadata.status is not ConversationStatus.ACTIVE:
            raise _source_error(ContextSourceErrorCode.SOURCE_UNAVAILABLE)
        bootstrap = context.bootstrap
        if bootstrap.session_id not in (
            None,
            self.scope.session_id,
        ) or bootstrap.conversation_id not in (None, conversation_id):
            raise _source_error(ContextSourceErrorCode.SCOPE_MISMATCH)
        if context.retained_records > policy.max_records_per_source:
            raise _source_error(ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)
        values = tuple(_readable_record_value(record) for record in context.records)
        records = tuple(
            _record(
                record.event_id,
                ContextSourceBoundary.records(record.sequence, record.sequence),
                _canonical_json_bytes(value).decode("utf-8"),
                "\n".join(field.text for field in record.fields),
            )
            for record, value in zip(context.records, values, strict=True)
        )
        body = _canonical_json_bytes(
            {
                "bootstrap": _readable_context_bootstrap(
                    context,
                    session_id=self.scope.session_id,
                    conversation_id=conversation_id,
                ),
                "records": values,
            }
        )
        if len(body) > policy.max_source_utf8_bytes:
            raise _source_error(ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)
        descriptor = _descriptor(
            scope=self.scope,
            source_id=_structured_source_id("conversation", conversation_id),
            source_kind=self.source_kind,
            revision=_digest(body),
            provenance=ContextProvenance(
                ContextAuthorityKind.CONVERSATION_STORAGE,
                locator,
            ),
            content=body,
            record_count=len(records),
        )
        return ContextSourceSnapshot(descriptor, records, body)

    def capture(
        self,
        locator: str,
        policy: ContextSourcePolicy,
    ) -> ContextSourceSnapshot:
        policy = _require_source_policy(policy)
        self._authorized_conversation_id(locator)
        cache_key = (locator, policy)
        if self._include_searchable_history:
            cached = self._snapshots.get(cache_key)
            if cached is not None:
                return cached
        for _attempt in range(2):
            first = self._capture_once(locator, policy)
            second = self._capture_once(locator, policy)
            if first == second:
                if self._include_searchable_history:
                    self._snapshots[cache_key] = second
                return second
        raise _source_error(ContextSourceErrorCode.SOURCE_DRIFT)


class ActiveDebateSourceAdapter:
    """Capture one exact active public debate through the Workspace owner."""

    source_kind = ContextSourceKind.ACTIVE_DEBATE

    def __init__(
        self,
        workspace: Workspace,
        scope: ContextScope,
        debate_id: str | None,
    ) -> None:
        if not isinstance(workspace, Workspace) or type(scope) is not ContextScope:
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        if debate_id is not None:
            _safe_identity(debate_id)
            if scope.subject_kind.value != "debate" or scope.subject_id != debate_id:
                raise _source_error(ContextSourceErrorCode.SCOPE_MISMATCH)
        self._workspace = workspace
        self.scope = scope
        self._debate_id = debate_id

    def _locator(self) -> str:
        assert self._debate_id is not None
        return f"debate/{self._debate_id}"

    def inventory(
        self,
        policy: ContextSourcePolicy,
    ) -> tuple[ContextSourceDescriptor, ...]:
        policy = _require_source_policy(policy)
        if self._debate_id is None:
            return ()
        return (self.capture(self._locator(), policy).descriptor,)

    def _capture_once(
        self,
        locator: str,
        policy: ContextSourcePolicy,
    ) -> ContextSourceSnapshot:
        if self._debate_id is None or locator != self._locator():
            raise _source_error(ContextSourceErrorCode.AUTHORITY_MISMATCH)
        value: dict[str, object] | None = None
        failed = False
        try:
            value = self._workspace.inspect_debate(
                self.scope.session_id,
                self._debate_id,
            )
        except (StoryWritingAgentsError, OSError, ValueError, TypeError):
            failed = True
        if failed:
            raise _source_error(ContextSourceErrorCode.SOURCE_UNAVAILABLE)
        if type(value) is not dict:
            raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
        if (
            value.get("session_id") != self.scope.session_id
            or value.get("debate_id") != self._debate_id
        ):
            raise _source_error(ContextSourceErrorCode.SCOPE_MISMATCH)
        if value.get("status") == "not_found":
            raise _source_error(ContextSourceErrorCode.SOURCE_MISSING)
        if (
            value.get("status") != "active"
            or value.get("terminal") is not False
            or value.get("result") is not None
        ):
            raise _source_error(ContextSourceErrorCode.SOURCE_UNAVAILABLE)
        state = value.get("state")
        messages = value.get("messages")
        if type(state) is not dict or type(messages) is not list:
            raise _source_error(ContextSourceErrorCode.SOURCE_UNAVAILABLE)
        if "owner_token" in state:
            raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
        state_content = _canonical_json_bytes(state).decode("utf-8")
        records: list[ContextSourceRecord] = [
            _record(
                "state",
                ContextSourceBoundary.records(1, 1),
                state_content,
                state_content,
            )
        ]
        message_values: list[dict[str, object]] = []
        for index, raw in enumerate(messages, start=1):
            message: DebateMessage | None = None
            try:
                if type(raw) is dict:
                    message = DebateMessage(**raw)
            except (TypeError, ValueError):
                pass
            if message is None:
                raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
            message_value = message.to_dict()
            message_values.append(message_value)
            content = _canonical_json_bytes(message_value).decode("utf-8")
            records.append(
                _record(
                    f"message_{index}",
                    ContextSourceBoundary.records(index + 1, index + 1),
                    content,
                    message.content,
                )
            )
        if len(records) > policy.max_records_per_source:
            raise _source_error(ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)
        body = _canonical_json_bytes({"messages": message_values, "state": state})
        if len(body) > policy.max_source_utf8_bytes:
            raise _source_error(ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)
        provenance = ContextProvenance(ContextAuthorityKind.ACTIVE_DEBATE, locator)
        descriptor = _descriptor(
            scope=self.scope,
            source_id=_structured_source_id("active_debate", self._debate_id),
            source_kind=self.source_kind,
            revision=_digest(body),
            provenance=provenance,
            content=body,
            record_count=len(records),
        )
        return ContextSourceSnapshot(descriptor, tuple(records), body)

    def capture(
        self,
        locator: str,
        policy: ContextSourcePolicy,
    ) -> ContextSourceSnapshot:
        policy = _require_source_policy(policy)
        for _attempt in range(2):
            first = self._capture_once(locator, policy)
            second = self._capture_once(locator, policy)
            if first == second:
                return second
        raise _source_error(ContextSourceErrorCode.SOURCE_DRIFT)


class OwnAgentStateSourceAdapter:
    """Capture only the invoking agent's complete ordered session state."""

    source_kind = ContextSourceKind.OWN_AGENT_STATE

    def __init__(self, workspace: Workspace, scope: ContextScope) -> None:
        if not isinstance(workspace, Workspace) or type(scope) is not ContextScope:
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        self._workspace = workspace
        self.scope = scope

    def _locator(self) -> str:
        return f"agent-state/{self.scope.agent_id}"

    def inventory(
        self,
        policy: ContextSourcePolicy,
    ) -> tuple[ContextSourceDescriptor, ...]:
        policy = _require_source_policy(policy)
        return (self.capture(self._locator(), policy).descriptor,)

    def _capture_once(
        self,
        locator: str,
        policy: ContextSourcePolicy,
    ) -> ContextSourceSnapshot:
        if locator != self._locator():
            raise _source_error(ContextSourceErrorCode.AUTHORITY_MISMATCH)
        entries: tuple[AgentStateEntry, ...] | None = None
        failed = False
        try:
            entries = self._workspace.read_agent_session_state(
                self.scope.agent_id,
                self.scope.session_id,
            )
        except (StoryWritingAgentsError, OSError, ValueError, TypeError):
            failed = True
        if failed:
            raise _source_error(ContextSourceErrorCode.SOURCE_UNAVAILABLE)
        if type(entries) is not tuple:
            raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
        if len(entries) > policy.max_records_per_source:
            raise _source_error(ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)
        records: list[ContextSourceRecord] = []
        entry_values: list[dict[str, object]] = []
        for index, entry in enumerate(entries, start=1):
            if (
                type(entry) is not AgentStateEntry
                or entry.agent_id != self.scope.agent_id
                or entry.session_id != self.scope.session_id
            ):
                raise _source_error(ContextSourceErrorCode.SCOPE_MISMATCH)
            entry_value = entry.to_dict()
            entry_values.append(entry_value)
            content = _canonical_json_bytes(entry_value).decode("utf-8")
            records.append(
                _record(
                    f"entry_{index}",
                    ContextSourceBoundary.records(index, index),
                    content,
                    entry.content,
                )
            )
        body = _canonical_json_bytes(entry_values)
        if len(body) > policy.max_source_utf8_bytes:
            raise _source_error(ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)
        provenance = ContextProvenance(
            ContextAuthorityKind.OWN_AGENT_STATE,
            locator,
        )
        descriptor = _descriptor(
            scope=self.scope,
            source_id=_structured_source_id("own_agent_state", self.scope.agent_id),
            source_kind=self.source_kind,
            revision=_digest(body),
            provenance=provenance,
            content=body,
            record_count=len(records),
        )
        return ContextSourceSnapshot(descriptor, tuple(records), body)

    def capture(
        self,
        locator: str,
        policy: ContextSourcePolicy,
    ) -> ContextSourceSnapshot:
        policy = _require_source_policy(policy)
        for _attempt in range(2):
            first = self._capture_once(locator, policy)
            second = self._capture_once(locator, policy)
            if first == second:
                return second
        raise _source_error(ContextSourceErrorCode.SOURCE_DRIFT)


def _descriptor_matches_entry(
    descriptor: ContextSourceDescriptor,
    entry: ContextManifestEntry,
) -> bool:
    return (
        descriptor.source_id == entry.source_id
        and descriptor.source_kind is entry.source_kind
        and descriptor.revision == entry.revision
        and descriptor.provenance == entry.provenance
        and descriptor.content_digest == entry.content_digest
        and descriptor.utf8_bytes == entry.utf8_bytes
    )


def _normalize_source_text(value: str) -> str:
    replaced = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in value
    )
    return " ".join(unicodedata.normalize("NFKC", replaced).casefold().split())


def _snippet(
    normalized: str,
    occurrence: int,
    literal: str,
    policy: ContextActionPolicy,
) -> str:
    literal_end = occurrence + len(literal)
    if literal_end > len(normalized):
        raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
    start = max(0, occurrence - policy.max_snippet_characters // 3)
    end = min(len(normalized), start + policy.max_snippet_characters)
    if end < literal_end:
        end = literal_end
        start = max(0, end - policy.max_snippet_characters)
    while len(normalized[start:end].encode("utf-8")) > (policy.max_snippet_utf8_bytes):
        if start < occurrence:
            start += 1
        elif end > literal_end:
            end -= 1
        else:
            raise _source_error(ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)
    candidate = normalized[start:end]
    if literal not in candidate:
        raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
    return candidate


def _expanded_artifact_record(
    snapshot: ContextSourceSnapshot,
    record: ContextSourceRecord,
    max_utf8_bytes: int,
) -> ContextSourceRecord | None:
    assert record.boundary.start is not None
    center = record.boundary.start
    if record.utf8_bytes > max_utf8_bytes:
        return None
    selected_start = center
    selected_end = center
    upper = len(snapshot.records)
    for distance in range(1, 13):
        candidate_start = center - distance
        if candidate_start >= 1:
            content = "".join(
                item.content
                for item in snapshot.records[candidate_start - 1 : selected_end]
            )
            if len(content.encode("utf-8")) <= max_utf8_bytes:
                selected_start = candidate_start
        candidate_end = center + distance
        if candidate_end <= upper:
            content = "".join(
                item.content
                for item in snapshot.records[selected_start - 1 : candidate_end]
            )
            if len(content.encode("utf-8")) <= max_utf8_bytes:
                selected_end = candidate_end
    content = "".join(
        item.content for item in snapshot.records[selected_start - 1 : selected_end]
    )
    return _record(
        f"lines_{selected_start}_{selected_end}",
        ContextSourceBoundary.artifact_lines(selected_start, selected_end),
        content,
        content,
    )


class ContextSourceRegistry:
    """Bind exactly four canonical authority adapters for one invocation scope."""

    def __init__(
        self,
        writing_artifacts: WritingArtifactSourceAdapter,
        focused_conversation: FocusedConversationSourceAdapter,
        active_debate: ActiveDebateSourceAdapter,
        own_agent_state: OwnAgentStateSourceAdapter,
    ) -> None:
        adapters = (
            writing_artifacts,
            focused_conversation,
            active_debate,
            own_agent_state,
        )
        expected_types = (
            WritingArtifactSourceAdapter,
            FocusedConversationSourceAdapter,
            ActiveDebateSourceAdapter,
            OwnAgentStateSourceAdapter,
        )
        if any(
            type(adapter) is not expected
            for adapter, expected in zip(adapters, expected_types)
        ):
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        scopes = {adapter.scope for adapter in adapters}
        if len(scopes) != 1:
            raise _source_error(ContextSourceErrorCode.SCOPE_MISMATCH)
        self.scope = adapters[0].scope
        self._adapters = adapters
        self._by_kind = {adapter.source_kind: adapter for adapter in adapters}

    def inventory(
        self,
        policy: ContextSourcePolicy = CONTEXT_SOURCE_POLICY,
    ) -> tuple[ContextSourceDescriptor, ...]:
        if type(policy) is not ContextSourcePolicy:
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        descriptors: list[ContextSourceDescriptor] = []
        for adapter in self._adapters:
            descriptors.extend(adapter.inventory(policy))
            if len(descriptors) > policy.max_inventory_sources:
                raise _source_error(ContextSourceErrorCode.INVENTORY_LIMIT_EXCEEDED)
        if sum(item.utf8_bytes for item in descriptors) > (
            policy.max_inventory_body_utf8_bytes
        ):
            raise _source_error(ContextSourceErrorCode.INVENTORY_LIMIT_EXCEEDED)
        if len({item.source_id for item in descriptors}) != len(descriptors):
            raise _source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
        return tuple(
            sorted(
                descriptors,
                key=lambda item: (
                    _SOURCE_KIND_ORDER.index(item.source_kind),
                    item.source_id.encode("utf-8"),
                ),
            )
        )

    def capture(
        self,
        manifest: ContextManifest,
        entry: ContextManifestEntry,
        policy: ContextSourcePolicy = CONTEXT_SOURCE_POLICY,
    ) -> ContextSourceSnapshot:
        policy = _require_source_policy(policy)
        if (
            type(manifest) is not ContextManifest
            or manifest.scope != self.scope
            or type(entry) is not ContextManifestEntry
            or entry not in manifest.entries
        ):
            raise _source_error(ContextSourceErrorCode.SCOPE_MISMATCH)
        if entry.utf8_bytes > policy.max_source_utf8_bytes:
            raise _source_error(ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)
        adapter = self._by_kind.get(entry.source_kind)
        if adapter is None:
            raise _source_error(ContextSourceErrorCode.AUTHORITY_MISMATCH)
        snapshot = adapter.capture(entry.provenance.locator, policy)
        if snapshot.descriptor.scope != manifest.scope:
            raise _source_error(ContextSourceErrorCode.SCOPE_MISMATCH)
        if not _descriptor_matches_entry(snapshot.descriptor, entry):
            raise _source_error(ContextSourceErrorCode.SOURCE_DRIFT)
        return snapshot

    def search(
        self,
        manifest: ContextManifest,
        queries: tuple[ContextSearchQuery, ...],
        *,
        source_policy: ContextSourcePolicy = CONTEXT_SOURCE_POLICY,
        action_policy: ContextActionPolicy = CONTEXT_ACTION_POLICY,
        remaining_scan_utf8_bytes: int | None = None,
    ) -> tuple[tuple[ContextSourceSearchMatch, ...], bool, int]:
        if type(manifest) is not ContextManifest or manifest.scope != self.scope:
            raise _source_error(ContextSourceErrorCode.SCOPE_MISMATCH)
        if (
            type(source_policy) is not ContextSourcePolicy
            or type(action_policy) is not ContextActionPolicy
        ):
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        if (
            type(queries) is not tuple
            or not queries
            or len(queries) > action_policy.max_queries_per_search
            or any(
                type(query) is not ContextSearchQuery
                or len(query.text) > action_policy.max_query_characters
                or len(query.normalized_text) > action_policy.max_query_characters
                for query in queries
            )
        ):
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        if remaining_scan_utf8_bytes is None:
            remaining_scan_utf8_bytes = source_policy.max_cumulative_scan_utf8_bytes
        _nonnegative(remaining_scan_utf8_bytes)
        remaining_scan_utf8_bytes = min(
            remaining_scan_utf8_bytes,
            source_policy.max_cumulative_scan_utf8_bytes,
        )
        normalized_pairs = tuple(
            (query.normalized_text, query.source_kinds) for query in queries
        )
        if len(set(normalized_pairs)) != len(normalized_pairs):
            raise _source_error(ContextSourceErrorCode.INVALID_VALUE)
        matches: list[ContextSourceSearchMatch] = []
        scanned = 0
        truncated = False
        requested_kinds = {
            source_kind for query in queries for source_kind in query.source_kinds
        }
        entries = tuple(
            entry
            for entry in sorted(manifest.entries, key=context_manifest_entry_key)
            if entry.searchable and entry.source_kind in requested_kinds
        )
        captured: list[tuple[ContextManifestEntry, ContextSourceSnapshot]] = []
        for entry in entries:
            if entry.utf8_bytes > source_policy.max_source_scan_utf8_bytes:
                truncated = True
                continue
            if scanned + entry.utf8_bytes > remaining_scan_utf8_bytes:
                truncated = True
                break
            snapshot = self.capture(manifest, entry, source_policy)
            if snapshot.descriptor.utf8_bytes > (
                source_policy.max_source_scan_utf8_bytes
            ):
                raise _source_error(ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)
            scanned += snapshot.descriptor.utf8_bytes
            captured.append((entry, snapshot))
        emitted: set[tuple[str, str, ContextSourceBoundary]] = set()
        for query_index, query in enumerate(queries, start=1):
            query_hits = 0
            for entry, snapshot in captured:
                if entry.source_kind not in query.source_kinds:
                    continue
                source_hits = 0
                for record in snapshot.records:
                    normalized = _normalize_source_text(record.search_text)
                    occurrence = normalized.find(query.normalized_text)
                    if occurrence < 0:
                        continue
                    selected = record
                    if entry.source_kind is ContextSourceKind.WRITING_ARTIFACT:
                        selected = _expanded_artifact_record(
                            snapshot,
                            record,
                            action_policy.max_excerpt_utf8_bytes,
                        )
                    if (
                        selected is None
                        or selected.utf8_bytes > action_policy.max_excerpt_utf8_bytes
                    ):
                        truncated = True
                        continue
                    identity = (
                        entry.source_id,
                        entry.revision,
                        selected.boundary,
                    )
                    if identity in emitted:
                        continue
                    if (
                        source_hits >= action_policy.max_hits_per_source_query
                        or query_hits >= action_policy.max_hits_per_query
                        or len(matches) >= action_policy.max_hits_per_search
                    ):
                        truncated = True
                        continue
                    snippet = _snippet(
                        normalized,
                        occurrence,
                        query.normalized_text,
                        action_policy,
                    )
                    matches.append(
                        ContextSourceSearchMatch(
                            query_index=query_index,
                            descriptor=snapshot.descriptor,
                            record=selected,
                            snippet=snippet,
                            snippet_utf8_bytes=len(snippet.encode("utf-8")),
                        )
                    )
                    emitted.add(identity)
                    source_hits += 1
                    query_hits += 1
        for entry, snapshot in captured:
            if self.capture(manifest, entry, source_policy) != snapshot:
                raise _source_error(ContextSourceErrorCode.SOURCE_DRIFT)
        return tuple(matches), truncated, scanned
