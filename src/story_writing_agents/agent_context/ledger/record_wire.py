"""Record wire."""

from __future__ import annotations

import hashlib
import json
from ..contracts.planning_inputs import (
    ContextBootstrapReason,
    ContextDeliveryMode,
    ContextPlanningError,
)
from .accounting_wire import (
    ledger_call_accounting_value,
    ledger_decode_call_accounting,
    ledger_decode_projection_accounting,
    ledger_projection_accounting_value,
)
from .checkpoint_wire import (
    context_checkpoint_proposal_digest,
    ledger_catalog_cursor_value,
    ledger_checkpoint_source_value,
    ledger_conditions_value,
    ledger_decode_catalog_cursor,
    ledger_decode_checkpoint_source,
    ledger_decode_conditions,
    ledger_decode_scope,
    ledger_scope_value,
)
from .record_validation import (
    CONTEXT_LEDGER_SCHEMA_VERSION,
    ContextLedgerError,
    ContextLedgerErrorCode,
    LedgerDecodeError,
    MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES,
    MAX_CONTEXT_LEDGER_SOURCES,
    ledger_canonical_json,
    ledger_decoded_dict,
    ledger_decoded_enum,
    ledger_decoded_integer,
    ledger_decoded_list,
    ledger_decoded_optional_string,
    ledger_decoded_string,
)
from .record_values import (
    ContextLedgerAttempt,
    ContextLedgerAttemptOutcome,
    ContextLedgerAttemptPlan,
    ContextLedgerCommitAuthorization,
    ContextLedgerKey,
    ContextLedgerProviderCall,
    ContextLedgerReadyCheckpoint,
    ContextLedgerRecord,
    ContextLedgerRecordState,
    context_ledger_scope_key,
)
from ..manifest import ContextManifestError


def _key_value(key: ContextLedgerKey) -> dict[str, object]:
    return {
        "provider_id": key.provider_id,
        "scope": ledger_scope_value(key.scope),
        "scope_key": context_ledger_scope_key(key),
    }

def _decode_key(value: object) -> ContextLedgerKey:
    item = ledger_decoded_dict(value, frozenset({"scope_key", "scope", "provider_id"}))
    key = ContextLedgerKey(
        scope=ledger_decode_scope(item["scope"]),
        provider_id=ledger_decoded_string(item["provider_id"]),
    )
    if ledger_decoded_string(item["scope_key"]) != context_ledger_scope_key(key):
        raise LedgerDecodeError
    return key

def _provider_call_value(value: ContextLedgerProviderCall) -> dict[str, object]:
    return {
        "accounting": ledger_call_accounting_value(value.accounting),
        "call_id": value.call_id,
        "ordinal": value.ordinal,
    }

def _decode_provider_call(value: object) -> ContextLedgerProviderCall:
    item = ledger_decoded_dict(value, frozenset({"ordinal", "call_id", "accounting"}))
    return ContextLedgerProviderCall(
        ordinal=ledger_decoded_integer(item["ordinal"]),
        call_id=ledger_decoded_string(item["call_id"]),
        accounting=ledger_decode_call_accounting(item["accounting"]),
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
        "projection_accounting": ledger_projection_accounting_value(
            value.projection_accounting
        ),
        "proposal_digest": value.proposal_digest,
        "proposed_delta_depth": value.proposed_delta_depth,
        "reference_operation_id": value.reference_operation_id,
        "result_correlation_id": value.result_correlation_id,
    }

