"""Call-local state machine for bounded agent-directed context operations."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import inspect
import json
import math
import secrets
import threading
from ..structured_output_validation import (
    validation_failure,
    validation_call,
    carry_validation,
    failure_issues,
    remap_validation,
    encoding_failure,
)
from contextlib import AsyncExitStack
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import AsyncContextManager, Protocol, cast, runtime_checkable

from ..errors import ProviderAuthenticationRequiredError
from ..structured_output_validation import (
    DomainValidationRule, bounded_chunks, diagnose_json_text, domain_rule_chunks,
)
from ..provider_auth_recovery import authentication_replay, request_authentication
from ..provider_execution_workspace import ProviderExecutionWorkspace
from ..workspace import Workspace
from ..conversations.session_context import (
    MAX_SESSION_CONTEXT_UTF8_BYTES,
    SessionContextCapacityError,
    SessionContextHistoryChangedError,
    SessionHistorySnapshot,
)
from .continuation import ContextContinuationTransaction
from .accounting import (
    ContextComponentKind,
    JsonPath,
    ProviderCallAccounting,
    ProviderCallKind,
    ProviderCallState,
)

from .actions import (
    CONTEXT_ACTION_POLICY,
    ContextActionCorrelation,
    ContextActionError,
    ContextActionErrorCode,
    ContextActionPolicy,
    ContextAnswerAction,
    ContextAnswerAuthorization,
    ContextEvidenceReceipt,
    ContextReadAction,
    ContextReadObservation,
    ContextSearchAction,
    ContextSearchHit,
    ContextSearchObservation,
    ContextSearchQuery,
    ContextSourceBoundary,
    ContextSourceBoundaryKind,
    ContextSourceEvidence,
    ContextSourceHandle,
    ContextSourceStatusReceipt,
    ContextZeroHitSearchReceipt,
    serialize_context_action_observation,
)
from .manifest import (
    ContextContinuationIdentity,
    ContextManifest,
    ContextManifestEntry,
    ContextManifestError,
    ContextPriority,
    ContextScope,
    ContextSemanticChannel,
    ContextSourceKind,
    ContextSubjectKind,
    validate_context_digest,
    validate_context_identity,
)
from .planning import (
    DEFAULT_CONTEXT_PLANNING_POLICY,
    MAX_MANDATORY_REFERENCE_RESERVATION_UTF8_BYTES,
    ContextCheckpointState,
    ContextCatalogCursor,
    ContextCheckpointRecord,
    ContextCheckpointSource,
    ContextCheckpointView,
    ContextDeliveryMode,
    ContextMaterial,
    ContextRecordCursor,
    ContextRecordMaterial,
    MandatoryReferenceReservation,
    ContextPlanningConditions,
    ContextPlanningPolicy,
    ContextProjection,
    ContextProjectionAccounting,
    context_source_uses_catalog_alias,
    plan_context_projection,
)
from .progress import (
    ContextActionProgressEvent,
    ContextActionProgressKind,
    ContextActionProgressQuery,
    ContextActionProgressSink,
    ContextActionProgressSource,
)
from .ledger import (
    MAX_CONTEXT_LEDGER_SOURCES,
    ContextLedger,
    ContextLedgerAttemptOutcome,
    ContextLedgerAttemptPlan,
    ContextLedgerCommitAuthorization,
    ContextLedgerKey,
    ContextLedgerProviderCall,
    context_checkpoint_proposal_digest,
)
from .request_compaction import compact_context_payload
from .sources import (
    CONTEXT_SOURCE_POLICY,
    ActiveDebateSourceAdapter,
    ContextSourceError,
    ContextSourceErrorCode,
    ContextSourceDescriptor,
    ContextSourcePolicy,
    ContextSourceRegistry,
    ContextSourceSnapshot,
    FocusedConversationSourceAdapter,
    OwnAgentStateSourceAdapter,
    WritingArtifactSourceAdapter,
)

__all__ = [
    "AgentContextService",
    "ContextExecutionError",
    "ContextExecutionErrorCode",
    "ContextExecutionEvent",
    "ContextExecutionEventKind",
    "ContextExecutionEventSink",
    "ContextInvocationBinding",
    "ContextInvocationKind",
    "ContextInvocationRequest",
    "ContextInvocationResult",
    "ContextInvocationSession",
    "ContextInvocationState",
    "ContextJsonObjectResultContract",
    "ContextProvider",
    "ContextProviderCallFinalizer",
    "ContextProviderCallRequest",
    "ContextProviderCallResult",
    "ContextProviderFailureFacts",
    "ContextContinuationRejection",
    "ContextProviderTerminalCode",
    "ContextReferencePreludeRequest",
    "ContextReferencePreludeResult",
    "ContextReadReceipt",
    "ContextScope",
    "ContextSourceClassification",
    "context_provider_failure_facts",
    "record_context_provider_failure",
]

_CONTEXT_WIRE_VERSION = "agent-context-wire-v8"
_ACTION_WIRE_CODES = {
    "search": "s",
    "read": "r",
    "answer": "a",
}
_SOURCE_KIND_WIRE_CODES = {
    ContextSourceKind.WRITING_ARTIFACT: "w",
    ContextSourceKind.CONVERSATION: "c",
    ContextSourceKind.ACTIVE_DEBATE: "d",
    ContextSourceKind.OWN_AGENT_STATE: "o",
}
_SEARCH_SOURCE_KIND_ORDER = tuple(_SOURCE_KIND_WIRE_CODES)
_SEMANTIC_CHANNEL_WIRE_CODES = {
    ContextSemanticChannel.CURRENT_INPUT: "i",
    ContextSemanticChannel.CANDIDATE: "k",
    ContextSemanticChannel.STABLE_BEHAVIOR: "s",
    ContextSemanticChannel.HARD_CONSTRAINT: "h",
    ContextSemanticChannel.FOCUSED_EVIDENCE: "f",
    ContextSemanticChannel.RETRIEVED_EVIDENCE: "r",
    ContextSemanticChannel.HISTORY: "y",
    ContextSemanticChannel.OPTIONAL_BACKGROUND: "b",
}
_ACTION_POLICY_FINGERPRINT_FIELDS = (
    "max_nonterminal_actions",
    "max_provider_calls",
    "max_search_actions",
    "max_read_actions",
    "max_queries_per_search",
    "max_cumulative_queries",
    "max_handles_per_read",
    "max_cumulative_handles",
    "max_hits_per_query",
    "max_hits_per_search",
    "max_cumulative_hits",
    "max_hits_per_source_query",
    "max_query_characters",
    "max_snippet_characters",
    "max_snippet_utf8_bytes",
    "max_excerpt_utf8_bytes",
    "max_source_read_utf8_bytes",
    "max_read_utf8_bytes",
    "max_observation_utf8_bytes",
)
_SOURCE_POLICY_FINGERPRINT_FIELDS = (
    "max_inventory_sources",
    "max_traversal_entries",
    "max_records_per_source",
    "max_source_utf8_bytes",
    "max_inventory_body_utf8_bytes",
    "max_source_scan_utf8_bytes",
    "max_cumulative_scan_utf8_bytes",
)
_STRUCTURED_WIRE_SOURCE_KINDS = {
    ContextSourceKind.RUNTIME_INPUT,
    ContextSourceKind.CONVERSATION,
    ContextSourceKind.ACTIVE_DEBATE,
    ContextSourceKind.OWN_AGENT_STATE,
}


class ContextInvocationState(StrEnum):
    """Observable lifecycle of one call-local context action session."""

    OPEN = "open"
    SEARCHING = "searching"
    READING = "reading"
    ANSWER_AUTHORIZED = "answer_authorized"
    FAILED = "failed"


class ContextInvocationKind(StrEnum):
    """Optional provider-neutral semantic phase for a contextual invocation."""

    LEAD = "lead"
    SPECIALIST_ASSIGNMENT = "specialist_assignment"
    LEAD_SYNTHESIS = "lead_synthesis"
    SPECIALIST_ADVISORY = "specialist_advisory"


@dataclass(frozen=True, slots=True)
class ContextJsonObjectResultContract:
    """Carry one immutable owner-supplied JSON-object result schema."""

    contract_id: str
    schema: Mapping[str, object] = field(repr=False, compare=False)
    _schema_bytes: bytes = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _safe_identity(self.contract_id)
        encoded = _validated_result_schema_bytes(self.schema)
        decoded = json.loads(encoded)
        assert type(decoded) is dict
        object.__setattr__(self, "schema", MappingProxyType(decoded))
        object.__setattr__(self, "_schema_bytes", encoded)

    def schema_value(self) -> dict[str, object]:
        """Return a detached exact schema for one physical provider call."""

        decoded = json.loads(self._schema_bytes)
        assert type(decoded) is dict
        return decoded


class ContextExecutionEventKind(StrEnum):
    """Content-free lifecycle facts emitted by contextual execution."""

    BOOTSTRAP = "bootstrap"
    DELTA = "delta"
    OMISSION = "omission"
    SEARCH_STARTED = "search_started"
    SEARCH_COMPLETED = "search_completed"
    READ_STARTED = "read_started"
    READ_COMPLETED = "read_completed"
    ANSWER_AUTHORIZED = "answer_authorized"
    INVALIDATED = "invalidated"
    BUDGET_EXHAUSTED = "budget_exhausted"
    ACTION_EXHAUSTED = "action_exhausted"
    TAINTED = "tainted"
    CHECKPOINT_ADVANCED = "checkpoint_advanced"
    CHECKPOINT_NOT_ADVANCED = "checkpoint_not_advanced"


@dataclass(frozen=True, slots=True)
class ContextExecutionEvent:
    """One typed lifecycle event containing exact content-free facts only."""

    kind: ContextExecutionEventKind
    scope: ContextScope
    operation_id: str
    phase: str | None = None
    round_number: int | None = None
    action_index: int | None = None
    selected_source_count: int | None = None
    selected_source_utf8_bytes: int | None = None
    omitted_searchable_source_count: int | None = None
    omitted_searchable_source_utf8_bytes: int | None = None
    search_hit_count: int | None = None
    retrieved_source_count: int | None = None
    retrieved_source_utf8_bytes: int | None = None
    mandatory_reference_count: int | None = None
    mandatory_reference_utf8_bytes: int | None = None
    provider_input_tokens: int | None = None
    history_record_count: int | None = None
    observation_utf8_bytes: int | None = None
    truncated: bool | None = None

    def __post_init__(self) -> None:
        if (
            type(self.kind) is not ContextExecutionEventKind
            or type(self.scope) is not ContextScope
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        _safe_identity(self.operation_id)
        if self.scope.subject_kind is ContextSubjectKind.DEBATE:
            if self.phase is None or self.round_number is None:
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            _safe_identity(self.phase)
            if type(self.round_number) is not int or self.round_number < 0:
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        elif self.phase is not None or self.round_number is not None:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.action_index is not None and (
            type(self.action_index) is not int or self.action_index < 1
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        for value in (
            self.selected_source_count,
            self.selected_source_utf8_bytes,
            self.omitted_searchable_source_count,
            self.omitted_searchable_source_utf8_bytes,
            self.search_hit_count,
            self.retrieved_source_count,
            self.retrieved_source_utf8_bytes,
            self.mandatory_reference_count,
            self.mandatory_reference_utf8_bytes,
            self.provider_input_tokens,
            self.history_record_count,
            self.observation_utf8_bytes,
        ):
            if value is not None and (type(value) is not int or value < 0):
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.truncated is not None and type(self.truncated) is not bool:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        plan_facts = (
            self.selected_source_count,
            self.selected_source_utf8_bytes,
            self.omitted_searchable_source_count,
            self.omitted_searchable_source_utf8_bytes,
            self.mandatory_reference_count,
            self.mandatory_reference_utf8_bytes,
        )
        read_facts = (
            self.retrieved_source_count,
            self.retrieved_source_utf8_bytes,
            self.history_record_count,
            self.observation_utf8_bytes,
        )
        all_optional_facts = (
            *plan_facts,
            self.search_hit_count,
            *read_facts,
            self.provider_input_tokens,
            self.truncated,
        )
        if self.kind in {
            ContextExecutionEventKind.BOOTSTRAP,
            ContextExecutionEventKind.DELTA,
        }:
            if any(value is None for value in plan_facts) or any(
                value is not None
                for value in (
                    self.action_index,
                    self.search_hit_count,
                    *read_facts,
                    self.provider_input_tokens,
                    self.truncated,
                )
            ):
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            return
        if self.kind is ContextExecutionEventKind.OMISSION:
            if (
                self.omitted_searchable_source_count is None
                or self.omitted_searchable_source_count < 1
                or self.omitted_searchable_source_utf8_bytes is None
                or any(
                    value is not None
                    for value in (
                        self.action_index,
                        self.selected_source_count,
                        self.selected_source_utf8_bytes,
                        self.search_hit_count,
                        *read_facts,
                        self.mandatory_reference_count,
                        self.mandatory_reference_utf8_bytes,
                        self.provider_input_tokens,
                        self.truncated,
                    )
                )
            ):
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            return
        if self.kind in {
            ContextExecutionEventKind.SEARCH_STARTED,
            ContextExecutionEventKind.READ_STARTED,
            ContextExecutionEventKind.ANSWER_AUTHORIZED,
        }:
            if self.action_index is None or any(
                value is not None for value in all_optional_facts
            ):
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            return
        if self.kind is ContextExecutionEventKind.SEARCH_COMPLETED:
            if (
                self.action_index is None
                or self.search_hit_count is None
                or self.observation_utf8_bytes is None
                or self.truncated is None
                or any(
                    value is not None
                    for value in (
                        *plan_facts,
                        self.retrieved_source_count,
                        self.retrieved_source_utf8_bytes,
                        self.provider_input_tokens,
                        self.history_record_count,
                    )
                )
            ):
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            return
        if self.kind is ContextExecutionEventKind.READ_COMPLETED:
            if (
                self.action_index is None
                or any(value is None for value in read_facts)
                or any(
                    value is not None
                    for value in (
                        *plan_facts,
                        self.search_hit_count,
                        self.provider_input_tokens,
                        self.truncated,
                    )
                )
            ):
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            return
        if self.kind in {
            ContextExecutionEventKind.CHECKPOINT_ADVANCED,
            ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED,
        }:
            if self.action_index is not None or any(
                value is not None
                for value in (
                    *plan_facts,
                    self.search_hit_count,
                    *read_facts,
                    self.truncated,
                )
            ):
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            return
        if self.action_index is not None or any(
            value is not None for value in all_optional_facts
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)


ContextExecutionEventSink = Callable[[ContextExecutionEvent], None]


def _action_error(code: ContextActionErrorCode) -> ContextActionError:
    paths_and_rules = {
        ContextActionErrorCode.CORRELATION_MISMATCH: ("$", "action correlation must match the current request"),
        ContextActionErrorCode.INVALID_TRANSITION: ("$.kind", "action must be allowed by the current phase and completed read state"),
        ContextActionErrorCode.ACTION_LIMIT_EXCEEDED: ("$.kind", "action count must remain within the disclosed invocation budget"),
        ContextActionErrorCode.SEARCH_LIMIT_EXCEEDED: ("$.queries", "search count, query count, and requested results must fit the remaining search budget"),
        ContextActionErrorCode.READ_LIMIT_EXCEEDED: ("$.handles", "read count, handle count, and source bytes must fit the remaining read budget"),
        ContextActionErrorCode.OBSERVATION_LIMIT_EXCEEDED: ("$.handles", "selected source observations must fit the remaining context budget"),
        ContextActionErrorCode.HANDLE_INVALID: ("$.handles", "expected unconsumed source handles issued by this invocation's search"),
        ContextActionErrorCode.OUTSTANDING_READ: ("$.kind", "all outstanding read observations must be delivered before another action"),
        ContextActionErrorCode.ANSWER_NOT_AUTHORIZED: ("$.result", "answer requires completed mandatory reads and current source authorization"),
    }
    failure = ContextActionError(code)
    if code in paths_and_rules:
        path, rule = paths_and_rules[code]
        validation_failure(failure, path, rule)
    return failure


def _canonical_bytes(value: object) -> bytes:
    encoded: bytes | None = None
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError):
        pass
    if encoded is None:
        raise _action_error(ContextActionErrorCode.INVALID_VALUE)
    return encoded


def _boundary_value(boundary: ContextSourceBoundary) -> tuple[object, ...]:
    return (
        boundary.kind.value,
        boundary.start,
        boundary.end,
        boundary.first_id,
        boundary.last_id,
    )


def _scope_value(scope: ContextScope) -> tuple[str, ...]:
    return (
        scope.workspace_id,
        scope.session_id,
        scope.subject_kind.value,
        scope.subject_id,
        scope.agent_id,
    )


def _entry_value(entry: ContextManifestEntry) -> tuple[object, ...]:
    return (
        entry.source_id,
        entry.source_kind.value,
        entry.semantic_channel.value,
        entry.priority.value,
        entry.ordinal,
        entry.revision,
        entry.content_digest,
        entry.provenance.authority_kind.value,
        entry.provenance.locator,
        entry.utf8_bytes,
        entry.searchable,
    )


def _manifest_fingerprint(manifest: ContextManifest) -> str:
    body = _canonical_bytes(
        [
            "context-action-manifest-v1",
            _scope_value(manifest.scope),
            *map(_entry_value, manifest.entries),
        ]
    )
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def _authorized_source_fingerprint(
    entries: tuple[ContextManifestEntry, ...],
) -> str:
    """Bind stable source identity and non-self-appending revision facts."""

    body = _canonical_bytes(
        [
            "context-zero-hit-authorized-sources-v1",
            [
                [
                    entry.source_id,
                    entry.source_kind.value,
                    (
                        None
                        if entry.source_kind is ContextSourceKind.CONVERSATION
                        else entry.revision
                    ),
                    (
                        None
                        if entry.source_kind is ContextSourceKind.CONVERSATION
                        else entry.content_digest
                    ),
                    entry.provenance.authority_kind.value,
                    entry.provenance.locator,
                    (
                        None
                        if entry.source_kind is ContextSourceKind.CONVERSATION
                        else entry.utf8_bytes
                    ),
                ]
                for entry in entries
            ],
        ]
    )
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def _digest(body: bytes) -> str:
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def _execution_policy_fingerprint(
    planning_policy: ContextPlanningPolicy,
    action_policy: ContextActionPolicy,
    source_policy: ContextSourcePolicy,
) -> str:
    """Bind durable execution state to wire and every effective policy value."""

    body = _canonical_bytes(
        [
            _CONTEXT_WIRE_VERSION,
            planning_policy.fingerprint,
            [
                [name, getattr(action_policy, name)]
                for name in _ACTION_POLICY_FINGERPRINT_FIELDS
            ],
            [
                [name, getattr(source_policy, name)]
                for name in _SOURCE_POLICY_FINGERPRINT_FIELDS
            ],
        ]
    )
    return _digest(body)


def _evidence_value(receipt: ContextEvidenceReceipt) -> tuple[object, ...]:
    return (
        receipt.evidence_id,
        receipt.handle.value,
        receipt.source_id,
        receipt.source_kind.value,
        receipt.revision,
        receipt.provenance.authority_kind.value,
        receipt.provenance.locator,
        receipt.source_digest,
        receipt.source_utf8_bytes,
        _boundary_value(receipt.boundary),
        receipt.boundary_digest,
        receipt.boundary_utf8_bytes,
        receipt.originating_search_action_index,
        receipt.completing_read_action_index,
    )


def _status_value(receipt: ContextSourceStatusReceipt) -> tuple[object, ...]:
    return (
        receipt.evidence_id,
        receipt.source_id,
        receipt.locator,
        receipt.status.value,
        receipt.ordinal,
        receipt.manifest_digest,
        receipt.revision,
        receipt.source_digest,
        receipt.source_utf8_bytes,
    )


def _zero_hit_value(receipt: ContextZeroHitSearchReceipt) -> tuple[object, ...]:
    return (
        receipt.evidence_id,
        receipt.correlation.action_index,
        receipt.manifest_digest,
        receipt.authorized_source_ids,
        tuple(item.value for item in receipt.authorized_source_kinds),
        receipt.authorized_source_fingerprint,
        receipt.artifact_path_prefix,
        receipt.query_digest,
        receipt.query_count,
    )


def _completed_evidence_digest(
    evidence: tuple[ContextEvidenceReceipt, ...],
) -> str:
    values = []
    for receipt in evidence:
        boundary = receipt.boundary
        values.append(
            {
                "boundary": {
                    "end": boundary.end,
                    "first_id": boundary.first_id,
                    "kind": boundary.kind.value,
                    "last_id": boundary.last_id,
                    "start": boundary.start,
                },
                "boundary_digest": receipt.boundary_digest,
                "boundary_utf8_bytes": receipt.boundary_utf8_bytes,
                "completing_read_action_index": (receipt.completing_read_action_index),
                "evidence_id": receipt.evidence_id,
                "handle": receipt.handle.value,
                "originating_search_action_index": (
                    receipt.originating_search_action_index
                ),
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
        )
    return _digest(
        _canonical_bytes({"evidence": values, "kind": "completed_context_reads_v1"})
    )


def _reference_value(
    reference: MandatoryReferenceReservation,
) -> tuple[object, ...]:
    return (
        reference.ordinal,
        reference.reference_id,
        reference.source_id,
        reference.revision,
        reference.provenance.authority_kind.value,
        reference.provenance.locator,
        reference.content_digest,
        reference.utf8_bytes,
    )


def _safe_identity(value: object) -> str:
    validated: str | None = None
    try:
        validated = validate_context_identity(value)
    except ContextManifestError:
        pass
    if validated is None:
        raise _action_error(ContextActionErrorCode.INVALID_VALUE)
    return validated


def _safe_digest(value: object) -> str:
    validated: str | None = None
    try:
        validated = validate_context_digest(value)
    except ContextManifestError:
        pass
    if validated is None:
        raise _action_error(ContextActionErrorCode.INVALID_VALUE)
    return validated


class ContextExecutionErrorCode(StrEnum):
    """Stable content-free failures owned by contextual orchestration."""

    INVALID_VALUE = "invalid_value"
    PROVIDER_CONTRACT = "provider_contract"
    MALFORMED_ACTION = "malformed_action"
    ACTION_EXHAUSTED = "action_exhausted"
    CONTINUATION_MISMATCH = "continuation_mismatch"


class _ContextExecutionErrorDetail(StrEnum):
    """Private allow-list for fixed, content-free execution diagnostics."""

    READ_SOURCE_HANDLES_REQUIRED = "read_source_handles_required"
    RESPONSE_CORRELATION_MISMATCH = "response_correlation_mismatch"


_EXECUTION_ERROR_MESSAGES = {
    ContextExecutionErrorCode.INVALID_VALUE: "contextual invocation is invalid",
    ContextExecutionErrorCode.PROVIDER_CONTRACT: (
        "contextual provider contract is invalid"
    ),
    ContextExecutionErrorCode.MALFORMED_ACTION: (
        "contextual provider action is malformed"
    ),
    ContextExecutionErrorCode.ACTION_EXHAUSTED: (
        "contextual provider action limit is exhausted"
    ),
    ContextExecutionErrorCode.CONTINUATION_MISMATCH: (
        "contextual provider continuation does not match"
    ),
}

_EXECUTION_ERROR_DETAIL_MESSAGES = {
    (
        ContextExecutionErrorCode.MALFORMED_ACTION,
        _ContextExecutionErrorDetail.READ_SOURCE_HANDLES_REQUIRED,
    ): (
        "contextual provider read action is invalid: `handles` must contain "
        "source handles returned by the preceding context search"
    ),
    (
        ContextExecutionErrorCode.MALFORMED_ACTION,
        _ContextExecutionErrorDetail.RESPONSE_CORRELATION_MISMATCH,
    ): (
        "contextual provider response is invalid: `correlation` does not match "
        "the current request correlation token"
    ),
}


class ContextExecutionError(ValueError):
    """Report one fixed contextual lifecycle failure without story content."""

    def __init__(self, code: ContextExecutionErrorCode) -> None:
        if type(code) is not ContextExecutionErrorCode:
            raise TypeError(
                "context execution error code must be a ContextExecutionErrorCode"
            )
        self.code = code
        super().__init__(_EXECUTION_ERROR_MESSAGES[code])


def _execution_error(
    code: ContextExecutionErrorCode,
    *,
    detail: _ContextExecutionErrorDetail | None = None,
) -> ContextExecutionError:
    failure = ContextExecutionError(code)
    if detail is None:
        return failure
    if type(detail) is not _ContextExecutionErrorDetail:
        raise TypeError("context execution error detail is invalid")
    try:
        message = _EXECUTION_ERROR_DETAIL_MESSAGES[(code, detail)]
    except KeyError:
        raise TypeError("context execution error detail is invalid") from None
    failure.args = (message,)
    failure._detail = detail
    return failure


def _execution_error_has_detail(
    failure: ContextExecutionError,
    detail: _ContextExecutionErrorDetail,
) -> bool:
    """Recognize only one internally attached, unchanged fixed detail."""

    if (
        type(failure) is not ContextExecutionError
        or type(detail) is not _ContextExecutionErrorDetail
        or getattr(failure, "_detail", None) is not detail
    ):
        return False
    try:
        expected = _EXECUTION_ERROR_DETAIL_MESSAGES[(failure.code, detail)]
    except KeyError:
        return False
    return str(failure) == expected


_PROVIDER_SCHEMA_TYPES = frozenset(
    {"array", "boolean", "integer", "null", "number", "object", "string"}
)
_PROVIDER_SCHEMA_KEYS = frozenset(
    {
        "additionalProperties",
        "anyOf",
        "description",
        "enum",
        "items",
        "maxItems",
        "minItems",
        "properties",
        "required",
        "type",
    }
)


def _schema_type_matches(value: object, schema_type: str) -> bool:
    if schema_type == "null":
        return value is None
    if schema_type == "boolean":
        return type(value) is bool
    if schema_type == "integer":
        return type(value) is int
    if schema_type == "number":
        return type(value) is int or (type(value) is float and math.isfinite(value))
    if schema_type == "string":
        return type(value) is str
    if schema_type == "array":
        return type(value) is list
    if schema_type == "object":
        return type(value) is dict
    return False


def _schema_types(schema: dict[str, object]) -> tuple[str, ...]:
    raw = schema.get("type")
    if type(raw) is str:
        values = (raw,)
    elif type(raw) is list and all(type(item) is str for item in raw):
        values = tuple(raw)
    else:
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    if (
        not values
        or len(set(values)) != len(values)
        or any(value not in _PROVIDER_SCHEMA_TYPES for value in values)
        or (
            len(values) > 1
            and (len(values) != 2 or "null" not in values)
        )
    ):
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    return values


def _validate_provider_schema_node(schema: object) -> None:
    """Validate the closed OpenAI/Gemini schema subset used by this wire."""

    if type(schema) is not dict or not schema or not set(schema).issubset(
        _PROVIDER_SCHEMA_KEYS
    ):
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    description = schema.get("description")
    if description is not None and (type(description) is not str or not description):
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    if "anyOf" in schema:
        if not set(schema).issubset({"anyOf", "description"}):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        variants = schema["anyOf"]
        if type(variants) is not list or len(variants) < 2:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        for variant in variants:
            _validate_provider_schema_node(variant)
        return
    schema_types = _schema_types(schema)
    concrete_types = tuple(item for item in schema_types if item != "null")
    if len(concrete_types) > 1:
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    concrete_type = concrete_types[0] if concrete_types else "null"
    raw_enum = schema.get("enum")
    if raw_enum is not None:
        if type(raw_enum) is not list or not raw_enum:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        for value in raw_enum:
            if not any(_schema_type_matches(value, item) for item in schema_types):
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    structural_keys = set(schema) - {"description", "enum", "type"}
    if concrete_type == "object":
        if structural_keys != {"additionalProperties", "properties", "required"}:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        properties = schema["properties"]
        required = schema["required"]
        if (
            type(properties) is not dict
            or not properties
            or any(type(key) is not str or not key for key in properties)
            or type(required) is not list
            or any(type(key) is not str for key in required)
            or len(required) != len(properties)
            or set(required) != set(properties)
            or schema["additionalProperties"] is not False
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        for child in properties.values():
            _validate_provider_schema_node(child)
        return
    if concrete_type == "array":
        if not structural_keys.issubset({"items", "maxItems", "minItems"}) or (
            "items" not in schema
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        minimum = schema.get("minItems", 0)
        maximum = schema.get("maxItems")
        if (
            type(minimum) is not int
            or minimum < 0
            or (maximum is not None and (type(maximum) is not int or maximum < minimum))
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        _validate_provider_schema_node(schema["items"])
        return
    if structural_keys:
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)


def _schema_accepts_only_objects(schema: dict[str, object]) -> bool:
    variants = schema.get("anyOf")
    if type(variants) is list:
        return bool(variants) and all(
            type(variant) is dict and _schema_accepts_only_objects(variant)
            for variant in variants
        )
    return schema.get("type") == "object"


def _validated_result_schema_bytes(schema: object) -> bytes:
    if type(schema) is not dict:
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    try:
        encoded = json.dumps(
            schema,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="strict")
        decoded = json.loads(encoded)
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE) from None
    _validate_provider_schema_node(decoded)
    if type(decoded) is not dict or not _schema_accepts_only_objects(decoded):
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    return encoded


def _value_matches_provider_schema(value: object, schema: dict[str, object]) -> bool:
    variants = schema.get("anyOf")
    if type(variants) is list:
        return any(
            type(variant) is dict and _value_matches_provider_schema(value, variant)
            for variant in variants
        )
    try:
        schema_types = _schema_types(schema)
    except ContextExecutionError:
        return False
    if not any(_schema_type_matches(value, item) for item in schema_types):
        return False
    raw_enum = schema.get("enum")
    if type(raw_enum) is list and not any(
        type(value) is type(candidate) and value == candidate for candidate in raw_enum
    ):
        return False
    if value is None:
        return True
    if type(value) is dict:
        properties = schema.get("properties")
        if type(properties) is not dict or set(value) != set(properties):
            return False
        return all(
            type(child) is dict and _value_matches_provider_schema(value[key], child)
            for key, child in properties.items()
        )
    if type(value) is list:
        items = schema.get("items")
        minimum = schema.get("minItems", 0)
        maximum = schema.get("maxItems")
        if (
            type(items) is not dict
            or type(minimum) is not int
            or len(value) < minimum
            or (type(maximum) is int and len(value) > maximum)
        ):
            return False
        return all(_value_matches_provider_schema(item, items) for item in value)
    return True


def _validate_sync_callback(value: object, *, optional: bool = True) -> None:
    if value is None and optional:
        return
    if not callable(value):
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    call = getattr(value, "__call__", None)
    if (
        inspect.iscoroutinefunction(value)
        or inspect.isasyncgenfunction(value)
        or inspect.iscoroutinefunction(call)
        or inspect.isasyncgenfunction(call)
    ):
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)


def _require_sync_none(value: object) -> None:
    if inspect.isawaitable(value):
        if inspect.iscoroutine(value):
            value.close()
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    if inspect.isasyncgen(value) or value is not None:
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)


def _emit_context_event(
    sink: ContextExecutionEventSink | None,
    event: ContextExecutionEvent | None,
) -> None:
    if sink is None:
        return
    assert event is not None
    _require_sync_none(sink(event))


def _emit_action_progress(
    sink: ContextActionProgressSink | None,
    event: ContextActionProgressEvent,
) -> None:
    if sink is not None:
        _require_sync_none(sink(event))


def _emit_context_event_without_replacing(
    sink: ContextExecutionEventSink | None,
    event: ContextExecutionEvent | None,
) -> None:
    """Best-effort emit after an authoritative outcome already exists."""

    try:
        _emit_context_event(sink, event)
    except BaseException:
        pass


def _accept_provider_call_without_replacing(
    finalizer: ContextProviderCallFinalizer,
) -> None:
    """Best-effort accept one response without changing runtime behavior."""

    try:
        callback = cast(Callable[[], object], finalizer.accepted)
        _require_sync_none(callback())
    except BaseException:
        pass


def _reject_provider_call_without_replacing(
    finalizer: ContextProviderCallFinalizer,
    code: ContextProviderTerminalCode,
) -> None:
    """Best-effort reject one response with a closed semantic code."""

    try:
        callback = cast(
            Callable[[ContextProviderTerminalCode], object],
            finalizer.rejected,
        )
        _require_sync_none(callback(code))
    except BaseException:
        pass


def _cancel_provider_call_without_replacing(
    finalizer: ContextProviderCallFinalizer,
) -> None:
    """Best-effort record cancellation after one response."""

    try:
        callback = cast(Callable[[], object], finalizer.cancelled)
        _require_sync_none(callback())
    except BaseException:
        pass


def _fail_provider_call_without_replacing(
    finalizer: ContextProviderCallFinalizer,
    code: ContextProviderTerminalCode,
) -> None:
    """Best-effort record one post-response runtime failure."""

    try:
        callback = cast(
            Callable[[ContextProviderTerminalCode], object],
            finalizer.post_response_runtime_failure,
        )
        _require_sync_none(callback(code))
    except BaseException:
        pass


def _provider_input_token_total(
    accountings: list[ProviderCallAccounting],
) -> int | None:
    """Sum exact submitted input tokens only when every call supplies them."""

    if not accountings:
        return None
    total = 0
    for accounting in accountings:
        usage = accounting.usage
        if usage is None or usage.input_tokens is None:
            return None
        total += usage.input_tokens
    return total


def _conversation_read_record_count(
    observation: ContextReadObservation,
) -> int:
    """Count readable collaboration records without retaining their bodies."""

    records = 0
    for evidence in observation.evidence:
        receipt = evidence.receipt
        if receipt.source_kind is not ContextSourceKind.CONVERSATION:
            continue
        if receipt.boundary.kind is ContextSourceBoundaryKind.RECORDS:
            assert receipt.boundary.start is not None
            assert receipt.boundary.end is not None
            records += receipt.boundary.end - receipt.boundary.start + 1
            continue
        if receipt.boundary.kind is not ContextSourceBoundaryKind.WHOLE:
            continue
        try:
            value = json.loads(evidence.content)
        except (json.JSONDecodeError, RecursionError, TypeError, UnicodeError):
            continue
        if type(value) is dict and type(value.get("records")) is list:
            records += len(value["records"])
    return records


@dataclass(frozen=True, slots=True)
class ContextSourceClassification:
    """Content-free priority/channel assignment for one inventoried source."""

    source_id: str
    semantic_channel: ContextSemanticChannel
    priority: ContextPriority
    ordinal: int
    allow_absent: bool = False

    def __post_init__(self) -> None:
        _safe_identity(self.source_id)
        if (
            type(self.semantic_channel) is not ContextSemanticChannel
            or type(self.priority) is not ContextPriority
            or self.priority
            not in {ContextPriority.P2_WORKING, ContextPriority.P3_OPTIONAL}
            or type(self.ordinal) is not int
            or self.ordinal < 0
            or type(self.allow_absent) is not bool
            or (
                self.allow_absent
                and (
                    self.priority is not ContextPriority.P2_WORKING
                    or self.semantic_channel
                    is not ContextSemanticChannel.HARD_CONSTRAINT
                )
            )
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)


@dataclass(frozen=True, slots=True)
class ContextInvocationRequest:
    """Domain facts and transient caller-owned material for one invocation."""

    session_id: str
    subject_kind: ContextSubjectKind
    subject_id: str
    agent_id: str
    operation_id: str
    result_correlation_id: str
    materials: tuple[ContextMaterial, ...]
    result_contract: ContextJsonObjectResultContract
    source_classifications: tuple[ContextSourceClassification, ...] = ()
    reference_operation_id: str | None = None
    mandatory_references: tuple[MandatoryReferenceReservation, ...] = ()
    fresh: bool = False
    allowed_search_source_kinds: tuple[ContextSourceKind, ...] = (
        _SEARCH_SOURCE_KIND_ORDER
    )
    invocation_kind: ContextInvocationKind | None = None
    physical_call_limit: int | None = None
    include_core_statuses: bool = False
    session_state_json: str | None = field(default=None, repr=False)
    execution_workspace: ProviderExecutionWorkspace | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        _safe_identity(self.session_id)
        if type(self.subject_kind) is not ContextSubjectKind:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.subject_kind is ContextSubjectKind.SESSION:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        # Reuse the canonical subject grammar without accepting workspace input.
        ContextScope(
            "workspace",
            self.session_id,
            self.subject_kind,
            self.subject_id,
            self.agent_id,
        )
        _safe_identity(self.operation_id)
        _safe_identity(self.result_correlation_id)
        if type(self.materials) is not tuple or any(
            type(item) is not ContextMaterial for item in self.materials
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if any(
            item.entry.source_kind
            not in {
                ContextSourceKind.RUNTIME_INPUT,
                ContextSourceKind.AGENT_DEFINITION,
            }
            for item in self.materials
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.source_classifications) is not tuple or any(
            type(item) is not ContextSourceClassification
            for item in self.source_classifications
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        source_ids = tuple(item.source_id for item in self.source_classifications)
        if len(set(source_ids)) != len(source_ids):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.reference_operation_id is not None:
            _safe_identity(self.reference_operation_id)
        if type(self.mandatory_references) is not tuple or any(
            type(item) is not MandatoryReferenceReservation
            for item in self.mandatory_references
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if bool(self.reference_operation_id) is not bool(self.mandatory_references):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.fresh) is not bool:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if (
            type(self.allowed_search_source_kinds) is not tuple
            or not self.allowed_search_source_kinds
            or any(
                type(kind) is not ContextSourceKind
                or kind not in _SEARCH_SOURCE_KIND_ORDER
                for kind in self.allowed_search_source_kinds
            )
            or self.allowed_search_source_kinds
            != tuple(
                kind
                for kind in _SEARCH_SOURCE_KIND_ORDER
                if kind in self.allowed_search_source_kinds
            )
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.invocation_kind is not None and type(
            self.invocation_kind
        ) is not ContextInvocationKind:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.result_contract) is not ContextJsonObjectResultContract:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.physical_call_limit is not None and (
            type(self.physical_call_limit) is not int
            or self.physical_call_limit < 1
            or self.physical_call_limit > CONTEXT_ACTION_POLICY.max_provider_calls
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.include_core_statuses) is not bool:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.session_state_json is not None:
            try:
                state = json.loads(self.session_state_json)
                if type(state) is not dict:
                    raise ValueError
                _canonical_bytes(state)
            except (TypeError, ValueError):
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE) from None
        if self.execution_workspace is not None and (
            type(self.execution_workspace) is not ProviderExecutionWorkspace
            or self.execution_workspace.session_id != self.session_id
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)


class ContextContinuationRejection(StrEnum):
    """Transport-proven rejection before execution, never inferred from text."""

    UNAVAILABLE = "continuation_unavailable"
    CAPACITY = "context_capacity"


@dataclass(frozen=True, slots=True)
class ContextProviderFailureFacts:
    """Validated physical-call facts attached without replacing a failure."""

    accounting: ProviderCallAccounting | None = None
    timed_out: bool = False
    taint_existing_thread: bool = False
    submitted_accountings: tuple[ProviderCallAccounting, ...] = ()
    submission_uncertain: bool = False
    response_rejection_code: ContextProviderTerminalCode | None = None
    continuation_rejection: ContextContinuationRejection | None = None

    def __post_init__(self) -> None:
        if self.continuation_rejection is not None and (
            type(self.continuation_rejection) is not ContextContinuationRejection
            or self.timed_out or self.submission_uncertain
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.accounting is not None and type(self.accounting) is not (
            ProviderCallAccounting
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if (
            type(self.timed_out) is not bool
            or type(self.taint_existing_thread) is not bool
            or type(self.submission_uncertain) is not bool
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.submitted_accountings) is not tuple or any(
            type(item) is not ProviderCallAccounting
            or item.state is not ProviderCallState.SUBMITTED
            for item in self.submitted_accountings
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.response_rejection_code is not None and (
            type(self.response_rejection_code) is not ContextProviderTerminalCode
            or self.response_rejection_code
            not in _CONTEXT_PROVIDER_RESPONSE_REJECTION_CODES
            or not self.submitted_accountings
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)


_EMPTY_PROVIDER_FAILURE_FACTS = ContextProviderFailureFacts()
_PROVIDER_FAILURE_FACTS_ATTRIBUTE = "_agent_context_provider_failure_facts"


def record_context_provider_failure(
    failure: BaseException,
    facts: ContextProviderFailureFacts,
) -> None:
    """Attach only validated content-free facts to the original failure."""

    if not isinstance(failure, BaseException) or type(facts) is not (
        ContextProviderFailureFacts
    ):
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    try:
        setattr(failure, _PROVIDER_FAILURE_FACTS_ATTRIBUTE, facts)
    except (AttributeError, TypeError):
        pass


def context_provider_failure_facts(
    failure: BaseException,
) -> ContextProviderFailureFacts:
    """Return validated private call facts or a prepared-free default."""

    facts = getattr(failure, _PROVIDER_FAILURE_FACTS_ATTRIBUTE, None)
    if type(facts) is not ContextProviderFailureFacts:
        return _EMPTY_PROVIDER_FAILURE_FACTS
    return facts


class ContextProviderTerminalCode(StrEnum):
    """Closed provider-neutral classifications for a completed response."""

    MALFORMED_STRUCTURED_RESPONSE = "malformed_structured_response"
    RESPONSE_CORRELATION_MISMATCH = "response_correlation_mismatch"
    RESPONSE_SEMANTIC_REJECTED = "response_semantic_rejected"
    ACTION_EXHAUSTED = "action_exhausted"
    ANSWER_VALIDATION_FAILED = "answer_validation_failed"
    REFERENCE_RUNTIME_FAILED = "reference_runtime_failed"
    CONTEXT_RUNTIME_FAILED = "context_runtime_failed"


_CONTEXT_PROVIDER_RESPONSE_REJECTION_CODES = frozenset(
    {
        ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,
        ContextProviderTerminalCode.RESPONSE_CORRELATION_MISMATCH,
        ContextProviderTerminalCode.RESPONSE_SEMANTIC_REJECTED,
        ContextProviderTerminalCode.ACTION_EXHAUSTED,
        ContextProviderTerminalCode.ANSWER_VALIDATION_FAILED,
    }
)


def _context_provider_decode_terminal_code(
    failure: ContextExecutionError,
) -> ContextProviderTerminalCode:
    """Classify one rejected decoded response without response content."""

    if _execution_error_has_detail(
        failure,
        _ContextExecutionErrorDetail.RESPONSE_CORRELATION_MISMATCH,
    ):
        return ContextProviderTerminalCode.RESPONSE_CORRELATION_MISMATCH
    return ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE


@runtime_checkable
class ContextProviderCallFinalizer(Protocol):
    """Provider-neutral exactly-once terminal callback for one raw response."""

    def accepted(self) -> None:
        """Record that the response completed its contextual operation."""

    def rejected(self, code: ContextProviderTerminalCode) -> None:
        """Record a closed semantic rejection of the response."""

    def cancelled(self) -> None:
        """Record cancellation after the response was received."""

    def post_response_runtime_failure(
        self,
        code: ContextProviderTerminalCode,
    ) -> None:
        """Record a closed runtime failure after the response was received."""


def _validate_context_provider_finalizer(value: object) -> None:
    """Require one synchronous provider-neutral finalizer implementation."""

    for name in (
        "accepted",
        "rejected",
        "cancelled",
        "post_response_runtime_failure",
    ):
        method = getattr(value, name, None)
        call = getattr(method, "__call__", None)
        if (
            not callable(method)
            or inspect.iscoroutinefunction(method)
            or inspect.isasyncgenfunction(method)
            or inspect.iscoroutinefunction(call)
            or inspect.isasyncgenfunction(call)
        ):
            raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)


@dataclass(frozen=True, slots=True)
class ContextProviderCallRequest:
    """One exact service-built substantive physical provider call."""

    scope: ContextScope
    policy_fingerprint: str
    call_id: str
    call_kind: ProviderCallKind
    schema: Mapping[str, object] = field(repr=False)
    payload: Mapping[str, object] = field(repr=False)
    component_paths: Mapping[JsonPath, ContextComponentKind] = field(
        default_factory=dict,
        repr=False,
    )
    mandatory_reference_paths: tuple[JsonPath, ...] = ()
    expected_thread_id: str | None = None
    continuation_identity: ContextContinuationIdentity | None = None
    execution_workspace: ProviderExecutionWorkspace | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if type(self.scope) is not ContextScope:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.continuation_identity is not None and (
            type(self.continuation_identity) is not ContextContinuationIdentity
            or not self.continuation_identity.accepts(self.scope)
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        _safe_digest(self.policy_fingerprint)
        _safe_identity(self.call_id)
        if type(self.call_kind) is not ProviderCallKind or self.call_kind in {
            ProviderCallKind.UNCLASSIFIED,
            ProviderCallKind.WRITING_ARTIFACT_REFERENCE_READ,
        }:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if not isinstance(self.schema, Mapping) or not isinstance(
            self.payload,
            Mapping,
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if not isinstance(self.component_paths, Mapping):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.mandatory_reference_paths) is not tuple:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.expected_thread_id is not None:
            _safe_identity(self.expected_thread_id)
        if self.execution_workspace is not None and (
            type(self.execution_workspace) is not ProviderExecutionWorkspace
            or self.execution_workspace.session_id != self.scope.session_id
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        object.__setattr__(self, "schema", MappingProxyType(dict(self.schema)))
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
        object.__setattr__(
            self,
            "component_paths",
            MappingProxyType(dict(self.component_paths)),
        )


@dataclass(frozen=True, slots=True)
class ContextProviderCallResult:
    """Raw provider response plus exact submitted physical-call accounting."""

    response_text: str = field(repr=False)
    thread_id: str
    accounting: ProviderCallAccounting
    finalizer: ContextProviderCallFinalizer = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.response_text) is not str:
            raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        _safe_identity(self.thread_id)
        if (
            type(self.accounting) is not ProviderCallAccounting
            or self.accounting.state is not ProviderCallState.SUBMITTED
        ):
            raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        _validate_context_provider_finalizer(self.finalizer)


@dataclass(frozen=True, slots=True)
class ContextReferencePreludeRequest:
    """One exact mandatory-reference request resolved locally by the backend."""

    scope: ContextScope
    policy_fingerprint: str
    call_id: str
    reference_operation_id: str
    expected_thread_id: str | None
    continuation_identity: ContextContinuationIdentity | None = None
    execution_workspace: ProviderExecutionWorkspace | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if type(self.scope) is not ContextScope:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.continuation_identity is not None and (
            type(self.continuation_identity) is not ContextContinuationIdentity
            or not self.continuation_identity.accepts(self.scope)
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        _safe_digest(self.policy_fingerprint)
        _safe_identity(self.call_id)
        _safe_identity(self.reference_operation_id)
        if self.expected_thread_id is not None:
            _safe_identity(self.expected_thread_id)
        if self.execution_workspace is not None and (
            type(self.execution_workspace) is not ProviderExecutionWorkspace
            or self.execution_workspace.session_id != self.scope.session_id
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)


@dataclass(frozen=True, slots=True)
class ContextReferencePreludeResult:
    """Transient exact reference context loaded by the runtime."""

    context: tuple[Mapping[str, object], ...] = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.context) is not tuple or any(
            not isinstance(item, Mapping) for item in self.context
        ):
            raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)


@runtime_checkable
class ContextProvider(Protocol):
    """Service-owned inversion consumed by the backend physical-call seam."""

    provider_id: str
    definition_fingerprint: str
    reference_operation_id: str | None

    def invocation(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
        *,
        continuation_identity: ContextContinuationIdentity | None = None,
        execution_workspace: ProviderExecutionWorkspace | None = None,
    ) -> AsyncContextManager[None]:
        """Retain the provider binding for one complete contextual lifecycle."""

    def inspect_context_thread(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
        *,
        execution_workspace: ProviderExecutionWorkspace | None = None,
    ) -> str | None:
        """Return one usable exact scoped continuation, if present."""

    def require_current_execution_workspace(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
    ) -> None:
        """Revalidate any execution workspace retained by the invocation."""

    async def prepare_references(
        self,
        request: ContextReferencePreludeRequest,
    ) -> ContextReferencePreludeResult:
        """Load authorized mandatory references without a provider call."""

    async def invoke_context(
        self,
        request: ContextProviderCallRequest,
    ) -> ContextProviderCallResult:
        """Execute one raw structured call without decoding context actions."""

    def complete_references(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
    ) -> None:
        """Accept the pending exact reference operation after terminal success."""

    def abort_references(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
        *,
        cancelled: bool,
        provider_failure: bool,
    ) -> None:
        """Best-effort terminate a pending reference operation."""

    def taint_context_thread(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
        *,
        execution_workspace: ProviderExecutionWorkspace | None = None,
    ) -> None:
        """Best-effort mark the exact scoped native continuation unusable."""


@dataclass(frozen=True, slots=True)
class ContextInvocationResult:
    """Accepted answer with content-free plan, authorization, and call facts."""

    answer: str = field(repr=False)
    authorization: ContextAnswerAuthorization
    projection_accounting: ContextProjectionAccounting
    provider_thread_id: str
    provider_calls: tuple[ContextLedgerProviderCall, ...]
    ledger_generation: int
    checkpoint_pending: bool = False

    def __post_init__(self) -> None:
        if type(self.answer) is not str or not self.answer:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.authorization) is not ContextAnswerAuthorization:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.projection_accounting) is not ContextProjectionAccounting:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        _safe_identity(self.provider_thread_id)
        if type(self.provider_calls) is not tuple or any(
            type(item) is not ContextLedgerProviderCall for item in self.provider_calls
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.ledger_generation) is not int or self.ledger_generation < 1:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)


@dataclass(frozen=True, slots=True)
class ContextInvocationBinding:
    """Immutable scope, correlation, manifest, and pending reference facts."""

    scope: ContextScope
    operation_id: str
    result_correlation_id: str
    manifest: ContextManifest
    reference_operation_id: str | None = None
    mandatory_references: tuple[MandatoryReferenceReservation, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.scope) is not ContextScope
            or type(self.operation_id) is not str
            or not self.operation_id
            or type(self.result_correlation_id) is not str
            or not self.result_correlation_id
            or type(self.manifest) is not ContextManifest
            or self.manifest.scope != self.scope
        ):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        _safe_identity(self.operation_id)
        _safe_identity(self.result_correlation_id)
        if self.reference_operation_id is not None:
            _safe_identity(self.reference_operation_id)
        if type(self.mandatory_references) is not tuple or any(
            type(item) is not MandatoryReferenceReservation
            for item in self.mandatory_references
        ):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        if tuple(item.ordinal for item in self.mandatory_references) != tuple(
            range(1, len(self.mandatory_references) + 1)
        ):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        if sum(item.utf8_bytes for item in self.mandatory_references) > (
            MAX_MANDATORY_REFERENCE_RESERVATION_UTF8_BYTES
        ):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        if bool(self.reference_operation_id) is not bool(self.mandatory_references):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)


@dataclass(frozen=True, slots=True)
class ContextReadReceipt:
    """Content-free receipt for one delivered ordered read observation."""

    correlation: ContextActionCorrelation
    evidence: tuple[ContextEvidenceReceipt, ...]
    observation_digest: str
    observation_utf8_bytes: int

    def __post_init__(self) -> None:
        if type(self.correlation) is not ContextActionCorrelation:
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        if (
            type(self.evidence) is not tuple
            or not self.evidence
            or any(type(item) is not ContextEvidenceReceipt for item in self.evidence)
        ):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        if len({item.handle.value for item in self.evidence}) != len(self.evidence):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        if (
            type(self.observation_digest) is not str
            or not self.observation_digest.startswith("sha256:")
            or len(self.observation_digest) != 71
            or any(
                character not in "0123456789abcdef"
                for character in self.observation_digest[7:]
            )
            or type(self.observation_utf8_bytes) is not int
            or self.observation_utf8_bytes < 0
            or self.observation_utf8_bytes
            > CONTEXT_ACTION_POLICY.max_observation_utf8_bytes
        ):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)


@dataclass(frozen=True, slots=True)
class _HandleBinding:
    handle: ContextSourceHandle
    entry: ContextManifestEntry
    boundary: ContextSourceBoundary
    origin_action_index: int
    safe_locator: str


class ContextInvocationSession:
    """Own one synchronized invocation-local action and evidence lifecycle."""

    def __init__(
        self,
        binding: ContextInvocationBinding,
        sources: ContextSourceRegistry,
        *,
        action_policy: ContextActionPolicy = CONTEXT_ACTION_POLICY,
        source_policy: ContextSourcePolicy = CONTEXT_SOURCE_POLICY,
        source_aliases: Mapping[str, str] | None = None,
        allowed_search_source_kinds: tuple[ContextSourceKind, ...] = (
            _SEARCH_SOURCE_KIND_ORDER
        ),
        physical_call_limit: int | None = None,
        core_statuses: tuple[ContextSourceStatusReceipt, ...] = (),
    ) -> None:
        if (
            type(binding) is not ContextInvocationBinding
            or type(sources) is not ContextSourceRegistry
            or sources.scope != binding.scope
            or type(action_policy) is not ContextActionPolicy
            or type(source_policy) is not ContextSourcePolicy
            or type(allowed_search_source_kinds) is not tuple
            or not allowed_search_source_kinds
            or any(
                type(kind) is not ContextSourceKind
                or kind not in _SEARCH_SOURCE_KIND_ORDER
                for kind in allowed_search_source_kinds
            )
            or allowed_search_source_kinds
            != tuple(
                kind
                for kind in _SEARCH_SOURCE_KIND_ORDER
                if kind in allowed_search_source_kinds
            )
            or (
                physical_call_limit is not None
                and (
                    type(physical_call_limit) is not int
                    or physical_call_limit < 1
                    or physical_call_limit > action_policy.max_provider_calls
                )
            )
            or type(core_statuses) is not tuple
            or any(
                type(item) is not ContextSourceStatusReceipt for item in core_statuses
            )
        ):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        if source_aliases is not None:
            aliases = dict(source_aliases)
            readable_ids = {
                entry.source_id
                for entry in binding.manifest.entries
                if context_source_uses_catalog_alias(entry)
            }
            if (
                set(aliases) != readable_ids
                or any(
                    type(alias) is not str
                    or not alias.startswith("s")
                    or not alias[1:].isdigit()
                    or alias[1:].startswith("0")
                    for alias in aliases.values()
                )
                or len(set(aliases.values())) != len(aliases)
            ):
                raise _action_error(ContextActionErrorCode.INVALID_VALUE)
            self._source_aliases: dict[str, str] | None = aliases
        else:
            self._source_aliases = None
        self._allowed_search_source_kinds = allowed_search_source_kinds
        self._physical_call_limit = (
            action_policy.max_provider_calls
            if physical_call_limit is None
            else physical_call_limit
        )
        self.binding = binding
        self._sources = sources
        self._action_policy = action_policy
        self._source_policy = source_policy
        secret: bytes | None = None
        try:
            secret = secrets.token_bytes(32)
        except (OSError, RuntimeError, ValueError):
            pass
        if type(secret) is not bytes or len(secret) != 32:
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        self._secret: bytearray | None = bytearray(secret)
        self._manifest_fingerprint = _manifest_fingerprint(binding.manifest)
        if any(
            item.manifest_digest != self._manifest_fingerprint for item in core_statuses
        ):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        self._core_statuses = core_statuses
        self._lock = threading.RLock()
        self._state = ContextInvocationState.OPEN
        self._next_action_index = 1
        self._nonterminal_actions = 0
        self._search_actions = 0
        self._read_actions = 0
        self._queries = 0
        self._read_handles = 0
        self._hits = 0
        self._scan_utf8_bytes = 0
        self._read_utf8_bytes = 0
        self._observation_utf8_bytes = 0
        self._source_read_utf8_bytes: dict[str, int] = {}
        self._handles: dict[str, _HandleBinding] = {}
        self._wire_handle_by_full: dict[str, str] = {}
        self._full_handle_by_wire: dict[str, str] = {}
        self._safe_locator_by_full: dict[str, str] = {}
        self._completed_receipts: list[ContextReadReceipt] = []
        self._zero_hit_receipts: list[ContextZeroHitSearchReceipt] = []
        self._pending_receipt: ContextReadReceipt | None = None

    @property
    def state(self) -> ContextInvocationState:
        """Return the current call-local lifecycle state."""

        with self._lock:
            return self._state

    @property
    def next_action_index(self) -> int:
        """Return the next exact substantive provider-call ordinal."""

        with self._lock:
            return self._next_action_index

    @property
    def completed_handles(self) -> tuple[ContextSourceHandle, ...]:
        """Return locally completed READ handles in deterministic receipt order."""

        with self._lock:
            return tuple(
                evidence.handle
                for receipt in self._completed_receipts
                for evidence in receipt.evidence
            )

    def _handle_message(
        self,
        entry: ContextManifestEntry,
        boundary: ContextSourceBoundary,
        origin_action_index: int,
    ) -> bytes:
        return _canonical_bytes(
            [
                "context-source-handle-v1",
                _scope_value(self.binding.scope),
                self.binding.operation_id,
                self.binding.result_correlation_id,
                self._manifest_fingerprint,
                _entry_value(entry),
                _boundary_value(boundary),
                origin_action_index,
            ]
        )

    def _build_handle_binding(
        self,
        entry: ContextManifestEntry,
        boundary: ContextSourceBoundary,
        *,
        origin_action_index: int,
        safe_locator: str,
    ) -> _HandleBinding:
        if self._secret is None:
            raise _action_error(ContextActionErrorCode.INVALID_TRANSITION)
        digest = hmac.new(
            self._secret,
            self._handle_message(entry, boundary, origin_action_index),
            hashlib.sha256,
        ).hexdigest()
        return _HandleBinding(
            ContextSourceHandle(f"ctxh_v1_{digest}"),
            entry,
            boundary,
            origin_action_index,
            safe_locator,
        )

    def _register_handle(self, binding: _HandleBinding) -> None:
        existing = self._handles.get(binding.handle.value)
        if existing is not None and existing != binding:
            raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
        self._handles[binding.handle.value] = binding
        self._safe_locator_by_full[binding.handle.value] = binding.safe_locator

    def _register_wire_handle(self, handle: ContextSourceHandle) -> str:
        """Return the invocation alias for one already authorized full handle."""

        existing = self._wire_handle_by_full.get(handle.value)
        if existing is not None:
            return existing
        alias = self._wire_handle_alias(handle)
        collision = self._full_handle_by_wire.get(alias)
        if collision is not None and not hmac.compare_digest(
            collision,
            handle.value,
        ):
            raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
        self._wire_handle_by_full[handle.value] = alias
        self._full_handle_by_wire[alias] = handle.value
        return alias

    def _wire_handle_alias(self, handle: ContextSourceHandle) -> str:
        """Derive one invocation-bound 96-bit opaque provider handle."""

        if self._secret is None:
            raise _action_error(ContextActionErrorCode.INVALID_TRANSITION)
        digest = hmac.new(
            self._secret,
            b"context-wire-handle-alias-v5\x00"
            + handle.value.encode("ascii", errors="strict"),
            hashlib.sha256,
        ).digest()[:12]
        return "h" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def _preview_handle_aliases(
        self,
        pending: tuple[_HandleBinding, ...],
    ) -> dict[str, str]:
        """Project exact aliases for a not-yet-committed search observation."""

        aliases = dict(self._wire_handle_by_full)
        for binding in pending:
            if binding.handle.value not in aliases:
                alias = self._wire_handle_alias(binding.handle)
                if alias in aliases.values():
                    raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
                aliases[binding.handle.value] = alias
        return aliases

    def _preview_safe_locators(
        self,
        pending: tuple[_HandleBinding, ...],
    ) -> dict[str, str]:
        locators = dict(self._safe_locator_by_full)
        locators.update(
            (binding.handle.value, binding.safe_locator) for binding in pending
        )
        return locators

    def resolve_wire_handle(self, value: object) -> ContextSourceHandle:
        """Resolve one emitted invocation alias to its full opaque authority."""

        if self._source_aliases is None:
            return ContextSourceHandle(value)
        candidate = "" if type(value) is not str else value
        matched: str | None = None
        for alias in self._full_handle_by_wire:
            if hmac.compare_digest(alias, candidate):
                matched = alias
        if matched is None:
            raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
        return ContextSourceHandle(self._full_handle_by_wire[matched])

    def progress_sources(
        self,
        handles: tuple[ContextSourceHandle, ...],
        *,
        utf8_bytes: Mapping[str, int] | None = None,
    ) -> tuple[ContextActionProgressSource, ...]:
        """Describe authorized handles without exposing their opaque authority."""

        with self._lock:
            bindings = tuple(self._lookup_handle(handle) for handle in handles)
            return tuple(
                ContextActionProgressSource(
                    source_kind=binding.entry.source_kind,
                    locator=binding.safe_locator,
                    boundary=binding.boundary,
                    utf8_bytes=(
                        None
                        if utf8_bytes is None
                        else utf8_bytes.get(binding.handle.value)
                    ),
                )
                for binding in bindings
            )

    def serialize_observation(
        self,
        observation: ContextSearchObservation | ContextReadObservation,
    ) -> str:
        """Serialize one observation with this invocation's compact aliases."""

        if self._source_aliases is None:
            return serialize_context_action_observation(observation)
        return serialize_context_action_observation(
            observation,
            source_aliases=self._source_aliases,
            handle_aliases=self._wire_handle_by_full,
            safe_locators=self._safe_locator_by_full,
        )

    def source_alias(self, source_id: str) -> str:
        """Return the compact source alias configured for this invocation."""

        if self._source_aliases is None:
            return source_id
        alias = self._source_aliases.get(source_id)
        if alias is None:
            raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
        return alias

    @property
    def core_statuses(self) -> tuple[ContextSourceStatusReceipt, ...]:
        """Return the invocation-bound content-free canonical core statuses."""

        return self._core_statuses

    def wire_handle_alias(self, handle: ContextSourceHandle) -> str:
        """Return an already-emitted compact alias for one full handle."""

        if self._source_aliases is None:
            return handle.value
        alias = self._wire_handle_by_full.get(handle.value)
        if alias is None:
            raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
        return alias

    def _lookup_handle(self, handle: ContextSourceHandle) -> _HandleBinding:
        matched: str | None = None
        for candidate in self._handles:
            if hmac.compare_digest(candidate, handle.value):
                matched = candidate
        if matched is None:
            raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
        return self._handles[matched]

    def _require_open(self) -> None:
        if self._state is ContextInvocationState.READING:
            raise _action_error(ContextActionErrorCode.OUTSTANDING_READ)
        if self._state is not ContextInvocationState.OPEN:
            raise _action_error(ContextActionErrorCode.INVALID_TRANSITION)

    def _validate_correlation(self, correlation: ContextActionCorrelation) -> None:
        if (
            type(correlation) is not ContextActionCorrelation
            or correlation.scope != self.binding.scope
            or correlation.operation_id != self.binding.operation_id
            or correlation.result_correlation_id != self.binding.result_correlation_id
            or correlation.action_index != self._next_action_index
        ):
            raise _action_error(ContextActionErrorCode.CORRELATION_MISMATCH)
        if correlation.action_index > self._physical_call_limit:
            raise _action_error(ContextActionErrorCode.ACTION_LIMIT_EXCEEDED)

    def _require_nonterminal_capacity(self) -> None:
        if self._nonterminal_actions >= self._action_policy.max_nonterminal_actions:
            raise _action_error(ContextActionErrorCode.ACTION_LIMIT_EXCEEDED)

    def _fail_locked(self) -> None:
        self._state = ContextInvocationState.FAILED
        self._clear_sensitive_locked()

    def _clear_sensitive_locked(self) -> None:
        if self._secret is not None:
            for index in range(len(self._secret)):
                self._secret[index] = 0
            self._secret = None
        self._handles.clear()
        self._wire_handle_by_full.clear()
        self._full_handle_by_wire.clear()
        self._safe_locator_by_full.clear()
        self._pending_receipt = None
        self._completed_receipts.clear()

    def allowed_actions(
        self,
        *,
        after: str | None,
        search_had_hits: bool = False,
    ) -> tuple[str, ...]:
        """Return the one authoritative action tuple for the next call."""

        with self._lock:
            if (
                after not in {None, "search", "read"}
                or type(search_had_hits) is not bool
            ):
                raise _action_error(ContextActionErrorCode.INVALID_VALUE)
            nonterminal_capacity = (
                self._next_action_index < self._physical_call_limit
                and self._nonterminal_actions
                < self._action_policy.max_nonterminal_actions
            )
            if not nonterminal_capacity:
                return ("answer",)
            can_search = bool(
                self._search_actions < self._action_policy.max_search_actions
                and self._queries < self._action_policy.max_cumulative_queries
                and self._allowed_search_source_kinds
            )
            can_read = bool(
                self._read_actions < self._action_policy.max_read_actions
                and self._read_handles < self._action_policy.max_cumulative_handles
                and self._handles
            )
            actions: list[str] = []
            if can_search:
                actions.append("search")
            if can_read and (
                (after == "search" and search_had_hits) or after == "read"
            ):
                actions.append("read")
            actions.append("answer")
            return tuple(actions)

    def action_limits(self) -> list[int]:
        """Return exact remaining dynamic limits for the next wire call."""

        with self._lock:
            return [
                max(
                    0,
                    min(
                        self._action_policy.max_nonterminal_actions
                        - self._nonterminal_actions,
                        self._physical_call_limit - self._next_action_index,
                    ),
                ),
                max(
                    0,
                    self._action_policy.max_search_actions - self._search_actions,
                ),
                max(
                    0,
                    self._action_policy.max_read_actions - self._read_actions,
                ),
                self._action_policy.max_queries_per_search,
                max(
                    0,
                    self._action_policy.max_cumulative_queries - self._queries,
                ),
                self._action_policy.max_handles_per_read,
                max(
                    0,
                    self._action_policy.max_cumulative_handles - self._read_handles,
                ),
                self._action_policy.max_query_characters,
            ]

    @property
    def allowed_search_source_kinds(self) -> tuple[ContextSourceKind, ...]:
        """Return the immutable route-specific search authority."""

        return self._allowed_search_source_kinds

    def _record_failed_attempt_locked(
        self,
        action_kind: str,
        *,
        queries: int = 0,
    ) -> None:
        self._next_action_index += 1
        if action_kind == "search":
            self._nonterminal_actions += 1
            self._search_actions += 1
            self._queries += queries
        elif action_kind == "read":
            self._nonterminal_actions += 1
            self._read_actions += 1

    def fail(self) -> None:
        """Terminally invalidate this invocation and discard its handle table."""

        with self._lock:
            self._fail_locked()

    @staticmethod
    def _search_safe_locator(
        entry: ContextManifestEntry,
        record_content: str,
    ) -> str:
        """Retain own-state debate identity in an otherwise canonical locator."""

        locator = entry.provenance.locator
        if (
            entry.source_kind is ContextSourceKind.WRITING_ARTIFACT
            and locator.startswith("manuscript/")
        ):
            return locator.removeprefix("manuscript/")
        if entry.source_kind is not ContextSourceKind.OWN_AGENT_STATE:
            return locator
        try:
            value = json.loads(record_content)
            debate_id = _safe_identity(value.get("debate_id"))
        except (
            AttributeError,
            ContextActionError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ):
            raise _action_error(ContextActionErrorCode.HANDLE_INVALID) from None
        return f"{locator}/{debate_id}"

    def _search_manifest(
        self,
        artifact_path_prefix: str | None,
    ) -> ContextManifest:
        """Filter one shared artifact-prefix scope before any source scan."""

        if artifact_path_prefix is None:
            return self.binding.manifest
        return ContextManifest(
            self.binding.scope,
            tuple(
                entry
                for entry in self.binding.manifest.entries
                if entry.source_kind is ContextSourceKind.WRITING_ARTIFACT
                and entry.provenance.locator.startswith(artifact_path_prefix)
            ),
        )

    def search(self, action: ContextSearchAction) -> ContextSearchObservation:
        """Execute one bounded search and atomically retain its emitted handles."""

        with self._lock:
            executed = False
            try:
                self._require_open()
                if type(action) is not ContextSearchAction:
                    raise _action_error(ContextActionErrorCode.INVALID_VALUE)
                self._validate_correlation(action.correlation)
                self._require_nonterminal_capacity()
                if self._search_actions >= self._action_policy.max_search_actions:
                    raise _action_error(ContextActionErrorCode.SEARCH_LIMIT_EXCEEDED)
                if len(action.queries) > self._action_policy.max_queries_per_search:
                    raise _action_error(ContextActionErrorCode.SEARCH_LIMIT_EXCEEDED)
                if any(
                    len(query.text) > self._action_policy.max_query_characters
                    or len(query.normalized_text)
                    > self._action_policy.max_query_characters
                    or any(
                        kind not in self._allowed_search_source_kinds
                        for kind in query.source_kinds
                    )
                    for query in action.queries
                ):
                    raise _action_error(ContextActionErrorCode.SEARCH_LIMIT_EXCEEDED)
                if self._queries + len(action.queries) > (
                    self._action_policy.max_cumulative_queries
                ):
                    raise _action_error(ContextActionErrorCode.SEARCH_LIMIT_EXCEEDED)
                executed = True
                self._state = ContextInvocationState.SEARCHING
                remaining_scan = (
                    self._source_policy.max_cumulative_scan_utf8_bytes
                    - self._scan_utf8_bytes
                )
                matches, truncated, scanned = self._sources.search(
                    self._search_manifest(action.queries[0].artifact_path_prefix),
                    action.queries,
                    source_policy=self._source_policy,
                    action_policy=self._action_policy,
                    remaining_scan_utf8_bytes=remaining_scan,
                )
                if self._state is not ContextInvocationState.SEARCHING:
                    raise _action_error(ContextActionErrorCode.INVALID_TRANSITION)
                if self._hits + len(matches) > self._action_policy.max_cumulative_hits:
                    allowed = self._action_policy.max_cumulative_hits - self._hits
                    matches = matches[:allowed]
                    truncated = True
                pending_bindings: list[_HandleBinding] = []
                hits: list[ContextSearchHit] = []
                for match in matches:
                    entry = next(
                        (
                            candidate
                            for candidate in self.binding.manifest.entries
                            if candidate.source_id == match.descriptor.source_id
                        ),
                        None,
                    )
                    if entry is None:
                        raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
                    handle_binding = self._build_handle_binding(
                        entry,
                        match.record.boundary,
                        origin_action_index=action.correlation.action_index,
                        safe_locator=self._search_safe_locator(
                            entry,
                            match.record.content,
                        ),
                    )
                    pending_bindings.append(handle_binding)
                    hits.append(
                        ContextSearchHit(
                            query_index=match.query_index,
                            handle=handle_binding.handle,
                            source_id=entry.source_id,
                            source_kind=entry.source_kind,
                            revision=entry.revision,
                            provenance=entry.provenance,
                            source_digest=match.descriptor.content_digest,
                            source_utf8_bytes=match.descriptor.utf8_bytes,
                            boundary=match.record.boundary,
                            boundary_digest=match.record.content_digest,
                            boundary_utf8_bytes=match.record.utf8_bytes,
                            snippet=match.snippet,
                            snippet_utf8_bytes=match.snippet_utf8_bytes,
                        )
                    )
                available = (
                    self._action_policy.max_observation_utf8_bytes
                    - self._observation_utf8_bytes
                )
                while True:
                    search_manifest = self._search_manifest(
                        action.queries[0].artifact_path_prefix
                    )
                    requested_kinds = tuple(
                        kind
                        for kind in _SEARCH_SOURCE_KIND_ORDER
                        if any(kind in query.source_kinds for query in action.queries)
                    )
                    authorized_entries = tuple(
                        entry
                        for entry in search_manifest.entries
                        if entry.searchable and entry.source_kind in requested_kinds
                    )
                    zero_hit_receipt = (
                        ContextZeroHitSearchReceipt(
                            evidence_id=(f"search_{action.correlation.action_index}"),
                            correlation=action.correlation,
                            manifest_digest=self._manifest_fingerprint,
                            authorized_source_ids=tuple(
                                entry.source_id for entry in authorized_entries
                            ),
                            authorized_source_kinds=requested_kinds,
                            authorized_source_fingerprint=(
                                _authorized_source_fingerprint(authorized_entries)
                            ),
                            artifact_path_prefix=(
                                action.queries[0].artifact_path_prefix
                            ),
                            query_digest=_digest(
                                _canonical_bytes(
                                    [
                                        "context-zero-hit-query-v1",
                                        [
                                            [
                                                query.normalized_text,
                                                [
                                                    kind.value
                                                    for kind in query.source_kinds
                                                ],
                                                query.artifact_path_prefix,
                                            ]
                                            for query in action.queries
                                        ],
                                    ]
                                )
                            ),
                            query_count=len(action.queries),
                        )
                        if not hits and not truncated
                        else None
                    )
                    observation = ContextSearchObservation(
                        action.correlation,
                        tuple(hits),
                        truncated,
                        zero_hit_receipt,
                    )
                    framed_bytes = len(
                        serialize_context_action_observation(
                            observation,
                            source_aliases=self._source_aliases,
                            handle_aliases=self._preview_handle_aliases(
                                tuple(pending_bindings)
                            ),
                            safe_locators=self._preview_safe_locators(
                                tuple(pending_bindings)
                            ),
                        ).encode("utf-8")
                        if self._source_aliases is not None
                        else serialize_context_action_observation(observation).encode(
                            "utf-8"
                        )
                    )
                    if framed_bytes <= available:
                        break
                    if not hits:
                        raise _action_error(
                            ContextActionErrorCode.OBSERVATION_LIMIT_EXCEEDED
                        )
                    hits.pop()
                    pending_bindings.pop()
                    truncated = True
                if self._state is not ContextInvocationState.SEARCHING:
                    raise _action_error(ContextActionErrorCode.INVALID_TRANSITION)
                for handle_binding in pending_bindings:
                    self._register_handle(handle_binding)
                    self._register_wire_handle(handle_binding.handle)
                self._nonterminal_actions += 1
                self._search_actions += 1
                self._queries += len(action.queries)
                self._hits += len(hits)
                self._scan_utf8_bytes += scanned
                self._observation_utf8_bytes += framed_bytes
                if observation.zero_hit_receipt is not None:
                    self._zero_hit_receipts.append(observation.zero_hit_receipt)
                self._next_action_index += 1
                self._state = ContextInvocationState.OPEN
                return observation
            except BaseException:
                if executed:
                    self._record_failed_attempt_locked(
                        "search",
                        queries=(
                            len(action.queries)
                            if type(action) is ContextSearchAction
                            else 0
                        ),
                    )
                self._fail_locked()
                raise

    @staticmethod
    def _boundary_content(
        snapshot: ContextSourceSnapshot,
        boundary: ContextSourceBoundary,
    ) -> bytes:
        if boundary.kind is ContextSourceBoundaryKind.WHOLE:
            return snapshot.content
        if boundary.kind in {
            ContextSourceBoundaryKind.ARTIFACT_LINES,
            ContextSourceBoundaryKind.RECORDS,
        }:
            assert boundary.start is not None and boundary.end is not None
            selected = tuple(
                record
                for record in snapshot.records
                if record.boundary.start is not None
                and boundary.start <= record.boundary.start <= boundary.end
            )
            if len(selected) != boundary.end - boundary.start + 1 or tuple(
                record.boundary.start for record in selected
            ) != tuple(range(boundary.start, boundary.end + 1)):
                raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
            if boundary.kind is ContextSourceBoundaryKind.ARTIFACT_LINES:
                return b"".join(record.content.encode("utf-8") for record in selected)
            if len(selected) == 1:
                return selected[0].content.encode("utf-8")
            return _canonical_bytes([json.loads(record.content) for record in selected])
        matches = tuple(
            record for record in snapshot.records if record.boundary == boundary
        )
        if len(matches) != 1:
            raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
        return matches[0].content.encode("utf-8")

    def read(
        self,
        action: ContextReadAction,
    ) -> tuple[ContextReadObservation, ContextReadReceipt]:
        """Capture one ordered batch without committing any partial result."""

        with self._lock:
            executed = False
            try:
                self._require_open()
                if type(action) is not ContextReadAction:
                    raise _action_error(ContextActionErrorCode.INVALID_VALUE)
                self._validate_correlation(action.correlation)
                self._require_nonterminal_capacity()
                if self._read_actions >= self._action_policy.max_read_actions:
                    raise _action_error(ContextActionErrorCode.READ_LIMIT_EXCEEDED)
                if len(action.handles) > self._action_policy.max_handles_per_read:
                    raise _action_error(ContextActionErrorCode.READ_LIMIT_EXCEEDED)
                if self._read_handles + len(action.handles) > (
                    self._action_policy.max_cumulative_handles
                ):
                    raise _action_error(ContextActionErrorCode.READ_LIMIT_EXCEEDED)
                bindings = tuple(
                    self._lookup_handle(handle) for handle in action.handles
                )
                executed = True
                self._state = ContextInvocationState.READING
                requested_source_ids = {binding.entry.source_id for binding in bindings}
                ordered_entries = tuple(
                    entry
                    for entry in self.binding.manifest.entries
                    if entry.source_id in requested_source_ids
                )
                snapshots: dict[str, ContextSourceSnapshot] = {}
                for entry in ordered_entries:
                    snapshots[entry.source_id] = self._sources.capture(
                        self.binding.manifest,
                        entry,
                        self._source_policy,
                    )
                    if self._state is not ContextInvocationState.READING:
                        raise _action_error(ContextActionErrorCode.INVALID_TRANSITION)
                source_additions: dict[str, int] = {}
                evidence: list[ContextSourceEvidence] = []
                batch_bytes = 0
                for evidence_ordinal, handle_binding in enumerate(bindings, start=1):
                    entry = handle_binding.entry
                    snapshot = snapshots[entry.source_id]
                    excerpt = self._boundary_content(
                        snapshot,
                        handle_binding.boundary,
                    )
                    if len(excerpt) > self._action_policy.max_excerpt_utf8_bytes:
                        raise _action_error(ContextActionErrorCode.READ_LIMIT_EXCEEDED)
                    source_additions[entry.source_id] = source_additions.get(
                        entry.source_id, 0
                    ) + len(excerpt)
                    prior_source_bytes = self._source_read_utf8_bytes.get(
                        entry.source_id,
                        0,
                    )
                    if prior_source_bytes + source_additions[entry.source_id] > (
                        self._action_policy.max_source_read_utf8_bytes
                    ):
                        raise _action_error(ContextActionErrorCode.READ_LIMIT_EXCEEDED)
                    batch_bytes += len(excerpt)
                    if self._read_utf8_bytes + batch_bytes > (
                        self._action_policy.max_read_utf8_bytes
                    ):
                        raise _action_error(ContextActionErrorCode.READ_LIMIT_EXCEEDED)
                    receipt = ContextEvidenceReceipt(
                        evidence_id=(
                            f"read_{action.correlation.action_index}_{evidence_ordinal}"
                        ),
                        handle=handle_binding.handle,
                        source_id=entry.source_id,
                        source_kind=entry.source_kind,
                        revision=entry.revision,
                        provenance=entry.provenance,
                        source_digest=entry.content_digest,
                        source_utf8_bytes=entry.utf8_bytes,
                        boundary=handle_binding.boundary,
                        boundary_digest=(
                            f"sha256:{hashlib.sha256(excerpt).hexdigest()}"
                        ),
                        boundary_utf8_bytes=len(excerpt),
                        originating_search_action_index=(
                            handle_binding.origin_action_index
                        ),
                        completing_read_action_index=(action.correlation.action_index),
                    )
                    evidence.append(
                        ContextSourceEvidence(
                            receipt,
                            excerpt.decode("utf-8", errors="strict"),
                        )
                    )
                for entry in ordered_entries:
                    if (
                        self._sources.capture(
                            self.binding.manifest,
                            entry,
                            self._source_policy,
                        )
                        != snapshots[entry.source_id]
                    ):
                        raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
                    if self._state is not ContextInvocationState.READING:
                        raise _action_error(ContextActionErrorCode.INVALID_TRANSITION)
                observation = ContextReadObservation(
                    action.correlation,
                    tuple(evidence),
                )
                framed = self.serialize_observation(observation).encode("utf-8")
                if self._observation_utf8_bytes + len(framed) > (
                    self._action_policy.max_observation_utf8_bytes
                ):
                    raise _action_error(
                        ContextActionErrorCode.OBSERVATION_LIMIT_EXCEEDED
                    )
                receipt = ContextReadReceipt(
                    action.correlation,
                    tuple(item.receipt for item in evidence),
                    f"sha256:{hashlib.sha256(framed).hexdigest()}",
                    len(framed),
                )
                for handle in action.handles:
                    del self._handles[handle.value]
                self._nonterminal_actions += 1
                self._read_actions += 1
                self._read_handles += len(action.handles)
                self._read_utf8_bytes += batch_bytes
                for source_id, added in source_additions.items():
                    self._source_read_utf8_bytes[source_id] = (
                        self._source_read_utf8_bytes.get(source_id, 0) + added
                    )
                self._observation_utf8_bytes += len(framed)
                self._next_action_index += 1
                self._pending_receipt = receipt
                return observation, receipt
            except BaseException:
                if executed:
                    self._record_failed_attempt_locked("read")
                self._fail_locked()
                raise

    def complete_read(self, receipt: ContextReadReceipt) -> None:
        """Mark one exact read frame as delivered before accepting another action."""

        with self._lock:
            try:
                if (
                    self._state is not ContextInvocationState.READING
                    or type(receipt) is not ContextReadReceipt
                    or receipt != self._pending_receipt
                ):
                    raise _action_error(ContextActionErrorCode.OUTSTANDING_READ)
                self._completed_receipts.append(receipt)
                self._pending_receipt = None
                self._state = ContextInvocationState.OPEN
            except BaseException:
                self._fail_locked()
                raise

    def authorize_answer(
        self,
        action: ContextAnswerAction,
    ) -> ContextAnswerAuthorization:
        """Revalidate every completed source and authorize one terminal answer."""

        with self._lock:
            executed = False
            try:
                self._require_open()
                if type(action) is not ContextAnswerAction:
                    raise _action_error(ContextActionErrorCode.INVALID_VALUE)
                self._validate_correlation(action.correlation)
                completed_evidence = tuple(
                    evidence
                    for receipt in self._completed_receipts
                    for evidence in receipt.evidence
                )
                completed = {
                    evidence.handle.value: evidence for evidence in completed_evidence
                }
                if len(completed) != len(completed_evidence):
                    raise _action_error(ContextActionErrorCode.ANSWER_NOT_AUTHORIZED)
                if any(handle.value not in completed for handle in action.used_handles):
                    raise _action_error(ContextActionErrorCode.ANSWER_NOT_AUTHORIZED)
                if len(action.used_handles) > (
                    self._action_policy.max_cumulative_handles
                ):
                    raise _action_error(ContextActionErrorCode.ANSWER_NOT_AUTHORIZED)
                executed = True
                self._state = ContextInvocationState.SEARCHING
                completed_source_ids = {
                    evidence.source_id for evidence in completed_evidence
                }
                for entry in self.binding.manifest.entries:
                    if entry.source_id not in completed_source_ids:
                        continue
                    if not any(
                        evidence.source_id == entry.source_id
                        and evidence.revision == entry.revision
                        and evidence.source_digest == entry.content_digest
                        and evidence.provenance == entry.provenance
                        for evidence in completed_evidence
                    ):
                        raise _action_error(
                            ContextActionErrorCode.ANSWER_NOT_AUTHORIZED
                        )
                    self._sources.capture(
                        self.binding.manifest,
                        entry,
                        self._source_policy,
                    )
                    if self._state is not ContextInvocationState.SEARCHING:
                        raise _action_error(ContextActionErrorCode.INVALID_TRANSITION)
                completed_read_digest = _completed_evidence_digest(completed_evidence)
                answer_digest = _digest(action.answer.encode("utf-8"))
                if self._secret is None:
                    raise _action_error(ContextActionErrorCode.INVALID_TRANSITION)
                authorization_message = _canonical_bytes(
                    [
                        "context-answer-authorization-v1",
                        _scope_value(self.binding.scope),
                        self.binding.operation_id,
                        self.binding.result_correlation_id,
                        action.correlation.action_index,
                        self._manifest_fingerprint,
                        answer_digest,
                        completed_read_digest,
                        [_evidence_value(item) for item in completed_evidence],
                        [handle.value for handle in action.used_handles],
                        [_status_value(item) for item in self._core_statuses],
                        [_zero_hit_value(item) for item in self._zero_hit_receipts],
                        self.binding.reference_operation_id,
                        [
                            _reference_value(reference)
                            for reference in self.binding.mandatory_references
                        ],
                    ]
                )
                authorization_id = (
                    "ctxa_v1_"
                    + hmac.new(
                        self._secret,
                        authorization_message,
                        hashlib.sha256,
                    ).hexdigest()
                )
                authorization = ContextAnswerAuthorization(
                    authorization_id=authorization_id,
                    correlation=action.correlation,
                    manifest_digest=self._manifest_fingerprint,
                    answer_digest=answer_digest,
                    completed_evidence=completed_evidence,
                    completed_read_digest=completed_read_digest,
                    used_evidence=tuple(
                        completed[handle.value] for handle in action.used_handles
                    ),
                    core_statuses=self._core_statuses,
                    zero_hit_searches=tuple(self._zero_hit_receipts),
                    reference_operation_id=self.binding.reference_operation_id,
                    mandatory_references=self.binding.mandatory_references,
                )
                self._next_action_index += 1
                self._state = ContextInvocationState.ANSWER_AUTHORIZED
                self._clear_sensitive_locked()
                return authorization
            except BaseException:
                if executed:
                    self._record_failed_attempt_locked("answer")
                self._fail_locked()
                raise


