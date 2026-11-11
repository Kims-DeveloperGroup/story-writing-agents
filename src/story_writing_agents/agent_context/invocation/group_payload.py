"""Read-only grouped payload projection of ledger-backed invocation facts."""

from __future__ import annotations

import hashlib
import json
from ...provider_schema_selection import SchemaSelectionInput
from ...provider_wire import (
    add_contract_guidance,
    project_context_payload,
)
from ..accounting import (
    ContextComponentKind,
    JsonPath,
    canonical_provider_json,
)
from ..contracts.bound_call import BoundCallContract
from ..contracts.errors import (
    ContextExecutionErrorCode,
    execution_error,
)
from ..contracts.planning_inputs import ContextPlanningConditions
from ..request_compaction import compact_context_payload
from .delivery import (
    ContextGroupInvocationState,
    group_native_deliveries,
)
from collections.abc import Mapping


def render_group_context_payload(
    payload: dict[str, object], component_paths: Mapping[JsonPath, ContextComponentKind],
    mandatory_paths: tuple[JsonPath, ...], *, schema: Mapping[str, object],
    contract: BoundCallContract, state: ContextGroupInvocationState, remaining: int,
    observation: str | None, conditions: ContextPlanningConditions, fresh: bool,
    ledger_verified: bool, provider_thread_id: str | None,
) -> tuple[dict, dict, tuple, bytes]:
    """논리 입력을 비교한 뒤 현재 요청 안에서만 공유 pool을 다시 만든다."""
    full, paths, references = project_context_payload(payload, component_paths, mandatory_paths)
    prior = state.transport_delivery
    compatible = prior is not None and prior.source_valid() and prior.request_basis == state.request_basis and prior.conditions == conditions
    old_envelope = json.loads(prior.logical_context) if compatible else {}
    current_basis_ref = contract.group_stage.catalog.request_basis_ref
    accepted = state.accepted_delivery
    accepted_compatible = (accepted is not None and accepted.source_valid() and accepted.request_basis == state.request_basis
                           and accepted.conditions == conditions)
    accepted_envelope = json.loads(accepted.logical_context) if accepted_compatible else {}
    transport_input = old_envelope.get("input", {})
    old = accepted_envelope.get("input", {})
    evidence_offers = contract.eligibility_offers if contract.lineage is not None else contract.offers
    read_facts = {f.evidence_id: f for f in (*evidence_offers.completed_read_facts, *evidence_offers.delivery_read_facts)}
    facts = {identity: fact.snapshot_value() for identity, fact in read_facts.items()}
    previous_facts = old_envelope.get("read_facts", {})
    observations = []
    for item in transport_input.get("context_observations", []):
        if all(facts.get(identity) == previous_facts.get(identity) for identity in item["read_fact_ids"]):
            observations.append(item)
    if observation is not None:
        projected, _, _ = project_context_payload({"o": json.loads(observation)}, {}, ())
        value = projected["context_observation"]
        # READ 묶음을 evidence별로 보유하여 한 자료의 교체/제거가 다른 자료를 지우지 않게 한다.
        read_key = "read_results" if "read_results" in value else "evidence" if value.get("kind") == "read_observation" else None
        entries = []
        if read_key is not None:
            for evidence in value[read_key]:
                fact = read_facts.get(evidence.get("evidence_id"))
                if fact is None:
                    continue
                source = evidence.get("source", evidence)
                boundary = evidence.get("source_range", evidence)
                if (source.get("source_id") != fact.source_id
                        or source.get("source_revision", source.get("revision")) != fact.revision
                        or source.get("source_digest") != fact.source_digest
                        or boundary.get("range_digest") != fact.boundary_digest):
                    continue
                entries.append({"read_fact_ids": [fact.evidence_id],
                                "observation": {**value, read_key: [evidence]}})
        else:
            entries.append({"read_fact_ids": [], "observation": value})
        for entry in entries:
            if not any(item["observation"] == entry["observation"] for item in observations):
                observations.append(entry)
    if observations:
        full["context_observations"] = observations
        paths[("context_observations",)] = ContextComponentKind.RETRIEVED_OBSERVATION
    catalog = contract.group_stage.catalog.as_object()
    envelope = canonical_provider_json({"input": full, "catalog": catalog, "read_facts": facts,
                                         "selection_request_basis_ref": current_basis_ref})
    context_ref = "ctx_" + hashlib.sha256(envelope).hexdigest()
    delta = bool(ledger_verified and not fresh and compatible and accepted_compatible
                 and group_native_deliveries(state, conditions, provider_thread_id)
                 and old_envelope.get("selection_request_basis_ref") == current_basis_ref
                 and accepted_envelope.get("selection_request_basis_ref") == current_basis_ref)
    current = full
    if delta:
        fresh_keys = {"protocol_version", "request_correlation_token", "response_contract"}
        add = {k: v for k, v in full.items() if k not in old and k not in fresh_keys}
        changed = {k: v for k, v in full.items() if k in old and (old[k] != v or transport_input.get(k) != v) and k not in fresh_keys}
        removed = [k for k in dict.fromkeys((*old, *transport_input)) if k not in full and k not in fresh_keys]
        current = {k: v for k, v in full.items() if k in fresh_keys}
        current["context_changes"] = {"add": add, "replace": changed, "remove": removed}
        rebased = {}
        for path, kind in paths.items():
            if path[0] in fresh_keys:
                rebased[path] = kind
            elif path[0] in add or path[0] in changed:
                rebased[("context_changes", "add" if path[0] in add else "replace", *path)] = kind
        references = tuple(("context_changes", "add" if p[0] in add else "replace", *p)
                           for p in references if p[0] in add or p[0] in changed)
        paths = rebased
    active = state.workflow.active_selection
    include_catalog = not delta or accepted_envelope.get("catalog", {}).get("catalog_id") != catalog["catalog_id"] or old_envelope.get("catalog", {}).get("catalog_id") != catalog["catalog_id"]
    selection = SchemaSelectionInput(
        contract.schema_group_phase.value, contract.offers.call_id, contract.outer_inner_schema_ids,
        remaining, context_ref, accepted.context_ref if delta else None,
        canonical_provider_json(catalog) if include_catalog else None,
        None if include_catalog else catalog["catalog_id"],
        None if active is None else (active.catalog.catalog_id, active.group_id))
    current["response_contract"]["schema_selection"] = selection.as_object()
    # 현재 제어 슬롯은 공유 pool에 넘기지 않아 정확한 닫힌 타입을 보존한다.
    control = current["response_contract"].pop("schema_selection")
    rendered = add_contract_guidance(current, schema)
    rendered, paths = compact_context_payload(rendered, paths, references)
    rendered["response_contract"]["schema_selection"] = control
    rendered = add_contract_guidance(rendered, schema)
    if SchemaSelectionInput.from_value(rendered["response_contract"]["schema_selection"]) != selection:
        raise execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
    return rendered, paths, references, envelope


__all__ = [
    'render_group_context_payload',
]
