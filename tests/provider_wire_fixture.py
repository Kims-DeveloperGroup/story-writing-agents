"""Independent wire-v10 writers for existing typed-domain provider scenarios."""
from __future__ import annotations

import copy
from collections.abc import Mapping
import json

_FIELDS = {
    "message": "response_text", "authoring_scope": "writing_task_scope",
    "activity": "activity_type",
    "intent": "writing_intent", "domains": "specialist_domains",
    "target_paths": "target_artifact_paths", "proposal": "specialist_consultation_proposal",
    "artifact_intent": "artifact_change_proposal", "next_intent": "follow_up_consultation",
    "instruction": "task_instructions", "input_ids": "input_source_ids",
    "mode": "assignment_type", "purpose": "consultation_purpose",
    "assessment": "assignment_status", "decision": "review_outcome",
    "operations": "artifact_changes", "suggested_path": "proposed_artifact_path",
    "suggested_destination": "proposed_destination_path", "new_text": "new_content_text",
    "replacement_text": "replacement_content_text", "recommended_action": "proposal_drafting_request",
    "objective_intent": "document_change_request_update", "objective_id": "document_change_request_id",
    "next_step": "requested_workflow_step",
}
_VALUES = {
    "activity": {"workflow": "workflow_guidance", "draft": "draft_writing", "revise": "revise_writing", "review": "review_writing"},
    "mode": {"draft": "draft_content", "review": "review_content", "constraint_assessment": "assess_constraints", "premise_analysis": "analyze_premise"},
    "assessment": {"drafted": "draft_completed", "supports": "supports_proposal", "concern": "concerns_found", "conflict": "conflicts_found", "constraints_assessed": "constraints_assessed"},
    "decision": {"propose": "ready_for_apply_confirmation", "revise": "revision_required", "reject": "rejected"},
    "next_step": {"clarify": "ask_clarification", "consult": "consult_specialists", "prepare": "draft_document_change_proposal"},
}
_CHANGE = {"create": "create_artifact", "patch": "patch_artifact_text", "replace": "replace_artifact_text", "rename": "rename_artifact", "delete": "delete_artifact"}
_DRAFTING = {"prepare_artifact": "draft_document_change_proposal", "prepare_consultation": "draft_specialist_consultation_proposal"}
_UPDATES = {"start": "start_request", "continue": "continue_request", "cancel": "cancel_request"}
_LEAD_TYPES = {"answer": "answer", "question": "clarification_question", "critique": "critique", "recommendation": "proposal_drafting_recommendation"}
_FAMILIES = {
    "lead-response-v7": "lead",
    "lead-response-v6": "lead",
    "specialist-assignment-response-v4": "specialist_assignment_result",
    "specialist-assignment-response-v3": "specialist_assignment_result",
    "consultation-summary-v4": "consultation_summary",
    "artifact-review-summary-v4": "artifact_review_summary",
    "lead-response-v8": "lead",
    "lead-response-v9": "lead",
    "specialist-assignment-response-v5": "specialist_assignment_result",
    "premise-analysis-response-v1": "premise_analysis_result",
    "premise-decision-summary-v1": "premise_decision_summary",
}


def result(value, contract_id):
    """Write independently specified wire names from a domain scenario result."""
    family = _FAMILIES.get(contract_id)
    if family is None or not isinstance(value, dict):
        return copy.deepcopy(value)

    def project(item, shape=""):
        if isinstance(item, list):
            return [project(child, shape) for child in item]
        if not isinstance(item, dict):
            return item
        output = {}
        for key, child in item.items():
            name = _FIELDS.get(key, key)
            if key == "task" and shape == "authoring_scope" and isinstance(child, str):
                task_id, revision = child.rsplit("@", 1)
                output["task_reference"] = {"task_id": task_id, "task_revision": int(revision)}
                continue
            if key == "kind" and shape == "operations":
                name, child = "change_type", _CHANGE.get(child, child)
            elif key == "kind" and shape == "recommended_action":
                name, child = "action_type", _DRAFTING.get(child, child)
            elif key == "action" and shape == "objective_intent":
                name, child = "update_type", _UPDATES.get(child, child)
            elif isinstance(child, str):
                child = _VALUES.get(key, {}).get(child, child)
            output[name] = copy.deepcopy(child) if key == "authoring_assessment" else project(child, key)
        return output

    output = project(value)
    if family == "lead":
        kind = output.pop("kind", "answer")
        response_type = ("artifact_change_proposal" if "artifact_change_proposal" in output else "specialist_consultation_proposal") if kind == "proposal" else _LEAD_TYPES.get(kind, kind)
    else:
        response_type = family
    return {"response_type": response_type, **output}


