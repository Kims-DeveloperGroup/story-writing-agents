"""Pure validation for agent and debate session records."""
from __future__ import annotations

import re
from typing import Any, Mapping

from ..errors import WorkspaceFormatError

__all__ = [
    "validate_agent_state_entry",
    "validate_debate_state",
    "validate_debate_result",
    "require_debate_ownership",
]
_SAFE_REFERENCE_OPERATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


def validate_agent_state_entry(value: Any) -> dict[str, Any]:
    expected = {
        "agent_id",
        "session_id",
        "debate_id",
        "phase",
        "round",
        "revision",
        "kind",
        "content",
        "timestamp",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise TypeError("agent state has an invalid schema")
    string_fields = expected.difference({"round"})
    if any(not isinstance(value[field], str) for field in string_fields):
        raise TypeError("agent state string field has an invalid type")
    round_number = value["round"]
    if (
        isinstance(round_number, bool)
        or not isinstance(round_number, int)
        or round_number < 0
    ):
        raise TypeError("agent state round must be a non-negative integer")
    if len(value["content"]) > 4096:
        raise TypeError("agent state content exceeds 4096 characters")
    return dict(value)


def validate_debate_state(value: Any) -> dict[str, Any]:
    expected = {
        "schema_version",
        "session_id",
        "debate_id",
        "input_kind",
        "status",
        "terminal",
        "owner_token",
        "created_at",
        "updated_at",
        "revision_id",
        "rounds_completed",
        "error",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise WorkspaceFormatError("debate state has an invalid schema")
    string_fields = {
        "session_id",
        "debate_id",
        "input_kind",
        "status",
        "owner_token",
        "created_at",
        "updated_at",
    }
    if any(not isinstance(value[field], str) for field in string_fields):
        raise WorkspaceFormatError("debate state has an invalid string field")
    if value["schema_version"] == 1:
        raise WorkspaceFormatError("unsupported debate state schema version")
    if not isinstance(value["terminal"], bool):
        raise WorkspaceFormatError("debate state terminal must be boolean")
    if (
        isinstance(value["rounds_completed"], bool)
        or not isinstance(value["rounds_completed"], int)
        or value["rounds_completed"] < 0
    ):
        raise WorkspaceFormatError(
            "debate state rounds_completed must be non-negative"
        )
    if value["revision_id"] is not None and not isinstance(
        value["revision_id"], str
    ):
        raise WorkspaceFormatError("debate state revision_id is invalid")
    if value["error"] is not None and not isinstance(value["error"], str):
        raise WorkspaceFormatError("debate state error is invalid")
    return dict(value)


def validate_debate_result(value: Any) -> dict[str, Any]:
    expected = {
        "session_id",
        "debate_id",
        "input_kind",
        "status",
        "revision_id",
        "rounds_completed",
        "final_candidate",
        "latest_candidate",
        "manuscript_path",
        "core_updates",
        "error",
        "action_required",
    }
    if not isinstance(value, dict) or frozenset(value) not in {
        frozenset(expected),
        frozenset((*expected, "reference_operation_id")),
    }:
        raise WorkspaceFormatError("debate result has an invalid schema")
    required_strings = {"session_id", "debate_id", "input_kind", "status"}
    if any(not isinstance(value[field], str) for field in required_strings):
        raise WorkspaceFormatError("debate result has an invalid string field")
    nullable_strings = {
        "revision_id",
        "final_candidate",
        "latest_candidate",
        "manuscript_path",
        "error",
        "action_required",
    }
    if any(
        value[field] is not None and not isinstance(value[field], str)
        for field in nullable_strings
    ):
        raise WorkspaceFormatError("debate result has an invalid optional field")
    if "reference_operation_id" in value and (
        type(value["reference_operation_id"]) is not str
        or _SAFE_REFERENCE_OPERATION_ID.fullmatch(value["reference_operation_id"])
        is None
    ):
        raise WorkspaceFormatError(
            "debate result reference_operation_id is invalid"
        )
    rounds = value["rounds_completed"]
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 0:
        raise WorkspaceFormatError("debate result rounds_completed is invalid")
    updates = value["core_updates"]
    if not isinstance(updates, list) or any(
        not isinstance(item, str) for item in updates
    ):
        raise WorkspaceFormatError("debate result core_updates is invalid")
    return dict(value)


def require_debate_ownership(
    value: Mapping[str, Any],
    session_id: str,
    debate_id: str,
    label: str,
) -> None:
    if (
        value.get("session_id") != session_id
        or value.get("debate_id") != debate_id
    ):
        raise WorkspaceFormatError(
            f"debate {label} identity does not match its path"
        )
