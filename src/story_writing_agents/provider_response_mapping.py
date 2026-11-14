"""Provider response normalization and public-response recovery."""

from __future__ import annotations

import json
from copy import deepcopy

from .provider_message_catalog import (
    ACTION_TYPES,
    BUILTIN_RESULT_CONTRACT_IDS,
    DIRECT_NEXT,
    DIRECT_RESPONSE_TYPES,
    DIRECT_REVIEW,
    LEAD_RESPONSES,
    RESULT_FIELDS,
    RETRIEVAL_ACTIONS,
    SOURCE_TYPES,
    WireShapeError,
    result_enum_map as _enum_map,
)

def normalize_result(value: object, contract_id: str) -> object:
    """Restore internal domain field names after strict new-wire validation."""
    family = {"lead-response-v7": "lead-response-v6", "lead-response-v8": "lead-response-v6", "lead-response-v9": "lead-response-v6", "lead-discussion-response-v1": "lead-response-v6", "specialist-assignment-response-v4": "specialist-assignment-response-v3", "specialist-assignment-response-v5": "specialist-assignment-response-v3"}.get(contract_id, contract_id)
    if family not in BUILTIN_RESULT_CONTRACT_IDS:
        return value

    def visit(item: object, shape: str = "", root: bool = False) -> object:
        if shape in {"authoring_assessment", "core_document_assessment"}:
            return deepcopy(item)
        if isinstance(item, list):
            return [visit(child, shape) for child in item]
        if not isinstance(item, dict):
            return item
        result = {}
        reverse = {wire: old for old, wire in RESULT_FIELDS.items()}
        for name, child in item.items():
            if root and name == "response_type":
                expected_type = {"premise-analysis-response-v1": "premise_analysis_result", "premise-decision-summary-v1": "premise_decision_summary"}.get(family)
                if expected_type is not None and child != expected_type:
                    raise ValueError("unexpected premise response type")
                if family == "lead-exploration-response-v1":
                    if child != "lead_exploration":
                        raise ValueError("unexpected exploration response type")
                    result["kind"] = "exploration"
                if family == "lead-response-v6" and (
                    child != "answer" or "document_change_request_update" in item
                ):
                    result["kind"] = {**{v: k for k, v in LEAD_RESPONSES.items()}, "specialist_consultation_proposal": "proposal", "artifact_change_proposal": "proposal", "core_document_readiness_assessment": "answer"}[child]
                continue
            if name == "task_reference" and shape == "authoring_scope":
                result["task"] = f"{child['task_id']}@{child['task_revision']}"
                continue
            old = {"change_type": "kind", "action_type": "kind", "idea_kind": "kind", "update_type": "action"}.get(name, reverse.get(name, name))
            transformed = visit(child, old)
            enums = {wire: domain for domain, wire in _enum_map(old, shape).items()}
            result[old] = enums.get(transformed, transformed) if isinstance(transformed, str) else transformed
        return result

    return visit(value, root=True)