def answer(value, contract_id):
    return json.dumps({"agent_action": {"action_type": "submit_result", "task_result": result(value, contract_id)}}, ensure_ascii=False, separators=(",", ":"))


def contract_id(payload):
    return payload["response_contract"]["result_contract_id"]


def scenario_input(value):
    """View runtime fields as domain scenario names; string content stays literal."""
    reverse = {new: old for old, new in _FIELDS.items()}
    reverse.update({"workflow_type": "workflow", "user_request_text": "command", "available_writing_tasks": "authoring_tasks", "available_input_sources": "input_aliases", "document_change_requests": "artifact_objectives", "confirmed_proposal_drafting_context": "structured_recommendation_execution", "input_source_id": "input_id", "confirmed_proposal_drafting_constraints": "preparation", "current_writing_task_scope": "current_scope", "input_source_type": "kind", "decision_type": "kind"})
    def visit(item, shape=""):
        if isinstance(item, list):
            return [visit(child, shape) for child in item]
        if not isinstance(item, dict):
            return item
        output = {}
        for key, child in item.items():
            name = reverse.get(key, key)
            if shape in {"input_aliases", "inputs", "selected_inputs"}:
                name = {"content_text": "text", "source_locator": "locator"}.get(key, name)
            if shape in {"assignment", "assignments"} and key == "assignment_round_number":
                name = "round"
            if key == "input_source_type":
                child = {"story_document": "writing_artifact", "agent_work_history": "agent_state"}.get(child, child)
            if key == "task_reference" and isinstance(child, dict):
                output.update(child)
                continue
            if isinstance(child, str):
                child = {new: old for old, new in _VALUES.get(name, {}).items()}.get(child, child)
                if name == "workflow":
                    child = {"lead_user_turn": "lead_front_channel", "specialist_task": "specialist_assignment", "consultation_summary": "lead_synthesis", "specialist_consultation": "lead_specialist_consultation", "specialist_advice": "specialist_advisory", "artifact_change_review": "artifact_mutation"}.get(child, child)
            output[name] = visit(child, name)
        return output
    return visit(value)


