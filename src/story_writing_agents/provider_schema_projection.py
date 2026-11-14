"""Provider schema projection, factoring, and inverse scope translation."""

from __future__ import annotations

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
    strict_json_object,
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


def factor_rule_schema(schema: Mapping[str, object]) -> dict[str, object]:
    """Share equivalent acyclic rule nodes without expanding or pruning the graph."""
    root = strict_json_object(json.dumps(dict(schema), ensure_ascii=False, allow_nan=False))
    original_fingerprint, definition_fingerprints = schema_rule_fingerprints(root)
    source_definitions = root.pop("$defs", {})
    names = {identity: f"d{number}" for number, identity in enumerate(sorted(set(definition_fingerprints.values())))}
    aliases = {name: names[identity] for name, identity in definition_fingerprints.items()}

    def rename(node: object, symbols: Mapping[str, str]) -> object:
        if type(node) is not dict:
            return node
        result = dict(node)
        if "$ref" in result:
            result["$ref"] = "#/$defs/" + symbols[result["$ref"][len("#/$defs/"):]]
            return result
        if type(result.get("properties")) is dict:
            result["properties"] = {name: rename(child, symbols) for name, child in result["properties"].items()}
        if type(result.get("anyOf")) is list:
            result["anyOf"] = [rename(child, symbols) for child in result["anyOf"]]
        if "items" in result:
            result["items"] = rename(result["items"], symbols)
        return result

    definitions: dict[str, object] = {}
    for name, node in sorted(source_definitions.items()):
        # Bare aliases resolve to their concrete target, avoiding a self-alias after merging.
        while set(node) == {"$ref"}:
            node = source_definitions[node["$ref"][len("#/$defs/"):]]
        definitions.setdefault(aliases[name], rename(node, aliases))
    root = rename(root, aliases)
    counts: dict[str, int] = {}
    nodes: dict[str, object] = {}
    node_keys: dict[int, str] = {}

    def children(node: Mapping[str, object]):
        properties = node.get("properties")
        if type(properties) is dict:
            yield from properties.values()
        variants = node.get("anyOf")
        if type(variants) is list:
            yield from variants
        if "items" in node:
            yield node["items"]

    def key(node: object) -> str:
        return json.dumps(node, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

    fingerprint_definitions = dict(definitions)
    fingerprint_names: dict[int, str] = {}

    def collect_fingerprint_nodes(node: object) -> None:
        if type(node) is not dict or id(node) in fingerprint_names:
            return
        name = f"p{len(fingerprint_names)}"
        if name in fingerprint_definitions:
            raise ValueError("schema fingerprint scratch symbol conflicts with an input definition")
        fingerprint_names[id(node)] = name
        fingerprint_definitions[name] = node
        for name in sorted(node.get("properties", {})):
            collect_fingerprint_nodes(node["properties"][name])
        for child in node.get("anyOf", ()):
            collect_fingerprint_nodes(child)
        if "items" in node:
            collect_fingerprint_nodes(node["items"])

    collect_fingerprint_nodes(root)
    for name in sorted(definitions):
        collect_fingerprint_nodes(definitions[name])
    _, node_fingerprints = schema_rule_fingerprints(
        {**root, "$defs": fingerprint_definitions})
    node_keys.update({identity: node_fingerprints[name]
                      for identity, name in fingerprint_names.items()})

    def source_key(node: object) -> str:
        return node_keys[id(node)]

    def count(node: object, *, outer: bool = False) -> None:
        if type(node) is not dict:
            return
        identity = source_key(node)
        if not outer and "$ref" not in node:
            counts[identity] = counts.get(identity, 0) + 1
            nodes[identity] = node
        for child in children(node):
            count(child)

    count(root, outer=True)
    for node in definitions.values():
        count(node, outer=True)
    existing = {source_key(node): name for name, node in definitions.items()}
    source_order: dict[str, tuple[int, str]] = {}
    for source_identity, source_node in nodes.items():
        source_literal = key(source_node)
        source_order[source_identity] = (len(source_literal.encode("utf-8")), source_literal)
    shared: dict[str, str] = {}
    # Preserve the current large-node seed; additional bindings must reduce its actual bytes.
    for identity, times in sorted(counts.items(), key=lambda item: source_order[item[0]][1]):
        if source_order[identity][0] < 80:
            continue
        if identity in existing:
            shared[identity] = existing[identity]
        elif times > 1:
            shared[identity] = f"n{len(shared)}"

    def render(node: object, *, own: str | None = None) -> object:
        if type(node) is not dict:
            return node
        identity = source_key(node)
        if identity in shared and identity != own:
            return {"$ref": "#/$defs/" + shared[identity]}
        result = dict(node)
        if type(result.get("properties")) is dict:
            result["properties"] = {name: render(child) for name, child in result["properties"].items()}
        if type(result.get("anyOf")) is list:
            result["anyOf"] = [render(child) for child in result["anyOf"]]
        if "items" in result:
            result["items"] = render(result["items"])
        return result

    def symbol_aliases(reference_counts: Mapping[str, int]) -> dict[str, str]:
        alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

        def short_name(number: int) -> str:
            name = ""
            while True:
                number, digit = divmod(number, len(alphabet))
                name = alphabet[digit] + name
                if not number:
                    return name
                number -= 1

        return {
            name: short_name(number)
            for number, name in enumerate(sorted(reference_counts, key=lambda name: (-reference_counts[name], name)))
        }

    def compact_symbols(graph: dict[str, object]) -> tuple[dict[str, object], dict[str, str]]:
        """Use the same local-symbol layout for every cost trial and final graph."""
        if not graph.get("$defs"):
            return graph, {}
        reference_counts = dict.fromkeys(graph["$defs"], 0)

        def count_references(node: object) -> None:
            if type(node) is not dict:
                return
            if "$ref" in node:
                reference_counts[node["$ref"][len("#/$defs/"):]] += 1
                return
            for child in children(node):
                count_references(child)

        count_references(graph)
        for node in graph["$defs"].values():
            count_references(node)
        aliases = symbol_aliases(reference_counts)
        compact = rename(graph, aliases)
        compact["$defs"] = {aliases[name]: rename(node, aliases) for name, node in graph["$defs"].items()}
        return compact, aliases

    def assemble() -> tuple[dict[str, object], dict[str, str]]:
        result = render(root, own=source_key(root))
        assert type(result) is dict
        declarations = {name: render(node, own=source_key(node)) for name, node in definitions.items()}
        for identity, name in shared.items():
            if name not in declarations:
                declarations[name] = render(nodes[identity], own=identity)
        if declarations:
            result["$defs"] = declarations
        return compact_symbols(result)

    quoted_sizes: dict[str, int] = {}
    fixed_sizes: dict[int, int] = {}

    def quoted_size(value: str) -> int:
        if value not in quoted_sizes:
            quoted_sizes[value] = len(key(value).encode("utf-8"))
        return quoted_sizes[value]

    def fixed_size(node: dict[str, object]) -> int:
        """Count unchanged bytes; the detached source keeps every node alive."""
        identity = id(node)
        if identity not in fixed_sizes:
            size = 2 + max(0, len(node) - 1)
            for name, value in node.items():
                size += quoted_size(name) + 1
                if name == "properties" and type(value) is dict:
                    size += 2 + max(0, len(value) - 1)
                    size += sum(quoted_size(field) + 1 for field in value)
                elif name == "anyOf" and type(value) is list:
                    size += 2 + max(0, len(value) - 1)
                elif name != "items":
                    size += len(key(value).encode("utf-8"))
            fixed_sizes[identity] = size
        return fixed_sizes[identity]

    def measure(node: object, reference_counts: dict[str, int], *,
                own: str | None = None, count_refs: bool = True) -> int:
        if type(node) is not dict:
            return len(key(node).encode("utf-8"))
        identity = source_key(node)
        if identity in shared and identity != own:
            name = shared[identity]
            if count_refs:
                reference_counts[name] += 1
            return 2 + quoted_size("$ref") + 1 + quoted_size("#/$defs/" + name)
        size = fixed_size(node)
        if "$ref" in node:
            if count_refs:
                reference_counts[node["$ref"][len("#/$defs/"):]] += 1
            count_refs = False
        for child in children(node):
            size += measure(child, reference_counts, count_refs=count_refs)
        return size

    def trial_size() -> tuple[int, dict[str, str]]:
        """Measure the exact rendered/renamed trial without materializing it."""
        declarations = {name: (node, source_key(node)) for name, node in definitions.items()}
        for identity, name in shared.items():
            if name not in declarations:
                declarations[name] = (nodes[identity], identity)
        reference_counts = dict.fromkeys(declarations, 0)
        size = measure(root, reference_counts, own=source_key(root))
        if declarations:
            size += (1 if root else 0) + quoted_size("$defs") + 1
            size += 2 + max(0, len(declarations) - 1)
            for name, (node, own) in declarations.items():
                size += quoted_size(name) + 1 + measure(node, reference_counts, own=own)
        aliases = symbol_aliases(reference_counts)
        for name, alias in aliases.items():
            size += quoted_size(alias) - quoted_size(name)
            size += reference_counts[name] * (
                quoted_size("#/$defs/" + alias) - quoted_size("#/$defs/" + name))
        return size, aliases

    def share_annotations(graph: dict[str, object]) -> dict[str, object]:
        literal_ids: dict[tuple[object, ...], int] = {}
        full_keys: dict[int, int] = {}
        body_keys: dict[int, int] = {}

        def literal_key(value: object) -> int:
            if type(value) in {dict, list}:
                identity = id(value)
                if identity in full_keys:
                    return full_keys[identity]
                if type(value) is dict:
                    pairs = tuple((field, literal_key(child))
                                  for field, child in sorted(value.items()))
                    signature = ("object", pairs)
                    body = ("object", tuple(pair for pair in pairs
                                            if pair[0] != "description"))
                    body_keys[identity] = literal_ids.setdefault(body, len(literal_ids))
                else:
                    signature = ("array", tuple(literal_key(child) for child in value))
                result = literal_ids.setdefault(signature, len(literal_ids))
                full_keys[identity] = result
                return result
            signature = ("scalar", key(value))
            return literal_ids.setdefault(signature, len(literal_ids))

        groups: dict[int, list[object]] = {}

        def collect(node: object) -> None:
            if type(node) is not dict:
                return
            if "$defs" not in node:
                groups.setdefault(body_keys[id(node)], []).append(node)
            for child in children(node):
                collect(child)
            for child in node.get("$defs", {}).values():
                collect(child)

        literal_key(graph)
        collect(graph)
        used = set(graph.get("$defs", {}))
        selected: dict[int, str] = {}
        eligible: list[tuple[str, int, list[object]]] = []
        for identity, occurrences in groups.items():
            meanings = {key({"description": node["description"]}) if "description" in node
                        else "" for node in occurrences}
            if len(occurrences) < 2 or len(meanings) < 2:
                continue
            representative = occurrences[0]
            body = key({field: value for field, value in representative.items()
                        if field != "description"})
            eligible.append((body, identity, occurrences))
        for body, identity, occurrences in sorted(eligible, key=lambda item: item[0]):
            number = len(selected)
            name = f"a{number}"
            while name in used:
                number += 1
                name = f"a{number}"
            reference = key({"$ref": "#/$defs/" + name})
            gain = (len(occurrences) * (len(body.encode("utf-8"))
                    - len(reference.encode("utf-8")))
                    - len(body.encode("utf-8")) - len(key(name).encode("utf-8")) - 2)
            if gain > 0:
                selected[identity] = name
                used.add(name)
        targets: dict[str, object] = {}

        def visit(node: object) -> object:
            if type(node) is not dict:
                return node
            name = selected.get(body_keys[id(node)]) if "$defs" not in node else None
            result = dict(node)
            if "properties" in node:
                result["properties"] = {name: visit(child)
                                        for name, child in node["properties"].items()}
            if "anyOf" in node:
                result["anyOf"] = [visit(child) for child in node["anyOf"]]
            if "items" in node:
                result["items"] = visit(node["items"])
            if "$defs" in node:
                result["$defs"] = {name: visit(child) for name, child in node["$defs"].items()}
            if name is not None:
                target = {field: value for field, value in result.items()
                          if field != "description"}
                if name in targets and key(targets[name]) != key(target):
                    raise ValueError("annotation sharing changed an exact rule body")
                targets[name] = target
                result = {"$ref": "#/$defs/" + name}
                if "description" in node:
                    result["description"] = node["description"]
            return result

        result = visit(graph)
        assert type(result) is dict
        if targets:
            result.setdefault("$defs", {}).update(targets)
        return result

    seeded = dict(shared)
    shared.clear()
    baseline, baseline_aliases = assemble()
    baseline_bytes = len(key(baseline).encode("utf-8"))
    current_bytes, expected_aliases = baseline_bytes, baseline_aliases
    if seeded:
        shared.update(seeded)
        current_bytes, expected_aliases = trial_size()
        if current_bytes >= baseline_bytes:
            shared.clear()
            current_bytes, expected_aliases = baseline_bytes, baseline_aliases
    for identity, times in sorted(counts.items(), key=lambda item: source_order[item[0]]):
        if identity in shared or (times <= 1 and identity not in existing):
            continue
        shared[identity] = existing.get(identity, f"n{len(shared)}")
        candidate_bytes, candidate_aliases = trial_size()
        if candidate_bytes < current_bytes:
            current_bytes, expected_aliases = candidate_bytes, candidate_aliases
        else:
            del shared[identity]
    result, final_aliases = assemble() if shared else (baseline, baseline_aliases)
    if (final_aliases != expected_aliases or len(key(result).encode("utf-8")) != current_bytes or current_bytes > baseline_bytes
            or (shared and current_bytes >= baseline_bytes)):
        raise ValueError("schema sharing does not preserve its complete UTF-8 cost guard")
    candidate, candidate_aliases = compact_symbols(share_annotations(result))
    candidate_bytes = len(key(candidate).encode("utf-8"))
    if candidate_bytes < current_bytes:
        result, current_bytes = candidate, candidate_bytes
        final_aliases = {name: candidate_aliases[alias] for name, alias in final_aliases.items()}
    if len(key(result).encode("utf-8")) != current_bytes:
        raise ValueError("annotation sharing changed complete UTF-8 cost accounting")
    result_fingerprint, result_definitions = schema_rule_fingerprints(result)
    if result_fingerprint != original_fingerprint or any(
        result_definitions.get(final_aliases[aliases[name]]) != identity for name, identity in definition_fingerprints.items()
    ):
        raise ValueError("schema factoring changed the declared rule graph")
    return result


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
    'factor_rule_schema',
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
]