def normalize_context_action(value: object, *, finite_source_kinds: bool = False) -> dict:
    if type(value) is not dict or "agent_action" not in value:
        raise WireShapeError("$", "expected object containing exactly agent_action")
    action = value["agent_action"]
    if type(action) is not dict:
        raise WireShapeError("$", "expected an action object")
    selected = action.get("action_type")
    if type(selected) is not str or selected not in ACTION_TYPES.values():
        raise WireShapeError("$.action_type", "expected one of search_context, read_context, submit_result")
    kind = {v: k for k, v in ACTION_TYPES.items()}[selected]
    required = {"search": {"action_type", "queries"}, "read": {"action_type", "read_handles"}, "answer": {"action_type", "task_result"}}[kind]
    if not required.issubset(action):
        raise WireShapeError("$", "expected exact required action properties for " + selected)
    result = {"kind": kind}
    if kind == "search":
        queries = action["queries"]
        if not isinstance(queries, list):
            raise WireShapeError("$.queries", "expected a nonempty array of search query objects")
        result["queries"] = []
        for index, query in enumerate(queries):
            path = f"$.queries[{index}]"
            if type(query) is not dict or not {"search_text", "source_types"}.issubset(query):
                raise WireShapeError(path, "expected object containing exactly search_text, source_types")
            if finite_source_kinds and type(query["source_types"]) is dict:
                selected = query["source_types"]
                if (not selected or not set(selected).issubset(SOURCE_TYPES.values())
                        or any(value is not True for value in selected.values())):
                    raise WireShapeError(path + ".source_types", "expected exact offered source-kind selection")
                query = {**query, "source_types": [value for value in SOURCE_TYPES.values() if value in selected]}
            if type(query["source_types"]) is not list:
                raise WireShapeError(path + ".source_types", "expected an array of authorized source types")
            types = {v: k for k, v in SOURCE_TYPES.items()}
            for offset, item in enumerate(query["source_types"]):
                if type(item) is not str or item not in types:
                    raise WireShapeError(path + f".source_types[{offset}]", "expected an authorized source type: story_document, conversation_history, current_consensus_run, agent_work_history")
            result["queries"].append({"text": query["search_text"], "source_kinds": [types[item] for item in query["source_types"]]})
        if "artifact_directory_prefix" in action:
            result["path_prefix"] = action["artifact_directory_prefix"]
    elif kind == "read":
        result["handles"] = action["read_handles"]
    else:
        result["result"] = action["task_result"]
    return {"action": result}


def normalize_direct_result(value: object, phase: str) -> dict:
    fields = {"conversation": {"response_text"}, "draft": {"response_text"}, "propose_revision": {"response_text"}, "audit": {"candidate_review_status", "response_text"}, "synthesize": {"next_consensus_step", "response_text"}, "finalize": {"reviewed_proposal_id", "reviewed_proposal_digest"}}[phase]
    if type(value) is not dict or not (fields | {"response_type"}).issubset(value) or value["response_type"] != DIRECT_RESPONSE_TYPES[phase]:
        raise ValueError("invalid current direct response contract")
    result = {}
    for name, item in value.items():
        if name not in fields:
            continue
        target = {"response_text": "message", "candidate_review_status": "verdict", "next_consensus_step": "next"}.get(name, name)
        if name in {"candidate_review_status", "next_consensus_step"}:
            table = DIRECT_REVIEW if name == "candidate_review_status" else DIRECT_NEXT
            item = {v: k for k, v in table.items()}[item]
        result[target] = item
    return result


def normalize_retrieval_action(value: object) -> dict:
    """Decode only the current retrieval envelope with precise trusted findings."""
    if type(value) is not dict or set(value) != {"agent_action"}:
        raise WireShapeError("$", "expected an object containing exactly agent_action")
    action = value["agent_action"]
    if type(action) is not dict:
        raise WireShapeError("$.agent_action", "expected an object")
    action_type = action.get("action_type")
    inverse = {wire: domain for domain, wire in RETRIEVAL_ACTIONS.items()}
    if type(action_type) is not str or action_type not in inverse:
        raise WireShapeError("$.agent_action.action_type", "expected search_conversation_history, read_conversation_history, or submit_result")
    kind = inverse[action_type]
    if kind == "answer":
        if set(action) != {"action_type", "task_result"}:
            raise WireShapeError("$.agent_action", "submit_result requires exactly action_type and task_result")
        result = action["task_result"]
        if type(result) is not dict or set(result) != {"response_type", "response_text", "citation_ids"}:
            raise WireShapeError("$.agent_action.task_result", "expected exactly response_type, response_text, and citation_ids")
        if result["response_type"] != "conversation_answer":
            raise WireShapeError("$.agent_action.task_result.response_type", "expected conversation_answer")
        return {"action": {"kind": "answer", "message": result["response_text"], "citation_ids": result["citation_ids"]}}
    required = {"action_type", "search_query"} if kind == "search_conversations" else {"action_type", "citation_id", "start_sequence_number"}
    optional = set() if kind == "search_conversations" else {"end_sequence_number"}
    if not required <= action.keys() or not action.keys() <= required | optional:
        raise WireShapeError("$.agent_action", "expected exactly action_type and search_query" if kind == "search_conversations" else "expected action_type, citation_id, start_sequence_number, and optional end_sequence_number")
    aliases = {"action_type": "kind", "search_query": "query", "start_sequence_number": "start_sequence", "end_sequence_number": "end_sequence"}
    return {"action": {aliases.get(name, name): kind if name == "action_type" else item for name, item in action.items()}}