def response(text, result_contract):
    """Serialize a fake's domain action; malformed JSON and unknown fields survive."""
    def unique_pairs(pairs):
        value = dict(pairs)
        if len(value) != len(pairs):
            raise ValueError("duplicate JSON property")
        return value
    try:
        body = json.loads(text, object_pairs_hook=unique_pairs)
    except (TypeError, ValueError):
        return text
    if not isinstance(body, dict):
        return text
    action = body.pop("action", body.get("agent_action"))
    if not isinstance(action, dict):
        return text
    action.pop("used_handles", None)
    action_type = action.pop("kind", action.get("action_type"))
    action["action_type"] = {"search": "search_context", "read": "read_context", "answer": "submit_result"}.get(action_type, action_type)
    for old, new in (("result", "task_result"), ("handles", "read_handles"), ("path_prefix", "artifact_directory_prefix")):
        if old in action:
            action[new] = action.pop(old)
    for query in action.get("queries", []) if isinstance(action.get("queries", []), list) else []:
        if isinstance(query, dict):
            if "text" in query:
                query["search_text"] = query.pop("text")
            if "path_prefix" in query:
                query["artifact_directory_prefix"] = query.pop("path_prefix")
            if "source_kinds" in query:
                query["source_types"] = [{"writing_artifact": "story_document", "conversation": "conversation_history", "active_debate": "current_consensus_run", "own_agent_state": "agent_work_history"}.get(kind, kind) for kind in query.pop("source_kinds")]
    value = action.get("task_result")
    if action["action_type"] == "submit_result" and isinstance(value, dict) and "response_type" not in value:
        action["task_result"] = result(value, result_contract)
    body["agent_action"] = action
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def direct_response(text, payload):
    """Encode deterministic direct-call domain scenarios without runtime helpers."""
    def unique_pairs(pairs):
        value = dict(pairs)
        if len(value) != len(pairs):
            raise ValueError("duplicate JSON property")
        return value
    try:
        value = json.loads(text, object_pairs_hook=unique_pairs)
    except (TypeError, ValueError):
        return text
    if not isinstance(value, dict):
        return text
    if not isinstance(value, dict) or "response_type" in value or "agent_action" in value:
        return text
    phase = payload.get("consensus_phase")
    if payload.get("request_type") == "answer_with_conversation_history" and isinstance(value.get("action"), dict):
        action = value["action"].copy()
        kind = action.pop("kind", None)
        action["action_type"] = {"search_conversations": "search_conversation_history", "read_history": "read_conversation_history", "answer": "submit_result"}.get(kind, kind)
        for old, new in (("query", "search_query"), ("start_sequence", "start_sequence_number"), ("end_sequence", "end_sequence_number")):
            if old in action:
                action[new] = action.pop(old)
        if kind == "answer":
            action = {"action_type": "submit_result", "task_result": {"response_type": "conversation_answer", "response_text": action.pop("message", None), **{key: item for key, item in action.items() if key != "action_type"}}}
        value = {"agent_action": action, **{key: item for key, item in value.items() if key != "action"}}
    elif "response_contract" in payload:
        return text
    elif phase is not None or "conversation_id" in payload:
        value = value.copy()
        value["response_type"] = {"draft_candidate": "candidate_draft", "review_candidate": "candidate_review", "revise_candidate": "candidate_revision", "synthesize_reviews": "consensus_next_step", "confirm_reviewed_proposal": "reviewed_proposal_confirmation"}.get(phase, "conversation_reply")
        if "message" in value:
            value["response_text"] = value.pop("message")
        if "verdict" in value:
            verdict = value.pop("verdict")
            value["candidate_review_status"] = {"approve": "no_conflicts_found", "conflict": "conflicts_found"}.get(verdict, verdict)
        if "next" in value:
            next_step = value.pop("next")
            value["next_consensus_step"] = {"repair": "revise_candidate", "finalization": "finalize_reviewed_proposal", "review_completion": "complete_review", "deadlock": "report_deadlock"}.get(next_step, next_step)
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def assert_guidance(test, payload, schema):
    """Check independently observable guidance, not the runtime's catalog helper."""
    test.assertEqual(payload["protocol_version"], "agent-provider-wire-v10")
    guide = payload["contract_guidance"]
    test.assertEqual(set(guide), {"input_fields", "response_rules"})
    for name in payload:
        if name != "contract_guidance":
            test.assertTrue(guide["input_fields"]["$." + name].strip())
    test.assertIn("schema", " ".join(guide["response_rules"]).lower())
    assert_schema_descriptions(test, schema)


def assert_schema_descriptions(test, schema):
    """Inspect schema positions independently; property names are caller data."""
    def visit(node, required=False):
        if not isinstance(node, Mapping):
            return
        if required:
            test.assertIsInstance(node.get("description"), str)
            test.assertTrue(node["description"].strip())
        for keyword in ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas"):
            for child in node.get(keyword, {}).values():
                visit(child)
        for keyword in ("items", "additionalProperties", "contains", "not", "if", "then", "else", "propertyNames"):
            visit(node.get(keyword))
        for keyword in ("anyOf", "allOf", "oneOf", "prefixItems"):
            for child in node.get(keyword, ()):
                visit(child, keyword == "anyOf")
    visit(schema, True)


def capture_sample(call, name=None):
    """Optional synthetic fixture evidence, enabled only for an assigned test run."""
    import os
    from pathlib import Path
    directory = os.environ.get("STORY_WIRE_TEST_CAPTURE_DIR")
    if directory is None:
        return
    payload = call["payload"] if isinstance(call, Mapping) else call.payload
    schema = call["schema"] if isinstance(call, Mapping) else call.schema
    name = name or payload.get("response_contract", {}).get("result_contract_id")
    if name not in {"lead-response-v6", "specialist-assignment-response-v3", "consultation-summary-v4", "artifact-review-summary-v4", "direct-conversation", "lead-response-v7", "specialist-assignment-response-v4"}:
        return
    destination = Path(directory) / (name + ".json")
    try:
        with destination.open("x", encoding="utf-8") as stream:
            json.dump({"payload": payload, "schema": schema}, stream, ensure_ascii=False, default=dict)
    except FileExistsError:
        pass

