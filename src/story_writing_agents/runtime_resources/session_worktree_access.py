"""Short-lived guarded access to the exact session worktree."""
from __future__ import annotations
import os
from contextlib import AbstractContextManager, contextmanager
from ..backend import BoundAgent, provider_execution_workspace_manager
from collections.abc import Iterator
from ..provider_execution_workspace import ProviderExecutionWorkspace
from ..workspace.initialization import SessionArtifactBinding
from ..writing_artifacts.repository import WritingArtifactRepository


@contextmanager
def open_session_artifact_repository(binding: SessionArtifactBinding) -> Iterator[WritingArtifactRepository]:
    """Yield one repository only for a short synchronous guarded operation."""

    with binding.open_ready_resources() as access:
        yield access.repository

def session_provider_execution_workspace_manager(bound_agent: BoundAgent,
    binding: SessionArtifactBinding
) -> AbstractContextManager[ProviderExecutionWorkspace] | None:
    """Return capability acquisition for a workspace-backed story provider."""

    if bound_agent.vendor not in {"codex", "antigravity"}:
        return None
    return provider_execution_workspace_manager(bound_agent, binding)

def duplicate_session_worktree_descriptor(binding: SessionArtifactBinding) -> int:
    """Duplicate only the exact guarded session-root descriptor."""

    descriptor: int | None = None
    try:
        with binding.open_ready_resources() as access:
            descriptor = access.duplicate_worktree_descriptor()
        return descriptor
    except BaseException:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise
