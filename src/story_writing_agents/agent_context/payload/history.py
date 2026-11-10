"""Pure history counts and current-field projections."""

from __future__ import annotations

import json
from ...conversations.session_context import SessionHistorySnapshot
from ..actions import (
    ContextReadObservation,
    ContextSourceBoundaryKind,
)
from ..contracts.call_offers import ContextInvocationKind
from ..contracts.errors import (
    ContextExecutionErrorCode,
    execution_error,
)
from ..contracts.payload import ContextPayloadRequest
from ..contracts.planning_inputs import ContextMaterial
from ..manifest import (
    ContextSemanticChannel,
    ContextSourceKind,
)
from .actions import (
    DuplicateJsonKey,
    unique_json_object,
)
from .records import wire_projected_content


def conversation_read_record_count(
    observation: ContextReadObservation,
) -> int:
    """Count readable collaboration records without retaining their bodies."""

    records = 0
    for evidence in observation.evidence:
        receipt = evidence.receipt
        if receipt.source_kind is not ContextSourceKind.CONVERSATION:
            continue
        if receipt.boundary.kind is ContextSourceBoundaryKind.RECORDS:
            assert receipt.boundary.start is not None
            assert receipt.boundary.end is not None
            records += receipt.boundary.end - receipt.boundary.start + 1
            continue
        if receipt.boundary.kind is not ContextSourceBoundaryKind.WHOLE:
            continue
        try:
            value = json.loads(evidence.content)
        except (json.JSONDecodeError, RecursionError, TypeError, UnicodeError):
            continue
        if type(value) is dict and type(value.get("records")) is list:
            records += len(value["records"])
    return records


def conversation_counts(material: ContextMaterial) -> tuple[int, int]:
    """Return complete/omitted turn counts from one validated snapshot body."""

    try:
        value = json.loads(
            material.body_utf8,
            object_pairs_hook=unique_json_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (DuplicateJsonKey, json.JSONDecodeError, TypeError, ValueError):
        raise execution_error(ContextExecutionErrorCode.INVALID_VALUE) from None
    if type(value) is not dict:
        raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    window = value.get("window")
    if window is None:
        return len(material.records), 0
    if (
        type(window) is not dict
        or type(window.get("total_complete_turns")) is not int
        or type(window.get("omitted_turns")) is not int
    ):
        raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    return window["total_complete_turns"], window["omitted_turns"]


def history_current_fields(
    history: SessionHistorySnapshot,
    request: ContextPayloadRequest,
) -> dict[str, frozenset[str]]:
    """Find exact visible messages already carried in runtime-owned current input."""
    workflows = {
        ContextInvocationKind.LEAD: "lead_front_channel",
        ContextInvocationKind.LEAD_SYNTHESIS: "lead_synthesis",
    }
    if request.invocation_kind not in workflows:
        return {}
    supplied_by_locator: dict[str, str] = {}
    supplied_by_event: dict[str, str] = {}
    for material in request.materials:
        if material.entry.semantic_channel is not ContextSemanticChannel.CURRENT_INPUT:
            continue
        current = wire_projected_content((material.entry,), material.body_utf8)
        if type(current) is not dict:
            continue
        if current.get("workflow") != workflows[request.invocation_kind]:
            continue
        command = current.get("command")
        aliases = current.get("input_aliases")
        if type(command) is str and type(aliases) is list:
            for alias in aliases:
                if (
                    type(alias) is dict
                    and alias.get("kind") == "user_input"
                    and type(alias.get("locator")) is str
                ):
                    supplied_by_locator[alias["locator"]] = command
        input_values = current.get("inputs")
        inputs = list(input_values) if type(input_values) in {list, tuple} else []
        execution = current.get("structured_recommendation_execution")
        if type(execution) is dict:
            selected = execution.get("selected_inputs")
            if type(selected) in {list, tuple}:
                inputs.extend(selected)
        for item in inputs:
            if type(item) is not dict or type(item.get("locator")) is not str:
                continue
            if type(item.get("text")) is str:
                supplied_by_locator[item["locator"]] = item["text"]
            reply = item.get("reply")
            if type(reply) is dict and type(reply.get("message")) is str:
                supplied_by_locator[item["locator"]] = reply["message"]
        replies = current.get("replies", ())
        if type(replies) not in {list, tuple}:
            continue
        for reply in replies:
            if (
                type(reply) is dict
                and type(reply.get("reply_event_id")) is str
                and type(reply.get("message")) is str
            ):
                supplied_by_event[reply["reply_event_id"]] = reply["message"]
    result: dict[str, frozenset[str]] = {}
    for conversation in history.conversations:
        for record in conversation.records:
            supplied = supplied_by_event.get(record.event_id)
            if supplied is None:
                supplied = supplied_by_locator.get(f"conversation/{record.event_id}")
            if supplied is None and record.sender.kind.value == "user":
                supplied = supplied_by_locator.get(
                    f"conversation/{record.turn_id}/user_input",
                )
            if supplied is None:
                continue
            fields = frozenset(
                item.key.value for item in record.fields
                if item.key.value in {"message.content", "specialist_reply.content"}
                and item.text == supplied
            )
            if fields:
                result[record.event_id] = fields
    return result


__all__ = [
    'conversation_read_record_count',
    'conversation_counts',
    'history_current_fields',
]
