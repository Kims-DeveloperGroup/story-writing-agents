"""Ledger-backed immutable delivery and schema-group invocation facts."""

from __future__ import annotations

import hashlib
import json
from ...lead_provider_contracts import LeadGroupCompilation
from ...lead_schema_groups import (
    LeadGroupPhase,
    LeadGroupRequestBasis,
    LeadGroupWorkflow,
)
from ...provider_adapter import ProviderContextWitness
from ...provider_schema_selection import SchemaSelectionInput
from ..accounting import ProviderCallState
from ..contracts.context_values import (
    digest,
    safe_identity,
)
from ..contracts.errors import (
    ContextExecutionErrorCode,
    execution_error,
)
from ..contracts.planning_inputs import ContextPlanningConditions
from ..ledger.record_values import (
    ContextLedgerAttemptOutcome,
    ContextLedgerRecord,
    ContextLedgerRecordState,
)
from ..manifest import (
    ContextPriority,
    ContextSemanticChannel,
)
from dataclasses import (
    dataclass,
    field,
)


@dataclass(frozen=True, slots=True)
class ContextSourceClassification:
    """Content-free priority/channel assignment for one inventoried source."""

    source_id: str
    semantic_channel: ContextSemanticChannel
    priority: ContextPriority
    ordinal: int
    allow_absent: bool = False

    def __post_init__(self) -> None:
        safe_identity(self.source_id)
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
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)


@dataclass(frozen=True, slots=True)
class ContextDeliverySnapshot:
    """실제 한 입력의 논리 내용과 전송 근거를 보존하며 업무 승인은 소유하지 않는다."""

    request_basis: LeadGroupRequestBasis
    conditions: ContextPlanningConditions
    attempt_id: str
    ledger_generation: int
    call_id: str
    logical_context: bytes = field(repr=False)
    payload_json: bytes = field(repr=False)
    payload_sha256: str
    schema_sha256: str
    ledger_record: ContextLedgerRecord = field(repr=False)
    witness: ProviderContextWitness | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (type(self.request_basis) is not LeadGroupRequestBasis
                or type(self.conditions) is not ContextPlanningConditions
                or type(self.logical_context) is not bytes
                or type(json.loads(self.logical_context)) is not dict
                or type(self.ledger_generation) is not int or self.ledger_generation < 0
                or self.witness is not None and type(self.witness) is not ProviderContextWitness):
            raise ValueError("context delivery snapshot is invalid")
        record = self.ledger_record
        if (type(record) is not ContextLedgerRecord or record.generation != self.ledger_generation
                or record.key.scope != self.conditions.scope or record.key.provider_id != self.conditions.provider_id
                or record.state is not ContextLedgerRecordState.INCOMPLETE or record.ready_checkpoint is not None
                or record.attempt is None or record.attempt.outcome is not ContextLedgerAttemptOutcome.IN_PROGRESS or record.attempt.plan.attempt_id != self.attempt_id
                or record.attempt.plan.operation_id != self.request_basis.operation_id
                or record.attempt.plan.result_correlation_id != self.request_basis.result_correlation_id
                or not record.attempt.provider_calls or record.attempt.provider_calls[-1].call_id != self.call_id
                or record.attempt.provider_calls[-1].accounting.state is not ProviderCallState.SUBMITTED):
            raise ValueError("delivery snapshot does not match its actual ledger append")
        safe_identity(self.attempt_id)
        safe_identity(self.call_id)
        for digest in (self.payload_sha256, self.schema_sha256):
            if type(digest) is not str or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("context delivery snapshot digest is invalid")
        if (type(self.payload_json) is not bytes
                or hashlib.sha256(self.payload_json).hexdigest() != self.payload_sha256):
            raise ValueError("delivery snapshot payload is not the actual encoded input")
        selection = SchemaSelectionInput.from_value(json.loads(self.payload_json)["response_contract"]["schema_selection"])
        if selection.context_ref != self.context_ref or selection.call_id != self.call_id:
            raise ValueError("delivery snapshot logical input is not bound to the physical input")
        if self.witness is not None and (self.witness.call_id != self.call_id
                or self.witness.payload_sha256 != self.payload_sha256):
            raise ValueError("context delivery snapshot belongs to another physical input")

    def source_valid(self) -> bool:
        try:
            self.__post_init__()
            return True
        except (ValueError, TypeError, KeyError, AttributeError):
            return False

    def matches_ledger(self, record: ContextLedgerRecord | None, *, latest: bool = False) -> bool:
        """저장된 실제 append와 현재 authoritative 이력의 허용된 prefix만 비교한다."""
        if not self.source_valid():
            return False
        original = self.ledger_record
        if (type(record) is not ContextLedgerRecord or record.key != original.key
                or record.generation < original.generation or record.created_at != original.created_at
                or record.state not in {ContextLedgerRecordState.INCOMPLETE, ContextLedgerRecordState.TAINTED}
                or record.ready_checkpoint != original.ready_checkpoint or record.attempt is None
                or record.attempt.plan != original.attempt.plan
                or record.attempt.outcome not in {ContextLedgerAttemptOutcome.IN_PROGRESS,
                    ContextLedgerAttemptOutcome.MALFORMED, ContextLedgerAttemptOutcome.OUTPUT_INVALID}):
            return False
        calls = original.attempt.provider_calls
        return (record.attempt.provider_calls[:len(calls)] == calls
                and (not latest or len(record.attempt.provider_calls) == len(calls)))

    @property
    def context_ref(self) -> str:
        return "ctx_" + hashlib.sha256(self.logical_context).hexdigest()


