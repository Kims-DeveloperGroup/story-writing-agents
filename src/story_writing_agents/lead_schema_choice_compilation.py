"""Compile closed Lead choices while preserving accepted values and wire cost."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy

from .provider_schema_projection import factor_rule_schema, project_lead_scope_schema
from .structured_output_validation import schema_rule_fingerprint, schema_rule_fingerprints


def compile_closed_lead_choices(
    schema: Mapping[str, object],
) -> dict[str, object]:
    """Certify a same-payload one-property distribution of closed alternatives."""
    original = deepcopy(dict(schema))
    original_fingerprints = schema_rule_fingerprints(original)

    def plain_json_graph(value: object) -> bool:
        pending = [(value, False)]
        visiting: set[int] = set()
        complete: set[int] = set()
        try:
            while pending:
                node, leaving = pending.pop()
                kind = type(node)
                if kind is dict or kind is list:
                    identity = id(node)
                    if leaving:
                        visiting.remove(identity)
                        complete.add(identity)
                        continue
                    if identity in visiting:
                        return False
                    if identity in complete:
                        continue
                    visiting.add(identity)
                    pending.append((node, True))
                    if kind is dict:
                        if any(type(name) is not str for name in node):
                            return False
                        pending.extend((child, False) for child in node.values())
                    else:
                        pending.extend((child, False) for child in node)
                elif node is None or kind is str or kind is int or kind is bool:
                    continue
                elif kind is not float or node.hex() in ("nan", "inf", "-inf"):
                    return False
        except Exception:
            return False
        return True

    # The complete proof above remains authoritative for this private snapshot.
    plain_context = plain_json_graph(original)
    working = deepcopy(original)
    fingerprints: dict[str, str] = {}
    grouping_prefill: tuple[list, list[str]] | None = None
    trace: list[tuple[tuple[object, ...], str, tuple[tuple[int, ...], ...],
                      bool, dict[str, object], str]] = []

    def encoded(value: object) -> str:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":"))

    probe_definitions = dict(original.get("$defs", {}))
    probe_names: dict[str, str] = {}

    def collect_original_rules(node: object) -> None:
        if type(node) is not dict:
            return
        key = encoded(node)
        if key in probe_names:
            return
        name = f"p{len(probe_names)}"
        while name in probe_definitions:
            name += "_"
        probe_names[key] = name
        probe_definitions[name] = node
        for field in sorted(node.get("properties", {})):
            collect_original_rules(node["properties"][field])
        for child in node.get("anyOf", ()):
            collect_original_rules(child)
        if "items" in node:
            collect_original_rules(node["items"])

    collect_original_rules(original)
    for name in sorted(original.get("$defs", {})):
        collect_original_rules(original["$defs"][name])
    batch_root, batch_fingerprints = schema_rule_fingerprints(
        {**original, "$defs": probe_definitions})
    if batch_root != original_fingerprints[0] or any(
            batch_fingerprints[name] != value
            for name, value in original_fingerprints[1].items()):
        raise ValueError("closed-choice fingerprint batch changes its original context")
    fingerprints.update({key: batch_fingerprints[name] for key, name in probe_names.items()})

    def fingerprint(node: Mapping[str, object]) -> str:
        nonlocal grouping_prefill
        key = encoded(node)
        if key not in fingerprints:
            if grouping_prefill is not None:
                choices, names = grouping_prefill
                grouping_prefill = None
                prefill_grouping_fingerprints(choices, names)
            if key not in fingerprints:
                fingerprints[key] = (schema_rule_fingerprint(original, selected=node)
                                     if plain_context and plain_json_graph(node)
                                     else schema_rule_fingerprints(original, selected=node)[0])
        return fingerprints[key]

    def prefill_grouping_fingerprints(choices: list, names: list[str]) -> None:
        nonlocal fingerprints
        if len(names) < 2:
            return
        try:
            # Check source trees before intentional probe aliases are introduced.
            pending: list[object] = [original, choices]
            seen: set[int] = set()
            while pending:
                value = pending.pop()
                if type(value) in (dict, list):
                    if id(value) in seen:
                        return
                    seen.add(id(value))
                    if type(value) is dict:
                        if any(type(key) is not str for key in value):
                            return
                        pending.extend(value.values())
                    else:
                        pending.extend(value)
                elif value is not None and type(value) not in (bool, int, float, str):
                    return
            missing: dict[str, Mapping[str, object]] = {}
            for name in names:
                for choice in choices:
                    if closed(choice) and name in choice["properties"]:
                        for field, child in sorted(choice["properties"].items()):
                            if field == name:
                                continue
                            key = encoded(child)
                            if key not in fingerprints and key not in missing:
                                missing[key] = child
            if len(missing) < 2:
                return
            definitions = dict(original.get("$defs", {}))
            probe_keys: dict[str, str] = {}
            for key, node in missing.items():
                name = f"_grouping_{len(probe_keys)}"
                while name in definitions:
                    name += "_"
                definitions[name] = node
                probe_keys[key] = name
            _, digests = schema_rule_fingerprints({
                "$ref": f"#/$defs/{next(iter(probe_keys.values()))}",
                "$defs": definitions,
            })
            if any(digests[name] != expected
                   for name, expected in original_fingerprints[1].items()):
                return
            additions = {key: digests[name] for key, name in probe_keys.items()}
            if any(type(value) is not str for value in additions.values()):
                return
            updated = {**fingerprints, **additions}
        except Exception:
            # The unchanged ordered queries retain original failure authority.
            return
        fingerprints = updated

    try:
        final_costs: dict[str, int] | None = {}
    except MemoryError:
        final_costs = None

    def final_cost(graph: Mapping[str, object]) -> int:
        key: str | None = None
        if final_costs is not None:
            try:
                pending = [graph]
                containers: set[int] = set()
                while pending:
                    value = pending.pop()
                    if type(value) is dict:
                        if any(type(name) is not str for name in value):
                            break
                        identity = id(value)
                        if identity in containers:
                            break
                        containers.add(identity)
                        pending.extend(value.values())
                    elif type(value) is list:
                        identity = id(value)
                        if identity in containers:
                            break
                        containers.add(identity)
                        pending.extend(value)
                    elif value is not None and type(value) not in (str, int, float, bool):
                        break
                else:
                    key = json.dumps(
                        graph, sort_keys=False, ensure_ascii=False, allow_nan=False,
                        separators=(",", ":"),
                    )
                    cached_cost = final_costs.get(key)
                    if cached_cost is not None:
                        return cached_cost
            except (TypeError, ValueError, OverflowError, RecursionError, MemoryError):
                key = None
        compact = factor_rule_schema(project_lead_scope_schema(factor_rule_schema(graph)))
        cost = len(encoded(compact).encode("utf-8", "strict"))
        if final_costs is not None and key is not None:
            try:
                final_costs[key] = cost
            except MemoryError:
                pass
        return cost

    def at(graph: dict[str, object], path: tuple[object, ...]) -> dict[str, object]:
        node = graph
        for key in path:
            node = node[key]
        return node

    def replace_at(graph: dict[str, object], path: tuple[object, ...], node: dict):
        if not path:
            return node
        parent = graph
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = node
        return graph

    def paths(node: dict[str, object], path: tuple[object, ...] = ()):
        for name, child in node.get("properties", {}).items():
            yield from paths(child, path + ("properties", name))
        if "items" in node:
            yield from paths(node["items"], path + ("items",))
        for number, child in enumerate(node.get("anyOf", ())):
            yield from paths(child, path + ("anyOf", number))
        if "anyOf" in node:
            yield path

    def closed(node: dict[str, object]) -> bool:
        properties = node.get("properties")
        required = node.get("required")
        return (node.get("type") == "object" and node.get("additionalProperties") is False
                and type(properties) is dict and "variant_id" not in properties and type(required) is list
                and len(required) == len(set(required)) and set(required) == set(properties)
                and not set(node) - {"type", "properties", "required",
                                     "additionalProperties", "description", "x-swa-property-policy"})

    size = initial_size = final_cost(working)
    while True:
        accepted = False
        for path in paths(working):
            before = at(working, path)
            choices = before["anyOf"]
            names = sorted({name for choice in choices if closed(choice)
                            for name in choice["properties"]})
            grouping_prefill = (choices, names)
            best = None
            best_size = size
            for name in names:
                groups: dict[object, list[int]] = {}
                for number, choice in enumerate(choices):
                    if not closed(choice) or name not in choice["properties"]:
                        key = ("unchanged", number)
                    else:
                        common = tuple((field, fingerprint(child))
                                       for field, child in sorted(choice["properties"].items())
                                       if field != name)
                        metadata = {field: value for field, value in choice.items()
                                    if field != "properties"}
                        key = (encoded(metadata), tuple(sorted(choice["properties"])), common)
                    groups.setdefault(key, []).append(number)
                partition = tuple(tuple(numbers) for numbers in groups.values())
                if all(len(numbers) == 1 for numbers in partition):
                    continue
                distributed = []
                for numbers in partition:
                    branch = deepcopy(choices[numbers[0]])
                    if len(numbers) > 1:
                        branch["properties"][name] = {"anyOf": [
                            deepcopy(choices[number]["properties"][name]) for number in numbers]}
                    distributed.append(branch)
                collapsed = len(distributed) == 1
                if collapsed:
                    annotations = {key: value for key, value in before.items() if key != "anyOf"}
                    if set(annotations) - {"description", "$defs"} or any(
                        key in distributed[0] and distributed[0][key] != value
                        for key, value in annotations.items()
                    ):
                        continue
                    after = {**distributed[0], **annotations}
                else:
                    after = {**before, "anyOf": distributed}
                trial = replace_at(deepcopy(working), path, after)
                trial_size = final_cost(trial)
                if trial_size < best_size:
                    best_size = trial_size
                    best = (name, partition, collapsed, after, trial)
            if best is not None:
                name, partition, collapsed, after, working = best
                trace.append((path, name, partition, collapsed, deepcopy(before),
                              fingerprint(after)))
                size = best_size
                accepted = True
                break
        if not accepted:
            break

    inverse = deepcopy(working)
    for path, name, partition, collapsed, before, after_fingerprint in reversed(trace):
        after = at(inverse, path)
        if fingerprint(after) != after_fingerprint:
            raise ValueError("closed-choice inverse has a changed derived occurrence")
        if collapsed:
            branch = deepcopy(after)
            original_branch = before["anyOf"][partition[0][0]]
            for key, value in before.items():
                if key == "anyOf":
                    continue
                if key not in branch or branch[key] != value:
                    raise ValueError("closed-choice inverse changes a wrapper annotation")
                if key not in original_branch:
                    del branch[key]
            distributed = [branch]
        else:
            distributed = after["anyOf"]
        if len(distributed) != len(partition):
            raise ValueError("closed-choice inverse has an incomplete partition")
        rebuilt = [None] * len(before["anyOf"])
        for branch, numbers in zip(distributed, partition, strict=True):
            if len(numbers) == 1:
                values = [branch]
            else:
                varying = branch["properties"][name]
                if set(varying) != {"anyOf"} or len(varying["anyOf"]) != len(numbers):
                    raise ValueError("closed-choice inverse has a changed varying rule")
                values = []
                for child in varying["anyOf"]:
                    value = deepcopy(branch)
                    value["properties"][name] = deepcopy(child)
                    values.append(value)
            for number, value in zip(numbers, values, strict=True):
                if rebuilt[number] is not None or fingerprint(value) != fingerprint(
                    before["anyOf"][number]
                ):
                    raise ValueError("closed-choice inverse changes an original alternative")
                rebuilt[number] = value
        restored = {**before, "anyOf": rebuilt}
        if any(value is None for value in rebuilt) or fingerprint(restored) != fingerprint(before):
            raise ValueError("closed-choice inverse does not recover its ordered source")
        # Keep the captured occurrence's alias spelling for earlier descendant traces.
        inverse = replace_at(inverse, path, deepcopy(before))
    if schema_rule_fingerprints(inverse) != original_fingerprints:
        raise ValueError("closed-choice inverse changes the root or an input definition")
    if working.get("$defs", {}) != original.get("$defs", {}):
        raise ValueError("closed-choice compiler changes an input definition")
    if final_cost(working) != size or (trace and size >= initial_size):
        raise ValueError("closed-choice compiler does not decrease complete final UTF-8 bytes")
    return working
