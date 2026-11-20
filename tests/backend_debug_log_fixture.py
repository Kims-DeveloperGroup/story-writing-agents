"""Shared debug log fixtures for Codex backend tests."""
from __future__ import annotations

import json
from typing import Any

from story_writing_agents.debug_log import DEBUG_LOG_RELATIVE_PATH


def workspace_debug_records(workspace: Any) -> list[dict[str, object]]:
    """Read the complete diagnostic log for one initialized workspace."""

    path = workspace.path(DEBUG_LOG_RELATIVE_PATH)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def mark_debug_session_attached(workspace: Any) -> None:
    """Model the attached-session boundary required before runtime logging."""

    state = workspace.path(
        "agent_states/orchestrator_state/sessions/test_attached.json")
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{}\n", encoding="utf-8")


def debug_signature(record: dict[str, object]) -> tuple[object, ...]:
    """Project one diagnostic record to its exact content-safe contract."""

    exception = record.get("exception")
    return (record.get("schema_version"),
        record.get("level"), record.get("component"),
        record.get("operation"), record.get("phase"),
        record.get("outcome"), record.get("session_id"),
        record.get("debate_id"), record.get("conversation_id"),
        record.get("agent_id"),
        exception.get("type") if isinstance(exception, dict) else None,)
