"""Content-free identities and canonical manifests for agent context sources."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "ContextAuthorityKind",
    "ContextManifest",
    "ContextManifestEntry",
    "ContextManifestError",
    "ContextManifestErrorCode",
    "ContextPriority",
    "ContextProvenance",
    "ContextScope",
    "ContextSemanticChannel",
    "ContextSourceKind",
    "ContextSubjectKind",
    "context_manifest_entry_key",
    "validate_context_digest",
    "validate_context_identity",
]

_MAX_CONTEXT_IDENTITY_UTF8_BYTES = 1024
_SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RUNTIME_SUBJECT_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_ABSOLUTE_WINDOWS_LOCATOR_PATTERN = re.compile(r"[A-Za-z]:[\\/]")


class ContextPriority(StrEnum):
    """Delivery priorities used by provider-neutral context planning."""

    P0_REQUIRED = "p0_required"
    P1_STABLE = "p1_stable"
    P2_WORKING = "p2_working"
    P3_OPTIONAL = "p3_optional"


class ContextSourceKind(StrEnum):
    """Authorized logical source categories without source capabilities."""

    RUNTIME_INPUT = "runtime_input"
    AGENT_DEFINITION = "agent_definition"
    WRITING_ARTIFACT = "writing_artifact"
    CONVERSATION = "conversation"
    ACTIVE_DEBATE = "active_debate"
    OWN_AGENT_STATE = "own_agent_state"


class ContextSubjectKind(StrEnum):
    """Runtime subject categories that participate in context scope identity."""

    CONVERSATION = "conversation"
    DEBATE = "debate"


class ContextAuthorityKind(StrEnum):
    """Authoritative owners identified by content-free provenance metadata."""

    REQUEST = "request"
    AGENT_DEFINITION = "agent_definition"
    WRITING_ARTIFACT_REPOSITORY = "writing_artifact_repository"
    CONVERSATION_STORAGE = "conversation_storage"
    ACTIVE_DEBATE = "active_debate"
    OWN_AGENT_STATE = "own_agent_state"
    REFERENCE_AUTHORIZATION = "reference_authorization"


class ContextSemanticChannel(StrEnum):
    """Meaning-preserving channels used for ordering and bounded deduplication."""

    CURRENT_INPUT = "current_input"
    CANDIDATE = "candidate"
    STABLE_BEHAVIOR = "stable_behavior"
    HARD_CONSTRAINT = "hard_constraint"
    FOCUSED_EVIDENCE = "focused_evidence"
    RETRIEVED_EVIDENCE = "retrieved_evidence"
    HISTORY = "history"
    OPTIONAL_BACKGROUND = "optional_background"


class ContextManifestErrorCode(StrEnum):
    """Stable content-free manifest validation failures."""

    INVALID_VALUE = "invalid_value"
    INVALID_MATRIX = "invalid_matrix"
    DUPLICATE_SOURCE_ID = "duplicate_source_id"


def _manifest_error_message(code: ContextManifestErrorCode) -> str:
    """Return fixed content-free text for one manifest failure code."""

    if code is ContextManifestErrorCode.INVALID_VALUE:
        return "context manifest contains an invalid value"
    if code is ContextManifestErrorCode.INVALID_MATRIX:
        return "context source classification is incompatible"
    return "context manifest source identities must be unique"


class ContextManifestError(ValueError):
    """Report a stable content-free manifest validation failure."""

    def __init__(self, code: ContextManifestErrorCode) -> None:
        if type(code) is not ContextManifestErrorCode:
            raise TypeError("manifest error code must be a ContextManifestErrorCode")
        self.code = code
        super().__init__(_manifest_error_message(code))


def validate_context_identity(value: object) -> str:
    """Return bounded safe UTF-8 identity text or raise a content-free error."""

    if type(value) is not str or not value:
        raise ContextManifestError(ContextManifestErrorCode.INVALID_VALUE)
    assert isinstance(value, str)
    encoded: bytes | None
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        encoded = None
    if encoded is None:
        raise ContextManifestError(ContextManifestErrorCode.INVALID_VALUE)
    if len(encoded) > _MAX_CONTEXT_IDENTITY_UTF8_BYTES:
        raise ContextManifestError(ContextManifestErrorCode.INVALID_VALUE)
    if any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs"}
        or character in {"\u2028", "\u2029"}
        for character in value
    ):
        raise ContextManifestError(ContextManifestErrorCode.INVALID_VALUE)
    return value


def validate_context_digest(value: object) -> str:
    """Return one canonical lowercase SHA-256 identity or fail safely."""

    if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
        raise ContextManifestError(ContextManifestErrorCode.INVALID_VALUE)
    assert isinstance(value, str)
    return value


def _exact_nonnegative_integer(value: object) -> int:
    """Return one exact nonnegative integer or fail safely."""

    if type(value) is not int or value < 0:
        raise ContextManifestError(ContextManifestErrorCode.INVALID_VALUE)
    assert isinstance(value, int)
    return value


@dataclass(frozen=True, slots=True)
class ContextScope:
    """Content-free ownership scope for one context manifest."""

    workspace_id: str
    session_id: str
    subject_kind: ContextSubjectKind
    subject_id: str
    agent_id: str

    def __post_init__(self) -> None:
        validate_context_identity(self.workspace_id)
        validate_context_identity(self.session_id)
        if type(self.subject_kind) is not ContextSubjectKind:
            raise ContextManifestError(ContextManifestErrorCode.INVALID_VALUE)
        if (
            type(self.subject_id) is not str
            or _RUNTIME_SUBJECT_ID_PATTERN.fullmatch(self.subject_id) is None
        ):
            raise ContextManifestError(ContextManifestErrorCode.INVALID_VALUE)
        validate_context_identity(self.agent_id)


@dataclass(frozen=True, slots=True)
class ContextProvenance:
    """Content-free locator for the current authoritative source revision."""

    authority_kind: ContextAuthorityKind
    locator: str

    def __post_init__(self) -> None:
        if type(self.authority_kind) is not ContextAuthorityKind:
            raise ContextManifestError(ContextManifestErrorCode.INVALID_VALUE)
        validate_context_identity(self.locator)
        if (
            self.locator.startswith(("/", "\\"))
            or _ABSOLUTE_WINDOWS_LOCATOR_PATTERN.match(self.locator) is not None
        ):
            raise ContextManifestError(ContextManifestErrorCode.INVALID_VALUE)


@dataclass(frozen=True, slots=True)
class ContextManifestEntry:
    """One immutable content-free description of a current context source."""

    source_id: str
    source_kind: ContextSourceKind
    semantic_channel: ContextSemanticChannel
    priority: ContextPriority
    ordinal: int
    revision: str
    content_digest: str
    provenance: ContextProvenance
    utf8_bytes: int
    searchable: bool = field(kw_only=True)

    def __post_init__(self) -> None:
        validate_context_identity(self.source_id)
        if type(self.source_kind) is not ContextSourceKind:
            raise ContextManifestError(ContextManifestErrorCode.INVALID_VALUE)
        if type(self.semantic_channel) is not ContextSemanticChannel:
            raise ContextManifestError(ContextManifestErrorCode.INVALID_VALUE)
        if type(self.priority) is not ContextPriority:
            raise ContextManifestError(ContextManifestErrorCode.INVALID_VALUE)
        _exact_nonnegative_integer(self.ordinal)
        validate_context_identity(self.revision)
        validate_context_digest(self.content_digest)
        if type(self.provenance) is not ContextProvenance:
            raise ContextManifestError(ContextManifestErrorCode.INVALID_VALUE)
        _exact_nonnegative_integer(self.utf8_bytes)
        if type(self.searchable) is not bool:
            raise ContextManifestError(ContextManifestErrorCode.INVALID_VALUE)
        should_be_searchable = self.priority in {
            ContextPriority.P2_WORKING,
            ContextPriority.P3_OPTIONAL,
        }
        if self.searchable is not should_be_searchable:
            raise ContextManifestError(ContextManifestErrorCode.INVALID_MATRIX)
        if not _authority_allows(self.source_kind, self.provenance.authority_kind):
            raise ContextManifestError(ContextManifestErrorCode.INVALID_MATRIX)
        if not _matrix_allows(
            self.source_kind,
            self.semantic_channel,
            self.priority,
        ):
            raise ContextManifestError(ContextManifestErrorCode.INVALID_MATRIX)


def _matrix_allows(
    source_kind: ContextSourceKind,
    semantic_channel: ContextSemanticChannel,
    priority: ContextPriority,
) -> bool:
    """Return whether a source classification has one supported meaning."""

    if source_kind is ContextSourceKind.RUNTIME_INPUT:
        return priority is ContextPriority.P0_REQUIRED and semantic_channel in {
            ContextSemanticChannel.CURRENT_INPUT,
            ContextSemanticChannel.CANDIDATE,
        }
    if source_kind is ContextSourceKind.AGENT_DEFINITION:
        return (
            priority is ContextPriority.P1_STABLE
            and semantic_channel is ContextSemanticChannel.STABLE_BEHAVIOR
        )
    if source_kind not in {
        ContextSourceKind.WRITING_ARTIFACT,
        ContextSourceKind.CONVERSATION,
        ContextSourceKind.ACTIVE_DEBATE,
        ContextSourceKind.OWN_AGENT_STATE,
    }:
        return False
    if priority is ContextPriority.P2_WORKING:
        return semantic_channel in {
            ContextSemanticChannel.HARD_CONSTRAINT,
            ContextSemanticChannel.FOCUSED_EVIDENCE,
            ContextSemanticChannel.RETRIEVED_EVIDENCE,
            ContextSemanticChannel.HISTORY,
        }
    if priority is ContextPriority.P3_OPTIONAL:
        return semantic_channel is ContextSemanticChannel.OPTIONAL_BACKGROUND
    return False


def _authority_allows(
    source_kind: ContextSourceKind,
    authority_kind: ContextAuthorityKind,
) -> bool:
    """Return whether one source kind names its sole authoritative owner."""

    if source_kind is ContextSourceKind.RUNTIME_INPUT:
        return authority_kind is ContextAuthorityKind.REQUEST
    if source_kind is ContextSourceKind.AGENT_DEFINITION:
        return authority_kind is ContextAuthorityKind.AGENT_DEFINITION
    if source_kind is ContextSourceKind.WRITING_ARTIFACT:
        return authority_kind is ContextAuthorityKind.WRITING_ARTIFACT_REPOSITORY
    if source_kind is ContextSourceKind.CONVERSATION:
        return authority_kind is ContextAuthorityKind.CONVERSATION_STORAGE
    if source_kind is ContextSourceKind.ACTIVE_DEBATE:
        return authority_kind is ContextAuthorityKind.ACTIVE_DEBATE
    if source_kind is ContextSourceKind.OWN_AGENT_STATE:
        return authority_kind is ContextAuthorityKind.OWN_AGENT_STATE
    return False


def context_manifest_entry_key(
    entry: ContextManifestEntry,
) -> tuple[int, int, int, bytes, bytes, bytes, bytes, bytes, bytes]:
    """Return the locale-independent canonical ordering key for one entry."""

    if type(entry) is not ContextManifestEntry:
        raise ContextManifestError(ContextManifestErrorCode.INVALID_VALUE)
    return (
        tuple(ContextPriority).index(entry.priority),
        tuple(ContextSemanticChannel).index(entry.semantic_channel),
        entry.ordinal,
        entry.source_id.encode("utf-8", errors="strict"),
        entry.revision.encode("utf-8", errors="strict"),
        entry.provenance.locator.encode("utf-8", errors="strict"),
        entry.source_kind.value.encode("ascii"),
        entry.provenance.authority_kind.value.encode("ascii"),
        b"1" if entry.searchable else b"0",
    )


@dataclass(frozen=True, slots=True)
class ContextManifest:
    """A canonically ordered content-free snapshot of current context sources."""

    scope: ContextScope
    entries: tuple[ContextManifestEntry, ...]

    def __post_init__(self) -> None:
        if type(self.scope) is not ContextScope:
            raise ContextManifestError(ContextManifestErrorCode.INVALID_VALUE)
        if type(self.entries) is not tuple or any(
            type(entry) is not ContextManifestEntry for entry in self.entries
        ):
            raise ContextManifestError(ContextManifestErrorCode.INVALID_VALUE)
        source_ids = tuple(entry.source_id for entry in self.entries)
        if len(set(source_ids)) != len(source_ids):
            raise ContextManifestError(
                ContextManifestErrorCode.DUPLICATE_SOURCE_ID
            )
        object.__setattr__(
            self,
            "entries",
            tuple(sorted(self.entries, key=context_manifest_entry_key)),
        )