def _workspace_context_id(workspace: Workspace) -> str:
    """Derive a content-free identity from the public canonical root."""

    if type(workspace) is not Workspace:
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    try:
        root = workspace.root.as_posix().encode("utf-8", errors="strict")
    except (AttributeError, UnicodeEncodeError):
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE) from None
    body = b"story-writing-agents-context-workspace-v1\x00" + root
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def _descriptor_entry(
    descriptor: object,
    classification: ContextSourceClassification,
) -> ContextManifestEntry:
    """Project one authoritative descriptor into its planned manifest entry."""

    return ContextManifestEntry(
        source_id=descriptor.source_id,
        source_kind=descriptor.source_kind,
        semantic_channel=classification.semantic_channel,
        priority=classification.priority,
        ordinal=classification.ordinal,
        revision=descriptor.revision,
        content_digest=descriptor.content_digest,
        provenance=descriptor.provenance,
        utf8_bytes=descriptor.utf8_bytes,
        searchable=True,
    )


def _action_correlation(
    binding: ContextInvocationBinding,
    action_index: int,
) -> ContextActionCorrelation:
    """Reconstruct the complete exact correlation hidden behind one wire token."""

    return ContextActionCorrelation(
        binding.scope,
        binding.operation_id,
        binding.result_correlation_id,
        action_index,
    )


