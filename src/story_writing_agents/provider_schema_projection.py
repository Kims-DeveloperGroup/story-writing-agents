"""Provider schema projection, factoring, and inverse scope translation."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from copy import deepcopy

from .provider_contract_guidance import describe_schema
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
    field_description,
    parse_task_reference_token as _task_reference,
    result_enum_map as _enum_map,
)
from .structured_output_validation import (
    require_native_schema_projection,
    schema_rule_fingerprints,
)

def hoist_schema_definitions(schema: Mapping) -> dict:
    """Keep local references rooted in the final physical schema envelope."""
    result = deepcopy(dict(schema))
    definitions: dict[str, object] = {}

    def visit(node: object) -> None:
        if type(node) is not dict:
            return
        local = node.pop("$defs", None)
        if local is not None:
            if type(local) is not dict:
                raise WireShapeError("$.$defs", "invalid local definitions")
            for name, child in local.items():
                if name in definitions and definitions[name] != child:
                    raise WireShapeError("$.$defs", "conflicting local definition")
                definitions[name] = child
                visit(child)
        properties = node.get("properties")
        if type(properties) is dict:
            for child in properties.values():
                visit(child)
        variants = node.get("anyOf")
        if type(variants) is list:
            for child in variants:
                visit(child)
        visit(node.get("items"))

    visit(result)
    if definitions:
        result["$defs"] = definitions
    return result


def rebase_schema_definitions(
    schema: Mapping[str, object], namespace: str,
) -> dict[str, object]:
    """Rebase one complete captured graph before composing independent components."""
    if type(namespace) is not str or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", namespace) is None:
        raise WireShapeError("$.$defs", "expected host definition namespace")
    result = deepcopy(dict(schema))
    definitions = result.get("$defs", {})
    if type(definitions) is not dict:
        raise WireShapeError("$.$defs", "expected root-owned definitions")
    before, definition_fingerprints = schema_rule_fingerprints(result)
    aliases = {name: f"{namespace}_{name}" for name in definition_fingerprints}
    had_definitions = "$defs" in result
    result.pop("$defs", None)
    visited: set[int] = set()

    def visit(node: object) -> None:
        if type(node) is not dict:
            return
        identity = id(node)
        if identity in visited:
            return
        visited.add(identity)
        if "$defs" in node:
            raise WireShapeError("$.$defs", "expected root-owned definitions")
        if "$ref" in node:
            name = node["$ref"][len("#/$defs/"):]
            node["$ref"] = "#/$defs/" + aliases[name]
        properties = node.get("properties")
        if type(properties) is dict:
            for child in properties.values():
                visit(child)
        variants = node.get("anyOf")
        if type(variants) is list:
            for child in variants:
                visit(child)
        visit(node.get("items"))

    visit(result)
    for child in definitions.values():
        visit(child)
    if had_definitions:
        result["$defs"] = {aliases[name]: child for name, child in definitions.items()}
    after, rebased_fingerprints = schema_rule_fingerprints(result)
    if after != before or any(
        rebased_fingerprints.get(aliases[name]) != fingerprint
        for name, fingerprint in definition_fingerprints.items()
    ):
        raise WireShapeError("$.$defs", "definition rebasing changed rules")
    return result
LEAD_SCOPE_NATIVE_FIELDS = (
    ("scope", (
        ("activity_type", "activity"), ("writing_intent", "intent"),
        ("specialist_domains", "domains"), ("target_artifact_paths", "paths"),
        ("contributions", "roles"), ("dependency_slots", "slots"),
        ("task_reference", "task"),
    )),
    ("task", (("task_id", "id"), ("task_revision", "revision"))),
    ("slot", (("slot_id", "id"), ("dependent_domains", "domains"))),
    ("marker", (("writing_task_scope", "task_scope"),)),
)


def _lead_scope_names(context: str, inverse: bool, fields: tuple) -> dict[str, str]:
    pairs = dict(fields).get(context, ())
    return {new: old for old, new in pairs} if inverse else dict(pairs)


def _lead_scope_choices(nodes: tuple, definitions: Mapping) -> tuple:
    result = []
    visited: set[int] = set()

    def visit(node: Mapping) -> None:
        if id(node) in visited:
            return
        visited.add(id(node))
        if "$ref" in node:
            visit(definitions[node["$ref"][len("#/$defs/"):]])
        elif "anyOf" in node:
            for child in node["anyOf"]:
                visit(child)
        else:
            result.append(node)

    for node in nodes:
        visit(node)
    return tuple(result)


def _lead_scope_child_context(
    context: str, name: str, children: tuple, definitions: Mapping,
    inverse: bool, fields: tuple,
) -> str:
    if context == "slots":
        return "slot"
    original = _lead_scope_names(context, inverse, fields).get(name, name) if inverse else name
    if context == "scope":
        return {"task_reference": "task", "dependency_slots": "slots"}.get(original, "")
    if context:
        return ""
    if name in _lead_scope_names("marker", inverse, fields):
        required = {"activity_type", "specialist_domains", "target_artifact_paths"}
        if inverse:
            mapping = _lead_scope_names("scope", False, fields)
            required = {mapping[name] for name in required}
        if any(required.issubset(node.get("properties", {})) for node in _lead_scope_choices(children, definitions)):
            return "scope"
    return ""


def _lead_scope_schema_graph(schema: Mapping, inverse: bool, fields: tuple) -> dict:
    root = deepcopy(dict(schema))
    schema_rule_fingerprints(root)
    had_definitions = "$defs" in root
    original = root.pop("$defs", {})
    contexts: dict[str, set[str]] = {name: set() for name in original}
    seen: set[tuple[int, str]] = set()

    def collect(node: Mapping, context: str) -> None:
        token = (id(node), context)
        if token in seen:
            return
        seen.add(token)
        if "$ref" in node:
            name = node["$ref"][len("#/$defs/"):]
            contexts[name].add(context)
            collect(original[name], context)
            return
        for child in node.get("anyOf", ()):
            collect(child, context)
        for name, child in node.get("properties", {}).items():
            collect(child, _lead_scope_child_context(context, name, (child,), original, inverse, fields))
        if "items" in node:
            collect(node["items"], "slot" if context == "slots" else context)

    collect(root, "")
    for name, node in sorted(original.items()):
        if not contexts[name]:
            contexts[name].add("")
            collect(node, "")
    primary = {name: min(values) for name, values in contexts.items()}
    aliases: dict[tuple[str, str], str] = {}
    declarations: dict[str, object] = {}
    next_alias = 0

    def definition(name: str, context: str) -> str:
        nonlocal next_alias
        token = (name, context)
        if token in aliases:
            return aliases[token]
        if context != primary[name]:
            base = definition(name, primary[name])
            projected = render(original[name], context)
            if projected == declarations[base]:
                aliases[token] = base
                return base
            while "s" + str(next_alias) in original or "s" + str(next_alias) in declarations:
                next_alias += 1
            alias = "s" + str(next_alias)
            next_alias += 1
        else:
            alias = name
            projected = render(original[name], context)
        aliases[token] = alias
        declarations[alias] = projected
        return alias

    def render(node: Mapping, context: str) -> dict:
        if "$defs" in node:
            raise WireShapeError("$.$defs", "expected root-owned scope definitions")
        result = dict(node)
        if "$ref" in node:
            name = node["$ref"][len("#/$defs/"):]
            result["$ref"] = "#/$defs/" + definition(name, context)
            return result
        if "anyOf" in node:
            result["anyOf"] = [render(child, context) for child in node["anyOf"]]
        mapping = _lead_scope_names(context, inverse, fields)
        if "properties" in node:
            properties = {}
            for name, child in sorted(node["properties"].items()):
                child_context = _lead_scope_child_context(context, name, (child,), original, inverse, fields)
                if not context and child_context == "scope":
                    mapping[name] = _lead_scope_names("marker", inverse, fields)[name]
                current = mapping.get(name, name)
                if current in properties:
                    raise WireShapeError("$", "scope projection has ambiguous declared fields")
                properties[current] = render(child, child_context)
            result["properties"] = properties
        if "required" in node:
            result["required"] = [mapping.get(name, name) for name in node["required"]]
        if "items" in node:
            result["items"] = render(node["items"], "slot" if context == "slots" else context)
        return result

    result = render(root, "")
    for name in sorted(original):
        definition(name, primary[name])
    if had_definitions or declarations:
        result["$defs"] = declarations
    return result


def project_lead_scope_schema(
    schema: Mapping[str, object], *, _fields: tuple = LEAD_SCOPE_NATIVE_FIELDS,
) -> dict[str, object]:
    """Project the complete current native Lead scope vocabulary without omitting rules."""
    if _fields != LEAD_SCOPE_NATIVE_FIELDS:
        raise WireShapeError("$", "scope codec declaration changed")
    before, definitions = schema_rule_fingerprints(schema)
    result = _lead_scope_schema_graph(schema, False, _fields)
    inverse = _lead_scope_schema_graph(result, True, _fields)
    after, restored = schema_rule_fingerprints(inverse)
    if after != before or any(restored.get(name) != identity for name, identity in definitions.items()):
        raise WireShapeError("$", "scope projection changed the declared rule graph")
    return result


def restore_lead_scope_result(
    value: Mapping[str, object], schema: Mapping[str, object], *,
    _fields: tuple = LEAD_SCOPE_NATIVE_FIELDS,
) -> dict[str, object]:
    """Restore only declared scope fields after the complete selected graph is valid."""
    if _fields != LEAD_SCOPE_NATIVE_FIELDS:
        raise WireShapeError("$", "scope codec declaration changed")
    schema_rule_fingerprints(schema)
    definitions = schema.get("$defs", {})

    def visit(item: object, nodes: tuple, context: str) -> object:
        choices = _lead_scope_choices(nodes, definitions)
        if type(item) is list:
            children = tuple(node["items"] for node in choices if "items" in node)
            if not children:
                raise WireShapeError("$", "scope codec requires selected declared items")
            return [visit(child, children, "slot" if context == "slots" else context) for child in item]
        if type(item) is not dict:
            return deepcopy(item)
        mapping = _lead_scope_names(context, True, _fields)
        result = {}
        for name, child in item.items():
            children = tuple(node["properties"][name] for node in choices if name in node.get("properties", {}))
            if not children:
                raise WireShapeError("$", "scope codec requires selected declared fields")
            child_context = _lead_scope_child_context(context, name, children, definitions, True, _fields)
            if not context and child_context == "scope":
                mapping[name] = _lead_scope_names("marker", True, _fields)[name]
            current = mapping.get(name, name)
            if current in result:
                raise WireShapeError("$", "scope codec has ambiguous declared fields")
            result[current] = visit(child, children, child_context)
        return result

    result = visit(value, (schema,), "")
    if type(result) is not dict:
        raise WireShapeError("$", "scope codec requires a selected result object")
    return result


def project_lead_scope_diagnostic(
    text: str, *, _fields: tuple = LEAD_SCOPE_NATIVE_FIELDS,
) -> str:
    """Translate trusted member detail/scope paths to the current native graph."""
    if _fields != LEAD_SCOPE_NATIVE_FIELDS:
        raise WireShapeError("$", "scope codec declaration changed")

    def path(match: re.Match) -> str:
        tokens = re.findall(r"[A-Za-z_][A-Za-z_0-9]*|\[[^]]*\]", match[0][1:])
        result = "$"
        context = ""
        member_roots = ((), ("agent_action", "task_result"))
        scope_roots = member_roots + tuple(root + ("response_details",) for root in member_roots)
        markers = _lead_scope_names("marker", False, _fields)
        details = {RESULT_FIELDS[name] for name in (
            "proposal", "artifact_intent", "next_intent", "recommended_action", "objective_intent"
        )}
        for index, token in enumerate(tokens):
            if token.startswith("["):
                result += token
                if context == "slots":
                    context = "slot"
                continue
            prefix = tuple(tokens[:index])
            scope_marker = (not context and prefix in scope_roots
                            and (token in markers or token in markers.values()))
            mapping = markers if scope_marker else _lead_scope_names(context, False, _fields)
            if prefix in member_roots and (scope_marker or token in details):
                result += ".response_details"
            renamed = mapping.get(token, token)
            result += "." + renamed
            if context == "slots":
                context = "slot"
            elif context == "scope":
                context = {"task_reference": "task", "dependency_slots": "slots"}.get(token, "")
            elif context:
                context = ""
            elif scope_marker:
                context = "scope"
        return result

    return re.sub(r"\$(?:\.[A-Za-z_][A-Za-z_0-9]*|\[[^]]*\])+", path, text)


def require_schema_projection(
    bound_schema: Mapping, actual_native_schema: Mapping, *,
    declared_lowering: str = "identity",
) -> None:
    """Preserve the provider adapter's projection error boundary."""
    try:
        require_native_schema_projection(bound_schema, actual_native_schema, declared_lowering=declared_lowering)
    except ValueError as failure:
        raise WireShapeError("$", str(failure)) from None