def recover_lead_public_response_kind(
    response_text: str, contract_id: str, *, maximum_utf8_bytes: int,
) -> str | None:
    """Observe a finite native response type without accepting its action or text."""
    if recover_lead_public_response(response_text, contract_id,
            maximum_utf8_bytes=maximum_utf8_bytes) is None:
        return None
    source = response_text.strip()
    lines = source.splitlines(keepends=True)
    if lines and lines[0].lstrip().startswith(("```", "~~~")):
        source = "".join(lines[1:-1])

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate response field")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError("non-JSON response constant")

    try:
        value = json.loads(source, object_pairs_hook=unique, parse_constant=reject_constant)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return None
    candidates = []
    for path in (("agent_action", "task_result"), ("task_result",), ("agent_action",), ()):
        item = value
        for key in path:
            item = item.get(key) if type(item) is dict else None
        if type(item) is dict and "response_text" in item:
            candidates.append(item)
    if len(candidates) != 1:
        return None
    native = candidates[0].get("response_type")
    if type(native) is not str:
        return None
    return {**{wire: kind for kind, wire in LEAD_RESPONSES.items()},
        "specialist_consultation_proposal": "proposal", "artifact_change_proposal": "proposal",
        "core_document_readiness_assessment": "answer", "lead_exploration": "exploration",
        "consultation_summary": "synthesis", "artifact_review_summary": "artifact_review",
        "premise_analysis_result": "premise_analysis", "premise_decision_summary": "premise_decision",
    }.get(native)


