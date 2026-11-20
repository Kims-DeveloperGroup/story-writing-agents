"""Invocation-local observation of Codex CLI progress events."""

from __future__ import annotations

import json
from collections.abc import Callable

from .agent_context.progress import (
    ProviderStreamEvent,
    ProviderStreamEventKind,
    ProviderStreamSink,
)
from .codex_event_decoding import (
    extract_stream_text as _extract_stream_text,
    finite_float as _finite_float,
    reject_constant as _reject_constant,
    reject_duplicate_fields as _reject_duplicate_fields,
    resolve_aliased_field as _resolve_aliased_field,
)

__all__ = ["make_stream_line_handler"]


def make_stream_line_handler(
    agent_id: str, stream_sink: ProviderStreamSink | None,
) -> Callable[[bytes], None]:
    """Create a line handler and its state for one Codex invocation."""
    emitted_len_by_item: dict[str, int] = {}
    item_kinds_by_id: dict[str, ProviderStreamEventKind] = {}
    tombstoned_ids: set[str] = set()

    def _retire_id(target_id: object) -> None:
        if isinstance(target_id, str):
            stripped_id = target_id.strip()
            if stripped_id:
                emitted_len_by_item.pop(stripped_id, None)
                item_kinds_by_id.pop(stripped_id, None)
                tombstoned_ids.add(stripped_id)

    def handle_stream_line(raw_line: bytes) -> None:
        if stream_sink is None:
            return
        stripped = raw_line.strip()
        if not stripped:
            return
        try:
            event = json.loads(
                stripped.decode("utf-8", errors="strict"),
                object_pairs_hook=_reject_duplicate_fields,
                parse_constant=_reject_constant,
                parse_float=_finite_float,
            )
            if type(event) is not dict:
                return
            event_type = event.get("type")
            if event_type == "turn.started":
                stream_sink(
                    ProviderStreamEvent(
                        ProviderStreamEventKind.STATUS,
                        agent_id,
                        "Generating response...",
                    )
                )
            elif event_type in {"item.updated", "item.started", "item.completed"}:
                item = event.get("item")
                if type(item) is not dict:
                    if event_type == "item.completed":
                        for cand in (event.get("item_id"), event.get("id")):
                            _retire_id(cand)
                    return

                top_id, top_id_conflict = _resolve_aliased_field(event, "item_id", "id")
                if top_id_conflict:
                    _retire_id(event.get("item_id"))
                    _retire_id(event.get("id"))
                    _retire_id(item.get("id"))
                    _retire_id(item.get("item_id"))
                    return

                nested_id, id_conflict = _resolve_aliased_field(item, "id", "item_id")
                if id_conflict:
                    _retire_id(top_id)
                    _retire_id(item.get("id"))
                    _retire_id(item.get("item_id"))
                    return
                if top_id is not None and nested_id is not None and top_id != nested_id:
                    _retire_id(top_id)
                    _retire_id(nested_id)
                    return
                raw_id = nested_id if nested_id is not None else top_id
                item_id = raw_id if raw_id else None

                if (top_id is not None and top_id in tombstoned_ids) or (
                    nested_id is not None and nested_id in tombstoned_ids
                ) or (item_id is not None and item_id in tombstoned_ids):
                    return

                if "item_type" in event:
                    raw_top_type = event.get("item_type")
                    if raw_top_type is None or not isinstance(raw_top_type, str) or not raw_top_type.strip():
                        if event_type == "item.completed":
                            _retire_id(item_id)
                        return
                    top_type = raw_top_type.strip().lower()
                else:
                    top_type = None

                def _cleanup_completed_item() -> None:
                    if event_type == "item.completed":
                        _retire_id(item_id)

                nested_type, type_conflict = _resolve_aliased_field(
                    item, "type", "item_type", normalize_case=True
                )
                if type_conflict:
                    _cleanup_completed_item()
                    return
                if top_type is not None and nested_type is not None:
                    if top_type != nested_type:
                        _cleanup_completed_item()
                        return
                    resolved_type = top_type
                elif nested_type is not None:
                    resolved_type = nested_type
                elif top_type is not None:
                    resolved_type = top_type
                else:
                    resolved_type = None

                current_kind: ProviderStreamEventKind | None = None
                if resolved_type is not None:
                    if resolved_type == "reasoning":
                        explicit_kind = ProviderStreamEventKind.THOUGHT_DELTA
                    elif resolved_type == "agent_message":
                        explicit_kind = ProviderStreamEventKind.TEXT_DELTA
                    else:
                        _cleanup_completed_item()
                        return
                    if item_id is not None:
                        if item_id in item_kinds_by_id and item_kinds_by_id[item_id] != explicit_kind:
                            _cleanup_completed_item()
                            return
                        item_kinds_by_id[item_id] = explicit_kind
                    current_kind = explicit_kind
                elif item_id is not None and item_id in item_kinds_by_id:
                    current_kind = item_kinds_by_id[item_id]
                else:
                    _cleanup_completed_item()
                    return

                text_key = (
                    "summary"
                    if current_kind == ProviderStreamEventKind.THOUGHT_DELTA
                    else "content"
                )
                text = _extract_stream_text(
                    item.get("text") or item.get(text_key) or ""
                )
                if item_id is not None:
                    if event_type == "item.completed":
                        prev_len = emitted_len_by_item.pop(item_id, 0)
                        item_kinds_by_id.pop(item_id, None)
                        tombstoned_ids.add(item_id)
                    else:
                        prev_len = emitted_len_by_item.get(item_id, 0)
                    delta = text[prev_len:] if len(text) > prev_len else ""
                    if event_type != "item.completed":
                        emitted_len_by_item[item_id] = len(text)
                else:
                    delta = text

                if delta:
                    stream_sink(
                        ProviderStreamEvent(
                            current_kind,
                            agent_id,
                            delta,
                            )
                    )
            elif event_type in {"item.delta", "response.output_item.delta"}:
                if "item_id" in event:
                    raw_top_id = event.get("item_id")
                    if raw_top_id is None or not isinstance(raw_top_id, str) or not raw_top_id.strip():
                        return
                    top_id = raw_top_id.strip()
                else:
                    top_id = None

                if top_id is not None and top_id in tombstoned_ids:
                    return

                if "item_type" in event:
                    raw_top_type = event.get("item_type")
                    if raw_top_type is None or not isinstance(raw_top_type, str) or not raw_top_type.strip():
                        return
                    top_type = raw_top_type.strip().lower()
                else:
                    top_type = None

                raw_delta = event.get("delta")
                item_obj = event.get("item")

                nested_id = None
                nested_type = None
                if type(item_obj) is dict:
                    nested_id, id_conflict = _resolve_aliased_field(item_obj, "id", "item_id")
                    if id_conflict:
                        _retire_id(top_id)
                        _retire_id(item_obj.get("id"))
                        _retire_id(item_obj.get("item_id"))
                        return
                    if nested_id is not None and nested_id in tombstoned_ids:
                        return
                    nested_type, type_conflict = _resolve_aliased_field(
                        item_obj, "type", "item_type", normalize_case=True
                    )
                    if type_conflict:
                        _retire_id(top_id)
                        _retire_id(nested_id)
                        return
                    nested_delta, delta_conflict = _resolve_aliased_field(item_obj, "delta", "text")
                    if delta_conflict:
                        _retire_id(top_id)
                        _retire_id(nested_id)
                        return
                    if raw_delta is None:
                        raw_delta = nested_delta
                    elif nested_delta is not None and str(raw_delta) != nested_delta:
                        _retire_id(top_id)
                        _retire_id(nested_id)
                        return

                if top_id is not None and nested_id is not None and top_id != nested_id:
                    _retire_id(top_id)
                    _retire_id(nested_id)
                    return
                raw_id = nested_id if nested_id is not None else top_id
                if raw_id is not None and raw_id in tombstoned_ids:
                    return

                if top_type is not None and nested_type is not None:
                    if top_type != nested_type:
                        return
                    resolved_type = top_type
                elif top_type is not None:
                    resolved_type = top_type
                elif nested_type is not None:
                    resolved_type = nested_type
                else:
                    resolved_type = None

                item_id = str(raw_id) if raw_id is not None and str(raw_id) else None
                if item_id is not None and item_id in tombstoned_ids:
                    return
                kind: ProviderStreamEventKind | None = None

                if resolved_type is not None:
                    if resolved_type == "reasoning":
                        explicit_kind = ProviderStreamEventKind.THOUGHT_DELTA
                    elif resolved_type == "agent_message":
                        explicit_kind = ProviderStreamEventKind.TEXT_DELTA
                    else:
                        return
                    if item_id is not None:
                        if item_id in item_kinds_by_id and item_kinds_by_id[item_id] != explicit_kind:
                            return
                        item_kinds_by_id[item_id] = explicit_kind
                    kind = explicit_kind
                elif item_id is not None and item_id in item_kinds_by_id:
                    kind = item_kinds_by_id[item_id]
                else:
                    return

                delta_text = _extract_stream_text(raw_delta)
                if delta_text:
                    if item_id is not None:
                        emitted_len_by_item[item_id] = (
                            emitted_len_by_item.get(item_id, 0) + len(delta_text)
                        )
                    stream_sink(
                        ProviderStreamEvent(
                            kind,
                            agent_id,
                            delta_text,
                        )
                    )
        except Exception:
            pass

    return handle_stream_line
