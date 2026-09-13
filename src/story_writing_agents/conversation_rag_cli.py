"""Fixed local conversation-RAG command workflows and JSON reports."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from enum import StrEnum

from .config import (
    ConfigurationCommitError,
    ConfigurationCommitFailure,
    ConfigurationSnapshot,
    ConfigManager,
)
from .conversation_rag import (
    CONVERSATION_RAG_POLICY,
    ConversationRagDocumentKey,
    ConversationRagOutcome,
    ConversationRagReason,
)
from .conversation_rag_composition import (
    ConversationRagCompositionError,
    ConversationRagCompositionFailure,
    LocalConversationRagRuntimeState,
    compose_local_conversation_rag_runtime,
    inspect_conversation_rag_compatibility,
)
from .conversation_rag_local import LOCAL_CONVERSATION_RAG_PROVIDER_ID
from .conversation_rag_runtime import (
    CONVERSATION_RAG_RUNTIME_POLICY,
    CONVERSATION_RAG_RUNTIME_SCHEMA_ID,
    ConversationRagRuntimeInspection,
    inspect_authorized_conversation_rag,
)
from .conversation_rag_store import (
    ConversationRagGenerationStore,
    ConversationRagStoreCounts,
    ConversationRagStoreError,
    ConversationRagStoreFailure,
    ConversationRagStoreSnapshot,
)
from .conversation_retrieval import (
    ConversationRagAttemptOutcome,
    ConversationRagContext,
    ConversationRagExcerpt,
)
from .conversation_search import CollaborationHistoryCitation
from .conversation_storage.contracts import (
    ConversationStorageCoordinationError,
    ConversationStorageError,
    ConversationStorageServices,
)
from .conversation_storage.selection import (
    compose_active_conversation_storage,
)
from .conversations import CollaborationReadableFieldKey
from .errors import ConfigurationError, WorkspaceFormatError
from .workspace import Workspace


CONVERSATION_RAG_CLI_SCHEMA_VERSION = 1

_SHA256_TOKEN = re.compile(r"sha256:[0-9a-f]{64}")
_SAFE_OPAQUE_ID = re.compile(r"(?:[A-Za-z0-9][A-Za-z0-9_-]{0,127}|sha256:[0-9a-f]{64})")
_RAG_CITATION_IDS = frozenset(
    f"g{ordinal}" for ordinal in range(1, CONVERSATION_RAG_POLICY.max_candidates + 1)
)


def _strict_utf8(
    value: object,
    *,
    label: str,
    nonempty: bool = False,
    nonblank: bool = False,
) -> str:
    if type(value) is not str:
        raise TypeError(f"{label} must be a string")
    assert isinstance(value, str)
    if nonempty and not value:
        raise ValueError(f"{label} must not be empty")
    if nonblank and (not value or value.isspace()):
        raise ValueError(f"{label} must not be empty or whitespace-only")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8") from exc
    return value


def _safe_opaque_id(value: object, *, label: str) -> str:
    text = _strict_utf8(value, label=label, nonblank=True)
    if _SAFE_OPAQUE_ID.fullmatch(text) is None:
        raise ValueError(f"{label} is invalid")
    return text


def _checkpoint(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str or _SHA256_TOKEN.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


class ConversationRagCliOperation(StrEnum):
    """Allowlisted local conversation-RAG command operations."""

    STATUS = "status"
    ENABLE = "enable"
    REBUILD = "rebuild"
    SEARCH = "search"
    DISABLE = "disable"
    PURGE = "purge"


class ConversationRagCliState(StrEnum):
    """Content-free user-facing state vocabulary."""

    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    MISSING = "missing"
    READY = "ready"
    STALE = "stale"
    FAILED = "failed"
    CORRUPT = "corrupt"
    INCOMPATIBLE = "incompatible"


class ConversationRagCliFailure(StrEnum):
    """Fixed content-free operation failure vocabulary."""

    INVALID_REQUEST = "invalid_request"
    WORKSPACE_INVALID = "workspace_invalid"
    SESSION_INVALID = "session_invalid"
    PROVIDER_DISABLED = "provider_disabled"
    PROVIDER_UNSUPPORTED = "provider_unsupported"
    DEPENDENCY_MISSING = "dependency_missing"
    VERSION_INCOMPATIBLE = "version_incompatible"
    CAPABILITY_INCOMPATIBLE = "capability_incompatible"
    CONFIGURATION_INVALID = "configuration_invalid"
    CONFIGURATION_CONFLICT = "configuration_conflict"
    CONFIGURATION_WRITE_FAILED = "configuration_write_failed"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    COORDINATION_FAILED = "coordination_failed"
    CONFIRMATION_REQUIRED = "confirmation_required"
    UNSAFE = "unsafe"
    CORRUPT = "corrupt"
    INCOMPATIBLE = "incompatible"
    PROVIDER_FAILED = "provider_failed"
    OPERATION_FAILED = "operation_failed"


@dataclass(frozen=True, slots=True)
class ConversationRagCliRequest:
    """One immutable, operation-specific command request."""

    operation: ConversationRagCliOperation
    session_id: str | None = None
    provider: str | None = None
    query: str | None = field(default=None, repr=False)
    confirmed: bool = False

    def __post_init__(self) -> None:
        if type(self.operation) is not ConversationRagCliOperation:
            raise TypeError("conversation RAG CLI operation is invalid")
        session_scoped = self.operation in {
            ConversationRagCliOperation.STATUS,
            ConversationRagCliOperation.REBUILD,
            ConversationRagCliOperation.SEARCH,
            ConversationRagCliOperation.PURGE,
        }
        if session_scoped:
            if type(self.session_id) is not str or not self.session_id:
                raise ValueError("session-scoped conversation RAG request is invalid")
            Workspace.validate_identifier(self.session_id)
        elif self.session_id is not None:
            raise ValueError(
                "workspace-scoped conversation RAG request named a session"
            )
        if self.operation is ConversationRagCliOperation.ENABLE:
            if type(self.provider) is not str or not self.provider:
                raise ValueError("conversation RAG enable request requires a provider")
            ConfigManager.validate_conversation_rag_provider(self.provider)
        elif self.provider is not None:
            raise ValueError("conversation RAG request named an inapplicable provider")
        if self.operation is ConversationRagCliOperation.SEARCH:
            _strict_utf8(
                self.query,
                label="conversation RAG search query",
                nonblank=True,
            )
        elif self.query is not None:
            raise ValueError("conversation RAG request included an inapplicable query")
        if type(self.confirmed) is not bool:
            raise TypeError("conversation RAG confirmation must be a boolean")
        if self.confirmed and self.operation not in {
            ConversationRagCliOperation.REBUILD,
            ConversationRagCliOperation.PURGE,
        }:
            raise ValueError("conversation RAG request included invalid confirmation")


@dataclass(frozen=True, slots=True)
class ConversationRagCliCounts:
    """Safe authoritative and derived-state counts in every report."""

    authoritative_conversations: int = 0
    authoritative_events: int = 0
    authoritative_documents: int = 0
    authoritative_source_utf8_bytes: int = 0
    indexed_conversations: int = 0
    indexed_events: int = 0
    indexed_documents: int = 0
    indexed_fragments: int = 0
    indexed_source_utf8_bytes: int = 0
    indexed_payload_bytes: int = 0
    indexed_generation_bytes: int = 0
    indexed_generations: int = 0
    indexed_orphan_generations: int = 0

    def __post_init__(self) -> None:
        for label, value in (
            ("authoritative conversations", self.authoritative_conversations),
            ("authoritative events", self.authoritative_events),
            ("authoritative documents", self.authoritative_documents),
            (
                "authoritative source UTF-8 bytes",
                self.authoritative_source_utf8_bytes,
            ),
            ("indexed conversations", self.indexed_conversations),
            ("indexed events", self.indexed_events),
            ("indexed documents", self.indexed_documents),
            ("indexed fragments", self.indexed_fragments),
            ("indexed source UTF-8 bytes", self.indexed_source_utf8_bytes),
            ("indexed payload bytes", self.indexed_payload_bytes),
            ("indexed generation bytes", self.indexed_generation_bytes),
            ("indexed generations", self.indexed_generations),
            ("indexed orphan generations", self.indexed_orphan_generations),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"conversation RAG report {label} is invalid")
        if self.authoritative_conversations > CONVERSATION_RAG_POLICY.max_conversations:
            raise ValueError("authoritative conversation count exceeds the RAG budget")
        if self.authoritative_events > CONVERSATION_RAG_POLICY.max_events:
            raise ValueError("authoritative event count exceeds the RAG budget")
        if self.authoritative_documents > CONVERSATION_RAG_POLICY.max_events:
            raise ValueError("authoritative document count exceeds the RAG budget")
        if (
            self.authoritative_source_utf8_bytes
            > CONVERSATION_RAG_RUNTIME_POLICY.max_projected_source_utf8_bytes
        ):
            raise ValueError("authoritative source bytes exceed the RAG budget")
        if self.authoritative_documents != self.authoritative_events:
            raise ValueError("authoritative document and event counts are inconsistent")
        if self.authoritative_documents > 0 and self.authoritative_conversations == 0:
            raise ValueError(
                "authoritative document and conversation counts are inconsistent"
            )
        if (self.authoritative_documents == 0) is not (
            self.authoritative_source_utf8_bytes == 0
        ):
            raise ValueError("authoritative document and byte counts are inconsistent")

        # Round-trip the store owner's existing caps and record correlations.
        ConversationRagStoreCounts(
            conversations=self.indexed_conversations,
            events=self.indexed_events,
            documents=self.indexed_documents,
            fragments=self.indexed_fragments,
            source_map_records=self.indexed_fragments,
            vector_records=self.indexed_fragments,
            generations=self.indexed_generations,
            orphan_generations=self.indexed_orphan_generations,
            source_utf8_bytes=self.indexed_source_utf8_bytes,
            payload_bytes=self.indexed_payload_bytes,
            generation_bytes=self.indexed_generation_bytes,
        )
        if self.indexed_documents > self.indexed_events:
            raise ValueError("indexed document and event counts are inconsistent")
        if self.indexed_documents == 0:
            if any(
                value != 0
                for value in (
                    self.indexed_conversations,
                    self.indexed_events,
                    self.indexed_fragments,
                    self.indexed_source_utf8_bytes,
                )
            ):
                raise ValueError("indexed empty document counts are inconsistent")
        elif (
            self.indexed_conversations == 0
            or self.indexed_conversations > self.indexed_documents
            or self.indexed_events != self.indexed_documents
            or self.indexed_fragments < self.indexed_documents
            or self.indexed_source_utf8_bytes == 0
            or self.indexed_generations == 0
        ):
            raise ValueError("indexed document records are inconsistent")
        if self.indexed_payload_bytes > self.indexed_generation_bytes:
            raise ValueError("indexed generation byte counts are inconsistent")
        if (
            self.indexed_generation_bytes > 0 or self.indexed_payload_bytes > 0
        ) and self.indexed_generations == 0:
            raise ValueError("indexed generation counts are inconsistent")

    def to_dict(self) -> dict[str, object]:
        return {
            "authoritative": {
                "conversations": self.authoritative_conversations,
                "documents": self.authoritative_documents,
                "events": self.authoritative_events,
                "source_utf8_bytes": self.authoritative_source_utf8_bytes,
            },
            "indexed": {
                "conversations": self.indexed_conversations,
                "documents": self.indexed_documents,
                "events": self.indexed_events,
                "fragments": self.indexed_fragments,
                "generation_bytes": self.indexed_generation_bytes,
                "generations": self.indexed_generations,
                "orphan_generations": self.indexed_orphan_generations,
                "payload_bytes": self.indexed_payload_bytes,
                "source_utf8_bytes": self.indexed_source_utf8_bytes,
            },
        }


@dataclass(frozen=True, slots=True)
class ConversationRagCliExcerpt:
    """Bounded authorized text with stable citation identities and ranges."""

    citation_id: str
    session_id: str
    conversation_id: str
    event_id: str
    sequence: int
    field_key: str
    field_start_codepoint: int
    field_end_codepoint: int
    assignment_ordinal: int | None
    input_ordinal: int | None
    document_id: str
    revision_id: str
    fragment_id: str
    start_codepoint: int
    end_codepoint: int
    content: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.citation_id) is not str
            or self.citation_id not in _RAG_CITATION_IDS
        ):
            raise ValueError("conversation RAG CLI citation ID is invalid")
        if type(self.field_key) is not str:
            raise TypeError("conversation RAG CLI field key must be a string")
        try:
            field_key = CollaborationReadableFieldKey(self.field_key)
        except ValueError as exc:
            raise ValueError("conversation RAG CLI field key is invalid") from exc
        citation = CollaborationHistoryCitation(
            session_id=self.session_id,
            conversation_id=self.conversation_id,
            event_id=self.event_id,
            sequence=self.sequence,
            field_key=field_key,
            start_codepoint=self.field_start_codepoint,
            end_codepoint=self.field_end_codepoint,
            assignment_ordinal=self.assignment_ordinal,
            input_ordinal=self.input_ordinal,
        )
        ConversationRagDocumentKey(
            session_id=citation.session_id,
            document_id=self.document_id,
        )
        _safe_opaque_id(self.revision_id, label="conversation RAG CLI revision ID")
        _safe_opaque_id(self.fragment_id, label="conversation RAG CLI fragment ID")
        for label, value in (
            ("start codepoint", self.start_codepoint),
            ("end codepoint", self.end_codepoint),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"conversation RAG CLI {label} is invalid")
        if self.start_codepoint >= self.end_codepoint:
            raise ValueError("conversation RAG CLI excerpt range is invalid")
        content = _strict_utf8(
            self.content,
            label="conversation RAG CLI excerpt content",
            nonempty=True,
        )
        width = self.end_codepoint - self.start_codepoint
        if len(content) != width:
            raise ValueError("conversation RAG CLI excerpt width is inconsistent")
        if width > CONVERSATION_RAG_POLICY.max_fragment_codepoints:
            raise ValueError("conversation RAG CLI excerpt exceeds the fixed limit")

    @classmethod
    def from_excerpt(cls, excerpt: ConversationRagExcerpt) -> ConversationRagCliExcerpt:
        if type(excerpt) is not ConversationRagExcerpt:
            raise TypeError("conversation RAG CLI excerpt is invalid")
        citation = excerpt.source.citation
        document = excerpt.reference.document
        return cls(
            citation_id=excerpt.citation_id,
            session_id=citation.session_id,
            conversation_id=citation.conversation_id,
            event_id=citation.event_id,
            sequence=citation.sequence,
            field_key=citation.field_key.value,
            field_start_codepoint=citation.start_codepoint,
            field_end_codepoint=citation.end_codepoint,
            assignment_ordinal=citation.assignment_ordinal,
            input_ordinal=citation.input_ordinal,
            document_id=document.key.document_id,
            revision_id=document.revision_id,
            fragment_id=excerpt.reference.fragment_id,
            start_codepoint=excerpt.reference.start_codepoint,
            end_codepoint=excerpt.reference.end_codepoint,
            content=excerpt.content,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "citation_id": self.citation_id,
            "content": self.content,
            "source": {
                "conversation_id": self.conversation_id,
                "event_id": self.event_id,
                "field_key": self.field_key,
                "start_codepoint": self.field_start_codepoint,
                "end_codepoint": self.field_end_codepoint,
                "assignment_ordinal": self.assignment_ordinal,
                "input_ordinal": self.input_ordinal,
                "sequence": self.sequence,
                "session_id": self.session_id,
            },
            "reference": {
                "document_id": self.document_id,
                "end_codepoint": self.end_codepoint,
                "fragment_id": self.fragment_id,
                "revision_id": self.revision_id,
                "start_codepoint": self.start_codepoint,
            },
        }


@dataclass(frozen=True, slots=True)
class ConversationRagCliReport:
    """One compact schema-v1 result with a stable key set."""

    operation: ConversationRagCliOperation
    outcome: str
    state: ConversationRagCliState | None = None
    provider: str | None = None
    automatic: bool | None = None
    compatibility: str | None = None
    configuration: str = "not_applicable"
    authoritative_checkpoint: str | None = None
    indexed_checkpoint: str | None = None
    rebuild_required: bool | None = None
    counts: ConversationRagCliCounts = ConversationRagCliCounts()
    purged_generations: int = 0
    purged_orphan_generations: int = 0
    retrieval_outcome: str | None = None
    retrieval_reason: str | None = None
    truncated: bool | None = None
    excerpts: tuple[ConversationRagCliExcerpt, ...] = ()
    failure: ConversationRagCliFailure | None = None
    schema_version: int = CONVERSATION_RAG_CLI_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != CONVERSATION_RAG_CLI_SCHEMA_VERSION
        ):
            raise ValueError("conversation RAG report schema version is invalid")
        if type(self.operation) is not ConversationRagCliOperation:
            raise TypeError("conversation RAG report operation is invalid")
        if type(self.outcome) is not str or self.outcome not in {
            "completed",
            "failed",
        }:
            raise ValueError("conversation RAG report outcome is invalid")
        if self.state is not None and type(self.state) is not ConversationRagCliState:
            raise TypeError("conversation RAG report state is invalid")
        if self.provider is not None and (
            type(self.provider) is not str or self.provider != "local"
        ):
            raise ValueError("conversation RAG report provider is invalid")
        if self.compatibility is not None:
            if type(self.compatibility) is not str or self.compatibility not in {
                "compatible",
                *(failure.value for failure in ConversationRagCompositionFailure),
            }:
                raise ValueError(
                    "conversation RAG compatibility report value is invalid"
                )
        if self.automatic is not None and type(self.automatic) is not bool:
            raise TypeError("conversation RAG automatic report value is invalid")
        if type(self.configuration) is not str or self.configuration not in {
            "not_applicable",
            "saved",
            "unchanged",
            "preserved",
        }:
            raise ValueError("conversation RAG configuration report value is invalid")
        _checkpoint(
            self.authoritative_checkpoint,
            label="conversation RAG authoritative checkpoint",
        )
        _checkpoint(
            self.indexed_checkpoint,
            label="conversation RAG indexed checkpoint",
        )
        if (
            self.rebuild_required is not None
            and type(self.rebuild_required) is not bool
        ):
            raise TypeError("conversation RAG rebuild report value is invalid")
        if type(self.counts) is not ConversationRagCliCounts:
            raise TypeError("conversation RAG report counts are invalid")
        if (
            type(self.purged_generations) is not int
            or self.purged_generations < 0
            or type(self.purged_orphan_generations) is not int
            or self.purged_orphan_generations < 0
        ):
            raise ValueError("conversation RAG purge report count is invalid")
        if self.truncated is not None and type(self.truncated) is not bool:
            raise TypeError("conversation RAG truncation report value is invalid")
        for label, value in (
            ("retrieval outcome", self.retrieval_outcome),
            ("retrieval reason", self.retrieval_reason),
        ):
            if value is not None and type(value) is not str:
                raise TypeError(f"conversation RAG report {label} is invalid")
        if type(self.excerpts) is not tuple or any(
            type(excerpt) is not ConversationRagCliExcerpt for excerpt in self.excerpts
        ):
            raise TypeError("conversation RAG report excerpts are invalid")
        if (
            self.failure is not None
            and type(self.failure) is not ConversationRagCliFailure
        ):
            raise TypeError("conversation RAG report failure is invalid")
        if (self.outcome == "completed") is (self.failure is not None):
            raise ValueError("conversation RAG report failure is inconsistent")
        if self.automatic is True and self.provider != "local":
            raise ValueError("automatic conversation RAG requires the local provider")

        empty_counts = ConversationRagCliCounts()
        has_retrieval_payload = (
            self.retrieval_outcome is not None
            or self.retrieval_reason is not None
            or self.truncated is not None
            or bool(self.excerpts)
        )
        if self.outcome == "failed":
            expected_configuration = (
                "preserved"
                if self.operation
                in {
                    ConversationRagCliOperation.ENABLE,
                    ConversationRagCliOperation.DISABLE,
                }
                else "not_applicable"
            )
            if self.configuration != expected_configuration:
                raise ValueError(
                    "conversation RAG failed configuration state is inconsistent"
                )
            if (
                self.state is not None
                or self.authoritative_checkpoint is not None
                or self.indexed_checkpoint is not None
                or self.rebuild_required is not None
                or self.counts != empty_counts
                or self.purged_generations != 0
                or self.purged_orphan_generations != 0
                or has_retrieval_payload
            ):
                raise ValueError("conversation RAG failed report contains a payload")
            self._validate_failed_report_context()
            return

        if self.operation in {
            ConversationRagCliOperation.STATUS,
            ConversationRagCliOperation.REBUILD,
        }:
            self._validate_status_report()
            return
        if self.operation is ConversationRagCliOperation.ENABLE:
            if (
                self.state is not None
                or self.provider != "local"
                or type(self.automatic) is not bool
                or self.compatibility != "compatible"
                or self.configuration not in {"saved", "unchanged"}
            ):
                raise ValueError("conversation RAG enable report is inconsistent")
        elif self.operation is ConversationRagCliOperation.DISABLE:
            if (
                self.state is not ConversationRagCliState.DISABLED
                or self.provider is not None
                or self.automatic is not False
                or self.compatibility is not None
                or self.configuration not in {"saved", "unchanged"}
            ):
                raise ValueError("conversation RAG disable report is inconsistent")
        elif self.operation is ConversationRagCliOperation.SEARCH:
            self._validate_search_report()
            return
        else:
            self._validate_purge_report()
            return
        if (
            self.authoritative_checkpoint is not None
            or self.indexed_checkpoint is not None
            or self.rebuild_required is not None
            or self.counts != empty_counts
            or self.purged_generations != 0
            or self.purged_orphan_generations != 0
            or has_retrieval_payload
        ):
            raise ValueError("conversation RAG configuration report contains a payload")

    def _validate_failed_report_context(self) -> None:
        if self.compatibility is not None and (
            self.failure is None or self.compatibility != self.failure.value
        ):
            raise ValueError("conversation RAG failure compatibility is inconsistent")
        if self.operation in {
            ConversationRagCliOperation.STATUS,
            ConversationRagCliOperation.DISABLE,
            ConversationRagCliOperation.PURGE,
        }:
            if (
                self.provider is not None
                or self.automatic is not None
                or self.compatibility is not None
            ):
                raise ValueError("conversation RAG failure context is inapplicable")
            return
        if self.operation is ConversationRagCliOperation.ENABLE:
            if self.provider is None:
                if self.automatic is not None or self.compatibility is not None:
                    raise ValueError("conversation RAG enable failure is inconsistent")
                return
            if self.automatic is not None or self.compatibility in {None, "compatible"}:
                raise ValueError("conversation RAG enable failure is inconsistent")
            return
        if self.provider is None:
            if self.compatibility is not None or self.automatic not in {None, False}:
                raise ValueError("conversation RAG operation failure is inconsistent")
            return
        if type(self.automatic) is not bool or self.compatibility in {
            None,
            "compatible",
        }:
            raise ValueError("conversation RAG operation failure is inconsistent")

    def _validate_status_report(self) -> None:
        if (
            self.state is None
            or type(self.automatic) is not bool
            or self.compatibility is None
            or self.compatibility
            == ConversationRagCompositionFailure.PROVIDER_UNSUPPORTED.value
            or self.configuration != "not_applicable"
            or self.authoritative_checkpoint is None
            or self.rebuild_required is None
            or self.purged_generations != 0
            or self.purged_orphan_generations != 0
            or self.retrieval_outcome is not None
            or self.retrieval_reason is not None
            or self.truncated is not None
            or self.excerpts
        ):
            raise ValueError("conversation RAG status report is inconsistent")
        if (
            self.operation is ConversationRagCliOperation.REBUILD
            and self.provider != "local"
        ):
            raise ValueError("conversation RAG rebuild report requires local provider")
        if self.provider is None:
            if self.state is not ConversationRagCliState.DISABLED:
                raise ValueError("disabled conversation RAG status is inconsistent")
        elif self.compatibility == ConversationRagCompositionFailure.DEPENDENCY_MISSING:
            if self.state is not ConversationRagCliState.UNAVAILABLE:
                raise ValueError("unavailable conversation RAG status is inconsistent")
        elif self.compatibility != "compatible":
            if self.state is not ConversationRagCliState.INCOMPATIBLE:
                raise ValueError("incompatible conversation RAG status is inconsistent")
        elif self.state in {
            ConversationRagCliState.DISABLED,
            ConversationRagCliState.UNAVAILABLE,
        }:
            raise ValueError("compatible conversation RAG status is inconsistent")
        if (
            self.provider == "local"
            and self.compatibility == "compatible"
            and self.rebuild_required
            != (self.state is not ConversationRagCliState.READY)
        ):
            raise ValueError("conversation RAG rebuild status is inconsistent")

    def _validate_search_report(self) -> None:
        if (
            self.state is None
            or self.provider != "local"
            or type(self.automatic) is not bool
            or self.compatibility != "compatible"
            or self.configuration != "not_applicable"
            or self.authoritative_checkpoint is not None
            or self.indexed_checkpoint is not None
            or self.rebuild_required is not None
            or self.counts != ConversationRagCliCounts()
            or self.purged_generations != 0
            or self.purged_orphan_generations != 0
            or self.retrieval_outcome is None
            or self.retrieval_reason is None
            or self.truncated is None
        ):
            raise ValueError("conversation RAG search report is inconsistent")
        try:
            attempt = ConversationRagAttemptOutcome(
                outcome=ConversationRagOutcome(self.retrieval_outcome),
                reason=ConversationRagReason(self.retrieval_reason),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("conversation RAG retrieval result is invalid") from exc
        if self.state is not _retrieval_state_from_attempt(attempt):
            raise ValueError("conversation RAG retrieval state is inconsistent")
        if self.truncated and attempt.outcome is ConversationRagOutcome.COMPLETE:
            raise ValueError("truncated conversation RAG search cannot be complete")
        if len(self.excerpts) > CONVERSATION_RAG_POLICY.max_candidates:
            raise ValueError("conversation RAG search exceeds the excerpt limit")
        if tuple(excerpt.citation_id for excerpt in self.excerpts) != tuple(
            f"g{ordinal}" for ordinal in range(1, len(self.excerpts) + 1)
        ):
            raise ValueError("conversation RAG search citations are inconsistent")
        references = tuple(
            (
                excerpt.session_id,
                excerpt.conversation_id,
                excerpt.document_id,
                excerpt.revision_id,
                excerpt.fragment_id,
                excerpt.start_codepoint,
                excerpt.end_codepoint,
            )
            for excerpt in self.excerpts
        )
        if len(references) != len(set(references)):
            raise ValueError("conversation RAG search references are duplicated")
        if len({excerpt.session_id for excerpt in self.excerpts}) > 1:
            raise ValueError("conversation RAG search excerpts crossed their session")
        retained_bytes = sum(
            len(excerpt.content.encode("utf-8", errors="strict"))
            for excerpt in self.excerpts
        )
        if retained_bytes > CONVERSATION_RAG_RUNTIME_POLICY.max_context_utf8_bytes:
            raise ValueError("conversation RAG search exceeds the UTF-8 byte budget")

    def _validate_purge_report(self) -> None:
        if (
            self.state is not ConversationRagCliState.MISSING
            or self.provider is not None
            or self.automatic is not None
            or self.compatibility is not None
            or self.configuration != "not_applicable"
            or self.authoritative_checkpoint is not None
            or self.indexed_checkpoint is not None
            or self.rebuild_required is not True
            or self.counts != ConversationRagCliCounts()
            or self.retrieval_outcome is not None
            or self.retrieval_reason is not None
            or self.truncated is not None
            or self.excerpts
        ):
            raise ValueError("conversation RAG purge report is inconsistent")

    @classmethod
    def failed(
        cls,
        operation: ConversationRagCliOperation,
        failure: ConversationRagCliFailure,
        *,
        provider: str | None = None,
        automatic: bool | None = None,
        compatibility: str | None = None,
        configuration: str | None = None,
    ) -> ConversationRagCliReport:
        if configuration is None:
            configuration = (
                "preserved"
                if operation
                in {
                    ConversationRagCliOperation.ENABLE,
                    ConversationRagCliOperation.DISABLE,
                }
                else "not_applicable"
            )
        return cls(
            operation=operation,
            outcome="failed",
            provider=provider,
            automatic=automatic,
            compatibility=compatibility,
            configuration=configuration,
            failure=failure,
        )

    @property
    def exit_code(self) -> int:
        return 0 if self.outcome == "completed" else 2

    def to_dict(self) -> dict[str, object]:
        return {
            "automatic": self.automatic,
            "authoritative_checkpoint": self.authoritative_checkpoint,
            "compatibility": self.compatibility,
            "configuration": self.configuration,
            "counts": self.counts.to_dict(),
            "excerpts": [excerpt.to_dict() for excerpt in self.excerpts],
            "failure": None if self.failure is None else self.failure.value,
            "indexed_checkpoint": self.indexed_checkpoint,
            "operation": self.operation.value,
            "outcome": self.outcome,
            "provider": self.provider,
            "purged": {
                "generations": self.purged_generations,
                "orphan_generations": self.purged_orphan_generations,
            },
            "rebuild_required": self.rebuild_required,
            "retrieval_outcome": self.retrieval_outcome,
            "retrieval_reason": self.retrieval_reason,
            "schema_version": self.schema_version,
            "state": None if self.state is None else self.state.value,
            "truncated": self.truncated,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def _compatibility_value(
    failure: ConversationRagCompositionFailure | None,
) -> str:
    return "compatible" if failure is None else failure.value


def _composition_failure(
    failure: ConversationRagCompositionFailure,
) -> ConversationRagCliFailure:
    return ConversationRagCliFailure(failure.value)


def _store_failure(failure: ConversationRagStoreFailure) -> ConversationRagCliFailure:
    return {
        ConversationRagStoreFailure.UNSUPPORTED: ConversationRagCliFailure.STORAGE_UNAVAILABLE,
        ConversationRagStoreFailure.BUSY: ConversationRagCliFailure.COORDINATION_FAILED,
        ConversationRagStoreFailure.UNSAFE: ConversationRagCliFailure.UNSAFE,
        ConversationRagStoreFailure.CORRUPT: ConversationRagCliFailure.CORRUPT,
        ConversationRagStoreFailure.INCOMPATIBLE: ConversationRagCliFailure.INCOMPATIBLE,
        ConversationRagStoreFailure.PROVIDER_FAILED: ConversationRagCliFailure.PROVIDER_FAILED,
        ConversationRagStoreFailure.CONFIRMATION_REQUIRED: ConversationRagCliFailure.CONFIRMATION_REQUIRED,
        ConversationRagStoreFailure.OPERATION_FAILED: ConversationRagCliFailure.OPERATION_FAILED,
    }[failure]


def _configuration_failure(
    failure: ConfigurationCommitFailure,
) -> ConversationRagCliFailure:
    return {
        ConfigurationCommitFailure.VALIDATION: ConversationRagCliFailure.CONFIGURATION_INVALID,
        ConfigurationCommitFailure.CONFLICT: ConversationRagCliFailure.CONFIGURATION_CONFLICT,
        ConfigurationCommitFailure.WRITE: ConversationRagCliFailure.CONFIGURATION_WRITE_FAILED,
        ConfigurationCommitFailure.ROLLBACK: ConversationRagCliFailure.CONFIGURATION_WRITE_FAILED,
    }[failure]


def _store_snapshot(
    state: LocalConversationRagRuntimeState,
    session_id: str,
) -> ConversationRagStoreSnapshot:
    return state.store.inspect(
        session_id,
        LOCAL_CONVERSATION_RAG_PROVIDER_ID,
        state.embedding,
        expected_projection_schema_id=CONVERSATION_RAG_RUNTIME_SCHEMA_ID,
    )


def _report_counts(
    inspection: ConversationRagRuntimeInspection,
    counts: ConversationRagStoreCounts,
) -> ConversationRagCliCounts:
    return ConversationRagCliCounts(
        authoritative_conversations=inspection.authoritative_conversations,
        authoritative_events=inspection.authoritative_events,
        authoritative_documents=inspection.authoritative_documents,
        authoritative_source_utf8_bytes=inspection.authoritative_source_utf8_bytes,
        indexed_conversations=counts.conversations,
        indexed_events=counts.events,
        indexed_documents=counts.documents,
        indexed_fragments=counts.fragments,
        indexed_source_utf8_bytes=counts.source_utf8_bytes,
        indexed_payload_bytes=counts.payload_bytes,
        indexed_generation_bytes=counts.generation_bytes,
        indexed_generations=counts.generations,
        indexed_orphan_generations=counts.orphan_generations,
    )


def _status_state(
    provider: str | None,
    compatibility: ConversationRagCompositionFailure | None,
    inspection: ConversationRagRuntimeInspection,
) -> ConversationRagCliState:
    if provider is None:
        return ConversationRagCliState.DISABLED
    if compatibility is ConversationRagCompositionFailure.DEPENDENCY_MISSING:
        return ConversationRagCliState.UNAVAILABLE
    if compatibility is not None:
        return ConversationRagCliState.INCOMPATIBLE
    return ConversationRagCliState(inspection.status.value)


def _status_report(
    workspace: Workspace,
    services: ConversationStorageServices,
    session_id: str,
    *,
    operation: ConversationRagCliOperation = ConversationRagCliOperation.STATUS,
) -> ConversationRagCliReport:
    manager = ConfigManager(workspace)
    configuration = manager.load_configuration_snapshot()
    defaults = configuration.system_defaults
    compatibility = inspect_conversation_rag_compatibility("local")
    store = ConversationRagGenerationStore(workspace)
    state = LocalConversationRagRuntimeState(store)
    store_before = _store_snapshot(state, session_id)
    inspection = inspect_authorized_conversation_rag(services, state, session_id)
    store_after = _store_snapshot(state, session_id)
    configuration_after = manager.load_configuration_snapshot()
    if store_before != store_after or configuration != configuration_after:
        raise RuntimeError("conversation RAG store changed while inspected")
    return ConversationRagCliReport(
        operation=operation,
        outcome="completed",
        state=_status_state(
            defaults.conversation_rag_provider, compatibility, inspection
        ),
        provider=defaults.conversation_rag_provider,
        automatic=defaults.conversation_rag_automatic,
        compatibility=_compatibility_value(compatibility),
        authoritative_checkpoint=inspection.authoritative_checkpoint,
        indexed_checkpoint=inspection.indexed_checkpoint,
        rebuild_required=inspection.rebuild_required,
        counts=_report_counts(inspection, store_after.counts),
    )


def _dispatch_enable(
    workspace: Workspace,
    request: ConversationRagCliRequest,
) -> ConversationRagCliReport:
    compatibility = inspect_conversation_rag_compatibility(request.provider)
    if compatibility is not None:
        return ConversationRagCliReport.failed(
            request.operation,
            _composition_failure(compatibility),
            provider=(request.provider if request.provider == "local" else None),
            compatibility=_compatibility_value(compatibility),
        )
    provider = ConfigManager.validate_conversation_rag_provider(request.provider)
    assert provider is not None
    manager = ConfigManager(workspace)
    baseline = manager.load_configuration_baseline()
    current = baseline.configuration
    candidate = ConfigurationSnapshot(
        replace(current.system_defaults, conversation_rag_provider=provider),
        current.agent_configs,
    )
    changed = manager.commit_configuration(baseline, candidate)
    return ConversationRagCliReport(
        operation=request.operation,
        outcome="completed",
        provider=provider,
        automatic=candidate.system_defaults.conversation_rag_automatic,
        compatibility="compatible",
        configuration="saved" if changed else "unchanged",
    )


def _dispatch_disable(
    workspace: Workspace,
    request: ConversationRagCliRequest,
) -> ConversationRagCliReport:
    manager = ConfigManager(workspace)
    baseline = manager.load_configuration_baseline()
    current = baseline.configuration
    candidate = ConfigurationSnapshot(
        replace(
            current.system_defaults,
            conversation_rag_provider=None,
            conversation_rag_automatic=False,
        ),
        current.agent_configs,
    )
    changed = manager.commit_configuration(baseline, candidate)
    return ConversationRagCliReport(
        operation=request.operation,
        outcome="completed",
        state=ConversationRagCliState.DISABLED,
        provider=None,
        automatic=False,
        configuration="saved" if changed else "unchanged",
    )


def _load_enabled_configuration(
    workspace: Workspace,
    operation: ConversationRagCliOperation,
) -> tuple[ConfigurationSnapshot | None, ConversationRagCliReport | None]:
    configuration = ConfigManager(workspace).load_configuration_snapshot()
    defaults = configuration.system_defaults
    if defaults.conversation_rag_provider is None:
        return None, ConversationRagCliReport.failed(
            operation,
            ConversationRagCliFailure.PROVIDER_DISABLED,
            automatic=defaults.conversation_rag_automatic,
        )
    compatibility = inspect_conversation_rag_compatibility(
        defaults.conversation_rag_provider
    )
    if compatibility is not None:
        return None, ConversationRagCliReport.failed(
            operation,
            _composition_failure(compatibility),
            provider=defaults.conversation_rag_provider,
            automatic=defaults.conversation_rag_automatic,
            compatibility=_compatibility_value(compatibility),
        )
    return configuration, None


def _retrieval_state_from_attempt(
    attempt: ConversationRagAttemptOutcome,
) -> ConversationRagCliState:
    if type(attempt) is not ConversationRagAttemptOutcome:
        raise TypeError("conversation RAG retrieval attempt is invalid")
    if attempt.reason in {
        ConversationRagReason.NONE,
        ConversationRagReason.PARTIAL,
    }:
        return ConversationRagCliState.READY
    return {
        ConversationRagReason.DISABLED: ConversationRagCliState.DISABLED,
        ConversationRagReason.UNAVAILABLE: ConversationRagCliState.MISSING,
        ConversationRagReason.STALE: ConversationRagCliState.STALE,
        ConversationRagReason.FAILED: ConversationRagCliState.FAILED,
        ConversationRagReason.CORRUPT: ConversationRagCliState.CORRUPT,
        ConversationRagReason.INCOMPATIBLE: ConversationRagCliState.INCOMPATIBLE,
    }[attempt.reason]


def _retrieval_state(context: ConversationRagContext) -> ConversationRagCliState:
    return _retrieval_state_from_attempt(context.outcome)


def _dispatch_rebuild(
    workspace: Workspace,
    request: ConversationRagCliRequest,
    services: ConversationStorageServices | None,
) -> ConversationRagCliReport:
    if not request.confirmed:
        return ConversationRagCliReport.failed(
            request.operation,
            ConversationRagCliFailure.CONFIRMATION_REQUIRED,
        )
    if services is None:
        return ConversationRagCliReport.failed(
            request.operation,
            ConversationRagCliFailure.INVALID_REQUEST,
        )
    configuration, failure = _load_enabled_configuration(workspace, request.operation)
    if failure is not None:
        return failure
    assert configuration is not None
    defaults = configuration.system_defaults
    composition = compose_local_conversation_rag_runtime(
        workspace,
        automatic_enabled=defaults.conversation_rag_automatic,
    )
    composition.service.project_authorized(
        services,
        request.session_id,  # type: ignore[arg-type]
        rebuild=True,
    )
    return _status_report(
        workspace,
        services,
        request.session_id,  # type: ignore[arg-type]
        operation=request.operation,
    )


def _dispatch_search(
    workspace: Workspace,
    request: ConversationRagCliRequest,
    services: ConversationStorageServices | None,
) -> ConversationRagCliReport:
    if services is None:
        return ConversationRagCliReport.failed(
            request.operation,
            ConversationRagCliFailure.INVALID_REQUEST,
        )
    configuration, failure = _load_enabled_configuration(workspace, request.operation)
    if failure is not None:
        return failure
    assert configuration is not None
    defaults = configuration.system_defaults
    composition = compose_local_conversation_rag_runtime(
        workspace,
        automatic_enabled=defaults.conversation_rag_automatic,
    )
    context = composition.service.search_authorized(
        services,
        request.session_id,  # type: ignore[arg-type]
        request.query,  # type: ignore[arg-type]
    )
    return ConversationRagCliReport(
        operation=request.operation,
        outcome="completed",
        state=_retrieval_state(context),
        provider=defaults.conversation_rag_provider,
        automatic=defaults.conversation_rag_automatic,
        compatibility="compatible",
        retrieval_outcome=context.outcome.outcome.value,
        retrieval_reason=context.outcome.reason.value,
        truncated=context.truncated,
        excerpts=tuple(
            ConversationRagCliExcerpt.from_excerpt(excerpt)
            for excerpt in context.excerpts
        ),
    )


def _dispatch_purge(
    workspace: Workspace,
    request: ConversationRagCliRequest,
) -> ConversationRagCliReport:
    counts = ConversationRagGenerationStore(workspace).purge(
        request.session_id,  # type: ignore[arg-type]
        confirmed=request.confirmed,
    )
    return ConversationRagCliReport(
        operation=request.operation,
        outcome="completed",
        state=ConversationRagCliState.MISSING,
        rebuild_required=True,
        purged_generations=counts.generations,
        purged_orphan_generations=counts.orphan_generations,
    )


def dispatch_conversation_rag(
    workspace: Workspace,
    request: ConversationRagCliRequest,
    /,
    *,
    conversation_storage_services: ConversationStorageServices | None = None,
) -> ConversationRagCliReport:
    """Dispatch one fixed workflow and sanitize every ordinary boundary failure."""

    if (
        not isinstance(workspace, Workspace)
        or type(request) is not ConversationRagCliRequest
    ):
        operation = (
            request.operation
            if type(request) is ConversationRagCliRequest
            else ConversationRagCliOperation.STATUS
        )
        return ConversationRagCliReport.failed(
            operation,
            ConversationRagCliFailure.INVALID_REQUEST,
        )
    if (
        conversation_storage_services is not None
        and type(conversation_storage_services) is not ConversationStorageServices
    ):
        return ConversationRagCliReport.failed(
            request.operation,
            ConversationRagCliFailure.INVALID_REQUEST,
        )
    try:
        if request.operation is ConversationRagCliOperation.STATUS:
            services = (
                conversation_storage_services
                or compose_active_conversation_storage(workspace)
            )
            return _status_report(workspace, services, request.session_id)  # type: ignore[arg-type]
        if request.operation is ConversationRagCliOperation.ENABLE:
            return _dispatch_enable(workspace, request)
        if request.operation is ConversationRagCliOperation.REBUILD:
            return _dispatch_rebuild(workspace, request, conversation_storage_services)
        if request.operation is ConversationRagCliOperation.SEARCH:
            return _dispatch_search(workspace, request, conversation_storage_services)
        if request.operation is ConversationRagCliOperation.DISABLE:
            return _dispatch_disable(workspace, request)
        return _dispatch_purge(workspace, request)
    except (KeyboardInterrupt, SystemExit):
        raise
    except ConfigurationCommitError as exc:
        return ConversationRagCliReport.failed(
            request.operation,
            _configuration_failure(exc.failure),
        )
    except (ConfigurationError, WorkspaceFormatError):
        return ConversationRagCliReport.failed(
            request.operation,
            ConversationRagCliFailure.CONFIGURATION_INVALID,
        )
    except ConversationRagCompositionError as exc:
        return ConversationRagCliReport.failed(
            request.operation,
            _composition_failure(exc.failure),
        )
    except ConversationStorageCoordinationError:
        return ConversationRagCliReport.failed(
            request.operation,
            ConversationRagCliFailure.COORDINATION_FAILED,
        )
    except ConversationStorageError:
        return ConversationRagCliReport.failed(
            request.operation,
            ConversationRagCliFailure.STORAGE_UNAVAILABLE,
        )
    except ConversationRagStoreError as exc:
        return ConversationRagCliReport.failed(
            request.operation,
            _store_failure(exc.failure),
        )
    except Exception:
        return ConversationRagCliReport.failed(
            request.operation,
            ConversationRagCliFailure.OPERATION_FAILED,
        )


__all__ = [
    "CONVERSATION_RAG_CLI_SCHEMA_VERSION",
    "ConversationRagCliFailure",
    "ConversationRagCliOperation",
    "ConversationRagCliReport",
    "ConversationRagCliRequest",
    "ConversationRagCliState",
    "dispatch_conversation_rag",
]