def _correlation_token(correlation: ContextActionCorrelation) -> str:
    """Derive one deterministic URL-safe 256-bit token from full correlation."""

    body = _canonical_bytes(
        [
            _CONTEXT_WIRE_VERSION,
            _scope_value(correlation.scope),
            correlation.operation_id,
            correlation.result_correlation_id,
            correlation.action_index,
        ]
    )
    return (
        base64.urlsafe_b64encode(hashlib.sha256(body).digest())
        .rstrip(b"=")
        .decode("ascii")
    )


def _projected_component_kind(entry: ContextManifestEntry) -> ContextComponentKind:
    if entry.semantic_channel is ContextSemanticChannel.CURRENT_INPUT:
        return ContextComponentKind.CURRENT_INPUT
    if entry.semantic_channel is ContextSemanticChannel.CANDIDATE:
        return ContextComponentKind.CANDIDATE
    if entry.source_kind is ContextSourceKind.AGENT_DEFINITION:
        return ContextComponentKind.ROLE_SYSTEM_PROMPT
    if entry.source_kind is ContextSourceKind.CONVERSATION:
        return ContextComponentKind.CONVERSATION_CONTEXT
    if entry.source_kind is ContextSourceKind.ACTIVE_DEBATE:
        return ContextComponentKind.DEBATE_MESSAGES
    if entry.source_kind is ContextSourceKind.OWN_AGENT_STATE:
        return ContextComponentKind.AGENT_STATE
    return ContextComponentKind.RETRIEVED_OBSERVATION


