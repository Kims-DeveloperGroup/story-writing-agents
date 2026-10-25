"""Independent wire-v10 writers for existing typed-domain provider scenarios."""
from __future__ import annotations

import copy
from collections.abc import Mapping
import json

_FIELDS = {'message': 'response_text', 'authoring_scope': 'writing_task_scope',
    'activity': 'activity_type', 'intent': 'writing_intent', 'domains': 'specialist_domains',
    'target_paths': 'target_artifact_paths', 'proposal': 'specialist_consultation_proposal',
    'artifact_intent': 'artifact_change_proposal', 'next_intent': 'follow_up_consultation',
    'instruction': 'task_instructions', 'input_ids': 'input_source_ids', 'mode': 'assignment_type',
    'purpose': 'consultation_purpose', 'assessment': 'assignment_status',
    'decision': 'review_outcome', 'operations': 'artifact_changes',
    'suggested_path': 'proposed_artifact_path',
    'suggested_destination': 'proposed_destination_path', 'new_text': 'new_content_text',
    'replacement_text': 'replacement_content_text',
    'recommended_action': 'proposal_drafting_request',
    'objective_intent': 'document_change_request_update',
    'objective_id': 'document_change_request_id', 'next_step': 'requested_workflow_step'}
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
_FAMILIES = {'lead-response-v7': 'lead', 'lead-response-v6': 'lead',
    'specialist-assignment-response-v4': 'specialist_assignment_result',
    'specialist-assignment-response-v3': 'specialist_assignment_result',
    'consultation-summary-v4': 'consultation_summary',
    'artifact-review-summary-v4': 'artifact_review_summary', 'lead-response-v8': 'lead',
    'lead-response-v9': 'lead', 'specialist-assignment-response-v5': 'specialist_assignment_result',
    'premise-analysis-response-v1': 'premise_analysis_result',
    'premise-decision-summary-v1': 'premise_decision_summary'}


def result(value, contract_id, *, native_types=None):
    """Write independently specified wire names from a domain scenario result."""
    family = "lead" if native_types is not None else _FAMILIES.get(contract_id)
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
            if native_types is not None and key != name and name in item:
                output[key] = copy.deepcopy(child)
                continue
            if key == "kind" and not shape and native_types is not None:
                if "response_type" not in item:
                    name = "response_type"
                    if isinstance(child, str):
                        child = native_types.get(child, child)
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
    if native_types is not None:
        return output
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


def indexed_response(action, *, outer_schema_id, inner_schema_id, contract=None):
    """Write explicit fixture bindings without choosing or repairing action values."""
    value = copy.deepcopy(action)
    if "schema_id" in value:
        raise ValueError("Supply the outer ID through its explicit fixture binding")
    value["schema_id"] = outer_schema_id
    if inner_schema_id is not None and type(value.get("task_result")) is dict:
        if "schema_id" in value["task_result"]:
            raise ValueError("Supply the inner ID through its explicit fixture binding")
        if contract is not None:
            owner = next(row for row in contract.semantic_index.contracts if row.schema_id == inner_schema_id)
            value["task_result"] = fixture_variant_value(owner.rule_graph.schema_value(), value["task_result"])
        value["task_result"]["schema_id"] = inner_schema_id
    return json.dumps({"agent_action": value}, ensure_ascii=False, separators=(",", ":"))


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
    test.assertEqual(payload["protocol_version"], "agent-provider-wire-v11")
    guide = payload["contract_guidance"]
    test.assertEqual(set(guide), {"input_fields", "response_rules"})
    for name in payload:
        if name != "contract_guidance":
            test.assertTrue(guide["input_fields"]["$." + name].strip())
    test.assertIn("schema", " ".join(guide["response_rules"]).lower())
    assert_schema_descriptions(test, schema)


