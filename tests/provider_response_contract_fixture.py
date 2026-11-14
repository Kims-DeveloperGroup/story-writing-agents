"""Fresh collaboration JSON samples shared by response contract tests."""
from __future__ import annotations
import json


def canonical_collaboration_json(value: object) -> bytes:
    return json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode("utf-8")


def collaboration_group_trace_fixture():
    from story_writing_agents.agent_context.accounting import canonical_provider_json
    from story_writing_agents.agent_context.group_observation import (
        LeadGroupObservation, LeadGroupObservationKind, LeadGroupTrace,
    )
    from story_writing_agents.lead_schema_groups import LeadGroupPhase, LeadGroupRequestBasis
    basis = LeadGroupRequestBasis("group-operation", "group-result", "group-scope")
    catalog = {
        "catalog_id": "historical-catalog", "catalog_version": "lead-groups-v1",
        "groups": [{"group_id": "answer.workflow", "definition": "Historical definition 한🙂",
                    "selection_criteria": "Historical criterion", "result_effect": "Existing answer only"}],
    }
    selection = {
        "catalog_at_selection": catalog, "selection_basis_ref": "historical-basis",
        "group_id": "answer.workflow", "selection_reason": "PRIVATE_SELECTION_REASON",
        "response_type": "schema_group_selection", "call_id": "group-call-1",
        "outer_schema_id": "historical-outer", "inner_schema_id": "historical-inner",
    }
    catalog = json.loads(json.dumps(catalog, sort_keys=True))
    selection = json.loads(json.dumps(selection, sort_keys=True))
    observations = (
        LeadGroupObservation(LeadGroupObservationKind.BOUND, basis, LeadGroupPhase.SELECT,
                             "group-call-1", catalog_snapshot=canonical_provider_json(catalog)),
        LeadGroupObservation(LeadGroupObservationKind.CONTROL_ACCEPTED, basis, LeadGroupPhase.SELECT,
                             "group-call-1", catalog_snapshot=canonical_provider_json(catalog),
                             selection_snapshots=(canonical_provider_json(selection),)),
        LeadGroupObservation(LeadGroupObservationKind.TERMINAL, basis, LeadGroupPhase.STOP,
                             selection_snapshots=(canonical_provider_json(selection),),
                             terminal_code="evidence_validation_rejected"),
    )
    return LeadGroupTrace("group-invocation", observations, "evidence_validation_rejected", elapsed_ns=37)