class _DuplicateJsonKey(ValueError):
    pass


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _exact_object(
    value: object,
    fields: frozenset[str],
) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        raise validation_failure(
            _execution_error(ContextExecutionErrorCode.MALFORMED_ACTION), "$",
            "expected an object containing exactly " + ", ".join(sorted(fields)),
        )
    assert isinstance(value, dict)
    return value


def _answer_text_for_contract(
    value: object,
    contract: ContextJsonObjectResultContract,
) -> str:
    """Validate one owner-supplied result schema and canonicalize its object."""

    if type(contract) is not ContextJsonObjectResultContract or not (
        _value_matches_provider_schema(value, contract.schema_value())
    ):
        raise validation_failure(
            ValueError(),
            '$',
            'expected a JSON object matching the current result contract',
        )
    try:
        return _canonical_bytes(value).decode("utf-8", errors="strict")
    except ContextActionError as failure:
        raise encoding_failure(failure, value, contract.schema_value())


def _decode_context_action(
    response_text: str,
    *,
    correlation: ContextActionCorrelation,
    allowed_actions: tuple[str, ...],
    result_contract: ContextJsonObjectResultContract,
    allowed_search_source_kinds: tuple[ContextSourceKind, ...] = _SEARCH_SOURCE_KIND_ORDER,
    resolve_handle: Callable[[object], ContextSourceHandle] = ContextSourceHandle,
    completed_handles: tuple[ContextSourceHandle, ...] = (),
) -> ContextSearchAction | ContextReadAction | ContextAnswerAction:
    """Strictly decode typed wire v7 and retain the actual rejected property."""
    try:
        value = json.loads(
            response_text, object_pairs_hook=_unique_json_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        raw = _exact_object(value, frozenset({"action"}))
        action = raw["action"]
        if type(action) is not dict or type(action.get("kind")) is not str:
            raise validation_failure(ValueError(), "$.kind", "expected one of search, read, answer")
        action_kind = action["kind"]
        if action_kind not in {"answer", "read", "search"}:
            raise validation_failure(ValueError(), "$.kind", "expected one of search, read, answer")
        if action_kind == "search":
            if set(action) not in ({"kind", "queries"}, {"kind", "path_prefix", "queries"}):
                raise validation_failure(
                    ValueError(),
                    '$',
                    'search requires kind and queries, with only optional path_prefix',
                )
            path_prefix = action.get("path_prefix")
            query_values = action["queries"]
            if action_kind not in allowed_actions:
                raise validation_failure(
                    ValueError(),
                    '$.kind',
                    'expected an action from ' + ', '.join(allowed_actions),
                )
            if path_prefix is not None and type(path_prefix) is not str:
                raise validation_failure(
                    ValueError(),
                    '$.path_prefix',
                    'expected text or null',
                    value=path_prefix,
                )
            if type(query_values) is not list or not query_values:
                raise validation_failure(
                    ValueError(),
                    '$.queries',
                    'expected a nonempty array',
                    value=query_values,
                )
            queries: list[ContextSearchQuery] = []
            for query_index, query in enumerate(query_values):
                query_path = f"$.queries[{query_index}]"
                query_object = validation_call(query_path, _exact_object, query, frozenset({'source_kinds', 'text'}))
                kinds = query_object["source_kinds"]
                if type(query_object["text"]) is not str:
                    raise validation_failure(
                        ValueError(),
                        query_path + '.text',
                        'expected text',
                        value=query_object['text'],
                    )
                if type(kinds) is not list:
                    raise validation_failure(
                        ValueError(),
                        query_path + '.source_kinds',
                        'expected an array',
                        value=kinds,
                    )
                decoded_kinds = tuple(
                    validation_call(f"{query_path}.source_kinds[{index}]", ContextSourceKind, item)
                    for index, item in enumerate(kinds)
                )
                if not decoded_kinds:
                    raise validation_failure(
                        ValueError(),
                        query_path + '.source_kinds',
                        'expected at least one source kind',
                    )
                if len(set(decoded_kinds)) != len(decoded_kinds):
                    raise validation_failure(
                        ValueError(),
                        query_path + '.source_kinds',
                        'source kinds must be unique',
                    )
                if any(kind not in allowed_search_source_kinds for kind in decoded_kinds):
                    raise validation_failure(
                        ValueError(),
                        query_path + '.source_kinds',
                        'expected source kinds from ' + ', '.join((kind.value for kind in allowed_search_source_kinds)),
                    )
                canonical_kinds = tuple(kind for kind in _SEARCH_SOURCE_KIND_ORDER if kind in decoded_kinds)
                try:
                    queries.append(validation_call(
                        query_path,
                        ContextSearchQuery,
                        query_object['text'],
                        canonical_kinds,
                        path_prefix,
                    ))
                except ContextActionError as failure:
                    raise remap_validation(failure, {query_path + ".path_prefix": "$.path_prefix"})
            return ContextSearchAction(correlation, tuple(queries))
        if action_kind == "read":
            read = _exact_object(action, frozenset({"handles", "kind"}))
            handles_value = read["handles"]
            if type(handles_value) is not list or not handles_value:
                raise _execution_error(ContextExecutionErrorCode.MALFORMED_ACTION, detail=_ContextExecutionErrorDetail.READ_SOURCE_HANDLES_REQUIRED)
            if action_kind not in allowed_actions:
                raise validation_failure(
                    ValueError(),
                    '$.kind',
                    'expected an action from ' + ', '.join(allowed_actions),
                )
            handles: list[ContextSourceHandle] = []
            for index, item in enumerate(handles_value):
                try:
                    handles.append(resolve_handle(item))
                except ContextActionError as failure:
                    if failure.code in {ContextActionErrorCode.HANDLE_INVALID, ContextActionErrorCode.INVALID_VALUE}:
                        raise validation_failure(
                            _execution_error(ContextExecutionErrorCode.MALFORMED_ACTION, detail=_ContextExecutionErrorDetail.READ_SOURCE_HANDLES_REQUIRED),
                            f"$.handles[{index}]", "expected a source handle returned by a preceding search",
                        ) from None
                    raise
            return ContextReadAction(correlation, tuple(handles))
        answer = _exact_object(action, frozenset({"kind", "result"}))
        if action_kind not in allowed_actions:
            raise validation_failure(
                ValueError(),
                '$.kind',
                'expected an action from ' + ', '.join(allowed_actions),
            )
        return ContextAnswerAction(
            correlation,
            validation_call("$.result", _answer_text_for_contract, answer["result"], result_contract),
            completed_handles,
        )
    except ContextExecutionError:
        raise
    except (ContextActionError, ContextManifestError, _DuplicateJsonKey, json.JSONDecodeError, TypeError, ValueError) as failure:
        raise carry_validation(_execution_error(ContextExecutionErrorCode.MALFORMED_ACTION), failure) from None


def _context_validation_details(
    response_text: str,
    schema_issues: tuple[str, ...],
    failure: ContextExecutionError,
) -> tuple[str, ...]:
    """Retain the actual schema/domain rejection; never revalidate heuristically."""
    issues = schema_issues or failure_issues(failure, path="$.action")
    if not issues and _execution_error_has_detail(
        failure, _ContextExecutionErrorDetail.READ_SOURCE_HANDLES_REQUIRED,
    ):
        issues = ("$.action.handles: expected unique handles returned by a preceding search",)
    if not issues and _execution_error_has_detail(
        failure, _ContextExecutionErrorDetail.RESPONSE_CORRELATION_MISMATCH,
    ):
        issues = ("$.action: response correlation must match the current request",)
    return bounded_chunks(issues or (
        "$.action: action decoder failed without a property diagnostic",
    ))


def _closed_provider_object_schema(
    properties: dict[str, object],
    *,
    description: str | None = None,
) -> dict[str, object]:
    schema: dict[str, object] = {
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
        "type": "object",
    }
    if description is not None:
        schema["description"] = description
    return schema


def _context_action_schema(
    *,
    allowed_actions: tuple[str, ...],
    result_contract: ContextJsonObjectResultContract,
    allowed_search_source_kinds: tuple[ContextSourceKind, ...],
    action_limits: list[int],
) -> dict[str, object]:
    """Build one closed typed action envelope for the exact physical call."""

    variants: list[dict[str, object]] = []
    if "search" in allowed_actions:
        query_limit = min(action_limits[3], action_limits[4])
        query_schema = _closed_provider_object_schema(
            {
                "text": {"type": "string"},
                "source_kinds": {
                    "description": (
                        "Choose distinct allowed source kinds; provider order is "
                        "not significant and the runtime canonicalizes it."
                    ),
                    "items": {
                        "enum": [kind.value for kind in allowed_search_source_kinds],
                        "type": "string",
                    },
                    "maxItems": len(allowed_search_source_kinds),
                    "minItems": 1,
                    "type": "array",
                },
            }
        )
        search_properties = {
            "kind": {"enum": ["search"], "type": "string"},
            "queries": {
                "items": query_schema,
                "maxItems": query_limit,
                "minItems": 1,
                "type": "array",
            },
        }
        variants.append(_closed_provider_object_schema(search_properties))
        if ContextSourceKind.WRITING_ARTIFACT in allowed_search_source_kinds:
            artifact_query = _closed_provider_object_schema({
                "text": {"type": "string", "description": "Nonempty literal search text."},
                "source_kinds": {
                    "type": "array", "items": {"type": "string", "enum": ["writing_artifact"]},
                    "minItems": 1, "maxItems": 1,
                },
            })
            variants.append(_closed_provider_object_schema({
                **search_properties,
                "queries": {"type": "array", "items": artifact_query,
                            "minItems": 1, "maxItems": query_limit},
                "path_prefix": {"type": "string",
                                "description": "Normalized relative artifact directory; writing_artifact queries only."},
            }))
    if "read" in allowed_actions:
        handle_limit = min(action_limits[5], action_limits[6])
        variants.append(
            _closed_provider_object_schema(
                {
                    "kind": {"enum": ["read"], "type": "string"},
                    "handles": {
                        "items": {"type": "string"},
                        "maxItems": handle_limit,
                        "minItems": 1,
                        "type": "array",
                    },
                },
                description="READ: retrieve one or more SEARCH result handles.",
            )
        )
    if "answer" in allowed_actions:
        variants.append(
            _closed_provider_object_schema(
                {
                    "kind": {"enum": ["answer"], "type": "string"},
                    "result": result_contract.schema_value(),
                },
                description=(
                    "ANSWER: return only the exact semantic result. Runtime "
                    "attaches successful READ receipts."
                ),
            )
        )
    if not variants:
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    action_schema: dict[str, object] = (
        variants[0] if len(variants) == 1 else {"anyOf": variants}
    )
    schema = _closed_provider_object_schema(
        {"action": action_schema},
        description=(
            "Return exactly one schema-selected context action. The runtime "
            "owns correlation and action-index bookkeeping."
        ),
    )
    _validate_provider_schema_node(schema)
    return schema


def _source_number(source_alias: str) -> int:
    """Project one validated internal ``sN`` alias as its wire integer."""

    if (
        type(source_alias) is not str
        or not source_alias.startswith("s")
        or not source_alias[1:].isdigit()
        or source_alias[1:].startswith("0")
    ):
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    return int(source_alias[1:])


def _wire_projected_content(
    unit_entries: tuple[ContextManifestEntry, ...],
    body_utf8: bytes,
) -> object:
    """Losslessly remove one redundant JSON-string layer from structured sources."""

    content = body_utf8.decode("utf-8", errors="strict")
    if any(
        entry.source_kind not in _STRUCTURED_WIRE_SOURCE_KINDS for entry in unit_entries
    ):
        return content
    try:
        parsed = json.loads(
            content,
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (_DuplicateJsonKey, json.JSONDecodeError, TypeError, ValueError):
        return content
    try:
        if _canonical_bytes(parsed) == body_utf8:
            return parsed
    except ContextActionError:
        pass
    return content


def _wire_duplicate_value(
    value: object,
    duplicate_channels: Mapping[str, str | None],
) -> object:
    """Replace only an exact uniquely identified P0 byte duplicate."""

    if type(value) is str:
        channel = duplicate_channels.get(value)
        if channel is not None:
            return ["=", channel]
    return value


def _wire_record_value(
    entry: ContextManifestEntry,
    record: ContextCheckpointRecord,
    body_utf8: bytes,
    duplicate_channels: Mapping[str, str | None],
) -> list[object]:
    """Project one positional conversation or debate record patch."""

    value = _wire_projected_content((entry,), body_utf8)
    alias = record.record_alias
    if type(value) is not dict:
        return [alias, value]
    if entry.source_kind is ContextSourceKind.CONVERSATION:
        user = value.get("user_message")
        reply = value.get("agent_reply")
        if type(user) is dict and type(reply) is dict:
            return [
                alias,
                user.get("content"),
                reply.get("sender"),
                reply.get("content"),
            ]
        return [alias, value]
    if entry.source_kind is ContextSourceKind.ACTIVE_DEBATE:
        if record.cursor.record_id == "state":
            return [
                alias,
                "s",
                value.get("input_kind"),
                value.get("revision_id"),
                value.get("rounds_completed"),
            ]
        return [
            alias,
            "m",
            value.get("sender"),
            value.get("phase"),
            value.get("round"),
            value.get("revision"),
            value.get("kind"),
            _wire_duplicate_value(value.get("content"), duplicate_channels),
        ]
    return [alias, value]


@dataclass(frozen=True, slots=True)
class _WireCandidateRecord:
    """Exact candidate content addressed by one persistent debate record."""

    source_alias: str
    record_alias: int
    content: str = field(repr=False)
    content_digest: str
    utf8_bytes: int

    def __post_init__(self) -> None:
        _source_number(self.source_alias)
        if type(self.record_alias) is not int or self.record_alias < 1:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.content) is not str:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        try:
            encoded = self.content.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE) from None
        if _digest(encoded) != self.content_digest or len(encoded) != self.utf8_bytes:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)