def assert_schema_descriptions(test, schema):
    """Inspect schema positions independently; property names are caller data."""
    def visit(node, required=False, refs=()):
        if not isinstance(node, Mapping):
            return
        if "$ref" in node:
            reference = node["$ref"]
            test.assertIsInstance(reference, str)
            test.assertTrue(reference.startswith("#/"))
            test.assertNotIn(reference, refs)
            target = schema
            for part in reference[2:].split("/"):
                test.assertNotRegex(part, r"~(?![01])")
                name = part.replace("~1", "/").replace("~0", "~")
                test.assertIsInstance(target, Mapping)
                test.assertIn(name, target)
                target = target[name]
            test.assertIsInstance(target, Mapping)
            visit(target, required and "description" not in node, (*refs, reference))
            required = required and "description" in node
        if required:
            test.assertIsInstance(node.get("description"), str)
            test.assertTrue(node["description"].strip())
        for keyword in ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas"):
            for child in node.get(keyword, {}).values():
                visit(child, refs=refs)
        for keyword in ("items", "additionalProperties", "contains", "not", "if", "then", "else", "propertyNames"):
            visit(node.get(keyword), refs=refs)
        for keyword in ("anyOf", "allOf", "oneOf", "prefixItems"):
            for child in node.get(keyword, ()):
                visit(child, keyword == "anyOf", refs)
    visit(schema, True)


def capture_sample(call, name=None, *, witness=None, finalizers=None):
    """Optional synthetic fixture evidence, enabled only for an assigned test run."""
    if witness is not None:
        from story_writing_agents.agent_context import canonical_provider_json
        from story_writing_agents.antigravity_adapter import AntigravityAdapter
        assert name in ('conclude', 'ready', 'prepared')
        bound = call.contract
        assert [item.calls for item in finalizers] == [[('accepted', None)]]
        assert not bound.structural_findings(witness)
        assert bound.decode_response(witness).belongs_to(bound)
        schemas = [canonical_provider_json(call.schema), canonical_provider_json(
            AntigravityAdapter.native_output_schema(call.schema))]
        good = len(witness.encode('utf-8')) <= 65536 and all(len(x) <= 524288 for x in schemas)
        profile = {'profile': name, 'overflow': not good, 'native_admitted': False,
            'semantic_members': len(bound.semantic_index.contracts),
            'direct_members': sum(
            x.owner.variant.direct_delivery for x in bound.semantic_index.contracts),
            'offered_members': len(bound.offered_semantic_schema_ids),
            'selectable_members': len(bound.selectable_semantic_schema_ids),
            'remaining_physical_calls': bound.offers.remaining_physical_calls,
            'schema_utf8_bytes': [len(x) for x in schemas]}
        if good:
            profile.update(witness=witness, codex_cli_schema_file_json=schemas[0].decode('utf-8'),
                antigravity_response_schema_json=schemas[1].decode('utf-8'))
        print('SWA_COOFFER_V1 ' + json.dumps(profile, ensure_ascii=False))
        return good
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


def fixture_schema_node(schema, node):
    """Follow an offered schema pointer without validating a fixture payload."""
    while '$ref' in node:
        pointer = node['$ref']
        if not pointer.startswith('#/'):
            raise ValueError('Fixture bindings require local offered schema references')
        node = schema
        for part in pointer[2:].split('/'):
            node = node[part.replace('~1', '/').replace('~0', '~')]
    return node


