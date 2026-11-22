"""Shared inputs and log projections for Backend lifecycle diagnostic cases."""

from __future__ import annotations

import json
from typing import Any
from unittest import mock

from story_writing_agents.debug_log import DEBUG_LOG_RELATIVE_PATH


def _fresh_literal_object_side_effect(target, attribute, value):
    return mock.patch.object(target, attribute, side_effect=value)


def _debug_records(workspace: Any) -> list[dict[str, Any]]:
    """Return every currently persisted diagnostic record."""

    path = workspace.path(DEBUG_LOG_RELATIVE_PATH)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _cleanup_rows_since(
    workspace: Any,
    mark: int,
) -> list[tuple[object, ...]]:
    """Project backend cleanup records after one exact log position."""

    return [
        (
            record["level"],
            record["phase"],
            record.get("outcome"),
            record.get("session_id"),
        )
        for record in _debug_records(workspace)[mark:]
        if record["component"] == "backend" and record["operation"] == "cleanup"
    ]
