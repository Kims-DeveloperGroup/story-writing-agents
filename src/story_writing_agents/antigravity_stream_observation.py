"""Invocation-local observation of Antigravity CLI stream lines."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence

from .agent_context.progress import (
    ProviderStreamEvent,
    ProviderStreamEventKind,
    ProviderStreamSink,
)
from .antigravity_response_decoding import (
    reject_duplicate_fields as _reject_duplicate_fields,
    reject_constant as _reject_constant,
    finite_float as _finite_float,
)

__all__ = ["make_stream_observation"]


def make_stream_observation(
    agent_id: str,
    stream_sink: ProviderStreamSink | None,
) -> tuple[
    Callable[[bytes], None],
    Callable[[Callable[[], bytes] | None], tuple[Sequence[bytes], str | None, bool]],
]:
    """Supply one invocation's direct callback and its replay and observation finalizer."""
    captured_result_bytes: list[bytes] = []
    invalid_stream_line: str | None = None
    has_trailing_data_after_terminal: bool = False
    callback_lines_delivered: int = 0

    def emit_stream_event(event: ProviderStreamEvent) -> None:
        if stream_sink is not None:
            stream_sink(event)

    def _extract_string_message(value: object) -> str | None:
        """Extract validated string message from string or dict message field."""
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned if cleaned else None
        if isinstance(value, dict):
            msg = value.get("message")
            if isinstance(msg, str):
                cleaned = msg.strip()
                return cleaned if cleaned else None
        return None

    def handle_stream_line(raw_line: bytes) -> None:
        nonlocal callback_lines_delivered, invalid_stream_line, has_trailing_data_after_terminal
        callback_lines_delivered += 1
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
        except (ValueError, RecursionError, UnicodeError):
            if invalid_stream_line is None:
                invalid_stream_line = "malformed JSON line"
            return
        if type(event) is not dict:
            if invalid_stream_line is None:
                invalid_stream_line = "non-object JSON line"
            return

        has_type = "type" in event
        has_event = "event" in event
        has_discriminator = has_type or has_event
        if has_discriminator:
            types_to_check: list[str] = []
            for key in ("type", "event"):
                if key in event:
                    val = event.get(key)
                    if val is None or not isinstance(val, str):
                        if invalid_stream_line is None:
                            invalid_stream_line = "invalid event discriminator"
                        return
                    norm_val = val.strip().lower()
                    if not norm_val:
                        if invalid_stream_line is None:
                            invalid_stream_line = "unknown event type ''"
                        return
                    types_to_check.append(norm_val)
            if len(types_to_check) == 2 and types_to_check[0] != types_to_check[1]:
                if invalid_stream_line is None:
                    invalid_stream_line = "conflicting event discriminator"
                return
            event_type = types_to_check[0]
        else:
            event_type = ""

        is_terminal = False
        if not has_discriminator:
            is_terminal = (
                "structured_output" in event
                or (
                    "conversation_id" in event
                    and str(event.get("status", "")).upper() in {"SUCCESS", "ERROR"}
                )
            )
        elif event_type == "result" and isinstance(event.get("result"), dict):
            is_terminal = True

        if is_terminal:
            captured_result_bytes.append(stripped)
            target_dict = None
            if event_type == "result":
                res = event.get("result")
                if isinstance(res, dict):
                    target_dict = res
            elif not has_discriminator:
                target_dict = event
            if target_dict is not None and str(target_dict.get("status", "")).upper() == "ERROR":
                err_text = _extract_string_message(target_dict.get("error"))
                if err_text:
                    emit_stream_event(
                        ProviderStreamEvent(
                            ProviderStreamEventKind.STATUS,
                            agent_id,
                            f"Error: {err_text[:120]}",
                        )
                    )
            return

        if captured_result_bytes:
            has_trailing_data_after_terminal = True
            return

        if event_type in {
            "turn_start",
            "turn.started",
            "turn_started",
            "status",
        } or (not has_discriminator and str(event.get("status", "")).lower() == "running"):
            msg = None
            for key in ("text", "message", "status"):
                extracted = _extract_string_message(event.get(key))
                if extracted:
                    msg = extracted
                    break
            if msg is None:
                msg = "Generating response..."
            emit_stream_event(
                ProviderStreamEvent(
                    ProviderStreamEventKind.STATUS,
                    agent_id,
                    msg,
                )
            )
        elif event_type in {
            "thought",
            "reasoning",
            "thought_delta",
            "reasoning_delta",
        }:
            text = str(
                event.get("text")
                or event.get("delta")
                or event.get("thought")
                or event.get("reasoning")
                or ""
            )
            if text:
                emit_stream_event(
                    ProviderStreamEvent(
                        ProviderStreamEventKind.THOUGHT_DELTA,
                        agent_id,
                        text,
                    )
                )
        elif event_type in {
            "text_delta",
            "content_delta",
            "token",
            "agent_message",
            "agent_response",
            "text",
        }:
            is_agent_response = (
                event_type in {"agent_message", "agent_response"}
                and str(event.get("step_type") or "agent_response").strip().lower() == "agent_response"
            ) or (
                str(event.get("step_type") or "").strip().lower() == "agent_response"
            )
            if is_agent_response:
                raw_text = (
                    event.get("text")
                    if "text" in event
                    else event.get("delta")
                    if "delta" in event
                    else event.get("content")
                )
                if isinstance(raw_text, str) and raw_text:
                    emit_stream_event(
                        ProviderStreamEvent(
                            ProviderStreamEventKind.TEXT_DELTA,
                            agent_id,
                            raw_text,
                        )
                    )
        elif event_type == "init":
            init_data = event.get("init")
            if init_data is not None and type(init_data) is not dict:
                if invalid_stream_line is None:
                    invalid_stream_line = "init payload is not an object"
                return
            emit_stream_event(
                ProviderStreamEvent(
                    ProviderStreamEventKind.STATUS,
                    agent_id,
                    "Generating response...",
                )
            )
        elif event_type == "step_update":
            step_update = event.get("step_update")
            if type(step_update) is not dict:
                if invalid_stream_line is None:
                    invalid_stream_line = "step_update payload is not an object"
                return
            step_type_val = step_update.get("step_type")
            if step_type_val is not None and not isinstance(step_type_val, str):
                if invalid_stream_line is None:
                    invalid_stream_line = "invalid step_type discriminator"
                return
            step_type = str(step_type_val or "").strip().lower()

            raw_text_delta = step_update.get("text_delta")
            if raw_text_delta is not None and not isinstance(raw_text_delta, str):
                if invalid_stream_line is None:
                    invalid_stream_line = "invalid text_delta type"
                return
            raw_thought_delta = (
                step_update.get("thought_delta")
                or step_update.get("reasoning_delta")
            )
            if raw_thought_delta is not None and not isinstance(raw_thought_delta, str):
                if invalid_stream_line is None:
                    invalid_stream_line = "invalid thought_delta type"
                return

            text_delta = raw_text_delta or ""
            thought_delta = raw_thought_delta or ""
            if step_type in {"thought", "reasoning"}:
                if text_delta and not thought_delta:
                    thought_delta = text_delta
                text_delta = ""
            elif step_type != "agent_response":
                text_delta = ""

            if thought_delta:
                emit_stream_event(
                    ProviderStreamEvent(
                        ProviderStreamEventKind.THOUGHT_DELTA,
                        agent_id,
                        thought_delta,
                    )
                )
            if text_delta:
                emit_stream_event(
                    ProviderStreamEvent(
                        ProviderStreamEventKind.TEXT_DELTA,
                        agent_id,
                        text_delta,
                    )
                )
            if step_type == "error_message":
                error_text = _extract_string_message(step_update.get("error"))
                if not error_text:
                    for key in ("message", "text"):
                        extracted = _extract_string_message(step_update.get(key))
                        if extracted:
                            error_text = extracted
                            break
                if error_text:
                    emit_stream_event(
                        ProviderStreamEvent(
                            ProviderStreamEventKind.STATUS,
                            agent_id,
                            f"Error: {error_text[:120]}",
                        )
                    )
        else:
            if invalid_stream_line is None:
                invalid_stream_line = f"unknown event type {event_type!r}"

    def finalize_stream_observation(
        read_stdout: Callable[[], bytes] | None,
    ) -> tuple[Sequence[bytes], str | None, bool]:
        if read_stdout is not None and callback_lines_delivered == 0 and read_stdout():
            for raw_line in read_stdout().splitlines(keepends=True):
                handle_stream_line(raw_line)
        return captured_result_bytes, invalid_stream_line, has_trailing_data_after_terminal

    return handle_stream_line, finalize_stream_observation