def _fixture_partition_matches(schema, value):
    """Match finite action and scope selectors within one complete detail arm."""
    def choices(node):
        node = fixture_schema_node(schema, node)
        if 'anyOf' in node:
            return tuple(choice for branch in node['anyOf'] for choice in choices(branch))
        return (node,)
    def literals(node):
        return tuple(item for choice in choices(node) for item in choice.get('enum', ()))
    details = value.get('response_details', {}) if isinstance(value, Mapping) else {}
    request = details.get('proposal_drafting_request') if isinstance(details, Mapping) else None
    scope = details.get('task_scope', {}) if isinstance(details, Mapping) else {}
    if not isinstance(scope, Mapping):
        return False
    root = fixture_schema_node(schema, schema)
    for detail in choices(root['properties']['response_details']):
        if isinstance(request, Mapping) and isinstance(request.get('action_type'), str) and not any(
            request['action_type'] in literals(option.get('properties', {}).get('action_type', {}))
            for option in choices(detail.get('properties', {}).get('proposal_drafting_request', {}))):
            continue
        scope_node = detail.get('properties', {}).get('task_scope')
        if scope_node is None:
            continue
        for declared in choices(scope_node):
            props = declared.get('properties', {})
            if any(name in scope and (name not in props or scope[name] not in literals(props[name]))
                   for name in ('intent', 'activity')):
                continue
            if any(name in scope and (not isinstance(scope[name], Mapping) or set(scope[name]) !=
                   set(fixture_schema_node(schema, props.get(name, {})).get('properties', {})))
                   for name in ('domains', 'roles')):
                continue
            roles = scope.get('roles', {})
            role_nodes = fixture_schema_node(schema, props.get('roles', {})).get('properties', {})
            if any(not isinstance(row, Mapping) or domain not in role_nodes or ('role' in row and
                   row['role'] not in literals(fixture_schema_node(schema, role_nodes[domain]).get(
                       'properties', {}).get('role', {}))) for domain, row in roles.items()):
                continue
            return True
    return False


def fixture_variant_value(schema, value):
    """Copy one offered routing literal during construction, never during mutation replay."""
    result = copy.deepcopy(value)
    details = result.get('response_details')
    if not isinstance(details, dict) or 'variant_id' in details:
        return result

    def choices(node):
        node = fixture_schema_node(schema, node)
        return tuple(arm for child in node['anyOf'] for arm in choices(child)) if 'anyOf' in node else (node,)

    def matches(node, supplied):
        node = fixture_schema_node(schema, node)
        if 'anyOf' in node:
            return any(matches(arm, supplied) for arm in node['anyOf'])
        if 'enum' in node and supplied not in node['enum']:
            return False
        if isinstance(supplied, dict) and isinstance(node.get('properties'), dict):
            props = node['properties']
            if node.get('x-swa-property-policy') == 'closed-map' and set(supplied) != set(props):
                return False
            return all(matches(props[key], item) for key, item in supplied.items() if key in props)
        if isinstance(supplied, list) and isinstance(node.get('items'), dict):
            return all(matches(node['items'], item) for item in supplied)
        return True

    root = fixture_schema_node(schema, schema)
    arms = tuple(arm for arm in choices(root.get('properties', {}).get('response_details', {}))
                 if 'variant_id' in arm.get('properties', {}))
    if arms:
        # Malformed fixtures keep their authored values; only the offered route is supplied.
        arm = next((arm for arm in arms if matches(arm, details)), arms[0])
        literals = fixture_schema_node(schema, arm['properties']['variant_id']).get('enum', ())
        if len(literals) != 1 or type(literals[0]) is not str:
            raise ValueError('Fixture variant requires one offered routing literal')
        details['variant_id'] = literals[0]
    return result


def fixture_semantic_member(request, *, response_type, family, branch_key=None, value=None, offered_only=False):
    """Resolve the caller's original full family before physical offer filtering."""
    families = {}
    for member in request.contract.semantic_index.contracts:
        graph = member.rule_graph.schema_value()
        root = fixture_schema_node(graph, graph)
        declared = fixture_schema_node(graph, root['properties']['response_type'])
        if response_type not in declared['enum']:
            continue
        key = member.owner.variant.branch_key
        original = key.partition(':partition:')[0]
        if branch_key is not None and branch_key not in (key, original):
            continue
        families.setdefault(original, []).append(member)
    if type(family) is not int or not 0 <= family < len(families):
        raise ValueError('The explicitly intended fixture family is not offered')
    choices = list(families.values())[family]
    if offered_only:
        choices = [member for member in choices
                   if member.schema_id in request.contract.offered_semantic_schema_ids]
        if not choices:
            raise ValueError('The explicitly intended fixture family is not offered')
    if len(choices) > 1 and value is not None:
        for member in choices:
            if _fixture_partition_matches(member.rule_graph.schema_value(), value):
                return member
    return choices[0]


