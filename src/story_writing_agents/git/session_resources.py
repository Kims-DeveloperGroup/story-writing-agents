"""Locked entry points for exact local Git session resources."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from .contracts import GitBaselineManifest, GitManifestEntry, GitOperationError
from .repository_lock import GitRepositoryLease
from .session_contracts import (
    GitSessionFailureCode,
    GitSessionIdentity,
    GitSessionOperationKind,
    GitSessionRecoveryAction,
    GitSessionResourceError,
    GitSessionResourceInspection,
    GitSessionResourceInventory,
    GitSessionResourceState,
)
from .session_generation import (
    GitSessionGenerationIdentity as GitSessionGenerationIdentity,
    GitSessionGenerationPin as GitSessionGenerationPin,
    acquire_git_session_generation_pin as acquire_git_session_generation_pin,
    acquire_locked_git_session_generation_pin as acquire_locked_git_session_generation_pin,
    capture_locked_git_session_generation as capture_locked_git_session_generation,
    require_locked_git_session_untracked_prefix as require_locked_git_session_untracked_prefix,
)
from .session_provisioning import SessionResourceProvisioner
from .session_resource_files import bind_directory, linked_worktree_common_git
from .session_resource_inspection import SessionResourceInspector
from .session_resource_repository import (
    SessionResourceRepository,
    lock_session_resources,
    repository_from_lease,
)
from .session_resource_values import clear_resource_error, resource_error


@dataclass(frozen=True, slots=True)
class GitSessionRecoveryTopology:
    """Exact session topology whose only deferred fact is index cleanliness."""

    resources: GitSessionResourceInspection
    main_status_sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.resources) is not GitSessionResourceInspection
            or self.resources.state is not GitSessionResourceState.READY
            or type(self.main_status_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", self.main_status_sha256) is None
        ):
            raise TypeError("recovery topology requires exact ready resources")


def inspect_git_session_resources(
    root: str | os.PathLike[str],
    manifest: GitBaselineManifest,
    session_id: str,
    *,
    allowed_worktree_changes: tuple[str, ...] = (),
) -> GitSessionResourceInspection:
    """Inspect one requested triple without creating or repairing it."""

    failure: GitSessionResourceError | None = None
    try:
        main_root, expected_manifest, identity = _request(root, manifest, session_id)
        with lock_session_resources(main_root, create=False) as repository:
            return SessionResourceInspector(repository).inspect(
                expected_manifest,
                identity,
                allowed_worktree_changes=allowed_worktree_changes,
            )
    except GitSessionResourceError as exc:
        failure = clear_resource_error(exc)
    raise failure


def list_git_session_resources(
    root: str | os.PathLike[str],
    manifest: GitBaselineManifest,
    *,
    allowed_worktree_changes: tuple[str, ...] = (),
) -> GitSessionResourceInventory:
    """Return the exhaustive correlated inventory without mutation."""

    failure: GitSessionResourceError | None = None
    try:
        main_root, expected_manifest = _root_and_manifest(root, manifest)
        with lock_session_resources(main_root, create=False) as repository:
            return SessionResourceInspector(repository).list(
                expected_manifest,
                allowed_worktree_changes=allowed_worktree_changes,
            )
    except GitSessionResourceError as exc:
        failure = clear_resource_error(exc)
    raise failure


def provision_git_session_resources(
    root: str | os.PathLike[str],
    manifest: GitBaselineManifest,
    session_id: str,
    *,
    allowed_worktree_changes: tuple[str, ...] = (),
) -> GitSessionResourceInspection:
    """Create or resume only one exact ordered session-resource prefix."""

    failure: GitSessionResourceError | None = None
    try:
        supplied = _path(root)
        main_root, expected_manifest, identity = _request(root, manifest, session_id)
        if supplied != main_root:
            raise resource_error(
                GitSessionFailureCode.INVALID_REQUEST,
                GitSessionOperationKind.PROVISION,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.NONE,
            )
        with lock_session_resources(main_root, create=True) as repository:
            return SessionResourceProvisioner(repository).provision(
                expected_manifest,
                identity,
                allowed_worktree_changes=allowed_worktree_changes,
            )
    except GitSessionResourceError as exc:
        failure = clear_resource_error(exc)
    raise failure


def require_ready_git_session_resources(
    root: str | os.PathLike[str],
    manifest: GitBaselineManifest,
    session_id: str,
    *,
    allowed_worktree_changes: tuple[str, ...] = (),
) -> GitSessionResourceInspection:
    """Require one exact ready triple without provisioning it."""

    failure: GitSessionResourceError | None = None
    try:
        result = inspect_git_session_resources(
            root,
            manifest,
            session_id,
            allowed_worktree_changes=allowed_worktree_changes,
        )
        if result.state is not GitSessionResourceState.READY:
            raise resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.REQUIRE_READY,
                result.state,
                result.recovery_action,
            )
        return result
    except GitSessionResourceError as exc:
        failure = clear_resource_error(exc)
    raise failure


def inspect_locked_git_session_resources(
    lease: GitRepositoryLease,
    manifest: GitBaselineManifest,
    session_id: str,
    *,
    allowed_worktree_changes: tuple[str, ...] = (),
) -> GitSessionResourceInspection:
    """Inspect one session through an already-held shared repository lease."""

    failure: GitSessionResourceError | None = None
    try:
        repository, expected_manifest, identity = _locked_request(
            lease,
            manifest,
            session_id,
        )
        return SessionResourceInspector(repository).inspect(
            expected_manifest,
            identity,
            allowed_worktree_changes=allowed_worktree_changes,
        )
    except GitSessionResourceError as exc:
        failure = clear_resource_error(exc)
    raise failure


def require_ready_locked_git_session_resources(
    lease: GitRepositoryLease,
    manifest: GitBaselineManifest,
    session_id: str,
    *,
    allowed_worktree_changes: tuple[str, ...] = (),
) -> GitSessionResourceInspection:
    """Require one ready session through an already-held repository lease."""

    failure: GitSessionResourceError | None = None
    try:
        result = inspect_locked_git_session_resources(
            lease,
            manifest,
            session_id,
            allowed_worktree_changes=allowed_worktree_changes,
        )
        if result.state is not GitSessionResourceState.READY:
            raise resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.REQUIRE_READY,
                result.state,
                result.recovery_action,
            )
        return result
    except GitSessionResourceError as exc:
        failure = clear_resource_error(exc)
    raise failure


def require_story_commit_recovery_locked_git_session_resources(
    lease: GitRepositoryLease,
    manifest: GitBaselineManifest,
    session_id: str,
    *,
    allowed_worktree_changes: tuple[str, ...] = (),
) -> GitSessionRecoveryTopology:
    """Require exact recovery topology while deferring only session-index cleanliness."""

    failure: GitSessionResourceError | None = None
    try:
        repository, expected_manifest, identity = _locked_request(
            lease,
            manifest,
            session_id,
        )
        inspector = SessionResourceInspector(repository)
        refs = inspector.data.read_refs()
        baseline = inspector.data.verify_main(
            expected_manifest,
            refs,
            allowed_worktree_changes=allowed_worktree_changes,
        )
        main_status_sha256 = inspector.data.main_status_sha256(
            allowed_worktree_changes=allowed_worktree_changes,
        )
        resources = inspector.classify(
            identity,
            baseline,
            inspector.facts(refs),
            require_clean_session_index=False,
        )
        inspector.data.require_refs_current(refs)
        if resources.state is not GitSessionResourceState.READY:
            raise resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.REQUIRE_READY,
                resources.state,
                resources.recovery_action,
            )
        return GitSessionRecoveryTopology(resources, main_status_sha256)
    except GitSessionResourceError as exc:
        failure = clear_resource_error(exc)
    raise failure


def _locked_request(
    lease: GitRepositoryLease,
    manifest: GitBaselineManifest,
    session_id: str,
) -> tuple[SessionResourceRepository, GitBaselineManifest, GitSessionIdentity]:
    repository = repository_from_lease(lease)
    main_root, expected_manifest, identity = _request(
        repository.main_root,
        manifest,
        session_id,
    )
    if main_root != repository.main_root:
        raise resource_error(
            GitSessionFailureCode.UNSAFE_ROOT,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
    return repository, expected_manifest, identity


def _request(
    root: str | os.PathLike[str],
    manifest: GitBaselineManifest,
    session_id: str,
) -> tuple[Path, GitBaselineManifest, GitSessionIdentity]:
    supplied = _path(root)
    main_root, expected_manifest = _root_and_manifest(root, manifest)
    try:
        identity = GitSessionIdentity(main_root, session_id)
    except GitSessionResourceError:
        raise resource_error(
            GitSessionFailureCode.INVALID_REQUEST,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.NONE,
        ) from None
    if supplied not in {main_root, identity.worktree_root}:
        raise resource_error(
            GitSessionFailureCode.UNSAFE_ROOT,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
    return main_root, expected_manifest, identity


def _root_and_manifest(
    root: str | os.PathLike[str],
    manifest: GitBaselineManifest,
) -> tuple[Path, GitBaselineManifest]:
    path = _path(root)
    bind_directory(path)
    main_root = _candidate_main_root(path)
    try:
        if type(manifest) is not GitBaselineManifest:
            raise TypeError
        supplied_entries = manifest.entries
        supplied_digest = manifest.manifest_digest
        if (
            type(supplied_entries) is not tuple
            or not supplied_entries
            or any(type(entry) is not GitManifestEntry for entry in supplied_entries)
        ):
            raise TypeError
        expected = GitBaselineManifest(
            tuple(
                GitManifestEntry(entry.path, entry.content_digest)
                for entry in supplied_entries
            )
        )
        if expected.manifest_digest != supplied_digest:
            raise TypeError
    except (AttributeError, GitOperationError, TypeError):
        raise resource_error(
            GitSessionFailureCode.INVALID_REQUEST,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.NONE,
        ) from None
    return main_root, expected


def _path(root: str | os.PathLike[str]) -> Path:
    try:
        raw = os.fspath(root)
        path = Path(raw)
    except (TypeError, ValueError):
        raise resource_error(
            GitSessionFailureCode.INVALID_REQUEST,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.NONE,
        ) from None
    if not path.is_absolute() or path != Path(os.path.normpath(os.fspath(path))):
        raise resource_error(
            GitSessionFailureCode.INVALID_REQUEST,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.NONE,
        )
    return path


def _candidate_main_root(path: Path) -> Path:
    administrative = path / ".git"
    try:
        value = os.lstat(administrative)
    except OSError:
        raise resource_error(
            GitSessionFailureCode.UNSAFE_ROOT,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        ) from None
    if stat.S_ISDIR(value.st_mode) and not stat.S_ISLNK(value.st_mode):
        return path
    if not stat.S_ISREG(value.st_mode) or stat.S_ISLNK(value.st_mode):
        raise resource_error(
            GitSessionFailureCode.UNSAFE_ROOT,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
    linked = linked_worktree_common_git(path)
    if linked is None:
        raise resource_error(
            GitSessionFailureCode.UNSAFE_ROOT,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
    common, _identity = linked
    return common.parent


__all__ = [
    "GitSessionGenerationIdentity",
    "GitSessionGenerationPin",
    "acquire_git_session_generation_pin",
    "capture_locked_git_session_generation",
    "inspect_git_session_resources",
    "inspect_locked_git_session_resources",
    "list_git_session_resources",
    "provision_git_session_resources",
    "require_locked_git_session_untracked_prefix",
    "require_ready_git_session_resources",
    "require_ready_locked_git_session_resources",
]