def _debate_candidate_text(material: ContextRecordMaterial) -> str | None:
    """Return one exact canonical debate candidate record body, when present."""

    try:
        value = json.loads(
            material.body_utf8,
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (_DuplicateJsonKey, json.JSONDecodeError, TypeError, ValueError):
        return None
    if (
        type(value) is not dict
        or value.get("kind") != "candidate"
        or type(value.get("content")) is not str
    ):
        return None
    content = value["content"]
    assert isinstance(content, str)
    try:
        content.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return None
    return content


def _latest_debate_candidate_record(
    sources: tuple[ContextCheckpointSource, ...],
    catalog_sources: tuple[ContextCatalogCursor, ...],
    materials: tuple[ContextMaterial, ...],
) -> _WireCandidateRecord | None:
    """Resolve the latest provable candidate from content-free checkpoint facts."""

    debate_sources = tuple(
        source
        for source in sources
        if source.entry.source_kind is ContextSourceKind.ACTIVE_DEBATE
    )
    if len(debate_sources) != 1:
        return None
    source = debate_sources[0]
    catalog = next(
        (
            item
            for item in catalog_sources
            if item.entry.source_id == source.entry.source_id
        ),
        None,
    )
    material = next(
        (item for item in materials if item.entry.source_id == source.entry.source_id),
        None,
    )
    if catalog is None or material is None:
        return None
    material_by_id = {record.cursor.record_id: record for record in material.records}
    candidate: _WireCandidateRecord | None = None
    for accepted in source.records:
        record = material_by_id.get(accepted.cursor.record_id)
        if record is None or record.cursor != accepted.cursor:
            continue
        content = _debate_candidate_text(record)
        if content is None:
            continue
        encoded = content.encode("utf-8", errors="strict")
        candidate = _WireCandidateRecord(
            catalog.source_alias,
            accepted.record_alias,
            content,
            _digest(encoded),
            len(encoded),
        )
    return candidate


def _candidate_single_splice(
    base: str,
    target: str,
) -> tuple[int, int, str]:
    """Return the maximal nonoverlapping code-point prefix/suffix splice."""

    prefix = 0
    shared_limit = min(len(base), len(target))
    while prefix < shared_limit and base[prefix] == target[prefix]:
        prefix += 1
    suffix = 0
    remaining_base = len(base) - prefix
    remaining_target = len(target) - prefix
    while (
        suffix < remaining_base
        and suffix < remaining_target
        and base[len(base) - suffix - 1] == target[len(target) - suffix - 1]
    ):
        suffix += 1
    replacement_end = len(target) - suffix if suffix else len(target)
    return prefix, suffix, target[prefix:replacement_end]


def _wire_candidate_facts(record: _WireCandidateRecord) -> dict[str, object]:
    """Project one self-describing candidate record identity and byte facts."""

    return {
        "source_alias": _source_number(record.source_alias),
        "record_alias": record.record_alias,
        "digest": record.content_digest,
        "utf8_bytes": record.utf8_bytes,
    }


def _wire_candidate_value(
    candidate: str,
    projection: ContextProjection,
    checkpoint: ContextCheckpointView,
    materials: tuple[ContextMaterial, ...],
) -> object:
    """Project one exact full candidate, accepted pointer, or verified splice."""

    if (
        projection.mode is not ContextDeliveryMode.DELTA
        or checkpoint.state is not ContextCheckpointState.READY
        or checkpoint.provider_thread_id is None
    ):
        return candidate
    base = _latest_debate_candidate_record(
        checkpoint.sources,
        checkpoint.catalog_sources,
        materials,
    )
    target = _latest_debate_candidate_record(
        projection.checkpoint_proposal.sources,
        projection.checkpoint_proposal.catalog_sources,
        materials,
    )
    if base is None or target is None or target.content != candidate:
        return candidate
    base_facts = _wire_candidate_facts(base)
    target_facts = _wire_candidate_facts(target)
    candidate_bytes = candidate.encode("utf-8", errors="strict")
    if base.content == target.content == candidate:
        pointer: object = {
            "op": "candidate_pointer",
            "base": base_facts,
            "target": target_facts,
            "result": "candidate_text",
            "resolve_record_candidate": "logical_n",
        }
        if len(_canonical_bytes(pointer)) >= len(_canonical_bytes(candidate)):
            return candidate
        return pointer
    prefix, suffix, replacement = _candidate_single_splice(
        base.content,
        candidate,
    )
    retained_suffix = base.content[len(base.content) - suffix :] if suffix else ""
    reconstructed = base.content[:prefix] + replacement + retained_suffix
    if (
        reconstructed.encode("utf-8", errors="strict") != candidate_bytes
        or _digest(candidate_bytes) != target.content_digest
        or len(candidate_bytes) != target.utf8_bytes
    ):
        return candidate
    patch: object = {
        "op": "candidate_single_splice",
        "base": base_facts,
        "prefix_codepoints": prefix,
        "replacement": replacement,
        "suffix_codepoints": suffix,
        "target": target_facts,
        "result": "candidate_text",
        "resolve_record_candidate": "logical_n",
    }
    if len(_canonical_bytes(patch)) >= len(_canonical_bytes(candidate)):
        return candidate
    return patch


def _conversation_counts(material: ContextMaterial) -> tuple[int, int]:
    """Return complete/omitted turn counts from one validated snapshot body."""

    try:
        value = json.loads(
            material.body_utf8,
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (_DuplicateJsonKey, json.JSONDecodeError, TypeError, ValueError):
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE) from None
    if type(value) is not dict:
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    window = value.get("window")
    if window is None:
        return len(material.records), 0
    if (
        type(window) is not dict
        or type(window.get("total_complete_turns")) is not int
        or type(window.get("omitted_turns")) is not int
    ):
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    return window["total_complete_turns"], window["omitted_turns"]


def _invocation_phase_contract(
    request: ContextInvocationRequest,
) -> tuple[str, ContextJsonObjectResultContract]:
    """Pair the route-derived phase with its owner-supplied result contract."""

    if request.invocation_kind is not None:
        return request.invocation_kind.value, request.result_contract
    if request.subject_kind is ContextSubjectKind.CONVERSATION:
        if request.allowed_search_source_kinds == (ContextSourceKind.CONVERSATION,):
            return "retrieval", request.result_contract
        return "conversation", request.result_contract
    phases = (
        "propose_revision",
        "synthesize",
        "finalize",
        "audit",
        "draft",
    )
    phase = next(
        (
            candidate
            for candidate in phases
            if request.operation_id.startswith(f"debate_{candidate}_")
        ),
        None,
    )
    if phase is None:
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    return phase, request.result_contract


def _action_query(
    phase_contract: tuple[str, ContextJsonObjectResultContract],
    session: ContextInvocationSession,
    allowed_actions: tuple[str, ...],
) -> list[object]:
    """Build the authoritative phase, action, kind, limit, and result tuple."""

    phase, contract = phase_contract
    return [
        phase,
        "".join(_ACTION_WIRE_CODES[action] for action in allowed_actions),
        (
            "".join(
                _SOURCE_KIND_WIRE_CODES[kind]
                for kind in session.allowed_search_source_kinds
            )
            if "search" in allowed_actions
            else ""
        ),
        session.action_limits(),
        [phase, contract.contract_id],
    ]


def _initial_payload(
    binding: ContextInvocationBinding,
    projection: ContextProjection,
    checkpoint: ContextCheckpointView,
    session: ContextInvocationSession,
    materials: tuple[ContextMaterial, ...],
    *,
    phase_contract: tuple[str, ContextJsonObjectResultContract],
    action_index: int,
    allowed_actions: tuple[str, ...],
    reference_context: tuple[Mapping[str, object], ...],
) -> tuple[
    dict[str, object],
    dict[JsonPath, ContextComponentKind],
    tuple[JsonPath, ...],
]:
    component_paths: dict[JsonPath, ContextComponentKind] = {}
    duplicate_channels: dict[str, str | None] = {}
    current: object | None = None
    candidate: object | None = None
    candidate_entry: ContextManifestEntry | None = None

    def remember_duplicate(text: str, channel: str) -> None:
        previous = duplicate_channels.get(text)
        if previous is None and text not in duplicate_channels:
            duplicate_channels[text] = channel
        elif previous != channel:
            duplicate_channels[text] = None

    for unit in projection.units:
        if any(context_source_uses_catalog_alias(entry) for entry in unit.entries):
            continue
        content = _wire_projected_content(unit.entries, unit.body_utf8)
        channel = _SEMANTIC_CHANNEL_WIRE_CODES[unit.entries[0].semantic_channel]
        if type(content) is str:
            remember_duplicate(content, channel)
        elif type(content) is dict and type(content.get("input")) is str:
            remember_duplicate(content["input"], channel)
        if channel == "i":
            if current is not None:
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            current = content
            component_paths[("i",)] = ContextComponentKind.CURRENT_INPUT
        elif channel == "k":
            if candidate is not None:
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            candidate = content
            candidate_entry = unit.entries[0]
            component_paths[("n",)] = ContextComponentKind.CANDIDATE
    if current is None:
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    catalog_by_alias = {
        source.source_alias: source
        for source in projection.checkpoint_proposal.catalog_sources
    }
    material_by_source = {material.entry.source_id: material for material in materials}
    records_by_alias: dict[str, list[ContextCheckpointRecord]] = {}
    projected_by_key = {
        (record.source_alias, record.record.record_alias): record
        for record in projection.projected_records
    }
    for record in projection.projected_records:
        records_by_alias.setdefault(record.source_alias, []).append(record.record)
    changed_group_aliases = {
        *records_by_alias,
        *(record.source_alias for record in projection.removed_checkpoint_records),
        *(
            source.source_alias
            for source in projection.catalog_updates
            if source.entry.source_kind
            in {
                ContextSourceKind.CONVERSATION,
                ContextSourceKind.ACTIVE_DEBATE,
            }
        ),
    }
    proposal_source_ids = {
        source.entry.source_id for source in projection.checkpoint_proposal.sources
    }
    group_aliases = tuple(
        source.source_alias
        for source in projection.checkpoint_proposal.catalog_sources
        if source.entry.source_id in proposal_source_ids
        and source.entry.source_kind
        in {
            ContextSourceKind.CONVERSATION,
            ContextSourceKind.ACTIVE_DEBATE,
        }
        and (
            projection.mode is ContextDeliveryMode.BOOTSTRAP
            or source.source_alias in changed_group_aliases
        )
    )
    projected_records: list[list[object]] = []
    for source_alias in group_aliases:
        records = records_by_alias.get(source_alias, [])
        source = catalog_by_alias[source_alias]
        record_values = [
            _wire_record_value(
                source.entry,
                record,
                projected_by_key[(source_alias, record.record_alias)].body_utf8,
                duplicate_channels,
            )
            for record in records
        ]
        group_index = len(projected_records)
        if source.entry.source_kind is ContextSourceKind.CONVERSATION:
            material = material_by_source.get(source.entry.source_id)
            if material is None:
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            total, omitted = _conversation_counts(material)
            projected_records.append(
                ["c", _source_number(source_alias), total, omitted, record_values]
            )
            record_path_index = 4
        elif source.entry.source_kind is ContextSourceKind.ACTIVE_DEBATE:
            projected_records.append(["d", _source_number(source_alias), record_values])
            record_path_index = 2
        else:
            projected_records.append(["o", _source_number(source_alias), record_values])
            record_path_index = 2
        for record_index in range(len(records)):
            component_paths[("p", group_index, record_path_index, record_index)] = (
                _projected_component_kind(source.entry)
            )
    correlation = _action_correlation(binding, action_index)
    docs: list[list[object]] = []
    catalog_by_id = {
        source.entry.source_id: source
        for source in projection.checkpoint_proposal.catalog_sources
    }
    for unit in projection.units:
        for entry in unit.entries:
            if (
                entry.source_kind is not ContextSourceKind.WRITING_ARTIFACT
                or entry.semantic_channel is not ContextSemanticChannel.HARD_CONSTRAINT
            ):
                continue
            catalog = catalog_by_id.get(entry.source_id)
            if catalog is None:
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            index = len(docs)
            docs.append(
                [
                    _source_number(catalog.source_alias),
                    entry.provenance.locator,
                    unit.body_utf8.decode("utf-8", errors="strict"),
                ]
            )
            component_paths[("docs", index, 2)] = (
                ContextComponentKind.RETRIEVED_OBSERVATION
            )
    proposal_catalog_by_id = {
        source.entry.source_id: source
        for source in projection.checkpoint_proposal.catalog_sources
    }
    payload: dict[str, object] = {
        "c": _correlation_token(correlation),
        "q": _action_query(phase_contract, session, allowed_actions),
        "i": current,
    }
    if session.core_statuses:
        payload["core_statuses"] = [
            {
                "evidence_id": item.evidence_id,
                "locator": item.locator,
                "manifest_digest": item.manifest_digest,
                "revision": item.revision,
                "source_digest": item.source_digest,
                "source_id": item.source_id,
                "status": item.status.value,
                "utf8_bytes": item.source_utf8_bytes,
            }
            for item in session.core_statuses
        ]
    if candidate is not None:
        payload["n"] = (
            _wire_candidate_value(
                candidate,
                projection,
                checkpoint,
                materials,
            )
            if type(candidate) is str and candidate_entry is not None
            else candidate
        )
    if docs:
        payload["docs"] = docs
    if projected_records:
        payload["p"] = projected_records
    tombstones = [
        *[
            ["s", _source_number(source.source_alias)]
            for source in projection.removed_catalog_sources
        ],
        *[
            [
                "d",
                _source_number(
                    proposal_catalog_by_id[source.entry.source_id].source_alias
                ),
            ]
            for source in projection.removed_checkpoint_sources
            if source.entry.source_id in proposal_catalog_by_id
            and source.entry.source_id
            not in {
                removed.entry.source_id
                for removed in projection.removed_catalog_sources
            }
        ],
        *[
            [
                "r",
                _source_number(record.source_alias),
                record.record_alias,
            ]
            for record in projection.removed_checkpoint_records
            if record.source_alias
            not in {
                removed.source_alias for removed in projection.removed_catalog_sources
            }
        ],
    ]
    if tombstones:
        payload["t"] = tombstones
    mandatory_paths: list[JsonPath] = []
    if reference_context:
        payload["untrusted_writing_artifact_context"] = [
            dict(item) for item in reference_context
        ]
        component_paths[("untrusted_writing_artifact_context",)] = (
            ContextComponentKind.MANDATORY_REFERENCE_DESCRIPTOR
        )
        for index, item in enumerate(reference_context):
            if "content" in item:
                path = ("untrusted_writing_artifact_context", index, "content")
                component_paths[path] = ContextComponentKind.MANDATORY_REFERENCE_CONTENT
                mandatory_paths.append(path)
    return payload, component_paths, tuple(mandatory_paths)


def _continuation_payload(
    binding: ContextInvocationBinding,
    session: ContextInvocationSession,
    *,
    phase_contract: tuple[str, ContextJsonObjectResultContract],
    action_index: int,
    allowed_actions: tuple[str, ...],
    observation: str,
) -> tuple[dict[str, object], dict[JsonPath, ContextComponentKind]]:
    correlation = _action_correlation(binding, action_index)
    try:
        observation_value = json.loads(
            observation,
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (_DuplicateJsonKey, json.JSONDecodeError, TypeError, ValueError):
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE) from None
    return (
        {
            "c": _correlation_token(correlation),
            "q": _action_query(phase_contract, session, allowed_actions),
            "o": observation_value,
        },
        {("o",): ContextComponentKind.RETRIEVED_OBSERVATION},
    )


def _invocation_source_aliases(
    manifest: ContextManifest,
    projection: ContextProjection,
) -> Mapping[str, str]:
    """Return the planner-owned complete readable catalog alias map."""

    aliases = {
        source.entry.source_id: source.source_alias
        for source in projection.checkpoint_proposal.catalog_sources
    }
    expected_ids = {
        entry.source_id
        for entry in manifest.entries
        if context_source_uses_catalog_alias(entry)
    }
    if set(aliases) != expected_ids:
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    return MappingProxyType(aliases)


def _attempt_identifier() -> str:
    value: str | None = None
    try:
        value = secrets.token_hex(16)
    except (OSError, RuntimeError, ValueError):
        pass
    if type(value) is not str or len(value) != 32:
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    return f"context_attempt_{value}"


class AgentContextService:
    """Own workspace-scoped contextual composition, execution, and commit."""

    def __init__(
        self,
        workspace: Workspace,
        *,
        planning_policy: ContextPlanningPolicy = DEFAULT_CONTEXT_PLANNING_POLICY,
        action_policy: ContextActionPolicy = CONTEXT_ACTION_POLICY,
        source_policy: ContextSourcePolicy = CONTEXT_SOURCE_POLICY,
    ) -> None:
        if (
            type(workspace) is not Workspace
            or type(planning_policy) is not ContextPlanningPolicy
            or type(action_policy) is not ContextActionPolicy
            or type(source_policy) is not ContextSourcePolicy
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        self._workspace = workspace
        self._workspace_id = _workspace_context_id(workspace)
        self._planning_policy = planning_policy
        self._action_policy = action_policy
        self._source_policy = source_policy
        self._execution_policy_fingerprint = _execution_policy_fingerprint(
            planning_policy,
            action_policy,
            source_policy,
        )
        self._ledger = ContextLedger(workspace)
        self._locks: dict[tuple[str, ...], asyncio.Lock] = {}

    @property
    def workspace_id(self) -> str:
        """Return the authoritative content-free identity of this workspace."""

        return self._workspace_id

    @property
    def policy_fingerprint(self) -> str:
        """Return the exact provider-neutral execution-policy fingerprint."""

        return self._execution_policy_fingerprint

    def scope(
        self,
        session_id: str,
        subject_kind: ContextSubjectKind,
        subject_id: str,
        agent_id: str,
    ) -> ContextScope:
        """Construct one scope without accepting caller-selected workspace identity."""

        return ContextScope(
            self._workspace_id,
            session_id,
            subject_kind,
            subject_id,
            agent_id,
        )

    def session_continuation(
        self, session_id: str, agent_id: str,
    ) -> ContextContinuationTransaction:
        """Serialize a lead operation until its authoritative result is durable."""

        return ContextContinuationTransaction(ContextContinuationIdentity(
            self._workspace_id, session_id, agent_id,
        ))

    def _scope_for(self, request: ContextInvocationRequest) -> ContextScope:
        return self.scope(
            request.session_id,
            request.subject_kind,
            request.subject_id,
            request.agent_id,
        )

    def _lock_for(
        self,
        scope: ContextScope,
        provider_id: str,
        definition_fingerprint: str,
    ) -> asyncio.Lock:
        key = (
            scope.workspace_id,
            scope.session_id,
            scope.subject_kind.value,
            scope.subject_id,
            scope.agent_id,
            provider_id,
            definition_fingerprint,
            self._execution_policy_fingerprint,
        )
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def _compose_context(
        self,
        request: ContextInvocationRequest,
        scope: ContextScope,
        writing_artifacts: WritingArtifactSourceAdapter,
        focused_conversation: FocusedConversationSourceAdapter,
        active_debate: ActiveDebateSourceAdapter,
        own_agent_state: OwnAgentStateSourceAdapter,
    ) -> tuple[ContextSourceRegistry, ContextManifest, tuple[ContextMaterial, ...]]:
        registry = ContextSourceRegistry(
            writing_artifacts,
            focused_conversation,
            active_debate,
            own_agent_state,
        )
        if registry.scope != scope:
            raise ContextSourceError(ContextSourceErrorCode.SCOPE_MISMATCH)
        descriptors = registry.inventory(self._source_policy)
        manifest = self._manifest_from_descriptors(request, scope, descriptors)
        source_entries = manifest.entries[len(request.materials) :]
        source_materials: list[ContextMaterial] = []
        for entry in source_entries:
            snapshot = registry.capture(manifest, entry, self._source_policy)
            records = tuple(
                ContextRecordMaterial(
                    ContextRecordCursor(
                        record_id=record.record_id,
                        boundary_kind=record.boundary.kind.value,
                        boundary_start=record.boundary.start,
                        boundary_end=record.boundary.end,
                        boundary_first_id=record.boundary.first_id,
                        boundary_last_id=record.boundary.last_id,
                        content_digest=record.content_digest,
                        utf8_bytes=record.utf8_bytes,
                    ),
                    record.content.encode("utf-8", errors="strict"),
                )
                for record in snapshot.records
            )
            source_materials.append(ContextMaterial(entry, snapshot.content, records))
        materials = (*request.materials, *source_materials)
        if len({item.entry.source_id for item in materials}) != len(materials):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        return registry, manifest, tuple(materials)

    def _manifest_from_descriptors(
        self,
        request: ContextInvocationRequest,
        scope: ContextScope,
        descriptors: tuple[ContextSourceDescriptor, ...],
    ) -> ContextManifest:
        """Classify one authoritative content-free source inventory."""

        classifications = {
            item.source_id: item for item in request.source_classifications
        }
        descriptor_ids = {item.source_id for item in descriptors}
        unmatched_classifications = set(classifications).difference(descriptor_ids)
        if any(
            not classifications[source_id].allow_absent
            for source_id in unmatched_classifications
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        source_entries: list[ContextManifestEntry] = []
        for index, descriptor in enumerate(descriptors):
            classification = classifications.get(descriptor.source_id)
            if classification is None:
                classification = ContextSourceClassification(
                    descriptor.source_id,
                    ContextSemanticChannel.OPTIONAL_BACKGROUND,
                    ContextPriority.P3_OPTIONAL,
                    index,
                )
            source_entries.append(_descriptor_entry(descriptor, classification))
        base_entries = tuple(item.entry for item in request.materials)
        entries = (*base_entries, *source_entries)
        if len(entries) > MAX_CONTEXT_LEDGER_SOURCES:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        return ContextManifest(scope, entries)

    @staticmethod
    def _provider_identity(provider: ContextProvider) -> tuple[str, str]:
        provider_id = getattr(provider, "provider_id", None)
        definition_fingerprint = getattr(
            provider,
            "definition_fingerprint",
            None,
        )
        try:
            _safe_identity(provider_id)
            _safe_digest(definition_fingerprint)
        except ContextActionError:
            raise _execution_error(
                ContextExecutionErrorCode.PROVIDER_CONTRACT
            ) from None
        for name in (
            "invocation",
            "inspect_context_thread",
            "prepare_references",
            "invoke_context",
            "complete_references",
            "abort_references",
            "taint_context_thread",
        ):
            if not callable(getattr(provider, name, None)):
                raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        assert isinstance(provider_id, str)
        assert isinstance(definition_fingerprint, str)
        return provider_id, definition_fingerprint

    @staticmethod
    def _append_call(
        ledger: ContextLedger,
        key: ContextLedgerKey,
        generation: int,
        attempt_id: str,
        calls: list[ContextLedgerProviderCall],
        call_id: str,
        accounting: ProviderCallAccounting,
    ) -> int:
        provider_call = ContextLedgerProviderCall(
            len(calls) + 1,
            call_id,
            accounting,
        )
        inspection = ledger.append_provider_call(
            key,
            expected_generation=generation,
            attempt_id=attempt_id,
            provider_call=provider_call,
        )
        calls.append(provider_call)
        return inspection.generation

    @classmethod
    def _append_failure_call_without_replacing(
        cls,
        ledger: ContextLedger,
        key: ContextLedgerKey,
        generation: int,
        attempt_id: str,
        calls: list[ContextLedgerProviderCall],
        call_id: str,
        accounting: ProviderCallAccounting,
    ) -> int:
        """Best-effort retain facts while preserving the primary failure."""

        try:
            return cls._append_call(
                ledger,
                key,
                generation,
                attempt_id,
                calls,
                call_id,
                accounting,
            )
        except BaseException:
            return generation

    @staticmethod
    def _failure_outcome(
        failure: BaseException,
        *,
        stage: str,
        history_advanced: bool,
        terminal_provider_succeeded: bool,
    ) -> ContextLedgerAttemptOutcome:
        facts = context_provider_failure_facts(failure)
        submitted = (
            facts.accounting is not None
            and facts.accounting.state is ProviderCallState.SUBMITTED
        )
        if isinstance(failure, asyncio.CancelledError):
            return ContextLedgerAttemptOutcome.CANCELLED
        if facts.timed_out:
            return ContextLedgerAttemptOutcome.TIMED_OUT
        if (
            isinstance(failure, ProviderAuthenticationRequiredError)
            and failure.replay_safe
        ):
            return ContextLedgerAttemptOutcome.FAILED
        if isinstance(failure, ContextSourceError):
            return ContextLedgerAttemptOutcome.SOURCE_INVALID
        if stage == "decode":
            return ContextLedgerAttemptOutcome.MALFORMED
        if stage == "action" and isinstance(failure, ContextActionError):
            return ContextLedgerAttemptOutcome.MALFORMED
        if stage in {"source", "source_revalidation"}:
            return ContextLedgerAttemptOutcome.SOURCE_INVALID
        if stage in {"answer", "answer_validation"}:
            return ContextLedgerAttemptOutcome.OUTPUT_INVALID
        if stage in {"reference", "reference_complete"}:
            return ContextLedgerAttemptOutcome.REFERENCE_INVALID
        if stage == "exhausted":
            return ContextLedgerAttemptOutcome.INCOMPLETE
        if submitted or history_advanced or terminal_provider_succeeded:
            return ContextLedgerAttemptOutcome.AMBIGUOUS
        if stage == "provider":
            return ContextLedgerAttemptOutcome.FAILED
        return ContextLedgerAttemptOutcome.INCOMPLETE

    @staticmethod
    def _should_taint_context(
        failure: BaseException,
        outcome: ContextLedgerAttemptOutcome,
        *,
        history_advanced: bool,
        terminal_provider_succeeded: bool,
    ) -> bool:
        facts = context_provider_failure_facts(failure)
        submitted = (
            facts.accounting is not None
            and facts.accounting.state is ProviderCallState.SUBMITTED
        )
        return bool(
            outcome is ContextLedgerAttemptOutcome.AMBIGUOUS
            or facts.taint_existing_thread
            or outcome is ContextLedgerAttemptOutcome.MALFORMED
            or (
                outcome
                in {
                    ContextLedgerAttemptOutcome.CANCELLED,
                    ContextLedgerAttemptOutcome.TIMED_OUT,
                }
                and submitted
            )
            or history_advanced
            or terminal_provider_succeeded
        )

    async def invoke(
        self,
        request: ContextInvocationRequest,
        provider: ContextProvider,
        *,
        writing_artifacts: WritingArtifactSourceAdapter,
        focused_conversation: FocusedConversationSourceAdapter,
        active_debate: ActiveDebateSourceAdapter,
        own_agent_state: OwnAgentStateSourceAdapter,
        answer_validator: Callable[[str, ContextAnswerAuthorization], object]
        | None = None,
        answer_diagnostic: Callable[[str], tuple[DomainValidationRule, ...]] | None = None,
        validation_sink: Callable[[str, tuple[str, ...]], object] | None = None,
        event_sink: ContextExecutionEventSink | None = None,
        progress_sink: ContextActionProgressSink | None = None,
        phase: str | None = None,
        round_number: int | None = None,
        continuation: ContextContinuationTransaction | None = None,
        session_history: SessionHistorySnapshot | None = None,
        publication_validator: Callable[[], bool] | None = None,
        abandoned_operation_ids: tuple[str, ...] = (),
    ) -> ContextInvocationResult:
        """Execute under the exact scope lock and active continuation lease.

        Abandoned operations identify durable read-only failures. Only an exact
        matching unfinished session attempt may retire its native continuation;
        a successful checkpoint for another operation is preserved.
        """

        if type(abandoned_operation_ids) is not tuple:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        for operation_id in abandoned_operation_ids:
            _safe_identity(operation_id)
        if abandoned_operation_ids and continuation is None:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(request) is not ContextInvocationRequest:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if request.execution_workspace is not None:
            request.execution_workspace.require_current()
        physical_call_limit = (
            self._action_policy.max_provider_calls
            if request.physical_call_limit is None
            else request.physical_call_limit
        )
        if (
            physical_call_limit > self._action_policy.max_provider_calls
            or physical_call_limit <= 0
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        _validate_sync_callback(answer_validator)
        _validate_sync_callback(answer_diagnostic)
        _validate_sync_callback(validation_sink)
        _validate_sync_callback(event_sink)
        _validate_sync_callback(progress_sink)
        if request.subject_kind is ContextSubjectKind.DEBATE:
            if event_sink is not None and (phase is None or round_number is None):
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            if (phase is None) is not (round_number is None):
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            if phase is not None:
                _safe_identity(phase)
                if type(round_number) is not int or round_number < 0:
                    raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        elif phase is not None or round_number is not None:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        provider_id, definition_fingerprint = self._provider_identity(provider)
        reference_operation_id = getattr(provider, "reference_operation_id", None)
        if reference_operation_id != request.reference_operation_id:
            raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        scope = self._scope_for(request)
        if continuation is not None:
            continuation.require_active()
            if (
                not continuation.identity.accepts(scope)
                or type(session_history) is not SessionHistorySnapshot
                or session_history.session_id != scope.session_id
                or not callable(publication_validator)
            ):
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        elif (
            session_history is not None or publication_validator is not None
            or request.session_state_json is not None
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        lock = self._lock_for(scope, provider_id, definition_fingerprint)
        policy_fingerprint = self._execution_policy_fingerprint
        if request.execution_workspace is None:
            context_manager = (
                provider.invocation(scope, policy_fingerprint)
                if continuation is None
                else provider.invocation(
                    scope,
                    policy_fingerprint,
                    continuation_identity=continuation.identity,
                )
            )
        else:
            context_manager = (
                provider.invocation(
                    scope,
                    policy_fingerprint,
                    execution_workspace=request.execution_workspace,
                )
                if continuation is None
                else provider.invocation(
                    scope,
                    policy_fingerprint,
                    continuation_identity=continuation.identity,
                    execution_workspace=request.execution_workspace,
                )
            )
        if not hasattr(context_manager, "__aenter__") or not hasattr(
            context_manager,
            "__aexit__",
        ):
            raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        checkpoint_scope = (
            scope if continuation is None else continuation.identity.checkpoint_scope()
        )
        key = ContextLedgerKey(checkpoint_scope, provider_id)
        result: ContextInvocationResult | None = None
        try:
            async with AsyncExitStack() as provider_stack:
                await provider_stack.enter_async_context(context_manager)
                async with lock:
                    if abandoned_operation_ids:
                        assert continuation is not None
                        inspection = self._ledger.inspect(key)
                        record = inspection.record
                        if (record is not None and record.attempt is not None
                            and record.attempt.plan.operation_id in abandoned_operation_ids
                            and record.attempt.outcome is not ContextLedgerAttemptOutcome.COMMITTED):
                            # Invalidate first: a crash during native retirement must
                            # still force the next owner to bootstrap, never resume.
                            self._ledger.invalidate(key, expected_generation=inspection.generation)
                            if request.execution_workspace is None:
                                provider.taint_context_thread(scope, policy_fingerprint)
                                remaining_thread = provider.inspect_context_thread(
                                    scope, policy_fingerprint
                                )
                            else:
                                provider.taint_context_thread(
                                    scope,
                                    policy_fingerprint,
                                    execution_workspace=request.execution_workspace,
                                )
                                remaining_thread = provider.inspect_context_thread(
                                    scope,
                                    policy_fingerprint,
                                    execution_workspace=request.execution_workspace,
                                )
                            if remaining_thread is not None:
                                raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
                    result = await self._invoke_locked(
                        request,
                        provider,
                        provider_id=provider_id,
                        definition_fingerprint=definition_fingerprint,
                        scope=scope,
                        writing_artifacts=writing_artifacts,
                        focused_conversation=focused_conversation,
                        active_debate=active_debate,
                        own_agent_state=own_agent_state,
                        physical_call_limit=physical_call_limit,
                        answer_validator=answer_validator,
                        answer_diagnostic=answer_diagnostic,
                        validation_sink=validation_sink,
                        event_sink=event_sink,
                        progress_sink=progress_sink,
                        phase=phase,
                        round_number=round_number,
                        continuation=continuation,
                        session_history=session_history,
                        publication_validator=publication_validator,
                    )
                    if (
                        continuation is not None
                        and provider_id == "codex"
                        and result.checkpoint_pending
                    ):
                        continuation.retain_async_context_manager(
                            provider_stack.pop_all()
                        )
        except BaseException as failure:
            if result is not None and not result.checkpoint_pending:
                try:
                    self._ledger.invalidate(
                        key,
                        expected_generation=result.ledger_generation,
                    )
                except BaseException as invalidation_failure:
                    try:
                        failure.add_note(
                            "context checkpoint invalidation failed: "
                            f"{type(invalidation_failure).__name__}"
                        )
                    except BaseException:
                        pass
            raise
        assert result is not None
        return result

    async def _invoke_locked(
        self,
        request: ContextInvocationRequest,
        provider: ContextProvider,
        *,
        provider_id: str,
        definition_fingerprint: str,
        scope: ContextScope,
        writing_artifacts: WritingArtifactSourceAdapter,
        focused_conversation: FocusedConversationSourceAdapter,
        active_debate: ActiveDebateSourceAdapter,
        own_agent_state: OwnAgentStateSourceAdapter,
        physical_call_limit: int,
        answer_validator: Callable[[str, ContextAnswerAuthorization], object] | None,
        answer_diagnostic: Callable[[str], tuple[DomainValidationRule, ...]] | None,
        validation_sink: Callable[[str, tuple[str, ...]], object] | None,
        event_sink: ContextExecutionEventSink | None,
        progress_sink: ContextActionProgressSink | None,
        phase: str | None,
        round_number: int | None,
        continuation: ContextContinuationTransaction | None,
        session_history: SessionHistorySnapshot | None,
        publication_validator: Callable[[], bool] | None,
    ) -> ContextInvocationResult:
        policy_fingerprint = self._execution_policy_fingerprint
        operation_id = request.operation_id

        def inspect_provider_thread() -> str | None:
            if request.execution_workspace is None:
                return provider.inspect_context_thread(scope, policy_fingerprint)
            return provider.inspect_context_thread(
                scope,
                policy_fingerprint,
                execution_workspace=request.execution_workspace,
            )

        def taint_provider_thread() -> None:
            if request.execution_workspace is None:
                provider.taint_context_thread(scope, policy_fingerprint)
                return
            provider.taint_context_thread(
                scope,
                policy_fingerprint,
                execution_workspace=request.execution_workspace,
            )

        def require_execution_workspace_current() -> None:
            if request.execution_workspace is not None:
                request.execution_workspace.require_current()
                return
            callback = getattr(
                provider,
                "require_current_execution_workspace",
                None,
            )
            if callback is not None:
                _require_sync_none(callback(scope, policy_fingerprint))

        checkpoint_scope = (
            scope if continuation is None else continuation.identity.checkpoint_scope()
        )
        key = ContextLedgerKey(checkpoint_scope, provider_id)
        attempt_id: str | None = None
        generation: int | None = None
        calls: list[ContextLedgerProviderCall] = []
        submitted_accountings: list[ProviderCallAccounting] = []
        action_session: ContextInvocationSession | None = None
        reference_started = False
        history_advanced = False
        terminal_provider_succeeded = False
        stage = "preflight"
        provider_failure = False
        retired_thread_id: str | None = None
        checkpoint_committed = False
        call_finalizer: ContextProviderCallFinalizer | None = None
        call_validation_details: tuple[str, ...] = ()
        response_rejection_code: ContextProviderTerminalCode | None = None

        def execution_event(
            kind: ContextExecutionEventKind,
            **facts: object,
        ) -> ContextExecutionEvent | None:
            """Build one event with authoritative call-local identity facts."""

            if event_sink is None:
                return None
            return ContextExecutionEvent(
                kind,
                scope,
                operation_id,
                phase=phase,
                round_number=round_number,
                **facts,
            )

        def progress_event(
            kind: ContextActionProgressKind,
            action_index: int,
            **facts: object,
        ) -> ContextActionProgressEvent:
            return ContextActionProgressEvent(
                kind=kind,
                agent_id=scope.agent_id,
                operation_id=operation_id,
                action_index=action_index,
                **facts,
            )

        def reject_response(
            finalizer: ContextProviderCallFinalizer,
            code: ContextProviderTerminalCode,
        ) -> None:
            """Finalize and retain one deterministic raw-response rejection."""

            nonlocal response_rejection_code
            details = call_validation_details
            if not details and code is ContextProviderTerminalCode.RESPONSE_CORRELATION_MISMATCH:
                details = bounded_chunks(("$.action: response correlation must match the current request",))
            try:
                retain_details = getattr(finalizer, "set_validation_details", None)
                if callable(retain_details):
                    retain_details(details)
            except BaseException:
                pass
            _reject_provider_call_without_replacing(finalizer, code)
            response_rejection_code = code

        try:
            stage = "source"
            registry, manifest, materials = self._compose_context(
                request,
                scope,
                writing_artifacts,
                focused_conversation,
                active_debate,
                own_agent_state,
            )
            manifest_digest = _manifest_fingerprint(manifest)
            core_statuses = (
                registry.canonical_core_statuses(
                    manifest_digest,
                    self._source_policy,
                )
                if request.include_core_statuses
                else ()
            )
            stage = "preflight"
            active_thread_id = inspect_provider_thread()
            if active_thread_id is not None:
                _safe_identity(active_thread_id)
            execution_conditions = ContextPlanningConditions(
                checkpoint_scope,
                provider_id,
                definition_fingerprint,
                policy_fingerprint,
            )
            planning_conditions = replace(
                execution_conditions,
                policy_fingerprint=self._planning_policy.fingerprint,
            )
            inspection = self._ledger.inspect(key)
            planning_checkpoint = inspection.checkpoint_view
            if planning_checkpoint.state is ContextCheckpointState.READY:
                checkpoint_conditions = planning_checkpoint.conditions
                assert checkpoint_conditions is not None
                if checkpoint_conditions.policy_fingerprint == policy_fingerprint:
                    planning_checkpoint = replace(
                        planning_checkpoint,
                        conditions=replace(
                            checkpoint_conditions,
                            policy_fingerprint=self._planning_policy.fingerprint,
                        ),
                    )
                else:
                    # Leave the incompatible persisted record untouched while
                    # ensuring neither its checkpoint nor its native thread can
                    # be resumed by the current contextual attempt.
                    planning_checkpoint = ContextCheckpointView(
                        ContextCheckpointState.STALE
                    )
            projection = plan_context_projection(
                replace(manifest, scope=checkpoint_scope),
                materials,
                policy=self._planning_policy,
                conditions=planning_conditions,
                checkpoint=planning_checkpoint,
                active_provider_thread_id=active_thread_id,
                mandatory_references=request.mandatory_references,
                fresh=request.fresh,
            )
            history_delivery = None
            if session_history is not None:
                try:
                    history_delivery = session_history.delivery(
                        planning_checkpoint.session_history_coverage
                        if projection.mode is ContextDeliveryMode.DELTA else ()
                    )
                except SessionContextHistoryChangedError:
                    inspection = self._ledger.invalidate(
                        key, expected_generation=inspection.generation,
                    )
                    planning_checkpoint = inspection.checkpoint_view
                    projection = plan_context_projection(
                        replace(manifest, scope=checkpoint_scope), materials,
                        policy=self._planning_policy,
                        conditions=planning_conditions,
                        checkpoint=planning_checkpoint,
                        active_provider_thread_id=active_thread_id,
                        mandatory_references=request.mandatory_references,
                        fresh=request.fresh,
                    )
                    history_delivery = session_history.delivery()
                if request.session_state_json is not None:
                    history_delivery["current_state"] = json.loads(request.session_state_json)
                history_delivery["delivery_mode"] = projection.mode.value
            projection = replace(
                projection,
                checkpoint_proposal=replace(
                    projection.checkpoint_proposal,
                    conditions=execution_conditions,
                    session_history_coverage=(
                        () if session_history is None else session_history.coverage
                    ),
                ),
            )
            binding = ContextInvocationBinding(
                scope,
                request.operation_id,
                request.result_correlation_id,
                manifest,
                request.reference_operation_id,
                request.mandatory_references,
            )
            action_session = ContextInvocationSession(
                binding,
                registry,
                action_policy=self._action_policy,
                source_policy=self._source_policy,
                source_aliases=_invocation_source_aliases(
                    manifest,
                    projection,
                ),
                allowed_search_source_kinds=(request.allowed_search_source_kinds),
                physical_call_limit=physical_call_limit,
                core_statuses=core_statuses,
            )

            # All statically available mandatory material is checked before the
            # local reference load. Its generated observation is checked again
            # in the exact substantive payload before physical submission.
            if history_delivery is not None:
                known_payload, known_component_paths, known_reference_paths = _initial_payload(
                    binding, projection, planning_checkpoint, action_session,
                    materials, phase_contract=_invocation_phase_contract(request),
                    action_index=1,
                    allowed_actions=action_session.allowed_actions(
                        after=None, search_had_hits=False,
                    ),
                    reference_context=(),
                )
                known_payload["h"] = history_delivery
                known_component_paths[("h",)] = ContextComponentKind.CONVERSATION_CONTEXT
                known_payload, _ = compact_context_payload(
                    known_payload, known_component_paths, known_reference_paths,
                )
                if len(_canonical_bytes(known_payload)) > MAX_SESSION_CONTEXT_UTF8_BYTES:
                    raise SessionContextCapacityError()

            if projection.mode is ContextDeliveryMode.BOOTSTRAP:
                retired_thread_id = active_thread_id
                if retired_thread_id is not None:
                    stage = "thread_reset"
                    taint_provider_thread()
                    remaining_thread = inspect_provider_thread()
                    if remaining_thread is not None:
                        raise _execution_error(
                            ContextExecutionErrorCode.PROVIDER_CONTRACT
                        )
                active_thread_id = None
            _emit_context_event(
                event_sink,
                execution_event(
                    (
                        ContextExecutionEventKind.BOOTSTRAP
                        if projection.mode is ContextDeliveryMode.BOOTSTRAP
                        else ContextExecutionEventKind.DELTA
                    ),
                    selected_source_count=(
                        projection.accounting.selected_logical_source_count
                    ),
                    selected_source_utf8_bytes=(
                        projection.accounting.selected_logical_utf8_bytes
                    ),
                    omitted_searchable_source_count=(
                        projection.accounting.searchable.unprojected_source_count
                    ),
                    omitted_searchable_source_utf8_bytes=(
                        projection.accounting.searchable.unprojected_source_utf8_bytes
                    ),
                    mandatory_reference_count=(
                        projection.accounting.mandatory_reference_count
                    ),
                    mandatory_reference_utf8_bytes=(
                        projection.accounting.mandatory_reference_utf8_bytes
                    ),
                ),
            )
            omitted_count = projection.accounting.searchable.unprojected_source_count
            if omitted_count:
                _emit_context_event(
                    event_sink,
                    execution_event(
                        ContextExecutionEventKind.OMISSION,
                        omitted_searchable_source_count=omitted_count,
                        omitted_searchable_source_utf8_bytes=(
                            projection.accounting.searchable.unprojected_source_utf8_bytes
                        ),
                    ),
                )
            attempt_id = _attempt_identifier()
            proposal_digest = context_checkpoint_proposal_digest(
                projection.checkpoint_proposal
            )
            plan = ContextLedgerAttemptPlan(
                attempt_id=attempt_id,
                operation_id=request.operation_id,
                result_correlation_id=request.result_correlation_id,
                proposal_digest=proposal_digest,
                delivery_mode=projection.mode,
                bootstrap_reason=projection.bootstrap_reason,
                proposed_delta_depth=(projection.checkpoint_proposal.delta_depth),
                mandatory_reference_count=len(request.mandatory_references),
                planned_provider_thread_id=active_thread_id,
                reference_operation_id=request.reference_operation_id,
                projection_accounting=projection.accounting,
            )
            reserved = self._ledger.reserve_attempt(
                key,
                expected_generation=inspection.generation,
                plan=plan,
            )
            generation = reserved.generation

            def replay_sources_unchanged() -> bool:
                """Recheck captured source identity before replaying held payload bytes."""

                try:
                    if request.include_core_statuses and (
                        registry.canonical_core_statuses(manifest_digest, self._source_policy)
                        != core_statuses
                    ):
                        return False
                    return self._manifest_from_descriptors(
                        request, scope, registry.inventory(self._source_policy),
                    ) == manifest
                except Exception:
                    return False

            current_thread_id = active_thread_id
            reference_context: tuple[Mapping[str, object], ...] = ()
            if request.mandatory_references:
                stage = "reference"
                reference_started = True
                prelude_request = ContextReferencePreludeRequest(
                    scope, policy_fingerprint,
                    f"{attempt_id}_call_{len(calls) + 1}",
                    request.reference_operation_id, current_thread_id,
                    None if continuation is None else continuation.identity,
                    request.execution_workspace,
                )
                prelude = await provider.prepare_references(prelude_request)
                stage = "reference_result"
                if type(prelude) is not ContextReferencePreludeResult:
                    raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
                reference_context = prelude.context

            observation: str | None = None
            pending_read: ContextReadReceipt | None = None
            terminal_call_id: str | None = None
            previous_action: str | None = None
            previous_search_had_hits = False
            phase_contract = _invocation_phase_contract(request)
            substantive_call_limit = physical_call_limit - len(calls)
            for _round in range(substantive_call_limit):
                if len(calls) >= physical_call_limit:
                    break
                action_index = action_session.next_action_index
                allowed_actions = action_session.allowed_actions(
                    after=previous_action,
                    search_had_hits=previous_search_had_hits,
                )
                call_id = f"{attempt_id}_call_{len(calls) + 1}"
                correlation = _action_correlation(binding, action_index)
                schema = _context_action_schema(
                    allowed_actions=allowed_actions,
                    result_contract=phase_contract[1],
                    allowed_search_source_kinds=(
                        request.allowed_search_source_kinds
                    ),
                    action_limits=action_session.action_limits(),
                )
                if observation is None:
                    payload, component_paths, reference_paths = _initial_payload(
                        binding,
                        projection,
                        planning_checkpoint,
                        action_session,
                        materials,
                        phase_contract=phase_contract,
                        action_index=action_index,
                        allowed_actions=allowed_actions,
                        reference_context=reference_context,
                    )
                    if history_delivery is not None:
                        payload["h"] = history_delivery
                        component_paths[("h",)] = ContextComponentKind.CONVERSATION_CONTEXT
                    call_kind = (
                        ProviderCallKind.DEBATE
                        if scope.subject_kind is ContextSubjectKind.DEBATE
                        else (
                            ProviderCallKind.COLLABORATION_INITIAL
                            if request.invocation_kind is not None
                            else ProviderCallKind.CONVERSATION_RETRIEVAL_INITIAL
                        )
                    )
                    reference_context = ()
                else:
                    payload, component_paths = _continuation_payload(
                        binding,
                        action_session,
                        phase_contract=phase_contract,
                        action_index=action_index,
                        allowed_actions=allowed_actions,
                        observation=observation,
                    )
                    reference_paths = ()
                    call_kind = (
                        ProviderCallKind.DEBATE
                        if scope.subject_kind is ContextSubjectKind.DEBATE
                        else (
                            ProviderCallKind.COLLABORATION_CONTINUATION
                            if request.invocation_kind is not None
                            else ProviderCallKind.CONVERSATION_RETRIEVAL_CONTINUATION
                        )
                    )
                payload, component_paths = compact_context_payload(
                    payload, component_paths, reference_paths,
                )
                if (
                    observation is None
                    and history_delivery is not None
                    and len(_canonical_bytes(payload)) > MAX_SESSION_CONTEXT_UTF8_BYTES
                ):
                    raise SessionContextCapacityError()
                call_request = ContextProviderCallRequest(
                    scope=scope,
                    policy_fingerprint=policy_fingerprint,
                    call_id=call_id,
                    call_kind=call_kind,
                    schema=schema,
                    payload=payload,
                    component_paths=component_paths,
                    mandatory_reference_paths=reference_paths,
                    expected_thread_id=current_thread_id,
                    continuation_identity=(
                        None if continuation is None else continuation.identity
                    ),
                    execution_workspace=request.execution_workspace,
                )
                if continuation is not None and (
                    len(_canonical_bytes(payload)) > MAX_SESSION_CONTEXT_UTF8_BYTES
                ):
                    raise SessionContextCapacityError()
                stage = "provider"
                provider_failure = True
                for auth_attempt in range(2):
                    call_id = call_request.call_id
                    try:
                        if auth_attempt:
                            with authentication_replay(auth_failure, replay_sources_unchanged):
                                result = await provider.invoke_context(call_request)
                        else:
                            result = await provider.invoke_context(call_request)
                        break
                    except BaseException as failure:
                        if (
                            isinstance(failure, ProviderAuthenticationRequiredError)
                            and failure.replay_aborted
                        ):
                            raise
                        facts = context_provider_failure_facts(failure)
                        recorded_before = len(calls)
                        if facts.accounting is not None:
                            if facts.accounting.state is ProviderCallState.SUBMITTED:
                                submitted_accountings.append(facts.accounting)
                            generation = self._append_failure_call_without_replacing(
                                self._ledger, key, generation, attempt_id,
                                calls, call_id, facts.accounting,
                            )
                        if (
                            not isinstance(failure, ProviderAuthenticationRequiredError)
                            or auth_attempt
                        ):
                            raise
                        try:
                            authenticated = await request_authentication(failure)
                        except BaseException:
                            failure.clear_replay_validator()
                            raise
                        if not (
                            authenticated and failure.replay_safe
                            and not facts.timed_out and not facts.submission_uncertain
                            and not facts.taint_existing_thread
                            and len(calls) == recorded_before + 1
                            and len(calls) < physical_call_limit
                        ):
                            raise
                        if not failure.validate_replay() or not replay_sources_unchanged():
                            raise
                        auth_failure = failure
                        call_request = replace(
                            call_request,
                            call_id=f"{attempt_id}_call_{len(calls) + 1}",
                        )
                terminal_provider_succeeded = True
                if type(result) is not ContextProviderCallResult:
                    failure = _execution_error(
                        ContextExecutionErrorCode.PROVIDER_CONTRACT
                    )
                    accounting = getattr(result, "accounting", None)
                    if (
                        type(accounting) is ProviderCallAccounting
                        and accounting.state is ProviderCallState.SUBMITTED
                    ):
                        submitted_accountings.append(accounting)
                        record_context_provider_failure(
                            failure,
                            ContextProviderFailureFacts(
                                accounting,
                                taint_existing_thread=True,
                            ),
                        )
                        generation = self._append_failure_call_without_replacing(
                            self._ledger,
                            key,
                            generation,
                            attempt_id,
                            calls,
                            call_id,
                            accounting,
                        )
                    raise failure
                call_finalizer = result.finalizer
                call_validation_details = ()
                submitted_accountings.append(result.accounting)
                generation = self._append_call(
                    self._ledger,
                    key,
                    generation,
                    attempt_id,
                    calls,
                    call_id,
                    result.accounting,
                )
                require_execution_workspace_current()
                if result.accounting.payload.call_kind is not call_kind:
                    finalizer = call_finalizer
                    call_finalizer = None
                    if finalizer is not None:
                        reject_response(
                            finalizer,
                            ContextProviderTerminalCode.RESPONSE_CORRELATION_MISMATCH,
                        )
                    failure = _execution_error(
                        ContextExecutionErrorCode.PROVIDER_CONTRACT
                    )
                    record_context_provider_failure(
                        failure,
                        ContextProviderFailureFacts(
                            result.accounting,
                            taint_existing_thread=True,
                        ),
                    )
                    raise failure
                if (
                    current_thread_id is not None
                    and result.thread_id != current_thread_id
                ):
                    finalizer = call_finalizer
                    call_finalizer = None
                    if finalizer is not None:
                        reject_response(
                            finalizer,
                            ContextProviderTerminalCode.RESPONSE_CORRELATION_MISMATCH,
                        )
                    failure = _execution_error(
                        ContextExecutionErrorCode.CONTINUATION_MISMATCH
                    )
                    record_context_provider_failure(
                        failure,
                        ContextProviderFailureFacts(
                            result.accounting,
                            taint_existing_thread=True,
                        ),
                    )
                    raise failure
                if (
                    retired_thread_id is not None
                    and result.thread_id == retired_thread_id
                ):
                    finalizer = call_finalizer
                    call_finalizer = None
                    if finalizer is not None:
                        reject_response(
                            finalizer,
                            ContextProviderTerminalCode.RESPONSE_CORRELATION_MISMATCH,
                        )
                    failure = _execution_error(
                        ContextExecutionErrorCode.CONTINUATION_MISMATCH
                    )
                    record_context_provider_failure(
                        failure,
                        ContextProviderFailureFacts(
                            result.accounting,
                            taint_existing_thread=True,
                        ),
                    )
                    raise failure
                provider_failure = False
                current_thread_id = result.thread_id
                if pending_read is not None:
                    stage = "observation_delivery"
                    action_session.complete_read(pending_read)
                    pending_read = None
                stage = "decode"
                try:
                    schema_issues = diagnose_json_text(result.response_text, schema)
                    if schema_issues:
                        raise _execution_error(ContextExecutionErrorCode.MALFORMED_ACTION)
                    action = _decode_context_action(
                        result.response_text,
                        correlation=correlation,
                        allowed_actions=allowed_actions,
                        result_contract=phase_contract[1],
                        allowed_search_source_kinds=(
                            request.allowed_search_source_kinds
                        ),
                        resolve_handle=action_session.resolve_wire_handle,
                        completed_handles=action_session.completed_handles,
                    )
                except ContextExecutionError as decode_failure:
                    call_validation_details = _context_validation_details(
                        result.response_text, schema_issues, decode_failure,
                    )
                    if validation_sink is not None:
                        _require_sync_none(validation_sink(call_id, call_validation_details))
                    if (
                        decode_failure.code
                        is ContextExecutionErrorCode.MALFORMED_ACTION
                        and action_index == substantive_call_limit
                    ):
                        try:
                            exhausted_action = _decode_context_action(
                                result.response_text,
                                correlation=correlation,
                                allowed_actions=("search", "read", "answer"),
                                result_contract=phase_contract[1],
                                allowed_search_source_kinds=(
                                    request.allowed_search_source_kinds
                                ),
                                resolve_handle=(action_session.resolve_wire_handle),
                                completed_handles=action_session.completed_handles,
                            )
                        except ContextExecutionError:
                            finalizer = call_finalizer
                            call_finalizer = None
                            if finalizer is not None:
                                reject_response(
                                    finalizer,
                                    _context_provider_decode_terminal_code(
                                        decode_failure
                                    ),
                                )
                            raise decode_failure
                        if type(exhausted_action) in {
                            ContextSearchAction,
                            ContextReadAction,
                        }:
                            stage = "exhausted"
                            finalizer = call_finalizer
                            call_finalizer = None
                            if finalizer is not None:
                                reject_response(
                                    finalizer,
                                    ContextProviderTerminalCode.ACTION_EXHAUSTED,
                                )
                            raise _execution_error(
                                ContextExecutionErrorCode.ACTION_EXHAUSTED
                            )
                    finalizer = call_finalizer
                    call_finalizer = None
                    if finalizer is not None:
                        reject_response(
                            finalizer,
                            _context_provider_decode_terminal_code(decode_failure),
                        )
                    raise
                terminal_provider_succeeded = False
                if type(action) is ContextSearchAction:
                    history_advanced = True
                    stage = "action_observer"
                    _emit_context_event(
                        event_sink,
                        execution_event(
                            ContextExecutionEventKind.SEARCH_STARTED,
                            action_index=action_index,
                        ),
                    )
                    _emit_action_progress(
                        progress_sink,
                        progress_event(
                            ContextActionProgressKind.SEARCH_STARTED,
                            action_index,
                            queries=tuple(
                                ContextActionProgressQuery(
                                    query.text,
                                    query.source_kinds,
                                    query.artifact_path_prefix,
                                )
                                for query in action.queries
                            ),
                        ),
                    )
                    stage = "action"
                    search_observation = action_session.search(action)
                    observation = action_session.serialize_observation(
                        search_observation
                    )
                    previous_action = "search"
                    previous_search_had_hits = bool(search_observation.hits)
                    stage = "action_observer"
                    _emit_context_event(
                        event_sink,
                        execution_event(
                            ContextExecutionEventKind.SEARCH_COMPLETED,
                            action_index=action_index,
                            search_hit_count=len(search_observation.hits),
                            observation_utf8_bytes=len(
                                observation.encode("utf-8", errors="strict")
                            ),
                            truncated=search_observation.truncated,
                        ),
                    )
                    _emit_action_progress(
                        progress_sink,
                        progress_event(
                            ContextActionProgressKind.SEARCH_COMPLETED,
                            action_index,
                            sources=action_session.progress_sources(
                                tuple(hit.handle for hit in search_observation.hits),
                                utf8_bytes={
                                    hit.handle.value: hit.boundary_utf8_bytes
                                    for hit in search_observation.hits
                                },
                            ),
                            truncated=search_observation.truncated,
                        ),
                    )
                    require_execution_workspace_current()
                    finalizer = call_finalizer
                    call_finalizer = None
                    if finalizer is not None:
                        _accept_provider_call_without_replacing(finalizer)
                    continue
                if type(action) is ContextReadAction:
                    history_advanced = True
                    stage = "action_observer"
                    read_progress_sources = action_session.progress_sources(
                        action.handles
                    )
                    _emit_context_event(
                        event_sink,
                        execution_event(
                            ContextExecutionEventKind.READ_STARTED,
                            action_index=action_index,
                        ),
                    )
                    _emit_action_progress(
                        progress_sink,
                        progress_event(
                            ContextActionProgressKind.READ_STARTED,
                            action_index,
                            sources=read_progress_sources,
                        ),
                    )
                    stage = "action"
                    read_observation, pending_read = action_session.read(action)
                    observation = action_session.serialize_observation(read_observation)
                    previous_action = "read"
                    previous_search_had_hits = False
                    record_count = _conversation_read_record_count(read_observation)
                    stage = "action_observer"
                    _emit_context_event(
                        event_sink,
                        execution_event(
                            ContextExecutionEventKind.READ_COMPLETED,
                            action_index=action_index,
                            retrieved_source_count=len(read_observation.evidence),
                            retrieved_source_utf8_bytes=sum(
                                evidence.receipt.boundary_utf8_bytes
                                for evidence in read_observation.evidence
                            ),
                            history_record_count=record_count,
                            observation_utf8_bytes=len(
                                observation.encode("utf-8", errors="strict")
                            ),
                        ),
                    )
                    _emit_action_progress(
                        progress_sink,
                        progress_event(
                            ContextActionProgressKind.READ_COMPLETED,
                            action_index,
                            sources=tuple(
                                replace(
                                    source,
                                    utf8_bytes=evidence.receipt.boundary_utf8_bytes,
                                )
                                for source, evidence in zip(
                                    read_progress_sources,
                                    read_observation.evidence,
                                    strict=True,
                                )
                            ),
                        ),
                    )
                    require_execution_workspace_current()
                    finalizer = call_finalizer
                    call_finalizer = None
                    if finalizer is not None:
                        _accept_provider_call_without_replacing(finalizer)
                    continue

                terminal_provider_succeeded = True
                terminal_call_id = call_id
                stage = "answer"
                authorization = action_session.authorize_answer(action)
                stage = "source_revalidation"
                if request.include_core_statuses and (
                    registry.canonical_core_statuses(
                        manifest_digest,
                        self._source_policy,
                    )
                    != core_statuses
                ):
                    raise ContextSourceError(ContextSourceErrorCode.SOURCE_DRIFT)
                revalidated_manifest = self._manifest_from_descriptors(
                    request,
                    scope,
                    registry.inventory(self._source_policy),
                )
                if revalidated_manifest != manifest:
                    raise ContextSourceError(ContextSourceErrorCode.SOURCE_DRIFT)
                stage = "answer_validation"
                if answer_validator is not None:
                    try:
                        _require_sync_none(answer_validator(action.answer, authorization))
                    except Exception as answer_failure:
                        issues = failure_issues(answer_failure, path="$.action.result")
                        if issues:
                            details = bounded_chunks(issues)
                        else:
                            try:
                                rules = None if answer_diagnostic is None else answer_diagnostic(action.answer)
                            except Exception:
                                rules = None
                            details = domain_rule_chunks(rules)
                        call_validation_details = details
                        if call_finalizer is not None:
                            try:
                                retain_details = getattr(
                                    call_finalizer, "set_validation_details", None,
                                )
                                if callable(retain_details):
                                    retain_details(details)
                            except BaseException:
                                pass
                        if validation_sink is not None:
                            _require_sync_none(validation_sink(
                                call_id, details,
                            ))
                        raise
                stage = "answer_observer"
                _emit_context_event(
                    event_sink,
                    execution_event(
                        ContextExecutionEventKind.ANSWER_AUTHORIZED,
                        action_index=action_index,
                    ),
                )
                if request.mandatory_references:
                    stage = "reference_complete"
                    require_execution_workspace_current()
                    provider.complete_references(scope, policy_fingerprint)
                stage = "commit"
                require_execution_workspace_current()
                assert current_thread_id is not None
                assert terminal_call_id is not None
                commit_authorization = ContextLedgerCommitAuthorization(
                        attempt_id=attempt_id,
                        operation_id=request.operation_id,
                        result_correlation_id=request.result_correlation_id,
                        proposal=projection.checkpoint_proposal,
                        revalidated_proposal_digest=proposal_digest,
                        terminal_call_id=terminal_call_id,
                        final_provider_thread_id=current_thread_id,
                        completed_reference_operation_id=(
                            request.reference_operation_id
                        ),
                        output_action_authorization_id=(authorization.authorization_id),
                )
                if continuation is not None:
                    assert publication_validator is not None
                    deferred_finalizer = call_finalizer
                    call_finalizer = None
                    deferred_committed = False
                    deferred_generation: int | None = None
                    deferred_aborted = False

                    def commit_published() -> None:
                        nonlocal deferred_committed, deferred_generation
                        require_execution_workspace_current()
                        commit = self._ledger.commit_attempt(
                            key,
                            expected_generation=generation,
                            authorization=commit_authorization,
                        )
                        try:
                            require_execution_workspace_current()
                        except BaseException as failure:
                            try:
                                self._ledger.invalidate(
                                    key,
                                    expected_generation=commit.generation,
                                )
                            except BaseException as invalidation_failure:
                                try:
                                    failure.add_note(
                                        "context checkpoint invalidation failed: "
                                        f"{type(invalidation_failure).__name__}"
                                    )
                                except BaseException:
                                    pass
                            raise
                        deferred_generation = commit.generation
                        deferred_committed = True

                    def complete_published() -> None:
                        _emit_context_event(event_sink, execution_event(
                            ContextExecutionEventKind.CHECKPOINT_ADVANCED,
                            provider_input_tokens=_provider_input_token_total(submitted_accountings),
                        ))
                        if deferred_finalizer is not None:
                            _accept_provider_call_without_replacing(deferred_finalizer)

                    def abort_unpublished() -> None:
                        nonlocal deferred_aborted
                        if deferred_aborted:
                            return
                        deferred_aborted = True
                        try:
                            if deferred_committed:
                                assert deferred_generation is not None
                                self._ledger.invalidate(
                                    key,
                                    expected_generation=deferred_generation,
                                )
                            else:
                                self._ledger.taint_attempt(
                                    key, expected_generation=generation,
                                    attempt_id=attempt_id,
                                    outcome=ContextLedgerAttemptOutcome.FAILED,
                                )
                        except BaseException:
                            pass
                        if deferred_finalizer is not None:
                            _fail_provider_call_without_replacing(
                                deferred_finalizer,
                                ContextProviderTerminalCode.CONTEXT_RUNTIME_FAILED,
                            )
                        _emit_context_event_without_replacing(event_sink, execution_event(
                            ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED,
                        ))

                    continuation.stage(
                        commit_published,
                        abort_unpublished,
                        publication_validator,
                        complete_published,
                    )
                    require_execution_workspace_current()
                    return ContextInvocationResult(
                        action.answer, authorization, projection.accounting,
                        current_thread_id, tuple(calls), generation,
                        checkpoint_pending=True,
                    )
                commit = self._ledger.commit_attempt(
                    key, expected_generation=generation,
                    authorization=commit_authorization,
                )
                try:
                    require_execution_workspace_current()
                except BaseException as failure:
                    try:
                        self._ledger.invalidate(
                            key,
                            expected_generation=commit.generation,
                        )
                    except BaseException as invalidation_failure:
                        try:
                            failure.add_note(
                                "context checkpoint invalidation failed: "
                                f"{type(invalidation_failure).__name__}"
                            )
                        except BaseException:
                            pass
                    raise
                terminal_provider_succeeded = False
                checkpoint_committed = True
                finalizer = call_finalizer
                call_finalizer = None
                if finalizer is not None:
                    _accept_provider_call_without_replacing(finalizer)
                _emit_context_event(
                    event_sink,
                    execution_event(
                        ContextExecutionEventKind.CHECKPOINT_ADVANCED,
                        provider_input_tokens=_provider_input_token_total(
                            submitted_accountings
                        ),
                    ),
                )
                return ContextInvocationResult(
                    action.answer,
                    authorization,
                    projection.accounting,
                    current_thread_id,
                    tuple(calls),
                    commit.generation,
                )
            stage = "exhausted"
            raise _execution_error(ContextExecutionErrorCode.ACTION_EXHAUSTED)
        except BaseException as failure:
            if checkpoint_committed:
                raise
            if call_finalizer is not None:
                if stage in {"action", "answer"} and isinstance(failure, ContextActionError):
                    call_validation_details = bounded_chunks(failure_issues(failure, path="$.action"))
                    if validation_sink is not None and call_validation_details:
                        try:
                            _require_sync_none(validation_sink(call_id, call_validation_details))
                        except BaseException:
                            pass
                finalizer = call_finalizer
                call_finalizer = None
                if isinstance(failure, asyncio.CancelledError):
                    _cancel_provider_call_without_replacing(finalizer)
                elif stage in {"decode", "exhausted"} and isinstance(
                    failure,
                    ContextExecutionError,
                ):
                    code = (
                        ContextProviderTerminalCode.ACTION_EXHAUSTED
                        if stage == "exhausted"
                        else _context_provider_decode_terminal_code(failure)
                    )
                    reject_response(finalizer, code)
                elif stage in {"action", "answer"} and isinstance(
                    failure,
                    ContextActionError,
                ):
                    reject_response(
                        finalizer,
                        ContextProviderTerminalCode.RESPONSE_SEMANTIC_REJECTED,
                    )
                elif stage == "answer_validation":
                    reject_response(
                        finalizer,
                        ContextProviderTerminalCode.ANSWER_VALIDATION_FAILED,
                    )
                elif stage == "reference_complete":
                    _fail_provider_call_without_replacing(
                        finalizer,
                        ContextProviderTerminalCode.REFERENCE_RUNTIME_FAILED,
                    )
                else:
                    _fail_provider_call_without_replacing(
                        finalizer,
                        ContextProviderTerminalCode.CONTEXT_RUNTIME_FAILED,
                    )
            if isinstance(failure, ContextSourceError) or stage in {
                "source",
                "source_revalidation",
            }:
                _emit_context_event_without_replacing(
                    event_sink,
                    execution_event(
                        ContextExecutionEventKind.INVALIDATED,
                    ),
                )
            failure_code = getattr(getattr(failure, "code", None), "value", "")
            if (
                isinstance(failure_code, str)
                and ("budget_exceeded" in failure_code or "limit_exceeded" in failure_code)
            ) or isinstance(failure, SessionContextCapacityError):
                _emit_context_event_without_replacing(
                    event_sink,
                    execution_event(
                        ContextExecutionEventKind.BUDGET_EXHAUSTED,
                    ),
                )
            if stage == "exhausted":
                _emit_context_event_without_replacing(
                    event_sink,
                    execution_event(
                        ContextExecutionEventKind.ACTION_EXHAUSTED,
                    ),
                )
            if action_session is not None:
                try:
                    action_session.fail()
                except BaseException:
                    pass
            outcome = self._failure_outcome(
                failure,
                stage=stage,
                history_advanced=history_advanced,
                terminal_provider_succeeded=terminal_provider_succeeded,
            )
            if reference_started:
                try:
                    provider.abort_references(
                        scope,
                        policy_fingerprint,
                        cancelled=isinstance(failure, asyncio.CancelledError),
                        provider_failure=provider_failure,
                    )
                except BaseException:
                    pass
            should_taint = self._should_taint_context(
                failure,
                outcome,
                history_advanced=history_advanced,
                terminal_provider_succeeded=terminal_provider_succeeded,
            )
            if should_taint:
                try:
                    taint_provider_thread()
                except BaseException:
                    pass
                _emit_context_event_without_replacing(
                    event_sink,
                    execution_event(
                        ContextExecutionEventKind.TAINTED,
                    ),
                )
            if attempt_id is not None and generation is not None:
                try:
                    self._ledger.taint_attempt(
                        key,
                        expected_generation=generation,
                        attempt_id=attempt_id,
                        outcome=outcome,
                    )
                except BaseException:
                    pass
            _emit_context_event_without_replacing(
                event_sink,
                execution_event(
                    ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED,
                    provider_input_tokens=_provider_input_token_total(
                        submitted_accountings
                    ),
                ),
            )
            if isinstance(failure, ProviderAuthenticationRequiredError):
                failure.clear_replay_validator()
            existing_facts = context_provider_failure_facts(failure)
            cumulative = list(submitted_accountings)
            if (
                existing_facts.accounting is not None
                and existing_facts.accounting.state is ProviderCallState.SUBMITTED
                and existing_facts.accounting not in cumulative
            ):
                cumulative.append(existing_facts.accounting)
            record_context_provider_failure(
                failure,
                ContextProviderFailureFacts(
                    accounting=existing_facts.accounting,
                    timed_out=existing_facts.timed_out,
                    taint_existing_thread=existing_facts.taint_existing_thread,
                    submitted_accountings=tuple(cumulative),
                    submission_uncertain=(
                        existing_facts.submission_uncertain
                        or (
                            stage == "provider"
                            and provider_failure
                            and existing_facts.accounting is None
                        )
                    ),
                    response_rejection_code=response_rejection_code,
                    continuation_rejection=existing_facts.continuation_rejection,
                ),
            )
            raise