def _closed(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _result_field(name: str, shape: str) -> str:
    if name == "kind" and shape == "ideas":
        return "idea_kind"
    if name == "task" and shape == "authoring_scope":
        return "task_reference"
    if name == "kind":
        return "change_type" if shape == "operations" else "action_type"
    if name == "action" and shape == "objective_intent":
        return "update_type"
    return RESULT_FIELDS.get(name, name)


def project_result_schema(schema: Mapping, contract_id: str) -> dict:
    """Project an owner-authored result schema without changing its domain schema."""
    family = {"lead-response-v7": "lead-response-v6", "lead-response-v8": "lead-response-v6", "lead-response-v9": "lead-response-v6", "lead-discussion-response-v1": "lead-response-v6", "specialist-assignment-response-v4": "specialist-assignment-response-v3", "specialist-assignment-response-v5": "specialist-assignment-response-v3"}.get(contract_id, contract_id)
    known = family in BUILTIN_RESULT_CONTRACT_IDS
    if not known:
        return deepcopy(dict(schema))

    def visit(node: Mapping, shape: str = "", root: bool = False) -> dict:
        result = deepcopy(dict(node))
        if shape in {"authoring_assessment", "core_document_assessment"}:
            return result
        if "anyOf" in node:
            result["anyOf"] = [visit(item, shape, root) for item in node["anyOf"]]
            return result
        if "properties" not in node:
            if "items" in node:
                result["items"] = visit(node["items"], shape)
            return result
        properties = {}
        for name, child in node["properties"].items():
            if root and known and name == "kind":
                continue
            wire_name = _result_field(name, shape)
            if name == "task" and shape == "authoring_scope":
                variants = [{
                    **_closed({"task_id": {"type": "string", "enum": [pair["task_id"]]}, "task_revision": {"type": "integer", "enum": [pair["task_revision"]]}}),
                    "description": (
                        f"Continue task {pair['task_id']} at revision {pair['task_revision']} "
                        "when this is the task being addressed; copy this exact pair together."
                    ),
                } for token in child.get("enum", ()) for pair in [_task_reference(token)]]
                properties[wire_name] = (variants[0] if len(variants) == 1 else {"anyOf": variants}) if variants else _closed({"task_id": {"type": "string"}, "task_revision": {"type": "integer"}})
                continue
            projected = visit(child, name)
            values = _enum_map(name, shape)
            if "enum" in projected:
                projected["enum"] = [values.get(item, item) for item in projected["enum"]]
            properties[wire_name] = projected
        if root and known:
            if family == "lead-response-v6":
                kinds = node["properties"].get("kind", {}).get("enum", ["answer"])
                types = [("core_document_readiness_assessment" if "core_document_assessment" in node["properties"] else
                          ("artifact_change_proposal" if "artifact_intent" in node["properties"] else "specialist_consultation_proposal") if kind == "proposal" else LEAD_RESPONSES[kind]) for kind in kinds]
            else:
                types = [{"lead-exploration-response-v1": "lead_exploration", "specialist-assignment-response-v3": "specialist_assignment_result", "consultation-summary-v4": "consultation_summary", "artifact-review-summary-v4": "artifact_review_summary", "premise-analysis-response-v1": "premise_analysis_result", "premise-decision-summary-v1": "premise_decision_summary"}[family]]
            properties = {"response_type": {"type": "string", "enum": types}, **properties}
        result["properties"] = properties
        result["required"] = (["response_type"] if root and known else []) + [
            _result_field(name, shape) for name in node.get("required", ())
            if not (root and known and name == "kind")
        ]
        return result

    projected = describe_schema(visit(schema, root=True))
    return _compact_lead_schema_guidance(projected) if family == "lead-response-v6" else projected


def share_schema_descriptions(schema: Mapping) -> dict:
    """Share literal annotations in one final document, with an exact inverse."""
    def encoded(value: object) -> bytes:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          allow_nan=False, separators=(",", ":")).encode("utf-8", "strict")

    def annotated(node: dict):
        if "description" in node:
            yield node
        for child in node.get("properties", {}).values():
            yield from annotated(child)
        for child in node.get("anyOf", ()):
            yield from annotated(child)
        if isinstance(node.get("items"), dict):
            yield from annotated(node["items"])
        for child in node.get("$defs", {}).values():
            yield from annotated(child)

    original = deepcopy(dict(schema))
    original_bytes = encoded(original)
    original_fingerprints = schema_rule_fingerprints(original)
    counts: dict[str, int] = {}
    for node in annotated(original):
        value = node["description"]
        counts[value] = counts.get(value, 0) + 1
    values = sorted(counts)
    suffixes = set()
    for index, value in enumerate(values):
        for other in values[:index]:
            size = 0
            while size < min(len(value), len(other)) and value[-size - 1] == other[-size - 1]:
                size += 1
            size = min(size, len(value) - 1, len(other) - 1)
            if size > 0:
                suffixes.add(value[-size:])
    candidates = sorted(
        suffixes,
        key=lambda value: (
            -sum(count for text, count in counts.items() if text.endswith(value))
            * (len(encoded(value)) - 2),
            value,
        ),
    )
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

    def short_name(number: int) -> str:
        result = ""
        while True:
            number, digit = divmod(number, len(alphabet))
            result = alphabet[digit] + result
            if not number:
                return result
            number -= 1

    source_text = original_bytes.decode("utf-8")
    token_bound = len(candidates) + len(values) * (len(values) + 1) // 2
    prefix_size = 1
    if token_bound:
        digits = {character: number + 1 for number, character in enumerate(alphabet)}
        for word in re.finditer("[a-zA-Z]+", source_text):
            start, end = word.span()
            if (start == 0 or end == len(source_text)
                    or source_text[start - 1] != "~" or source_text[end] != "~"):
                continue
            number = 0
            for position in range(start, end):
                number = number * len(alphabet) + digits[source_text[position]]
                if number > token_bound:
                    break
            if number > token_bound:
                continue
            before, after = start, end
            while before and source_text[before - 1] == "~":
                before -= 1
            while after < len(source_text) and source_text[after] == "~":
                after += 1
            prefix_size = max(prefix_size, min(start - before, after - end) + 1)
    prefix = "~" * prefix_size
    segments = {value: [(False, value)] for value in values}
    literals: dict[str, str] = {}
    chunks: list[str] = []

    def intern(value: str, count: int) -> str | None:
        code = prefix + short_name(len(literals)) + prefix
        chunk = f"{code} {len(value)}:{value}\n"
        if count * (len(encoded(value)) - len(encoded(code))) <= len(encoded(chunk)) - 2:
            return None
        literals[code] = value
        chunks.append(chunk)
        return code

    for value in candidates:
        matched = [text for text, pieces in segments.items()
                   if len(pieces) == 1 and not pieces[0][0] and text.endswith(value)]
        code = intern(value, sum(counts[text] for text in matched))
        if code is not None:
            for text in matched:
                segments[text] = [(False, text[:-len(value)]), (True, code)]
    prefix_counts: dict[str, int] = {}
    for text, pieces in segments.items():
        for is_code, value in pieces:
            if not is_code and value:
                prefix_counts[value] = prefix_counts.get(value, 0) + counts[text]
    prefixes = sorted(prefix_counts)
    shared_prefixes = set()
    for index, value in enumerate(prefixes):
        for other in prefixes[:index]:
            size = 0
            while size < min(len(value), len(other)) and value[size] == other[size]:
                size += 1
            size = min(size, len(value) - 1, len(other) - 1)
            if size > 0:
                shared_prefixes.add(value[:size])
    for value in sorted(shared_prefixes, key=lambda part: (
        -sum(count for text, count in prefix_counts.items() if text.startswith(part))
        * (len(encoded(part)) - 2), part,
    )):
        matched = [text for text, pieces in segments.items()
                   if not pieces[0][0] and len(pieces[0][1]) > len(value)
                   and pieces[0][1].startswith(value)]
        code = intern(value, sum(counts[text] for text in matched))
        if code is not None:
            for text in matched:
                pieces = segments[text]
                segments[text] = [(True, code), (False, pieces[0][1][len(value):])] + pieces[1:]
    prefix_counts = {}
    for text, pieces in segments.items():
        for is_code, value in pieces:
            if not is_code and value:
                prefix_counts[value] = prefix_counts.get(value, 0) + counts[text]
    for value, count in sorted(prefix_counts.items()):
        code = intern(value, count)
        if code is not None:
            for text, pieces in segments.items():
                segments[text] = [
                    (is_code, code if not is_code and piece == value else piece)
                    for is_code, piece in pieces
                ]
    if not literals:
        return original
    scope = hashlib.sha256(original_bytes).hexdigest()
    marker = (
        f"\n[SWA-DESCRIPTIONS {scope}]\n"
        "Exact delimited description tokens use only this complete schema's literal dictionary. "
        "Expand each token once wherever it occurs; all other characters remain literal. "
        "Lengths count Unicode characters. The first line records original root-description "
        "presence and entry count.\n"
    )
    while marker in source_text:
        marker = "\n" + marker
    result = deepcopy(original)
    for node in annotated(result):
        node["description"] = "".join(piece for _, piece in segments[node["description"]])
    had_root = "description" in result
    result["description"] = (
        result.get("description", "") + marker + f"{int(had_root)} {len(literals)}\n"
        + "".join(chunks)
    )

    restored = deepcopy(result)
    root_text, separator, payload = restored["description"].rpartition(marker)
    if not separator:
        raise WireShapeError("$", "missing scoped description dictionary")
    header, payload = payload.split("\n", 1)
    root_flag, size = (int(value) for value in header.split())
    recovered: dict[str, str] = {}
    for _ in range(size):
        header, payload = payload.split(":", 1)
        code, length = header.rsplit(" ", 1)
        length = int(length)
        recovered[code], payload = payload[:length], payload[length:]
        if not payload.startswith("\n"):
            raise WireShapeError("$", "invalid description dictionary framing")
        payload = payload[1:]
    if payload or recovered != literals:
        raise WireShapeError("$", "incomplete scoped description dictionary")
    if root_flag:
        restored["description"] = root_text
    else:
        del restored["description"]
    token_pattern = re.compile("|".join(re.escape(code) for code in recovered))
    for node in annotated(restored):
        node["description"] = token_pattern.sub(
            lambda match: recovered[match.group()], node["description"]
        )
    if (encoded(restored) != original_bytes
            or schema_rule_fingerprints(restored) != original_fingerprints):
        raise WireShapeError("$", "description sharing changed complete rule graph")
    return result if len(encoded(result)) < len(original_bytes) else original


