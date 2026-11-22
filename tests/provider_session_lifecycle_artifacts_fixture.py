"""Shared artifact inputs and snapshots for provider session lifecycle tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any


_SESSION_DIRECTORY = "agent_states/orchestrator_state/provider_sessions"
_UPDATED = "2026-01-02T00:00:00+00:00"


def _manifest_relative(session_id: str) -> str:
    """Return the canonical V2 manifest path for ``session_id``."""

    return f"{_SESSION_DIRECTORY}/{session_id}.json"


def _access_relative(session_id: str) -> str:
    """Return the canonical V2 access-claim path for ``session_id``."""

    return f"{_SESSION_DIRECTORY}/{session_id}.access.json"


def _thread(*, updated_at: str = _UPDATED) -> dict[str, Any]:
    """Build one strict persisted provider-thread record."""

    return {
        "backend": "codex",
        "agent_id": "plot_architect",
        "fingerprint": "f" * 64,
        "thread_id": "thread-retained",
        "tainted": False,
        "updated_at": updated_at,
    }


def _artifact_snapshot(path: Path) -> tuple[bool, bytes | None, int | None]:
    """Capture existence, bytes, and modification time for one artifact."""

    if not path.exists():
        return False, None, None
    return True, path.read_bytes(), path.stat().st_mtime_ns