def offered_fixture_bindings(
    request, *, action_type, response_type, family, branch_key=None, value=None
):
    """Pair an explicit original family only when that current member is offered."""
    if action_type != 'submit_result' and response_type is not None:
        raise ValueError('Context-action fixtures have no semantic result family')
    bound = request.contract
    pairs = dict(bound.outer_inner_schema_ids)
    intended = None if action_type != 'submit_result' else fixture_semantic_member(
        request, response_type=response_type, family=family, branch_key=branch_key, value=value, offered_only=True)
    offered = []
    for member in bound.action_index.contracts:
        if member.rule_graph.literal_at(('action_type',)) != action_type:
            continue
        inner = pairs[member.schema_id]
        if intended is None or inner == intended.schema_id:
            offered.append((member.schema_id, inner))
    ordinal = family if intended is None else 0
    if type(ordinal) is not int or not 0 <= ordinal < len(offered):
        raise ValueError('The explicitly intended fixture family is not offered')
    return offered[ordinal]


def selected_fixture_calls(builder, *args, reuse_response=False, **kwargs):
    """Queue both paid callbacks explicitly; never submit or choose for the provider."""
    if reuse_response:
        respond = builder(*args, **kwargs)
        return (lambda call, provider: respond(call, provider, select=True)), respond
    return builder(*args, select=True, **kwargs), builder(*args, **kwargs)


def result_selection(request, *, response_type, family, branch_key=None, value=None):
    """Return one explicit paid selection response; never invoke or queue a provider."""
    member = fixture_semantic_member(request, response_type=response_type, family=family,
        branch_key=branch_key, value=value)
    if member.schema_id not in request.contract.selectable_semantic_schema_ids:
        raise ValueError('The explicitly intended fixture member is not selectable')
    outer, inner = offered_fixture_bindings(request, action_type='select_result',
        response_type=None, family=0)
    return indexed_response({'action_type': 'select_result', 'result_schema_id': member.schema_id},
        outer_schema_id=outer, inner_schema_id=inner)



def modeled_selection_binding(bound, *, response_type, family, branch_key=None, value=None):
    """PURE graph model: one selector and paid successor, never provider/ledger evidence."""
    from dataclasses import replace
    from types import SimpleNamespace
    request = SimpleNamespace(contract=bound)
    member = fixture_semantic_member(request, response_type=response_type, family=family,
        branch_key=branch_key, value=value)
    if member.schema_id in bound.offered_semantic_schema_ids:
        return bound
    response = result_selection(request, response_type=response_type, family=family,
        branch_key=branch_key, value=value)
    lineage = bound.selection_lineage(bound.decode_response(response))
    offers = bound.offers
    successor = replace(offers, call_id=offers.call_id + '-modeled-selection',
        action_index=offers.action_index + 1,
        correlation=replace(offers.correlation, action_index=offers.action_index + 1),
        remaining_physical_calls=offers.remaining_physical_calls - 1,
        action_limits=tuple((name, count - 1 if name == 'nonterminal_actions' else count)
            for name, count in offers.action_limits))
    return bound.factory.for_offer(successor, lineage)



def modeled_fixture_response(bound, value, *, family, branch_key=None):
    """PURE bound/frame model; no provider submission, ledger charge or authority."""
    from types import SimpleNamespace
    selected = modeled_selection_binding(bound, response_type=value['response_type'],
        family=family, branch_key=branch_key, value=value)
    frame = lead_fixture_response(SimpleNamespace(contract=selected), value,
        family=family, branch_key=branch_key)
    return selected, frame



def modeled_fixture_members(bound):
    """PURE owner-to-current-ID graph inventory; keys never authorize a response."""
    rows = []
    for original in bound.semantic_index.contracts:
        graph = original.rule_graph.schema_value()
        root = fixture_schema_node(graph, graph)
        kind = fixture_schema_node(graph, root['properties']['response_type'])['enum'][0]
        current = modeled_selection_binding(bound, response_type=kind, family=0,
            branch_key=original.owner.variant.branch_key)
        matches = [member for member in current.semantic_index.contracts
                   if member.schema_id in current.offered_semantic_schema_ids
                   and member.owner is original.owner]
        if len(matches) != 1:
            raise ValueError('Modeled physical scope must retain exactly one original owner')
        rows.append((original.schema_id, current, matches[0].schema_id))
    if len({current_id for _, _, current_id in rows}) != len(rows):
        raise ValueError('Modeled owner-to-current-ID inventory must be injective')
    return tuple(rows)

