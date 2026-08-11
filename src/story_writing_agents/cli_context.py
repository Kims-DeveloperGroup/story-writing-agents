"""Workspace discovery and workspace-local CLI selection state."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agents import AGENT_SPECS, CORE_DOCUMENTS, ROSTER
from .errors import ConfigurationError, WorkspaceSecurityError
from .workspace import Workspace


_CONTEXT_PATH = "agent_states/orchestrator_state/cli_context.json"
_CONTEXT_SCHEMA_VERSION = 1

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
