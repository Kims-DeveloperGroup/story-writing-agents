"""Antigravity native trace parsing and current finish matching."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from .antigravity_response_decoding import parse_result


MALFORMED_FUNCTION_CALL = re.compile(
    r"Your previous response contained an improperly formatted function call\. "
    r"Please retry with a properly formatted function call\. Retries remaining: ([0-3])"
)
_NATIVE_COMPLETED = re.compile(
    r"Created At: ([^\n]+)\nCompleted At: ([^\n]+)\n"
    r"Task is complete\. Summarize what you did and do not call any more tools\."
)
_NATIVE_TRACE_MAX_LINE_BYTES = 512 * 1024
_NATIVE_TRACE_MAX_STEPS = 4096


class NativeEvidenceUnavailable(ValueError):
    """Reject unsafe or incomplete recovery evidence without native content."""


def native_steps(content: bytes) -> list[dict[str, Any]]:
    """Validate finite JSONL structure; private thinking is never evidence."""

    if not content.endswith(b"\n"):
        raise NativeEvidenceUnavailable
    lines = content.split(b"\n")[:-1]
    if not 0 < len(lines) <= _NATIVE_TRACE_MAX_STEPS:
        raise NativeEvidenceUnavailable
    sources = {
        "USER_INPUT": "USER_EXPLICIT",
        "PLANNER_RESPONSE": "MODEL",
        "ERROR_MESSAGE": "SYSTEM",
        "GENERIC": "MODEL",
    }
    steps: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if not 0 < len(line) <= _NATIVE_TRACE_MAX_LINE_BYTES:
            raise NativeEvidenceUnavailable
        step = parse_result(line)
        step.pop("thinking", None)
        kind = step.get("type")
        if (
            type(step.get("step_index")) is not int
            or step["step_index"] != index
            or type(kind) is not str
            or kind not in sources
            or step.get("source") != sources[kind]
            or step.get("status") != "DONE"
            or (kind != "PLANNER_RESPONSE" and "tool_calls" in step)
        ):
            raise NativeEvidenceUnavailable
        steps.append(step)
    return steps


def _native_timestamp(value: object) -> datetime:
    if type(value) is not str:
        raise NativeEvidenceUnavailable
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise NativeEvidenceUnavailable
    return timestamp


def _matches_native_request(content: object, prompt: str) -> bool:
    if type(content) is not str:
        return False
    framed = f"<USER_REQUEST>\n{prompt}\n</USER_REQUEST>"
    if not content.startswith(framed):
        return False
    suffix = content[len(framed):]
    for tag in ("ADDITIONAL_METADATA", "USER_SETTINGS_CHANGE"):
        opening = f"\n<{tag}>\n"
        if suffix.startswith(opening):
            metadata, ending, suffix = suffix[len(opening):].partition(
                f"\n</{tag}>"
            )
            if (
                not ending
                or "<USER_REQUEST>" in metadata
                or "</USER_REQUEST>" in metadata
            ):
                return False
    return suffix == ""


def matches_current_finish(
    steps: list[dict[str, Any]],
    prompt: str,
    structured_output: dict[str, Any],
    native_error: str,
) -> bool:
    if (
        len(steps) < 3
        or steps[0]["type"] != "USER_INPUT"
        or not _matches_native_request(steps[0].get("content"), prompt)
    ):
        return False
    timestamps = [
        _native_timestamp(step.get("created_at")) for step in steps
    ]
    if any(
        later < earlier
        for earlier, later in zip(timestamps, timestamps[1:])
    ):
        return False
    last_error: str | None = None
    retries: int | None = None
    for step in steps[1:-2]:
        if step["type"] == "PLANNER_RESPONSE" and not step.get("tool_calls"):
            continue
        if (
            step["type"] != "ERROR_MESSAGE"
            or type(step.get("content")) is not str
        ):
            return False
        content = step["content"]
        if not content.startswith("Error: "):
            return False
        match = MALFORMED_FUNCTION_CALL.fullmatch(content[len("Error: "):])
        if match is None or (
            retries is not None and int(match[1]) != retries - 1
        ):
            return False
        retries = int(match[1])
        last_error = content[len("Error: "):]
    if last_error is not None and last_error != native_error:
        return False
    finish, completed = steps[-2:]
    calls = finish.get("tool_calls")
    if (
        finish["type"] != "PLANNER_RESPONSE"
        or type(calls) is not list
        or len(calls) != 1
        or type(calls[0]) is not dict
        or set(calls[0]) != {"name", "args"}
        or calls[0]["name"] != "finish"
        or type(calls[0]["args"]) is not dict
    ):
        return False
    arguments = dict(calls[0]["args"])
    for metadata in ("toolAction", "toolSummary"):
        if metadata in arguments and metadata not in structured_output:
            if type(arguments.pop(metadata)) is not str:
                return False

    def structural_json(value: dict[str, Any]) -> str:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    if structural_json(arguments) != structural_json(structured_output):
        return False
    if (
        completed["type"] != "GENERIC"
        or type(completed.get("content")) is not str
    ):
        return False
    match = _NATIVE_COMPLETED.fullmatch(completed["content"])
    if match is None:
        return False
    created = _native_timestamp(match[1])
    ended = _native_timestamp(match[2])
    return timestamps[-2] <= created <= timestamps[-1] <= ended


__all__ = (
    "MALFORMED_FUNCTION_CALL",
    "NativeEvidenceUnavailable",
    "matches_current_finish",
    "native_steps",
)
