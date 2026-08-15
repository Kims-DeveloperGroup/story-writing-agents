"""Workspace discovery and workspace-local CLI selection state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agents import AGENT_SPECS, CORE_DOCUMENTS, ROSTER
from .errors import (
    ConfigurationError,
    SessionIdError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from .workspace import Workspace


_CONTEXT_PATH = "agent_states/orchestrator_state/cli_context.json"
_CONTEXT_SCHEMA_VERSION = 1
_CONVERSATION_SELECTION_SCHEMA_VERSION = 1
_CONVERSATION_SELECTION_FIELDS = frozenset(
    {"schema_version", "session_id", "selected_conversation_id"}
)

_FILE_SENTINELS = (
    "config/system_defaults.json",
    "config/agent_configs.json",
    "config/persona_templates/default_kim_eun_hee.md",
    *(AGENT_SPECS[agent_id].system_prompt_file for agent_id in ROSTER),
    *(f"core_documents/{filename}" for filename in CORE_DOCUMENTS),
)
_DIRECTORY_SENTINELS = (
    "agent_states/orchestrator_state",
    "discussion_logs",
    "manuscript",
)


@dataclass(frozen=True, slots=True)
class CliContext:
    """Validated CLI state local to one initialized workspace."""

    selected_session: str | None = None


def resolve_workspace(
    explicit_path: str | None,
    *,
    start: Path | None = None,
) -> Workspace:
    """Resolve an explicit workspace or discover the nearest ancestor."""

    if explicit_path is not None:
        workspace = Workspace(Path(explicit_path))
        if not _is_initialized(workspace):
            raise ConfigurationError(
                f"workspace is not initialized: {workspace.root}; "
                f"run 'story init {workspace.root}'"
            )
        return workspace

    origin = (start or Path.cwd()).expanduser().resolve()
    if not origin.is_dir():
        origin = origin.parent
    for candidate in (origin, *origin.parents):
        workspace = Workspace(candidate)
        if _is_initialized(workspace):
            return workspace
    raise ConfigurationError(
        f"no initialized story workspace found from {origin}; "
        "run 'story init' or pass --workspace PATH"
    )


def load_cli_context(workspace: Workspace) -> CliContext:
    """Load and strictly validate the workspace-local selected session."""

    target = workspace.path(_CONTEXT_PATH)
    if not target.exists():
        return CliContext()
    value = workspace.read_json(_CONTEXT_PATH)
    expected = {"schema_version", "selected_session"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ConfigurationError("CLI context has an invalid schema")
    schema_version = value["schema_version"]
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != _CONTEXT_SCHEMA_VERSION
    ):
        raise ConfigurationError(
            f"unsupported CLI context schema version: {schema_version!r}"
        )
    selected = value["selected_session"]
    if not isinstance(selected, str):
        raise ConfigurationError(
            "CLI context selected_session must be a session ID"
        )
    workspace.validate_identifier(selected)
    return CliContext(selected_session=selected)


def select_session(workspace: Workspace, session_id: str) -> CliContext:
    """Atomically replace the selected session after a successful start."""

    selected = workspace.validate_identifier(session_id)
    value: dict[str, Any] = {
        "schema_version": _CONTEXT_SCHEMA_VERSION,
        "selected_session": selected,
    }
    workspace.write_json(_CONTEXT_PATH, value)
    return CliContext(selected_session=selected)


def load_conversation_selection(
    workspace: Workspace,
    session_id: str,
) -> str | None:
    """Load one session's selected conversation without creating storage.

    The session and exact version-1 JSON object are validated before the
    nullable conversation ID is returned. Reads have no mutation or cleanup
    effect; unsafe paths and malformed storage propagate as typed workspace
    failures.
    """

    session = workspace.validate_identifier(session_id)
    relative = _conversation_selection_path(session)
    target = workspace.path(relative)
    if not target.exists():
        return None

    text = workspace.read_text(relative)

    def reject_duplicate_fields(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        """Build one object while rejecting duplicate persisted field names."""

        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate field: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(text, object_pairs_hook=reject_duplicate_fields)
    except (json.JSONDecodeError, ValueError) as exc:
        raise WorkspaceFormatError(
            f"conversation selection is not valid JSON: {session}"
        ) from exc
    if (
        not isinstance(value, dict)
        or set(value) != _CONVERSATION_SELECTION_FIELDS
    ):
        raise WorkspaceFormatError(
            f"conversation selection has an invalid schema: {session}"
        )
    schema_version = value["schema_version"]
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != _CONVERSATION_SELECTION_SCHEMA_VERSION
    ):
        raise WorkspaceFormatError(
            f"conversation selection has an unsupported schema: {session}"
        )
    embedded_session = value["session_id"]
    if not isinstance(embedded_session, str):
        raise WorkspaceFormatError(
            f"conversation selection has an invalid session ID: {session}"
        )
    try:
        validated_embedded_session = workspace.validate_identifier(
            embedded_session
        )
    except SessionIdError as exc:
        raise WorkspaceFormatError(
            f"conversation selection has an invalid session ID: {session}"
        ) from exc
    if validated_embedded_session != session:
        raise WorkspaceFormatError(
            f"conversation selection does not match its session: {session}"
        )

    selected = value["selected_conversation_id"]
    if selected is None:
        return None
    if not isinstance(selected, str):
        raise WorkspaceFormatError(
            f"conversation selection has an invalid conversation ID: {session}"
        )
    try:
        return workspace.validate_identifier(
            selected,
            label="conversation ID",
        )
    except SessionIdError as exc:
        raise WorkspaceFormatError(
            f"conversation selection has an invalid conversation ID: {session}"
        ) from exc


def save_conversation_selection(
    workspace: Workspace,
    session_id: str,
    selected_conversation_id: str | None,
) -> None:
    """Atomically save or explicitly clear one session's conversation choice.

    Safe identifiers are validated before the exact version-1 object replaces
    its lazy session-scoped file through ``Workspace``. Failed validation or
    replacement leaves controller memory to the caller and propagates without
    an additional cleanup action here.
    """

    session = workspace.validate_identifier(session_id)
    selected = (
        None
        if selected_conversation_id is None
        else workspace.validate_identifier(
            selected_conversation_id,
            label="conversation ID",
        )
    )
    value: dict[str, Any] = {
        "schema_version": _CONVERSATION_SELECTION_SCHEMA_VERSION,
        "session_id": session,
        "selected_conversation_id": selected,
    }
    workspace.write_json(_conversation_selection_path(session), value)


def resolve_session(
    *,
    workspace: Workspace,
    context: CliContext,
    option_session: str | None,
    positional_session: str | None = None,
) -> str:
    """Resolve one explicit or selected session without changing selection."""

    if option_session is not None and positional_session is not None:
        raise ConfigurationError(
            "provide a session once, either positionally or with --session"
        )
    selected = (
        option_session
        if option_session is not None
        else (
            positional_session
            if positional_session is not None
            else context.selected_session
        )
    )
    if selected is None:
        raise ConfigurationError(
            "no session selected; run 'story session start SESSION' "
            "or pass --session SESSION"
        )
    return workspace.validate_identifier(selected)


def _conversation_selection_path(session_id: str) -> str:
    """Return the fixed safe relative selection path for a validated session."""

    return (
        "agent_states/orchestrator_state/conversation_selections/"
        f"{session_id}.json"
    )


def _is_initialized(workspace: Workspace) -> bool:
    try:
        return all(
            workspace.path(relative).is_file()
            for relative in _FILE_SENTINELS
        ) and all(
            workspace.path(relative).is_dir()
            for relative in _DIRECTORY_SENTINELS
        )
    except (OSError, WorkspaceSecurityError):
        return False
