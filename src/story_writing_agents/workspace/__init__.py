"""Stable import boundary for workspace persistence."""

from ..conversation_storage.filesystem import (
    FilesystemConversationCatalog as FilesystemConversationCatalog,
)
from .core import (
    WORKSPACE_DIRECTORIES as WORKSPACE_DIRECTORIES,
    Workspace as Workspace,
    initialize_workspace as initialize_workspace,
    open_workspace_root_directory as open_workspace_root_directory,
    verify_workspace_root_directory as verify_workspace_root_directory,
)


Workspace.__module__ = __name__
initialize_workspace.__module__ = __name__

__all__ = [
    "Workspace",
    "initialize_workspace",
    "FilesystemConversationCatalog",
]
