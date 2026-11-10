"""Checkpoint ledger."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from ..accounting import ProviderCallKind, ProviderCallState
from ..contracts.checkpoints import ContextCheckpointView
from ..contracts.planning_inputs import ContextCheckpointState, ContextDeliveryMode
from .atomic_files import (
    LedgerStorageFault,
    ledger_directory,
    ledger_read_payload,
    ledger_record_name,
    ledger_replace_record,
    ledger_writer_directory,
)
from .checkpoint_wire import context_checkpoint_proposal_digest
from .record_validation import (
    ContextLedgerError,
    ContextLedgerErrorCode,
    MAX_CONTEXT_LEDGER_PROVIDER_CALLS,
    MAX_CONTEXT_LEDGER_SOURCES,
    MAX_LEDGER_GENERATION,
    ledger_exact_nonnegative_integer,
    ledger_format_timestamp,
    ledger_invalid_value,
    ledger_safe_identity,
)
from .record_values import (
    ContextLedgerAttempt,
    ContextLedgerAttemptOutcome,
    ContextLedgerAttemptPlan,
    ContextLedgerCommitAuthorization,
    ContextLedgerInspection,
    ContextLedgerKey,
    ContextLedgerProviderCall,
    ContextLedgerReadyCheckpoint,
    ContextLedgerRecord,
    ContextLedgerRecordState,
    UNSAFE_LEDGER_OUTCOMES,
)
from .record_wire import ledger_authorization_digest, ledger_decode_record
from ...workspace import Workspace
from typing import TypeVar


_ResultT = TypeVar("_ResultT")

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
            session_history_coverage=ready.session_history_coverage,
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
    found, payload = ledger_read_payload(directory, ledger_record_name(key))
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
    record = ledger_decode_record(payload, key)
    if record is None:
        return ContextLedgerInspection(
            key,
            0,
            ContextCheckpointView(ContextCheckpointState.CORRUPT),
            None,
        )
    return _inspection_for_record(record)

def _expected_generation(value: object) -> int:
    generation = ledger_exact_nonnegative_integer(value)
    if generation > MAX_LEDGER_GENERATION:
        raise ledger_invalid_value()
    return generation

def _next_generation(value: int) -> int:
    if value >= MAX_LEDGER_GENERATION:
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
            raise ledger_invalid_value()
        if clock is not None and not callable(clock):
            raise ledger_invalid_value()
        self._workspace = workspace
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)

    def _now(self) -> str:
        value: datetime | None
        try:
            value = self._clock()
        except Exception:
            value = None
        if value is None:
            raise ledger_invalid_value()
        return ledger_format_timestamp(value)

    @staticmethod
    def _translate(operation: Callable[[], _ResultT]) -> _ResultT:
        code: ContextLedgerErrorCode | None = None
        result: _ResultT | None = None
        try:
            result = operation()
        except ContextLedgerError:
            raise
        except LedgerStorageFault as failure:
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
            raise ledger_invalid_value()

        def operation() -> ContextLedgerInspection:
            with ledger_directory(self._workspace, create=False) as directory:
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
            raise ledger_invalid_value()
        expected = _expected_generation(expected_generation)

        def operation() -> ContextLedgerInspection:
            with ledger_writer_directory(self._workspace, key) as directory:
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
                ledger_replace_record(directory, record)
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
            raise ledger_invalid_value()
        ledger_safe_identity(attempt_id)
        expected = _expected_generation(expected_generation)

        def operation() -> ContextLedgerInspection:
            with ledger_writer_directory(self._workspace, key) as directory:
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
                ledger_replace_record(directory, updated)
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
            raise ledger_invalid_value()
        ledger_safe_identity(attempt_id)
        if outcome not in UNSAFE_LEDGER_OUTCOMES:
            raise ledger_invalid_value()
        expected = _expected_generation(expected_generation)

        def operation() -> ContextLedgerInspection:
            with ledger_writer_directory(self._workspace, key) as directory:
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
                ledger_replace_record(directory, updated)
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
            raise ledger_invalid_value()
        expected = _expected_generation(expected_generation)
        authorization_digest = ledger_authorization_digest(authorization)

        def operation() -> ContextLedgerInspection:
            with ledger_writer_directory(self._workspace, key) as directory:
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
                    session_history_coverage=proposal.session_history_coverage,
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
                ledger_replace_record(directory, updated)
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
            raise ledger_invalid_value()
        expected = _expected_generation(expected_generation)

        def operation() -> ContextLedgerInspection:
            with ledger_writer_directory(self._workspace, key) as directory:
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
                ledger_replace_record(directory, updated)
                return _inspection_for_record(updated)

        return self._translate(operation)