def _compact_lead_schema_guidance(schema: dict) -> dict:
    """Factor exact repeated prose into scoped guidance; schema constraints stay literal."""
    entries = []
    meanings = {}

    def visit(node: dict, name: str = "", required: bool = False) -> None:
        description = node.get("description")
        if isinstance(description, str):
            entries.append((node, name, required, description))
            meanings.setdefault(name, set()).add(description)
        for field_name, child in node.get("properties", {}).items():
            visit(child, field_name)
        for child in node.get("anyOf", ()):
            visit(child, name, True)
        if isinstance(node.get("items"), dict):
            visit(node["items"], name)

    visit(schema, required=True)
    required_nodes = {id(node) for node, _, required, _ in entries if required}
    field_guidance = []
    for name, descriptions in meanings.items():
        if not name or len(descriptions) != 1:
            continue
        description = next(iter(descriptions))
        targets = [node for node, field_name, required, text in entries
                   if field_name == name and id(node) not in required_nodes and text == description]
        guidance = f" Field {name}: {description}"
        saving = sum(len(json.dumps({"description": description}, ensure_ascii=False).encode("utf-8")) - 2
                     for _ in targets)
        if saving > len(guidance.encode("utf-8")) + 16:
            for node in targets:
                del node["description"]
            field_guidance.append(guidance)
    counts = {}
    for node, _, _, description in entries:
        if "description" in node:
            counts[description] = counts.get(description, 0) + 1
    codes = {}
    for description, count in counts.items():
        code = f"[G{len(codes) + 1}]"
        saving = (len(json.dumps(description, ensure_ascii=False).encode("utf-8"))
                  - len(json.dumps(code).encode("utf-8"))) * count
        if saving > len(f" {code}: {description}".encode("utf-8")) + 16:
            codes[description] = code
    for node, _, _, description in entries:
        if "description" in node and description in codes:
            node["description"] = codes[description]
    if field_guidance or codes:
        schema["description"] += (
            " Shared field guidance applies to all identically named slots; "
            "codes apply only to nodes marked with that exact code."
            + "".join(field_guidance)
            + "".join(f" {code}: {description}" for description, code in codes.items())
        )
    return schema


