"""Accounting wire."""

from __future__ import annotations

from ..accounting import (
    ContextComponentBytes,
    ContextComponentKind,
    ProviderCallAccounting,
    ProviderCallKind,
    ProviderPayloadAccounting,
    ProviderUsage,
    provider_call_accounting_from_object,
    provider_call_accounting_to_object,
)
from ..contracts.planning_inputs import (
    ContextBootstrapReason,
    ContextDecisionKind,
    ContextDeliveryMode,
)
from ..contracts.projection_accounting import (
    ContextBudgetAccounting,
    ContextDecisionAccounting,
    ContextProjectionAccounting,
    ContextReferenceBudgetUsage,
    ContextSearchableAccounting,
    ContextSourceBudgetUsage,
)
from .record_validation import (
    ledger_decoded_dict,
    ledger_decoded_enum,
    ledger_decoded_integer,
    ledger_decoded_list,
    ledger_decoded_optional_integer,
    ledger_decoded_optional_string,
)
from ..manifest import ContextPriority, ContextSemanticChannel


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
    item = ledger_decoded_dict(
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
        kind=ledger_decoded_enum(ContextComponentKind, item["kind"]),
        serialized_utf8_bytes=ledger_decoded_integer(item["serialized_utf8_bytes"]),
        raw_mandatory_reference_utf8_bytes=ledger_decoded_integer(
            item["raw_mandatory_reference_utf8_bytes"]
        ),
        identity=ledger_decoded_optional_string(item["identity"]),
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
    item = ledger_decoded_dict(
        value,
        frozenset(
            {"call_kind", "schema_utf8_bytes", "payload_utf8_bytes", "components"}
        ),
    )
    components = tuple(
        _decode_component(component)
        for component in ledger_decoded_list(item["components"])
    )
    return ProviderPayloadAccounting(
        call_kind=ledger_decoded_enum(ProviderCallKind, item["call_kind"]),
        schema_utf8_bytes=ledger_decoded_integer(item["schema_utf8_bytes"]),
        payload_utf8_bytes=ledger_decoded_integer(item["payload_utf8_bytes"]),
        components=components,
    )

def _usage_value(value: ProviderUsage) -> dict[str, object]:
    return {
        "cached_input_tokens": value.cached_input_tokens,
        "input_tokens": value.input_tokens,
        "output_tokens": value.output_tokens,
    }

def _decode_usage(value: object) -> ProviderUsage:
    item = ledger_decoded_dict(
        value,
        frozenset({"input_tokens", "output_tokens", "cached_input_tokens"}),
    )
    return ProviderUsage(
        input_tokens=ledger_decoded_optional_integer(item["input_tokens"]),
        output_tokens=ledger_decoded_optional_integer(item["output_tokens"]),
        cached_input_tokens=ledger_decoded_optional_integer(
            item["cached_input_tokens"]
        ),
    )

def ledger_call_accounting_value(value: ProviderCallAccounting) -> dict[str, object]:
    return provider_call_accounting_to_object(value)

def ledger_decode_call_accounting(value: object) -> ProviderCallAccounting:
    return provider_call_accounting_from_object(value)

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
    item = ledger_decoded_dict(
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
        priority=ledger_decoded_enum(ContextPriority, item["priority"]),
        semantic_channel=ledger_decoded_enum(
            ContextSemanticChannel,
            item["semantic_channel"],
        ),
        decision=ledger_decoded_enum(ContextDecisionKind, item["decision"]),
        source_count=ledger_decoded_integer(item["source_count"]),
        raw_source_utf8_bytes=ledger_decoded_integer(item["raw_source_utf8_bytes"]),
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
    item = ledger_decoded_dict(value, frozenset(_SEARCHABLE_FIELDS))
    return ContextSearchableAccounting(
        **{name: ledger_decoded_integer(item[name]) for name in _SEARCHABLE_FIELDS}
    )

def _source_budget_value(value: ContextSourceBudgetUsage) -> dict[str, object]:
    return {
        "configured_utf8_bytes": value.configured_utf8_bytes,
        "remaining_utf8_bytes": value.remaining_utf8_bytes,
        "selected_unique_utf8_bytes": value.selected_unique_utf8_bytes,
    }

def _decode_source_budget(value: object) -> ContextSourceBudgetUsage:
    item = ledger_decoded_dict(
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
        configured_utf8_bytes=ledger_decoded_integer(item["configured_utf8_bytes"]),
        selected_unique_utf8_bytes=ledger_decoded_integer(
            item["selected_unique_utf8_bytes"]
        ),
        remaining_utf8_bytes=ledger_decoded_integer(item["remaining_utf8_bytes"]),
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
    item = ledger_decoded_dict(
        value,
        frozenset({"configured_utf8_bytes", "used_utf8_bytes", "remaining_utf8_bytes"}),
    )
    return ContextReferenceBudgetUsage(
        configured_utf8_bytes=ledger_decoded_integer(item["configured_utf8_bytes"]),
        used_utf8_bytes=ledger_decoded_integer(item["used_utf8_bytes"]),
        remaining_utf8_bytes=ledger_decoded_integer(item["remaining_utf8_bytes"]),
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
    item = ledger_decoded_dict(
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

def ledger_projection_accounting_value(
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

def ledger_decode_projection_accounting(value: object) -> ContextProjectionAccounting:
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
    item = ledger_decoded_dict(value, fields)
    reason = (
        None
        if item["bootstrap_reason"] is None
        else ledger_decoded_enum(ContextBootstrapReason, item["bootstrap_reason"])
    )
    integers = {
        name: ledger_decoded_integer(item[name]) for name in _PROJECTION_INTEGER_FIELDS
    }
    return ContextProjectionAccounting(
        mode=ledger_decoded_enum(ContextDeliveryMode, item["mode"]),
        bootstrap_reason=reason,
        searchable=_decode_searchable(item["searchable"]),
        budgets=_decode_budget_accounting(item["budgets"]),
        decision_buckets=tuple(
            _decode_decision_accounting(bucket)
            for bucket in ledger_decoded_list(item["decision_buckets"])
        ),
        **integers,
    )