@dataclass(frozen=True, slots=True)
class ContextGroupInvocationState:
    """한 논리 호출에서 선택 이력과 현재 준비 계획을 전달한다."""

    request_basis: LeadGroupRequestBasis
    workflow: LeadGroupWorkflow | None = None
    prepared_compilation: LeadGroupCompilation | None = field(default=None, repr=False)
    transport_delivery: ContextDeliverySnapshot | None = field(default=None, repr=False)
    accepted_delivery: ContextDeliverySnapshot | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if any(value is not None and type(value) is not ContextDeliverySnapshot for value in (self.transport_delivery, self.accepted_delivery)):
            raise TypeError("group delivery snapshot has an invalid type")
        if (type(self.request_basis) is not LeadGroupRequestBasis
                or (self.workflow is not None and type(self.workflow) is not LeadGroupWorkflow)
                or (self.prepared_compilation is not None
                    and type(self.prepared_compilation) is not LeadGroupCompilation)
                or ((self.workflow is None) != (self.prepared_compilation is None))):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.workflow is not None:
            self.workflow.catalog.require_snapshot()
            self.prepared_compilation.require_provenance()


def group_native_deliveries(
    state: ContextGroupInvocationState | None, conditions: ContextPlanningConditions,
    provider_thread_id: str | None,
) -> tuple[ContextDeliverySnapshot, ...]:
    """같은 요청·조건과 실제 native 계보를 가진 수락·전송 쌍만 반환한다."""
    if (state is None or state.workflow is None
            or state.workflow.phase not in {LeadGroupPhase.SELECT, LeadGroupPhase.GENERATE}
            or state.accepted_delivery is None or state.transport_delivery is None):
        return ()
    accepted, transport = state.accepted_delivery, state.transport_delivery
    for snapshot in (accepted, transport):
        if (snapshot.witness is None or snapshot.request_basis != state.request_basis
                or snapshot.conditions != conditions or not snapshot.source_valid()):
            return ()
    anchor, witness = accepted.witness, transport.witness
    if witness.thread_id != provider_thread_id or not witness.has_lineage_anchor(anchor):
        return ()
    return accepted, transport


def verified_group_deliveries(
    state: ContextGroupInvocationState | None, conditions: ContextPlanningConditions,
    record: ContextLedgerRecord | None, provider_thread_id: str | None, *,
    incoming: tuple[ContextDeliverySnapshot, ...] = (),
) -> tuple[ContextDeliverySnapshot, ...]:
    """실제 원장 prefix/latest 또는 reserve 전에 검증한 같은 쌍만 인계한다."""
    deliveries = group_native_deliveries(state, conditions, provider_thread_id)
    if (not deliveries or type(record) is not ContextLedgerRecord or record.attempt is None
            or record.key.scope != conditions.scope or record.key.provider_id != conditions.provider_id
            or record.state not in {ContextLedgerRecordState.INCOMPLETE, ContextLedgerRecordState.TAINTED}
            or record.ready_checkpoint is not None):
        return ()
    for snapshot, latest in zip(deliveries, (False, True), strict=True):
        if snapshot.matches_ledger(record, latest=latest):
            continue
        original = snapshot.ledger_record
        if (not any(snapshot is item for item in incoming)
                or record.key != original.key or record.created_at != original.created_at
                or record.generation < original.generation
                or latest and record.attempt.provider_calls):
            return ()
    return deliveries


__all__ = [
    'ContextSourceClassification',
    'ContextDeliverySnapshot',
    'ContextGroupInvocationState',
    'group_native_deliveries',
    'verified_group_deliveries',
]
