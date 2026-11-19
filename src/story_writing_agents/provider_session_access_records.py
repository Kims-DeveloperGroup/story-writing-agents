"""Provider session access claim values and diagnostics."""

from __future__ import annotations

import os
import re
import socket
from typing import Any, Mapping

from .errors import WorkspaceFormatError, WorkspaceVersionUnsupportedError
from .provider_session_timestamps import parse_record_timestamp, record_timestamp
from .session_lifecycle import SessionAccessState

_ACCESS_CLAIM_SCHEMA_VERSION = 2
_ACCESS_DIAGNOSTIC_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


def validate_access_claim(value: object, session_id: str) -> dict[str, Any]:
    """Return a copy of a valid access claim for the given session identity."""
    expected = {
        "schema_version",
        "session_id",
        "owner_hostname",
        "owner_pid",
        "claim_token",
        "claimed_at",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise WorkspaceFormatError(
            "provider session access claim has an invalid schema"
        )
    schema_version = value["schema_version"]
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise WorkspaceFormatError(
            "provider session access claim has an invalid schema version"
        )
    if schema_version != _ACCESS_CLAIM_SCHEMA_VERSION:
        raise WorkspaceVersionUnsupportedError(
            "unsupported provider session access claim schema version"
        )
    if value["session_id"] != session_id:
        raise WorkspaceFormatError("provider session access claim path mismatch")
    for field in (
        "session_id",
        "owner_hostname",
        "claim_token",
        "claimed_at",
    ):
        if not isinstance(value[field], str) or not value[field]:
            raise WorkspaceFormatError(
                "provider session access claim has an invalid string field"
            )
    parse_record_timestamp(
        value["claimed_at"],
        label="provider session access claimed_at",
    )
    if (
        isinstance(value["owner_pid"], bool)
        or not isinstance(value["owner_pid"], int)
        or value["owner_pid"] <= 0
    ):
        raise WorkspaceFormatError(
            "provider session access claim has an invalid owner PID"
        )
    return dict(value)


def unowned_access_state(value: Mapping[str, Any]) -> SessionAccessState:
    """Classify unowned access using only safe local process liveness.

    A missing local POSIX process is claimable. Remote hosts, unsupported
    platforms, inaccessible processes, and ambiguous probes remain unknown.
    """

    if value["owner_hostname"] != socket.gethostname():
        return SessionAccessState.OWNERSHIP_UNKNOWN
    if os.name != "posix":
        return SessionAccessState.OWNERSHIP_UNKNOWN
    pid = value["owner_pid"]
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return SessionAccessState.CLAIMABLE
    except PermissionError:
        return SessionAccessState.OWNERSHIP_UNKNOWN
    except (OSError, OverflowError, ValueError):
        return SessionAccessState.OWNERSHIP_UNKNOWN
    return SessionAccessState.BLOCKED_BY_LIVE_LOCAL_OWNER


def access_hostname_diagnostic(hostname: str) -> str:
    """Return a control-sanitized hostname diagnostic of at most 255 chars."""

    sanitized = _ACCESS_DIAGNOSTIC_CHARACTERS.sub("?", hostname)
    if len(sanitized) > 255:
        sanitized = sanitized[:243] + " [truncated]"
    return sanitized


def new_access_claim(session_id: str, token: str) -> dict[str, Any]:
    """Build one exact V2 claim for the current host and process."""

    return {
        "schema_version": _ACCESS_CLAIM_SCHEMA_VERSION,
        "session_id": session_id,
        "owner_hostname": socket.gethostname(),
        "owner_pid": os.getpid(),
        "claim_token": token,
        "claimed_at": record_timestamp(),
    }


__all__ = (
    "access_hostname_diagnostic",
    "new_access_claim",
    "unowned_access_state",
    "validate_access_claim",
)
