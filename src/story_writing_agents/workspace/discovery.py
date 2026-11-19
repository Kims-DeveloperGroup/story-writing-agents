"""Nearest lexical workspace boundary and strict ready discovery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..errors import WorkspaceFormatError
from .schema import WORKSPACE_INITIALIZATION_JOURNAL_PATH, WORKSPACE_MARKER_PATH
from .initialization import inspect_workspace_root
from .root_contracts import (
    LEGACY_WORKSPACE_SENTINELS,
    WorkspaceRootRole,
    WorkspaceRootState,
)
from .root_filesystem import lexical_absolute


@dataclass(frozen=True, slots=True)
class WorkspaceRootDiscovery:
    """One nearest exact v2 boundary with its main/session identity."""

    root: Path
    role: WorkspaceRootRole
    session_id: str | None
    main_root: Path

    def __post_init__(self) -> None:
        if (
            not isinstance(self.root, Path)
            or not isinstance(self.main_root, Path)
            or type(self.role) is not WorkspaceRootRole
            or (self.role is WorkspaceRootRole.MAIN) != (self.session_id is None)
        ):
            raise TypeError("workspace discovery is invalid")


def discover_workspace_location(start: Path) -> WorkspaceRootDiscovery | None:
    """Return the nearest recognizable exact main or session boundary."""

    boundary = _nearest_workspace_boundary(start)
    if boundary is None:
        return None
    inspection = inspect_workspace_root(boundary)
    if inspection.state is not WorkspaceRootState.READY:
        raise WorkspaceFormatError("workspace is not ready")
    assert inspection.role is not None
    assert inspection.main_root is not None
    return WorkspaceRootDiscovery(
        root=boundary,
        role=inspection.role,
        session_id=inspection.session_id,
        main_root=inspection.main_root,
    )


def discover_workspace_root(start: Path) -> Path | None:
    """Return the nearest recognizable workspace boundary without skipping it."""

    return _nearest_workspace_boundary(start)


def _nearest_workspace_boundary(start: Path) -> Path | None:
    current = lexical_absolute(start)
    try:
        if not current.is_dir():
            current = current.parent
    except OSError:
        current = current.parent
    for candidate in (current, *current.parents):
        if _recognizable_boundary(candidate):
            return candidate
    return None


def _recognizable_boundary(root: Path) -> bool:
    try:
        if not root.is_dir():
            return False
        marker = root / WORKSPACE_MARKER_PATH
        journal = root / WORKSPACE_INITIALIZATION_JOURNAL_PATH
        if (
            marker.exists()
            or marker.is_symlink()
            or journal.exists()
            or journal.is_symlink()
        ):
            return True
        return any(
            (root / path).exists() or (root / path).is_symlink()
            for path in LEGACY_WORKSPACE_SENTINELS
        )
    except OSError:
        return False