def _decode_attempt_plan(value: object) -> ContextLedgerAttemptPlan:
    item = ledger_decoded_dict(
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
        else ledger_decoded_enum(ContextBootstrapReason, item["bootstrap_reason"])
    )
    return ContextLedgerAttemptPlan(
        attempt_id=ledger_decoded_string(item["attempt_id"]),
        operation_id=ledger_decoded_string(item["operation_id"]),
        result_correlation_id=ledger_decoded_string(item["result_correlation_id"]),
        proposal_digest=ledger_decoded_string(item["proposal_digest"]),
        delivery_mode=ledger_decoded_enum(ContextDeliveryMode, item["delivery_mode"]),
        bootstrap_reason=reason,
        proposed_delta_depth=ledger_decoded_integer(item["proposed_delta_depth"]),
        mandatory_reference_count=ledger_decoded_integer(
            item["mandatory_reference_count"]
        ),
        planned_provider_thread_id=ledger_decoded_optional_string(
            item["planned_provider_thread_id"]
        ),
        reference_operation_id=ledger_decoded_optional_string(
            item["reference_operation_id"]
        ),
        projection_accounting=ledger_decode_projection_accounting(
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
    item = ledger_decoded_dict(
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
            for call in ledger_decoded_list(item["provider_calls"])
        ),
        outcome=ledger_decoded_enum(ContextLedgerAttemptOutcome, item["outcome"]),
        started_at=ledger_decoded_string(item["started_at"]),
        updated_at=ledger_decoded_string(item["updated_at"]),
        completed_at=ledger_decoded_optional_string(item["completed_at"]),
        commit_authorization_digest=ledger_decoded_optional_string(
            item["commit_authorization_digest"]
        ),
    )

def _ready_checkpoint_value(
    value: ContextLedgerReadyCheckpoint,
) -> dict[str, object]:
    return {
        **({"session_history_coverage": value.session_history_coverage}
           if value.session_history_coverage else {}),
        "catalog_sources": [
            ledger_catalog_cursor_value(source) for source in value.catalog_sources
        ],
        "committed_at": value.committed_at,
        "conditions": ledger_conditions_value(value.conditions),
        "delta_depth": value.delta_depth,
        "proposal_digest": value.proposal_digest,
        "provider_thread_id": value.provider_thread_id,
        "source_alias_high_watermark": value.source_alias_high_watermark,
        "sources": [
            ledger_checkpoint_source_value(source) for source in value.sources
        ],
    }

def _decode_ready_checkpoint(value: object) -> ContextLedgerReadyCheckpoint:
    coverage: tuple[tuple[str, int, str], ...] = ()
    if type(value) is dict and "session_history_coverage" in value:
        value = dict(value)
        raw_coverage = ledger_decoded_list(value.pop("session_history_coverage"))
        if len(raw_coverage) > MAX_CONTEXT_LEDGER_SOURCES:
            raise LedgerDecodeError
        decoded_coverage = []
        for raw in raw_coverage:
            row = ledger_decoded_list(raw)
            if len(row) != 3:
                raise LedgerDecodeError
            decoded_coverage.append((
                ledger_decoded_string(row[0]), ledger_decoded_integer(row[1]),
                ledger_decoded_string(row[2]),
            ))
        coverage = tuple(decoded_coverage)
    item = ledger_decoded_dict(
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
    sources = ledger_decoded_list(item["sources"])
    catalog_sources = ledger_decoded_list(item["catalog_sources"])
    if (
        len(sources) > MAX_CONTEXT_LEDGER_SOURCES
        or len(catalog_sources) > MAX_CONTEXT_LEDGER_SOURCES
    ):
        raise LedgerDecodeError
    return ContextLedgerReadyCheckpoint(
        conditions=ledger_decode_conditions(item["conditions"]),
        provider_thread_id=ledger_decoded_string(item["provider_thread_id"]),
        sources=tuple(ledger_decode_checkpoint_source(source) for source in sources),
        delta_depth=ledger_decoded_integer(item["delta_depth"]),
        proposal_digest=ledger_decoded_string(item["proposal_digest"]),
        committed_at=ledger_decoded_string(item["committed_at"]),
        catalog_sources=tuple(
            ledger_decode_catalog_cursor(source) for source in catalog_sources
        ),
        source_alias_high_watermark=ledger_decoded_integer(
            item["source_alias_high_watermark"]
        ),
        session_history_coverage=coverage,
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

def ledger_record_bytes(value: ContextLedgerRecord) -> bytes:
    payload = ledger_canonical_json(_record_value(value))
    if len(payload) > MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES:
        raise ContextLedgerError(ContextLedgerErrorCode.LIMIT_EXCEEDED)
    return payload

def _reject_constant(_value: str) -> object:
    raise LedgerDecodeError

def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise LedgerDecodeError
        result[key] = value
    return result

def ledger_decode_record(
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
        item = ledger_decoded_dict(
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
        if ledger_decoded_integer(item["schema_version"]) != CONTEXT_LEDGER_SCHEMA_VERSION:
            raise LedgerDecodeError
        key = _decode_key(item["key"])
        if key != expected_key:
            raise LedgerDecodeError
        attempt = None if item["attempt"] is None else _decode_attempt(item["attempt"])
        ready = (
            None
            if item["ready_checkpoint"] is None
            else _decode_ready_checkpoint(item["ready_checkpoint"])
        )
        candidate = ContextLedgerRecord(
            key=key,
            generation=ledger_decoded_integer(item["generation"]),
            state=ledger_decoded_enum(ContextLedgerRecordState, item["state"]),
            created_at=ledger_decoded_string(item["created_at"]),
            updated_at=ledger_decoded_string(item["updated_at"]),
            attempt=attempt,
            ready_checkpoint=ready,
            schema_version=ledger_decoded_integer(item["schema_version"]),
        )
        if ledger_record_bytes(candidate) != payload:
            raise LedgerDecodeError
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
        LedgerDecodeError,
    ):
        result = None
    return result

def ledger_authorization_digest(value: ContextLedgerCommitAuthorization) -> str:
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
    return f"sha256:{hashlib.sha256(ledger_canonical_json(payload)).hexdigest()}"
