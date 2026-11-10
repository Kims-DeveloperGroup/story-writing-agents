"""Pure context record and source-content wire projections."""

from __future__ import annotations

import json
from ..accounting import ContextComponentKind
from ..actions import ContextActionError
from ..contracts.checkpoints import ContextCheckpointRecord
from ..contracts.context_values import canonical_bytes
from ..contracts.errors import (
    ContextExecutionErrorCode,
    execution_error,
)
from ..manifest import (
    ContextManifestEntry,
    ContextSemanticChannel,
    ContextSourceKind,
)
from .actions import (
    DuplicateJsonKey,
    unique_json_object,
)
from collections.abc import Mapping


_STRUCTURED_WIRE_SOURCE_KINDS = {
    ContextSourceKind.RUNTIME_INPUT,
    ContextSourceKind.CONVERSATION,
    ContextSourceKind.ACTIVE_DEBATE,
    ContextSourceKind.OWN_AGENT_STATE,
}


def projected_component_kind(entry: ContextManifestEntry) -> ContextComponentKind:
    if entry.semantic_channel is ContextSemanticChannel.CURRENT_INPUT:
        return ContextComponentKind.CURRENT_INPUT
    if entry.semantic_channel is ContextSemanticChannel.CANDIDATE:
        return ContextComponentKind.CANDIDATE
    if entry.source_kind is ContextSourceKind.AGENT_DEFINITION:
        return ContextComponentKind.ROLE_SYSTEM_PROMPT
    if entry.source_kind is ContextSourceKind.CONVERSATION:
        return ContextComponentKind.CONVERSATION_CONTEXT
    if entry.source_kind is ContextSourceKind.ACTIVE_DEBATE:
        return ContextComponentKind.DEBATE_MESSAGES
    if entry.source_kind is ContextSourceKind.OWN_AGENT_STATE:
        return ContextComponentKind.AGENT_STATE
    return ContextComponentKind.RETRIEVED_OBSERVATION


def source_number(source_alias: str) -> int:
    """Project one validated internal ``sN`` alias as its wire integer."""

    if (
        type(source_alias) is not str
        or not source_alias.startswith("s")
        or not source_alias[1:].isdigit()
        or source_alias[1:].startswith("0")
    ):
        raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    return int(source_alias[1:])


def wire_projected_content(
    unit_entries: tuple[ContextManifestEntry, ...],
    body_utf8: bytes,
) -> object:
    """Losslessly remove one redundant JSON-string layer from structured sources."""

    content = body_utf8.decode("utf-8", errors="strict")
    if any(
        entry.source_kind not in _STRUCTURED_WIRE_SOURCE_KINDS for entry in unit_entries
    ):
        return content
    try:
        parsed = json.loads(
            content,
            object_pairs_hook=unique_json_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (DuplicateJsonKey, json.JSONDecodeError, TypeError, ValueError):
        return content
    try:
        if canonical_bytes(parsed) == body_utf8:
            return parsed
    except ContextActionError:
        pass
    return content


def _wire_duplicate_value(
    value: object,
    duplicate_channels: Mapping[str, str | None],
) -> object:
    """Replace only an exact uniquely identified P0 byte duplicate."""

    if type(value) is str:
        channel = duplicate_channels.get(value)
        if channel is not None:
            return ["=", channel]
    return value


def wire_record_value(
    entry: ContextManifestEntry,
    record: ContextCheckpointRecord,
    body_utf8: bytes,
    duplicate_channels: Mapping[str, str | None],
) -> list[object]:
    """Project one positional conversation or debate record patch."""

    value = wire_projected_content((entry,), body_utf8)
    alias = record.record_alias
    if type(value) is not dict:
        return [alias, value]
    if entry.source_kind is ContextSourceKind.CONVERSATION:
        user = value.get("user_message")
        reply = value.get("agent_reply")
        if type(user) is dict and type(reply) is dict:
            return [
                alias,
                user.get("content"),
                reply.get("sender"),
                reply.get("content"),
            ]
        return [alias, value]
    if entry.source_kind is ContextSourceKind.ACTIVE_DEBATE:
        if record.cursor.record_id == "state":
            return [
                alias,
                "s",
                value.get("input_kind"),
                value.get("revision_id"),
                value.get("rounds_completed"),
            ]
        return [
            alias,
            "m",
            value.get("sender"),
            value.get("phase"),
            value.get("round"),
            value.get("revision"),
            value.get("kind"),
            _wire_duplicate_value(value.get("content"), duplicate_channels),
        ]
    return [alias, value]


__all__ = [
    'projected_component_kind',
    'source_number',
    'wire_projected_content',
    'wire_record_value',
]
