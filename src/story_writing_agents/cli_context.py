"""Workspace discovery and workspace-local CLI default state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import (
    ConfigurationError,
    SessionIdError,
    WorkspaceFormatError,
    WorkspaceVersionUnsupportedError,
)
from .workspace import Workspace, discover_workspace


_CONTEXT_PATH = "agent_states/orchestrator_state/cli_context.json"
_CONTEXT_SCHEMA_VERSION = 2
_CONVERSATION_DEFAULT_SCHEMA_VERSION = 2
_CONVERSATION_DEFAULT_FIELDS = frozenset(
    {"schema_version", "session_id", "default_conversation_id"}
)
_UNSUPPORTED_V1_CONVERSATION_DEFAULT_DIRECTORY = (
    "agent_states/orchestrator_state/conversation_selections"
)


@dataclass(frozen=True, slots=True)
class CliContext:
    """Validated saved defaults local to one initialized workspace."""

    default_session_id: str | None = None


def resolve_workspace(
    explicit_path: str | None,
    *,
    start: Path | None = None,
) -> Workspace:
    """Resolve an explicit workspace or discover the nearest ancestor."""

    if explicit_path is not None:
        return Workspace(Path(explicit_path)).require_ready()

    origin = start or Path.cwd()
    workspace = discover_workspace(origin)
    if workspace is not None:
        return workspace
    raise ConfigurationError(
        f"no initialized story workspace found from {origin}; "
        "run 'story init' or pass --workspace PATH"
    )


def load_cli_context(workspace: Workspace) -> CliContext:
    """Load the exact V2 default-session record without mutation."""

    _reject_unsupported_conversation_artifacts(workspace, None)
    target = workspace.path(_CONTEXT_PATH)
    if not target.exists():
        return CliContext()
    value = _read_json_without_duplicate_fields(workspace, _CONTEXT_PATH)
    if isinstance(value, dict):
        schema_version = value.get("schema_version")
        if (
            isinstance(schema_version, int)
            and not isinstance(schema_version, bool)
            and schema_version != _CONTEXT_SCHEMA_VERSION
        ):
            raise WorkspaceVersionUnsupportedError(
                "unsupported CLI context schema version"
            )
    expected = {"schema_version", "default_session_id"}
    if not isinstance(value, dict) or set(value) != expected:
        raise WorkspaceFormatError("CLI context has an invalid schema")
    schema_version = value["schema_version"]
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise WorkspaceFormatError("CLI context has an invalid schema version")
    if schema_version != _CONTEXT_SCHEMA_VERSION:
        raise WorkspaceVersionUnsupportedError("unsupported CLI context schema version")
    default_session_id = value["default_session_id"]
    if not isinstance(default_session_id, str):
        raise WorkspaceFormatError(
            "CLI context default_session_id must be a session ID"
        )
    try:
        workspace.validate_identifier(default_session_id)
    except SessionIdError as exc:
        raise WorkspaceFormatError(
            "CLI context default_session_id must be a session ID"
        ) from exc
    return CliContext(default_session_id=default_session_id)


def save_default_session(workspace: Workspace, session_id: str) -> CliContext:
    """Atomically save the default session after a successful transition."""

    if workspace.path(_CONTEXT_PATH).exists():
        load_cli_context(workspace)
    else:
        _reject_unsupported_conversation_artifacts(workspace, None)
    default_session_id = workspace.validate_identifier(session_id)
    value: dict[str, Any] = {
        "schema_version": _CONTEXT_SCHEMA_VERSION,
        "default_session_id": default_session_id,
    }
    workspace.write_json(_CONTEXT_PATH, value)
    return CliContext(default_session_id=default_session_id)


def load_default_conversation(
    workspace: Workspace,
    session_id: str,
) -> str | None:
    """Load one session's exact V2 conversation default when present."""

    session = workspace.validate_identifier(session_id)
    _reject_unsupported_conversation_artifacts(workspace, session)
    relative = _conversation_default_path(session)
    try:
        value = _read_json_without_duplicate_fields(workspace, relative)
    except WorkspaceFormatError as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            return None
        raise
    if isinstance(value, dict):
        schema_version = value.get("schema_version")
        if (
            isinstance(schema_version, int)
            and not isinstance(schema_version, bool)
            and schema_version != _CONVERSATION_DEFAULT_SCHEMA_VERSION
        ):
            raise WorkspaceVersionUnsupportedError(
                f"unsupported conversation-default schema: {session}"
            )
    if not isinstance(value, dict) or set(value) != _CONVERSATION_DEFAULT_FIELDS:
        raise WorkspaceFormatError(
            f"conversation default has an invalid schema: {session}"
        )
    schema_version = value["schema_version"]
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise WorkspaceFormatError(
            f"conversation default has an invalid schema version: {session}"
        )
    if schema_version != _CONVERSATION_DEFAULT_SCHEMA_VERSION:
        raise WorkspaceVersionUnsupportedError(
            f"unsupported conversation-default schema: {session}"
        )
    embedded_session = value["session_id"]
    if not isinstance(embedded_session, str):
        raise WorkspaceFormatError(
            f"conversation default has an invalid session ID: {session}"
        )
    try:
        embedded_session = workspace.validate_identifier(embedded_session)
    except SessionIdError as exc:
        raise WorkspaceFormatError(
            f"conversation default has an invalid session ID: {session}"
        ) from exc
    if embedded_session != session:
        raise WorkspaceFormatError(
            f"conversation default does not match its session: {session}"
        )

    default_conversation_id = value["default_conversation_id"]
    if default_conversation_id is None:
        return None
    if not isinstance(default_conversation_id, str):
        raise WorkspaceFormatError(
            f"conversation default has an invalid conversation ID: {session}"
        )
    try:
        return workspace.validate_identifier(
            default_conversation_id,
            label="conversation ID",
        )
    except SessionIdError as exc:
        raise WorkspaceFormatError(
            f"conversation default has an invalid conversation ID: {session}"
        ) from exc


