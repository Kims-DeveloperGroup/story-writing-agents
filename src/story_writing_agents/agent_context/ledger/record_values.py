"""Record values."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from ..accounting import ProviderCallAccounting, ProviderCallState
from ..contracts.checkpoints import (
    ContextCatalogCursor,
    ContextCheckpointSource,
    ContextCheckpointView,
)
from ..contracts.planning_inputs import (
    ContextBootstrapReason,
    ContextCheckpointState,
    ContextDeliveryMode,
    ContextPlanningConditions,
)
from ..contracts.projection import ContextCheckpointProposal
from ..contracts.projection_accounting import ContextProjectionAccounting
from .checkpoint_wire import context_checkpoint_proposal_digest
from .record_validation import (
    CONTEXT_LEDGER_SCHEMA_VERSION,
    ContextLedgerError,
    ContextLedgerErrorCode,
    MAX_CONTEXT_LEDGER_PROVIDER_CALLS,
    MAX_CONTEXT_LEDGER_SOURCES,
    ledger_canonical_json,
    ledger_exact_nonnegative_integer,
    ledger_invalid_value,
    ledger_parse_timestamp,
    ledger_positive_generation,
    ledger_safe_digest,
    ledger_safe_identity,
)
from ..manifest import ContextScope, context_manifest_entry_key


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

@dataclass(frozen=True, slots=True)
class ContextLedgerKey:
    """Exact scope/provider identity for one independently stored ledger."""

    scope: ContextScope
    provider_id: str

    def __post_init__(self) -> None:
        if type(self.scope) is not ContextScope:
            raise ledger_invalid_value()
        ledger_safe_identity(self.provider_id)

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
        raise ledger_invalid_value()
    digest = hashlib.sha256(ledger_canonical_json(_scope_key_value(key))).hexdigest()
    return f"sha256:{digest}"

@dataclass(frozen=True, slots=True)
class ContextLedgerProviderCall:
    """One ordered physical provider-call accounting record."""

    ordinal: int
    call_id: str
    accounting: ProviderCallAccounting

    def __post_init__(self) -> None:
        ordinal = ledger_exact_nonnegative_integer(self.ordinal)
        if ordinal == 0:
            raise ledger_invalid_value()
        ledger_safe_identity(self.call_id)
        if type(self.accounting) is not ProviderCallAccounting:
            raise ledger_invalid_value()

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
        ledger_safe_identity(self.attempt_id)
        ledger_safe_identity(self.operation_id)
        ledger_safe_identity(self.result_correlation_id)
        ledger_safe_digest(self.proposal_digest)
        if type(self.delivery_mode) is not ContextDeliveryMode:
            raise ledger_invalid_value()
        if self.bootstrap_reason is not None and (
            type(self.bootstrap_reason) is not ContextBootstrapReason
        ):
            raise ledger_invalid_value()
        depth = ledger_exact_nonnegative_integer(self.proposed_delta_depth)
        reference_count = ledger_exact_nonnegative_integer(
            self.mandatory_reference_count
        )
        if self.planned_provider_thread_id is not None:
            ledger_safe_identity(self.planned_provider_thread_id)
        if self.reference_operation_id is not None:
            ledger_safe_identity(self.reference_operation_id)
        if type(self.projection_accounting) is not ContextProjectionAccounting:
            raise ledger_invalid_value()
        accounting = self.projection_accounting
        if (
            accounting.mode is not self.delivery_mode
            or accounting.bootstrap_reason is not self.bootstrap_reason
            or accounting.proposed_delta_depth != depth
            or accounting.mandatory_reference_count != reference_count
        ):
            raise ledger_invalid_value()
        if self.delivery_mode is ContextDeliveryMode.BOOTSTRAP:
            if self.bootstrap_reason is None or depth != 0:
                raise ledger_invalid_value()
        elif (
            self.bootstrap_reason is not None
            or depth == 0
            or self.planned_provider_thread_id is None
        ):
            raise ledger_invalid_value()
        if (reference_count == 0) is not (
            self.reference_operation_id is None
        ):
            raise ledger_invalid_value()

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
            raise ledger_invalid_value()
        if type(self.provider_calls) is not tuple or any(
            type(call) is not ContextLedgerProviderCall
            for call in self.provider_calls
        ):
            raise ledger_invalid_value()
        if len(self.provider_calls) > MAX_CONTEXT_LEDGER_PROVIDER_CALLS:
            raise ContextLedgerError(ContextLedgerErrorCode.LIMIT_EXCEEDED)
        if tuple(call.ordinal for call in self.provider_calls) != tuple(
            range(1, len(self.provider_calls) + 1)
        ):
            raise ledger_invalid_value()
        call_ids = tuple(call.call_id for call in self.provider_calls)
        if len(set(call_ids)) != len(call_ids):
            raise ledger_invalid_value()
        if type(self.outcome) is not ContextLedgerAttemptOutcome:
            raise ledger_invalid_value()
        started = ledger_parse_timestamp(self.started_at)
        updated = ledger_parse_timestamp(self.updated_at)
        if updated < started:
            raise ledger_invalid_value()
        if self.outcome is ContextLedgerAttemptOutcome.IN_PROGRESS:
            if (
                self.completed_at is not None
                or self.commit_authorization_digest is not None
            ):
                raise ledger_invalid_value()
        else:
            if self.completed_at is None:
                raise ledger_invalid_value()
            completed = ledger_parse_timestamp(self.completed_at)
            if completed < updated:
                raise ledger_invalid_value()
            if self.outcome is ContextLedgerAttemptOutcome.COMMITTED:
                ledger_safe_digest(self.commit_authorization_digest)
                if (
                    not self.provider_calls
                    or self.provider_calls[-1].accounting.state
                    is not ProviderCallState.SUBMITTED
                ):
                    raise ledger_invalid_value()
            elif self.commit_authorization_digest is not None:
                raise ledger_invalid_value()

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
    session_history_coverage: tuple[tuple[str, int, str], ...] = ()

    def __post_init__(self) -> None:
        if type(self.conditions) is not ContextPlanningConditions:
            raise ledger_invalid_value()
        ledger_safe_identity(self.provider_thread_id)
        if type(self.sources) is not tuple or any(
            type(source) is not ContextCheckpointSource for source in self.sources
        ):
            raise ledger_invalid_value()
        if len(self.sources) > MAX_CONTEXT_LEDGER_SOURCES:
            raise ContextLedgerError(ContextLedgerErrorCode.LIMIT_EXCEEDED)
        if type(self.catalog_sources) is not tuple or any(
            type(source) is not ContextCatalogCursor
            for source in self.catalog_sources
        ):
            raise ledger_invalid_value()
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
            raise ledger_invalid_value()
        ordered_catalog = tuple(
            sorted(
                self.catalog_sources,
                key=lambda source: context_manifest_entry_key(source.entry),
            )
        )
        ledger_exact_nonnegative_integer(self.delta_depth)
        ledger_safe_digest(self.proposal_digest)
        ledger_parse_timestamp(self.committed_at)
        object.__setattr__(self, "sources", ordered)
        object.__setattr__(self, "catalog_sources", ordered_catalog)
        proposal = ContextCheckpointProposal(
            self.conditions,
            ordered,
            self.delta_depth,
            ordered_catalog,
            self.source_alias_high_watermark,
            self.session_history_coverage,
        )
        if context_checkpoint_proposal_digest(proposal) != self.proposal_digest:
            raise ledger_invalid_value()

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
            raise ledger_invalid_value()
        ledger_positive_generation(self.generation)
        if type(self.state) is not ContextLedgerRecordState:
            raise ledger_invalid_value()
        created = ledger_parse_timestamp(self.created_at)
        updated = ledger_parse_timestamp(self.updated_at)
        if updated < created:
            raise ledger_invalid_value()
        if self.attempt is not None and type(self.attempt) is not ContextLedgerAttempt:
            raise ledger_invalid_value()
        if self.ready_checkpoint is not None and (
            type(self.ready_checkpoint) is not ContextLedgerReadyCheckpoint
        ):
            raise ledger_invalid_value()
        if self.schema_version != CONTEXT_LEDGER_SCHEMA_VERSION or (
            type(self.schema_version) is not int
        ):
            raise ledger_invalid_value()
        if self.attempt is not None and self.attempt.updated_at > self.updated_at:
            raise ledger_invalid_value()
        if self.state is ContextLedgerRecordState.INCOMPLETE:
            if (
                self.attempt is None
                or self.attempt.outcome is not ContextLedgerAttemptOutcome.IN_PROGRESS
                or self.ready_checkpoint is not None
            ):
                raise ledger_invalid_value()
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
                raise ledger_invalid_value()
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
                raise ledger_invalid_value()
        else:
            if self.ready_checkpoint is not None or (
                self.attempt is not None
                and self.attempt.outcome is ContextLedgerAttemptOutcome.IN_PROGRESS
            ):
                raise ledger_invalid_value()

@dataclass(frozen=True, slots=True)
class ContextLedgerInspection:
    """Mutation-free ledger observation and exact planner checkpoint view."""

    key: ContextLedgerKey
    generation: int
    checkpoint_view: ContextCheckpointView
    record: ContextLedgerRecord | None

    def __post_init__(self) -> None:
        if type(self.key) is not ContextLedgerKey:
            raise ledger_invalid_value()
        generation = ledger_exact_nonnegative_integer(self.generation)
        if type(self.checkpoint_view) is not ContextCheckpointView:
            raise ledger_invalid_value()
        if self.record is None:
            if generation != 0 or self.checkpoint_view.state not in {
                ContextCheckpointState.MISSING,
                ContextCheckpointState.CORRUPT,
            }:
                raise ledger_invalid_value()
        elif (
            type(self.record) is not ContextLedgerRecord
            or self.record.key != self.key
            or generation != self.record.generation
        ):
            raise ledger_invalid_value()
        else:
            expected_state = {
                ContextLedgerRecordState.INCOMPLETE: ContextCheckpointState.TAINTED,
                ContextLedgerRecordState.TAINTED: ContextCheckpointState.TAINTED,
                ContextLedgerRecordState.STALE: ContextCheckpointState.STALE,
                ContextLedgerRecordState.READY: ContextCheckpointState.READY,
            }[self.record.state]
            if self.checkpoint_view.state is not expected_state:
                raise ledger_invalid_value()
            if self.record.state is ContextLedgerRecordState.READY:
                ready = self.record.ready_checkpoint
                assert ready is not None
                if (
                    self.checkpoint_view.conditions != ready.conditions
                    or self.checkpoint_view.provider_thread_id
                    != ready.provider_thread_id
                    or self.checkpoint_view.sources != ready.sources
                    or self.checkpoint_view.delta_depth != ready.delta_depth
                    or self.checkpoint_view.session_history_coverage
                    != ready.session_history_coverage
                ):
                    raise ledger_invalid_value()

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
        ledger_safe_identity(self.attempt_id)
        ledger_safe_identity(self.operation_id)
        ledger_safe_identity(self.result_correlation_id)
        if type(self.proposal) is not ContextCheckpointProposal:
            raise ledger_invalid_value()
        ledger_safe_digest(self.revalidated_proposal_digest)
        ledger_safe_identity(self.terminal_call_id)
        ledger_safe_identity(self.final_provider_thread_id)
        if self.completed_reference_operation_id is not None:
            ledger_safe_identity(self.completed_reference_operation_id)
        ledger_safe_identity(self.output_action_authorization_id)

UNSAFE_LEDGER_OUTCOMES = frozenset(
    outcome
    for outcome in ContextLedgerAttemptOutcome
    if outcome
    not in {
        ContextLedgerAttemptOutcome.IN_PROGRESS,
        ContextLedgerAttemptOutcome.COMMITTED,
    }
)