def finite_fixture_selection(choices, selected):
    """Encode one explicit valid selection in issued order, including false leaves."""
    choices, selected = tuple(choices), tuple(selected)
    if len(set(choices)) != len(choices) or len(set(selected)) != len(selected):
        raise ValueError('Finite fixture selections must not conceal duplicates')
    if not set(selected) <= set(choices):
        raise ValueError('Finite fixture selections must not conceal unoffered identities')
    def tree(names):
        if not names:
            return {}
        if len(names) == 1:
            return {names[0]: names[0] in selected}
        middle = len(names) // 2
        return {'left': tree(names[:middle]), 'right': tree(names[middle:])}
    return tree(choices)


def lead_fixture_assessment(value, *, source_choices):
    """Encode supplied assessment rows; preserve omissions and malformed values."""
    encoded = copy.deepcopy(value)
    if type(encoded) is not dict:
        return encoded
    rows = [encoded[name] for name in ('direction', 'creative_scope')
            if type(encoded.get(name)) is dict]
    for name in ('foundations', 'decisions'):
        supplied = encoded.get(name)
        if type(supplied) in (list, tuple):
            rows.extend(row for row in supplied if type(row) is dict)
    for row in rows:
        selected = row.get('source_ids')
        if (type(selected) in (list, tuple)
                and all(type(item) is str for item in selected)
                and len(set(selected)) == len(selected)
                and set(selected).issubset(source_choices)):
            row['source_ids'] = finite_fixture_selection(source_choices, tuple(selected))
    return encoded


def lead_fixture_scope_record(record, *, fixed_targets=False, fixed_dependencies=False):
    """Encode only fields present in the caller's explicit domain scope declaration."""
    if type(record) is not dict:
        return copy.deepcopy(record)
    names = {'activity': 'activity_type', 'intent': 'writing_intent',
             'target_paths': 'target_artifact_paths', 'domains': 'specialist_domains',
             'contributions': 'contributions', 'dependency_slots': 'dependency_slots'}
    native = {names.get(name, name): copy.deepcopy(value) for name, value in record.items()
              if name not in ('interpretation', 'schema_version', 'task_id', 'task_revision')}
    if 'activity_type' in native:
        native['activity_type'] = {'workflow': 'workflow_guidance', 'draft': 'draft_writing',
            'review': 'review_writing', 'revise': 'revise_writing'}.get(
                native['activity_type'], native['activity_type'])
    task = {name: copy.deepcopy(record[name]) for name in ('task_id', 'task_revision')
            if name in record}
    if task:
        native['task_reference'] = task
    return lead_fixture_scope(native, fixed_targets=fixed_targets, fixed_dependencies=fixed_dependencies)


