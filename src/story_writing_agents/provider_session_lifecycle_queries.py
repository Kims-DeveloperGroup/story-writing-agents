"""Provider session lifecycle reads and state projection."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Mapping

from .errors import WorkspaceFormatError, WorkspaceVersionUnsupportedError
from .provider_session_artifacts import (
    PROVIDER_SESSION_DIRECTORY,
    read_access_claim,
    read_access_claim_if_present,
    read_manifest,
    reject_unsupported_access_artifacts,
)
from .provider_session_timestamps import parse_record_timestamp
from .session_lifecycle import SessionAccessState, SessionDurableState, SessionLifecycleState
from .workspace import Workspace


def get_session_lifecycle(
    workspace: Workspace,
    session_id: str,
    classify_access_state: Callable[[str, Mapping[str, Any]], SessionAccessState],
) -> SessionLifecycleState:
    """Read lifecycle state for an already validated session identifier."""
    reject_unsupported_access_artifacts(workspace, session_id)
    manifest = read_manifest(workspace, session_id, missing_ok=True)
    if manifest is None:
        if read_access_claim_if_present(workspace, session_id) is not None:
            raise WorkspaceFormatError(
                f"provider session access claim has no manifest: {session_id}"
            )
        state = SessionLifecycleState(
            session_id=session_id,
            durable_state=SessionDurableState.ABSENT,
            access_state=SessionAccessState.CLAIMABLE,
            owner_hostname=None,
            owner_pid=None,
            created_at=None,
            updated_at=None,
        )
    else:
        state = session_lifecycle_from_manifest(
            workspace, manifest, classify_access_state,
        )
    return state


def list_session_lifecycles(
    workspace: Workspace,
    classify_access_state: Callable[[str, Mapping[str, Any]], SessionAccessState],
) -> tuple[SessionLifecycleState, ...]:
    """Read persisted lifecycle states in the existing newest-first order."""
    reject_unsupported_access_artifacts(workspace, None)
    directory = workspace.path(PROVIDER_SESSION_DIRECTORY)
    if not directory.exists():
        result: tuple[SessionLifecycleState, ...] = ()
    else:
        if not directory.is_dir():
            raise WorkspaceFormatError(
                "provider session manifest path is not a directory"
            )
        try:
            entries = sorted(
                directory.iterdir(),
                key=lambda entry: entry.name,
            )
        except OSError as exc:
            raise WorkspaceFormatError(
                "cannot enumerate provider session manifests"
            ) from exc

        manifest_sessions: set[str] = set()
        claim_sessions: set[str] = set()
        states: list[SessionLifecycleState] = []
        for entry in entries:
            name = entry.name
            if name in {".gitkeep", ".access-claims.lock"}:
                continue
            if name == ".lease-claims.lock" or name.endswith(".lease.json"):
                raise WorkspaceVersionUnsupportedError(
                    "workspace contains unsupported version-1 "
                    "session access data"
                )
            if name.endswith(".access.json"):
                session = workspace.validate_identifier(
                    name[: -len(".access.json")]
                )
                claim_sessions.add(session)
                read_access_claim(workspace, session)
                continue
            if not name.endswith(".json"):
                continue
            session = workspace.validate_identifier(name[:-5])
            manifest_sessions.add(session)
            manifest = read_manifest(workspace, session, missing_ok=False)
            assert manifest is not None
            states.append(
                    session_lifecycle_from_manifest(
                        workspace, manifest, classify_access_state,
                    )
                )

        orphaned = sorted(claim_sessions.difference(manifest_sessions))
        if orphaned:
            raise WorkspaceFormatError(
                f"provider session access claim has no manifest: {orphaned[0]}"
            )
        states.sort(key=lambda state: state.session_id)
        states.sort(
            key=lambda state: parse_record_timestamp(
                state.updated_at,
                label="provider session updated_at",
            ),
            reverse=True,
        )
        result = tuple(states)
    return result


def session_lifecycle_from_manifest(
    workspace: Workspace,
    manifest: Mapping[str, Any],
    classify_access_state: Callable[[str, Mapping[str, Any]], SessionAccessState],
) -> SessionLifecycleState:
    """Combine one validated manifest with an observed access claim."""

    session = manifest["session_id"]
    claim = read_access_claim_if_present(workspace, session)
    if claim is None:
        access_state = SessionAccessState.CLAIMABLE
        owner_hostname = None
        owner_pid = None
    else:
        access_state = classify_access_state(session, claim)
        owner_hostname = claim["owner_hostname"]
        owner_pid = claim["owner_pid"]
    try:
        durable_state = SessionDurableState(manifest["durable_state"])
    except ValueError as exc:
        raise WorkspaceFormatError(
            "provider session durable state is invalid"
        ) from exc
    return SessionLifecycleState(
        session_id=session,
        durable_state=durable_state,
        access_state=access_state,
        owner_hostname=owner_hostname,
        owner_pid=owner_pid,
        created_at=manifest["created_at"],
        updated_at=manifest["updated_at"],
    )


__all__ = (
    "get_session_lifecycle",
    "list_session_lifecycles",
    "session_lifecycle_from_manifest",
)