def save_default_conversation(
    workspace: Workspace,
    session_id: str,
    default_conversation_id: str | None,
) -> None:
    """Atomically save or clear one session's V2 conversation default."""

    session = workspace.validate_identifier(session_id)
    _reject_unsupported_conversation_artifacts(workspace, session)
    default_id = (
        None
        if default_conversation_id is None
        else workspace.validate_identifier(
            default_conversation_id,
            label="conversation ID",
        )
    )
    value: dict[str, Any] = {
        "schema_version": _CONVERSATION_DEFAULT_SCHEMA_VERSION,
        "session_id": session,
        "default_conversation_id": default_id,
    }
    workspace.write_json(_conversation_default_path(session), value)


def resolve_session(
    *,
    workspace: Workspace,
    context: CliContext,
    option_session: str | None,
) -> str:
    """Resolve one explicit option or default without changing that default."""

    resolved = (
        option_session if option_session is not None else context.default_session_id
    )
    if resolved is None:
        raise ConfigurationError(
            "no default session; run 'story session create SESSION' or pass "
            "--session SESSION"
        )
    return workspace.validate_identifier(resolved)


def _read_json_without_duplicate_fields(
    workspace: Workspace,
    relative: str,
) -> Any:
    """Read strict JSON while rejecting repeated object field names."""

    text = workspace.read_text(relative)

    def reject_duplicate_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        """Build one object or reject a repeated persisted field name."""

        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate field: {key}")
            result[key] = value
        return result

    try:
        return json.loads(text, object_pairs_hook=reject_duplicate_fields)
    except (json.JSONDecodeError, ValueError) as exc:
        raise WorkspaceFormatError(
            f"workspace file is not valid JSON: {relative}"
        ) from exc


def _reject_unsupported_conversation_artifacts(
    workspace: Workspace,
    session_id: str | None,
) -> None:
    """Reject version-1 conversation-choice files without changing them."""

    directory = workspace.path(_UNSUPPORTED_V1_CONVERSATION_DEFAULT_DIRECTORY)
    if not directory.exists():
        return
    if not directory.is_dir():
        raise WorkspaceFormatError(
            "version-1 conversation-choice path is not a directory"
        )
    if session_id is not None:
        if workspace.path(
            f"{_UNSUPPORTED_V1_CONVERSATION_DEFAULT_DIRECTORY}/{session_id}.json"
        ).exists():
            raise WorkspaceVersionUnsupportedError(
                "workspace contains an unsupported version-1 conversation default"
            )
        return
    try:
        has_unsupported_file = any(
            entry.name.endswith(".json") for entry in directory.iterdir()
        )
    except OSError as exc:
        raise WorkspaceFormatError(
            "cannot enumerate version-1 conversation-choice files"
        ) from exc
    if has_unsupported_file:
        raise WorkspaceVersionUnsupportedError(
            "workspace contains an unsupported version-1 conversation default"
        )


def _conversation_default_path(session_id: str) -> str:
    """Return the fixed V2 conversation-default path for a safe session ID."""

    return f"agent_states/orchestrator_state/conversation_defaults/{session_id}.json"