def lead_fixture_scope(scope, *, fixed_targets=False, fixed_dependencies=False):
    """Encode supplied valid cells; preserve omitted fields and malformed values for rejection."""
    if type(scope) is not dict:
        return copy.deepcopy(scope)
    scope = copy.deepcopy(scope)
    workflow = scope.get('activity_type', scope.get('activity')) == 'workflow_guidance'
    domains = scope.get('specialist_domains')
    if type(domains) in (list, tuple):
        if len(set(domains)) != len(domains):
            raise ValueError('Use a raw frame for intentionally duplicate scope')
        scope['specialist_domains'] = {domain: True for domain in domains}
    rows = scope.get('contributions')
    if not workflow and type(rows) in (list, tuple) and all((type(row) is dict for row in rows)):
        if len({row['domain'] for row in rows}) != len(rows):
            raise ValueError('Use a raw frame for intentionally duplicate contribution')
        scope['contributions'] = {row['domain']: row for row in rows}
    targets = scope.get('target_artifact_paths')
    if fixed_targets and type(targets) in (list, tuple):
        if len(set(targets)) != len(targets):
            raise ValueError('Use a raw frame for intentionally duplicate target')
        scope['target_artifact_paths'] = {target: True for target in targets}
    slots = scope.get('dependency_slots')
    if (not workflow and type(slots) in (list, tuple)
            and all(type(slot) is dict for slot in slots)):
        if fixed_dependencies:
            scope['dependency_slots'] = {slot['slot_id']: {**slot, 'dependent_domains': {
                domain: True for domain in slot['dependent_domains']}} for slot in slots}
        elif slots and type(rows) in (list, tuple):
            drafts = tuple(row['domain'] for row in rows if row['role'] == 'draft_owner')
            for item in slots:
                selected = tuple(item['dependent_domains'])
                finite_fixture_selection(drafts, selected)
                item['dependent_domains'] = dict.fromkeys(selected, True)
    names = {'activity_type': 'activity', 'writing_intent': 'intent',
             'specialist_domains': 'domains', 'target_artifact_paths': 'paths',
             'contributions': 'roles', 'dependency_slots': 'slots', 'task_reference': 'task'}
    # A deliberate mixed old/new input remains mixed and invalid; collisions are never discarded.
    compact = {name if names.get(name, name) in scope and names.get(name, name) != name else
               names.get(name, name): value for name, value in scope.items()}
    if type(compact.get('task')) is dict:
        task_names = {'task_id': 'id', 'task_revision': 'revision'}
        task = compact['task']
        compact['task'] = {name if task_names.get(name, name) in task and
                           task_names.get(name, name) != name else task_names.get(name, name): value
                           for name, value in task.items()}
    if 'slots' in compact and not workflow:
        def slot(value):
            if type(value) is not dict:
                return value
            names = {'slot_id': 'id', 'dependent_domains': 'domains'}
            return {name if names.get(name, name) in value and names.get(name, name) != name else
                    names.get(name, name): item for name, item in value.items()}
        rows = compact['slots']
        if type(rows) is dict:
            compact['slots'] = {name: slot(value) for name, value in rows.items()}
        elif type(rows) in (list, tuple):
            compact['slots'] = [slot(value) for value in rows]
    return compact


def lead_fixture_bound_scope(request, scope, *, record=False):
    """Encode supplied scope fields using the frozen declaration presence only."""
    constraints = request.contract.factory.constraints
    writer = lead_fixture_scope_record if record else lead_fixture_scope
    return writer(
        scope, fixed_targets=(constraints.scope is not None
                              or constraints.adoption_intent is not None),
        fixed_dependencies=constraints.scope is not None)


def lead_fixture_result(value):
    """Group explicitly supplied wire fields; no defaults, binding or branch inference."""
    value = copy.deepcopy(value)
    if type(value) is not dict:
        return value
    if 'response_details' in value:
        raise ValueError('Supply either grouped or ungrouped fixture fields explicitly')
    common = {'schema_id', 'response_type', 'response_text'}
    common.update(key for key in value if key.endswith('_result_type'))
    result = {key: child for key, child in value.items() if key in common}
    result['response_details'] = {key: child for key, child in value.items() if key not in common}
    return result


def author_positive_fixture_review_bases(rows, *, candidate_choices):
    """Author the selected-input/instruction basis for explicitly opted positive fixtures."""
    authored = copy.deepcopy(rows)
    for row in authored:
        if 'review_basis' in row:
            continue
        if row['assignment_type'] != 'review_content':
            row['review_basis'] = None
            continue
        selected = row['input_source_ids']
        criteria = row['task_instructions']
        if (type(selected) not in (list, tuple) or not selected
                or any(type(item) is not str for item in selected)
                or len(set(selected)) != len(selected)
                or any(item not in candidate_choices for item in selected)
                or type(criteria) is not str or not criteria.strip()):
            raise ValueError('positive review authoring requires eligible edges and criterion')
        row['review_basis'] = {
            'candidate_input_ids': list(selected), 'criteria': criteria}
    return authored


