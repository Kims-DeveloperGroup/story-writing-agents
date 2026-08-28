"""Call-local state machine for bounded agent-directed context operations."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import json
import secrets
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import AsyncContextManager, Protocol, runtime_checkable

from ..workspace import Workspace
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
    serialize_context_action_observation,
)
from .manifest import (
    ContextManifest,
    ContextManifestEntry,
    ContextManifestError,
    ContextPriority,
    ContextScope,
    ContextSemanticChannel,
    ContextSourceKind,
    ContextSubjectKind,
    context_manifest_entry_key,
    validate_context_digest,
    validate_context_identity,
)
from .planning import (
    DEFAULT_CONTEXT_PLANNING_POLICY,
    MAX_MANDATORY_REFERENCE_RESERVATION_UTF8_BYTES,
    ContextDeliveryMode,
    ContextDecisionKind,
    ContextMaterial,
    MandatoryReferenceReservation,
    ContextPlanningConditions,
    ContextPlanningPolicy,
    ContextProjection,
    ContextProjectionAccounting,
    plan_context_projection,
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
from .sources import (
    CONTEXT_SOURCE_POLICY,
    ActiveDebateSourceAdapter,
    ContextSourceError,
    ContextSourceErrorCode,
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
    "ContextInvocationBinding",
    "ContextInvocationRequest",
    "ContextInvocationResult",
    "ContextInvocationSession",
    "ContextInvocationState",
    "ContextProvider",
    "ContextProviderCallRequest",
    "ContextProviderCallResult",
    "ContextProviderFailureFacts",
    "ContextReferencePreludeRequest",
    "ContextReferencePreludeResult",
    "ContextReadReceipt",
    "ContextScope",
    "ContextSourceClassification",
    "context_provider_failure_facts",
    "record_context_provider_failure",
]

_READABLE_SOURCE_KINDS = {
    ContextSourceKind.WRITING_ARTIFACT,
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
    """One typed lifecycle event containing counts and identities only."""

    kind: ContextExecutionEventKind
    scope: ContextScope
    action_index: int | None = None
    source_count: int = 0
    search_hit_count: int = 0
    transcript_turn_count: int = 0
    transcript_event_count: int = 0
    observation_utf8_bytes: int = 0
    truncated: bool = False

    def __post_init__(self) -> None:
        if type(self.kind) is not ContextExecutionEventKind or type(
            self.scope
        ) is not ContextScope:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.action_index is not None and (
            type(self.action_index) is not int or self.action_index < 1
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        for value in (
            self.source_count,
            self.search_hit_count,
            self.transcript_turn_count,
            self.transcript_event_count,
            self.observation_utf8_bytes,
        ):
            if type(value) is not int or value < 0:
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.truncated) is not bool:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)


ContextExecutionEventSink = Callable[[ContextExecutionEvent], None]


def _action_error(code: ContextActionErrorCode) -> ContextActionError:
    return ContextActionError(code)


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


def _digest(body: bytes) -> str:
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def _evidence_value(receipt: ContextEvidenceReceipt) -> tuple[object, ...]:
    return (
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
        )
    return _digest(
        _canonical_bytes(
            {"evidence": values, "kind": "completed_context_reads_v1"}
        )
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


class ContextExecutionError(ValueError):
    """Report one fixed contextual lifecycle failure without story content."""

    def __init__(self, code: ContextExecutionErrorCode) -> None:
        if type(code) is not ContextExecutionErrorCode:
            raise TypeError(
                "context execution error code must be a ContextExecutionErrorCode"
            )
        self.code = code
        super().__init__(_EXECUTION_ERROR_MESSAGES[code])


def _execution_error(code: ContextExecutionErrorCode) -> ContextExecutionError:
    return ContextExecutionError(code)


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
    event: ContextExecutionEvent,
) -> None:
    if sink is None:
        return
    _require_sync_none(sink(event))


def _emit_context_event_without_replacing(
    sink: ContextExecutionEventSink | None,
    event: ContextExecutionEvent,
) -> None:
    """Best-effort emit after an authoritative outcome already exists."""

    try:
        _emit_context_event(sink, event)
    except BaseException:
        pass


def _conversation_read_counts(
    observation: ContextReadObservation,
) -> tuple[int, int]:
    """Count complete conversation pairs without retaining or emitting bodies."""

    turns = 0
    for evidence in observation.evidence:
        receipt = evidence.receipt
        if receipt.source_kind is not ContextSourceKind.CONVERSATION:
            continue
        if receipt.boundary.kind is ContextSourceBoundaryKind.CONVERSATION_TURNS:
            turns += 1
            continue
        if receipt.boundary.kind is not ContextSourceBoundaryKind.WHOLE:
            continue
        try:
            value = json.loads(evidence.content)
        except (json.JSONDecodeError, RecursionError, TypeError, UnicodeError):
            continue
        if type(value) is dict and type(value.get("pairs")) is list:
            turns += len(value["pairs"])
    return turns, turns * 2


@dataclass(frozen=True, slots=True)
class ContextSourceClassification:
    """Content-free priority/channel assignment for one inventoried source."""

    source_id: str
    semantic_channel: ContextSemanticChannel
    priority: ContextPriority
    ordinal: int

    def __post_init__(self) -> None:
        _safe_identity(self.source_id)
        if (
            type(self.semantic_channel) is not ContextSemanticChannel
            or type(self.priority) is not ContextPriority
            or self.priority
            not in {ContextPriority.P2_WORKING, ContextPriority.P3_OPTIONAL}
            or type(self.ordinal) is not int
            or self.ordinal < 0
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
    source_classifications: tuple[ContextSourceClassification, ...] = ()
    reference_operation_id: str | None = None
    mandatory_references: tuple[MandatoryReferenceReservation, ...] = ()
    fresh: bool = False

    def __post_init__(self) -> None:
        _safe_identity(self.session_id)
        if type(self.subject_kind) is not ContextSubjectKind:
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
        if bool(self.reference_operation_id) is not bool(
            self.mandatory_references
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.fresh) is not bool:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)


@dataclass(frozen=True, slots=True)
class ContextProviderFailureFacts:
    """Validated physical-call facts attached without replacing a failure."""

    accounting: ProviderCallAccounting | None = None
    timed_out: bool = False
    taint_existing_thread: bool = False

    def __post_init__(self) -> None:
        if self.accounting is not None and type(self.accounting) is not (
            ProviderCallAccounting
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.timed_out) is not bool or type(
            self.taint_existing_thread
        ) is not bool:
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

    def __post_init__(self) -> None:
        if type(self.scope) is not ContextScope:
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

    def __post_init__(self) -> None:
        if type(self.response_text) is not str:
            raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        _safe_identity(self.thread_id)
        if (
            type(self.accounting) is not ProviderCallAccounting
            or self.accounting.state is not ProviderCallState.SUBMITTED
        ):
            raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)


@dataclass(frozen=True, slots=True)
class ContextReferencePreludeRequest:
    """One separately budgeted exact mandatory-reference provider prelude."""

    scope: ContextScope
    policy_fingerprint: str
    call_id: str
    reference_operation_id: str
    expected_thread_id: str | None

    def __post_init__(self) -> None:
        if type(self.scope) is not ContextScope:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        _safe_digest(self.policy_fingerprint)
        _safe_identity(self.call_id)
        _safe_identity(self.reference_operation_id)
        if self.expected_thread_id is not None:
            _safe_identity(self.expected_thread_id)


@dataclass(frozen=True, slots=True)
class ContextReferencePreludeResult:
    """Transient exact reference context and its physical-call facts."""

    context: tuple[Mapping[str, object], ...] = field(repr=False)
    thread_id: str
    accounting: ProviderCallAccounting

    def __post_init__(self) -> None:
        if type(self.context) is not tuple or any(
            not isinstance(item, Mapping) for item in self.context
        ):
            raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        _safe_identity(self.thread_id)
        if (
            type(self.accounting) is not ProviderCallAccounting
            or self.accounting.state is not ProviderCallState.SUBMITTED
            or self.accounting.payload.call_kind
            is not ProviderCallKind.WRITING_ARTIFACT_REFERENCE_READ
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
    ) -> AsyncContextManager[None]:
        """Retain the provider binding for one complete contextual lifecycle."""

    def inspect_context_thread(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
    ) -> str | None:
        """Return one usable exact scoped continuation, if present."""

    async def prepare_references(
        self,
        request: ContextReferencePreludeRequest,
    ) -> ContextReferencePreludeResult:
        """Run the existing mandatory-reference prelude on the scoped thread."""

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

    def __post_init__(self) -> None:
        if type(self.answer) is not str or not self.answer:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.authorization) is not ContextAnswerAuthorization:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.projection_accounting) is not ContextProjectionAccounting:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        _safe_identity(self.provider_thread_id)
        if type(self.provider_calls) is not tuple or any(
            type(item) is not ContextLedgerProviderCall
            for item in self.provider_calls
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
        if type(self.evidence) is not tuple or not self.evidence or any(
            type(item) is not ContextEvidenceReceipt for item in self.evidence
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


class ContextInvocationSession:
    """Own one synchronized invocation-local action and evidence lifecycle."""

    def __init__(
        self,
        binding: ContextInvocationBinding,
        sources: ContextSourceRegistry,
        *,
        action_policy: ContextActionPolicy = CONTEXT_ACTION_POLICY,
        source_policy: ContextSourcePolicy = CONTEXT_SOURCE_POLICY,
    ) -> None:
        if (
            type(binding) is not ContextInvocationBinding
            or type(sources) is not ContextSourceRegistry
            or sources.scope != binding.scope
            or type(action_policy) is not ContextActionPolicy
            or type(source_policy) is not ContextSourcePolicy
        ):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
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
        self._completed_receipts: list[ContextReadReceipt] = []
        self._pending_receipt: ContextReadReceipt | None = None
        manifest_handles: list[tuple[ContextManifestEntry, ContextSourceHandle]] = []
        for entry in binding.manifest.entries:
            if (
                entry.source_kind in _READABLE_SOURCE_KINDS
                and entry.utf8_bytes <= action_policy.max_excerpt_utf8_bytes
            ):
                handle_binding = self._build_handle_binding(
                    entry,
                    ContextSourceBoundary.whole(),
                    origin_action_index=0,
                )
                self._register_handle(handle_binding)
                manifest_handles.append((entry, handle_binding.handle))
        self._manifest_handles = tuple(manifest_handles)

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
    def manifest_handles(
        self,
    ) -> tuple[tuple[ContextManifestEntry, ContextSourceHandle], ...]:
        """Return canonical whole-source handles that fit one bounded read."""

        with self._lock:
            return self._manifest_handles

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
        )

    def _register_handle(self, binding: _HandleBinding) -> None:
        existing = self._handles.get(binding.handle.value)
        if existing is not None and existing != binding:
            raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
        self._handles[binding.handle.value] = binding

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
            or correlation.result_correlation_id
            != self.binding.result_correlation_id
            or correlation.action_index != self._next_action_index
        ):
            raise _action_error(ContextActionErrorCode.CORRELATION_MISMATCH)
        if correlation.action_index > self._action_policy.max_provider_calls:
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
        self._manifest_handles = ()
        self._pending_receipt = None
        self._completed_receipts.clear()

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
                    self.binding.manifest,
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
                    observation = ContextSearchObservation(
                        action.correlation,
                        tuple(hits),
                        truncated,
                    )
                    framed_bytes = len(
                        serialize_context_action_observation(observation).encode(
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
                    raise _action_error(
                        ContextActionErrorCode.INVALID_TRANSITION
                    )
                for handle_binding in pending_bindings:
                    self._register_handle(handle_binding)
                self._nonterminal_actions += 1
                self._search_actions += 1
                self._queries += len(action.queries)
                self._hits += len(hits)
                self._scan_utf8_bytes += scanned
                self._observation_utf8_bytes += framed_bytes
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
            if (
                len(selected) != boundary.end - boundary.start + 1
                or tuple(record.boundary.start for record in selected)
                != tuple(range(boundary.start, boundary.end + 1))
            ):
                raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
            if boundary.kind is ContextSourceBoundaryKind.ARTIFACT_LINES:
                return b"".join(
                    record.content.encode("utf-8") for record in selected
                )
            if len(selected) == 1:
                return selected[0].content.encode("utf-8")
            return _canonical_bytes(
                [json.loads(record.content) for record in selected]
            )
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
                requested_source_ids = {
                    binding.entry.source_id for binding in bindings
                }
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
                        raise _action_error(
                            ContextActionErrorCode.INVALID_TRANSITION
                        )
                source_additions: dict[str, int] = {}
                evidence: list[ContextSourceEvidence] = []
                batch_bytes = 0
                for handle_binding in bindings:
                    entry = handle_binding.entry
                    snapshot = snapshots[entry.source_id]
                    excerpt = self._boundary_content(
                        snapshot,
                        handle_binding.boundary,
                    )
                    if len(excerpt) > self._action_policy.max_excerpt_utf8_bytes:
                        raise _action_error(ContextActionErrorCode.READ_LIMIT_EXCEEDED)
                    source_additions[entry.source_id] = (
                        source_additions.get(entry.source_id, 0) + len(excerpt)
                    )
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
                        raise _action_error(
                            ContextActionErrorCode.INVALID_TRANSITION
                        )
                observation = ContextReadObservation(
                    action.correlation,
                    tuple(evidence),
                )
                framed = serialize_context_action_observation(observation).encode(
                    "utf-8"
                )
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
                    evidence.handle.value: evidence
                    for evidence in completed_evidence
                }
                if len(completed) != len(completed_evidence):
                    raise _action_error(
                        ContextActionErrorCode.ANSWER_NOT_AUTHORIZED
                    )
                if any(handle.value not in completed for handle in action.used_handles):
                    raise _action_error(
                        ContextActionErrorCode.ANSWER_NOT_AUTHORIZED
                    )
                if len(action.used_handles) > (
                    self._action_policy.max_cumulative_handles
                ):
                    raise _action_error(
                        ContextActionErrorCode.ANSWER_NOT_AUTHORIZED
                    )
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
                        raise _action_error(
                            ContextActionErrorCode.INVALID_TRANSITION
                        )
                completed_read_digest = _completed_evidence_digest(
                    completed_evidence
                )
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


_SEARCHABLE_SOURCE_KINDS = (
    ContextSourceKind.WRITING_ARTIFACT,
    ContextSourceKind.CONVERSATION,
    ContextSourceKind.ACTIVE_DEBATE,
    ContextSourceKind.OWN_AGENT_STATE,
)


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


def _entry_payload(entry: ContextManifestEntry) -> dict[str, object]:
    return {
        "content_digest": entry.content_digest,
        "ordinal": entry.ordinal,
        "priority": entry.priority.value,
        "provenance": {
            "authority_kind": entry.provenance.authority_kind.value,
            "locator": entry.provenance.locator,
        },
        "revision": entry.revision,
        "searchable": entry.searchable,
        "semantic_channel": entry.semantic_channel.value,
        "source_id": entry.source_id,
        "source_kind": entry.source_kind.value,
        "utf8_bytes": entry.utf8_bytes,
    }


def _scope_payload(scope: ContextScope) -> dict[str, str]:
    return {
        "agent_id": scope.agent_id,
        "session_id": scope.session_id,
        "subject_id": scope.subject_id,
        "subject_kind": scope.subject_kind.value,
        "workspace_id": scope.workspace_id,
    }


def _correlation_payload(
    binding: ContextInvocationBinding,
    action_index: int,
) -> dict[str, object]:
    return {
        "action_index": action_index,
        "operation_id": binding.operation_id,
        "result_correlation_id": binding.result_correlation_id,
        "scope": _scope_payload(binding.scope),
    }


def _action_controls(
    policy: ContextActionPolicy,
    allowed_actions: tuple[str, ...],
) -> dict[str, object]:
    return {
        "allowed_actions": list(allowed_actions),
        "max_cumulative_handles": policy.max_cumulative_handles,
        "max_cumulative_hits": policy.max_cumulative_hits,
        "max_cumulative_queries": policy.max_cumulative_queries,
        "max_excerpt_utf8_bytes": policy.max_excerpt_utf8_bytes,
        "max_handles_per_read": policy.max_handles_per_read,
        "max_nonterminal_actions": policy.max_nonterminal_actions,
        "max_observation_utf8_bytes": policy.max_observation_utf8_bytes,
        "max_provider_calls": policy.max_provider_calls,
        "max_queries_per_search": policy.max_queries_per_search,
        "max_read_actions": policy.max_read_actions,
        "max_read_utf8_bytes": policy.max_read_utf8_bytes,
        "max_search_actions": policy.max_search_actions,
    }


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
        raise _execution_error(ContextExecutionErrorCode.MALFORMED_ACTION)
    assert isinstance(value, dict)
    return value


def _decode_correlation(value: object) -> ContextActionCorrelation:
    raw = _exact_object(
        value,
        frozenset(
            {
                "action_index",
                "operation_id",
                "result_correlation_id",
                "scope",
            }
        ),
    )
    scope_raw = _exact_object(
        raw["scope"],
        frozenset(
            {
                "agent_id",
                "session_id",
                "subject_id",
                "subject_kind",
                "workspace_id",
            }
        ),
    )
    try:
        scope = ContextScope(
            scope_raw["workspace_id"],
            scope_raw["session_id"],
            ContextSubjectKind(scope_raw["subject_kind"]),
            scope_raw["subject_id"],
            scope_raw["agent_id"],
        )
        return ContextActionCorrelation(
            scope,
            raw["operation_id"],
            raw["result_correlation_id"],
            raw["action_index"],
        )
    except (ContextManifestError, ContextActionError, TypeError, ValueError):
        raise _execution_error(
            ContextExecutionErrorCode.MALFORMED_ACTION
        ) from None


def _decode_context_action(
    response_text: str,
    *,
    allowed_actions: tuple[str, ...],
) -> ContextSearchAction | ContextReadAction | ContextAnswerAction:
    """Strictly decode one exact action without retaining rejected prose."""

    try:
        value = json.loads(
            response_text,
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        if type(value) is not dict:
            raise ValueError
        action_kind = value.get("action")
        if type(action_kind) is not str or action_kind not in allowed_actions:
            raise ValueError
        if action_kind == "search":
            raw = _exact_object(
                value,
                frozenset({"action", "correlation", "queries"}),
            )
            queries_raw = raw["queries"]
            if type(queries_raw) is not list:
                raise ValueError
            queries: list[ContextSearchQuery] = []
            for query_value in queries_raw:
                query_raw = _exact_object(
                    query_value,
                    frozenset({"source_kinds", "text"}),
                )
                kinds_raw = query_raw["source_kinds"]
                if type(kinds_raw) is not list:
                    raise ValueError
                queries.append(
                    ContextSearchQuery(
                        query_raw["text"],
                        tuple(ContextSourceKind(item) for item in kinds_raw),
                    )
                )
            return ContextSearchAction(
                _decode_correlation(raw["correlation"]),
                tuple(queries),
            )
        if action_kind == "read":
            raw = _exact_object(
                value,
                frozenset({"action", "correlation", "handles"}),
            )
            handles_raw = raw["handles"]
            if type(handles_raw) is not list:
                raise ValueError
            return ContextReadAction(
                _decode_correlation(raw["correlation"]),
                tuple(ContextSourceHandle(item) for item in handles_raw),
            )
        raw = _exact_object(
            value,
            frozenset({"action", "content", "correlation", "used_handles"}),
        )
        handles_raw = raw["used_handles"]
        if type(handles_raw) is not list:
            raise ValueError
        return ContextAnswerAction(
            _decode_correlation(raw["correlation"]),
            raw["content"],
            tuple(ContextSourceHandle(item) for item in handles_raw),
        )
    except ContextExecutionError:
        raise
    except (
        ContextActionError,
        ContextManifestError,
        _DuplicateJsonKey,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ):
        raise _execution_error(
            ContextExecutionErrorCode.MALFORMED_ACTION
        ) from None


def _context_action_schema(
    binding: ContextInvocationBinding,
    action_index: int,
    allowed_actions: tuple[str, ...],
    action_policy: ContextActionPolicy,
) -> dict[str, object]:
    """Build one exact finite JSON schema for the next correlated action."""

    scope_properties = {
        key: {"const": value}
        for key, value in _scope_payload(binding.scope).items()
    }
    correlation = {
        "additionalProperties": False,
        "properties": {
            "action_index": {"const": action_index},
            "operation_id": {"const": binding.operation_id},
            "result_correlation_id": {
                "const": binding.result_correlation_id
            },
            "scope": {
                "additionalProperties": False,
                "properties": scope_properties,
                "required": list(scope_properties),
                "type": "object",
            },
        },
        "required": [
            "action_index",
            "operation_id",
            "result_correlation_id",
            "scope",
        ],
        "type": "object",
    }
    variants: list[dict[str, object]] = []
    if "search" in allowed_actions:
        variants.append(
            {
                "additionalProperties": False,
                "properties": {
                    "action": {"const": "search"},
                    "correlation": correlation,
                    "queries": {
                        "items": {
                            "additionalProperties": False,
                            "properties": {
                                "source_kinds": {
                                    "items": {
                                        "enum": [
                                            item.value
                                            for item in _SEARCHABLE_SOURCE_KINDS
                                        ],
                                        "type": "string",
                                    },
                                    "maxItems": len(_SEARCHABLE_SOURCE_KINDS),
                                    "minItems": 1,
                                    "type": "array",
                                    "uniqueItems": True,
                                },
                                "text": {
                                    "maxLength": action_policy.max_query_characters,
                                    "minLength": 1,
                                    "type": "string",
                                },
                            },
                            "required": ["source_kinds", "text"],
                            "type": "object",
                        },
                        "maxItems": action_policy.max_queries_per_search,
                        "minItems": 1,
                        "type": "array",
                    },
                },
                "required": ["action", "correlation", "queries"],
                "type": "object",
            }
        )
    if "read" in allowed_actions:
        variants.append(
            {
                "additionalProperties": False,
                "properties": {
                    "action": {"const": "read"},
                    "correlation": correlation,
                    "handles": {
                        "items": {"type": "string"},
                        "maxItems": action_policy.max_handles_per_read,
                        "minItems": 1,
                        "type": "array",
                        "uniqueItems": True,
                    },
                },
                "required": ["action", "correlation", "handles"],
                "type": "object",
            }
        )
    if "answer" in allowed_actions:
        variants.append(
            {
                "additionalProperties": False,
                "properties": {
                    "action": {"const": "answer"},
                    "content": {"minLength": 1, "type": "string"},
                    "correlation": correlation,
                    "used_handles": {
                        "items": {"type": "string"},
                        "maxItems": action_policy.max_cumulative_handles,
                        "type": "array",
                        "uniqueItems": True,
                    },
                },
                "required": [
                    "action",
                    "content",
                    "correlation",
                    "used_handles",
                ],
                "type": "object",
            }
        )
    return {"oneOf": variants}


def _reference_payload(reference: MandatoryReferenceReservation) -> dict[str, object]:
    return {
        "content_digest": reference.content_digest,
        "ordinal": reference.ordinal,
        "provenance": {
            "authority_kind": reference.provenance.authority_kind.value,
            "locator": reference.provenance.locator,
        },
        "reference_id": reference.reference_id,
        "revision": reference.revision,
        "source_id": reference.source_id,
        "utf8_bytes": reference.utf8_bytes,
    }


def _initial_payload(
    binding: ContextInvocationBinding,
    projection: ContextProjection,
    session: ContextInvocationSession,
    *,
    action_index: int,
    allowed_actions: tuple[str, ...],
    action_policy: ContextActionPolicy,
    reference_context: tuple[Mapping[str, object], ...],
) -> tuple[
    dict[str, object],
    dict[JsonPath, ContextComponentKind],
    tuple[JsonPath, ...],
]:
    handles = {
        entry.source_id: handle.value for entry, handle in session.manifest_handles
    }
    units: list[dict[str, object]] = []
    component_paths: dict[JsonPath, ContextComponentKind] = {}
    for index, unit in enumerate(projection.units):
        units.append(
            {
                "content": unit.body_utf8.decode("utf-8", errors="strict"),
                "sources": [_entry_payload(entry) for entry in unit.entries],
            }
        )
        component_paths[("projected_units", index, "content")] = (
            _projected_component_kind(unit.entries[0])
        )
    payload: dict[str, object] = {
        "action_controls": _action_controls(action_policy, allowed_actions),
        "available_sources": [
            {
                **_entry_payload(entry),
                "whole_read_handle": handles.get(entry.source_id),
            }
            for entry in binding.manifest.entries
        ],
        "correlation": _correlation_payload(binding, action_index),
        "delivery_mode": projection.mode.value,
        "mandatory_references": [
            _reference_payload(item) for item in projection.mandatory_references
        ],
        "operation": "agent_context_v1",
        "projected_units": units,
    }
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
                component_paths[path] = (
                    ContextComponentKind.MANDATORY_REFERENCE_CONTENT
                )
                mandatory_paths.append(path)
    return payload, component_paths, tuple(mandatory_paths)


def _continuation_payload(
    binding: ContextInvocationBinding,
    *,
    action_index: int,
    allowed_actions: tuple[str, ...],
    action_policy: ContextActionPolicy,
    observation: str,
) -> tuple[dict[str, object], dict[JsonPath, ContextComponentKind]]:
    return (
        {
            "action_controls": _action_controls(action_policy, allowed_actions),
            "correlation": _correlation_payload(binding, action_index),
            "observation": observation,
            "operation": "agent_context_v1_continuation",
        },
        {("observation",): ContextComponentKind.RETRIEVED_OBSERVATION},
    )


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
        self._ledger = ContextLedger(workspace)
        self._locks: dict[tuple[str, ...], asyncio.Lock] = {}

    @property
    def workspace_id(self) -> str:
        """Return the authoritative content-free identity of this workspace."""

        return self._workspace_id

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
            self._planning_policy.fingerprint,
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
        classifications = {
            item.source_id: item for item in request.source_classifications
        }
        descriptor_ids = {item.source_id for item in descriptors}
        if set(classifications).difference(descriptor_ids):
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
        manifest = ContextManifest(scope, entries)
        source_materials: list[ContextMaterial] = []
        for entry in source_entries:
            snapshot = registry.capture(manifest, entry, self._source_policy)
            source_materials.append(ContextMaterial(entry, snapshot.content))
        materials = (*request.materials, *source_materials)
        if len({item.entry.source_id for item in materials}) != len(materials):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        return registry, manifest, tuple(materials)

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
                raise _execution_error(
                    ContextExecutionErrorCode.PROVIDER_CONTRACT
                )
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
        event_sink: ContextExecutionEventSink | None = None,
    ) -> ContextInvocationResult:
        """Execute one complete contextual lifecycle under its exact scope lock."""

        if type(request) is not ContextInvocationRequest:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        _validate_sync_callback(answer_validator)
        _validate_sync_callback(event_sink)
        provider_id, definition_fingerprint = self._provider_identity(provider)
        reference_operation_id = getattr(provider, "reference_operation_id", None)
        if reference_operation_id != request.reference_operation_id:
            raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        scope = self._scope_for(request)
        lock = self._lock_for(scope, provider_id, definition_fingerprint)
        policy_fingerprint = self._planning_policy.fingerprint
        context_manager = provider.invocation(scope, policy_fingerprint)
        if not hasattr(context_manager, "__aenter__") or not hasattr(
            context_manager,
            "__aexit__",
        ):
            raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        async with context_manager:
            async with lock:
                return await self._invoke_locked(
                    request,
                    provider,
                    provider_id=provider_id,
                    definition_fingerprint=definition_fingerprint,
                    scope=scope,
                    writing_artifacts=writing_artifacts,
                    focused_conversation=focused_conversation,
                    active_debate=active_debate,
                    own_agent_state=own_agent_state,
                    answer_validator=answer_validator,
                    event_sink=event_sink,
                )

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
        answer_validator: Callable[[str, ContextAnswerAuthorization], object]
        | None,
        event_sink: ContextExecutionEventSink | None,
    ) -> ContextInvocationResult:
        policy_fingerprint = self._planning_policy.fingerprint
        key = ContextLedgerKey(scope, provider_id)
        attempt_id: str | None = None
        generation: int | None = None
        calls: list[ContextLedgerProviderCall] = []
        action_session: ContextInvocationSession | None = None
        reference_started = False
        history_advanced = False
        terminal_provider_succeeded = False
        stage = "preflight"
        provider_failure = False
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
            stage = "preflight"
            active_thread_id = provider.inspect_context_thread(
                scope,
                policy_fingerprint,
            )
            if active_thread_id is not None:
                _safe_identity(active_thread_id)
            conditions = ContextPlanningConditions(
                scope,
                provider_id,
                definition_fingerprint,
                policy_fingerprint,
            )
            inspection = self._ledger.inspect(key)
            projection = plan_context_projection(
                manifest,
                materials,
                policy=self._planning_policy,
                conditions=conditions,
                checkpoint=inspection.checkpoint_view,
                active_provider_thread_id=active_thread_id,
                mandatory_references=request.mandatory_references,
                fresh=request.fresh,
            )
            _emit_context_event(
                event_sink,
                ContextExecutionEvent(
                    (
                        ContextExecutionEventKind.BOOTSTRAP
                        if projection.mode is ContextDeliveryMode.BOOTSTRAP
                        else ContextExecutionEventKind.DELTA
                    ),
                    scope,
                    source_count=len(projection.decisions),
                ),
            )
            omitted_count = sum(
                decision.decision
                in {
                    ContextDecisionKind.OMITTED_CLASS_BUDGET,
                    ContextDecisionKind.OMITTED_MATERIAL,
                }
                for decision in projection.decisions
            )
            if omitted_count:
                _emit_context_event_without_replacing(
                    event_sink,
                    ContextExecutionEvent(
                        ContextExecutionEventKind.OMISSION,
                        scope,
                        source_count=omitted_count,
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
                proposed_delta_depth=(
                    projection.checkpoint_proposal.delta_depth
                ),
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
            )

            current_thread_id = active_thread_id
            reference_context: tuple[Mapping[str, object], ...] = ()
            if request.mandatory_references:
                stage = "reference"
                reference_started = True
                call_id = f"{attempt_id}_call_{len(calls) + 1}"
                try:
                    prelude = await provider.prepare_references(
                        ContextReferencePreludeRequest(
                            scope,
                            policy_fingerprint,
                            call_id,
                            request.reference_operation_id,
                            current_thread_id,
                        )
                    )
                except BaseException as failure:
                    facts = context_provider_failure_facts(failure)
                    if facts.accounting is not None:
                        generation = self._append_failure_call_without_replacing(
                            self._ledger,
                            key,
                            generation,
                            attempt_id,
                            calls,
                            call_id,
                            facts.accounting,
                        )
                    provider_failure = True
                    raise
                stage = "reference_result"
                terminal_provider_succeeded = True
                if type(prelude) is not ContextReferencePreludeResult:
                    failure = _execution_error(
                        ContextExecutionErrorCode.PROVIDER_CONTRACT
                    )
                    accounting = getattr(prelude, "accounting", None)
                    if (
                        type(accounting) is ProviderCallAccounting
                        and accounting.state is ProviderCallState.SUBMITTED
                    ):
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
                generation = self._append_call(
                    self._ledger,
                    key,
                    generation,
                    attempt_id,
                    calls,
                    call_id,
                    prelude.accounting,
                )
                if (
                    current_thread_id is not None
                    and prelude.thread_id != current_thread_id
                ):
                    failure = _execution_error(
                        ContextExecutionErrorCode.CONTINUATION_MISMATCH
                    )
                    record_context_provider_failure(
                        failure,
                        ContextProviderFailureFacts(
                            prelude.accounting,
                            taint_existing_thread=True,
                        ),
                    )
                    raise failure
                terminal_provider_succeeded = False
                current_thread_id = prelude.thread_id
                reference_context = prelude.context
                history_advanced = True

            observation: str | None = None
            pending_read: ContextReadReceipt | None = None
            terminal_call_id: str | None = None
            for _round in range(self._action_policy.max_provider_calls):
                action_index = action_session.next_action_index
                allowed_actions = (
                    ("answer",)
                    if action_index == self._action_policy.max_provider_calls
                    else ("search", "read", "answer")
                )
                call_id = f"{attempt_id}_call_{len(calls) + 1}"
                schema = _context_action_schema(
                    binding,
                    action_index,
                    allowed_actions,
                    self._action_policy,
                )
                if observation is None:
                    payload, component_paths, reference_paths = _initial_payload(
                        binding,
                        projection,
                        action_session,
                        action_index=action_index,
                        allowed_actions=allowed_actions,
                        action_policy=self._action_policy,
                        reference_context=reference_context,
                    )
                    call_kind = (
                        ProviderCallKind.DEBATE
                        if scope.subject_kind is ContextSubjectKind.DEBATE
                        else ProviderCallKind.CONVERSATION_RETRIEVAL_INITIAL
                    )
                    reference_context = ()
                else:
                    payload, component_paths = _continuation_payload(
                        binding,
                        action_index=action_index,
                        allowed_actions=allowed_actions,
                        action_policy=self._action_policy,
                        observation=observation,
                    )
                    reference_paths = ()
                    call_kind = (
                        ProviderCallKind.DEBATE
                        if scope.subject_kind is ContextSubjectKind.DEBATE
                        else ProviderCallKind.CONVERSATION_RETRIEVAL_CONTINUATION
                    )
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
                )
                stage = "provider"
                provider_failure = True
                try:
                    result = await provider.invoke_context(call_request)
                except BaseException as failure:
                    facts = context_provider_failure_facts(failure)
                    if facts.accounting is not None:
                        generation = self._append_failure_call_without_replacing(
                            self._ledger,
                            key,
                            generation,
                            attempt_id,
                            calls,
                            call_id,
                            facts.accounting,
                        )
                    raise
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
                generation = self._append_call(
                    self._ledger,
                    key,
                    generation,
                    attempt_id,
                    calls,
                    call_id,
                    result.accounting,
                )
                if result.accounting.payload.call_kind is not call_kind:
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
                action = _decode_context_action(
                    result.response_text,
                    allowed_actions=allowed_actions,
                )
                terminal_provider_succeeded = False
                if type(action) is ContextSearchAction:
                    stage = "action"
                    history_advanced = True
                    _emit_context_event(
                        event_sink,
                        ContextExecutionEvent(
                            ContextExecutionEventKind.SEARCH_STARTED,
                            scope,
                            action_index=action_index,
                        ),
                    )
                    search_observation = action_session.search(action)
                    observation = serialize_context_action_observation(
                        search_observation
                    )
                    _emit_context_event(
                        event_sink,
                        ContextExecutionEvent(
                            ContextExecutionEventKind.SEARCH_COMPLETED,
                            scope,
                            action_index=action_index,
                            search_hit_count=len(search_observation.hits),
                            observation_utf8_bytes=len(
                                observation.encode("utf-8", errors="strict")
                            ),
                            truncated=search_observation.truncated,
                        ),
                    )
                    continue
                if type(action) is ContextReadAction:
                    stage = "action"
                    history_advanced = True
                    _emit_context_event(
                        event_sink,
                        ContextExecutionEvent(
                            ContextExecutionEventKind.READ_STARTED,
                            scope,
                            action_index=action_index,
                        ),
                    )
                    read_observation, pending_read = action_session.read(action)
                    observation = serialize_context_action_observation(
                        read_observation
                    )
                    turn_count, event_count = _conversation_read_counts(
                        read_observation
                    )
                    _emit_context_event(
                        event_sink,
                        ContextExecutionEvent(
                            ContextExecutionEventKind.READ_COMPLETED,
                            scope,
                            action_index=action_index,
                            transcript_turn_count=turn_count,
                            transcript_event_count=event_count,
                            observation_utf8_bytes=len(
                                observation.encode("utf-8", errors="strict")
                            ),
                        ),
                    )
                    continue

                terminal_provider_succeeded = True
                terminal_call_id = call_id
                stage = "answer"
                authorization = action_session.authorize_answer(action)
                stage = "source_revalidation"
                _registry, revalidated_manifest, revalidated_materials = (
                    self._compose_context(
                        request,
                        scope,
                        writing_artifacts,
                        focused_conversation,
                        active_debate,
                        own_agent_state,
                    )
                )
                if (
                    revalidated_manifest != manifest
                    or revalidated_materials != materials
                ):
                    raise ContextSourceError(ContextSourceErrorCode.SOURCE_DRIFT)
                stage = "answer_validation"
                if answer_validator is not None:
                    _require_sync_none(answer_validator(action.answer, authorization))
                _emit_context_event(
                    event_sink,
                    ContextExecutionEvent(
                        ContextExecutionEventKind.ANSWER_AUTHORIZED,
                        scope,
                        action_index=action_index,
                    ),
                )
                if request.mandatory_references:
                    stage = "reference_complete"
                    provider.complete_references(scope, policy_fingerprint)
                stage = "commit"
                assert current_thread_id is not None
                assert terminal_call_id is not None
                commit = self._ledger.commit_attempt(
                    key,
                    expected_generation=generation,
                    authorization=ContextLedgerCommitAuthorization(
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
                        output_action_authorization_id=(
                            authorization.authorization_id
                        ),
                    ),
                )
                terminal_provider_succeeded = False
                _emit_context_event_without_replacing(
                    event_sink,
                    ContextExecutionEvent(
                        ContextExecutionEventKind.CHECKPOINT_ADVANCED,
                        scope,
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
            if isinstance(failure, ContextSourceError) or stage in {
                "source",
                "source_revalidation",
            }:
                _emit_context_event_without_replacing(
                    event_sink,
                    ContextExecutionEvent(
                        ContextExecutionEventKind.INVALIDATED,
                        scope,
                    ),
                )
            failure_code = getattr(getattr(failure, "code", None), "value", "")
            if isinstance(failure_code, str) and (
                "budget_exceeded" in failure_code
                or "limit_exceeded" in failure_code
            ):
                _emit_context_event_without_replacing(
                    event_sink,
                    ContextExecutionEvent(
                        ContextExecutionEventKind.BUDGET_EXHAUSTED,
                        scope,
                    ),
                )
            if stage == "exhausted":
                _emit_context_event_without_replacing(
                    event_sink,
                    ContextExecutionEvent(
                        ContextExecutionEventKind.ACTION_EXHAUSTED,
                        scope,
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
                    provider.taint_context_thread(scope, policy_fingerprint)
                except BaseException:
                    pass
                _emit_context_event_without_replacing(
                    event_sink,
                    ContextExecutionEvent(
                        ContextExecutionEventKind.TAINTED,
                        scope,
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
                ContextExecutionEvent(
                    ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED,
                    scope,
                ),
            )
            raise
