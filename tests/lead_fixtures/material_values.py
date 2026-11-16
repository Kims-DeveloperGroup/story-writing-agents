"""Existing Lead material values fixtures with explicit ownership."""
from __future__ import annotations
import copy


def expanded_context_payload(payload):
    if "shared_context_objects" not in payload:
        return payload
    result = copy.deepcopy(dict(payload))
    table = result.pop("shared_context_objects")
    result.pop("context_reuse")
    def expand(value, shape):
        if type(value) is not dict:
            return value
        if set(value) == {"shared_context_object_id"}:
            return copy.deepcopy(table[value["shared_context_object_id"]])
        value = dict(value)
        children = {
            "context": (("basis", "basis"), ("authoring_admission", "admission"),
                        ("core_document_readiness", "readiness"), ("core_document_assessment", "assessment")),
            "basis": (("authoring_admission", "admission"), ("core_document_readiness", "readiness")),
            "admission": (("assessment", "literal"),),
            "readiness": (("assessment", "assessment"),),
            "assessment": (("book_basis", "literal"),),
        }.get(shape, ())
        for field, child_shape in children:
            if field in value:
                value[field] = expand(value[field], child_shape)
        if shape == "assessment":
            rows = value.get("document_judgments")
            if type(rows) in (list, tuple):
                value["document_judgments"] = type(rows)(expand(row, "literal") for row in rows)
        return value

    def resolve(owner, key):
        value = owner.get(key)
        if value is not None:
            owner[key] = expand(value, "context")
    current, history = result.get("task_input"), result.get("session_history")
    if type(current) is dict:
        resolve(current, "document_change_requests")
        execution = current.get("confirmed_proposal_drafting_context")
        if type(execution) is dict:
            for key in ("request", "decision", "start"):
                resolve(execution, key)
            for item in execution.get("selected_inputs", []):
                resolve(item, "conversation_event")
    if type(history) is dict:
        for conversation in history.get("conversations", []):
            resolve(conversation, "document_change_requests")
            for record in conversation.get("records", []):
                resolve(record, "details")
    return result