def lead_fixture_assignments(rows, *, input_choices, review_choices, anchors, recipients):
    """Encode authored edges and basis without inventing a missing review value."""
    if (type(rows) not in (list, tuple)
            or any(type(row) is not dict or type(row.get('agent_id')) is not str for row in rows)):
        return {'assignments': copy.deepcopy(rows), 'required_input_recipients': copy.deepcopy(recipients)}
    if len({row['agent_id'] for row in rows}) != len(rows):
        raise ValueError('Use a raw frame for an intentionally duplicate assignment')
    assignments = {}
    for row in rows:
        identity = row['agent_id']
        native = {key: copy.deepcopy(value) for key, value in row.items()
                  if key not in ('agent_id', 'input_source_ids')}
        if 'review_basis' not in native:
            raise ValueError('fixture allocation requires an authored review_basis value')
        basis = native['review_basis']
        if type(basis) is dict:
            if set(basis) != {'candidate_input_ids', 'criteria'}:
                raise ValueError('fixture review basis requires its closed native semantic fields')
            native['review_basis'] = {
                'candidate_input_ids': finite_fixture_selection(
                    review_choices, tuple(basis['candidate_input_ids'])),
                'criteria': copy.deepcopy(basis['criteria']),
            }
        elif basis is not None:
            raise ValueError('fixture review basis must be an authored object or null')
        selected = row.get('input_source_ids')
        required = ([source for source, agent in recipients.items() if agent == identity]
                    if type(recipients) is dict else [])
        anchor = native.get('input_anchor_id', anchors.get(identity))
        if (type(selected) in (list, tuple) and all(type(item) is str for item in selected)
                and len(set(selected)) == len(selected) and anchor in selected
                and all(source in selected for source in required)):
            if 'input_anchor_id' not in native:
                native['input_anchor_id'] = copy.deepcopy(anchor)
            if 'optional_inputs' not in native:
                native['optional_inputs'] = {
                    source: source in selected
                    for source in dict.fromkeys((*input_choices, *selected))}
            else:
                native['input_source_ids'] = copy.deepcopy(selected)
        elif 'input_source_ids' in row:
            native['input_source_ids'] = copy.deepcopy(selected)
        assignments[identity] = native
    return {'assignments': assignments, 'required_input_recipients': copy.deepcopy(recipients)}


def lead_fixture_response(request, value, *, family, branch_key=None, response_type=None,
                          select=False):
    """Pair caller-authored current wire values with one explicitly intended issued family."""
    if select:
        return result_selection(request, response_type=(
            value['response_type'] if response_type is None else response_type),
            family=family, branch_key=branch_key, value=value)
    outer, inner = offered_fixture_bindings(
        request, action_type='submit_result',
        response_type=value['response_type'] if response_type is None else response_type,
        family=family, branch_key=branch_key, value=value)
    return indexed_response({'action_type': 'submit_result', 'task_result': value},
                            outer_schema_id=outer, inner_schema_id=inner, contract=request.contract)


def finite_fixture_read_selection(choices, selected):
    """Encode caller-supplied READ choices; no count or value repair."""
    if not choices:
        raise ValueError('READ fixtures require actual offered handles')
    return finite_fixture_selection(choices, selected)


