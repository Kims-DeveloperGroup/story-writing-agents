"""Pure action, observation, and evidence contracts for bounded agent context."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeAlias

from .manifest import (
    ContextAuthorityKind,
    ContextManifestError,
    ContextProvenance,
    ContextScope,
    ContextSourceKind,
    validate_context_digest,
    validate_context_identity,
)
from .planning import MandatoryReferenceReservation

__all__ = [
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
]

_BEGIN_UNTRUSTED_STORY_DATA = "<<<BEGIN_UNTRUSTED_STORY_DATA>>>"
_END_UNTRUSTED_STORY_DATA = "<<<END_UNTRUSTED_STORY_DATA>>>"
_HANDLE_PATTERN = re.compile(r"ctxh_v1_[0-9a-f]{64}\Z")
_AUTHORIZATION_PATTERN = re.compile(r"ctxa_v1_[0-9a-f]{64}\Z")
_MAX_ARTIFACT_PREFIX_UTF8_BYTES = 1_024
_SEARCHABLE_SOURCE_KIND_ORDER = (
    ContextSourceKind.WRITING_ARTIFACT,
    ContextSourceKind.CONVERSATION,
    ContextSourceKind.ACTIVE_DEBATE,
    ContextSourceKind.OWN_AGENT_STATE,
)
_SOURCE_KIND_WIRE_CODE = {
    ContextSourceKind.WRITING_ARTIFACT: "w",
    ContextSourceKind.CONVERSATION: "c",
    ContextSourceKind.ACTIVE_DEBATE: "d",
    ContextSourceKind.OWN_AGENT_STATE: "o",
}
_ACTION_POLICY_MAXIMA = {
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
    "max_snippet_utf8_bytes": 1_024,
    "max_excerpt_utf8_bytes": 16_384,
    "max_source_read_utf8_bytes": 16_384,
    "max_read_utf8_bytes": 24_576,
    "max_observation_utf8_bytes": 32_768,
}
_SOURCE_AUTHORITY = {
    ContextSourceKind.WRITING_ARTIFACT: (
        ContextAuthorityKind.WRITING_ARTIFACT_REPOSITORY
    ),
    ContextSourceKind.CONVERSATION: ContextAuthorityKind.CONVERSATION_STORAGE,
    ContextSourceKind.ACTIVE_DEBATE: ContextAuthorityKind.ACTIVE_DEBATE,
    ContextSourceKind.OWN_AGENT_STATE: ContextAuthorityKind.OWN_AGENT_STATE,
}


class ContextActionKind(StrEnum):
    """Substantive actions one agent can request during a context session."""

    SEARCH = "search"
    READ = "read"
    ANSWER = "answer"


class ContextSourceBoundaryKind(StrEnum):
    """Exact record boundaries supported by canonical context sources."""

    WHOLE = "whole"
    ARTIFACT_LINES = "artifact_lines"
    CONVERSATION_TURNS = "conversation_turns"
    RECORDS = "records"


_SOURCE_BOUNDARY = {
    ContextSourceKind.WRITING_ARTIFACT: ContextSourceBoundaryKind.ARTIFACT_LINES,
    ContextSourceKind.CONVERSATION: ContextSourceBoundaryKind.CONVERSATION_TURNS,
    ContextSourceKind.ACTIVE_DEBATE: ContextSourceBoundaryKind.RECORDS,
    ContextSourceKind.OWN_AGENT_STATE: ContextSourceBoundaryKind.RECORDS,
}


class ContextActionErrorCode(StrEnum):
    """Stable content-free action and session failure categories."""

    INVALID_VALUE = "invalid_value"
    CORRELATION_MISMATCH = "correlation_mismatch"
    INVALID_TRANSITION = "invalid_transition"
    ACTION_LIMIT_EXCEEDED = "action_limit_exceeded"
    SEARCH_LIMIT_EXCEEDED = "search_limit_exceeded"
    READ_LIMIT_EXCEEDED = "read_limit_exceeded"
    OBSERVATION_LIMIT_EXCEEDED = "observation_limit_exceeded"
    HANDLE_INVALID = "handle_invalid"
    OUTSTANDING_READ = "outstanding_read"
    ANSWER_NOT_AUTHORIZED = "answer_not_authorized"


_ACTION_ERROR_MESSAGES = {
    ContextActionErrorCode.INVALID_VALUE: "context action value is invalid",
    ContextActionErrorCode.CORRELATION_MISMATCH: (
        "context action correlation does not match"
    ),
    ContextActionErrorCode.INVALID_TRANSITION: (
        "context action transition is invalid"
    ),
    ContextActionErrorCode.ACTION_LIMIT_EXCEEDED: (
        "context action limit exceeded"
    ),
    ContextActionErrorCode.SEARCH_LIMIT_EXCEEDED: (
        "context search limit exceeded"
    ),
    ContextActionErrorCode.READ_LIMIT_EXCEEDED: "context read limit exceeded",
    ContextActionErrorCode.OBSERVATION_LIMIT_EXCEEDED: (
        "context observation limit exceeded"
    ),
    ContextActionErrorCode.HANDLE_INVALID: "context source handle is invalid",
    ContextActionErrorCode.OUTSTANDING_READ: (
        "context read delivery remains outstanding"
    ),
    ContextActionErrorCode.ANSWER_NOT_AUTHORIZED: (
        "context answer is not authorized"
    ),
}


class ContextActionError(ValueError):
    """Report one fixed content-free context action failure."""

    def __init__(self, code: ContextActionErrorCode) -> None:
        if type(code) is not ContextActionErrorCode:
            raise TypeError("action error code must be a ContextActionErrorCode")
        self.code = code
        super().__init__(_ACTION_ERROR_MESSAGES[code])


def _invalid() -> ContextActionError:
    """Build the fixed invalid-value error without reflecting caller data."""

    return ContextActionError(ContextActionErrorCode.INVALID_VALUE)


def _safe_identity(value: object) -> str:
    """Validate one content-free identity without retaining nested failures."""

    validated: str | None = None
    try:
        validated = validate_context_identity(value)
    except ContextManifestError:
        pass
    if validated is None:
        raise _invalid()
    return validated


def _safe_digest(value: object) -> str:
    """Validate one canonical digest without retaining nested failures."""

    validated: str | None = None
    try:
        validated = validate_context_digest(value)
    except ContextManifestError:
        pass
    if validated is None:
        raise _invalid()
    return validated


def _positive(value: object) -> int:
    """Return one exact positive integer or fail with fixed text."""

    if type(value) is not int or value < 1:
        raise _invalid()
    assert isinstance(value, int)
    return value


def _nonnegative(value: object) -> int:
    """Return one exact nonnegative integer or fail with fixed text."""

    if type(value) is not int or value < 0:
        raise _invalid()
    assert isinstance(value, int)
    return value


def _strict_text(value: object, *, allow_empty: bool = False) -> str:
    """Return strict UTF-8 text without leaking an encoding failure."""

    if type(value) is not str or (not value and not allow_empty):
        raise _invalid()
    assert isinstance(value, str)
    valid = True
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        valid = False
    if not valid or "\x00" in value:
        raise _invalid()
    return value


def _artifact_directory_prefix(value: object) -> str:
    """Validate one canonical writing-artifact directory prefix."""

    prefix = _strict_text(value)
    encoded = prefix.encode("utf-8", errors="strict")
    if (
        len(encoded) > _MAX_ARTIFACT_PREFIX_UTF8_BYTES
        or not prefix.endswith("/")
        or prefix.startswith("/")
        or "\\" in prefix
        or "//" in prefix
    ):
        raise _invalid()
    body = prefix[:-1]
    parts = body.split("/")
    if (
        not body
        or any(part in {"", ".", ".."} for part in parts)
        or any(
            unicodedata.category(character).startswith("C")
            for character in prefix
        )
        or parts[0] not in {"core_documents", "manuscript"}
        or (parts[0] == "core_documents" and len(parts) != 1)
        or (parts[0] == "manuscript" and parts[-1].endswith(".md"))
    ):
        raise _invalid()
    return prefix


def _validate_source_facts(
    source_kind: object,
    provenance: object,
    boundary: object,
    *,
    whole_allowed: bool,
) -> None:
    """Validate one exact source/authority/boundary classification."""

    if type(source_kind) is not ContextSourceKind or source_kind not in (
        _SOURCE_AUTHORITY
    ):
        raise _invalid()
    assert isinstance(source_kind, ContextSourceKind)
    if (
        type(provenance) is not ContextProvenance
        or provenance.authority_kind is not _SOURCE_AUTHORITY[source_kind]
    ):
        raise _invalid()
    if type(boundary) is not ContextSourceBoundary:
        raise _invalid()
    assert isinstance(boundary, ContextSourceBoundary)
    if boundary.kind is ContextSourceBoundaryKind.WHOLE:
        if not whole_allowed:
            raise _invalid()
    elif boundary.kind is not _SOURCE_BOUNDARY[source_kind]:
        raise _invalid()


@dataclass(frozen=True, slots=True)
class ContextActionPolicy:
    """Finite independent limits for one call-local action session."""

    max_nonterminal_actions: int = 4
    max_provider_calls: int = 5
    max_search_actions: int = 2
    max_read_actions: int = 2
    max_queries_per_search: int = 4
    max_cumulative_queries: int = 8
    max_handles_per_read: int = 8
    max_cumulative_handles: int = 16
    max_hits_per_query: int = 10
    max_hits_per_search: int = 20
    max_cumulative_hits: int = 20
    max_hits_per_source_query: int = 3
    max_query_characters: int = 200
    max_snippet_characters: int = 240
    max_snippet_utf8_bytes: int = 1_024
    max_excerpt_utf8_bytes: int = 16_384
    max_source_read_utf8_bytes: int = 16_384
    max_read_utf8_bytes: int = 24_576
    max_observation_utf8_bytes: int = 32_768

    def __post_init__(self) -> None:
        for name, maximum in _ACTION_POLICY_MAXIMA.items():
            if _positive(getattr(self, name)) > maximum:
                raise _invalid()
        if self.max_provider_calls != self.max_nonterminal_actions + 1:
            raise _invalid()
        if max(self.max_search_actions, self.max_read_actions) > (
            self.max_nonterminal_actions
        ):
            raise _invalid()
        if self.max_queries_per_search > self.max_cumulative_queries:
            raise _invalid()
        if self.max_handles_per_read > self.max_cumulative_handles:
            raise _invalid()
        if self.max_hits_per_source_query > self.max_hits_per_query:
            raise _invalid()
        if self.max_hits_per_query > self.max_hits_per_search:
            raise _invalid()
        if self.max_hits_per_search > self.max_cumulative_hits:
            raise _invalid()
        if self.max_snippet_utf8_bytes > self.max_excerpt_utf8_bytes:
            raise _invalid()
        if self.max_excerpt_utf8_bytes > self.max_source_read_utf8_bytes:
            raise _invalid()
        if self.max_source_read_utf8_bytes > self.max_read_utf8_bytes:
            raise _invalid()
        if self.max_read_utf8_bytes > self.max_observation_utf8_bytes:
            raise _invalid()


CONTEXT_ACTION_POLICY = ContextActionPolicy()


@dataclass(frozen=True, slots=True)
class ContextActionCorrelation:
    """Exact invocation and provider-call identity for one action."""

    scope: ContextScope
    operation_id: str
    result_correlation_id: str
    action_index: int

    def __post_init__(self) -> None:
        if type(self.scope) is not ContextScope:
            raise _invalid()
        _safe_identity(self.operation_id)
        _safe_identity(self.result_correlation_id)
        _positive(self.action_index)


@dataclass(frozen=True, slots=True)
class ContextSearchQuery:
    """One literal query constrained to a canonical source-kind subset."""

    text: str = field(repr=False)
    source_kinds: tuple[ContextSourceKind, ...]
    artifact_path_prefix: str | None = None

    def __post_init__(self) -> None:
        text = _strict_text(self.text)
        if len(text) > CONTEXT_ACTION_POLICY.max_query_characters:
            raise _invalid()
        if any(unicodedata.category(character).startswith("C") for character in text):
            raise _invalid()
        normalized = unicodedata.normalize("NFKC", text).casefold()
        normalized = " ".join(normalized.split())
        if not normalized or len(normalized) > (
            CONTEXT_ACTION_POLICY.max_query_characters
        ):
            raise _invalid()
        if type(self.source_kinds) is not tuple or not self.source_kinds:
            raise _invalid()
        if any(type(kind) is not ContextSourceKind for kind in self.source_kinds):
            raise _invalid()
        canonical = tuple(
            kind for kind in _SEARCHABLE_SOURCE_KIND_ORDER if kind in self.source_kinds
        )
        if canonical != self.source_kinds or len(set(self.source_kinds)) != len(
            self.source_kinds
        ):
            raise _invalid()
        if self.artifact_path_prefix is not None:
            if self.source_kinds != (ContextSourceKind.WRITING_ARTIFACT,):
                raise _invalid()
            _artifact_directory_prefix(self.artifact_path_prefix)

    @property
    def normalized_text(self) -> str:
        """Return the already-validated deterministic literal search key."""

        return " ".join(unicodedata.normalize("NFKC", self.text).casefold().split())


@dataclass(frozen=True, slots=True)
class ContextSourceBoundary:
    """One exact whole, line, complete-turn, or record boundary."""

    kind: ContextSourceBoundaryKind
    start: int | None = None
    end: int | None = None
    first_id: str | None = None
    last_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not ContextSourceBoundaryKind:
            raise _invalid()
        if self.kind is ContextSourceBoundaryKind.WHOLE:
            if any(
                value is not None
                for value in (self.start, self.end, self.first_id, self.last_id)
            ):
                raise _invalid()
            return
        start = _positive(self.start)
        end = _positive(self.end)
        if end < start:
            raise _invalid()
        if self.kind is ContextSourceBoundaryKind.CONVERSATION_TURNS:
            if self.first_id is None or self.last_id is None:
                raise _invalid()
            _safe_identity(self.first_id)
            _safe_identity(self.last_id)
            return
        if self.first_id is not None or self.last_id is not None:
            raise _invalid()

    @classmethod
    def whole(cls) -> ContextSourceBoundary:
        """Construct the sole whole-source boundary."""

        return cls(ContextSourceBoundaryKind.WHOLE)

    @classmethod
    def artifact_lines(cls, start: int, end: int) -> ContextSourceBoundary:
        """Construct one one-based inclusive artifact line boundary."""

        return cls(ContextSourceBoundaryKind.ARTIFACT_LINES, start, end)

    @classmethod
    def conversation_turns(
        cls,
        first_sequence: int,
        last_sequence: int,
        first_turn_id: str,
        last_turn_id: str,
    ) -> ContextSourceBoundary:
        """Construct one complete-turn sequence and identity boundary."""

        return cls(
            ContextSourceBoundaryKind.CONVERSATION_TURNS,
            first_sequence,
            last_sequence,
            first_turn_id,
            last_turn_id,
        )

    @classmethod
    def records(cls, start: int, end: int) -> ContextSourceBoundary:
        """Construct one one-based inclusive record boundary."""

        return cls(ContextSourceBoundaryKind.RECORDS, start, end)


@dataclass(frozen=True, slots=True)
class ContextSourceHandle:
    """One opaque invocation-local read handle."""

    value: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.value) is not str or _HANDLE_PATTERN.fullmatch(self.value) is None:
            raise _invalid()


@dataclass(frozen=True, slots=True)
class ContextSearchAction:
    """Request deterministic literal search over authorized source subsets."""

    correlation: ContextActionCorrelation
    queries: tuple[ContextSearchQuery, ...]

    def __post_init__(self) -> None:
        if type(self.correlation) is not ContextActionCorrelation:
            raise _invalid()
        if (
            type(self.queries) is not tuple
            or not self.queries
            or len(self.queries) > CONTEXT_ACTION_POLICY.max_queries_per_search
            or any(type(query) is not ContextSearchQuery for query in self.queries)
        ):
            raise _invalid()
        normalized = tuple(
            (
                query.normalized_text,
                query.source_kinds,
                query.artifact_path_prefix,
            )
            for query in self.queries
        )
        if len(set(normalized)) != len(normalized):
            raise _invalid()

    @property
    def kind(self) -> ContextActionKind:
        return ContextActionKind.SEARCH


@dataclass(frozen=True, slots=True)
class ContextReadAction:
    """Request one ordered all-or-nothing batch of source handles."""

    correlation: ContextActionCorrelation
    handles: tuple[ContextSourceHandle, ...]

    def __post_init__(self) -> None:
        if type(self.correlation) is not ContextActionCorrelation:
            raise _invalid()
        if (
            type(self.handles) is not tuple
            or not self.handles
            or len(self.handles) > CONTEXT_ACTION_POLICY.max_handles_per_read
            or any(type(handle) is not ContextSourceHandle for handle in self.handles)
            or len({handle.value for handle in self.handles}) != len(self.handles)
        ):
            raise _invalid()

    @property
    def kind(self) -> ContextActionKind:
        return ContextActionKind.READ


@dataclass(frozen=True, slots=True)
class ContextAnswerAction:
    """Request terminal answer authorization using completed read handles."""

    correlation: ContextActionCorrelation
    answer: str = field(repr=False)
    used_handles: tuple[ContextSourceHandle, ...] = ()

    def __post_init__(self) -> None:
        if type(self.correlation) is not ContextActionCorrelation:
            raise _invalid()
        _strict_text(self.answer)
        if (
            type(self.used_handles) is not tuple
            or any(
                type(handle) is not ContextSourceHandle
                for handle in self.used_handles
            )
            or len({handle.value for handle in self.used_handles})
            != len(self.used_handles)
            or len(self.used_handles) > CONTEXT_ACTION_POLICY.max_cumulative_handles
        ):
            raise _invalid()

    @property
    def kind(self) -> ContextActionKind:
        return ContextActionKind.ANSWER


ContextAction: TypeAlias = ContextSearchAction | ContextReadAction | ContextAnswerAction


@dataclass(frozen=True, slots=True)
class ContextSearchHit:
    """One bounded, ordered literal-search result with an opaque read handle."""

    query_index: int
    handle: ContextSourceHandle
    source_id: str
    source_kind: ContextSourceKind
    revision: str
    provenance: ContextProvenance
    source_digest: str
    source_utf8_bytes: int
    boundary: ContextSourceBoundary
    boundary_digest: str
    boundary_utf8_bytes: int
    snippet: str = field(repr=False)
    snippet_utf8_bytes: int = 0

    def __post_init__(self) -> None:
        _positive(self.query_index)
        if self.query_index > CONTEXT_ACTION_POLICY.max_queries_per_search:
            raise _invalid()
        if type(self.handle) is not ContextSourceHandle:
            raise _invalid()
        _safe_identity(self.source_id)
        if type(self.source_kind) is not ContextSourceKind:
            raise _invalid()
        _safe_digest(self.revision)
        _validate_source_facts(
            self.source_kind,
            self.provenance,
            self.boundary,
            whole_allowed=False,
        )
        _safe_digest(self.source_digest)
        source_bytes = _nonnegative(self.source_utf8_bytes)
        _safe_digest(self.boundary_digest)
        boundary_bytes = _positive(self.boundary_utf8_bytes)
        if boundary_bytes > source_bytes or boundary_bytes > (
            CONTEXT_ACTION_POLICY.max_excerpt_utf8_bytes
        ):
            raise _invalid()
        snippet = _strict_text(self.snippet)
        encoded = snippet.encode("utf-8")
        if self.snippet_utf8_bytes != len(encoded):
            raise _invalid()
        if (
            len(snippet) > CONTEXT_ACTION_POLICY.max_snippet_characters
            or len(encoded) > CONTEXT_ACTION_POLICY.max_snippet_utf8_bytes
        ):
            raise _invalid()


@dataclass(frozen=True, slots=True)
class ContextSearchObservation:
    """One canonical ordered search result frame."""

    correlation: ContextActionCorrelation
    hits: tuple[ContextSearchHit, ...]
    truncated: bool

    def __post_init__(self) -> None:
        if type(self.correlation) is not ContextActionCorrelation:
            raise _invalid()
        if (
            type(self.hits) is not tuple
            or any(type(hit) is not ContextSearchHit for hit in self.hits)
            or len(self.hits) > CONTEXT_ACTION_POLICY.max_hits_per_search
            or type(self.truncated) is not bool
        ):
            raise _invalid()
        handles = tuple(hit.handle.value for hit in self.hits)
        if len(set(handles)) != len(handles):
            raise _invalid()
        query_indices = tuple(hit.query_index for hit in self.hits)
        if query_indices != tuple(sorted(query_indices)):
            raise _invalid()
        identities = tuple(
            (hit.source_id, hit.revision, hit.boundary) for hit in self.hits
        )
        if len(set(identities)) != len(identities):
            raise _invalid()
        for query_index in {hit.query_index for hit in self.hits}:
            query_hits = tuple(
                hit for hit in self.hits if hit.query_index == query_index
            )
            if len(query_hits) > CONTEXT_ACTION_POLICY.max_hits_per_query:
                raise _invalid()
            for source_id in {hit.source_id for hit in query_hits}:
                if sum(hit.source_id == source_id for hit in query_hits) > (
                    CONTEXT_ACTION_POLICY.max_hits_per_source_query
                ):
                    raise _invalid()


@dataclass(frozen=True, slots=True)
class ContextEvidenceReceipt:
    """Content-free identity and digest receipt for one completed read."""

    handle: ContextSourceHandle
    source_id: str
    source_kind: ContextSourceKind
    revision: str
    provenance: ContextProvenance
    source_digest: str
    source_utf8_bytes: int
    boundary: ContextSourceBoundary
    boundary_digest: str
    boundary_utf8_bytes: int

    def __post_init__(self) -> None:
        if type(self.handle) is not ContextSourceHandle:
            raise _invalid()
        _safe_identity(self.source_id)
        if type(self.source_kind) is not ContextSourceKind:
            raise _invalid()
        _safe_digest(self.revision)
        _validate_source_facts(
            self.source_kind,
            self.provenance,
            self.boundary,
            whole_allowed=True,
        )
        _safe_digest(self.source_digest)
        source_bytes = _nonnegative(self.source_utf8_bytes)
        _safe_digest(self.boundary_digest)
        boundary_bytes = _nonnegative(self.boundary_utf8_bytes)
        if boundary_bytes > source_bytes or boundary_bytes > (
            CONTEXT_ACTION_POLICY.max_excerpt_utf8_bytes
        ):
            raise _invalid()


@dataclass(frozen=True, slots=True)
class ContextSourceEvidence:
    """One receipt paired with the exact untrusted excerpt delivered to an agent."""

    receipt: ContextEvidenceReceipt
    content: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.receipt) is not ContextEvidenceReceipt:
            raise _invalid()
        content = _strict_text(self.content, allow_empty=True)
        encoded = content.encode("utf-8")
        if len(encoded) != self.receipt.boundary_utf8_bytes:
            raise _invalid()
        if f"sha256:{hashlib.sha256(encoded).hexdigest()}" != (
            self.receipt.boundary_digest
        ):
            raise _invalid()


@dataclass(frozen=True, slots=True)
class ContextReadObservation:
    """One ordered all-or-nothing read evidence frame."""

    correlation: ContextActionCorrelation
    evidence: tuple[ContextSourceEvidence, ...]

    def __post_init__(self) -> None:
        if type(self.correlation) is not ContextActionCorrelation:
            raise _invalid()
        if type(self.evidence) is not tuple or any(
            type(item) is not ContextSourceEvidence for item in self.evidence
        ):
            raise _invalid()
        handles = tuple(item.receipt.handle.value for item in self.evidence)
        if (
            not handles
            or len(handles) > CONTEXT_ACTION_POLICY.max_handles_per_read
            or len(set(handles)) != len(handles)
        ):
            raise _invalid()


@dataclass(frozen=True, slots=True)
class ContextAnswerAuthorization:
    """Content-free authorization facts produced after final source revalidation."""

    authorization_id: str
    correlation: ContextActionCorrelation
    manifest_digest: str
    answer_digest: str
    completed_evidence: tuple[ContextEvidenceReceipt, ...]
    completed_read_digest: str
    used_evidence: tuple[ContextEvidenceReceipt, ...]
    reference_operation_id: str | None
    mandatory_references: tuple[MandatoryReferenceReservation, ...]

    def __post_init__(self) -> None:
        if (
            type(self.authorization_id) is not str
            or _AUTHORIZATION_PATTERN.fullmatch(self.authorization_id) is None
            or type(self.correlation) is not ContextActionCorrelation
        ):
            raise _invalid()
        _safe_digest(self.manifest_digest)
        _safe_digest(self.answer_digest)
        _safe_digest(self.completed_read_digest)
        if type(self.completed_evidence) is not tuple or any(
            type(receipt) is not ContextEvidenceReceipt
            for receipt in self.completed_evidence
        ):
            raise _invalid()
        if type(self.used_evidence) is not tuple or any(
            type(receipt) is not ContextEvidenceReceipt
            for receipt in self.used_evidence
        ):
            raise _invalid()
        completed_by_handle = {
            item.handle.value: item for item in self.completed_evidence
        }
        if (
            len(completed_by_handle) != len(self.completed_evidence)
            or len(self.completed_evidence)
            > CONTEXT_ACTION_POLICY.max_cumulative_handles
            or any(
                completed_by_handle.get(item.handle.value) != item
                for item in self.used_evidence
            )
        ):
            raise _invalid()
        if len({item.handle.value for item in self.used_evidence}) != len(
            self.used_evidence
        ) or len(self.used_evidence) > CONTEXT_ACTION_POLICY.max_cumulative_handles:
            raise _invalid()
        if self.completed_read_digest != _completed_evidence_digest(
            self.completed_evidence
        ):
            raise _invalid()
        if self.reference_operation_id is not None:
            _safe_identity(self.reference_operation_id)
        if type(self.mandatory_references) is not tuple or any(
            type(item) is not MandatoryReferenceReservation
            for item in self.mandatory_references
        ):
            raise _invalid()
        expected = tuple(range(1, len(self.mandatory_references) + 1))
        if tuple(item.ordinal for item in self.mandatory_references) != expected:
            raise _invalid()
        if bool(self.mandatory_references) is not bool(self.reference_operation_id):
            raise _invalid()


ContextActionObservation: TypeAlias = ContextSearchObservation | ContextReadObservation


def _boundary_value(boundary: ContextSourceBoundary) -> dict[str, object]:
    return {
        "end": boundary.end,
        "first_id": boundary.first_id,
        "kind": boundary.kind.value,
        "last_id": boundary.last_id,
        "start": boundary.start,
    }


def _correlation_value(
    correlation: ContextActionCorrelation,
) -> dict[str, object]:
    return {
        "action_index": correlation.action_index,
        "operation_id": correlation.operation_id,
        "result_correlation_id": correlation.result_correlation_id,
        "scope": {
            "agent_id": correlation.scope.agent_id,
            "session_id": correlation.scope.session_id,
            "subject_id": correlation.scope.subject_id,
            "subject_kind": correlation.scope.subject_kind.value,
            "workspace_id": correlation.scope.workspace_id,
        },
    }


def _receipt_value(receipt: ContextEvidenceReceipt) -> dict[str, object]:
    return {
        "boundary": _boundary_value(receipt.boundary),
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


def _completed_evidence_digest(
    evidence: tuple[ContextEvidenceReceipt, ...],
) -> str:
    encoded = json.dumps(
        {
            "evidence": [_receipt_value(receipt) for receipt in evidence],
            "kind": "completed_context_reads_v1",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8", errors="strict")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _wire_boundary_value(boundary: ContextSourceBoundary) -> list[object]:
    """Project one exact boundary identity."""

    return [
        boundary.kind.value,
        boundary.start,
        boundary.end,
        boundary.first_id,
        boundary.last_id,
    ]


def _wire_source_facts(
    *,
    source_alias: int,
    source_id: str,
    source_kind: ContextSourceKind,
    revision: str,
    source_digest: str,
    source_utf8_bytes: int,
) -> list[object]:
    """Project complete source facts, collapsing only an explicit digest equality."""

    digest_facts: list[object]
    if revision == source_digest:
        digest_facts = ["revision=source_digest", revision]
    else:
        digest_facts = ["revision", revision, "source_digest", source_digest]
    return [
        "source",
        source_alias,
        source_id,
        source_kind.value,
        digest_facts,
        source_utf8_bytes,
    ]


def _wire_boundary_facts(
    boundary: ContextSourceBoundary,
    boundary_digest: str,
    boundary_utf8_bytes: int,
) -> list[object]:
    """Project complete exact boundary identity, digest, and size facts."""

    return [
        "boundary",
        *_wire_boundary_value(boundary),
        boundary_digest,
        boundary_utf8_bytes,
    ]


def _wire_provenance_facts(
    authority_kind: ContextAuthorityKind,
    locator: str,
) -> list[object]:
    """Project complete safe provenance facts."""

    return ["provenance", authority_kind.value, locator]


def serialize_context_action_observation(
    observation: ContextActionObservation,
    *,
    source_aliases: Mapping[str, str] | None = None,
    handle_aliases: Mapping[str, str] | None = None,
    safe_locators: Mapping[str, str] | None = None,
) -> str:
    """Serialize one complete observation inside the established trust markers."""

    compact = source_aliases is not None
    if compact is not (handle_aliases is not None) or compact is not (
        safe_locators is not None
    ):
        raise _invalid()

    def source_value(source_id: str) -> int:
        if source_aliases is None:
            raise _invalid()
        value = source_aliases.get(source_id)
        if (
            type(value) is not str
            or not value.startswith("s")
            or not value[1:].isdigit()
            or value[1:].startswith("0")
        ):
            raise _invalid()
        return int(value[1:])

    def handle_value(handle: ContextSourceHandle) -> str:
        if handle_aliases is None:
            return handle.value
        value = handle_aliases.get(handle.value)
        if (
            type(value) is not str
            or len(value) != 17
            or value[0] != "h"
            or any(
                character
                not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
                for character in value[1:]
            )
        ):
            raise _invalid()
        return value

    def locator_value(
        handle: ContextSourceHandle,
        established: str,
    ) -> str:
        if safe_locators is None:
            return established
        value = safe_locators.get(handle.value)
        if type(value) is not str or not value:
            raise _invalid()
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise _invalid() from None
        return value

    if compact and type(observation) is ContextSearchObservation:
        payload: object = [
            "s",
            observation.truncated,
            [
                [
                    hit.query_index,
                    _wire_source_facts(
                        source_alias=source_value(hit.source_id),
                        source_id=hit.source_id,
                        source_kind=hit.source_kind,
                        revision=hit.revision,
                        source_digest=hit.source_digest,
                        source_utf8_bytes=hit.source_utf8_bytes,
                    ),
                    _wire_boundary_facts(
                        hit.boundary,
                        hit.boundary_digest,
                        hit.boundary_utf8_bytes,
                    ),
                    _wire_provenance_facts(
                        hit.provenance.authority_kind,
                        locator_value(hit.handle, hit.provenance.locator),
                    ),
                    _SOURCE_KIND_WIRE_CODE[hit.source_kind],
                    handle_value(hit.handle),
                    ["snippet", hit.snippet, hit.snippet_utf8_bytes],
                ]
                for hit in observation.hits
            ],
        ]
    elif compact and type(observation) is ContextReadObservation:
        payload = [
            "r",
            [
                [
                    _wire_source_facts(
                        source_alias=source_value(item.receipt.source_id),
                        source_id=item.receipt.source_id,
                        source_kind=item.receipt.source_kind,
                        revision=item.receipt.revision,
                        source_digest=item.receipt.source_digest,
                        source_utf8_bytes=item.receipt.source_utf8_bytes,
                    ),
                    _wire_boundary_facts(
                        item.receipt.boundary,
                        item.receipt.boundary_digest,
                        item.receipt.boundary_utf8_bytes,
                    ),
                    _wire_provenance_facts(
                        item.receipt.provenance.authority_kind,
                        locator_value(
                            item.receipt.handle,
                            item.receipt.provenance.locator,
                        ),
                    ),
                    _SOURCE_KIND_WIRE_CODE[item.receipt.source_kind],
                    handle_value(item.receipt.handle),
                    item.content,
                ]
                for item in observation.evidence
            ],
        ]
    elif type(observation) is ContextSearchObservation:
        payload = {
            "correlation": _correlation_value(observation.correlation),
            "hits": [
                {
                    "boundary": _boundary_value(hit.boundary),
                    "handle": hit.handle.value,
                    "provenance": {
                        "authority_kind": hit.provenance.authority_kind.value,
                        "locator": hit.provenance.locator,
                    },
                    "query_index": hit.query_index,
                    "revision": hit.revision,
                    "source_digest": hit.source_digest,
                    "source_utf8_bytes": hit.source_utf8_bytes,
                    "boundary_digest": hit.boundary_digest,
                    "boundary_utf8_bytes": hit.boundary_utf8_bytes,
                    "snippet": hit.snippet,
                    "snippet_utf8_bytes": hit.snippet_utf8_bytes,
                    "source_id": hit.source_id,
                    "source_kind": hit.source_kind.value,
                }
                for hit in observation.hits
            ],
            "kind": "search_observation",
            "truncated": observation.truncated,
        }
    elif type(observation) is ContextReadObservation:
        payload = {
            "correlation": _correlation_value(observation.correlation),
            "evidence": [
                {"content": item.content, **_receipt_value(item.receipt)}
                for item in observation.evidence
            ],
            "kind": "read_observation",
        }
    else:
        raise _invalid()
    encoded: str | None = None
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        encoded.encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError):
        pass
    if encoded is None:
        raise _invalid()
    if compact:
        return encoded
    return (
        f"{_BEGIN_UNTRUSTED_STORY_DATA}\n"
        f"{encoded}\n"
        f"{_END_UNTRUSTED_STORY_DATA}"
    )
