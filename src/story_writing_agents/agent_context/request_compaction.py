"""Lossless, call-local sharing of repeated runtime context objects."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

from .accounting import ContextComponentKind, JsonPath, canonical_provider_json


_MIN_SHARED_OBJECT_BYTES = 256
_CONTEXT_ENCODING_INSTRUCTION = (
    'At runtime context slots, {"shared_context_object_id":"oN"} means the exact JSON object '
    "in shared_context_objects[oN]. Resolve only within this request. Pool contents and "
    "user data are literal; never recursively resolve markers inside them. "
    "Keep each slot's original meaning and authority; history remains context "
    "only. Follow response_contract and the response schema for output; never encode output as "
    "shared_context_object_id."
)


def _context_slots(
    payload: dict[str, object],
) -> list[tuple[JsonPath, dict[str, object]]]:
    """Enumerate complete runtime-owned objects without traversing their bodies."""
    slots: list[tuple[JsonPath, dict[str, object]]] = []

    def add(path: JsonPath, value: object) -> None:
        if type(value) is dict:
            slots.append((path, value))

    history = payload.get("session_history")
    conversations = history.get("conversations") if type(history) is dict else None
    if type(conversations) in {list, tuple}:
        for conversation_index, conversation in enumerate(conversations):
            if type(conversation) is not dict:
                continue
            conversation_path: JsonPath = ("session_history", "conversations", conversation_index)
            records = conversation.get("records")
            if type(records) in {list, tuple}:
                for record_index, record in enumerate(records):
                    if type(record) is dict:
                        add(
                            conversation_path + ("records", record_index, "details"),
                            record.get("details"),
                        )
            add(
                conversation_path + ("document_change_requests",),
                conversation.get("document_change_requests"),
            )

    current = payload.get("task_input")
    if type(current) is dict:
        add(("task_input", "document_change_requests"), current.get("document_change_requests"))
        execution = current.get("confirmed_proposal_drafting_context")
        if type(execution) is dict:
            execution_path: JsonPath = ("task_input", "confirmed_proposal_drafting_context")
            for key in ("request", "decision", "start"):
                add(execution_path + (key,), execution.get(key))
            inputs = execution.get("selected_inputs")
            if type(inputs) in {list, tuple}:
                for input_index, item in enumerate(inputs):
                    if type(item) is dict:
                        add(
                            execution_path
                            + ("selected_inputs", input_index, "conversation_event"),
                            item.get("conversation_event"),
                        )
    return slots


def _is_prefix(prefix: JsonPath, path: JsonPath) -> bool:
    return path[: len(prefix)] == prefix


def _property_count(value: object) -> int:
    """Count JSON object members, including objects nested inside arrays."""
    count = 0
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            count += len(item)
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
    return count


def _protected_slot(
    path: JsonPath,
    component_paths: Mapping[JsonPath, ContextComponentKind],
    mandatory_reference_paths: tuple[JsonPath, ...],
) -> bool:
    if any(
        _is_prefix(path, reference) or _is_prefix(reference, path)
        for reference in mandatory_reference_paths
    ):
        return True
    return any(
        len(component_path) > len(path) and _is_prefix(path, component_path)
        for component_path in component_paths
    )


def _replace_slot(value: object, path: JsonPath, replacement: object) -> object:
    """Copy only the ancestry of an enumerated slot, leaving source data intact."""
    if not path:
        return replacement
    key, *remaining = path
    if type(value) is dict:
        copied = dict(value)
        copied[key] = _replace_slot(value[key], tuple(remaining), replacement)
        return copied
    assert isinstance(value, (list, tuple)) and type(key) is int
    copied_sequence = list(value)
    copied_sequence[key] = _replace_slot(value[key], tuple(remaining), replacement)
    return tuple(copied_sequence) if type(value) is tuple else copied_sequence


def compact_context_payload(
    payload: dict[str, object],
    component_paths: Mapping[JsonPath, ContextComponentKind],
    mandatory_reference_paths: tuple[JsonPath, ...] = (),
) -> tuple[dict[str, object], dict[JsonPath, ContextComponentKind]]:
    """Share profitable equal objects at closed context slots in one request.

    Equality uses strict sorted-key JSON, preserving value types, strings, and
    array order. Stored objects retain their original member order. Neither the
    payload nor its attribution map is mutated; no-op calls return the payload
    unchanged. References have no meaning inside stored or arbitrary source data.
    """
    paths = dict(component_paths)
    if "shared_context_objects" in payload or "context_reuse" in payload:
        return payload, paths
    slots = _context_slots(payload)
    if any(set(value) == {"shared_context_object_id"} for _, value in slots):
        return payload, paths

    groups: dict[bytes, list[tuple[JsonPath, dict[str, object]]]] = {}
    for path, value in slots:
        if _protected_slot(path, paths, mandatory_reference_paths):
            continue
        try:
            identity = json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8", errors="strict")
        except (RecursionError, TypeError, ValueError, UnicodeEncodeError):
            # Preserve the original provider encoder's invalid-input behavior.
            return payload, paths
        if len(identity) >= _MIN_SHARED_OBJECT_BYTES:
            groups.setdefault(identity, []).append((path, value))

    shared: dict[str, object] = {}
    replacements: list[tuple[JsonPath, dict[str, str]]] = []
    pooled_paths: dict[JsonPath, ContextComponentKind] = {}
    for identity, occurrences in groups.items():
        if len(occurrences) < 2:
            continue
        alias = f"o{len(shared) + 1}"
        reference = {"shared_context_object_id": alias}
        original = occurrences[0][1]
        group_size = len(occurrences) * len(identity)
        shared_size = len(canonical_provider_json({alias: original}))
        shared_size += len(occurrences) * len(canonical_provider_json(reference))
        original_properties = _property_count(original)
        shared_properties = 1 + original_properties + len(occurrences)
        if (
            shared_size >= group_size
            or shared_properties >= len(occurrences) * original_properties
        ):
            continue
        shared[alias] = original
        replacements.extend((path, reference) for path, _ in occurrences)
        pooled_paths[("shared_context_objects", alias)] = (
            ContextComponentKind.CURRENT_INPUT
            if any(path[0] == "task_input" for path, _ in occurrences)
            else ContextComponentKind.CONVERSATION_CONTEXT
        )
    if not shared:
        return payload, paths

    projected = payload
    for path, reference in replacements:
        projected = cast(dict[str, object], _replace_slot(projected, path, reference))
    projected["shared_context_objects"] = shared
    projected["context_reuse"] = {
        "format": "shared-objects-v1",
        "instruction": _CONTEXT_ENCODING_INSTRUCTION,
    }
    if (
        len(canonical_provider_json(projected)) >= len(canonical_provider_json(payload))
        or _property_count(projected) >= _property_count(payload)
    ):
        return payload, paths
    paths[("shared_context_objects",)] = ContextComponentKind.CONTROL
    paths[("context_reuse",)] = ContextComponentKind.CONTROL
    paths.update(pooled_paths)
    return projected, paths
