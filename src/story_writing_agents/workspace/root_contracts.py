"""Workspace root state, identity, and main/session path contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ..errors import WorkspaceFormatError
from ..git.session_contracts import GIT_SESSION_WORKTREE_SIBLING_SUFFIX
from .root_filesystem import require_lexical_root, valid_identity
from .schema import validate_workspace_identifier


LEGACY_WORKSPACE_SENTINELS = (
    "config/system_defaults.json",
    "config/agent_configs.json",
    "config/persona_templates/default_kim_eun_hee.md",
    "config/agents/lead_orchestrator/AGENTS.md",
)


class WorkspaceRootState(StrEnum):
    """Closed safe states from one nonmutating workspace-root inspection."""

    MISSING = "missing"
    EMPTY = "empty"
    INITIALIZING = "initializing"
    READY = "ready"


class WorkspaceRootRole(StrEnum):
    """Closed root roles recognized by schema-v2 discovery."""

    MAIN = "main"
    SESSION = "session"


@dataclass(frozen=True, slots=True)
class WorkspaceRootInspection:
    """One exact root identity and initialization state."""

    state: WorkspaceRootState
    identity: tuple[int, int] | None
    role: WorkspaceRootRole | None = None
    session_id: str | None = None
    main_root: Path | None = None

    def __post_init__(self) -> None:
        if type(self.state) is not WorkspaceRootState:
            raise TypeError("state must be a WorkspaceRootState")
        if self.state is WorkspaceRootState.MISSING:
            if any(
                value is not None
                for value in (self.identity, self.role, self.session_id, self.main_root)
            ):
                raise TypeError("a missing workspace cannot have an identity")
        elif not valid_identity(self.identity):
            raise TypeError("an existing workspace must have an identity")
        if self.state is WorkspaceRootState.READY:
            if type(self.role) is not WorkspaceRootRole or not isinstance(
                self.main_root, Path
            ):
                raise TypeError("a ready workspace must have one exact root role")
            if (self.role is WorkspaceRootRole.MAIN) != (self.session_id is None):
                raise TypeError("workspace session identity is inconsistent")
        elif any(
            value is not None for value in (self.role, self.session_id, self.main_root)
        ):
            raise TypeError("an unready workspace cannot have a root role")


def workspace_root_role(root: Path) -> tuple[WorkspaceRootRole, str | None, Path]:
    """Derive the role and main path of a lexical workspace root."""
    parent = root.parent
    suffix = GIT_SESSION_WORKTREE_SIBLING_SUFFIX
    if parent.name.endswith(suffix):
        workspace_name = parent.name[: -len(suffix)]
        if not workspace_name:
            raise WorkspaceFormatError("workspace session path is invalid")
        try:
            session_id = validate_workspace_identifier(root.name)
        except Exception:
            raise WorkspaceFormatError("workspace session path is invalid") from None
        main_root = parent.parent / workspace_name
        return WorkspaceRootRole.SESSION, session_id, main_root
    return WorkspaceRootRole.MAIN, None, root


def main_workspace_root(root: Path) -> Path:
    """Return the main workspace path for an exact lexical root."""
    lexical = require_lexical_root(root)
    _role, _session_id, main_root = workspace_root_role(lexical)
    return main_root