def project_action_schema(schema: Mapping) -> dict:
    """Project contextual actions without replacing owner-authored meanings."""
    def action(node: Mapping) -> dict:
        if "anyOf" in node:
            return {**deepcopy(dict(node)), "anyOf": [action(item) for item in node["anyOf"]]}
        properties = {}
        for name, spec in node["properties"].items():
            wire = {"kind": "action_type", "result": "task_result", "handles": "read_handles", "path_prefix": "artifact_directory_prefix", "query": "search_query", "start_sequence": "start_sequence_number", "end_sequence": "end_sequence_number", "verdict": "candidate_review_status", "next": "next_consensus_step"}.get(name, name)
            child = deepcopy(spec)
            if name == "kind":
                child["enum"] = [ACTION_TYPES[item] for item in spec["enum"]]
            elif name == "queries":
                query = child["items"]
                query["properties"] = {"search_text": query["properties"]["text"], "source_types": query["properties"]["source_kinds"]}
                query["required"] = ["search_text", "source_types"]
                selection = query["properties"]["source_types"]
                if selection.get("type") == "array":
                    selection["items"]["enum"] = [SOURCE_TYPES[item] for item in selection["items"]["enum"]]
                else:
                    for candidate in selection.get("anyOf", [selection]):
                        candidate["properties"] = {SOURCE_TYPES[key]: value for key, value in candidate["properties"].items()}
                        candidate["required"] = list(candidate["properties"])
            properties[wire] = child
        branch = {**_closed(properties), "description": node["description"]}
        # Result contracts have their own owner validation; caller-owned result
        # names and enums must never enter the built-in vocabulary catalog.
        describe_schema({
            **branch,
            "properties": {name: spec for name, spec in properties.items() if name != "task_result"},
        })
        if "task_result" in properties:
            field_description("task_result")
        return branch
    field_description("agent_action")
    return {
        **_closed({"agent_action": action(schema["properties"]["action"])}),
        "description": schema["description"],
    }


