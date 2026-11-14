"""Outbound provider request and runtime-value mapping."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy

from .agent_task_projection import (
    HOST_TASK_FIELDS as _HOST_TASK_FIELDS,
    TASK_CONTEXT_SHAPES as _TASK_CONTEXT_SHAPES,
)
from .provider_contract_guidance import diagnostic_to_wire
from .provider_message_catalog import (
    ACTION_LIMIT_NAMES,
    ACTION_TYPES,
    ACTIVITIES,
    ASSIGNMENT_STATUSES,
    ASSIGNMENT_TYPES,
    BUILTIN_RESULT_CONTRACT_IDS,
    CHANGE_TYPES,
    DIRECT_FIELDS,
    DRAFTING_ACTIONS,
    INPUT_FIELDS,
    OPAQUE_FIELDS,
    PHASES,
    REQUEST_STATUSES,
    REQUEST_STEPS,
    REQUEST_UPDATES,
    RETRIEVAL_ACTIONS,
    SOURCE_TYPES,
    STRUCTURED_INPUT_FIELDS,
    TOP_LEVEL_FIELDS,
    WIRE_VERSION,
    WORKFLOWS,
    parse_task_reference_token as _task_reference,
)
from .provider_schema_selection import SchemaSelectionInput

def project_artifact_candidate(value: Mapping) -> dict:
    """Project the immutable review candidate without changing its literal text."""
    names = {
        "candidate": {"candidate_id": "candidate_id", "candidate_revision": "candidate_revision", "candidate_digest": "candidate_digest", "change_set": "artifact_change_set", "disclosed_review_domains": "disclosed_review_domains", "grounding_evidence_ids": "grounding_evidence_ids", "mandatory_review_domains": "mandatory_review_domains", "relationship_validation": "relationship_validation", "schema_version": "schema_version"},
        "change_set": {"base_state_digest": "base_state_digest", "change_set_digest": "change_set_digest", "operations": "artifact_changes", "projected_state_digest": "projected_state_digest", "schema_version": "schema_version"},
        "operation": {"operation": "change_type", "kind": "artifact_type", "path": "artifact_path", "source_path": "source_artifact_path", "destination_path": "destination_artifact_path", "content": "content_text", "base_digest": "base_content_digest", "projected_digest": "proposed_content_digest", "hunks": "text_patches"},
        "hunk": {"base_digest": "base_content_digest", "start_utf8_byte": "start_utf8_byte", "end_utf8_byte": "end_utf8_byte", "old_text": "expected_content_text", "new_text": "new_content_text"},
        "relationship": {"added_edges": "added_document_links", "removed_edges": "removed_document_links", "retained_edges": "retained_document_links", "projected_dangling_edges": "unresolved_document_links", "review_facts": "required_review_reasons", "base_state_digest": "base_state_digest", "change_set_digest": "change_set_digest", "projected_state_digest": "projected_state_digest", "schema_version": "schema_version"},
        "edge": {"source_path": "source_artifact_path", "target_path": "target_artifact_path", "destination_start_utf8_byte": "destination_start_utf8_byte", "destination_end_utf8_byte": "destination_end_utf8_byte", "fragment": "target_fragment", "relationship_kind": "relationship_type"},
        "review_fact": {"artifact_path": "artifact_path", "domain": "review_domain", "operation_index": "artifact_change_index", "reason": "review_reason"},
    }
    child_shapes = {("candidate", "change_set"): "change_set", ("candidate", "relationship_validation"): "relationship", ("change_set", "operations"): "operation", ("operation", "hunks"): "hunk", ("relationship", "added_edges"): "edge", ("relationship", "removed_edges"): "edge", ("relationship", "retained_edges"): "edge", ("relationship", "projected_dangling_edges"): "edge", ("relationship", "review_facts"): "review_fact"}
    review_reasons = {"touched_core": "core_document_changed", "manuscript_operation": "manuscript_changed", "attached_core_evidence": "attached_core_document_evidence", "relationship_core_evidence": "linked_core_document_evidence"}

    def visit(node: object, shape: str) -> object:
        if isinstance(node, (list, tuple)):
            return [visit(item, shape) for item in node]
        if not isinstance(node, Mapping):
            raise TypeError("artifact review candidate must contain named records")
        result = {}
        for name, item in node.items():
            wire = names[shape][name]
            child_shape = child_shapes.get((shape, name))
            if child_shape:
                result[wire] = visit(item, child_shape)
            elif shape == "operation" and name == "operation":
                result[wire] = CHANGE_TYPES[item]
            elif shape == "review_fact" and name == "reason":
                result[wire] = review_reasons[item]
            else:
                result[wire] = deepcopy(item)
        return result

    return visit(value, "candidate")


def project_runtime_object(value: object, shape: str = "") -> object:
    """Project only known runtime containers; literal source-owned bodies stop traversal."""
    if shape in {"authoring_assessment", "core_document_assessment", "core_document_readiness"}:
        return deepcopy(value)
    if shape in OPAQUE_FIELDS:
        return deepcopy(value)
    if shape == "artifact_candidate":
        return project_artifact_candidate(value)
    if shape == "objective_steps":
        return {"clarification_question": "ask_clarification", "specialist_consultation_proposal": "consult_specialists", "artifact_change_proposal": "draft_document_change_proposal"}
    if shape in {"artifact_objectives", "document_change_requests"} and isinstance(value, Mapping) and not ({"objectives", "requests"} & value.keys()):
        return deepcopy(value)
    if isinstance(value, (list, tuple)):
        return [project_runtime_object(item, shape) for item in value]
    if not isinstance(value, Mapping):
        return value
    if shape == "" and set(value) == {"original_request", "response_correction"}:
        return {
            "original_task_input": project_runtime_object(value["original_request"]),
            "response_correction": project_runtime_object(value["response_correction"], "response_correction"),
        }
    if shape in {"context", "conversation_context", "events"} and "bootstrap" not in value:
        names = {"kind": "message_type", "content": "content_text", "sequence": "sequence_number"}
        return {names.get(key, key): deepcopy(item) for key, item in value.items()}
    if shape in {"messages", "agent_state"}:
        names = {"phase": "consensus_phase", "round": "consensus_round_number", "revision": "candidate_revision_id", "content": "content_text", "kind": "message_type" if shape == "messages" else "outcome_type"}
        kinds = {"candidate": "candidate_text", "audit": "candidate_review", "synthesis": "review_synthesis", "deadlock": "consensus_deadlock", "finalization": "reviewed_proposal_confirmation"}
        return {names.get(key, key): PHASES.get(item, item) if key == "phase" else kinds.get(item, item) if key == "kind" else deepcopy(item) for key, item in value.items()}
    result = {}
    for name, item in value.items():
        owned_shape = not shape or shape in _TASK_CONTEXT_SHAPES or shape in STRUCTURED_INPUT_FIELDS
        if owned_shape and (name in _HOST_TASK_FIELDS or (name == "task" and shape in
                {"authoring_scope", "scope", "confirmed_scope", "writing_task_scope"})):
            continue
        wire = INPUT_FIELDS.get(name, name)
        if shape == "authoring_admission" and name == "assessment":
            result[name] = deepcopy(item)
            continue
        if shape in {"inputs", "selected_inputs"}:
            if name == "candidate":
                result["artifact_candidate"] = project_artifact_candidate(item)
                continue
            if name == "reply":
                result["specialist_reply"] = project_runtime_object(item, "replies")
                continue
            if name == "boundary":
                boundary_kind = item["kind"]
                boundary = {"range_type": {"whole": "entire_source", "artifact_lines": "line_range", "records": "record_range"}[boundary_kind]}
                if boundary_kind != "whole":
                    start, end = (("start_line", "end_line") if boundary_kind == "artifact_lines" else ("start_record_number", "end_record_number"))
                    boundary.update({start: item["start"], end: item["end"]})
                result["source_range"] = boundary
                continue
        if name == "source_kind":
            wire = "source_type"
        if name == "authorized_source_kinds":
            result["authorized_source_types"] = [{**SOURCE_TYPES, "agent_state": "agent_work_history"}.get(choice, choice) for choice in item]
            continue
        if name == "artifact_path_prefix":
            wire = "artifact_directory_prefix"
        if name == "decision":
            wire = "decision"
        if shape == "evidence" and name == "scope":
            wire = "evidence_scope"
        if shape == "evidence" and name == "status":
            wire = "evidence_status"
        if name == "facts" and shape == "objectives":
            wire = "request_details"
        if name == "request" and shape == "facts":
            wire = "originating_user_request_text"
        if name == "satisfied_by_event_id" and shape == "objectives":
            wire = "completed_by_event_id"
        if name == "content" and shape == "excerpts":
            wire = "content_text"
        if name == "subject" and shape in {"conversations", "session_history", "h"}:
            wire = "conversation_subject"
        if name == "status" and shape == "conversations":
            wire = "conversation_status"
        if name == "current_state":
            wire = "current_session_state"
        if name == "delivery_mode":
            wire = "context_delivery_mode"
        if name == "fields" and shape == "records":
            wire = "readable_fields"
        if name == "key" and shape == "fields":
            wire = "field_key"
        if name == "text" and shape == "fields":
            wire = "field_text"
        if name == "sequence" and shape == "records":
            wire = "sequence_number"
        if name == "text" and shape in {"inputs", "selected_inputs"}:
            wire = "content_text"
        if name == "locator" and shape in {"input_aliases", "inputs", "selected_inputs"}:
            wire = "source_locator"
        if name == "round" and shape in {"assignment", "assignments", "replies"}:
            wire = "assignment_round_number"
        if name == "current_scope":
            wire = "current_writing_task_scope"
        if name == "max_preparation_physical_calls":
            wire = "max_proposal_drafting_physical_calls"
        if name == "kind" and shape in {"input_aliases", "inputs", "selected_inputs"}:
            result["input_source_type"] = {**SOURCE_TYPES, "agent_state": "agent_work_history"}.get(item, item)
            continue
        if name == "task" and shape in {"authoring_scope", "scope", "confirmed_scope"} and isinstance(item, str) and "@" in item:
            result["task_reference"] = _task_reference(item)
            continue
        if name == "kind":
            wire = ("change_type" if shape == "operations" else "action_type" if shape in {"recommended_action", "preparation"}
                    else "actor_type" if shape in {"sender", "recipient"} else "action_type" if shape == "proposal_drafting_request_record" else "decision_type" if shape == "confirmation_decision" else "record_type")
        if name == "mode":
            wire = "assignment_type" if shape in {"assignment", "assignments", "replies"} else "context_mode"
        if name == "scope" and shape == "story_session_context":
            wire = "context_scope"
        if name == "action" and shape in {"objective_intent", "objectives"}:
            wire = "update_type"
        if name == "state" and shape == "objectives":
            wire = "request_status"
        if name == "instruction" and shape == "artifact_objectives":
            result["request_lifecycle_instructions"] = "Only the originating conversation may continue or cancel a pending document change request. Draft sources identify prior text, never current READ evidence or execution authority. Only exact applied or unchanged Apply completes the request."
            continue
        if shape == "owner_outcomes" and name == "entries":
            projected = project_runtime_object(item, "agent_state")
        elif shape == "structured_recommendation_execution" and name in {"request", "decision", "start"}:
            projected = project_runtime_object(item, {"request": "proposal_drafting_request_record", "decision": "confirmation_decision", "start": "proposal_drafting_start_record"}[name])
        elif shape == "confirmation_decision" and name == "decision":
            projected = project_runtime_object(item, "confirmation_decision")
        elif name in OPAQUE_FIELDS:
            projected = deepcopy(item)
        elif wire in STRUCTURED_INPUT_FIELDS or name in _TASK_CONTEXT_SHAPES:
            projected = project_runtime_object(item, "authoring_scope" if name == "current_writing_scope" else name)
        else:
            projected = deepcopy(item)
        mapping = {"workflow": WORKFLOWS, "source_kind": SOURCE_TYPES, "activity": ACTIVITIES,
                   "mode": ASSIGNMENT_TYPES if shape in {"assignment", "assignments", "replies"} else {},
                   "assessment": ASSIGNMENT_STATUSES, "next_step": REQUEST_STEPS,
                   "kind": CHANGE_TYPES if shape == "operations" else DRAFTING_ACTIONS,
                   "action": REQUEST_UPDATES if shape in {"objective_intent", "objectives"} else {},
                   "state": REQUEST_STATUSES if shape == "objectives" else {}}.get(name, {})
        if shape == "objective_steps":
            mapping = REQUEST_STEPS
        if isinstance(projected, str):
            projected = mapping.get(projected, projected)
            if shape == "provider_constraints" and name in {
                "source_membership", "recommendation_source_membership", "artifact_path_rule", "runtime_input_rule",
            }:
                projected = diagnostic_to_wire(projected)
        result[wire] = projected
    return result


def _candidate_reference(value: Mapping) -> dict:
    return {"source_reference_id": value["source_alias"], "record_reference_id": value["record_alias"], "content_digest": value["digest"], "content_utf8_bytes": value["utf8_bytes"]}


def project_candidate(value: object) -> object:
    if isinstance(value, str):
        return {"content_delivery": "full_text", "content_text": value}
    if isinstance(value, dict) and value.get("op") in {"candidate_pointer", "candidate_single_splice"}:
        result = {"content_delivery": "reuse_previous_candidate" if value["op"] == "candidate_pointer" else "apply_text_splice",
                  "base_candidate_reference": _candidate_reference(value["base"]),
                  "target_candidate_reference": _candidate_reference(value["target"])}
        if value["op"] == "candidate_single_splice":
            result["text_splice"] = {"prefix_codepoint_count": value["prefix_codepoints"], "replacement_content_text": value["replacement"], "suffix_codepoint_count": value["suffix_codepoints"]}
        return result
    return project_runtime_object(value)


def project_context_payload(payload: Mapping, component_paths: Mapping, mandatory_paths: tuple = ()) -> tuple[dict, dict, tuple]:
    """Replace closed positional request shapes with named, lossless objects."""
    result = {}
    path_aliases = {}
    for key, value in payload.items():
        target = TOP_LEVEL_FIELDS.get(key, key)
        if key == "q":
            phase, actions, sources, limits, contract = value
            result[target] = {"workflow_phase": WORKFLOWS.get(phase, PHASES.get(phase, phase)),
                              "allowed_action_types": [ACTION_TYPES[{"s": "search", "r": "read", "a": "answer"}[code]] for code in actions],
                              "searchable_source_types": [SOURCE_TYPES[{"w": "writing_artifact", "c": "conversation", "d": "active_debate", "o": "own_agent_state"}[code]] for code in sources],
                              "action_limits": dict(zip(ACTION_LIMIT_NAMES, limits, strict=True)), "result_contract_id": contract[-1]}
        elif key == "docs":
            result[target] = [dict(zip(("source_reference_id", "artifact_path", "content_text"), record, strict=True)) for record in value]
            for index in range(len(value)):
                path_aliases[(key, index, 2)] = (target, index, "content_text")
        elif key == "p":
            groups = []
            for index, group in enumerate(value):
                code, source_id = group[:2]
                item = {"source_type": {"c": "conversation_history", "d": "current_consensus_run", "o": "agent_work_history"}[code], "source_reference_id": source_id}
                if code == "c":
                    item.update(total_complete_turns=group[2], omitted_turns=group[3])
                records = []
                record_index = 4 if code == "c" else 2
                path_aliases[(key, index, record_index)] = (target, index, "records")
                for offset, record in enumerate(group[record_index]):
                    out = {"record_reference_id": record[0]}
                    if code == "c" and len(record) == 4:
                        out.update(record_type="conversation_turn", user_input_text=record[1], responding_agent_id=record[2], response_text=record[3])
                    elif code == "d" and record[1] == "s":
                        out.update(record_type="consensus_state", input_type={"prompt": "writing_brief", "draft": "user_supplied_draft"}[record[2]], candidate_revision_id=record[3], completed_round_count=record[4])
                    elif code == "d" and record[1] == "m":
                        content = record[7]
                        if isinstance(content, list) and content[:1] == ["="]:
                            if content[1] == "k" and not isinstance(payload.get("n"), str):
                                content = {"candidate_text_reference": "/current_draft"}
                            else:
                                content = {"content_reference": "/" + ({"i": "task_input/input" if isinstance(payload.get("i"), dict) and "input" in payload["i"] else "task_input", "k": "current_draft/content_text"}[content[1]])}
                        out.update(record_type="consensus_message", sender=record[2], consensus_phase=PHASES.get(record[3], record[3]), consensus_round_number=record[4], candidate_revision_id=record[5], message_type={"candidate": "candidate_text", "audit": "candidate_review", "synthesis": "review_synthesis", "deadlock": "consensus_deadlock", "finalization": "reviewed_proposal_confirmation"}.get(record[6], record[6]), content_text=content)
                    else:
                        out.update(record_type="conversation_event" if code == "c" else "agent_outcome", record_content=project_runtime_object(record[1], "records" if code == "c" else "agent_state"))
                    records.append(out)
                    path_aliases[(key, index, record_index, offset)] = (target, index, "records", offset)
                item["records"] = records
                groups.append(item)
            result[target] = groups
        elif key == "t":
            result[target] = [{"removal_type": {"s": "remove_source", "d": "remove_source_content", "r": "remove_record"}[item[0]], "source_reference_id": item[1], **({"record_reference_id": item[2]} if len(item) > 2 else {})} for item in value]
        elif key == "n":
            result[target] = project_candidate(value)
        elif key == "core_statuses":
            result[target] = [{"evidence_id": item["evidence_id"], "artifact_path": item["locator"], "document_status": {"present": "has_content", "nonempty": "has_content", "empty": "empty_file", "absent": "missing_file", "missing": "missing_file"}.get(item["status"], item["status"])} for item in value]
        elif key == "untrusted_writing_artifact_context":
            result[target] = [project_attached_reference(item) for item in value]
            for index in range(len(value)):
                path_aliases[(key, index, "content")] = (target, index, "content_text")
        elif key == "i" and payload["q"][4][-1] not in BUILTIN_RESULT_CONTRACT_IDS:
            result[target] = deepcopy(value)
        elif key == "o":
            result[target] = value
        else:
            result[target] = project_runtime_object(value)
    def path(value: tuple) -> tuple:
        if value in path_aliases:
            return path_aliases[value]
        return (TOP_LEVEL_FIELDS.get(value[0], value[0]), *value[1:]) if value else value
    result["protocol_version"] = WIRE_VERSION
    selection = result.get("response_contract", {}).get("schema_selection")
    if selection is not None:
        SchemaSelectionInput.from_value(selection)
    return result, {path(key): val for key, val in component_paths.items()}, tuple(path(key) for key in mandatory_paths)


def project_attached_reference(item: Mapping) -> dict:
    reference = item.get("reference")
    result = dict(item)
    if isinstance(reference, Mapping):
        bounds = reference.get("line_range")
        result["artifact_reference"] = {"artifact_path": reference["path"], "line_range": None if bounds is None else {"start_line": bounds["start"], "end_line": bounds["end"]}}
        result.pop("reference", None)
    if "content" in result:
        result["content_text"] = result.pop("content")
    return result


def project_zero_hit_receipt(value: Mapping) -> dict:
    """Project the provider receipt; durable receipt identities are unchanged."""
    return {({"authorized_source_kinds": "authorized_source_types", "artifact_path_prefix": "artifact_directory_prefix"}.get(key, key)):
            [SOURCE_TYPES[item] for item in item_value] if key == "authorized_source_kinds" else deepcopy(item_value)
            for key, item_value in value.items()}


def project_retrieval_observation(frame: str) -> str:
    """Project only the validated direct-retrieval observation inside its frame."""
    begin, body, end = frame.split("\n", 2)
    if begin != "<<<BEGIN_UNTRUSTED_STORY_DATA>>>" or end != "<<<END_UNTRUSTED_STORY_DATA>>>":
        raise ValueError("retrieval observation frame is invalid")
    value = json.loads(body)
    def citation(source: Mapping) -> dict:
        return {"citation": {("sequence_number" if key == "sequence" else key): deepcopy(item)
                             for key, item in source["citation"].items()}}
    result = {"observation_type": "conversation_search_results" if value["kind"] == "search_conversations" else "conversation_history_records",
              "session_id": value["session_id"], "conversation_id": value["conversation_id"],
              "turn_id": value["turn_id"], "retrieval_action_number": value["action_index"], "truncated": value["truncated"]}
    if value["kind"] == "search_conversations":
        result["search_results"] = [{"citation_id": item["citation_id"], "source": citation(item["source"]), "snippet_text": item["snippet"]} for item in value["hits"]]
    else:
        result.update(source_citation_id=value["source_citation_id"], source=citation(value["source"]))
        records = []
        for record in value["records"]:
            item = {({"sequence": "sequence_number", "kind": "record_type"}.get(key, key)): deepcopy(content)
                    for key, content in record.items() if key not in {"fields", "sender", "recipient"}}
            for actor in ("sender", "recipient"):
                item[actor] = {("actor_type" if key == "kind" else key): deepcopy(content) for key, content in record[actor].items()}
            item["readable_fields"] = [{({"key": "field_key", "text": "field_text"}.get(key, key)): deepcopy(content)
                    for key, content in field.items()} for field in record["fields"]]
            records.append(item)
        result["history_records"] = records
    return begin + "\n" + json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" + end


def project_direct_payload(payload: Mapping) -> dict:
    result = {}
    for key, value in payload.items():
        name = DIRECT_FIELDS.get(key, key)
        if key == "phase":
            value = PHASES[value]
        elif key == "operation" and value == "conversation_retrieval":
            value = "answer_with_conversation_history"
        elif key == "retrieval":
            value = {"retrieval_action_number": value["action_index"], "allowed_action_types": [RETRIEVAL_ACTIONS[item] for item in value["allowed_actions"]], "retrieval_limits": value["policy"], "retrieval_instructions": list(value["rules"])}
        elif key == "observation":
            value = project_retrieval_observation(value)
        elif key == "untrusted_writing_artifact_context":
            value = [project_attached_reference(item) for item in value]
        elif isinstance(value, (dict, list, tuple)):
            value = project_runtime_object(value, key)
        result[name] = value
    result["protocol_version"] = WIRE_VERSION
    return result


__all__ = [
    'project_artifact_candidate',
    'project_attached_reference',
    'project_candidate',
    'project_context_payload',
    'project_direct_payload',
    'project_retrieval_observation',
    'project_runtime_object',
    'project_zero_hit_receipt',
]