def field_neighbor_cases(packet, property_policies, required_fields):
    """Describe each original mutation once; no case shares a changed payload."""
    details = ('agent_action', 'task_result', 'response_details')
    nullable = (details + ('recommended_option_key',), details + ('authoring_assessment',),
                details + ('authoring_assessment', 'direction'), details + ('authoring_assessment', 'creative_scope'),
                details + ('specialist_consultation_proposal', 'shared_premises', '*', 'selected_premise'))
    closed_literals = {'schema_id', 'variant_id', 'action_type', 'response_type', 'activity', 'intent', 'role',
        'assignment_type', 'update_type', 'change_type', 'status', 'idea_kind', 'book_scope_status',
        'book_unit_coverage', 'author_decision_coverage', 'criterion_judgment', 'author_fact_coverage',
        'document_path', 'criterion_id', 'objective_id', 'agent_id', 'input_anchor_id',
        'requested_workflow_step', 'document_change_request_id'}
    def nodes(value, path=()):
        yield path, value
        if type(value) is dict:
            for key, child in value.items(): yield from nodes(child, path + (key,))
        elif type(value) is list:
            for index, child in enumerate(value): yield from nodes(child, path + (index,))
    cases = []
    for path, value in nodes(packet):
        if type(value) is dict:
            for key in value:
                association = key == 'document_change_request_id' and path[-1:] == ('proposal_drafting_request',)
                optional = association or (not property_policies[path] and key not in required_fields[path])
                cases.append(((path, key, 'missing'), path + (key,), 'delete', None, optional, None))
            cases.append(((path, 'foreign'), path + ('foreign_branch_field',), 'set', None, False, path))
        if path:
            wrong = '' if type(value) in (dict, list) else {} if type(value) is str else 'wrong'
            for replacement in ((wrong, None) if value is not None else ('wrong', {})):
                cases.append(((path, type(replacement)), path, 'set', replacement,
                              replacement is None and tuple('*' if type(part) is int else part
                                                            for part in path) in nullable, None))
        if path and isinstance(value, str) and path[-1] in closed_literals:
            cases.append((path, path, 'set', 'unoffered-closed-enum-or-literal', False, None))
        if path and isinstance(value, str) and (path[-1] in ('path_prefix', 'artifact_directory_prefix')
                                               or path[-2:-1] == ('paths',)):
            cases.append(((path, 'forbidden final newline'), path, 'set', value + '\n', False, None))
        if path == details + ('task_scope', 'paths') and type(value) is dict:
            for key in value:
                cases.append(((path, 'path-key-newline'), path + (key,), 'rename', key + '\n', False, None))
    return tuple(cases)



def field_neighbor_value(packet, case):
    """Materialize one independent value, shared by the three validation passes."""
    _coordinate, path, operation, replacement, _native_valid, _foreign_path = case
    changed = copy.deepcopy(packet); node = changed
    for key in path[:-1]: node = node[key]
    if operation == 'delete': del node[path[-1]]
    elif operation == 'rename': node[replacement] = node.pop(path[-1])
    else: node[path[-1]] = copy.deepcopy(replacement)
    return changed


def fixture_property_policies(packet, schema):
    """Locate declared map policies in the valid packet without runtime-private tables."""
    policies, required, seen = {}, {}, set()
    def resolved(rule):
        refs = set()
        while '$ref' in rule:
            ref = rule['$ref']
            if ref in refs or not ref.startswith('#/'):
                raise AssertionError('fixture requires an acyclic local reference')
            refs.add(ref); rule = schema
            for part in ref[2:].split('/'): rule = rule[part.replace('~1', '/').replace('~0', '~')]
        return rule
    def literals_match(rule, value):
        rule = resolved(rule)
        if type(value) is dict:
            for key, child in rule.get('properties', {}).items():
                child = resolved(child)
                if key in value and 'enum' in child and value[key] not in child['enum']: return False
        return True
    def visit(rule, value, path):
        rule = resolved(rule)
        key = (id(rule), id(value), path)
        if key in seen: return
        seen.add(key)
        if 'anyOf' in rule:
            for alternative in rule['anyOf']:
                if literals_match(alternative, value): visit(alternative, value, path)
        if type(value) is dict and rule.get('type') == 'object':
            policy = rule.get('x-swa-property-policy') == 'closed-map'
            if path in policies and policies[path] != policy:
                raise AssertionError(('ambiguous fixture property policy', path))
            policies[path] = policy
            required.setdefault(path, set()).update(rule.get('required', ()))
            for name, child in value.items():
                if name in rule.get('properties', {}): visit(rule['properties'][name], child, path + (name,))
                elif type(rule.get('additionalProperties')) is dict:
                    visit(rule['additionalProperties'], child, path + (name,))
        elif type(value) is list and type(rule.get('items')) is dict:
            for index, child in enumerate(value): visit(rule['items'], child, path + (index,))
    visit(schema, packet, ())
    return policies, required