def project_direct_schema(schema: Mapping, phase: str) -> dict:
    properties = {"response_type": {"type": "string", "enum": [DIRECT_RESPONSE_TYPES[phase]]}}
    for name, child in schema["properties"].items():
        target = {"message": "response_text", "verdict": "candidate_review_status", "next": "next_consensus_step"}.get(name, name)
        child = deepcopy(child)
        if name in {"verdict", "next"}:
            mapping = DIRECT_REVIEW if name == "verdict" else DIRECT_NEXT
            child["enum"] = [mapping[item] for item in child["enum"]]
        properties[target] = child
    return describe_schema({**_closed(properties), "description": schema["description"]})


def project_retrieval_schema(schema: Mapping) -> dict:
    branches = []
    for branch in schema["properties"]["action"]["anyOf"]:
        kind = branch["properties"]["kind"]["enum"][0]
        fields = {"action_type": {"type": "string", "enum": [RETRIEVAL_ACTIONS[kind]]}}
        for name, child in branch["properties"].items():
            if name != "kind":
                wire = {"query": "search_query", "start_sequence": "start_sequence_number", "end_sequence": "end_sequence_number", "message": "response_text"}.get(name, name)
                fields[wire] = deepcopy(child)
        if kind == "answer":
            fields = {"action_type": fields.pop("action_type"), "task_result": _closed({"response_type": {"type": "string", "enum": ["conversation_answer"]}, **fields})}
        branches.append({**_closed(fields), "description": branch["description"]})
    return describe_schema({
        **_closed({"agent_action": {"anyOf": branches}}),
        "description": schema["description"],
    })


__all__ = [
    'LEAD_SCOPE_NATIVE_FIELDS',
    'hoist_schema_definitions',
    'project_action_schema',
    'project_direct_schema',
    'project_lead_scope_diagnostic',
    'project_lead_scope_schema',
    'project_result_schema',
    'project_retrieval_schema',
    'rebase_schema_definitions',
    'require_schema_projection',
    'restore_lead_scope_result',
    'share_schema_descriptions',
]