def recover_lead_public_response(
    response_text: str, contract_id: str, *, maximum_utf8_bytes: int,
) -> str | None:
    """Recover public final content without accepting its structured action."""
    if contract_id not in {
        "lead-response-v6", "lead-response-v7", "lead-response-v8",
        "lead-response-v9", "lead-discussion-response-v1",
        "lead-exploration-response-v1", "consultation-summary-v4", "artifact-review-summary-v4",
        "premise-decision-summary-v1", "premise-analysis-response-v1",
    } or type(response_text) is not str:
        return None
    if type(maximum_utf8_bytes) is not int or maximum_utf8_bytes < 1:
        return None
    try:
        if len(response_text.encode("utf-8")) > max(maximum_utf8_bytes, 262144):
            return None
    except UnicodeError:
        return None

    fence_lines = response_text.strip().splitlines(keepends=True)
    if fence_lines:
        opening = fence_lines[0].strip()
        if opening and opening[0] in "`~":
            marker_character = opening[0]
            marker_size = len(opening) - len(opening.lstrip(marker_character))
            if marker_size >= 3:
                marker = marker_character * marker_size
                language = opening[marker_size:].strip().casefold()
                if len(fence_lines) < 3 or fence_lines[-1].strip() != marker:
                    return None
                fenced_body = "".join(fence_lines[1:-1])
                if fenced_body.lstrip().startswith(("{", "[")):
                    if language not in {"", "json"}:
                        return None
                    response_text = fenced_body

    paths = (
        ("agent_action", "task_result", "response_text"),
        ("task_result", "response_text"),
        ("agent_action", "response_text"),
        ("response_text",),
    )
    private_keys = {
        "private",
        "privatethought", "private_reasoning", "private_thought",
        "private_thoughts", "debug", "callback", "callbacks",
        "tool_calls", "function_call", "analysis", "reasoning",
    }
    position = 0
    length = len(response_text)
    string_decoder = json.JSONDecoder()

    def whitespace() -> None:
        nonlocal position
        while position < length and response_text[position].isspace():
            position += 1

    def value():
        nonlocal position
        whitespace()
        start = position
        if position >= length:
            raise ValueError("missing value")
        token = response_text[position]
        if token == '"':
            decoded, position = string_decoder.raw_decode(response_text, position)
            return ("string", start, position, decoded, True)
        if token in "{[":
            closing = "}" if token == "{" else "]"
            kind = "object" if token == "{" else "array"
            position += 1
            children = []
            whitespace()
            if position < length and response_text[position] == closing:
                position += 1
                return (kind, start, position, children, True)
            while position < length:
                if kind == "object":
                    if response_text[position] != '"':
                        raise ValueError("object key")
                    key, position = string_decoder.raw_decode(response_text, position)
                    whitespace()
                    if position >= length or response_text[position] != ":":
                        raise ValueError("object separator")
                    position += 1
                    child = value()
                    children.append((key, child))
                else:
                    children.append(value())
                whitespace()
                if position >= length:
                    return (kind, start, position, children, False)
                if response_text[position] == closing:
                    position += 1
                    return (kind, start, position, children, True)
                if response_text[position] != ",":
                    raise ValueError("value separator")
                position += 1
                whitespace()
                if position >= length:
                    raise ValueError("missing trailing value")
            return (kind, start, position, children, False)
        for constant in ("-Infinity", "Infinity", "NaN", "true", "false", "null"):
            if response_text.startswith(constant, position):
                position += len(constant)
                return ("scalar", start, position, None, True)
        if token == "-":
            position += 1
        if position >= length or response_text[position] not in "0123456789":
            raise ValueError("number")
        if response_text[position] == "0":
            position += 1
        else:
            while position < length and response_text[position] in "0123456789":
                position += 1
        if position < length and response_text[position] == ".":
            position += 1
            digit_start = position
            while position < length and response_text[position] in "0123456789":
                position += 1
            if position == digit_start:
                raise ValueError("fraction")
        if position < length and response_text[position] in "eE":
            position += 1
            if position < length and response_text[position] in "+-":
                position += 1
            digit_start = position
            while position < length and response_text[position] in "0123456789":
                position += 1
            if position == digit_start:
                raise ValueError("exponent")
        return ("scalar", start, position, None, True)

    def visible(text: str) -> str | None:
        if not text or "\x00" in text:
            return None
        try:
            return text if len(text.encode("utf-8")) <= maximum_utf8_bytes else None
        except UnicodeError:
            return None

    def unsafe(node) -> bool:
        if node[0] == "string":
            return "\x00" in node[3] or not unicode_safe(node[3])
        if node[0] == "object":
            return any(
                key.casefold() in private_keys or not unicode_safe(key) or unsafe(child)
                for key, child in node[3]
            )
        if node[0] == "array":
            return any(unsafe(child) for child in node[3])
        return False

    def unicode_safe(text: str) -> bool:
        try:
            text.encode("utf-8")
            return "\x00" not in text
        except UnicodeError:
            return False

    ambiguous = False

    def at_path(node, path):
        nonlocal ambiguous
        if not path:
            return [node]
        if node[0] != "object":
            return []
        matching = [child for key, child in node[3] if key == path[0]]
        found = [answer for child in matching for answer in at_path(child, path[1:])]
        if len(matching) > 1 and found:
            ambiguous = True
        return found

    stripped = response_text.lstrip()
    if not stripped:
        return None
    try:
        root = value()
        whitespace()
        if position != length:
            raise ValueError("extra final content")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return visible(response_text) if stripped[0] not in '{["' else None
    if root[0] == "string":
        return visible(root[3])
    if root[0] == "scalar":
        return visible("Public final value (not a structured Lead result):\n"
                       + response_text[root[1]:root[2]])
    if root[0] != "object":
        return None
    answers = [answer for path in paths for answer in at_path(root, path)]
    if ambiguous or len(answers) != 1:
        return None
    answer = answers[0]
    if answer[0] == "string":
        return visible(answer[3])
    if not answer[4] or unsafe(answer):
        return None
    return visible("Public response value (not a literal response_text string):\n"
                   + response_text[answer[1]:answer[2]])


__all__ = [
    'normalize_context_action',
    'normalize_direct_result',
    'normalize_result',
    'normalize_retrieval_action',
    'recover_lead_public_response',
    'recover_lead_public_response_kind',
]