def direct_story_context(value):
    """Independent expected wire view of typed, public story-session context."""
    if isinstance(value, list):
        return [direct_story_context(item) for item in value]
    if not isinstance(value, dict):
        return value
    names = {"scope": "context_scope", "mode": "context_mode", "kind": "outcome_type" if "debate_id" in value else "message_type", "content": "content_text", "sequence": "sequence_number", "phase": "consensus_phase", "round": "consensus_round_number", "revision": "candidate_revision_id"}
    phases = {"draft": "draft_candidate", "audit": "review_candidate", "synthesize": "synthesize_reviews", "propose_revision": "revise_candidate", "finalize": "confirm_reviewed_proposal"}
    return {names.get(key, key): phases.get(item, item) if key == "phase" else direct_story_context(item) for key, item in value.items()}

def retrieval_observation(frame):
    """Encode known typed observation facts without reading runtime wire helpers."""
    begin, body, end = frame.split("\n", 2)
    value = json.loads(body)
    names = {"kind": "observation_type", "action_index": "retrieval_action_number", "hits": "search_results", "records": "history_records", "details": "readable_fields", "snippet": "snippet_text", "sequence": "sequence_number", "key": "field_key", "text": "field_text"}
    def visit(item, slot=""):
        if isinstance(item, list):
            return [visit(child, slot) for child in item]
        if not isinstance(item, dict):
            return item
        result = {}
        for key, child in item.items():
            name = "record_type" if key == "kind" and slot == "records" else names.get(key, key)
            if key == "kind" and not slot:
                child = {"search_conversations": "conversation_search_results", "read_history": "conversation_history_records"}[child]
            result[name] = visit(child, key)
        return result
    return begin + "\n" + json.dumps(visit(value), ensure_ascii=False, separators=(",", ":")) + "\n" + end

def direct_reply(value, phase):
    if phase == "conversation":
        return direct_response(json.dumps(value), {"conversation_id": "fixture"})
    phases = {"draft": "draft_candidate", "audit": "review_candidate", "synthesize": "synthesize_reviews", "propose_revision": "revise_candidate", "finalize": "confirm_reviewed_proposal"}
    return direct_response(json.dumps(value, ensure_ascii=False), {"consensus_phase": phases[phase]})

def artifact_candidate(value):
    """Independent named encoding for the immutable candidate fixture records."""
    names = {"change_set": "artifact_change_set", "operations": "artifact_changes",
             "operation": "change_type", "kind": "artifact_type", "path": "artifact_path",
             "source_path": "source_artifact_path", "destination_path": "destination_artifact_path",
             "content": "content_text", "base_digest": "base_content_digest",
             "projected_digest": "proposed_content_digest", "hunks": "text_patches",
             "old_text": "expected_content_text", "new_text": "new_content_text",
             "added_edges": "added_document_links", "removed_edges": "removed_document_links",
             "retained_edges": "retained_document_links", "projected_dangling_edges": "unresolved_document_links",
             "review_facts": "required_review_reasons", "domain": "review_domain",
             "reason": "review_reason", "operation_index": "artifact_change_index",
             "target_path": "target_artifact_path", "fragment": "target_fragment",
             "relationship_kind": "relationship_type"}
    reasons = {"touched_core": "core_document_changed", "manuscript_operation": "manuscript_changed",
               "attached_core_evidence": "attached_core_document_evidence", "relationship_core_evidence": "linked_core_document_evidence"}
    if isinstance(value, (tuple, list)): return [artifact_candidate(item) for item in value]
    if not isinstance(value, Mapping): return value
    return {names.get(key, key): _CHANGE[item] if key == "operation" else reasons[item]
            if key == "reason" else artifact_candidate(item) for key, item in value.items()}
