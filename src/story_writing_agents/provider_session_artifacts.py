"""Provider session manifest and access artifact I/O."""

from __future__ import annotations

import json
from typing import Any, Mapping

from .errors import SessionNotFoundError, WorkspaceFormatError, WorkspaceVersionUnsupportedError
from .provider_session_access_records import validate_access_claim
from .provider_thread_records import validate_session_manifest
from .workspace import Workspace

PROVIDER_SESSION_DIRECTORY = "agent_states/orchestrator_state/provider_sessions"
_UNSUPPORTED_V1_ACCESS_CLAIM_GUARD = f"{PROVIDER_SESSION_DIRECTORY}/.lease-claims.lock"


def read_access_claim_if_present(
    workspace: Workspace,
    session_id: str,
) -> dict[str, Any] | None:
    """Return a validated claim or ``None`` without storage mutation."""

    relative = access_claim_relative(session_id)
    if not workspace.path(relative).exists():
        return None
    return read_access_claim(workspace, session_id)


def read_access_claim(workspace: Workspace, session_id: str) -> dict[str, Any]:
    """Read and strictly validate one identity-bound V2 access claim."""

    value = _read_lifecycle_json(workspace, access_claim_relative(session_id))
    return validate_access_claim(value, session_id)


def _read_lifecycle_json(workspace: Workspace, relative: str) -> Any:
    """Read lifecycle JSON while rejecting duplicate object fields."""

    text = workspace.read_text(relative)

    def reject_duplicate_fields(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        """Build one object or reject a repeated persisted field name."""

        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate field: {key}")
            result[key] = value
        return result

    try:
        return json.loads(text, object_pairs_hook=dict)
    except (json.JSONDecodeError, ValueError) as exc:
        raise WorkspaceFormatError(
            f"lifecycle file is not valid strict JSON: {relative}"
        ) from exc


def reject_unsupported_access_artifacts(
    workspace: Workspace,
    session_id: str | None,
) -> None:
    """Reject version-1 access sidecars or guards without changing them."""

    directory = workspace.path(PROVIDER_SESSION_DIRECTORY)
    if not directory.exists():
        return
    if not directory.is_dir():
        raise WorkspaceFormatError(
            "provider session manifest path is not a directory"
        )
    if workspace.path(_UNSUPPORTED_V1_ACCESS_CLAIM_GUARD).exists():
        raise WorkspaceVersionUnsupportedError(
            "workspace contains an unsupported version-1 access guard"
        )
    if session_id is not None:
        unsupported = workspace.path(
            f"{PROVIDER_SESSION_DIRECTORY}/{session_id}.lease.json"
        )
        if unsupported.exists():
            raise WorkspaceVersionUnsupportedError(
                "workspace contains an unsupported version-1 access sidecar"
            )
        return
    try:
        has_unsupported_sidecar = any(
            entry.name.endswith(".lease.json") for entry in directory.iterdir()
        )
    except OSError as exc:
        raise WorkspaceFormatError(
            "cannot enumerate provider session manifests"
        ) from exc
    if has_unsupported_sidecar:
        raise WorkspaceVersionUnsupportedError(
            "workspace contains an unsupported version-1 access sidecar"
        )


def read_manifest(
    workspace: Workspace,
    session_id: str,
    *,
    missing_ok: bool,
) -> dict[str, Any] | None:
    """Read and strictly validate an identity-bound session manifest.

    ``None`` is returned only for an absent path when ``missing_ok`` is
    true. Exact schema, timestamp ordering, and every thread entry are
    validated without mutation before copied mappings are returned.
    """

    relative = manifest_relative(session_id)
    target = workspace.path(relative)
    if not target.exists():
        if missing_ok:
            return None
        raise SessionNotFoundError(
            f"provider session does not exist: {session_id}; "
            f"run 'story session create {session_id}'"
        )
    value = _read_lifecycle_json(workspace, relative)
    return validate_session_manifest(value, session_id)


def manifest_exists(workspace: Workspace, session_id: str) -> bool:
    """Report whether the canonical manifest path exists without reading it."""

    return workspace.path(manifest_relative(session_id)).exists()


def write_manifest(
    workspace: Workspace,
    session_id: str,
    value: Mapping[str, Any],
    *,
    overwrite: bool = True,
) -> None:
    """Write one complete manifest through the workspace boundary."""

    workspace.write_json(
        manifest_relative(session_id),
        dict(value),
        overwrite=overwrite,
    )


def manifest_relative(session_id: str) -> str:
    """Return the manifest path for a previously validated session ID."""

    return f"{PROVIDER_SESSION_DIRECTORY}/{session_id}.json"


def access_claim_relative(session_id: str) -> str:
    """Return the access-claim path for a validated session ID."""

    return f"{PROVIDER_SESSION_DIRECTORY}/{session_id}.access.json"


__all__ = (
    "PROVIDER_SESSION_DIRECTORY",
    "access_claim_relative",
    "manifest_exists",
    "manifest_relative",
    "read_access_claim",
    "read_access_claim_if_present",
    "read_manifest",
    "reject_unsupported_access_artifacts",
    "write_manifest",
)
