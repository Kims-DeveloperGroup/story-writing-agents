"""Pure closed path grammar for WorkspaceAgentExchangeStore resources."""

from __future__ import annotations

import re

from .agents import ROSTER


AGENT_EXCHANGE_GUARD_FILENAME = ".agent_exchanges.lock"
AGENT_EXCHANGE_GLOBAL_STATE_FILENAME = ".state.json"
AGENT_EXCHANGE_GLOBAL_PENDING_FILENAME = ".state.pending"
AGENT_EXCHANGE_SEGMENT_BACKUP_COUNT = 7
_ROOT = "debug_logs/agent_exchanges"
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def is_agent_exchange_runtime_directory(path: str) -> bool:
    """Recognize only the store root and one safe session directory."""

    if path == _ROOT:
        return True
    parts = path.split("/")
    return (
        len(parts) == 3
        and "/".join(parts[:2]) == _ROOT
        and _SAFE_IDENTIFIER.fullmatch(parts[2]) is not None
    )


def is_agent_exchange_session_filename(name: str) -> bool:
    """Recognize one exact roster-agent stream/state/pending/backup leaf."""

    for agent_id in ROSTER:
        if name in {
            f"{agent_id}.sequence.json",
            f".{agent_id}.sequence.pending",
            f".{agent_id}.segment.pending",
            f"{agent_id}.jsonl",
        }:
            return True
        if any(
            name == f"{agent_id}.jsonl.{index}"
            for index in range(1, AGENT_EXCHANGE_SEGMENT_BACKUP_COUNT + 1)
        ):
            return True
    return False


def is_agent_exchange_runtime_file(path: str) -> bool:
    """Recognize one exact store-owned root or per-session regular-file path."""

    if path in {
        f"{_ROOT}/{AGENT_EXCHANGE_GUARD_FILENAME}",
        f"{_ROOT}/{AGENT_EXCHANGE_GLOBAL_STATE_FILENAME}",
        f"{_ROOT}/{AGENT_EXCHANGE_GLOBAL_PENDING_FILENAME}",
    }:
        return True
    parts = path.split("/")
    return (
        len(parts) == 4
        and is_agent_exchange_runtime_directory("/".join(parts[:3]))
        and is_agent_exchange_session_filename(parts[3])
    )


__all__: list[str] = []
