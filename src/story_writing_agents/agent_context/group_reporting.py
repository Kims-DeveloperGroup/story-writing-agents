"""Read-only Lead group measurements; no compilation, calls, storage, or authority."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from ..provider_adapter import ProviderInvocation
from .accounting import (
    ProviderCallAccounting, ProviderCallState, ProviderUsageObservation,
    canonical_provider_json, normalized_provider_usage, provider_schema_statistics,
)
from .group_observation import LeadGroupObservationKind, LeadGroupTrace


@dataclass(frozen=True, slots=True)
class LeadGroupRequestMeasurement:
    """A final native preparation plus its explicit, frozen comparison conditions.

    The composing owner supplies the same original request/context/profile basis
    for every alternative. This object does not claim that a hash proves source
    authenticity, native submission, or provider acceptance.
    """

    variant: str
    comparison_basis: bytes = field(repr=False)
    invocation: ProviderInvocation = field(repr=False)
    group_id: str | None = None
    _schema_json: bytes = field(init=False, repr=False)
    _payload_json: bytes = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.variant not in {"full", "select", "generate"}:
            raise ValueError("unknown group measurement variant")
        if (self.variant == "generate") != (type(self.group_id) is str and bool(self.group_id)):
            raise ValueError("only GENERATE measurements identify a group")
        if type(self.comparison_basis) is not bytes or type(json.loads(self.comparison_basis)) is not dict:
            raise ValueError("comparison conditions must be a frozen object")
        if canonical_provider_json(json.loads(self.comparison_basis)) != self.comparison_basis:
            raise ValueError("comparison conditions must be canonical")
        if type(self.invocation) is not ProviderInvocation or self.invocation.call_accounting is None:
            raise ValueError("measurement requires an actual final native preparation")
        schema_json = canonical_provider_json(self.invocation.schema)
        payload_json = self.invocation.payload_json
        accounting = self.invocation.call_accounting.payload
        if payload_json is None or len(schema_json) != accounting.schema_utf8_bytes or len(payload_json) != accounting.payload_utf8_bytes:
            raise ValueError("native measurement differs from prepared accounting")
        object.__setattr__(self, "_schema_json", schema_json)
        object.__setattr__(self, "_payload_json", payload_json)


def group_request_size_report(
    samples: tuple[LeadGroupRequestMeasurement, ...],
    *, group_ids: tuple[str, ...],
) -> dict[str, object]:
    """Compare full/SELECT/every offered GENERATE, including the largest request."""

    if type(samples) is not tuple or not samples or any(type(item) is not LeadGroupRequestMeasurement for item in samples):
        raise ValueError("native measurements must be an immutable nonempty tuple")
    if type(group_ids) is not tuple or not group_ids or len(set(group_ids)) != len(group_ids):
        raise ValueError("measurement requires the actual offered group inventory")
    if any(item.comparison_basis != samples[0].comparison_basis for item in samples):
        raise ValueError("measurements have different request/context/profile conditions")
    if sum(item.variant == "full" for item in samples) != 1 or sum(item.variant == "select" for item in samples) != 1:
        raise ValueError("comparison requires exactly one full and one SELECT preparation")
    if sorted(item.group_id for item in samples if item.variant == "generate") != sorted(group_ids):
        raise ValueError("comparison must include every offered GENERATE exactly once")
    rows = []
    for sample in samples:
        accounting = sample.invocation.call_accounting
        assert accounting is not None
        schema = json.loads(sample._schema_json)
        payload = json.loads(sample._payload_json)
        shape = provider_schema_statistics(schema)
        response_contract = payload.get("response_contract", {})
        selection = response_contract.get("schema_selection", {}) if type(response_contract) is dict else {}
        catalog = selection.get("catalog") if type(selection) is dict else None
        links = {name: selection[name] for name in ("call_id", "contracts", "active_selection", "catalog_id")
                 if type(selection) is dict and name in selection}
        rows.append({
            "variant": sample.variant, "group_id": sample.group_id,
            "schema_utf8_bytes": accounting.payload.schema_utf8_bytes,
            "catalog_utf8_bytes": None if catalog is None else len(canonical_provider_json(catalog)),
            "schema_selection_data_utf8_bytes": len(canonical_provider_json(selection)) if selection else None,
            "outer_contract_linkage_utf8_bytes": len(canonical_provider_json(links)) if links else None,
            "complete_select_schema_utf8_bytes": accounting.payload.schema_utf8_bytes if sample.variant == "select" else None,
            "payload_utf8_bytes": accounting.payload.payload_utf8_bytes,
            "binding_utf8_bytes": accounting.binding_instruction_utf8_bytes,
            "prepared_model_input_utf8_bytes": accounting.prepared_model_input_utf8_bytes,
            "outer_alternatives": shape.outer_alternatives,
            "any_of_paths": list(shape.any_of_paths), "definition_count": shape.definition_count,
            "enum_value_count": shape.enum_value_count,
            "schema_sha256": hashlib.sha256(sample._schema_json).hexdigest(),
            "payload_sha256": hashlib.sha256(sample._payload_json).hexdigest(),
            "measurement_stage": "native_prepared", "submitted": False,
        })
    groups = [row for row in rows if row["variant"] == "generate"]
    return {
        "comparison_basis_sha256": hashlib.sha256(samples[0].comparison_basis).hexdigest(),
        "measurements": rows,
        "largest_generate_schema": max(groups, key=lambda row: row["schema_utf8_bytes"]),
        "largest_generate_request": max(groups, key=lambda row: row["prepared_model_input_utf8_bytes"]),
        "limits": ["Prepared bytes are not tokens or native acceptance.",
                   "SELECT enum complexity and the normal extra call remain.",
                   "Schema reduction alone does not establish lower total cost."],
    }


def group_trace_report(
    trace: LeadGroupTrace,
    calls: tuple[tuple[str, ProviderCallAccounting], ...],
    *, unmeasured_prepared_call_ids: tuple[str, ...] = (),
) -> dict[str, object]:
    """Join physical costs to a private trace; missing observations stay unknown."""

    if type(trace) is not LeadGroupTrace or type(calls) is not tuple:
        raise TypeError("report requires a typed durable trace and call facts")
    by_id: dict[str, ProviderCallAccounting] = {}
    for call_id, accounting in calls:
        if type(call_id) is not str or type(accounting) is not ProviderCallAccounting:
            raise TypeError("invalid physical-call report input")
        old = by_id.get(call_id)
        if old is not None and old.state is ProviderCallState.SUBMITTED:
            raise ValueError("submitted physical call appears more than once")
        by_id[call_id] = accounting
    submitted = [(identity, item) for identity, item in by_id.items() if item.state is ProviderCallState.SUBMITTED]
    unresolved_ids = set(unmeasured_prepared_call_ids) - set(by_id)
    proven_prepared_ids = {identity for identity, item in by_id.items()
                           if item.state is ProviderCallState.PREPARED}
    submission_complete = not unresolved_ids
    submitted_denominator = len(submitted) if submission_complete else None
    prior: dict[tuple[str, str], ProviderUsageObservation] = {}
    usage_rows = []
    rows = []
    for identity, item in submitted:
        observation = item.usage_observation
        usage = None
        if observation is not None:
            key = (observation.session_ref or "", observation.previous_prefix_ref or "")
            usage = normalized_provider_usage(observation, prior.get(key))
            if observation.session_ref is not None and observation.prefix_ref is not None:
                prior[(observation.session_ref, observation.prefix_ref)] = observation
        usage_rows.append(usage)
        rows.append({"call_id": identity, "call_kind": item.payload.call_kind.value,
                     "schema_utf8_bytes": item.payload.schema_utf8_bytes,
                     "payload_utf8_bytes": item.payload.payload_utf8_bytes,
                     "model_input_utf8_bytes": item.submitted_model_input_utf8_bytes,
                     "transport_utf8_bytes": item.actual_transport_utf8_bytes,
                     "elapsed_ns": item.elapsed_ns,
                     "raw_usage": None if item.usage is None else {
                         name: getattr(item.usage, name) for name in ("input_tokens", "output_tokens", "cached_input_tokens")},
                     "usage_scope": None if observation is None else observation.scope.value,
                     "usage_source": None if observation is None else observation.source,
                     "attributable_usage": None if usage is None else {
                         name: getattr(usage, name) for name in ("input_tokens", "output_tokens", "cached_input_tokens")}})
    def total(name: str) -> int | None:
        values = [None if item is None else getattr(item, name) for item in usage_rows]
        return sum(values) if values and all(value is not None for value in values) else None
    transport = [item.actual_transport_utf8_bytes for _, item in submitted]
    elapsed = [item.elapsed_ns for _, item in submitted]
    bound_by_id = {item.call_id: item for item in trace.observations
                   if item.kind is LeadGroupObservationKind.BOUND and item.call_id is not None}
    bound = [bound_by_id[identity] for identity, _ in submitted if identity in bound_by_id]
    planned_deliveries = [json.loads(item.delivery_snapshot) for item in bound_by_id.values()
                          if item.delivery_snapshot is not None]
    actual_deliveries = {identity: json.loads(bound_by_id[identity].delivery_snapshot)
                         for identity, _ in submitted if identity in bound_by_id
                         and bound_by_id[identity].delivery_snapshot is not None}
    submitted_ids = {identity for identity, _ in submitted}
    binding_complete = submission_complete and len(bound) == len(submitted)
    classified_deliveries = {identity: delivery for identity, delivery in actual_deliveries.items()
                             if delivery.get("mode") in {"bootstrap", "delta"}}
    delivery_complete = submission_complete and len(classified_deliveries) == len(submitted)
    full_ids = {identity for identity, delivery in classified_deliveries.items()
                if delivery["mode"] == "bootstrap"}
    first_submitted_id = submitted[0][0] if submitted else None
    redelivery_ids = full_ids - {first_submitted_id}
    repairs = sum(item.lineage_kind == "repair" for item in bound)
    replays = sum(item.lineage_kind == "replay" for item in bound)
    reselections = len({item.call_id for item in trace.observations
        if item.kind is LeadGroupObservationKind.CONTROL_ACCEPTED
        and item.call_id in submitted_ids and item.selection_snapshots
        and json.loads(item.selection_snapshots[-1]).get("response_type") == "schema_group_reselection"})

    def fraction(numerator, denominator):
        return {"numerator": numerator, "denominator": denominator,
                "fraction": (None if numerator is None or denominator is None or denominator == 0
                             else numerator / denominator)}

    def occurrence(observed):
        return int(observed > 0) if observed or binding_complete else None

    def delivery_bytes(mode, *, transport=False, identities=None):
        if not delivery_complete:
            return None
        values = [item.actual_transport_utf8_bytes if transport else item.payload.payload_utf8_bytes
                  for identity, item in submitted
                  if classified_deliveries.get(identity, {}).get("mode") == mode
                  and (identities is None or identity in identities)]
        return None if any(value is None for value in values) else sum(values)
    terminal = None if trace.terminal_snapshot is None else json.loads(trace.terminal_snapshot)
    known_subtotals = {
        "calls": len(submitted),
        "model_input_utf8_bytes": sum(item.gross_model_input_utf8_bytes for _, item in submitted),
        "max_call_model_input_utf8_bytes": max((item.gross_model_input_utf8_bytes for _, item in submitted), default=None),
        "transport_utf8_bytes": sum(transport) if transport and all(value is not None for value in transport) else None,
        "elapsed_ns": sum(elapsed) if elapsed and all(value is not None for value in elapsed) else None,
        "input_tokens": total("input_tokens"), "output_tokens": total("output_tokens"),
        "cached_input_tokens": total("cached_input_tokens"),
    }
    return {
        "invocation_id": trace.invocation_id, "terminal_code": trace.terminal_code,
        "trace": trace.as_object(), "physical_calls": rows,
        "submission_coverage": {
            "scope": "known_submitted_accounting_records", "complete": submission_complete,
            "known_submitted_call_ids": [identity for identity, _ in submitted],
            "proven_prepared_only_call_ids": sorted(proven_prepared_ids),
            "unresolved_call_ids": sorted(unresolved_ids),
        },
        "known_submitted_subtotals": known_subtotals,
        "known_submitted_calls": len(submitted),
        "submitted_calls": submitted_denominator,
        "prepared_only_calls": len(proven_prepared_ids), "unresolved_calls": len(unresolved_ids),
        "input_tokens": known_subtotals["input_tokens"] if submission_complete else None,
        "output_tokens": known_subtotals["output_tokens"] if submission_complete else None,
        "cached_input_tokens": known_subtotals["cached_input_tokens"] if submission_complete else None,
        "submitted_model_input_utf8_bytes": known_subtotals["model_input_utf8_bytes"] if submission_complete else None,
        "max_call_model_input_utf8_bytes": known_subtotals["max_call_model_input_utf8_bytes"] if submission_complete else None,
        "transport_utf8_bytes": known_subtotals["transport_utf8_bytes"] if submission_complete else None,
        "elapsed_ns": known_subtotals["elapsed_ns"] if submission_complete else None,
        "logical_elapsed_ns": trace.elapsed_ns,
        "logical_latency_stage": "first_bound_to_terminal_prepublication",
        "end_to_end_elapsed_ns": None,
        "full_deliveries": len(full_ids) if delivery_complete else None,
        "delta_deliveries": len(classified_deliveries) - len(full_ids) if delivery_complete else None,
        "observed_full_deliveries": len(full_ids),
        "observed_delta_deliveries": len(classified_deliveries) - len(full_ids),
        "unclassified_submitted_deliveries": len(submitted) - len(classified_deliveries),
        "unclassified_submitted_bindings": len(submitted) - len(bound),
        "planned_full_deliveries": sum(item.get("mode") == "bootstrap" for item in planned_deliveries),
        "planned_delta_deliveries": sum(item.get("mode") == "delta" for item in planned_deliveries),
        "submitted_delivery_details": actual_deliveries,
        "full_delivery_payload_utf8_bytes": delivery_bytes("bootstrap"),
        "full_delivery_transport_utf8_bytes": delivery_bytes("bootstrap", transport=True),
        "delta_delivery_payload_utf8_bytes": delivery_bytes("delta"),
        "delta_delivery_transport_utf8_bytes": delivery_bytes("delta", transport=True),
        "full_redeliveries": len(redelivery_ids) if delivery_complete else None,
        "full_redelivery_payload_utf8_bytes": delivery_bytes("bootstrap", identities=redelivery_ids),
        "full_redelivery_transport_utf8_bytes": delivery_bytes("bootstrap", transport=True, identities=redelivery_ids),
        "phase_submitted_calls": {phase: (sum(item.phase.value == phase for item in bound) if binding_complete else None)
                                  for phase in ("select", "generate")},
        "rejected_responses": sum(item.kind is LeadGroupObservationKind.REJECTED for item in trace.observations),
        "logical_invocations": 1,
        "result_accepted": trace.terminal_code == "accepted",
        "logical_terminal_state": "done" if trace.terminal_code == "accepted" else "stopped_or_failed",
        "terminal_result": terminal,
        "task_completion": None, "objective_completion": None,
        "reselection_count": reselections if binding_complete else None,
        "max_output_tokens_failures": None,
        "repairs": repairs if binding_complete else None,
        "replays": replays if binding_complete else None,
        "observed_repair_calls": repairs,
        "observed_replay_calls": replays,
        "observed_reselection_controls": reselections,
        "planned_repairs": sum(item.lineage_kind == "repair" for item in bound_by_id.values()),
        "planned_replays": sum(item.lineage_kind == "replay" for item in bound_by_id.values()),
        "prepared_only_repairs": sum(item.lineage_kind == "repair" and identity in proven_prepared_ids
                                     for identity, item in bound_by_id.items()),
        "prepared_only_replays": sum(item.lineage_kind == "replay" and identity in proven_prepared_ids
                                     for identity, item in bound_by_id.items()),
        "rates": {
            "repair_calls_per_submitted_call": fraction(repairs if binding_complete else None, submitted_denominator),
            "replay_calls_per_submitted_call": fraction(replays if binding_complete else None, submitted_denominator),
            "reselection_controls_per_submitted_call": fraction(reselections if binding_complete else None, submitted_denominator),
            "invocations_with_submitted_repair": fraction(occurrence(repairs), 1),
            "invocations_with_submitted_replay": fraction(occurrence(replays), 1),
            "invocations_with_submitted_reselection": fraction(occurrence(reselections), 1),
            "full_deliveries_per_submitted_call": fraction(len(full_ids) if delivery_complete else None, submitted_denominator),
            "full_redeliveries_per_followup_submitted_call": fraction(len(redelivery_ids) if delivery_complete else None, max(0, len(submitted) - 1) if submission_complete else None),
            "accepted_results_per_logical_invocation": fraction(int(trace.terminal_code == "accepted"), 1),
            "task_completion": None, "objective_completion": None, "max_output_tokens_failures": None,
        },
        "cost": None, "output_token_cap": None,
        "limits": ["Missing usage, price, latency, or output cap is not zero.",
                   "Raw cumulative observations are never blindly summed.",
                   "Delta stdin does not prove lower full model input or cost.",
                   "Redelivery excludes the first submitted call; rates include their explicit physical-call or one-invocation denominator.",
                   "Planned counts include all BOUND preparations; prepared-only counts require explicit PREPARED accounting.",
                   "An unmeasured durable PREPARED record leaves submission unresolved; known-submitted subtotals are not complete actual totals.",
                   "Logical timing excludes pre-BOUND Host preparation and publication I/O; full end-to-end latency is unavailable."],
    }
