"""Compose Git session resource, history, and checkpoint operations."""

from __future__ import annotations

from pathlib import Path

from ..git.story_commits import (
    StoryCommitRecoverySnapshot,
    inspect_story_commit_recovery_history,
)
from ..git.session_checkpoints import SessionCheckpointService
from ..git.session_contracts import (
    GitSessionResourceInspection,
    GitSessionResourceInventory,
)
from ..git.session_history import GitSessionHistorySnapshot, open_git_session_history
from ..git.session_resources import (
    GitSessionGenerationPin,
    inspect_git_session_resources as _inspect_git_session_resources,
    list_git_session_resources as _list_git_session_resources,
    provision_git_session_resources as _provision_git_session_resources,
    require_ready_git_session_resources as _require_ready_git_session_resources,
)
from .policy_scaffold import known_baseline_manifest, validated_configuration_allowance
from .root_contracts import main_workspace_root


def inspect_session_resources(
    root: Path,
    session_id: str,
) -> GitSessionResourceInspection:
    """Inspect one session through the bounded workspace-owned composition seam."""

    main_root = main_workspace_root(root)
    manifest = known_baseline_manifest(main_root)
    allowance = validated_configuration_allowance(main_root)
    return _inspect_git_session_resources(
        main_root,
        manifest,
        session_id,
        allowed_worktree_changes=allowance,
    )


def inspect_session_history(
    root: Path,
    session_id: str,
) -> GitSessionHistorySnapshot:
    """Return one immutable complete history snapshot without creating storage."""

    main_root = main_workspace_root(root)
    manifest = known_baseline_manifest(main_root)
    allowance = validated_configuration_allowance(main_root)
    with open_git_session_history(
        main_root,
        manifest,
        session_id,
        create=False,
        allowed_worktree_changes=allowance,
    ) as history:
        return history.snapshot()


def inspect_story_commit_recovery_snapshot(
    root: Path,
    session_id: str,
) -> StoryCommitRecoverySnapshot:
    """Return only one exact staged story-commit recovery correlation."""

    main_root = main_workspace_root(root)
    manifest = known_baseline_manifest(main_root)
    allowance = validated_configuration_allowance(main_root)
    generation_pin: GitSessionGenerationPin | None = None
    try:
        with inspect_story_commit_recovery_history(
            main_root,
            manifest,
            session_id,
            None,
            expected_session_root_identity=None,
            expected_generation_identity=None,
            allowed_worktree_changes=allowance,
        ) as (history, operation, candidate, resources):
            generation_pin = history.acquire_generation_pin()
            snapshot = StoryCommitRecoverySnapshot(
                resources,
                candidate,
                operation,
                generation_pin,
            )
        generation_pin = None
        return snapshot
    except BaseException as failure:
        if generation_pin is not None:
            try:
                generation_pin.close()
            except BaseException as cleanup_failure:
                try:
                    failure.add_note(
                        "story commit recovery pin cleanup failed: "
                        f"{type(cleanup_failure).__name__}"
                    )
                except BaseException:
                    pass
        raise


def list_session_resources(root: Path) -> GitSessionResourceInventory:
    """List the complete correlated session inventory without mutation."""

    main_root = main_workspace_root(root)
    manifest = known_baseline_manifest(main_root)
    return _list_git_session_resources(main_root, manifest)


def provision_session_resources(
    root: Path,
    session_id: str,
) -> GitSessionResourceInspection:
    """Create or resume one exact session triple under the repository lock."""

    main_root = main_workspace_root(root)
    manifest = known_baseline_manifest(main_root)
    allowance = validated_configuration_allowance(main_root)
    return _provision_git_session_resources(
        main_root,
        manifest,
        session_id,
        allowed_worktree_changes=allowance,
    )


def require_ready_session_resources(
    root: Path,
    session_id: str,
) -> GitSessionResourceInspection:
    """Require one exact ready session triple without mutation."""

    main_root = main_workspace_root(root)
    manifest = known_baseline_manifest(main_root)
    allowance = validated_configuration_allowance(main_root)
    return _require_ready_git_session_resources(
        main_root,
        manifest,
        session_id,
        allowed_worktree_changes=allowance,
    )


def compose_session_checkpoint_service(
    root: Path,
    session_id: str,
) -> SessionCheckpointService:
    """Compose checkpoint authority for one freshly validated workspace."""

    main_root = main_workspace_root(root)
    manifest = known_baseline_manifest(main_root)
    allowance = validated_configuration_allowance(main_root)
    _require_ready_git_session_resources(
        main_root,
        manifest,
        session_id,
        allowed_worktree_changes=allowance,
    )
    return SessionCheckpointService(
        main_root,
        manifest,
        session_id,
        allowed_worktree_changes=allowance,
    )
