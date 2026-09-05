"""Durable content-free checkpoints and accounting for agent context."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import secrets
import stat
import threading
import time
import weakref
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import TypeVar

try:  # pragma: no cover - exercised by fail-closed platform tests.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - platform dependent.
    _fcntl = None

from ..workspace import (
    Workspace,
    open_workspace_root_directory,
    verify_workspace_root_directory,
)
from ..runtime_state_paths import CONTEXT_LEDGER_DIRECTORY
from .accounting import (
    ContextComponentBytes,
    ContextComponentKind,
    ProviderCallAccounting,
    ProviderCallKind,
    ProviderCallState,
    ProviderPayloadAccounting,
    ProviderUsage,
)
from .manifest import (
    ContextAuthorityKind,
    ContextManifestEntry,
    ContextManifestError,
    ContextPriority,
    ContextProvenance,
    ContextScope,
    ContextSemanticChannel,
    ContextSourceKind,
    ContextSubjectKind,
    context_manifest_entry_key,
    validate_context_digest,
    validate_context_identity,
)
from .planning import (
    ContextBootstrapReason,
    ContextBudgetAccounting,
    ContextCatalogCursor,
    ContextCheckpointProposal,
    ContextCheckpointRecord,
    ContextCheckpointSource,
    ContextCheckpointState,
    ContextCheckpointView,
    ContextDecisionAccounting,
    ContextDecisionKind,
    ContextDeliveryMode,
    ContextPlanningConditions,
    ContextPlanningError,
    ContextProjectionAccounting,
    ContextReferenceBudgetUsage,
    ContextRecordCursor,
    ContextSearchableAccounting,
    ContextSourceBudgetUsage,
)

__all__ = [
    "CONTEXT_LEDGER_SCHEMA_VERSION",
    "MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES",
    "MAX_CONTEXT_LEDGER_SOURCES",
    "MAX_CONTEXT_LEDGER_PROVIDER_CALLS",
    "CONTEXT_LEDGER_LOCK_TIMEOUT_SECONDS",
    "ContextLedger",
    "ContextLedgerKey",
    "ContextLedgerRecordState",
    "ContextLedgerAttemptOutcome",
    "ContextLedgerAttemptPlan",
    "ContextLedgerProviderCall",
    "ContextLedgerAttempt",
    "ContextLedgerReadyCheckpoint",
    "ContextLedgerRecord",
    "ContextLedgerInspection",
    "ContextLedgerCommitAuthorization",
    "ContextLedgerError",
    "ContextLedgerErrorCode",
    "context_checkpoint_proposal_digest",
    "context_ledger_scope_key",
]

CONTEXT_LEDGER_SCHEMA_VERSION = 2
MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES = 8 * 1024 * 1024
MAX_CONTEXT_LEDGER_SOURCES = 4096
MAX_CONTEXT_LEDGER_PROVIDER_CALLS = 32
CONTEXT_LEDGER_LOCK_TIMEOUT_SECONDS = 5.0

_LEDGER_PATH = tuple(CONTEXT_LEDGER_DIRECTORY.split("/"))
_PRIVATE_PATH_START = 2
_LOCK_RETRY_SECONDS = 0.01
_MAX_LOCK_FREE_READ_ATTEMPTS = 8
_MAX_GENERATION = (1 << 63) - 1
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class ContextLedgerRecordState(StrEnum):
    """Durable states for one scope/provider ledger record."""

    INCOMPLETE = "incomplete"
    READY = "ready"
    TAINTED = "tainted"
    STALE = "stale"


class ContextLedgerAttemptOutcome(StrEnum):
    """Content-free outcomes retained for one reserved attempt."""

    IN_PROGRESS = "in_progress"
    COMMITTED = "committed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    MALFORMED = "malformed"
    REFERENCE_INVALID = "reference_invalid"
    SOURCE_INVALID = "source_invalid"
    OUTPUT_INVALID = "output_invalid"
    INCOMPLETE = "incomplete"
    AMBIGUOUS = "ambiguous"


class ContextLedgerErrorCode(StrEnum):
    """Stable content-free ledger failure categories."""

    INVALID_VALUE = "invalid_value"
    INVALID_TRANSITION = "invalid_transition"
    CONFLICT = "conflict"
    COMMIT_NOT_AUTHORIZED = "commit_not_authorized"
    LIMIT_EXCEEDED = "limit_exceeded"
    LOCK_TIMEOUT = "lock_timeout"
    STORAGE_UNSUPPORTED = "storage_unsupported"
    STORAGE_UNSAFE = "storage_unsafe"
    STORAGE_FAILURE = "storage_failure"


_ERROR_MESSAGES = {
    ContextLedgerErrorCode.INVALID_VALUE: "context ledger value is invalid",
    ContextLedgerErrorCode.INVALID_TRANSITION: (
        "context ledger transition is invalid"
    ),
    ContextLedgerErrorCode.CONFLICT: "context ledger generation conflicts",
    ContextLedgerErrorCode.COMMIT_NOT_AUTHORIZED: (
        "context ledger commit is not authorized"
    ),
    ContextLedgerErrorCode.LIMIT_EXCEEDED: "context ledger limit is exceeded",
    ContextLedgerErrorCode.LOCK_TIMEOUT: "context ledger writer lock timed out",
    ContextLedgerErrorCode.STORAGE_UNSUPPORTED: (
        "context ledger storage is unsupported"
    ),
    ContextLedgerErrorCode.STORAGE_UNSAFE: "context ledger storage is unsafe",
    ContextLedgerErrorCode.STORAGE_FAILURE: "context ledger storage failed",
}


class ContextLedgerError(ValueError):
    """Report one fixed content-free ledger failure."""

    def __init__(self, code: ContextLedgerErrorCode) -> None:
        if type(code) is not ContextLedgerErrorCode:
            raise TypeError("ledger error code must be a ContextLedgerErrorCode")
        self.code = code
        super().__init__(_ERROR_MESSAGES[code])


def _invalid() -> ContextLedgerError:
    """Construct the fixed invalid-value failure."""

    return ContextLedgerError(ContextLedgerErrorCode.INVALID_VALUE)


def _exact_nonnegative_integer(value: object) -> int:
    """Return one exact nonnegative integer or fail without reflection."""

    if type(value) is not int or value < 0:
        raise _invalid()
    assert isinstance(value, int)
    return value


def _positive_generation(value: object) -> int:
    """Return one bounded positive generation."""

    generation = _exact_nonnegative_integer(value)
    if generation == 0 or generation > _MAX_GENERATION:
        raise _invalid()
    return generation


def _safe_identity(value: object) -> str:
    """Validate one content-free identity without retaining rejected text."""

    result: str | None
    try:
        result = validate_context_identity(value)
    except ContextManifestError:
        result = None
    if result is None:
        raise _invalid()
    return result


def _safe_digest(value: object) -> str:
    """Validate one lowercase SHA-256 identity without reflected failures."""

    result: str | None
    try:
        result = validate_context_digest(value)
    except ContextManifestError:
        result = None
    if result is None:
        raise _invalid()
    return result


def _parse_timestamp(value: object) -> datetime:
    """Parse one canonical UTC timestamp or fail content-free."""

    parsed: datetime | None = None
    if type(value) is str:
        try:
            parsed = datetime.strptime(value, _TIMESTAMP_FORMAT).replace(tzinfo=UTC)
        except ValueError:
            parsed = None
    if parsed is None:
        raise _invalid()
    return parsed


def _format_timestamp(value: datetime) -> str:
    """Return one canonical UTC timestamp."""

    if type(value) is not datetime or value.tzinfo is None:
        raise _invalid()
    return value.astimezone(UTC).strftime(_TIMESTAMP_FORMAT)


def _canonical_json(value: object) -> bytes:
    """Encode one validated ledger value as canonical strict UTF-8 JSON."""

    encoded: bytes | None
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="strict")
    except (RecursionError, TypeError, ValueError, UnicodeEncodeError):
        encoded = None
    if encoded is None:
        raise _invalid()
    return encoded


@dataclass(frozen=True, slots=True)
class ContextLedgerKey:
    """Exact scope/provider identity for one independently stored ledger."""

    scope: ContextScope
    provider_id: str

    def __post_init__(self) -> None:
        if type(self.scope) is not ContextScope:
            raise _invalid()
        _safe_identity(self.provider_id)


def _scope_key_value(key: ContextLedgerKey) -> list[object]:
    """Return the canonical, length-unambiguous scope-key payload."""

    return [
        "context-ledger-scope-v1",
        key.scope.workspace_id,
        key.scope.session_id,
        key.scope.subject_kind.value,
        key.scope.subject_id,
        key.scope.agent_id,
        key.provider_id,
    ]


def context_ledger_scope_key(key: ContextLedgerKey) -> str:
    """Return the canonical SHA-256 storage identity for one ledger key."""

    if type(key) is not ContextLedgerKey:
        raise _invalid()
    digest = hashlib.sha256(_canonical_json(_scope_key_value(key))).hexdigest()
    return f"sha256:{digest}"


@dataclass(frozen=True, slots=True)
class ContextLedgerProviderCall:
    """One ordered physical provider-call accounting record."""

    ordinal: int
    call_id: str
    accounting: ProviderCallAccounting

    def __post_init__(self) -> None:
        ordinal = _exact_nonnegative_integer(self.ordinal)
        if ordinal == 0:
            raise _invalid()
        _safe_identity(self.call_id)
        if type(self.accounting) is not ProviderCallAccounting:
            raise _invalid()


@dataclass(frozen=True, slots=True)
class ContextLedgerAttemptPlan:
    """Content-free reservation facts fixed before provider work begins."""

    attempt_id: str
    operation_id: str
    result_correlation_id: str
    proposal_digest: str
    delivery_mode: ContextDeliveryMode
    bootstrap_reason: ContextBootstrapReason | None
    proposed_delta_depth: int
    mandatory_reference_count: int
    planned_provider_thread_id: str | None
    reference_operation_id: str | None
    projection_accounting: ContextProjectionAccounting

    def __post_init__(self) -> None:
        _safe_identity(self.attempt_id)
        _safe_identity(self.operation_id)
        _safe_identity(self.result_correlation_id)
        _safe_digest(self.proposal_digest)
        if type(self.delivery_mode) is not ContextDeliveryMode:
            raise _invalid()
        if self.bootstrap_reason is not None and (
            type(self.bootstrap_reason) is not ContextBootstrapReason
        ):
            raise _invalid()
        depth = _exact_nonnegative_integer(self.proposed_delta_depth)
        reference_count = _exact_nonnegative_integer(
            self.mandatory_reference_count
        )
        if self.planned_provider_thread_id is not None:
            _safe_identity(self.planned_provider_thread_id)
        if self.reference_operation_id is not None:
            _safe_identity(self.reference_operation_id)
        if type(self.projection_accounting) is not ContextProjectionAccounting:
            raise _invalid()
        accounting = self.projection_accounting
        if (
            accounting.mode is not self.delivery_mode
            or accounting.bootstrap_reason is not self.bootstrap_reason
            or accounting.proposed_delta_depth != depth
            or accounting.mandatory_reference_count != reference_count
        ):
            raise _invalid()
        if self.delivery_mode is ContextDeliveryMode.BOOTSTRAP:
            if self.bootstrap_reason is None or depth != 0:
                raise _invalid()
        elif (
            self.bootstrap_reason is not None
            or depth == 0
            or self.planned_provider_thread_id is None
        ):
            raise _invalid()
        if (reference_count == 0) is not (
            self.reference_operation_id is None
        ):
            raise _invalid()


@dataclass(frozen=True, slots=True)
class ContextLedgerAttempt:
    """One persisted attempt with ordered calls and a content-free outcome."""

    plan: ContextLedgerAttemptPlan
    provider_calls: tuple[ContextLedgerProviderCall, ...]
    outcome: ContextLedgerAttemptOutcome
    started_at: str
    updated_at: str
    completed_at: str | None = None
    commit_authorization_digest: str | None = None

    def __post_init__(self) -> None:
        if type(self.plan) is not ContextLedgerAttemptPlan:
            raise _invalid()
        if type(self.provider_calls) is not tuple or any(
            type(call) is not ContextLedgerProviderCall
            for call in self.provider_calls
        ):
            raise _invalid()
        if len(self.provider_calls) > MAX_CONTEXT_LEDGER_PROVIDER_CALLS:
            raise ContextLedgerError(ContextLedgerErrorCode.LIMIT_EXCEEDED)
        if tuple(call.ordinal for call in self.provider_calls) != tuple(
            range(1, len(self.provider_calls) + 1)
        ):
            raise _invalid()
        call_ids = tuple(call.call_id for call in self.provider_calls)
        if len(set(call_ids)) != len(call_ids):
            raise _invalid()
        if type(self.outcome) is not ContextLedgerAttemptOutcome:
            raise _invalid()
        started = _parse_timestamp(self.started_at)
        updated = _parse_timestamp(self.updated_at)
        if updated < started:
            raise _invalid()
        if self.outcome is ContextLedgerAttemptOutcome.IN_PROGRESS:
            if (
                self.completed_at is not None
                or self.commit_authorization_digest is not None
            ):
                raise _invalid()
        else:
            if self.completed_at is None:
                raise _invalid()
            completed = _parse_timestamp(self.completed_at)
            if completed < updated:
                raise _invalid()
            if self.outcome is ContextLedgerAttemptOutcome.COMMITTED:
                _safe_digest(self.commit_authorization_digest)
                if (
                    not self.provider_calls
                    or self.provider_calls[-1].accounting.state
                    is not ProviderCallState.SUBMITTED
                ):
                    raise _invalid()
            elif self.commit_authorization_digest is not None:
                raise _invalid()


@dataclass(frozen=True, slots=True)
class ContextLedgerReadyCheckpoint:
    """Verified content-free checkpoint eligible for planner suppression."""

    conditions: ContextPlanningConditions
    provider_thread_id: str
    sources: tuple[ContextCheckpointSource, ...]
    delta_depth: int
    proposal_digest: str
    committed_at: str
    catalog_sources: tuple[ContextCatalogCursor, ...] = ()
    source_alias_high_watermark: int = 0

    def __post_init__(self) -> None:
        if type(self.conditions) is not ContextPlanningConditions:
            raise _invalid()
        _safe_identity(self.provider_thread_id)
        if type(self.sources) is not tuple or any(
            type(source) is not ContextCheckpointSource for source in self.sources
        ):
            raise _invalid()
        if len(self.sources) > MAX_CONTEXT_LEDGER_SOURCES:
            raise ContextLedgerError(ContextLedgerErrorCode.LIMIT_EXCEEDED)
        if type(self.catalog_sources) is not tuple or any(
            type(source) is not ContextCatalogCursor
            for source in self.catalog_sources
        ):
            raise _invalid()
        if len(self.catalog_sources) > MAX_CONTEXT_LEDGER_SOURCES:
            raise ContextLedgerError(ContextLedgerErrorCode.LIMIT_EXCEEDED)
        ordered = tuple(
            sorted(
                self.sources,
                key=lambda source: context_manifest_entry_key(source.entry),
            )
        )
        source_ids = tuple(source.entry.source_id for source in ordered)
        if len(set(source_ids)) != len(source_ids):
            raise _invalid()
        ordered_catalog = tuple(
            sorted(
                self.catalog_sources,
                key=lambda source: context_manifest_entry_key(source.entry),
            )
        )
        _exact_nonnegative_integer(self.delta_depth)
        _safe_digest(self.proposal_digest)
        _parse_timestamp(self.committed_at)
        object.__setattr__(self, "sources", ordered)
        object.__setattr__(self, "catalog_sources", ordered_catalog)
        proposal = ContextCheckpointProposal(
            self.conditions,
            ordered,
            self.delta_depth,
            ordered_catalog,
            self.source_alias_high_watermark,
        )
        if context_checkpoint_proposal_digest(proposal) != self.proposal_digest:
            raise _invalid()


@dataclass(frozen=True, slots=True)
class ContextLedgerRecord:
    """One complete schema-v2 record stored under a hashed ledger key."""

    key: ContextLedgerKey
    generation: int
    state: ContextLedgerRecordState
    created_at: str
    updated_at: str
    attempt: ContextLedgerAttempt | None
    ready_checkpoint: ContextLedgerReadyCheckpoint | None
    schema_version: int = CONTEXT_LEDGER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.key) is not ContextLedgerKey:
            raise _invalid()
        _positive_generation(self.generation)
        if type(self.state) is not ContextLedgerRecordState:
            raise _invalid()
        created = _parse_timestamp(self.created_at)
        updated = _parse_timestamp(self.updated_at)
        if updated < created:
            raise _invalid()
        if self.attempt is not None and type(self.attempt) is not ContextLedgerAttempt:
            raise _invalid()
        if self.ready_checkpoint is not None and (
            type(self.ready_checkpoint) is not ContextLedgerReadyCheckpoint
        ):
            raise _invalid()
        if self.schema_version != CONTEXT_LEDGER_SCHEMA_VERSION or (
            type(self.schema_version) is not int
        ):
            raise _invalid()
        if self.attempt is not None and self.attempt.updated_at > self.updated_at:
            raise _invalid()
        if self.state is ContextLedgerRecordState.INCOMPLETE:
            if (
                self.attempt is None
                or self.attempt.outcome is not ContextLedgerAttemptOutcome.IN_PROGRESS
                or self.ready_checkpoint is not None
            ):
                raise _invalid()
        elif self.state is ContextLedgerRecordState.READY:
            if (
                self.attempt is None
                or self.attempt.outcome is not ContextLedgerAttemptOutcome.COMMITTED
                or self.ready_checkpoint is None
                or self.attempt.completed_at != self.ready_checkpoint.committed_at
                or self.attempt.plan.proposal_digest
                != self.ready_checkpoint.proposal_digest
                or self.ready_checkpoint.conditions.scope != self.key.scope
                or self.ready_checkpoint.conditions.provider_id
                != self.key.provider_id
            ):
                raise _invalid()
        elif self.state is ContextLedgerRecordState.TAINTED:
            if (
                self.attempt is None
                or self.attempt.outcome
                in {
                    ContextLedgerAttemptOutcome.IN_PROGRESS,
                    ContextLedgerAttemptOutcome.COMMITTED,
                }
                or self.ready_checkpoint is not None
            ):
                raise _invalid()
        else:
            if self.ready_checkpoint is not None or (
                self.attempt is not None
                and self.attempt.outcome is ContextLedgerAttemptOutcome.IN_PROGRESS
            ):
                raise _invalid()


@dataclass(frozen=True, slots=True)
class ContextLedgerInspection:
    """Mutation-free ledger observation and exact planner checkpoint view."""

    key: ContextLedgerKey
    generation: int
    checkpoint_view: ContextCheckpointView
    record: ContextLedgerRecord | None

    def __post_init__(self) -> None:
        if type(self.key) is not ContextLedgerKey:
            raise _invalid()
        generation = _exact_nonnegative_integer(self.generation)
        if type(self.checkpoint_view) is not ContextCheckpointView:
            raise _invalid()
        if self.record is None:
            if generation != 0 or self.checkpoint_view.state not in {
                ContextCheckpointState.MISSING,
                ContextCheckpointState.CORRUPT,
            }:
                raise _invalid()
        elif (
            type(self.record) is not ContextLedgerRecord
            or self.record.key != self.key
            or generation != self.record.generation
        ):
            raise _invalid()
        else:
            expected_state = {
                ContextLedgerRecordState.INCOMPLETE: ContextCheckpointState.TAINTED,
                ContextLedgerRecordState.TAINTED: ContextCheckpointState.TAINTED,
                ContextLedgerRecordState.STALE: ContextCheckpointState.STALE,
                ContextLedgerRecordState.READY: ContextCheckpointState.READY,
            }[self.record.state]
            if self.checkpoint_view.state is not expected_state:
                raise _invalid()
            if self.record.state is ContextLedgerRecordState.READY:
                ready = self.record.ready_checkpoint
                assert ready is not None
                if (
                    self.checkpoint_view.conditions != ready.conditions
                    or self.checkpoint_view.provider_thread_id
                    != ready.provider_thread_id
                    or self.checkpoint_view.sources != ready.sources
                    or self.checkpoint_view.delta_depth != ready.delta_depth
                ):
                    raise _invalid()


@dataclass(frozen=True, slots=True)
class ContextLedgerCommitAuthorization:
    """Strict content-free evidence required to advance one READY checkpoint."""

    attempt_id: str
    operation_id: str
    result_correlation_id: str
    proposal: ContextCheckpointProposal
    revalidated_proposal_digest: str
    terminal_call_id: str
    final_provider_thread_id: str
    completed_reference_operation_id: str | None
    output_action_authorization_id: str

    def __post_init__(self) -> None:
        _safe_identity(self.attempt_id)
        _safe_identity(self.operation_id)
        _safe_identity(self.result_correlation_id)
        if type(self.proposal) is not ContextCheckpointProposal:
            raise _invalid()
        _safe_digest(self.revalidated_proposal_digest)
        _safe_identity(self.terminal_call_id)
        _safe_identity(self.final_provider_thread_id)
        if self.completed_reference_operation_id is not None:
            _safe_identity(self.completed_reference_operation_id)
        _safe_identity(self.output_action_authorization_id)


_UNSAFE_OUTCOMES = frozenset(
    outcome
    for outcome in ContextLedgerAttemptOutcome
    if outcome
    not in {
        ContextLedgerAttemptOutcome.IN_PROGRESS,
        ContextLedgerAttemptOutcome.COMMITTED,
    }
)


class _DecodeError(Exception):
    """Internal marker for content-free schema rejection."""


_EnumT = TypeVar("_EnumT", bound=StrEnum)
_ResultT = TypeVar("_ResultT")


def _decoded_dict(value: object, fields: frozenset[str]) -> dict[str, object]:
    """Return one exact-field JSON object or reject it privately."""

    if type(value) is not dict or frozenset(value) != fields:
        raise _DecodeError
    assert isinstance(value, dict)
    return value


def _decoded_list(value: object) -> list[object]:
    """Return one exact JSON list."""

    if type(value) is not list:
        raise _DecodeError
    assert isinstance(value, list)
    return value


def _decoded_string(value: object) -> str:
    """Return one JSON string."""

    if type(value) is not str:
        raise _DecodeError
    assert isinstance(value, str)
    return value


def _decoded_integer(value: object) -> int:
    """Return one exact JSON integer, excluding booleans."""

    if type(value) is not int:
        raise _DecodeError
    assert isinstance(value, int)
    return value


def _decoded_optional_integer(value: object) -> int | None:
    """Return one nullable exact JSON integer."""

    if value is None:
        return None
    return _decoded_integer(value)


def _decoded_optional_string(value: object) -> str | None:
    """Return one nullable exact JSON string."""

    if value is None:
        return None
    return _decoded_string(value)


def _decoded_boolean(value: object) -> bool:
    """Return one exact JSON boolean."""

    if type(value) is not bool:
        raise _DecodeError
    assert isinstance(value, bool)
    return value


def _decoded_enum(enum_type: type[_EnumT], value: object) -> _EnumT:
    """Return one exact string-backed enum value."""

    text = _decoded_string(value)
    result: _EnumT | None
    try:
        result = enum_type(text)
    except ValueError:
        result = None
    if result is None:
        raise _DecodeError
    return result


def _scope_value(scope: ContextScope) -> dict[str, object]:
    return {
        "agent_id": scope.agent_id,
        "session_id": scope.session_id,
        "subject_id": scope.subject_id,
        "subject_kind": scope.subject_kind.value,
        "workspace_id": scope.workspace_id,
    }


def _decode_scope(value: object) -> ContextScope:
    item = _decoded_dict(
        value,
        frozenset(
            {"workspace_id", "session_id", "subject_kind", "subject_id", "agent_id"}
        ),
    )
    return ContextScope(
        workspace_id=_decoded_string(item["workspace_id"]),
        session_id=_decoded_string(item["session_id"]),
        subject_kind=_decoded_enum(ContextSubjectKind, item["subject_kind"]),
        subject_id=_decoded_string(item["subject_id"]),
        agent_id=_decoded_string(item["agent_id"]),
    )


def _provenance_value(value: ContextProvenance) -> dict[str, object]:
    return {
        "authority_kind": value.authority_kind.value,
        "locator": value.locator,
    }


def _decode_provenance(value: object) -> ContextProvenance:
    item = _decoded_dict(value, frozenset({"authority_kind", "locator"}))
    return ContextProvenance(
        authority_kind=_decoded_enum(
            ContextAuthorityKind,
            item["authority_kind"],
        ),
        locator=_decoded_string(item["locator"]),
    )


def _entry_value(entry: ContextManifestEntry) -> dict[str, object]:
    return {
        "content_digest": entry.content_digest,
        "ordinal": entry.ordinal,
        "priority": entry.priority.value,
        "provenance": _provenance_value(entry.provenance),
        "revision": entry.revision,
        "searchable": entry.searchable,
        "semantic_channel": entry.semantic_channel.value,
        "source_id": entry.source_id,
        "source_kind": entry.source_kind.value,
        "utf8_bytes": entry.utf8_bytes,
    }


def _decode_entry(value: object) -> ContextManifestEntry:
    item = _decoded_dict(
        value,
        frozenset(
            {
                "source_id",
                "source_kind",
                "semantic_channel",
                "priority",
                "ordinal",
                "revision",
                "content_digest",
                "provenance",
                "utf8_bytes",
                "searchable",
            }
        ),
    )
    return ContextManifestEntry(
        source_id=_decoded_string(item["source_id"]),
        source_kind=_decoded_enum(ContextSourceKind, item["source_kind"]),
        semantic_channel=_decoded_enum(
            ContextSemanticChannel,
            item["semantic_channel"],
        ),
        priority=_decoded_enum(ContextPriority, item["priority"]),
        ordinal=_decoded_integer(item["ordinal"]),
        revision=_decoded_string(item["revision"]),
        content_digest=_decoded_string(item["content_digest"]),
        provenance=_decode_provenance(item["provenance"]),
        utf8_bytes=_decoded_integer(item["utf8_bytes"]),
        searchable=_decoded_boolean(item["searchable"]),
    )


def _conditions_value(value: ContextPlanningConditions) -> dict[str, object]:
    return {
        "definition_fingerprint": value.definition_fingerprint,
        "policy_fingerprint": value.policy_fingerprint,
        "provider_id": value.provider_id,
        "schema_version": value.schema_version,
        "scope": _scope_value(value.scope),
    }


def _decode_conditions(value: object) -> ContextPlanningConditions:
    item = _decoded_dict(
        value,
        frozenset(
            {
                "scope",
                "provider_id",
                "definition_fingerprint",
                "policy_fingerprint",
                "schema_version",
            }
        ),
    )
    return ContextPlanningConditions(
        scope=_decode_scope(item["scope"]),
        provider_id=_decoded_string(item["provider_id"]),
        definition_fingerprint=_decoded_string(item["definition_fingerprint"]),
        policy_fingerprint=_decoded_string(item["policy_fingerprint"]),
        schema_version=_decoded_integer(item["schema_version"]),
    )


def _record_cursor_value(value: ContextRecordCursor) -> dict[str, object]:
    return {
        "boundary": list(value.boundary),
        "content_digest": value.content_digest,
        "record_id": value.record_id,
        "utf8_bytes": value.utf8_bytes,
    }


def _decode_record_cursor(value: object) -> ContextRecordCursor:
    item = _decoded_dict(
        value,
        frozenset({"record_id", "boundary", "content_digest", "utf8_bytes"}),
    )
    boundary = _decoded_list(item["boundary"])
    if len(boundary) != 5:
        raise _DecodeError
    return ContextRecordCursor(
        record_id=_decoded_string(item["record_id"]),
        boundary_kind=_decoded_string(boundary[0]),
        boundary_start=_decoded_optional_integer(boundary[1]),
        boundary_end=_decoded_optional_integer(boundary[2]),
        boundary_first_id=_decoded_optional_string(boundary[3]),
        boundary_last_id=_decoded_optional_string(boundary[4]),
        content_digest=_decoded_string(item["content_digest"]),
        utf8_bytes=_decoded_integer(item["utf8_bytes"]),
    )


def _checkpoint_record_value(value: ContextCheckpointRecord) -> dict[str, object]:
    return {
        **_record_cursor_value(value.cursor),
        "record_alias": value.record_alias,
    }


def _decode_checkpoint_record(value: object) -> ContextCheckpointRecord:
    item = _decoded_dict(
        value,
        frozenset(
            {
                "record_alias",
                "record_id",
                "boundary",
                "content_digest",
                "utf8_bytes",
            }
        ),
    )
    return ContextCheckpointRecord(
        _decode_record_cursor(
            {
                "record_id": item["record_id"],
                "boundary": item["boundary"],
                "content_digest": item["content_digest"],
                "utf8_bytes": item["utf8_bytes"],
            }
        ),
        _decoded_integer(item["record_alias"]),
    )


def _checkpoint_source_value(value: ContextCheckpointSource) -> dict[str, object]:
    return {
        "entry": _entry_value(value.entry),
        "records": [
            _checkpoint_record_value(record) for record in value.records
        ],
    }


def _decode_checkpoint_source(value: object) -> ContextCheckpointSource:
    item = _decoded_dict(
        value,
        frozenset({"entry", "records"}),
    )
    records = _decoded_list(item["records"])
    if len(records) > 10_000:
        raise _DecodeError
    source = ContextCheckpointSource(
        _decode_entry(item["entry"]),
        tuple(_decode_checkpoint_record(record) for record in records),
    )
    return source


def _catalog_cursor_value(value: ContextCatalogCursor) -> dict[str, object]:
    return {
        "entry": _entry_value(value.entry),
        "record_alias_high_watermark": value.record_alias_high_watermark,
        "source_alias": value.source_alias,
    }


def _decode_catalog_cursor(value: object) -> ContextCatalogCursor:
    item = _decoded_dict(
        value,
        frozenset(
            {"entry", "record_alias_high_watermark", "source_alias"}
        ),
    )
    return ContextCatalogCursor(
        _decode_entry(item["entry"]),
        _decoded_string(item["source_alias"]),
        _decoded_integer(item["record_alias_high_watermark"]),
    )


def _proposal_value(value: ContextCheckpointProposal) -> dict[str, object]:
    return {
        "catalog_sources": [
            _catalog_cursor_value(source) for source in value.catalog_sources
        ],
        "conditions": _conditions_value(value.conditions),
        "delta_depth": value.delta_depth,
        "source_alias_high_watermark": value.source_alias_high_watermark,
        "sources": [_checkpoint_source_value(source) for source in value.sources],
    }


def context_checkpoint_proposal_digest(
    proposal: ContextCheckpointProposal,
) -> str:
    """Hash the complete canonical content-free checkpoint proposal."""

    if type(proposal) is not ContextCheckpointProposal:
        raise _invalid()
    if len(proposal.sources) > MAX_CONTEXT_LEDGER_SOURCES:
        raise ContextLedgerError(ContextLedgerErrorCode.LIMIT_EXCEEDED)
    if len(proposal.catalog_sources) > MAX_CONTEXT_LEDGER_SOURCES:
        raise ContextLedgerError(ContextLedgerErrorCode.LIMIT_EXCEEDED)
    digest = hashlib.sha256(_canonical_json(_proposal_value(proposal))).hexdigest()
    return f"sha256:{digest}"


def _component_value(value: ContextComponentBytes) -> dict[str, object]:
    return {
        "identity": value.identity,
        "kind": value.kind.value,
        "raw_mandatory_reference_utf8_bytes": (
            value.raw_mandatory_reference_utf8_bytes
        ),
        "serialized_utf8_bytes": value.serialized_utf8_bytes,
    }


def _decode_component(value: object) -> ContextComponentBytes:
    item = _decoded_dict(
        value,
        frozenset(
            {
                "kind",
                "serialized_utf8_bytes",
                "raw_mandatory_reference_utf8_bytes",
                "identity",
            }
        ),
    )
    return ContextComponentBytes(
        kind=_decoded_enum(ContextComponentKind, item["kind"]),
        serialized_utf8_bytes=_decoded_integer(item["serialized_utf8_bytes"]),
        raw_mandatory_reference_utf8_bytes=_decoded_integer(
            item["raw_mandatory_reference_utf8_bytes"]
        ),
        identity=_decoded_optional_string(item["identity"]),
    )


def _payload_accounting_value(
    value: ProviderPayloadAccounting,
) -> dict[str, object]:
    return {
        "call_kind": value.call_kind.value,
        "components": [_component_value(component) for component in value.components],
        "payload_utf8_bytes": value.payload_utf8_bytes,
        "schema_utf8_bytes": value.schema_utf8_bytes,
    }


def _decode_payload_accounting(value: object) -> ProviderPayloadAccounting:
    item = _decoded_dict(
        value,
        frozenset(
            {"call_kind", "schema_utf8_bytes", "payload_utf8_bytes", "components"}
        ),
    )
    components = tuple(
        _decode_component(component)
        for component in _decoded_list(item["components"])
    )
    return ProviderPayloadAccounting(
        call_kind=_decoded_enum(ProviderCallKind, item["call_kind"]),
        schema_utf8_bytes=_decoded_integer(item["schema_utf8_bytes"]),
        payload_utf8_bytes=_decoded_integer(item["payload_utf8_bytes"]),
        components=components,
    )


def _usage_value(value: ProviderUsage) -> dict[str, object]:
    return {
        "cached_input_tokens": value.cached_input_tokens,
        "input_tokens": value.input_tokens,
        "output_tokens": value.output_tokens,
    }


def _decode_usage(value: object) -> ProviderUsage:
    item = _decoded_dict(
        value,
        frozenset({"input_tokens", "output_tokens", "cached_input_tokens"}),
    )
    return ProviderUsage(
        input_tokens=_decoded_optional_integer(item["input_tokens"]),
        output_tokens=_decoded_optional_integer(item["output_tokens"]),
        cached_input_tokens=_decoded_optional_integer(
            item["cached_input_tokens"]
        ),
    )


def _call_accounting_value(value: ProviderCallAccounting) -> dict[str, object]:
    return {
        "actual_transport_utf8_bytes": value.actual_transport_utf8_bytes,
        "binding_components": [
            _component_value(component) for component in value.binding_components
        ],
        "payload": _payload_accounting_value(value.payload),
        "state": value.state.value,
        "usage": None if value.usage is None else _usage_value(value.usage),
    }


def _decode_call_accounting(value: object) -> ProviderCallAccounting:
    item = _decoded_dict(
        value,
        frozenset(
            {
                "payload",
                "binding_components",
                "state",
                "actual_transport_utf8_bytes",
                "usage",
            }
        ),
    )
    usage = None if item["usage"] is None else _decode_usage(item["usage"])
    return ProviderCallAccounting(
        payload=_decode_payload_accounting(item["payload"]),
        binding_components=tuple(
            _decode_component(component)
            for component in _decoded_list(item["binding_components"])
        ),
        state=_decoded_enum(ProviderCallState, item["state"]),
        actual_transport_utf8_bytes=_decoded_optional_integer(
            item["actual_transport_utf8_bytes"]
        ),
        usage=usage,
    )


def _decision_accounting_value(
    value: ContextDecisionAccounting,
) -> dict[str, object]:
    return {
        "decision": value.decision.value,
        "priority": value.priority.value,
        "raw_source_utf8_bytes": value.raw_source_utf8_bytes,
        "semantic_channel": value.semantic_channel.value,
        "source_count": value.source_count,
    }


def _decode_decision_accounting(value: object) -> ContextDecisionAccounting:
    item = _decoded_dict(
        value,
        frozenset(
            {
                "priority",
                "semantic_channel",
                "decision",
                "source_count",
                "raw_source_utf8_bytes",
            }
        ),
    )
    return ContextDecisionAccounting(
        priority=_decoded_enum(ContextPriority, item["priority"]),
        semantic_channel=_decoded_enum(
            ContextSemanticChannel,
            item["semantic_channel"],
        ),
        decision=_decoded_enum(ContextDecisionKind, item["decision"]),
        source_count=_decoded_integer(item["source_count"]),
        raw_source_utf8_bytes=_decoded_integer(item["raw_source_utf8_bytes"]),
    )


_SEARCHABLE_FIELDS = (
    "total_source_count",
    "total_source_utf8_bytes",
    "selected_primary_count",
    "selected_primary_utf8_bytes",
    "deduplicated_pointer_count",
    "deduplicated_pointer_utf8_bytes",
    "checkpoint_suppressed_count",
    "checkpoint_suppressed_utf8_bytes",
    "class_budget_omitted_count",
    "class_budget_omitted_utf8_bytes",
    "material_omitted_count",
    "material_omitted_utf8_bytes",
    "selected_source_count",
    "selected_source_utf8_bytes",
    "unprojected_source_count",
    "unprojected_source_utf8_bytes",
)


def _searchable_value(value: ContextSearchableAccounting) -> dict[str, object]:
    return {name: getattr(value, name) for name in _SEARCHABLE_FIELDS}


def _decode_searchable(value: object) -> ContextSearchableAccounting:
    item = _decoded_dict(value, frozenset(_SEARCHABLE_FIELDS))
    return ContextSearchableAccounting(
        **{name: _decoded_integer(item[name]) for name in _SEARCHABLE_FIELDS}
    )


def _source_budget_value(value: ContextSourceBudgetUsage) -> dict[str, object]:
    return {
        "configured_utf8_bytes": value.configured_utf8_bytes,
        "remaining_utf8_bytes": value.remaining_utf8_bytes,
        "selected_unique_utf8_bytes": value.selected_unique_utf8_bytes,
    }


def _decode_source_budget(value: object) -> ContextSourceBudgetUsage:
    item = _decoded_dict(
        value,
        frozenset(
            {
                "configured_utf8_bytes",
                "selected_unique_utf8_bytes",
                "remaining_utf8_bytes",
            }
        ),
    )
    return ContextSourceBudgetUsage(
        configured_utf8_bytes=_decoded_integer(item["configured_utf8_bytes"]),
        selected_unique_utf8_bytes=_decoded_integer(
            item["selected_unique_utf8_bytes"]
        ),
        remaining_utf8_bytes=_decoded_integer(item["remaining_utf8_bytes"]),
    )


def _reference_budget_value(
    value: ContextReferenceBudgetUsage,
) -> dict[str, object]:
    return {
        "configured_utf8_bytes": value.configured_utf8_bytes,
        "remaining_utf8_bytes": value.remaining_utf8_bytes,
        "used_utf8_bytes": value.used_utf8_bytes,
    }


def _decode_reference_budget(value: object) -> ContextReferenceBudgetUsage:
    item = _decoded_dict(
        value,
        frozenset({"configured_utf8_bytes", "used_utf8_bytes", "remaining_utf8_bytes"}),
    )
    return ContextReferenceBudgetUsage(
        configured_utf8_bytes=_decoded_integer(item["configured_utf8_bytes"]),
        used_utf8_bytes=_decoded_integer(item["used_utf8_bytes"]),
        remaining_utf8_bytes=_decoded_integer(item["remaining_utf8_bytes"]),
    )


def _budget_accounting_value(value: ContextBudgetAccounting) -> dict[str, object]:
    return {
        "mandatory_references": _reference_budget_value(value.mandatory_references),
        "p0": _source_budget_value(value.p0),
        "p1": _source_budget_value(value.p1),
        "p2": _source_budget_value(value.p2),
        "p3": _source_budget_value(value.p3),
        "total": _source_budget_value(value.total),
    }


def _decode_budget_accounting(value: object) -> ContextBudgetAccounting:
    item = _decoded_dict(
        value,
        frozenset({"total", "p0", "p1", "p2", "p3", "mandatory_references"}),
    )
    return ContextBudgetAccounting(
        total=_decode_source_budget(item["total"]),
        p0=_decode_source_budget(item["p0"]),
        p1=_decode_source_budget(item["p1"]),
        p2=_decode_source_budget(item["p2"]),
        p3=_decode_source_budget(item["p3"]),
        mandatory_references=_decode_reference_budget(item["mandatory_references"]),
    )


_PROJECTION_INTEGER_FIELDS = (
    "proposed_delta_depth",
    "logical_source_count",
    "logical_source_utf8_bytes",
    "selected_logical_source_count",
    "selected_logical_utf8_bytes",
    "projected_unit_count",
    "projected_unique_utf8_bytes",
    "saved_duplicate_utf8_bytes",
    "mandatory_reference_count",
    "mandatory_reference_utf8_bytes",
    "removed_checkpoint_source_count",
    "removed_checkpoint_source_utf8_bytes",
)


def _projection_accounting_value(
    value: ContextProjectionAccounting,
) -> dict[str, object]:
    result: dict[str, object] = {
        "bootstrap_reason": (
            None if value.bootstrap_reason is None else value.bootstrap_reason.value
        ),
        "budgets": _budget_accounting_value(value.budgets),
        "decision_buckets": [
            _decision_accounting_value(bucket) for bucket in value.decision_buckets
        ],
        "mode": value.mode.value,
        "searchable": _searchable_value(value.searchable),
    }
    result.update({name: getattr(value, name) for name in _PROJECTION_INTEGER_FIELDS})
    return result


def _decode_projection_accounting(value: object) -> ContextProjectionAccounting:
    fields = frozenset(
        {
            "mode",
            "bootstrap_reason",
            "searchable",
            "budgets",
            "decision_buckets",
            *_PROJECTION_INTEGER_FIELDS,
        }
    )
    item = _decoded_dict(value, fields)
    reason = (
        None
        if item["bootstrap_reason"] is None
        else _decoded_enum(ContextBootstrapReason, item["bootstrap_reason"])
    )
    integers = {
        name: _decoded_integer(item[name]) for name in _PROJECTION_INTEGER_FIELDS
    }
    return ContextProjectionAccounting(
        mode=_decoded_enum(ContextDeliveryMode, item["mode"]),
        bootstrap_reason=reason,
        searchable=_decode_searchable(item["searchable"]),
        budgets=_decode_budget_accounting(item["budgets"]),
        decision_buckets=tuple(
            _decode_decision_accounting(bucket)
            for bucket in _decoded_list(item["decision_buckets"])
        ),
        **integers,
    )


def _key_value(key: ContextLedgerKey) -> dict[str, object]:
    return {
        "provider_id": key.provider_id,
        "scope": _scope_value(key.scope),
        "scope_key": context_ledger_scope_key(key),
    }


def _decode_key(value: object) -> ContextLedgerKey:
    item = _decoded_dict(value, frozenset({"scope_key", "scope", "provider_id"}))
    key = ContextLedgerKey(
        scope=_decode_scope(item["scope"]),
        provider_id=_decoded_string(item["provider_id"]),
    )
    if _decoded_string(item["scope_key"]) != context_ledger_scope_key(key):
        raise _DecodeError
    return key


def _provider_call_value(value: ContextLedgerProviderCall) -> dict[str, object]:
    return {
        "accounting": _call_accounting_value(value.accounting),
        "call_id": value.call_id,
        "ordinal": value.ordinal,
    }


def _decode_provider_call(value: object) -> ContextLedgerProviderCall:
    item = _decoded_dict(value, frozenset({"ordinal", "call_id", "accounting"}))
    return ContextLedgerProviderCall(
        ordinal=_decoded_integer(item["ordinal"]),
        call_id=_decoded_string(item["call_id"]),
        accounting=_decode_call_accounting(item["accounting"]),
    )


def _attempt_plan_value(value: ContextLedgerAttemptPlan) -> dict[str, object]:
    return {
        "attempt_id": value.attempt_id,
        "bootstrap_reason": (
            None if value.bootstrap_reason is None else value.bootstrap_reason.value
        ),
        "delivery_mode": value.delivery_mode.value,
        "mandatory_reference_count": value.mandatory_reference_count,
        "operation_id": value.operation_id,
        "planned_provider_thread_id": value.planned_provider_thread_id,
        "projection_accounting": _projection_accounting_value(
            value.projection_accounting
        ),
        "proposal_digest": value.proposal_digest,
        "proposed_delta_depth": value.proposed_delta_depth,
        "reference_operation_id": value.reference_operation_id,
        "result_correlation_id": value.result_correlation_id,
    }


def _decode_attempt_plan(value: object) -> ContextLedgerAttemptPlan:
    item = _decoded_dict(
        value,
        frozenset(
            {
                "attempt_id",
                "operation_id",
                "result_correlation_id",
                "proposal_digest",
                "delivery_mode",
                "bootstrap_reason",
                "proposed_delta_depth",
                "mandatory_reference_count",
                "planned_provider_thread_id",
                "reference_operation_id",
                "projection_accounting",
            }
        ),
    )
    reason = (
        None
        if item["bootstrap_reason"] is None
        else _decoded_enum(ContextBootstrapReason, item["bootstrap_reason"])
    )
    return ContextLedgerAttemptPlan(
        attempt_id=_decoded_string(item["attempt_id"]),
        operation_id=_decoded_string(item["operation_id"]),
        result_correlation_id=_decoded_string(item["result_correlation_id"]),
        proposal_digest=_decoded_string(item["proposal_digest"]),
        delivery_mode=_decoded_enum(ContextDeliveryMode, item["delivery_mode"]),
        bootstrap_reason=reason,
        proposed_delta_depth=_decoded_integer(item["proposed_delta_depth"]),
        mandatory_reference_count=_decoded_integer(
            item["mandatory_reference_count"]
        ),
        planned_provider_thread_id=_decoded_optional_string(
            item["planned_provider_thread_id"]
        ),
        reference_operation_id=_decoded_optional_string(
            item["reference_operation_id"]
        ),
        projection_accounting=_decode_projection_accounting(
            item["projection_accounting"]
        ),
    )


def _attempt_value(value: ContextLedgerAttempt) -> dict[str, object]:
    return {
        "commit_authorization_digest": value.commit_authorization_digest,
        "completed_at": value.completed_at,
        "outcome": value.outcome.value,
        "plan": _attempt_plan_value(value.plan),
        "provider_calls": [
            _provider_call_value(call) for call in value.provider_calls
        ],
        "started_at": value.started_at,
        "updated_at": value.updated_at,
    }


def _decode_attempt(value: object) -> ContextLedgerAttempt:
    item = _decoded_dict(
        value,
        frozenset(
            {
                "plan",
                "provider_calls",
                "outcome",
                "started_at",
                "updated_at",
                "completed_at",
                "commit_authorization_digest",
            }
        ),
    )
    return ContextLedgerAttempt(
        plan=_decode_attempt_plan(item["plan"]),
        provider_calls=tuple(
            _decode_provider_call(call)
            for call in _decoded_list(item["provider_calls"])
        ),
        outcome=_decoded_enum(ContextLedgerAttemptOutcome, item["outcome"]),
        started_at=_decoded_string(item["started_at"]),
        updated_at=_decoded_string(item["updated_at"]),
        completed_at=_decoded_optional_string(item["completed_at"]),
        commit_authorization_digest=_decoded_optional_string(
            item["commit_authorization_digest"]
        ),
    )


def _ready_checkpoint_value(
    value: ContextLedgerReadyCheckpoint,
) -> dict[str, object]:
    return {
        "catalog_sources": [
            _catalog_cursor_value(source) for source in value.catalog_sources
        ],
        "committed_at": value.committed_at,
        "conditions": _conditions_value(value.conditions),
        "delta_depth": value.delta_depth,
        "proposal_digest": value.proposal_digest,
        "provider_thread_id": value.provider_thread_id,
        "source_alias_high_watermark": value.source_alias_high_watermark,
        "sources": [
            _checkpoint_source_value(source) for source in value.sources
        ],
    }


def _decode_ready_checkpoint(value: object) -> ContextLedgerReadyCheckpoint:
    item = _decoded_dict(
        value,
        frozenset(
            {
                "conditions",
                "catalog_sources",
                "provider_thread_id",
                "sources",
                "delta_depth",
                "proposal_digest",
                "committed_at",
                "source_alias_high_watermark",
            }
        ),
    )
    sources = _decoded_list(item["sources"])
    catalog_sources = _decoded_list(item["catalog_sources"])
    if (
        len(sources) > MAX_CONTEXT_LEDGER_SOURCES
        or len(catalog_sources) > MAX_CONTEXT_LEDGER_SOURCES
    ):
        raise _DecodeError
    return ContextLedgerReadyCheckpoint(
        conditions=_decode_conditions(item["conditions"]),
        provider_thread_id=_decoded_string(item["provider_thread_id"]),
        sources=tuple(_decode_checkpoint_source(source) for source in sources),
        delta_depth=_decoded_integer(item["delta_depth"]),
        proposal_digest=_decoded_string(item["proposal_digest"]),
        committed_at=_decoded_string(item["committed_at"]),
        catalog_sources=tuple(
            _decode_catalog_cursor(source) for source in catalog_sources
        ),
        source_alias_high_watermark=_decoded_integer(
            item["source_alias_high_watermark"]
        ),
    )


def _record_value(value: ContextLedgerRecord) -> dict[str, object]:
    return {
        "attempt": None if value.attempt is None else _attempt_value(value.attempt),
        "created_at": value.created_at,
        "generation": value.generation,
        "key": _key_value(value.key),
        "ready_checkpoint": (
            None
            if value.ready_checkpoint is None
            else _ready_checkpoint_value(value.ready_checkpoint)
        ),
        "schema_version": value.schema_version,
        "state": value.state.value,
        "updated_at": value.updated_at,
    }


def _record_bytes(value: ContextLedgerRecord) -> bytes:
    payload = _canonical_json(_record_value(value))
    if len(payload) > MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES:
        raise ContextLedgerError(ContextLedgerErrorCode.LIMIT_EXCEEDED)
    return payload


def _reject_constant(_value: str) -> object:
    raise _DecodeError


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DecodeError
        result[key] = value
    return result


def _decode_record(
    payload: bytes,
    expected_key: ContextLedgerKey,
) -> ContextLedgerRecord | None:
    """Decode one exact canonical record, returning None for safe corruption."""

    result: ContextLedgerRecord | None = None
    try:
        text = payload.decode("utf-8", errors="strict")
        raw = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        item = _decoded_dict(
            raw,
            frozenset(
                {
                    "schema_version",
                    "key",
                    "generation",
                    "state",
                    "created_at",
                    "updated_at",
                    "attempt",
                    "ready_checkpoint",
                }
            ),
        )
        if _decoded_integer(item["schema_version"]) != CONTEXT_LEDGER_SCHEMA_VERSION:
            raise _DecodeError
        key = _decode_key(item["key"])
        if key != expected_key:
            raise _DecodeError
        attempt = None if item["attempt"] is None else _decode_attempt(item["attempt"])
        ready = (
            None
            if item["ready_checkpoint"] is None
            else _decode_ready_checkpoint(item["ready_checkpoint"])
        )
        candidate = ContextLedgerRecord(
            key=key,
            generation=_decoded_integer(item["generation"]),
            state=_decoded_enum(ContextLedgerRecordState, item["state"]),
            created_at=_decoded_string(item["created_at"]),
            updated_at=_decoded_string(item["updated_at"]),
            attempt=attempt,
            ready_checkpoint=ready,
            schema_version=_decoded_integer(item["schema_version"]),
        )
        if _record_bytes(candidate) != payload:
            raise _DecodeError
        result = candidate
    except (
        ContextLedgerError,
        ContextManifestError,
        ContextPlanningError,
        TypeError,
        ValueError,
        RecursionError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DecodeError,
    ):
        result = None
    return result


def _authorization_digest(value: ContextLedgerCommitAuthorization) -> str:
    payload = {
        "attempt_id": value.attempt_id,
        "completed_reference_operation_id": value.completed_reference_operation_id,
        "final_provider_thread_id": value.final_provider_thread_id,
        "operation_id": value.operation_id,
        "output_action_authorization_id": value.output_action_authorization_id,
        "proposal_digest": context_checkpoint_proposal_digest(value.proposal),
        "result_correlation_id": value.result_correlation_id,
        "revalidated_proposal_digest": value.revalidated_proposal_digest,
        "terminal_call_id": value.terminal_call_id,
    }
    return f"sha256:{hashlib.sha256(_canonical_json(payload)).hexdigest()}"


class _StorageFault(Exception):
    """Carry only a fixed storage failure code to the public boundary."""

    def __init__(self, code: ContextLedgerErrorCode) -> None:
        self.code = code


def _storage_fault(code: ContextLedgerErrorCode) -> None:
    raise _StorageFault(code)


def _storage_error_for(error: OSError) -> ContextLedgerErrorCode:
    if error.errno in {
        errno.EISDIR,
        errno.ELOOP,
        errno.ENOTDIR,
        getattr(errno, "EMLINK", errno.ELOOP),
    }:
        return ContextLedgerErrorCode.STORAGE_UNSAFE
    return ContextLedgerErrorCode.STORAGE_FAILURE


def _require_storage_features() -> None:
    required_constants = ("O_DIRECTORY", "O_NOFOLLOW")
    required_dir_fd = (os.open, os.mkdir, os.stat, os.unlink)
    if (
        _fcntl is None
        or any(not getattr(os, name, 0) for name in required_constants)
        or any(function not in os.supports_dir_fd for function in required_dir_fd)
    ):
        _storage_fault(ContextLedgerErrorCode.STORAGE_UNSUPPORTED)


def _identity(value: os.stat_result) -> tuple[int, int]:
    identity = (value.st_dev, value.st_ino)
    if identity[0] <= 0 or identity[1] <= 0:
        _storage_fault(ContextLedgerErrorCode.STORAGE_UNSUPPORTED)
    return identity


def _directory_flags() -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    for name in ("O_CLOEXEC", "O_NOINHERIT", "O_BINARY", "O_NONBLOCK"):
        flags |= getattr(os, name, 0)
    return flags


def _file_flags(*, write: bool) -> int:
    flags = (os.O_RDWR if write else os.O_RDONLY) | os.O_NOFOLLOW
    for name in ("O_CLOEXEC", "O_NOINHERIT", "O_BINARY", "O_NONBLOCK"):
        flags |= getattr(os, name, 0)
    return flags


def _set_noninheritable(descriptor: int) -> None:
    try:
        os.set_inheritable(descriptor, False)
    except (AttributeError, OSError):
        pass


def _verify_directory(
    parent: int,
    name: str,
    descriptor: int,
    *,
    private: bool,
) -> None:
    try:
        opened = os.fstat(descriptor)
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except OSError as error:
        _storage_fault(_storage_error_for(error))
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(named.st_mode)
        or _identity(opened) != _identity(named)
    ):
        _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)
    if private and (
        stat.S_IMODE(opened.st_mode) != 0o700
        or stat.S_IMODE(named.st_mode) != 0o700
    ):
        _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)


def _open_child_directory(
    parent: int,
    name: str,
    *,
    create: bool,
    private: bool,
) -> int | None:
    created = False
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent)
    except FileNotFoundError:
        if not create:
            return None
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
            created = True
        except FileExistsError:
            pass
        except OSError as error:
            _storage_fault(_storage_error_for(error))
        try:
            descriptor = os.open(name, _directory_flags(), dir_fd=parent)
        except OSError as error:
            _storage_fault(_storage_error_for(error))
    except OSError as error:
        _storage_fault(_storage_error_for(error))
    assert descriptor is not None
    _set_noninheritable(descriptor)
    if created:
        try:
            os.fchmod(descriptor, 0o700)
            os.fsync(parent)
        except OSError as error:
            try:
                os.close(descriptor)
            except OSError:
                pass
            _storage_fault(_storage_error_for(error))
    try:
        _verify_directory(parent, name, descriptor, private=private)
        return descriptor
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


@contextmanager
def _ledger_directory(
    workspace: Workspace,
    *,
    create: bool,
) -> Iterator[int | None]:
    _require_storage_features()
    descriptors: list[int] = []
    body_failure: BaseException | None = None
    try:
        try:
            root = open_workspace_root_directory(workspace)
        except Exception:
            _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)
        descriptors.append(root)
        current = root
        missing = False
        for index, name in enumerate(_LEDGER_PATH):
            child = _open_child_directory(
                current,
                name,
                create=create,
                private=index >= _PRIVATE_PATH_START,
            )
            if child is None:
                missing = True
                break
            descriptors.append(child)
            current = child
        try:
            verify_workspace_root_directory(workspace, root)
        except Exception:
            _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)
        try:
            yield None if missing else current
        except BaseException as error:
            body_failure = error
            raise
        finally:
            try:
                verify_workspace_root_directory(workspace, root)
            except Exception:
                if body_failure is None:
                    _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)
    finally:
        cleanup_failed = False
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                cleanup_failed = True
        if cleanup_failed and body_failure is None:
            _storage_fault(ContextLedgerErrorCode.STORAGE_FAILURE)


def _verify_regular(
    parent: int,
    name: str,
    descriptor: int,
) -> os.stat_result:
    try:
        opened = os.fstat(descriptor)
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except OSError as error:
        _storage_fault(_storage_error_for(error))
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(named.st_mode)
        or opened.st_nlink != 1
        or named.st_nlink != 1
        or stat.S_IMODE(opened.st_mode) != 0o600
        or stat.S_IMODE(named.st_mode) != 0o600
        or _identity(opened) != _identity(named)
    ):
        _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)
    return opened


def _open_regular(
    parent: int,
    name: str,
    *,
    write: bool,
    create: bool,
) -> tuple[int, bool]:
    flags = _file_flags(write=write)
    created = False
    if create:
        try:
            descriptor = os.open(
                name,
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=parent,
            )
            created = True
        except FileExistsError:
            try:
                descriptor = os.open(name, flags, dir_fd=parent)
            except OSError as error:
                _storage_fault(_storage_error_for(error))
        except OSError as error:
            _storage_fault(_storage_error_for(error))
    else:
        try:
            descriptor = os.open(name, flags, dir_fd=parent)
        except OSError as error:
            _storage_fault(_storage_error_for(error))
    _set_noninheritable(descriptor)
    if created:
        try:
            os.fchmod(descriptor, 0o600)
        except OSError as error:
            try:
                os.close(descriptor)
            except OSError:
                pass
            _storage_fault(_storage_error_for(error))
    try:
        _verify_regular(parent, name, descriptor)
        return descriptor, created
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _open_existing_regular(
    parent: int,
    name: str,
    *,
    write: bool,
) -> int | None:
    """Open one existing safe regular file or return None if absent."""

    try:
        descriptor = os.open(name, _file_flags(write=write), dir_fd=parent)
    except FileNotFoundError:
        return None
    except OSError as error:
        _storage_fault(_storage_error_for(error))
    _set_noninheritable(descriptor)
    try:
        _verify_regular(parent, name, descriptor)
        return descriptor
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _read_descriptor_metadata(descriptor: int) -> os.stat_result:
    """Return safe metadata for one reader-owned regular descriptor."""

    try:
        opened = os.fstat(descriptor)
    except OSError as error:
        _storage_fault(_storage_error_for(error))
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink not in {0, 1}
        or stat.S_IMODE(opened.st_mode) != 0o600
    ):
        _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)
    return opened


def _read_named_metadata(parent: int, name: str) -> os.stat_result:
    """Return safe metadata for the currently named reader target."""

    try:
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except OSError as error:
        _storage_fault(_storage_error_for(error))
    if (
        not stat.S_ISREG(named.st_mode)
        or named.st_nlink != 1
        or stat.S_IMODE(named.st_mode) != 0o600
    ):
        _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)
    return named


def _open_read_candidate(
    parent: int,
    name: str,
) -> tuple[int, os.stat_result, os.stat_result] | None:
    """Open one safe read candidate while permitting atomic replacement."""

    try:
        descriptor = os.open(name, _file_flags(write=False), dir_fd=parent)
    except FileNotFoundError:
        return None
    except OSError as error:
        # A stable nonregular entry can fail before a descriptor exists. Probe
        # it without following links so that it remains a security failure.
        _read_named_metadata(parent, name)
        _storage_fault(_storage_error_for(error))
    _set_noninheritable(descriptor)
    try:
        opened = _read_descriptor_metadata(descriptor)
        named = _read_named_metadata(parent, name)
        return descriptor, opened, named
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _same_read_content(
    before: os.stat_result,
    after: os.stat_result,
) -> bool:
    """Return whether one opened inode retained the same exact content facts."""

    return (
        _identity(before) == _identity(after)
        and before.st_size == after.st_size
        and before.st_mtime_ns == after.st_mtime_ns
    )


def _read_payload_once(
    parent: int,
    name: str,
) -> tuple[bool, bytes | None, bool]:
    """Read one complete inode and report whether its name was replaced."""

    candidate = _open_read_candidate(parent, name)
    if candidate is None:
        return False, None, False
    descriptor, initial, initial_named = candidate
    try:
        oversized = initial.st_size > MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES
        chunks: list[bytes] = []
        total = 0
        while not oversized:
            try:
                chunk = os.read(
                    descriptor,
                    min(
                        65_536,
                        MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES + 1 - total,
                    ),
                )
            except OSError as error:
                _storage_fault(_storage_error_for(error))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES:
                oversized = True

        final = _read_descriptor_metadata(descriptor)
        final_named = _read_named_metadata(parent, name)
        if not _same_read_content(initial, final):
            _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)
        if not oversized and total != final.st_size:
            _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)

        replaced = (
            _identity(initial) != _identity(initial_named)
            or _identity(initial_named) != _identity(final_named)
            or _identity(final) != _identity(final_named)
        )
        if replaced:
            # Re-stat after observing the new name. Atomic replacement leaves
            # the safely opened predecessor unlinked but content-stable.
            settled = _read_descriptor_metadata(descriptor)
            if settled.st_nlink != 0 or not _same_read_content(initial, settled):
                _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)
        elif (
            initial.st_nlink != 1
            or final.st_nlink != 1
            or initial.st_ctime_ns != final.st_ctime_ns
        ):
            _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)
        return True, None if oversized else b"".join(chunks), replaced
    finally:
        try:
            os.close(descriptor)
        except OSError:
            _storage_fault(ContextLedgerErrorCode.STORAGE_FAILURE)


def _record_name(key: ContextLedgerKey) -> str:
    return f"{context_ledger_scope_key(key)[len('sha256:'): ]}.json"


def _lock_name(key: ContextLedgerKey) -> str:
    return f"{context_ledger_scope_key(key)[len('sha256:'): ]}.lock"


def _read_payload(parent: int, name: str) -> tuple[bool, bytes | None]:
    predecessor: tuple[bool, bytes | None] | None = None
    for _attempt in range(_MAX_LOCK_FREE_READ_ATTEMPTS):
        found, payload, replaced = _read_payload_once(parent, name)
        if not found:
            return False, None
        predecessor = (True, payload)
        if not replaced:
            return predecessor
    assert predecessor is not None
    return predecessor


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        try:
            written = os.write(descriptor, payload[offset:])
        except OSError as error:
            _storage_fault(_storage_error_for(error))
        if written <= 0:
            _storage_fault(ContextLedgerErrorCode.STORAGE_FAILURE)
        offset += written


def _existing_target_is_safe(parent: int, name: str) -> None:
    descriptor = _open_existing_regular(parent, name, write=False)
    if descriptor is None:
        return
    try:
        _verify_regular(parent, name, descriptor)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            _storage_fault(ContextLedgerErrorCode.STORAGE_FAILURE)


def _replace_record(parent: int, record: ContextLedgerRecord) -> None:
    payload = _record_bytes(record)
    target = _record_name(record.key)
    _existing_target_is_safe(parent, target)
    temporary = f".tmp-{secrets.token_hex(16)}"
    descriptor: int | None = None
    replaced = False
    try:
        descriptor, created = _open_regular(
            parent,
            temporary,
            write=True,
            create=True,
        )
        if not created:
            _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)
        _write_all(descriptor, payload)
        try:
            os.fsync(descriptor)
        except OSError as error:
            _storage_fault(_storage_error_for(error))
        final = _verify_regular(parent, temporary, descriptor)
        if final.st_size != len(payload):
            _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)
        try:
            os.close(descriptor)
        except OSError as error:
            descriptor = None
            _storage_fault(_storage_error_for(error))
        descriptor = None
        try:
            os.replace(
                temporary,
                target,
                src_dir_fd=parent,
                dst_dir_fd=parent,
            )
        except OSError as error:
            _storage_fault(_storage_error_for(error))
        replaced = True
        target_descriptor, _created = _open_regular(
            parent,
            target,
            write=False,
            create=False,
        )
        try:
            _verify_regular(parent, target, target_descriptor)
        finally:
            try:
                os.close(target_descriptor)
            except OSError:
                _storage_fault(ContextLedgerErrorCode.STORAGE_FAILURE)
        try:
            os.fsync(parent)
        except OSError as error:
            _storage_fault(_storage_error_for(error))
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if not replaced:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass
            except OSError:
                pass


class _WriterState:
    """Weakly retained in-process serialization for one ledger key."""

    __slots__ = ("lock", "__weakref__")

    def __init__(self) -> None:
        self.lock = threading.Lock()


_WRITER_REGISTRY_GUARD = threading.Lock()
_WRITER_STATES: weakref.WeakValueDictionary[str, _WriterState] = (
    weakref.WeakValueDictionary()
)


def _writer_state(workspace: Workspace, key: ContextLedgerKey) -> _WriterState:
    registry_key = f"{os.fspath(workspace.root)}\0{context_ledger_scope_key(key)}"
    with _WRITER_REGISTRY_GUARD:
        state = _WRITER_STATES.get(registry_key)
        if state is None:
            state = _WriterState()
            _WRITER_STATES[registry_key] = state
        return state


def _acquire_advisory_lock(descriptor: int, *, deadline: float) -> None:
    assert _fcntl is not None
    contention = {
        errno.EACCES,
        errno.EAGAIN,
        getattr(errno, "EDEADLK", errno.EAGAIN),
    }
    while True:
        try:
            _fcntl.flock(descriptor, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            return
        except OSError as error:
            if error.errno not in contention:
                _storage_fault(_storage_error_for(error))
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                _storage_fault(ContextLedgerErrorCode.LOCK_TIMEOUT)
            time.sleep(min(_LOCK_RETRY_SECONDS, remaining))


@contextmanager
def _writer_directory(
    workspace: Workspace,
    key: ContextLedgerKey,
) -> Iterator[int]:
    started = time.monotonic()
    state = _writer_state(workspace, key)
    if not state.lock.acquire(timeout=CONTEXT_LEDGER_LOCK_TIMEOUT_SECONDS):
        _storage_fault(ContextLedgerErrorCode.LOCK_TIMEOUT)
    lock_descriptor: int | None = None
    advisory_acquired = False
    body_failure: BaseException | None = None
    try:
        with _ledger_directory(workspace, create=True) as directory:
            assert directory is not None
            lock_descriptor, created = _open_regular(
                directory,
                _lock_name(key),
                write=True,
                create=True,
            )
            if created:
                try:
                    os.fsync(directory)
                except OSError as error:
                    _storage_fault(_storage_error_for(error))
            deadline = started + CONTEXT_LEDGER_LOCK_TIMEOUT_SECONDS
            _acquire_advisory_lock(lock_descriptor, deadline=deadline)
            advisory_acquired = True
            _verify_regular(directory, _lock_name(key), lock_descriptor)
            yield directory
    except BaseException as error:
        body_failure = error
        raise
    finally:
        cleanup_failed = False
        if lock_descriptor is not None:
            if advisory_acquired and _fcntl is not None:
                try:
                    _fcntl.flock(lock_descriptor, _fcntl.LOCK_UN)
                except OSError:
                    cleanup_failed = True
            try:
                os.close(lock_descriptor)
            except OSError:
                cleanup_failed = True
        try:
            state.lock.release()
        except RuntimeError:
            cleanup_failed = True
        if cleanup_failed and body_failure is None:
            _storage_fault(ContextLedgerErrorCode.STORAGE_FAILURE)


def _inspection_for_record(record: ContextLedgerRecord) -> ContextLedgerInspection:
    if record.state is ContextLedgerRecordState.READY:
        assert record.ready_checkpoint is not None
        ready = record.ready_checkpoint
        view = ContextCheckpointView(
            state=ContextCheckpointState.READY,
            conditions=ready.conditions,
            provider_thread_id=ready.provider_thread_id,
            sources=ready.sources,
            delta_depth=ready.delta_depth,
            catalog_sources=ready.catalog_sources,
            source_alias_high_watermark=ready.source_alias_high_watermark,
        )
    elif record.state is ContextLedgerRecordState.STALE:
        view = ContextCheckpointView(ContextCheckpointState.STALE)
    else:
        view = ContextCheckpointView(ContextCheckpointState.TAINTED)
    return ContextLedgerInspection(
        key=record.key,
        generation=record.generation,
        checkpoint_view=view,
        record=record,
    )


def _inspect_in_directory(
    directory: int,
    key: ContextLedgerKey,
) -> ContextLedgerInspection:
    found, payload = _read_payload(directory, _record_name(key))
    if not found:
        return ContextLedgerInspection(
            key,
            0,
            ContextCheckpointView(ContextCheckpointState.MISSING),
            None,
        )
    if payload is None:
        return ContextLedgerInspection(
            key,
            0,
            ContextCheckpointView(ContextCheckpointState.CORRUPT),
            None,
        )
    record = _decode_record(payload, key)
    if record is None:
        return ContextLedgerInspection(
            key,
            0,
            ContextCheckpointView(ContextCheckpointState.CORRUPT),
            None,
        )
    return _inspection_for_record(record)


def _expected_generation(value: object) -> int:
    generation = _exact_nonnegative_integer(value)
    if generation > _MAX_GENERATION:
        raise _invalid()
    return generation


def _next_generation(value: int) -> int:
    if value >= _MAX_GENERATION:
        raise ContextLedgerError(ContextLedgerErrorCode.LIMIT_EXCEEDED)
    return value + 1


def _require_generation(
    inspection: ContextLedgerInspection,
    expected_generation: int,
) -> None:
    if inspection.generation != expected_generation:
        raise ContextLedgerError(ContextLedgerErrorCode.CONFLICT)


def _commit_is_authorized(
    key: ContextLedgerKey,
    attempt: ContextLedgerAttempt,
    authorization: ContextLedgerCommitAuthorization,
) -> bool:
    plan = attempt.plan
    proposal = authorization.proposal
    digest = context_checkpoint_proposal_digest(proposal)
    if (
        authorization.attempt_id != plan.attempt_id
        or authorization.operation_id != plan.operation_id
        or authorization.result_correlation_id != plan.result_correlation_id
        or digest != plan.proposal_digest
        or authorization.revalidated_proposal_digest != digest
        or proposal.conditions.scope != key.scope
        or proposal.conditions.provider_id != key.provider_id
        or proposal.delta_depth != plan.proposed_delta_depth
        or len(proposal.sources) > MAX_CONTEXT_LEDGER_SOURCES
        or not attempt.provider_calls
    ):
        return False
    terminal = attempt.provider_calls[-1]
    if (
        terminal.call_id != authorization.terminal_call_id
        or terminal.accounting.state is not ProviderCallState.SUBMITTED
        or terminal.accounting.payload.call_kind
        in {
            ProviderCallKind.UNCLASSIFIED,
            ProviderCallKind.WRITING_ARTIFACT_REFERENCE_READ,
        }
    ):
        return False
    if plan.mandatory_reference_count == 0:
        if authorization.completed_reference_operation_id is not None:
            return False
    elif (
        authorization.completed_reference_operation_id
        != plan.reference_operation_id
    ):
        return False
    if plan.delivery_mode is ContextDeliveryMode.DELTA:
        return authorization.final_provider_thread_id == (
            plan.planned_provider_thread_id
        )
    return True


class ContextLedger:
    """Own secure durable state for content-free context checkpoints.

    Construction retains only the explicit workspace and clock. It performs no
    filesystem operation; inspection is mutation-free, and all directory or
    lock creation is deferred until the first writer transition.
    """

    __slots__ = ("_clock", "_workspace")

    def __init__(
        self,
        workspace: Workspace,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if type(workspace) is not Workspace:
            raise _invalid()
        if clock is not None and not callable(clock):
            raise _invalid()
        self._workspace = workspace
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)

    def _now(self) -> str:
        value: datetime | None
        try:
            value = self._clock()
        except Exception:
            value = None
        if value is None:
            raise _invalid()
        return _format_timestamp(value)

    @staticmethod
    def _translate(operation: Callable[[], _ResultT]) -> _ResultT:
        code: ContextLedgerErrorCode | None = None
        result: _ResultT | None = None
        try:
            result = operation()
        except ContextLedgerError:
            raise
        except _StorageFault as failure:
            code = failure.code
        except Exception:
            code = ContextLedgerErrorCode.STORAGE_FAILURE
        if code is not None:
            raise ContextLedgerError(code)
        assert result is not None
        return result

    def inspect(self, key: ContextLedgerKey) -> ContextLedgerInspection:
        """Inspect one ledger without creating a directory, lock, or record."""

        if type(key) is not ContextLedgerKey:
            raise _invalid()

        def operation() -> ContextLedgerInspection:
            with _ledger_directory(self._workspace, create=False) as directory:
                if directory is None:
                    return ContextLedgerInspection(
                        key,
                        0,
                        ContextCheckpointView(ContextCheckpointState.MISSING),
                        None,
                    )
                return _inspect_in_directory(directory, key)

        return self._translate(operation)

    def reserve_attempt(
        self,
        key: ContextLedgerKey,
        *,
        expected_generation: int,
        plan: ContextLedgerAttemptPlan,
    ) -> ContextLedgerInspection:
        """Persist INCOMPLETE and remove any older READY before provider work."""

        if type(key) is not ContextLedgerKey or (
            type(plan) is not ContextLedgerAttemptPlan
        ):
            raise _invalid()
        expected = _expected_generation(expected_generation)

        def operation() -> ContextLedgerInspection:
            with _writer_directory(self._workspace, key) as directory:
                current = _inspect_in_directory(directory, key)
                if (
                    current.record is not None
                    and current.record.state is ContextLedgerRecordState.INCOMPLETE
                    and current.record.attempt is not None
                    and current.record.attempt.plan == plan
                ):
                    return current
                if (
                    current.record is not None
                    and current.record.state
                    is ContextLedgerRecordState.INCOMPLETE
                ):
                    raise ContextLedgerError(
                        ContextLedgerErrorCode.INVALID_TRANSITION
                    )
                _require_generation(current, expected)
                now = self._now()
                attempt = ContextLedgerAttempt(
                    plan=plan,
                    provider_calls=(),
                    outcome=ContextLedgerAttemptOutcome.IN_PROGRESS,
                    started_at=now,
                    updated_at=now,
                )
                record = ContextLedgerRecord(
                    key=key,
                    generation=_next_generation(current.generation),
                    state=ContextLedgerRecordState.INCOMPLETE,
                    created_at=(
                        current.record.created_at
                        if current.record is not None
                        else now
                    ),
                    updated_at=now,
                    attempt=attempt,
                    ready_checkpoint=None,
                )
                _replace_record(directory, record)
                return _inspection_for_record(record)

        return self._translate(operation)

    def append_provider_call(
        self,
        key: ContextLedgerKey,
        *,
        expected_generation: int,
        attempt_id: str,
        provider_call: ContextLedgerProviderCall,
    ) -> ContextLedgerInspection:
        """Append one physical call exactly once and in one-based order."""

        if type(key) is not ContextLedgerKey or (
            type(provider_call) is not ContextLedgerProviderCall
        ):
            raise _invalid()
        _safe_identity(attempt_id)
        expected = _expected_generation(expected_generation)

        def operation() -> ContextLedgerInspection:
            with _writer_directory(self._workspace, key) as directory:
                current = _inspect_in_directory(directory, key)
                record = current.record
                if (
                    record is None
                    or record.state is not ContextLedgerRecordState.INCOMPLETE
                    or record.attempt is None
                    or record.attempt.plan.attempt_id != attempt_id
                ):
                    raise ContextLedgerError(
                        ContextLedgerErrorCode.INVALID_TRANSITION
                    )
                for existing in record.attempt.provider_calls:
                    if existing.call_id == provider_call.call_id:
                        if existing == provider_call:
                            return current
                        raise ContextLedgerError(ContextLedgerErrorCode.CONFLICT)
                _require_generation(current, expected)
                if (
                    len(record.attempt.provider_calls)
                    >= MAX_CONTEXT_LEDGER_PROVIDER_CALLS
                ):
                    raise ContextLedgerError(ContextLedgerErrorCode.LIMIT_EXCEEDED)
                if provider_call.ordinal != len(record.attempt.provider_calls) + 1:
                    raise ContextLedgerError(
                        ContextLedgerErrorCode.INVALID_TRANSITION
                    )
                now = self._now()
                attempt = replace(
                    record.attempt,
                    provider_calls=record.attempt.provider_calls + (provider_call,),
                    updated_at=now,
                )
                updated = replace(
                    record,
                    generation=_next_generation(record.generation),
                    updated_at=now,
                    attempt=attempt,
                )
                _replace_record(directory, updated)
                return _inspection_for_record(updated)

        return self._translate(operation)

    def taint_attempt(
        self,
        key: ContextLedgerKey,
        *,
        expected_generation: int,
        attempt_id: str,
        outcome: ContextLedgerAttemptOutcome,
    ) -> ContextLedgerInspection:
        """Terminalize one unsafe attempt without retaining exception prose."""

        if type(key) is not ContextLedgerKey or (
            type(outcome) is not ContextLedgerAttemptOutcome
        ):
            raise _invalid()
        _safe_identity(attempt_id)
        if outcome not in _UNSAFE_OUTCOMES:
            raise _invalid()
        expected = _expected_generation(expected_generation)

        def operation() -> ContextLedgerInspection:
            with _writer_directory(self._workspace, key) as directory:
                current = _inspect_in_directory(directory, key)
                record = current.record
                if (
                    record is not None
                    and record.state is ContextLedgerRecordState.TAINTED
                    and record.attempt is not None
                    and record.attempt.plan.attempt_id == attempt_id
                    and record.attempt.outcome is outcome
                ):
                    return current
                if (
                    record is None
                    or record.state is not ContextLedgerRecordState.INCOMPLETE
                    or record.attempt is None
                    or record.attempt.plan.attempt_id != attempt_id
                ):
                    raise ContextLedgerError(
                        ContextLedgerErrorCode.INVALID_TRANSITION
                    )
                _require_generation(current, expected)
                now = self._now()
                attempt = replace(
                    record.attempt,
                    outcome=outcome,
                    updated_at=now,
                    completed_at=now,
                )
                updated = replace(
                    record,
                    generation=_next_generation(record.generation),
                    state=ContextLedgerRecordState.TAINTED,
                    updated_at=now,
                    attempt=attempt,
                    ready_checkpoint=None,
                )
                _replace_record(directory, updated)
                return _inspection_for_record(updated)

        return self._translate(operation)

    def commit_attempt(
        self,
        key: ContextLedgerKey,
        *,
        expected_generation: int,
        authorization: ContextLedgerCommitAuthorization,
    ) -> ContextLedgerInspection:
        """Advance READY only after complete correlated verification evidence."""

        if type(key) is not ContextLedgerKey or (
            type(authorization) is not ContextLedgerCommitAuthorization
        ):
            raise _invalid()
        expected = _expected_generation(expected_generation)
        authorization_digest = _authorization_digest(authorization)

        def operation() -> ContextLedgerInspection:
            with _writer_directory(self._workspace, key) as directory:
                current = _inspect_in_directory(directory, key)
                record = current.record
                if (
                    record is not None
                    and record.state is ContextLedgerRecordState.READY
                    and record.attempt is not None
                    and record.attempt.plan.attempt_id == authorization.attempt_id
                    and record.attempt.commit_authorization_digest
                    == authorization_digest
                ):
                    return current
                if (
                    record is None
                    or record.state is not ContextLedgerRecordState.INCOMPLETE
                    or record.attempt is None
                    or record.attempt.plan.attempt_id != authorization.attempt_id
                ):
                    raise ContextLedgerError(
                        ContextLedgerErrorCode.INVALID_TRANSITION
                    )
                _require_generation(current, expected)
                if not _commit_is_authorized(key, record.attempt, authorization):
                    raise ContextLedgerError(
                        ContextLedgerErrorCode.COMMIT_NOT_AUTHORIZED
                    )
                now = self._now()
                proposal = authorization.proposal
                digest = context_checkpoint_proposal_digest(proposal)
                ready = ContextLedgerReadyCheckpoint(
                    conditions=proposal.conditions,
                    provider_thread_id=authorization.final_provider_thread_id,
                    sources=proposal.sources,
                    delta_depth=proposal.delta_depth,
                    proposal_digest=digest,
                    committed_at=now,
                    catalog_sources=proposal.catalog_sources,
                    source_alias_high_watermark=(
                        proposal.source_alias_high_watermark
                    ),
                )
                attempt = replace(
                    record.attempt,
                    outcome=ContextLedgerAttemptOutcome.COMMITTED,
                    updated_at=now,
                    completed_at=now,
                    commit_authorization_digest=authorization_digest,
                )
                updated = replace(
                    record,
                    generation=_next_generation(record.generation),
                    state=ContextLedgerRecordState.READY,
                    updated_at=now,
                    attempt=attempt,
                    ready_checkpoint=ready,
                )
                _replace_record(directory, updated)
                return _inspection_for_record(updated)

        return self._translate(operation)

    def invalidate(
        self,
        key: ContextLedgerKey,
        *,
        expected_generation: int,
    ) -> ContextLedgerInspection:
        """Persist explicit STALE state and remove any READY checkpoint."""

        if type(key) is not ContextLedgerKey:
            raise _invalid()
        expected = _expected_generation(expected_generation)

        def operation() -> ContextLedgerInspection:
            with _writer_directory(self._workspace, key) as directory:
                current = _inspect_in_directory(directory, key)
                record = current.record
                if (
                    record is not None
                    and record.state is ContextLedgerRecordState.STALE
                ):
                    return current
                _require_generation(current, expected)
                now = self._now()
                attempt = None if record is None else record.attempt
                if (
                    attempt is not None
                    and attempt.outcome is ContextLedgerAttemptOutcome.IN_PROGRESS
                ):
                    attempt = replace(
                        attempt,
                        outcome=ContextLedgerAttemptOutcome.INCOMPLETE,
                        updated_at=now,
                        completed_at=now,
                    )
                updated = ContextLedgerRecord(
                    key=key,
                    generation=_next_generation(current.generation),
                    state=ContextLedgerRecordState.STALE,
                    created_at=record.created_at if record is not None else now,
                    updated_at=now,
                    attempt=attempt,
                    ready_checkpoint=None,
                )
                _replace_record(directory, updated)
                return _inspection_for_record(updated)

        return self._translate(operation)
